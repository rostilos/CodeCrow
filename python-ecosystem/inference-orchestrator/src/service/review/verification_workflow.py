"""Normalize evidence work before committing source-dependent outcomes.

An explicit source request names its purpose and work. Repeating the same request
as a needs_evidence assessment is unnecessary; a provisional answer cannot close
its own still-requested check. This module has no provider or tool dependencies.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any, Mapping, Sequence

from service.review.verification_state import VerificationState


class VerificationWorkflow:
    def __init__(self, state: VerificationState, *, tool_names: set[str] | None = None):
        self.state = state
        self.tool_names = tool_names

    def apply(self, steps: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        assessments: list[Any] = []
        findings: list[Any] = []
        requests: list[Any] = []
        rejected: list[str] = []
        for step in steps:
            if not isinstance(step, Mapping):
                rejected.append("A review step must be an object")
                continue
            for name, collected in (("assessments", assessments), ("findings", findings),
                                    ("evidenceRequests", requests)):
                value = step.get(name, [])
                if isinstance(value, Mapping) and name != "evidenceRequests":
                    value = [value]
                if not isinstance(value, list):
                    rejected.append(f"{name} must be a list")
                else:
                    collected.extend(value)

        reassessments = {
            value["workId"]: value for value in assessments
            if isinstance(value, Mapping) and isinstance(value.get("workId"), str)
            and self.state.work_target(value["workId"]) is not None
            and isinstance(value.get("verdict"), str) and value["verdict"] in {"confirmed", "refuted", "uncertain", "duplicate", "needs_evidence"}
            and isinstance(value.get("reason"), str) and value["reason"].strip()
        }
        terminal: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for value in assessments:
            if (isinstance(value, Mapping) and isinstance(value.get("workId"), str)
                    and value["workId"] in reassessments
                    and isinstance(value.get("verdict"), str)
                    and value["verdict"] in {"confirmed", "refuted", "uncertain", "duplicate"}
                    and isinstance(value.get("reason"), str) and value["reason"].strip()):
                terminal[value["workId"]].append(value)
        conflicts = {work_id: values for work_id, values in terminal.items()
                     if len({value["verdict"] for value in values}) > 1}
        pending = set(self.state.pending_work_ids())
        normalized = []
        deferred: set[str] = set(conflicts)
        for request in requests:
            if not isinstance(request, Mapping):
                rejected.append("Each evidence request needs workIds, missingFact and calls")
                continue
            ids, fact, calls = request.get("workIds"), request.get("missingFact"), request.get("calls")
            if (not isinstance(ids, list) or not ids or any(not isinstance(key, str) for key in ids)
                    or not isinstance(fact, str) or not fact.strip() or not isinstance(calls, list) or not calls):
                rejected.append("Each evidence request needs nonempty workIds, a concrete missingFact, and calls")
                continue
            usable_calls = []
            for call in calls:
                if (not isinstance(call, Mapping) or not isinstance(call.get("name"), str)
                        or not call["name"] or not isinstance(call.get("arguments"), dict)
                        or self.tool_names is not None and call["name"] not in self.tool_names):
                    rejected.append("Evidence calls must name an available read tool and supply its arguments object")
                else:
                    usable_calls.append(deepcopy(dict(call)))
            if not usable_calls:
                continue
            active = []
            for work_id in dict.fromkeys(ids):
                if self.state.work_target(work_id) is None:
                    rejected.append(f"{work_id}: unknown workId in evidence request")
                elif work_id in pending or work_id in reassessments:
                    active.append(work_id)
                else:
                    rejected.append(f"{work_id}: work already has an outcome; reassess it explicitly before requesting more evidence")
            if active:
                deferred.update(active)
                normalized.append({"workIds": active, "missingFact": fact, "calls": usable_calls})

        # Process the entire submission together. An outcome in an earlier call
        # cannot close work whose missing source is requested in a sibling call.
        for work_id in deferred:
            provisional = reassessments.get(work_id)
            if work_id in conflicts:
                provisional = {"workId": work_id, "conflictingAssessments": conflicts[work_id]}
                rejected.append(f"{work_id}: conflicting outcomes in the same response; reconcile the proposed assessments using observed evidence")
            self.state.defer_assessment(work_id, provisional)
        ready_assessments = []
        for value in assessments:
            work_id = value.get("workId") if isinstance(value, Mapping) else None
            if isinstance(work_id, str):
                if work_id in deferred:
                    continue
                if value.get("verdict") == "needs_evidence" and work_id in terminal:
                    # A provisional status does not contradict a final outcome.
                    # Actual read requests above still defer the final outcome.
                    continue
            ready_assessments.append(value)
        receipt = self.state.apply_assessments(ready_assessments, findings)
        rejected.extend(receipt["rejected"])
        # Preserve the causal question across the assessment -> evidence
        # handoff. Source calls do not need a redundant assessment alongside them.
        evidence_work = set(receipt["evidenceWorkIds"])
        missing_facts = {value["workId"]: value["reason"] for value in ready_assessments
                         if isinstance(value, Mapping) and isinstance(value.get("workId"), str)
                         and value["workId"] in evidence_work
                         and str(value.get("verdict") or "").lower() == "needs_evidence"}
        for request in normalized:
            for work_id in request["workIds"]:
                missing_facts[work_id] = request["missingFact"]
        return {"requests": normalized, "acceptedWorkIds": receipt["acceptedWorkIds"],
                "missingFacts": missing_facts, "evidenceWorkIds": receipt["evidenceWorkIds"],
                "pendingWorkIds": receipt["pendingWorkIds"], "rejected": list(dict.fromkeys(rejected))}
