from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .evaluation import evaluate_dataset


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exception:
        raise ValueError(f"cannot read evidence artifact {path}") from exception
    return digest.hexdigest()


def _sha256_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("captured diff must be a string or null")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _changed_line_count(capture: Mapping[str, Any]) -> int:
    raw_diff = capture["request"].get("rawDiff")
    if not isinstance(raw_diff, str) or not raw_diff:
        raise ValueError(
            "paired candidate evaluation requires a non-empty complete raw diff"
        )
    return sum(
        1
        for line in raw_diff.splitlines()
        if (
            (line.startswith("+") and not line.startswith("+++"))
            or (line.startswith("-") and not line.startswith("---"))
        )
    )


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _non_negative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _non_negative_number(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative number")
    return float(value)


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{field} must be a list of non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} must not contain duplicates")
    return value


def _ground_truth(
    value: Any,
    *,
    case_id: str,
) -> tuple[Mapping[str, Any], list[str], list[str]]:
    if not isinstance(value, dict):
        raise ValueError(f"case {case_id}: groundTruth must be an object")
    if value.get("status") != "complete":
        raise ValueError(f"case {case_id}: ground truth is not complete")
    if value.get("scope") != "complete-fixed-diff":
        raise ValueError(
            f"case {case_id}: ground truth must cover the complete fixed diff"
        )
    if value.get("candidateOutputsHiddenDuringDefectInventory") is not True:
        raise ValueError(
            f"case {case_id}: expected defects must be inventoried independently "
            "before candidate outputs are inspected"
        )
    for field in ("adjudicator", "adjudicatedAt", "method", "sourceIdentityDigest"):
        _non_empty_string(value.get(field), f"case {case_id} groundTruth {field}")

    raw_defects = value.get("expectedDefects")
    if not isinstance(raw_defects, list):
        raise ValueError(
            f"case {case_id}: groundTruth expectedDefects must be a list"
        )
    expected: list[str] = []
    defect_files: list[str] = []
    for index, raw_defect in enumerate(raw_defects, start=1):
        if not isinstance(raw_defect, dict):
            raise ValueError(
                f"case {case_id}: expected defect {index} must be an object"
            )
        expected.append(
            _non_empty_string(
                raw_defect.get("id"),
                f"case {case_id} expected defect {index} id",
            )
        )
        defect_files.append(
            _non_empty_string(
                raw_defect.get("file"),
                f"case {case_id} expected defect {index} file",
            )
        )
        line = raw_defect.get("line")
        if line is not None and (
            not isinstance(line, int) or isinstance(line, bool) or line < 1
        ):
            raise ValueError(
                f"case {case_id} expected defect {index} line must be "
                "a positive integer or null"
            )
        _non_empty_string(
            raw_defect.get("summary"),
            f"case {case_id} expected defect {index} summary",
        )
    if len(expected) != len(set(expected)):
        raise ValueError(f"case {case_id}: expected defect ids must be unique")
    return value, expected, defect_files


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exception:
        raise ValueError(f"cannot read JSON from {path}: {exception}") from exception
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _validate_capture(payload: Mapping[str, Any], path: Path) -> None:
    if payload.get("kind") != "review-quality-candidate-capture":
        raise ValueError(f"{path}: unsupported capture kind")
    if payload.get("status") != "completed":
        raise ValueError(f"{path}: capture must be completed")

    request = payload.get("request")
    if not isinstance(request, dict):
        raise ValueError(f"{path}: capture request is missing")
    if payload.get("requestDigest") != _digest(request):
        raise ValueError(f"{path}: request digest mismatch")

    result = payload.get("result")
    if payload.get("resultDigest") != _digest(result):
        raise ValueError(f"{path}: result digest mismatch")

    calls = payload.get("calls")
    if not isinstance(calls, list):
        raise ValueError(f"{path}: calls must be a list")
    provider_calls = 0
    for index, call in enumerate(calls, start=1):
        if not isinstance(call, dict) or call.get("status") != "completed":
            raise ValueError(f"{path}: call {index} must be completed")
        count = _non_negative_int(
            call.get("providerCallCount"),
            f"{path}: call {index} providerCallCount",
        )
        if count < 1:
            raise ValueError(f"{path}: call {index} has no provider call")
        provider_calls += count
        if call.get("responseDigest") != _digest(call.get("response")):
            raise ValueError(f"{path}: call {index} response digest mismatch")
    if payload.get("providerCalls") != provider_calls:
        raise ValueError(f"{path}: provider call count mismatch")
    if payload.get("modelBoundaryInvocations") != len(calls):
        raise ValueError(f"{path}: model-boundary invocation count mismatch")

    plugin_identity = payload.get("pluginIdentity")
    if not isinstance(plugin_identity, dict):
        raise ValueError(f"{path}: plugin identity is missing")
    if plugin_identity.get("status") != "resolved":
        raise ValueError(
            f"{path}: plugin identity is unresolved; capture an explicit "
            "empty selection for fallback mode"
        )
    _string_list(
        plugin_identity.get("repositoryPlugins"),
        f"{path}: repositoryPlugins",
    )
    if plugin_identity.get("descriptorMatch") is not True:
        raise ValueError(f"{path}: selected plugin descriptor does not match runtime")
    for field in (
        "selectionFingerprint",
        "requestDescriptorFingerprint",
        "runtimeDescriptorFingerprint",
        "implementationFingerprint",
    ):
        _non_empty_string(plugin_identity.get(field), f"{path}: {field}")

    _non_empty_string(
        payload.get("reviewRuntimeFingerprint"),
        f"{path}: reviewRuntimeFingerprint",
    )
    _non_empty_string(payload.get("modeIdentity"), f"{path}: modeIdentity")
    _capture_hunk_counts(payload, path)

    digest_payload = dict(payload)
    digest_payload["captureDigest"] = None
    if payload.get("captureDigest") != _digest(digest_payload):
        raise ValueError(f"{path}: capture digest mismatch")


