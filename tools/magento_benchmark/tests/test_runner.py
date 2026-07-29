from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pytest

from magento2_benchmark.corpus import attach_corpus_digest
from magento2_benchmark.execution_corpus import build_execution_corpus
from magento2_benchmark.replay import LOCK_KIND, build_plan
from magento2_benchmark.runner import (
    _analysis_result,
    _archive_quality_capture_artifact,
    _expected_quality_capture_request,
    _finalizer_file_contents,
    _findings,
    _git_blob,
    _git_diff,
    _git_paths,
    _product_finalize,
    _quality_capture_container_source,
    _quality_capture_receipt_from_events,
    _redacted_request,
    _reconstruct_quality_capture_receipt,
    _request_payload,
    _retrieval_evidence,
    _validate_bound_model_call_evidence,
    _validate_quality_capture_artifact,
    exact_index_receipt,
    run_analysis,
)
from magento2_benchmark.util import sha256_json, sha256_text

from conftest import make_git_pair, write_json


def _replace_commit_tree(
    repository: Path,
    commit: str,
    *,
    path: str,
    content: str,
) -> None:
    blob = subprocess.run(
        ["git", "-C", str(repository), "hash-object", "-w", "--stdin"],
        input=content,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "update-index",
            "--cacheinfo",
            "100644",
            blob,
            path,
        ],
        check=True,
    )
    tree = subprocess.check_output(
        ["git", "-C", str(repository), "write-tree"],
        text=True,
    ).strip()
    replacement = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "commit-tree",
            tree,
            "-m",
            "hostile replacement",
        ],
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(repository), "replace", commit, replacement],
        check=True,
    )


def test_local_snapshot_reads_ignore_replace_refs_and_git_environment(
    monkeypatch,
    tmp_path,
):
    repository = tmp_path / "repository"
    base, head = make_git_pair(repository)
    _replace_commit_tree(
        repository,
        head,
        path="A.php",
        content="<?php\nreturn 9;\n",
    )

    replaced = subprocess.check_output(
        ["git", "-C", str(repository), "show", f"{head}:A.php"],
    )
    assert b"return 9;" in replaced
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "hostile-git-dir"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(tmp_path / "hostile-hooks"))

    assert _git_blob(repository, head, "A.php") == b"<?php\nreturn 2;\n"
    assert "+return 2;" in _git_diff(repository, base, head)
    assert _git_paths(repository, base, head) == ["A.php", "B.php", "C.php"]


def analysis_config(**overrides):
    value = {
        "transport": "http",
        "endpoint": "http://analysis.invalid/review",
        "rag_endpoint": "http://rag.invalid",
        "finalizer_endpoint": (
            "http://finalizer.invalid"
            "/api/internal/analysis/benchmark-finalize"
        ),
        "provider": "fixture-provider",
        "model": "fixture-model",
        "api_key_env": "BENCHMARK_TEST_API_KEY",
        "service_secret_env": "BENCHMARK_TEST_SERVICE_SECRET",
        "base_url": "",
        "custom_parameters": {"temperature": 0},
        "project_id": 42,
        "project_vcs_workspace": "benchmark-owner",
        "project_vcs_repo_slug": "magento2",
        "project_workspace": "fixture-workspace",
        "project_namespace": "fixture-project",
        "rag_workspace": "",
        "rag_project": "",
        "require_exact_index": True,
        "require_retrieval_evidence": False,
        "require_replay_attestation": False,
        "replay_attestation_max_age_seconds": 3_600,
        "required_repository_plugins": ["php", "magento"],
        "analysis_container": "analysis-fixture",
        "rag_container": "rag-fixture",
        "finalizer_container": "finalizer-fixture",
        "require_runtime_provenance": False,
        "max_enrichment_file_bytes": 1_000_000,
        "max_enrichment_total_bytes": 3_000_000,
        "timeout_seconds": 10,
    }
    value.update(overrides)
    return value


def test_redacted_request_removes_top_level_and_nested_secrets():
    redacted = _redacted_request(
        {
            "aiApiKey": "top-level-secret",
            "aiCustomParameters": {
                "temperature": 0,
                "provider_token": "nested-secret",
            },
        }
    )

    assert redacted == {
        "aiApiKey": "<redacted>",
        "aiCustomParameters": {
            "temperature": 0,
            "provider_token": "<redacted>",
        },
    }


def quality_capture_request():
    return {
        "projectId": 42,
        "projectVcsWorkspace": "benchmark-owner",
        "projectVcsRepoSlug": "magento2",
        "projectWorkspace": "fixture-workspace",
        "projectNamespace": "fixture-project",
        "aiProvider": "fixture-provider",
        "aiModel": "fixture-model",
        "aiApiKey": "<redacted>",
        "aiBaseUrl": (
            "https://user:password@example.test/v1?token=secret#fragment"
        ),
        "aiCustomParameters": {
            "temperature": 0,
            "default_headers": {
                "X-Trace": "visible",
                "Authorization": "<redacted>",
            },
        },
        "analysisType": "PR_REVIEW",
        "targetBranchName": "benchmark/base",
        "sourceBranchName": "benchmark/head",
        "pullRequestId": 123,
        "commitHash": "b" * 40,
        "currentCommitHash": "b" * 40,
        "baseCommitHash": "a" * 40,
        "prTitle": "Fixture",
        "prDescription": "",
        "prAuthor": "benchmark-fixture",
        "taskContext": {},
        "taskHistoryContext": "",
        "changedFiles": ["A.php"],
        "deletedFiles": [],
        "diffSnippets": [],
        "rawDiff": "diff --git a/A.php b/A.php\n",
        "vcsProvider": "github",
        "analysisMode": "FULL",
        "previousCodeAnalysisIssues": [],
        "enrichmentData": {
            "fileContents": [
                {
                    "path": "A.php",
                    "content": "<?php\n",
                    "sizeBytes": 6,
                    "skipped": False,
                    "skipReason": None,
                }
            ],
            "fileMetadata": [],
            "relationships": [],
            "stats": {
                "totalFilesRequested": 1,
                "filesEnriched": 1,
                "filesSkipped": 0,
                "relationshipsFound": 0,
                "totalContentSizeBytes": 6,
                "processingTimeMs": 0,
                "skipReasons": {},
            },
        },
        "projectCapabilities": {
            "repositoryPlugins": ["php", "magento"],
            "filePlugins": {"A.php": ["php", "magento"]},
            "detectionEvidence": {"magento": ["app/etc/di.xml"]},
            "unavailableCapabilities": [],
            "fingerprint": "sha256:" + "c" * 64,
            "descriptorFingerprint": "sha256:" + "d" * 64,
        },
        "useMcpTools": False,
        "ragEnabled": True,
        "projectRules": "[]",
    }


