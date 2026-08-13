"""
Stage 0: Planning & Prioritization — analyze PR metadata and build a review plan.
"""
import json
import logging
import os
import re
from typing import Any, Dict, Optional

from model.dtos import ReviewRequestDto
from model.multi_stage import ReviewPlan, FileGroup, ReviewFile, FileToSkip
from utils.prompts.prompt_builder import PromptBuilder
from utils.diff_processor import HunkDisposition, ProcessedDiff
from utils.task_context_builder import build_task_context
from service.review.plugin_context import review_plugin_context

from utils.llm_response import extract_llm_response_text
from service.review.orchestrator.json_utils import (
    load_json_with_local_repairs,
    supports_structured_output,
)
from utils.path_identity import normalize_repository_path
from utils.prompts.review_messages import to_review_messages

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("Invalid integer for %s; using %d", name, default)
        return default


STAGE0_PLANNING_CHAR_BUDGET = max(
    20_000,
    _env_int("REVIEW_STAGE0_PLANNING_CHAR_BUDGET", 120_000),
)
_RISK_LEVELS = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


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
    host_plan = _build_fallback_review_plan(
        request,
        processed_diff,
        analysis_summary=(
            "Fallback review plan generated locally as a host-owned mandatory "
            "manifest. LLM annotations are additive and cannot alter coverage."
        ),
        infer_cross_file_concerns=False,
    )

    changed_files_summary = _build_bounded_planning_summaries(
        request,
        planning_paths,
        diff_by_path,
    )

    # Include refactoring signals so the planner can adjust expectations
    refactoring_context = ""
    if processed_diff and processed_diff.refactoring_signals:
        refactoring_context = (
            "\n\n⚠️ REFACTORING SIGNALS DETECTED:\n"
            + "\n".join(
                f"- {_truncate_planning_line(str(signal), 300)}"
                for signal in processed_diff.refactoring_signals[:12]
            )
            + "\nThese suggest code reorganisation rather than new functionality. "
            "Flag fewer issues for moved/renamed code — focus on real regressions."
        )

    if use_local_planning:
        logger.info("Stage 0 fast check: using local deterministic review plan")
        return host_plan.model_copy(update={
            "analysis_summary": (
                "Fast check uses the host-owned mandatory review plan without "
                "optional LLM annotations."
            ),
        })

    if processed_diff is not None and not planning_paths:
        logger.info(
            "Stage 0 provider call skipped: host manifest contains no reviewable paths"
        )
        return host_plan.model_copy(update={
            "analysis_summary": (
                "No changed source hunks require direct review after deterministic "
                "file-policy and mechanical disposition accounting."
            ),
        })

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
    messages = to_review_messages(prompt)

    if supports_structured_output(llm):
        try:
            structured_llm = llm.with_structured_output(ReviewPlan)
            result = await structured_llm.ainvoke(messages)
            if result:
                logger.info("Stage 0 annotation completed with structured output")
                return _apply_planner_annotations(host_plan, result)
        except Exception as e:
            logger.info(
                "Stage 0 annotation unavailable; preserving the complete host plan: %s",
                e,
            )
        # A failed or empty structured generation is one incomplete annotation
        # call, not permission to spend another call on JSON repair/fallback.
        return host_plan
    else:
        logger.info("Structured output skipped for Stage 0; using prompt JSON parsing")

    try:
        response = await llm.ainvoke(messages)
        content = extract_llm_response_text(response)
        _, payload = load_json_with_local_repairs(content)
        annotation = ReviewPlan(**payload)
        return _apply_planner_annotations(host_plan, annotation)
    except Exception as e:
        logger.info("Stage 0 planning unavailable; using local fallback plan: %s", e)
        return host_plan


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


def _summarize_file_for_planning(
    path: str,
    diff_file: Any = None,
    current_source: Optional[str] = None,
    *,
    include_unit_detail: bool = True,
) -> Dict[str, Any]:
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

    reviewable_hunks = [
        hunk
        for hunk in (getattr(diff_file, "hunks", None) or ())
        if hunk.disposition is HunkDisposition.REVIEWABLE
    ]
    summary["mandatory_unit_count"] = len(reviewable_hunks)
    if include_unit_detail and reviewable_hunks:
        summary["mandatory_units"] = [
            _planning_unit_header(hunk, current_source)
            for hunk in reviewable_hunks
        ]

    return summary


