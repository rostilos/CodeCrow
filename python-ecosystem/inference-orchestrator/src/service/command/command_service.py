"""
Service for handling CodeCrow commands (summarize, ask) with AI and MCP integration.
"""

import os
import asyncio
import logging
from typing import Dict, Any, Optional, Callable
from dotenv import load_dotenv
from mcp_use import MCPAgent, MCPClient
from langchain_core.agents import AgentAction

from model.dtos import SummarizeRequestDto, AskRequestDto
from model.output_schemas import SummarizeOutput, AskOutput
from utils.mcp_config import MCPConfigBuilder
from llm.llm_factory import LLMFactory
from service.rag.rag_client import RagClient
from utils.error_sanitizer import sanitize_error_for_display, create_user_friendly_error

logger = logging.getLogger(__name__)


class CommandService:
    """Service class for handling CodeCrow commands with AI integration."""

    # Maximum agent steps for commands (lower than full review)
    MAX_STEPS_SUMMARIZE = 30
    MAX_STEPS_ASK = 40

    # Hard timeout ceiling for commands (seconds). Configurable via .env
    COMMAND_TIMEOUT_SECONDS = int(os.environ.get("COMMAND_TIMEOUT_SECONDS", "600"))

    EMPTY_RESULT_SENTINELS = {
        "null",
        "none",
        "no output generated",
        "failed to generate summary",
        "i couldn't generate an answer. please try rephrasing your question.",
    }

    def __init__(self):
        load_dotenv(interpolate=False)
        self.default_jar_path = os.environ.get(
            "MCP_SERVER_JAR",
            "/app/codecrow-vcs-mcp-1.0.jar"
        )
        self.rag_client = RagClient()

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

                # Fetch RAG context
                rag_context = await self._fetch_rag_context_for_summarize(request, event_callback)

                # Build prompt
                prompt = self._build_summarize_prompt(request, rag_context)

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
                        event_callback=event_callback
                    )
                finally:
                    # Always close MCP sessions to release JVM subprocesses
                    try:
                        await client.close_all_sessions()
                    except Exception as close_err:
                        logger.warning(f"Error closing MCP sessions: {close_err}")

                result = self._normalize_summarize_result(result, supports_mermaid=False)
                if "error" in result:
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

                # Fetch RAG context for the question
                rag_context = await self._fetch_rag_context_for_ask(request, event_callback)

                # Build prompt with Platform MCP tools if available
                prompt = self._build_ask_prompt(request, rag_context, has_platform_mcp=include_platform)

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
                        event_callback=event_callback
                    )
                finally:
                    # Always close MCP sessions to release JVM subprocesses
                    try:
                        await client.close_all_sessions()
                    except Exception as close_err:
                        logger.warning(f"Error closing MCP sessions: {close_err}")

                result = self._normalize_ask_result(result)
                if "error" in result:
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

        except Exception as e:
            logger.error(f"Ask failed: {str(e)}", exc_info=True)
            sanitized_msg = create_user_friendly_error(e)
            self._emit_event(event_callback, {"type": "error", "message": sanitized_msg})
            return {"error": sanitized_msg}

    def _normalize_summarize_result(self, result: Any, supports_mermaid: bool) -> Dict[str, Any]:
        """Validate summarize output before the queue consumer publishes a final event."""
        if not isinstance(result, dict):
            return {"error": "AI service returned an invalid summarize result"}
        if result.get("error"):
            return {"error": str(result["error"])}

        summary = result.get("summary")
        if not self._has_usable_text(summary):
            return {"error": "AI service returned an empty summary"}

        diagram_type = result.get("diagramType") or ("MERMAID" if supports_mermaid else "ASCII")
        return {
            "summary": str(summary),
            "diagram": self._string_or_empty(result.get("diagram")),
            "diagramType": str(diagram_type),
        }

    def _normalize_ask_result(self, result: Any) -> Dict[str, Any]:
        """Validate ask output before the queue consumer publishes a final event."""
        if not isinstance(result, dict):
            return {"error": "AI service returned an invalid ask result"}
        if result.get("error"):
            return {"error": str(result["error"])}

        answer = result.get("answer")
        if not self._has_usable_text(answer):
            return {"error": "AI service returned an empty answer"}

        return {"answer": str(answer)}

    @classmethod
    def _has_usable_text(cls, value: Any) -> bool:
        if value is None:
            return False
        text = str(value).strip()
        return bool(text) and text.lower() not in cls.EMPTY_RESULT_SENTINELS

    @staticmethod
    def _string_or_empty(value: Any) -> str:
        return "" if value is None else str(value)

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
            vcs_provider=request.vcsProvider
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
            vcs_provider=request.vcsProvider
        )

    async def _fetch_rag_context_for_summarize(
            self,
            request: SummarizeRequestDto,
            event_callback: Optional[Callable[[Dict], None]]
    ) -> Optional[Dict[str, Any]]:
        """Fetch RAG context for summarization."""
        try:
            self._emit_event(event_callback, {
                "type": "status",
                "state": "rag_querying",
                "message": "Fetching codebase context"
            })

            rag_response = await self.rag_client.get_pr_context(
                workspace=request.projectWorkspace,
                project=request.projectNamespace,
                branch=request.get_rag_branch() or "main",
                changed_files=[],
                diff_snippets=[],
                pr_title=f"PR #{request.pullRequestId}",
                pr_description=None,
                top_k=5,
                base_branch=request.get_rag_base_branch(),
            )

            if rag_response and rag_response.get("context"):
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "rag_retrieved",
                    "message": "Codebase context retrieved"
                })
                return rag_response.get("context")

            return None

        except Exception as e:
            logger.warning(f"Failed to fetch RAG context for summarize: {e}")
            return None

    async def _fetch_rag_context_for_ask(
            self,
            request: AskRequestDto,
            event_callback: Optional[Callable[[Dict], None]]
    ) -> Optional[Dict[str, Any]]:
        """Fetch RAG context for the question."""
        try:
            self._emit_event(event_callback, {
                "type": "status",
                "state": "rag_querying",
                "message": "Searching codebase for relevant context"
            })

            # Use the question as the query for RAG
            rag_response = await self.rag_client.semantic_search(
                workspace=request.projectWorkspace,
                project=request.projectNamespace,
                query=request.question,
                branch="main", # AskRequestDto doesn't have branch, default to main
                top_k=8
            )

            if rag_response and rag_response.get("results"):
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "rag_retrieved",
                    "message": "Found relevant codebase context"
                })
                return rag_response.get("results")

            return None

        except Exception as e:
            logger.warning(f"Failed to fetch RAG context for ask: {e}")
            return None

    def _build_summarize_prompt(
            self,
            request: SummarizeRequestDto,
            rag_context: Optional[Dict[str, Any]]
    ) -> str:
        """Build the prompt for PR summarization."""
        diagram_instruction = ""
        if request.supportsMermaid:
            diagram_instruction = """
6. **Architecture Diagram**: Create a Mermaid flowchart diagram showing the main components and flow affected by this PR.
   Use this format:
   ```mermaid
   graph TD
       A[Component] --> B[Another Component]
   ```
"""
        else:
            diagram_instruction = """
6. **Architecture Diagram**: Create a simple ASCII art diagram showing the main components affected.
   Use this format:
   ```
   +---------------+     +---------------+
   |  Component A  | --> |  Component B  |
   +---------------+     +---------------+
   ```
"""

        rag_section = ""
        if rag_context:
            rag_section = "\n--- RELEVANT CODEBASE CONTEXT ---\n"
            if isinstance(rag_context, list):
                for idx, chunk in enumerate(rag_context[:5], 1):
                    rag_section += f"\nContext {idx}:\n{chunk.get('text', '')}\n"
            elif isinstance(rag_context, dict) and rag_context.get("relevant_code"):
                for idx, chunk in enumerate(rag_context.get("relevant_code", [])[:5], 1):
                    rag_section += f"\nContext {idx} (from {chunk.get('metadata', {}).get('path', 'unknown')}):\n"
                    rag_section += f"{chunk.get('text', '')}\n"
            rag_section += "\n--- END CODEBASE CONTEXT ---\n\n"

        prompt = f"""You are an expert code reviewer and technical writer. Analyze this pull request and provide a concise summary.

## Pull Request Information
- PR Number: #{request.pullRequestId}
- Repository: {request.projectVcsWorkspace}/{request.projectVcsRepoSlug}
- Workspace/Owner: {request.projectVcsWorkspace}
- Repo Slug: {request.projectVcsRepoSlug}
- Source Branch: {request.sourceBranch or "unknown"}
- Target Branch: {request.targetBranch or "unknown"}

**IMPORTANT for MCP tool calls:** When calling tools like `getPullRequestDiff`, `getPullRequest`, etc:
- Use `workspace: "{request.projectVcsWorkspace}"` (NOT the full repository path)
- Use `repoSlug: "{request.projectVcsRepoSlug}"`
- Use `pullRequestId: "{request.pullRequestId}"`

{rag_section}

## Your Task

Use the MCP tools available to you:
1. First, call `getPullRequestDiff` to get the PR changes
2. Optionally call `getFileContent` for key files if needed for context
3. Then generate a summary appropriate to the PR size

## Required Output Format

Your response MUST be a valid JSON object with this exact structure:
{{
    "summary": "The full markdown summary text",
    "diagram": "The diagram code (mermaid or ascii) - use empty string if not needed",
    "diagramType": "MERMAID" or "ASCII"
}}

## Summary Content Requirements - ADAPT TO PR SIZE

For **small PRs** (1-5 files, minor changes):
- Keep it brief - just Overview and Key Changes
- NO diagrams needed
- Skip sections that aren't relevant

For **medium PRs** (5-15 files, significant changes):
- Include Overview, Key Changes, and Impact Analysis
- Only include diagram if it helps understand the change
- Skip Files Modified section if Key Changes covers it

For **large PRs** (15+ files, major changes):
The "summary" field should contain well-formatted markdown with:
1. **📋 Overview**: A 2-3 sentence high-level description
2. **🔑 Key Changes**: Bullet list of the most important changes
3. **📁 Files Modified**: Quick list grouped by type/purpose
4. **⚡ Impact Analysis**: What parts are affected and risks
5. **💡 Recommendations**: Suggestions for the reviewer
{diagram_instruction}

## IMPORTANT RULES
- Be CONCISE - don't pad the summary with unnecessary sections
- Only include a diagram if the PR involves architectural/structural changes
- Do NOT duplicate information between sections
- For trivial changes (typos, minor fixes), keep summary to 2-3 sentences total

## Efficiency Instructions

You have LIMITED steps (max {self.MAX_STEPS_SUMMARIZE}). Be efficient:
1. Get the PR diff first
2. Analyze it directly without fetching every file
3. Produce your JSON response promptly

CRITICAL: Return ONLY the JSON object, no other text or markdown formatting around it.
"""
        return prompt

    def _build_ask_prompt(
            self,
            request: AskRequestDto,
            rag_context: Optional[Any],
            has_platform_mcp: bool = False
    ) -> str:
        """Build the prompt for answering a question."""
        context_section = ""
        
        # Add analysis context if provided
        if request.analysisContext:
            context_section += f"\n--- ANALYSIS CONTEXT ---\n{request.analysisContext}\n--- END ANALYSIS CONTEXT ---\n\n"

        # Add RAG context if available
        if rag_context:
            context_section += "\n--- RELEVANT CODEBASE CONTEXT ---\n"
            if isinstance(rag_context, list):
                for idx, chunk in enumerate(rag_context[:8], 1):
                    if isinstance(chunk, dict):
                        context_section += f"\nContext {idx} (from {chunk.get('path', chunk.get('metadata', {}).get('path', 'unknown'))}):\n"
                        context_section += f"{chunk.get('text', chunk.get('content', ''))}\n"
                    else:
                        context_section += f"\nContext {idx}:\n{chunk}\n"
            context_section += "\n--- END CODEBASE CONTEXT ---\n\n"

        # Add issue references context
        issue_section = ""
        if request.issueReferences:
            if has_platform_mcp:
                issue_section = f"\n## IMPORTANT: Issue References\nThe question references these issues: {', '.join(['#' + ref for ref in request.issueReferences])}\n"
                issue_section += "**YOU MUST USE `getIssueDetails` tool to fetch details about these issues before answering!**\n\n"
            else:
                issue_section = f"\nThe question references these issues: {', '.join(['#' + ref for ref in request.issueReferences])}\n"
                issue_section += "Note: Issue tracking details are not available. Focus on the code changes in the PR to provide relevant insights.\n\n"

        pr_context = ""
        if request.pullRequestId:
            pr_context = f"""
## Pull Request Context
- PR Number: #{request.pullRequestId}
- Repository: {request.projectVcsWorkspace}/{request.projectVcsRepoSlug}
- Workspace/Owner: {request.projectVcsWorkspace}
- Repo Slug: {request.projectVcsRepoSlug}

**IMPORTANT for MCP tool calls:** When calling tools like `getPullRequestDiff`, `getPullRequest`, etc:
- Use `workspace: "{request.projectVcsWorkspace}"` (NOT the full repository path)
- Use `repoSlug: "{request.projectVcsRepoSlug}"`
- Use `pullRequestId: "{request.pullRequestId}"`
"""

        # Build the MCP tools section based on available servers
        platform_tools_section = ""
        if has_platform_mcp:
            platform_tools_section = """
### Platform Tools (for issue/analysis data) - USE THESE FIRST for issue queries:
- `getIssueDetails` - **USE THIS** when user asks about a specific issue (e.g., "issue 312", "#312"). Pass the issue ID as parameter.
- `searchIssues` - Search for issues with filters (severity, category, filePath, query)

**IMPORTANT:** When the question mentions an issue number (like "issue 312" or "#312"), you MUST call `getIssueDetails` with that issue ID first!"""
        else:
            platform_tools_section = "\nUse these tools ONLY if needed to answer the question accurately."

        prompt = f"""You are a helpful code assistant for the CodeCrow platform. Answer the user's question about the codebase or analysis.

## The Question
{request.question}

{pr_context}
{issue_section}
{context_section}

## Available MCP Tools

### VCS Tools (for code access):
- `getPullRequestDiff` - Get changes in a PR
- `getFileContent` - Get content of a specific file
- `getBranchFileContent` - Get file content from a branch
{platform_tools_section}

## Your Task

1. **If the question mentions an issue number, FIRST call `getIssueDetails` to get the issue data**
2. Analyze the question and available context
3. Use additional MCP tools only if necessary
4. Provide a clear, helpful answer

## Required Output Format

Your response MUST be a valid JSON object:
{{
    "answer": "Your detailed markdown-formatted answer here"
}}

## Answer Guidelines

- Be concise but thorough
- Use code blocks for code examples
- Reference specific files and line numbers when relevant
- Format the answer with proper markdown for readability

## Efficiency Instructions

You have LIMITED steps (max {self.MAX_STEPS_ASK}). Be efficient:
1. For issue questions: call `getIssueDetails` first
2. Check if the context already has the answer
3. Only use additional tools if necessary
4. Produce your JSON response promptly

CRITICAL: Return ONLY the JSON object, no other text or markdown formatting around it.
"""
        return prompt

    async def _execute_summarize(
            self,
            llm,
            client: MCPClient,
            prompt: str,
            supports_mermaid: bool,
            event_callback: Optional[Callable[[Dict], None]]
    ) -> Dict[str, Any]:
        """Execute the summarize command with MCP agent using streaming and output_schema."""
        additional_instructions = (
            "CRITICAL: Your response MUST be a valid JSON object with 'summary', 'diagram', and 'diagramType' fields.\n"
            "Do NOT include any text outside the JSON object.\n"
            f"For diagramType, use {'MERMAID' if supports_mermaid else 'ASCII'}."
        )

        agent = MCPAgent(
            llm=llm,
            client=client,
            max_steps=self.MAX_STEPS_SUMMARIZE,
            additional_instructions=additional_instructions,
        )

        try:
            self._emit_event(event_callback, {
                "type": "progress",
                "step": 0,
                "max_steps": self.MAX_STEPS_SUMMARIZE,
                "message": "Starting summarization"
            })

            step_count = 0
            final_result = None

            # Use streaming with output_schema for structured output
            async for item in agent.stream(
                prompt,
                max_steps=self.MAX_STEPS_SUMMARIZE,
                output_schema=SummarizeOutput
            ):
                if isinstance(item, tuple) and len(item) == 2:
                    # Tool call with observation
                    action, observation = item
                    step_count += 1
                    
                    tool_name = action.tool if hasattr(action, 'tool') else str(action)
                    logger.info(f"[Summarize Step {step_count}] Tool: {tool_name}")
                    
                    self._emit_event(event_callback, {
                        "type": "mcp_step",
                        "step": step_count,
                        "max_steps": self.MAX_STEPS_SUMMARIZE,
                        "tool": tool_name,
                        "message": f"Executed tool: {tool_name}"
                    })
                    
                elif isinstance(item, SummarizeOutput):
                    # Final structured output
                    final_result = item
                    logger.info(f"Received structured summarize output")
                    
                elif isinstance(item, str):
                    # Intermediate text output
                    final_result = item

                else:
                    extracted = self._extract_agent_item_text(item)
                    if extracted is not None:
                        final_result = extracted

            self._emit_event(event_callback, {
                "type": "progress",
                "step": self.MAX_STEPS_SUMMARIZE,
                "max_steps": self.MAX_STEPS_SUMMARIZE,
                "message": f"Summarization completed ({step_count} tool calls)"
            })

            result = self._coerce_summarize_final_result(final_result, supports_mermaid)
            if "error" not in result:
                return result

            logger.warning("Summarize streaming produced an empty final summary; retrying without output_schema")
            self._emit_event(event_callback, {
                "type": "status",
                "state": "retrying",
                "message": "Retrying summary generation"
            })

            raw_result = await self._run_agent_with_heartbeat(
                agent=agent,
                prompt=prompt,
                event_callback=event_callback,
                max_steps=self.MAX_STEPS_SUMMARIZE
            )
            result = self._coerce_summarize_final_result(raw_result, supports_mermaid)
            if "error" not in result:
                return result

            logger.warning("Summarize agent retry also produced an empty summary; trying direct LLM fallback")
            direct_response = await llm.ainvoke(
                prompt
                + "\n\nIf tool calls are unavailable, summarize from the context already provided. "
                  "Return a JSON object with non-empty 'summary', 'diagram', and 'diagramType' fields."
            )
            return self._coerce_summarize_final_result(direct_response, supports_mermaid)

        except Exception as e:
            logger.warning(f"Summarize streaming failed, retrying without output_schema: {e}", exc_info=True)
            self._emit_event(event_callback, {
                "type": "status",
                "state": "retrying",
                "message": "Retrying summary generation"
            })
            try:
                raw_result = await self._run_agent_with_heartbeat(
                    agent=agent,
                    prompt=prompt,
                    event_callback=event_callback,
                    max_steps=self.MAX_STEPS_SUMMARIZE
                )
                result = self._coerce_summarize_final_result(raw_result, supports_mermaid)
                if "error" not in result:
                    return result

                direct_response = await llm.ainvoke(
                    prompt
                    + "\n\nIf tool calls are unavailable, summarize from the context already provided. "
                      "Return a JSON object with non-empty 'summary', 'diagram', and 'diagramType' fields."
                )
                return self._coerce_summarize_final_result(direct_response, supports_mermaid)
            except Exception as retry_error:
                logger.error(f"Summarize agent error: {retry_error}", exc_info=True)
                sanitized_msg = create_user_friendly_error(retry_error)
                return {"error": sanitized_msg}

    def _coerce_summarize_final_result(
            self,
            final_result: Any,
            supports_mermaid: bool
    ) -> Dict[str, Any]:
        """Convert structured, dict, message, or text agent output into a summary dict."""
        diagram_type = "MERMAID" if supports_mermaid else "ASCII"

        if isinstance(final_result, SummarizeOutput):
            logger.info("Successfully received structured summarize output")
            return self._summary_or_empty_error(
                summary=final_result.summary,
                diagram=final_result.diagram,
                diagram_type=final_result.diagramType or diagram_type,
            )

        if isinstance(final_result, dict) and "summary" in final_result:
            return self._summary_or_empty_error(
                summary=final_result.get("summary"),
                diagram=final_result.get("diagram"),
                diagram_type=final_result.get("diagramType") or diagram_type,
            )

        text = self._extract_agent_item_text(final_result)
        if not self._has_usable_text(text):
            return {"error": "AI service returned an empty summary"}

        logger.debug(f"Summarize raw result (first 500 chars): {str(text)[:500] if text else 'None'}")
        parsed = self._parse_json_response(str(text))
        if parsed:
            logger.info("Successfully parsed JSON response for summarize")
            return self._summary_or_empty_error(
                summary=parsed.get("summary"),
                diagram=parsed.get("diagram"),
                diagram_type=parsed.get("diagramType") or diagram_type,
            )

        extracted = self._extract_summary_field_fallback(str(text))
        if extracted:
            logger.warning("Used regex fallback to extract summary field")
            return self._summary_or_empty_error(
                summary=extracted,
                diagram="",
                diagram_type=diagram_type,
            )

        logger.warning("JSON parsing failed for summarize, using raw result")
        return self._summary_or_empty_error(
            summary=str(text),
            diagram="",
            diagram_type=diagram_type,
        )

    def _summary_or_empty_error(
            self,
            summary: Any,
            diagram: Any,
            diagram_type: Any
    ) -> Dict[str, Any]:
        if not self._has_usable_text(summary):
            return {"error": "AI service returned an empty summary"}
        return {
            "summary": str(summary),
            "diagram": self._string_or_empty(diagram),
            "diagramType": str(diagram_type or "ASCII"),
        }

    def _extract_summary_field_fallback(self, text: str) -> Optional[str]:
        """
        Fallback extraction of summary field when JSON parsing fails.
        Tries to extract the content between "summary": " and the closing quote.
        """
        if not text:
            return None
        
        import re
        
        # Look for "summary": "..." pattern
        # This pattern handles multi-line strings and escaped quotes
        pattern = r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"|\'summary\'\s*:\s*\'((?:[^\'\\]|\\.)*)\''
        match = re.search(pattern, text, re.DOTALL)
        if match:
            # Get the captured group (either double or single quoted)
            content = match.group(1) or match.group(2)
            if content:
                # Unescape common JSON escapes
                content = content.replace('\\"', '"')
                content = content.replace('\\n', '\n')
                content = content.replace('\\t', '\t')
                content = content.replace('\\\\', '\\')
                return content
        
        return None

    async def _execute_ask(
            self,
            llm,
            client: MCPClient,
            prompt: str,
            event_callback: Optional[Callable[[Dict], None]]
    ) -> Dict[str, Any]:
        """Execute the ask command with MCP agent using streaming and output_schema."""
        additional_instructions = (
            "CRITICAL: Your response MUST be a valid JSON object with an 'answer' field.\n"
            "Do NOT include any text outside the JSON object.\n"
            "The answer should be well-formatted markdown."
        )

        agent = MCPAgent(
            llm=llm,
            client=client,
            max_steps=self.MAX_STEPS_ASK,
            additional_instructions=additional_instructions,
        )

        try:
            self._emit_event(event_callback, {
                "type": "progress",
                "step": 0,
                "max_steps": self.MAX_STEPS_ASK,
                "message": "Processing question"
            })

            step_count = 0
            final_result = None

            # Use streaming with output_schema for structured output
            async for item in agent.stream(
                prompt,
                max_steps=self.MAX_STEPS_ASK,
                output_schema=AskOutput
            ):
                if isinstance(item, tuple) and len(item) == 2:
                    # Tool call with observation
                    action, observation = item
                    step_count += 1
                    
                    tool_name = action.tool if hasattr(action, 'tool') else str(action)
                    logger.info(f"[Ask Step {step_count}] Tool: {tool_name}")
                    
                    self._emit_event(event_callback, {
                        "type": "mcp_step",
                        "step": step_count,
                        "max_steps": self.MAX_STEPS_ASK,
                        "tool": tool_name,
                        "message": f"Executed tool: {tool_name}"
                    })
                    
                elif isinstance(item, AskOutput):
                    # Final structured output
                    final_result = item
                    logger.info(f"Received structured ask output")
                    
                elif isinstance(item, str):
                    # Intermediate text output
                    final_result = item

                else:
                    extracted = self._extract_agent_item_text(item)
                    if extracted is not None:
                        final_result = extracted

            self._emit_event(event_callback, {
                "type": "progress",
                "step": self.MAX_STEPS_ASK,
                "max_steps": self.MAX_STEPS_ASK,
                "message": f"Completed ({step_count} tool calls)"
            })

            result = self._coerce_ask_final_result(final_result)
            if "error" not in result:
                return result

            logger.warning("Ask streaming produced an empty final answer; retrying without output_schema")
            self._emit_event(event_callback, {
                "type": "status",
                "state": "retrying",
                "message": "Retrying answer generation"
            })

            raw_result = await self._run_agent_with_heartbeat(
                agent=agent,
                prompt=prompt,
                event_callback=event_callback,
                max_steps=self.MAX_STEPS_ASK
            )
            result = self._coerce_ask_final_result(raw_result)
            if "error" not in result:
                return result

            logger.warning("Ask agent retry also produced an empty answer; trying direct LLM fallback")
            direct_response = await llm.ainvoke(
                prompt
                + "\n\nIf tool calls are unavailable, answer from the context already provided. "
                  "Return a JSON object with a non-empty 'answer' field."
            )
            return self._coerce_ask_final_result(direct_response)

        except Exception as e:
            logger.error(f"Ask agent error: {e}", exc_info=True)
            sanitized_msg = create_user_friendly_error(e)
            return {"error": sanitized_msg}

    def _coerce_ask_final_result(self, final_result: Any) -> Dict[str, Any]:
        """Convert structured, dict, message, or text agent output into an answer dict."""
        if isinstance(final_result, AskOutput):
            logger.info("Successfully received structured ask output")
            return self._answer_or_empty_error(final_result.answer)

        if isinstance(final_result, dict) and "answer" in final_result:
            return self._answer_or_empty_error(final_result.get("answer"))

        text = self._extract_agent_item_text(final_result)
        if not self._has_usable_text(text):
            return {"error": "AI service returned an empty answer"}

        parsed = self._parse_json_response(str(text))
        if parsed and "answer" in parsed:
            return self._answer_or_empty_error(parsed.get("answer"))

        return {"answer": str(text)}

    def _answer_or_empty_error(self, answer: Any) -> Dict[str, Any]:
        if not self._has_usable_text(answer):
            return {"error": "AI service returned an empty answer"}
        return {"answer": str(answer)}

    def _extract_agent_item_text(self, item: Any) -> Optional[str]:
        """Extract final text from common LangChain/mcp_use stream item shapes."""
        if item is None:
            return None

        if isinstance(item, str):
            return item

        if isinstance(item, dict):
            for key in ("answer", "output", "final_output", "response", "result", "content", "text"):
                if key in item:
                    return self._extract_agent_item_text(item.get(key))

            messages = item.get("messages")
            if isinstance(messages, list) and messages:
                return self._extract_agent_item_text(messages[-1])

            return None

        if hasattr(item, "content"):
            return self._coerce_text_content(getattr(item, "content"))

        if hasattr(item, "model_dump"):
            try:
                dumped = item.model_dump()
                if isinstance(dumped, dict):
                    return self._extract_agent_item_text(dumped)
            except Exception:
                return None

        return None

    def _coerce_text_content(self, content: Any) -> str:
        """Convert provider content blocks to plain text."""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    text = block.get("text") or block.get("content")
                    if text is not None:
                        parts.append(str(text))
                elif hasattr(block, "text"):
                    parts.append(str(block.text))
            return "".join(parts)
        if isinstance(content, dict):
            text = content.get("text") or content.get("content")
            return "" if text is None else str(text)
        return str(content)

    async def _run_agent_with_heartbeat(
            self,
            agent: MCPAgent,
            prompt: str,
            event_callback: Optional[Callable[[Dict], None]],
            max_steps: int
    ) -> str:
        """Run the agent with periodic heartbeat events. (Legacy method - kept for compatibility)"""
        raw_result = None
        agent_exception = None

        async def run_agent_task():
            nonlocal raw_result, agent_exception
            try:
                raw_result = await agent.run(prompt)
            except Exception as e:
                agent_exception = e

        agent_task = asyncio.create_task(run_agent_task())
        step_count = 1

        while not agent_task.done():
            await asyncio.sleep(3)

            if not agent_task.done():
                current_step = min(step_count, max_steps - 1)
                self._emit_event(event_callback, {
                    "type": "progress",
                    "step": current_step,
                    "max_steps": max_steps,
                    "message": "Processing..."
                })
                step_count += 1

        if agent_exception:
            raise agent_exception

        return raw_result

    def _parse_json_response(self, response: str) -> Optional[Dict[str, Any]]:
        """Parse JSON from agent response."""
        if not response:
            return None

        import json
        import re

        # Try direct parse
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from markdown code blocks
        json_patterns = [
            r'```json\s*([\s\S]*?)\s*```',
            r'```\s*([\s\S]*?)\s*```',
        ]

        for pattern in json_patterns:
            matches = re.findall(pattern, response)
            for match in matches:
                try:
                    cleaned = match.strip()
                    if not cleaned.startswith('{'):
                        continue
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    continue

        # Try to find JSON object by matching balanced braces
        json_obj = self._extract_json_object(response)
        if json_obj:
            try:
                return json.loads(json_obj)
            except json.JSONDecodeError:
                pass

        logger.warning(f"Failed to parse JSON from response: {response[:200]}...")
        return None

    def _extract_json_object(self, text: str) -> Optional[str]:
        """Extract the first balanced JSON object from text."""
        start = text.find('{')
        if start == -1:
            return None

        depth = 0
        in_string = False
        escape_next = False
        
        for i, char in enumerate(text[start:], start):
            if escape_next:
                escape_next = False
                continue
            
            if char == '\\':
                escape_next = True
                continue
            
            if char == '"' and not escape_next:
                in_string = not in_string
                continue
            
            if in_string:
                continue
            
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        
        return None

    def _create_mcp_client(self, config: Dict[str, Any]) -> MCPClient:
        """Create MCP client from configuration."""
        try:
            return MCPClient.from_dict(config)
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