def _capture_hunk_counts(
    capture: Mapping[str, Any],
    path: Path,
) -> tuple[int, int]:
    if capture.get("pipelineEvidenceStatus") != "complete":
        raise ValueError(
            f"{path}: capture has no complete terminal pipeline evidence"
        )
    evidence = capture.get("pipelineEvidence")
    if not isinstance(evidence, dict):
        raise ValueError(f"{path}: terminal pipeline evidence is missing")
    if capture.get("pipelineEvidenceDigest") != _digest(evidence):
        raise ValueError(f"{path}: terminal pipeline evidence digest mismatch")
    if evidence.get("state") != "review_evidence_completed":
        raise ValueError(f"{path}: terminal pipeline evidence state is invalid")

    coverage = evidence.get("hunkCoverage")
    if not isinstance(coverage, dict):
        raise ValueError(f"{path}: terminal hunk coverage is missing")
    counts: dict[str, int] = {}
    for state in (
        "ingested",
        "planned",
        "reviewed",
        "validated",
        "completed",
        "excluded",
    ):
        value = coverage.get(state)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"{path}: terminal hunk coverage {state!r} is invalid"
            )
        counts[state] = value
    if any(counts[state] for state in ("ingested", "planned", "reviewed", "validated")):
        raise ValueError(f"{path}: terminal hunk coverage is incomplete")

    review_units = evidence.get("reviewUnits")
    if not isinstance(review_units, dict):
        raise ValueError(f"{path}: terminal review-unit evidence is missing")
    registered = review_units.get("registered")
    completed_units = review_units.get("completed")
    if (
        not isinstance(registered, int)
        or isinstance(registered, bool)
        or registered < 0
        or not isinstance(completed_units, int)
        or isinstance(completed_units, bool)
        or completed_units < 0
        or registered != completed_units
    ):
        raise ValueError(f"{path}: terminal review-unit evidence is incomplete")

    candidates = evidence.get("candidates")
    if not isinstance(candidates, dict):
        raise ValueError(f"{path}: terminal candidate evidence is missing")
    candidate_counts: dict[str, int] = {}
    for field in ("generated", "published", "rejected"):
        value = candidates.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{path}: terminal candidate evidence is invalid")
        candidate_counts[field] = value
    if (
        candidate_counts["published"] + candidate_counts["rejected"]
        != candidate_counts["generated"]
    ):
        raise ValueError(f"{path}: terminal candidate evidence is incomplete")
    records = candidates.get("records")
    if (
        not isinstance(records, list)
        or len(records) != candidate_counts["generated"]
    ):
        raise ValueError(f"{path}: terminal candidate records are incomplete")
    observed_ids: set[str] = set()
    computed_rejections: dict[str, int] = {}
    prompt_digests_by_stage: dict[str, set[str]] = {}
    calls = capture.get("calls")
    if not isinstance(calls, list):
        raise ValueError(f"{path}: capture calls are missing")
    for call in calls:
        if not isinstance(call, dict):
            continue
        stage = str(call.get("stage") or "")
        rendered = call.get("renderedPrompt")
        if stage and isinstance(rendered, str):
            prompt_digests_by_stage.setdefault(stage, set()).add(
                "sha256:"
                + hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            )
    for record in records:
        if not isinstance(record, dict):
            raise ValueError(f"{path}: terminal candidate record is invalid")
        candidate_id = record.get("candidateId")
        if (
            not isinstance(candidate_id, str)
            or not candidate_id.startswith("sha256:")
            or len(candidate_id) != 71
            or any(character not in "0123456789abcdef" for character in candidate_id[7:])
            or candidate_id in observed_ids
        ):
            raise ValueError(f"{path}: terminal candidate identity is invalid")
        observed_ids.add(candidate_id)
        if record.get("stage") not in {"stage_1", "stage_2"}:
            raise ValueError(f"{path}: terminal candidate stage is invalid")
        generation_prompt_digest = record.get("generationPromptDigest")
        if (
            not isinstance(generation_prompt_digest, str)
            or not generation_prompt_digest.startswith("sha256:")
            or len(generation_prompt_digest) != 71
            or any(
                character not in "0123456789abcdef"
                for character in generation_prompt_digest[7:]
            )
        ):
            raise ValueError(
                f"{path}: terminal candidate generation prompt is invalid"
            )
        if generation_prompt_digest not in prompt_digests_by_stage.get(
            str(record.get("stage") or ""),
            set(),
        ):
            raise ValueError(
                f"{path}: terminal candidate is not bound to a captured prompt"
            )
        terminal_state = record.get("terminalState")
        if terminal_state not in {"published", "rejected"}:
            raise ValueError(f"{path}: terminal candidate state is invalid")
        for field in (
            "reviewUnitIds",
            "promptHunkIds",
            "anchorHunkIds",
            "evidenceRefs",
            "visibleEvidenceIds",
        ):
            values = record.get(field)
            if (
                not isinstance(values, list)
                or values != sorted(set(values))
                or any(not isinstance(value, str) or not value for value in values)
            ):
                raise ValueError(
                    f"{path}: terminal candidate provenance is invalid"
                )
        fact_digests = record.get("visibleEvidenceFactDigests")
        if (
            not isinstance(fact_digests, dict)
            or set(fact_digests) != set(record["visibleEvidenceIds"])
            or any(
                not isinstance(digests, list)
                or digests != sorted(set(digests))
                or any(
                    not isinstance(digest, str)
                    or not digest.startswith("sha256:")
                    or len(digest) != 71
                    or any(
                        character not in "0123456789abcdef"
                        for character in digest[7:]
                    )
                    for digest in digests
                )
                for digests in fact_digests.values()
            )
        ):
            raise ValueError(
                f"{path}: terminal candidate visible evidence facts are invalid"
            )
        if (
            terminal_state == "published"
            and not set(record["evidenceRefs"]).issubset(
                record["visibleEvidenceIds"]
            )
        ):
            raise ValueError(
                f"{path}: published candidate cites evidence outside its "
                "generation prompt"
            )
        rejection = record.get("rejection")
        if terminal_state == "published":
            if rejection is not None:
                raise ValueError(
                    f"{path}: published candidate has rejection evidence"
                )
        else:
            if (
                not isinstance(rejection, dict)
                or not isinstance(rejection.get("gate"), str)
                or not rejection.get("gate")
                or not isinstance(rejection.get("code"), str)
                or not rejection.get("code")
            ):
                raise ValueError(
                    f"{path}: rejected candidate has no rejection evidence"
                )
            key = f"{rejection['gate']}:{rejection['code']}"
            computed_rejections[key] = computed_rejections.get(key, 0) + 1
    if [record["candidateId"] for record in records] != sorted(observed_ids):
        raise ValueError(
            f"{path}: terminal candidate records are not deterministic"
        )
    if candidates.get("rejectionCounts") != dict(sorted(computed_rejections.items())):
        raise ValueError(
            f"{path}: terminal candidate rejection counts are inconsistent"
        )

    hunk_receipts = evidence.get("hunkReceipts")
    if (
        not isinstance(hunk_receipts, list)
        or len(hunk_receipts) != counts["completed"]
    ):
        raise ValueError(
            f"{path}: terminal hunk receipts do not match completed hunks"
        )
    records_by_id = {
        record["candidateId"]: record for record in records
    }
    receipt_hunk_ids: list[str] = []
    for receipt in hunk_receipts:
        if not isinstance(receipt, dict):
            raise ValueError(f"{path}: terminal hunk receipt is invalid")
        hunk_id = receipt.get("hunkId")
        if (
            not isinstance(hunk_id, str)
            or not hunk_id
            or not isinstance(receipt.get("path"), str)
            or not receipt.get("path")
        ):
            raise ValueError(f"{path}: terminal hunk receipt is invalid")
        receipt_hunk_ids.append(hunk_id)
        expected_prompt = sorted(
            candidate_id
            for candidate_id, record in records_by_id.items()
            if hunk_id in record["promptHunkIds"]
        )
        expected_anchored = sorted(
            candidate_id
            for candidate_id, record in records_by_id.items()
            if hunk_id in record["anchorHunkIds"]
        )
        expected_published = sorted(
            candidate_id
            for candidate_id in expected_anchored
            if records_by_id[candidate_id]["terminalState"] == "published"
        )
        expected_rejected = sorted(
            candidate_id
            for candidate_id in expected_anchored
            if records_by_id[candidate_id]["terminalState"] == "rejected"
        )
        expected_outcome = (
            "published"
            if expected_published
            else "rejected"
            if expected_rejected
            else "no_anchored_candidate"
        )
        if (
            receipt.get("promptCandidateIds") != expected_prompt
            or receipt.get("anchoredCandidateIds") != expected_anchored
            or receipt.get("publishedCandidateIds") != expected_published
            or receipt.get("rejectedCandidateIds") != expected_rejected
            or receipt.get("outcome") != expected_outcome
        ):
            raise ValueError(
                f"{path}: terminal hunk receipt conflicts with candidate records"
            )
    if (
        receipt_hunk_ids != sorted(receipt_hunk_ids)
        or len(receipt_hunk_ids) != len(set(receipt_hunk_ids))
    ):
        raise ValueError(
            f"{path}: terminal hunk receipts are not deterministic"
        )

    retrieval = evidence.get("retrieval")
    if not isinstance(retrieval, dict):
        raise ValueError(f"{path}: terminal retrieval evidence is missing")
    deterministic_states = retrieval.get("deterministicStates")
    if not isinstance(deterministic_states, list) or any(
        not isinstance(state, str) or not state
        for state in deterministic_states
    ):
        raise ValueError(f"{path}: deterministic retrieval evidence is invalid")
    if registered > 0 and not deterministic_states:
        raise ValueError(f"{path}: deterministic retrieval evidence is missing")
    if any(state != "complete" for state in deterministic_states):
        raise ValueError(f"{path}: deterministic retrieval evidence is incomplete")
    semantic_failures = retrieval.get("semanticFailures")
    exact_evidence_ids = retrieval.get("exactEvidenceIds")
    if (
        not isinstance(semantic_failures, int)
        or isinstance(semantic_failures, bool)
        or semantic_failures != 0
        or not isinstance(exact_evidence_ids, int)
        or isinstance(exact_evidence_ids, bool)
        or exact_evidence_ids < 0
        or not isinstance(retrieval.get("semanticDisabled"), bool)
    ):
        raise ValueError(f"{path}: terminal retrieval evidence is degraded")

    # Excluded/generated/malformed hunks are terminal but are not reviewable.
    # The host ledger reaches this event only after every reviewable hunk moves
    # to ``completed``.
    return counts["completed"], counts["completed"]


