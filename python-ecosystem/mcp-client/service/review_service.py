import os
import asyncio
import logging
from typing import Dict, Any, Optional, Callable
from dotenv import load_dotenv
from mcp_use import MCPAgent, MCPClient

from model.models import ReviewRequestDto
from utils.mcp_config import MCPConfigBuilder
from llm.llm_factory import LLMFactory
from utils.prompt_builder import PromptBuilder
from utils.response_parser import ResponseParser
from service.rag_client import RagClient

logger = logging.getLogger(__name__)

class ReviewService:
    """Service class for handling code review requests with streaming support."""
    
    # Maximum retries for LLM-based response fixing
    MAX_FIX_RETRIES = 2

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
                logger.info(f"Agent raw result preview: {result_preview}")
            else:
                logger.warning("Agent returned empty or None result")

            # Parse the result
            ResponseParser.reset_retry_state()
            parsed_result = ResponseParser.extract_json_from_response(raw_result)
            
            # Check if parsing failed and we have raw content to try fixing
            if parsed_result.get("_needs_retry") and raw_result:
                logger.info("Initial parsing failed, attempting LLM-based fix...")
                self._emit_event(event_callback, {
                    "type": "progress",
                    "step": max_steps,
                    "max_steps": max_steps,
                    "message": "Attempting to fix response format..."
                })
                
                fixed_result = await self._try_fix_response_with_llm(
                    raw_result, 
                    llm, 
                    event_callback
                )
                if fixed_result:
                    # Remove internal tracking fields
                    fixed_result.pop("_needs_retry", None)
                    fixed_result.pop("_raw_response", None)
                    return fixed_result
            
            # Remove internal tracking fields
            parsed_result.pop("_needs_retry", None)
            parsed_result.pop("_raw_response", None)
            return parsed_result

        except Exception as e:
            self._emit_event(event_callback, {
                "type": "error",
                "message": f"Agent execution error: {str(e)}"
            })
            raise
    
    async def _try_fix_response_with_llm(
            self,
            raw_response: str,
            llm,
            event_callback: Optional[Callable[[Dict], None]]
    ) -> Optional[Dict[str, Any]]:
        """
        Try to fix a malformed response using a direct LLM call.
        
        Args:
            raw_response: The raw response that failed to parse
            llm: The LLM instance to use for fixing
            event_callback: Optional callback for progress events
            
        Returns:
            Fixed and parsed response, or None if all retries failed
        """
        fix_prompt = ResponseParser.get_fix_prompt(raw_response)
        
        for attempt in range(self.MAX_FIX_RETRIES):
            try:
                logger.info(f"LLM fix attempt {attempt + 1}/{self.MAX_FIX_RETRIES}")
                self._emit_event(event_callback, {
                    "type": "progress",
                    "step": 0,
                    "max_steps": self.MAX_FIX_RETRIES,
                    "message": f"Fix attempt {attempt + 1}/{self.MAX_FIX_RETRIES}..."
                })
                
                # Make a simple direct LLM call (no MCP tools)
                response = await llm.ainvoke(fix_prompt)
                
                # Extract the content from the response
                if hasattr(response, 'content'):
                    fixed_text = response.content
                else:
                    fixed_text = str(response)
                
                logger.info(f"LLM fix response preview: {fixed_text[:300] if len(fixed_text) > 300 else fixed_text}")
                
                # Try to parse the fixed response
                # Don't track retry state for the fix attempts
                ResponseParser._last_parse_needs_retry = False
                fixed_result = ResponseParser.extract_json_from_response(fixed_text)
                
                # Check if parsing succeeded (no error markers)
                if not fixed_result.get("_needs_retry"):
                    if "comment" in fixed_result and "issues" in fixed_result:
                        # Validate it's not an error response
                        comment = fixed_result.get("comment", "")
                        if not comment.startswith("Failed to parse"):
                            logger.info(f"LLM fix succeeded on attempt {attempt + 1}")
                            self._emit_event(event_callback, {
                                "type": "status",
                                "state": "fix_succeeded",
                                "message": f"Response fixed successfully on attempt {attempt + 1}"
                            })
                            return fixed_result
                
                logger.warning(f"LLM fix attempt {attempt + 1} produced invalid result")
                
            except Exception as e:
                logger.error(f"LLM fix attempt {attempt + 1} failed with error: {e}")
                self._emit_event(event_callback, {
                    "type": "warning",
                    "message": f"Fix attempt {attempt + 1} failed: {str(e)}"
                })
        
        logger.error(f"All {self.MAX_FIX_RETRIES} LLM fix attempts failed")
        self._emit_event(event_callback, {
            "type": "warning",
            "message": f"Failed to fix response after {self.MAX_FIX_RETRIES} attempts"
        })
        return None

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