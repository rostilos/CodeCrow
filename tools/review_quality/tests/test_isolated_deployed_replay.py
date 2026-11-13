from __future__ import annotations

import json
import stat
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from tools.review_quality.isolated_deployed_replay import (
    BASE_FILES,
    DUMMY_REVIEW_KEY,
    EXPECTED_RELATED_PATHS,
    HEAD_REPLACEMENTS,
    ISOLATED_PROJECT_ID,
    audit_expected_context,
    require_expected_context,
    build_review_request,
    build_review_overlay,
    build_synthetic_repository,
    _copy_artifact_for_audit,
    _assert_no_connected_identity,
    _exclusive_isolated_state_lock,
    _queue_request_payload,
    _queue_review,
    _review_generation_cleanup_receipt,
    _review_generation_receipt,
    _structural_generation_cleanup_paths,
    _structural_generation_discovery_path,
    _trusted_relation_records,
)


def test_synthetic_repository_is_remote_free_and_immutable(tmp_path):
    repository = build_synthetic_repository(tmp_path)

    assert len(repository.base_revision) == 40
    assert len(repository.head_revision) == 40
    assert repository.base_revision != repository.head_revision
    assert repository.changed_files == tuple(sorted(HEAD_REPLACEMENTS))
    assert repository.raw_diff.count("diff --git ") == len(HEAD_REPLACEMENTS)
    assert not (repository.base_tree / ".git").exists()
    assert {
        path: (repository.base_tree / path).read_text(encoding="utf-8")
        for path in BASE_FILES
    } == BASE_FILES

    request = build_review_request(
        repository,
        project_namespace="neutral-mixed-test",
        dry_run_id="neutral-mixed-test-run",
    )
    assert request.projectId == ISOLATED_PROJECT_ID
    assert request.promptDryRun is True
    assert request.aiApiKey == DUMMY_REVIEW_KEY
    assert request.changedFiles == list(repository.changed_files)
    assert request.currentCommitHash == repository.head_revision
    assert request.baseCommitHash == repository.base_revision
    assert request.projectCapabilities is not None
    assert request.projectCapabilities.repositoryPlugins == [
        "java",
        "python",
        "typescript",
    ]
    assert request.useMcpTools is True


def test_review_overlay_matches_production_manifest_layout(tmp_path):
    repository = build_synthetic_repository(tmp_path)
    overlay = build_review_overlay(tmp_path, repository)

    assert json.loads((overlay / "manifest.json").read_text()) == {
        "changedFiles": list(repository.changed_files),
        "deletedFiles": [],
    }
    assert {
        path: (overlay / "files" / path).read_text(encoding="utf-8")
        for path in repository.changed_files
    } == repository.head_files


def test_expected_context_audit_requires_one_owner_and_related_path():
    changed_paths = list(EXPECTED_RELATED_PATHS)
    relations = [
        {
            "evidenceId": "relation:" + f"{index:x}" * 64,
            "kind": "fixture-relation",
            "source": changed_path,
            "target": related_path,
            "relatedPaths": [changed_path, related_path],
        }
        for index, (changed_path, related_path) in enumerate(
            EXPECTED_RELATED_PATHS.items(),
            start=1,
        )
    ]
    artifact = {
        "prompts": [
            {
                "stage": "stage_1",
                "renderedPrompt": (
                    "RELATION-FIRST PROPOSED-TREE BRIEFING\n"
                    + json.dumps({
                        "kind": "proposed_tree_relation_briefing",
                        "focusPaths": changed_paths,
                        "relations": relations,
                        "nodes": [],
                    }, separators=(",", ":"))
                ),
            },
        ],
        "promptAssemblyDiagnostics": {
            "stage1": [{
                "batchPaths": changed_paths,
                "structuralContextChars": 500,
            }],
        },
    }

    report = audit_expected_context(
        artifact,
        trusted_relation_records=_trusted_relation_records({
            # Hop is query-local navigation metadata. The all-path preflight
            # and per-batch briefing may assign different distances to the same
            # attested relation fact.
            "evidence": {
                "relations": [
                    {**relation, "hop": 7} for relation in relations
                ],
                "nodes": [],
            },
        }),
    )

    assert report["status"] == "passed"
    require_expected_context(report)
    assert report["failedPaths"] == []
    assert set(report["paths"]) == set(changed_paths)
    assert all(
        item["relationEvidenceVisible"]
        and item["pairedRelationEvidenceCount"] == 1
        and item["structuralCharacters"] > 0
        for item in report["paths"].values()
    )
    assert DUMMY_REVIEW_KEY not in json.dumps(report)