def _source_identity(capture: Mapping[str, Any]) -> dict[str, Any]:
    request = capture["request"]
    pull_request_id = request.get("pullRequestId")
    if not isinstance(pull_request_id, int) or isinstance(pull_request_id, bool):
        raise ValueError("paired candidate evaluation requires a pull-request capture")
    base_commit = _non_empty_string(
        request.get("baseCommitHash"),
        "capture request baseCommitHash",
    )
    head_commit = request.get("currentCommitHash") or request.get("commitHash")
    head_commit = _non_empty_string(head_commit, "capture request currentCommitHash")
    return {
        "projectId": request.get("projectId"),
        "workspace": request.get("projectVcsWorkspace"),
        "repository": request.get("projectVcsRepoSlug"),
        "pullRequestId": pull_request_id,
        "sourceBranch": request.get("sourceBranchName"),
        "targetBranch": request.get("targetBranchName")
        or request.get("branch"),
        "baseCommit": base_commit,
        "headCommit": head_commit,
        "previousCommit": request.get("previousCommitHash"),
        "analysisMode": request.get("analysisMode"),
        "rawDiffSha256": _sha256_text(request.get("rawDiff")),
        "deltaDiffSha256": _sha256_text(request.get("deltaDiff")),
        "changedFiles": sorted(
            _string_list(request.get("changedFiles", []), "changedFiles")
        ),
        "deletedFiles": sorted(
            _string_list(request.get("deletedFiles", []), "deletedFiles")
        ),
    }


