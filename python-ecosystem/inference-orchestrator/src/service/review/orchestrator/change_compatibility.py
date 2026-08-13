"""Bounded compatibility review for deletion-only and metadata-only rename PRs.

These changes have no current-side source line that can honestly be used as an
inline review anchor.  This path therefore uses the removed hunk or provider
rename receipt as change evidence, requires exact current-head related source,
and emits only summary-level (``line=0``) candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
import re
from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

from model.dtos import ReviewRequestDto
from model.multi_stage import ReviewContextRequest
from model.output_schemas import CodeReviewIssue
from service.review.candidate_ledger import CandidateEvidenceLedger
from service.review.orchestrator.exact_context import (
    ExactContextEvidence,
    ExactContextResolver,
    ReviewFollowupBudget,
)
from service.review.orchestrator.json_utils import (
    load_json_with_local_repairs,
    supports_structured_output,
)
from utils.diff_processor import (
    DiffChangeType,
    DiffFile,
    HunkDisposition,
    ProcessedDiff,
)
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


MAX_CHANGE_UNITS = max(1, _env_int("REVIEW_COMPATIBILITY_MAX_CHANGES", 4))
MAX_RELATED_SOURCES = max(
    1,
    _env_int("REVIEW_COMPATIBILITY_MAX_RELATED_SOURCES", 3),
)
PROMPT_CHAR_BUDGET = max(
    12_000,
    _env_int("REVIEW_COMPATIBILITY_PROMPT_MAX_CHARS", 60_000),
)
REMOVED_EVIDENCE_CHARS = 12_000


@dataclass(frozen=True)
class CompatibilityChange:
    ticket_id: str
    path: str
    kind: str
    anchor: str
    anchor_evidence_id: str
    prompt_hunk_ids: tuple[str, ...]
    change_evidence: str
    related_requests: tuple[ReviewContextRequest, ...]
    navigation_identifiers: tuple[str, ...]
    excluded_evidence_paths: tuple[str, ...]


@dataclass(frozen=True)
class CompatibilityResult:
    issues: tuple[CodeReviewIssue, ...] = ()
    changes_considered: int = 0
    call_used: bool = False
    incomplete_changes: tuple[str, ...] = ()


class CompatibilityOutput(BaseModel):
    issues: list[CodeReviewIssue] = Field(default_factory=list)


def _digest_id(prefix: str, payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return prefix + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _removed_lines(diff_file: DiffFile) -> list[str]:
    return [
        line[1:]
        for hunk in diff_file.hunks
        if hunk.disposition is HunkDisposition.DELETED
        for line in hunk.content.splitlines()
        if line.startswith("-")
        and not line.startswith("---")
        and line[1:].strip()
    ]


def _removed_anchor(lines: Iterable[str]) -> str:
    values = list(lines)
    declaration = re.compile(
        r"\b(?:class|interface|enum|record|def|function|fun|type|struct|"
        r"public|protected|export|route|endpoint)\b",
        re.IGNORECASE,
    )
    return next(
        (line for line in values if declaration.search(line)),
        values[0] if values else "",
    ).strip()[:1_000]


def _bounded_removed_evidence(diff_file: DiffFile, anchor: str) -> str:
    evidence = "\n".join(
        f"{hunk.header}\n{hunk.content}"
        for hunk in diff_file.hunks
        if hunk.disposition is HunkDisposition.DELETED
    )
    if len(evidence) <= REMOVED_EVIDENCE_CHARS:
        return evidence
    offset = evidence.find(anchor)
    if offset < 0:
        return evidence[:REMOVED_EVIDENCE_CHARS]
    start = max(0, offset - REMOVED_EVIDENCE_CHARS // 2)
    end = min(len(evidence), start + REMOVED_EVIDENCE_CHARS)
    return evidence[max(0, end - REMOVED_EVIDENCE_CHARS):end]


def _current_contents(request: ReviewRequestDto) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    ambiguous: set[str] = set()
    enrichment = getattr(request, "enrichmentData", None)
    for item in getattr(enrichment, "fileContents", None) or ():
        path = str(getattr(item, "path", "") or "").strip()
        content = getattr(item, "content", None)
        key = normalize_repository_path(path)
        if (
            not key
            or not isinstance(content, str)
            or getattr(item, "skipped", False) is True
        ):
            continue
        previous = result.get(key)
        if previous is not None and previous[1] != content:
            ambiguous.add(key)
            continue
        result[key] = (path, content)
    for key in ambiguous:
        result.pop(key, None)
    return result


def _line_for(content: str, needles: Iterable[str]) -> int:
    normalized = tuple(value for value in needles if value)
    for index, line in enumerate(content.splitlines(), start=1):
        if any(value in line for value in normalized):
            return index
    return 1


def _related_paths(
    request: ReviewRequestDto,
    *,
    old_path: str,
    current_path: str = "",
) -> list[tuple[str, str, int]]:
    """Choose a few exact current-head sources using deterministic receipts."""
    contents = _current_contents(request)
    old_key = normalize_repository_path(old_path)
    current_key = normalize_repository_path(current_path)
    related: dict[str, tuple[str, str, int]] = {}
    enrichment = getattr(request, "enrichmentData", None)

    for edge in getattr(enrichment, "relationships", None) or ():
        source_key = normalize_repository_path(
            str(getattr(edge, "sourceFile", "") or "")
        )
        target_key = normalize_repository_path(
            str(getattr(edge, "targetFile", "") or "")
        )
        if old_key not in {source_key, target_key} and current_key not in {
            source_key,
            target_key,
        }:
            continue
        other_key = target_key if source_key in {old_key, current_key} else source_key
        current = contents.get(other_key)
        if current is None or other_key in {old_key, current_key}:
            continue
        symbol = str(getattr(edge, "matchedOn", "") or "").strip()
        related[other_key] = (
            current[0],
            symbol,
            _line_for(current[1], (symbol, old_path, current_path)),
        )

    # An explicit stale path reference is strong navigation evidence even when
    # the relationship parser has no edge for the deleted/renamed-away file.
    without_extension = old_key.rsplit(".", 1)[0]
    path_needles = tuple(dict.fromkeys(filter(None, (
        old_path,
        old_key,
        old_key.replace("/", "."),
        without_extension.replace("/", "."),
    ))))
    for key, (path, content) in sorted(contents.items()):
        if key in {old_key, current_key} or key in related:
            continue
        if any(needle in content for needle in path_needles):
            related[key] = (path, next(
                needle for needle in path_needles if needle in content
            ), _line_for(content, path_needles))

    return list(related.values())[:MAX_RELATED_SOURCES]


def _context_requests(
    ticket_id: str,
    paths: Iterable[tuple[str, str, int]],
) -> tuple[ReviewContextRequest, ...]:
    return tuple(
        ReviewContextRequest(
            requestId=f"{ticket_id}-ctx-{index}",
            kind="LOCAL_EXACT",
            question="Does this current source remain compatible with the removed contract?",
            targetPath=path,
            targetSymbol=symbol or None,
            requiredEvidence="Exact current-head source proving or disproving the compatibility path.",
            startLine=max(1, line - 40),
            endLine=max(1, line + 40),
        )
        for index, (path, symbol, line) in enumerate(paths, start=1)
    )


def _navigation_identifiers(
    old_path: str,
    removed_lines: Iterable[str] = (),
) -> tuple[str, ...]:
    declarations: list[str] = []
    declaration = re.compile(
        r"\b(?:class|interface|enum|record|def|function|fun|type|struct)\s+"
        r"([A-Za-z_$][A-Za-z0-9_$]*)"
    )
    for line in removed_lines:
        match = declaration.search(line)
        if match and match.group(1) not in declarations:
            declarations.append(match.group(1))
    normalized = normalize_repository_path(old_path)
    module = normalized.rsplit(".", 1)[0].replace("/", ".")
    basename = normalized.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return tuple(dict.fromkeys(filter(None, (
        *declarations[:2],
        module,
        basename,
    ))))[:2]


def _combined_context_requests(
    ticket_id: str,
    known_paths: Iterable[tuple[str, str, int]],
    navigation_identifiers: Iterable[str],
) -> tuple[ReviewContextRequest, ...]:
    del navigation_identifiers
    return _context_requests(ticket_id, known_paths)


def _proves_external_stale_reference(
    change: CompatibilityChange,
    evidence: ExactContextEvidence,
) -> bool:
    excluded = {
        normalize_repository_path(path)
        for path in change.excluded_evidence_paths
    }
    if normalize_repository_path(evidence.path) in excluded:
        return False
    return any(
        re.search(
            rf"(?<![A-Za-z0-9_$]){re.escape(identifier)}"
            rf"(?![A-Za-z0-9_$])",
            evidence.content,
        )
        for identifier in change.navigation_identifiers
        if identifier
    )


def build_compatibility_changes(
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
) -> list[CompatibilityChange]:
    if processed_diff is None:
        return []
    changes: list[CompatibilityChange] = []
    seen: set[tuple[str, str]] = set()

    for diff_file in processed_diff.files:
        if (
            diff_file.change_type is not DiffChangeType.DELETED
            or (
                diff_file.plugin_disposition in {"excluded", "generated"}
                or (
                    diff_file.is_skipped
                    and diff_file.skip_reason not in {
                        "Deleted file",
                        "Metadata-only change",
                    }
                )
            )
        ):
            continue
        lines = _removed_lines(diff_file)
        anchor = _removed_anchor(lines)
        hunk_ids = tuple(
            hunk.id for hunk in diff_file.hunks
            if hunk.disposition is HunkDisposition.DELETED
        )
        if not anchor or not hunk_ids:
            continue
        identity = ("DELETED", normalize_repository_path(diff_file.path))
        if identity in seen:
            continue
        seen.add(identity)
        ticket_id = _digest_id("CC", identity)
        anchor_id = _digest_id("REM-", {
            "path": diff_file.path,
            "hunks": hunk_ids,
            "anchor": anchor,
        })
        changes.append(CompatibilityChange(
            ticket_id=ticket_id,
            path=diff_file.path,
            kind="DELETED",
            anchor=anchor,
            anchor_evidence_id=anchor_id,
            prompt_hunk_ids=hunk_ids,
            change_evidence=_bounded_removed_evidence(diff_file, anchor),
            related_requests=_combined_context_requests(
                ticket_id,
                _related_paths(request, old_path=diff_file.path),
                _navigation_identifiers(diff_file.path, lines),
            ),
            navigation_identifiers=_navigation_identifiers(diff_file.path, lines),
            excluded_evidence_paths=(diff_file.path,),
        ))

    manifest = getattr(request, "pullRequestFileManifest", None)
    full_review = str(getattr(request, "analysisMode", "FULL") or "FULL").upper() == "FULL"
    enrichment_dispositions = {
        normalize_repository_path(str(item.path or "")): str(
            item.skipReason or ""
        ).casefold()
        for item in (
            getattr(getattr(request, "enrichmentData", None), "fileContents", None)
            or ()
        )
        if getattr(item, "skipped", False)
    }
    processed_rename_pairs = {
        (
            normalize_repository_path(diff_file.old_path or ""),
            normalize_repository_path(diff_file.path),
        )
        for diff_file in processed_diff.files
        if diff_file.change_type is DiffChangeType.RENAMED
        and diff_file.old_path
        and diff_file.path
    }
    selected_renames = {
        (
            normalize_repository_path(diff_file.old_path or ""),
            normalize_repository_path(diff_file.path),
        )
        for diff_file in processed_diff.files
        if diff_file.change_type is DiffChangeType.RENAMED
        and diff_file.plugin_disposition not in {"excluded", "generated"}
        and (
            not diff_file.is_skipped
            or diff_file.skip_reason == "Metadata-only change"
        )
        and diff_file.old_path
        and diff_file.path
    }
    for receipt in getattr(manifest, "changes", None) or ():
        kind = str(getattr(receipt, "kind", "") or "").strip().upper()
        previous_path = str(getattr(receipt, "previousPath", "") or "").strip()
        current_path = str(getattr(receipt, "path", "") or "").strip()
        if kind != "RENAMED" or not previous_path or not current_path:
            continue
        current_disposition = enrichment_dispositions.get(
            normalize_repository_path(current_path),
            "",
        )
        if "generated" in current_disposition or "excluded" in current_disposition:
            continue
        rename_pair = (
            normalize_repository_path(previous_path),
            normalize_repository_path(current_path),
        )
        if rename_pair in processed_rename_pairs and rename_pair not in selected_renames:
            # A selected diff entry with an explicit generated/excluded policy
            # always wins over the provider-wide manifest, including FULL runs.
            continue
        if not full_review and rename_pair not in selected_renames:
            # The provider manifest is the complete base-to-head inventory,
            # while processed_diff owns this run's scoped review delta. Old
            # incremental or excluded renames must maintain the overlay but
            # must not be rediscovered as current findings.
            continue
        identity = ("RENAMED", normalize_repository_path(previous_path))
        if identity in seen:
            continue
        seen.add(identity)
        ticket_id = _digest_id("CC", {
            "kind": kind,
            "previousPath": previous_path,
            "path": current_path,
            "manifestReceipt": getattr(manifest, "receipt", ""),
        })
        anchor = f"rename from {previous_path}\nrename to {current_path}"
        anchor_id = _digest_id("REN-", {
            "previousPath": previous_path,
            "path": current_path,
            "receipt": getattr(manifest, "receipt", ""),
        })
        changes.append(CompatibilityChange(
            ticket_id=ticket_id,
            path=previous_path,
            kind="RENAMED",
            anchor=anchor,
            anchor_evidence_id=anchor_id,
            prompt_hunk_ids=(anchor_id,),
            change_evidence=anchor,
            related_requests=_combined_context_requests(
                ticket_id,
                _related_paths(
                    request,
                    old_path=previous_path,
                    current_path=current_path,
                ),
                _navigation_identifiers(previous_path),
            ),
            navigation_identifiers=_navigation_identifiers(previous_path),
            excluded_evidence_paths=(previous_path, current_path),
        ))
    return sorted(
        changes,
        key=lambda item: (
            normalize_repository_path(item.path),
            item.kind,
            item.ticket_id,
        ),
    )


def _unassessable_deletions(
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
    discovered_changes: Iterable[CompatibilityChange],
) -> list[str]:
    """Return selected deletions that lack truthful removed-side evidence."""
    if processed_diff is None:
        return []
    assessable_paths = {
        normalize_repository_path(change.path)
        for change in discovered_changes
        if change.kind == "DELETED"
    }
    explicit_plugin_skips = {
        normalize_repository_path(diff_file.path)
        for diff_file in processed_diff.files
        if diff_file.plugin_disposition in {"excluded", "generated"}
    }
    unavailable_paths = {
        normalize_repository_path(diff_file.path)
        for diff_file in processed_diff.files
        if diff_file.change_type is DiffChangeType.DELETED
        and diff_file.plugin_disposition not in {"excluded", "generated"}
        and normalize_repository_path(diff_file.path) not in assessable_paths
    }

    manifest = getattr(request, "pullRequestFileManifest", None)
    full_review = (
        str(getattr(request, "analysisMode", "FULL") or "FULL").upper()
        == "FULL"
    )
    selected_deleted = {
        normalize_repository_path(path)
        for path in (getattr(request, "deletedFiles", None) or ())
    }
    for receipt in getattr(manifest, "changes", None) or ():
        kind = str(getattr(receipt, "kind", "") or "").strip().upper()
        path = normalize_repository_path(str(getattr(receipt, "path", "") or ""))
        if (
            kind != "DELETED"
            or not path
            or path in assessable_paths
            or path in explicit_plugin_skips
            or (not full_review and path not in selected_deleted)
        ):
            continue
        unavailable_paths.add(path)

    return [
        f"{_digest_id('CC', ('DELETED', path))}:removed_evidence_unavailable"
        for path in sorted(unavailable_paths)
    ]


def _prompt(
    request: ReviewRequestDto,
    packets: list[tuple[CompatibilityChange, tuple[ExactContextEvidence, ...]]],
) -> str:
    payload = []
    for change, evidence in packets:
        payload.append({
            "ticketId": change.ticket_id,
            "kind": change.kind,
            "summaryOnlyFile": change.path,
            "requiredLine": 0,
            "allowedCodeSnippet": change.anchor,
            "changeEvidenceId": change.anchor_evidence_id,
            "changeEvidence": change.change_evidence,
            "currentRelatedEvidence": [item.prompt_payload() for item in evidence],
        })
    return """You are reviewing deletion-only or metadata-only rename changes.

