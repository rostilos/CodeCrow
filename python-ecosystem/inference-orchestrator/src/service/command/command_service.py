"""
Service for handling CodeCrow commands (summarize, ask) with AI and MCP integration.
"""

import asyncio
import logging
import os
from typing import Dict, Any, Optional, Callable
from dotenv import load_dotenv
from utils.mcp_runtime import configure_mcp_runtime

configure_mcp_runtime()

from mcp_use import MCPClient
from utils.mcp_tool_serialization import (
    install_per_connection_tool_serialization,
)

from model.dtos import SummarizeRequestDto, AskRequestDto
from model.output_schemas import SummarizeOutput, AskOutput
from service.command import results as command_results
from service.command.ask_context import AskContextPreparer
from service.command.execution import execute_command
from service.command.prompts import build_ask_prompt, build_summarize_prompt
from service.command.input_budget import (
    COMMAND_INPUT_TOKEN_TARGET,
    COMMAND_MAX_OUTPUT_TOKENS,
    CommandInputLimitError,
    _CommandProviderInputGuard,
    _assert_command_input_fits,
    _command_input_token_budget,
    _estimated_command_input_tokens,
)
from service.agent import (
    AgentExecutionRequest,
)
from llm.reasoning_policy import ReasoningEffort
from utils.mcp_config import MCPConfigBuilder
from llm.llm_factory import LLMFactory
from service.rag.rag_client import RagClient
from utils.error_sanitizer import create_user_friendly_error

logger = logging.getLogger(__name__)


SUMMARIZE_ALLOWED_MCP_TOOLS = frozenset({
    "getPullRequest",
    "getPullRequestDiff",
    "getPullRequestCommits",
    "getBranchFileContent",
})

ASK_ALLOWED_MCP_TOOLS = frozenset({
    "getRepository",
    "getPullRequest",
    "getPullRequestActivity",
    "getPullRequestComments",
    "getPullRequestDiff",
    "getPullRequestCommits",
    "getRepositoryBranchingModel",
    "getRepositoryBranchingModelSettings",
    "getEffectiveRepositoryBranchingModel",
    "getProjectBranchingModel",
    "getProjectBranchingModelSettings",
    "getBranchFileContent",
    "getRootDirectory",
    "getDirectoryByPath",
    "getAnalysisResults",
    "getIssueDetails",
    "listProjectAnalyses",
    "searchIssues",
})


def _search_response_error(response: Any) -> Optional[str]:
    if not isinstance(response, dict) or response.get("status") != "error":
        return None
    detail = response.get("error") or response.get("detail") or "unknown failure"
    status_code = response.get("status_code")
    return f"status={status_code} detail={detail}" if status_code else str(detail)


