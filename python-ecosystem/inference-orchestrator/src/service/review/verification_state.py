"""Request-local evidence and verdict ledger, independent of model providers."""
from __future__ import annotations

from copy import deepcopy
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
        matched_source = (record["kind"] == "grepReviewCode" and any(
            isinstance(match, Mapping) and isinstance(match.get("line"), int)
            and isinstance(match.get("text"), str) and match["text"]
            for item in result.get("results", []) if isinstance(item, Mapping)
            for match in item.get("matches", [])))
        if result.get("status") != "ready" and not (matched_source and result.get("status") == "partial"):
            continue
        if (matched_source or (record["kind"] == "diff" and result.get("diff"))
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
        if record["kind"] == "grepReviewCode" and source.get("side") == part.side:
            if any(item.get("path") == part.path and any(
                    match.get("line") == line and isinstance(match.get("text"), str)
                    for match in item.get("matches", [])) for item in source.get("results", [])):
                return True
        if record["kind"] in {"readReviewFile", "getStructuralUnit"} and source.get("path") == part.path:
            try:
                start, end = int(source.get("startLine") or 0), int(source.get("endLine") or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            if source.get("side") == part.side and start <= line <= end:
                return True
    return False


def with_observed_anchor(issue: Mapping[str, Any], part: Any, evidence: Mapping[str, Any], refs: list[str]) -> list[str]:
    """Join an existing candidate's witness to its already-delivered location.

    The model may cite a caller or guard instead of repeating the changed hunk
    that established this candidate. Keep that witness and reuse host-owned
    anchor provenance; never turn missing or invented citations into evidence.
    """
    if not refs or contains_anchor(issue, part, evidence, refs):
        return refs
    for key in exact_refs({"evidenceIds": list(evidence)}, evidence):
        if contains_anchor(issue, part, evidence, [key]):
            return [*refs, key]
    return refs


def source_discovery_problems(value: Mapping[str, Any], parts: Mapping[str, Any],
                              evidence: Mapping[str, Any]) -> list[str]:
    """Describe report repair separately from missing source or an invalid anchor."""
    problems = []
    for field in ("title", "reason"):
        if not value.get(field):
            problems.append(f"issue.{field} is required; supply the missing report field")
    resolved = resolve_changed_anchor(value, parts)
    refs = exact_refs(value, evidence)
    if resolved is None:
        problems.append("issue.file/line must identify an unambiguous active changed anchor; correct the report location")
    if not refs:
        problems.append("issue.evidenceIds must cite exact source already observed; reuse its supplied evidence IDs")
    elif resolved is not None and not contains_anchor(value, resolved[0], evidence, refs):
        problems.append("issue.evidenceIds do not include observed source at the reported changed anchor; cite its diff or source evidence")
    if problems and resolved is not None and refs and contains_anchor(value, resolved[0], evidence, refs):
        problems.append("The changed anchor and source evidence are already established; repair the report fields without rereading source")
    return problems


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
        self._work_targets: dict[str, tuple[str, str]] = {}
        self._work_ids: dict[tuple[str, str], str] = {}
        self._work_representatives: dict[str, str] = {}
        self._work_verdicts: dict[str, str] = {}
        self._work_duplicates: dict[str, str] = {}
        self._pending_issue_corrections: dict[str, list[str]] = {}
        self._pending_issues: dict[str, Any] = {}
        self._pending_assessments: dict[str, dict[str, Any]] = {}
        self._ensure_work_items()

    def _ensure_work_items(self) -> None:
        for kind, values in (("candidate", self.candidates), ("investigation", self.investigations)):
            for key in values:
                target = (kind, key)
                if target not in self._work_ids:
                    work_id = f"work-{len(self._work_ids) + 1}"
                    self._work_ids[target] = work_id
                    self._work_targets[work_id] = target

    def work_target(self, work_id: str) -> tuple[str, str] | None:
        """Resolve the single model-facing ID space without guessing an ID."""
        self._ensure_work_items()
        return self._work_targets.get(work_id)

    def work_items(self) -> list[dict[str, Any]]:
        """Project hypotheses and questions into one stable assessment contract."""
        self._ensure_work_items()
        items = []
        for work_id, (kind, key) in self._work_targets.items():
            source = self.candidates[key] if kind == "candidate" else self.investigations[key]
            item = {name: value for name, value in source.items()
                    if name not in {"id", "candidateId", "codeSnippet", "batchIds", "origin"}
                    and not name.startswith("_")}
            item.update(id=work_id, kind=kind)
            if work_id in self._pending_assessments:
                item["pendingAssessment"] = deepcopy(self._pending_assessments[work_id])
            if work_id in self._pending_issue_corrections:
                item["issueCorrections"] = list(self._pending_issue_corrections[work_id])
            if work_id in self._pending_issues:
                pending = self._pending_issues[work_id]
                item["pendingIssue"] = ({name: value for name, value in pending.items() if not name.startswith("_")}
                                        if isinstance(pending, Mapping) else pending)
            outcome = self.decisions.get(key) if kind == "candidate" else self.answers.get(key)
            if outcome:
                projected = {name: value for name, value in outcome.items() if name != "id"}
                if kind == "candidate":
                    projected["verdict"] = {"keep": "confirmed", "dismiss": "refuted"}.get(
                        str(outcome.get("verdict")), outcome.get("verdict"))
                    representative = outcome.get("duplicateOf")
                    if isinstance(representative, str):
                        projected["duplicateOf"] = self._work_ids.get(("candidate", representative), representative)
                else:
                    projected.pop("status", None)
                    projected["verdict"] = self._work_verdicts.get(work_id,
                        "uncertain" if outcome.get("status") == "uncertain" else "confirmed")
                    if work_id in self._work_duplicates:
                        projected["duplicateOf"] = self._work_duplicates[work_id]
                item["outcome"] = projected
            items.append(item)
        return items

    def pending_work_ids(self) -> list[str]:
        self._ensure_work_items()
        return [work_id for work_id, (kind, key) in self._work_targets.items()
                if key not in (self.decisions if kind == "candidate" else self.answers)
                or work_id in self._pending_issue_corrections]

    def defer_assessment(self, work_id: str, assessment: Mapping[str, Any] | None = None) -> None:
        """Keep a requested source check provisional, including a revised outcome.

        A source request is work still to do. Preserve the proposed answer for
        the next assessment without publishing it or treating uncertainty as a
        completed source check. No text classification is involved.
        """
        target = self.work_target(work_id)
        if target is None:
            return
        kind, key = target
        if assessment is not None:
            self._pending_assessments[work_id] = deepcopy(dict(assessment))
        if kind == "candidate":
            self.decisions.pop(key, None)
        else:
            self.answers.pop(key, None)
            self.resolved.discard(key)
            representative = self._work_representatives.get(work_id)
            if representative in self.candidates:
                # Revisiting the answer also reopens its derived finding. The
                # question's next answer may omit a report, so the finding must
                # retain separate pending work until explicitly reassessed.
                previous = self.decisions.pop(representative, None)
                representative_work = self._work_ids.get(("candidate", representative))
                if representative_work:
                    if previous:
                        provisional = {"workId": representative_work, **deepcopy(previous)}
                        provisional["verdict"] = {"keep": "confirmed", "dismiss": "refuted"}.get(
                            str(provisional.get("verdict")), provisional.get("verdict"))
                        duplicate = provisional.get("duplicateOf")
                        if isinstance(duplicate, str):
                            provisional["duplicateOf"] = self._work_ids.get(("candidate", duplicate), duplicate)
                        self._pending_assessments.setdefault(representative_work, provisional)
                    self._work_verdicts.pop(representative_work, None)
                    self._work_duplicates.pop(representative_work, None)
        self._work_verdicts.pop(work_id, None)
        self._work_duplicates.pop(work_id, None)

    def _prepare_candidate_report(self, key: str, report: dict[str, Any]) -> str | None:
        """Preserve issue identity while proving a corrected publication anchor."""
        if not any(field in report for field in ("file", "line", "partId", "side")):
            return None
        original = self.candidates[key]
        proposed = {**original, **report}
        resolved = resolve_changed_anchor(proposed, self.parts)
        if resolved is not None and resolved == resolve_changed_anchor(original, self.parts):
            return None
        observed = {name: item for name, item in self.evidence.items() if name in self.visible}
        relocated = source_discovery(proposed, self.parts, observed)
        if relocated is None:
            self.decisions.pop(key, None)
            return "corrected issue location needs an unambiguous active changed anchor and observed source proving it"
        revise_issue(original, relocated)
        original.update({name: item for name, item in relocated.items() if not name.startswith("_")})
        report["evidenceIds"] = relocated["_verificationEvidenceIds"]
        return None

    def apply_assessments(self, assessments: Any, findings: Any = ()) -> dict[str, Any]:
        """Apply a structured assessment before the controller retrieves source.

        Acquiring evidence is not a verdict or progress. Its explicit work IDs
        remain pending, while successful sibling outcomes settle independently.
        """
        self._ensure_work_items()
        rejected: list[str] = []
        evidence_work: list[str] = []
        accepted: list[str] = []
        if isinstance(assessments, Mapping):
            assessments = [assessments]
        elif assessments is None:
            assessments = []
        elif not isinstance(assessments, (list, tuple)):
            rejected.append("assessments: expected a list of work outcomes")
            assessments = []
        for value in assessments:
            if not isinstance(value, Mapping):
                rejected.append("assessment: expected an object with workId and verdict")
                continue
            work_id = value.get("workId")
            target = self.work_target(work_id) if isinstance(work_id, str) else None
            if target is None:
                rejected.append(f"{work_id or 'assessment'}: unknown workId; use a supplied work ID")
                continue
            kind, key = target
            verdict = str(value.get("verdict") or "").lower()
            reason = str(value.get("reason") or "").strip()
            if verdict not in {"confirmed", "refuted", "uncertain", "duplicate", "needs_evidence"} or not reason:
                rejected.append(f"{work_id}: a supported verdict and concrete reason are required")
                continue
            if verdict == "needs_evidence":
                if work_id in self.pending_work_ids():
                    evidence_work.append(work_id)
                else:
                    rejected.append(f"{work_id}: work already has an outcome; no additional retrieval is pending")
                continue
            record = {"reason": reason, "evidenceIds": value.get("evidenceIds") or []}
            issue = value.get("issue")
            if "issue" in value and issue is None:
                self._pending_issue_corrections.pop(work_id, None)
                self._pending_issues.pop(work_id, None)
            elif issue is not None and not isinstance(issue, Mapping):
                self._pending_issue_corrections[work_id] = [f"{work_id}: issue must be an object, or null to withdraw this proposed issue"]
                self._pending_issues[work_id] = issue
            issue_patch = issue if isinstance(issue, Mapping) else {}
            if isinstance(issue, Mapping):
                # A formatting repair is a patch to the retained report. Losing
                # its location/citations here turns a missing title into another
                # source investigation even though the evidence is complete.
                pending_issue = self._pending_issues.get(work_id)
                if isinstance(pending_issue, Mapping):
                    issue = {**pending_issue, **issue}
                    if not record["evidenceIds"]:
                        record["evidenceIds"] = pending_issue.get("evidenceIds") or []
            report = {**issue, "reason": issue_patch.get("reason") or reason,
                      "evidenceIds": issue_patch.get("evidenceIds") or record["evidenceIds"]} if isinstance(issue, Mapping) else None
            duplicate = value.get("duplicateOf")
            duplicate_target = self.work_target(duplicate) if isinstance(duplicate, str) else None
            if verdict == "duplicate" and (duplicate_target is None or duplicate == work_id):
                rejected.append(f"{work_id}: duplicateOf must identify another supplied work item")
                continue
            if kind == "candidate":
                decision = {**record, "candidateId": key,
                            "verdict": {"confirmed": "keep", "refuted": "dismiss"}.get(verdict, verdict)}
                if report is not None:
                    if verdict == "confirmed":
                        problem = self._prepare_candidate_report(key, report)
                        if problem:
                            self._pending_issue_corrections[work_id] = [f"{work_id}: {problem}"]
                            self._pending_issues[work_id] = {**self.candidates[key], **report}
                            continue
                        decision["evidenceIds"] = report["evidenceIds"]
                    decision["issue"] = report
                if verdict == "duplicate":
                    assert duplicate_target is not None
                    representative = (duplicate_target[1] if duplicate_target[0] == "candidate"
                                      else self._work_representatives.get(str(duplicate)))
                    if representative is None:
                        rejected.append(f"{work_id}: duplicate representative has no confirmed issue")
                        continue
                    decision["duplicateOf"] = representative
                receipt = self.record(decisions=[decision])
                settled = key in self.decisions and not receipt["rejected"]
                if settled and verdict == "confirmed":
                    self._work_representatives[work_id] = key
                    if report is not None:
                        self._pending_issue_corrections.pop(work_id, None)
                        self._pending_issues.pop(work_id, None)
            else:
                status = "uncertain" if verdict == "uncertain" else "resolved"
                receipt = self.record(investigations=[{**record, "id": key, "status": status}])
                settled = key in self.answers and not receipt["rejected"]
                if report is not None and verdict != "uncertain":
                    # Location must be explicit, or already owned unambiguously
                    # by this question. Never pick an arbitrary changed line.
                    question = self.investigations[key]
                    for field in ("partId", "file", "line", "side"):
                        if field not in report and field in question:
                            report[field] = question[field]
                    previous_candidates = set(self.candidates)
                    representative = self._work_representatives.get(work_id)
                    if representative in self.candidates:
                        # This question's published issue has an identity. A
                        # correction updates it instead of creating a stale
                        # confirmed copy alongside the corrected report.
                        problem = self._prepare_candidate_report(representative, report)
                        finding_receipt = ({"rejected": [f"{work_id}: {problem}"]} if problem else
                            self.record(findings=[{**report, "candidateId": representative}]))
                    else:
                        finding_receipt = self.record(findings=[report])
                    receipt["rejected"].extend(finding_receipt["rejected"])
                    issue_errors = list(finding_receipt["rejected"])
                    created = self.candidates.keys() - previous_candidates
                    if len(created) == 1:
                        self._work_representatives[work_id] = next(iter(created))
                    elif not created and not finding_receipt["rejected"]:
                        observed = {name: item for name, item in self.evidence.items() if name in self.visible}
                        known = source_discovery(report, self.parts, observed)
                        if known is None:
                            messages = [f"{work_id}: {problem}" for problem in
                                        source_discovery_problems(report, self.parts, observed)]
                            receipt["rejected"].extend(messages)
                            issue_errors.extend(messages)
                        else:
                            representative = next((candidate_id for candidate_id, candidate in self.candidates.items()
                                                   if all(candidate.get(name) == item for name, item in known.items()
                                                          if not name.startswith("_"))), None)
                            if representative:
                                self._work_representatives[work_id] = representative
                    if issue_errors:
                        self._pending_issue_corrections[work_id] = issue_errors
                        self._pending_issues[work_id] = dict(report)
                    else:
                        self._pending_issue_corrections.pop(work_id, None)
                        self._pending_issues.pop(work_id, None)
                        representative_work = self._work_ids.get(("candidate", self._work_representatives.get(work_id)))
                        if representative_work:
                            self._pending_assessments.pop(representative_work, None)
            rejected.extend(receipt["rejected"])
            if settled:
                self._pending_assessments.pop(work_id, None)
                accepted.append(work_id)
                self._work_verdicts[work_id] = verdict
                if verdict == "duplicate":
                    self._work_duplicates[work_id] = str(duplicate)
                else:
                    self._work_duplicates.pop(work_id, None)
        if findings:
            receipt = self.record(findings=findings)
            rejected.extend(receipt["rejected"])
        self._ensure_work_items()
        rejected.extend(message for messages in self._pending_issue_corrections.values() for message in messages)
        rejected = list(dict.fromkeys(rejected))
        self.rejections = rejected
        pending = self.pending_work_ids()
        return {"status": "ready", "acceptedWorkIds": list(dict.fromkeys(accepted)),
                "evidenceWorkIds": [key for key in dict.fromkeys(evidence_work) if key in pending],
                "pendingWorkIds": pending, "rejected": rejected}

    @property
    def complete(self) -> bool:
        return (self.candidates.keys() <= self.decisions.keys()
                and self.investigations.keys() <= self.answers.keys()
                and not self._pending_issue_corrections)

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
        evidence = {key: record for key, record in self.evidence.items() if key in self.visible}
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

        # Normalize the unambiguous historical tool mistake: a known question
        # ID submitted in the candidate bucket. Keep this at the compatibility
        # boundary; the active model contract exposes only unified work IDs.
        question_records = objects(investigations, "investigations")
        for value in objects(decisions, "decisions"):
            key = str(value.get("candidateId") or "")
            if key in self.investigations and key not in self.candidates:
                verdict = str(value.get("verdict") or "").lower()
                if verdict in {"keep", "dismiss", "duplicate", "uncertain"}:
                    question_records.append({**value, "id": key,
                                             "status": "uncertain" if verdict == "uncertain" else "resolved"})
                else:
                    rejected.append(f"{key}: a recognized question disposition is required")
                continue
            if key not in self.candidates:
                rejected.append(f"{key or 'decision'}: unknown candidate ID; new findings require their source location and report")
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
                part = self.parts.get(str(issue.get("partId") or ""))
                refs = with_observed_anchor(issue, part, evidence, refs)
                if verdict == "keep" and not contains_anchor(issue, part, evidence, refs):
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
            if verdict == "keep":
                # The verified explanation owns publication. An optional report
                # supplies title/fix corrections, but omitting that duplicate
                # reason field must not preserve a superseded discovery claim.
                verified_report = dict(report) if isinstance(report, Mapping) else {}
                verified_report["reason"] = verified_report.get("reason") or reason
                revise_issue(self.candidates[key], verified_report)
            previous = self.decisions.get(key) or {}
            if (previous.get("verdict") == verdict and previous.get("duplicateOf") == decision.get("duplicateOf")
                    and set(previous.get("evidenceIds") or ()) == set(refs)):
                # Keep the explanation consistent with the publication without
                # treating prose rewrites as another investigation opportunity.
                self.decisions[key] = decision
                continue
            self.decisions[key] = decision
            signature = fingerprint({"verdict": verdict, "duplicateOf": decision.get("duplicateOf")})
            seen = self._decision_states.setdefault(key, set())
            if signature not in seen:
                seen.add(signature)
                self.revision += 1

        for value in question_records:
            key = str(value.get("id") or "")
            if key not in self.investigations:
                rejected.append(f"{key or 'investigation'}: unknown investigation ID")
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
                refs = with_observed_anchor(original, self.parts.get(str(original.get("partId") or "")),
                                            evidence, exact_refs(value, evidence))
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
                    rejected.extend(f"finding: {problem}" for problem in
                                    source_discovery_problems(value, self.parts, evidence))
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