def _build_bounded_planning_summaries(
    request: ReviewRequestDto,
    planning_paths: list[str],
    diff_by_path: Dict[str, Any],
) -> list[Dict[str, Any]]:
    content_by_path: Dict[str, str] = {}
    enrichment = getattr(request, "enrichmentData", None)
    for file_content in getattr(enrichment, "fileContents", None) or ():
        path = normalize_repository_path(getattr(file_content, "path", ""))
        content = getattr(file_content, "content", None)
        if path and isinstance(content, str) and content:
            content_by_path[path] = content

    summaries: list[Dict[str, Any]] = []
    used_chars = 2
    omitted_units = 0
    omitted_files = 0
    for path in planning_paths:
        diff_file = diff_by_path.get(path) or diff_by_path.get(
            path.rsplit("/", 1)[-1] if "/" in path else path
        )
        source = content_by_path.get(normalize_repository_path(path))
        detailed = _summarize_file_for_planning(path, diff_file, source)
        detailed_chars = len(json.dumps(detailed, ensure_ascii=False, default=str))
        if used_chars + detailed_chars <= STAGE0_PLANNING_CHAR_BUDGET:
            summaries.append(detailed)
            used_chars += detailed_chars
            continue
        compact = _summarize_file_for_planning(
            path,
            diff_file,
            None,
            include_unit_detail=False,
        )
        compact["annotation_context_omitted"] = True
        compact_chars = len(json.dumps(compact, ensure_ascii=False, default=str))
        if used_chars + compact_chars <= STAGE0_PLANNING_CHAR_BUDGET:
            summaries.append(compact)
            used_chars += compact_chars
        else:
            omitted_files += 1
            omitted_units += int(compact.get("mandatory_unit_count") or 0)
    if omitted_files:
        summaries.append({
            "annotation_manifest_truncated": True,
            "omitted_file_count": omitted_files,
            "omitted_mandatory_unit_count": omitted_units,
            "coverage_effect": "none; host-owned Stage 1 review remains mandatory",
        })
    return summaries


def _planning_unit_header(hunk, current_source: Optional[str]) -> Dict[str, Any]:
    changed_lines = [
        _truncate_planning_line(line)
        for line in hunk.content.splitlines()
        if line.startswith(("+", "-"))
        and not line.startswith(("+++", "---"))
    ][:12]
    header: Dict[str, Any] = {
        "unit_id": hunk.id,
        "hunk_header": hunk.header,
        "new_start": hunk.new_start,
        "new_count": hunk.new_count,
        "changed_lines": changed_lines,
    }
    if current_source and hunk.new_start > 0:
        lines = current_source.splitlines()
        start = max(1, hunk.new_start - 6)
        end = min(
            len(lines),
            hunk.new_start + max(1, hunk.new_count) + 6,
        )
        if start <= end:
            window = "\n".join(lines[start - 1:end])
            header["current_source_window"] = {
                "start_line": start,
                "end_line": end,
                "content": window[:2_400],
            }
    return header


def _apply_planner_annotations(
    host_plan: ReviewPlan,
    annotation: ReviewPlan,
) -> ReviewPlan:
    """Apply only bounded risk/focus/hypothesis annotations to host coverage."""
    annotation_by_path: Dict[str, ReviewFile] = {}
    for group in annotation.file_groups or ():
        for review_file in group.files or ():
            key = normalize_repository_path(review_file.path)
            if key and key not in annotation_by_path:
                annotation_by_path[key] = review_file

    host_files = [
        review_file
        for group in host_plan.file_groups
        for review_file in group.files
    ]
    buckets: Dict[str, list[ReviewFile]] = {risk: [] for risk in _RISK_LEVELS}
    allowed_paths = {normalize_repository_path(item.path) for item in host_files}
    for host_file in host_files:
        planner_file = annotation_by_path.get(
            normalize_repository_path(host_file.path)
        )
        risk = str(
            getattr(planner_file, "risk_level", "MEDIUM") or "MEDIUM"
        ).strip().upper()
        if risk not in _RISK_LEVELS:
            risk = "MEDIUM"
        focus_areas = _sanitize_focus_areas(
            getattr(planner_file, "focus_areas", ()) if planner_file else ()
        )
        buckets[risk].append(host_file.model_copy(update={
            "risk_level": risk,
            "focus_areas": focus_areas,
        }))

    groups = [
        FileGroup(
            group_id=f"HOST_ANNOTATED_{risk}",
            priority=risk,
            rationale=(
                "Host-owned mandatory units grouped by bounded planner risk "
                "annotation; universal review coverage is unchanged."
            ),
            files=buckets[risk],
        )
        for risk in _RISK_LEVELS
        if buckets[risk]
    ]
    annotation.analysis_summary = str(annotation.analysis_summary or "")[:1_000]
    annotation.file_groups = groups
    annotation.files_to_skip = list(host_plan.files_to_skip or ())
    annotation.cross_file_concerns = _sanitize_cross_file_hypotheses(
        annotation.cross_file_concerns,
        allowed_paths,
    )
    return annotation


def _sanitize_focus_areas(values) -> list[str]:
    return list(dict.fromkeys(
        str(value).strip()[:80]
        for value in (values or ())
        if str(value).strip()
    ))[:8]


_PATH_TOKEN = re.compile(r"(?<![\w.-])([\w.-]+(?:/[\w.@+-]+)+)")


def _sanitize_cross_file_hypotheses(
    values,
    allowed_paths: set[str],
) -> list[str]:
    retained = []
    for value in values or ():
        hypothesis = str(value or "").strip()[:500]
        if not hypothesis:
            continue
        mentioned_paths = {
            normalize_repository_path(match)
            for match in _PATH_TOKEN.findall(hypothesis)
        }
        if mentioned_paths and not mentioned_paths.issubset(allowed_paths):
            continue
        retained.append(hypothesis)
        if len(retained) >= 8:
            break
    return list(dict.fromkeys(retained))


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
