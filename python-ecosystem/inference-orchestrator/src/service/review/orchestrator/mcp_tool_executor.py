"""
Controlled MCP tool executor with per-stage whitelist and call budget.

Stage 1 (context gaps):      getBranchFileContent — max 3 calls/batch
Stage 3 (issue verification): getBranchFileContent, getPullRequestComments — max 5 calls total
"""
import asyncio
import logging
from time import monotonic_ns
from typing import Any, Dict, List, Optional, Set

from service.review.telemetry import (
    StageOutcome,
    ToolCallTelemetry,
    current_telemetry,
)

logger = logging.getLogger(__name__)


class McpToolExecutor:
    """
    Wraps an MCP client session with safety controls:
    - Tool whitelist per stage
    - Call budget (hard limit)
    - Pre-filled workspace/repoSlug from request context
    - Call logging for observability
    """

    STAGE_CONFIG = {
        "stage_1": {
            "tools": {"getBranchFileContent"},
            "max_calls": 3,
        },
        "stage_3": {
            "tools": {"getBranchFileContent", "getPullRequestComments"},
            "max_calls": 5,
        },
    }

    def __init__(self, mcp_client, request, stage: str):
        if stage not in self.STAGE_CONFIG:
            raise ValueError(f"Unknown stage '{stage}'. Valid: {list(self.STAGE_CONFIG)}")

        config = self.STAGE_CONFIG[stage]
        self.client = mcp_client
        self.request = request
        self.stage = stage
        self.allowed_tools: Set[str] = config["tools"]
        self.max_calls: int = config["max_calls"]
        self.call_count: int = 0
        self.call_log: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Execute a single MCP tool call with safety checks."""
        async with self._lock:
            if tool_name not in self.allowed_tools:
                msg = f"Tool '{tool_name}' not allowed in {self.stage}. Allowed: {self.allowed_tools}"
                logger.warning(msg)
                self._record_telemetry(
                    tool_name, StageOutcome.SKIPPED, 0, "tool_not_allowed"
                )
                return msg

            if self.call_count >= self.max_calls:
                msg = f"Tool budget exhausted ({self.max_calls} calls used in {self.stage})."
                logger.warning(msg)
                self._record_telemetry(
                    tool_name, StageOutcome.SKIPPED, 0, "tool_budget_exhausted"
                )
                return msg

            self.call_count += 1

        # Pre-fill workspace/repo from request context so the LLM doesn't
        # have to guess these values.
        arguments.setdefault("workspace", self.request.projectVcsWorkspace)
        arguments.setdefault("repoSlug", self.request.projectVcsRepoSlug)

        logger.info(
            "[MCP %s] Calling %s (call %d/%d)",
            self.stage,
            tool_name,
            self.call_count,
            self.max_calls,
        )

        started_ns = monotonic_ns()
        try:
            result = await self.client.session.call_tool(tool_name, arguments)
            self.call_log.append(
                {"tool": tool_name, "success": True}
            )
            self._record_telemetry(
                tool_name,
                StageOutcome.COMPLETE,
                (monotonic_ns() - started_ns) // 1_000_000,
                None,
            )
            # Extract text content from MCP result
            if hasattr(result, "content") and result.content:
                return "\n".join(
                    block.text for block in result.content if hasattr(block, "text")
                )
            return str(result)
        except Exception as e:
            logger.error("[MCP %s] Tool call failed: %s", self.stage, type(e).__name__)
            self.call_log.append(
                {"tool": tool_name, "success": False, "error": type(e).__name__}
            )
            self._record_telemetry(
                tool_name,
                StageOutcome.FAILED,
                (monotonic_ns() - started_ns) // 1_000_000,
                "tool_call_failed",
            )
            return f"Tool call failed: {e}"

    def _record_telemetry(
        self,
        tool_name: str,
        outcome: StageOutcome,
        duration_ms: int,
        reason: Optional[str],
    ) -> None:
        recorder = current_telemetry()
        if recorder is None:
            return
        try:
            recorder.record_tool_call(
                ToolCallTelemetry(
                    stage=self.stage,
                    tool=tool_name,
                    outcome=outcome,
                    duration_ms=max(0, duration_ms),
                    reason=reason,
                )
            )
        except Exception as error:
            logger.warning("Tool telemetry rejected: %s", type(error).__name__)

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Return OpenAI-compatible function definitions for allowed tools."""
        definitions = []
        for tool_name in self.allowed_tools:
            if tool_name == "getBranchFileContent":
                definitions.append({
                    "type": "function",
                    "function": {
                        "name": "getBranchFileContent",
                        "description": "Read a file's content from the target branch.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "branch": {
                                    "type": "string",
                                    "description": "Branch name (e.g. 'main', 'develop')"
                                },
                                "filePath": {
                                    "type": "string",
                                    "description": "Path to the file in the repository"
                                },
                            },
                            "required": ["branch", "filePath"],
                        },
                    },
                })
            elif tool_name == "getPullRequestComments":
                definitions.append({
                    "type": "function",
                    "function": {
                        "name": "getPullRequestComments",
                        "description": "Get comments from the pull request.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "pullRequestId": {
                                    "type": "string",
                                    "description": "Pull request ID"
                                },
                            },
                            "required": ["pullRequestId"],
                        },
                    },
                })
        return definitions

    @property
    def budget_remaining(self) -> int:
        return max(0, self.max_calls - self.call_count)

    @property
    def budget_exhausted(self) -> bool:
        return self.call_count >= self.max_calls

    def summary(self) -> str:
        """Return a human-readable summary for logging."""
        return (
            f"McpToolExecutor({self.stage}): "
            f"{self.call_count}/{self.max_calls} calls used, "
            f"{len(self.call_log)} logged"
        )
