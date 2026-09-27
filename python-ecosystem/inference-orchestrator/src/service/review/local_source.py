"""Exact, descriptor-bound local source access for review verification."""
from __future__ import annotations
import json
import logging
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)


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
    def _read_file(cls, root: Path, path: str) -> bytes:
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
            with os.fdopen(file_fd, "rb") as source:
                file_fd = None
                return source.read()
        finally:
            if file_fd is not None:
                os.close(file_fd)
            os.close(directory_fd)

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

    def grep(self, query: str, *, paths: Sequence[str] = (), side: str = "proposed",
             case_sensitive: bool = True) -> dict[str, Any]:
        """Return every matching location; source bodies are fetched separately.

        File and directory scopes are expanded within descriptor-bound source
        roots. Location-only results avoid copying unrelated source while
        keeping every match available, without result or character truncation.
        """
        if not isinstance(query, str) or not query:
            return {"status": "unavailable", "diagnostic": "a nonempty literal query is required"}
        if side not in {"proposed", "target"}:
            return {"status": "unavailable", "diagnostic": "side must be proposed or target"}
        unavailable: list[str] = []
        try:
            scopes = sorted({self._path(path) for path in paths if path not in {".", "./"}})
            if any(path in {".", "./"} for path in paths):
                scopes = []
            selected = self._paths(side, unavailable, scopes)
        except (TypeError, ValueError) as error:
            return {"status": "unavailable", "diagnostic": str(error)}
        needle = query if case_sensitive else query.casefold()
        matches: list[dict[str, Any]] = []
        for path in selected:
            source = self.read(path, side=side)
            if source["status"] not in {"ready", "binary", "deleted"}:
                unavailable.append(path)
                continue
            if source["status"] != "ready":
                continue
            lines = [number for number, line in enumerate(source["content"].splitlines(), 1)
                     if needle in (line if case_sensitive else line.casefold())]
            if lines:
                matches.append({"path": path, "lines": lines})
        unavailable = sorted(set(unavailable))
        unavailable_source = bool(unavailable or (side == "proposed" and self.overlay_error)
                                  or ((self.target is None or not self.target.is_dir()) and not paths))
        return {"status": "partial" if unavailable_source else "ready", "query": query,
                "side": side, "results": matches, "unavailablePaths": unavailable,
                "diagnostic": ("Search did not cover all requested source; absence is not evidence."
                               if unavailable_source else ""), "complete": not unavailable_source}


