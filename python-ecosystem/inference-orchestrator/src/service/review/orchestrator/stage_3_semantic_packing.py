"""Semantic record normalization and prompt packing for Stage 3."""

import hashlib
import json
import logging
import re
from bisect import bisect_right
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from model.multi_stage import ReviewPlan
from model.output_schemas import CodeReviewIssue
from service.review.orchestrator.mcp_tool_executor import McpToolExecutor
from service.review.orchestrator.stage_3_mcp_verification import (
    location_file_path as _location_file_path,
    safe_issue_field as _safe_issue_field,
    verification_record as _stage_3_verification_record,
)
from utils.prompts.prompt_builder import PromptBuilder


logger = logging.getLogger(__name__)

_STAGE3_ESTIMATOR_SAFETY_TOKENS = 512
_STAGE3_OMISSION_NOTICE_RESERVE_TOKENS = 256
_SEMANTIC_SHARD_NOTICE = (
    "BOUNDED SEMANTIC SHARD: this prompt contains an admitted subset of the "
    "Stage 3 records. Invocation-coverage diagnostics identify records that "
    "could not be admitted under the review profile. Analyze the records and "
    "dependency anchors assigned here; do not infer that an issue, Stage 2 "
    "finding, plan item, or task fact is absent merely because it is not "
    "assigned to this shard."
)


@dataclass(frozen=True)
class _Stage3SemanticRecord:
    key: str
    section: str
    value: Any
    verification_id: str = ""


@dataclass(frozen=True)
class _Stage3PromptContext:
    repo_slug: str
    pr_id: str
    author: str
    pr_title: str
    total_files: int
    additions: int
    deletions: int
    recommendation: str
    incremental_context: str
    use_mcp_tools: bool
    review_revision: str
    issue_inventory: str
    mcp_local_only: bool = False


@dataclass(frozen=True)
class _Stage3PromptShard:
    prompt: str
    record_keys: tuple[str, ...]
    verification_ids: tuple[str, ...]
    use_mcp_tools: bool
    boundary_authority_records: tuple[Dict[str, Any], ...] = ()
    omitted_shard_count: int = 0
    omitted_record_count: int = 0
    omitted_verification_count: int = 0



def _stage_3_tool_definitions(
    mcp_local_only: bool = False,
) -> List[Dict[str, Any]]:
    definitions = McpToolExecutor(
        None,
        SimpleNamespace(mcpLocalOnly=mcp_local_only),
        stage="stage_3",
    ).get_tool_definitions()
    return sorted(
        definitions,
        key=lambda item: str(item.get("function", {}).get("name", "")),
    )


