from __future__ import annotations

import copy
import json
from urllib.parse import quote

import pytest

from magento2_benchmark.corpus import attach_corpus_digest
from magento2_benchmark.execution_corpus import build_execution_corpus
from magento2_benchmark.judge import (
    MATCH_SYSTEM,
    NOVEL_SYSTEM,
    PROMPT_VERSION,
    _gold_prompt,
    _majority_match,
    _maximum_assignment,
    _validate_match_response,
)
from magento2_benchmark.metrics import (
    _analysis_control_digest,
    _pair_prompt_failures,
    _paper_judgment_failures,
    _paper_run_failures,
    build_metrics,
)
from magento2_benchmark.replay import LOCK_KIND, build_plan
from magento2_benchmark.runner import (
    _expected_quality_capture_request,
    _git_diff,
    _git_paths,
    _reconstruct_quality_capture_receipt,
    _request_control_digest,
)
from magento2_benchmark.util import sha256_json, sha256_text

from conftest import make_git_pair, make_judgment, scored_case, write_json


def _two_scored_cases():
    return [
        scored_case(
            "m2b-001",
            gold_count=1,
            candidate_count=2,
            assignments=1,
            novel_verdicts=["invalid"],
        ),
        scored_case(
            "m2b-002",
            gold_count=1,
            candidate_count=0,
            assignments=0,
        ),
    ]


def _sealed_receipt(case):
    selection_policy = {
        "schema": "codecrow.repository-index-selection",
        "includePatterns": ["app/code/**"],
        "excludePatterns": ["vendor/**"],
    }
    return {
        "workspace": "fixture-workspace",
        "project": "fixture-project",
        "branch": case["replay"]["baseRef"],
        "commit": case["snapshot"]["baseSha"],
        "point_count": 11,
        "generation_schema": "codecrow.repository-index-generation",
        "generation_member_count": 10,
        "generation_members_sha256": "1" * 64,
        "generation_manifest_sha256": "2" * 64,
        "source_tree_sha256": "8" * 64,
        "index_include_patterns": selection_policy["includePatterns"],
        "index_exclude_patterns": selection_policy["excludePatterns"],
        "index_selection_policy_sha256": sha256_json(selection_policy),
        "repository_revision": case["snapshot"]["baseSha"],
        "repository_facts_sha256": "3" * 64,
        "plugin_ids": ["php", "magento"],
        "plugin_fingerprint": "sha256:" + "4" * 64,
        "plugin_descriptor_fingerprint": "sha256:" + "5" * 64,
        "plugin_implementation_fingerprint": "sha256:" + "6" * 64,
        "index_representation_fingerprint": "sha256:" + "7" * 64,
    }


def _redacted_request(case, analysis_config, replay_case):
    changed_paths = case["snapshot"]["changedPaths"]
    content = "<?php\n"
    size = len(content.encode("utf-8"))
    return {
        "projectId": analysis_config["project_id"],
        "projectVcsWorkspace": analysis_config["project_vcs_workspace"],
        "projectVcsRepoSlug": analysis_config["project_vcs_repo_slug"],
        "projectWorkspace": analysis_config["project_workspace"],
        "projectNamespace": analysis_config["project_namespace"],
        "aiProvider": analysis_config["provider"],
        "aiModel": "model-paper",
        "aiApiKey": "<redacted>",
        "aiBaseUrl": None,
        "aiCustomParameters": analysis_config["custom_parameters"],
        "analysisType": "PR_REVIEW",
        "targetBranchName": case["replay"]["baseRef"],
        "sourceBranchName": case["replay"]["headRef"],
        "pullRequestId": replay_case["forkPrNumber"],
        "commitHash": case["snapshot"]["headSha"],
        "currentCommitHash": case["snapshot"]["headSha"],
        "baseCommitHash": case["snapshot"]["baseSha"],
        "prTitle": f"Magento 2 review benchmark fixture {case['caseId']}",
        "prDescription": "",
        "prAuthor": "benchmark-fixture",
        "taskContext": {},
        "taskHistoryContext": "",
        "changedFiles": list(changed_paths),
        "deletedFiles": [],
        "diffSnippets": [],
        "rawDiff": (
            f"diff-{int(case['caseId'].rsplit('-', 1)[1])}"
        ),
        "vcsProvider": "github",
        "analysisMode": "FULL",
        "previousCodeAnalysisIssues": [],
        "enrichmentData": {
            "fileContents": [
                {
                    "path": path,
                    "content": content,
                    "sizeBytes": size,
                    "skipped": False,
                    "skipReason": None,
                }
                for path in changed_paths
            ],
            "fileMetadata": [],
            "relationships": [],
            "stats": {
                "totalFilesRequested": len(changed_paths),
                "filesEnriched": len(changed_paths),
                "filesSkipped": 0,
                "relationshipsFound": 0,
                "totalContentSizeBytes": len(changed_paths) * size,
                "processingTimeMs": 0,
                "skipReasons": {},
            },
        },
        "useMcpTools": False,
        "ragEnabled": True,
        "projectRules": "[]",
    }


def _paper_replay_evidence(corpus):
    fork_repository = "benchmark-owner/magento2"
    plan = build_plan(corpus, fork_repository=fork_repository)
    lock_cases = []
    for number, case in enumerate(corpus["cases"], start=123):
        lock_cases.append(
            {
                "caseId": case["caseId"],
                "baseRef": case["replay"]["baseRef"],
                "baseSha": case["snapshot"]["baseSha"],
                "headRef": case["replay"]["headRef"],
                "headSha": case["snapshot"]["headSha"],
                "forkPrNumber": number,
                "forkPrUrl": (
                    f"https://github.com/{fork_repository}/pull/{number}"
                ),
            }
        )
    lock = {
        "kind": LOCK_KIND,
        "generatedAt": "2026-07-29T12:00:00Z",
        "forkRepository": fork_repository,
        "corpusId": corpus["corpusId"],
        "corpusDigest": corpus["corpusDigest"],
        "executionCorpusDigest": plan["executionCorpusDigest"],
        "planDigest": plan["planDigest"],
        "plan": plan,
        "cases": lock_cases,
    }
    lock["lockDigest"] = sha256_json(lock)
    observed_cases = []
    for index, locked in enumerate(lock_cases, start=1):
        refs = {}
        for side in ("base", "head"):
            name = locked[f"{side}Ref"]
            sha = locked[f"{side}Sha"]
            refs[f"{side}Ref"] = {
                "apiPath": (
                    f"/repos/{fork_repository}/git/ref/heads/"
                    f"{quote(name, safe='')}"
                ),
                "name": name,
                "qualifiedName": f"refs/heads/{name}",
                "sha": sha,
                "objectType": "commit",
                "objectApiUrl": (
                    "https://api.github.com/repos/"
                    f"{fork_repository}/git/commits/{sha}"
                ),
            }
        observed_cases.append(
            {
                "caseId": locked["caseId"],
                **refs,
                "pullRequest": {
                    "apiPath": (
                        f"/repos/{fork_repository}/pulls/"
                        f"{locked['forkPrNumber']}"
                    ),
                    "pullRequestId": 50_000 + index,
                    "nodeId": f"PR_attestation_{index}",
                    "number": locked["forkPrNumber"],
                    "htmlUrl": locked["forkPrUrl"],
                    "state": "open",
                    "baseRepository": fork_repository,
                    "baseRef": locked["baseRef"],
                    "baseSha": locked["baseSha"],
                    "headRepository": fork_repository,
                    "headRef": locked["headRef"],
                    "headSha": locked["headSha"],
                },
            }
        )
    attestation = {
        "kind": "codecrow-magento2-replay-attestation",
        "collectedAt": "2026-07-29T12:00:00Z",
        "corpusId": corpus["corpusId"],
        "corpusDigest": corpus["corpusDigest"],
        "executionCorpusDigest": lock["executionCorpusDigest"],
        "replayLockDigest": lock["lockDigest"],
        "planDigest": lock["planDigest"],
        "forkRepository": fork_repository,
        "repositoryObservation": {
            "apiPath": f"/repos/{fork_repository}",
            "repositoryId": 42,
            "nodeId": "R_attestation",
            "fullName": fork_repository,
            "fork": True,
            "upstreamRepository": "magento/magento2",
        },
        "cases": observed_cases,
    }
    attestation["attestationDigest"] = sha256_json(attestation)
    return lock, attestation


