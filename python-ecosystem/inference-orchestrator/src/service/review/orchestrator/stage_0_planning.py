"""
Stage 0: Planning & Prioritization — analyze PR metadata and build a review plan.
"""
import json
import logging
from typing import Any, Dict, Optional

from model.dtos import ReviewRequestDto
from model.multi_stage import ReviewPlan, FileGroup, ReviewFile, FileToSkip
from utils.prompts.prompt_builder import PromptBuilder
from utils.diff_processor import ProcessedDiff
from utils.task_context_builder import build_task_context

from service.review.orchestrator.agents import extract_llm_response_text
from service.review.telemetry import observed_ainvoke
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


async def execute_stage_0_planning(
    llm,
    request: ReviewRequestDto,
    is_incremental: bool = False,
    processed_diff: Optional[ProcessedDiff] = None,
    use_local_planning: bool = False,
) -> ReviewPlan:
    diff_by_path = _build_diff_lookup(processed_diff)

    changed_files_summary = []
    if request.changedFiles:
        for f in request.changedFiles:
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

    prompt = PromptBuilder.build_stage_0_planning_prompt(
        repo_slug=request.projectVcsRepoSlug,
        pr_id=str(request.pullRequestId),
        pr_title=request.prTitle or "",
        author=request.prAuthor or "Unknown",
        branch_name=request.sourceBranchName or "source-branch",
        target_branch=request.targetBranchName or "main",
        commit_hash=request.commitHash or "HEAD",
        task_context=(
            build_task_context(request.taskContext, max_description_length=4000)
            or "No task context available."
        ),
        changed_files_json=json.dumps(changed_files_summary, indent=2) + refactoring_context,
    )

    structured_output_attempted = supports_structured_output(llm)
    if structured_output_attempted:
        try:
            structured_llm = llm.with_structured_output(ReviewPlan)
            result = await observed_ainvoke(
                structured_llm, prompt, stage="planning", producer="stage_0"
            )
            if result:
                logger.info("Stage 0 planning completed with structured output")
                return result
        except Exception as e:
            logger.warning(f"Structured output failed for Stage 0: {e}")
    else:
        logger.info("Structured output skipped for Stage 0; using prompt JSON parsing")

    try:
        response = await observed_ainvoke(
            llm,
            prompt,
            stage="planning",
            producer="stage_0",
            retry=structured_output_attempted,
        )
        content = extract_llm_response_text(response)
        return await parse_llm_response(content, ReviewPlan, llm)
    except Exception as e:
        logger.error(f"Stage 0 planning failed, using local fallback plan: {e}")
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
    paths = list(dict.fromkeys(request.changedFiles or []))
    diff_by_path = _build_diff_lookup(processed_diff)

    if not paths and processed_diff:
        paths = [df.path for df in processed_diff.files]

    files = []
    files_to_skip = []
    for path in paths:
        diff_file = diff_by_path.get(path) or diff_by_path.get(path.rsplit('/', 1)[-1] if '/' in path else path)
        skip_reason = _mechanical_skip_reason(diff_file)
        if skip_reason:
            files_to_skip.append(FileToSkip(path=path, reason=skip_reason))
            continue

        focus_areas = []
        if _diff_was_limited(diff_file):
            focus_areas.append("SUMMARY_REVIEW")

        files.append(
            ReviewFile(
                path=path,
                focus_areas=focus_areas,
                risk_level="MEDIUM",
                estimated_issues=0,
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
    if getattr(diff_file, "is_binary", False) or reason_lower == "binary file":
        return "Binary file has no text diff to review."
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