def _model_settings_identity(capture: Mapping[str, Any]) -> dict[str, Any]:
    request = capture["request"]
    return {
        "provider": capture.get("provider"),
        "model": capture.get("model"),
        "requestProvider": request.get("aiProvider"),
        "requestModel": request.get("aiModel"),
        "baseUrl": request.get("aiBaseUrl"),
        "customParameters": request.get("aiCustomParameters"),
        "maxAllowedTokens": request.get("maxAllowedTokens"),
        "useMcpTools": request.get("useMcpTools"),
        "projectRules": request.get("projectRules"),
    }


def _unwrap_issues(result: Any) -> list[Mapping[str, Any]]:
    current = result
    for _ in range(4):
        if not isinstance(current, dict):
            break
        issues = current.get("issues")
        if isinstance(issues, list):
            if any(not isinstance(issue, dict) for issue in issues):
                raise ValueError("capture result issues must contain JSON objects")
            return issues
        current = current.get("result")
    raise ValueError("capture result does not contain an issues list")


def _finding_projection(issue: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "digest": _digest(issue),
        "file": issue.get("file"),
        "line": issue.get("line"),
        "title": issue.get("title") or issue.get("reason"),
        "category": issue.get("category") or issue.get("type"),
        "severity": issue.get("severity"),
        "verdict": "UNLABELED",
        "expectedId": None,
    }


def _usage_pair(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, dict):
        return None
    input_value = value.get("input_tokens", value.get("prompt_tokens"))
    output_value = value.get("output_tokens", value.get("completion_tokens"))
    if input_value is not None and output_value is not None:
        return (
            _non_negative_int(input_value, "input token usage"),
            _non_negative_int(output_value, "output token usage"),
        )

    for key in ("usage_metadata", "token_usage", "usage"):
        pair = _usage_pair(value.get(key))
        if pair is not None:
            return pair
    llm_output = value.get("llm_output")
    if isinstance(llm_output, dict):
        for key in ("token_usage", "usage"):
            pair = _usage_pair(llm_output.get(key))
            if pair is not None:
                return pair
    for key in ("response_metadata", "generations", "message", "messages"):
        nested = value.get(key)
        values = nested if isinstance(nested, list) else [nested]
        for item in values:
            if isinstance(item, list):
                for child in item:
                    pair = _usage_pair(child)
                    if pair is not None:
                        return pair
            else:
                pair = _usage_pair(item)
                if pair is not None:
                    return pair
    return None


def _capture_usage(capture: Mapping[str, Any]) -> tuple[int, int]:
    total_input = 0
    total_output = 0
    for call_index, call in enumerate(capture["calls"], start=1):
        count = call["providerCallCount"]
        events = call.get("providerEvents")
        if events:
            if not isinstance(events, list) or len(events) != count:
                raise ValueError(
                    f"call {call_index} provider events do not account for every call"
                )
            pairs: list[tuple[int, int]] = []
            for event in events:
                if not isinstance(event, dict) or event.get("status") != "completed":
                    raise ValueError(
                        f"call {call_index} has provider activity without billable usage"
                    )
                pair = _usage_pair(event.get("response"))
                if pair is None:
                    raise ValueError(
                        f"call {call_index} provider event has no actual token usage"
                    )
                pairs.append(pair)
        else:
            if count != 1:
                raise ValueError(
                    f"call {call_index} cannot attribute usage across {count} calls"
                )
            pair = _usage_pair(call.get("response"))
            if pair is None:
                raise ValueError(
                    f"call {call_index} response has no actual token usage"
                )
            pairs = [pair]
        total_input += sum(pair[0] for pair in pairs)
        total_output += sum(pair[1] for pair in pairs)
    return total_input, total_output


def _provider_response_digests(
    capture: Mapping[str, Any],
) -> list[str]:
    """Return every underlying completed provider-event response digest."""
    result: list[str] = []
    calls = capture.get("calls")
    if not isinstance(calls, list):
        return result
    for call in calls:
        if not isinstance(call, dict):
            continue
        events = call.get("providerEvents")
        if not isinstance(events, list):
            continue
        for event in events:
            if (
                isinstance(event, dict)
                and event.get("status") == "completed"
                and "response" in event
            ):
                result.append(_digest(event.get("response")))
    return result


def create_template(
    *,
    case_id: str,
    languages: Sequence[str],
    frameworks: Sequence[str],
    captures: Sequence[tuple[str, Path]],
    baseline: str,
) -> dict[str, Any]:
    _non_empty_string(case_id, "caseId")
    language_values = _string_list(list(languages), "languages")
    if not language_values:
        raise ValueError("languages must not be empty")
    framework_values = _string_list(list(frameworks), "frameworks")
    modes: list[dict[str, Any]] = []
    seen_modes: set[str] = set()
    source_identity_digest: str | None = None
    changed_lines: int | None = None
    reviewable: int | None = None
    terminal: int | None = None
    for mode, capture_path in captures:
        _non_empty_string(mode, "mode")
        if mode in seen_modes:
            raise ValueError(f"duplicate mode {mode!r}")
        seen_modes.add(mode)
        capture = _load_json(capture_path)
        _validate_capture(capture, capture_path)
        current_reviewable, current_terminal = _capture_hunk_counts(
            capture,
            capture_path,
        )
        current_source_identity_digest = _digest(_source_identity(capture))
        current_changed_lines = _changed_line_count(capture)
        if source_identity_digest is None:
            source_identity_digest = current_source_identity_digest
            changed_lines = current_changed_lines
            reviewable = current_reviewable
            terminal = current_terminal
        elif current_source_identity_digest != source_identity_digest:
            raise ValueError("template captures are not the same immutable PR")
        elif current_changed_lines != changed_lines:
            raise ValueError("template captures change the complete diff line count")
        elif (
            current_reviewable != reviewable
            or current_terminal != terminal
        ):
            raise ValueError("template captures have different terminal hunk coverage")
        modes.append({
            "mode": mode,
            "capture": str(capture_path),
            "cost": None,
            "costEvidence": {
                "status": "unverified",
                "currency": "USD",
                "source": "",
                "costUsd": None,
                "verifiedBy": "",
                "verifiedAt": "",
                "artifact": None,
                "responseCosts": [
                    {
                        "responseDigest": digest,
                        "jsonPointer": "",
                        "costUsd": None,
                    }
                    for digest in _provider_response_digests(capture)
                ],
                "sourceDigest": None,
            },
            "findings": [
                _finding_projection(issue)
                for issue in _unwrap_issues(capture.get("result"))
            ],
        })
    if baseline not in seen_modes:
        raise ValueError(f"baseline mode {baseline!r} is absent")
    return {
        "baseline": baseline,
        "cases": [{
            "caseId": case_id,
            "languages": language_values,
            "frameworks": framework_values,
            "groundTruth": {
                "status": "unreviewed",
                "scope": "complete-fixed-diff",
                "candidateOutputsHiddenDuringDefectInventory": False,
                "adjudicator": "",
                "adjudicatedAt": "",
                "method": "",
                "sourceIdentityDigest": source_identity_digest,
                "expectedDefects": [],
            },
            "reviewableHunks": reviewable,
            "terminalHunks": terminal,
            "changedLines": changed_lines,
            "modes": modes,
        }],
    }