def _paper_run(tmp_path, corpus):
    artifact_root = tmp_path / "analysis-paper"
    raw_dir = artifact_root / "raw"
    raw_dir.mkdir(parents=True)
    attempts_dir = raw_dir / "attempts"
    attempts_dir.mkdir()
    analysis_config = {
        "project_id": 123,
        "project_vcs_workspace": "benchmark-owner",
        "project_vcs_repo_slug": "magento2",
        "project_workspace": "fixture-workspace",
        "project_namespace": "fixture-project",
        "rag_workspace": "",
        "rag_project": "",
        "provider": "fixture-provider",
        "model": "model-paper",
        "expected_response_model": "model-paper",
        "base_url": "",
        "custom_parameters": {"temperature": 0},
        "required_repository_plugins": ["php", "magento"],
        "require_exact_index": True,
        "require_retrieval_evidence": True,
        "require_replay_attestation": True,
        "replay_attestation_max_age_seconds": 3_600,
        "require_runtime_provenance": True,
        "require_model_call_evidence": True,
        "quality_capture_container_dir": (
            "/app/logs/review-quality-captures"
        ),
        "max_case_attempts": 1,
        "max_enrichment_file_bytes": 1_000_000,
        "max_enrichment_total_bytes": 50_000_000,
    }
    replay_lock, replay_attestation = _paper_replay_evidence(corpus)
    replay_by_case = {
        case["caseId"]: case for case in replay_lock["cases"]
    }
    receipts = {}
    run_cases = []
    attempt_ledger = []
    for case in corpus["cases"]:
        case_id = case["caseId"]
        replay_case = replay_by_case[case_id]
        receipt = _sealed_receipt(case)
        receipts[case_id] = receipt
        revision_binding = {
            "prIndexed": True,
            "pullRequestId": replay_case["forkPrNumber"],
            "targetBranch": case["replay"]["baseRef"],
            "sourceRevision": case["snapshot"]["headSha"],
            "baseRevision": case["snapshot"]["baseSha"],
            "baseGenerationManifestSha256": receipt[
                "generation_manifest_sha256"
            ],
            "prGenerationFingerprint": "sha256:" + "7" * 64,
            "prOverlayGenerationManifestSha256": "8" * 64,
            "basePluginFingerprint": receipt["plugin_fingerprint"],
            "basePluginDescriptorFingerprint": receipt[
                "plugin_descriptor_fingerprint"
            ],
            "basePluginImplementationFingerprint": receipt[
                "plugin_implementation_fingerprint"
            ],
            "baseIndexRepresentationFingerprint": receipt[
                "index_representation_fingerprint"
            ],
        }
        event = {
            "type": "status",
            "state": "review_evidence_completed",
            "reviewUnits": {"registered": 1, "completed": 1},
            "retrieval": {
                "deterministicStates": ["complete"],
                "semanticFailures": 0,
                "semanticDisabled": False,
                "exactEvidenceIds": 1,
            },
            "revisionBinding": revision_binding,
        }
        finalization = {
            "kind": "codecrow-isolated-analysis-finalization",
            "rawIssueCount": 0,
            "finalIssueCount": 0,
            "issues": [],
            "analysisDataValidated": True,
            "persisted": False,
            "published": False,
            "previousIssueStateUsed": False,
        }
        redacted_request = _redacted_request(
            case,
            analysis_config,
            replay_case,
        )
        job_id = f"analysis-paper:{case_id}:fixture"
        response = {"issues": []}
        capture_container_path = (
            "/app/logs/review-quality-captures/"
            f"project-123-review-{replay_case['forkPrNumber']}-"
            f"{int(case_id.rsplit('-', 1)[1]):032x}.json"
        )
        capture = {
            "kind": "review-quality-candidate-capture",
            "status": "completed",
            "provider": "fixture-provider",
            "model": "model-paper",
            "request": _expected_quality_capture_request(
                redacted_request
            ),
            "requestDigest": None,
            "modelBoundaryInvocations": 1,
            "providerCalls": 1,
            "calls": [
                {
                    "sequence": 1,
                    "stage": "stage_1",
                    "status": "completed",
                    "providerCallCount": 1,
                    "promptDigest": sha256_text(
                        f"prompt:{case_id}"
                    ),
                    "responseDigest": sha256_json(response),
                    "providerEvents": [
                        {
                            "status": "completed",
                            "providerReportedModels": ["model-paper"],
                            "response": response,
                        }
                    ],
                }
            ],
            "captureDigest": None,
        }
        capture["requestDigest"] = sha256_json(capture["request"])
        capture["captureDigest"] = sha256_json(capture)
        attempt_id = (
            "attempt-"
            f"{int(case_id.rsplit('-', 1)[1]):032x}"
        )
        capture_relative = (
            f"raw/attempts/{attempt_id}-quality-capture.json"
        )
        write_json(artifact_root / capture_relative, capture)
        capture_receipt = _reconstruct_quality_capture_receipt(
            capture,
            artifact_container_path=capture_container_path,
        )
        model_call_evidence = {
            "receipt": capture_receipt,
            "artifact": capture_relative,
            "artifactDigest": sha256_json(capture),
        }
        retrieval_evidence = {
            "state": "review_evidence_completed",
            "reviewUnits": {"registered": 1, "completed": 1},
            "retrieval": {
                "deterministicStates": ["complete"],
                "semanticFailures": 0,
                "semanticDisabled": False,
                "exactEvidenceIds": 1,
            },
            "revisionBinding": revision_binding,
            "evidenceSha256": sha256_json(event),
        }
        product_finalization = {
            "kind": "codecrow-isolated-analysis-finalization",
            "rawIssueCount": 0,
            "finalIssueCount": 0,
            "responseDigest": sha256_json(finalization),
            "analysisDataValidated": True,
            "persisted": False,
            "published": False,
            "previousIssueStateUsed": False,
        }
        request_digest = sha256_json(redacted_request)
        request_control_digest = _request_control_digest(
            redacted_request
        )
        started_at = "2026-07-29T12:00:00Z"
        completed_at = "2026-07-29T12:00:01Z"
        start_relative = f"raw/attempts/{attempt_id}-start.json"
        result_relative = f"raw/attempts/{attempt_id}-result.json"
        start_artifact = {
            "kind": "codecrow-magento2-analysis-attempt-start",
            "attemptId": attempt_id,
            "caseId": case_id,
            "attemptNumber": 1,
            "jobId": job_id,
            "startedAt": started_at,
            "maxAttempts": 1,
            "requestDigest": request_digest,
            "requestControlDigest": request_control_digest,
            "redactedRequest": redacted_request,
        }
        write_json(artifact_root / start_relative, start_artifact)
        raw = {
            "kind": "codecrow-magento2-analysis-attempt-result",
            "attemptId": attempt_id,
            "caseId": case_id,
            "attemptNumber": 1,
            "jobId": job_id,
            "status": "completed",
            "startedAt": started_at,
            "completedAt": completed_at,
            "durationSeconds": 1.0,
            "requestDigest": request_digest,
            "requestControlDigest": request_control_digest,
            "redactedRequest": redacted_request,
            "events": [
                event,
                {
                    "type": "status",
                    "state": "review_quality_capture_completed",
                    "qualityCapture": capture_receipt,
                },
                {
                    "type": "final",
                    "jobId": job_id,
                    "result": response,
                },
            ],
            "response": response,
            "productFinalization": finalization,
            "modelCallEvidence": model_call_evidence,
            "caseOutcome": {
                "analysisState": None,
                "retrievalEvidence": retrieval_evidence,
                "modelCallEvidence": model_call_evidence,
                "productFinalization": product_finalization,
                "findings": [],
            },
            "error": None,
            "terminationReason": "case_completed",
            "stoppingReason": "case_completed",
            "retryEligible": False,
        }
        write_json(artifact_root / result_relative, raw)
        raw_digest = sha256_json(raw)
        attempt_ledger.append(
            {
                "attemptId": attempt_id,
                "caseId": case_id,
                "attemptNumber": 1,
                "jobId": job_id,
                "status": "completed",
                "startedAt": started_at,
                "completedAt": completed_at,
                "durationSeconds": 1.0,
                "requestDigest": request_digest,
                "requestControlDigest": request_control_digest,
                "startArtifact": start_relative,
                "startArtifactDigest": sha256_json(start_artifact),
                "resultArtifact": result_relative,
                "resultArtifactDigest": raw_digest,
                "modelCallEvidence": model_call_evidence,
                "error": None,
                "stoppingReason": "case_completed",
                "retryEligible": False,
            }
        )
        run_cases.append(
            {
                "caseId": case_id,
                "jobId": job_id,
                "attemptId": attempt_id,
                "attemptNumber": 1,
                "attemptCount": 1,
                "maxAttempts": 1,
                "sizeBand": case["sizeBand"],
                "partition": case["partition"],
                "status": "completed",
                "startedAt": started_at,
                "completedAt": completed_at,
                "durationSeconds": 1.0,
                "requestDigest": request_digest,
                "requestControlDigest": request_control_digest,
                "responseDigest": raw_digest,
                "rawResponse": result_relative,
                "analysisState": None,
                "retrievalEvidence": retrieval_evidence,
                "productFinalization": product_finalization,
                "modelCallEvidence": model_call_evidence,
                "findings": [],
                "error": None,
                "indexReceipt": receipt,
                "retryEligible": False,
                "stoppingReason": "case_completed",
            }
        )
    runtime = {}
    for index, service in enumerate(("analysis", "rag", "finalizer"), start=9):
        runtime[service] = {
            "containerId": f"{index:x}" * 64,
            "imageId": "sha256:" + f"{index + 3:x}" * 64,
            "imageReference": f"codecrow/{service}:fixture",
        }
    runtime["required"] = True
    write_json(artifact_root / "replay-lock.json", replay_lock)
    execution_corpus = build_execution_corpus(corpus)
    write_json(
        artifact_root / "analysis-execution-corpus.json",
        execution_corpus,
    )
    write_json(
        artifact_root / "replay-attestation.json",
        replay_attestation,
    )
    run = {
        "kind": "codecrow-magento2-analysis-run",
        "runId": "analysis-paper",
        "startedAt": "2026-07-29T12:00:00Z",
        "completedAt": "2026-07-29T12:00:00Z",
        "corpusDigest": corpus["corpusDigest"],
        "executionCorpusDigest": execution_corpus[
            "executionCorpusDigest"
        ],
        "executionCorpusArtifact": "analysis-execution-corpus.json",
        "analysisModel": "model-paper",
        "analysisProvider": "fixture-provider",
        "analysisModelRoles": {
            "reviewPipeline": "model-paper",
            "reviewPipelineRequested": "model-paper",
            "reviewPipelineExpectedResponse": "model-paper",
            "reviewPipelineProviderReported": ["model-paper"],
            "providerReportedByStage": {
                "stage_1": ["model-paper"],
            },
        },
        "analysisConfig": analysis_config,
        "analysisConfigDigest": sha256_json(analysis_config),
        "replayLockDigest": replay_lock["lockDigest"],
        "replayLockArtifact": "replay-lock.json",
        "replayAttestationDigest": replay_attestation[
            "attestationDigest"
        ],
        "replayAttestationArtifact": "replay-attestation.json",
        "runtimeProvenance": runtime,
        "selectedCaseIds": [case["caseId"] for case in corpus["cases"]],
        "transport": "redis",
        "findingSemantics": "java-finalized-transient-first-iteration",
        "attemptPolicy": {
            "maxAttemptsPerCase": 1,
            "attemptsPerInvocation": 1,
            "retryTrigger": "explicit-resume",
            "exhaustedCaseStatus": "failed",
        },
        "cases": run_cases,
        "attemptLedger": attempt_ledger,
        "indexReceiptsBefore": copy.deepcopy(receipts),
        "indexReceiptsAfter": copy.deepcopy(receipts),
        "status": "completed",
    }
    return run, artifact_root


