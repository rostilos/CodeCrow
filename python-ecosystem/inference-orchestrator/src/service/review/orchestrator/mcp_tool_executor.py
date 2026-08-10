"""
Controlled MCP tool executor with per-stage whitelist and call budget.

Stage 1 (context gaps):      getBranchFileContent — max 3 calls/batch
Stage 3 (issue verification): getBranchFileContent, getPullRequestComments — max 5 calls total
"""
import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_REVIEW_SOURCE_CONTEXT_LINES = 80
_FILTERED_CONTENT_MARKER = "[CodeCrow Filter:"
_MCP_ERROR_PREFIXES = ("Error executing tool:", "Tool call failed:")
_RELATED_LOCATIONS_RE = re.compile(
    r"(?im)^\s*(?:[*_]{1,2})?also affects\s*:(?:[*_]{1,2})?\s*(.+)$"
)


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

    def __init__(
        self,
        mcp_client,
        request,
        stage: str,
        review_revision: Optional[str] = None,
        verification_issues: Optional[Dict[str, Any]] = None,
    ):
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
        self.review_revision = str(review_revision or "").strip()
        self.verification_issues = dict(verification_issues or {})
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
                return msg

            if self.call_count >= self.max_calls:
                msg = f"Tool budget exhausted ({self.max_calls} calls used in {self.stage})."
                logger.warning(msg)
                return msg

            self.call_count += 1

        # Pre-fill workspace/repo from request context so the LLM doesn't
        # have to guess these values.
        arguments = dict(arguments or {})
        arguments.setdefault("workspace", self.request.projectVcsWorkspace)
        arguments.setdefault("repoSlug", self.request.projectVcsRepoSlug)
        if (
            self.stage == "stage_3"
            and tool_name == "getBranchFileContent"
            and self.review_revision
        ):
            # Post-review evidence must come from the exact reviewed revision.
            # Never let a model accidentally verify new PR code against target.
            arguments["branch"] = self.review_revision
            verification_id = str(
                arguments.get("verificationId") or ""
            ).strip()
            source_line = self._verification_line_for_path(
                verification_id,
                str(arguments.get("filePath") or ""),
            )
            if source_line > 0:
                # Request an anchor-centred source window. This avoids replacing
                # large files with a generic size placeholder or sending an
                # unrelated full file merely to verify one concrete finding.
                arguments["startLine"] = max(
                    1,
                    source_line - _REVIEW_SOURCE_CONTEXT_LINES,
                )
                arguments["endLine"] = (
                    source_line + _REVIEW_SOURCE_CONTEXT_LINES
                )

        logger.info(
            f"[MCP {self.stage}] Calling {tool_name} "
            f"(call {self.call_count}/{self.max_calls}): {arguments}"
        )

        try:
            result = await self.client.session.call_tool(tool_name, arguments)
            # Extract text content from MCP result
            if hasattr(result, "content"):
                text = "\n".join(
                    block.text
                    for block in (result.content or [])
                    if hasattr(block, "text")
                )
            else:
                text = str(result)
            evidence = self._file_evidence_metadata(tool_name, text)
            tool_reported_error = bool(
                getattr(result, "isError", False)
                or getattr(result, "is_error", False)
            )
            if tool_reported_error:
                evidence = self._file_evidence_metadata(tool_name, "")
            if (
                self.stage == "stage_3"
                and tool_name == "getBranchFileContent"
                and evidence.get("evidence_valid") is True
                and evidence.get("evidence_structured") is not True
                and arguments.get("startLine")
            ):
                # Legacy adapters may return raw source rather than the VCS MCP
                # metadata envelope. Since Stage 3 explicitly requested a
                # window, bind that raw response to the requested range instead
                # of incorrectly treating it as proof for the entire file.
                evidence["evidence_complete_file"] = False
                evidence["evidence_start_line"] = arguments["startLine"]
                evidence["evidence_end_line"] = arguments.get(
                    "endLine",
                    arguments["startLine"],
                )
            log_entry = {
                "tool": tool_name,
                "args": dict(arguments),
                "success": not tool_reported_error,
                "result_chars": len(text.strip()),
                **evidence,
            }
            self.call_log.append(log_entry)
            return text
        except Exception as e:
            logger.error(f"[MCP {self.stage}] Tool call failed: {e}")
            self.call_log.append(
                {"tool": tool_name, "args": arguments, "success": False, "error": str(e)}
            )
            return f"Tool call failed: {e}"

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Return OpenAI-compatible function definitions for allowed tools."""
        definitions = []
        for tool_name in self.allowed_tools:
            if tool_name == "getBranchFileContent":
                definitions.append({
                    "type": "function",
                    "function": {
                        "name": "getBranchFileContent",
                        "description": (
                            "Read repository file content. For Stage 3, provide the "
                            "finding Verification ID; the host pins the reviewed "
                            "revision and requests an anchor-centred source window."
                            if self.stage == "stage_3"
                            else "Read a file's content from the target branch."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                **({
                                    "branch": {
                                        "type": "string",
                                        "description": "Target branch name (for example main).",
                                    },
                                } if self.stage != "stage_3" else {}),
                                "filePath": {
                                    "type": "string",
                                    "description": "Path to the file in the repository"
                                },
                                **({
                                    "verificationId": {
                                        "type": "string",
                                        "description": (
                                            "Verification ID from the Stage 3 finding record."
                                        ),
                                    },
                                } if self.stage == "stage_3" else {}),
                            },
                            "required": (
                                ["filePath", "verificationId"]
                                if self.stage == "stage_3"
                                else ["branch", "filePath"]
                            ),
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

    @staticmethod
    def _normalized_path(value: Any) -> str:
        return str(value or "").strip().replace("\\", "/").lstrip("/")

    @staticmethod
    def _location_parts(value: Any) -> tuple[str, int]:
        normalized = McpToolExecutor._normalized_path(value)
        path, separator, possible_line = normalized.rpartition(":")
        if separator and possible_line.isdigit():
            return path, int(possible_line)
        return normalized, 0

    def _verification_line_for_path(
        self,
        verification_id: str,
        file_path: str,
    ) -> int:
        issue = self.verification_issues.get(verification_id)
        if issue is None:
            return 0
        requested_path = self._normalized_path(file_path)
        issue_path = self._normalized_path(getattr(issue, "file", ""))
        if requested_path == issue_path:
            try:
                return max(0, int(getattr(issue, "line", 0) or 0))
            except (TypeError, ValueError):
                return 0
        for location in self._related_locations(issue):
            path, line = self._location_parts(location)
            if path == requested_path:
                return line
        return 0

    @staticmethod
    def _related_locations(issue: Any) -> List[str]:
        values = list(getattr(issue, "relatedLocations", None) or [])
        reason = str(getattr(issue, "reason", "") or "")
        for match in _RELATED_LOCATIONS_RE.finditer(reason):
            values.extend(match.group(1).split(","))
        return sorted({
            str(value).strip()
            for value in values
            if str(value).strip()
        })

    @staticmethod
    def _file_evidence_metadata(
        tool_name: str,
        text: str,
    ) -> Dict[str, Any]:
        if tool_name != "getBranchFileContent":
            return {}

        stripped = str(text or "").strip()
        metadata: Dict[str, Any] = {
            "evidence_valid": False,
            "evidence_structured": False,
            "evidence_complete_file": False,
            "evidence_start_line": 0,
            "evidence_end_line": 0,
        }
        if (
            not stripped
            or _FILTERED_CONTENT_MARKER in stripped
            or stripped.startswith(_MCP_ERROR_PREFIXES)
        ):
            return metadata

        try:
            payload = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            # Some MCP adapters return raw source text instead of a JSON map.
            metadata["evidence_valid"] = True
            metadata["evidence_complete_file"] = True
            return metadata

        if not isinstance(payload, dict) or payload.get("error"):
            return metadata
        metadata["evidence_structured"] = True
        file_content = payload.get("fileContent")
        if not isinstance(file_content, str) or not file_content.strip():
            return metadata
        if _FILTERED_CONTENT_MARKER in file_content:
            return metadata

        def integer(name: str) -> int:
            try:
                return max(0, int(payload.get(name) or 0))
            except (TypeError, ValueError):
                return 0

        metadata.update({
            "evidence_valid": True,
            "evidence_complete_file": payload.get("completeFile") is True,
            "evidence_start_line": integer("startLine"),
            "evidence_end_line": integer("endLine"),
        })
        return metadata
