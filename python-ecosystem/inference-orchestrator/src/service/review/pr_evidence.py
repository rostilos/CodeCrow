"""Bounded, deterministic evidence scopes for incremental pull-request review.

The review scope and the PR-state scope intentionally have different jobs:

* the review scope owns model work and publication anchors and is the current
  incremental delta when one exists;
* the PR-state scope is a compact base-to-head ledger used only for PR-wide
  reasoning such as task coverage and cross-file interaction.

The ledger never expands the Stage 1 workload and never sends the complete PR
diff unless it already fits inside the fixed Stage 2 evidence budget.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from utils.diff_processor import (
    DiffChangeType,
    DiffFile,
    DiffHunk,
    HunkDisposition,
    ProcessedDiff,
)


STAGE_2_PR_EVIDENCE_CHAR_BUDGET = 24_000
INCREMENTAL_DELTA_CHAR_BUDGET = 6_000
PERSISTED_TASK_EVIDENCE_CHAR_BUDGET = 2_400
PERSISTED_TASK_EVIDENCE_MAX_ITEMS = 8

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:$\\/-]{2,}")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_STOP_WORDS = {
    "about", "acceptance", "add", "added", "adding", "after", "against", "also",
    "another", "before",
    "being", "branch", "change", "changes", "code", "context",
    "could", "create", "criteria", "description", "does", "enable", "ensure",
    "from", "have", "implement", "implementation", "into", "issue", "must",
    "new", "only", "pull", "request", "requested", "return", "review", "should",
    "support", "task", "that", "their", "then", "there", "these", "they", "this",
    "through", "update", "using", "when", "where", "which", "with", "without",
}
_TASK_COVERAGE_FORWARD_RE = re.compile(
    r"\b(?:pr|pull request|task|requirement|acceptance criteri(?:on|a)|"
    r"requested (?:feature|behavior|functionality|tracking|change))\b"
    r".{0,120}\b(?:missing|omitt(?:ed|ing)|absent|incomplete|"
    r"not implemented|does not implement|fails? to implement)\b",
    re.IGNORECASE | re.DOTALL,
)
_TASK_COVERAGE_REVERSE_RE = re.compile(
    r"\b(?:missing|omitt(?:ed|ing)|absent|incomplete|"
    r"not implemented|does not implement|fails? to implement)\b"
    r".{0,120}\b(?:pr|pull request|task|requirement|acceptance criteri(?:on|a)|"
    r"requested (?:feature|behavior|functionality|tracking|change))\b",
    re.IGNORECASE | re.DOTALL,
)
_TASK_INTENT_FIELDS = (
    "task_summary",
    "taskSummary",
    "summary",
    "title",
    "description",
    "task_description",
    "taskDescription",
    "acceptance_criteria",
    "acceptanceCriteria",
)


@dataclass(frozen=True)
class PrLedgerEvidence:
    """One prompt-visible, stable ledger excerpt."""

    ref: str
    scope: str
    path: str
    hunk_id: str
    line_start: int
    line_end: int
    excerpt: str
    has_removal: bool


@dataclass(frozen=True)
class TaskImplementationEvidence:
    """One structured positive-evidence record returned to the persistence host."""

    evidence_ref: str
    path: str
    hunk_id: str
    line_start: int
    line_end: int
    excerpt: str

    def to_client_dict(self) -> Dict[str, Any]:
        return {
            "evidenceRef": self.evidence_ref,
            "filePath": self.path,
            "hunkId": self.hunk_id,
            "lineStart": self.line_start,
            "lineEnd": self.line_end,
            "excerpt": self.excerpt,
        }


@dataclass(frozen=True)
class PrEvidenceLedger:
    """Fixed-budget evidence passed to Stage 2 and its publication gate."""

    full_pr_context: str
    incremental_delta_context: str
    manifest_complete: bool
    full_evidence_complete: bool
    incremental: bool
    evidence_by_ref: Mapping[str, PrLedgerEvidence]
    delta_removal_refs: frozenset[str]
    delta_hunk_ids: frozenset[str]
    task_terms: tuple[str, ...]
    task_relevant_paths: tuple[str, ...]

    @property
    def prompt_chars(self) -> int:
        return len(self.full_pr_context) + len(self.incremental_delta_context)

    def has_refs(self, refs: Iterable[str]) -> bool:
        normalized = tuple(ref for ref in refs if ref)
        return bool(normalized) and all(ref in self.evidence_by_ref for ref in normalized)

    def has_delta_removal_ref(self, refs: Iterable[str]) -> bool:
        return any(ref in self.delta_removal_refs for ref in refs)

    def task_implementation_evidence_payload(
        self,
        task_key: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Return bounded structured evidence for host-owned database persistence.

        This data is deliberately separate from the human-facing review comment.
        It records positive changed-line evidence rather than an LLM assertion
        that a requirement is complete. Later reviews may use it as supporting
        context, but never as proof that missing behavior exists.
        """
        if not task_key or not self.task_terms:
            return None

        full_pr_evidence = [
            evidence
            for evidence in self.evidence_by_ref.values()
            if evidence.scope == "full_pr"
            and evidence.path in self.task_relevant_paths
        ]
        if not full_pr_evidence:
            return None

        items: list[TaskImplementationEvidence] = []
        used_chars = 0
        for evidence in full_pr_evidence:
            added_lines = [
                line[1:].strip()
                for line in evidence.excerpt.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            ]
            if not added_lines:
                continue
            # A replacement whose task terms exist only in the removed side is
            # a possible regression, not positive implementation evidence.
            if (
                evidence.has_removal
                and not _content_contains_task_term(added_lines, self.task_terms)
            ):
                continue
            compact_excerpt = " | ".join(added_lines)
            compact_excerpt = compact_excerpt[:420]
            if not compact_excerpt:
                continue
            remaining = PERSISTED_TASK_EVIDENCE_CHAR_BUDGET - used_chars
            if remaining <= 0:
                break
            compact_excerpt = compact_excerpt[:remaining]
            items.append(TaskImplementationEvidence(
                evidence_ref=evidence.ref,
                path=evidence.path,
                hunk_id=evidence.hunk_id,
                line_start=evidence.line_start,
                line_end=evidence.line_end,
                excerpt=compact_excerpt,
            ))
            used_chars += len(compact_excerpt)
            if len(items) >= PERSISTED_TASK_EVIDENCE_MAX_ITEMS:
                break

        if not items:
            return None
        return {
            "taskKey": task_key.strip(),
            "source": "DETERMINISTIC_PR_LEDGER",
            "fullEvidenceComplete": self.full_evidence_complete,
            "items": [item.to_client_dict() for item in items],
        }