def _stage_3_declaration_bytes(
    use_mcp_tools: bool,
    mcp_local_only: bool = False,
) -> bytes:
    if not use_mcp_tools:
        return b""
    return json.dumps(
        {"tools": _stage_3_tool_definitions(mcp_local_only)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _estimated_prompt_tokens(
    prompt: str,
    *,
    use_mcp_tools: bool = False,
    mcp_local_only: bool = False,
) -> int:
    """Estimate the exact UTF-8 prompt plus bound tool-schema declaration."""
    if use_mcp_tools:
        return _estimated_stage_3_messages_tokens(
            [{"role": "user", "content": prompt}],
            use_mcp_tools=True,
            mcp_local_only=mcp_local_only,
        )
    byte_count = len(prompt.encode("utf-8")) + len(
        _stage_3_declaration_bytes(False)
    )
    return max(
        1,
        (byte_count + 3) // 4 + _STAGE3_ESTIMATOR_SAFETY_TOKENS,
    )


def _stage_3_message_payload(message: Any) -> Dict[str, Any]:
    if isinstance(message, dict):
        return dict(message)
    message_type = str(getattr(message, "type", "assistant") or "assistant")
    role = "assistant" if message_type in {"ai", "assistant"} else message_type
    payload: Dict[str, Any] = {
        "role": role,
        "content": getattr(message, "content", ""),
    }
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        payload["tool_calls"] = tool_calls
    return payload


def _estimated_stage_3_messages_tokens(
    messages: List[Any],
    *,
    use_mcp_tools: bool,
    mcp_local_only: bool = False,
) -> int:
    """Account for every rendered message and the bound tool declarations."""
    message_bytes = json.dumps(
        {"messages": [_stage_3_message_payload(item) for item in messages]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    byte_count = len(message_bytes) + len(
        _stage_3_declaration_bytes(use_mcp_tools, mcp_local_only)
    )
    return max(
        1,
        (byte_count + 3) // 4 + _STAGE3_ESTIMATOR_SAFETY_TOKENS,
    )


def _stage_3_mcp_continuation_messages(
    prompt: str,
    verification_records: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    continuation = "\n\n".join((
        prompt,
        (
            "MCP VERIFICATION CONTINUATION: the JSON below contains every "
            "complete prior assistant/tool iteration record, in order. No "
            "tool result was clipped. Continue optional verification from "
            "these authoritative records, or produce the final report now."
        ),
        json.dumps(
            verification_records,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
    ))
    return [{"role": "user", "content": continuation}]


def _stage_3_issue_inventory(
    issue_by_verification_id: Dict[str, CodeReviewIssue],
) -> str:
    severity_counts: Dict[str, int] = {}
    category_counts: Dict[str, int] = {}
    for issue in issue_by_verification_id.values():
        severity = str(_safe_issue_field(issue, "severity") or "").upper()
        category = str(_safe_issue_field(issue, "category") or "").upper()
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1
    return "\n".join((
        f"Global active issue count: {len(issue_by_verification_id)}",
        "Global severity inventory: " + (
            ", ".join(
                f"{key}: {value}"
                for key, value in sorted(severity_counts.items())
            ) or "none"
        ),
        "Global category inventory: " + (
            ", ".join(
                f"{key}: {value}"
                for key, value in sorted(category_counts.items())
            ) or "none"
        ),
    ))


def _render_complete_stage_3_prompt(
    context: _Stage3PromptContext,
    *,
    plan_summary: str,
    stage_1_json: str,
    stage_2_json: str,
    task_context: str,
) -> str:
    return PromptBuilder.build_stage_3_aggregation_prompt(
        repo_slug=context.repo_slug,
        pr_id=context.pr_id,
        author=context.author,
        pr_title=context.pr_title,
        total_files=context.total_files,
        additions=context.additions,
        deletions=context.deletions,
        stage_0_plan=plan_summary,
        stage_1_issues_json=stage_1_json,
        stage_2_findings_json=stage_2_json,
        recommendation=context.recommendation,
        incremental_context=context.incremental_context,
        task_context=task_context,
        use_mcp_tools=context.use_mcp_tools,
        review_revision=context.review_revision,
        mcp_local_only=context.mcp_local_only,
    )


def _json_record_section(
    records: List[_Stage3SemanticRecord],
    *,
    assigned_message: str,
    empty_message: str,
) -> str:
    if not records:
        return empty_message
    payload = [
        {"record_key": record.key, "value": record.value}
        for record in records
    ]
    return assigned_message + "\n" + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _render_stage_3_semantic_shard(
    context: _Stage3PromptContext,
    records: List[_Stage3SemanticRecord],
) -> _Stage3PromptShard:
    issue_records = [record for record in records if record.section == "issue"]
    stage_2_records = [
        record for record in records if record.section == "stage_2"
    ]
    plan_records = [record for record in records if record.section == "plan"]
    task_records = [record for record in records if record.section == "task"]
    boundary_anchors = [
        record.value for record in records if record.section == "anchor"
    ]
    authority_by_key = {
        str(record.value.get("recordKey") or ""): record.value
        for record in records
        if record.section == "synthesis_authority"
        and isinstance(record.value, dict)
        and record.value.get("recordKey")
    }
    original_records = [
        record
        for record in records
        if record.section not in {"anchor", "synthesis_authority"}
    ]
    verification_ids = tuple(
        record.verification_id
        for record in issue_records
        if record.verification_id
    )
    use_mcp_tools = bool(
        context.use_mcp_tools
        and context.review_revision
        and verification_ids
    )
    stage_1_json = context.issue_inventory + "\n" + _json_record_section(
        issue_records,
        assigned_message=(
            "Complete Stage 1 issue records assigned to this semantic shard "
            "(JSON):"
        ),
        empty_message=(
            "No Stage 1 issue record is assigned to this shard; consult the "
            "global inventory and other shards."
        ),
    )
    stage_2_json = _json_record_section(
        stage_2_records,
        assigned_message="Complete Stage 2 records assigned to this shard (JSON):",
        empty_message=(
            "No Stage 2 record is assigned to this shard; this is not evidence "
            "that Stage 2 produced no findings."
        ),
    )
    dependency_anchor = {
        "assignedRecordKeys": [record.key for record in original_records],
        "verificationIds": list(verification_ids),
        "relatedPaths": sorted({
            path
            for record in original_records
            for path in _stage_3_record_paths(record)
        }),
        "sectionRecordCounts": {
            section: sum(1 for record in records if record.section == section)
            for section in ("issue", "stage_2", "plan", "task")
        },
        "connectedComponentBoundaries": boundary_anchors,
        "completeBoundaryAuthorityRecordCount": len(authority_by_key),
        "completeBoundaryAuthoritySha256": (
            "sha256:" + hashlib.sha256(
                "\0".join(sorted(authority_by_key)).encode("utf-8")
            ).hexdigest()
        ),
        "upperSynthesisCarriesCompleteBoundaryRecords": bool(authority_by_key),
        "upperSynthesisRequired": True,
    }
    plan_summary = (
        _SEMANTIC_SHARD_NOTICE
        + "\n\nShard dependency/provenance anchor (JSON):\n"
        + json.dumps(
            dependency_anchor,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n\n"
        + _json_record_section(
        plan_records,
        assigned_message="Complete review-plan records assigned to this shard (JSON):",
        empty_message="No review-plan record is assigned to this shard.",
        )
    )
    task_context = _json_record_section(
        task_records,
        assigned_message="Complete task-context records assigned to this shard (JSON):",
        empty_message=(
            "No task-context record is assigned to this shard; do not infer "
            "that task context is unavailable."
        ),
    )
    prompt = PromptBuilder.build_stage_3_aggregation_prompt(
        repo_slug=context.repo_slug,
        pr_id=context.pr_id,
        author=context.author,
        pr_title=context.pr_title,
        total_files=context.total_files,
        additions=context.additions,
        deletions=context.deletions,
        stage_0_plan=plan_summary,
        stage_1_issues_json=stage_1_json,
        stage_2_findings_json=stage_2_json,
        recommendation=context.recommendation,
        incremental_context=context.incremental_context,
        task_context=task_context,
        use_mcp_tools=use_mcp_tools,
        review_revision=context.review_revision,
        mcp_local_only=context.mcp_local_only,
    )
    return _Stage3PromptShard(
        prompt=prompt,
        record_keys=tuple(record.key for record in original_records),
        verification_ids=verification_ids,
        use_mcp_tools=use_mcp_tools,
        boundary_authority_records=tuple(
            authority_by_key[key] for key in sorted(authority_by_key)
        ),
    )


def _stage_2_semantic_records(stage_2_json: str) -> List[_Stage3SemanticRecord]:
    serialized = (
        stage_2_json
        if isinstance(stage_2_json, str)
        else str(stage_2_json)
    )
    try:
        payload = json.loads(serialized)
    except (TypeError, json.JSONDecodeError):
        return [_Stage3SemanticRecord(
            key="stage_2:raw",
            section="stage_2",
            value=serialized,
        )]
    if not isinstance(payload, dict):
        return [_Stage3SemanticRecord(
            key="stage_2:root",
            section="stage_2",
            value=payload,
        )]

    if not payload:
        return [_Stage3SemanticRecord(
            key="stage_2:root",
            section="stage_2",
            value={},
        )]

    records: List[_Stage3SemanticRecord] = []
    for field_name in sorted(payload):
        value = payload[field_name]
        if isinstance(value, list) and value:
            records.extend(
                _Stage3SemanticRecord(
                    key=f"stage_2:{field_name}:{index:06d}",
                    section="stage_2",
                    value={
                        "field": field_name,
                        "index": index,
                        "value": item,
                    },
                )
                for index, item in enumerate(value)
            )
        else:
            records.append(_Stage3SemanticRecord(
                key=f"stage_2:{field_name}",
                section="stage_2",
                value={"field": field_name, "value": value},
            ))
    return records


def _stage_3_object_value(value: Any, default: Any) -> Any:
    if value is None or value.__class__.__module__.startswith("unittest.mock"):
        return default
    return value


def _complete_review_plan_payload(plan: ReviewPlan) -> Dict[str, Any]:
    if isinstance(plan, ReviewPlan):
        return plan.model_dump(mode="json")
    groups = list(_stage_3_object_value(
        getattr(plan, "file_groups", None),
        [],
    ) or [])
    return {
        "analysis_summary": str(_stage_3_object_value(
            getattr(plan, "analysis_summary", ""),
            "",
        ) or ""),
        "file_groups": [
            {
                "group_id": str(_stage_3_object_value(
                    getattr(group, "group_id", ""),
                    "",
                ) or ""),
                "priority": str(_stage_3_object_value(
                    getattr(group, "priority", ""),
                    "",
                ) or ""),
                "rationale": str(_stage_3_object_value(
                    getattr(group, "rationale", ""),
                    "",
                ) or ""),
                "files": [
                    {
                        "path": str(_stage_3_object_value(
                            getattr(review_file, "path", ""),
                            "",
                        ) or ""),
                        "focus_areas": list(_stage_3_object_value(
                            getattr(review_file, "focus_areas", None),
                            [],
                        ) or []),
                        "risk_level": str(_stage_3_object_value(
                            getattr(review_file, "risk_level", "MEDIUM"),
                            "MEDIUM",
                        ) or "MEDIUM"),
                    }
                    for review_file in list(_stage_3_object_value(
                        getattr(group, "files", None),
                        [],
                    ) or [])
                ],
            }
            for group in groups
        ],
        "files_to_skip": [
            {
                "path": str(_stage_3_object_value(
                    getattr(skipped, "path", ""),
                    "",
                ) or ""),
                "reason": str(_stage_3_object_value(
                    getattr(skipped, "reason", ""),
                    "",
                ) or ""),
            }
            for skipped in list(_stage_3_object_value(
                getattr(plan, "files_to_skip", None),
                [],
            ) or [])
        ],
        "cross_file_concerns": [
            str(concern)
            for concern in list(_stage_3_object_value(
                getattr(plan, "cross_file_concerns", None),
                [],
            ) or [])
        ],
    }


def _plan_semantic_records(plan: ReviewPlan) -> List[_Stage3SemanticRecord]:
    payload = _complete_review_plan_payload(plan)
    records = [_Stage3SemanticRecord(
        key="plan:analysis_summary",
        section="plan",
        value={"field": "analysis_summary", "value": payload["analysis_summary"]},
    )]
    for group_index, group in enumerate(payload["file_groups"]):
        records.append(_Stage3SemanticRecord(
            key=f"plan:group:{group_index:06d}",
            section="plan",
            value={
                "group_index": group_index,
                "group_id": group["group_id"],
                "priority": group["priority"],
                "rationale": group["rationale"],
            },
        ))
        records.extend(
            _Stage3SemanticRecord(
                key=f"plan:group:{group_index:06d}:file:{file_index:06d}",
                section="plan",
                value={
                    "group_index": group_index,
                    "file_index": file_index,
                    **review_file,
                },
            )
            for file_index, review_file in enumerate(group["files"])
        )
    records.extend(
        _Stage3SemanticRecord(
            key=f"plan:concern:{index:06d}",
            section="plan",
            value={"index": index, "cross_file_concern": str(concern)},
        )
        for index, concern in enumerate(payload["cross_file_concerns"])
    )
    records.extend(
        _Stage3SemanticRecord(
            key=f"plan:skip:{index:06d}",
            section="plan",
            value={"index": index, **skipped},
        )
        for index, skipped in enumerate(payload["files_to_skip"])
    )
    return records


def _task_semantic_records(task_context: str) -> List[_Stage3SemanticRecord]:
    # Each formatted line is moved whole. Newline characters remain attached,
    # so the complete formatted context can be reconstructed byte-for-byte.
    lines = task_context.splitlines(keepends=True)
    if not lines and task_context:
        lines = [task_context]
    return [
        _Stage3SemanticRecord(
            key=f"task:line:{index:06d}",
            section="task",
            value={"index": index, "text": line},
        )
        for index, line in enumerate(lines)
    ]


_FREE_TEXT_BOUNDARY_RE = re.compile(r"(?:\r\n|\r|\n)+|[^\S\r\n]+")
_FREE_TEXT_SEQUENCE_PLACEHOLDER = 999_999_999


def _stage_3_splittable_text(
    record: _Stage3SemanticRecord,
) -> Optional[str]:
    if record.section == "task" and isinstance(record.value, dict):
        text = record.value.get("text")
        return text if isinstance(text, str) else None
    if (
        record.section == "stage_2"
        and record.key in {"stage_2:raw", "stage_2:root"}
        and isinstance(record.value, str)
    ):
        return record.value
    if record.section == "stage_2" and isinstance(record.value, dict):
        raw_text = record.value.get("rawText")
        return raw_text if isinstance(raw_text, str) else None
    return None


def _stage_3_text_segment_record(
    record: _Stage3SemanticRecord,
    *,
    text: str,
    start: int,
    end: int,
    segment_index: int,
    segment_count: int,
) -> _Stage3SemanticRecord:
    sequence = {
        "sourceRecordKey": record.key,
        "segmentIndex": segment_index,
        "segmentCount": segment_count,
        "characterStart": start,
        "characterEnd": end,
        "sourceCharacterCount": len(text),
    }
    if record.section == "task":
        value = dict(record.value)
        value["text"] = text[start:end]
        value["textSequence"] = sequence
    else:
        value = {
            "rawText": text[start:end],
            "textSequence": sequence,
        }
    return _Stage3SemanticRecord(
        key=f"{record.key}:segment:{segment_index:06d}",
        section=record.section,
        value=value,
        verification_id=record.verification_id,
    )


def _stage_3_free_text_probe_anchor(
    record: _Stage3SemanticRecord,
) -> _Stage3SemanticRecord:
    """Reserve the largest compact boundary metadata a text shard can add."""
    value = {
        "componentSha256": "sha256:" + ("f" * 64),
        "componentRecordCount": _FREE_TEXT_SEQUENCE_PLACEHOLDER,
        "assignedRecordCount": _FREE_TEXT_SEQUENCE_PLACEHOLDER,
        "omittedRecordCount": _FREE_TEXT_SEQUENCE_PLACEHOLDER,
        "boundaryPaths": sorted(_stage_3_record_paths(record)),
        "omittedSectionCounts": {
            section: _FREE_TEXT_SEQUENCE_PLACEHOLDER
            for section in ("issue", "stage_2", "plan", "task")
        },
        "omittedVerificationCount": _FREE_TEXT_SEQUENCE_PLACEHOLDER,
        "upperSynthesisCarriesOmittedRecords": True,
    }
    return _Stage3SemanticRecord(
        key="component-anchor:free-text-probe",
        section="anchor",
        value=value,
    )


def _split_stage_3_free_text_record(
    context: _Stage3PromptContext,
    record: _Stage3SemanticRecord,
    token_budget: int,
) -> List[_Stage3SemanticRecord]:
    """Split optional free text losslessly; structured records stay atomic."""
    text = _stage_3_splittable_text(record)
    if text is None or not text:
        return [record]

    probe_anchor = _stage_3_free_text_probe_anchor(record)
    complete = _render_stage_3_semantic_shard(context, [record, probe_anchor])
    if _estimated_prompt_tokens(
        complete.prompt,
        use_mcp_tools=complete.use_mcp_tools,
        mcp_local_only=context.mcp_local_only,
    ) <= token_budget:
        return [record]

    preferred_boundaries = sorted({
        match.end() for match in _FREE_TEXT_BOUNDARY_RE.finditer(text)
    })
    spans: List[tuple[int, int]] = []
    start = 0
    while start < len(text):
        low = start + 1
        high = len(text)
        maximum_end = start
        while low <= high:
            middle = (low + high) // 2
            candidate = _stage_3_text_segment_record(
                record,
                text=text,
                start=start,
                end=middle,
                segment_index=_FREE_TEXT_SEQUENCE_PLACEHOLDER,
                segment_count=_FREE_TEXT_SEQUENCE_PLACEHOLDER,
            )
            rendered = _render_stage_3_semantic_shard(
                context,
                [candidate, probe_anchor],
            )
            if _estimated_prompt_tokens(
                rendered.prompt,
                use_mcp_tools=rendered.use_mcp_tools,
                mcp_local_only=context.mcp_local_only,
            ) <= token_budget:
                maximum_end = middle
                low = middle + 1
            else:
                high = middle - 1

        if maximum_end == start:
            maximum_end = start + 1
        boundary_index = bisect_right(preferred_boundaries, maximum_end) - 1
        end = (
            preferred_boundaries[boundary_index]
            if boundary_index >= 0
            and preferred_boundaries[boundary_index] > start
            else maximum_end
        )
        spans.append((start, end))
        start = end

    segment_count = len(spans)
    return [
        _stage_3_text_segment_record(
            record,
            text=text,
            start=start,
            end=end,
            segment_index=index,
            segment_count=segment_count,
        )
        for index, (start, end) in enumerate(spans)
    ]


def _split_oversized_stage_3_free_text_records(
    context: _Stage3PromptContext,
    records: List[_Stage3SemanticRecord],
    token_budget: int,
) -> List[_Stage3SemanticRecord]:
    return [
        segment
        for record in records
        for segment in _split_stage_3_free_text_record(
            context,
            record,
            token_budget,
        )
    ]


_PATH_VALUE_KEYS = {
    "file",
    "filepath",
    "path",
    "primary_file",
    "affected_files",
    "related_locations",
}
_PATH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
)
_SEMANTIC_TERM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]{2,}")


def _stage_3_record_paths(record: _Stage3SemanticRecord) -> set[str]:
    paths: set[str] = set()

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                visit(nested_value, str(nested_key).casefold())
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                visit(item, key)
            return
        if not isinstance(value, str):
            return
        candidates = []
        if key in _PATH_VALUE_KEYS:
            candidates.extend(value.split(","))
        candidates.extend(_PATH_TOKEN_RE.findall(value))
        for candidate in candidates:
            path = _location_file_path(candidate)
            if path:
                paths.add(path.casefold())

    visit(record.value)
    return paths


def _stage_3_record_terms(record: _Stage3SemanticRecord) -> set[str]:
    terms: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                visit(nested)
            return
        if isinstance(value, (list, tuple, set)):
            for nested in value:
                visit(nested)
            return
        if not isinstance(value, str):
            return
        terms.update(
            token.casefold()
            for token in _SEMANTIC_TERM_RE.findall(value)
        )

    visit(record.value)
    return terms


def _dependency_aware_stage_3_units(
    records: List[_Stage3SemanticRecord],
) -> List[List[_Stage3SemanticRecord]]:
    """Co-locate issue records with path-related Stage 2 and plan context."""
    issues = [record for record in records if record.section == "issue"]
    stage_2 = [record for record in records if record.section == "stage_2"]
    plan = [record for record in records if record.section == "plan"]
    task = [record for record in records if record.section == "task"]

    if issues:
        units: List[List[_Stage3SemanticRecord]] = [[record] for record in issues]
    else:
        path_stage_2 = [record for record in stage_2 if _stage_3_record_paths(record)]
        units = [[record] for record in path_stage_2]
        stage_2 = [record for record in stage_2 if record not in path_stage_2]
    if not units:
        units = [[]]

    def unit_paths(unit: List[_Stage3SemanticRecord]) -> set[str]:
        return {
            path
            for item in unit
            for path in _stage_3_record_paths(item)
        }

    def unit_terms(unit: List[_Stage3SemanticRecord]) -> set[str]:
        anchors = [item for item in unit if item.section == "issue"]
        if not anchors:
            anchors = unit[:1]
        return {
            term
            for item in anchors
            for term in _stage_3_record_terms(item)
        }

    def unit_weight(unit: List[_Stage3SemanticRecord]) -> int:
        return sum(
            len(json.dumps(item.value, ensure_ascii=False, default=str))
            for item in unit
        )

    assigned_keys = {item.key for unit in units for item in unit}
    contextual_records = [
        record
        for record in (*stage_2, *plan)
        if record.key not in assigned_keys
    ]
    unmatched: List[_Stage3SemanticRecord] = []
    for record in contextual_records:
        paths = _stage_3_record_paths(record)
        matching = [
            index
            for index, unit in enumerate(units)
            if paths and paths.intersection(unit_paths(unit))
        ]
        if matching:
            target = matching[0]
            units[target].append(record)
            # A relationship joining multiple issue units is a real connected
            # component. Keep it intact instead of creating issue/context
            # shards that need to rediscover the edge independently.
            for source in reversed(matching[1:]):
                units[target].extend(units.pop(source))
                if source < target:
                    target -= 1
        else:
            unmatched.append(record)

    # Non-path context is assigned once to the most semantically related unit;
    # deterministic least-weight distribution handles global inventory fields.
    for record in (*unmatched, *task):
        terms = _stage_3_record_terms(record)
        overlaps = [len(terms.intersection(unit_terms(unit))) for unit in units]
        best_overlap = max(overlaps, default=0)
        candidates = [
            index for index, overlap in enumerate(overlaps)
            if overlap == best_overlap
        ]
        target = min(candidates, key=lambda index: (unit_weight(units[index]), index))
        units[target].append(record)

    return [unit for unit in units if unit]


def _stage_3_component_boundary_anchor(
    component: List[_Stage3SemanticRecord],
    assigned: List[_Stage3SemanticRecord],
) -> _Stage3SemanticRecord:
    component_keys = sorted(record.key for record in component)
    assigned_keys = {record.key for record in assigned}
    omitted = [record for record in component if record.key not in assigned_keys]
    assigned_paths = {
        path for record in assigned for path in _stage_3_record_paths(record)
    }
    omitted_paths = {
        path for record in omitted for path in _stage_3_record_paths(record)
    }
    component_digest = hashlib.sha256(
        "\0".join(component_keys).encode("utf-8")
    ).hexdigest()
    value = {
        "componentSha256": "sha256:" + component_digest,
        "componentRecordCount": len(component),
        "assignedRecordCount": len(assigned),
        "omittedRecordCount": len(omitted),
        "boundaryPaths": sorted(assigned_paths.intersection(omitted_paths)),
        "omittedSectionCounts": {
            section: sum(1 for record in omitted if record.section == section)
            for section in ("issue", "stage_2", "plan", "task")
        },
        "omittedVerificationCount": sum(
            1 for record in omitted if record.verification_id
        ),
        "upperSynthesisCarriesOmittedRecords": True,
    }
    return _Stage3SemanticRecord(
        key="component-anchor:" + component_digest,
        section="anchor",
        value=value,
    )


def _stage_3_component_authority_records(
    component: List[_Stage3SemanticRecord],
    groups: List[List[_Stage3SemanticRecord]],
) -> List[_Stage3SemanticRecord]:
    """Carry each original cross-group relationship record to synthesis once."""
    group_paths = [
        {
            path
            for record in group
            if record.section != "anchor"
            for path in _stage_3_record_paths(record)
        }
        for group in groups
    ]
    boundary_paths = {
        path
        for paths in group_paths
        for path in paths
        if sum(path in other_paths for other_paths in group_paths) > 1
    }
    authority = []
    for record in component:
        record_boundary_paths = boundary_paths.intersection(
            _stage_3_record_paths(record)
        )
        if (
            record.section not in {"issue", "stage_2", "plan"}
            and not record_boundary_paths
        ):
            continue
        authority.append(_Stage3SemanticRecord(
            key=f"synthesis-authority:{record.key}",
            section="synthesis_authority",
            value={
                "recordKey": record.key,
                "section": record.section,
                "value": record.value,
                "boundaryPaths": sorted(record_boundary_paths),
            },
        ))
    return authority


def _expand_oversized_stage_3_unit(
    context: _Stage3PromptContext,
    unit: List[_Stage3SemanticRecord],
    token_budget: int,
) -> List[List[_Stage3SemanticRecord]]:
    rendered = _render_stage_3_semantic_shard(context, unit)
    if _estimated_prompt_tokens(
        rendered.prompt,
        use_mcp_tools=rendered.use_mcp_tools,
        mcp_local_only=context.mcp_local_only,
    ) <= token_budget:
        return [unit]

    groups: List[List[_Stage3SemanticRecord]] = []
    current: List[_Stage3SemanticRecord] = []
    for record in unit:
        candidate = [*current, record]
        candidate_with_anchor = [
            *candidate,
            _stage_3_component_boundary_anchor(unit, candidate),
        ]
        candidate_shard = _render_stage_3_semantic_shard(
            context,
            candidate_with_anchor,
        )
        if (
            current
            and _estimated_prompt_tokens(
                candidate_shard.prompt,
                use_mcp_tools=candidate_shard.use_mcp_tools,
                mcp_local_only=context.mcp_local_only,
            ) > token_budget
        ):
            groups.append([
                *current,
                _stage_3_component_boundary_anchor(unit, current),
            ])
            current = [record]
        else:
            current = candidate
    if current:
        groups.append([
            *current,
            _stage_3_component_boundary_anchor(unit, current),
        ])
    if len(groups) > 1:
        groups[0].extend(_stage_3_component_authority_records(unit, groups))
    return groups


def _build_stage_3_prompt_shards(
    *,
    context: _Stage3PromptContext,
    complete_plan_summary: str,
    complete_stage_1_json: str,
    complete_stage_2_json: str,
    complete_task_context: str,
    plan: ReviewPlan,
    issue_by_verification_id: Dict[str, CodeReviewIssue],
    token_budget: int,
    max_children: int = 4,
) -> List[_Stage3PromptShard]:
    """Return one full prompt or a bounded set of semantic child shards."""
    complete_prompt = _render_complete_stage_3_prompt(
        context,
        plan_summary=complete_plan_summary,
        stage_1_json=complete_stage_1_json,
        stage_2_json=complete_stage_2_json,
        task_context=complete_task_context,
    )
    complete_uses_mcp = bool(
        context.use_mcp_tools and context.review_revision
    )
    if _estimated_prompt_tokens(
        complete_prompt,
        use_mcp_tools=complete_uses_mcp,
        mcp_local_only=context.mcp_local_only,
    ) <= token_budget:
        return [_Stage3PromptShard(
            prompt=complete_prompt,
            record_keys=(),
            verification_ids=tuple(issue_by_verification_id),
            use_mcp_tools=complete_uses_mcp,
        )]

    # Reserve the visible invocation-coverage diagnostic before packing. The
    # diagnostic must not be appended to an already-full provider request.
    packing_token_budget = max(
        1,
        token_budget - _STAGE3_OMISSION_NOTICE_RESERVE_TOKENS,
    )

    records = _split_oversized_stage_3_free_text_records(context, [
        *(
            _Stage3SemanticRecord(
                key=f"stage_1:{verification_id}",
                section="issue",
                value=_stage_3_verification_record(verification_id, issue),
                verification_id=verification_id,
            )
            for verification_id, issue in issue_by_verification_id.items()
        ),
        *_stage_2_semantic_records(complete_stage_2_json),
        *_plan_semantic_records(plan),
        *_task_semantic_records(complete_task_context),
    ], packing_token_budget)

    units = [
        expanded
        for unit in _dependency_aware_stage_3_units(records)
        for expanded in _expand_oversized_stage_3_unit(
            context,
            unit,
            packing_token_budget,
        )
    ]
    packets: List[List[_Stage3SemanticRecord]] = []
    current: List[_Stage3SemanticRecord] = []
    for unit in units:
        candidate = [*current, *unit]
        candidate_shard = _render_stage_3_semantic_shard(context, candidate)
        if (
            current
            and _estimated_prompt_tokens(
                candidate_shard.prompt,
                use_mcp_tools=bool(
                    candidate_shard.use_mcp_tools and context.review_revision
                ),
                mcp_local_only=context.mcp_local_only,
            ) > packing_token_budget
        ):
            packets.append(current)
            current = list(unit)
        else:
            current = candidate
    if current:
        packets.append(current)
    if not packets:
        packets = [[]]

    shards = [
        _render_stage_3_semantic_shard(context, packet)
        for packet in packets
    ]
    expected_keys = [record.key for record in records]
    observed_keys = [
        record_key
        for shard in shards
        for record_key in shard.record_keys
    ]
    if (
        set(observed_keys) != set(expected_keys)
        or len(observed_keys) != len(expected_keys)
        or len(set(observed_keys)) != len(observed_keys)
    ):
        raise RuntimeError("Stage 3 semantic packing lost or repeated input records")

    record_by_key = {record.key: record for record in records}
    for shard in shards:
        estimated_tokens = _estimated_prompt_tokens(
            shard.prompt,
            use_mcp_tools=bool(
                shard.use_mcp_tools and context.review_revision
            ),
            mcp_local_only=context.mcp_local_only,
        )
        if estimated_tokens > packing_token_budget:
            splittable_keys = [
                record_key
                for record_key in shard.record_keys
                if record_key in record_by_key
                and _stage_3_splittable_text(record_by_key[record_key]) is not None
            ]
            if splittable_keys:
                raise RuntimeError(
                    "Stage 3 free-text semantic packing exceeded the exact "
                    f"rendered-prompt target: keys={splittable_keys}"
                )
            logger.warning(
                "Indivisible Stage 3 semantic record exceeds the input packing "
                "target without clipping: keys=%s estimated_tokens=%d "
                "target_tokens=%d",
                list(shard.record_keys),
                estimated_tokens,
                packing_token_budget,
            )
    max_children = max(1, max_children)
    omitted_shards = shards[max_children:]
    shards = shards[:max_children]
    omitted_record_keys = {
        key for shard in omitted_shards for key in shard.record_keys
    }
    omitted_verification_ids = {
        verification_id
        for shard in omitted_shards
        for verification_id in shard.verification_ids
    }
    if omitted_shards and shards:
        notice = (
            "\n\n[CodeCrow Stage 3 child invocation ceiling reached: "
            f"omitted_shards={len(omitted_shards)}, "
            f"omitted_records={len(omitted_record_keys)}, "
            f"omitted_verification_records={len(omitted_verification_ids)}. "
            "Issue/diff and Stage 2 architecture components were ordered before "
            "lower-priority plan/task context. Absence is not negative evidence.]"
        )
        last = shards[-1]
        bounded_last = _Stage3PromptShard(
            prompt=last.prompt + notice,
            record_keys=last.record_keys,
            verification_ids=last.verification_ids,
            use_mcp_tools=last.use_mcp_tools,
            boundary_authority_records=last.boundary_authority_records,
            omitted_shard_count=len(omitted_shards),
            omitted_record_count=len(omitted_record_keys),
            omitted_verification_count=len(omitted_verification_ids),
        )
        bounded_tokens = _estimated_prompt_tokens(
            bounded_last.prompt,
            use_mcp_tools=bool(
                bounded_last.use_mcp_tools and context.review_revision
            ),
            mcp_local_only=context.mcp_local_only,
        )
        if bounded_tokens > token_budget:
            raise RuntimeError(
                "Stage 3 invocation-coverage notice exceeded its reserved "
                f"prompt budget: estimated_tokens={bounded_tokens} "
                f"target_tokens={token_budget}"
            )
        shards[-1] = bounded_last
        logger.warning(
            "Stage 3 child invocation ceiling reached: admitted=%d "
            "omitted_shards=%d omitted_records=%d omitted_verification=%d",
            len(shards),
            len(omitted_shards),
            len(omitted_record_keys),
            len(omitted_verification_ids),
        )
    logger.info(
        "Stage 3 semantic packing: complete_estimated_tokens=%d shards=%d "
        "records=%d target_tokens=%d",
        _estimated_prompt_tokens(
            complete_prompt,
            use_mcp_tools=complete_uses_mcp,
            mcp_local_only=context.mcp_local_only,
        ),
        len(shards),
        len(records),
        token_budget,
    )
    return shards
