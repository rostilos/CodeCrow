"""Deterministic parsing of review and full pull-request evidence scopes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
from typing import Optional

from model.dtos import ReviewRequestDto
from service.review.plugin_context import apply_plugin_file_policy
from utils.diff_processor import DiffChangeType, DiffHunk, DiffProcessor, ProcessedDiff


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


def _include_rename_impact_source(
    request: ReviewRequestDto, processed: ProcessedDiff,
) -> ProcessedDiff:
    """Use supplied proposed source for path-only moves, without inventing edits.

    These context-only units own exact line anchors just like ordinary context
    lines in a diff. Missing optional source leaves an observable path obligation
    in the PR ledger; it never blocks review of the other files.
    """
    enrichment = request.enrichmentData
    contents = {
        item.path: item.content
        for item in (enrichment.fileContents if enrichment else ())
        if not item.skipped and item.content is not None
    }
    for file in processed.files:
        if (file.change_type is not DiffChangeType.RENAMED or file.hunks
                or file.is_binary or file.is_gitlink):
            continue
        source = contents.get(file.path)
        if not source:
            logger.warning("Path-change impact source unavailable: %s -> %s", file.old_path, file.path)
            continue
        lines = source.splitlines()
        header = f"@@ -1,{len(lines)} +1,{len(lines)} @@ PATH-CHANGE IMPACT: unchanged source"
        content = "\n".join([header, *(" " + line for line in lines)])
        digest = hashlib.sha256(f"{file.old_path}\0{file.path}\0{source}".encode()).hexdigest()
        file.hunks = [DiffHunk(
            id="path-impact:" + digest, path=file.path, header=header,
            old_start=1, old_count=len(lines), new_start=1, new_count=len(lines),
            content=content, evidence_kind="path_change_impact",
        )]
        file.content += (
            "\n[PATH-CHANGE IMPACT: " + str(file.old_path) + " → " + file.path
            + ". The following lines are unchanged source, not additions/deletions. "
            "Review breakage from moved paths, imports, callers, registrations and resolution; "
            "do not report unrelated pre-existing defects.]\n" + content
        )
        file.full_content = source
        file.is_skipped = False
        file.skip_reason = None
        processed.total_files += 1
        processed.skipped_files -= 1
    return processed


def process_review_evidence_scopes(
    request: ReviewRequestDto,
) -> ProcessedReviewEvidenceScopes:
    """Parse delta review evidence and full PR state independently.

    This helper performs no VCS, repository retrieval, or model call. The full PR
    parse is used only by the fixed-budget Stage 2 ledger.
    """
    review_raw_diff = select_review_evidence_diff(request)
    if not review_raw_diff:
        return ProcessedReviewEvidenceScopes(None, None)

    review = apply_plugin_file_policy(
        request,
        _include_rename_impact_source(request, DiffProcessor().process(review_raw_diff)),
    )
    full_pr_raw_diff = request.rawDiff
    if request.analysisMode == "INCREMENTAL" and request.deltaDiff:
        if full_pr_raw_diff:
            full_pr = _include_rename_impact_source(request, DiffProcessor().process(full_pr_raw_diff))
            try:
                full_pr = apply_plugin_file_policy(request, full_pr)
            except Exception as exc:
                # Full-PR classification is optional Stage 2 enrichment. A plugin
                # failure in a file from an earlier PR commit must not block review
                # of the current delta.
                logger.warning(
                    "Full PR evidence scope plugin policy unavailable; using neutral "
                    "diff classification: %s",
                    exc,
                )
                full_pr = _include_rename_impact_source(request, DiffProcessor().process(full_pr_raw_diff))
        else:
            logger.warning(
                "Incremental request has no full PR diff; Stage 2 full-PR "
                "evidence will be marked unavailable"
            )
            full_pr = None
    else:
        full_pr = review
    return ProcessedReviewEvidenceScopes(review, full_pr)
