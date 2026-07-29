"""Best available repository identity for optional review enrichment."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional

from model.dtos import ReviewRequestDto


_GIT_REVISION = re.compile(r"[^\s]+")


class ReviewSnapshotPreconditionError(RuntimeError):
    """Raised only when the core review has no usable repository identity."""


@dataclass(frozen=True)
class ReviewSnapshotIdentity:
    target_branch: str
    head_revision: str
    source_branch: Optional[str] = None
    base_revision: Optional[str] = None


def _required_exact_text(value: Optional[str], field_name: str) -> str:
    if value is None or not value.strip():
        raise ReviewSnapshotPreconditionError(
            f"Review snapshot precondition failed: {field_name} is required. "
            "No review-model stage was started."
        )
    if value != value.strip():
        raise ReviewSnapshotPreconditionError(
            f"Review snapshot precondition failed: {field_name} contains "
            "surrounding whitespace. No review-model stage was started."
        )
    return value


def _required_immutable_revision(
    value: Optional[str],
    field_name: str,
) -> str:
    revision = _required_exact_text(value, field_name)
    if _GIT_REVISION.fullmatch(revision) is None:
        raise ReviewSnapshotPreconditionError(
            f"Review snapshot precondition failed: {field_name} must be a valid "
            "non-blank Git revision. No review-model stage was started."
        )
    return revision


def validate_review_snapshot_identity(
    request: ReviewRequestDto,
) -> ReviewSnapshotIdentity:
    """Return provider identity without imposing a full-hash representation."""
    target_branch = _required_exact_text(
        request.targetBranchName,
        "targetBranchName",
    )
    head_candidate = (
        request.currentCommitHash
        if request.currentCommitHash is not None
        else request.commitHash
    )
    head_revision = _required_immutable_revision(
        head_candidate,
        "currentCommitHash",
    )

    source_branch: Optional[str] = None
    base_revision: Optional[str] = None
    if request.pullRequestId:
        source_branch = (
            request.sourceBranchName.strip()
            if request.sourceBranchName and request.sourceBranchName.strip()
            else None
        )
        base_revision = (
            request.baseCommitHash.strip()
            if request.baseCommitHash and request.baseCommitHash.strip()
            else None
        )

    return ReviewSnapshotIdentity(
        target_branch=target_branch,
        head_revision=head_revision,
        source_branch=source_branch,
        base_revision=base_revision,
    )