def _prepend_failed_paper_attempt(run, artifact_root):
    run["analysisConfig"]["max_case_attempts"] = 2
    run["analysisConfigDigest"] = sha256_json(run["analysisConfig"])
    run["attemptPolicy"]["maxAttemptsPerCase"] = 2
    attempts = {
        item["caseId"]: item for item in run["attemptLedger"]
    }
    cases = {item["caseId"]: item for item in run["cases"]}
    for case_id, attempt in attempts.items():
        case = cases[case_id]
        case["maxAttempts"] = 2
        start_path = artifact_root / attempt["startArtifact"]
        start = json.loads(start_path.read_text())
        start["maxAttempts"] = 2
        if case_id == "m2b-001":
            start["attemptNumber"] = 2
            attempt["attemptNumber"] = 2
            case["attemptNumber"] = 2
            case["attemptCount"] = 2
            result_path = artifact_root / attempt["resultArtifact"]
            result = json.loads(result_path.read_text())
            result["attemptNumber"] = 2
            write_json(result_path, result)
            attempt["resultArtifactDigest"] = sha256_json(result)
            case["responseDigest"] = attempt["resultArtifactDigest"]
        write_json(start_path, start)
        attempt["startArtifactDigest"] = sha256_json(start)

    completed = attempts["m2b-001"]
    completed_index = run["attemptLedger"].index(completed)
    case = cases["m2b-001"]
    current_start = json.loads(
        (artifact_root / completed["startArtifact"]).read_text()
    )
    prior_id = "attempt-" + "f" * 32
    prior_job = "analysis-paper:m2b-001:failed"
    prior_start_relative = f"raw/attempts/{prior_id}-start.json"
    prior_result_relative = f"raw/attempts/{prior_id}-result.json"
    prior_start = {
        **current_start,
        "attemptId": prior_id,
        "attemptNumber": 1,
        "jobId": prior_job,
    }
    write_json(artifact_root / prior_start_relative, prior_start)
    prior_error = "RuntimeError: fixture failed attempt"
    prior_result = {
        "kind": "codecrow-magento2-analysis-attempt-result",
        "attemptId": prior_id,
        "caseId": "m2b-001",
        "attemptNumber": 1,
        "jobId": prior_job,
        "status": "failed",
        "startedAt": prior_start["startedAt"],
        "completedAt": "2026-07-29T12:00:00Z",
        "durationSeconds": 0.5,
        "requestDigest": prior_start["requestDigest"],
        "requestControlDigest": prior_start["requestControlDigest"],
        "redactedRequest": prior_start["redactedRequest"],
        "events": [],
        "response": None,
        "productFinalization": None,
        "modelCallEvidence": None,
        "caseOutcome": {
            "analysisState": None,
            "retrievalEvidence": None,
            "modelCallEvidence": None,
            "productFinalization": None,
            "findings": [],
        },
        "error": prior_error,
        "terminationReason": "case_exception",
        "stoppingReason": "explicit_resume_required",
        "retryEligible": True,
    }
    write_json(artifact_root / prior_result_relative, prior_result)
    prior_attempt = {
        "attemptId": prior_id,
        "caseId": "m2b-001",
        "attemptNumber": 1,
        "jobId": prior_job,
        "status": "failed",
        "startedAt": prior_start["startedAt"],
        "completedAt": prior_result["completedAt"],
        "durationSeconds": prior_result["durationSeconds"],
        "requestDigest": prior_start["requestDigest"],
        "requestControlDigest": prior_start["requestControlDigest"],
        "startArtifact": prior_start_relative,
        "startArtifactDigest": sha256_json(prior_start),
        "resultArtifact": prior_result_relative,
        "resultArtifactDigest": sha256_json(prior_result),
        "modelCallEvidence": None,
        "error": prior_error,
        "stoppingReason": "explicit_resume_required",
        "retryEligible": True,
    }
    run["attemptLedger"].insert(completed_index, prior_attempt)
    return prior_attempt, case


def _stub_analysis_source_reconstruction(
    monkeypatch,
    *,
    artifact_root,
    run,
    repository,
):
    repository.mkdir()
    (repository / ".git").mkdir()
    requests = {}
    for case in run["cases"]:
        raw = json.loads(
            (artifact_root / case["rawResponse"]).read_text()
        )
        request = raw["redactedRequest"]
        request["aiApiKey"] = "runtime-fixture-secret"
        requests[case["caseId"]] = request

    def reconstruct(**kwargs):
        return copy.deepcopy(requests[kwargs["case"]["caseId"]])

    monkeypatch.setattr(
        "magento2_benchmark.metrics._request_payload",
        reconstruct,
    )
    return repository


def _paper_judgment(tmp_path, corpus, run):
    artifact_root = tmp_path / "judgment-paper"
    raw_dir = artifact_root / "raw"
    raw_dir.mkdir(parents=True)
    cases = [
        scored_case(
            case["caseId"],
            gold_count=len(case["goldenComments"]),
            candidate_count=0,
            assignments=0,
        )
        for case in corpus["cases"]
    ]
    judgment = make_judgment(
        corpus,
        suffix="paper",
        analysis_model=run["analysisModel"],
        cases=cases,
    )
    judge_config = {"model": judgment["judgeModel"], "temperature": 0}
    judge_config_digest = sha256_json(judge_config)
    run_cases = {case["caseId"]: case for case in run["cases"]}
    for source_case, case in zip(corpus["cases"], cases, strict=True):
        case_input_digest = sha256_json(
            {
                "corpusCase": source_case,
                "analysisCase": run_cases[source_case["caseId"]],
                "analysisRunDigest": run["runDigest"],
                "judgeConfigDigest": judge_config_digest,
                "promptVersion": PROMPT_VERSION,
            }
        )
        case["caseInputDigest"] = case_input_digest
        case["judgeConfigDigest"] = judge_config_digest
        case["sizeBand"] = source_case["sizeBand"]
        case["partition"] = source_case["partition"]
        case["calls"] = []
        case["caseDigest"] = sha256_json(case)
        raw_path = raw_dir / f"{source_case['caseId']}.json"
        write_json(raw_path, case)
        case["rawJudgment"] = str(raw_path.relative_to(artifact_root))
    judgment.update(
        {
            "promptVersion": PROMPT_VERSION,
            "promptDigest": sha256_text(
                MATCH_SYSTEM + NOVEL_SYSTEM + PROMPT_VERSION
            ),
            "corpusId": corpus["corpusId"],
            "analysisRunId": run["runId"],
            "analysisRunDigest": run["runDigest"],
            "analysisModel": run["analysisModel"],
            "judgeConfig": judge_config,
            "judgeConfigDigest": judge_config_digest,
        }
    )
    judgment.pop("judgmentDigest")
    judgment["judgmentDigest"] = sha256_json(judgment)
    return judgment, artifact_root


