"""Request-local evidence and verdict ledger, independent of model providers."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from service.review.change_context import resolve_changed_anchor



def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def exact_refs(value: Mapping[str, Any], evidence: Mapping[str, Any]) -> list[str]:
    refs = value.get("evidenceIds") or []
    if not isinstance(refs, list):
        return []
    accepted = []
    for key in refs:
        record = evidence.get(key) if isinstance(key, str) else None
        if not record:
            continue
        result = record["result"]
        if result.get("status") != "ready":
            continue
        if ((record["kind"] == "diff" and result.get("diff"))
                or (record["kind"] in {"readReviewFile", "getStructuralUnit"} and result.get("content"))
                or (record["kind"] == "getReviewDiff" and result.get("parts"))):
            accepted.append(key)
    return list(dict.fromkeys(accepted))


def contains_anchor(issue: Mapping[str, Any], part: Any, evidence: Mapping[str, Any], refs: list[str]) -> bool:
    if part is None or issue.get("file") != part.path:
        return False
    try:
        line = int(issue.get("line"))
    except (TypeError, ValueError, OverflowError):
        return False
    if line not in part.anchors:
        return False
    for key in refs:
        record = evidence[key]
        source = record["result"]
        if record["kind"] == "diff" and source.get("partId") == part.id:
            return True
        if record["kind"] == "getReviewDiff" and any(item.get("id") == part.id for item in source.get("parts", [])):
            return True
        if record["kind"] in {"readReviewFile", "getStructuralUnit"} and source.get("path") == part.path:
            try:
                start, end = int(source.get("startLine") or 0), int(source.get("endLine") or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            if source.get("side") == part.side and start <= line <= end:
                return True
    return False


def source_discovery(value: Any, parts: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    refs = exact_refs(value, evidence)
    resolved = resolve_changed_anchor(value, parts)
    if not refs or resolved is None or not value.get("title") or not value.get("reason"):
        return None
    part, line = resolved
    if not contains_anchor(value, part, evidence, refs):
        return None
    return {"partId": part.id, "file": part.path, "line": line, "codeSnippet": part.anchors[line],
            "severity": str(value.get("severity") or "MEDIUM").upper(),
            "category": str(value.get("category") or "BUG_RISK"), "scope": "LINE",
            "title": str(value["title"]), "reason": str(value["reason"]),
            "suggestedFixDescription": str(value.get("suggestedFixDescription") or ""),
            "_verificationEvidenceIds": refs}


def revise_issue(issue: dict[str, Any], report: Mapping[str, Any]) -> None:
    """Replace report prose without retaining superseded discovery premises."""
    reason = report.get("reason")
    if isinstance(reason, str) and reason.strip() and reason.strip() != issue.get("reason"):
        for field in ("trigger", "failureMechanism", "causalEvidence", "sourceLocations", "evidenceToCheck"):
            issue.pop(field, None)
    for field in ("title", "reason", "suggestedFixDescription"):
        if isinstance(report.get(field), str) and report[field].strip():
            issue[field] = report[field].strip()


class VerificationState:
    def __init__(self, findings: list[dict[str, Any]], investigations: list[dict[str, Any]], parts: Mapping[str, Any]):
        self.candidates = {f"candidate-{index}": dict(issue) for index, issue in enumerate(findings, 1)}
        self.investigations = {str(item.get("id") or f"investigation-{index}"): dict(item)
                               for index, item in enumerate(investigations, 1)}
        self.parts = parts
        self.evidence: dict[str, dict[str, Any]] = {}
        self.pending_visibility: set[str] = set()
        self.visible: set[str] = set()
        self._decision_states: dict[str, set[str]] = {}
        self._answer_states: dict[str, set[str]] = {}
        self.decisions: dict[str, dict[str, Any]] = {}
        self.answers: dict[str, dict[str, Any]] = {}
        self.resolved: set[str] = set()
        self.discoveries: list[dict[str, Any]] = []
        self.rejections: list[str] = []
        self.revision = 0
        self._evidence_keys: dict[str, str] = {}

    @property
    def complete(self) -> bool:
        return self.candidates.keys() <= self.decisions.keys() and self.investigations.keys() <= self.answers.keys()

    def begin_turn(self, delivered=()) -> None:
        self.visible.update(self.pending_visibility | set(delivered))
        self.pending_visibility.clear()

    def add_evidence(self, kind: str, result: dict[str, Any], arguments: Mapping[str, Any] | None = None) -> tuple[str, bool]:
        identity = fingerprint({"kind": kind, "result": result})
        key = self._evidence_keys.get(identity)
        fresh = key is None
        if key is None:
            key = f"read-{len(self._evidence_keys) + 1}"
            self._evidence_keys[identity] = key
            self.evidence[key] = {"kind": kind, "result": result, "arguments": dict(arguments or {})}
        self.pending_visibility.add(key)
        return key, fresh

    def record(self, *, decisions=(), investigations=(), findings=()) -> dict[str, Any]:
        """Record usable items independently; only new outcomes advance work.

        Provenance may improve and later evidence may correct any verdict. The
        revision counts first-seen dispositions/representatives, not permutations
        of citations or wording. New source facts fund continuation separately.
        """
        evidence = {key: self.evidence[key] for key in self.visible}
        rejected: list[str] = []

        def objects(values: Any, field: str) -> list[Mapping[str, Any]]:
            if isinstance(values, Mapping):
                return [values]
            if values is None:
                return []
            if not isinstance(values, (list, tuple)):
                rejected.append(f"{field}: expected a list of records")
                return []
            if any(not isinstance(value, Mapping) for value in values):
                rejected.append(f"{field}: invalid records ignored")
            return [value for value in values if isinstance(value, Mapping)]

        for value in objects(decisions, "decisions"):
            key = str(value.get("candidateId") or "")
            if key not in self.candidates:
                continue
            verdict = str(value.get("verdict") or "").lower()
            reason = str(value.get("reason") or "").strip()
            refs = exact_refs(value, evidence)
            if verdict not in {"keep", "dismiss", "duplicate", "uncertain"} or not reason:
                rejected.append(f"{key}: a verdict and concrete reason are required")
                continue
            if verdict != "uncertain" and not refs:
                rejected.append(f"{key}: cite exact source already observed")
                continue
            if verdict != "uncertain":
                issue = self.candidates[key]
                if not contains_anchor(issue, self.parts.get(str(issue.get("partId") or "")), evidence, refs):
                    rejected.append(f"{key}: supplied evidence does not contain the candidate's changed anchor")
                    continue
            representative = value.get("duplicateOf")
            if verdict == "duplicate" and (not isinstance(representative, str)
                    or representative not in self.candidates or representative == key):
                rejected.append(f"{key}: identify another candidate with the same failure, trigger and fix")
                continue
            decision = {"verdict": verdict, "reason": reason, "evidenceIds": refs}
            if verdict == "duplicate":
                decision["duplicateOf"] = representative
            report = value.get("issue")
            if verdict == "keep" and isinstance(report, Mapping):
                # Verification can repair a partly wrong report without losing
                # its independently demonstrated defect or its original anchor.
                revise_issue(self.candidates[key], report)
            previous = self.decisions.get(key) or {}
            if (previous.get("verdict") == verdict and previous.get("duplicateOf") == decision.get("duplicateOf")
                    and set(previous.get("evidenceIds") or ()) == set(refs)):
                continue  # Explanation-only rewrites do not change the ledger.
            self.decisions[key] = decision
            signature = fingerprint({"verdict": verdict, "duplicateOf": decision.get("duplicateOf")})
            seen = self._decision_states.setdefault(key, set())
            if signature not in seen:
                seen.add(signature)
                self.revision += 1

        for value in objects(investigations, "investigations"):
            key = str(value.get("id") or "")
            if key not in self.investigations:
                continue
            status = value.get("status")
            refs = exact_refs(value, evidence)
            reason = str(value.get("reason") or "").strip()
            if not reason or not isinstance(status, str) or status not in {"resolved", "uncertain"}:
                rejected.append(f"{key}: a resolved/uncertain status and source answer are required")
                continue
            if status == "resolved" and not refs:
                rejected.append(f"{key}: cite source answering the contract question")
                continue
            previous = self.answers.get(key) or {}
            if previous.get("status") == status and set(previous.get("evidenceIds") or ()) == set(refs):
                continue
            self.answers[key] = {"id": key, "status": status, "reason": reason, "evidenceIds": refs}
            if status == "resolved":
                self.resolved.add(key)
            else:
                self.resolved.discard(key)
            seen = self._answer_states.setdefault(key, set())
            if status not in seen:
                seen.add(status)
                self.revision += 1

        for value in objects(findings, "findings"):
            candidate_id = value.get("candidateId")
            original = self.candidates.get(candidate_id) if isinstance(candidate_id, str) else None
            location = ({key: original[key] for key in ("partId", "file", "line")}
                        | {key: value[key] for key in ("partId", "file", "line", "side") if key in value}) if original else {}
            resolved = resolve_changed_anchor(location, self.parts) if original else None
            if original is not None and resolved == (self.parts.get(original.get("partId")), original.get("line")):
                # Only a revision at the original anchor keeps that identity.
                # A conflicting explicit location is a distinct source-backed
                # finding; it must not overwrite and relocate the first report.
                refs = exact_refs(value, evidence)
                if refs and contains_anchor(original, self.parts.get(str(original.get("partId") or "")), evidence, refs):
                    revise_issue(original, value)
                    self.decisions[candidate_id] = {"verdict": "keep", "reason": str(value.get("reason") or original["reason"]), "evidenceIds": refs}
                    signature = fingerprint({"verdict": "keep", "duplicateOf": None})
                    seen = self._decision_states.setdefault(candidate_id, set())
                    if signature not in seen:
                        seen.add(signature)
                        self.revision += 1
                else:
                    rejected.append(f"{candidate_id}: cite observed source containing the candidate anchor")
                continue
            representative = value.get("duplicateOf")
            if representative is not None and not isinstance(representative, str):
                rejected.append("finding: duplicateOf must name an existing candidate")
                continue
            issue = source_discovery(value, self.parts, evidence)
            if issue is None:
                if any(part.path == value.get("file") for part in self.parts.values()):
                    rejected.append("finding: supply an active changed file/line, a concrete report and observed anchor evidence")
                # Wider historical context is readable but is not publication
                # work. Ignoring an out-of-scope suggestion does not undo a
                # completed delta review.
                continue
            if issue not in self.discoveries:
                self.discoveries.append(issue)
                key = f"candidate-{len(self.candidates) + 1}"
                refs = issue["_verificationEvidenceIds"]
                self.candidates[key] = {name: item for name, item in issue.items() if not name.startswith("_")}
                if representative in self.candidates and representative != key:
                    self.decisions[key] = {"verdict": "duplicate", "duplicateOf": representative,
                                           "reason": issue["reason"], "evidenceIds": refs}
                else:
                    self.decisions[key] = {"verdict": "keep", "reason": issue["reason"], "evidenceIds": refs}
                self._decision_states[key] = {fingerprint({
                    "verdict": self.decisions[key]["verdict"],
                    "duplicateOf": self.decisions[key].get("duplicateOf"),
                })}
                # Discovery alone does not fund another turn of the same open
                # investigation. The agent must answer the question or read on.
        self.rejections = rejected
        return {"status": "ready", "acceptedCandidateIds": list(self.decisions),
                "answeredInvestigationIds": list(self.answers), "rejected": rejected,
                "remainingCandidateIds": [key for key in self.candidates if key not in self.decisions],
                "remainingInvestigationIds": [key for key in self.investigations if key not in self.answers]}