Each ticket contains real removed-hunk or provider rename evidence plus exact
current-head related source. Actively try to disprove a compatibility break.
Emit at most one issue per ticket, and omit it unless the supplied evidence proves
that current code still depends on the removed path/contract and will fail.

For every emitted issue:
- set id to ticketId, file to summaryOnlyFile, line to 0, and scope to FILE;
- copy allowedCodeSnippet exactly, even though it is old-side/receipt evidence;
- include changeEvidenceId and every relied-on current evidenceId in evidenceRefs;
- name a concrete triggerCondition, causalPath, and observableImpact;
- use relatedLocations for current-head paths;
- report only an actionable defect, never migration advice, optional hardening,
  uncertain usage, or a rename that current code already handles.

Return only JSON matching {"issues": [CodeReviewIssue, ...]}.

Repository: %s
Revision: %s
Tickets:
%s
""" % (
        request.projectVcsRepoSlug,
        request.currentCommitHash or request.commitHash or "",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )


async def _invoke(llm: Any, prompt: str) -> CompatibilityOutput:
    messages = [
        {
            "role": "system",
            "content": "Return only bounded change-compatibility review JSON.",
        },
        {"role": "user", "content": prompt},
    ]
    if hasattr(llm, "with_structured_output") and supports_structured_output(llm):
        return await llm.with_structured_output(CompatibilityOutput).ainvoke(messages)
    response = await llm.ainvoke(messages)
    _, data = load_json_with_local_repairs(extract_llm_response_text(response))
    return CompatibilityOutput(**data)


async def run_change_compatibility_review(
    llm: Any,
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
    *,
    exact_context_resolver: ExactContextResolver,
    followup_budget: ReviewFollowupBudget,
    candidate_ledger: CandidateEvidenceLedger,
) -> CompatibilityResult:
    discovered_changes = build_compatibility_changes(request, processed_diff)
    unassessable_deletions = _unassessable_deletions(
        request,
        processed_diff,
        discovered_changes,
    )
    total_changes = len(discovered_changes) + len(unassessable_deletions)
    if not discovered_changes:
        return CompatibilityResult(
            changes_considered=total_changes,
            incomplete_changes=tuple(unassessable_deletions),
        )
    changes = discovered_changes[:MAX_CHANGE_UNITS]
    overflow = discovered_changes[MAX_CHANGE_UNITS:]
    initial_incomplete = [
        *unassessable_deletions,
        *(f"{change.ticket_id}:change_limit" for change in overflow),
    ]
    budget_key = "change-compatibility"
    budget_kind = "change_compatibility"
    if not await followup_budget.reserve(budget_kind, budget_key):
        return CompatibilityResult(
            changes_considered=total_changes,
            incomplete_changes=tuple([
                *initial_incomplete,
                *(f"{change.ticket_id}:followup_budget" for change in changes),
            ]),
        )

    packets: list[tuple[CompatibilityChange, tuple[ExactContextEvidence, ...]]] = []
    incomplete: list[str] = list(initial_incomplete)
    for change in changes:
        evidence_candidates: list[ExactContextEvidence] = []
        remaining = MAX_RELATED_SOURCES
        if change.related_requests:
            resolution = await exact_context_resolver.resolve(
                change.related_requests[:remaining],
                originating_paths=(change.path,),
            )
            evidence_candidates.extend(resolution.resolved)
            remaining = max(
                0,
                remaining
                - len(resolution.resolved)
                - len(resolution.unresolved),
            )
        for index, identifier in enumerate(change.navigation_identifiers, start=1):
            if remaining <= 0:
                break
            navigation = await exact_context_resolver.resolve_reverse_references(
                identifier,
                originating_paths=(change.path,),
                excluded_paths=change.excluded_evidence_paths,
                max_results=remaining,
                request_id_prefix=f"{change.ticket_id}-nav-{index}",
            )
            evidence_candidates.extend(navigation.resolved)
            remaining = max(
                0,
                remaining
                - len(navigation.resolved)
                - len(navigation.unresolved),
            )
        resolved = tuple(sorted({
            item.evidence_id: item
            for item in evidence_candidates
            if _proves_external_stale_reference(change, item)
        }.values(), key=lambda item: (
            normalize_repository_path(item.path),
            item.start_line,
            item.end_line,
            item.evidence_id,
        )))[:MAX_RELATED_SOURCES]
        if not resolved:
            incomplete.append(f"{change.ticket_id}:external_reference_unavailable")
            continue
        packets.append((change, resolved))

    if not packets:
        await followup_budget.release(budget_kind, budget_key)
        return CompatibilityResult(
            changes_considered=total_changes,
            incomplete_changes=tuple(incomplete),
        )

    prompt = _prompt(request, packets)
    while packets and len(prompt) > PROMPT_CHAR_BUDGET:
        removed, _ = packets.pop()
        incomplete.append(f"{removed.ticket_id}:prompt_budget")
        prompt = _prompt(request, packets)
    if not packets:
        await followup_budget.release(budget_kind, budget_key)
        return CompatibilityResult(
            changes_considered=total_changes,
            incomplete_changes=tuple(incomplete),
        )

    await followup_budget.commit(budget_kind, budget_key)
    try:
        output = await _invoke(llm, prompt)
    except Exception as exc:
        logger.warning("Change compatibility review incomplete: %s", exc)
        return CompatibilityResult(
            changes_considered=total_changes,
            call_used=True,
            incomplete_changes=tuple([
                *incomplete,
                *(f"{change.ticket_id}:provider_failure" for change, _ in packets),
            ]),
        )

    by_ticket = {change.ticket_id: (change, evidence) for change, evidence in packets}
    accepted: list[CodeReviewIssue] = []
    seen_tickets: set[str] = set()
    for issue in output.issues:
        ticket_id = str(issue.id or "").strip()
        packet = by_ticket.get(ticket_id)
        if packet is None or ticket_id in seen_tickets:
            continue
        change, evidence = packet
        current_ids = {item.evidence_id for item in evidence}
        refs = {str(value).strip() for value in issue.evidenceRefs if str(value).strip()}
        if (
            normalize_repository_path(issue.file)
            != normalize_repository_path(change.path)
            or int(issue.line or 0) != 0
            or issue.scope != "FILE"
            or issue.codeSnippet != change.anchor
            or change.anchor_evidence_id not in refs
            or not (refs & current_ids)
            or not refs.issubset({change.anchor_evidence_id, *current_ids})
            or not issue.triggerCondition.strip()
            or not issue.causalPath.strip()
            or not issue.observableImpact.strip()
            or issue.isResolved is True
        ):
            continue
        related_locations = [
            f"{item.path}:{item.start_line}"
            for item in evidence
            if item.evidence_id in refs
        ]
        issue.relatedLocations = list(dict.fromkeys(related_locations))
        visible = {
            change.anchor_evidence_id: ({
                "kind": "change_compatibility_anchor",
                "changeKind": change.kind,
                "path": change.path,
                "anchor": change.anchor,
                "promptHunkIds": list(change.prompt_hunk_ids),
            },),
            **{
                item.evidence_id: (item.ledger_fact(),)
                for item in evidence
            },
        }
        candidate_ledger.register(
            issue,
            stage="change_compatibility",
            source_key=change.ticket_id,
            review_unit_ids=(change.ticket_id,),
            prompt_hunk_ids=change.prompt_hunk_ids,
            generation_prompt=prompt,
            visible_evidence_by_id=visible,
        )
        issue.id = None
        issue.isResolved = False
        issue.resolutionReason = None
        issue.resolutionExplanation = None
        issue.resolvedInCommit = None
        issue.visibility = None
        accepted.append(issue)
        seen_tickets.add(ticket_id)

    return CompatibilityResult(
        issues=tuple(accepted),
        changes_considered=total_changes,
        call_used=True,
        incomplete_changes=tuple(incomplete),
    )