def _single_call_paper_judgment(tmp_path, corpus):
    source_case = corpus["cases"][0]
    finding = {
        "path": source_case["snapshot"]["changedPaths"][0],
        "line": 1,
        "title": "Fixture candidate",
        "description": "The wrong collaborator is called.",
        "category": "bug",
        "severity": "medium",
        "suggestedFix": "Call the correct collaborator.",
    }
    run_case = {
        "caseId": source_case["caseId"],
        "status": "completed",
        "findings": [finding],
    }
    run = {
        "runId": "analysis-single-call",
        "analysisModel": "analysis-fixture",
        "cases": [run_case],
    }
    run["runDigest"] = sha256_json(run)
    judge_config = {
        "model": "judge-fixture",
        "expected_response_model": "judge-fixture-immutable",
        "temperature": 0,
        "repeats": 1,
        "validate_unmatched_findings": False,
    }
    judge_config_digest = sha256_json(judge_config)
    case_input_digest = sha256_json(
        {
            "corpusCase": source_case,
            "analysisCase": run_case,
            "analysisRunDigest": run["runDigest"],
            "judgeConfigDigest": judge_config_digest,
            "promptVersion": PROMPT_VERSION,
        }
    )
    evidence = {
        "inFrozenDiff": True,
        "lineOnAddedRightSide": True,
        "pathDiff": "@@ -0,0 +1 @@\n+bad_call();",
        "headSourceWindow": "     1 bad_call();",
        "pathDiffSha256": sha256_text("@@ -0,0 +1 @@\n+bad_call();"),
        "headSourceSha256": sha256_text("bad_call();\n"),
    }
    prompt = _gold_prompt(
        gold_label="G001",
        gold=source_case["goldenComments"][0],
        findings=[finding],
        candidate_evidence=[evidence],
    )
    response = {
        "gold_id": "G001",
        "judgments": [
            {
                "candidate_id": "C001",
                "specific_issue": "yes",
                "grounded_at_snapshot": "yes",
                "same_root_cause": "yes",
                "same_failure_or_consequence": "yes",
                "compatible_required_change": "yes",
                "location_relation": "same_symbol",
                "verdict": "substantive_match",
                "confidence": 0.9,
                "rationale": "The same incorrect call is identified.",
            }
        ],
    }
    content = json.dumps(response)
    request = {
        "model": judge_config["model"],
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": MATCH_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    provider_response = {
        "id": "response-fixture",
        "model": "judge-fixture-immutable",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        "choices": [{"message": {"content": content}}],
    }
    metadata = {
        "usage": provider_response["usage"],
        "responseId": provider_response["id"],
        "model": provider_response["model"],
        "promptSha256": sha256_text(MATCH_SYSTEM + "\n" + prompt),
        "rawContentSha256": sha256_text(content),
        "request": request,
        "requestSha256": sha256_json(request),
        "providerResponse": provider_response,
        "providerResponseSha256": sha256_json(provider_response),
    }
    binding = {
        "kind": "pair",
        "caseId": source_case["caseId"],
        "goldId": "G001",
        "repeat": 1,
        "caseInputDigest": case_input_digest,
        "judgeConfigDigest": judge_config_digest,
    }
    checkpoint = {
        "bindingDigest": sha256_json(
            {
                **binding,
                "systemSha256": sha256_text(MATCH_SYSTEM),
                "promptSha256": sha256_text(prompt),
            }
        ),
        "completedAt": "2026-07-29T12:00:00Z",
        "system": MATCH_SYSTEM,
        "prompt": prompt,
        "response": response,
        "metadata": metadata,
        "rejectedStructuredResponses": [],
    }
    checkpoint["callDigest"] = sha256_json(checkpoint)
    artifact_root = tmp_path / "judgment-single-call"
    checkpoint_path = artifact_root / "checkpoints/m2b-001/pair.json"
    checkpoint_path.parent.mkdir(parents=True)
    write_json(checkpoint_path, checkpoint)
    call = {
        "kind": "pair",
        "goldId": "G001",
        "repeat": 1,
        "checkpoint": str(checkpoint_path.relative_to(artifact_root)),
        **{
            key: value
            for key, value in checkpoint.items()
            if key != "metadata"
        },
        **metadata,
    }
    normalized = _validate_match_response(
        response,
        gold_label="G001",
        candidate_count=1,
    )
    pair = {
        "goldId": "G001",
        "candidateId": "C001",
        **_majority_match([normalized[0]]),
    }
    assignments = _maximum_assignment(1, 1, [pair])
    gold = source_case["goldenComments"][0]
    raw = {
        "caseId": source_case["caseId"],
        "caseInputDigest": case_input_digest,
        "judgeConfigDigest": judge_config_digest,
        "status": "scored",
        "sizeBand": source_case["sizeBand"],
        "partition": source_case["partition"],
        "goldCount": 1,
        "candidateCount": 1,
        "goldIssues": [
            {
                "goldId": "G001",
                "sourceId": gold["id"],
                "sourceUrl": gold["sourceUrl"],
                "path": gold["path"],
                "line": gold["originalLine"],
                "reviewComment": gold["body"],
                "summary": gold["expectedIssue"]["summary"],
                "category": gold["expectedIssue"]["category"],
                "severity": gold["expectedIssue"]["severity"],
            }
        ],
        "candidateFindings": [{"candidateId": "C001", **finding}],
        "pairJudgments": [pair],
        "assignments": assignments,
        "unmatchedGold": [],
        "unmatchedCandidates": [],
        "novelFindingJudgments": [],
        "calls": [call],
    }
    raw["caseDigest"] = sha256_json(raw)
    raw_path = artifact_root / "raw/m2b-001.json"
    raw_path.parent.mkdir(parents=True)
    write_json(raw_path, raw)
    judgment_case = {
        **raw,
        "rawJudgment": str(raw_path.relative_to(artifact_root)),
    }
    judgment = {
        "kind": "codecrow-magento2-judgment-run",
        "judgmentId": "judgment-single-call",
        "promptVersion": PROMPT_VERSION,
        "promptDigest": sha256_text(
            MATCH_SYSTEM + NOVEL_SYSTEM + PROMPT_VERSION
        ),
        "corpusDigest": corpus["corpusDigest"],
        "analysisRunId": run["runId"],
        "analysisRunDigest": run["runDigest"],
        "analysisModel": run["analysisModel"],
        "judgeModel": judge_config["model"],
        "judgeConfig": judge_config,
        "judgeConfigDigest": judge_config_digest,
        "cases": [judgment_case],
    }
    judgment["judgmentDigest"] = sha256_json(judgment)
    return judgment, run, artifact_root, checkpoint_path


