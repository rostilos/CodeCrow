"""Single-wave, evidence-packet verification for all review candidates."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import logging
import os
import re
from typing import Any, Iterable, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from service.review.candidate_ledger import CandidateEvidenceLedger
from service.review.orchestrator.verification_agent import (
    is_summary_only_change_compatibility_candidate,
)
from service.review.orchestrator.json_utils import load_json_with_local_repairs
from service.review.orchestrator.json_utils import supports_structured_output
from utils.diff_processor import ProcessedDiff
from utils.llm_response import extract_llm_response_text
from utils.path_identity import normalize_repository_path


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %d", name, raw, default)
        return default


MAX_CANDIDATES_PER_PACKET = max(
    1,
    _env_int("REVIEW_VERIFICATION_MAX_CANDIDATES_PER_CALL", 8),
)
MAX_PACKETS = max(1, _env_int("REVIEW_VERIFICATION_MAX_CALLS", 4))
PACKET_CHAR_BUDGET = max(
    16_000,
    _env_int("REVIEW_VERIFICATION_PACKET_MAX_CHARS", 120_000),
)
SOURCE_WINDOW_LINES = max(
    20,
    _env_int("REVIEW_VERIFICATION_SOURCE_WINDOW_LINES", 100),
)
SOURCE_WINDOW_CHARS = max(
    4_000,
    _env_int("REVIEW_VERIFICATION_SOURCE_WINDOW_CHARS", 16_000),
)
CHANGED_HUNK_CHARS = max(
    4_000,
    _env_int("REVIEW_VERIFICATION_CHANGED_HUNK_CHARS", 16_000),
)
TRANSPORT_RETRIES = min(
    1,
    max(0, _env_int("REVIEW_PROVIDER_RETRY_ATTEMPTS", 1)),
)


class CandidateVerdict(BaseModel):
    verificationId: str
    verdict: Literal["CONFIRMED", "REJECTED", "INCOMPLETE"]
    duplicateOf: Optional[str] = None
    rationale: str = ""

    @field_validator("verdict", mode="before")
    @classmethod
    def normalize_verdict(cls, value: Any) -> str:
        return str(value or "").strip().upper()


class VerificationPacketOutput(BaseModel):
    verdicts: list[CandidateVerdict] = Field(default_factory=list)


@dataclass(frozen=True)
class VerificationRecord:
    verification_id: str
    issue: CodeReviewIssue
    payload: dict[str, Any]
    cluster_key: str


@dataclass(frozen=True)
class VerificationWaveResult:
    confirmed: tuple[CodeReviewIssue, ...]
    rejected_count: int
    incomplete_count: int
    packets_used: int


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def causal_evidence_fingerprint(issue: CodeReviewIssue) -> str:
    """Exact fingerprint for safe pre-model merging.

    The fingerprint is intentionally strict.  Empty causal fields prevent a
    merge, which preserves recall until the verifier can compare candidates.
    """

    trigger = _normalized_text(getattr(issue, "triggerCondition", ""))
    path = _normalized_text(getattr(issue, "causalPath", ""))
    impact = _normalized_text(getattr(issue, "observableImpact", ""))
    if not trigger or not path or not impact:
        return ""
    canonical = json.dumps(
        {
            "trigger": trigger,
            "causalPath": path,
            "impact": impact,
            "file": normalize_repository_path(getattr(issue, "file", "")),
            "anchor": _normalized_text(getattr(issue, "codeSnippet", "")),
            "evidenceRefs": sorted({
                str(value).strip()
                for value in getattr(issue, "evidenceRefs", None) or ()
                if str(value).strip()
            }),
            "relatedLocations": sorted({
                _normalized_text(value)
                for value in getattr(issue, "relatedLocations", None) or ()
                if _normalized_text(value)
            }),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def merge_exact_candidates(
    issues: Iterable[CodeReviewIssue],
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
) -> list[CodeReviewIssue]:
    retained: list[CodeReviewIssue] = []
    by_fingerprint: dict[str, CodeReviewIssue] = {}
    for issue in issues:
        fingerprint = causal_evidence_fingerprint(issue)
        existing = by_fingerprint.get(fingerprint) if fingerprint else None
        if existing is None:
            retained.append(issue)
            if fingerprint:
                by_fingerprint[fingerprint] = issue
            continue
        existing.relatedLocations = list(dict.fromkeys([
            *(getattr(existing, "relatedLocations", None) or ()),
            getattr(issue, "file", ""),
            *(getattr(issue, "relatedLocations", None) or ()),
        ]))
        existing.evidenceRefs = list(dict.fromkeys([
            *(getattr(existing, "evidenceRefs", None) or ()),
            *(getattr(issue, "evidenceRefs", None) or ()),
        ]))
        if candidate_ledger is not None:
            candidate_ledger.reject(
                issue,
                gate="deduplication",
                code="exact_causal_evidence_duplicate",
            )
    return retained


def _current_source(request: ReviewRequestDto) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    ambiguous: set[str] = set()
    enrichment = getattr(request, "enrichmentData", None)
    for item in getattr(enrichment, "fileContents", None) or ():
        path = str(getattr(item, "path", "") or "").strip()
        content = getattr(item, "content", None)
        if (
            not path
            or not isinstance(content, str)
            or getattr(item, "skipped", False) is True
        ):
            continue
        key = normalize_repository_path(path)
        previous = result.get(key)
        if previous is not None and previous[1] != content:
            ambiguous.add(key)
            continue
        result[key] = (path, content)
    for key in ambiguous:
        result.pop(key, None)
    return result


def _source_window(content: str, line: int) -> dict[str, Any]:
    lines = content.splitlines()
    if not lines:
        return {"startLine": 0, "endLine": 0, "source": ""}
    center = max(1, min(line or 1, len(lines)))
    half = SOURCE_WINDOW_LINES // 2
    start = max(1, center - half)
    end = min(len(lines), start + SOURCE_WINDOW_LINES - 1)
    start = max(1, end - SOURCE_WINDOW_LINES + 1)
    numbered = "\n".join(
        f"{number}: {lines[number - 1]}"
        for number in range(start, end + 1)
    )
    numbered = _bounded_around(numbered, "", SOURCE_WINDOW_CHARS)
    return {"startLine": start, "endLine": end, "source": numbered}


def _bounded_around(value: str, needle: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    offset = value.find(needle) if needle else -1
    if offset < 0:
        return value[:limit]
    start = max(0, offset - (limit // 2))
    end = min(len(value), start + limit)
    start = max(0, end - limit)
    return value[start:end]


def _changed_hunk_evidence(diff_file: Any, issue: CodeReviewIssue) -> str:
    if diff_file is None:
        return ""
    anchor = str(getattr(issue, "codeSnippet", "") or "").strip()
    try:
        line = int(getattr(issue, "line", 0) or 0)
    except (TypeError, ValueError):
        line = 0
    hunks = list(getattr(diff_file, "hunks", None) or ())
    matching = [
        hunk
        for hunk in hunks
        if (
            anchor
            and any(
                candidate.startswith("+")
                and not candidate.startswith("+++")
                and anchor in candidate[1:]
                for candidate in hunk.content.splitlines()
            )
        )
        or (
            line > 0
            and hunk.new_start <= line < hunk.new_start + max(1, hunk.new_count)
        )
    ]
    if matching:
        evidence = "\n".join(
            f"{hunk.header}\n{hunk.content}" for hunk in matching[:2]
        )
    else:
        evidence = str(getattr(diff_file, "content", "") or "")
    return _bounded_around(evidence, anchor, CHANGED_HUNK_CHARS)


def _changed_anchor_line(diff_file: Any, anchor: str) -> int:
    if diff_file is None or not anchor:
        return 0
    first_anchor_line = next(
        (line.strip() for line in anchor.splitlines() if line.strip()),
        "",
    )
    if not first_anchor_line:
        return 0
    matches: list[int] = []
    for hunk in getattr(diff_file, "hunks", None) or ():
        current_line = hunk.new_start
        for value in hunk.content.splitlines():
            if value.startswith("+") and not value.startswith("+++"):
                if first_anchor_line in value[1:]:
                    matches.append(current_line)
                current_line += 1
            elif value.startswith("-") and not value.startswith("---"):
                continue
            elif not value.startswith(("@@", "diff --git", "index ", "---", "+++")):
                current_line += 1
    return matches[0] if len(set(matches)) == 1 else 0


def _related_path(location: Any) -> tuple[str, int]:
    value = str(location or "").strip()
    if not value:
        return "", 0
    path, separator, suffix = value.rpartition(":")
    if separator and suffix.isdigit():
        return normalize_repository_path(path), int(suffix)
    return normalize_repository_path(value), 1


def _ledger_evidence(
    issue: CodeReviewIssue,
    ledger: Optional[CandidateEvidenceLedger],
) -> list[dict[str, Any]]:
    if ledger is None:
        return []
    record = ledger.record_for(issue)
    if record is None:
        return []
    facts: list[dict[str, Any]] = []
    for evidence_id in record.evidence_refs:
        for fact in record.visible_evidence_by_id.get(evidence_id, ()):
            facts.append({"evidenceId": evidence_id, "fact": fact})
    return facts


def build_verification_records(
    issues: Iterable[CodeReviewIssue],
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff] = None,
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
) -> tuple[list[VerificationRecord], list[CodeReviewIssue]]:
    sources = _current_source(request)
    diff_by_path = {
        normalize_repository_path(item.path): item
        for item in (processed_diff.files if processed_diff is not None else ())
    }
    records: list[VerificationRecord] = []
    missing_source: list[CodeReviewIssue] = []
    for index, issue in enumerate(issues):
        path_key = normalize_repository_path(getattr(issue, "file", ""))
        source = sources.get(path_key)
        diff_file = diff_by_path.get(path_key)
        exact_diff = _changed_hunk_evidence(diff_file, issue)
        anchor = str(getattr(issue, "codeSnippet", "") or "").strip()
        anchor_lines = [line.strip() for line in anchor.splitlines() if line.strip()]
        added_text = "\n".join(
            line[1:]
            for line in exact_diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        has_exact_hunk_anchor = bool(
            exact_diff
            and anchor_lines
            and all(line in added_text for line in anchor_lines)
        )
        summary_only_compatibility = (
            is_summary_only_change_compatibility_candidate(
                issue,
                candidate_ledger,
            )
        )
        if (
            source is None
            and not has_exact_hunk_anchor
            and not summary_only_compatibility
        ):
            missing_source.append(issue)
            continue
        try:
            line = int(getattr(issue, "line", 0) or 0)
        except (TypeError, ValueError):
            line = 0
        line = _changed_anchor_line(diff_file, anchor) or line
        related_windows: list[dict[str, Any]] = []
        unresolved_related: list[str] = []
        for location in getattr(issue, "relatedLocations", None) or ():
            related_key, related_line = _related_path(location)
            related_source = sources.get(related_key)
            if related_source is None:
                unresolved_related.append(str(location))
                continue
            related_windows.append({
                "path": related_source[0],
                **_source_window(related_source[1], related_line),
            })
        payload = {
            "verificationId": f"issue_{index}",
            "file": source[0] if source is not None else getattr(issue, "file", ""),
            "line": line,
            "severity": getattr(issue, "severity", ""),
            "category": getattr(issue, "category", ""),
            "title": _bounded_around(getattr(issue, "title", "") or "", "", 1_000),
            "reason": _bounded_around(getattr(issue, "reason", ""), anchor, 4_000),
            "suggestedFix": _bounded_around(
                getattr(issue, "suggestedFixDescription", ""), "", 4_000
            ),
            "codeSnippet": _bounded_around(anchor, "", 4_000),
            "triggerCondition": _bounded_around(
                getattr(issue, "triggerCondition", ""), "", 2_000
            ),
            "causalPath": _bounded_around(
                getattr(issue, "causalPath", ""), "", 2_000
            ),
            "observableImpact": _bounded_around(
                getattr(issue, "observableImpact", ""), "", 2_000
            ),
            "currentSource": (
                _source_window(source[1], line)
                if source is not None
                else None
            ),
            "currentChangedHunk": exact_diff,
            "evidenceScope": (
                "removed_change_and_current_related_source"
                if summary_only_compatibility
                else
                "current_head_source_and_changed_hunk"
                if source is not None
                else "changed_hunk_only"
            ),
            "relatedSource": related_windows,
            "unresolvedRelatedLocations": unresolved_related,
            "exactEvidence": _ledger_evidence(issue, candidate_ledger),
        }
        cluster_seed = (
            _normalized_text(getattr(issue, "triggerCondition", ""))
            or _normalized_text(getattr(issue, "codeSnippet", ""))
            or path_key
        )
        records.append(VerificationRecord(
            verification_id=f"issue_{index}",
            issue=issue,
            payload=payload,
            cluster_key=hashlib.sha256(cluster_seed.encode("utf-8")).hexdigest(),
        ))
    return records, missing_source


def _packet_prompt(records: list[VerificationRecord]) -> str:
    return """You are the final adversarial verifier for code-review candidates.