def bind_ground_truth(
    manifest: Mapping[str, Any],
    inventory: Mapping[str, Any],
    *,
    base_dir: Path,
) -> dict[str, Any]:
    """Bind a candidate-blind certified inventory to one paired capture template."""
    bound = copy.deepcopy(dict(manifest))
    cases = bound.get("cases")
    if not isinstance(cases, list) or len(cases) != 1:
        raise ValueError(
            "ground-truth binding requires a single-case capture template"
        )
    case = cases[0]
    if not isinstance(case, dict):
        raise ValueError("ground-truth binding case must be an object")
    case_id = _non_empty_string(case.get("caseId"), "caseId")

    if inventory.get("kind") != "review-quality-ground-truth-inventory":
        raise ValueError("unsupported ground-truth inventory kind")
    if inventory.get("status") != "complete":
        raise ValueError(
            "ground-truth inventory must be independently certified as complete"
        )
    if inventory.get("scope") != "complete-fixed-diff":
        raise ValueError("ground-truth inventory must cover the complete fixed diff")
    if inventory.get("candidateOutputsHiddenDuringDefectInventory") is not True:
        raise ValueError(
            "ground-truth inventory must be completed before candidate outputs "
            "are inspected"
        )
    if inventory.get("caseId") != case_id:
        raise ValueError("ground-truth inventory caseId does not match template")

    existing = case.get("groundTruth")
    if (
        not isinstance(existing, dict)
        or existing.get("status") != "unreviewed"
        or existing.get("expectedDefects") != []
        or existing.get("candidateOutputsHiddenDuringDefectInventory") is not False
    ):
        raise ValueError(
            "ground-truth binding refuses an already edited capture template"
        )
    source_identity_digest = _non_empty_string(
        existing.get("sourceIdentityDigest"),
        "template groundTruth sourceIdentityDigest",
    )

    certification = inventory.get("certification")
    if not isinstance(certification, dict):
        raise ValueError("ground-truth inventory certification must be an object")
    adjudicator = _non_empty_string(
        certification.get("adjudicator"),
        "ground-truth inventory adjudicator",
    )
    adjudicated_at = _non_empty_string(
        certification.get("adjudicatedAt"),
        "ground-truth inventory adjudicatedAt",
    )
    method = _non_empty_string(
        certification.get("method"),
        "ground-truth inventory method",
    )

    expected_defects = inventory.get("expectedDefects")
    if not isinstance(expected_defects, list):
        raise ValueError("ground-truth inventory expectedDefects must be a list")
    projected_defects: list[dict[str, Any]] = []
    defect_ids: set[str] = set()
    for index, value in enumerate(expected_defects, start=1):
        if not isinstance(value, dict):
            raise ValueError(
                f"ground-truth inventory defect {index} must be an object"
            )
        defect_id = _non_empty_string(
            value.get("id"),
            f"ground-truth inventory defect {index} id",
        )
        if defect_id in defect_ids:
            raise ValueError("ground-truth inventory defect ids must be unique")
        defect_ids.add(defect_id)
        defect_file = _non_empty_string(
            value.get("file"),
            f"ground-truth inventory defect {index} file",
        )
        line = value.get("line")
        if line is not None and (
            not isinstance(line, int) or isinstance(line, bool) or line < 1
        ):
            raise ValueError(
                f"ground-truth inventory defect {index} line must be a "
                "positive integer or null"
            )
        summary = _non_empty_string(
            value.get("summary"),
            f"ground-truth inventory defect {index} summary",
        )
        projected = {
            "id": defect_id,
            "file": defect_file,
            "line": line,
            "summary": summary,
        }
        if "evidenceFiles" in value:
            projected["evidenceFiles"] = _string_list(
                value.get("evidenceFiles"),
                f"ground-truth inventory defect {index} evidenceFiles",
            )
        projected_defects.append(projected)

    declared_base = _non_empty_string(
        inventory.get("baseCommit"),
        "ground-truth inventory baseCommit",
    )
    declared_head = _non_empty_string(
        inventory.get("headCommit"),
        "ground-truth inventory headCommit",
    )
    declared_diff = _non_empty_string(
        inventory.get("rawDiffSha256"),
        "ground-truth inventory rawDiffSha256",
    )
    declared_files = sorted(
        _string_list(
            inventory.get("changedFiles"),
            "ground-truth inventory changedFiles",
        )
    )
    if any(defect["file"] not in declared_files for defect in projected_defects):
        raise ValueError(
            "ground-truth inventory defects must reference changed files"
        )

    modes = case.get("modes")
    if not isinstance(modes, list) or len(modes) < 2:
        raise ValueError("ground-truth binding requires paired capture modes")
    source_identity: dict[str, Any] | None = None
    for mode in modes:
        if not isinstance(mode, dict):
            raise ValueError("ground-truth binding modes must be objects")
        capture_value = _non_empty_string(
            mode.get("capture"),
            "ground-truth binding capture path",
        )
        capture_path = Path(capture_value)
        if not capture_path.is_absolute():
            capture_path = base_dir / capture_path
        capture = _load_json(capture_path)
        _validate_capture(capture, capture_path)
        current_identity = _source_identity(capture)
        if source_identity is None:
            source_identity = current_identity
        elif current_identity != source_identity:
            raise ValueError(
                "ground-truth binding captures are not the same immutable PR"
            )

    if source_identity is None:
        raise ValueError("ground-truth binding has no capture source identity")
    if _digest(source_identity) != source_identity_digest:
        raise ValueError(
            "capture template source identity digest does not match captures"
        )
    if source_identity["baseCommit"] != declared_base:
        raise ValueError("ground-truth inventory baseCommit does not match capture")
    if source_identity["headCommit"] != declared_head:
        raise ValueError("ground-truth inventory headCommit does not match capture")
    if source_identity["rawDiffSha256"] != declared_diff:
        raise ValueError("ground-truth inventory rawDiffSha256 does not match capture")
    if source_identity["changedFiles"] != declared_files:
        raise ValueError("ground-truth inventory changedFiles do not match capture")

    case["groundTruth"] = {
        "status": "complete",
        "scope": "complete-fixed-diff",
        "candidateOutputsHiddenDuringDefectInventory": True,
        "adjudicator": adjudicator,
        "adjudicatedAt": adjudicated_at,
        "method": method,
        "sourceIdentityDigest": source_identity_digest,
        "inventoryDigest": _digest(inventory),
        "expectedDefects": projected_defects,
    }
    return bound


