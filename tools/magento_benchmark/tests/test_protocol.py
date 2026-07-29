from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import magento2_benchmark.protocol as protocol_module

from magento2_benchmark.corpus import attach_corpus_digest
from magento2_benchmark.judge import _validated_judgment_id
from magento2_benchmark.metrics import (
    build_metrics,
    validate_metrics_derivation,
)
from magento2_benchmark.protocol import (
    COMPARISON_CONTROLS,
    JUDGE_EVALUATION_SUBJECT_POLICY,
    build_reproducibility_package,
    create_judge_evaluation,
    create_seal_ledger,
    create_study_registration,
    export_judge_evaluation_packet,
    validate_protocol_bundle,
    validate_study_registration,
    verify_reproducibility_package,
    _audit_subjects,
)
from magento2_benchmark.runner import RUN_KIND
from magento2_benchmark.postfix import POST_FIX_RUN_KIND
from magento2_benchmark.util import sha256_json

from conftest import write_json


REGISTERED_AT = "2026-07-29T10:00:00Z"
RUN_STARTED_AT = "2026-07-29T11:00:00Z"
RUN_COMPLETED_AT = "2026-07-29T12:00:00Z"
UNSEALED_AT = "2026-07-29T13:00:00Z"
EVALUATED_AT = "2026-07-29T14:00:00Z"
LEDGER_GENERATED_AT = "2026-07-29T15:00:00Z"


def _registration_plan(corpus: dict[str, Any]) -> dict[str, Any]:
    sealed_ids = [
        case["caseId"]
        for case in corpus["cases"]
        if case["partition"] == "sealed"
    ][:5]
    analysis_config = {
        "model": "analysis-a",
        "provider": "fixture",
        "temperature": 0,
    }
    judge_config = {
        "model": "judge-a",
        "expected_response_model": "judge-a",
        "temperature": 0,
    }
    return {
        "studyId": "magento-study-a",
        "registeredAt": REGISTERED_AT,
        "analysisPlans": [
            {
                "runId": "analysis-a",
                "model": "analysis-a",
                "provider": "fixture",
                "config": analysis_config,
            }
        ],
        "judgePlans": [
            {
                "judgmentId": "judgment-a",
                "analysisRunId": "analysis-a",
                "model": "judge-a",
                "expectedResponseModel": "judge-a",
                "promptVersion": "fixture-prompt",
                "promptDigest": "a" * 64,
                "config": judge_config,
            }
        ],
        "endpoints": {
            "primary": {
                "partition": "sealed",
                "caseCount": 20,
                "metrics": [
                    "micro_reference_set_precision",
                    "micro_reviewer_issue_recall",
                    "micro_f1",
                ],
            },
            "secondary": [
                {
                    "scope": "all_50",
                    "metrics": [
                        "micro_reference_set_precision",
                        "micro_reviewer_issue_recall",
                        "micro_f1",
                    ],
                },
                {
                    "scope": "development_30",
                    "metrics": [
                        "micro_reference_set_precision",
                        "micro_reviewer_issue_recall",
                        "micro_f1",
                    ],
                },
            ],
        },
        "bootstrap": {
            "method": "paired_pull_request_cluster_percentile",
            "iterations": 10_000,
            "seed": 20260729,
            "confidenceLevel": 0.95,
        },
        "executionPolicy": {
            "analysisMaxCaseAttempts": 2,
            "analysisTransportRetries": 1,
            "judgeTransportRetries": 1,
            "judgeStructuredOutputRetries": 3,
            "missingCasePolicy": "fail_and_report_coverage",
            "zeroFindingPolicy": "score_as_zero_candidates",
            "stoppingRule": (
                "complete_all_planned_runs_without_sealed_result_model_selection"
            ),
        },
        "comparisonControls": sorted(COMPARISON_CONTROLS),
        "postFixPlan": {
            "required": True,
            "snapshot": "verified_F",
            "endpoint": "per_gold_same_root_cause_disappearance",
            "sameBaseAndControls": True,
            "executionArtifactRequired": True,
        },
        "judgeEvaluationPlan": {
            "mode": "blinded_human_audit",
            "caseIds": sealed_ids,
            "subjectPolicy": JUDGE_EVALUATION_SUBJECT_POLICY,
            "minimumIndependentRaters": 2,
            "agreementMetric": "percent_pairwise_agreement",
            "minimumAgreement": 0.8,
            "disagreementResolution": "independent_adjudicator",
            "modelIdentityBlinded": True,
        },
        "allowedClaims": [
            "Sealed reference-set metrics for preregistered models",
        ],
        "prohibitedClaims": [
            "Causal production effectiveness",
        ],
    }


def _analysis_run(
    corpus: dict[str, Any],
    registration: dict[str, Any],
) -> dict[str, Any]:
    plan = registration["analysisPlans"][0]
    case_ids = [case["caseId"] for case in corpus["cases"]]
    audit_ids = set(registration["judgeEvaluationPlan"]["caseIds"])
    result = {
        "kind": RUN_KIND,
        "runId": plan["runId"],
        "startedAt": RUN_STARTED_AT,
        "completedAt": RUN_COMPLETED_AT,
        "status": "completed",
        "corpusDigest": corpus["corpusDigest"],
        "analysisModel": plan["model"],
        "analysisProvider": plan["provider"],
        "analysisConfig": plan["config"],
        "analysisConfigDigest": plan["configDigest"],
        "selectedCaseIds": case_ids,
        "cases": [
            {
                "caseId": case_id,
                "status": "completed",
                "findings": (
                    [
                        {
                            "findingId": f"finding-{case_id}-{index}",
                            "path": f"app/code/{case_id}.php",
                            "line": 9 + index,
                            "title": f"Fixture finding {index}",
                            "description": "Fixture finding description",
                            "category": "bug",
                            "severity": "medium",
                            "suggestedFix": "Apply the fixture correction",
                            "confidence": 0.9,
                            "raw": {"not": "published"},
                        }
                        for index in (1, 2)
                    ]
                    if case_id in audit_ids
                    else []
                ),
            }
            for case_id in case_ids
        ],
    }
    result["runDigest"] = sha256_json(result)
    return result


