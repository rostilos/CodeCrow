"""
Stage 0: Planning & Prioritization — analyze PR metadata and build a review plan.
"""
from service.review.execution_scheduler import review_model_slot
import json
import logging
import os
from typing import Any, Dict, Optional

from model.dtos import ReviewRequestDto
from model.multi_stage import ReviewPlan, FileGroup, ReviewFile, FileToSkip
from utils.prompts.prompt_builder import PromptBuilder
from utils.diff_processor import HunkDisposition, ProcessedDiff
from utils.task_context_builder import build_task_context
from service.review.plugin_context import review_plugin_context
from llm.reasoning_policy import ReasoningEffort, reasoning_request_kwargs

from utils.llm_response import extract_llm_response_text
from service.review.orchestrator.json_utils import (
    parse_llm_response,
    resolve_structured_output,
    supports_structured_output,
)
from service.review.orchestrator.structured_output import (
    format_response_diagnostics,
    invoke_structured_output,
)

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


STAGE0_INPUT_TOKEN_TARGET = max(
    10_000,
    _env_int("REVIEW_STAGE1_BATCH_TOKEN_BUDGET", 60_000),
)
_STAGE0_CONTEXT_RESERVE_TOKENS = 20_000
_STAGE0_ESTIMATOR_SAFETY_TOKENS = 256


