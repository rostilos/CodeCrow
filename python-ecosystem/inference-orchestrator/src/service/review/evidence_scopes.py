"""Deterministic parsing of review and full pull-request evidence scopes."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Optional

from model.dtos import ReviewRequestDto
from service.review.plugin_context import apply_plugin_file_policy
from utils.diff_processor import DiffChangeType, DiffFile, DiffProcessor, ProcessedDiff


logger = logging.getLogger(__name__)


def select_review_evidence_diff(request: ReviewRequestDto) -> Optional[str]:
    """Return the diff whose hunks and publication anchors this run owns."""
    if request.analysisMode == "INCREMENTAL" and request.deltaDiff:
        return request.deltaDiff
    return request.rawDiff


@dataclass(frozen=True)
class ProcessedReviewEvidenceScopes:
    """Locally parsed evidence; constructing this object performs no I/O."""

    review: Optional[ProcessedDiff]
    full_pr: Optional[ProcessedDiff]


def process_review_evidence_scopes(
    request: ReviewRequestDto,
) -> ProcessedReviewEvidenceScopes:
    """Parse delta review evidence and full PR state independently.

    This helper performs no VCS, RAG, embedding, or model call. The full PR
    parse is used only by the fixed-budget Stage 2 ledger.
    """
    review_raw_diff = select_review_evidence_diff(request)
    manifest = request.pullRequestFileManifest
    if not review_raw_diff and not (manifest and manifest.changes):
        return ProcessedReviewEvidenceScopes(None, None)

    # A context-maintenance request deliberately carries only manifest headers.
    # They are sufficient to refresh the PR overlay, but they are not review
    # evidence and must never manufacture review units or publication anchors.
    if request.prContextMaintenanceRequired:
        review = ProcessedDiff(files=[])
    else:
        review = apply_plugin_file_policy(
            request,
            DiffProcessor().process(review_raw_diff or ""),
        )

    full_pr_raw_diff = request.rawDiff
    if full_pr_raw_diff:
        full_pr = DiffProcessor().process(full_pr_raw_diff)
        if not request.prContextMaintenanceRequired:
            try:
                full_pr = apply_plugin_file_policy(request, full_pr)
            except Exception as exc:
                # Full-PR classification is optional enrichment. A plugin
                # failure in an earlier PR file must not block the current delta.
                logger.warning(
                    "Full PR evidence scope plugin policy unavailable; using neutral "
                    "diff classification: %s",
                    exc,
                )
                full_pr = DiffProcessor().process(full_pr_raw_diff)
    else:
        full_pr = ProcessedDiff(files=[])

    full_pr = _merge_full_pr_manifest(request, full_pr)
    if not full_pr.files and request.analysisMode == "INCREMENTAL":
        logger.warning(
            "Incremental request has neither a usable full PR manifest nor a "
            "full PR diff; exact current-head context is unavailable"
        )
        full_pr = None

    return ProcessedReviewEvidenceScopes(review, full_pr)


_MANIFEST_CHANGE_TYPES = {
    "ADDED": DiffChangeType.ADDED,
    "MODIFIED": DiffChangeType.MODIFIED,
    "DELETED": DiffChangeType.DELETED,
    "RENAMED": DiffChangeType.RENAMED,
    "COPIED": DiffChangeType.ADDED,
    "UNKNOWN": DiffChangeType.MODIFIED,
}


def _normalize_path(path: str) -> str:
    return str(path or "").strip().replace("\\", "/").lstrip("/")


def _merge_full_pr_manifest(
    request: ReviewRequestDto,
    parsed: ProcessedDiff,
) -> ProcessedDiff:
    """Project a provider path manifest onto the parsed base-to-head diff.

    Unified patches can omit binary, oversized, or provider-truncated files.
    The provider manifest owns membership; enrichment owns post-change source.
    An incomplete manifest is never promoted to complete by this projection.
    """
    manifest = request.pullRequestFileManifest
    if manifest is None or not manifest.changes:
        return parsed

    parsed_by_path = {
        _normalize_path(item.path): item
        for item in parsed.files
        if _normalize_path(item.path)
    }
    enrichment_by_path = {
        _normalize_path(item.path): item
        for item in (
            request.enrichmentData.fileContents
            if request.enrichmentData is not None
            else ()
        )
        if _normalize_path(item.path)
    }

    projected: list[DiffFile] = []
    seen: set[str] = set()
    for change in manifest.changes:
        path = _normalize_path(change.path)
        if not path or path in seen:
            continue
        seen.add(path)
        kind = str(change.kind or "UNKNOWN").strip().upper()
        change_type = _MANIFEST_CHANGE_TYPES.get(kind, DiffChangeType.MODIFIED)
        item = parsed_by_path.get(path)
        if item is None:
            item = DiffFile(
                path=path,
                change_type=change_type,
                old_path=_normalize_path(change.previousPath) or None,
            )
        else:
            item.path = path
            item.change_type = change_type
            if change.previousPath:
                item.old_path = _normalize_path(change.previousPath) or None

        enrichment = enrichment_by_path.get(path)
        if change_type != DiffChangeType.DELETED and enrichment is not None:
            if enrichment.content is not None and not enrichment.skipped:
                item.full_content = enrichment.content
            elif enrichment.skipped:
                item.is_skipped = True
                item.skip_reason = (
                    enrichment.skipReason
                    or "Current-head source enrichment was skipped"
                )
        projected.append(item)

    # When the provider did not attest completeness, preserve patch-only paths
    # as reduced-guarantee evidence instead of silently discarding them.
    if request.fullPrManifestComplete is not True:
        for path, item in parsed_by_path.items():
            if path not in seen:
                projected.append(item)

    parsed.files = projected
    parsed.total_additions = sum(
        item.additions for item in projected if not item.is_skipped
    )
    parsed.total_deletions = sum(
        item.deletions for item in projected if not item.is_skipped
    )
    parsed.total_files = sum(not item.is_skipped for item in projected)
    parsed.skipped_files = sum(item.is_skipped for item in projected)
    parsed.processed_size_bytes = sum(
        item.size_bytes for item in projected if not item.is_skipped
    )
    return parsed
