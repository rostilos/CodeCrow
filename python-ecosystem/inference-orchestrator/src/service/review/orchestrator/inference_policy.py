"""Inference policy for the multi-stage review pipeline.

Input packers bound the evidence sent to a model. This module limits semantic
invocation fan-out. Stages own any narrower response policy required by their
output contract; in particular, Stage 2 bounds its compact structured result so
hidden reasoning cannot consume the provider's entire completion window.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from model.multi_stage import ReviewPlan
from utils.diff_processor import ProcessedDiff

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


FAST_CHECK_ENABLED = _env_bool("REVIEW_FAST_CHECK_ENABLED", True)
STAGE_2_ENABLED = _env_bool("REVIEW_STAGE_2_ENABLED", True)
LLM_DEDUP_ENABLED = _env_bool("REVIEW_LLM_DEDUP_ENABLED", True)
FAST_CHECK_MAX_FILES = _env_int("REVIEW_FAST_CHECK_MAX_FILES", 4)
FAST_CHECK_MAX_CHANGED_LINES = _env_int("REVIEW_FAST_CHECK_MAX_CHANGED_LINES", 800)
FAST_CHECK_MAX_DIFF_BYTES = _env_int("REVIEW_FAST_CHECK_MAX_DIFF_BYTES", 120_000)

MEDIUM_REVIEW_MAX_FILES = _env_int("REVIEW_MEDIUM_MAX_FILES", 15)
MEDIUM_REVIEW_MAX_CHANGED_LINES = _env_int("REVIEW_MEDIUM_MAX_CHANGED_LINES", 3_000)
MEDIUM_REVIEW_MAX_DIFF_BYTES = _env_int("REVIEW_MEDIUM_MAX_DIFF_BYTES", 450_000)

FAST_CHECK_DEDUP_MAX_ISSUES = _env_int("REVIEW_FAST_CHECK_DEDUP_MAX_ISSUES", 5)

DEFAULT_STAGE_INVOCATION_CAPS = {
    "stage_1_total": {"small": 4, "medium": 12, "large": 24},
    "stage_1_per_unit": {"small": 1, "medium": 2, "large": 3},
    "stage_2_packets": {"small": 1, "medium": 2, "large": 4},
    "stage_3_children": {"small": 1, "medium": 2, "large": 4},
    "branch_reconciliation_packets": {"small": 1, "medium": 2, "large": 4},
}

@dataclass(frozen=True)
class ReviewInferenceProfile:
    file_count: int
    changed_lines: int
    diff_bytes: int
    size_class: str
    fast_check_enabled: bool
    fast_check_reason: str
    invocation_caps: dict[str, int] = field(default_factory=dict)

    def describe(self) -> str:
        return (
            f"files={self.file_count}, changed_lines={self.changed_lines}, "
            f"diff_bytes={self.diff_bytes}, size={self.size_class}, "
            f"fast_check={self.fast_check_enabled} ({self.fast_check_reason})"
        )

    def invocation_cap(self, stage: str) -> int:
        cap = (getattr(self, "invocation_caps", None) or {}).get(stage)
        if isinstance(cap, int) and cap > 0:
            return cap
        defaults = DEFAULT_STAGE_INVOCATION_CAPS.get(stage, {})
        default = defaults.get(self.size_class)
        return default if isinstance(default, int) and default > 0 else 1

def build_review_inference_profile(
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
) -> ReviewInferenceProfile:
    file_count = _count_review_files(request, processed_diff)
    changed_lines = _count_changed_lines(request, processed_diff)
    diff_bytes = _count_diff_bytes(request, processed_diff)

    size_class = _classify_size(file_count, changed_lines, diff_bytes)
    fast_check, reason = _classify_fast_check(file_count, changed_lines, diff_bytes)
    invocation_caps = {
        stage: _stage_invocation_cap(stage, size_class)
        for stage in DEFAULT_STAGE_INVOCATION_CAPS
    }
    profile = ReviewInferenceProfile(
        file_count=file_count,
        changed_lines=changed_lines,
        diff_bytes=diff_bytes,
        size_class=size_class,
        fast_check_enabled=fast_check,
        fast_check_reason=reason,
        invocation_caps=invocation_caps,
    )
    logger.info(
        "Review inference profile: %s; invocation_caps=%s",
        profile.describe(),
        invocation_caps,
    )
    return profile


def should_run_stage_2(
    profile: ReviewInferenceProfile,
    request: ReviewRequestDto,
    plan: ReviewPlan,
    issues: list[CodeReviewIssue],
) -> tuple[bool, str]:
    if not STAGE_2_ENABLED:
        return False, "disabled by REVIEW_STAGE_2_ENABLED"

    if not profile.fast_check_enabled:
        return True, "full review profile"

    if getattr(request, "taskContext", None):
        return True, "task context requires PR-wide coverage check"

    task_history_context = getattr(request, "taskHistoryContext", None)
    if isinstance(task_history_context, str) and task_history_context.strip():
        return True, "task history context requires PR-wide coverage check"

    if plan.cross_file_concerns:
        return True, "review plan contains cross-file concerns"

    relationships = getattr(getattr(request, "enrichmentData", None), "relationships", None)
    if relationships:
        return True, "dependency analysis found relationships between changed files"

    for issue in issues:
        severity = (getattr(issue, "severity", "") or "").upper()
        if severity in {"CRITICAL", "HIGH"}:
            return True, "Stage 1 found high-severity issue"

    return False, "small PR fast check: no cross-file risk signals"


def should_use_fast_dedup(profile: ReviewInferenceProfile, issue_count: int) -> bool:
    return profile.fast_check_enabled and issue_count <= FAST_CHECK_DEDUP_MAX_ISSUES


def should_use_llm_dedup(
    profile: ReviewInferenceProfile,
    issue_count: int,
) -> bool:
    """Enable grouped semantic dedup; singleton candidate sets skip internally."""
    del profile
    return LLM_DEDUP_ENABLED and issue_count > 1


def _count_review_files(
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
) -> int:
    if processed_diff:
        return len(processed_diff.get_included_files())
    return len(getattr(request, "changedFiles", None) or [])


def _count_changed_lines(
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
) -> int:
    if processed_diff:
        return processed_diff.total_additions + processed_diff.total_deletions

    delta_diff = getattr(request, "deltaDiff", None)
    diff = (
        delta_diff
        if getattr(request, "analysisMode", None) == "INCREMENTAL" and delta_diff
        else getattr(request, "rawDiff", None)
    )
    if not diff:
        return 0
    return sum(
        1
        for line in diff.splitlines()
        if (line.startswith("+") and not line.startswith("+++"))
        or (line.startswith("-") and not line.startswith("---"))
    )


def _count_diff_bytes(
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
) -> int:
    if processed_diff:
        return processed_diff.processed_size_bytes or processed_diff.original_size_bytes
    delta_diff = getattr(request, "deltaDiff", None)
    diff = (
        delta_diff
        if getattr(request, "analysisMode", None) == "INCREMENTAL" and delta_diff
        else getattr(request, "rawDiff", None)
    )
    return len((diff or "").encode("utf-8"))


def _classify_size(file_count: int, changed_lines: int, diff_bytes: int) -> str:
    if (
        file_count <= FAST_CHECK_MAX_FILES
        and changed_lines <= FAST_CHECK_MAX_CHANGED_LINES
        and diff_bytes <= FAST_CHECK_MAX_DIFF_BYTES
    ):
        return "small"
    if (
        file_count <= MEDIUM_REVIEW_MAX_FILES
        and changed_lines <= MEDIUM_REVIEW_MAX_CHANGED_LINES
        and diff_bytes <= MEDIUM_REVIEW_MAX_DIFF_BYTES
    ):
        return "medium"
    return "large"


def _classify_fast_check(file_count: int, changed_lines: int, diff_bytes: int) -> tuple[bool, str]:
    if not FAST_CHECK_ENABLED:
        return False, "disabled by REVIEW_FAST_CHECK_ENABLED"
    if file_count > FAST_CHECK_MAX_FILES:
        return False, f"file count {file_count} > {FAST_CHECK_MAX_FILES}"
    if changed_lines > FAST_CHECK_MAX_CHANGED_LINES:
        return False, f"changed lines {changed_lines} > {FAST_CHECK_MAX_CHANGED_LINES}"
    if diff_bytes > FAST_CHECK_MAX_DIFF_BYTES:
        return False, f"diff bytes {diff_bytes} > {FAST_CHECK_MAX_DIFF_BYTES}"
    return True, "within small PR thresholds"


def _stage_invocation_cap(stage: str, size_class: str) -> int:
    default = DEFAULT_STAGE_INVOCATION_CAPS[stage][size_class]
    env_stage = stage.upper()
    env_stage_no_underscore = env_stage.replace("_", "")
    env_size = size_class.upper()
    for name in (
        f"REVIEW_{env_stage}_MAX_INVOCATIONS",
        f"REVIEW_{env_stage_no_underscore}_MAX_INVOCATIONS",
        f"REVIEW_{env_stage}_{env_size}_MAX_INVOCATIONS",
        f"REVIEW_{env_stage_no_underscore}_{env_size}_MAX_INVOCATIONS",
    ):
        value = os.environ.get(name)
        if value is None or not value.strip():
            continue
        cap = _env_int(name, default)
        if cap > 0:
            return cap
        logger.warning(
            "Ignoring non-positive %s=%s; using finite default %s",
            name,
            value,
            default,
        )
        return default
    return default
