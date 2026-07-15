from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from codecrow_evaluation.scoring import (
    EvaluationInputError,
    _normalize_input,
    score_evaluation,
)


def _provenance() -> dict:
    return {
        "baselineManifestSha256": "a" * 64,
        "codecrowPublicRevision": "1" * 40,
        "codecrowStaticRevision": "2" * 40,
        "dirtyStateSha256": "b" * 64,
        "splitRegistrySha256": "c" * 64,
        "corpusManifestSha256": "d" * 64,
        "oracleCatalogSha256": "e" * 64,
        "executionTelemetrySha256": "f" * 64,
        "environmentSha256": "3" * 64,
        "modelVersion": "model-v1",
        "promptVersion": "prompt-v1",
        "ruleVersion": "rule-v1",
        "indexVersion": "rag-disabled",
        "seed": 0,
        "command": ["score"],
        "accessGrantId": None,
        "accessLedgerHeadSha256": None,
    }


def _label(label_id: str = "bug-a") -> dict:
    return {
        "labelId": label_id,
        "severity": "high",
        "labelVersion": "labels-v1",
        "oracleId": "oracle-v1",
    }


def _prediction(finding_id: str = "finding-a") -> dict:
    return {
        "findingId": finding_id,
        "matchedLabelId": "bug-a",
        "severity": "high",
        "supported": True,
        "duplicateOf": None,
    }


def _case(case_id: str = "pr-a") -> dict:
    return {
        "caseId": case_id,
        "labels": [_label()],
        "predictions": [_prediction()],
        "analysis": {"state": "complete", "partialReason": None},
        "coverage": {"represented": 1, "total": 1},
        "resource": {
            "estimatedCostMicrousd": 1,
            "providerReportedCostMicrousd": 1,
            "latencyMs": 1,
        },
    }


def _bundle() -> dict:
    return {
        "schemaVersion": 1,
        "evaluationId": "eval-v1",
        "splitPurpose": "calibration",
        "scoringPolicyVersion": "p0-05-v1",
        "provenance": _provenance(),
        "cases": [_case()],
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("bad_schema", "schemaVersion"),
        ("bad_purpose", "splitPurpose"),
        ("cases_not_array", "cases must be an array"),
        ("empty_cases", "at least one PR"),
        ("duplicate_case", "duplicate caseId"),
        ("duplicate_label", "duplicate labelId"),
        ("duplicate_finding", "duplicate findingId"),
        ("bad_label_severity", "severity"),
        ("bad_match", "matchedLabelId"),
        ("bad_prediction_severity", "severity"),
        ("bad_supported", "supported"),
        ("self_duplicate", "duplicateOf"),
        ("duplicate_chain", "duplicate chain"),
        ("bad_state", "analysis.state"),
        ("complete_reason", "must be null"),
        ("negative_cost", "estimatedCostMicrousd"),
        ("bad_reported_cost", "providerReportedCostMicrousd"),
        ("extra_provenance", "unsupported field"),
        ("bad_revision_type", "codecrowPublicRevision"),
        ("bad_revision_shape", "codecrowPublicRevision"),
        ("bad_index_type", "indexVersion"),
        ("bad_index_shape", "indexVersion"),
        ("bad_seed_bool", "seed"),
        ("bad_seed_type", "seed"),
        ("bad_seed_negative", "seed"),
        ("bad_command_type", "command"),
        ("empty_command", "command"),
        ("bad_command_arg", "command"),
        ("public_grant", "must not claim"),
    ],
)
def test_scoring_negative_contract_matrix(mutation: str, message: str) -> None:
    bundle = _bundle()
    case = bundle["cases"][0]
    provenance = bundle["provenance"]
    if mutation == "bad_schema":
        bundle["schemaVersion"] = 2
    elif mutation == "bad_purpose":
        bundle["splitPurpose"] = "secret-test"
    elif mutation == "cases_not_array":
        bundle["cases"] = {}
    elif mutation == "empty_cases":
        bundle["cases"] = []
    elif mutation == "duplicate_case":
        bundle["cases"].append(copy.deepcopy(case))
    elif mutation == "duplicate_label":
        case["labels"].append(copy.deepcopy(case["labels"][0]))
    elif mutation == "duplicate_finding":
        case["predictions"].append(copy.deepcopy(case["predictions"][0]))
    elif mutation == "bad_label_severity":
        case["labels"][0]["severity"] = "blocker"
    elif mutation == "bad_match":
        case["predictions"][0]["matchedLabelId"] = "missing"
    elif mutation == "bad_prediction_severity":
        case["predictions"][0]["severity"] = "blocker"
    elif mutation == "bad_supported":
        case["predictions"][0]["supported"] = "yes"
    elif mutation == "self_duplicate":
        case["predictions"][0]["duplicateOf"] = "finding-a"
    elif mutation == "duplicate_chain":
        case["predictions"].extend(
            [
                {
                    **_prediction("finding-b"),
                    "duplicateOf": "finding-a",
                },
                {
                    **_prediction("finding-c"),
                    "duplicateOf": "finding-b",
                },
            ]
        )
    elif mutation == "bad_state":
        case["analysis"]["state"] = "failed"
    elif mutation == "complete_reason":
        case["analysis"]["partialReason"] = "unexpected"
    elif mutation == "negative_cost":
        case["resource"]["estimatedCostMicrousd"] = -1
    elif mutation == "bad_reported_cost":
        case["resource"]["providerReportedCostMicrousd"] = "unknown"
    elif mutation == "extra_provenance":
        provenance["protectedIdentity"] = "leak"
    elif mutation == "bad_revision_type":
        provenance["codecrowPublicRevision"] = 1
    elif mutation == "bad_revision_shape":
        provenance["codecrowPublicRevision"] = "A" * 40
    elif mutation == "bad_index_type":
        provenance["indexVersion"] = 1
    elif mutation == "bad_index_shape":
        provenance["indexVersion"] = "rag-commit-ABC"
    elif mutation == "bad_seed_bool":
        provenance["seed"] = True
    elif mutation == "bad_seed_type":
        provenance["seed"] = "0"
    elif mutation == "bad_seed_negative":
        provenance["seed"] = -1
    elif mutation == "bad_command_type":
        provenance["command"] = "score"
    elif mutation == "empty_command":
        provenance["command"] = []
    elif mutation == "bad_command_arg":
        provenance["command"] = [""]
    elif mutation == "public_grant":
        provenance["accessGrantId"] = "4" * 64

    with pytest.raises(EvaluationInputError, match=message):
        score_evaluation(bundle)