@dataclass(frozen=True)
class TaskCoverageGateResult:
    kept: tuple[Any, ...]
    rejected: tuple[tuple[Any, str], ...]


def gate_task_coverage_candidates(
    issues: Sequence[Any],
    *,
    incremental: bool,
    task_context: Optional[Dict[str, Any]],
    previous_issue_ids: Iterable[str],
    ledger: PrEvidenceLedger,
) -> TaskCoverageGateResult:
    """Reject task-absence assertions that the visible scope cannot prove.

    This gate is deliberately independent of RAG. A retrieval miss never
    becomes negative evidence, and a model cannot bypass the gate by labelling
    a PR/task omission as a generic defect.
    """
    previous_ids = {
        str(issue_id).strip()
        for issue_id in previous_issue_ids
        if str(issue_id).strip()
    }
    kept: list[Any] = []
    rejected: list[tuple[Any, str]] = []

    for issue in issues:
        issue_id = str(getattr(issue, "id", "") or "").strip()
        if issue_id and issue_id in previous_ids:
            kept.append(issue)
            continue

        declared_scope = str(
            getattr(issue, "findingScope", "CONCRETE_DEFECT")
            or "CONCRETE_DEFECT"
        ).strip().upper()
        coverage_claim = (
            declared_scope == "TASK_COVERAGE_GAP"
            or _looks_like_task_coverage_claim(issue)
        )
        if not coverage_claim:
            kept.append(issue)
            continue

        refs = tuple(
            str(ref).strip()
            for ref in (getattr(issue, "coverageEvidenceRefs", None) or ())
            if str(ref).strip()
        )
        if not task_context:
            rejected.append((issue, "task_context_unavailable"))
            continue
        if not ledger.manifest_complete:
            rejected.append((issue, "full_pr_manifest_incomplete"))
            continue
        if not ledger.has_refs(refs):
            rejected.append((issue, "coverage_evidence_refs_missing_or_unknown"))
            continue

        if incremental:
            regression = bool(getattr(issue, "coverageRegression", False))
            if not regression:
                rejected.append((issue, "new_incremental_omission_claim"))
                continue
            if not ledger.has_delta_removal_ref(refs):
                rejected.append((issue, "delta_removal_evidence_missing"))
                continue
        else:
            if not ledger.full_evidence_complete:
                rejected.append((issue, "full_pr_changed_line_evidence_bounded"))
                continue
            if not all(ref.startswith("PRF") for ref in refs):
                rejected.append((issue, "full_review_requires_pr_evidence_refs"))
                continue

        kept.append(issue)

    return TaskCoverageGateResult(tuple(kept), tuple(rejected))