Try to disprove each candidate from the exact current-head evidence supplied.
Confirm only when the evidence establishes the trigger, executable/data path, and
observable wrong behavior. A real defect remains real even if category or severity
is imperfect. Reject praise, already-fixed behavior, optional hardening, style,
unsupported absence claims, and contradicted causal paths. Use INCOMPLETE when an
essential link is not present in the packet.

The host has already merged candidates with identical causal/evidence receipts.
Do not mark additional duplicates from prose, category, severity, or proximity;
return duplicateOf as null.

Return only JSON: {"verdicts":[{"verificationId":"issue_0","verdict":
"CONFIRMED|REJECTED|INCOMPLETE","duplicateOf":null,"rationale":"short evidence
summary"}]}. Return exactly one verdict per supplied ID.

Evidence packets:
""" + json.dumps(
        [record.payload for record in records],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def build_verification_packets(
    records: list[VerificationRecord],
) -> tuple[list[list[VerificationRecord]], list[VerificationRecord]]:
    grouped: dict[str, list[VerificationRecord]] = {}
    order: list[str] = []
    for record in records:
        if record.cluster_key not in grouped:
            order.append(record.cluster_key)
            grouped[record.cluster_key] = []
        grouped[record.cluster_key].append(record)

    ordered_records = [record for key in order for record in grouped[key]]
    packets: list[list[VerificationRecord]] = []
    oversized: list[VerificationRecord] = []
    current: list[VerificationRecord] = []
    for record in ordered_records:
        if len(_packet_prompt([record])) > PACKET_CHAR_BUDGET:
            oversized.append(record)
            continue
        candidate = [*current, record]
        exceeds = (
            len(candidate) > MAX_CANDIDATES_PER_PACKET
            or len(_packet_prompt(candidate)) > PACKET_CHAR_BUDGET
        )
        if current and exceeds:
            packets.append(current)
            current = [record]
        else:
            current = candidate
    if current:
        packets.append(current)

    accepted = packets[:MAX_PACKETS]
    overflow = [
        *oversized,
        *(record for packet in packets[MAX_PACKETS:] for record in packet),
    ]
    return accepted, overflow


async def _invoke_packet(llm: Any, prompt: str) -> VerificationPacketOutput:
    messages = [
        {
            "role": "system",
            "content": "Return only final code-review verification JSON.",
        },
        {"role": "user", "content": prompt},
    ]
    response = None
    structured = (
        llm.with_structured_output(VerificationPacketOutput)
        if hasattr(llm, "with_structured_output") and supports_structured_output(llm)
        else None
    )
    for attempt in range(TRANSPORT_RETRIES + 1):
        try:
            response = await (
                structured.ainvoke(messages)
                if structured is not None
                else llm.ainvoke(messages)
            )
            break
        except Exception:
            if attempt >= TRANSPORT_RETRIES:
                raise
            logger.info("Retrying one failed verification provider request")
    if isinstance(response, VerificationPacketOutput):
        return response
    content = extract_llm_response_text(response)
    # Parsing is deterministic.  Malformed output does not trigger a repair call.
    _, data = load_json_with_local_repairs(content)
    return VerificationPacketOutput(**data)


async def run_verification_wave(
    llm: Any,
    issues: Iterable[CodeReviewIssue],
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff] = None,
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
) -> VerificationWaveResult:
    exact = merge_exact_candidates(issues, candidate_ledger)
    records, missing_source = build_verification_records(
        exact,
        request,
        processed_diff,
        candidate_ledger,
    )
    for issue in missing_source:
        if candidate_ledger is not None:
            candidate_ledger.reject(
                issue,
                gate="llm_verification",
                code="incomplete_current_source",
            )

    packets, overflow = build_verification_packets(records)
    for record in overflow:
        if candidate_ledger is not None:
            candidate_ledger.reject(
                record.issue,
                gate="llm_verification",
                code="verification_budget_exhausted",
            )

    async def verify_packet(
        packet: list[VerificationRecord],
    ) -> tuple[list[CandidateVerdict], bool]:
        try:
            output = await _invoke_packet(llm, _packet_prompt(packet))
            return output.verdicts, False
        except Exception as exc:
            logger.warning("Verification packet incomplete: %s", exc)
            return [], True

    outputs = await asyncio.gather(*(verify_packet(packet) for packet in packets))
    confirmed: list[CodeReviewIssue] = []
    rejected_count = 0
    incomplete_count = len(missing_source) + len(overflow)

    for packet, (verdicts, failed) in zip(packets, outputs):
        by_id = {record.verification_id: record for record in packet}
        if failed:
            incomplete_count += len(packet)
            for record in packet:
                if candidate_ledger is not None:
                    candidate_ledger.reject(
                        record.issue,
                        gate="llm_verification",
                        code="verification_packet_failed",
                    )
            continue

        normalized: dict[str, CandidateVerdict] = {}
        for verdict in verdicts:
            verification_id = verdict.verificationId.strip()
            if verification_id in by_id and verification_id not in normalized:
                normalized[verification_id] = verdict

        packet_confirmed: dict[str, CodeReviewIssue] = {}
        for verification_id, record in by_id.items():
            verdict = normalized.get(verification_id)
            if verdict is None or verdict.verdict == "INCOMPLETE":
                incomplete_count += 1
                if candidate_ledger is not None:
                    candidate_ledger.reject(
                        record.issue,
                        gate="llm_verification",
                        code="incomplete_causal_evidence",
                    )
            elif verdict.verdict == "REJECTED":
                rejected_count += 1
                if candidate_ledger is not None:
                    candidate_ledger.reject(
                        record.issue,
                        gate="llm_verification",
                        code="rejected_false_positive",
                    )
            else:
                packet_confirmed[verification_id] = record.issue
        confirmed.extend(packet_confirmed.values())

    return VerificationWaveResult(
        confirmed=tuple(confirmed),
        rejected_count=rejected_count,
        incomplete_count=incomplete_count,
        packets_used=len(packets),
    )