def test_valid_protected_provenance_and_integer_percentile_paths() -> None:
    bundle = _bundle()
    bundle["splitPurpose"] = "confirmation_reserve"
    bundle["provenance"]["accessGrantId"] = "4" * 64
    bundle["provenance"]["accessLedgerHeadSha256"] = "5" * 64
    bundle["provenance"]["indexVersion"] = "rag-commit-" + ("6" * 40)
    bundle["cases"].extend([_case("pr-b"), _case("pr-c")])
    bundle["cases"][1]["resource"]["latencyMs"] = 2
    bundle["cases"][2]["resource"]["latencyMs"] = 3

    result = score_evaluation(bundle)

    assert result["aggregate"]["latencyMs"]["p50"] == 2
    assert result["splitPurpose"] == "confirmation_reserve"


def test_normalization_is_defensive_for_prevalidation_shapes() -> None:
    assert _normalize_input({"cases": {}})["cases"] == {}
    value = {"cases": [None, {"caseId": "x", "labels": {}, "predictions": {}}]}
    assert _normalize_input(value) == value


@pytest.mark.parametrize(
    "mutation",
    ["missing", "bad_json", "bad_schema", "bad_id", "bad_confidence", "bad_severity"],
)
def test_scoring_policy_fails_closed_on_drift(tmp_path: Path, mutation: str) -> None:
    policy = Path(__file__).resolve().parents[1] / "policy" / "scoring-policy-v1.json"
    target = tmp_path / "policy.json"
    if mutation == "missing":
        target = tmp_path / "missing.json"
    elif mutation == "bad_json":
        target.write_text("{", encoding="utf-8")
    else:
        value = json.loads(policy.read_text(encoding="utf-8"))
        if mutation == "bad_schema":
            value["schemaVersion"] = 2
        elif mutation == "bad_id":
            value["policyId"] = "p0-05-v2"
        elif mutation == "bad_confidence":
            value["confidenceInterval"]["kind"] = "bootstrap"
        elif mutation == "bad_severity":
            value["severityOrder"] = list(reversed(value["severityOrder"]))
        target.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(EvaluationInputError):
        score_evaluation(_bundle(), policy_path=target)


def test_bundle_policy_identifier_must_match_loaded_policy() -> None:
    bundle = _bundle()
    bundle["scoringPolicyVersion"] = "p0-05-v2"
    with pytest.raises(EvaluationInputError, match="must match"):
        score_evaluation(bundle)