def test_metrics_use_explicit_reference_set_fp_label_and_report_coverage(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    judgment = make_judgment(
        corpus,
        suffix="one",
        analysis_model="model-one",
        cases=_two_scored_cases(),
    )

    result = build_metrics(
        corpus_path=write_json(tmp_path / "corpus.json", corpus),
        judgment_paths=[write_json(tmp_path / "judgment.json", judgment)],
        bootstrap_iterations=40,
        seed=77,
    )
    config = result["configurations"][0]

    assert config["coverage"]["scoredCases"] == 2
    assert config["coverage"]["totalCases"] == 50
    assert config["coverage"]["rate"] == 0.04
    assert len(config["coverage"]["notScored"]) == 48
    assert config["coverage"]["uncertainCases"] == 0
    assert config["coverage"]["uncertainty"] == []
    assert config["primaryScope"] == {
        "partition": "sealed",
        "confirmatory": True,
    }
    assert config["confirmatoryCoverage"]["scoredCases"] == 0
    assert config["primary"]["micro"]["counts"] == {
        "truePositive": 0,
        "falseNegative": 0,
        "referenceSetFalsePositive": 0,
        "goldIssues": 0,
        "candidateFindings": 0,
    }
    secondary = config["secondary"]["allCases"]
    assert secondary["micro"]["counts"] == {
        "truePositive": 1,
        "falseNegative": 1,
        "referenceSetFalsePositive": 1,
        "goldIssues": 2,
        "candidateFindings": 2,
    }
    assert secondary["micro"]["precision"] == 0.5
    assert secondary["micro"]["recall"] == 0.5
    assert secondary["micro"]["f1"] == 0.5
    assert "falsePositive" not in secondary["micro"]["counts"]
    assert "reference-set false positives" in config["primary"]["definition"]
    assert config["adjudicated"]["confirmedFindingPrecision"] is None
    assert config["adjudicated"]["invalid"] == 0
    assert result["methodology"]["paperReady"] is False
    assert result["methodology"]["analysisArtifactsBound"] is False
    assert "metricsVersion" not in result


def test_metrics_reconstructs_deterministically_compacted_pair_prompt(
    corpus_factory,
):
    corpus = corpus_factory()
    gold = corpus["cases"][0]["goldenComments"][0]
    finding = {
        "path": gold["path"],
        "line": gold["originalLine"],
        "title": "T" * 20_000,
        "description": "D" * 20_000,
        "category": "bug",
        "severity": "medium",
        "suggestedFix": "F" * 20_000,
    }
    path_diff = "@@ -0,0 +1 @@\n+" + ("x" * 20_000)
    head_source = "x" * 20_000
    evidence = {
        "inFrozenDiff": True,
        "lineOnAddedRightSide": True,
        "pathDiff": path_diff,
        "headSourceWindow": head_source,
        "pathDiffSha256": sha256_text(path_diff),
        "headSourceSha256": sha256_text(head_source),
    }
    prompt = _gold_prompt(
        gold_label="G001",
        gold=gold,
        findings=[finding],
        candidate_evidence=[evidence],
        max_prompt_characters=10_000,
    )

    assert len(prompt) <= 10_000
    assert '"evidence_compaction"' in prompt
    assert _pair_prompt_failures(
        prompt,
        gold_label="G001",
        gold=gold,
        findings=[finding],
        changed_paths={gold["path"]},
        max_prompt_characters=10_000,
        expected_evidence=[evidence],
    ) == []


def test_pairwise_bootstrap_is_paired_and_deterministic(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    first = make_judgment(
        corpus,
        suffix="first",
        analysis_model="model-a",
        cases=[
            scored_case(
                "m2b-031",
                gold_count=1,
                candidate_count=2,
                assignments=1,
                novel_verdicts=["invalid"],
            ),
            scored_case(
                "m2b-032",
                gold_count=1,
                candidate_count=0,
                assignments=0,
            ),
        ],
    )
    second = make_judgment(
        corpus,
        suffix="second",
        analysis_model="model-b",
        cases=[
            scored_case(
                "m2b-031",
                gold_count=1,
                candidate_count=1,
                assignments=1,
            ),
            scored_case(
                "m2b-032",
                gold_count=1,
                candidate_count=1,
                assignments=1,
            ),
        ],
    )
    corpus_path = write_json(tmp_path / "corpus.json", corpus)
    paths = [
        write_json(tmp_path / "first.json", first),
        write_json(tmp_path / "second.json", second),
    ]

    result_one = build_metrics(
        corpus_path=corpus_path,
        judgment_paths=paths,
        bootstrap_iterations=120,
        seed=12345,
    )
    result_two = build_metrics(
        corpus_path=corpus_path,
        judgment_paths=paths,
        bootstrap_iterations=120,
        seed=12345,
    )

    comparison_one = result_one["pairwiseComparisons"][0]
    comparison_two = result_two["pairwiseComparisons"][0]
    assert comparison_one == comparison_two
    assert comparison_one["commonScoredCases"] == 2
    assert comparison_one["comparisonScope"] == {
        "partition": "sealed",
        "confirmatory": True,
    }
    assert comparison_one["differenceDirection"] == "right_minus_left"
    assert comparison_one["macroPerCaseF1Delta"] == 0.666667
    assert comparison_one["microDeltaOnCommonCases"] == {
        "precision": 0.5,
        "recall": 0.5,
        "f1": 0.5,
    }
    assert (
        comparison_one["macroPerCaseF1DeltaConfidenceInterval95"]["iterations"]
        == 120
    )
    assert (
        comparison_one["microDeltaConfidenceInterval95"]["f1"]["iterations"]
        == 120
    )


def test_unverifiable_pair_excludes_entire_case_and_surfaces_uncertainty(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    uncertain_case = scored_case(
        "m2b-031",
        gold_count=1,
        candidate_count=1,
        assignments=0,
    )
    uncertain_case["pairJudgments"][0]["verdict"] = "unverifiable"
    judgment = make_judgment(
        corpus,
        suffix="uncertain",
        analysis_model="model",
        cases=[uncertain_case],
    )

    result = build_metrics(
        corpus_path=write_json(tmp_path / "corpus.json", corpus),
        judgment_paths=[
            write_json(tmp_path / "judgment.json", judgment)
        ],
        bootstrap_iterations=0,
    )
    config = result["configurations"][0]

    assert config["coverage"]["scoredCases"] == 0
    assert config["coverage"]["uncertainCases"] == 1
    assert config["coverage"]["uncertainty"] == [
        {
            "caseId": "m2b-031",
            "partition": "sealed",
            "reason": "judge_unverifiable_pair",
            "unverifiablePairs": 1,
        }
    ]
    assert config["confirmatoryCoverage"]["uncertainCases"] == 1
    assert config["cases"] == []
    assert config["primary"]["micro"]["counts"] == {
        "truePositive": 0,
        "falseNegative": 0,
        "referenceSetFalsePositive": 0,
        "goldIssues": 0,
        "candidateFindings": 0,
    }


def test_metrics_reject_tampered_judgment_digest(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    judgment = make_judgment(
        corpus,
        suffix="tampered",
        analysis_model="model",
        cases=_two_scored_cases(),
    )
    judgment["analysisModel"] = "changed-after-signing"

    with pytest.raises(ValueError, match="judgmentDigest mismatch"):
        build_metrics(
            corpus_path=write_json(tmp_path / "corpus.json", corpus),
            judgment_paths=[
                write_json(tmp_path / "judgment.json", judgment)
            ],
            bootstrap_iterations=0,
        )


def test_metrics_reject_gold_count_that_disagrees_with_corpus(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    inconsistent = scored_case(
        "m2b-001",
        gold_count=0,
        candidate_count=1,
        assignments=0,
    )
    judgment = make_judgment(
        corpus,
        suffix="bad-count",
        analysis_model="model",
        cases=[inconsistent],
    )

    with pytest.raises(ValueError, match="goldCount.*corpus"):
        build_metrics(
            corpus_path=write_json(tmp_path / "corpus.json", corpus),
            judgment_paths=[
                write_json(tmp_path / "judgment.json", judgment)
            ],
            bootstrap_iterations=0,
        )


def test_metrics_reject_non_one_to_one_assignment_artifact(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    first_case = corpus["cases"][0]
    second_gold = copy.deepcopy(first_case["goldenComments"][0])
    second_gold["id"] += "-second"
    second_gold["sourceCommentId"] += 1_000_000
    second_gold["reviewId"] += 1_000_000
    second_gold["sourceUrl"] += "-second"
    first_case["goldenComments"].append(second_gold)
    from magento2_benchmark.corpus import attach_corpus_digest

    corpus = attach_corpus_digest(corpus)
    scored = scored_case(
        "m2b-001",
        gold_count=2,
        candidate_count=2,
        assignments=2,
    )
    scored["assignments"][1]["goldId"] = "G001"
    judgment = make_judgment(
        corpus,
        suffix="duplicate-edge",
        analysis_model="model",
        cases=[scored],
    )

    with pytest.raises(ValueError, match="one-to-one|duplicate.*gold"):
        build_metrics(
            corpus_path=write_json(tmp_path / "corpus.json", corpus),
            judgment_paths=[
                write_json(tmp_path / "judgment.json", judgment)
            ],
            bootstrap_iterations=0,
        )


def test_paper_run_gate_accepts_complete_bound_product_evidence(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    run, artifact_root = _paper_run(tmp_path, corpus)

    assert _paper_run_failures(
        run,
        corpus_cases={
            case["caseId"]: case for case in corpus["cases"]
        },
        artifact_root=artifact_root,
    ) == []


def test_paper_run_gate_requires_model_call_evidence_on_every_case(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    run, artifact_root = _paper_run(tmp_path, corpus)
    run["cases"][0]["modelCallEvidence"] = None

    failures = _paper_run_failures(
        run,
        corpus_cases={
            case["caseId"]: case for case in corpus["cases"]
        },
        artifact_root=artifact_root,
    )

    assert (
        "m2b-001:model_call_evidence_attempt_binding_mismatch"
        in failures
    )
    assert "m2b-001:model_call_evidence_invalid" in failures


@pytest.mark.parametrize(
    "mutation",
    ["omit_history", "missing_start", "resealed_stopping_policy"],
)
def test_paper_run_gate_reopens_complete_prior_attempt_history(
    tmp_path,
    corpus_factory,
    mutation,
):
    corpus = corpus_factory()
    run, artifact_root = _paper_run(tmp_path, corpus)
    prior, _ = _prepend_failed_paper_attempt(run, artifact_root)
    corpus_cases = {
        case["caseId"]: case for case in corpus["cases"]
    }
    assert _paper_run_failures(
        run,
        corpus_cases=corpus_cases,
        artifact_root=artifact_root,
    ) == []

    if mutation == "omit_history":
        run["attemptLedger"].remove(prior)
    elif mutation == "missing_start":
        (artifact_root / prior["startArtifact"]).unlink()
    else:
        result_path = artifact_root / prior["resultArtifact"]
        result = json.loads(result_path.read_text())
        result["stoppingReason"] = "attempt_limit_reached"
        result["retryEligible"] = False
        write_json(result_path, result)
        prior["resultArtifactDigest"] = sha256_json(result)
        prior["stoppingReason"] = "attempt_limit_reached"
        prior["retryEligible"] = False

    failures = _paper_run_failures(
        run,
        corpus_cases=corpus_cases,
        artifact_root=artifact_root,
    )

    assert "analysis_attempt_ledger_invalid" in failures


@pytest.mark.parametrize("mutation", ["request", "provider_model"])
def test_paper_run_gate_rejects_self_consistent_capture_substitution(
    tmp_path,
    corpus_factory,
    mutation,
):
    corpus = corpus_factory()
    run, artifact_root = _paper_run(tmp_path, corpus)
    run_case = run["cases"][0]
    evidence = run_case["modelCallEvidence"]
    artifact_path = artifact_root / evidence["artifact"]
    artifact = json.loads(artifact_path.read_text())
    if mutation == "request":
        artifact["request"]["aiCustomParameters"]["temperature"] = 0.5
        artifact["requestDigest"] = sha256_json(artifact["request"])
    else:
        artifact["calls"][0]["providerEvents"][0][
            "providerReportedModels"
        ] = ["substituted-provider-model"]
    artifact["captureDigest"] = None
    artifact["captureDigest"] = sha256_json(artifact)
    receipt = _reconstruct_quality_capture_receipt(
        artifact,
        artifact_container_path=evidence["receipt"][
            "artifactContainerPath"
        ],
    )
    rebound = {
        "receipt": receipt,
        "artifact": evidence["artifact"],
        "artifactDigest": sha256_json(artifact),
    }
    write_json(artifact_path, artifact)
    run_case["modelCallEvidence"] = rebound
    attempt = next(
        item
        for item in run["attemptLedger"]
        if item["caseId"] == run_case["caseId"]
    )
    attempt["modelCallEvidence"] = rebound
    raw_path = artifact_root / run_case["rawResponse"]
    raw = json.loads(raw_path.read_text())
    raw["modelCallEvidence"] = rebound
    write_json(raw_path, raw)
    run_case["responseDigest"] = sha256_json(raw)
    attempt["resultArtifactDigest"] = run_case["responseDigest"]

    failures = _paper_run_failures(
        run,
        corpus_cases={
            case["caseId"]: case for case in corpus["cases"]
        },
        artifact_root=artifact_root,
    )

    assert "m2b-001:model_call_evidence_invalid" in failures


@pytest.mark.parametrize(
    ("field", "value", "expected_failure"),
    [
        (
            "baseCommitHash",
            "f" * 40,
            "m2b-001:request_identity_or_configuration_mismatch",
        ),
        (
            "rawDiff",
            "tampered diff",
            "m2b-001:request_diff_mismatch",
        ),
        (
            "aiApiKey",
            "leaked-secret",
            "m2b-001:request_identity_or_configuration_mismatch",
        ),
    ],
)
def test_paper_run_gate_rejects_self_consistent_request_tampering(
    tmp_path,
    corpus_factory,
    field,
    value,
    expected_failure,
):
    corpus = corpus_factory()
    run, artifact_root = _paper_run(tmp_path, corpus)
    run_case = run["cases"][0]
    raw_path = artifact_root / run_case["rawResponse"]
    raw = json.loads(raw_path.read_text())
    raw["redactedRequest"][field] = value
    run_case["requestDigest"] = sha256_json(raw["redactedRequest"])
    run_case["requestControlDigest"] = _request_control_digest(
        raw["redactedRequest"]
    )
    run_case["responseDigest"] = sha256_json(raw)
    write_json(raw_path, raw)

    failures = _paper_run_failures(
        run,
        corpus_cases={
            case["caseId"]: case for case in corpus["cases"]
        },
        artifact_root=artifact_root,
    )

    assert expected_failure in failures


@pytest.mark.parametrize(
    ("mutation", "expected_failure"),
    [
        ("missing_final", "m2b-001:event_stream_final_count_invalid"),
        ("mismatched_final", "m2b-001:event_stream_final_response_mismatch"),
        ("error_event", "m2b-001:event_stream_contains_error"),
        ("raw_job", "m2b-001:event_stream_job_binding_mismatch"),
        ("final_job", "m2b-001:event_stream_final_job_binding_mismatch"),
    ],
)
def test_paper_run_gate_rejects_spliced_or_nonterminal_event_streams(
    tmp_path,
    corpus_factory,
    mutation,
    expected_failure,
):
    corpus = corpus_factory()
    run, artifact_root = _paper_run(tmp_path, corpus)
    run_case = run["cases"][0]
    raw_path = artifact_root / run_case["rawResponse"]
    raw = json.loads(raw_path.read_text())
    if mutation == "missing_final":
        raw["events"].pop()
    elif mutation == "mismatched_final":
        raw["events"][-1]["result"] = {"issues": [{"title": "splice"}]}
    elif mutation == "error_event":
        raw["events"].insert(-1, {"type": "error", "error": "failed"})
    elif mutation == "raw_job":
        raw["jobId"] = "another-job"
    else:
        raw["events"][-1]["jobId"] = "another-job"
    run_case["responseDigest"] = sha256_json(raw)
    write_json(raw_path, raw)

    failures = _paper_run_failures(
        run,
        corpus_cases={
            case["caseId"]: case for case in corpus["cases"]
        },
        artifact_root=artifact_root,
    )

    assert expected_failure in failures


def test_paper_run_gate_binds_request_to_fork_pr_and_full_enrichment(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    run, artifact_root = _paper_run(tmp_path, corpus)
    run_case = run["cases"][0]
    raw_path = artifact_root / run_case["rawResponse"]
    raw = json.loads(raw_path.read_text())
    request = raw["redactedRequest"]
    request["pullRequestId"] += 1
    request["aiCustomParameters"]["provider_token"] = "<redacted>"
    request["enrichmentData"]["fileContents"][0]["sizeBytes"] += 1
    request["unexpected"] = "self-consistent-extra-field"
    run_case["requestDigest"] = sha256_json(request)
    run_case["requestControlDigest"] = _request_control_digest(request)
    run_case["responseDigest"] = sha256_json(raw)
    write_json(raw_path, raw)

    failures = _paper_run_failures(
        run,
        corpus_cases={
            case["caseId"]: case for case in corpus["cases"]
        },
        artifact_root=artifact_root,
    )

    assert "m2b-001:request_pull_request_identity_invalid" in failures
    assert "m2b-001:request_unexpected_redaction" in failures
    assert "m2b-001:request_enrichment_entry_invalid" in failures
    assert "m2b-001:request_fields_invalid" in failures


@pytest.mark.parametrize("mutation", ["content", "extra_field"])
def test_paper_run_source_reconstruction_rejects_request_tampering(
    tmp_path,
    monkeypatch,
    corpus_factory,
    mutation,
):
    corpus = corpus_factory()
    run, artifact_root = _paper_run(tmp_path, corpus)
    repository = _stub_analysis_source_reconstruction(
        monkeypatch,
        artifact_root=artifact_root,
        run=run,
        repository=tmp_path / "source-repository",
    )
    run_case = run["cases"][0]
    raw_path = artifact_root / run_case["rawResponse"]
    raw = json.loads(raw_path.read_text())
    request = raw["redactedRequest"]
    if mutation == "content":
        entry = request["enrichmentData"]["fileContents"][0]
        old_size = entry["sizeBytes"]
        entry["content"] = "<?php\nfabricated();\n"
        entry["sizeBytes"] = len(entry["content"].encode("utf-8"))
        request["enrichmentData"]["stats"][
            "totalContentSizeBytes"
        ] += entry["sizeBytes"] - old_size
    else:
        request["fabricatedControl"] = True
    run_case["requestDigest"] = sha256_json(request)
    run_case["requestControlDigest"] = _request_control_digest(request)
    run_case["responseDigest"] = sha256_json(raw)
    write_json(raw_path, raw)

    failures = _paper_run_failures(
        run,
        corpus_cases={
            case["caseId"]: case for case in corpus["cases"]
        },
        artifact_root=artifact_root,
        repository_path=repository,
        require_request_source_reconstruction=True,
    )

    assert "m2b-001:request_source_reconstruction_mismatch" in failures


def test_paper_run_gate_rejects_stale_replay_attestation(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    run, artifact_root = _paper_run(tmp_path, corpus)
    attestation_path = artifact_root / run["replayAttestationArtifact"]
    attestation = json.loads(attestation_path.read_text())
    attestation["collectedAt"] = "2026-07-29T10:00:00Z"
    attestation.pop("attestationDigest")
    attestation["attestationDigest"] = sha256_json(attestation)
    run["replayAttestationDigest"] = attestation["attestationDigest"]
    write_json(attestation_path, attestation)

    failures = _paper_run_failures(
        run,
        corpus_cases={
            case["caseId"]: case for case in corpus["cases"]
        },
        artifact_root=artifact_root,
    )

    assert "replay_attestation_not_fresh" in failures


def test_paper_run_gate_binds_project_coordinates_to_replay_fork(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    run, artifact_root = _paper_run(tmp_path, corpus)
    run["analysisConfig"]["project_vcs_workspace"] = "attacker-owner"
    run["analysisConfigDigest"] = sha256_json(run["analysisConfig"])
    run_case = run["cases"][0]
    raw_path = artifact_root / run_case["rawResponse"]
    raw = json.loads(raw_path.read_text())
    raw["redactedRequest"]["projectVcsWorkspace"] = "attacker-owner"
    run_case["requestDigest"] = sha256_json(raw["redactedRequest"])
    run_case["requestControlDigest"] = _request_control_digest(
        raw["redactedRequest"]
    )
    run_case["responseDigest"] = sha256_json(raw)
    write_json(raw_path, raw)

    failures = _paper_run_failures(
        run,
        corpus_cases={
            case["caseId"]: case for case in corpus["cases"]
        },
        artifact_root=artifact_root,
    )

    assert "m2b-001:request_fork_repository_mismatch" in failures


@pytest.mark.parametrize(
    ("field_path", "value", "expected_failure"),
    [
        (
            ("runtimeProvenance", "analysis", "imageId"),
            "mutable-tag",
            "missing_analysis_runtime_identity",
        ),
        (
            (
                "cases",
                0,
                "retrievalEvidence",
                "reviewUnits",
                "completed",
            ),
            0,
            "m2b-001:retrieval_review_units_incomplete",
        ),
        (
            (
                "cases",
                0,
                "retrievalEvidence",
                "retrieval",
                "deterministicStates",
            ),
            [],
            "m2b-001:retrieval_deterministic_states_incomplete",
        ),
        (
            (
                "cases",
                0,
                "retrievalEvidence",
                "retrieval",
                "semanticFailures",
            ),
            1,
            "m2b-001:retrieval_semantic_failures_nonzero",
        ),
        (
            (
                "cases",
                0,
                "retrievalEvidence",
                "retrieval",
                "semanticDisabled",
            ),
            True,
            "m2b-001:retrieval_semantic_disabled",
        ),
        (
            (
                "cases",
                0,
                "retrievalEvidence",
                "reviewUnits",
                "registered",
            ),
            0,
            "m2b-001:retrieval_review_units_incomplete",
        ),
        (
            (
                "cases",
                0,
                "retrievalEvidence",
                "retrieval",
                "exactEvidenceIds",
            ),
            True,
            "m2b-001:retrieval_exact_evidence_count_invalid",
        ),
        (
            (
                "cases",
                0,
                "retrievalEvidence",
                "revisionBinding",
                "prIndexed",
            ),
            False,
            "m2b-001:retrieval_revision_binding_mismatch",
        ),
        (
            (
                "cases",
                0,
                "retrievalEvidence",
                "revisionBinding",
                "baseRevision",
            ),
            "f" * 40,
            "m2b-001:retrieval_revision_binding_mismatch",
        ),
        (
            (
                "cases",
                0,
                "retrievalEvidence",
                "revisionBinding",
                "prOverlayGenerationManifestSha256",
            ),
            "not-a-digest",
            "m2b-001:retrieval_pr_overlay_generation_manifest_invalid",
        ),
        (
            (
                "cases",
                0,
                "retrievalEvidence",
                "evidenceSha256",
            ),
            "f" * 64,
            "m2b-001:retrieval_terminal_event_binding_mismatch",
        ),
        (
            ("cases", 0, "productFinalization", "kind"),
            "fabricated-finalizer",
            "m2b-001:product_finalization_state_ineligible",
        ),
        (
            ("cases", 0, "productFinalization", "finalIssueCount"),
            1,
            "m2b-001:product_finalization_final_count_mismatch",
        ),
        (
            ("cases", 0, "productFinalization", "persisted"),
            True,
            "m2b-001:product_finalization_state_ineligible",
        ),
        (
            ("cases", 0, "productFinalization", "responseDigest"),
            "f" * 64,
            "m2b-001:product_finalization_response_digest_mismatch",
        ),
        (
            ("cases", 0, "indexReceipt", "branch"),
            "benchmark/wrong/base",
            "m2b-001:index_receipt_binding_mismatch",
        ),
        (
            ("indexReceiptsAfter", "m2b-001", "generation_member_count"),
            9,
            "m2b-001:index_receipt_after_generation_count_invalid",
        ),
        (
            ("indexReceiptsBefore", "m2b-001", "plugin_ids"),
            ["php"],
            "m2b-001:index_receipt_before_required_plugin_ids_missing",
        ),
        (
            (
                "indexReceiptsAfter",
                "m2b-001",
                "repository_facts_sha256",
            ),
            "invalid",
            (
                "m2b-001:index_receipt_after_"
                "repository_facts_sha256_invalid"
            ),
        ),
        (
            (
                "indexReceiptsBefore",
                "m2b-001",
                "source_tree_sha256",
            ),
            "invalid",
            (
                "m2b-001:index_receipt_before_"
                "source_tree_sha256_invalid"
            ),
        ),
        (
            (
                "indexReceiptsBefore",
                "m2b-001",
                "plugin_fingerprint",
            ),
            "sha256:invalid",
            "m2b-001:index_receipt_before_plugin_fingerprint_invalid",
        ),
    ],
)
def test_paper_run_gate_rejects_fabricated_or_incomplete_evidence(
    tmp_path,
    corpus_factory,
    field_path,
    value,
    expected_failure,
):
    corpus = corpus_factory()
    run, artifact_root = _paper_run(tmp_path, corpus)
    target = run
    for field in field_path[:-1]:
        target = target[field]
    target[field_path[-1]] = value

    failures = _paper_run_failures(
        run,
        corpus_cases={
            case["caseId"]: case for case in corpus["cases"]
        },
        artifact_root=artifact_root,
    )

    assert expected_failure in failures


def test_paper_run_gate_requires_exact_case_and_receipt_sets(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    run, artifact_root = _paper_run(tmp_path, corpus)
    run["selectedCaseIds"][-1] = run["selectedCaseIds"][0]
    run["cases"][-1]["caseId"] = run["cases"][0]["caseId"]
    run["indexReceiptsBefore"].pop("m2b-050")

    failures = _paper_run_failures(
        run,
        corpus_cases={
            case["caseId"]: case for case in corpus["cases"]
        },
        artifact_root=artifact_root,
    )

    assert "analysis_case_selection_incomplete" in failures
    assert "analysis_case_artifacts_incomplete" in failures
    assert "index_receipts_before_incomplete" in failures


def test_metrics_paper_ready_uses_strict_corpus_validation(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    corpus["cases"][0]["goldenComments"][0]["adjudication"]["records"][0][
        "recordDigest"
    ] = "0" * 64
    corpus = attach_corpus_digest(corpus)
    judgment = make_judgment(
        corpus,
        suffix="strict-corpus",
        analysis_model="model",
        cases=_two_scored_cases(),
    )

    result = build_metrics(
        corpus_path=write_json(tmp_path / "corpus.json", corpus),
        judgment_paths=[write_json(tmp_path / "judgment.json", judgment)],
        bootstrap_iterations=0,
    )

    assert result["corpus"]["paperReady"] is False
    assert "corpus_not_strictly_paper_ready" in (
        result["methodology"]["paperGateFailures"]
    )


def test_metrics_can_pass_artifact_integrity_but_not_publication_protocol(
    tmp_path,
    monkeypatch,
    corpus_factory,
):
    corpus = corpus_factory()
    run, artifact_root = _paper_run(tmp_path, corpus)
    run["runDigest"] = sha256_json(run)
    run_path = write_json(artifact_root / "run.json", run)
    judgment, judgment_root = _paper_judgment(tmp_path, corpus, run)
    repository = _stub_analysis_source_reconstruction(
        monkeypatch,
        artifact_root=artifact_root,
        run=run,
        repository=tmp_path / "source-repository",
    )

    result = build_metrics(
        corpus_path=write_json(tmp_path / "corpus.json", corpus),
        judgment_paths=[
            write_json(judgment_root / "judgments.json", judgment)
        ],
        repository_path=repository,
        analysis_run_paths=[run_path],
        bootstrap_iterations=10_000,
    )

    methodology = result["methodology"]
    assert result["configurations"][0]["analysisModelRoles"] == (
        run["analysisModelRoles"]
    )
    assert methodology["artifactIntegrityGateFailures"] == []
    assert methodology["artifactIntegrityReady"] is True
    assert methodology["publicationProtocolReady"] is False
    assert methodology["publicationProtocolGateFailures"]
    assert methodology["paperReady"] is False
    assert methodology["paperGateFailures"] == (
        methodology["publicationProtocolGateFailures"]
    )


def test_metrics_zero_bootstrap_iterations_are_diagnostic_only(
    tmp_path,
    monkeypatch,
    corpus_factory,
):
    corpus = corpus_factory()
    run, artifact_root = _paper_run(tmp_path, corpus)
    run["runDigest"] = sha256_json(run)
    run_path = write_json(artifact_root / "run.json", run)
    judgment, judgment_root = _paper_judgment(tmp_path, corpus, run)
    repository = _stub_analysis_source_reconstruction(
        monkeypatch,
        artifact_root=artifact_root,
        run=run,
        repository=tmp_path / "source-repository",
    )

    result = build_metrics(
        corpus_path=write_json(tmp_path / "corpus.json", corpus),
        judgment_paths=[
            write_json(judgment_root / "judgments.json", judgment)
        ],
        repository_path=repository,
        analysis_run_paths=[run_path],
        bootstrap_iterations=0,
    )

    assert result["methodology"]["paperReady"] is False
    assert "bootstrap_iterations_below_publication_minimum" in (
        result["methodology"]["artifactIntegrityGateFailures"]
    )


def test_metrics_without_analysis_source_repository_are_not_paper_ready(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    run, artifact_root = _paper_run(tmp_path, corpus)
    run["runDigest"] = sha256_json(run)
    run_path = write_json(artifact_root / "run.json", run)
    judgment, judgment_root = _paper_judgment(tmp_path, corpus, run)

    result = build_metrics(
        corpus_path=write_json(tmp_path / "corpus.json", corpus),
        judgment_paths=[
            write_json(judgment_root / "judgments.json", judgment)
        ],
        analysis_run_paths=[run_path],
        bootstrap_iterations=10,
    )

    assert result["methodology"]["paperReady"] is False
    assert any(
        failure.endswith(":analysis_source_repository_missing")
        for failure in result["methodology"]["paperGateFailures"]
    )


def test_paper_judgment_gate_rejects_missing_raw_case_artifact(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    run, _ = _paper_run(tmp_path, corpus)
    run["runDigest"] = sha256_json(run)
    judgment, judgment_root = _paper_judgment(tmp_path, corpus, run)
    (judgment_root / judgment["cases"][0]["rawJudgment"]).unlink()

    failures = _paper_judgment_failures(
        judgment,
        corpus_cases={
            case["caseId"]: case for case in corpus["cases"]
        },
        analysis_run=run,
        artifact_root=judgment_root,
    )

    assert "m2b-001:raw_judgment_missing" in failures


def test_paper_judgment_gate_reconstructs_provider_checkpoint(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    judgment, run, artifact_root, _ = _single_call_paper_judgment(
        tmp_path,
        corpus,
    )

    assert _paper_judgment_failures(
        judgment,
        corpus_cases={"m2b-001": corpus["cases"][0]},
        analysis_run=run,
        artifact_root=artifact_root,
    ) == []


def test_paper_judgment_gate_rejects_missing_provider_response(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    judgment, run, artifact_root, checkpoint_path = (
        _single_call_paper_judgment(tmp_path, corpus)
    )
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["metadata"].pop("providerResponse")
    checkpoint.pop("callDigest")
    checkpoint["callDigest"] = sha256_json(checkpoint)
    write_json(checkpoint_path, checkpoint)
    raw_path = artifact_root / judgment["cases"][0]["rawJudgment"]
    raw = json.loads(raw_path.read_text())
    raw["calls"][0] = {
        "kind": "pair",
        "goldId": "G001",
        "repeat": 1,
        "checkpoint": str(checkpoint_path.relative_to(artifact_root)),
        **{
            key: value
            for key, value in checkpoint.items()
            if key != "metadata"
        },
        **checkpoint["metadata"],
    }
    raw.pop("caseDigest")
    raw["caseDigest"] = sha256_json(raw)
    write_json(raw_path, raw)
    judgment["cases"][0] = {
        **raw,
        "rawJudgment": str(raw_path.relative_to(artifact_root)),
    }

    failures = _paper_judgment_failures(
        judgment,
        corpus_cases={"m2b-001": corpus["cases"][0]},
        analysis_run=run,
        artifact_root=artifact_root,
    )

    assert (
        "m2b-001:provider_response_missing_or_tampered" in failures
    )


def test_paper_judgment_gate_rejects_self_consistent_wrong_provider_model(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    judgment, run, artifact_root, checkpoint_path = (
        _single_call_paper_judgment(tmp_path, corpus)
    )
    checkpoint = json.loads(checkpoint_path.read_text())
    metadata = checkpoint["metadata"]
    metadata["providerResponse"]["model"] = "attacker/model"
    metadata["providerResponseSha256"] = sha256_json(
        metadata["providerResponse"]
    )
    metadata["model"] = "attacker/model"
    checkpoint.pop("callDigest")
    checkpoint["callDigest"] = sha256_json(checkpoint)
    write_json(checkpoint_path, checkpoint)
    raw_path = artifact_root / judgment["cases"][0]["rawJudgment"]
    raw = json.loads(raw_path.read_text())
    raw["calls"][0] = {
        "kind": "pair",
        "goldId": "G001",
        "repeat": 1,
        "checkpoint": str(checkpoint_path.relative_to(artifact_root)),
        **{
            key: value
            for key, value in checkpoint.items()
            if key != "metadata"
        },
        **metadata,
    }
    raw.pop("caseDigest")
    raw["caseDigest"] = sha256_json(raw)
    write_json(raw_path, raw)
    judgment["cases"][0] = {
        **raw,
        "rawJudgment": str(raw_path.relative_to(artifact_root)),
    }

    failures = _paper_judgment_failures(
        judgment,
        corpus_cases={"m2b-001": corpus["cases"][0]},
        analysis_run=run,
        artifact_root=artifact_root,
    )

    assert "m2b-001:provider_model_mismatch" in failures


def test_paper_judgment_gate_reconstructs_source_prompt_from_git(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    repository = tmp_path / "repository"
    base, head = make_git_pair(repository)
    source_case = corpus["cases"][0]
    source_case["snapshot"].update(
        {
            "baseSha": base,
            "headSha": head,
            "fileCount": 3,
            "changedPaths": _git_paths(repository, base, head),
            "diffSha256": sha256_text(
                _git_diff(repository, base, head)
            ),
        }
    )
    corpus = attach_corpus_digest(corpus)
    judgment, run, artifact_root, _ = _single_call_paper_judgment(
        tmp_path,
        corpus,
    )

    failures = _paper_judgment_failures(
        judgment,
        corpus_cases={"m2b-001": source_case},
        analysis_run=run,
        artifact_root=artifact_root,
        repository=repository,
        require_source_reconstruction=True,
    )

    assert (
        "m2b-001:prompt_source_reconstruction_mismatch" in failures
    )


def test_metrics_requires_one_fixed_judge_for_model_comparisons(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    first = make_judgment(
        corpus,
        suffix="judge-a",
        analysis_model="analysis-a",
        cases=_two_scored_cases(),
    )
    second = make_judgment(
        corpus,
        suffix="judge-b",
        analysis_model="analysis-b",
        cases=_two_scored_cases(),
    )
    for judgment, model in ((first, "judge-a"), (second, "judge-b")):
        judgment["judgeModel"] = model
        judgment["judgeConfig"] = {"model": model, "temperature": 0}
        judgment["judgeConfigDigest"] = sha256_json(
            judgment["judgeConfig"]
        )
        judgment.pop("judgmentDigest")
        judgment["judgmentDigest"] = sha256_json(judgment)

    result = build_metrics(
        corpus_path=write_json(tmp_path / "corpus.json", corpus),
        judgment_paths=[
            write_json(tmp_path / "first.json", first),
            write_json(tmp_path / "second.json", second),
        ],
        bootstrap_iterations=0,
    )

    assert "judge_configuration_not_fixed_across_comparisons" in (
        result["methodology"]["paperGateFailures"]
    )


def test_analysis_control_digest_varies_only_the_selected_model(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    run, _ = _paper_run(tmp_path, corpus)
    run["analysisConfig"]["model"] = run["analysisModel"]
    baseline = _analysis_control_digest(run)

    model_variant = copy.deepcopy(run)
    model_variant["analysisModel"] = "another-model"
    model_variant["analysisConfig"]["model"] = "another-model"
    model_variant["analysisModelRoles"]["reviewPipeline"] = "another-model"
    for identity in model_variant["runtimeProvenance"].values():
        if isinstance(identity, dict) and "containerId" in identity:
            identity["containerId"] = "f" * 64
    assert _analysis_control_digest(model_variant) == baseline

    request_variant = copy.deepcopy(model_variant)
    request_variant["cases"][0]["requestControlDigest"] = "f" * 64
    assert _analysis_control_digest(request_variant) != baseline

    parameter_variant = copy.deepcopy(model_variant)
    parameter_variant["analysisConfig"]["custom_parameters"][
        "temperature"
    ] = 0.4
    assert _analysis_control_digest(parameter_variant) != baseline

    image_variant = copy.deepcopy(model_variant)
    image_variant["runtimeProvenance"]["analysis"]["imageId"] = (
        "sha256:" + "f" * 64
    )
    assert _analysis_control_digest(image_variant) != baseline
