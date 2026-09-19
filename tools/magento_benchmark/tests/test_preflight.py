from __future__ import annotations

import copy
import json
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

from magento2_benchmark.config import load_config
from magento2_benchmark.execution_corpus import build_execution_corpus
from magento2_benchmark.preflight import (
    PREFLIGHT_KIND,
    _source_tree_sha256_at_commit,
    build_operator_preflight,
)
from magento2_benchmark.replay import build_plan
from magento2_benchmark.util import sha256_json
from tools.source_tree_identity import compute_repository_source_tree_sha256

from conftest import make_git_pair


REFERENCE_AT = "2026-07-29T12:00:00Z"
FORK = "benchmark-owner/magento2"
REPOSITORY = Path(__file__).resolve().parents[3]


def _lock(corpus: dict[str, Any]) -> dict[str, Any]:
    plan = build_plan(corpus, fork_repository=FORK)
    value = {
        "kind": "codecrow-magento2-replay-lock",
        "generatedAt": REFERENCE_AT,
        "forkRepository": FORK,
        "corpusId": corpus["corpusId"],
        "corpusDigest": corpus["corpusDigest"],
        "executionCorpusDigest": plan["executionCorpusDigest"],
        "planDigest": plan["planDigest"],
        "plan": plan,
        "cases": [
            {
                "caseId": case["caseId"],
                "baseRef": case["replay"]["baseRef"],
                "baseSha": case["snapshot"]["baseSha"],
                "headRef": case["replay"]["headRef"],
                "headSha": case["snapshot"]["headSha"],
                "forkPrNumber": index,
                "forkPrUrl": f"https://github.com/{FORK}/pull/{index}",
            }
            for index, case in enumerate(corpus["cases"], start=1)
        ],
    }
    value["lockDigest"] = sha256_json(value)
    return value


def _attestation(
    corpus: dict[str, Any],
    lock: dict[str, Any],
) -> dict[str, Any]:
    cases = []
    for index, item in enumerate(lock["cases"], start=1):
        refs = {}
        for side in ("base", "head"):
            name = item[f"{side}Ref"]
            sha = item[f"{side}Sha"]
            encoded = urllib.parse.quote(name, safe="")
            refs[f"{side}Ref"] = {
                "apiPath": f"/repos/{FORK}/git/ref/heads/{encoded}",
                "name": name,
                "qualifiedName": f"refs/heads/{name}",
                "sha": sha,
                "objectType": "commit",
                "objectApiUrl": (
                    f"https://api.github.com/repos/{FORK}/git/commits/{sha}"
                ),
            }
        cases.append(
            {
                "caseId": item["caseId"],
                **refs,
                "pullRequest": {
                    "apiPath": (
                        f"/repos/{FORK}/pulls/{item['forkPrNumber']}"
                    ),
                    "pullRequestId": 10_000 + index,
                    "nodeId": f"PR_fixture_{index}",
                    "number": item["forkPrNumber"],
                    "htmlUrl": item["forkPrUrl"],
                    "state": "open",
                    "baseRepository": FORK,
                    "baseRef": item["baseRef"],
                    "baseSha": item["baseSha"],
                    "headRepository": FORK,
                    "headRef": item["headRef"],
                    "headSha": item["headSha"],
                },
            }
        )
    value = {
        "kind": "codecrow-magento2-replay-attestation",
        "collectedAt": REFERENCE_AT,
        "corpusId": corpus["corpusId"],
        "corpusDigest": corpus["corpusDigest"],
        "executionCorpusDigest": lock["executionCorpusDigest"],
        "replayLockDigest": lock["lockDigest"],
        "planDigest": lock["planDigest"],
        "forkRepository": FORK,
        "repositoryObservation": {
            "apiPath": f"/repos/{FORK}",
            "repositoryId": 123,
            "nodeId": "R_fixture",
            "fullName": FORK,
            "fork": True,
            "upstreamRepository": "magento/magento2",
        },
        "cases": cases,
    }
    value["attestationDigest"] = sha256_json(value)
    return value


def _config() -> dict[str, Any]:
    config = load_config(None)
    config["analysis"].update(
        {
            "project_id": 123,
            "project_vcs_workspace": "benchmark-owner",
            "project_vcs_repo_slug": "magento2",
            "project_workspace": "Magento Benchmark",
            "project_namespace": "magento2-core-review",
            "rag_workspace": "Magento Benchmark",
            "rag_project": "magento2-core-review",
            "provider": "fixture-provider",
            "model": "fixture/analysis",
            "expected_response_model": "fixture/analysis-immutable",
            "require_exact_index": True,
            "require_retrieval_evidence": True,
            "require_model_call_evidence": True,
            "require_replay_attestation": True,
            "require_runtime_provenance": True,
            "replay_attestation_max_age_seconds": 3_600,
            "transport": "redis",
        }
    )
    config["judge"].update(
        {
            "model": "fixture/judge",
            "expected_response_model": "fixture/judge-immutable",
        }
    )
    return config


