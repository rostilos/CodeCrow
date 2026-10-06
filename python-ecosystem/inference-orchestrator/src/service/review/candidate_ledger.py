"""Host-owned provenance and terminal accounting for generated review candidates.

The ledger is deliberately independent of language/framework plugins and never
enters an LLM prompt.  It lets the host prove which review unit produced a
candidate and why a candidate did or did not reach publication.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Iterable, Mapping, Optional

from model.output_schemas import CodeReviewIssue


def _normalized_values(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({
        value.strip()
        for value in values
        if isinstance(value, str) and value.strip()
    }))


def _issue_payload(issue: CodeReviewIssue) -> dict:
    if hasattr(issue, "model_dump"):
        return issue.model_dump(mode="json", by_alias=True)
    raise TypeError("candidate ledger accepts CodeReviewIssue instances only")


def _normalized_visible_evidence(
    evidence_by_id: Optional[
        Mapping[str, Iterable[Mapping[str, Any]]]
    ],
) -> dict[str, tuple[dict[str, Any], ...]]:
    normalized: dict[str, dict[str, dict[str, Any]]] = {}
    for raw_evidence_id, facts in (evidence_by_id or {}).items():
        evidence_id = str(raw_evidence_id).strip()
        if not evidence_id:
            continue
        target = normalized.setdefault(evidence_id, {})
        for fact in facts or ():
            if not isinstance(fact, Mapping):
                continue
            payload = dict(fact)
            canonical = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            target[canonical] = payload
    return {
        evidence_id: tuple(facts[key] for key in sorted(facts))
        for evidence_id, facts in sorted(normalized.items())
    }


def generation_prompt_digest(prompt: str) -> str:
    """Return the content identity of the exact prompt shown to a model."""
    if not isinstance(prompt, str) or not prompt:
        raise ValueError("candidate generation prompt is required")
    return "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CandidateRejection:
    gate: str
    code: str


@dataclass
class CandidateRecord:
    candidate_id: str
    stage: str
    source_key: str
    review_unit_ids: tuple[str, ...]
    prompt_hunk_ids: tuple[str, ...]
    generation_prompt_digest: str
    evidence_refs: tuple[str, ...]
    visible_evidence_by_id: dict[str, tuple[dict[str, Any], ...]]
    hypothesis: str = ""
    evidence_gaps: tuple[str, ...] = ()
    assessments: list[dict[str, Any]] = field(default_factory=list)
    anchor_hunk_ids: tuple[str, ...] = ()
    terminal_state: Optional[str] = None
    rejection: Optional[CandidateRejection] = None
    object_ids: set[int] = field(default_factory=set, repr=False)
    consolidated_candidate_ids: set[str] = field(default_factory=set)


class CandidateEvidenceLedger:
    """Deterministic candidate provenance and publication/rejection ledger."""

    def __init__(self) -> None:
        self._records: dict[str, CandidateRecord] = {}
        self._candidate_by_object_id: dict[int, str] = {}

    def register(
        self,
        issue: CodeReviewIssue,
        *,
        stage: str,
        source_key: str,
        review_unit_ids: Iterable[str],
        prompt_hunk_ids: Iterable[str],
        generation_prompt: Optional[str] = None,
        prompt_digest: Optional[str] = None,
        visible_evidence_by_id: Optional[
            Mapping[str, Iterable[Mapping[str, Any]]]
        ] = None,
    ) -> str:
        normalized_stage = str(stage).strip()
        normalized_source_key = str(source_key).strip()
        if not normalized_stage or not normalized_source_key:
            raise ValueError("candidate stage and source key are required")

        units = _normalized_values(review_unit_ids)
        hunks = _normalized_values(prompt_hunk_ids)
        evidence_refs = _normalized_values(
            getattr(issue, "evidenceRefs", None) or ()
        )
        visible_evidence = _normalized_visible_evidence(
            visible_evidence_by_id
        )
        if generation_prompt is not None and prompt_digest is not None:
            raise ValueError(
                "provide either a generation prompt or its digest, not both"
            )
        if generation_prompt is not None:
            resolved_prompt_digest = generation_prompt_digest(
                generation_prompt
            )
        else:
            resolved_prompt_digest = str(prompt_digest or "")
            if (
                not resolved_prompt_digest.startswith("sha256:")
                or len(resolved_prompt_digest) != 71
                or any(
                    character not in "0123456789abcdef"
                    for character in resolved_prompt_digest[7:]
                )
            ):
                raise ValueError(
                    "candidate generation prompt digest is required"
                )
        canonical = json.dumps(
            {
                "stage": normalized_stage,
                "sourceKey": normalized_source_key,
                "reviewUnitIds": units,
                "promptHunkIds": hunks,
                "generationPromptDigest": resolved_prompt_digest,
                "visibleEvidence": visible_evidence,
                "issue": _issue_payload(issue),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        candidate_id = "sha256:" + hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        if candidate_id in self._records:
            raise RuntimeError(
                "duplicate generated candidate identity: " + candidate_id
            )

        object_id = id(issue)
        if object_id in self._candidate_by_object_id:
            raise RuntimeError("issue object was registered as a candidate twice")
        record = CandidateRecord(
            candidate_id=candidate_id,
            stage=normalized_stage,
            source_key=normalized_source_key,
            review_unit_ids=units,
            prompt_hunk_ids=hunks,
            generation_prompt_digest=resolved_prompt_digest,
            evidence_refs=evidence_refs,
            visible_evidence_by_id=visible_evidence,
            hypothesis=str(getattr(issue, "hypothesis", "") or issue.reason)[:1600],
            evidence_gaps=_normalized_values(getattr(issue, "evidenceGaps", ()) or ()),
            assessments=[{"verdict": "unknown", "reason": "Candidate has not been independently verified"}],
            object_ids={object_id},
        )
        self._records[candidate_id] = record
        self._candidate_by_object_id[object_id] = candidate_id
        return candidate_id

    def transfer(
        self,
        source: CodeReviewIssue,
        target: CodeReviewIssue,
    ) -> bool:
        """Bind a reconciled replacement object to the original candidate."""
        candidate_id = self._candidate_by_object_id.get(id(source))
        if candidate_id is None:
            return False
        target_id = id(target)
        existing = self._candidate_by_object_id.get(target_id)
        if existing is not None and existing != candidate_id:
            raise RuntimeError("reconciled issue is bound to another candidate")
        self._candidate_by_object_id[target_id] = candidate_id
        self._records[candidate_id].object_ids.add(target_id)
        return True

    def record_for(self, issue: CodeReviewIssue) -> Optional[CandidateRecord]:
        candidate_id = self._candidate_by_object_id.get(id(issue))
        return self._records.get(candidate_id) if candidate_id else None

    def confirm_anchor_hunks(
        self,
        issue: CodeReviewIssue,
        hunk_ids: Iterable[str],
    ) -> None:
        record = self.record_for(issue)
        if record is None:
            raise RuntimeError("cannot confirm hunks for an unregistered candidate")
        record.anchor_hunk_ids = _normalized_values(hunk_ids)

    def retain_evidence_refs(
        self,
        issue: CodeReviewIssue,
        evidence_refs: Iterable[str],
    ) -> None:
        """Record the subset of generated citations retained by host gates."""
        record = self.record_for(issue)
        if record is None:
            raise RuntimeError(
                "cannot update evidence refs for an unregistered candidate"
            )
        retained = _normalized_values(evidence_refs)
        if not set(retained).issubset(record.evidence_refs):
            raise RuntimeError(
                "candidate evidence refs can only be narrowed after generation"
            )
        record.evidence_refs = retained

    def assess(self, issue: CodeReviewIssue, assessment: Mapping[str, Any]) -> None:
        """Record verification outcome without interpreting unavailable evidence as false."""
        record = self.record_for(issue)
        if record is not None:
            record.assessments.append(dict(assessment))

    def consolidate(self, retained: CodeReviewIssue, sources: Iterable[CodeReviewIssue]) -> None:
        """Keep original generation receipts when several findings become one."""
        record = self.record_for(retained)
        if record is None:
            return
        for source in sources:
            source_record = self.record_for(source)
            if source_record is not None and source_record is not record:
                record.consolidated_candidate_ids.add(source_record.candidate_id)
                record.consolidated_candidate_ids.update(source_record.consolidated_candidate_ids)
        record.consolidated_candidate_ids.discard(record.candidate_id)
        if record.consolidated_candidate_ids:
            record.assessments.append({"stage": "deduplication", "verdict": "unknown",
                "reason": "Consolidated evidence needs assessment as a whole",
                "reasonCode": "consolidated_evidence", "attempted": False, "completed": False})

    def verification_summary(self) -> dict[str, Any]:
        """Report published-candidate evidence coverage, separately from transport."""
        records = [record for record in self._records.values() if record.terminal_state == "published"]
        counts = {"candidates": len(records), "attempted": 0, "completed": 0,
                  "supported": 0, "disproven": 0, "unknown": 0, "incomplete": 0}
        reasons: dict[str, int] = {}
        for record in records:
            assessments = record.assessments
            for index, assessment in enumerate(assessments):
                if assessment.get("reasonCode") == "consolidated_evidence":
                    assessments = record.assessments[index:]
            counts["attempted"] += any(a.get("attempted") is True for a in assessments)
            completed = [a for a in assessments if a.get("completed") is True]
            counts["completed"] += bool(completed)
            counts["incomplete"] += not bool(completed)
            supported = [a for a in completed if a.get("verdict") in {"supported", "disproven"}]
            last = (supported or completed or assessments)[-1] if assessments else {}
            verdict = last.get("verdict", "unknown")
            counts[verdict if verdict in {"supported", "disproven"} else "unknown"] += 1
            if verdict == "unknown":
                reason = str(last.get("reasonCode") or "not_assessed")
                reasons[reason] = reasons.get(reason, 0) + 1
        return {**counts, "reasonCounts": dict(sorted(reasons.items()))}

    def absorb_optional(self, other: "CandidateEvidenceLedger", *, failure: Optional[str] = None) -> None:
        """Merge isolated supplemental discovery, closing partial failed output."""
        for candidate_id, record in other._records.items():
            if failure and record.terminal_state is None:
                record.terminal_state = "rejected"
                record.rejection = CandidateRejection("specialist_discovery", failure)
            self._records.setdefault(candidate_id, record)
            if record.terminal_state != "rejected":
                for object_id in record.object_ids:
                    self._candidate_by_object_id[object_id] = candidate_id

    def reject(self, issue: CodeReviewIssue, *, gate: str, code: str) -> bool:
        record = self.record_for(issue)
        if record is None:
            return False
        normalized_gate = str(gate).strip()
        normalized_code = str(code).strip()
        if not normalized_gate or not normalized_code:
            raise ValueError("candidate rejection gate and code are required")
        if record.terminal_state == "published":
            raise RuntimeError(
                f"published candidate {record.candidate_id} cannot be rejected"
            )
        rejection = CandidateRejection(normalized_gate, normalized_code)
        if record.rejection is not None and record.rejection != rejection:
            raise RuntimeError(
                f"candidate {record.candidate_id} has conflicting rejection reasons"
            )
        record.terminal_state = "rejected"
        record.rejection = rejection
        return True

    def reject_removed(
        self,
        before: Iterable[CodeReviewIssue],
        after: Iterable[CodeReviewIssue],
        *,
        gate: str,
        code: str,
    ) -> None:
        retained = {id(issue) for issue in after}
        for issue in before:
            if id(issue) not in retained:
                self.reject(issue, gate=gate, code=code)

    def publish(self, issues: Iterable[CodeReviewIssue]) -> None:
        for issue in issues:
            record = self.record_for(issue)
            if record is None:
                continue
            if record.terminal_state == "rejected":
                raise RuntimeError(
                    f"rejected candidate {record.candidate_id} reached publication"
                )
            record.terminal_state = "published"

    def assert_terminal(self) -> None:
        incomplete = sorted(
            record.candidate_id
            for record in self._records.values()
            if record.terminal_state not in {"published", "rejected"}
        )
        if incomplete:
            raise RuntimeError(
                "generated candidates have no terminal publication state: "
                + ", ".join(incomplete)
            )

    def hunk_receipts(
        self,
        reviewable_hunks: Iterable[tuple[str, str]],
    ) -> list[dict[str, Any]]:
        """Return exact per-hunk generation/publication accounting."""
        records = tuple(self._records.values())
        receipts: list[dict[str, Any]] = []
        for hunk_id, path in sorted(reviewable_hunks):
            prompt_candidates = sorted(
                record.candidate_id
                for record in records
                if hunk_id in record.prompt_hunk_ids
            )
            anchored_records = tuple(
                record
                for record in records
                if hunk_id in record.anchor_hunk_ids
            )
            anchored_candidates = sorted(
                record.candidate_id for record in anchored_records
            )
            published_candidates = sorted(
                record.candidate_id
                for record in anchored_records
                if record.terminal_state == "published"
            )
            rejected_candidates = sorted(
                record.candidate_id
                for record in anchored_records
                if record.terminal_state == "rejected"
            )
            outcome = (
                "published"
                if published_candidates
                else "rejected"
                if rejected_candidates
                else "no_anchored_candidate"
            )
            receipts.append({
                "hunkId": hunk_id,
                "path": path,
                "promptCandidateIds": prompt_candidates,
                "anchoredCandidateIds": anchored_candidates,
                "publishedCandidateIds": published_candidates,
                "rejectedCandidateIds": rejected_candidates,
                "outcome": outcome,
            })
        return receipts

    def summary(self) -> dict:
        records = sorted(
            self._records.values(),
            key=lambda record: record.candidate_id,
        )
        rejection_counts: dict[str, int] = {}
        for record in records:
            if record.rejection is None:
                continue
            key = f"{record.rejection.gate}:{record.rejection.code}"
            rejection_counts[key] = rejection_counts.get(key, 0) + 1
        return {
            "generated": len(records),
            "published": sum(
                record.terminal_state == "published" for record in records
            ),
            "rejected": sum(
                record.terminal_state == "rejected" for record in records
            ),
            "rejectionCounts": dict(sorted(rejection_counts.items())),
            "verification": self.verification_summary(),
            "records": [
                {
                    "candidateId": record.candidate_id,
                    "stage": record.stage,
                    "sourceKey": record.source_key,
                    "hypothesis": record.hypothesis,
                    "evidenceGaps": list(record.evidence_gaps),
                    "verificationAssessments": list(record.assessments),
                    "consolidatedCandidateIds": sorted(record.consolidated_candidate_ids),
                    "reviewUnitIds": list(record.review_unit_ids),
                    "promptHunkIds": list(record.prompt_hunk_ids),
                    "generationPromptDigest": (
                        record.generation_prompt_digest
                    ),
                    "anchorHunkIds": list(record.anchor_hunk_ids),
                    "evidenceRefs": list(record.evidence_refs),
                    "visibleEvidenceIds": sorted(
                        record.visible_evidence_by_id
                    ),
                    "visibleEvidenceFactDigests": {
                        evidence_id: [
                            "sha256:" + hashlib.sha256(
                                json.dumps(
                                    fact,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                    default=str,
                                ).encode("utf-8")
                            ).hexdigest()
                            for fact in facts
                        ]
                        for evidence_id, facts in sorted(
                            record.visible_evidence_by_id.items()
                        )
                    },
                    "terminalState": record.terminal_state,
                    "rejection": (
                        {
                            "gate": record.rejection.gate,
                            "code": record.rejection.code,
                        }
                        if record.rejection is not None
                        else None
                    ),
                }
                for record in records
            ],
        }
