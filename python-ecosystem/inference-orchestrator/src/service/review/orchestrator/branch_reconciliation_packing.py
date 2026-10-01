"""Bounded, request-aware input packing for branch reconciliation."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

from model.dtos import ReviewRequestDto
from model.output_schemas import ReconciliationOutput
from service.review.orchestrator.branch_analysis import (
    execute_branch_reconciliation_direct,
)
from utils.path_identity import normalize_repository_path, repository_paths_match

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


BRANCH_RECONCILIATION_INPUT_TOKEN_TARGET = max(
    10_000,
    _env_int("REVIEW_STAGE1_BATCH_TOKEN_BUDGET", 60_000),
)
_BRANCH_CONTEXT_RESERVE_TOKENS = 20_000
_BRANCH_ESTIMATOR_SAFETY_TOKENS = 256
_SEQUENCE_PLACEHOLDER = 999_999_999
_TEXT_BOUNDARY_RE = re.compile(r"(?:\r\n|\r|\n)+|[^\S\r\n]+")
_DIFF_FILE_RE = re.compile(
    r"^diff --git a/(.+?) b/(.+?)(?:\r?\n|$)",
    re.MULTILINE,
)
_DIFF_HUNK_RE = re.compile(r"^@@[^\r\n]*(?:\r?\n|$)", re.MULTILINE)
BRANCH_RECONCILIATION_MAX_SHARDS = 4


def _schema_declaration_bytes() -> bytes:
    return json.dumps(
        ReconciliationOutput.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


_RECONCILIATION_SCHEMA_BYTES = _schema_declaration_bytes()


def _positive_int_or_default(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def branch_reconciliation_input_token_target(
    request: ReviewRequestDto,
) -> int:
    model_context = _positive_int_or_default(
        getattr(request, "maxAllowedTokens", None),
        200_000,
    )
    if model_context > _BRANCH_CONTEXT_RESERVE_TOKENS:
        model_safe = model_context - _BRANCH_CONTEXT_RESERVE_TOKENS
    else:
        model_safe = max(1, model_context // 2)
    return min(BRANCH_RECONCILIATION_INPUT_TOKEN_TARGET, model_safe)


def estimated_branch_reconciliation_tokens(prompt: str) -> int:
    request_bytes = len(prompt.encode("utf-8")) + len(
        _RECONCILIATION_SCHEMA_BYTES
    )
    return max(
        1,
        (request_bytes + 2) // 3 + _BRANCH_ESTIMATOR_SAFETY_TOKENS,
    )


@dataclass(frozen=True)
class ReconciliationRecord:
    key: str
    kind: str
    file_path: str
    value: Dict[str, Any]
    issue_id: str = ""


@dataclass(frozen=True)
class ReconciliationShard:
    shard_id: str
    prompt: str
    record_keys: tuple[str, ...]
    anchor_record_keys: tuple[str, ...]
    issue_ids: tuple[str, ...]
    file_paths: tuple[str, ...]
    partial_file_paths: tuple[str, ...]
    estimated_input_tokens: int


def _normalized_path(value: Any) -> str:
    return normalize_repository_path(str(value or ""))


def _issue_file(issue: Dict[str, Any]) -> str:
    return _normalized_path(issue.get("file") or issue.get("filePath"))


def _issue_id(issue: Dict[str, Any]) -> str:
    return str(issue.get("id") or issue.get("issueId") or "").strip()


def _issue_records(issues: List[Dict[str, Any]]) -> List[ReconciliationRecord]:
    return [
        ReconciliationRecord(
            key=f"issue:{index:06d}",
            kind="issue",
            file_path=_issue_file(issue),
            value=dict(issue),
            issue_id=_issue_id(issue),
        )
        for index, issue in enumerate(issues)
    ]


def _complete_source_records(
    file_contents: Dict[str, str],
) -> List[ReconciliationRecord]:
    return [
        ReconciliationRecord(
            key=f"source:{index:06d}:complete",
            kind="source",
            file_path=_normalized_path(file_path),
            value={
                "file": str(file_path),
                "text": str(content),
                "sequence": {
                    "fragmentIndex": 0,
                    "fragmentCount": 1,
                    "characterStart": 0,
                    "characterEnd": len(str(content)),
                    "sourceCharacterCount": len(str(content)),
                    "completeFile": True,
                },
            },
        )
        for index, (file_path, content) in enumerate(file_contents.items())
    ]


def _diff_records(raw_diff: Optional[str]) -> List[ReconciliationRecord]:
    if not raw_diff:
        return []
    text = str(raw_diff)
    file_matches = list(_DIFF_FILE_RE.finditer(text))
    raw_records: List[tuple[str, str, str]] = []
    if not file_matches:
        raw_records.append(("diff:preamble", "", text))
    else:
        if file_matches[0].start() > 0:
            raw_records.append((
                "diff:preamble",
                "",
                text[:file_matches[0].start()],
            ))
        for file_index, match in enumerate(file_matches):
            section_end = (
                file_matches[file_index + 1].start()
                if file_index + 1 < len(file_matches)
                else len(text)
            )
            section = text[match.start():section_end]
            file_path = _normalized_path(match.group(2) or match.group(1))
            hunk_matches = list(_DIFF_HUNK_RE.finditer(section))
            if not hunk_matches:
                raw_records.append((
                    f"diff:file:{file_index:06d}",
                    file_path,
                    section,
                ))
                continue
            if hunk_matches[0].start() > 0:
                raw_records.append((
                    f"diff:file:{file_index:06d}:header",
                    file_path,
                    section[:hunk_matches[0].start()],
                ))
            for hunk_index, hunk_match in enumerate(hunk_matches):
                hunk_end = (
                    hunk_matches[hunk_index + 1].start()
                    if hunk_index + 1 < len(hunk_matches)
                    else len(section)
                )
                raw_records.append((
                    f"diff:file:{file_index:06d}:hunk:{hunk_index:06d}",
                    file_path,
                    section[hunk_match.start():hunk_end],
                ))
    record_count = len(raw_records)
    return [
        ReconciliationRecord(
            key=key,
            kind="diff",
            file_path=file_path,
            value={
                "file": file_path,
                "text": value,
                "sequence": {
                    "recordIndex": index,
                    "recordCount": record_count,
                },
            },
        )
        for index, (key, file_path, value) in enumerate(raw_records)
    ]


def _record_payload(record: ReconciliationRecord) -> Dict[str, Any]:
    return {"recordKey": record.key, "value": record.value}


def _render_reconciliation_prompt(
    *,
    branch: str,
    commit_hash: str,
    records: List[ReconciliationRecord],
    issue_anchors: Iterable[ReconciliationRecord] = (),
    partial_file_paths: Iterable[str] = (),
) -> str:
    issue_payload = [
        _record_payload(record)
        for record in records
        if record.kind == "issue"
    ]
    source_payload = [
        _record_payload(record)
        for record in records
        if record.kind == "source"
    ]
    diff_payload = [
        _record_payload(record)
        for record in records
        if record.kind == "diff"
    ]
    partial_paths = sorted(set(partial_file_paths))
    anchor_payload = [
        _record_payload(record)
        for record in issue_anchors
    ]
    evidence_mode = "PARTIAL" if partial_paths else "COMPLETE"
    return f"""You are performing branch issue reconciliation.
