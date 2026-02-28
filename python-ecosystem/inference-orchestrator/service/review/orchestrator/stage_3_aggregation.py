"""
Stage 3: Aggregation & final report — executive summary, optional MCP verification.
"""
import json
import logging
from typing import Any, Dict, List, Optional

from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from model.multi_stage import ReviewPlan, CrossFileAnalysisResult
from utils.prompts.prompt_builder import PromptBuilder
from utils.diff_processor import ProcessedDiff

from service.review.orchestrator.agents import extract_llm_response_text
from service.review.orchestrator.mcp_tool_executor import McpToolExecutor

logger = logging.getLogger(__name__)


async def execute_stage_3_aggregation(
    llm,
    request: ReviewRequestDto,
    plan: ReviewPlan,
    stage_1_issues: List[CodeReviewIssue],
    stage_2_results: CrossFileAnalysisResult,
    is_incremental: bool = False,
    processed_diff: Optional[ProcessedDiff] = None,
    mcp_client=None,
    use_mcp_tools: bool = False,
) -> Dict[str, Any]:
    stage_1_json = _summarize_issues_for_stage_3(stage_1_issues)
    stage_2_json = stage_2_results.model_dump_json(indent=2)
    plan_summary = _summarize_plan_for_stage_3(plan)

    incremental_context = ""
    if is_incremental:
        resolved_count = sum(1 for i in stage_1_issues if i.isResolved)
        new_count = len(stage_1_issues) - resolved_count
        previous_count = len(request.previousCodeAnalysisIssues or [])
        incremental_context = f"""
## INCREMENTAL REVIEW SUMMARY
- Previous issues from last review: {previous_count}
- Issues resolved in this update: {resolved_count}
- New issues found in delta: {new_count}
- Total issues after reconciliation: {len(stage_1_issues)}
"""

    additions = processed_diff.total_additions if processed_diff else 0
    deletions = processed_diff.total_deletions if processed_diff else 0
    target_branch = request.targetBranchName or ""

    prompt = PromptBuilder.build_stage_3_aggregation_prompt(
        repo_slug=request.projectVcsRepoSlug,
        pr_id=str(request.pullRequestId),
        author="Unknown",
        pr_title=request.prTitle or "",
        total_files=len(request.changedFiles or []),
        additions=additions,
        deletions=deletions,
        stage_0_plan=plan_summary,
        stage_1_issues_json=stage_1_json,
        stage_2_findings_json=stage_2_json,
        recommendation=stage_2_results.pr_recommendation,
        incremental_context=incremental_context,
        use_mcp_tools=use_mcp_tools,
        target_branch=target_branch,
    )

    if use_mcp_tools and mcp_client and target_branch:
        return await _stage_3_with_mcp(llm, request, prompt, mcp_client, target_branch)

    response = await llm.ainvoke(prompt)
    return {"report": extract_llm_response_text(response), "dismissed_issue_ids": []}


# ── Summary builders ──────────────────────────────────────────


def _summarize_issues_for_stage_3(issues: List[CodeReviewIssue]) -> str:
    if not issues:
        return "No issues found in Stage 1."

    severity_counts: Dict[str, int] = {}
    category_counts: Dict[str, int] = {}
    for issue in issues:
        sev = issue.severity.upper()
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
        cat = issue.category.upper()
        category_counts[cat] = category_counts.get(cat, 0) + 1

    lines = [
        f"Total issues: {len(issues)}",
        "By severity: " + ", ".join(f"{k}: {v}" for k, v in sorted(severity_counts.items())),
        "By category: " + ", ".join(f"{k}: {v}" for k, v in sorted(category_counts.items())),
    ]

    priority_order = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3, 'INFO': 4}
    ranked = sorted(issues, key=lambda i: priority_order.get(i.severity.upper(), 5))
    top_n = ranked[:10]
    if top_n:
        lines.append("\nTop findings (issue IDs are for internal reference):")
        for i, issue in enumerate(top_n, 1):
            issue_id = getattr(issue, 'id', '') or ''
            title = getattr(issue, 'title', '') or ''
            title_part = f" {title} —" if title else ""
            lines.append(f"  {i}. [id={issue_id}] [{issue.severity}] {issue.file}:{title_part} {issue.reason[:120]}")

    if issues:
        all_ids = [getattr(i, 'id', '') or '' for i in issues]
        all_ids = [i for i in all_ids if i]
        if all_ids:
            lines.append(f"\nAll issue IDs: {', '.join(all_ids)}")

    return "\n".join(lines)


