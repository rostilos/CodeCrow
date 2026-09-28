"""Exact, descriptor-bound local source access for review verification."""
from __future__ import annotations
from bisect import bisect_right
from contextlib import contextmanager
from fnmatch import fnmatchcase
from functools import lru_cache
import json
import logging
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import regex

logger = logging.getLogger(__name__)

# Interrupt pathological matching, without limiting source or result content.
_REGEX_MATCH_TIMEOUT_SECONDS = 1.0


class LocalReviewSource:
    """Expose the host's target snapshot with its proposed-file overlay."""

    def __init__(self, binding: Mapping[str, Any], changed_paths: Sequence[str] = ()):
        target = str(binding.get("target_repo_path") or "")
        overlay = str(binding.get("review_overlay_path") or "")
        self.target = Path(target).resolve() if target else None
        self.overlay = Path(overlay).resolve() if overlay else None
        self.changed: set[str] = set()
        self.metadata_diagnostics: list[str] = []
        for path in changed_paths:
            try:
                self.changed.add(self._path(path))
            except (TypeError, ValueError):
                self.metadata_diagnostics.append("A changed path could not be used for local source lookup")
        if self.metadata_diagnostics:
            logger.warning("Local review source skipped invalid changed-path metadata")
        self.deleted: set[str] = set()
        self.overlay_error = "proposed-file overlay is unavailable"
        if self.overlay:
            try:
                manifest = json.loads(self._read_file(self.overlay, "manifest.json").decode("utf-8"))
                if not isinstance(manifest, dict):
                    raise ValueError("overlay manifest must be an object")
                for field in ("changedFiles", "deletedFiles"):
                    if not isinstance(manifest.get(field), list):
                        raise ValueError("overlay manifest path lists are unavailable")
                changed = {self._path(path) for path in manifest["changedFiles"]}
                deleted = {self._path(path) for path in manifest["deletedFiles"]}
                self.changed.update(changed | deleted)
                self.deleted = deleted
                self.overlay_error = ""
            except (OSError, TypeError, ValueError):
                # An incomplete overlay is a source diagnostic, not a failed review.
                self.overlay_error = "proposed-file overlay manifest is unavailable or malformed"

    @staticmethod
    def _path(path: str) -> str:
        if not isinstance(path, str) or not path or "\x00" in path:
            raise ValueError("a repository-relative path is required")
        normalized = PurePosixPath(path.replace("\\", "/"))
        if normalized.is_absolute() or ".." in normalized.parts or not normalized.parts:
            raise ValueError("path must remain within the bound repository")
        if normalized.parts[0] == ".git":
            raise ValueError("repository metadata is not review source")
        return normalized.as_posix()

    @classmethod
    @contextmanager
    def _open_file(cls, root: Path, path: str):
        # Every component is opened relative to a pinned directory descriptor.
        # A repository entry replaced after lookup cannot redirect this read.
        parts = PurePosixPath(cls._path(path)).parts
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        directory_fd = os.open(root, flags | os.O_DIRECTORY)
        file_fd = None
        try:
            for component in parts[:-1]:
                child_fd = os.open(component, flags | os.O_DIRECTORY, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = child_fd
            file_fd = os.open(parts[-1], flags | os.O_NONBLOCK, dir_fd=directory_fd)
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise ValueError("source entry is not a regular file")
            yield file_fd
        finally:
            if file_fd is not None:
                os.close(file_fd)
            os.close(directory_fd)

    @classmethod
    def _read_file(cls, root: Path, path: str) -> bytes:
        with cls._open_file(root, path) as descriptor:
            with os.fdopen(os.dup(descriptor), "rb") as source:
                return source.read()

    def _location(self, path: str, side: str) -> tuple[tuple[Path, str] | None, str, str]:
        if side not in {"proposed", "target"}:
            raise ValueError("side must be proposed or target")
        if side == "proposed" and path in self.deleted:
            return None, "deleted", "review_overlay"
        if side == "proposed" and path in self.changed:
            if self.overlay_error or self.overlay is None:
                return None, "unavailable", "review_overlay"
            return (self.overlay, "files/" + path), "ready", "review_overlay"
        if side == "proposed" and self.overlay_error:
            # Without the manifest we cannot know whether a nominally unchanged
            # file was actually renamed or replaced by an unavailable PR file.
            return None, "unavailable", "review_overlay"
        if self.target is None:
            return None, "unavailable", "target_tree"
        return (self.target, path), "ready", "target_tree"

    def read(self, path: str, *, side: str = "proposed", start_line: int = 1,
             end_line: int | None = None) -> dict[str, Any]:
        """Read an explicit semantic range, or the complete file when requested."""
        try:
            path = self._path(path)
            if start_line < 1 or (end_line is not None and end_line < start_line):
                raise ValueError("invalid source line range")
            location, status, origin = self._location(path, side)
            result: dict[str, Any] = {"status": status, "path": path, "side": side, "origin": origin}
            if location is None:
                if status == "unavailable":
                    result["diagnostic"] = self.overlay_error or "host-bound source snapshot is unavailable"
                return result
            content = self._read_file(*location)
            if b"\x00" in content:
                return {**result, "status": "binary"}
            lines = content.decode("utf-8").splitlines(keepends=True)
            stop = len(lines) if end_line is None else min(end_line, len(lines))
            return {**result, "startLine": start_line, "endLine": stop,
                    "totalLines": len(lines), "content": "".join(lines[start_line - 1:stop])}
        except FileNotFoundError:
            # A changed path missing from the overlay is not absent from the PR.
            status = "unavailable" if side == "proposed" and path in self.changed else "missing"
            return {"status": status, "path": path, "side": side,
                    "diagnostic": "requested source file is unavailable"}
        except UnicodeDecodeError:
            return {"status": "binary", "path": path, "side": side}
        except (OSError, TypeError, ValueError) as error:
            return {"status": "unavailable", "path": path, "side": side,
                    "diagnostic": str(error) if isinstance(error, ValueError) else "source read unavailable"}

    def _paths(self, side: str, errors: list[str], scopes: Sequence[str] = ()) -> list[str]:
        def selected(path: str) -> bool:
            return not scopes or any(path == scope or path.startswith(scope + "/") for scope in scopes)

        paths = {path for path in self.changed if selected(path)} if side == "proposed" else set()
        covered = {scope for scope in scopes
                   if any(path == scope or path.startswith(scope + "/") for path in paths)}
        if self.target:
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_DIRECTORY
            # Hold only the current directory and its ancestors. Opening every
            # sibling before visiting it exhausts descriptors in wide checkouts.
            pending: list[tuple[int, str, Any]] = []

            def enter(directory_fd: int, prefix: str) -> None:
                try:
                    scanner = os.scandir(directory_fd)
                except OSError:
                    os.close(directory_fd)
                    errors.append(prefix or "<unreadable-directory>")
                    return
                pending.append((directory_fd, prefix, scanner))

            try:
                enter(os.open(self.target, flags), "")
                while pending:
                    directory_fd, prefix, scanner = pending[-1]
                    try:
                        entry = next(scanner)
                    except (StopIteration, OSError) as error:
                        if isinstance(error, OSError):
                            errors.append(prefix or "<unreadable-directory>")
                        scanner.close()
                        os.close(directory_fd)
                        pending.pop()
                        continue
                    if entry.name == ".git":
                        continue
                    path = prefix + entry.name
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if path in scopes:
                                covered.add(path)
                            if selected(path) or any(scope.startswith(path + "/") for scope in scopes):
                                enter(os.open(entry.name, flags, dir_fd=directory_fd), path + "/")
                        elif entry.is_file(follow_symlinks=False) and selected(path):
                            paths.add(path)
                            covered.update(scope for scope in scopes
                                           if path == scope or path.startswith(scope + "/"))
                        elif entry.is_symlink() and path in scopes:
                            errors.append(path)
                            covered.add(path)
                    except OSError:
                        errors.append(path)
            except OSError:
                errors.append("<unreadable-directory>")
            finally:
                for directory_fd, _prefix, scanner in pending:
                    scanner.close()
                    os.close(directory_fd)
        errors.extend(scope for scope in scopes if scope not in covered)
        return sorted(paths - self.deleted if side == "proposed" else paths)

    def _search_paths(self, side: str, paths: Sequence[str], unavailable: list[str]) -> list[str]:
        if side not in {"proposed", "target"}:
            raise ValueError("side must be proposed or target")
        scopes = sorted({self._path(path) for path in paths if path not in {".", "./"}})
        if any(path in {".", "./"} for path in paths):
            scopes = []
        if self.target is None and not (
            side == "proposed" and scopes and all(scope in self.changed for scope in scopes)
        ):
            unavailable.append("<target-snapshot>")
        return self._paths(side, unavailable, scopes)

    def _search_result(self, *, side: str, unavailable: Sequence[str], **values: Any) -> dict[str, Any]:
        partial = bool(unavailable or (side == "proposed" and self.overlay_error))
        return {"status": "partial" if partial else "ready", "side": side, **values,
                "unavailablePaths": sorted(set(unavailable)), "complete": not partial,
                "diagnostic": ("Search did not cover all requested source; absence is not evidence."
                               if partial else "")}

    @staticmethod
    def _glob_match(path: str, pattern: str) -> bool:
        """Basenames match anywhere; slash patterns are repository-relative globs."""
        pattern_parts = PurePosixPath(pattern).parts
        path_parts = PurePosixPath(path).parts
        if len(pattern_parts) == 1:
            return fnmatchcase(path_parts[-1], pattern_parts[0])

        @lru_cache(maxsize=None)
        def match(path_index: int, pattern_index: int) -> bool:
            if pattern_index == len(pattern_parts):
                return path_index == len(path_parts)
            component = pattern_parts[pattern_index]
            if component == "**":
                return (match(path_index, pattern_index + 1)
                        or path_index < len(path_parts) and match(path_index + 1, pattern_index))
            return (path_index < len(path_parts)
                    and fnmatchcase(path_parts[path_index], component)
                    and match(path_index + 1, pattern_index + 1))

        return match(0, 0)

    def find_files(self, pattern: str, *, paths: Sequence[str] = (), side: str = "proposed") -> dict[str, Any]:
        """Find snapshot file paths without reading or returning their source."""
        unavailable: list[str] = []
        try:
            pattern = self._path(pattern)
            selected = self._search_paths(side, paths, unavailable)
        except (TypeError, ValueError) as error:
            return {"status": "unavailable", "complete": False, "diagnostic": str(error)}
        matches = []
        for path in selected:
            if not self._glob_match(path, pattern):
                continue
            # A manifest path does not establish that its source is readable.
            # Do not replace a missing changed file with the old target version.
            if side == "proposed" and path in self.changed:
                location, status, _origin = self._location(path, side)
                if location is None:
                    if status != "deleted":
                        unavailable.append(path)
                    continue
                try:
                    with self._open_file(*location):
                        pass
                except (OSError, ValueError):
                    unavailable.append(path)
                    continue
            matches.append(path)
        return self._search_result(side=side, unavailable=unavailable, pattern=pattern, paths=matches)

    def grep(self, query: str, *, paths: Sequence[str] = (), side: str = "proposed",
             case_sensitive: bool = True, mode: str = "literal") -> dict[str, Any]:
        """Return all matching lines with exact text and explicit search semantics.

        Regex matching uses multiline anchors. A match spanning lines includes
        each affected line. Execution timeout is an incomplete search, never a
        negative result; no file, line, match or character limit is applied.
        """
        if not isinstance(query, str) or not query:
            return {"status": "unavailable", "complete": False, "diagnostic": "a nonempty query is required"}
        if mode not in {"literal", "regex"}:
            return {"status": "unavailable", "complete": False,
                    "diagnostic": "mode must be literal or regex"}
        expression = None
        if mode == "regex":
            try:
                expression = regex.compile(query, regex.MULTILINE | (0 if case_sensitive else regex.IGNORECASE))
            except regex.error as error:
                return {"status": "unavailable", "complete": False, "query": query, "mode": mode,
                        "diagnostic": f"Invalid regular expression: {error}. Correct the pattern or use mode=literal."}
        unavailable: list[str] = []
        try:
            selected = self._search_paths(side, paths, unavailable)
        except (TypeError, ValueError) as error:
            return {"status": "unavailable", "complete": False, "diagnostic": str(error)}
        needle = query if case_sensitive else query.casefold()
        matches: list[dict[str, Any]] = []
        interrupted = False
        for path_index, path in enumerate(selected):
            source = self.read(path, side=side)
            if source["status"] not in {"ready", "binary", "deleted"}:
                unavailable.append(path)
                continue
            if source["status"] != "ready":
                continue
            content = source["content"]
            source_lines = content.splitlines()
            if expression is None:
                line_numbers = {index for index, line in enumerate(source_lines)
                                if needle in (line if case_sensitive else line.casefold())}
            else:
                starts, offset = [], 0
                for line in content.splitlines(keepends=True):
                    starts.append(offset)
                    offset += len(line)
                line_numbers = set()
                try:
                    for match in expression.finditer(content, timeout=_REGEX_MATCH_TIMEOUT_SECONDS):
                        first = max(0, bisect_right(starts, match.start()) - 1)
                        last = max(first, bisect_right(starts, max(match.start(), match.end() - 1)) - 1)
                        line_numbers.update(range(first, min(last + 1, len(source_lines))))
                except TimeoutError:
                    # The engine interrupts matching. A thread timeout would
                    # leave expensive regex work running in the worker.
                    # Do not spend another timeout on every remaining file.
                    unavailable.extend(selected[path_index:])
                    interrupted = True
            lines = [{"line": index + 1, "text": source_lines[index]} for index in sorted(line_numbers)]
            if lines:
                matches.append({"path": path, "matches": lines})
            if interrupted:
                break
        result = self._search_result(side=side, unavailable=unavailable, query=query, mode=mode, results=matches)
        if interrupted:
            result["diagnostic"] = ("Regular-expression matching exceeded its execution timeout. "
                                    "Use a more specific pattern or literal search. Remaining paths were not searched; "
                                    "absence is not evidence.")
        return result
