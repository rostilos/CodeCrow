"""Execution-scoped, read-only tools for an exact agentic review workspace.

The model supplies only search intent and repository-relative paths. Repository,
revision, PR, snapshot, and execution coordinates are injected from the already
validated review request and cannot be overridden through tool arguments.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from fnmatch import fnmatchcase
from hashlib import sha256
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import time
from typing import Any, Dict, Iterable, Mapping, Optional, TYPE_CHECKING

from model.dtos import ReviewRequestDto
from model.related_context import ContextGapV1
from service.review.execution_context import (
    context_branch_labels,
    context_snapshot_v1,
    is_manifest_bound_v1,
)
from service.review.orchestrator.related_context import (
    build_related_context_pack,
    flatten_deterministic_context,
    manifest_anchor_digests,
)
from service.review.telemetry import (
    StageOutcome,
    ToolCallTelemetry,
    current_telemetry,
)
from utils.git_diff_paths import (
    GitDiffPathError,
    parse_git_diff_header,
    parse_git_marker_path,
)

if TYPE_CHECKING:
    from service.rag.rag_client import RagClient
    from utils.diff_processor import ProcessedDiff


logger = logging.getLogger(__name__)


_TOOL_NAMES = (
    "list_files",
    "search_text",
    "read_file",
    "find_symbol",
    "read_diff_hunk",
    "rag_search",
    "rag_related",
    "rag_similar_code",
)
_LOCAL_TOOLS = frozenset(_TOOL_NAMES[:5])
_RAG_TOOLS = frozenset(_TOOL_NAMES[5:])
_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SYMBOL = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$.:\\\-]{0,255}$")
_DIFF_HEADER = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@.*$",
    flags=re.MULTILINE,
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?"
    r"-----END [^-\r\n]*PRIVATE KEY-----",
    flags=re.DOTALL | re.IGNORECASE,
)
_SECRET_ASSIGNMENT_NAME = (
    r"(?<![A-Za-z0-9_-])"
    r"(?:[A-Za-z0-9]+[_-])*"
    r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|passwd|"
    r"authorization|private[_-]?key|credentials?)"
    r"(?:[_-][A-Za-z0-9]+)*"
)
_QUOTED_SECRET = re.compile(
    r"(?i)(" + _SECRET_ASSIGNMENT_NAME + r"\s*[:=]\s*)([\"'])(.*?)(\2)"
)
_UNQUOTED_SECRET = re.compile(
    r"(?i)(" + _SECRET_ASSIGNMENT_NAME + r"\s*[:=]\s*)([^\s,;}{]+)"
)
_KNOWN_SECRET_VALUE = re.compile(
    r"(?i)(?:"
    r"gh[pousr]_[A-Za-z0-9]{30,255}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,255}|"
    r"sk-[A-Za-z0-9_-]{20,255}|"
    r"AKIA[0-9A-Z]{16}"
    r")"
)
_URL_PASSWORD = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]*://[^\s/:@]+:)([^\s/@]+)(@)"
)


class AgenticToolError(ValueError):
    """A bounded tool rejected input or could not safely produce a result."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ToolGatewayLimits:
    """Finite budgets shared by repository and RAG operations."""

    max_calls: int = 48
    max_results: int = 20
    max_output_bytes_per_call: int = 48 * 1024
    max_total_output_bytes: int = 512 * 1024
    max_file_bytes: int = 2 * 1024 * 1024
    max_search_files: int = 10_000
    max_read_lines: int = 500
    max_query_chars: int = 1_000
    call_timeout_seconds: float = 12.0

    def __post_init__(self) -> None:
        integer_bounds = {
            "max_calls": (self.max_calls, 1, 256),
            "max_results": (self.max_results, 1, 100),
            "max_output_bytes_per_call": (
                self.max_output_bytes_per_call,
                256,
                256 * 1024,
            ),
            "max_total_output_bytes": (
                self.max_total_output_bytes,
                256,
                4 * 1024 * 1024,
            ),
            "max_file_bytes": (self.max_file_bytes, 1, 25 * 1024 * 1024),
            "max_search_files": (self.max_search_files, 1, 200_000),
            "max_read_lines": (self.max_read_lines, 1, 5_000),
            "max_query_chars": (self.max_query_chars, 16, 8_000),
        }
        for name, (value, minimum, maximum) in integer_bounds.items():
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} must be an integer")
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
        if not 0.005 <= self.call_timeout_seconds <= 60.0:
            raise ValueError("call_timeout_seconds must be between 0.005 and 60")


