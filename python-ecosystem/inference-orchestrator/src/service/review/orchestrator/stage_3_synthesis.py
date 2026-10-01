"""Hierarchical report synthesis for Stage 3 aggregation shards."""

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Dict, List, Protocol

from service.review.orchestrator.stage_3_semantic_packing import (
    _Stage3PromptContext,
    _Stage3PromptShard,
    _Stage3SemanticRecord,
    _estimated_prompt_tokens,
    _json_record_section,
    _stage_3_declaration_bytes,
)
from utils.prompts.prompt_builder import PromptBuilder


logger = logging.getLogger(__name__)


class Stage3SynthesisReportInvoker(Protocol):
    def __call__(
        self,
        llm: Any,
        prompt: str,
        fallback_llm: Any = None,
        allow_retry: bool = True,
    ) -> Awaitable[Dict[str, Any]]: ...


@dataclass(frozen=True)
class Stage3SynthesisRuntime:
    report_invoker: Stage3SynthesisReportInvoker


def _stable_result_union(
    results: List[Dict[str, Any]],
    field_name: str,
) -> List[Any]:
    values: List[Any] = []
    seen: set[tuple[type, Any]] = set()
    for result in results:
        for value in result.get(field_name, []) or []:
            identity = (type(value), value)
            if identity in seen:
                continue
            seen.add(identity)
            values.append(value)
    return values