def _positive_int_or_default(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return default
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized > 0 else default


def _stage_0_input_token_budget(request: ReviewRequestDto) -> int:
    """Reserve context space without setting a generation-output ceiling."""
    model_context_tokens = _positive_int_or_default(
        getattr(request, "maxAllowedTokens", None),
        200_000,
    )
    return min(
        STAGE0_INPUT_TOKEN_TARGET,
        max(4_000, model_context_tokens - _STAGE0_CONTEXT_RESERVE_TOKENS),
    )


def _estimated_stage_0_request_tokens(prompt: str) -> int:
    """Estimate the rendered prompt and structured-output declaration."""
    try:
        schema_bytes = len(json.dumps(
            ReviewPlan.model_json_schema(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"))
    except (AttributeError, TypeError, ValueError):
        schema_bytes = 0
    request_bytes = len(prompt.encode("utf-8")) + schema_bytes
    return max(
        1,
        (request_bytes + 2) // 3 + _STAGE0_ESTIMATOR_SAFETY_TOKENS,
    )


def _build_diff_lookup(processed_diff: Optional[ProcessedDiff]) -> Dict[str, Any]:
    diff_by_path: Dict[str, Any] = {}
    if not processed_diff:
        return diff_by_path

    for df in processed_diff.files:
        diff_by_path[df.path] = df
        if '/' in df.path:
            diff_by_path[df.path.rsplit('/', 1)[-1]] = df
    return diff_by_path


def _reviewable_planning_paths(
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
) -> list[str]:
    """Return only paths whose host manifest still requires direct review."""
    if processed_diff is None:
        return list(dict.fromkeys(request.changedFiles or []))

    reviewable = set()
    for diff_file in processed_diff.files:
        if diff_file.hunks:
            if any(
                hunk.disposition is HunkDisposition.REVIEWABLE
                for hunk in diff_file.hunks
            ):
                reviewable.add(diff_file.path)
            continue
        if (
            diff_file.plugin_disposition not in {"generated", "excluded"}
            and _mechanical_skip_reason(diff_file) is None
        ):
            # Compatibility for callers/tests that construct DiffFile records
            # without the parser-owned hunk manifest.
            reviewable.add(diff_file.path)

    ordered = [
        path
        for path in dict.fromkeys(request.changedFiles or [])
        if path in reviewable
    ]
    ordered.extend(sorted(reviewable - set(ordered)))
    return ordered


async def execute_stage_0_planning(
    llm,
    request: ReviewRequestDto,
    is_incremental: bool = False,
    processed_diff: Optional[ProcessedDiff] = None,
    use_local_planning: bool = False,
) -> ReviewPlan:
    diff_by_path = _build_diff_lookup(processed_diff)
    planning_paths = _reviewable_planning_paths(request, processed_diff)

    changed_files_summary = []
    if planning_paths:
        for f in planning_paths:
            df = diff_by_path.get(f) or diff_by_path.get(f.rsplit('/', 1)[-1] if '/' in f else f)
            changed_files_summary.append(_summarize_file_for_planning(f, df))

    # Include refactoring signals so the planner can adjust expectations
    refactoring_context = ""
    if processed_diff and processed_diff.refactoring_signals:
        refactoring_context = (
            "\n\n⚠️ REFACTORING SIGNALS DETECTED:\n"
            + "\n".join(f"- {s}" for s in processed_diff.refactoring_signals)
            + "\nThese suggest code reorganisation rather than new functionality. "
            "Flag fewer issues for moved/renamed code — focus on real regressions."
        )

    if use_local_planning:
        logger.info("Stage 0 fast check: using local deterministic review plan")
        return _build_fallback_review_plan(
            request,
            processed_diff,
            analysis_summary="Fast check review plan generated locally for a small PR.",
            infer_cross_file_concerns=False,
        )

    if processed_diff is not None and not planning_paths:
        logger.info(
            "Stage 0 provider call skipped: host manifest contains no reviewable paths"
        )
        return _build_fallback_review_plan(
            request,
            processed_diff,
            analysis_summary=(
                "No changed source hunks require direct review after deterministic "
                "file-policy and mechanical disposition accounting."
            ),
            infer_cross_file_concerns=False,
        )

    prompt = PromptBuilder.build_stage_0_planning_prompt(
        repo_slug=request.projectVcsRepoSlug,
        pr_id=str(request.pullRequestId),
        pr_title=request.prTitle or "",
        author=request.prAuthor or "Unknown",
        branch_name=request.sourceBranchName or "",
        target_branch=request.targetBranchName or "",
        commit_hash=request.currentCommitHash or request.commitHash or "",
        task_context=(
            build_task_context(request.taskContext)
            or ""
        ),
        changed_files_json=json.dumps(changed_files_summary, indent=2) + refactoring_context,
        plugin_context=review_plugin_context(
            request,
            planning_paths,
            include_evidence_targets=False,
        ),
    )

    input_token_budget = _stage_0_input_token_budget(request)
    estimated_input_tokens = _estimated_stage_0_request_tokens(prompt)
    if estimated_input_tokens > input_token_budget:
        # Planning is optional orchestration, not the code-analysis authority.
        # Sending a partial planner prompt would create exactly the misleading
        # global assumptions that semantic packing is meant to avoid. Preserve
        # every file in a deterministic plan and let the lossless Stage 1/2
        # paths consume the actual source, diff, and dependency evidence.
        logger.warning(
            "Stage 0 provider call skipped without truncating planner input: "
            "estimated_tokens=%d target_tokens=%d files=%d; using the local "
            "all-files plan",
            estimated_input_tokens,
            input_token_budget,
            len(planning_paths),
        )
        return _build_fallback_review_plan(
            request,
            processed_diff,
            analysis_summary=(
                "Deterministic all-files plan used because the optional planning "
                "request exceeded the semantic input target; no review evidence "
                "was truncated."
            ),
        )

    if supports_structured_output(llm):
        try:
            invocation = await invoke_structured_output(
                llm,
                prompt,
                ReviewPlan,
                effort=ReasoningEffort.LOW,
                label="stage-0-planning",
            )
            result = await resolve_structured_output(
                invocation,
                ReviewPlan,
                llm,
            )
            if result:
                logger.info("Stage 0 planning completed with structured output")
                return result
        except Exception as e:
            logger.warning(
                "Structured output failed for Stage 0: error_type=%s",
                type(e).__name__,
            )
    else:
        logger.info("Structured output skipped for Stage 0; using prompt JSON parsing")

    try:
        async with review_model_slot("stage_0_planning"):
            response = await llm.ainvoke(
                prompt,
                **reasoning_request_kwargs(llm, ReasoningEffort.LOW),
            )
        content = extract_llm_response_text(response)
        if not content.strip():
            logger.warning(
                "Stage 0 raw fallback returned no content: %s",
                format_response_diagnostics(response),
            )
        return await parse_llm_response(
            content,
            ReviewPlan,
            llm,
            max_provider_repairs=0,
        )
    except Exception as e:
        logger.info("Stage 0 planning unavailable; using local fallback plan: %s", e)
        return _build_fallback_review_plan(request, processed_diff)


def _build_fallback_review_plan(
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff] = None,
    analysis_summary: Optional[str] = None,
    infer_cross_file_concerns: bool = True,
) -> ReviewPlan:
    """
    Build a conservative review plan without another LLM call.

    Stage 0 is an optimization step. If a provider returns empty or malformed
    planning JSON, the review should still continue with all changed files.
    """
    paths = _reviewable_planning_paths(request, processed_diff)
    diff_by_path = _build_diff_lookup(processed_diff)
    manifest_paths = list(dict.fromkeys(request.changedFiles or []))
    if processed_diff is not None:
        manifest_set = {item.path for item in processed_diff.files}
        manifest_paths = [path for path in manifest_paths if path in manifest_set]
        manifest_paths.extend(sorted(manifest_set - set(manifest_paths)))
    else:
        manifest_paths = paths

    files = []
    files_to_skip = []
    reviewable_paths = set(paths)
    for path in manifest_paths:
        diff_file = diff_by_path.get(path) or diff_by_path.get(path.rsplit('/', 1)[-1] if '/' in path else path)
        skip_reason = _mechanical_skip_reason(diff_file)
        if skip_reason:
            files_to_skip.append(FileToSkip(path=path, reason=skip_reason))
            continue
        if path not in reviewable_paths:
            continue

        focus_areas = []
        if _diff_was_limited(diff_file):
            focus_areas.append("SUMMARY_REVIEW")

        files.append(
            ReviewFile(
                path=path,
                focus_areas=focus_areas,
                risk_level="MEDIUM",
            )
        )

    file_groups = []
    if files:
        file_groups.append(
            FileGroup(
                group_id="FALLBACK_ALL_FILES",
                priority="MEDIUM",
                rationale=(
                    "Local fallback plan generated because AI planning output "
                    "was unavailable; no filename-based priority inference was applied"
                ),
                files=files,
            )
        )

    return ReviewPlan(
        analysis_summary=(
            analysis_summary
            or "Fallback review plan generated locally after AI planning returned "
            "empty or invalid output."
        ),
        file_groups=file_groups,
        files_to_skip=files_to_skip,
        cross_file_concerns=_infer_cross_file_concerns(paths) if infer_cross_file_concerns else [],
    )


def apply_mechanical_skip_constraints(
    plan: ReviewPlan,
    processed_diff: Optional[ProcessedDiff],
) -> ReviewPlan:
    """Make parser-proven non-source dispositions authoritative over planning."""
    if processed_diff is None:
        return plan

    reasons = {
        diff_file.path: reason
        for diff_file in processed_diff.files
        if (reason := _mechanical_skip_reason(diff_file))
    }
    if not reasons:
        return plan

    retained_groups = []
    for group in plan.file_groups:
        retained_files = [
            review_file
            for review_file in group.files
            if review_file.path not in reasons
        ]
        if retained_files:
            retained_groups.append(
                group.model_copy(update={"files": retained_files})
            )

    existing_skips = {
        item.path: item
        for item in (plan.files_to_skip or [])
        if item.path not in reasons
    }
    for diff_file in processed_diff.files:
        if diff_file.path in reasons:
            existing_skips[diff_file.path] = FileToSkip(
                path=diff_file.path,
                reason=reasons[diff_file.path],
            )

    plan.file_groups = retained_groups
    plan.files_to_skip = list(existing_skips.values())
    return plan


def _summarize_file_for_planning(path: str, diff_file: Any = None) -> Dict[str, Any]:
    summary = {
        "path": path,
        "type": diff_file.change_type.value.upper() if diff_file else "MODIFIED",
        "lines_added": diff_file.additions if diff_file else "?",
        "lines_deleted": diff_file.deletions if diff_file else "?",
    }

    if not diff_file:
        return summary

    summary.update({
        "total_changed_lines": diff_file.total_changes,
        "diff_bytes": diff_file.size_bytes,
        "diff_available": bool(diff_file.content),
        "diff_was_limited": _diff_was_limited(diff_file),
        "processed_skip_reason": diff_file.skip_reason or "",
    })

    hunk_headers = _representative_hunk_headers(diff_file.content)
    changed_lines = _representative_changed_lines(diff_file.content)
    if hunk_headers:
        summary["representative_hunk_headers"] = hunk_headers
    if changed_lines:
        summary["representative_changed_lines"] = changed_lines

    return summary


def _diff_was_limited(diff_file: Any = None) -> bool:
    if not diff_file:
        return False
    reason = (diff_file.skip_reason or "").lower()
    return (
        reason.startswith("file too large")
        or reason.startswith("too many lines")
        or reason.startswith("would exceed total size limit")
        or reason.startswith("exceeds max files limit")
    )


def _mechanical_skip_reason(diff_file: Any = None) -> Optional[str]:
    if not diff_file:
        return None
    reason = diff_file.skip_reason or ""
    reason_lower = reason.lower()
    plugin_disposition = getattr(diff_file, "plugin_disposition", None)
    if plugin_disposition in {"generated", "excluded"}:
        return reason or f"Plugin file policy: {plugin_disposition}"
    if getattr(diff_file, "is_binary", False) or reason_lower == "binary file":
        return "Binary file has no text diff to review."
    if (
        getattr(diff_file, "is_gitlink", False)
        or reason_lower == "git submodule pointer"
    ):
        return (
            "Git submodule pointer contains commit identifiers, not source "
            "content from the referenced repository."
        )
    change_type = getattr(diff_file, "change_type", None)
    change_value = getattr(change_type, "value", "").lower()
    if change_value == "deleted" or reason_lower == "deleted file":
        return "Deleted file has no new code to review."
    return None


def _representative_hunk_headers(
    diff_content: str,
    limit: int = 12,
) -> list[str]:
    headers = []
    for line in (diff_content or "").splitlines():
        if line.startswith("@@"):
            headers.append(_truncate_planning_line(line.strip()))
            if len(headers) >= limit:
                break
    return list(dict.fromkeys(headers))


def _representative_changed_lines(
    diff_content: str,
    limit: int = 16,
) -> list[str]:
    changed_lines = []
    for line in (diff_content or "").splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", "-")):
            changed_lines.append(_truncate_planning_line(line))
            if len(changed_lines) >= limit:
                break
    return changed_lines


def _truncate_planning_line(line: str, max_length: int = 240) -> str:
    if len(line) <= max_length:
        return line
    return line[: max_length - 3] + "..."


def _infer_cross_file_concerns(paths: list[str]) -> list[str]:
    if len(paths) < 2:
        return []
    return [
        "Check interactions between changed files because AI planning was unavailable."
    ]