def _post_fix_analysis_run(
    registration: dict[str, Any],
    primary_run: dict[str, Any],
) -> dict[str, Any]:
    pair = registration["postFixPlan"]["analysisPairs"][0]
    result = copy.deepcopy(primary_run)
    result.update(
        {
            "kind": POST_FIX_RUN_KIND,
            "runId": pair["postFixAnalysisRunId"],
            "snapshotRole": "verified_F",
            "pairedPrimaryRunId": primary_run["runId"],
            "pairedPrimaryRunDigest": primary_run["runDigest"],
            "postFixReplayPlanDigest": "b" * 64,
            "replayLockDigest": "c" * 64,
            "replayAttestationDigest": "d" * 64,
        }
    )
    result["runDigest"] = sha256_json(
        {key: value for key, value in result.items() if key != "runDigest"}
    )
    return result


def _judgment(
    corpus: dict[str, Any],
    registration: dict[str, Any],
    run: dict[str, Any],
) -> dict[str, Any]:
    plan = registration["judgePlans"][0]
    corpus_by_id = {case["caseId"]: case for case in corpus["cases"]}
    run_by_id = {case["caseId"]: case for case in run["cases"]}
    audit_ids = set(registration["judgeEvaluationPlan"]["caseIds"])
    cases = []
    for case_id, corpus_case in corpus_by_id.items():
        run_case = run_by_id[case_id]
        gold_issues = [
            {
                "goldId": f"G{index:03d}",
                "sourceId": item["id"],
                "sourceUrl": item["sourceUrl"],
                "path": item["path"],
                "line": item["originalLine"],
                "reviewComment": item["body"],
                "summary": item["expectedIssue"]["summary"],
                "category": item["expectedIssue"]["category"],
                "severity": item["expectedIssue"]["severity"],
            }
            for index, item in enumerate(
                corpus_case["goldenComments"],
                start=1,
            )
        ]
        candidate_findings = [
            {
                "candidateId": f"C{index:03d}",
                **{
                    key: value
                    for key, value in item.items()
                    if key != "raw"
                },
            }
            for index, item in enumerate(run_case["findings"], start=1)
        ]
        pair = (
            {
                "goldId": "G001",
                "candidateId": "C001",
                "specific_issue": "yes",
                "grounded_at_snapshot": "yes",
                "same_root_cause": "yes",
                "same_failure_or_consequence": "yes",
                "compatible_required_change": "yes",
                "location_relation": "same_symbol",
                "verdict": "substantive_match",
                "confidence": 0.9,
                "repeatAgreement": 1.0,
                "repeatVerdicts": {"substantive_match": 3},
            }
            if case_id in audit_ids
            else None
        )
        unrelated_pair = (
            {
                "goldId": "G001",
                "candidateId": "C002",
                "specific_issue": "yes",
                "grounded_at_snapshot": "yes",
                "same_root_cause": "no",
                "same_failure_or_consequence": "no",
                "compatible_required_change": "no",
                "location_relation": "unrelated",
                "verdict": "no_match",
                "confidence": 0.9,
                "repeatAgreement": 1.0,
                "repeatVerdicts": {"no_match": 3},
            }
            if case_id in audit_ids
            else None
        )
        case = {
            "caseId": case_id,
            "status": "scored",
            "caseInputDigest": sha256_json(
                {
                    "corpusCase": corpus_case,
                    "analysisCase": run_case,
                    "analysisRunDigest": run["runDigest"],
                    "judgeConfigDigest": plan["configDigest"],
                    "promptVersion": plan["promptVersion"],
                }
            ),
            "judgeConfigDigest": plan["configDigest"],
            "sizeBand": corpus_case["sizeBand"],
            "partition": corpus_case["partition"],
            "goldCount": len(gold_issues),
            "candidateCount": len(candidate_findings),
            "goldIssues": gold_issues,
            "candidateFindings": candidate_findings,
            "pairJudgments": (
                [pair, unrelated_pair] if pair is not None else []
            ),
            "assignments": (
                [
                    {
                        "goldId": "G001",
                        "candidateId": "C001",
                        "weight": 1.06,
                        "judgment": pair,
                    }
                ]
                if pair is not None
                else []
            ),
            "unmatchedGold": [] if pair is not None else ["G001"],
            "unmatchedCandidates": (
                ["C002"] if pair is not None else []
            ),
            "novelFindingJudgments": (
                [
                    {
                        "candidateId": "C002",
                        "verdict": "invalid",
                        "grounded_at_snapshot": "no",
                        "actionable": "no",
                        "confidence": 0.9,
                        "repeatAgreement": 1.0,
                        "repeatVerdicts": {"invalid": 3},
                    }
                ]
                if pair is not None
                else []
            ),
            "calls": [],
        }
        case["caseDigest"] = sha256_json(case)
        case["rawJudgment"] = f"raw/{case_id}.json"
        cases.append(case)
    result = {
        "kind": "codecrow-magento2-judgment-run",
        "judgmentId": plan["judgmentId"],
        "createdAt": "2026-07-29T13:10:00Z",
        "corpusDigest": corpus["corpusDigest"],
        "analysisRunId": plan["analysisRunId"],
        "analysisRunDigest": run["runDigest"],
        "analysisModel": run["analysisModel"],
        "judgeModel": plan["model"],
        "judgeConfig": plan["config"],
        "judgeConfigDigest": plan["configDigest"],
        "promptVersion": plan["promptVersion"],
        "promptDigest": plan["promptDigest"],
        "cases": cases,
    }
    result["judgmentDigest"] = sha256_json(result)
    return result