_TOOL_DEFINITIONS = (
    {
        "name": "list_files",
        "description": "List bounded repository-relative file paths matching a pattern.",
        "inputSchema": {
            "type": "object",
            "properties": {"pattern": {"type": "string", "default": "*"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "search_text",
        "description": "Find literal text in bounded repository files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path_pattern": {"type": "string", "default": "*"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read an exact source span. Prefer the smallest useful start_line/end_line "
            "range around the relevant hunk or symbol; avoid whole-file reads."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1, "default": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_symbol",
        "description": "Find exact symbol occurrences in bounded repository files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "path_pattern": {"type": "string", "default": "*"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_diff_hunk",
        "description": "Read the immutable PR diff hunk containing a new-side line.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "line": {"type": "integer", "minimum": 1},
            },
            "required": ["path", "line"],
            "additionalProperties": False,
        },
    },
    {
        "name": "rag_search",
        "description": "Retrieve provenance-checked semantic leads for a focused question.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path_pattern": {"type": "string", "default": "*"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "rag_related",
        "description": "Retrieve provenance-checked structural context for a path or symbol.",
        "inputSchema": {
            "type": "object",
            "properties": {"path_or_symbol": {"type": "string"}},
            "required": ["path_or_symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "rag_similar_code",
        "description": "Find similar implementations using an exact local source span.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["path", "start_line", "end_line"],
            "additionalProperties": False,
        },
    },
)

_ARGUMENTS = {
    "list_files": (frozenset({"pattern"}), frozenset()),
    "search_text": (frozenset({"query", "path_pattern"}), frozenset({"query"})),
    "read_file": (
        frozenset({"path", "start_line", "end_line"}),
        frozenset({"path"}),
    ),
    "find_symbol": (
        frozenset({"name", "path_pattern"}),
        frozenset({"name"}),
    ),
    "read_diff_hunk": (
        frozenset({"path", "line"}),
        frozenset({"path", "line"}),
    ),
    "rag_search": (
        frozenset({"query", "path_pattern"}),
        frozenset({"query"}),
    ),
    "rag_related": (
        frozenset({"path_or_symbol"}),
        frozenset({"path_or_symbol"}),
    ),
    "rag_similar_code": (
        frozenset({"path", "start_line", "end_line"}),
        frozenset({"path", "start_line", "end_line"}),
    ),
}


class AgenticToolGateway:
    """A fixed-root repository and exact-RAG gateway for one review execution."""

    def __init__(
        self,
        workspace_root: Path | str,
        request: ReviewRequestDto,
        rag_client: Optional["RagClient"],
        processed_diff: Optional["ProcessedDiff"] = None,
        limits: Optional[ToolGatewayLimits] = None,
    ) -> None:
        if not is_manifest_bound_v1(request):
            raise AgenticToolError(
                "UNBOUND_EXECUTION",
                "agentic tools require an exact execution manifest",
            )
        root_input = Path(workspace_root)
        if root_input.is_symlink() or not root_input.is_dir():
            raise AgenticToolError(
                "INVALID_WORKSPACE", "workspace root must be a non-symlink directory"
            )
        self._root = root_input.resolve(strict=True)
        self._request = request
        self._rag_client = rag_client
        self._processed_diff = processed_diff
        self._limits = limits or ToolGatewayLimits()
        self._manifest = request.executionManifest
        rag_context = getattr(request, "ragContext", None)
        self._rag_index_version = getattr(rag_context, "indexVersion", None)
        expected_index_version = f"rag-commit-{self._manifest.baseSha}"
        if self._rag_index_version not in {
            "rag-disabled",
            expected_index_version,
        } or self._rag_index_version != getattr(request, "indexVersion", None):
            raise AgenticToolError(
                "UNBOUND_EXECUTION",
                "agentic RAG selection conflicts with the frozen execution context",
            )
        self.snapshot = context_snapshot_v1(request)
        if self.snapshot is None:
            raise AgenticToolError("UNBOUND_EXECUTION", "exact snapshot is unavailable")
        self._validate_snapshot(self.snapshot)
        self.snapshot_id = sha256(
            json.dumps(
                self.snapshot,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        self._source_branch, self._base_branch = context_branch_labels(request)
        self._source_branch = self._source_branch or self.snapshot["head_sha"]
        self._base_branch = self._base_branch or self.snapshot["base_sha"]
        self._workspace = self._required_coordinate(
            getattr(request, "projectVcsWorkspace", None), "VCS workspace"
        )
        self._project = self._required_coordinate(
            getattr(request, "projectVcsRepoSlug", None), "VCS project"
        )
        self._raw_diff = getattr(request, "rawDiff", None)
        if not isinstance(self._raw_diff, str):
            raise AgenticToolError("UNBOUND_EXECUTION", "immutable raw diff is unavailable")
        observed_diff_digest = sha256(self._raw_diff.encode("utf-8")).hexdigest()
        if observed_diff_digest != self._manifest.diffDigest:
            raise AgenticToolError(
                "UNBOUND_EXECUTION", "raw diff conflicts with execution manifest"
            )
        self._changed_files = []
        for path in getattr(request, "changedFiles", None) or []:
            try:
                normalized = self._validate_relative_path(path)
            except AgenticToolError:
                continue
            if normalized not in self._changed_files:
                self._changed_files.append(normalized)

        self._calls_used = 0
        self._local_calls = 0
        self._rag_calls = 0
        self._output_bytes = 0
        self._events: list[dict[str, Any]] = []
        self._receipts: dict[str, dict[str, Any]] = {}
        self._state_lock = asyncio.Lock()

    @staticmethod
    def _validate_snapshot(snapshot: Mapping[str, Any]) -> None:
        for key in ("base_sha", "head_sha", "merge_base_sha"):
            if not isinstance(snapshot.get(key), str) or not _SHA.fullmatch(snapshot[key]):
                raise AgenticToolError(
                    "UNBOUND_EXECUTION", f"snapshot {key} is missing or malformed"
                )
        for key in ("parser_version", "chunker_version", "embedding_version"):
            if not isinstance(snapshot.get(key), str) or not snapshot[key]:
                raise AgenticToolError(
                    "UNBOUND_EXECUTION", f"snapshot {key} is missing"
                )

    @staticmethod
    def _required_coordinate(value: Any, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise AgenticToolError("UNBOUND_EXECUTION", f"{label} is unavailable")
        return value

    @staticmethod
    def tool_definitions() -> list[dict[str, Any]]:
        """Return adapter-neutral definitions suitable for MCP/LLM wiring."""

        return deepcopy(list(_TOOL_DEFINITIONS))

    @staticmethod
    def langchain_tool_definitions() -> list[dict[str, Any]]:
        """Return the same tools in the OpenAI/LangChain function shape."""

        return [
            {
                "type": "function",
                "function": {
                    "name": definition["name"],
                    "description": definition["description"],
                    "parameters": deepcopy(definition["inputSchema"]),
                },
            }
            for definition in _TOOL_DEFINITIONS
        ]

    async def invoke(
        self, tool_name: str, arguments: Optional[Mapping[str, Any]] = None
    ) -> Dict[str, Any]:
        """Invoke one bounded tool while accounting for the shared review budget."""

        if tool_name not in _TOOL_NAMES:
            raise AgenticToolError("UNKNOWN_TOOL", "unknown agentic review tool")
        values = dict(arguments or {})
        await self._begin_call(tool_name)
        started = time.monotonic()
        try:
            self._validate_arguments(tool_name, values)
            deadline = time.monotonic() + self._limits.call_timeout_seconds
            result = await asyncio.wait_for(
                self._dispatch(tool_name, values, deadline),
                timeout=self._limits.call_timeout_seconds,
            )
            bounded, output_bytes = await self._bound_output(tool_name, result)
        except asyncio.TimeoutError as error:
            tool_error = AgenticToolError(
                "TOOL_TIMEOUT", "agentic review tool exceeded its time budget"
            )
            await self._finish_call(tool_name, started, False, 0, tool_error.code)
            raise tool_error from error
        except AgenticToolError as error:
            await self._finish_call(tool_name, started, False, 0, error.code)
            raise
        except Exception as error:
            tool_error = AgenticToolError(
                "TOOL_FAILURE", "agentic review tool failed safely"
            )
            await self._finish_call(tool_name, started, False, 0, tool_error.code)
            raise tool_error from error
        await self._finish_call(tool_name, started, True, output_bytes, None)
        return bounded

    async def _begin_call(self, tool_name: str) -> None:
        async with self._state_lock:
            if self._calls_used >= self._limits.max_calls:
                raise AgenticToolError(
                    "CALL_BUDGET_EXHAUSTED", "agentic review tool budget is exhausted"
                )
            self._calls_used += 1
            if tool_name in _RAG_TOOLS:
                self._rag_calls += 1
            else:
                self._local_calls += 1

    async def _finish_call(
        self,
        tool_name: str,
        started: float,
        success: bool,
        output_bytes: int,
        error_code: Optional[str],
    ) -> None:
        duration_ms = max(0, round((time.monotonic() - started) * 1_000))
        event = {
            "tool": tool_name,
            "kind": "rag" if tool_name in _RAG_TOOLS else "local",
            "success": success,
            "duration_ms": duration_ms,
            "output_bytes": output_bytes,
            "error_code": error_code,
        }
        async with self._state_lock:
            self._events.append(event)
        self._record_execution_telemetry(
            tool_name=tool_name,
            success=success,
            duration_ms=duration_ms,
            error_code=error_code,
        )

    @staticmethod
    def _record_execution_telemetry(
        *,
        tool_name: str,
        success: bool,
        duration_ms: int,
        error_code: Optional[str],
    ) -> None:
        recorder = current_telemetry()
        if recorder is None:
            return
        try:
            recorder.record_tool_call(
                ToolCallTelemetry(
                    stage="agentic_review",
                    tool=tool_name,
                    outcome=(
                        StageOutcome.COMPLETE if success else StageOutcome.FAILED
                    ),
                    duration_ms=duration_ms,
                    reason=None if success else str(error_code or "tool_failure").lower(),
                )
            )
        except Exception as error:
            logger.warning(
                "Agentic tool telemetry rejected: %s", type(error).__name__
            )

    def telemetry_snapshot(self) -> Dict[str, Any]:
        """Return content-free usage telemetry for evaluation and diagnostics."""

        return {
            "execution_id": self._manifest.executionId,
            "calls_used": self._calls_used,
            "calls_remaining": max(0, self._limits.max_calls - self._calls_used),
            "local_calls": self._local_calls,
            "rag_calls": self._rag_calls,
            "output_bytes": self._output_bytes,
            "events": deepcopy(self._events),
        }

    @property
    def telemetry_summary(self) -> Dict[str, Any]:
        """JSON-safe content-free telemetry for the agent engine result."""

        return self.telemetry_snapshot()

    @property
    def receipts(self) -> list[dict[str, Any]]:
        """Proof receipts emitted by exact local reads, without source content."""

        return deepcopy(list(self._receipts.values()))

    @staticmethod
    def _receipt_identity(proof: Mapping[str, Any]) -> str:
        canonical = {key: value for key, value in proof.items() if key != "receipt_id"}
        return sha256(
            json.dumps(
                canonical,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()

    def _register_proof(self, proof: Dict[str, Any]) -> Dict[str, Any]:
        registered = dict(proof)
        registered["receipt_id"] = self._receipt_identity(registered)
        self._receipts[registered["receipt_id"]] = deepcopy(registered)
        return registered

    def validate_proof(
        self, proof: Mapping[str, Any], *, expected_path: Optional[str] = None
    ) -> bool:
        """Revalidate an emitted proof against this execution and workspace."""

        try:
            if not isinstance(proof, Mapping):
                return False
            observed = dict(proof)
            receipt_id = observed.get("receipt_id")
            if (
                not isinstance(receipt_id, str)
                or receipt_id != self._receipt_identity(observed)
                or self._receipts.get(receipt_id) != observed
            ):
                return False
            if (
                observed.get("execution_id") != self._manifest.executionId
                or observed.get("head_sha") != self.snapshot["head_sha"]
                or observed.get("snapshot_id") != self.snapshot_id
            ):
                return False
            path = self._validate_relative_path(observed.get("path"))
            if expected_path is not None:
                if path != self._validate_relative_path(expected_path):
                    return False
            if observed.get("kind") == "exact_source_span_v1":
                normalized, file_path = self._resolve_file(path)
                data, text = self._decode_text(file_path, self._limits.max_file_bytes)
                start = observed.get("start_line")
                end = observed.get("end_line")
                if (
                    normalized != path
                    or not isinstance(start, int)
                    or isinstance(start, bool)
                    or not isinstance(end, int)
                    or isinstance(end, bool)
                    or start < 1
                    or end < start
                ):
                    return False
                lines = text.splitlines(keepends=True)
                if end > len(lines):
                    return False
                span = "".join(lines[start - 1 : end])
                return (
                    observed.get("source_digest") == sha256(data).hexdigest()
                    and observed.get("span_digest")
                    == sha256(span.encode("utf-8")).hexdigest()
                )
            if observed.get("kind") == "exact_diff_hunk_v1":
                if observed.get("diff_digest") != self._manifest.diffDigest:
                    return False
                located = self._locate_diff_hunk(path, observed.get("requested_line"))
                return observed.get("hunk_digest") == sha256(
                    located["raw_hunk"].encode("utf-8")
                ).hexdigest()
            return False
        except (AgenticToolError, KeyError, TypeError, ValueError):
            return False

    @staticmethod
    def _validate_arguments(tool_name: str, arguments: Mapping[str, Any]) -> None:
        allowed, required = _ARGUMENTS[tool_name]
        observed = set(arguments)
        if observed - allowed or required - observed:
            raise AgenticToolError(
                "INVALID_ARGUMENTS",
                f"invalid arguments for {tool_name}",
            )

    async def _dispatch(
        self, tool_name: str, arguments: Dict[str, Any], deadline: float
    ) -> Dict[str, Any]:
        if tool_name == "list_files":
            return self._list_files(arguments.get("pattern", "*"), deadline)
        if tool_name == "search_text":
            return self._search_text(
                arguments["query"],
                arguments.get("path_pattern", "*"),
                deadline,
            )
        if tool_name == "read_file":
            return self._read_file(
                arguments["path"],
                arguments.get("start_line", 1),
                arguments.get("end_line"),
                deadline,
            )
        if tool_name == "find_symbol":
            return self._find_symbol(
                arguments["name"],
                arguments.get("path_pattern", "*"),
                deadline,
            )
        if tool_name == "read_diff_hunk":
            return self._read_diff_hunk(
                arguments["path"], arguments["line"], deadline
            )
        if tool_name == "rag_search":
            return await self._rag_search(
                arguments["query"], arguments.get("path_pattern", "*")
            )
        if tool_name == "rag_related":
            return await self._rag_related(arguments["path_or_symbol"])
        return await self._rag_similar_code(
            arguments["path"], arguments["start_line"], arguments["end_line"]
        )

    async def _bound_output(
        self, tool_name: str, result: Dict[str, Any]
    ) -> tuple[Dict[str, Any], int]:
        async with self._state_lock:
            remaining = self._limits.max_total_output_bytes - self._output_bytes
            if remaining < 128:
                raise AgenticToolError(
                    "OUTPUT_BUDGET_EXHAUSTED",
                    "agentic review output budget is exhausted",
                )
            budget = min(self._limits.max_output_bytes_per_call, remaining)
            bounded = self._fit_payload(tool_name, result, budget)
            output_bytes = len(
                json.dumps(
                    bounded, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")
            )
            self._output_bytes += output_bytes
            return bounded, output_bytes

    @staticmethod
    def _encoded_size(value: Mapping[str, Any]) -> int:
        return len(
            json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
        )

    def _fit_payload(
        self, tool_name: str, result: Dict[str, Any], budget: int
    ) -> Dict[str, Any]:
        payload = deepcopy(result)
        payload.setdefault("tool", tool_name)
        payload.setdefault("truncated", False)
        if self._encoded_size(payload) <= budget:
            return payload

        payload["truncated"] = True
        payload["output_limit_bytes"] = budget
        while self._encoded_size(payload) > budget:
            changed = False
            results = payload.get("results")
            if isinstance(results, list) and len(results) > 1:
                results.pop()
                payload["omitted_result_count"] = (
                    int(payload.get("omitted_result_count", 0)) + 1
                )
                changed = True
            elif isinstance(results, list) and results:
                item = results[0]
                if isinstance(item, dict):
                    for key in ("content", "excerpt", "selection_reason"):
                        value = item.get(key)
                        if isinstance(value, str) and len(value) > 32:
                            item[key] = value[: max(16, len(value) // 2)] + "…"
                            changed = True
                            break
                if not changed:
                    results.clear()
                    payload["omitted_result_count"] = (
                        int(payload.get("omitted_result_count", 0)) + 1
                    )
                    changed = True
            else:
                for key in ("content", "query_source_content"):
                    value = payload.get(key)
                    if isinstance(value, str) and len(value) > 32:
                        payload[key] = value[: max(16, len(value) // 2)] + "…"
                        changed = True
                        break
            if not changed:
                for key in ("gaps", "snapshot_receipt", "proof"):
                    if key in payload:
                        payload.pop(key)
                        changed = True
                        break
            if not changed:
                break

        if self._encoded_size(payload) <= budget:
            return payload
        minimal = {
            "tool": tool_name,
            "truncated": True,
            "output_limit_bytes": budget,
            "results": [],
        }
        if self._encoded_size(minimal) > budget:
            raise AgenticToolError(
                "OUTPUT_BUDGET_EXHAUSTED",
                "agentic review output budget cannot fit a safe response",
            )
        return minimal

    @staticmethod
    def _validate_relative_path(value: Any) -> str:
        if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
            raise AgenticToolError("INVALID_PATH", "repository path is invalid")
        path = PurePosixPath(value)
        if path.is_absolute() or value.startswith("/") or any(
            part in {"", ".", ".."} for part in path.parts
        ):
            raise AgenticToolError("INVALID_PATH", "repository path escapes workspace")
        normalized = path.as_posix()
        if AgenticToolGateway._is_sensitive_path(normalized):
            raise AgenticToolError(
                "SENSITIVE_PATH", "sensitive repository path cannot be read"
            )
        return normalized

    @staticmethod
    def _validate_pattern(value: Any) -> str:
        if not isinstance(value, str) or not value or len(value) > 512:
            raise AgenticToolError("INVALID_PATH", "path pattern is invalid")
        if "\x00" in value or "\\" in value or value.startswith("/"):
            raise AgenticToolError("INVALID_PATH", "path pattern is invalid")
        parts = PurePosixPath(value).parts
        if any(part == ".." for part in parts):
            raise AgenticToolError("INVALID_PATH", "path pattern escapes workspace")
        return value

    @staticmethod
    def _is_sensitive_path(path: str) -> bool:
        parts = [part.lower() for part in PurePosixPath(path).parts]
        if any(
            part in {".git", ".ssh", ".gnupg", ".aws", ".azure"}
            for part in parts
        ):
            return True
        name = parts[-1] if parts else ""
        if name == ".env" or name.startswith(".env."):
            return True
        if name in {
            ".npmrc",
            ".pypirc",
            ".netrc",
            ".git-credentials",
            "credentials",
            "credentials.json",
            "secrets.json",
            "id_rsa",
            "id_dsa",
            "id_ecdsa",
            "id_ed25519",
        }:
            return True
        return name.endswith((".pem", ".key", ".p12", ".pfx", ".jks"))

    def _resolve_file(self, path: Any) -> tuple[str, Path]:
        normalized = self._validate_relative_path(path)
        current = self._root
        for part in PurePosixPath(normalized).parts:
            current = current / part
            if current.is_symlink():
                raise AgenticToolError(
                    "INVALID_PATH", "symbolic links are not readable"
                )
        try:
            resolved = current.resolve(strict=True)
            resolved.relative_to(self._root)
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            raise AgenticToolError(
                "INVALID_PATH", "repository file is outside the fixed workspace"
            ) from error
        if not resolved.is_file():
            raise AgenticToolError("INVALID_PATH", "repository path is not a file")
        if resolved.stat().st_size > self._limits.max_file_bytes:
            raise AgenticToolError("FILE_TOO_LARGE", "repository file exceeds read limit")
        return normalized, resolved

    @staticmethod
    def _check_deadline(deadline: Optional[float]) -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise AgenticToolError(
                "TOOL_TIMEOUT", "agentic review tool exceeded its time budget"
            )

    def _iter_files(
        self,
        pattern: str,
        *,
        result_limit: bool = True,
        deadline: Optional[float] = None,
    ) -> tuple[list[tuple[str, Path]], bool]:
        pattern = self._validate_pattern(pattern)
        selected: list[tuple[str, Path]] = []
        scanned = 0
        truncated = False
        for directory, dirnames, filenames in os.walk(
            self._root, topdown=True, followlinks=False
        ):
            self._check_deadline(deadline)
            parent = Path(directory)
            safe_dirs = []
            for name in sorted(dirnames):
                candidate = parent / name
                relative = candidate.relative_to(self._root).as_posix()
                if candidate.is_symlink() or self._is_sensitive_path(relative):
                    continue
                safe_dirs.append(name)
            dirnames[:] = safe_dirs
            for name in sorted(filenames):
                candidate = parent / name
                relative = candidate.relative_to(self._root).as_posix()
                if candidate.is_symlink() or self._is_sensitive_path(relative):
                    continue
                scanned += 1
                if scanned > self._limits.max_search_files:
                    return selected, True
                if not candidate.is_file() or not fnmatchcase(relative, pattern):
                    continue
                selected.append((relative, candidate))
                if result_limit and len(selected) >= self._limits.max_results:
                    truncated = True
                    return selected, truncated
        return selected, truncated

    @staticmethod
    def _decode_text(path: Path, max_bytes: int) -> tuple[bytes, str]:
        data = path.read_bytes()
        if len(data) > max_bytes:
            raise AgenticToolError("FILE_TOO_LARGE", "repository file exceeds read limit")
        if b"\x00" in data[:8192]:
            raise AgenticToolError("BINARY_FILE", "binary repository file is not readable")
        try:
            return data, data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise AgenticToolError(
                "NON_UTF8_FILE", "non-UTF-8 repository file is not readable"
            ) from error

    @staticmethod
    def _redact(text: str) -> tuple[str, bool]:
        redacted = _PRIVATE_KEY_BLOCK.sub("[REDACTED PRIVATE KEY]", text)
        redacted = _QUOTED_SECRET.sub(
            lambda match: (
                f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(4)}"
            ),
            redacted,
        )
        redacted = _UNQUOTED_SECRET.sub(r"\1[REDACTED]", redacted)
        redacted = _URL_PASSWORD.sub(r"\1[REDACTED]\3", redacted)
        redacted = _KNOWN_SECRET_VALUE.sub("[REDACTED SECRET]", redacted)
        return redacted, redacted != text

    def _list_files(self, pattern: str, deadline: Optional[float] = None) -> Dict[str, Any]:
        files, truncated = self._iter_files(pattern, deadline=deadline)
        return {
            "tool": "list_files",
            "results": [
                {"path": path, "size_bytes": file_path.stat().st_size}
                for path, file_path in files
            ],
            "truncated": truncated,
        }

    def _validate_query(self, value: Any) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or "\x00" in value
            or len(value) > self._limits.max_query_chars
        ):
            raise AgenticToolError("INVALID_QUERY", "search query is invalid")
        return value

    def _search_text(
        self, query: Any, path_pattern: Any, deadline: Optional[float] = None
    ) -> Dict[str, Any]:
        query = self._validate_query(query)
        files, scan_truncated = self._iter_files(
            path_pattern, result_limit=False, deadline=deadline
        )
        results = []
        truncated = scan_truncated
        for path, file_path in files:
            self._check_deadline(deadline)
            try:
                _data, text = self._decode_text(file_path, self._limits.max_file_bytes)
            except AgenticToolError as error:
                if error.code in {"BINARY_FILE", "NON_UTF8_FILE", "FILE_TOO_LARGE"}:
                    continue
                raise
            for line_number, line in enumerate(text.splitlines(), start=1):
                self._check_deadline(deadline)
                start = 0
                while True:
                    column = line.find(query, start)
                    if column < 0:
                        break
                    excerpt, was_redacted = self._redact(line[:2_000])
                    results.append(
                        {
                            "path": path,
                            "line": line_number,
                            "column": column + 1,
                            "excerpt": excerpt,
                            "redacted": was_redacted,
                            "proof_required": True,
                        }
                    )
                    if len(results) >= self._limits.max_results:
                        truncated = True
                        break
                    start = column + max(1, len(query))
                if truncated and len(results) >= self._limits.max_results:
                    break
            if truncated and len(results) >= self._limits.max_results:
                break
        return {"tool": "search_text", "results": results, "truncated": truncated}

    def _read_source_span(
        self,
        path: Any,
        start_line: Any,
        end_line: Any,
        deadline: Optional[float] = None,
    ) -> tuple[Dict[str, Any], str]:
        self._check_deadline(deadline)
        if not isinstance(start_line, int) or isinstance(start_line, bool) or start_line < 1:
            raise AgenticToolError("INVALID_ARGUMENTS", "start_line must be positive")
        if end_line is not None and (
            not isinstance(end_line, int)
            or isinstance(end_line, bool)
            or end_line < start_line
        ):
            raise AgenticToolError("INVALID_ARGUMENTS", "end_line is invalid")
        normalized, file_path = self._resolve_file(path)
        data, text = self._decode_text(file_path, self._limits.max_file_bytes)
        lines = text.splitlines(keepends=True)
        self._check_deadline(deadline)
        if not lines or start_line > len(lines):
            raise AgenticToolError("LINE_NOT_FOUND", "start_line is outside the file")
        requested_end = end_line if end_line is not None else len(lines)
        actual_end = min(
            max(start_line, requested_end),
            len(lines),
            start_line + self._limits.max_read_lines - 1,
        )
        raw_span = "".join(lines[start_line - 1 : actual_end])
        display, redacted = self._redact(raw_span)
        proof = self._register_proof({
            "kind": "exact_source_span_v1",
            "execution_id": self._manifest.executionId,
            "head_sha": self.snapshot["head_sha"],
            "snapshot_id": self.snapshot_id,
            "path": normalized,
            "start_line": start_line,
            "end_line": actual_end,
            "source_digest": sha256(data).hexdigest(),
            "span_digest": sha256(raw_span.encode("utf-8")).hexdigest(),
        })
        return (
            {
                "tool": "read_file",
                "path": normalized,
                "start_line": start_line,
                "end_line": actual_end,
                "content": display,
                "redacted": redacted,
                "proof": proof,
                "truncated": actual_end < requested_end,
            },
            raw_span,
        )

    def _read_file(
        self,
        path: Any,
        start_line: Any = 1,
        end_line: Any = None,
        deadline: Optional[float] = None,
    ) -> Dict[str, Any]:
        result, _raw_span = self._read_source_span(
            path, start_line, end_line, deadline
        )
        return result

    def _find_symbol(
        self, name: Any, path_pattern: Any, deadline: Optional[float] = None
    ) -> Dict[str, Any]:
        if not isinstance(name, str) or not _SYMBOL.fullmatch(name):
            raise AgenticToolError("INVALID_QUERY", "symbol name is invalid")
        files, scan_truncated = self._iter_files(
            path_pattern, result_limit=False, deadline=deadline
        )
        matcher = re.compile(
            rf"(?<![A-Za-z0-9_$]){re.escape(name)}(?![A-Za-z0-9_$])"
        )
        results = []
        truncated = scan_truncated
        for path, file_path in files:
            self._check_deadline(deadline)
            try:
                _data, text = self._decode_text(file_path, self._limits.max_file_bytes)
            except AgenticToolError as error:
                if error.code in {"BINARY_FILE", "NON_UTF8_FILE", "FILE_TOO_LARGE"}:
                    continue
                raise
            for line_number, line in enumerate(text.splitlines(), start=1):
                self._check_deadline(deadline)
                for match in matcher.finditer(line):
                    excerpt, redacted = self._redact(line[:2_000])
                    results.append(
                        {
                            "path": path,
                            "line": line_number,
                            "column": match.start() + 1,
                            "excerpt": excerpt,
                            "redacted": redacted,
                            "proof_required": True,
                        }
                    )
                    if len(results) >= self._limits.max_results:
                        truncated = True
                        break
                if truncated and len(results) >= self._limits.max_results:
                    break
            if truncated and len(results) >= self._limits.max_results:
                break
        return {"tool": "find_symbol", "results": results, "truncated": truncated}

    @staticmethod
    def _diff_section_path(section: str) -> Optional[str]:
        for line in section.splitlines()[:20]:
            if line.startswith("+++ "):
                try:
                    return parse_git_marker_path(line, "+++")
                except GitDiffPathError:
                    return None
        first = section.splitlines()[0] if section else ""
        try:
            _old_path, new_path = parse_git_diff_header(first)
            return new_path
        except GitDiffPathError:
            return None

    def _locate_diff_hunk(
        self, path: Any, line: Any, deadline: Optional[float] = None
    ) -> Dict[str, Any]:
        self._check_deadline(deadline)
        normalized = self._validate_relative_path(path)
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            raise AgenticToolError("INVALID_ARGUMENTS", "line must be positive")
        sections = re.split(r"(?=^diff --git )", self._raw_diff, flags=re.MULTILINE)
        for section in sections:
            self._check_deadline(deadline)
            if not section or self._diff_section_path(section) != normalized:
                continue
            matches = list(_DIFF_HEADER.finditer(section))
            for index, match in enumerate(matches):
                old_start = int(match.group(1))
                old_count = int(match.group(2) or "1")
                new_start = int(match.group(3))
                new_count = int(match.group(4) or "1")
                if new_count <= 0 or not new_start <= line < new_start + new_count:
                    continue
                end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
                raw_hunk = section[match.start() : end].rstrip("\n") + "\n"
                return {
                    "path": normalized,
                    "requested_line": line,
                    "old_start": old_start,
                    "old_count": old_count,
                    "new_start": new_start,
                    "new_count": new_count,
                    "raw_hunk": raw_hunk,
                }
        raise AgenticToolError(
            "DIFF_HUNK_NOT_FOUND", "no immutable diff hunk contains the requested line"
        )

    def _read_diff_hunk(
        self, path: Any, line: Any, deadline: Optional[float] = None
    ) -> Dict[str, Any]:
        located = self._locate_diff_hunk(path, line, deadline)
        raw_hunk = located.pop("raw_hunk")
        content, redacted = self._redact(raw_hunk)
        proof = self._register_proof({
            "kind": "exact_diff_hunk_v1",
            "execution_id": self._manifest.executionId,
            "head_sha": self.snapshot["head_sha"],
            "snapshot_id": self.snapshot_id,
            "diff_digest": self._manifest.diffDigest,
            "path": located["path"],
            "requested_line": located["requested_line"],
            "hunk_digest": sha256(raw_hunk.encode("utf-8")).hexdigest(),
        })
        return {
            "tool": "read_diff_hunk",
            **located,
            "content": content,
            "redacted": redacted,
            "proof": proof,
            "truncated": False,
        }

    def _empty_rag_response(
        self, tool_name: str, anchors: Iterable[str], code: str, detail: str
    ) -> Dict[str, Any]:
        result = build_related_context_pack(
            chunks=[],
            anchor_paths=list(anchors),
            snapshot=self.snapshot,
            execution_id=self._manifest.executionId,
            source_branch=self._source_branch,
            base_branch=self._base_branch,
            anchor_digests=manifest_anchor_digests(self._request),
            base_index_available=self._base_index_available,
            additional_gaps=[
                ContextGapV1(code=code, detail=detail, affected_paths=list(anchors))
            ],
        )
        return self._rag_payload(tool_name, result.pack, unsafe_rejected=0)

    @property
    def _base_index_available(self) -> bool:
        return self._rag_index_version == (
            f"rag-commit-{self.snapshot['base_sha']}"
        )

    @property
    def _rag_disabled(self) -> bool:
        return self._rag_index_version == "rag-disabled"

    def _safe_rag_chunks(
        self, chunks: Iterable[Dict[str, Any]]
    ) -> tuple[list[Dict[str, Any]], int]:
        accepted = []
        rejected = 0
        for chunk in chunks:
            if not isinstance(chunk, dict):
                rejected += 1
                continue
            metadata = chunk.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            path = (
                metadata.get("path")
                or metadata.get("file_path")
                or chunk.get("path")
                or chunk.get("file_path")
            )
            try:
                self._validate_relative_path(path)
            except AgenticToolError:
                rejected += 1
                continue
            accepted.append(chunk)
        return accepted, rejected

    def _build_rag_pack(
        self,
        chunks: Iterable[Dict[str, Any]],
        anchors: Iterable[str],
        *,
        retrieval_gap: Optional[ContextGapV1] = None,
    ):
        safe_chunks, unsafe_rejected = self._safe_rag_chunks(chunks)
        gaps = [retrieval_gap] if retrieval_gap is not None else []
        if unsafe_rejected:
            gaps.append(
                ContextGapV1(
                    code="unsafe_rag_path_rejected",
                    detail=(
                        f"Rejected {unsafe_rejected} RAG results with paths outside "
                        "the fixed repository namespace."
                    ),
                    affected_paths=list(anchors),
                )
            )
        build = build_related_context_pack(
            chunks=safe_chunks,
            anchor_paths=list(anchors),
            snapshot=self.snapshot,
            execution_id=self._manifest.executionId,
            source_branch=self._source_branch,
            base_branch=self._base_branch,
            anchor_digests=manifest_anchor_digests(self._request),
            base_index_available=self._base_index_available,
            additional_gaps=gaps,
        )
        return build.pack, unsafe_rejected

    def _rag_payload(
        self,
        tool_name: str,
        pack,
        *,
        unsafe_rejected: int,
        path_pattern: str = "*",
        query_source_proof: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        path_pattern = self._validate_pattern(path_pattern)
        results = []
        pattern_omitted = 0
        for item in pack.items:
            if not fnmatchcase(item.path, path_pattern):
                pattern_omitted += 1
                continue
            content, redacted = self._redact(item.content)
            results.append(
                {
                    "item_id": item.item_id,
                    "path": item.path,
                    "revision": item.revision,
                    "start_line": item.start_line,
                    "end_line": item.end_line,
                    "symbol": item.symbol,
                    "relationship_type": item.relationship_type,
                    "retrieval_method": item.retrieval_method,
                    "score": item.score,
                    "evidence_strength": item.evidence_strength,
                    "content_digest": item.content_digest,
                    "selection_reason": item.selection_reason,
                    "snapshot_verified": item.snapshot_verified,
                    "content": content,
                    "redacted": redacted,
                    "proof_required": True,
                    "lead_receipt": {
                        "snapshot_id": self.snapshot_id,
                        "revision": item.revision,
                        "content_digest": item.content_digest,
                    },
                }
            )
            if len(results) >= self._limits.max_results:
                break
        payload = {
            "tool": tool_name,
            "results": results,
            "proof_status": "exact_source_read_required",
            "snapshot_receipt": (
                pack.receipt.model_dump(mode="json") if pack.receipt else None
            ),
            "gaps": [gap.model_dump(mode="json") for gap in pack.gaps],
            "rejected_result_count": pack.rejected_chunk_count + unsafe_rejected,
            "pattern_omitted_count": pattern_omitted,
            "truncated": (
                pack.truncated_chunk_count > 0
                or pattern_omitted > 0
                or len(results) < len(pack.items) - pattern_omitted
            ),
        }
        if query_source_proof is not None:
            payload["query_source_proof"] = query_source_proof
        return payload

    async def _rag_search(self, query: Any, path_pattern: Any) -> Dict[str, Any]:
        query = self._validate_query(query)
        path_pattern = self._validate_pattern(path_pattern)
        if self._rag_disabled:
            return self._empty_rag_response(
                "rag_search",
                self._changed_files,
                "rag_disabled",
                (
                    "RAG was disabled in the immutable execution configuration; "
                    "continue with exact local repository tools."
                ),
            )
        if self._rag_client is None:
            return self._empty_rag_response(
                "rag_search",
                self._changed_files,
                "rag_client_unavailable",
                "RAG is unavailable; continue with exact local repository tools.",
            )
        coordinates = [
            (self._source_branch, self.snapshot["head_sha"]),
            (self._base_branch, self.snapshot["base_sha"]),
        ]
        coordinates = list(dict.fromkeys(coordinates))
        try:
            responses = await asyncio.gather(
                *(
                    self._rag_client.semantic_search(
                        query=query,
                        workspace=self._workspace,
                        project=self._project,
                        branch=branch,
                        top_k=self._limits.max_results,
                        revision=revision,
                        snapshot=self.snapshot,
                        execution_id=self._manifest.executionId,
                    )
                    for branch, revision in coordinates
                )
            )
            chunks = [
                chunk
                for response in responses
                if isinstance(response, Mapping)
                for chunk in (response.get("results") or [])
            ]
            pack, unsafe = self._build_rag_pack(chunks, self._changed_files)
        except Exception:
            return self._empty_rag_response(
                "rag_search",
                self._changed_files,
                "rag_retrieval_failed",
                "Semantic RAG retrieval failed; continue with exact local tools.",
            )
        return self._rag_payload(
            "rag_search", pack, unsafe_rejected=unsafe, path_pattern=path_pattern
        )

    async def _rag_related(self, path_or_symbol: Any) -> Dict[str, Any]:
        value = self._validate_query(path_or_symbol)
        file_paths: list[str]
        identifiers: Optional[list[str]]
        looks_like_path = "/" in value or value.startswith(".")
        if looks_like_path:
            file_paths = [self._validate_relative_path(value)]
            identifiers = None
        else:
            if not _SYMBOL.fullmatch(value):
                raise AgenticToolError("INVALID_QUERY", "path or symbol is invalid")
            file_paths = self._changed_files[: self._limits.max_results]
            identifiers = [value]
        anchors = file_paths or self._changed_files
        if self._rag_disabled:
            return self._empty_rag_response(
                "rag_related",
                anchors,
                "rag_disabled",
                (
                    "RAG was disabled in the immutable execution configuration; "
                    "continue with exact local repository tools."
                ),
            )
        if self._rag_client is None:
            return self._empty_rag_response(
                "rag_related",
                anchors,
                "rag_client_unavailable",
                "RAG is unavailable; continue with exact local repository tools.",
            )
        try:
            response = await self._rag_client.get_deterministic_context(
                workspace=self._workspace,
                project=self._project,
                branches=list(dict.fromkeys([self._source_branch, self._base_branch])),
                file_paths=file_paths,
                limit_per_file=min(20, self._limits.max_results),
                pr_number=self._manifest.pullRequestId,
                pr_changed_files=self._changed_files,
                additional_identifiers=identifiers,
                snapshot=self.snapshot,
                execution_id=self._manifest.executionId,
            )
            chunks = flatten_deterministic_context(response)
            pack, unsafe = self._build_rag_pack(chunks, anchors)
        except Exception:
            return self._empty_rag_response(
                "rag_related",
                anchors,
                "rag_retrieval_failed",
                "Structural RAG retrieval failed; continue with exact local tools.",
            )
        return self._rag_payload("rag_related", pack, unsafe_rejected=unsafe)

    async def _rag_similar_code(
        self, path: Any, start_line: Any, end_line: Any
    ) -> Dict[str, Any]:
        source_result, _raw_span = self._read_source_span(path, start_line, end_line)
        query = (
            "Find another implementation with the same behavior as this exact source "
            f"span from {source_result['path']}:{source_result['start_line']}-"
            f"{source_result['end_line']}:\n{source_result['content']}"
        )
        query = query[: self._limits.max_query_chars]
        anchors = [source_result["path"]]
        if self._rag_disabled:
            payload = self._empty_rag_response(
                "rag_similar_code",
                anchors,
                "rag_disabled",
                (
                    "RAG was disabled in the immutable execution configuration; "
                    "continue with exact local repository tools."
                ),
            )
            payload["query_source_proof"] = source_result["proof"]
            return payload
        if self._rag_client is None:
            payload = self._empty_rag_response(
                "rag_similar_code",
                anchors,
                "rag_client_unavailable",
                "RAG is unavailable; continue with exact local repository tools.",
            )
            payload["query_source_proof"] = source_result["proof"]
            return payload
        try:
            chunks = await self._rag_client.search_for_duplicates(
                workspace=self._workspace,
                project=self._project,
                branch=self._source_branch,
                queries=[query],
                top_k=self._limits.max_results,
                base_branch=self._base_branch,
                snapshot=self.snapshot,
                execution_id=self._manifest.executionId,
            )
            pack, unsafe = self._build_rag_pack(chunks, anchors)
        except Exception:
            payload = self._empty_rag_response(
                "rag_similar_code",
                anchors,
                "rag_retrieval_failed",
                "Similar-code RAG retrieval failed; continue with exact local tools.",
            )
            payload["query_source_proof"] = source_result["proof"]
            return payload
        return self._rag_payload(
            "rag_similar_code",
            pack,
            unsafe_rejected=unsafe,
            query_source_proof=source_result["proof"],
        )