class CommandService:
    """Service class for handling CodeCrow commands with AI integration."""

    # Maximum agent steps for commands (lower than full review)
    MAX_STEPS_SUMMARIZE = 30
    MAX_STEPS_ASK = 40

    # Hard timeout ceiling for commands (seconds). Configurable via .env
    COMMAND_TIMEOUT_SECONDS = int(os.environ.get("COMMAND_TIMEOUT_SECONDS", "600"))

    def __init__(self):
        load_dotenv(interpolate=False)
        self.default_jar_path = os.environ.get(
            "MCP_SERVER_JAR",
            "/app/codecrow-vcs-mcp-1.0.jar"
        )
        self.rag_client = RagClient()
        self.ask_context = AskContextPreparer(
            build_prompt=self._build_ask_prompt, emit_event=self._emit_event,
        )

    async def process_summarize(
            self,
            request: SummarizeRequestDto,
            event_callback: Optional[Callable[[Dict], None]] = None
    ) -> Dict[str, Any]:
        """
        Process a summarize command request.

        Args:
            request: The summarize request data
            event_callback: Optional callback to receive progress events

        Returns:
            Dict with "summary", "diagram", "diagramType" keys or "error"
        """
        jar_path = self.default_jar_path
        if not os.path.exists(jar_path):
            error_msg = f"MCP server jar not found at path: {jar_path}"
            self._emit_event(event_callback, {"type": "error", "message": error_msg})
            return {"error": error_msg}

        try:
            async with asyncio.timeout(self.COMMAND_TIMEOUT_SECONDS):
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "started",
                    "message": "Starting PR summarization"
                })

                # Build configuration
                jvm_props = self._build_jvm_props_for_summarize(request)
                            
                config = MCPConfigBuilder.build_config(jar_path, jvm_props)

                # Create MCP client and LLM
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "mcp_initializing",
                    "message": "Initializing MCP server"
                })
                client = self._create_mcp_client(config)
                llm = self._create_llm(request)
                input_token_budget = _command_input_token_budget(request)

                # Build prompt
                prompt = self._build_summarize_prompt(request)

                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "generating",
                    "message": "Generating PR summary with AI"
                })

                # Execute with MCP agent
                # TODO: Mermaid diagrams disabled for now - AI-generated Mermaid often has syntax errors
                # that fail to render on GitHub. Using ASCII diagrams until we add validation/fixing.
                # Original: supports_mermaid=request.supportsMermaid
                try:
                    result = await self._execute_summarize(
                        llm=llm,
                        client=client,
                        prompt=prompt,
                        supports_mermaid=False,  # Mermaid disabled - always use ASCII
                        event_callback=event_callback,
                        input_token_budget=input_token_budget,
                    )
                finally:
                    # Always close MCP sessions to release JVM subprocesses
                    try:
                        await client.close_all_sessions()
                    except Exception as close_err:
                        logger.warning(f"Error closing MCP sessions: {close_err}")

                result = command_results.normalize_summarize_result(result, supports_mermaid=False)
                if "error" in result:
                    logger.error("Summarize failed: %s", result["error"])
                    self._emit_event(event_callback, {"type": "error", "message": result["error"]})
                    return result

                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "completed",
                    "message": "Summary generated successfully"
                })

                return result

        except TimeoutError:
            timeout_msg = f"Summarize command timed out after {self.COMMAND_TIMEOUT_SECONDS} seconds"
            logger.error(timeout_msg)
            self._emit_event(event_callback, {"type": "error", "message": timeout_msg})
            return {"error": timeout_msg}

        except CommandInputLimitError as error:
            self._emit_event(event_callback, {
                "type": "error",
                "state": "input_limit_exceeded",
                "message": str(error),
            })
            return {"error": str(error)}

        except Exception as e:
            logger.error(f"Summarize failed: {str(e)}", exc_info=True)
            sanitized_msg = create_user_friendly_error(e)
            self._emit_event(event_callback, {"type": "error", "message": sanitized_msg})
            return {"error": sanitized_msg}

    async def process_ask(
            self,
            request: AskRequestDto,
            event_callback: Optional[Callable[[Dict], None]] = None
    ) -> Dict[str, Any]:
        """
        Process an ask command request with Platform MCP integration.

        Args:
            request: The ask request data
            event_callback: Optional callback to receive progress events

        Returns:
            Dict with "answer" key or "error"
        """
        jar_path = self.default_jar_path
        if not os.path.exists(jar_path):
            error_msg = f"MCP server jar not found at path: {jar_path}"
            self._emit_event(event_callback, {"type": "error", "message": error_msg})
            return {"error": error_msg}
        
        # Platform MCP JAR path
        platform_mcp_jar = os.environ.get(
            "PLATFORM_MCP_JAR",
            "/app/codecrow-platform-mcp-1.0.jar"
        )

        try:
            async with asyncio.timeout(self.COMMAND_TIMEOUT_SECONDS):
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "started",
                    "message": "Processing your question"
                })

                # Build configuration with both VCS and Platform MCP servers
                jvm_props = self._build_jvm_props_for_ask(request)
                
                # Platform MCP needs database connection info
                platform_jvm_props = self._build_platform_jvm_props(request)
                
                # Include Platform MCP if the JAR exists
                include_platform = os.path.exists(platform_mcp_jar)
                if include_platform:
                    logger.info("Including Platform MCP server for ASK command")
                
                config = MCPConfigBuilder.build_config(
                    jar_path, 
                    jvm_props,
                    include_platform_mcp=include_platform,
                    platform_mcp_jar_path=platform_mcp_jar,
                    platform_jvm_props=platform_jvm_props
                )

                # Create MCP client and LLM
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "mcp_initializing",
                    "message": "Initializing MCP servers"
                })
                client = self._create_mcp_client(config)
                llm = self._create_llm(request)
                input_token_budget = _command_input_token_budget(request)

                code_matches = await self._search_code_for_ask(
                    request,
                    event_callback,
                )

                # Preserve a one-path request when the complete prompt fits.
                # Only oversized supplied evidence enters lossless hierarchical
                # synthesis; no raw context is sliced.
                prompt = await self.ask_context.prepare(
                    request,
                    code_matches,
                    has_platform_mcp=include_platform,
                    llm=llm,
                    input_token_budget=input_token_budget,
                    event_callback=event_callback,
                )

                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "generating",
                    "message": "Generating answer with AI"
                })

                # Execute with MCP agent
                try:
                    result = await self._execute_ask(
                        llm=llm,
                        client=client,
                        prompt=prompt,
                        event_callback=event_callback,
                        input_token_budget=input_token_budget,
                    )
                finally:
                    # Always close MCP sessions to release JVM subprocesses
                    try:
                        await client.close_all_sessions()
                    except Exception as close_err:
                        logger.warning(f"Error closing MCP sessions: {close_err}")

                result = command_results.normalize_ask_result(result)
                if "error" in result:
                    logger.error("Ask failed: %s", result["error"])
                    self._emit_event(event_callback, {"type": "error", "message": result["error"]})
                    return result

                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "completed",
                    "message": "Answer generated successfully"
                })

                return result

        except TimeoutError:
            timeout_msg = f"Ask command timed out after {self.COMMAND_TIMEOUT_SECONDS} seconds"
            logger.error(timeout_msg)
            self._emit_event(event_callback, {"type": "error", "message": timeout_msg})
            return {"error": timeout_msg}

        except CommandInputLimitError as error:
            self._emit_event(event_callback, {
                "type": "error",
                "state": "input_limit_exceeded",
                "message": str(error),
            })
            return {"error": str(error)}

        except Exception as e:
            logger.error(f"Ask failed: {str(e)}", exc_info=True)
            sanitized_msg = create_user_friendly_error(e)
            self._emit_event(event_callback, {"type": "error", "message": sanitized_msg})
            return {"error": sanitized_msg}

    def _build_platform_jvm_props(self, request) -> Dict[str, str]:
        """Build JVM properties for Platform MCP server (API + VCS access)."""
        props = {
            "api.base.url": os.environ.get("CODECROW_API_URL", "http://codecrow-web-application:8081"),
            "project.id": str(request.projectId) if request.projectId else "",
            "internal.api.secret": os.environ.get("INTERNAL_API_SECRET", ""),
        }
        
        # Include VCS credentials for PR data/diff access
        if hasattr(request, 'pullRequestId') and request.pullRequestId:
            props["pullRequest.id"] = str(request.pullRequestId)
        if hasattr(request, 'projectVcsWorkspace') and request.projectVcsWorkspace:
            props["workspace"] = request.projectVcsWorkspace
        if hasattr(request, 'projectVcsRepoSlug') and request.projectVcsRepoSlug:
            props["repo.slug"] = request.projectVcsRepoSlug
        if hasattr(request, 'accessToken') and request.accessToken:
            props["accessToken"] = request.accessToken
        elif hasattr(request, 'oAuthClient') and request.oAuthClient:
            props["oAuthClient"] = request.oAuthClient
            if hasattr(request, 'oAuthSecret') and request.oAuthSecret:
                props["oAuthSecret"] = request.oAuthSecret
        if hasattr(request, 'vcsProvider') and request.vcsProvider:
            props["vcs.provider"] = request.vcsProvider
        if hasattr(request, 'vcsBaseUrl') and request.vcsBaseUrl:
            props["vcs.baseUrl"] = request.vcsBaseUrl
        
        return props

    def _build_jvm_props_for_summarize(self, request: SummarizeRequestDto) -> Dict[str, str]:
        """Build JVM properties for summarize request."""
        return MCPConfigBuilder.build_jvm_props(
            project_id=request.projectId,
            pull_request_id=request.pullRequestId,
            workspace=request.projectVcsWorkspace,
            repo_slug=request.projectVcsRepoSlug,
            oAuthClient=request.oAuthClient,
            oAuthSecret=request.oAuthSecret,
            access_token=request.accessToken,
            max_allowed_tokens=request.maxAllowedTokens,
            vcs_provider=request.vcsProvider,
            vcs_base_url=request.vcsBaseUrl,
        )

    def _build_jvm_props_for_ask(self, request: AskRequestDto) -> Dict[str, str]:
        """Build JVM properties for ask request."""
        return MCPConfigBuilder.build_jvm_props(
            project_id=request.projectId,
            pull_request_id=request.pullRequestId,
            workspace=request.projectVcsWorkspace,
            repo_slug=request.projectVcsRepoSlug,
            oAuthClient=request.oAuthClient,
            oAuthSecret=request.oAuthSecret,
            access_token=request.accessToken,
            max_allowed_tokens=request.maxAllowedTokens,
            vcs_provider=request.vcsProvider,
            vcs_base_url=request.vcsBaseUrl,
        )

    async def _search_code_for_ask(
            self,
            request: AskRequestDto,
            event_callback: Optional[Callable[[Dict], None]]
    ) -> Optional[Dict[str, Any]]:
        """Fetch optional deterministic code matches for an Ask question."""
        binding = (
            request.branch,
            request.repositoryRevision,
            request.ragGenerationManifestSha256,
            request.ragCollectionTarget,
        )
        if not all(
            isinstance(value, str) and bool(value.strip())
            for value in binding
        ):
            logger.info(
                "Deterministic code search skipped because Ask has no complete "
                "repository-generation binding"
            )
            self._emit_event(event_callback, {
                "type": "status",
                "state": "code_search_skipped",
                "message": (
                    "Repository search has no sealed generation binding; "
                    "exact source tools remain active"
                ),
            })
            return None

        try:
            self._emit_event(event_callback, {
                "type": "status",
                "state": "code_search_querying",
                "message": "Searching repository symbols and source text"
            })

            search_response = await self.rag_client.search_code(
                workspace=request.projectWorkspace,
                project=request.projectNamespace,
                query=request.question,
                branch=request.branch,
                repository_revision=request.repositoryRevision,
                repository_generation_manifest_sha256=(
                    request.ragGenerationManifestSha256
                ),
                collection_target=request.ragCollectionTarget,
            )

            if search_error := _search_response_error(search_response):
                logger.info(
                    "Optional deterministic code search unavailable; Ask will "
                    "continue with exact VCS MCP tools: %s",
                    search_error,
                )
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "code_search_skipped",
                    "message": (
                        "Repository search unavailable; exact source tools remain active"
                    ),
                })
                return None

            if search_response and search_response.get("results"):
                coverage = (
                    search_response.get("coverage")
                    if isinstance(search_response.get("coverage"), dict)
                    else {}
                )
                partial = coverage.get("complete") is not True
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": (
                        "code_search_partial" if partial
                        else "code_search_retrieved"
                    ),
                    "message": (
                        "Repository search reached an observable safety limit; "
                        "all returned exact matches will be analyzed"
                        if partial
                        else "Found complete deterministic repository matches"
                    ),
                })
                return search_response

            return None

        except Exception as e:
            logger.info(
                "Optional code search failed; Ask will continue with exact VCS "
                "MCP tools: %s",
                e,
            )
            return None

    def _build_summarize_prompt(self, request: SummarizeRequestDto) -> str:
        return build_summarize_prompt(request, max_steps=self.MAX_STEPS_SUMMARIZE)

    def _build_ask_prompt(
        self,
        request: AskRequestDto,
        code_matches: Optional[Any],
        has_platform_mcp: bool = False,
        context_section_override: Optional[str] = None,
    ) -> str:
        return build_ask_prompt(
            request,
            code_matches,
            max_steps=self.MAX_STEPS_ASK,
            has_platform_mcp=has_platform_mcp,
            context_section_override=context_section_override,
        )

    async def _execute_summarize(
        self, llm, client: MCPClient, prompt: str, supports_mermaid: bool,
        event_callback: Optional[Callable[[Dict], None]],
        input_token_budget: Optional[int] = None,
    ) -> Dict[str, Any]:
        request = AgentExecutionRequest(
            prompt=prompt,
            allowed_tool_names=SUMMARIZE_ALLOWED_MCP_TOOLS,
            max_steps=self.MAX_STEPS_SUMMARIZE,
            reasoning_effort=ReasoningEffort.LOW,
            max_output_tokens=COMMAND_MAX_OUTPUT_TOKENS,
            output_schema=SummarizeOutput,
            additional_instructions=(
                "CRITICAL: Your response MUST be a valid JSON object with 'summary', 'diagram', and 'diagramType' fields.\n"
                "Do NOT include any text outside the JSON object.\n"
                f"For diagramType, use {'MERMAID' if supports_mermaid else 'ASCII'}."
            ),
            metadata={"flow": "command", "command": "summarize"},
        )
        return await execute_command(
            llm=llm, client=client, request=request,
            input_token_budget=input_token_budget or COMMAND_INPUT_TOKEN_TARGET,
            coerce_result=lambda output: command_results.coerce_summarize_final_result(output, supports_mermaid),
            emit_event=lambda event: self._emit_event(event_callback, event),
            start_message="Starting summarization",
            completion_message="Summarization completed",
            fallback_instruction=(
                "If tool calls are unavailable, summarize from the context already provided. "
                "Return a JSON object with non-empty 'summary', 'diagram', and 'diagramType' fields."
            ),
        )

    async def _execute_ask(
        self, llm, client: MCPClient, prompt: str,
        event_callback: Optional[Callable[[Dict], None]],
        input_token_budget: Optional[int] = None,
    ) -> Dict[str, Any]:
        request = AgentExecutionRequest(
            prompt=prompt,
            allowed_tool_names=ASK_ALLOWED_MCP_TOOLS,
            max_steps=self.MAX_STEPS_ASK,
            reasoning_effort=ReasoningEffort.LOW,
            max_output_tokens=COMMAND_MAX_OUTPUT_TOKENS,
            output_schema=AskOutput,
            additional_instructions=(
                "CRITICAL: Your response MUST be a valid JSON object with an 'answer' field.\n"
                "Do NOT include any text outside the JSON object.\n"
                "The answer should be well-formatted markdown."
            ),
            metadata={"flow": "command", "command": "ask"},
        )
        return await execute_command(
            llm=llm, client=client, request=request,
            input_token_budget=input_token_budget or COMMAND_INPUT_TOKEN_TARGET,
            coerce_result=command_results.coerce_ask_final_result,
            emit_event=lambda event: self._emit_event(event_callback, event),
            start_message="Processing question",
            completion_message="Completed",
            fallback_instruction=(
                "If tool calls are unavailable, answer from the context already provided. "
                "Return a JSON object with a non-empty 'answer' field."
            ),
        )

    def _create_mcp_client(self, config: Dict[str, Any]) -> MCPClient:
        """Create MCP client from configuration."""
        try:
            return install_per_connection_tool_serialization(
                MCPClient.from_dict(config)
            )
        except Exception as e:
            raise Exception(f"Failed to construct MCPClient: {str(e)}")

    def _create_llm(self, request):
        """Create LLM instance from request parameters."""
        try:
            return LLMFactory.create_llm(
                request.aiModel,
                request.aiProvider,
                request.aiApiKey,
                ai_base_url=getattr(request, 'aiBaseUrl', None),
                max_tokens=COMMAND_MAX_OUTPUT_TOKENS,
            )
        except Exception as e:
            raise Exception(f"Failed to create LLM instance: {str(e)}")

    @staticmethod
    def _emit_event(callback: Optional[Callable[[Dict], None]], event: Dict[str, Any]) -> None:
        """Safely emit an event via the callback."""
        if callback:
            try:
                callback(event)
            except Exception as e:
                logger.warning(f"Event callback failed: {e}")
