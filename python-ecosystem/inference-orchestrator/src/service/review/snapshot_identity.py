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
    target_head_revision: Optional[str] = None
    merge_base_revision: Optional[str] = None

    @property
    def base_revision(self) -> Optional[str]:
        """Compatibility name for the target generation's base revision."""
        return self.target_head_revision


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
    target_head_revision: Optional[str] = None
    merge_base_revision: Optional[str] = None
    if request.pullRequestId:
        source_branch = (
            request.sourceBranchName.strip()
            if request.sourceBranchName and request.sourceBranchName.strip()
            else None
        )
        target_head_revision = request.get_target_head_commit_hash()
        merge_base_revision = (
            request.baseCommitHash.strip()
            if request.baseCommitHash and request.baseCommitHash.strip()
            else None
        )

    return ReviewSnapshotIdentity(
        target_branch=target_branch,
        head_revision=head_revision,
        source_branch=source_branch,
        target_head_revision=target_head_revision,
        merge_base_revision=merge_base_revision,
    )


def resolve_exact_structural_base_revision(
    request: ReviewRequestDto,
) -> Optional[str]:
    """Resolve a base revision only when the local PR target is cross-bound.

    Structural context is optional, so an incomplete or conflicting binding is
    represented by ``None`` rather than a core-review precondition error.  PRs
    must bind the staged target snapshot to both the provider-captured target
    head and target branch. Non-PR/manual reviews retain the legacy best
    available immutable-revision behavior.
    """

    local_revision = getattr(request, "localRepoRevision", None)
    if not isinstance(local_revision, str) or not local_revision.strip():
        local_revision = None
    elif local_revision != local_revision.strip():
        return None

    pull_request_id = getattr(request, "pullRequestId", None)
    is_pull_request = (
        isinstance(pull_request_id, int)
        and pull_request_id != 0
    )
    target_head_revision = getattr(request, "targetHeadCommitHash", None)
    if (
        not isinstance(target_head_revision, str)
        or not target_head_revision.strip()
        or target_head_revision != target_head_revision.strip()
    ):
        target_head_revision = None

    if not is_pull_request:
        return local_revision or request.get_target_head_commit_hash()

    if (
        local_revision is None
        or target_head_revision is None
        or local_revision != target_head_revision
    ):
        return None

    target_branch = getattr(request, "targetBranchName", None)
    local_target_branch = getattr(request, "localRepoTargetBranch", None)
    if (
        not isinstance(target_branch, str)
        or not target_branch.strip()
        or target_branch != target_branch.strip()
        or not isinstance(local_target_branch, str)
        or not local_target_branch.strip()
        or local_target_branch != local_target_branch.strip()
        or local_target_branch != target_branch
    ):
        return None

    return target_head_revision
