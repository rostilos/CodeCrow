"""Evidence-triggered cross-file investigation.

This module deliberately does not perform repository-wide semantic search.  The
host admits a model investigation only when it has both a falsifiable concern and
an exact relationship from the immutable review snapshot.  It emits the normal
``CodeReviewIssue`` type so every candidate follows the same downstream gates.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import logging
import os
import re
from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

from model.dtos import ReviewRequestDto
from model.multi_stage import ReviewContextRequest, ReviewPlan
from model.output_schemas import CodeReviewIssue
from service.review.orchestrator.exact_context import (
    ExactContextResolver,
    ReviewFollowupBudget,
)
from service.review.orchestrator.json_utils import supports_structured_output
from service.review.orchestrator.json_utils import load_json_with_local_repairs
from utils.diff_processor import DiffFile, ProcessedDiff
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


MAX_TICKETS = max(1, _env_int("REVIEW_CROSS_FILE_MAX_TICKETS", 8))
MAX_CALLS = max(1, _env_int("REVIEW_FOLLOW_UP_MAX_CALLS", 4))
PACK_CHAR_BUDGET = max(
    16_000,
    _env_int("REVIEW_EVIDENCE_PACK_MAX_CHARS", 120_000),
)
SOURCE_WINDOW_CHARS = max(
    2_000,
    _env_int("REVIEW_EXACT_SOURCE_WINDOW_CHARS", 16_000),
)


_CONTRACT_CHANGE_RE = re.compile(
    r"\b(?:public|interface|implements|extends|schema|migration|route|endpoint|"
    r"config(?:uration)?|serializer|deserializer|transaction|permission|"
    r"authori[sz]ation|event|return\s+type|parameter|argument|nullable|required|"
    r"request|response|payload|enum|protocol|abstract|override)\b",
    re.IGNORECASE,
)

_DECLARATION_CHANGE_RE = re.compile(
    r"\b(?:def|class|interface|function|fun|type|struct|enum|export|"
    r"public|protected|private)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class InvestigationTicket:
    ticket_id: str
    hypothesis: str
    trigger_file: str
    related_file: str
    relationship_type: str
    matched_on: str
    trigger_hunks: str
    trigger_source: str
    related_source: str
    related_start_line: int = 1

    def compact_payload(self) -> dict[str, Any]:
        return {
            "ticketId": self.ticket_id,
            "hypothesis": self.hypothesis,
            "changedTrigger": self.trigger_file,
            "relatedPath": self.related_file,
            "relationship": self.relationship_type,
            "matchedOn": self.matched_on,
            "changedHunks": self.trigger_hunks,
            "currentTriggerSource": self.trigger_source,
            "currentRelatedSource": self.related_source,
            "relatedStartLine": self.related_start_line,
        }


@dataclass(frozen=True)
class GeneratedCrossFileCandidate:
    issue: CodeReviewIssue
    ticket: InvestigationTicket
    prompt_digest: str


@dataclass(frozen=True)
class TargetedCrossFileResult:
    candidates: tuple[GeneratedCrossFileCandidate, ...]
    admitted_tickets: int
    calls_used: int
    incomplete_tickets: tuple[str, ...] = ()

    @property
    def issues(self) -> list[CodeReviewIssue]:
        return [candidate.issue for candidate in self.candidates]


class CrossFileInvestigationOutput(BaseModel):
    """One finding at most for each admitted causal hypothesis."""

    issues: list[CodeReviewIssue] = Field(default_factory=list)


def _relationship_value(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value or "").strip()


def _content_by_path(request: ReviewRequestDto) -> dict[str, tuple[str, str]]:
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
        if not key:
            continue
        existing = result.get(key)
        if existing is not None and existing[1] != content:
            ambiguous.add(key)
            continue
        result[key] = (path, content)
    for key in ambiguous:
        result.pop(key, None)
    return result


def _bounded_source(content: str, needle: str = "") -> str:
    if len(content) <= SOURCE_WINDOW_CHARS:
        return content
    offset = content.find(needle) if needle else -1
    if offset < 0:
        return content[:SOURCE_WINDOW_CHARS]
    half = SOURCE_WINDOW_CHARS // 2
    start = max(0, offset - half)
    end = min(len(content), start + SOURCE_WINDOW_CHARS)
    start = max(0, end - SOURCE_WINDOW_CHARS)
    return content[start:end]


def _bounded_trigger_hunks(file: DiffFile, needle: str = "") -> str:
    hunks = list(getattr(file, "hunks", None) or ())
    matching = [hunk for hunk in hunks if needle and needle in hunk.content]
    evidence = "\n".join(
        f"{hunk.header}\n{hunk.content}"
        for hunk in (matching[:2] if matching else hunks[:2])
    )
    if not evidence:
        evidence = file.content or ""
    return _bounded_source(evidence, needle)


def _changed_files(processed_diff: Optional[ProcessedDiff]) -> dict[str, DiffFile]:
    if processed_diff is None:
        return {}
    return {
        normalize_repository_path(item.path): item
        for item in processed_diff.get_included_files()
        if normalize_repository_path(item.path)
    }


def _location_path(location: str) -> str:
    value = str(location or "").strip()
    if not value:
        return ""
    path, separator, possible_line = value.rpartition(":")
    if separator and possible_line.isdigit():
        value = path
    return normalize_repository_path(value)


def _issue_supports_edge(
    issues: Iterable[CodeReviewIssue],
    trigger_key: str,
    related_key: str,
    matched_on: str,
) -> Optional[str]:
    for issue in issues:
        if normalize_repository_path(getattr(issue, "file", "")) != trigger_key:
            continue
        related = {
            _location_path(value)
            for value in getattr(issue, "relatedLocations", None) or ()
        }
        causal = str(getattr(issue, "causalPath", "") or "")
        reason = str(getattr(issue, "reason", "") or "")
        if (
            related_key in related
            or (matched_on and matched_on in causal)
            or (matched_on and matched_on in reason)
        ):
            title = str(getattr(issue, "title", "") or "").strip()
            return (
                "Verify the cross-file execution path behind the current candidate"
                + (f" '{title}'" if title else "")
            )
    return None


def _plan_supports_edge(
    plan: ReviewPlan,
    trigger_path: str,
    related_path: str,
    matched_on: str,
) -> Optional[str]:
    tokens = {
        trigger_path,
        related_path,
        trigger_path.rsplit("/", 1)[-1],
        related_path.rsplit("/", 1)[-1],
    }
    if matched_on:
        tokens.add(matched_on)
    for concern in getattr(plan, "cross_file_concerns", None) or ():
        value = str(concern or "").strip()
        lowered = value.lower()
        matches = sum(1 for token in tokens if token and token.lower() in lowered)
        if matches >= 2 or (matched_on and matched_on.lower() in lowered):
            return value
    return None


def _contract_hypothesis(
    file: DiffFile,
    related_path: str,
    relation: str,
    matched_on: str,
) -> Optional[str]:
    changed_lines = [
        line[1:]
        for line in (file.content or "").splitlines()
        if line.startswith(("+", "-"))
        and not line.startswith(("+++", "---"))
    ]
    changed_text = "\n".join(changed_lines)
    explicit_contract_change = bool(_CONTRACT_CHANGE_RE.search(changed_text))
    exact_symbol_declaration_change = bool(
        matched_on
        and any(
            matched_on in line and _DECLARATION_CHANGE_RE.search(line)
            for line in changed_lines
        )
    )
    if not explicit_contract_change and not exact_symbol_declaration_change:
        return None
    return (
        f"Verify that the contract changed in {file.path} remains compatible "
        f"with {related_path} across the exact {relation} edge"
    )


def _context_request_supports_edge(
    requests: Iterable[ReviewContextRequest],
    issues: list[CodeReviewIssue],
    trigger_key: str,
    related_key: str,
    relation: str,
    matched_on: str,
) -> Optional[str]:
    """Bind a Stage 1 cross-file question to one exact enrichment edge."""
    for request in requests:
        if request.kind != "CROSS_FILE":
            continue
        target_key = normalize_repository_path(request.targetPath or "")
        if target_key and target_key != related_key:
            continue
        trusted_origins = {
            normalize_repository_path(path)
            for path in request.originatingPaths
            if normalize_repository_path(path)
        }
        if trusted_origins:
            if trigger_key not in trusted_origins:
                continue
        elif request.relatedIssueIndexes:
            # Compatibility for direct callers that did not pass the host-bound
            # request recorded by Stage 1.
            origins = {
                normalize_repository_path(issues[index].file)
                for index in request.relatedIssueIndexes
                if 0 <= index < len(issues)
            }
            if trigger_key not in origins:
                continue
        relationship = str(request.relationship or "").lower()
        edge_tokens = tuple(
            value.lower()
            for value in (relation, matched_on, trigger_key, related_key)
            if value
        )
        if not target_key and not any(token in relationship for token in edge_tokens):
            continue
        return (
            f"{request.question} Required evidence: {request.requiredEvidence}"
        )
    return None


def build_investigation_tickets(
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
    plan: ReviewPlan,
    stage_1_issues: Iterable[CodeReviewIssue],
    *,
    context_requests: Iterable[ReviewContextRequest] = (),
    max_tickets: int = MAX_TICKETS,
) -> tuple[list[InvestigationTicket], list[str]]:
    """Return admitted tickets and exact-edge proposals that lacked source.

    An enrichment edge alone is insufficient.  Admission also requires a Stage 1
    causal dependency, a path-specific planner hypothesis, or visible contract
    change markers in the changed trigger.
    """

    changed = _changed_files(processed_diff)
    stage_1_issue_list = list(stage_1_issues)
    context_request_list = list(context_requests)
    contents = _content_by_path(request)
    enrichment = getattr(request, "enrichmentData", None)
    admitted: list[InvestigationTicket] = []
    incomplete: list[str] = []
    seen: set[tuple[str, str, str, str]] = set()

    relationships = sorted(
        getattr(enrichment, "relationships", None) or (),
        key=lambda edge: (
            str(getattr(edge, "sourceFile", "") or ""),
            str(getattr(edge, "targetFile", "") or ""),
            _relationship_value(getattr(edge, "relationshipType", "")),
            str(getattr(edge, "matchedOn", "") or ""),
        ),
    )
    for edge in relationships:
        source = str(getattr(edge, "sourceFile", "") or "").strip()
        target = str(getattr(edge, "targetFile", "") or "").strip()
        source_key = normalize_repository_path(source)
        target_key = normalize_repository_path(target)
        relation = _relationship_value(getattr(edge, "relationshipType", ""))
        matched_on = str(getattr(edge, "matchedOn", "") or "").strip()
        if not source_key or not target_key or not relation:
            continue

        orientations: list[tuple[str, str]] = []
        if source_key in changed:
            orientations.append((source_key, target_key))
        if target_key in changed and target_key != source_key:
            orientations.append((target_key, source_key))
        for trigger_key, related_key in orientations:
            identity = (trigger_key, related_key, relation, matched_on)
            if identity in seen:
                continue
            seen.add(identity)
            trigger_file = changed[trigger_key]
            hypothesis = (
                _context_request_supports_edge(
                    context_request_list,
                    stage_1_issue_list,
                    trigger_key,
                    related_key,
                    relation,
                    matched_on,
                )
                or
                _issue_supports_edge(
                    stage_1_issue_list,
                    trigger_key,
                    related_key,
                    matched_on,
                )
                or _plan_supports_edge(
                    plan,
                    trigger_file.path,
                    contents.get(related_key, (related_key, ""))[0],
                    matched_on,
                )
                or _contract_hypothesis(
                    trigger_file,
                    related_key,
                    relation,
                    matched_on,
                )
            )
            if not hypothesis:
                continue

            trigger_content = contents.get(trigger_key)
            related_content = contents.get(related_key)
            if trigger_content is None or related_content is None:
                incomplete.append(
                    f"{trigger_file.path}->{related_key}:{relation}:source_unavailable"
                )
                continue

            canonical = json.dumps(
                {
                    "trigger": trigger_key,
                    "related": related_key,
                    "relation": relation,
                    "matchedOn": matched_on,
                    "hypothesis": hypothesis,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            ticket_id = "X" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
            if len(admitted) >= max_tickets:
                incomplete.append(f"{ticket_id}:ticket_limit_exceeded")
                continue
            admitted.append(InvestigationTicket(
                ticket_id=ticket_id,
                hypothesis=hypothesis,
                trigger_file=trigger_content[0],
                related_file=related_content[0],
                relationship_type=relation,
                matched_on=matched_on,
                trigger_hunks=_bounded_trigger_hunks(trigger_file, matched_on),
                trigger_source=_bounded_source(trigger_content[1], matched_on),
                related_source=_bounded_source(related_content[1], matched_on),
                related_start_line=_symbol_line(related_content[1], matched_on),
            ))
    return admitted, incomplete


def _symbol_line(content: str, symbol: str) -> int:
    if symbol:
        for index, line in enumerate(content.splitlines(), start=1):
            if symbol in line:
                return index
    return 1


def _issue_matches_ticket(issue: CodeReviewIssue, ticket: InvestigationTicket) -> bool:
    if normalize_repository_path(issue.file) != normalize_repository_path(
        ticket.trigger_file
    ):
        return False
    anchor = str(issue.codeSnippet or "").strip()
    if not anchor:
        return False
    return any(
        line.startswith("+")
        and not line.startswith("+++")
        and anchor in line[1:]
        for line in ticket.trigger_hunks.splitlines()
    )


def _context_request_identity(request: ReviewContextRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"originatingPaths"})
    payload["originatingPaths"] = sorted(request.originatingPaths)
    return hashlib.sha256(json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:12]


async def _resolve_requested_tickets(
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
    context_requests: Iterable[ReviewContextRequest],
    resolver: Optional[ExactContextResolver],
    existing: list[InvestigationTicket],
) -> tuple[list[InvestigationTicket], list[str]]:
    """Resolve host-bound changed→unchanged questions absent from Java edges."""
    all_cross_requests = [
        item for item in context_requests
        if item.kind == "CROSS_FILE"
    ]
    cross_requests = [item for item in all_cross_requests if item.originatingPaths]
    unbound = [
        f"XREQ-{_context_request_identity(item)}:origin_unavailable"
        for item in all_cross_requests
        if not item.originatingPaths
    ]
    if not cross_requests:
        return [], unbound
    if resolver is None:
        return [], [
            *unbound,
            *(
                f"XREQ-{_context_request_identity(item)}:exact_resolver_unavailable"
                for item in cross_requests
            ),
        ]

    changed = _changed_files(processed_diff)
    contents = _content_by_path(request)
    existing_pairs = {
        (
            normalize_repository_path(ticket.trigger_file),
            normalize_repository_path(ticket.related_file),
        )
        for ticket in existing
    }
    resolved_tickets: list[InvestigationTicket] = []
    incomplete: list[str] = list(unbound)
    for context_request in cross_requests:
        origin_keys = [
            normalize_repository_path(path)
            for path in context_request.originatingPaths
            if normalize_repository_path(path) in changed
        ]
        if len(origin_keys) != 1:
            incomplete.append(
                f"XREQ-{_context_request_identity(context_request)}:ambiguous_origin"
            )
            continue
        local_request = context_request.model_copy(update={"kind": "LOCAL_EXACT"})
        resolution = await resolver.resolve(
            [local_request],
            originating_paths=context_request.originatingPaths,
        )
        evidence = resolution.resolved[0] if len(resolution.resolved) == 1 else None
        if evidence is None:
            incomplete.append(
                f"XREQ-{_context_request_identity(context_request)}:source_unavailable"
            )
            continue
        origin_key = origin_keys[0]
        related_key = normalize_repository_path(evidence.path)
        if not related_key or origin_key == related_key:
            incomplete.append(
                f"XREQ-{_context_request_identity(context_request)}:invalid_related_path"
            )
            continue
        if (origin_key, related_key) in existing_pairs:
            continue
        trigger_source = contents.get(origin_key)
        if trigger_source is None:
            incomplete.append(
                f"XREQ-{_context_request_identity(context_request)}:trigger_source_unavailable"
            )
            continue
        identity_payload = {
            "origin": origin_key,
            "related": related_key,
            "question": context_request.question,
            "requiredEvidence": context_request.requiredEvidence,
        }
        ticket_id = "X" + hashlib.sha256(json.dumps(
            identity_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()[:12]
        resolved_tickets.append(InvestigationTicket(
            ticket_id=ticket_id,
            hypothesis=(
                f"{context_request.question} Required evidence: "
                f"{context_request.requiredEvidence}"
            ),
            trigger_file=trigger_source[0],
            related_file=evidence.path,
            relationship_type=context_request.relationship or "requested exact edge",
            matched_on=context_request.targetSymbol or "",
            trigger_hunks=_bounded_trigger_hunks(
                changed[origin_key],
                context_request.targetSymbol or "",
            ),
            trigger_source=_bounded_source(
                trigger_source[1],
                context_request.targetSymbol or "",
            ),
            related_source=evidence.content,
            related_start_line=evidence.start_line,
        ))
        existing_pairs.add((origin_key, related_key))
    return resolved_tickets, incomplete


def _prompt(tickets: list[InvestigationTicket], request: ReviewRequestDto) -> str:
    payload = [ticket.compact_payload() for ticket in tickets]
    return """You are verifying bounded cross-file defect hypotheses for a code review.