def test_expected_context_audit_rejects_missing_fixture_relation_context():
    changed_path = next(iter(EXPECTED_RELATED_PATHS))
    related_path = EXPECTED_RELATED_PATHS[changed_path]
    relations = [{
        "evidenceId": "relation:" + "a" * 64,
        "kind": "unrelated",
        "source": "other.py",
        "target": related_path,
        "relatedPaths": ["other.py", related_path],
    }]
    artifact = {
        "prompts": [{
            "stage": "stage_1",
            "renderedPrompt": json.dumps({
                "kind": "proposed_tree_relation_briefing",
                "focusPaths": [changed_path],
                "relations": relations,
                "nodes": [],
            }, separators=(",", ":")),
        }],
        "promptAssemblyDiagnostics": {
            "stage1": [{
                "batchPaths": [changed_path],
                "structuralContextChars": 10,
            }],
        },
    }

    report = audit_expected_context(
        artifact,
        trusted_relation_records=_trusted_relation_records({
            "evidence": {"relations": relations, "nodes": []},
        }),
    )

    assert report["status"] == "degraded"
    assert changed_path in report["failedPaths"]
    with pytest.raises(
        RuntimeError,
        match="expected relation context audit failed:",
    ) as error:
        require_expected_context(report)
    assert changed_path in str(error.value)


def test_expected_context_audit_rejects_forged_fact_with_trusted_evidence_id():
    changed_paths = list(EXPECTED_RELATED_PATHS)
    trusted_relations = [
        {
            "evidenceId": "relation:" + f"{index:x}" * 64,
            "kind": "fixture-relation",
            "source": changed_path,
            "target": related_path,
            "relatedPaths": [changed_path, related_path],
            "attributes": {"attested": True},
        }
        for index, (changed_path, related_path) in enumerate(
            EXPECTED_RELATED_PATHS.items(),
            start=1,
        )
    ]
    forged_relations = json.loads(json.dumps(trusted_relations))
    forged_relations[0]["attributes"] = {"attested": False}
    rendered = json.dumps({
        "kind": "proposed_tree_relation_briefing",
        "focusPaths": changed_paths,
        "relations": forged_relations,
        "nodes": [],
    }, separators=(",", ":"))
    artifact = {
        "prompts": [{"stage": "stage_1", "renderedPrompt": rendered}],
        "promptAssemblyDiagnostics": {
            "stage1": [{
                "batchPaths": changed_paths,
                "totalPromptChars": len(rendered),
                "structuralContextChars": 500,
            }],
        },
    }

    report = audit_expected_context(
        artifact,
        trusted_relation_records=_trusted_relation_records({
            "evidence": {"relations": trusted_relations, "nodes": []},
        }),
    )

    assert report["status"] == "degraded"
    assert changed_paths[0] in report["failedPaths"]
    assert report["paths"][changed_paths[0]][
        "pairedRelationEvidenceCount"
    ] == 0


def test_expected_context_audit_correlates_parallel_records_by_content():
    relations = []
    prompts = []
    diagnostics = []
    for index, (changed_path, related_path) in enumerate(
        EXPECTED_RELATED_PATHS.items(),
        start=1,
    ):
        relation = {
            "evidenceId": "relation:" + f"{index:x}" * 64,
            "kind": "fixture-relation",
            "source": changed_path,
            "target": related_path,
            "relatedPaths": [changed_path, related_path],
        }
        relations.append(relation)
        rendered = json.dumps({
            "kind": "proposed_tree_relation_briefing",
            "focusPaths": [changed_path],
            "relations": [relation],
            "nodes": [],
        }, separators=(",", ":"))
        prompts.append({"stage": "stage_1", "renderedPrompt": rendered})
        diagnostics.append({
            "batchPaths": [changed_path],
            "totalPromptChars": len(rendered),
            "structuralContextChars": len(rendered),
        })
    artifact = {
        # Prompt capture order follows parallel completion order; diagnostic
        # order is independently normalized by batch identity.
        "prompts": list(reversed(prompts)),
        "promptAssemblyDiagnostics": {"stage1": diagnostics},
    }

    report = audit_expected_context(
        artifact,
        trusted_relation_records=_trusted_relation_records({
            "evidence": {"relations": relations, "nodes": []},
        }),
    )

    assert report["status"] == "passed"
    require_expected_context(report)


