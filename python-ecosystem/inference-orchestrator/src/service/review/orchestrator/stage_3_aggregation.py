"""
Stage 3: Aggregation & final report — executive summary, optional MCP verification.
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional

from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from model.multi_stage import ReviewPlan, CrossFileAnalysisResult
from utils.diff_processor import ProcessedDiff
from utils.task_context_builder import build_task_context

from utils.llm_response import extract_llm_response_text
from service.review.orchestrator.inference_policy import (
    ReviewInferenceProfile,
    build_review_inference_profile,
)
from service.review.orchestrator.stage_3_mcp_verification import (
    Stage3McpRuntime,
    _location_line,
    execute_stage_3_mcp_verification,
    extract_dismissed_issues as _extract_dismissed_issues,
    issue_reason_brief as _stage_3_reason_brief,
    location_file_path as _location_file_path,
    mcp_read_covers_location as _mcp_read_covers_location,
    normalized_related_locations as _normalized_related_locations,
    required_verification_locations as _required_verification_locations,
    safe_issue_field as _safe_issue_field,
    validated_mcp_dismissals as _validated_mcp_dismissals,
    verification_issue_map as _stage_3_verification_issue_map,
    verification_record as _stage_3_verification_record,
)
from service.review.orchestrator.stage_3_semantic_packing import (
    _FREE_TEXT_BOUNDARY_RE,
    _FREE_TEXT_SEQUENCE_PLACEHOLDER,
    _PATH_TOKEN_RE,
    _PATH_VALUE_KEYS,
    _SEMANTIC_SHARD_NOTICE,
    _SEMANTIC_TERM_RE,
    _STAGE3_ESTIMATOR_SAFETY_TOKENS,
    _STAGE3_OMISSION_NOTICE_RESERVE_TOKENS,
    _Stage3PromptContext,
    _Stage3PromptShard,
    _Stage3SemanticRecord,
    _build_stage_3_prompt_shards,
    _complete_review_plan_payload,
    _dependency_aware_stage_3_units,
    _estimated_prompt_tokens,
    _estimated_stage_3_messages_tokens,
    _expand_oversized_stage_3_unit,
    _json_record_section,
    _plan_semantic_records,
    _render_complete_stage_3_prompt,
    _render_stage_3_semantic_shard,
    _split_oversized_stage_3_free_text_records,
    _split_stage_3_free_text_record,
    _stage_2_semantic_records,
    _stage_3_component_authority_records,
    _stage_3_component_boundary_anchor,
    _stage_3_declaration_bytes,
    _stage_3_free_text_probe_anchor,
    _stage_3_issue_inventory,
    _stage_3_mcp_continuation_messages,
    _stage_3_message_payload,
    _stage_3_object_value,
    _stage_3_record_paths,
    _stage_3_record_terms,
    _stage_3_splittable_text,
    _stage_3_text_segment_record,
    _stage_3_tool_definitions,
    _task_semantic_records,
)
from service.review.orchestrator.stage_3_synthesis import (
    Stage3SynthesisRuntime,
    _build_stage_3_synthesis_shards,
    _merge_stage_3_results,
    _render_stage_3_synthesis_shard,
    _stable_result_union,
    _stage_3_shard_provenance,
    synthesize_stage_3_results,
)
from llm.reasoning_policy import ReasoningEffort, reasoning_request_kwargs

logger = logging.getLogger(__name__)

# Compatibility exports for callers that historically imported Stage 3
# packing, synthesis, or verification helpers from this coordinator module.
__all__ = [
    "STAGE3_INPUT_TOKEN_TARGET",
    "execute_stage_3_aggregation",
    "_FREE_TEXT_BOUNDARY_RE",
    "_FREE_TEXT_SEQUENCE_PLACEHOLDER",
    "_PATH_TOKEN_RE",
    "_PATH_VALUE_KEYS",
    "_SEMANTIC_SHARD_NOTICE",
    "_SEMANTIC_TERM_RE",
    "_STAGE3_ESTIMATOR_SAFETY_TOKENS",
    "_STAGE3_OMISSION_NOTICE_RESERVE_TOKENS",
    "_Stage3PromptContext",
    "_Stage3PromptShard",
    "_Stage3SemanticRecord",
    "_build_stage_3_prompt_shards",
    "_build_stage_3_synthesis_shards",
    "_complete_review_plan_payload",
    "_dependency_aware_stage_3_units",
    "_estimated_prompt_tokens",
    "_estimated_stage_3_messages_tokens",
    "_expand_oversized_stage_3_unit",
    "_extract_dismissed_issues",
    "_json_record_section",
    "_location_file_path",
    "_location_line",
    "_merge_stage_3_results",
    "_mcp_read_covers_location",
    "_normalized_related_locations",
    "_plan_semantic_records",
    "_render_complete_stage_3_prompt",
    "_render_stage_3_semantic_shard",
    "_render_stage_3_synthesis_shard",
    "_required_verification_locations",
    "_safe_issue_field",
    "_split_oversized_stage_3_free_text_records",
    "_split_stage_3_free_text_record",
    "_stable_result_union",
    "_stage_2_semantic_records",
    "_stage_3_component_authority_records",
    "_stage_3_component_boundary_anchor",
    "_stage_3_declaration_bytes",
    "_stage_3_free_text_probe_anchor",
    "_stage_3_issue_inventory",
    "_stage_3_mcp_continuation_messages",
    "_stage_3_message_payload",
    "_stage_3_object_value",
    "_stage_3_reason_brief",
    "_stage_3_record_paths",
    "_stage_3_record_terms",
    "_stage_3_shard_provenance",
    "_stage_3_splittable_text",
    "_stage_3_text_segment_record",
    "_stage_3_tool_definitions",
    "_stage_3_verification_issue_map",
    "_stage_3_verification_record",
    "_stage_3_with_mcp",
    "_synthesize_stage_3_results",
    "_task_semantic_records",
    "_validated_mcp_dismissals",
]


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


def _request_mcp_local_only(request: Any) -> bool:
    """Match the request DTO's false default for compatible legacy callers."""
    return getattr(request, "mcpLocalOnly", False) is True