def _environment() -> dict[str, str]:
    return {
        "GITHUB_TOKEN": "github-secret-value",
        "OPENROUTER_API_KEY": "provider-secret-value",
        "CODECROW_SERVICE_SECRET": "service-secret-value",
    }


def _tree_digest(commit: str) -> str:
    return sha256_json({"fixtureSourceTree": commit})


def _snapshot_probe(
    repository: Path,
    case: dict[str, Any],
) -> dict[str, Any]:
    assert repository == REPOSITORY
    snapshot = case["snapshot"]
    return {
        "basePresent": True,
        "headPresent": True,
        "baseAncestorHead": True,
        "diffSha256": snapshot["diffSha256"],
        "changedPaths": snapshot["changedPaths"],
        "baseSourceTreeSha256": _tree_digest(snapshot["baseSha"]),
    }


def _receipt_reader(
    analysis: dict[str, Any],
    *,
    branch: str,
    commit: str,
    service_secret: str,
) -> dict[str, Any]:
    assert service_secret
    policy = {
        "schema": "codecrow.repository-index-selection",
        "includePatterns": ["app/**"],
        "excludePatterns": ["dev/tests/**", "vendor/**"],
    }
    return {
        "workspace": analysis["rag_workspace"],
        "project": analysis["rag_project"],
        "branch": branch,
        "commit": commit,
        "point_count": 501,
        "generation_schema": "codecrow.repository-index-generation",
        "generation_member_count": 500,
        "generation_members_sha256": sha256_json(
            {"members": commit}
        ),
        "generation_manifest_sha256": sha256_json(
            {"manifest": commit}
        ),
        "source_tree_sha256": _tree_digest(commit),
        "index_include_patterns": policy["includePatterns"],
        "index_exclude_patterns": policy["excludePatterns"],
        "index_selection_policy_sha256": sha256_json(policy),
        "repository_revision": commit,
        "repository_facts_sha256": sha256_json({"facts": commit}),
        "plugin_ids": ["composer", "magento", "php"],
        "plugin_fingerprint": "sha256:" + "a" * 64,
        "plugin_descriptor_fingerprint": "sha256:" + "b" * 64,
        "plugin_implementation_fingerprint": "sha256:" + "c" * 64,
        "index_representation_fingerprint": "sha256:" + "d" * 64,
    }


def _runtime_probe(name: str) -> dict[str, Any]:
    return {
        "containerId": f"container:{name}",
        "imageId": f"sha256:{sha256_json({'container': name})}",
        "imageReference": f"fixture/{name}:paper",
        "status": "running",
        "health": "healthy",
    }


def _run(
    corpus: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    environment: dict[str, str] | None = None,
    receipt_reader=_receipt_reader,
) -> dict[str, Any]:
    lock = _lock(corpus)
    return build_operator_preflight(
        config=config or _config(),
        execution_corpus=build_execution_corpus(corpus),
        replay_lock=lock,
        replay_attestation=_attestation(corpus, lock),
        repository=REPOSITORY,
        environment=environment if environment is not None else _environment(),
        reference_at=REFERENCE_AT,
        receipt_reader=receipt_reader,
        snapshot_probe=_snapshot_probe,
        runtime_probe=_runtime_probe,
    )


def _check_by_id(artifact: dict[str, Any], check_id: str) -> dict[str, Any]:
    return next(check for check in artifact["checks"] if check["id"] == check_id)


def test_preflight_proves_all_50_read_only_inputs_and_seals_artifact(
    corpus_factory,
):
    artifact = _run(corpus_factory())

    assert artifact["kind"] == PREFLIGHT_KIND
    assert artifact["operationMode"] == "strictly-read-only"
    assert artifact["runReady"] is True
    assert artifact["paperReady"] is False
    assert all(artifact["sideEffectPolicy"].values()) is False
    assert len(artifact["snapshots"]) == 50
    assert len(artifact["indexReceipts"]) == 50
    assert all(
        check["status"] == "passed"
        for check in artifact["checks"]
        if check["requiredForRun"] is True
    )
    publication = _check_by_id(
        artifact,
        "publication.post_run_protocol_evidence",
    )
    assert publication["status"] == "blocked"
    assert publication["requiredForRun"] is False
    assert publication["failures"][0]["code"] == (
        "analysis_and_post_run_protocol_evidence_not_yet_produced"
    )
    digest_payload = dict(artifact)
    declared = digest_payload.pop("readinessDigest")
    assert declared == sha256_json(digest_payload)


