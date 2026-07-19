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
from utils.task_context_builder import build_task_context

from service.review.orchestrator.agents import extract_llm_response_text
from service.review.telemetry import observed_ainvoke
from service.review.orchestrator.mcp_tool_executor import McpToolExecutor

logger = logging.getLogger(__name__)


def build_deterministic_stage_3_report(
    request: ReviewRequestDto,
    plan: ReviewPlan,
    issues: List[CodeReviewIssue],
    stage_2_results: CrossFileAnalysisResult,
    processed_diff: Optional[ProcessedDiff] = None,
) -> Dict[str, Any]:
    """Build the manifest report from verified pipeline outputs without inference."""
    planned_files = sum(len(group.files) for group in plan.file_groups)
    reviewed_files = len(request.changedFiles or []) or planned_files
    issue_count = len(issues)
    file_label = "file" if reviewed_files == 1 else "files"
    issue_label = "issue" if issue_count == 1 else "issues"

    lines = [
        "## Code review summary",
        "",
        f"Reviewed {reviewed_files} changed {file_label}. "
        f"Found {issue_count} source-verified {issue_label}.",
    ]
    if processed_diff is not None:
        lines.append(
            f"Diff size: +{processed_diff.total_additions} "
            f"/-{processed_diff.total_deletions}."
        )

    if issues:
        severity_counts: Dict[str, int] = {}
        for issue in issues:
            severity = (issue.severity or "UNKNOWN").upper()
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
        severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
        ordered_counts = sorted(
            severity_counts.items(),
            key=lambda item: (severity_order.get(item[0], 5), item[0]),
        )
        lines.extend(
            [
                "",
                "Severity: " + ", ".join(
                    f"{severity}: {count}" for severity, count in ordered_counts
                ),
                "",
                "Top findings:",
            ]
        )
        for issue in issues[:5]:
            title = (issue.title or "Review finding").strip()
            lines.append(
                f"- **{issue.severity.upper()}** `{issue.file}:{issue.line}` — {title}"
            )
        if issue_count > 5:
            lines.append(f"- …and {issue_count - 5} more; see the inline findings.")
    else:
        lines.extend(["", "No actionable findings remain after source-evidence checks."])

    recommendation = (stage_2_results.pr_recommendation or "").strip()
    if recommendation:
        lines.extend(["", f"Recommendation: {recommendation}"])

    return {"report": "\n".join(lines), "dismissed_issue_ids": []}


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
    fallback_llm=None,
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
        author=request.prAuthor or "Unknown",
        pr_title=request.prTitle or "",
        total_files=len(request.changedFiles or []),
        additions=additions,
        deletions=deletions,
        stage_0_plan=plan_summary,
        stage_1_issues_json=stage_1_json,
        stage_2_findings_json=stage_2_json,
        recommendation=stage_2_results.pr_recommendation,
        incremental_context=incremental_context,
        task_context=(
            build_task_context(request.taskContext, max_description_length=4000)
            or "No task context available."
        ),
        use_mcp_tools=use_mcp_tools,
        target_branch=target_branch,
    )

    if use_mcp_tools and mcp_client and target_branch:
        return await _stage_3_with_mcp(
            llm,
            request,
            prompt,
            mcp_client,
            target_branch,
            fallback_llm=fallback_llm,
        )

    return await _invoke_stage_3_report(llm, prompt, fallback_llm=fallback_llm)


async def _invoke_stage_3_report(llm, prompt: str, fallback_llm=None) -> Dict[str, Any]:
    response = await observed_ainvoke(
        llm, prompt, stage="aggregation", producer="stage_3"
    )
    if _response_finished_by_length(response) and fallback_llm is not None and fallback_llm is not llm:
        logger.warning("Stage 3 report hit output cap; retrying without output cap")
        response = await observed_ainvoke(
            fallback_llm,
            prompt,
            stage="aggregation",
            producer="stage_3_retry",
            retry=True,
        )
    return {"report": extract_llm_response_text(response), "dismissed_issue_ids": []}


def _response_finished_by_length(response) -> bool:
    metadata = getattr(response, "response_metadata", None) or {}
    generation_info = getattr(response, "generation_info", None) or {}
    candidates = [
        metadata.get("finish_reason"),
        metadata.get("stop_reason"),
        metadata.get("finishReason"),
        generation_info.get("finish_reason") if isinstance(generation_info, dict) else None,
    ]
    return any(str(value).lower() in {"length", "max_tokens", "max_output_tokens"} for value in candidates if value)


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
    lines.append("\nTop findings (issue IDs are for internal reference):")
    for i, issue in enumerate(top_n, 1):
        issue_id = getattr(issue, 'id', '') or ''
        title = getattr(issue, 'title', '') or ''
        title_part = f" {title} —" if title else ""
        lines.append(f"  {i}. [id={issue_id}] [{issue.severity}] {issue.file}:{title_part} {issue.reason[:120]}")

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
        # The marker pattern only captures JSON arrays, so a successful parse
        # is necessarily a list.  Keeping a second type branch here made the
        # policy surface untestable without manufacturing an impossible input.
        dismissed = [str(d) for d in dismissed if d]
        logger.info(f"[Stage 3] MCP verification dismissed {len(dismissed)} issues: {dismissed}")
        clean_report = content[:match.start()].rstrip() + content[match.end():]
        return clean_report.strip(), dismissed
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(
            "[Stage 3] Failed to parse DISMISSED_ISSUES: error_type=%s",
            type(e).__name__,
        )
        return content, []


async def _stage_3_with_mcp(
    llm,
    request: ReviewRequestDto,
    prompt: str,
    mcp_client,
    target_branch: str,
    fallback_llm=None,
) -> Dict[str, Any]:
    executor = McpToolExecutor(mcp_client, request, stage="stage_3")
    tool_defs = executor.get_tool_definitions()
    max_iterations = 15

    messages = [{"role": "user", "content": prompt}]

    for iteration in range(max_iterations):
        try:
            llm_with_tools = llm.bind_tools(tool_defs)
            response = await observed_ainvoke(
                llm_with_tools,
                messages,
                stage="aggregation",
                producer="stage_3_tools",
            )
            messages.append(response)

            tool_calls = getattr(response, 'tool_calls', None)
            if not tool_calls:
                if _response_finished_by_length(response) and fallback_llm is not None and fallback_llm is not llm:
                    logger.warning("MCP Stage 3 report hit output cap; retrying without output cap")
                    return await _stage_3_with_mcp(
                        fallback_llm,
                        request,
                        prompt,
                        mcp_client,
                        target_branch,
                    )
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
            logger.warning(
                "[MCP Stage 3] Iteration %s failed: error_type=%s",
                iteration + 1,
                type(e).__name__,
            )
            break

    logger.warning("[MCP Stage 3] Agentic loop exhausted, falling back to plain call")
    return await _invoke_stage_3_report(llm, prompt, fallback_llm=fallback_llm)
