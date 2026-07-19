"""Bounded, read-only tools for an extracted agentic repository snapshot."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from fnmatch import fnmatchcase
import json
import os
from pathlib import Path, PurePosixPath
import re
import time
from typing import Any, Dict, Mapping, Optional

from model.dtos import ReviewRequestDto
from utils.git_diff_paths import (
    GitDiffPathError,
    parse_git_diff_header,
    parse_git_marker_path,
)


_TOOL_NAMES = (
    "search_text",
    "read_file",
    "read_diff_hunk",
)
_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_DIFF_HEADER = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@.*$",
    flags=re.MULTILINE,
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?"
    r"-----END [^-\r\n]*PRIVATE KEY-----",
    flags=re.DOTALL | re.IGNORECASE,
)
_SECRET_NAME = (
    r"(?<![A-Za-z0-9_-])(?:[A-Za-z0-9]+[_-])*"
    r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|passwd|"
    r"authorization|private[_-]?key|credentials?)(?:[_-][A-Za-z0-9]+)*"
)
_QUOTED_SECRET = re.compile(
    r"(?i)(" + _SECRET_NAME + r"\s*[:=]\s*)([\"'])(.*?)(\2)"
)
_UNQUOTED_SECRET = re.compile(
    r"(?i)(" + _SECRET_NAME + r"\s*[:=]\s*)([^\s,;}{]+)"
)
_KNOWN_SECRET_VALUE = re.compile(
    r"(?i)(?:gh[pousr]_[A-Za-z0-9]{30,255}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,255}|"
    r"sk-[A-Za-z0-9_-]{20,255}|AKIA[0-9A-Z]{16})"
)
_URL_PASSWORD = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]*://[^\s/:@]+:)([^\s/@]+)(@)"
)


class AgenticToolError(ValueError):
    """A local tool rejected input or could not safely produce a result."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ToolGatewayLimits:
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
        positive = (
            "max_calls",
            "max_results",
            "max_output_bytes_per_call",
            "max_total_output_bytes",
            "max_file_bytes",
            "max_search_files",
            "max_read_lines",
            "max_query_chars",
        )
        for name in positive:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not 0.005 <= self.call_timeout_seconds <= 60:
            raise ValueError("call_timeout_seconds must be between 0.005 and 60")