def _merge_stage_3_results(
    results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Merge disjoint shard reports and validated dismissals deterministically."""
    if not results:
        raise RuntimeError("Stage 3 produced no aggregation result")
    if len(results) == 1:
        return results[0]

    reports = [str(result.get("report") or "").strip() for result in results]
    primary_report = reports[0]
    supplements = []
    for index, report in enumerate(reports[1:], start=2):
        supplements.append(
            "\n".join((
                "<details>",
                (
                    "<summary>Additional bounded semantic aggregation "
                    f"shard {index} of {len(reports)}</summary>"
                ),
                "",
                report,
                "",
                "</details>",
            ))
        )
    merged_report = primary_report
    if supplements:
        merged_report += "\n\n---\n\n" + "\n\n".join(supplements)
    return {
        "report": merged_report,
        "dismissed_issue_ids": _stable_result_union(
            results, "dismissed_issue_ids"
        ),
        "dismissed_issue_keys": _stable_result_union(
            results, "dismissed_issue_keys"
        ),
        "dismissed_issue_object_ids": _stable_result_union(
            results, "dismissed_issue_object_ids"
        ),
    }
def _render_stage_3_synthesis_shard(
    context: _Stage3PromptContext,
    records: List[_Stage3SemanticRecord],
) -> _Stage3PromptShard:
    memo_records = [
        record for record in records if record.section == "synthesis"
    ]
    authority_records = [
        record for record in records if record.section == "boundary_authority"
    ]
    source_memos = _json_record_section(
        memo_records,
        assigned_message=(
            "Complete lower-level Stage 3 analysis memos assigned to this "
            "synthesis shard (JSON):"
        ),
        empty_message="No lower-level analysis memo is assigned.",
    )
    boundary_authority = _json_record_section(
        authority_records,
        assigned_message=(
            "Complete original cross-boundary relationship/dependency records "
            "assigned to this synthesis shard (JSON). These records are "
            "authoritative when a lower-level memo omitted a fact:"
        ),
        empty_message=(
            "No original cross-boundary authority record is assigned to this "
            "synthesis shard."
        ),
    )
    synthesis_notice = (
        "HIERARCHICAL STAGE 3 SYNTHESIS: the source memos below were produced "
        "from the disjoint semantic records admitted by the review profile. "
        "Cross-relate their issue, "
        "Stage 2, plan, task, and dependency evidence. Produce one integrated "
        "report; do not treat a fact absent from one memo as absent globally."
    )
    prompt = PromptBuilder.build_stage_3_aggregation_prompt(
        repo_slug=context.repo_slug,
        pr_id=context.pr_id,
        author=context.author,
        pr_title=context.pr_title,
        total_files=context.total_files,
        additions=context.additions,
        deletions=context.deletions,
        stage_0_plan=synthesis_notice,
        stage_1_issues_json=context.issue_inventory,
        stage_2_findings_json=source_memos + "\n\n" + boundary_authority,
        recommendation=context.recommendation,
        incremental_context=context.incremental_context,
        task_context=(
            "Task and review-plan facts are represented in the source memos; "
            "synthesize their combined coverage only."
        ),
        use_mcp_tools=False,
        review_revision=context.review_revision,
    )
    return _Stage3PromptShard(
        prompt=prompt,
        record_keys=tuple(record.key for record in memo_records),
        verification_ids=(),
        use_mcp_tools=False,
        boundary_authority_records=tuple(
            record.value for record in authority_records
        ),
    )


def _build_stage_3_synthesis_shards(
    context: _Stage3PromptContext,
    records: List[_Stage3SemanticRecord],
    token_budget: int,
) -> List[_Stage3PromptShard]:
    complete = _render_stage_3_synthesis_shard(context, records)
    if _estimated_prompt_tokens(complete.prompt) <= token_budget:
        return [complete]

    packets: List[List[_Stage3SemanticRecord]] = []
    current: List[_Stage3SemanticRecord] = []
    for record in records:
        candidate = [*current, record]
        rendered = _render_stage_3_synthesis_shard(context, candidate)
        if (
            current
            and _estimated_prompt_tokens(rendered.prompt) > token_budget
        ):
            packets.append(current)
            current = [record]
        else:
            current = candidate
    if current:
        packets.append(current)
    shards = [
        _render_stage_3_synthesis_shard(context, packet)
        for packet in packets
    ]
    for shard in shards:
        estimated_tokens = _estimated_prompt_tokens(shard.prompt)
        if estimated_tokens > token_budget:
            logger.warning(
                "Indivisible Stage 3 synthesis memo exceeds the input packing "
                "target without clipping: keys=%s estimated_tokens=%d "
                "target_tokens=%d",
                list(shard.record_keys),
                estimated_tokens,
                token_budget,
            )
    return shards


def _stage_3_shard_provenance(
    shard: _Stage3PromptShard,
    *,
    phase: str,
    level: int,
    index: int,
) -> Dict[str, Any]:
    uses_tools = bool(shard.use_mcp_tools)
    declarations = _stage_3_declaration_bytes(uses_tools)
    digest = hashlib.sha256(
        shard.prompt.encode("utf-8") + b"\0" + declarations
    ).hexdigest()
    return {
        "phase": phase,
        "level": level,
        "shard": index,
        "mode": (
            "complete" if phase == "analysis" and not shard.record_keys
            else "semantic-shard"
        ),
        "recordKeys": list(shard.record_keys),
        "verificationIds": list(shard.verification_ids),
        "omittedShardCount": shard.omitted_shard_count,
        "omittedRecordCount": shard.omitted_record_count,
        "omittedVerificationCount": shard.omitted_verification_count,
        "boundaryAuthorityRecordKeys": [
            str(record.get("recordKey") or "")
            for record in shard.boundary_authority_records
        ],
        "usesMcpTools": uses_tools,
        "estimatedInputTokens": _estimated_prompt_tokens(
            shard.prompt,
            use_mcp_tools=uses_tools,
        ),
        "promptSha256": "sha256:" + digest,
    }


async def synthesize_stage_3_results(
    llm,
    *,
    context: _Stage3PromptContext,
    input_results: List[Dict[str, Any]],
    input_shards: List[_Stage3PromptShard],
    token_budget: int,
    runtime: Stage3SynthesisRuntime,
    fallback_llm=None,
) -> Dict[str, Any]:
    """Cross-relate admitted child memos in at most one synthesis call."""
    dismissal_merge = _merge_stage_3_results(input_results)
    if len(input_results) == 1:
        dismissal_merge["_synthesis_provenance"] = []
        return dismissal_merge

    boundary_authority_by_key: Dict[str, Dict[str, Any]] = {}
    for shard in input_shards:
        for record in shard.boundary_authority_records:
            record_key = str(record.get("recordKey") or "")
            if record_key:
                boundary_authority_by_key.setdefault(record_key, record)

    memo_char_cap = max(
        1_000,
        (max(4_000, token_budget * 3) // max(1, len(input_results))),
    )

    def bounded_memo(value: str, cap: int) -> str:
        if len(value) <= cap:
            return value
        marker = f"\n[CodeCrow omitted {len(value) - cap} memo characters]\n"
        usable = max(1, cap - len(marker))
        head = (usable * 2) // 3
        return value[:head] + marker + value[-(usable - head):]

    def synthesis_records(cap: int) -> List[_Stage3SemanticRecord]:
        records = [
            _Stage3SemanticRecord(
                key=f"synthesis:0:{index:06d}",
                section="synthesis",
                value={
                    "source_shard": index,
                    "source_record_keys": list(source_shard.record_keys),
                    "source_verification_ids": list(
                        source_shard.verification_ids
                    ),
                    "source_boundary_authority_record_keys": [
                        str(authority.get("recordKey") or "")
                        for authority in source_shard.boundary_authority_records
                    ],
                    "analysis_memo": bounded_memo(
                        str(result.get("report") or ""),
                        cap,
                    ),
                },
            )
            for index, (result, source_shard) in enumerate(
                zip(input_results, input_shards),
                start=1,
            )
        ]
        records.extend(
            _Stage3SemanticRecord(
                key=f"boundary-authority:{record_key}",
                section="boundary_authority",
                value=boundary_authority_by_key[record_key],
            )
            for record_key in sorted(boundary_authority_by_key)
        )
        return records

    synthesis_shard = _render_stage_3_synthesis_shard(
        context,
        synthesis_records(memo_char_cap),
    )
    while (
        _estimated_prompt_tokens(synthesis_shard.prompt) > token_budget
        and memo_char_cap > 1_000
    ):
        memo_char_cap = max(1_000, (memo_char_cap * 3) // 4)
        synthesis_shard = _render_stage_3_synthesis_shard(
            context,
            synthesis_records(memo_char_cap),
        )
    logger.info(
        "Stage 3 bounded final synthesis: child_memos=%d memo_char_cap=%d "
        "estimated_tokens=%d target_tokens=%d",
        len(input_results),
        memo_char_cap,
        _estimated_prompt_tokens(synthesis_shard.prompt),
        token_budget,
    )
    synthesis_result = await runtime.report_invoker(
        llm,
        synthesis_shard.prompt,
        fallback_llm=fallback_llm,
    )
    final_report = str(synthesis_result.get("report") or "")
    return {
        "report": final_report,
        "dismissed_issue_ids": dismissal_merge.get(
            "dismissed_issue_ids", []
        ),
        "dismissed_issue_keys": dismissal_merge.get(
            "dismissed_issue_keys", []
        ),
        "dismissed_issue_object_ids": dismissal_merge.get(
            "dismissed_issue_object_ids", []
        ),
        "_synthesis_provenance": [
            _stage_3_shard_provenance(
                synthesis_shard,
                phase="synthesis",
                level=1,
                index=1,
            )
        ],
    }
