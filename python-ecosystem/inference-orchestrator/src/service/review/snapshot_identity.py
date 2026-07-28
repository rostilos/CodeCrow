"""Exact repository identity required by the review pipeline."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional

from model.dtos import ReviewRequestDto


_IMMUTABLE_GIT_REVISION = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")


class ReviewSnapshotPreconditionError(RuntimeError):
    """Raised before analysis when its repository snapshot is not exact."""


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
    if _IMMUTABLE_GIT_REVISION.fullmatch(revision) is None:
        raise ReviewSnapshotPreconditionError(
            f"Review snapshot precondition failed: {field_name} must be a full "
            "40- or 64-character hexadecimal Git object ID; mutable names and "
            "abbreviated hashes are not accepted. No review-model stage was started."
        )
    return revision


def validate_review_snapshot_identity(
    request: ReviewRequestDto,
) -> ReviewSnapshotIdentity:
    """Resolve one fail-closed identity for all review context consumers."""
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
        source_branch = _required_exact_text(
            request.sourceBranchName,
            "sourceBranchName",
        )
        base_revision = _required_immutable_revision(
            request.baseCommitHash,
            "baseCommitHash",
        )

    return ReviewSnapshotIdentity(
        target_branch=target_branch,
        head_revision=head_revision,
        source_branch=source_branch,
        base_revision=base_revision,
    )
