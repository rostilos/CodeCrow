"""
Stage 0: Planning & Prioritization — analyze PR metadata and build a review plan.
"""
import json
import logging
from typing import Any, Dict, Optional

from model.dtos import ReviewRequestDto
from model.multi_stage import ReviewPlan
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
        logger.error(f"Stage 0 planning failed: {e}")
        raise ValueError(f"Stage 0 planning failed: {e}")
