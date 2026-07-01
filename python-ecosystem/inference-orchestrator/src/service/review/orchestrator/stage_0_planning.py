"""
Stage 0: Planning & Prioritization — analyze PR metadata and build a review plan.
"""
import json
import logging
from typing import Any, Dict, Optional

from model.dtos import ReviewRequestDto
from model.multi_stage import ReviewPlan, FileGroup, ReviewFile
from utils.prompts.prompt_builder import PromptBuilder
from utils.diff_processor import ProcessedDiff

from service.review.orchestrator.agents import extract_llm_response_text
from service.review.orchestrator.json_utils import parse_llm_response

logger = logging.getLogger(__name__)


async def execute_stage_0_planning(
    llm,
    request: ReviewRequestDto,
    is_incremental: bool = False,
    processed_diff: Optional[ProcessedDiff] = None,
    use_local_planning: bool = False,
) -> ReviewPlan:
    diff_by_path: Dict[str, Any] = {}
    if processed_diff:
        for df in processed_diff.files:
            diff_by_path[df.path] = df
            if '/' in df.path:
                diff_by_path[df.path.rsplit('/', 1)[-1]] = df

    changed_files_summary = []
    if request.changedFiles:
        for f in request.changedFiles:
            df = diff_by_path.get(f) or diff_by_path.get(f.rsplit('/', 1)[-1] if '/' in f else f)
            changed_files_summary.append({
                "path": f,
                "type": df.change_type.value.upper() if df else "MODIFIED",
                "lines_added": df.additions if df else "?",
                "lines_deleted": df.deletions if df else "?",
            })

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
        changed_files_json=json.dumps(changed_files_summary, indent=2) + refactoring_context,
    )

    try:
        structured_llm = llm.with_structured_output(ReviewPlan)
        result = await structured_llm.ainvoke(prompt)
        if result:
            logger.info("Stage 0 planning completed with structured output")
            return result
    except Exception as e:
        logger.warning(f"Structured output failed for Stage 0: {e}")

    try:
        response = await llm.ainvoke(prompt)
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
    diff_by_path = {df.path: df for df in processed_diff.files} if processed_diff else {}

    if not paths and processed_diff:
        paths = [df.path for df in processed_diff.files if not df.is_skipped]

    groups: Dict[str, list[ReviewFile]] = {
        "HIGH": [],
        "MEDIUM": [],
        "LOW": [],
    }

    for path in paths:
        diff_file = diff_by_path.get(path)
        priority = _infer_file_priority(path, diff_file)
        groups[priority].append(
            ReviewFile(
                path=path,
                focus_areas=_infer_focus_areas(path),
                risk_level=priority,
                estimated_issues=0,
            )
        )

    file_groups = []
    for priority in ("HIGH", "MEDIUM", "LOW"):
        files = groups[priority]
        if not files:
            continue
        file_groups.append(
            FileGroup(
                group_id=f"FALLBACK_{priority}",
                priority=priority,
                rationale="Local fallback plan generated because AI planning output was unavailable",
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
        cross_file_concerns=_infer_cross_file_concerns(paths) if infer_cross_file_concerns else [],
    )


def _infer_file_priority(path: str, diff_file: Any = None) -> str:
    lower = path.lower()
    if any(marker in lower for marker in (
        "auth",
        "security",
        "permission",
        "billing",
        "payment",
        "migration",
        "schema",
        "controller",
        "handler",
        "service",
        "repository",
    )):
        return "HIGH"
    if diff_file and getattr(diff_file, "additions", 0) + getattr(diff_file, "deletions", 0) > 200:
        return "HIGH"
    if any(lower.endswith(ext) for ext in (
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".lock",
    )):
        return "LOW"
    if any(marker in lower for marker in ("/test/", "/tests/", ".test.", ".spec.", "test_")):
        return "LOW"
    return "MEDIUM"


def _infer_focus_areas(path: str) -> list[str]:
    lower = path.lower()
    focus = []
    if any(marker in lower for marker in ("auth", "security", "permission")):
        focus.append("SECURITY")
    if any(marker in lower for marker in ("migration", "schema", "repository", "entity", "model")):
        focus.append("DATA_ACCESS")
    if any(marker in lower for marker in ("controller", "handler", "api")):
        focus.append("API_CONTRACT")
    if any(marker in lower for marker in ("/test/", "/tests/", ".test.", ".spec.", "test_")):
        focus.append("TESTING")
    return focus or ["GENERAL"]


def _infer_cross_file_concerns(paths: list[str]) -> list[str]:
    if len(paths) < 2:
        return []
    return [
        "Check interactions between changed files because AI planning was unavailable."
    ]
