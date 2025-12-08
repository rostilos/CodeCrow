import os
import asyncio
from typing import Dict, Any, Optional, Callable
from dotenv import load_dotenv
from mcp_use import MCPAgent, MCPClient

from model.models import ReviewRequestDto
from utils.mcp_config import MCPConfigBuilder
from llm.llm_factory import LLMFactory
from utils.prompt_builder import PromptBuilder
from utils.response_parser import ResponseParser
from service.rag_client import RagClient

class ReviewService:
    """Service class for handling code review requests with streaming support."""

    def __init__(self):
        load_dotenv()
        self.default_jar_path = os.environ.get(
            "MCP_SERVER_JAR",
            #"/var/www/html/codecrow/java-bitbucket-mcp/codecrow-mcp-servers/target/codecrow-mcp-servers-1.0.jar",
            "/app/codecrow-mcp-servers-1.0.jar"
        )
        self.rag_client = RagClient()

    async def process_review_request(
            self,
            request: ReviewRequestDto,
            event_callback: Optional[Callable[[Dict], None]] = None
    ) -> Dict[str, Any]:
        """
        Process a review request with optional event streaming.

        Args:
            request: The review request data
            event_callback: Optional callback to receive progress events
                          Expected signature: callback(event: Dict) -> None
                          Events have structure: {"type": "status|progress|error|final", ...}

        Returns:
            Dict with "result" key containing the analysis result or error
        """
        return await self._process_review(
            request=request,
            repo_path=None,
            event_callback=event_callback
        )

    async def process_review_request_with_local_repo(
            self,
            request: ReviewRequestDto,
            repo_path: str,
            max_allowed_tokens: Optional[int] = None,
            event_callback: Optional[Callable[[Dict], None]] = None
    ) -> Dict[str, Any]:
        """
        Process a review request using a local repository directory.

        Args:
            request: The review request data
            repo_path: Path to the local repository
            max_allowed_tokens: Optional token limit
            event_callback: Optional callback to receive progress events

        Returns:
            Dict with "result" key containing the analysis result or error
        """
        return await self._process_review(
            request=request,
            repo_path=repo_path,
            max_allowed_tokens=max_allowed_tokens,
            event_callback=event_callback
        )

    async def _process_review(
            self,
            request: ReviewRequestDto,
            repo_path: Optional[str] = None,
            max_allowed_tokens: Optional[int] = None,
            event_callback: Optional[Callable[[Dict], None]] = None
    ) -> Dict[str, Any]:
        """
        Internal method that handles both regular and local repo reviews.

        Emits events via event_callback:
        - {"type": "status", "state": "started", "message": "..."}
        - {"type": "status", "state": "mcp_initialized", "message": "..."}
        - {"type": "progress", "step": N, "max_steps": M, "message": "..."}
        - {"type": "mcp_output", "content": "...", "step": N}
        - {"type": "final", "result": {...}}
        - {"type": "error", "message": "..."}
        """
        jar_path = self.default_jar_path
        if not os.path.exists(jar_path):
            error_msg = f"MCP server jar not found at path: {jar_path}"
            self._emit_event(event_callback, {"type": "error", "message": error_msg})
            return {"error": error_msg}

        try:
            # Emit initial status
            context = "local repo" if repo_path else "remote repo"
            self._emit_event(event_callback, {
                "type": "status",
                "state": "started",
                "message": f"Analysis starting ({context})"
            })

            # Build configuration
            jvm_props = self._build_jvm_props(request, max_allowed_tokens)
            if repo_path:
                jvm_props["local.repo.path"] = repo_path

            config = MCPConfigBuilder.build_config(jar_path, jvm_props)

            # Create MCP client
            self._emit_event(event_callback, {
                "type": "status",
                "state": "mcp_initializing",
                "message": "Initializing MCP server"
            })
            client = self._create_mcp_client(config)

            # Create LLM instance
            llm = self._create_llm(request)

            # Fetch RAG context if enabled
            rag_context = await self._fetch_rag_context(request, event_callback)

            # Build prompt
            pr_metadata = self._build_pr_metadata(request)
            prompt = self._build_prompt(request, pr_metadata, rag_context)

            self._emit_event(event_callback, {
                "type": "status",
                "state": "mcp_initialized",
                "message": "MCP server ready, starting analysis"
            })

            # Execute review with streaming
            result = await self._execute_review_with_streaming(
                llm=llm,
                client=client,
                prompt=prompt,
                event_callback=event_callback
            )

            # Emit final event
            self._emit_event(event_callback, {
                "type": "final",
                "result": "MCP Agent has completed processing the Pull Request, report is being generated..."
            })

            return {"result": result}

        except Exception as e:
            error_response = ResponseParser.create_error_response(
                f"Agent execution failed ({context})", str(e)
            )
            self._emit_event(event_callback, {
                "type": "error",
                "message": str(e)
            })
            return {"result": error_response}

    async def _execute_review_with_streaming(
            self,
            llm,
            client: MCPClient,
            prompt: str,
            event_callback: Optional[Callable[[Dict], None]]
    ) -> Dict[str, Any]:
        """
        Execute the code review using MCP agent with streaming output.

        This method captures MCP agent intermediate outputs and forwards them
        via event_callback.
        """
        additional_instructions = PromptBuilder.get_additional_instructions()

        # Create agent with streaming callback
        agent = MCPAgent(
            llm=llm,
            client=client,
            max_steps=120,
            additional_instructions=additional_instructions,
        )

        # Track steps for progress reporting
        step_count = 0
        max_steps = 120

        # Wrapper to capture agent output if possible
        # Note: mcp_use library may not support streaming yet, but we can try
        # to hook into it or poll for updates
        try:
            # If MCPAgent supports streaming, we could do:
            # result = await agent.run(prompt, on_step=lambda s: self._on_agent_step(s, event_callback))

            # For now, we'll execute normally and emit progress updates
            self._emit_event(event_callback, {
                "type": "progress",
                "step": 0,
                "max_steps": max_steps,
                "message": "Agent execution started"
            })

            raw_result = None
            agent_exception = None

            # Define the long-running agent task
            async def run_agent_task():
                nonlocal raw_result, agent_exception
                try:
                    raw_result = await agent.run(prompt)
                except Exception as e:
                    agent_exception = e

            # Start the agent task in the background
            agent_task = asyncio.create_task(run_agent_task())
            step_count = 1  # We are at step 1 of 120

            while not agent_task.done():
                # Wait for 30 seconds or until the task finishes
                await asyncio.sleep(5)

                # If task is still running, send a heartbeat
                if not agent_task.done():
                    # Clamp step count to not exceed max_steps-1
                    current_step = min(step_count, max_steps - 1)

                    self._emit_event(event_callback, {
                        "type": "progress",
                        "step": current_step,
                        "max_steps": max_steps,
                        "message": "Analysis in progress..."
                    })
                    step_count += 1  # Increment for next heartbeat

            # Task is done, check for errors
            if agent_exception:
                self._emit_event(event_callback, {
                    "type": "error",
                    "message": f"Agent execution error: {str(agent_exception)}"
                })
                raise agent_exception

            # Run the agent
            #raw_result = await agent.run(prompt)

            # Emit completion progress
            self._emit_event(event_callback, {
                "type": "progress",
                "step": max_steps,
                "max_steps": max_steps,
                "message": "Agent execution completed"
            })

            # Log the raw result for debugging
            if raw_result:
                result_preview = raw_result[:500] if len(raw_result) > 500 else raw_result
                print(f"Agent raw result preview: {result_preview}")
            else:
                print("Agent returned empty or None result")

            # Parse and return result
            return ResponseParser.extract_json_from_response(raw_result)

        except Exception as e:
            self._emit_event(event_callback, {
                "type": "error",
                "message": f"Agent execution error: {str(e)}"
            })
            raise

    def _build_jvm_props(
            self,
            request: ReviewRequestDto,
            max_allowed_tokens: Optional[int]
    ) -> Dict[str, str]:
        """Build JVM properties from request."""
        return MCPConfigBuilder.build_jvm_props(
            request.projectId,
            request.pullRequestId,
            request.projectVcsWorkspace,
            request.projectVcsRepoSlug,
            request.oAuthClient,
            request.oAuthSecret,
            request.accessToken,
            request.maxAllowedTokens or max_allowed_tokens
        )

    async def _fetch_rag_context(
            self,
            request: ReviewRequestDto,
            event_callback: Optional[Callable[[Dict], None]]
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch relevant context from RAG pipeline.

        Returns:
            Dict with RAG context or None if RAG is disabled/failed
        """
        try:
            self._emit_event(event_callback, {
                "type": "status",
                "state": "rag_querying",
                "message": "Fetching relevant code context from RAG"
            })

            # Use changed files and diff snippets from pipeline-agent
            changed_files = request.changedFiles or []
            diff_snippets = request.diffSnippets or []

            rag_response = await self.rag_client.get_pr_context(
                workspace=request.projectWorkspace,
                project=request.projectNamespace,
                branch=request.targetBranchName,
                changed_files=changed_files,
                diff_snippets=diff_snippets,
                pr_title=request.prTitle,
                pr_description=request.prDescription,
                top_k=10
            )

            if rag_response and rag_response.get("context"):
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "rag_retrieved",
                    "message": f"Retrieved relevant context from RAG"
                })
                return rag_response.get("context")

            return None

        except Exception as e:
            logger.warning(f"Failed to fetch RAG context: {e}")
            self._emit_event(event_callback, {
                "type": "status",
                "state": "rag_skipped",
                "message": "RAG context retrieval skipped (non-critical)"
            })
            return None

    def _build_prompt(
            self,
            request: ReviewRequestDto,
            pr_metadata: Dict[str, Any],
            rag_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Build the appropriate prompt based on previous analysis and RAG context."""
        analysis_type = request.analysisType
        has_previous_analysis = bool(request.previousCodeAnalysisIssues)

        if analysis_type is not None and analysis_type == "BRANCH_ANALYSIS":
            return PromptBuilder.build_branch_review_prompt_with_branch_issues_data(pr_metadata)

        if has_previous_analysis:
            return PromptBuilder.build_review_prompt_with_previous_analysis_data(pr_metadata, rag_context)
        else:
            return PromptBuilder.build_first_review_prompt(pr_metadata, rag_context)

    def _create_mcp_client(self, config: Dict[str, Any]) -> MCPClient:
        """Create MCP client from configuration."""
        try:
            return MCPClient.from_dict(config)
        except Exception as e:
            raise Exception(f"Failed to construct MCPClient: {str(e)}")

    def _create_llm(self, request: ReviewRequestDto):
        """Create LLM instance from request parameters."""
        try:
            return LLMFactory.create_llm(
                request.aiModel,
                request.aiProvider,
                request.aiApiKey
            )
        except Exception as e:
            raise Exception(f"Failed to create LLM instance: {str(e)}")

    def _build_pr_metadata(self, request: ReviewRequestDto) -> Dict[str, Any]:
        """Build pull request metadata dictionary from request."""
        return {
            "branch": request.targetBranchName,
            "commitHash": request.commitHash,
            "pullRequestId": request.pullRequestId,
            "repoSlug": request.projectVcsRepoSlug,
            "workspace": request.projectVcsWorkspace,
            "previousCodeAnalysisIssues": [
                issue.dict(by_alias=True, exclude_none=True)
                for issue in (request.previousCodeAnalysisIssues or [])
            ]
        }

    @staticmethod
    def _emit_event(callback: Optional[Callable[[Dict], None]], event: Dict[str, Any]) -> None:
        """Safely emit an event via the callback."""
        if callback:
            try:
                callback(event)
            except Exception as e:
                # Don't let callback errors break the processing
                print(f"Warning: Event callback failed: {e}")