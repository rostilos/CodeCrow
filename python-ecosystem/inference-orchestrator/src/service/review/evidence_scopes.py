"""Deterministic parsing of review and full pull-request evidence scopes."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Optional

from model.dtos import ReviewRequestDto
from service.review.plugin_context import apply_plugin_file_policy
from utils.diff_processor import DiffProcessor, ProcessedDiff


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
    if not review_raw_diff:
        return ProcessedReviewEvidenceScopes(None, None)

    review = apply_plugin_file_policy(
        request,
        DiffProcessor().process(review_raw_diff),
    )
    full_pr_raw_diff = request.rawDiff
    if request.analysisMode == "INCREMENTAL" and request.deltaDiff:
        if full_pr_raw_diff:
            full_pr = DiffProcessor().process(full_pr_raw_diff)
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
                full_pr = DiffProcessor().process(full_pr_raw_diff)
        else:
            logger.warning(
                "Incremental request has no full PR diff; Stage 2 full-PR "
                "evidence will be marked unavailable"
            )
            full_pr = None
    else:
        full_pr = review
    return ProcessedReviewEvidenceScopes(review, full_pr)