def quality_capture_fixture(
    *,
    expected_request=None,
    reported_model="provider-resolved-model",
    artifact_container_path=(
        "/app/logs/review-quality-captures/"
        "project-42-review-123-0123456789abcdef0123456789abcdef.json"
    ),
):
    request = expected_request or quality_capture_request()
    response = {"issues": []}
    artifact = {
        "kind": "review-quality-candidate-capture",
        "status": "completed",
        "provider": "fixture-provider",
        "model": "fixture-model",
        "request": _expected_quality_capture_request(request),
        "requestDigest": None,
        "modelBoundaryInvocations": 1,
        "providerCalls": 1,
        "calls": [
            {
                "sequence": 1,
                "stage": "stage_1",
                "status": "completed",
                "providerCallCount": 1,
                "providerEvents": [
                    {
                        "status": "completed",
                        "providerReportedModels": [reported_model],
                        "response": response,
                    }
                ],
                "promptDigest": sha256_text("fixture prompt"),
                "responseDigest": sha256_json(response),
            }
        ],
        "captureDigest": None,
    }
    artifact["requestDigest"] = sha256_json(artifact["request"])
    artifact["captureDigest"] = sha256_json(artifact)
    receipt = _reconstruct_quality_capture_receipt(
        artifact,
        artifact_container_path=artifact_container_path,
    )
    return artifact, receipt


def test_quality_capture_request_snapshot_is_exact_and_safely_normalized():
    request = quality_capture_request()
    snapshot = _expected_quality_capture_request(request)

    assert snapshot["branch"] == request["targetBranchName"]
    assert "targetBranchName" not in snapshot
    assert snapshot["aiApiKey"] == "[REDACTED]"
    assert snapshot["aiBaseUrl"] == "https://example.test/v1"
    assert (
        snapshot["aiCustomParameters"]["default_headers"]["Authorization"]
        == "[REDACTED]"
    )
    assert snapshot["aiCustomParameters"]["default_headers"]["X-Trace"] == (
        "visible"
    )
    assert snapshot["enrichmentData"] == request["enrichmentData"]
    assert snapshot["projectCapabilities"] == request["projectCapabilities"]
    assert snapshot["promptDryRun"] is False
    assert snapshot["promptDryRunId"] is None
    assert snapshot["reconciliationFileContents"] is None

    artifact, receipt = quality_capture_fixture(
        expected_request=request,
    )
    assert _validate_quality_capture_artifact(
        artifact,
        receipt=receipt,
        provider="fixture-provider",
        requested_model="fixture-model",
        expected_response_model="provider-resolved-model",
        expected_request=request,
    ) == artifact

    drifted_request = json.loads(json.dumps(request))
    drifted_request["aiCustomParameters"]["temperature"] = 0.5
    with pytest.raises(RuntimeError, match="exact normalized benchmark"):
        _validate_quality_capture_artifact(
            artifact,
            receipt=receipt,
            provider="fixture-provider",
            requested_model="fixture-model",
            expected_response_model="provider-resolved-model",
            expected_request=drifted_request,
        )


def test_quality_capture_receipt_fails_closed_for_missing_duplicate_and_model():
    _, receipt = quality_capture_fixture()
    event = {
        "type": "status",
        "state": "review_quality_capture_completed",
        "qualityCapture": receipt,
    }
    kwargs = {
        "provider": "fixture-provider",
        "requested_model": "fixture-model",
        "expected_response_model": "provider-resolved-model",
    }

    with pytest.raises(RuntimeError, match="no terminal"):
        _quality_capture_receipt_from_events([], required=True, **kwargs)
    with pytest.raises(RuntimeError, match="duplicate"):
        _quality_capture_receipt_from_events(
            [event, event],
            required=True,
            **kwargs,
        )
    with pytest.raises(RuntimeError, match="expected response model"):
        _quality_capture_receipt_from_events(
            [event],
            required=True,
            **{**kwargs, "expected_response_model": "wrong-model"},
        )


def test_quality_capture_path_and_copied_binding_fail_closed(tmp_path):
    artifact, receipt = quality_capture_fixture()
    config = analysis_config(
        expected_response_model="provider-resolved-model",
        require_model_call_evidence=True,
        quality_capture_container_dir=(
            "/app/logs/review-quality-captures"
        ),
    )
    escaped = dict(
        receipt,
        artifactContainerPath=(
            "/app/logs/review-quality-captures/../"
            "project-42-review-123-0123456789abcdef0123456789abcdef.json"
        ),
    )
    with pytest.raises(ValueError, match="outside"):
        _quality_capture_container_source(
            config,
            escaped,
            pull_request_id=123,
        )

    relative = "raw/attempts/attempt-" + "a" * 32 + (
        "-quality-capture.json"
    )
    (tmp_path / "raw" / "attempts").mkdir(parents=True)
    write_json(tmp_path / relative, artifact)
    evidence = {
        "receipt": receipt,
        "artifact": relative,
        "artifactDigest": sha256_json(artifact),
    }
    assert _validate_bound_model_call_evidence(
        evidence,
        output_dir=tmp_path,
        config=config,
        pull_request_id=123,
        expected_request=quality_capture_request(),
        require_present=True,
    ) == evidence

    artifact["providerCalls"] = 2
    write_json(tmp_path / relative, artifact)
    with pytest.raises(ValueError, match="digest drift"):
        _validate_bound_model_call_evidence(
            evidence,
            output_dir=tmp_path,
            config=config,
            pull_request_id=123,
            expected_request=quality_capture_request(),
            require_present=True,
        )


@pytest.mark.parametrize("copied_kind", ["symlink", "directory"])
def test_quality_capture_archive_rejects_non_regular_copy(
    tmp_path,
    monkeypatch,
    copied_kind,
):
    artifact, receipt = quality_capture_fixture()
    config = analysis_config(
        expected_response_model="provider-resolved-model",
        quality_capture_container_dir=(
            "/app/logs/review-quality-captures"
        ),
    )

    def unsafe_copy(*args, destination, **kwargs):
        if copied_kind == "symlink":
            target = tmp_path / "outside-capture.json"
            write_json(target, artifact)
            destination.symlink_to(target)
        else:
            destination.mkdir()

    monkeypatch.setattr(
        "magento2_benchmark.runner._docker_copy_container_file",
        unsafe_copy,
    )

    with pytest.raises(RuntimeError, match="not a regular file"):
        _archive_quality_capture_artifact(
            config=config,
            receipt=receipt,
            output_dir=tmp_path / "run",
            attempt_id="attempt-" + "b" * 32,
            pull_request_id=123,
            secret_values=set(),
            provider="fixture-provider",
            requested_model="fixture-model",
            expected_response_model="provider-resolved-model",
            expected_request=quality_capture_request(),
        )


def complete_receipt(**overrides):
    selection_policy = {
        "schema": "codecrow.repository-index-selection",
        "includePatterns": ["app/code/**"],
        "excludePatterns": ["vendor/**"],
    }
    value = {
        "workspace": "fixture-workspace",
        "project": "fixture-project",
        "branch": "base",
        "commit": "a" * 40,
        "point_count": 500,
        "generation_schema": "codecrow.repository-index-generation",
        "generation_member_count": 499,
        "generation_members_sha256": "1" * 64,
        "generation_manifest_sha256": "2" * 64,
        "source_tree_sha256": "3" * 64,
        "index_include_patterns": selection_policy["includePatterns"],
        "index_exclude_patterns": selection_policy["excludePatterns"],
        "index_selection_policy_sha256": sha256_json(selection_policy),
        "repository_revision": "a" * 40,
        "repository_facts_sha256": "b" * 64,
        "plugin_ids": ["php", "magento", "composer"],
        "plugin_fingerprint": "sha256:" + "c" * 64,
        "plugin_descriptor_fingerprint": "sha256:" + "d" * 64,
        "plugin_implementation_fingerprint": "sha256:" + "e" * 64,
        "index_representation_fingerprint": "sha256:" + "f" * 64,
    }
    value.update(overrides)
    return value