For each ticket, actively try to disprove the hypothesis using only the exact
current-head source supplied in that ticket. Emit at most one CodeReviewIssue for
the ticket, and set issue.id to the exact ticketId. Omit the ticket when the code
is compatible, the impact is speculative, or a required causal link is absent.

Every emitted issue must:
- anchor file/line/codeSnippet to an exact added or modified line in changedHunks;
- identify triggerCondition, causalPath, and observableImpact;
- use relatedLocations for the unchanged or secondary location;
- describe a concrete post-change failure, not style, praise, optional hardening,
  task coverage, or generic duplication;
- leave lifecycle/resolution fields unset and suggestedFixDiff empty;
- preserve a real defect even if its best category or severity is uncertain.

Return only JSON matching {"issues": [CodeReviewIssue, ...]}.

Repository: %s
Commit: %s
Tickets:
%s
""" % (
        request.projectVcsRepoSlug,
        request.currentCommitHash or request.commitHash or "",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )


def _pack_tickets(tickets: list[InvestigationTicket]) -> list[list[InvestigationTicket]]:
    batches: list[list[InvestigationTicket]] = []
    current: list[InvestigationTicket] = []
    for ticket in tickets:
        candidate = [*current, ticket]
        if current and len(_prompt(candidate, _EMPTY_REQUEST)) > PACK_CHAR_BUDGET:
            batches.append(current)
            current = [ticket]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


class _EmptyRequest:
    projectVcsRepoSlug = ""
    currentCommitHash = ""
    commitHash = ""


_EMPTY_REQUEST = _EmptyRequest()


async def _invoke_once(llm: Any, prompt: str) -> CrossFileInvestigationOutput:
    messages = [
        {
            "role": "system",
            "content": "Return only the requested structured cross-file review JSON.",
        },
        {"role": "user", "content": prompt},
    ]
    if hasattr(llm, "with_structured_output") and supports_structured_output(llm):
        structured = llm.with_structured_output(CrossFileInvestigationOutput)
        return await structured.ainvoke(messages)
    response = await llm.ainvoke(messages)
    content = extract_llm_response_text(response)
    _, data = load_json_with_local_repairs(content)
    return CrossFileInvestigationOutput(**data)


async def run_targeted_cross_file(
    llm: Any,
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
    plan: ReviewPlan,
    stage_1_issues: Iterable[CodeReviewIssue],
    *,
    context_requests: Iterable[ReviewContextRequest] = (),
    exact_context_resolver: Optional[ExactContextResolver] = None,
    followup_budget: Optional[ReviewFollowupBudget] = None,
    available_calls: int = MAX_CALLS,
) -> TargetedCrossFileResult:
    context_request_list = list(context_requests)
    model_call_slots = min(
        max(0, available_calls),
        followup_budget.remaining if followup_budget is not None else MAX_CALLS,
    )
    explicit_requests = [
        item for item in context_request_list if item.kind == "CROSS_FILE"
    ]
    bound_requests = [item for item in explicit_requests if item.originatingPaths]
    unbound_requests = [item for item in explicit_requests if not item.originatingPaths]
    requested_tickets: list[InvestigationTicket] = []
    requested_incomplete: list[str] = []
    if model_call_slots > 0:
        # Stage 1 emitted these falsifiable hypotheses from the actual changed
        # source. Resolve them before filling the fixed ticket cap with generic
        # enrichment relationships; failed exact reads do not expand the cap.
        admitted_bound = bound_requests[:MAX_TICKETS]
        requested_tickets, resolution_incomplete = await _resolve_requested_tickets(
            request,
            processed_diff,
            [*unbound_requests, *admitted_bound],
            exact_context_resolver,
            [],
        )
        requested_incomplete.extend(resolution_incomplete)
        requested_incomplete.extend(
            f"XREQ-{_context_request_identity(item)}:ticket_limit_exceeded"
            for item in bound_requests[MAX_TICKETS:]
        )
    else:
        requested_incomplete.extend(
            f"XREQ-{_context_request_identity(item)}:followup_budget_unavailable"
            for item in explicit_requests
        )

    automatic_tickets, incomplete = build_investigation_tickets(
        request,
        processed_diff,
        plan,
        stage_1_issues,
        context_requests=context_request_list,
    )
    for resolved_ticket in requested_tickets:
        resolved_prefix = (
            f"{resolved_ticket.trigger_file}->"
            f"{normalize_repository_path(resolved_ticket.related_file)}:"
        )
        incomplete = [
            value for value in incomplete
            if not value.startswith(resolved_prefix)
        ]
    requested_pairs = {
        (
            normalize_repository_path(ticket.trigger_file),
            normalize_repository_path(ticket.related_file),
        )
        for ticket in requested_tickets
    }
    automatic_tickets = [
        ticket
        for ticket in automatic_tickets
        if (
            normalize_repository_path(ticket.trigger_file),
            normalize_repository_path(ticket.related_file),
        ) not in requested_pairs
    ]
    automatic_slots = max(0, MAX_TICKETS - len(requested_tickets))
    incomplete.extend(
        f"{ticket.ticket_id}:ticket_limit_exceeded"
        for ticket in automatic_tickets[automatic_slots:]
    )
    tickets = [
        *requested_tickets,
        *automatic_tickets[:automatic_slots],
    ]
    incomplete.extend(requested_incomplete)
    admitted_count = len(tickets)
    bounded_tickets: list[InvestigationTicket] = []
    for ticket in tickets:
        if len(_prompt([ticket], request)) > PACK_CHAR_BUDGET:
            incomplete.append(f"{ticket.ticket_id}:evidence_packet_too_large")
        else:
            bounded_tickets.append(ticket)
    tickets = bounded_tickets
    effective_available = min(
        max(0, available_calls),
        followup_budget.remaining if followup_budget is not None else MAX_CALLS,
    )
    if not tickets or effective_available <= 0:
        return TargetedCrossFileResult(
            candidates=(),
            admitted_tickets=admitted_count,
            calls_used=0,
            incomplete_tickets=tuple(
                [*incomplete, *(ticket.ticket_id for ticket in tickets)]
                if effective_available <= 0
                else incomplete
            ),
        )

    batches = _pack_tickets(tickets)
    selected: list[list[InvestigationTicket]] = []
    for batch in batches[:effective_available]:
        if followup_budget is not None:
            source_key = ",".join(ticket.ticket_id for ticket in batch)
            if not await followup_budget.try_acquire("cross_file", source_key):
                incomplete.extend(ticket.ticket_id for ticket in batch)
                continue
        selected.append(batch)
    for skipped in batches[effective_available:]:
        incomplete.extend(ticket.ticket_id for ticket in skipped)

    async def run_batch(
        batch: list[InvestigationTicket],
    ) -> tuple[list[GeneratedCrossFileCandidate], list[str]]:
        prompt = _prompt(batch, request)
        digest = "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        by_id = {ticket.ticket_id: ticket for ticket in batch}
        try:
            output = await _invoke_once(llm, prompt)
        except Exception as exc:
            logger.warning(
                "Cross-file evidence packet failed and is incomplete: %s",
                exc,
            )
            return [], list(by_id)

        generated: list[GeneratedCrossFileCandidate] = []
        returned: set[str] = set()
        invalid_ticket_ids: set[str] = set()
        for issue in output.issues:
            ticket_id = str(getattr(issue, "id", "") or "").strip()
            ticket = by_id.get(ticket_id)
            if ticket is None or ticket_id in returned:
                logger.warning("Ignoring cross-file issue with unknown/duplicate ticket id %r", ticket_id)
                continue
            if not _issue_matches_ticket(issue, ticket):
                logger.warning(
                    "Ignoring cross-wired cross-file issue for ticket %s",
                    ticket_id,
                )
                invalid_ticket_ids.add(ticket_id)
                continue
            returned.add(ticket_id)
            issue.relatedLocations = list(dict.fromkeys([
                *(getattr(issue, "relatedLocations", None) or ()),
                f"{ticket.related_file}:{ticket.related_start_line}",
            ]))
            generated.append(GeneratedCrossFileCandidate(issue, ticket, digest))
        # A model omission is a disproved/no-finding ticket, not incomplete.
        return generated, sorted(invalid_ticket_ids)

    results = await asyncio.gather(*(run_batch(batch) for batch in selected))
    generated: list[GeneratedCrossFileCandidate] = []
    for candidates, failed in results:
        generated.extend(candidates)
        incomplete.extend(failed)
    return TargetedCrossFileResult(
        candidates=tuple(generated),
        admitted_tickets=admitted_count,
        calls_used=len(selected),
        incomplete_tickets=tuple(dict.fromkeys(incomplete)),
    )