Branch: {branch}
Commit Hash: {commit_hash}
Evidence mode: {evidence_mode}

Return only existing issues that current positive evidence proves are resolved.
Never discover new issues. Never resolve an issue merely because code, a symbol,
a file, or a diff is absent from this prompt. In PARTIAL mode, every omitted
source fragment or diff hunk is owned by another shard; local absence is never
proof of deletion or resolution. A host-side atomic merge accepts a resolution
only when all required evidence shards agree. If evidence is incomplete or
ambiguous, omit the issue so it remains unresolved.

COMPLETE ISSUE RECORDS ASSIGNED TO THIS SHARD (JSON):
{json.dumps(issue_payload, ensure_ascii=False, separators=(",", ":"), default=str)}

COMPLETE DEPENDENCY-SCOPED ISSUE AUTHORITY ANCHORS (JSON):
{json.dumps(anchor_payload, ensure_ascii=False, separators=(",", ":"), default=str)}

Authority anchors are complete issue records repeated only into other shards of
the same file/dependency component. They are context, not semantic ownership.

LOSSLESS CURRENT-SOURCE RECORDS ASSIGNED TO THIS SHARD (JSON):
{json.dumps(source_payload, ensure_ascii=False, separators=(",", ":"), default=str)}

LOSSLESS DIFF HEADER/HUNK RECORDS ASSIGNED TO THIS SHARD (JSON):
{json.dumps(diff_payload, ensure_ascii=False, separators=(",", ":"), default=str)}

