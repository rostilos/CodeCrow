"""Exact source reads over one graph-bound proposed-tree session."""
from __future__ import annotations

from typing import Any

from .review_snapshot import ProposedTreeUnavailableError, _normalize_path, load_review_overlay
from .source_tree import attest_repository_source_tree, read_repository_file_bytes


class ReviewSourceReader:
    """Own source attestation and lookup, independently of graph navigation."""

    def __init__(self, session, *, target_repo_path: str, review_overlay_path: str, base_revision: str):
        self.session = session
        self.target_repo_path = target_repo_path
        self.base_revision = base_revision
        self.overlay = load_review_overlay(review_overlay_path)
        if self.overlay.fingerprint != session.generation.overlay_sha256:
            raise ProposedTreeUnavailableError("review overlay changed after indexing")
        self._target_source = None

    def _target(self):
        if self._target_source is None:
            source = attest_repository_source_tree(self.target_repo_path, self.base_revision)
            if source.tree_sha256 != self.session.generation.target_source_tree_sha256:
                raise ProposedTreeUnavailableError("target tree changed after proposed graph indexing")
            self._target_source = source
        return self._target_source

    def _content(self, path: str, side: str) -> tuple[bytes | None, str]:
        if side == "proposed" and path in self.overlay.file_sha256_by_path:
            return read_repository_file_bytes(
                self.overlay.files_root, path,
                expected_sha256=self.overlay.file_sha256_by_path[path],
            ), "review_overlay"
        expected = self._target().file_sha256_by_path.get(path)
        if expected is None:
            return None, "target_tree"
        return read_repository_file_bytes(self.target_repo_path, path, expected_sha256=expected), "target_tree"

    def read(self, path: str, *, side: str = "proposed", start_line: int = 1,
             end_line: int | None = None) -> dict[str, Any]:
        path = _normalize_path(path)
        if side not in {"proposed", "target"}:
            raise ValueError("side must be proposed or target")
        if start_line < 1 or (end_line is not None and end_line < start_line):
            raise ValueError("invalid source line range")
        snapshot = self.session.reader.snapshot()
        result = {"path": path, "side": side, "snapshot": snapshot}
        if side == "proposed" and path in self.overlay.deleted_paths:
            return {"status": "deleted", **result}
        content, origin = self._content(path, side)
        if content is None:
            return {"status": "missing", **result}
        try:
            lines = content.decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            return {"status": "binary", **result}
        last_line = min(end_line or len(lines), len(lines))
        return {
            "status": "ready", **result, "origin": origin,
            "startLine": start_line, "endLine": last_line, "totalLines": len(lines),
            "content": "".join(lines[start_line - 1:last_line]),
        }

    def search(self, query: str, *, cursor: int = 0, max_results: int = 100) -> dict[str, Any]:
        needle = query.strip().casefold()
        if not needle or cursor < 0 or max_results < 1:
            raise ValueError("search requires a query and non-negative page")
        paths = sorted(
            (set(self._target().file_sha256_by_path) | set(self.overlay.file_sha256_by_path))
            - set(self.overlay.deleted_paths)
        )
        results: list[dict[str, Any]] = []
        matching = 0
        for path in paths:
            if needle in path.casefold():
                if matching >= cursor:
                    results.append({"path": path, "line": None, "text": None})
                matching += 1
                if len(results) > max_results:
                    break
            content, _ = self._content(path, "proposed")
            if content is None:
                continue
            try:
                lines = content.decode("utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for line_number, text in enumerate(lines, start=1):
                if needle not in text.casefold():
                    continue
                if matching >= cursor:
                    results.append({"path": path, "line": line_number, "text": text})
                matching += 1
                if len(results) > max_results:
                    break
            if len(results) > max_results:
                break
        return {
            "status": "ready", "query": query, "cursor": cursor,
            "results": results[:max_results],
            "nextCursor": cursor + max_results if len(results) > max_results else None,
            "snapshot": self.session.reader.snapshot(),
        }