def _looks_like_task_coverage_claim(issue: Any) -> bool:
    text = "\n".join(
        str(getattr(issue, field, "") or "")
        for field in ("title", "description", "evidence", "business_impact")
    )
    return bool(
        _TASK_COVERAGE_FORWARD_RE.search(text)
        or _TASK_COVERAGE_REVERSE_RE.search(text)
    )


def build_pr_evidence_ledger(
    full_pr_diff: Optional[ProcessedDiff],
    review_diff: Optional[ProcessedDiff],
    *,
    incremental: bool,
    provider_manifest_complete: bool = True,
    task_context: Optional[Dict[str, Any]] = None,
    pr_title: str = "",
    pr_description: str = "",
) -> PrEvidenceLedger:
    """Build both evidence scopes inside one fixed Stage 2 character budget."""
    effective_full_pr_diff = (
        full_pr_diff
        if incremental
        else (full_pr_diff or review_diff)
    )
    task_terms = _extract_task_terms(task_context, pr_title, pr_description)

    delta_budget = INCREMENTAL_DELTA_CHAR_BUDGET if incremental else 0
    full_budget = STAGE_2_PR_EVIDENCE_CHAR_BUDGET - delta_budget

    evidence_by_ref: Dict[str, PrLedgerEvidence] = {}
    full_context, manifest_complete, full_evidence_complete, relevant_paths = (
        _build_scope_context(
            effective_full_pr_diff,
            scope="full_pr",
            ref_prefix="PRF",
            budget=full_budget,
            task_terms=task_terms,
            evidence_by_ref=evidence_by_ref,
            provider_manifest_complete=provider_manifest_complete,
        )
    )

    if incremental:
        delta_context, _, _, _ = _build_scope_context(
            review_diff,
            scope="delta",
            ref_prefix="DELTA",
            budget=delta_budget,
            task_terms=task_terms,
            evidence_by_ref=evidence_by_ref,
            provider_manifest_complete=True,
        )
    else:
        delta_context = (
            "This is a full review. The review scope and full PR state scope are identical."
        )

    delta_removal_refs = frozenset(
        ref
        for ref, evidence in evidence_by_ref.items()
        if (
            evidence.scope == "delta"
            and evidence.has_removal
            and _evidence_removes_task_signal(evidence, task_terms)
        )
    )
    delta_hunk_ids = frozenset(
        evidence.hunk_id
        for evidence in evidence_by_ref.values()
        if evidence.scope == ("delta" if incremental else "full_pr")
    )

    return PrEvidenceLedger(
        full_pr_context=full_context,
        incremental_delta_context=delta_context,
        manifest_complete=manifest_complete,
        full_evidence_complete=full_evidence_complete,
        incremental=incremental,
        evidence_by_ref=dict(evidence_by_ref),
        delta_removal_refs=delta_removal_refs,
        delta_hunk_ids=delta_hunk_ids,
        task_terms=task_terms,
        task_relevant_paths=tuple(sorted(relevant_paths)),
    )