PARTIAL FILE PATHS (JSON):
{json.dumps(partial_paths, ensure_ascii=False, separators=(",", ":"))}

Return ONLY a valid JSON object with this shape:
{{"comment":"summary","issues":[{{"issueId":"original id","isResolved":true,"resolutionReason":"specific positive fix evidence"}}]}}
Unresolved issues must be omitted.
"""


def _source_segment_record(
    record: ReconciliationRecord,
    *,
    text: str,
    start: int,
    end: int,
    fragment_index: int,
    fragment_count: int,
) -> ReconciliationRecord:
    return ReconciliationRecord(
        key=f"{record.key}:fragment:{fragment_index:06d}",
        kind="source",
        file_path=record.file_path,
        value={
            "file": record.value["file"],
            "text": text[start:end],
            "sequence": {
                "sourceRecordKey": record.key,
                "fragmentIndex": fragment_index,
                "fragmentCount": fragment_count,
                "characterStart": start,
                "characterEnd": end,
                "sourceCharacterCount": len(text),
                "completeFile": False,
            },
        },
    )


def _split_source_record(
    record: ReconciliationRecord,
    *,
    branch: str,
    commit_hash: str,
    token_target: int,
    issue_anchors: Iterable[ReconciliationRecord] = (),
) -> tuple[List[ReconciliationRecord], Optional[str]]:
    text = str(record.value.get("text") or "")
    if not text:
        return [record], None
    boundaries = sorted({
        match.end() for match in _TEXT_BOUNDARY_RE.finditer(text)
    })
    spans: List[tuple[int, int]] = []
    start = 0
    while start < len(text):
        low = start + 1
        high = len(text)
        maximum_end = start
        while low <= high:
            middle = (low + high) // 2
            candidate = _source_segment_record(
                record,
                text=text,
                start=start,
                end=middle,
                fragment_index=_SEQUENCE_PLACEHOLDER,
                fragment_count=_SEQUENCE_PLACEHOLDER,
            )
            prompt = _render_reconciliation_prompt(
                branch=branch,
                commit_hash=commit_hash,
                records=[candidate],
                issue_anchors=issue_anchors,
                partial_file_paths=[record.file_path],
            )
            if estimated_branch_reconciliation_tokens(prompt) <= token_target:
                maximum_end = middle
                low = middle + 1
            else:
                high = middle - 1
        if maximum_end == start:
            return [], (
                f"source record {record.key} cannot fit even one Unicode "
                "code point within the request-aware input target"
            )
        boundary_index = bisect_right(boundaries, maximum_end) - 1
        end = (
            boundaries[boundary_index]
            if boundary_index >= 0 and boundaries[boundary_index] > start
            else maximum_end
        )
        spans.append((start, end))
        start = end
    count = len(spans)
    return [
        _source_segment_record(
            record,
            text=text,
            start=start,
            end=end,
            fragment_index=index,
            fragment_count=count,
        )
        for index, (start, end) in enumerate(spans)
    ], None


def _location_path(value: Any) -> str:
    raw = str(value or "").strip()
    path, separator, line = raw.rpartition(":")
    if separator and line.strip().isdigit():
        raw = path
    return _normalized_path(raw)


def _record_dependency_paths(record: ReconciliationRecord) -> set[str]:
    paths = {record.file_path} if record.file_path else set()
    if record.kind != "issue":
        return paths
    for field in ("relatedLocations", "affected_files", "affectedFiles"):
        values = record.value.get(field) or []
        if isinstance(values, str):
            values = values.split(",")
        if isinstance(values, (list, tuple, set)):
            paths.update(
                path
                for path in (_location_path(value) for value in values)
                if path
            )
    for line in str(record.value.get("reason") or "").splitlines():
        if line.strip().casefold().startswith("also affects:"):
            paths.update(
                path
                for path in (
                    _location_path(value)
                    for value in line.split(":", 1)[1].split(",")
                )
                if path
            )
    return paths


def _dependency_record_groups(
    records: List[ReconciliationRecord],
) -> List[tuple[set[str], List[ReconciliationRecord]]]:
    parent: Dict[str, str] = {}

    def find(path: str) -> str:
        parent.setdefault(path, path)
        while parent[path] != path:
            parent[path] = parent[parent[path]]
            path = parent[path]
        return path

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for record in records:
        paths = sorted(_record_dependency_paths(record))
        for path in paths:
            find(path)
        for path in paths[1:]:
            union(paths[0], path)
    known_paths = list(parent)
    for index, left in enumerate(known_paths):
        for right in known_paths[index + 1:]:
            if repository_paths_match(left, right):
                union(left, right)

    grouped: Dict[str, tuple[set[str], List[ReconciliationRecord]]] = {}
    for record in records:
        dependency_paths = _record_dependency_paths(record)
        group_key = (
            find(sorted(dependency_paths)[0])
            if dependency_paths
            else f"__global__:{record.key}"
        )
        paths, group_records = grouped.setdefault(group_key, (set(), []))
        paths.update(dependency_paths)
        group_records.append(record)
    return list(grouped.values())


def _build_shards(
    *,
    branch: str,
    commit_hash: str,
    records: List[ReconciliationRecord],
    token_target: int,
    blocked_record_keys: set[str],
    resolution_blocked_issue_keys: set[str],
) -> List[ReconciliationShard]:
    groups = _dependency_record_groups(records)
    packets: List[
        tuple[
            List[ReconciliationRecord],
            set[str],
            List[ReconciliationRecord],
        ]
    ] = []
    current: List[ReconciliationRecord] = []
    current_partial: set[str] = set()

    def append_group(
        group_paths: set[str],
        group: List[ReconciliationRecord],
    ) -> None:
        nonlocal current, current_partial
        complete_prompt = _render_reconciliation_prompt(
            branch=branch,
            commit_hash=commit_hash,
            records=group,
        )
        if (
            not any(record.key in blocked_record_keys for record in group)
            and estimated_branch_reconciliation_tokens(complete_prompt)
            <= token_target
        ):
            candidate = [*current, *group]
            candidate_prompt = _render_reconciliation_prompt(
                branch=branch,
                commit_hash=commit_hash,
                records=candidate,
                partial_file_paths=current_partial,
            )
            if current and estimated_branch_reconciliation_tokens(
                candidate_prompt
            ) > token_target:
                packets.append((current, set(current_partial), []))
                current = list(group)
                current_partial = set()
            else:
                current = candidate
            return

        if current:
            packets.append((current, set(current_partial), []))
            current = []
            current_partial = set()
        issue_authorities = [
            record
            for record in group
            if record.kind == "issue"
            and record.key not in blocked_record_keys
        ]
        partial_packet: List[ReconciliationRecord] = []

        def packet_anchors(
            packet: List[ReconciliationRecord],
        ) -> List[ReconciliationRecord]:
            owned = {record.key for record in packet}
            return [
                record
                for record in issue_authorities
                if record.key not in owned
                and record.key not in blocked_record_keys
                and record.key not in resolution_blocked_issue_keys
            ]

        for record in group:
            if record.key in blocked_record_keys:
                continue
            candidate = [*partial_packet, record]
            candidate_anchors = packet_anchors(candidate)
            candidate_prompt = _render_reconciliation_prompt(
                branch=branch,
                commit_hash=commit_hash,
                records=candidate,
                issue_anchors=candidate_anchors,
                partial_file_paths=group_paths,
            )
            if partial_packet and estimated_branch_reconciliation_tokens(
                candidate_prompt
            ) > token_target:
                packets.append((
                    partial_packet,
                    set(group_paths),
                    packet_anchors(partial_packet),
                ))
                partial_packet = [record]
                candidate = partial_packet
                candidate_anchors = packet_anchors(partial_packet)
                candidate_prompt = _render_reconciliation_prompt(
                    branch=branch,
                    commit_hash=commit_hash,
                    records=partial_packet,
                    issue_anchors=candidate_anchors,
                    partial_file_paths=group_paths,
                )
            if estimated_branch_reconciliation_tokens(
                candidate_prompt
            ) > token_target:
                # A complete issue authority can itself consume nearly the
                # whole request. Drop only anchors (never owned evidence), and
                # retain those issues unresolved instead of replaying evidence
                # across issue subsets.
                for anchor in reversed(candidate_anchors):
                    resolution_blocked_issue_keys.add(anchor.key)
                    candidate_anchors = packet_anchors(candidate)
                    candidate_prompt = _render_reconciliation_prompt(
                        branch=branch,
                        commit_hash=commit_hash,
                        records=candidate,
                        issue_anchors=candidate_anchors,
                        partial_file_paths=group_paths,
                    )
                    if estimated_branch_reconciliation_tokens(
                        candidate_prompt
                    ) <= token_target:
                        break
                if estimated_branch_reconciliation_tokens(
                    candidate_prompt
                ) > token_target:
                    blocked_record_keys.add(record.key)
                    continue
            partial_packet = candidate
        if partial_packet:
            packets.append((
                partial_packet,
                set(group_paths),
                packet_anchors(partial_packet),
            ))

    for group_paths, group in groups:
        append_group(group_paths, group)
    if current:
        packets.append((current, set(current_partial), []))

    shards: List[ReconciliationShard] = []
    for index, (packet, partial_paths, anchors) in enumerate(packets, start=1):
        anchors = [
            anchor
            for anchor in anchors
            if anchor.key not in blocked_record_keys
            and anchor.key not in resolution_blocked_issue_keys
        ]
        prompt = _render_reconciliation_prompt(
            branch=branch,
            commit_hash=commit_hash,
            records=packet,
            issue_anchors=anchors,
            partial_file_paths=partial_paths,
        )
        estimate = estimated_branch_reconciliation_tokens(prompt)
        if estimate > token_target:
            # Only atomic issue/diff records can reach this point. They are not
            # sent; their file's issues remain unresolved with provenance.
            blocked_record_keys.update(record.key for record in packet)
            continue
        shards.append(ReconciliationShard(
            shard_id=f"reconciliation-{index:06d}",
            prompt=prompt,
            record_keys=tuple(record.key for record in packet),
            anchor_record_keys=tuple(record.key for record in anchors),
            issue_ids=tuple(
                record.issue_id
                for record in (*packet, *anchors)
                if record.kind == "issue" and record.issue_id
            ),
            file_paths=tuple(sorted({
                *(record.file_path for record in packet if record.file_path),
                *partial_paths,
            })),
            partial_file_paths=tuple(sorted(partial_paths)),
            estimated_input_tokens=estimate,
        ))
    return shards


def _result_issue_id(issue: Dict[str, Any]) -> str:
    return str(issue.get("issueId") or issue.get("id") or "").strip()


def _result_votes_resolved(issue: Dict[str, Any]) -> bool:
    return issue.get("isResolved", True) is not False


async def execute_packed_branch_reconciliation(
    *,
    llm,
    request: ReviewRequestDto,
    pr_metadata: Dict[str, Any],
    file_contents: Dict[str, str],
    raw_diff: Optional[str],
    event_callback: Optional[Callable[[Dict], None]] = None,
    direct_executor=execute_branch_reconciliation_direct,
    max_shards: int = BRANCH_RECONCILIATION_MAX_SHARDS,
) -> Dict[str, Any]:
    """Run finite direct reconciliation or retain affected issues unresolved."""
    issues = [
        dict(issue) for issue in pr_metadata.get("previousCodeAnalysisIssues", [])
    ]
    token_target = branch_reconciliation_input_token_target(request)
    branch = str(pr_metadata.get("branch") or "<unknown_branch>")
    commit_hash = str(
        pr_metadata.get("commitHash") or "<unknown_commit_hash>"
    )
    issue_records = _issue_records(issues)
    complete_sources = _complete_source_records(file_contents)
    diffs = _diff_records(raw_diff)
    complete_records = [*issue_records, *complete_sources, *diffs]
    complete_prompt = _render_reconciliation_prompt(
        branch=branch,
        commit_hash=commit_hash,
        records=complete_records,
    )

    blocked_record_keys: set[str] = set()
    resolution_blocked_issue_keys: set[str] = set()
    diagnostics: List[str] = []
    source_records: List[ReconciliationRecord] = []
    if estimated_branch_reconciliation_tokens(complete_prompt) <= token_target:
        source_records = complete_sources
    else:
        for source in complete_sources:
            issue_anchors = [
                issue_record
                for issue_record in issue_records
                if any(
                    repository_paths_match(source.file_path, path)
                    for path in _record_dependency_paths(issue_record)
                )
            ]
            fragments, diagnostic = _split_source_record(
                source,
                branch=branch,
                commit_hash=commit_hash,
                token_target=token_target,
                issue_anchors=issue_anchors,
            )
            if diagnostic and issue_anchors:
                resolution_blocked_issue_keys.update(
                    record.key for record in issue_anchors
                )
                fragments, diagnostic = _split_source_record(
                    source,
                    branch=branch,
                    commit_hash=commit_hash,
                    token_target=token_target,
                )
            source_records.extend(fragments)
            if diagnostic:
                blocked_record_keys.add(source.key)
                diagnostics.append(diagnostic)

    records = [*issue_records, *source_records, *diffs]
    for record in records:
        prompt = _render_reconciliation_prompt(
            branch=branch,
            commit_hash=commit_hash,
            records=[record],
            partial_file_paths=[record.file_path] if record.file_path else [],
        )
        if estimated_branch_reconciliation_tokens(prompt) > token_target:
            blocked_record_keys.add(record.key)
            diagnostics.append(
                f"indivisible {record.kind} record {record.key} exceeds the "
                "request-aware input target; related issues remain unresolved"
            )

    shards = _build_shards(
        branch=branch,
        commit_hash=commit_hash,
        records=records,
        token_target=token_target,
        blocked_record_keys=blocked_record_keys,
        resolution_blocked_issue_keys=resolution_blocked_issue_keys,
    )
    shard_ceiling = min(
        BRANCH_RECONCILIATION_MAX_SHARDS,
        max(1, _positive_int_or_default(max_shards, BRANCH_RECONCILIATION_MAX_SHARDS)),
    )
    source_shard_count = len(shards)
    omitted_shards: List[ReconciliationShard] = []
    omitted_record_keys: set[str] = set()
    omitted_issue_ids: set[str] = set()
    if len(shards) > shard_ceiling:
        issue_record_keys = {record.key for record in issue_records}

        def shard_priority(shard: ReconciliationShard) -> tuple:
            owned_issue_count = len(
                issue_record_keys.intersection(shard.record_keys)
            )
            return (
                0 if owned_issue_count else 1,
                -owned_issue_count,
                -len(shard.issue_ids),
                -len(shard.anchor_record_keys),
                shard.shard_id,
            )

        admitted_ids = {
            shard.shard_id
            for shard in sorted(shards, key=shard_priority)[:shard_ceiling]
        }
        omitted_shards = [
            shard for shard in shards if shard.shard_id not in admitted_ids
        ]
        shards = [
            shard for shard in shards if shard.shard_id in admitted_ids
        ]
        omitted_record_keys = {
            key for shard in omitted_shards for key in shard.record_keys
        }
        omitted_issue_ids = {
            issue_id for shard in omitted_shards for issue_id in shard.issue_ids
        }
        blocked_record_keys.update(omitted_record_keys)
        resolution_blocked_issue_keys.update(
            record.key
            for record in issue_records
            if record.issue_id in omitted_issue_ids
        )
        diagnostic = (
            "Branch reconciliation invocation ceiling admitted "
            f"{len(shards)}/{source_shard_count} shard(s); omitted "
            f"{len(omitted_record_keys)} owned record(s) affecting "
            f"{len(omitted_issue_ids)} issue(s). Omitted evidence remains unresolved."
        )
        diagnostics.append(diagnostic)
        logger.warning(diagnostic)
    for key in sorted(resolution_blocked_issue_keys):
        diagnostics.append(
            f"complete issue authority {key} could not accompany every "
            "dependency evidence shard within the request-aware target; "
            "the issue remains unresolved without replaying evidence"
        )
    expected_owned_keys = {
        record.key for record in records
        if record.key not in blocked_record_keys
    }
    observed_owned_keys = [
        record_key
        for shard in shards
        for record_key in shard.record_keys
    ]
    if (
        set(observed_owned_keys) != expected_owned_keys
        or len(observed_owned_keys) != len(expected_owned_keys)
        or len(set(observed_owned_keys)) != len(observed_owned_keys)
    ):
        raise RuntimeError(
            "Branch reconciliation semantic packing lost or repeated records"
        )
    record_by_key = {
        record.key: record
        for record in (*complete_sources, *records)
    }
    source_paths = {
        record.file_path for record in complete_sources
    }
    blocked_files = {
        record_by_key[key].file_path
        for key in blocked_record_keys
        if (
            key in record_by_key
            and record_by_key[key].file_path
            and record_by_key[key].kind in {"source", "diff"}
        )
    }
    blocked_global_evidence = any(
        key in record_by_key
        and record_by_key[key].kind in {"source", "diff"}
        and not record_by_key[key].file_path
        for key in blocked_record_keys
    )
    global_evidence_keys = {
        record.key
        for record in records
        if record.kind in {"source", "diff"} and not record.file_path
    }
    global_evidence_shards = [
        shard
        for shard in shards
        if global_evidence_keys.intersection(shard.record_keys)
    ]
    required_shards_by_issue: Dict[str, List[str]] = {}
    issue_record_key_by_id: Dict[str, str] = {}
    for issue_record in issue_records:
        issue_id = issue_record.issue_id
        if not issue_id:
            diagnostics.append(
                f"{issue_record.key} has no stable issue id and remains unresolved"
            )
            continue
        issue_record_key_by_id.setdefault(issue_id, issue_record.key)
        evidence_shards = [
            shard.shard_id
            for shard in shards
            if issue_id in shard.issue_ids
            and any(
                record_by_key[key].kind in {"source", "diff"}
                for key in shard.record_keys
                if key in record_by_key
            )
        ]
        required_shards_by_issue[issue_id] = evidence_shards or [
            shard.shard_id
            for shard in shards
            if issue_id in shard.issue_ids
        ]
        if not any(
            repository_paths_match(issue_record.file_path, source_path)
            for source_path in source_paths
        ):
            diagnostics.append(
                f"issue {issue_id} has no complete current-source authority; "
                "local absence cannot resolve it"
            )

    shard_results: Dict[str, Dict[str, Any]] = {}
    for shard in shards:
        try:
            result = await direct_executor(
                llm,
                shard.prompt,
                event_callback,
            )
        except Exception as exception:
            raise RuntimeError(
                "Branch reconciliation failed atomically at "
                f"{shard.shard_id}; refusing partial resolution output"
            ) from exception
        shard_results[shard.shard_id] = result

    votes_by_shard = {
        shard_id: {
            _result_issue_id(issue)
            for issue in result.get("issues", [])
            if (
                isinstance(issue, dict)
                and _result_issue_id(issue)
                and _result_votes_resolved(issue)
            )
        }
        for shard_id, result in shard_results.items()
    }
    first_result_by_id: Dict[str, Dict[str, Any]] = {}
    for shard in shards:
        for issue in shard_results[shard.shard_id].get("issues", []):
            if not isinstance(issue, dict):
                continue
            issue_id = _result_issue_id(issue)
            if (
                issue_id
                and issue_id in shard.issue_ids
                and _result_votes_resolved(issue)
            ):
                first_result_by_id.setdefault(issue_id, issue)

    accepted: List[Dict[str, Any]] = []
    issue_provenance: Dict[str, Dict[str, Any]] = {}
    for issue_record in issue_records:
        issue_id = issue_record.issue_id
        if not issue_id or issue_id in issue_provenance:
            continue
        required = required_shards_by_issue.get(issue_id, [])
        complete_source = any(
            repository_paths_match(issue_record.file_path, source_path)
            for source_path in source_paths
        )
        unblocked = (
            issue_record.key not in blocked_record_keys
            and issue_record.key not in resolution_blocked_issue_keys
            and not any(
                repository_paths_match(issue_record.file_path, blocked_file)
                for blocked_file in blocked_files
            )
            and not blocked_global_evidence
            and all(
                issue_id in shard.issue_ids
                for shard in global_evidence_shards
            )
        )
        unanimous = bool(required) and all(
            issue_id in votes_by_shard.get(shard_id, set())
            for shard_id in required
        )
        resolution = first_result_by_id.get(issue_id)
        accepted_resolution = bool(
            resolution and unanimous and complete_source and unblocked
        )
        if accepted_resolution:
            accepted.append(resolution)
        issue_provenance[issue_id] = {
            "issueRecordKey": issue_record_key_by_id.get(issue_id, ""),
            "requiredShardIds": required,
            "completeCurrentSource": complete_source,
            "blockedByIndivisibleRecord": not unblocked,
            "resolutionBlockedByPacking": (
                issue_record.key in resolution_blocked_issue_keys
            ),
            "completeGlobalEvidenceAuthority": all(
                issue_id in shard.issue_ids
                for shard in global_evidence_shards
            ),
            "unanimousResolutionVote": unanimous,
            "resolutionAccepted": accepted_resolution,
        }

    provenance = {
        "inputTokenTarget": token_target,
        "completePromptEstimatedInputTokens": (
            estimated_branch_reconciliation_tokens(complete_prompt)
        ),
        "shards": [
            {
                "shardId": shard.shard_id,
                "recordKeys": list(shard.record_keys),
                "anchorRecordKeys": list(shard.anchor_record_keys),
                "issueIds": list(shard.issue_ids),
                "filePaths": list(shard.file_paths),
                "partialFilePaths": list(shard.partial_file_paths),
                "estimatedInputTokens": shard.estimated_input_tokens,
                "promptSha256": "sha256:" + hashlib.sha256(
                    shard.prompt.encode("utf-8")
                ).hexdigest(),
            }
            for shard in shards
        ],
        "issues": issue_provenance,
        "blockedRecordKeys": sorted(blocked_record_keys),
        "resolutionBlockedIssueRecordKeys": sorted(
            resolution_blocked_issue_keys
        ),
        "diagnostics": diagnostics,
        "invocationCoverage": {
            "coverage": "PARTIAL" if omitted_shards else "COMPLETE",
            "reason": (
                "branch reconciliation invocation ceiling"
                if omitted_shards
                else "all packed shards admitted"
            ),
            "maxShards": shard_ceiling,
            "sourceShardCount": source_shard_count,
            "admittedShardCount": len(shards),
            "omittedShardCount": len(omitted_shards),
            "omittedRecordCount": len(omitted_record_keys),
            "omittedIssueCount": len(omitted_issue_ids),
        },
    }
    return {
        "issues": accepted,
        "comment": (
            f"Branch reconciliation checked {len(issue_records)} issues in "
            f"{len(shards)} request-aware shard(s); accepted "
            f"{len(accepted)} atomic resolution(s)."
        ),
        "reconciliationPromptProvenance": provenance,
    }


def legacy_reconciliation_fail_open(
    *,
    request: ReviewRequestDto,
    issue_count: int,
) -> Dict[str, Any]:
    """Avoid an opaque third-party MCP transcript that cannot be packed."""
    target = branch_reconciliation_input_token_target(request)
    diagnostic = (
        "Legacy MCP reconciliation stopped fail-open because complete current "
        "source was not pre-fetched and the agent transcript cannot be bounded "
        "record-by-record. No issue was resolved from missing optional context."
    )
    logger.warning(diagnostic)
    return {
        "issues": [],
        "comment": diagnostic,
        "reconciliationPromptProvenance": {
            "inputTokenTarget": target,
            "shards": [],
            "issuesRetainedUnresolved": issue_count,
            "diagnostics": [diagnostic],
            "legacyMcpStoppedFailOpen": True,
        },
    }