def _ledger_plan(registration: dict[str, Any]) -> dict[str, Any]:
    return {
        "generatedAt": LEDGER_GENERATED_AT,
        "custodians": [
            {"custodianId": "custodian-a", "role": "study custodian"},
            {"custodianId": "custodian-b", "role": "independent witness"},
        ],
        "accessEvents": [
            {
                "eventId": "event-commit",
                "at": REGISTERED_AT,
                "actor": "custodian-a",
                "action": "commitment_created",
                "partition": "sealed",
                "purpose": "Commit sealed labels before model execution",
            },
            {
                "eventId": "event-unseal",
                "at": UNSEALED_AT,
                "actor": "custodian-a",
                "action": "sealed_unseal",
                "partition": "sealed",
                "purpose": "Unseal after every planned analysis run completed",
            },
            {
                "eventId": "event-access",
                "at": "2026-07-29T13:01:00Z",
                "actor": "custodian-b",
                "action": "sealed_access",
                "partition": "sealed",
                "purpose": "Execute preregistered judge and human audit",
            },
        ],
        "unseal": {
            "at": UNSEALED_AT,
            "authorizedBy": ["custodian-a", "custodian-b"],
            "reason": "All preregistered analysis runs completed",
            "commitmentDigest": registration["sealedCommitment"]["digest"],
        },
    }


def _make_protocol(tmp_path: Path, corpus: dict[str, Any]) -> dict[str, Any]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    corpus_path = write_json(tmp_path / "corpus.json", corpus)
    plan_path = write_json(
        tmp_path / "registration-plan.json",
        _registration_plan(corpus),
    )
    registration_path = tmp_path / "registration.json"
    registration = create_study_registration(
        corpus_path=corpus_path,
        plan_path=plan_path,
        output_path=registration_path,
    )
    run = _analysis_run(corpus, registration)
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    run_path = write_json(analysis_dir / "run.json", run)
    post_fix_run = _post_fix_analysis_run(registration, run)
    post_fix_analysis_dir = tmp_path / "post-fix-analysis"
    post_fix_analysis_dir.mkdir()
    post_fix_run_path = write_json(
        post_fix_analysis_dir / "run.json",
        post_fix_run,
    )
    ledger_plan_path = write_json(
        tmp_path / "ledger-plan.json",
        _ledger_plan(registration),
    )
    seal_path = tmp_path / "seal.json"
    seal = create_seal_ledger(
        corpus_path=corpus_path,
        registration_path=registration_path,
        analysis_run_paths=[run_path],
        post_fix_analysis_run_paths=[post_fix_run_path],
        ledger_plan_path=ledger_plan_path,
        output_path=seal_path,
    )
    judgment = _judgment(corpus, registration, run)
    judgment_dir = tmp_path / "judgment"
    judgment_dir.mkdir()
    judgment_path = write_json(
        judgment_dir / "judgments.json",
        judgment,
    )
    subjects = _audit_subjects(
        registration,
        {judgment["judgmentId"]: judgment},
    )
    records = [
        {
            "subjectId": subject["subjectId"],
            "annotator": annotator,
            "humanVerdict": subject["judgeVerdict"],
            "modelIdentityBlinded": True,
            "at": "2026-07-29T13:45:00Z",
        }
        for subject in subjects
        for annotator in ("reviewer-a", "reviewer-b")
    ]
    evaluation_plan_path = write_json(
        tmp_path / "evaluation-plan.json",
        {
            "createdAt": EVALUATED_AT,
            "records": records,
            "adjudications": [],
        },
    )
    evaluation_path = tmp_path / "evaluation.json"
    evaluation = create_judge_evaluation(
        corpus_path=corpus_path,
        registration_path=registration_path,
        seal_ledger_path=seal_path,
        analysis_run_paths=[run_path],
        post_fix_analysis_run_paths=[post_fix_run_path],
        judgment_paths=[judgment_path],
        evaluation_plan_path=evaluation_plan_path,
        output_path=evaluation_path,
    )
    return {
        "corpusPath": corpus_path,
        "registrationPath": registration_path,
        "registration": registration,
        "runPath": run_path,
        "run": run,
        "postFixRunPath": post_fix_run_path,
        "postFixRun": post_fix_run,
        "sealPath": seal_path,
        "seal": seal,
        "judgmentPath": judgment_path,
        "judgment": judgment,
        "evaluationPath": evaluation_path,
        "evaluation": evaluation,
        "analysisDir": analysis_dir,
        "postFixAnalysisDir": post_fix_analysis_dir,
        "judgmentDir": judgment_dir,
    }