# This is an input packing target, not a provider or output-token cap. Stage 3
# shares the rendered-prompt target used by the earlier review stages.
STAGE3_INPUT_TOKEN_TARGET = max(
    10_000,
    _env_int("REVIEW_STAGE1_BATCH_TOKEN_BUDGET", 60_000),
)


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
    inference_profile: Optional[ReviewInferenceProfile] = None,
) -> Dict[str, Any]:
    inference_profile = inference_profile or build_review_inference_profile(
        request,
        processed_diff,
    )
    stage_1_json = _summarize_issues_for_stage_3(stage_1_issues)
    verification_issues = _stage_3_verification_issue_map(stage_1_issues)
    stage_2_json = stage_2_results.model_dump_json(indent=2)
    plan_summary = _summarize_plan_for_stage_3(plan)
    try:
        task_context = (
            build_task_context(request.taskContext)
            or "No task context available."
        )
    except Exception as exception:
        logger.warning(
            "Optional Stage 3 task context could not be rendered; "
            "continuing without it: %s",
            exception,
        )
        task_context = (
            "Task context is unavailable because optional enrichment could "
            "not be rendered."
        )

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
    context = _Stage3PromptContext(
        repo_slug=request.projectVcsRepoSlug,
        pr_id=str(request.pullRequestId),
        author=request.prAuthor or "Unknown",
        pr_title=request.prTitle or "",
        total_files=len(request.changedFiles or []),
        additions=additions,
        deletions=deletions,
        recommendation=stage_2_results.pr_recommendation,
        incremental_context=incremental_context,
        use_mcp_tools=use_mcp_tools,
        review_revision=review_revision,
        issue_inventory=_stage_3_issue_inventory(verification_issues),
        mcp_local_only=_request_mcp_local_only(request),
    )
    token_target = _stage_3_input_token_target(request)

    shards = _build_stage_3_prompt_shards(
        context=context,
        complete_plan_summary=plan_summary,
        complete_stage_1_json=stage_1_json,
        complete_stage_2_json=stage_2_json,
        complete_task_context=task_context,
        plan=plan,
        issue_by_verification_id=verification_issues,
        token_budget=token_target,
        max_children=inference_profile.invocation_cap("stage_3_children"),
    )

    if use_mcp_tools and mcp_client and not review_revision:
        logger.warning(
            "[Stage 3] MCP verification skipped: no immutable reviewed commit "
            "hash was supplied"
        )

    results: List[Dict[str, Any]] = []
    for index, shard in enumerate(shards, start=1):
        estimated_tokens = _estimated_prompt_tokens(
            shard.prompt,
            use_mcp_tools=bool(
                shard.use_mcp_tools and review_revision
            ),
            mcp_local_only=context.mcp_local_only,
        )
        logger.info(
            "Stage 3 prompt assembled: shard=%d/%d chars=%d "
            "estimated_tokens=%d target_tokens=%d records=%d",
            index,
            len(shards),
            len(shard.prompt),
            estimated_tokens,
            token_target,
            len(shard.record_keys),
        )
        shard_issues = {
            verification_id: verification_issues[verification_id]
            for verification_id in shard.verification_ids
        }
        if (
            shard.use_mcp_tools
            and mcp_client
            and review_revision
        ):
            result = await _stage_3_with_mcp(
                llm,
                request,
                shard.prompt,
                mcp_client,
                review_revision,
                shard_issues,
                fallback_llm=fallback_llm,
            )
        else:
            result = await _invoke_stage_3_report(
                llm,
                shard.prompt,
                fallback_llm=fallback_llm,
            )
        results.append(result)

    merged = await _synthesize_stage_3_results(
        llm,
        context=context,
        input_results=results,
        input_shards=shards,
        token_budget=token_target,
        fallback_llm=fallback_llm,
    )
    merged["stage_3_prompt_provenance"] = {
        "inputShards": [
            _stage_3_shard_provenance(
                shard,
                phase="analysis",
                level=0,
                index=index,
            )
            for index, shard in enumerate(shards, start=1)
        ],
        "synthesisShards": merged.pop("_synthesis_provenance", []),
    }
    return merged