def _build_scope_context(
    processed_diff: Optional[ProcessedDiff],
    *,
    scope: str,
    ref_prefix: str,
    budget: int,
    task_terms: Sequence[str],
    evidence_by_ref: Dict[str, PrLedgerEvidence],
    provider_manifest_complete: bool,
) -> tuple[str, bool, bool, set[str]]:
    heading = (
        "FULL PR STATE LEDGER (base to current PR head)"
        if scope == "full_pr"
        else "CURRENT INCREMENTAL DELTA (publication/review scope)"
    )
    if processed_diff is None or not processed_diff.files:
        return (
            f"{heading}\nNo evidence is available for this scope.",
            False,
            False,
            set(),
        )

    manifest_lines = [_manifest_line(diff_file) for diff_file in processed_diff.files]
    manifest_header = [
        heading,
        (
            f"Files represented: {len(processed_diff.files)}; "
            f"reviewable={processed_diff.total_files}; "
            f"additions=+{processed_diff.total_additions}; "
            f"deletions=-{processed_diff.total_deletions}"
        ),
        "FILE MANIFEST:",
    ]
    manifest_text, rendered_manifest_complete = _fit_lines(
        manifest_header,
        manifest_lines,
        max(1_500, min(budget // 2, 9_000)),
    )
    manifest_complete = (
        bool(provider_manifest_complete) and rendered_manifest_complete
    )
    if not manifest_complete:
        manifest_text += (
            "\nManifest status: INCOMPLETE — PR-wide absence claims are not permitted."
        )
    else:
        manifest_text += "\nManifest status: COMPLETE"

    # Reserve room for the evidence-completeness declaration added below so no
    # registered evidence reference can be truncated out of the actual prompt.
    remaining = max(0, budget - len(manifest_text) - 240)
    all_evidence = _evidence_candidates(
        processed_diff,
        task_terms=task_terms,
    )
    complete_candidate_text, all_evidence_rendered = _render_evidence_candidates(
        all_evidence,
        scope=scope,
        ref_prefix=ref_prefix,
        budget=remaining,
        evidence_by_ref=evidence_by_ref,
    )
    all_reviewable_hunks = sum(
        1
        for diff_file in processed_diff.files
        for hunk in diff_file.hunks
        if hunk.disposition is HunkDisposition.REVIEWABLE
    )
    rendered_scope_evidence = [
        item
        for item in evidence_by_ref.values()
        if item.scope == scope
    ]
    no_compaction = (
        not processed_diff.truncated
        and all(
            not diff_file.skip_reason
            for diff_file in processed_diff.files
            if not diff_file.is_skipped
        )
    )
    missing_text_evidence = any(
        not diff_file.hunks
        and not diff_file.is_skipped
        and diff_file.change_type in {
            DiffChangeType.ADDED,
            DiffChangeType.MODIFIED,
        }
        for diff_file in processed_diff.files
    )
    full_evidence_complete = (
        manifest_complete
        and no_compaction
        and not missing_text_evidence
        and len(rendered_scope_evidence) == all_reviewable_hunks
        and all_evidence_rendered
    )

    if not complete_candidate_text and remaining:
        complete_candidate_text = "No changed source hunks are available in this scope."

    relevant_paths = {
        evidence.path
        for evidence in rendered_scope_evidence
        if evidence.path in all_evidence.task_relevant_paths
    }
    status = (
        "Changed-line evidence status: COMPLETE"
        if full_evidence_complete
        else (
            "Changed-line evidence status: BOUNDED — excerpts are positive supporting "
            "evidence and their absence cannot prove missing behavior."
        )
    )
    context = f"{manifest_text}\n\n{status}\n{complete_candidate_text}".strip()
    return context[:budget], manifest_complete, full_evidence_complete, relevant_paths


@dataclass(frozen=True)
class _EvidenceCandidates:
    ordered: tuple[tuple[int, str, DiffHunk], ...]
    task_relevant_paths: frozenset[str]


def _evidence_candidates(
    processed_diff: ProcessedDiff,
    *,
    task_terms: Sequence[str],
) -> _EvidenceCandidates:
    candidates: list[tuple[int, str, DiffHunk]] = []
    relevant_paths: set[str] = set()
    for diff_file in processed_diff.files:
        if diff_file.is_skipped:
            continue
        for hunk in diff_file.hunks:
            if hunk.disposition is not HunkDisposition.REVIEWABLE:
                continue
            score = _task_relevance_score(diff_file.path, hunk.content, task_terms)
            if score > 0:
                relevant_paths.add(diff_file.path)
            # Task-relevant hunks lead. Stable path/header ordering makes dry-run
            # artifacts deterministic across executions.
            candidates.append((score, diff_file.path, hunk))

    ordered = tuple(sorted(
        candidates,
        key=lambda item: (-item[0], item[1], item[2].header, item[2].id),
    ))
    return _EvidenceCandidates(
        ordered=ordered,
        task_relevant_paths=frozenset(relevant_paths),
    )


def _render_evidence_candidates(
    candidates: _EvidenceCandidates,
    *,
    scope: str,
    ref_prefix: str,
    budget: int,
    evidence_by_ref: Dict[str, PrLedgerEvidence],
) -> tuple[str, bool]:
    if budget <= 0:
        return "", not candidates.ordered

    lines = ["EVIDENCE EXCERPTS:"]
    used = len(lines[0])
    rendered = 0
    all_excerpts_complete = True
    for _, path, hunk in candidates.ordered:
        ref = f"{ref_prefix}{rendered + 1:03d}"
        excerpt, excerpt_complete = _bounded_hunk_excerpt(hunk)
        block = f"[{ref}] {path}\n{excerpt}"
        extra = len(block) + 2
        if used + extra > budget:
            break
        lines.append(block)
        used += extra
        rendered += 1
        all_excerpts_complete = all_excerpts_complete and excerpt_complete
        evidence_by_ref[ref] = PrLedgerEvidence(
            ref=ref,
            scope=scope,
            path=path,
            hunk_id=hunk.id,
            line_start=hunk.new_start,
            line_end=max(hunk.new_start, hunk.new_start + hunk.new_count - 1),
            excerpt=excerpt,
            has_removal=any(
                line.startswith("-") and not line.startswith("---")
                for line in hunk.content.splitlines()
            ),
        )

    fully_rendered = (
        rendered == len(candidates.ordered)
        and all_excerpts_complete
    )
    if rendered < len(candidates.ordered):
        lines.append(
            f"... {len(candidates.ordered) - rendered} additional hunks omitted "
            "by the fixed evidence budget"
        )
    return "\n\n".join(lines), fully_rendered


def _bounded_hunk_excerpt(
    hunk: DiffHunk,
    max_chars: int = 1_200,
) -> tuple[str, bool]:
    lines = [hunk.header]
    lines.extend(
        line[:320]
        for line in hunk.content.splitlines()
        if (
            not line.startswith("@@")
            and line.startswith(("+", "-"))
            and not line.startswith(("+++", "---"))
        )
    )
    complete_excerpt = "\n".join(lines)
    return complete_excerpt[:max_chars], len(complete_excerpt) <= max_chars


def _manifest_line(diff_file: DiffFile) -> str:
    change_type = getattr(diff_file.change_type, "value", str(diff_file.change_type))
    disposition = (
        diff_file.plugin_disposition
        or ("skipped" if diff_file.is_skipped else "reviewable")
    )
    return (
        f"- {change_type.upper()} {diff_file.path} "
        f"(+{diff_file.additions}/-{diff_file.deletions}, {disposition})"
    )


def _fit_lines(
    prefix_lines: Sequence[str],
    item_lines: Sequence[str],
    budget: int,
) -> tuple[str, bool]:
    lines = list(prefix_lines)
    used = len("\n".join(lines))
    included = 0
    for line in item_lines:
        if used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1
        included += 1
    if included < len(item_lines):
        lines.append(f"... {len(item_lines) - included} paths omitted by the manifest budget")
    return "\n".join(lines), included == len(item_lines)


def _task_relevance_score(
    path: str,
    content: str,
    task_terms: Sequence[str],
) -> int:
    if not task_terms:
        return 0
    path_text = path.casefold()
    content_text = content.casefold()
    score = 0
    for term in task_terms:
        if term in path_text:
            score += 6
        occurrences = content_text.count(term)
        score += min(occurrences, 5) * 2
    return score


def _content_contains_task_term(
    lines: Sequence[str],
    task_terms: Sequence[str],
) -> bool:
    content = "\n".join(lines).casefold()
    return any(term in content for term in task_terms)


def _evidence_removes_task_signal(
    evidence: PrLedgerEvidence,
    task_terms: Sequence[str],
) -> bool:
    removed_lines = [
        line[1:].strip()
        for line in evidence.excerpt.splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]
    return bool(removed_lines) and _task_relevance_score(
        evidence.path,
        "\n".join(removed_lines),
        task_terms,
    ) > 0


def _extract_task_terms(
    task_context: Optional[Dict[str, Any]],
    pr_title: str,
    pr_description: str,
) -> tuple[str, ...]:
    values = [pr_title, pr_description]
    if task_context:
        values.extend(
            str(task_context[key])
            for key in _TASK_INTENT_FIELDS
            if task_context.get(key) is not None
        )

    terms: set[str] = set()
    for value in values:
        for token in _TOKEN_RE.findall(value or ""):
            normalized = token.casefold().strip("./\\-_:")
            for candidate in (normalized, *_CAMEL_BOUNDARY_RE.split(token)):
                candidate = candidate.casefold().strip("./\\-_:")
                if (
                    len(candidate) >= 3
                    and candidate not in _STOP_WORDS
                    and not candidate.isdigit()
                ):
                    terms.add(candidate)
    return tuple(sorted(terms, key=lambda term: (-len(term), term))[:80])