def test_protocol_bundle_binds_timing_and_all_judge_decisions(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    artifacts = _make_protocol(tmp_path, corpus)
    assert artifacts["registration"]["postFixPlan"]["analysisPairs"] == [
        {
            "primaryAnalysisRunId": "analysis-a",
            "postFixAnalysisRunId": "analysis-a:post-fix",
        }
    ]
    assert artifacts["registration"]["postFixPlan"]["judgmentPairs"][0][
        "postFixJudgmentId"
    ] == "judgment-a:post-fix"
    assert artifacts["seal"]["boundPostFixRuns"][0]["runId"] == (
        "analysis-a:post-fix"
    )
    assert artifacts["postFixRun"]["completedAt"] <= artifacts["seal"][
        "unseal"
    ]["at"]

    summary = validate_protocol_bundle(
        corpus=corpus,
        registration_path=artifacts["registrationPath"],
        seal_ledger_path=artifacts["sealPath"],
        judge_evaluation_path=artifacts["evaluationPath"],
        analysis_run_paths=[artifacts["runPath"]],
        post_fix_analysis_run_paths=[artifacts["postFixRunPath"]],
        judgment_paths=[artifacts["judgmentPath"]],
    )

    assert summary["registration"]["status"] == "validated"
    assert summary["sealedLabelCustody"]["status"] == "validated"
    assert (
        summary["judgeCalibrationOrAudit"]["humanHumanAgreement"]
        == 1.0
    )
    assert summary["judgeCalibrationOrAudit"]["judgeHumanAgreement"] == 1.0

    packet = export_judge_evaluation_packet(
        corpus_path=artifacts["corpusPath"],
        registration_path=artifacts["registrationPath"],
        seal_ledger_path=artifacts["sealPath"],
        analysis_run_paths=[artifacts["runPath"]],
        post_fix_analysis_run_paths=[artifacts["postFixRunPath"]],
        judgment_paths=[artifacts["judgmentPath"]],
    )
    assert packet["modelIdentityBlinded"] is True
    encoded = json.dumps(packet["subjects"])
    assert "judgeVerdict" not in encoded
    assert "judgmentId" not in encoded
    assert "judge-a" not in encoded
    assert packet["rubric"]["pair"]
    assert packet["subjects"][0]["allowedHumanVerdicts"]


def test_registration_rejects_vacuous_audit_and_bootstrap(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    corpus_path = write_json(tmp_path / "corpus.json", corpus)
    plan = _registration_plan(corpus)
    plan["bootstrap"]["iterations"] = 9_999
    with pytest.raises(ValueError, match="iterations"):
        create_study_registration(
            corpus_path=corpus_path,
            plan_path=write_json(tmp_path / "bad-bootstrap.json", plan),
        )

    plan = _registration_plan(corpus)
    plan["judgeEvaluationPlan"]["minimumAgreement"] = 0
    with pytest.raises(ValueError, match="at least 0.70"):
        create_study_registration(
            corpus_path=corpus_path,
            plan_path=write_json(tmp_path / "bad-agreement.json", plan),
        )


def test_sealed_labels_cannot_unseal_until_completed_run(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    artifacts = _make_protocol(tmp_path, corpus)
    late_run = copy.deepcopy(artifacts["run"])
    late_run["completedAt"] = "2026-07-29T13:30:00Z"
    late_run["runDigest"] = sha256_json(
        {key: value for key, value in late_run.items() if key != "runDigest"}
    )
    late_run_path = write_json(tmp_path / "late-run.json", late_run)

    with pytest.raises(ValueError, match="complete before unseal"):
        create_seal_ledger(
            corpus_path=artifacts["corpusPath"],
            registration_path=artifacts["registrationPath"],
            analysis_run_paths=[late_run_path],
            ledger_plan_path=write_json(
                tmp_path / "late-ledger-plan.json",
                _ledger_plan(artifacts["registration"]),
            ),
        )


@pytest.mark.parametrize("bad_subject", ["foo", "m2e-" + "0" * 64])
def test_evaluation_rejects_arbitrary_subjects(
    tmp_path,
    corpus_factory,
    bad_subject,
):
    corpus = corpus_factory()
    artifacts = _make_protocol(tmp_path, corpus)
    bad_plan = {
        "createdAt": EVALUATED_AT,
        "records": [
            {
                "subjectId": bad_subject,
                "annotator": "reviewer-a",
                "humanVerdict": "substantive_match",
                "modelIdentityBlinded": True,
                "at": "2026-07-29T13:45:00Z",
            }
        ],
        "adjudications": [],
    }
    with pytest.raises(ValueError, match="unbound subjectId"):
        create_judge_evaluation(
            corpus_path=artifacts["corpusPath"],
            registration_path=artifacts["registrationPath"],
            seal_ledger_path=artifacts["sealPath"],
            analysis_run_paths=[artifacts["runPath"]],
            post_fix_analysis_run_paths=[artifacts["postFixRunPath"]],
            judgment_paths=[artifacts["judgmentPath"]],
            evaluation_plan_path=write_json(
                tmp_path / f"bad-eval-{bad_subject[:4]}.json",
                bad_plan,
            ),
        )


def test_evaluation_rejects_missing_subject_and_pre_registration_record(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    artifacts = _make_protocol(tmp_path, corpus)
    evaluation = copy.deepcopy(artifacts["evaluation"])
    missing = evaluation["subjects"][0]["subjectId"]
    evaluation["records"] = [
        item
        for item in evaluation["records"]
        if item["subjectId"] != missing
    ]
    evaluation["judgeEvaluationDigest"] = sha256_json(
        {
            key: value
            for key, value in evaluation.items()
            if key != "judgeEvaluationDigest"
        }
    )
    write_json(artifacts["evaluationPath"], evaluation)
    with pytest.raises(ValueError, match="cover exactly"):
        validate_protocol_bundle(
            corpus=corpus,
            registration_path=artifacts["registrationPath"],
            seal_ledger_path=artifacts["sealPath"],
            judge_evaluation_path=artifacts["evaluationPath"],
            analysis_run_paths=[artifacts["runPath"]],
            post_fix_analysis_run_paths=[artifacts["postFixRunPath"]],
            judgment_paths=[artifacts["judgmentPath"]],
        )

    artifacts = _make_protocol(tmp_path / "second", corpus)
    evaluation = copy.deepcopy(artifacts["evaluation"])
    evaluation["records"][0]["at"] = "2026-07-29T09:59:00Z"
    record = evaluation["records"][0]
    record["recordDigest"] = sha256_json(
        {key: value for key, value in record.items() if key != "recordDigest"}
    )
    evaluation["judgeEvaluationDigest"] = sha256_json(
        {
            key: value
            for key, value in evaluation.items()
            if key != "judgeEvaluationDigest"
        }
    )
    write_json(artifacts["evaluationPath"], evaluation)
    with pytest.raises(ValueError, match="between registration"):
        validate_protocol_bundle(
            corpus=corpus,
            registration_path=artifacts["registrationPath"],
            seal_ledger_path=artifacts["sealPath"],
            judge_evaluation_path=artifacts["evaluationPath"],
            analysis_run_paths=[artifacts["runPath"]],
            post_fix_analysis_run_paths=[artifacts["postFixRunPath"]],
            judgment_paths=[artifacts["judgmentPath"]],
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("omit_case", "cover every corpus"),
        ("omit_pair", "omits or adds a pair"),
        ("omit_novel", "omits or adds a novel"),
        ("assign_ineligible", "production maximum assignment"),
        ("detach_run", "analysis-run binding"),
    ],
)
def test_resealed_judgment_cannot_shrink_audit_universe(
    tmp_path,
    corpus_factory,
    mutation,
    message,
):
    corpus = corpus_factory()
    artifacts = _make_protocol(tmp_path, corpus)
    judgment = copy.deepcopy(artifacts["judgment"])
    audit_case_id = artifacts["registration"]["judgeEvaluationPlan"][
        "caseIds"
    ][0]
    if mutation == "omit_case":
        judgment["cases"] = [
            case
            for case in judgment["cases"]
            if case["caseId"] != audit_case_id
        ]
    elif mutation == "detach_run":
        judgment["analysisRunDigest"] = "0" * 64
    else:
        case = next(
            item
            for item in judgment["cases"]
            if item["caseId"] == audit_case_id
        )
        if mutation == "omit_pair":
            case["pairJudgments"] = []
        elif mutation == "assign_ineligible":
            pair = case["pairJudgments"][0]
            pair["verdict"] = "no_match"
            pair["repeatVerdicts"] = {"no_match": 3}
            case["assignments"][0]["judgment"] = copy.deepcopy(pair)
        else:
            case["novelFindingJudgments"] = []
        case["caseDigest"] = sha256_json(
            {
                key: value
                for key, value in case.items()
                if key not in {"caseDigest", "rawJudgment"}
            }
        )
    judgment["judgmentDigest"] = sha256_json(
        {
            key: value
            for key, value in judgment.items()
            if key != "judgmentDigest"
        }
    )
    write_json(artifacts["judgmentPath"], judgment)

    with pytest.raises(ValueError, match=message):
        validate_protocol_bundle(
            corpus=corpus,
            registration_path=artifacts["registrationPath"],
            seal_ledger_path=artifacts["sealPath"],
            judge_evaluation_path=artifacts["evaluationPath"],
            analysis_run_paths=[artifacts["runPath"]],
            post_fix_analysis_run_paths=[artifacts["postFixRunPath"]],
            judgment_paths=[artifacts["judgmentPath"]],
        )


def test_blinded_audit_cannot_predate_unseal_or_sealed_access(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    artifacts = _make_protocol(tmp_path, corpus)
    evaluation = copy.deepcopy(artifacts["evaluation"])
    evaluation["createdAt"] = "2026-07-29T12:59:00Z"
    evaluation["judgeEvaluationDigest"] = sha256_json(
        {
            key: value
            for key, value in evaluation.items()
            if key != "judgeEvaluationDigest"
        }
    )
    write_json(artifacts["evaluationPath"], evaluation)
    with pytest.raises(ValueError, match="follow sealed unseal"):
        validate_protocol_bundle(
            corpus=corpus,
            registration_path=artifacts["registrationPath"],
            seal_ledger_path=artifacts["sealPath"],
            judge_evaluation_path=artifacts["evaluationPath"],
            analysis_run_paths=[artifacts["runPath"]],
            post_fix_analysis_run_paths=[artifacts["postFixRunPath"]],
            judgment_paths=[artifacts["judgmentPath"]],
        )


def test_development_calibration_cannot_satisfy_sealed_bundle(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    artifacts = _make_protocol(tmp_path, corpus)
    registration = copy.deepcopy(artifacts["registration"])
    registration["judgeEvaluationPlan"]["mode"] = "development_calibration"
    registration["judgeEvaluationPlan"]["caseIds"] = [
        case["caseId"]
        for case in corpus["cases"]
        if case["partition"] == "development"
    ][:5]
    registration["registrationDigest"] = sha256_json(
        {
            key: value
            for key, value in registration.items()
            if key != "registrationDigest"
        }
    )
    write_json(artifacts["registrationPath"], registration)
    with pytest.raises(ValueError, match="development calibration is diagnostic"):
        validate_protocol_bundle(
            corpus=corpus,
            registration_path=artifacts["registrationPath"],
            seal_ledger_path=artifacts["sealPath"],
            judge_evaluation_path=artifacts["evaluationPath"],
            analysis_run_paths=[artifacts["runPath"]],
            post_fix_analysis_run_paths=[artifacts["postFixRunPath"]],
            judgment_paths=[artifacts["judgmentPath"]],
        )


def _package_inputs(tmp_path: Path, artifacts: dict[str, Any]) -> dict[str, Any]:
    metrics_path = tmp_path / "metrics.json"
    build_metrics(
        corpus_path=artifacts["corpusPath"],
        judgment_paths=[artifacts["judgmentPath"]],
        analysis_run_paths=[artifacts["runPath"]],
        post_fix_analysis_run_paths=[artifacts["postFixRunPath"]],
        study_registration_path=artifacts["registrationPath"],
        seal_ledger_path=artifacts["sealPath"],
        judge_evaluation_path=artifacts["evaluationPath"],
        output_path=metrics_path,
        bootstrap_iterations=10_000,
        seed=20_260_729,
    )
    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()
    (dashboard / "index.html").write_text("<html></html>", encoding="utf-8")
    (dashboard / "app.js").write_text("void 0;", encoding="utf-8")
    (dashboard / "styles.css").write_text("body{}", encoding="utf-8")
    (dashboard / "data.json").write_bytes(metrics_path.read_bytes())
    runtime = tmp_path / "runtime.txt"
    runtime.write_text("python=3.11\nsourceRevision=fixture\n", encoding="utf-8")
    config = tmp_path / "benchmark.toml"
    config.write_text(
        '[analysis]\nmodel = "analysis-a"\napi_key_env = "BENCHMARK_KEY"\n',
        encoding="utf-8",
    )
    evidence = {}
    for category in ("source", "curation", "replay", "current-comment"):
        directory = tmp_path / f"{category}-evidence"
        directory.mkdir()
        (directory / "inventory.txt").write_text(
            f"{category}=fixture\n",
            encoding="utf-8",
        )
        evidence[category] = directory
    return {
        "metrics": metrics_path,
        "dashboard": dashboard,
        "runtime": runtime,
        "config": config,
        **evidence,
    }


def _package_semantic_fixture(
    *,
    root: Path,
    corpus_path: Path,
    registration_path: Path,
    seal_path: Path,
    evaluation_path: Path,
    metrics_path: Path,
    analysis_run_paths: list[Path],
    judgment_paths: list[Path],
    **_kwargs,
) -> dict[str, Any]:
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    derivation = validate_metrics_derivation(
        metrics=metrics,
        corpus_path=corpus_path,
        judgment_paths=judgment_paths,
        analysis_run_paths=analysis_run_paths,
        post_fix_analysis_run_paths=[
            root / "post-fix-analysis" / "run.json"
        ],
        replay_lock_paths=[],
        replay_attestation_paths=[],
        study_registration_path=registration_path,
        seal_ledger_path=seal_path,
        judge_evaluation_path=evaluation_path,
    )
    digest = "0" * 64
    return {
        "sourceCuration": {
            "corpusDigest": metrics["corpus"]["corpusDigest"],
            "selectionDigest": digest,
            "discoveryDigest": digest,
            "discoverySelectionLinkageDigest": digest,
            "sourceArchiveDigest": digest,
            "threadEvidenceDigest": digest,
            "curationPacketDigest": digest,
            "currentCommentAttestationDigest": digest,
        },
        "primaryReplay": {
            "lockDigest": digest,
            "attestationDigest": digest,
            "analysisRuns": len(analysis_run_paths),
        },
        "postFix": {
            "planDigest": digest,
            "lockDigest": digest,
            "attestationDigest": digest,
            "analysisRuns": 1,
            "judgments": 1,
            "controlSetDigest": digest,
            "controls": 1,
        },
        "metricsDerivation": derivation,
    }


def _build_package(
    tmp_path: Path,
    artifacts: dict[str, Any],
    inputs: dict[str, Any],
    *,
    corpus_path: Path | None = None,
) -> dict[str, Any]:
    return build_reproducibility_package(
        artifact_root=tmp_path,
        corpus_path=corpus_path or artifacts["corpusPath"],
        registration_path=artifacts["registrationPath"],
        seal_ledger_path=artifacts["sealPath"],
        judge_evaluation_path=artifacts["evaluationPath"],
        metrics_path=inputs["metrics"],
        dashboard_path=inputs["dashboard"],
        analysis_artifacts=[artifacts["analysisDir"]],
        judgment_artifacts=[artifacts["judgmentDir"]],
        runtime_artifacts=[inputs["runtime"]],
        config_artifacts=[inputs["config"]],
        source_artifacts=[inputs["source"]],
        curation_artifacts=[inputs["curation"]],
        replay_artifacts=[inputs["replay"]],
        current_comment_artifacts=[inputs["current-comment"]],
        post_fix_artifacts=[artifacts["postFixAnalysisDir"]],
        rerun_instructions=[
            "Install the benchmark module from the bound source revision.",
            "Run validate, analysis, judge, metrics, and dashboard commands.",
        ],
        limitations=[
            "The package does not establish production causal impact.",
        ],
        output_path=tmp_path / "reproducibility-package.json",
    )


def test_reproducibility_package_binds_corpus_and_detects_tamper(
    tmp_path,
    corpus_factory,
    monkeypatch,
):
    monkeypatch.setattr(
        protocol_module,
        "_validate_extended_package_evidence",
        _package_semantic_fixture,
    )
    corpus = corpus_factory()
    artifacts = _make_protocol(tmp_path, corpus)
    inputs = _package_inputs(tmp_path, artifacts)
    package = _build_package(tmp_path, artifacts, inputs)
    assert package["corpusDigest"] == corpus["corpusDigest"]
    verified = verify_reproducibility_package(
        artifact_root=tmp_path,
        manifest_path=tmp_path / "reproducibility-package.json",
    )
    assert verified["publicationProtocolReady"] is False
    assert verified["metricsDerivationVerified"] is True
    assert verified["remainingBlocker"] == (
        "post_fix_judge_human_audit_not_bound"
    )

    inputs["runtime"].write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        verify_reproducibility_package(
            artifact_root=tmp_path,
            manifest_path=tmp_path / "reproducibility-package.json",
        )


def test_reproducibility_package_rejects_unrelated_corpus_and_toml_secret(
    tmp_path,
    corpus_factory,
    monkeypatch,
):
    monkeypatch.setattr(
        protocol_module,
        "_validate_extended_package_evidence",
        _package_semantic_fixture,
    )
    corpus = corpus_factory()
    artifacts = _make_protocol(tmp_path, corpus)
    inputs = _package_inputs(tmp_path, artifacts)
    unrelated = copy.deepcopy(corpus)
    unrelated["corpusId"] = "unrelated-magento-corpus"
    unrelated = attach_corpus_digest(
        {
            key: value
            for key, value in unrelated.items()
            if key != "corpusDigest"
        }
    )
    unrelated_path = write_json(tmp_path / "unrelated.json", unrelated)
    with pytest.raises(ValueError, match="bindings are inconsistent"):
        _build_package(
            tmp_path,
            artifacts,
            inputs,
            corpus_path=unrelated_path,
        )

    inputs["config"].write_text(
        '[analysis]\napi_key = "plain-secret"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="secret-like"):
        _build_package(tmp_path, artifacts, inputs)


@pytest.mark.parametrize(
    "value",
    [
        "https://user:plain-secret@example.test/v1",
        "https://example.test/v1?api_key=plain-secret",
        "https://example.test/v1#plain-secret",
    ],
)
def test_public_package_config_rejects_private_url_components(value):
    with pytest.raises(ValueError, match="URL userinfo|query credentials"):
        protocol_module._reject_sensitive_config({"base_url": value})


def test_public_package_config_allows_environment_url_placeholder():
    protocol_module._reject_sensitive_config(
        {
            "rerun": (
                "curl 'https://example.test/v1?api_key=${BENCHMARK_API_KEY}'"
            )
        }
    )


@pytest.mark.parametrize(
    "instruction",
    [
        "curl 'https://example.test/v1?api_key=plain-secret'",
        "Authorization: Bearer abcdefghijklmnop",
        "api_key=plain-secret",
    ],
)
def test_reproducibility_manifest_free_text_rejects_secrets(instruction):
    with pytest.raises(ValueError, match="secret-like|URL userinfo"):
        protocol_module._scan_manifest_for_secrets(
            {
                "rerunInstructions": [
                    instruction,
                    "use an isolated runtime",
                ],
                "limitations": ["fixture"],
            }
        )


@pytest.mark.parametrize(
    "text",
    [
        "api_key=plain-secret\n",
        "curl https://example.test/v1?access_token=plain-secret\n",
    ],
)
def test_reproducibility_text_artifact_rejects_secrets(tmp_path, text):
    artifact = tmp_path / "runtime.txt"
    artifact.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="secret-like|URL userinfo"):
        protocol_module._scan_file_for_secrets(artifact, "runtime.txt")


def test_reproducibility_package_rejects_resealed_fake_metrics(
    tmp_path,
    corpus_factory,
    monkeypatch,
):
    monkeypatch.setattr(
        protocol_module,
        "_validate_extended_package_evidence",
        _package_semantic_fixture,
    )
    artifacts = _make_protocol(tmp_path, corpus_factory())
    inputs = _package_inputs(tmp_path, artifacts)
    _build_package(tmp_path, artifacts, inputs)

    metrics = json.loads(inputs["metrics"].read_text(encoding="utf-8"))
    metrics["configurations"] = []
    metrics["pairwiseComparisons"] = []
    metrics["metricsDigest"] = sha256_json(
        {
            key: value
            for key, value in metrics.items()
            if key != "metricsDigest"
        }
    )
    write_json(inputs["metrics"], metrics)
    (inputs["dashboard"] / "data.json").write_bytes(
        inputs["metrics"].read_bytes()
    )

    manifest_path = tmp_path / "reproducibility-package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["metricsDigest"] = metrics["metricsDigest"]
    derivation = manifest["semanticVerification"]["metricsDerivation"]
    derivation.update(
        {
            "metricsDigest": metrics["metricsDigest"],
            "configurations": 0,
            "scoredCases": 0,
            "pairwiseComparisons": 0,
        }
    )
    for entry in manifest["files"]:
        path = tmp_path / entry["path"]
        entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        entry["sizeBytes"] = path.stat().st_size
    manifest["packageDigest"] = sha256_json(
        {
            key: value
            for key, value in manifest.items()
            if key != "packageDigest"
        }
    )
    write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="not exactly derivable"):
        verify_reproducibility_package(
            artifact_root=tmp_path,
            manifest_path=manifest_path,
        )


def test_safe_explicit_judgment_id_validation():
    assert _validated_judgment_id("judgment-model-a") == "judgment-model-a"
    assert _validated_judgment_id(None) is None
    with pytest.raises(ValueError, match="safe 1-256"):
        _validated_judgment_id("../escape")
    judgment_schema = json.loads(
        (
            Path(__file__).parents[1]
            / "schemas"
            / "judgment.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert (
        judgment_schema["properties"]["judgmentId"]["pattern"]
        == "^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"
    )


def test_registration_detects_config_digest_tamper(tmp_path, corpus_factory):
    corpus = corpus_factory()
    artifacts = _make_protocol(tmp_path, corpus)
    registration = copy.deepcopy(artifacts["registration"])
    registration["analysisPlans"][0]["configDigest"] = "0" * 64
    registration["registrationDigest"] = sha256_json(
        {
            key: value
            for key, value in registration.items()
            if key != "registrationDigest"
        }
    )
    with pytest.raises(ValueError, match="configDigest mismatch"):
        validate_study_registration(registration, corpus)


def test_metrics_protocol_artifacts_are_all_or_none(tmp_path):
    with pytest.raises(ValueError, match="must be supplied together"):
        build_metrics(
            corpus_path=tmp_path / "unused-corpus.json",
            judgment_paths=[tmp_path / "unused-judgment.json"],
            study_registration_path=tmp_path / "registration.json",
        )


def test_metrics_binds_validated_protocol_without_claiming_paper_ready(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    artifacts = _make_protocol(tmp_path, corpus)

    result = build_metrics(
        corpus_path=artifacts["corpusPath"],
        judgment_paths=[artifacts["judgmentPath"]],
        analysis_run_paths=[artifacts["runPath"]],
        post_fix_analysis_run_paths=[artifacts["postFixRunPath"]],
        study_registration_path=artifacts["registrationPath"],
        seal_ledger_path=artifacts["sealPath"],
        judge_evaluation_path=artifacts["evaluationPath"],
        bootstrap_iterations=10_000,
        seed=20_260_729,
    )

    methodology = result["methodology"]
    assert methodology["protocolControls"]["registration"]["status"] == (
        "validated"
    )
    assert methodology["protocolControls"]["sealedLabelCustody"]["status"] == (
        "validated"
    )
    assert methodology["protocolControls"]["judgeCalibrationOrAudit"][
        "status"
    ] == "validated"
    assert methodology["publicationProtocolGateFailures"] == [
        "post_fix_control_not_bound",
        "post_fix_judge_human_audit_not_bound",
        "reproducibility_package_not_bound",
    ]
    assert methodology["publicationProtocolReady"] is False
    assert methodology["paperReady"] is False


def _validate_packaged_metrics_derivation(
    artifacts: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    return validate_metrics_derivation(
        metrics=metrics,
        corpus_path=artifacts["corpusPath"],
        judgment_paths=[artifacts["judgmentPath"]],
        analysis_run_paths=[artifacts["runPath"]],
        post_fix_analysis_run_paths=[artifacts["postFixRunPath"]],
        replay_lock_paths=[],
        replay_attestation_paths=[],
        study_registration_path=artifacts["registrationPath"],
        seal_ledger_path=artifacts["sealPath"],
        judge_evaluation_path=artifacts["evaluationPath"],
    )


def test_metrics_derivation_recomputes_every_scoring_field(
    tmp_path,
    corpus_factory,
):
    artifacts = _make_protocol(tmp_path, corpus_factory())
    metrics = build_metrics(
        corpus_path=artifacts["corpusPath"],
        judgment_paths=[artifacts["judgmentPath"]],
        analysis_run_paths=[artifacts["runPath"]],
        post_fix_analysis_run_paths=[artifacts["postFixRunPath"]],
        study_registration_path=artifacts["registrationPath"],
        seal_ledger_path=artifacts["sealPath"],
        judge_evaluation_path=artifacts["evaluationPath"],
        bootstrap_iterations=10_000,
        seed=20_260_729,
    )

    summary = _validate_packaged_metrics_derivation(artifacts, metrics)
    assert summary == {
        "metricsDigest": metrics["metricsDigest"],
        "configurations": 1,
        "scoredCases": 50,
        "pairwiseComparisons": 0,
        "bootstrapIterations": 10_000,
        "bootstrapSeed": 20_260_729,
    }

    for mutate in (
        lambda value: value.__setitem__("configurations", []),
        lambda value: value["configurations"][0]["primary"]["micro"].__setitem__(
            "truePositive",
            999,
        ),
        lambda value: value["configurations"][0]["cases"][30].__setitem__(
            "assignments",
            [],
        ),
    ):
        tampered = copy.deepcopy(metrics)
        mutate(tampered)
        tampered["metricsDigest"] = sha256_json(
            {
                key: value
                for key, value in tampered.items()
                if key != "metricsDigest"
            }
        )
        with pytest.raises(ValueError, match="not exactly derivable"):
            _validate_packaged_metrics_derivation(artifacts, tampered)


def test_publication_protocol_schemas_are_checked_in_and_strict():
    schema_root = Path(__file__).parents[1] / "schemas"
    for name, kind in {
        "study-registration.schema.json": (
            "codecrow-magento2-study-registration"
        ),
        "seal-ledger.schema.json": "codecrow-magento2-seal-ledger",
        "judge-evaluation.schema.json": (
            "codecrow-magento2-judge-evaluation"
        ),
        "reproducibility-package.schema.json": (
            "codecrow-magento2-reproducibility-package"
        ),
        "post-fix-replay-plan.schema.json": (
            "codecrow-magento2-post-fix-replay-plan"
        ),
        "post-fix-replay-lock.schema.json": (
            "codecrow-magento2-post-fix-replay-lock"
        ),
        "post-fix-replay-attestation.schema.json": (
            "codecrow-magento2-post-fix-replay-attestation"
        ),
        "post-fix-analysis-run.schema.json": (
            "codecrow-magento2-post-fix-analysis-run"
        ),
        "post-fix-judgment.schema.json": (
            "codecrow-magento2-post-fix-judgment-run"
        ),
        "post-fix-control.schema.json": (
            "codecrow-magento2-post-fix-control"
        ),
        "post-fix-control-set.schema.json": (
            "codecrow-magento2-post-fix-control-set"
        ),
    }.items():
        schema = json.loads((schema_root / name).read_text(encoding="utf-8"))
        assert schema["additionalProperties"] is False
        assert schema["properties"]["kind"]["const"] == kind
        assert schema["required"]
