"""
Base Multi-Stage Orchestrator.

Provides shared infrastructure for multi-stage LLM pipelines:
- LLM instance management
- RAG indexing / cleanup lifecycle
- Smart dependency-aware batching via DependencyGraphBuilder
- Diff filtering for per-batch file subsets
- Event emission helpers

Subclasses: MultiStageReviewOrchestrator, QaDocOrchestrator
"""
import re
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Callable, Set

from model.enrichment import PrEnrichmentDataDto

logger = logging.getLogger(__name__)


# ── Event emission helpers ───────────────────────────────────────────

def emit_status(callback: Optional[Callable], stage: str, message: str) -> None:
    """Emit a status event to the caller (non-blocking)."""
    if callback:
        try:
            callback({"type": "status", "stage": stage, "message": message})
        except Exception:
            pass


def emit_progress(callback: Optional[Callable], percent: int, message: str) -> None:
    """Emit a progress event (0-100) to the caller."""
    if callback:
        try:
            callback({"type": "progress", "percent": percent, "message": message})
        except Exception:
            pass


def emit_error(callback: Optional[Callable], error: str) -> None:
    """Emit an error event to the caller."""
    if callback:
        try:
            callback({"type": "error", "message": error})
        except Exception:
            pass


class BaseOrchestrator(ABC):
    """
    Abstract base for multi-stage LLM pipelines.

    Provides:
    - ``self.llm`` — the LangChain LLM instance
    - ``self.rag_client`` — optional RAG client for hybrid queries
    - ``self.event_callback`` — optional SSE/WS event emitter
    - ``index_pr_files()`` / ``cleanup_pr_files()`` — RAG lifecycle
    - ``build_dependency_batches()`` — smart batching from enrichment data
    - ``filter_diff_for_files()`` — per-batch diff slicing
    """

    def __init__(
        self,
        llm,
        rag_client=None,
        event_callback: Optional[Callable[[Dict], None]] = None,
    ):
        self.llm = llm
        self.rag_client = rag_client
        self.event_callback = event_callback
        self._pr_number: Optional[int] = None
        self._pr_indexed: bool = False

    # ── RAG lifecycle ────────────────────────────────────────────────

    async def index_pr_files(
        self,
        *,
        workspace: str,
        project: str,
        pr_number: int,
        branch: str,
        enrichment_data: Optional[PrEnrichmentDataDto],
        changed_file_paths: Optional[List[str]],
        diff: Optional[str] = None,
    ) -> None:
        """
        Index PR files into RAG for hybrid context queries.

        Uses full file content from enrichment data when available;
        falls back to diff hunks otherwise.
        """
        if not self.rag_client or not pr_number:
            return

        # Build enrichment lookup: path → full file content
        enrichment_lookup: Dict[str, str] = {}
        if enrichment_data and enrichment_data.fileContents:
            for fc in enrichment_data.fileContents:
                if fc.content and not fc.skipped:
                    enrichment_lookup[fc.path] = fc.content
                    parts = fc.path.split("/", 1)
                    if len(parts) > 1:
                        enrichment_lookup[parts[1]] = fc.content

        # Build file list for indexing
        files: List[Dict[str, str]] = []
        paths_to_index = changed_file_paths or []
        for path in paths_to_index:
            content = enrichment_lookup.get(path, "")
            if not content:
                # Suffix matching for path variations
                for ep, ec in enrichment_lookup.items():
                    if path.endswith(ep) or ep.endswith(path):
                        content = ec
                        break
            if content:
                files.append({"path": path, "content": content, "change_type": "MODIFIED"})

        if not files:
            logger.info("No files to index for PR #%s", pr_number)
            return

        self._pr_number = pr_number
        try:
            result = await self.rag_client.index_pr_files(
                workspace=workspace,
                project=project,
                pr_number=pr_number,
                branch=branch,
                files=files,
            )
            if result.get("status") == "indexed":
                self._pr_indexed = True
                logger.info("Indexed PR #%s: %s chunks", pr_number, result.get("chunks_indexed", 0))
            else:
                logger.warning("Failed to index PR files: %s", result)
        except Exception as e:
            logger.warning("Error indexing PR files: %s", e)

    async def cleanup_pr_files(self, workspace: str, project: str) -> None:
        """Delete PR-indexed data (idempotent)."""
        if not self._pr_number or not self.rag_client:
            return
        try:
            await self.rag_client.delete_pr_files(
                workspace=workspace,
                project=project,
                pr_number=self._pr_number,
            )
            logger.info("Cleaned up PR #%s indexed data", self._pr_number)
        except Exception as e:
            logger.warning("Failed to cleanup PR files: %s", e)
        finally:
            self._pr_number = None
            self._pr_indexed = False

    # ── Smart batching ───────────────────────────────────────────────

    def build_dependency_batches(
        self,
        changed_file_paths: List[str],
        enrichment_data: Optional[PrEnrichmentDataDto],
        diff: Optional[str] = None,
        max_batch_tokens: int = 60_000,
    ) -> List[List[Dict[str, Any]]]:
        """
        Build dependency-aware file batches using the DependencyGraphBuilder.

        Falls back to simple sequential batching if enrichment data is unavailable.
        Returns a list of batches, where each batch is a list of dicts with
        ``file_info``, ``priority``, etc.
        """
        from utils.dependency_graph import build_dependency_aware_batches

        try:
            batches = build_dependency_aware_batches(
                changed_files=changed_file_paths,
                enrichment_data=enrichment_data,
                max_batch_token_budget=max_batch_tokens,
                diff=diff,
            )
            if batches:
                logger.info(
                    "Dependency-aware batching: %d files → %d batches",
                    len(changed_file_paths), len(batches),
                )
                return batches
        except Exception as e:
            logger.warning("Dependency batching failed, falling back to sequential: %s", e)

        # Fallback: simple sequential batches of ~15 files each
        return self._simple_batch(changed_file_paths, batch_size=15)

    @staticmethod
    def _simple_batch(
        paths: List[str], batch_size: int = 15
    ) -> List[List[Dict[str, Any]]]:
        """Fallback: chunk file paths into fixed-size batches."""
        batches = []
        for i in range(0, len(paths), batch_size):
            chunk = paths[i : i + batch_size]
            batches.append([{"file_info": type("FI", (), {"path": p})(), "priority": "MEDIUM"} for p in chunk])
        return batches

    # ── Diff filtering ───────────────────────────────────────────────

    @staticmethod
    def filter_diff_for_files(
        raw_diff: Optional[str], file_paths: Set[str]
    ) -> Optional[str]:
        """
        Filter a unified diff to include only hunks for the given file paths.
        Returns ``None`` if no relevant hunks are found.

        Uses suffix matching so that path format differences between the
        diff headers (e.g. ``src/main/Foo.java``) and ``file_paths``
        (e.g. ``repo/src/main/Foo.java`` or ``main/Foo.java``) don't
        silently cause the filter to drop every section.
        """
        if not raw_diff or not file_paths:
            return None

        sections = re.split(r'(?=^diff --git )', raw_diff, flags=re.MULTILINE)
        relevant = []

        def _matches(diff_path: str) -> bool:
            """Check if diff_path matches any entry in file_paths."""
            if diff_path in file_paths:
                return True
            for fp in file_paths:
                if fp.endswith(diff_path) or diff_path.endswith(fp):
                    return True
            return False

        for section in sections:
            if not section.strip():
                continue
            header_match = re.match(r'diff --git a/(.+?) b/(.+?)(?:\n|$)', section)
            if header_match:
                a_path = header_match.group(1)
                b_path = header_match.group(2)
                if _matches(a_path) or _matches(b_path):
                    relevant.append(section)

        return "\n".join(relevant) if relevant else None

    # ── Enrichment helpers ───────────────────────────────────────────

    @staticmethod
    def get_file_content_from_enrichment(
        path: str,
        enrichment_data: Optional[PrEnrichmentDataDto],
    ) -> Optional[str]:
        """Look up full file content from enrichment data by path."""
        if not enrichment_data or not enrichment_data.fileContents:
            return None
        for fc in enrichment_data.fileContents:
            if fc.path == path and fc.content and not fc.skipped:
                return fc.content
            # Suffix match
            if path.endswith(fc.path) or fc.path.endswith(path):
                if fc.content and not fc.skipped:
                    return fc.content
        return None

    @staticmethod
    def build_enrichment_lookup(
        enrichment_data: Optional[PrEnrichmentDataDto],
    ) -> Dict[str, str]:
        """Build a path → content dict from enrichment data."""
        lookup: Dict[str, str] = {}
        if not enrichment_data or not enrichment_data.fileContents:
            return lookup
        for fc in enrichment_data.fileContents:
            if fc.content and not fc.skipped:
                lookup[fc.path] = fc.content
                parts = fc.path.split("/", 1)
                if len(parts) > 1:
                    lookup[parts[1]] = fc.content
        return lookup
