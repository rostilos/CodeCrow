"""
Stage 3: Aggregation & final report — executive summary, optional MCP verification.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from model.multi_stage import ReviewPlan, CrossFileAnalysisResult
from utils.prompts.prompt_builder import PromptBuilder
from utils.diff_processor import ProcessedDiff
from utils.task_context_builder import build_task_context

from utils.llm_response import extract_llm_response_text
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
    fallback_llm=None,
) -> Dict[str, Any]:
    stage_1_json = _summarize_issues_for_stage_3(stage_1_issues)
    verification_issues = _stage_3_verification_issue_map(stage_1_issues)
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
    review_revision = _review_revision(request)

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
        review_revision=review_revision,
    )

    if use_mcp_tools and mcp_client and review_revision:
        return await _stage_3_with_mcp(
            llm,
            request,
            prompt,
            mcp_client,
            review_revision,
            verification_issues,
            fallback_llm=fallback_llm,
        )

    if use_mcp_tools and mcp_client and not review_revision:
        logger.warning(
            "[Stage 3] MCP verification skipped: no immutable reviewed commit "
            "hash was supplied"
        )

    return await _invoke_stage_3_report(llm, prompt, fallback_llm=fallback_llm)


def _review_revision(request: ReviewRequestDto) -> str:
    """Return an immutable review revision; never substitute a moving branch."""
    for field in ("currentCommitHash", "commitHash"):
        value = getattr(request, field, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


async def _invoke_stage_3_report(llm, prompt: str, fallback_llm=None) -> Dict[str, Any]:
    response = await llm.ainvoke(prompt)
    if _response_finished_by_length(response) and fallback_llm is not None and fallback_llm is not llm:
        logger.warning("Stage 3 report hit output cap; retrying without output cap")
        response = await fallback_llm.ainvoke(prompt)
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
    # Resolved records are carried to the caller for historical state updates,
    # but they are not open review findings and must not be summarized as such.
    issues = [
        issue
        for issue in issues
        if getattr(issue, "isResolved", False) is not True
    ]
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

    # Stage 3 can verify any finding, including fresh ones without database IDs.
    # Emit a semantically compact record for every active issue instead of
    # clipping details to a top-ten list.
    records = [
        _stage_3_verification_record(verification_id, issue)
        for verification_id, issue in _stage_3_verification_issue_map(issues).items()
    ]
    lines.append("\nComplete verification records (JSON):")
    lines.append(json.dumps(records, ensure_ascii=False, separators=(",", ":")))

    return "\n".join(lines)


def _safe_issue_field(issue: CodeReviewIssue, name: str) -> Any:
    value = getattr(issue, name, "")
    if value is None:
        return ""
    if value.__class__.__module__.startswith("unittest.mock"):
        return ""
    return value


def _stage_3_verification_issue_map(
    issues: List[CodeReviewIssue],
) -> Dict[str, CodeReviewIssue]:
    active = [
        issue
        for issue in issues
        if getattr(issue, "isResolved", False) is not True
    ]
    return {
        f"issue_{index}": issue
        for index, issue in enumerate(active)
    }


_RELATED_LOCATIONS_RE = re.compile(
    r"(?im)^\s*(?:[*_]{1,2})?also affects\s*:(?:[*_]{1,2})?\s*(.+)$"
)


def _stage_3_reason_brief(issue: CodeReviewIssue) -> str:
    """Remove exact repetition while preserving every substantive paragraph."""
    reason = str(_safe_issue_field(issue, "reason") or "").strip()
    if not reason:
        return ""
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", reason)
        if paragraph.strip()
    ]
    title = str(_safe_issue_field(issue, "title") or "").strip().casefold()
    selected: List[str] = []
    seen: set[str] = set()
    for paragraph in paragraphs:
        normalized = " ".join(paragraph.strip("*_# ").casefold().split())
        if normalized == title:
            continue
        if normalized in seen:
            continue
        selected.append(paragraph)
        seen.add(normalized)
    return "\n\n".join(selected) if selected else reason


def _normalized_related_locations(issue: CodeReviewIssue) -> List[str]:
    values = _safe_issue_field(issue, "relatedLocations") or []
    locations = list(values) if isinstance(values, (list, tuple, set)) else []
    reason = str(_safe_issue_field(issue, "reason") or "")
    for match in _RELATED_LOCATIONS_RE.finditer(reason):
        locations.extend(match.group(1).split(","))
    return sorted({
        str(value).strip()
        for value in locations
        if str(value).strip()
    })


def _stage_3_verification_record(
    verification_id: str,
    issue: CodeReviewIssue,
) -> Dict[str, Any]:
    return {
        "verification_id": verification_id,
        "original_id": str(_safe_issue_field(issue, "id") or ""),
        "severity": str(_safe_issue_field(issue, "severity") or ""),
        "category": str(_safe_issue_field(issue, "category") or ""),
        "file": str(_safe_issue_field(issue, "file") or ""),
        "line": _safe_issue_field(issue, "line") or 0,
        "title": str(_safe_issue_field(issue, "title") or ""),
        "reason": _stage_3_reason_brief(issue),
        "exact_source_anchor": str(
            _safe_issue_field(issue, "codeSnippet") or ""
        ),
        "related_locations": _normalized_related_locations(issue),
    }


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
            logger.warning(
                "[Stage 3] DISMISSED_ISSUES was not a list: %s",
                match.group(1),
            )
            return content, []
        dismissed = [str(d) for d in dismissed if d]
        logger.info(
            "[Stage 3] MCP verification requested dismissal of %d issues: %s",
            len(dismissed),
            dismissed,
        )
        clean_report = content[:match.start()].rstrip() + content[match.end():]
        return clean_report.strip(), dismissed
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("[Stage 3] Failed to parse DISMISSED_ISSUES: %s", exc)
        return content, []


def _location_file_path(location: str) -> str:
    normalized = str(location or "").strip().replace("\\", "/").lstrip("/")
    if not normalized:
        return ""
    path, separator, possible_line = normalized.rpartition(":")
    if separator and possible_line.isdigit():
        return path
    return normalized


def _location_line(location: str) -> int:
    normalized = str(location or "").strip().replace("\\", "/")
    _, separator, possible_line = normalized.rpartition(":")
    if separator and possible_line.isdigit():
        return int(possible_line)
    return 0


def _required_verification_locations(
    issue: CodeReviewIssue,
) -> set[tuple[str, int]]:
    primary_path = _location_file_path(
        str(_safe_issue_field(issue, "file") or "")
    )
    try:
        primary_line = int(_safe_issue_field(issue, "line") or 0)
    except (TypeError, ValueError):
        primary_line = 0
    locations = {(primary_path, max(0, primary_line))}
    locations.update(
        (_location_file_path(location), _location_line(location))
        for location in _normalized_related_locations(issue)
    )
    return {(path, line) for path, line in locations if path}


def _mcp_read_covers_location(
    entry: Dict[str, Any],
    verification_id: str,
    file_path: str,
    line: int,
    review_revision: str,
) -> bool:
    args = entry.get("args", {})
    if not (
        entry.get("tool") == "getBranchFileContent"
        and entry.get("success") is True
        and entry.get("evidence_valid") is True
        and str(args.get("verificationId") or "") == verification_id
        and _location_file_path(str(args.get("filePath") or "")) == file_path
        and str(args.get("branch") or "") == review_revision
    ):
        return False
    if entry.get("evidence_complete_file") is True:
        return True
    if line <= 0:
        return False
    try:
        start_line = int(entry.get("evidence_start_line") or 0)
        end_line = int(entry.get("evidence_end_line") or 0)
    except (TypeError, ValueError):
        return False
    return start_line > 0 and start_line <= line <= end_line


def _validated_mcp_dismissals(
    requested_ids: List[str],
    issue_by_verification_id: Dict[str, CodeReviewIssue],
    executor: McpToolExecutor,
    review_revision: str,
) -> List[str]:
    """Accept dismissals only when every affected anchor has bound evidence."""
    validated: List[str] = []
    for verification_id in requested_ids:
        issue = issue_by_verification_id.get(verification_id)
        if issue is None:
            logger.warning(
                "[Stage 3] Ignoring dismissal for unknown verification ID %s",
                verification_id,
            )
            continue
        required_locations = _required_verification_locations(issue)
        missing_locations = {
            location
            for location in required_locations
            if not any(
                _mcp_read_covers_location(
                    entry,
                    verification_id,
                    location[0],
                    location[1],
                    review_revision,
                )
                for entry in executor.call_log
            )
        }
        if not required_locations or missing_locations:
            logger.warning(
                "[Stage 3] Keeping %s: dismissal lacks successful reviewed-revision "
                "evidence for %s",
                verification_id,
                sorted(
                    f"{path}:{line}" if line > 0 else path
                    for path, line in missing_locations
                ),
            )
            continue
        validated.append(verification_id)
    return validated


async def _stage_3_with_mcp(
    llm,
    request: ReviewRequestDto,
    prompt: str,
    mcp_client,
    review_revision: str,
    issue_by_verification_id: Dict[str, CodeReviewIssue],
    fallback_llm=None,
) -> Dict[str, Any]:
    executor = McpToolExecutor(
        mcp_client,
        request,
        stage="stage_3",
        review_revision=review_revision,
        verification_issues=issue_by_verification_id,
    )
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
                if _response_finished_by_length(response) and fallback_llm is not None and fallback_llm is not llm:
                    logger.warning("MCP Stage 3 report hit output cap; retrying without output cap")
                    return await _stage_3_with_mcp(
                        fallback_llm,
                        request,
                        prompt,
                        mcp_client,
                        review_revision,
                        issue_by_verification_id,
                    )
                content = extract_llm_response_text(response)
                logger.info(
                    f"[MCP Stage 3] Completed in {iteration + 1} iterations, "
                    f"{executor.call_count} verification calls"
                )
                report, dismissed = _extract_dismissed_issues(content)
                validated = _validated_mcp_dismissals(
                    dismissed,
                    issue_by_verification_id,
                    executor,
                    review_revision,
                )
                return {
                    "report": report,
                    "dismissed_issue_ids": [
                        str(_safe_issue_field(issue_by_verification_id[key], "id") or "")
                        for key in validated
                        if str(_safe_issue_field(issue_by_verification_id[key], "id") or "")
                    ],
                    "dismissed_issue_keys": validated,
                    "dismissed_issue_object_ids": [
                        id(issue_by_verification_id[key]) for key in validated
                    ],
                }

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
    return await _invoke_stage_3_report(llm, prompt, fallback_llm=fallback_llm)