def test_analysis_run_schema_seals_product_and_index_provenance():
    schema = json.loads(
        (
            Path(__file__).parents[1]
            / "schemas"
            / "analysis-run.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert schema["properties"]["findingSemantics"]["const"] == (
        "java-finalized-transient-first-iteration"
    )
    assert schema["properties"]["runId"]["pattern"] == (
        "^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"
    )
    assert "replayAttestationDigest" in schema["required"]
    assert "replayAttestationArtifact" in schema["required"]
    assert "replayLockArtifact" in schema["required"]
    assert "attemptPolicy" in schema["required"]
    assert "attemptLedger" in schema["required"]
    assert set(
        schema["properties"]["analysisModelRoles"]["required"]
    ) == {
        "reviewPipeline",
        "reviewPipelineRequested",
        "reviewPipelineExpectedResponse",
        "reviewPipelineProviderReported",
        "providerReportedByStage",
    }
    assert "requestControlDigest" in schema["$defs"]["case"]["required"]
    assert "modelCallEvidence" in schema["$defs"]["case"]["required"]
    assert {
        "attemptId",
        "attemptNumber",
        "attemptCount",
        "maxAttempts",
        "retryEligible",
        "stoppingReason",
    }.issubset(schema["$defs"]["case"]["required"])
    assert {
        "startArtifact",
        "startArtifactDigest",
        "resultArtifact",
        "resultArtifactDigest",
        "modelCallEvidence",
        "stoppingReason",
        "retryEligible",
    }.issubset(schema["$defs"]["attempt"]["required"])
    assert {"analysis", "rag", "finalizer", "required"} == set(
        schema["$defs"]["runtimeProvenance"]["required"]
    )
    receipt = schema["$defs"]["receipt"]
    exact_required = set(receipt["oneOf"][1]["required"])
    assert {
        "generation_schema",
        "generation_member_count",
        "generation_members_sha256",
        "generation_manifest_sha256",
        "source_tree_sha256",
    }.issubset(exact_required)
    assert {
        "analysisDataValidated",
        "persisted",
        "published",
        "previousIssueStateUsed",
    }.issubset(schema["$defs"]["productFinalization"]["required"])
    assert {
        "prIndexed",
        "pullRequestId",
        "sourceRevision",
        "baseRevision",
        "baseGenerationManifestSha256",
        "prGenerationFingerprint",
        "prOverlayGenerationManifestSha256",
        "basePluginFingerprint",
        "basePluginDescriptorFingerprint",
        "basePluginImplementationFingerprint",
        "baseIndexRepresentationFingerprint",
    }.issubset(
        schema["$defs"]["retrievalEvidence"]["properties"][
            "revisionBinding"
        ]["required"]
    )


def request_case(repository, base, head):
    from magento2_benchmark.runner import _git_diff, _git_paths

    paths = _git_paths(repository, base, head)
    return {
        "caseId": "m2b-request",
        "snapshot": {
            "baseSha": base,
            "headSha": head,
            "changedPaths": paths,
            "diffSha256": sha256_text(_git_diff(repository, base, head)),
        },
    }


def request_replay(case):
    return {
        "baseSha": case["snapshot"]["baseSha"],
        "headSha": case["snapshot"]["headSha"],
        "baseRef": "benchmark/m2b-request/base",
        "headRef": "benchmark/m2b-request/head",
        "forkPrNumber": 321,
    }


def test_exact_index_receipt_checks_revision_points_and_plugins(monkeypatch):
    config = analysis_config()
    expected = complete_receipt(branch="benchmark/case/base")
    monkeypatch.setattr(
        "magento2_benchmark.runner._json_request",
        lambda *args, **kwargs: expected,
    )

    receipt = exact_index_receipt(
        config,
        branch=expected["branch"],
        commit=expected["commit"],
        service_secret="secret",
    )

    assert receipt == expected


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            complete_receipt(point_count=0),
            "no exact RAG index",
        ),
        (
            complete_receipt(branch="other"),
            "different exact revision identity",
        ),
        (
            complete_receipt(plugin_ids=["php"]),
            "lacks required repository plugins",
        ),
        (
            complete_receipt(repository_revision="c" * 40),
            "different exact revision identity",
        ),
        (
            complete_receipt(plugin_fingerprint="sha256:not-a-digest"),
            "no valid plugin_fingerprint",
        ),
        (
            complete_receipt(repository_facts_sha256="not-a-digest"),
            "no valid repository facts digest",
        ),
        (
            complete_receipt(source_tree_sha256="not-a-digest"),
            "no valid source_tree_sha256",
        ),
        (
            complete_receipt(
                index_selection_policy_sha256="0" * 64,
            ),
            "selection policy digest does not match",
        ),
        (
            complete_receipt(generation_member_count=498),
            "generation member count is inconsistent",
        ),
        (
            complete_receipt(generation_schema="legacy"),
            "no supported sealed generation manifest",
        ),
    ],
)
def test_exact_index_receipt_fails_closed(monkeypatch, response, message):
    monkeypatch.setattr(
        "magento2_benchmark.runner._json_request",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(RuntimeError, match=message):
        exact_index_receipt(
            analysis_config(),
            branch="base",
            commit="a" * 40,
            service_secret="secret",
        )


def test_analysis_result_fails_closed_instead_of_fabricating_no_findings():
    with pytest.raises(RuntimeError, match="no explicit issues array"):
        _analysis_result({})
    with pytest.raises(RuntimeError, match="returned an error result"):
        _analysis_result({"error": "provider unavailable"})
    with pytest.raises(RuntimeError, match="returned an error result"):
        _findings({"issues": [], "status": "error", "message": "failed"})
    with pytest.raises(RuntimeError, match="neither title nor description"):
        _findings({"issues": [{"file": "A.php", "line": 1}]})


def test_product_finalizer_is_authenticated_and_contract_checked(monkeypatch):
    captured = {}
    finalization = {
        "kind": "codecrow-isolated-analysis-finalization",
        "comment": "done",
        "rawIssueCount": 1,
        "finalIssueCount": 1,
        "issues": [
            {
                "file": "A.php",
                "line": 2,
                "title": "Finalized issue",
                "reason": "Product-filtered reason",
                "severity": "HIGH",
                "category": "SECURITY",
            }
        ],
        "analysisDataValidated": True,
        "persisted": False,
        "published": False,
        "previousIssueStateUsed": False,
    }

    def fake_request(*args, **kwargs):
        captured.update(kwargs)
        return finalization

    monkeypatch.setattr(
        "magento2_benchmark.runner._json_request",
        fake_request,
    )
    request_payload = {
        "enrichmentData": {
            "fileContents": [
                {
                    "path": "A.php",
                    "content": "<?php\nproblem();\n",
                },
                {
                    "path": "deleted.php",
                    "content": None,
                },
            ]
        }
    }

    result = _product_finalize(
        analysis_config(),
        response={
            "comment": "done",
            "issues": [
                {
                    "file": "A.php",
                    "line": 99,
                    "reason": "raw",
                }
            ],
        },
        request_payload=request_payload,
        service_secret="internal-secret",
    )

    assert result == finalization
    assert captured["secret"] == "internal-secret"
    assert captured["secret_header"] == "X-Internal-Secret"
    assert captured["payload"]["fileContents"] == {
        "A.php": "<?php\nproblem();\n"
    }
    assert _finalizer_file_contents(request_payload) == {
        "A.php": "<?php\nproblem();\n"
    }

    invalid = dict(finalization, persisted=True)
    monkeypatch.setattr(
        "magento2_benchmark.runner._json_request",
        lambda *args, **kwargs: invalid,
    )
    with pytest.raises(RuntimeError, match="invalid persisted"):
        _product_finalize(
            analysis_config(),
            response={"comment": "done", "issues": [{}]},
            request_payload=request_payload,
            service_secret="internal-secret",
        )


def test_retrieval_evidence_requires_complete_terminal_receipt():
    expected_revision_binding = {
        "pullRequestId": 77,
        "targetBranch": "benchmark/base",
        "sourceRevision": "1" * 40,
        "baseRevision": "2" * 40,
        "baseGenerationManifestSha256": "3" * 64,
        "basePluginFingerprint": "sha256:" + "5" * 64,
        "basePluginDescriptorFingerprint": "sha256:" + "6" * 64,
        "basePluginImplementationFingerprint": "sha256:" + "7" * 64,
        "baseIndexRepresentationFingerprint": "sha256:" + "8" * 64,
    }
    event = {
        "type": "status",
        "state": "review_evidence_completed",
        "reviewUnits": {"registered": 2, "completed": 2},
        "retrieval": {
            "deterministicStates": ["complete", "complete"],
            "semanticFailures": 0,
            "semanticDisabled": False,
            "exactEvidenceIds": 3,
        },
        "revisionBinding": {
            "prIndexed": True,
            **expected_revision_binding,
            "prGenerationFingerprint": "sha256:" + "4" * 64,
            "prOverlayGenerationManifestSha256": "9" * 64,
        },
    }
    evidence = _retrieval_evidence(
        [event],
        required=True,
        expected_revision_binding=expected_revision_binding,
    )
    assert evidence["retrieval"]["exactEvidenceIds"] == 3
    assert evidence["revisionBinding"]["sourceRevision"] == "1" * 40

    failed = json.loads(json.dumps(event))
    failed["retrieval"]["deterministicStates"][1] = "failed"
    with pytest.raises(RuntimeError, match="incomplete deterministic RAG"):
        _retrieval_evidence(
            [failed],
            required=True,
            expected_revision_binding=expected_revision_binding,
        )

    disabled = json.loads(json.dumps(event))
    disabled["retrieval"]["semanticDisabled"] = True
    with pytest.raises(RuntimeError, match="semantic RAG retrieval disabled"):
        _retrieval_evidence(
            [disabled],
            required=True,
            expected_revision_binding=expected_revision_binding,
        )

    no_review_units = json.loads(json.dumps(event))
    no_review_units["reviewUnits"] = {"registered": 0, "completed": 0}
    with pytest.raises(RuntimeError, match="registered review unit"):
        _retrieval_evidence(
            [no_review_units],
            required=True,
            expected_revision_binding=expected_revision_binding,
        )

    no_overlay = json.loads(json.dumps(event))
    no_overlay["revisionBinding"]["prIndexed"] = False
    with pytest.raises(RuntimeError, match="did not index"):
        _retrieval_evidence(
            [no_overlay],
            required=True,
            expected_revision_binding=expected_revision_binding,
        )

    wrong_base = json.loads(json.dumps(event))
    wrong_base["revisionBinding"]["baseRevision"] = "5" * 40
    with pytest.raises(RuntimeError, match="baseRevision"):
        _retrieval_evidence(
            [wrong_base],
            required=True,
            expected_revision_binding=expected_revision_binding,
        )

    with pytest.raises(RuntimeError, match="no terminal retrieval evidence"):
        _retrieval_evidence(
            [],
            required=True,
            expected_revision_binding=expected_revision_binding,
        )


def test_request_payload_is_snapshot_exact_and_blinded(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    base, head = make_git_pair(repository)
    case = request_case(repository, base, head)
    replay = request_replay(case)

    payload = _request_payload(
        config=analysis_config(),
        case=case,
        replay=replay,
        repository=repository,
        model="analysis-model",
        api_key="sensitive-key",
    )

    assert payload["baseCommitHash"] == base
    assert payload["commitHash"] == head
    assert payload["targetBranchName"] == replay["baseRef"]
    assert payload["sourceBranchName"] == replay["headRef"]
    assert payload["previousCodeAnalysisIssues"] == []
    assert payload["useMcpTools"] is False
    assert payload["ragEnabled"] is True
    assert payload["changedFiles"] == ["A.php", "B.php", "C.php"]
    assert all(
        item["content"] == "<?php\nreturn 2;\n"
        for item in payload["enrichmentData"]["fileContents"]
    )
    serialized = json.dumps(payload)
    assert "reviewer" not in serialized.casefold()
    assert "discussion_r" not in serialized


def test_request_payload_rejects_diff_path_and_replay_drift(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    base, head = make_git_pair(repository)
    case = request_case(repository, base, head)
    replay = request_replay(case)

    bad_diff = {
        **case,
        "snapshot": {**case["snapshot"], "diffSha256": "0" * 64},
    }
    with pytest.raises(ValueError, match="local diff digest drift"):
        _request_payload(
            config=analysis_config(),
            case=bad_diff,
            replay=replay,
            repository=repository,
            model="model",
            api_key="key",
        )

    bad_paths = {
        **case,
        "snapshot": {**case["snapshot"], "changedPaths": ["A.php"]},
    }
    with pytest.raises(ValueError, match="local changed-path drift"):
        _request_payload(
            config=analysis_config(),
            case=bad_paths,
            replay=replay,
            repository=repository,
            model="model",
            api_key="key",
        )

    bad_replay = {**replay, "headSha": "f" * 40}
    with pytest.raises(ValueError, match="replay lock SHA drift"):
        _request_payload(
            config=analysis_config(),
            case=case,
            replay=bad_replay,
            repository=repository,
            model="model",
            api_key="key",
        )


def _full_replay_lock(corpus):
    fork_repository = "benchmark-owner/magento2"
    plan = build_plan(corpus, fork_repository=fork_repository)
    cases = []
    for number, case in enumerate(corpus["cases"], start=123):
        cases.append(
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
        "cases": cases,
    }
    lock["lockDigest"] = sha256_json(lock)
    return lock


def _replay_attestation(lock):
    fork_repository = lock["forkRepository"]
    cases = []
    for index, locked in enumerate(lock["cases"], start=1):
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
        cases.append(
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
    value = {
        "kind": "codecrow-magento2-replay-attestation",
        "collectedAt": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "corpusId": lock["corpusId"],
        "corpusDigest": lock["corpusDigest"],
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
        "cases": cases,
    }
    value["attestationDigest"] = sha256_json(value)
    return value


def _write_execution_corpus(path: Path, corpus):
    return write_json(path, build_execution_corpus(corpus))


def _stub_run_dependencies(tmp_path, monkeypatch, corpus):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    receipt = {
        "branch": corpus["cases"][0]["replay"]["baseRef"],
        "commit": corpus["cases"][0]["snapshot"]["baseSha"],
        "point_count": 10,
        "plugin_ids": ["php", "magento"],
    }
    monkeypatch.setenv("BENCHMARK_TEST_API_KEY", "secret")
    monkeypatch.setenv(
        "BENCHMARK_TEST_SERVICE_SECRET",
        "internal-secret",
    )
    monkeypatch.setattr(
        "magento2_benchmark.runner.exact_index_receipt",
        lambda *args, **kwargs: receipt,
    )
    monkeypatch.setattr(
        "magento2_benchmark.runner._runtime_provenance",
        lambda config: {
            "analysis": None,
            "rag": None,
            "finalizer": None,
            "required": False,
        },
    )
    monkeypatch.setattr(
        "magento2_benchmark.runner._request_payload",
        lambda **kwargs: {
            "aiApiKey": "secret",
            "enrichmentData": {"fileContents": []},
        },
    )
    finalized = {
        "kind": "codecrow-isolated-analysis-finalization",
        "comment": "finalized",
        "rawIssueCount": 0,
        "finalIssueCount": 0,
        "issues": [],
        "analysisDataValidated": True,
        "persisted": False,
        "published": False,
        "previousIssueStateUsed": False,
    }
    monkeypatch.setattr(
        "magento2_benchmark.runner._product_finalize",
        lambda *args, **kwargs: finalized,
    )
    return repository


def test_run_refuses_non_empty_output_without_resume(tmp_path):
    output = tmp_path / "run"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")

    with pytest.raises(ValueError, match="output directory is non-empty"):
        run_analysis(
            execution_corpus_path=tmp_path / "not-read.json",
            replay_lock_path=tmp_path / "not-read-lock.json",
            repository=tmp_path / "not-read-repository",
            output_dir=output,
            config={},
        )

    assert marker.read_text(encoding="utf-8") == "preserve"
    assert set(output.iterdir()) == {marker}


def test_primary_run_rejects_full_labeled_corpus_before_any_transport(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()

    with pytest.raises(
        ValueError,
        match="label-free analysis execution corpus",
    ):
        run_analysis(
            execution_corpus_path=write_json(
                tmp_path / "released-corpus.json",
                corpus,
            ),
            replay_lock_path=tmp_path / "not-read-lock.json",
            repository=tmp_path / "not-read-repository",
            output_dir=tmp_path / "run",
            config={"analysis": analysis_config()},
        )

    assert not (tmp_path / "run").exists()


def test_run_rejects_every_unknown_selected_case(
    tmp_path,
    monkeypatch,
    corpus_factory,
):
    corpus = corpus_factory()
    lock = _full_replay_lock(corpus)
    repository = _stub_run_dependencies(tmp_path, monkeypatch, corpus)
    sent = []
    monkeypatch.setattr(
        "magento2_benchmark.runner._http_review",
        lambda *args, **kwargs: sent.append(True),
    )

    with pytest.raises(
        ValueError,
        match=r"unknown corpus case IDs: m2b-does-not-exist",
    ):
        run_analysis(
            execution_corpus_path=_write_execution_corpus(tmp_path / "execution-corpus.json", corpus),
            replay_lock_path=write_json(tmp_path / "lock.json", lock),
            repository=repository,
            output_dir=tmp_path / "run",
            config={"analysis": analysis_config()},
            selected_case_ids={"m2b-001", "m2b-does-not-exist"},
        )

    assert sent == []
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize(
    "coordinate_override",
    [
        {"project_vcs_workspace": "Benchmark-Owner"},
        {"project_vcs_repo_slug": "magento2-benchmark"},
    ],
)
def test_run_rejects_project_coordinates_that_do_not_exactly_match_fork(
    tmp_path,
    monkeypatch,
    corpus_factory,
    coordinate_override,
):
    corpus = corpus_factory()
    lock = _full_replay_lock(corpus)
    repository = _stub_run_dependencies(tmp_path, monkeypatch, corpus)
    sent = []
    monkeypatch.setattr(
        "magento2_benchmark.runner._http_review",
        lambda *args, **kwargs: sent.append(True),
    )

    with pytest.raises(
        ValueError,
        match="must exactly match replay lock forkRepository",
    ):
        run_analysis(
            execution_corpus_path=_write_execution_corpus(tmp_path / "execution-corpus.json", corpus),
            replay_lock_path=write_json(tmp_path / "lock.json", lock),
            repository=repository,
            output_dir=tmp_path / "run",
            config={
                "analysis": analysis_config(**coordinate_override),
            },
            selected_case_ids={"m2b-001"},
        )

    assert sent == []
    assert not (tmp_path / "run").exists()


def test_run_uses_safe_preregistered_id_and_requires_exact_resume_id(
    tmp_path,
    monkeypatch,
    corpus_factory,
):
    corpus = corpus_factory()
    lock = _full_replay_lock(corpus)
    repository = _stub_run_dependencies(tmp_path, monkeypatch, corpus)
    calls = []
    monkeypatch.setattr(
        "magento2_benchmark.runner._http_review",
        lambda *args, **kwargs: (
            calls.append(True) or ([], {"issues": []})
        ),
    )
    execution_corpus_path = _write_execution_corpus(tmp_path / "execution-corpus.json", corpus)
    lock_path = write_json(tmp_path / "lock.json", lock)
    output = tmp_path / "run"
    config = {"analysis": analysis_config()}

    manifest = run_analysis(
        execution_corpus_path=execution_corpus_path,
        replay_lock_path=lock_path,
        repository=repository,
        output_dir=output,
        config=config,
        run_id="study-2026:model-a",
        selected_case_ids={"m2b-001"},
    )
    assert manifest["runId"] == "study-2026:model-a"

    resumed = run_analysis(
        execution_corpus_path=execution_corpus_path,
        replay_lock_path=lock_path,
        repository=repository,
        output_dir=output,
        config=config,
        run_id="study-2026:model-a",
        selected_case_ids={"m2b-001"},
        resume=True,
    )
    assert resumed["runId"] == "study-2026:model-a"
    assert calls == [True]

    with pytest.raises(ValueError, match="cannot be resumed"):
        run_analysis(
            execution_corpus_path=execution_corpus_path,
            replay_lock_path=lock_path,
            repository=repository,
            output_dir=output,
            config=config,
            run_id="study-2026:model-b",
            selected_case_ids={"m2b-001"},
            resume=True,
        )


@pytest.mark.parametrize(
    "run_id",
    ["../escape", "", "x" * 257, "contains space"],
)
def test_run_rejects_unsafe_explicit_id_before_reading_inputs(
    tmp_path,
    run_id,
):
    with pytest.raises(ValueError, match="safe 1-256"):
        run_analysis(
            execution_corpus_path=tmp_path / "missing-corpus.json",
            replay_lock_path=tmp_path / "missing-lock.json",
            repository=tmp_path / "missing-repository",
            output_dir=tmp_path / "run",
            config={},
            run_id=run_id,
        )


def test_failed_resume_attempts_are_append_only_and_capped(
    tmp_path,
    monkeypatch,
    corpus_factory,
):
    corpus = corpus_factory()
    lock = _full_replay_lock(corpus)
    repository = _stub_run_dependencies(tmp_path, monkeypatch, corpus)
    calls = []

    def review(*args, **kwargs):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise RuntimeError("fixture transient failure")
        return [], {"issues": []}

    monkeypatch.setattr(
        "magento2_benchmark.runner._http_review",
        review,
    )
    config = {
        "analysis": analysis_config(max_case_attempts=2),
    }
    execution_corpus_path = _write_execution_corpus(tmp_path / "execution-corpus.json", corpus)
    lock_path = write_json(tmp_path / "lock.json", lock)
    output = tmp_path / "run"

    failed = run_analysis(
        execution_corpus_path=execution_corpus_path,
        replay_lock_path=lock_path,
        repository=repository,
        output_dir=output,
        config=config,
        selected_case_ids={"m2b-001"},
    )

    assert failed["status"] == "partial"
    assert failed["attemptPolicy"]["maxAttemptsPerCase"] == 2
    assert len(failed["attemptLedger"]) == 1
    first = failed["attemptLedger"][0]
    assert first["status"] == "failed"
    assert first["stoppingReason"] == "explicit_resume_required"
    assert first["retryEligible"] is True
    first_path = output / first["resultArtifact"]
    first_bytes = first_path.read_bytes()

    completed = run_analysis(
        execution_corpus_path=execution_corpus_path,
        replay_lock_path=lock_path,
        repository=repository,
        output_dir=output,
        config=config,
        selected_case_ids={"m2b-001"},
        resume=True,
    )

    assert calls == [1, 2]
    assert completed["status"] == "completed"
    assert len(completed["attemptLedger"]) == 2
    assert completed["attemptLedger"][0] == first
    assert first_path.read_bytes() == first_bytes
    second = completed["attemptLedger"][1]
    assert second["status"] == "completed"
    assert second["stoppingReason"] == "case_completed"
    assert second["resultArtifact"] != first["resultArtifact"]
    assert completed["cases"][0]["attemptCount"] == 2
    assert completed["cases"][0]["attemptId"] == second["attemptId"]


def test_required_model_evidence_failure_is_retained_before_resume_success(
    tmp_path,
    monkeypatch,
    corpus_factory,
):
    corpus = corpus_factory()
    lock = _full_replay_lock(corpus)
    repository = _stub_run_dependencies(tmp_path, monkeypatch, corpus)
    request = quality_capture_request()
    request["aiApiKey"] = "secret"
    artifact, receipt = quality_capture_fixture(
        expected_request=quality_capture_request(),
    )
    calls = []

    monkeypatch.setattr(
        "magento2_benchmark.runner._request_payload",
        lambda **kwargs: json.loads(json.dumps(request)),
    )

    def review(*args, **kwargs):
        calls.append(len(calls) + 1)
        events = []
        if len(calls) == 2:
            events.append(
                {
                    "type": "status",
                    "state": "review_quality_capture_completed",
                    "qualityCapture": receipt,
                }
            )
        return events, {"issues": []}

    monkeypatch.setattr(
        "magento2_benchmark.runner._queue_review",
        review,
    )
    monkeypatch.setattr(
        "magento2_benchmark.runner._docker_copy_container_file",
        lambda *args, destination, **kwargs: write_json(
            destination,
            artifact,
        ),
    )
    config = {
        "analysis": analysis_config(
            transport="redis",
            endpoint="",
            model="fixture-model",
            expected_response_model="provider-resolved-model",
            require_model_call_evidence=True,
            max_case_attempts=2,
        )
    }
    execution_corpus_path = _write_execution_corpus(tmp_path / "execution-corpus.json", corpus)
    lock_path = write_json(tmp_path / "lock.json", lock)
    output = tmp_path / "run"

    failed = run_analysis(
        execution_corpus_path=execution_corpus_path,
        replay_lock_path=lock_path,
        repository=repository,
        output_dir=output,
        config=config,
        selected_case_ids={"m2b-001"},
    )
    first = failed["attemptLedger"][0]
    first_bytes = (output / first["resultArtifact"]).read_bytes()
    assert first["status"] == "failed"
    assert first["modelCallEvidence"] is None
    assert "no terminal model-call" in first["error"]

    completed = run_analysis(
        execution_corpus_path=execution_corpus_path,
        replay_lock_path=lock_path,
        repository=repository,
        output_dir=output,
        config=config,
        selected_case_ids={"m2b-001"},
        resume=True,
    )

    assert calls == [1, 2]
    assert completed["status"] == "completed"
    assert len(completed["attemptLedger"]) == 2
    assert completed["attemptLedger"][0] == first
    assert (output / first["resultArtifact"]).read_bytes() == first_bytes
    evidence = completed["cases"][0]["modelCallEvidence"]
    assert evidence == completed["attemptLedger"][1]["modelCallEvidence"]
    assert (output / evidence["artifact"]).is_file()
    assert completed["analysisModelRoles"] == {
        "reviewPipeline": "fixture-model",
        "reviewPipelineRequested": "fixture-model",
        "reviewPipelineExpectedResponse": "provider-resolved-model",
        "reviewPipelineProviderReported": ["provider-resolved-model"],
        "providerReportedByStage": {
            "stage_1": ["provider-resolved-model"],
        },
    }


def test_resume_records_interrupted_attempt_before_retry(
    tmp_path,
    monkeypatch,
    corpus_factory,
):
    corpus = corpus_factory()
    lock = _full_replay_lock(corpus)
    repository = _stub_run_dependencies(tmp_path, monkeypatch, corpus)
    execution_corpus_path = _write_execution_corpus(tmp_path / "execution-corpus.json", corpus)
    lock_path = write_json(tmp_path / "lock.json", lock)
    output = tmp_path / "run"
    config = {
        "analysis": analysis_config(max_case_attempts=2),
    }
    monkeypatch.setattr(
        "magento2_benchmark.runner._http_review",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        run_analysis(
            execution_corpus_path=execution_corpus_path,
            replay_lock_path=lock_path,
            repository=repository,
            output_dir=output,
            config=config,
            selected_case_ids={"m2b-001"},
        )

    interrupted_manifest = json.loads((output / "run.json").read_text())
    interrupted = interrupted_manifest["attemptLedger"][0]
    assert interrupted["status"] == "running"
    assert (output / interrupted["startArtifact"]).is_file()
    assert not (output / interrupted["resultArtifact"]).exists()

    monkeypatch.setattr(
        "magento2_benchmark.runner._http_review",
        lambda *args, **kwargs: ([], {"issues": []}),
    )
    completed = run_analysis(
        execution_corpus_path=execution_corpus_path,
        replay_lock_path=lock_path,
        repository=repository,
        output_dir=output,
        config=config,
        selected_case_ids={"m2b-001"},
        resume=True,
    )

    assert completed["status"] == "completed"
    assert len(completed["attemptLedger"]) == 2
    recovered = completed["attemptLedger"][0]
    assert recovered["status"] == "failed"
    assert recovered["stoppingReason"] == "explicit_resume_required"
    recovered_raw = json.loads(
        (output / recovered["resultArtifact"]).read_text()
    )
    assert recovered_raw["terminationReason"] == "runner_interrupted"
    assert "prior runner invocation ended" in recovered_raw["error"]


def test_attempt_cap_stops_resume_without_new_request(
    tmp_path,
    monkeypatch,
    corpus_factory,
):
    corpus = corpus_factory()
    lock = _full_replay_lock(corpus)
    repository = _stub_run_dependencies(tmp_path, monkeypatch, corpus)
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("permanent fixture failure")

    monkeypatch.setattr(
        "magento2_benchmark.runner._http_review",
        fail,
    )
    config = {
        "analysis": analysis_config(max_case_attempts=1),
    }
    execution_corpus_path = _write_execution_corpus(tmp_path / "execution-corpus.json", corpus)
    lock_path = write_json(tmp_path / "lock.json", lock)
    output = tmp_path / "run"
    failed = run_analysis(
        execution_corpus_path=execution_corpus_path,
        replay_lock_path=lock_path,
        repository=repository,
        output_dir=output,
        config=config,
        selected_case_ids={"m2b-001"},
    )
    result_path = output / failed["attemptLedger"][0]["resultArtifact"]
    result_bytes = result_path.read_bytes()

    stopped = run_analysis(
        execution_corpus_path=execution_corpus_path,
        replay_lock_path=lock_path,
        repository=repository,
        output_dir=output,
        config=config,
        selected_case_ids={"m2b-001"},
        resume=True,
    )

    assert calls == [True]
    assert len(stopped["attemptLedger"]) == 1
    assert stopped["attemptLedger"][0]["stoppingReason"] == (
        "attempt_limit_reached"
    )
    assert stopped["cases"][0]["retryEligible"] is False
    assert stopped["cases"][0]["stoppingReason"] == "attempt_limit_reached"
    assert result_path.read_bytes() == result_bytes


def test_run_rejects_a_self_consistent_partial_replay_lock(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    lock = _full_replay_lock(corpus)
    lock["cases"] = lock["cases"][:1]
    lock["lockDigest"] = sha256_json(
        {key: value for key, value in lock.items() if key != "lockDigest"}
    )

    with pytest.raises(
        ValueError,
        match="exactly match corpus case order and set",
    ):
        run_analysis(
            execution_corpus_path=_write_execution_corpus(tmp_path / "execution-corpus.json", corpus),
            replay_lock_path=write_json(tmp_path / "lock.json", lock),
            repository=tmp_path / "not-reached",
            output_dir=tmp_path / "run",
            config={"analysis": analysis_config()},
            selected_case_ids={"m2b-001"},
        )


def test_frozen_paper_run_requires_replay_attestation_before_analysis(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    lock = _full_replay_lock(corpus)

    with pytest.raises(ValueError, match="live replay attestation is required"):
        run_analysis(
            execution_corpus_path=_write_execution_corpus(tmp_path / "execution-corpus.json", corpus),
            replay_lock_path=write_json(tmp_path / "lock.json", lock),
            repository=tmp_path / "not-reached",
            output_dir=tmp_path / "run",
            config={
                "analysis": analysis_config(
                    require_runtime_provenance=True,
                )
            },
            selected_case_ids={"m2b-001"},
        )


def test_frozen_paper_run_rejects_stale_replay_attestation_before_analysis(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    lock = _full_replay_lock(corpus)
    attestation = _replay_attestation(lock)
    attestation["collectedAt"] = "2000-01-01T00:00:00Z"
    attestation.pop("attestationDigest")
    attestation["attestationDigest"] = sha256_json(attestation)

    with pytest.raises(ValueError, match="stale"):
        run_analysis(
            execution_corpus_path=_write_execution_corpus(tmp_path / "execution-corpus.json", corpus),
            replay_lock_path=write_json(tmp_path / "lock.json", lock),
            replay_attestation_path=write_json(
                tmp_path / "attestation.json",
                attestation,
            ),
            repository=tmp_path / "not-reached",
            output_dir=tmp_path / "run",
            config={
                "analysis": analysis_config(
                    require_replay_attestation=True,
                )
            },
            selected_case_ids={"m2b-001"},
        )


def test_run_fails_if_exact_index_receipt_changes_mid_run(
    tmp_path,
    monkeypatch,
    corpus_factory,
):
    corpus = corpus_factory()
    lock = _full_replay_lock(corpus)
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    receipts = iter(
        [
            {
                "branch": corpus["cases"][0]["replay"]["baseRef"],
                "commit": corpus["cases"][0]["snapshot"]["baseSha"],
                "point_count": 10,
                "plugin_ids": ["php", "magento"],
                "receipt": "before",
            },
            {
                "branch": corpus["cases"][0]["replay"]["baseRef"],
                "commit": corpus["cases"][0]["snapshot"]["baseSha"],
                "point_count": 11,
                "plugin_ids": ["php", "magento"],
                "receipt": "after",
            },
        ]
    )
    monkeypatch.setenv("BENCHMARK_TEST_API_KEY", "secret")
    monkeypatch.setenv("BENCHMARK_TEST_SERVICE_SECRET", "internal-secret")
    monkeypatch.setattr(
        "magento2_benchmark.runner.exact_index_receipt",
        lambda *args, **kwargs: next(receipts),
    )
    monkeypatch.setattr(
        "magento2_benchmark.runner._runtime_provenance",
        lambda config: {
            "analysis": None,
            "rag": None,
            "finalizer": None,
            "required": False,
        },
    )
    monkeypatch.setattr(
        "magento2_benchmark.runner._request_payload",
        lambda **kwargs: {"aiApiKey": "secret", "request": "fixture"},
    )
    monkeypatch.setattr(
        "magento2_benchmark.runner._http_review",
        lambda *args, **kwargs: ([], {"issues": []}),
    )

    with pytest.raises(RuntimeError, match="receipt changed during run"):
        run_analysis(
            execution_corpus_path=_write_execution_corpus(tmp_path / "execution-corpus.json", corpus),
            replay_lock_path=write_json(tmp_path / "lock.json", lock),
            repository=repository,
            output_dir=tmp_path / "run",
            config={"analysis": analysis_config()},
            selected_case_ids={"m2b-001"},
        )


def test_run_preflights_index_before_sending_any_analysis_request(
    tmp_path,
    monkeypatch,
    corpus_factory,
):
    corpus = corpus_factory()
    lock = _full_replay_lock(corpus)
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    sent = []
    monkeypatch.setenv("BENCHMARK_TEST_API_KEY", "secret")
    monkeypatch.setenv("BENCHMARK_TEST_SERVICE_SECRET", "internal-secret")

    def unavailable(*args, **kwargs):
        raise RuntimeError("exact revision absent")

    monkeypatch.setattr(
        "magento2_benchmark.runner.exact_index_receipt",
        unavailable,
    )
    monkeypatch.setattr(
        "magento2_benchmark.runner._runtime_provenance",
        lambda config: {
            "analysis": None,
            "rag": None,
            "finalizer": None,
            "required": False,
        },
    )
    monkeypatch.setattr(
        "magento2_benchmark.runner._http_review",
        lambda *args, **kwargs: sent.append(True),
    )

    with pytest.raises(RuntimeError, match="exact revision absent"):
        run_analysis(
            execution_corpus_path=_write_execution_corpus(tmp_path / "execution-corpus.json", corpus),
            replay_lock_path=write_json(tmp_path / "lock.json", lock),
            repository=repository,
            output_dir=tmp_path / "run",
            config={"analysis": analysis_config()},
            selected_case_ids={"m2b-001"},
        )

    assert sent == []


def test_run_scores_only_java_finalized_product_issues(
    tmp_path,
    monkeypatch,
    corpus_factory,
):
    corpus = corpus_factory()
    lock = _full_replay_lock(corpus)
    attestation = _replay_attestation(lock)
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    receipt = {
        "branch": corpus["cases"][0]["replay"]["baseRef"],
        "commit": corpus["cases"][0]["snapshot"]["baseSha"],
        "point_count": 10,
        "plugin_ids": ["php", "magento"],
    }
    monkeypatch.setenv("BENCHMARK_TEST_API_KEY", "secret")
    monkeypatch.setenv(
        "BENCHMARK_TEST_SERVICE_SECRET",
        "internal-secret",
    )
    monkeypatch.setattr(
        "magento2_benchmark.runner.exact_index_receipt",
        lambda *args, **kwargs: receipt,
    )
    monkeypatch.setattr(
        "magento2_benchmark.runner._runtime_provenance",
        lambda config: {
            "analysis": None,
            "rag": None,
            "finalizer": None,
            "required": False,
        },
    )
    monkeypatch.setattr(
        "magento2_benchmark.runner._request_payload",
        lambda **kwargs: {
            "aiApiKey": "secret",
            "enrichmentData": {"fileContents": []},
        },
    )
    monkeypatch.setattr(
        "magento2_benchmark.runner._http_review",
        lambda *args, **kwargs: (
            [],
            {
                "comment": "raw",
                "issues": [
                    {
                        "file": "Raw.php",
                        "line": 99,
                        "title": "Raw issue",
                        "reason": "must not be scored",
                    }
                ],
            },
        ),
    )
    finalized = {
        "kind": "codecrow-isolated-analysis-finalization",
        "comment": "finalized",
        "rawIssueCount": 1,
        "finalIssueCount": 1,
        "issues": [
            {
                "file": "Final.php",
                "line": 7,
                "title": "Finalized issue",
                "reason": "score this product issue",
                "category": "CODE_QUALITY",
                "severity": "HIGH",
            }
        ],
        "analysisDataValidated": True,
        "persisted": False,
        "published": False,
        "previousIssueStateUsed": False,
    }
    monkeypatch.setattr(
        "magento2_benchmark.runner._product_finalize",
        lambda *args, **kwargs: finalized,
    )

    manifest = run_analysis(
        execution_corpus_path=_write_execution_corpus(tmp_path / "execution-corpus.json", corpus),
        replay_lock_path=write_json(tmp_path / "lock.json", lock),
        replay_attestation_path=write_json(
            tmp_path / "attestation.json",
            attestation,
        ),
        repository=repository,
        output_dir=tmp_path / "run",
        config={
            "analysis": analysis_config(
                require_replay_attestation=True,
            )
        },
        selected_case_ids={"m2b-001"},
    )

    assert manifest["status"] == "completed"
    assert manifest["replayAttestationDigest"] == (
        attestation["attestationDigest"]
    )
    assert manifest["replayAttestationArtifact"] == "replay-attestation.json"
    assert manifest["replayLockArtifact"] == "replay-lock.json"
    assert json.loads(
        (tmp_path / "run" / "replay-lock.json").read_text()
    ) == lock
    assert json.loads(
        (tmp_path / "run" / "replay-attestation.json").read_text()
    ) == attestation
    assert (
        manifest["findingSemantics"]
        == "java-finalized-transient-first-iteration"
    )
    assert manifest["cases"][0]["findings"][0]["path"] == "Final.php"
    assert (
        manifest["cases"][0]["productFinalization"]["finalIssueCount"]
        == 1
    )
    raw = json.loads(
        (
            tmp_path
            / "run"
            / manifest["cases"][0]["rawResponse"]
        ).read_text()
    )
    assert raw["response"]["issues"][0]["file"] == "Raw.php"
    assert raw["productFinalization"]["issues"][0]["file"] == "Final.php"
    assert raw["redactedRequest"]["aiApiKey"] == "<redacted>"
    assert manifest["cases"][0]["requestDigest"] == sha256_json(
        raw["redactedRequest"]
    )
    assert isinstance(
        manifest["cases"][0]["requestControlDigest"],
        str,
    )
    assert "secret" not in json.dumps(raw["redactedRequest"])
    gold = corpus["cases"][0]["goldenComments"][0]
    run_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((tmp_path / "run").rglob("*.json"))
    )
    assert gold["reviewer"] not in run_text
    assert gold["body"] not in run_text
    assert str(gold["sourceCommentId"]) not in run_text
    assert manifest["executionCorpusDigest"] == (
        lock["executionCorpusDigest"]
    )
    assert manifest["executionCorpusArtifact"] == (
        "analysis-execution-corpus.json"
    )
    assert json.loads(
        (
            tmp_path
            / "run"
            / manifest["executionCorpusArtifact"]
        ).read_text(encoding="utf-8")
    ) == build_execution_corpus(corpus)

    drifted_attestation = json.loads(json.dumps(attestation))
    drifted_attestation["repositoryObservation"]["nodeId"] = (
        "R_attestation_drifted"
    )
    drifted_attestation["attestationDigest"] = sha256_json(
        {
            key: value
            for key, value in drifted_attestation.items()
            if key != "attestationDigest"
        }
    )
    with pytest.raises(
        ValueError,
        match="replay attestation artifact cannot be replaced",
    ):
        run_analysis(
            execution_corpus_path=tmp_path / "execution-corpus.json",
            replay_lock_path=tmp_path / "lock.json",
            replay_attestation_path=write_json(
                tmp_path / "drifted-attestation.json",
                drifted_attestation,
            ),
            repository=repository,
            output_dir=tmp_path / "run",
            config={
                "analysis": analysis_config(
                    require_replay_attestation=True,
                )
            },
            selected_case_ids={"m2b-001"},
            resume=True,
        )