def merge_templates(templates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not templates:
        raise ValueError("at least one template is required")
    baseline: str | None = None
    cases: list[Mapping[str, Any]] = []
    case_ids: set[str] = set()
    for index, template in enumerate(templates, start=1):
        current_baseline = _non_empty_string(
            template.get("baseline"),
            f"template {index} baseline",
        )
        if baseline is None:
            baseline = current_baseline
        elif current_baseline != baseline:
            raise ValueError(
                f"template {index} changes baseline from {baseline!r} "
                f"to {current_baseline!r}"
            )
        raw_cases = template.get("cases")
        if not isinstance(raw_cases, list) or not raw_cases:
            raise ValueError(f"template {index} cases must be a non-empty list")
        for raw_case in raw_cases:
            if not isinstance(raw_case, dict):
                raise ValueError(f"template {index} cases must contain objects")
            case_id = _non_empty_string(
                raw_case.get("caseId"),
                f"template {index} caseId",
            )
            if case_id in case_ids:
                raise ValueError(f"duplicate caseId {case_id!r}")
            case_ids.add(case_id)
            cases.append(raw_case)
    return {
        "baseline": baseline,
        "cases": cases,
    }


def bind_cost_evidence(
    manifest: Mapping[str, Any],
    *,
    base_dir: Path,
) -> dict[str, Any]:
    """Bind declared mode costs to billing files or response-cost projections.

    This command computes hashes only. It deliberately does not change
    verification status, reviewer identity, or timestamp.
    """
    bound = copy.deepcopy(dict(manifest))
    raw_cases = bound.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases must be a non-empty list")
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise ValueError("each case must be an object")
        case_id = _non_empty_string(raw_case.get("caseId"), "caseId")
        raw_modes = raw_case.get("modes")
        if not isinstance(raw_modes, list) or not raw_modes:
            raise ValueError(f"case {case_id}: modes must be a non-empty list")
        for raw_mode in raw_modes:
            if not isinstance(raw_mode, dict):
                raise ValueError(f"case {case_id}: modes must contain objects")
            mode = _non_empty_string(raw_mode.get("mode"), "mode")
            cost = _non_negative_number(
                raw_mode.get("cost"),
                f"case {case_id}/{mode} cost",
            )
            evidence = raw_mode.get("costEvidence")
            if not isinstance(evidence, dict):
                raise ValueError(
                    f"case {case_id}/{mode}: costEvidence must be an object"
                )
            source = evidence.get("source")
            evidence["costUsd"] = cost
            if source == "provider-billing":
                artifact = _non_empty_string(
                    evidence.get("artifact"),
                    f"case {case_id}/{mode} billing artifact",
                )
                artifact_path = Path(artifact)
                if not artifact_path.is_absolute():
                    artifact_path = base_dir / artifact_path
                if not artifact_path.is_file():
                    raise ValueError(
                        f"case {case_id}/{mode}: billing artifact does not exist"
                    )
                evidence["sourceDigest"] = _sha256_file(artifact_path)
            elif source == "provider-response":
                response_costs = evidence.get("responseCosts")
                if not isinstance(response_costs, list) or not response_costs:
                    raise ValueError(
                        f"case {case_id}/{mode}: responseCosts must be non-empty"
                    )
                evidence["sourceDigest"] = _digest(response_costs)
            else:
                raise ValueError(
                    f"case {case_id}/{mode}: source must be provider-billing "
                    "or provider-response"
                )
    return bound


def evaluate_capture_manifest(
    manifest: Mapping[str, Any],
    *,
    base_dir: Path,
) -> dict[str, Any]:
    baseline = _non_empty_string(manifest.get("baseline"), "baseline")
    raw_cases = manifest.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases must be a non-empty list")

    evaluation_cases: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise ValueError("each case must be an object")
        case_id = _non_empty_string(raw_case.get("caseId"), "caseId")
        languages = _string_list(raw_case.get("languages"), "languages")
        if not languages:
            raise ValueError(f"case {case_id}: languages must not be empty")
        frameworks = _string_list(raw_case.get("frameworks", []), "frameworks")
        if "expected" in raw_case:
            raise ValueError(
                f"case {case_id}: use independently certified groundTruth, "
                "not a bare expected list"
            )
        ground_truth, expected, expected_defect_files = _ground_truth(
            raw_case.get("groundTruth"),
            case_id=case_id,
        )
        expected_set = set(expected)
        reviewable = _non_negative_int(
            raw_case.get("reviewableHunks"),
            "reviewableHunks",
        )
        terminal = _non_negative_int(
            raw_case.get("terminalHunks"),
            "terminalHunks",
        )
        changed_lines = _non_negative_int(
            raw_case.get("changedLines"),
            "changedLines",
        )
        if terminal > reviewable:
            raise ValueError(f"case {case_id}: terminal hunks exceed reviewable hunks")
        raw_modes = raw_case.get("modes")
        if not isinstance(raw_modes, list) or len(raw_modes) < 2:
            raise ValueError(f"case {case_id}: at least two paired modes are required")

        source_digest: str | None = None
        settings_digest: str | None = None
        mode_identities: set[str] = set()
        seen_modes: set[str] = set()
        for raw_mode in raw_modes:
            if not isinstance(raw_mode, dict):
                raise ValueError(f"case {case_id}: each mode must be an object")
            mode = _non_empty_string(raw_mode.get("mode"), "mode")
            if mode in seen_modes:
                raise ValueError(f"case {case_id}: duplicate mode {mode!r}")
            seen_modes.add(mode)
            capture_value = _non_empty_string(raw_mode.get("capture"), "capture")
            capture_path = Path(capture_value)
            if not capture_path.is_absolute():
                capture_path = base_dir / capture_path
            capture = _load_json(capture_path)
            _validate_capture(capture, capture_path)
            capture_reviewable, capture_terminal = _capture_hunk_counts(
                capture,
                capture_path,
            )
            if (
                capture_reviewable != reviewable
                or capture_terminal != terminal
            ):
                raise ValueError(
                    f"case {case_id}/{mode}: declared hunk coverage does not "
                    "match terminal capture evidence"
                )
            if _changed_line_count(capture) != changed_lines:
                raise ValueError(
                    f"case {case_id}/{mode}: changedLines does not match "
                    "the complete captured diff"
                )

            current_source_digest = _digest(_source_identity(capture))
            current_settings_digest = _digest(_model_settings_identity(capture))
            if source_digest is None:
                source_digest = current_source_digest
                settings_digest = current_settings_digest
            elif current_source_digest != source_digest:
                raise ValueError(
                    f"case {case_id}: mode {mode!r} is not the same immutable PR"
                )
            elif current_settings_digest != settings_digest:
                raise ValueError(
                    f"case {case_id}: mode {mode!r} changes provider/model settings"
                )

            mode_identity = capture["modeIdentity"]
            if mode_identity in mode_identities:
                raise ValueError(
                    f"case {case_id}: modes do not identify distinct review/plugin runtimes"
                )
            mode_identities.add(mode_identity)

            issues = _unwrap_issues(capture.get("result"))
            issues_by_digest = {_digest(issue): issue for issue in issues}
            if len(issues_by_digest) != len(issues):
                raise ValueError(
                    f"case {case_id}/{mode}: capture contains duplicate findings"
                )
            raw_findings = raw_mode.get("findings")
            if not isinstance(raw_findings, list):
                raise ValueError(f"case {case_id}/{mode}: findings must be a list")
            supplied_digests = [
                finding.get("digest")
                for finding in raw_findings
                if isinstance(finding, dict)
            ]
            if len(supplied_digests) != len(raw_findings):
                raise ValueError(f"case {case_id}/{mode}: invalid finding entry")
            if set(supplied_digests) != set(issues_by_digest):
                raise ValueError(
                    f"case {case_id}/{mode}: labels do not exactly cover capture findings"
                )

            published: list[str] = []
            matched_expected: set[str] = set()
            for finding in raw_findings:
                digest = finding["digest"]
                verdict = finding.get("verdict")
                expected_id = finding.get("expectedId")
                if verdict == "TP":
                    expected_id = _non_empty_string(
                        expected_id,
                        f"case {case_id}/{mode} TP expectedId",
                    )
                    if expected_id not in expected_set:
                        raise ValueError(
                            f"case {case_id}/{mode}: TP {digest} references "
                            f"unknown expectedId {expected_id!r}"
                        )
                    if expected_id in matched_expected:
                        raise ValueError(
                            f"case {case_id}/{mode}: multiple findings match "
                            f"expectedId {expected_id!r}"
                        )
                    matched_expected.add(expected_id)
                    published.append(expected_id)
                elif verdict == "FP":
                    if expected_id not in (None, ""):
                        raise ValueError(
                            f"case {case_id}/{mode}: FP must not have expectedId"
                        )
                    published.append(f"false-positive:{digest}")
                else:
                    raise ValueError(
                        f"case {case_id}/{mode}: finding {digest} is not labeled TP or FP"
                    )

            input_tokens, output_tokens = _capture_usage(capture)
            plugins = capture["pluginIdentity"]["repositoryPlugins"]
            evaluation_cases.append({
                "caseId": case_id,
                "mode": mode,
                "plugins": plugins,
                "languages": languages,
                "frameworks": frameworks,
                "expected": expected,
                "published": published,
                "abstained": [],
                "reviewableHunks": reviewable,
                "terminalHunks": terminal,
                "changedLines": changed_lines,
                "modelCalls": capture["providerCalls"],
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "cost": _non_negative_number(
                    raw_mode.get("cost"),
                    f"case {case_id}/{mode} cost",
                ),
            })
            provenance.append({
                "caseId": case_id,
                "mode": mode,
                "captureFile": capture_path.name,
                "captureDigest": capture["captureDigest"],
                "repositoryPlugins": list(
                    capture["pluginIdentity"]["repositoryPlugins"]
                ),
                "sourceIdentityDigest": source_digest,
                "modelSettingsDigest": settings_digest,
                "modeIdentity": mode_identity,
                "reviewRuntimeFingerprint": capture["reviewRuntimeFingerprint"],
                "pluginImplementationFingerprint": (
                    capture["pluginIdentity"]["implementationFingerprint"]
                ),
                "pipelineEvidenceDigest": capture["pipelineEvidenceDigest"],
            })
        if baseline not in seen_modes:
            raise ValueError(f"case {case_id}: baseline mode {baseline!r} is absent")
        if ground_truth["sourceIdentityDigest"] != source_digest:
            raise ValueError(
                f"case {case_id}: ground truth is not bound to the captured "
                "immutable PR"
            )
        changed_files = set(_source_identity(capture)["changedFiles"])
        outside_diff = sorted(set(expected_defect_files) - changed_files)
        if outside_diff:
            raise ValueError(
                f"case {case_id}: expected defects reference files outside the "
                f"fixed diff: {', '.join(outside_diff)}"
            )
        ground_truth_digest = _digest(ground_truth)
        for item in provenance:
            if item["caseId"] == case_id:
                item["groundTruthDigest"] = ground_truth_digest

    report = evaluate_dataset({
        "baseline": baseline,
        "cases": evaluation_cases,
    }).as_dict()
    case_metrics = []
    for item in evaluation_cases:
        expected = set(item["expected"])
        published = set(item["published"])
        true_positives = len(expected & published)
        false_positives = len(published - expected)
        false_negatives = len(expected - published)
        precision_denominator = true_positives + false_positives
        recall_denominator = true_positives + false_negatives
        case_metrics.append({
            "caseId": item["caseId"],
            "mode": item["mode"],
            "repositoryPlugins": list(item["plugins"]),
            "languages": list(item["languages"]),
            "frameworks": list(item["frameworks"]),
            "truePositives": true_positives,
            "falsePositives": false_positives,
            "falseNegatives": false_negatives,
            "precision": (
                true_positives / precision_denominator
                if precision_denominator
                else 0.0
            ),
            "recall": (
                true_positives / recall_denominator
                if recall_denominator
                else 0.0
            ),
            "reviewableHunks": item["reviewableHunks"],
            "terminalHunks": item["terminalHunks"],
            "changedLines": item["changedLines"],
            "modelCalls": item["modelCalls"],
            "inputTokens": item["inputTokens"],
            "outputTokens": item["outputTokens"],
            "cost": item["cost"],
            "costPerChangedKloc": (
                round(
                    (float(item["cost"]) * 1000) / item["changedLines"],
                    8,
                )
                if item["changedLines"]
                else 0.0
            ),
        })
    return {
        "kind": "review-quality-paired-capture-report",
        "provenance": provenance,
        "caseMetrics": case_metrics,
        "report": report,
    }


def _mode_capture(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected MODE=/path/to/capture.json")
    mode, path = value.split("=", 1)
    if not mode or not path:
        raise argparse.ArgumentTypeError("expected MODE=/path/to/capture.json")
    return mode, Path(path)


def _write_json(payload: Mapping[str, Any], path: Path | None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path is None:
        print(rendered, end="")
    else:
        path.write_text(rendered, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create and evaluate human-adjudicated pairs of actual full-pipeline "
            "review-quality captures."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    template_parser = subparsers.add_parser(
        "template",
        help="create an unlabeled adjudication manifest from paired captures",
    )
    template_parser.add_argument("--case-id", required=True)
    template_parser.add_argument(
        "--language",
        action="append",
        required=True,
        help="changed-source language; repeat for polyglot pull requests",
    )
    template_parser.add_argument("--framework", action="append", default=[])
    template_parser.add_argument(
        "--mode-capture",
        action="append",
        required=True,
        type=_mode_capture,
        metavar="MODE=PATH",
    )
    template_parser.add_argument("--baseline", default="fallback")
    template_parser.add_argument("--output", type=Path)

    merge_parser = subparsers.add_parser(
        "merge",
        help="combine single-case adjudication templates into one fixed corpus",
    )
    merge_parser.add_argument("templates", nargs="+", type=Path)
    merge_parser.add_argument("--output", type=Path)

    ground_truth_parser = subparsers.add_parser(
        "bind-ground-truth",
        help=(
            "bind one independently certified candidate-blind defect inventory "
            "to its exact paired capture template"
        ),
    )
    ground_truth_parser.add_argument("manifest", type=Path)
    ground_truth_parser.add_argument("inventory", type=Path)
    ground_truth_parser.add_argument("--output", type=Path)

    cost_parser = subparsers.add_parser(
        "bind-cost-evidence",
        help=(
            "bind mode costs to billing artifacts or provider-response cost "
            "projections without changing human verification fields"
        ),
    )
    cost_parser.add_argument("manifest", type=Path)
    cost_parser.add_argument("--output", type=Path)

    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="verify capture integrity and evaluate a fully labeled manifest",
    )
    evaluate_parser.add_argument("manifest", type=Path)
    evaluate_parser.add_argument("--output", type=Path)

    args = parser.parse_args()
    if args.command == "template":
        payload = create_template(
            case_id=args.case_id,
            languages=args.language,
            frameworks=args.framework,
            captures=args.mode_capture,
            baseline=args.baseline,
        )
        _write_json(payload, args.output)
        return 0

    if args.command == "merge":
        payload = merge_templates([
            _load_json(path)
            for path in args.templates
        ])
        _write_json(payload, args.output)
        return 0

    if args.command == "bind-ground-truth":
        manifest = _load_json(args.manifest)
        inventory = _load_json(args.inventory)
        payload = bind_ground_truth(
            manifest,
            inventory,
            base_dir=args.manifest.resolve().parent,
        )
        _write_json(payload, args.output)
        return 0

    if args.command == "bind-cost-evidence":
        manifest = _load_json(args.manifest)
        payload = bind_cost_evidence(
            manifest,
            base_dir=args.manifest.resolve().parent,
        )
        _write_json(payload, args.output)
        return 0

    manifest = _load_json(args.manifest)
    report = evaluate_capture_manifest(
        manifest,
        base_dir=args.manifest.resolve().parent,
    )
    _write_json(report, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