def test_queue_review_passes_source_bearing_payload_over_redis_stdin(
    monkeypatch,
    tmp_path,
):
    repository = build_synthetic_repository(tmp_path)
    request = build_review_request(
        repository,
        project_namespace="neutral-mixed-test",
        dry_run_id="neutral-mixed-test-run",
    ).model_copy(update={
        "ragCollectionTarget": "cc_http_g_exact",
        "ragBaseGenerationManifestSha256": "b" * 64,
    })
    redis_calls = []

    def fake_redis(container, *arguments, input_text=None):
        redis_calls.append((container, arguments, input_text))
        return "1"

    monkeypatch.setattr(
        "tools.review_quality.isolated_deployed_replay._redis",
        fake_redis,
    )
    monkeypatch.setattr(
        "tools.review_quality.isolated_deployed_replay._wait_for_job",
        lambda *_args: ([], {"type": "final", "result": {}}),
    )

    _queue_review(
        "isolated-redis",
        request,
        "isolated-job",
        timeout=1,
    )

    enqueue = redis_calls[-1]
    assert enqueue[0] == "isolated-redis"
    assert enqueue[1] == ("-x", "LPUSH", "codecrow:analysis:jobs")
    assert enqueue[2] is not None
    payload = json.loads(enqueue[2])
    assert payload["job_id"] == "isolated-job"
    assert payload["request"]["promptDryRun"] is True
    assert payload["request"]["ragCollectionTarget"] == "cc_http_g_exact"
    assert payload["request"]["ragBaseGenerationManifestSha256"] == "b" * 64


def test_captured_java_request_map_is_not_reserialized(tmp_path):
    repository = build_synthetic_repository(tmp_path)
    request = build_review_request(
        repository,
        project_namespace="neutral-mixed-test",
        dry_run_id="replay-run",
    )
    captured = request.model_dump(mode="json", by_alias=False)
    captured["targetBranchName"] = captured.pop("targetBranchName")
    captured["futureJavaOwnedField"] = {"kept": True}
    captured["promptDryRunId"] = "java-capture-id"

    queued = _queue_request_payload(
        request,
        captured_request_payload=captured,
    )

    assert queued["targetBranchName"] == "main"
    assert "branch" not in queued
    assert queued["futureJavaOwnedField"] == {"kept": True}
    assert queued["promptDryRunId"] == "replay-run"


def test_review_generation_receipt_and_discovery_path_are_exact():
    receipt = _review_generation_receipt({
        "status": "ready",
        "snapshot": {
            "kind": "proposed_tree",
            "branch": "main",
            "revision": "source-head",
            "baseRevision": "base-head",
            "sourceRevision": "source-head",
            "baseCollectionTarget": "cc_base_exact",
            "baseGenerationManifestSha256": "b" * 64,
            "generationManifestSha256": "c" * 64,
        },
        "freshness": {
            "state": "exact_proposed_tree",
            "baseRevision": "base-head",
            "sourceRevision": "source-head",
        },
        "provenance": {"collectionTarget": "cc_review_exact"},
    },
        expected_branch="main",
        expected_base_revision="base-head",
        expected_source_revision="source-head",
        expected_base_collection_target="cc_base_exact",
        expected_base_generation_manifest_sha256="b" * 64,
    )

    assert receipt == {
        "branch": "main",
        "repository_revision": "source-head",
        "collection_target": "cc_review_exact",
        "generation_manifest_sha256": "c" * 64,
    }
    assert _structural_generation_discovery_path(
        workspace="isolated workspace",
        project="neutral/project",
        branch="feature/review",
    ) == (
        "/index/isolated%20workspace/neutral%2Fproject/revisions"
        "?branch=feature%2Freview"
    )


def test_review_generation_receipt_rejects_wrong_snapshot_identity():
    with pytest.raises(RuntimeError, match="wrong exact proposed-tree identity"):
        _review_generation_receipt({
            "status": "ready",
            "snapshot": {
                "kind": "proposed_tree",
                "branch": "other-branch",
                "revision": "wrong-head",
                "baseRevision": "base-head",
                "sourceRevision": "wrong-head",
                "baseCollectionTarget": "cc_base_exact",
                "baseGenerationManifestSha256": "b" * 64,
                "generationManifestSha256": "c" * 64,
            },
            "freshness": {
                "state": "exact_proposed_tree",
                "baseRevision": "base-head",
                "sourceRevision": "wrong-head",
            },
            "provenance": {"collectionTarget": "cc_review_exact"},
        },
            expected_branch="main",
            expected_base_revision="base-head",
            expected_source_revision="source-head",
            expected_base_collection_target="cc_base_exact",
            expected_base_generation_manifest_sha256="b" * 64,
        )