def _summarize_plan_for_stage_3(plan: ReviewPlan) -> str:
    lines = []
    total_files = sum(len(g.files) for g in plan.file_groups)
    lines.append(f"Total files planned for review: {total_files}")

    priority_counts: Dict[str, int] = {}
    for group in plan.file_groups:
        p = group.priority.upper()
        priority_counts[p] = priority_counts.get(p, 0) + len(group.files)
    if priority_counts:
        lines.append("By priority: " + ", ".join(
            f"{k}: {v} files" for k, v in sorted(priority_counts.items())
        ))

    if plan.cross_file_concerns:
        lines.append(f"\nCross-file concerns ({len(plan.cross_file_concerns)}):")
        for concern in plan.cross_file_concerns[:5]:
            lines.append(f"  - {concern[:150]}")

    all_paths = [f.path for g in plan.file_groups for f in g.files]
    if all_paths:
        lines.append(f"\nFiles reviewed: {', '.join(all_paths[:20])}")
        if len(all_paths) > 20:
            lines.append(f"  ... and {len(all_paths) - 20} more")

    return "\n".join(lines)


# ── MCP verification ─────────────────────────────────────────


def _extract_dismissed_issues(content: str) -> tuple:
    import re as _re
    pattern = r'<!--\s*DISMISSED_ISSUES:\s*(\[.*?\])\s*-->'
    match = _re.search(pattern, content, _re.DOTALL)
    if not match:
        return content, []

    try:
        dismissed = json.loads(match.group(1))
        if not isinstance(dismissed, list):
            logger.warning(f"[Stage 3] DISMISSED_ISSUES was not a list: {match.group(1)}")
            return content, []
        dismissed = [str(d) for d in dismissed if d]
        logger.info(f"[Stage 3] MCP verification dismissed {len(dismissed)} issues: {dismissed}")
        clean_report = content[:match.start()].rstrip() + content[match.end():]
        return clean_report.strip(), dismissed
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"[Stage 3] Failed to parse DISMISSED_ISSUES: {e}")
        return content, []


async def _stage_3_with_mcp(
    llm,
    request: ReviewRequestDto,
    prompt: str,
    mcp_client,
    target_branch: str,
) -> Dict[str, Any]:
    executor = McpToolExecutor(mcp_client, request, stage="stage_3")
    tool_defs = executor.get_tool_definitions()
    max_iterations = 15

    messages = [{"role": "user", "content": prompt}]

    for iteration in range(max_iterations):
        try:
            llm_with_tools = llm.bind_tools(tool_defs)
            response = await llm_with_tools.ainvoke(messages)
            messages.append(response)

            tool_calls = getattr(response, 'tool_calls', None)
            if not tool_calls:
                content = extract_llm_response_text(response)
                logger.info(
                    f"[MCP Stage 3] Completed in {iteration + 1} iterations, "
                    f"{executor.call_count} verification calls"
                )
                report, dismissed = _extract_dismissed_issues(content)
                return {"report": report, "dismissed_issue_ids": dismissed}

            for tc in tool_calls:
                tool_result = await executor.execute_tool(tc["name"], tc["args"])
                messages.append({
                    "role": "tool",
                    "content": str(tool_result),
                    "tool_call_id": tc["id"],
                })

        except Exception as e:
            logger.warning(f"[MCP Stage 3] Iteration {iteration + 1} failed: {e}")
            break

    logger.warning("[MCP Stage 3] Agentic loop exhausted, falling back to plain call")
    response = await llm.ainvoke(prompt)
    return {"report": extract_llm_response_text(response), "dismissed_issue_ids": []}