_TOOL_DEFINITIONS = (
    {
        "name": "search_text",
        "description": "Find literal text in repository files.",
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
        "description": "Read a bounded source span from one repository file.",
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
        "name": "read_diff_hunk",
        "description": "Read the request diff hunk containing a new-side line.",
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
)

_ARGUMENTS = {
    "search_text": (frozenset({"query", "path_pattern"}), frozenset({"query"})),
    "read_file": (
        frozenset({"path", "start_line", "end_line"}),
        frozenset({"path"}),
    ),
    "read_diff_hunk": (
        frozenset({"path", "line"}),
        frozenset({"path", "line"}),
    ),
}


class AgenticToolGateway:
    """Expose only bounded local reads rooted in one prepared workspace."""

    def __init__(
        self,
        workspace_root: Path | str,
        request: ReviewRequestDto,
        limits: Optional[ToolGatewayLimits] = None,
    ) -> None:
        if request.reviewApproach != "AGENTIC":
            raise AgenticToolError(
                "INVALID_REQUEST", "agentic tools require an AGENTIC request"
            )
        descriptor = request.agenticRepository
        if descriptor is None:
            raise AgenticToolError(
                "INVALID_REQUEST", "agentic repository coordinates are missing"
            )
        for name in ("previousCommitHash", "currentCommitHash"):
            value = getattr(request, name, None)
            if not isinstance(value, str) or not _SHA.fullmatch(value):
                raise AgenticToolError(
                    "INVALID_REQUEST", f"{name} is missing or malformed"
                )
        if descriptor.snapshotSha != request.currentCommitHash:
            raise AgenticToolError(
                "INVALID_REQUEST",
                "repository snapshot does not match currentCommitHash",
            )
        if not isinstance(request.rawDiff, str) or not request.rawDiff:
            raise AgenticToolError("INVALID_REQUEST", "rawDiff is required")

        root_input = Path(workspace_root)
        if root_input.is_symlink() or not root_input.is_dir():
            raise AgenticToolError(
                "INVALID_WORKSPACE", "workspace root must be a non-symlink directory"
            )
        self._root = root_input.resolve(strict=True)
        self._request = request
        self._limits = limits or ToolGatewayLimits()
        self._raw_diff = request.rawDiff
        self._calls_used = 0
        self._output_bytes = 0
        self._state_lock = asyncio.Lock()

    @staticmethod
    def tool_definitions() -> list[dict[str, Any]]:
        return deepcopy(list(_TOOL_DEFINITIONS))

    @staticmethod
    def langchain_tool_definitions() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": item["name"],
                    "description": item["description"],
                    "parameters": deepcopy(item["inputSchema"]),
                },
            }
            for item in _TOOL_DEFINITIONS
        ]

    async def invoke(
        self, tool_name: str, arguments: Optional[Mapping[str, Any]] = None
    ) -> Dict[str, Any]:
        if tool_name not in _TOOL_NAMES:
            raise AgenticToolError("UNKNOWN_TOOL", "unknown agentic review tool")
        values = dict(arguments or {})
        self._validate_arguments(tool_name, values)
        async with self._state_lock:
            if self._calls_used >= self._limits.max_calls:
                raise AgenticToolError(
                    "CALL_BUDGET_EXHAUSTED", "agentic review tool budget is exhausted"
                )
            self._calls_used += 1

        try:
            executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="agentic-tool"
            )
            worker = asyncio.ensure_future(
                asyncio.get_running_loop().run_in_executor(
                    executor, self._dispatch, tool_name, values
                )
            )
            try:
                result = await self._wait_for_worker(
                    worker, self._limits.call_timeout_seconds
                )
            finally:
                executor.shutdown(
                    wait=worker.done() and not worker.cancelled(),
                    cancel_futures=True,
                )
        except TimeoutError as error:
            raise AgenticToolError(
                "TOOL_TIMEOUT", "agentic review tool exceeded its time budget"
            ) from error
        except AgenticToolError:
            raise
        except Exception as error:
            raise AgenticToolError(
                "TOOL_FAILURE", "agentic review tool failed safely"
            ) from error
        return await self._bound_output(tool_name, result)

    @staticmethod
    async def _wait_for_worker(
        worker: asyncio.Future[Dict[str, Any]], timeout: float
    ) -> Dict[str, Any]:
        """Await blocking I/O without losing the wall-clock timeout."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not worker.done():
            remaining = deadline - loop.time()
            if remaining <= 0:
                worker.cancel()
                raise TimeoutError
            await asyncio.wait({worker}, timeout=min(0.05, remaining))
        return worker.result()

    @staticmethod
    def _validate_arguments(tool_name: str, arguments: Mapping[str, Any]) -> None:
        allowed, required = _ARGUMENTS[tool_name]
        observed = set(arguments)
        if observed - allowed or required - observed:
            raise AgenticToolError(
                "INVALID_ARGUMENTS", f"invalid arguments for {tool_name}"
            )

    def _dispatch(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        deadline = time.monotonic() + self._limits.call_timeout_seconds
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
        return self._read_diff_hunk(
            arguments["path"], arguments["line"], deadline
        )

    async def _bound_output(
        self, tool_name: str, result: Dict[str, Any]
    ) -> Dict[str, Any]:
        async with self._state_lock:
            remaining = self._limits.max_total_output_bytes - self._output_bytes
            if remaining < 128:
                raise AgenticToolError(
                    "OUTPUT_BUDGET_EXHAUSTED",
                    "agentic review output budget is exhausted",
                )
            budget = min(self._limits.max_output_bytes_per_call, remaining)
            bounded = {"tool": tool_name, **result}
            encoded = self._encode(bounded)
            if len(encoded) > budget:
                bounded = self._truncate(bounded, budget)
                encoded = self._encode(bounded)
            self._output_bytes += len(encoded)
            return bounded

    @staticmethod
    def _encode(value: Mapping[str, Any]) -> bytes:
        return json.dumps(
            value, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def _truncate(self, payload: Dict[str, Any], budget: int) -> Dict[str, Any]:
        bounded = {**payload, "truncated": True}
        results = bounded.get("results")
        if isinstance(results, list):
            while results and len(self._encode(bounded)) > budget:
                results.pop()
        content = bounded.get("content")
        while (
            isinstance(content, str)
            and content
            and len(self._encode(bounded)) > budget
        ):
            content = content[: len(content) // 2]
            bounded["content"] = content + "…"
        if len(self._encode(bounded)) <= budget:
            return bounded
        minimal = {"tool": payload["tool"], "truncated": True}
        if len(self._encode(minimal)) > budget:
            raise AgenticToolError(
                "OUTPUT_BUDGET_EXHAUSTED", "tool output budget is too small"
            )
        return minimal

    @staticmethod
    def _validate_relative_path(value: Any) -> str:
        if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
            raise AgenticToolError("INVALID_PATH", "repository path is invalid")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise AgenticToolError("INVALID_PATH", "repository path escapes workspace")
        normalized = path.as_posix()
        if AgenticToolGateway._is_sensitive_path(normalized):
            raise AgenticToolError(
                "SENSITIVE_PATH", "sensitive repository path cannot be read"
            )
        return normalized

    @staticmethod
    def _validate_pattern(value: Any) -> str:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 512
            or "\x00" in value
            or "\\" in value
            or value.startswith("/")
            or any(part == ".." for part in PurePosixPath(value).parts)
        ):
            raise AgenticToolError("INVALID_PATH", "path pattern is invalid")
        return value

    @staticmethod
    def _is_sensitive_path(path: str) -> bool:
        parts = [part.lower() for part in PurePosixPath(path).parts]
        if any(part in {".git", ".ssh", ".gnupg", ".aws", ".azure"} for part in parts):
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
        for directory, dirnames, filenames in os.walk(
            self._root, topdown=True, followlinks=False
        ):
            self._check_deadline(deadline)
            parent = Path(directory)
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if not (parent / name).is_symlink()
                and not self._is_sensitive_path(
                    (parent / name).relative_to(self._root).as_posix()
                )
            ]
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
                    return selected, True
        return selected, False

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
        self, query: Any, path_pattern: Any, deadline: float
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
                _data, text = self._decode_text(
                    file_path, self._limits.max_file_bytes
                )
            except AgenticToolError as error:
                if error.code in {"BINARY_FILE", "NON_UTF8_FILE", "FILE_TOO_LARGE"}:
                    continue
                raise
            for line_number, line in enumerate(text.splitlines(), start=1):
                column = line.find(query)
                if column < 0:
                    continue
                excerpt, redacted = self._redact(line[:2_000])
                results.append(
                    {
                        "path": path,
                        "line": line_number,
                        "column": column + 1,
                        "excerpt": excerpt,
                        "redacted": redacted,
                    }
                )
                if len(results) >= self._limits.max_results:
                    return {"results": results, "truncated": True}
        return {"results": results, "truncated": truncated}

    def _read_file(
        self,
        path: Any,
        start_line: Any,
        end_line: Any,
        deadline: float,
    ) -> Dict[str, Any]:
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
        _data, text = self._decode_text(file_path, self._limits.max_file_bytes)
        lines = text.splitlines(keepends=True)
        if not lines or start_line > len(lines):
            raise AgenticToolError("LINE_NOT_FOUND", "start_line is outside the file")
        requested_end = end_line if end_line is not None else len(lines)
        actual_end = min(
            requested_end,
            len(lines),
            start_line + self._limits.max_read_lines - 1,
        )
        raw_span = "".join(lines[start_line - 1 : actual_end])
        display, redacted = self._redact(raw_span)
        return {
            "path": normalized,
            "start_line": start_line,
            "end_line": actual_end,
            "content": display,
            "redacted": redacted,
            "truncated": actual_end < requested_end,
        }

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
            "DIFF_HUNK_NOT_FOUND", "no request diff hunk contains the requested line"
        )

    def _read_diff_hunk(
        self, path: Any, line: Any, deadline: float
    ) -> Dict[str, Any]:
        located = self._locate_diff_hunk(path, line, deadline)
        raw_hunk = located.pop("raw_hunk")
        content, redacted = self._redact(raw_hunk)
        return {
            **located,
            "content": content,
            "redacted": redacted,
            "truncated": False,
        }