def _positive_int_or_default(value: Any, default: int) -> int:
    if (
        value is None
        or isinstance(value, bool)
        or value.__class__.__module__.startswith("unittest.mock")
    ):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _stage_3_input_token_target(request: ReviewRequestDto) -> int:
    """Honor the request's model-context hint while reserving response room."""
    model_context_tokens = _positive_int_or_default(
        getattr(request, "maxAllowedTokens", None),
        200_000,
    )
    if model_context_tokens > 20_000:
        model_safe_target = model_context_tokens - 20_000
    else:
        # Preserve generation room even for an unusually small provider hint.
        model_safe_target = max(1, model_context_tokens // 2)
    return min(STAGE3_INPUT_TOKEN_TARGET, model_safe_target)


async def _synthesize_stage_3_results(
    llm,
    *,
    context: _Stage3PromptContext,
    input_results: List[Dict[str, Any]],
    input_shards: List[_Stage3PromptShard],
    token_budget: int,
    fallback_llm=None,
) -> Dict[str, Any]:
    """Compatibility facade over the isolated synthesis subsystem."""
    return await synthesize_stage_3_results(
        llm,
        context=context,
        input_results=input_results,
        input_shards=input_shards,
        token_budget=token_budget,
        runtime=Stage3SynthesisRuntime(
            report_invoker=_invoke_stage_3_report,
        ),
        fallback_llm=fallback_llm,
    )


def _review_revision(request: ReviewRequestDto) -> str:
    """Return an immutable review revision; never substitute a moving branch."""
    for field in ("currentCommitHash", "commitHash"):
        value = getattr(request, field, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


async def _invoke_stage_3_report(
    llm,
    prompt: str,
    fallback_llm=None,
    allow_retry: bool = True,
    reasoning_effort: ReasoningEffort = ReasoningEffort.LOW,
) -> Dict[str, Any]:
    response = await llm.ainvoke(
        prompt,
        **reasoning_request_kwargs(llm, reasoning_effort),
    )
    if (
        _response_finished_by_length(response)
        and allow_retry
        and fallback_llm is not None
    ):
        logger.info(
            "Stage 3 report exhausted its output; retrying once as a "
            "reasoning-free direct output request"
        )
        response = await fallback_llm.ainvoke(
            prompt,
            **reasoning_request_kwargs(
                fallback_llm,
                ReasoningEffort.NONE,
            ),
        )
    return {"report": extract_llm_response_text(response), "dismissed_issue_ids": []}


def _response_finished_by_length(response) -> bool:
    metadata = getattr(response, "response_metadata", None) or {}
    generation_info = getattr(response, "generation_info", None) or {}
    candidates = [
        metadata.get("finish_reason"),
        metadata.get("stop_reason"),
        metadata.get("finishReason"),
        (
            generation_info.get("finish_reason")
            if isinstance(generation_info, dict)
            else None
        ),
    ]
    return any(
        str(value).lower() in {"length", "max_tokens", "max_output_tokens"}
        for value in candidates
        if value
    )


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


def _summarize_plan_for_stage_3(plan: ReviewPlan) -> str:
    complete_payload = _complete_review_plan_payload(plan)
    lines = []
    total_files = sum(
        len(group["files"])
        for group in complete_payload["file_groups"]
    )
    lines.append(f"Total files planned for review: {total_files}")

    priority_counts: Dict[str, int] = {}
    for group in complete_payload["file_groups"]:
        priority = str(group["priority"] or "").upper()
        priority_counts[priority] = (
            priority_counts.get(priority, 0) + len(group["files"])
        )
    if priority_counts:
        lines.append("By priority: " + ", ".join(
            f"{k}: {v} files" for k, v in sorted(priority_counts.items())
        ))

    if complete_payload["cross_file_concerns"]:
        lines.append(
            "Cross-file concern count: "
            f"{len(complete_payload['cross_file_concerns'])}"
        )
    if complete_payload["files_to_skip"]:
        lines.append(
            "Files skipped from deep review: "
            f"{len(complete_payload['files_to_skip'])}"
        )

    lines.extend((
        "\nComplete ReviewPlan record (JSON):",
        json.dumps(
            complete_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    ))
    return "\n".join(lines)


# ── MCP verification ─────────────────────────────────────────


async def _stage_3_with_mcp(
    llm,
    request: ReviewRequestDto,
    prompt: str,
    mcp_client,
    review_revision: str,
    issue_by_verification_id: Dict[str, CodeReviewIssue],
    fallback_llm=None,
) -> Dict[str, Any]:
    runtime = Stage3McpRuntime(
        input_token_target=_stage_3_input_token_target,
        estimate_messages_tokens=lambda messages, *, use_mcp_tools: (
            _estimated_stage_3_messages_tokens(
                messages,
                use_mcp_tools=use_mcp_tools,
                mcp_local_only=_request_mcp_local_only(request),
            )
        ),
        continuation_messages=_stage_3_mcp_continuation_messages,
        invoke_report=_invoke_stage_3_report,
        response_finished_by_length=_response_finished_by_length,
    )
    return await execute_stage_3_mcp_verification(
        llm,
        request,
        prompt,
        mcp_client,
        review_revision,
        issue_by_verification_id,
        runtime,
        fallback_llm=fallback_llm,
    )