def test_wrong_review_identity_still_yields_exact_cleanup_receipt():
    response = {
        "status": "ready",
        "snapshot": {
            "kind": "proposed_tree",
            "branch": "wrong-branch",
            "revision": "wrong-created-revision",
            "baseRevision": "base-head",
            "sourceRevision": "source-head",
            "baseCollectionTarget": "cc_base_exact",
            "baseGenerationManifestSha256": "b" * 64,
            "generationManifestSha256": "c" * 64,
        },
        "freshness": {
            "state": "exact_proposed_tree",
            "baseRevision": "base-head",
            "sourceRevision": "source-head",
        },
        "provenance": {"collectionTarget": "cc_review_exact"},
    }

    cleanup_receipt = _review_generation_cleanup_receipt(response)

    assert cleanup_receipt == {
        "branch": "wrong-branch",
        "repository_revision": "wrong-created-revision",
        "collection_target": "cc_review_exact",
        "generation_manifest_sha256": "c" * 64,
    }
    with pytest.raises(RuntimeError, match="wrong exact proposed-tree identity"):
        _review_generation_receipt(
            response,
            expected_branch="main",
            expected_base_revision="base-head",
            expected_source_revision="source-head",
            expected_base_collection_target="cc_base_exact",
            expected_base_generation_manifest_sha256="b" * 64,
        )


def test_generated_artifact_directory_mode_is_not_affected_by_host_umask(
    tmp_path,
):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    artifacts.chmod(0o777)

    assert stat.S_IMODE(artifacts.stat().st_mode) == 0o777


def test_artifact_copy_rejects_unsafe_filename_before_docker_call(
    monkeypatch,
    tmp_path,
):
    called = False

    def fake_run(*_args, **_kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(
        "tools.review_quality.isolated_deployed_replay._run",
        fake_run,
    )

    with pytest.raises(RuntimeError, match="unsafe"):
        _copy_artifact_for_audit(
            container_name="isolated",
            filename="../capture.json",
            destination=tmp_path / "capture.json",
        )

    assert called is False


def test_structural_cleanup_paths_are_bound_to_exact_generation():
    branch_path = _structural_generation_cleanup_paths(
        workspace="codecrow-quality-isolated",
        project="neutral/mixed-test",
        branch="feature/test",
        revision="a" * 40,
        index_result={
            "collection_target": "cc_http_g_exact",
            "generation_manifest_sha256": "b" * 64,
        },
    )

    branch_url = urlsplit(branch_path)
    assert branch_url.path == (
        "/index/codecrow-quality-isolated/neutral%2Fmixed-test/"
        "branch/feature%2Ftest"
    )
    assert parse_qs(branch_url.query) == {
        "collection_target": ["cc_http_g_exact"],
        "generation_revision": ["a" * 40],
        "generation_manifest_sha256": ["b" * 64],
    }


def test_structural_cleanup_paths_require_a_sealed_generation_receipt():
    with pytest.raises(RuntimeError, match="exact generation receipt"):
        _structural_generation_cleanup_paths(
            workspace="workspace",
            project="project",
            branch="main",
            revision="a" * 40,
            index_result={"document_count": 3},
        )


@pytest.mark.parametrize(
    "identity",
    [
        {"projectId": 352},
        {"project_id": 1802},
        {"projectNamespace": "ways"},
        {"repositoryPath": "/secure/corpus/hofmanflowers"},
    ],
)
def test_isolated_replay_rejects_connected_project_identity(identity):
    with pytest.raises(RuntimeError, match="connected repository identity"):
        _assert_no_connected_identity(identity)


def test_isolated_replay_does_not_treat_ordinary_ways_text_as_identity():
    _assert_no_connected_identity({
        "projectNamespace": "neutral-corpus",
        "rawDiff": "There are several ways to implement this.",
    })


def test_isolated_state_lock_rejects_concurrent_redis_db_15_owner(tmp_path):
    lock = tmp_path / "isolated-state.lock"

    with _exclusive_isolated_state_lock(lock):
        with pytest.raises(RuntimeError, match="owns Redis DB 15"):
            with _exclusive_isolated_state_lock(lock):
                pass

    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
