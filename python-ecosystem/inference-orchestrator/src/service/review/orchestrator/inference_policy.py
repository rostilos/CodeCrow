"""
Inference policy for the multi-stage review pipeline.

This module only caps generated output. It does not trim the diff, RAG context,
file outlines, or issue evidence that the model receives.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

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


OUTPUT_CAPS_ENABLED = _env_bool("REVIEW_OUTPUT_CAPS_ENABLED", True)
OUTPUT_CAP_MODEL_KWARG = os.environ.get("REVIEW_OUTPUT_CAP_MODEL_KWARG", "").strip()
FAST_CHECK_ENABLED = _env_bool("REVIEW_FAST_CHECK_ENABLED", True)
STAGE_2_ENABLED = _env_bool("REVIEW_STAGE_2_ENABLED", True)

FAST_CHECK_MAX_FILES = _env_int("REVIEW_FAST_CHECK_MAX_FILES", 4)
FAST_CHECK_MAX_CHANGED_LINES = _env_int("REVIEW_FAST_CHECK_MAX_CHANGED_LINES", 800)
FAST_CHECK_MAX_DIFF_BYTES = _env_int("REVIEW_FAST_CHECK_MAX_DIFF_BYTES", 120_000)

MEDIUM_REVIEW_MAX_FILES = _env_int("REVIEW_MEDIUM_MAX_FILES", 15)
MEDIUM_REVIEW_MAX_CHANGED_LINES = _env_int("REVIEW_MEDIUM_MAX_CHANGED_LINES", 3_000)
MEDIUM_REVIEW_MAX_DIFF_BYTES = _env_int("REVIEW_MEDIUM_MAX_DIFF_BYTES", 450_000)

FAST_CHECK_DEDUP_MAX_ISSUES = _env_int("REVIEW_FAST_CHECK_DEDUP_MAX_ISSUES", 5)

DEFAULT_STAGE_OUTPUT_CAPS = {
    "stage_0": {"small": 6_000, "medium": 8_000, "large": 12_000},
    "stage_1": {"small": 20_000, "medium": 30_000, "large": 40_000},
    "verification": {"small": 5_000, "medium": 8_000, "large": 12_000},
    "stage_2": {"small": 11_000, "medium": 18_000, "large": 25_000},
    "dedup": {"small": 3_000, "medium": 5_000, "large": 8_000},
    "stage_3": {"small": 8_000, "medium": 12_000, "large": 18_000},
}

@dataclass(frozen=True)
class ReviewInferenceProfile:
    file_count: int
    changed_lines: int
    diff_bytes: int
    size_class: str
    fast_check_enabled: bool
    fast_check_reason: str
    caps: dict[str, Optional[int]] = field(default_factory=dict)

    def describe(self) -> str:
        return (
            f"files={self.file_count}, changed_lines={self.changed_lines}, "
            f"diff_bytes={self.diff_bytes}, size={self.size_class}, "
            f"fast_check={self.fast_check_enabled} ({self.fast_check_reason})"
        )

    def output_cap(self, stage: str) -> Optional[int]:
        if not OUTPUT_CAPS_ENABLED:
            return None
        return self.caps.get(stage)


def build_review_inference_profile(
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
) -> ReviewInferenceProfile:
    file_count = _count_review_files(request, processed_diff)
    changed_lines = _count_changed_lines(request, processed_diff)
    diff_bytes = _count_diff_bytes(request, processed_diff)

    size_class = _classify_size(file_count, changed_lines, diff_bytes)
    fast_check, reason = _classify_fast_check(file_count, changed_lines, diff_bytes)
    caps = {
        stage: _stage_output_cap(stage, size_class)
        for stage in DEFAULT_STAGE_OUTPUT_CAPS
    }

    profile = ReviewInferenceProfile(
        file_count=file_count,
        changed_lines=changed_lines,
        diff_bytes=diff_bytes,
        size_class=size_class,
        fast_check_enabled=fast_check,
        fast_check_reason=reason,
        caps=caps,
    )
    logger.info("Review inference profile: %s; output_caps=%s", profile.describe(), caps)
    return profile


def with_stage_output_cap(llm, stage: str, profile: ReviewInferenceProfile):
    cap = profile.output_cap(stage)
    if not cap:
        return llm

    try:
        update = {"max_tokens": cap}
        if OUTPUT_CAP_MODEL_KWARG:
            model_kwargs = dict(getattr(llm, "model_kwargs", {}) or {})
            model_kwargs[OUTPUT_CAP_MODEL_KWARG] = cap
            update["model_kwargs"] = model_kwargs

        if hasattr(llm, "model_copy"):
            capped = llm.model_copy(update=update)
        elif hasattr(llm, "copy"):
            capped = llm.copy(update=update)
        else:
            bind_kwargs = {"max_tokens": cap}
            if OUTPUT_CAP_MODEL_KWARG:
                bind_kwargs[OUTPUT_CAP_MODEL_KWARG] = cap
            capped = llm.bind(**bind_kwargs)
        logger.info(
            "Using output cap for %s: max_tokens=%s, model_kwarg=%s (profile=%s)",
            stage,
            cap,
            OUTPUT_CAP_MODEL_KWARG or "none",
            profile.size_class,
        )
        return capped
    except Exception as exc:
        logger.warning("Failed to apply output cap for %s: %s", stage, exc)
        return llm


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


def _count_review_files(
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
) -> int:
    if processed_diff:
        return len(processed_diff.get_included_files())
    return len(request.changedFiles or [])


def _count_changed_lines(
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
) -> int:
    if processed_diff:
        return processed_diff.total_additions + processed_diff.total_deletions

    diff = request.deltaDiff if request.analysisMode == "INCREMENTAL" and request.deltaDiff else request.rawDiff
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
    diff = request.deltaDiff if request.analysisMode == "INCREMENTAL" and request.deltaDiff else request.rawDiff
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


def _stage_output_cap(stage: str, size_class: str) -> Optional[int]:
    default = DEFAULT_STAGE_OUTPUT_CAPS[stage][size_class]
    env_stage = stage.upper()
    env_stage_no_underscore = env_stage.replace("_", "")
    env_size = size_class.upper()
    for name in (
        f"REVIEW_{env_stage}_{env_size}_MAX_OUTPUT_TOKENS",
        f"REVIEW_{env_stage_no_underscore}_{env_size}_MAX_OUTPUT_TOKENS",
        f"REVIEW_{env_stage}_MAX_OUTPUT_TOKENS",
        f"REVIEW_{env_stage_no_underscore}_MAX_OUTPUT_TOKENS",
    ):
        value = os.environ.get(name)
        if value is None or not value.strip():
            continue
        cap = _env_int(name, default)
        return cap if cap > 0 else None
    return default