def test_checkout_free_source_tree_probe_matches_indexer_identity(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    _, head = make_git_pair(repository)

    assert _source_tree_sha256_at_commit(repository, head) == (
        compute_repository_source_tree_sha256(repository)
    )


def test_preflight_schema_declares_digest_and_read_only_contract():
    schema = json.loads(
        (
            Path(__file__).parents[1]
            / "schemas"
            / "operator-preflight.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert schema["properties"]["kind"]["const"] == PREFLIGHT_KIND
    assert schema["properties"]["operationMode"]["const"] == (
        "strictly-read-only"
    )
    assert schema["properties"]["sideEffectPolicy"]["properties"][
        "analysisJobsEnqueued"
    ]["const"] is False
    assert "readinessDigest" in schema["required"]
    assert "runReady" in schema["required"]
    assert schema["properties"]["paperReady"]["const"] is False


@pytest.mark.parametrize("mode", ["missing", "drifted"])
def test_preflight_fails_closed_for_missing_or_drifted_receipt(
    corpus_factory,
    mode,
):
    def hostile_reader(*args, branch, commit, **kwargs):
        if branch.endswith("m2b-017/base"):
            if mode == "missing":
                return None
            value = _receipt_reader(
                *args,
                branch=branch,
                commit=commit,
                **kwargs,
            )
            value["source_tree_sha256"] = "f" * 64
            return value
        return _receipt_reader(
            *args,
            branch=branch,
            commit=commit,
            **kwargs,
        )

    artifact = _run(corpus_factory(), receipt_reader=hostile_reader)

    assert artifact["runReady"] is False
    assert artifact["paperReady"] is False
    receipt_check = _check_by_id(artifact, "index.all_exact_base_receipts")
    assert receipt_check["status"] == "failed"
    assert any(
        failure["code"]
        in {"receipt_missing", "source_tree_mismatch"}
        for failure in receipt_check["failures"]
    )
    assert (
        _check_by_id(artifact, "index.receipt_control_consistency")["status"]
        == "blocked"
    )


def test_preflight_rejects_configured_fork_and_rag_identity_drift(
    corpus_factory,
):
    config = _config()
    config["analysis"]["project_vcs_workspace"] = "wrong-owner"
    config["analysis"]["rag_project"] = "wrong-project"

    artifact = _run(corpus_factory(), config=config)

    assert artifact["runReady"] is False
    assert artifact["paperReady"] is False
    identity = _check_by_id(artifact, "configuration.project_identity")
    assert identity["status"] == "failed"
    assert {failure["code"] for failure in identity["failures"]} == {
        "fork_identity_mismatch",
        "rag_project_identity_mismatch",
    }


def test_preflight_reports_missing_environment_variables_without_values(
    corpus_factory,
):
    environment = {"GITHUB_TOKEN": "github-secret-value"}

    artifact = _run(corpus_factory(), environment=environment)
    serialized = json.dumps(artifact)

    assert artifact["runReady"] is False
    assert artifact["paperReady"] is False
    env_check = _check_by_id(artifact, "environment.required_variables")
    assert env_check["status"] == "failed"
    assert sum(
        failure["code"] == "missing_environment_variable"
        for failure in env_check["failures"]
    ) == 3
    assert "github-secret-value" not in serialized
    assert "provider-secret-value" not in serialized
    assert "service-secret-value" not in serialized


def test_preflight_never_persists_a_secret_echoed_by_index_service(
    corpus_factory,
):
    secret = _environment()["CODECROW_SERVICE_SECRET"]

    def leaking_reader(*args, **kwargs):
        value = _receipt_reader(*args, **kwargs)
        value["diagnostic"] = f"unexpected echo: {secret}"
        return value

    artifact = _run(corpus_factory(), receipt_reader=leaking_reader)
    serialized = json.dumps(artifact)

    assert artifact["runReady"] is False
    assert artifact["paperReady"] is False
    assert secret not in serialized
    receipt_check = _check_by_id(artifact, "index.all_exact_base_receipts")
    assert receipt_check["status"] == "failed"
    assert all(
        secret not in failure["detail"]
        for check in artifact["checks"]
        for failure in check["failures"]
    )


def test_preflight_rejects_disabled_paper_runtime_evidence_controls(
    corpus_factory,
):
    config = _config()
    config["analysis"]["require_model_call_evidence"] = False
    config["analysis"]["require_runtime_provenance"] = False

    artifact = _run(corpus_factory(), config=config)

    assert artifact["runReady"] is False
    assert artifact["paperReady"] is False
    controls = _check_by_id(
        artifact,
        "configuration.paper_runtime_controls",
    )
    assert controls["status"] == "failed"
    assert len(controls["failures"]) == 2
