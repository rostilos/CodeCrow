"""
Stage 0: Planning & Prioritization — analyze PR metadata and build a review plan.
"""
import json
import logging
from typing import Any, Dict, Optional

from model.dtos import ReviewRequestDto
from model.multi_stage import ReviewPlan, FileGroup, ReviewFile, FileToSkip
from utils.prompts.prompt_builder import PromptBuilder
from utils.diff_processor import HunkDisposition, ProcessedDiff
from utils.task_context_builder import build_task_context
from service.review.plugin_context import review_plugin_context

from utils.llm_response import extract_llm_response_text
from service.review.orchestrator.json_utils import parse_llm_response, supports_structured_output

logger = logging.getLogger(__name__)


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
            build_task_context(request.taskContext, max_description_length=4000)
            or ""
        ),
        changed_files_json=json.dumps(changed_files_summary, indent=2) + refactoring_context,
        plugin_context=review_plugin_context(
            request,
            planning_paths,
            include_evidence_targets=False,
        ),
    )

    if supports_structured_output(llm):
        try:
            structured_llm = llm.with_structured_output(ReviewPlan)
            result = await structured_llm.ainvoke(prompt)
            if result:
                logger.info("Stage 0 planning completed with structured output")
                return result
        except Exception as e:
            logger.debug("Structured output failed for Stage 0: %s", e)
    else:
        logger.info("Structured output skipped for Stage 0; using prompt JSON parsing")

    try:
        response = await llm.ainvoke(prompt)
        content = extract_llm_response_text(response)
        return await parse_llm_response(content, ReviewPlan, llm)
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


def _representative_hunk_headers(diff_content: str, limit: int = 12) -> list[str]:
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
