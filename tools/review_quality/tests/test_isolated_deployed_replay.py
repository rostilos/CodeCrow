from __future__ import annotations

import json
import stat
from types import SimpleNamespace

import pytest

from tools.review_quality.isolated_deployed_replay import (
    DUMMY_REVIEW_KEY,
    EXPECTED_RELATED_PATHS,
    HEAD_REPLACEMENTS,
    ISOLATED_PROJECT_ID,
    audit_expected_context,
    build_review_request,
    build_synthetic_repository,
    _copy_artifact_for_audit,
    _assert_no_connected_identity,
    _exclusive_isolated_state_lock,
    _qdrant_collections_for_project,
    _queue_review,
)


def test_synthetic_repository_is_remote_free_and_immutable(tmp_path):
    repository = build_synthetic_repository(tmp_path)

    assert len(repository.base_revision) == 40
    assert len(repository.head_revision) == 40
    assert repository.base_revision != repository.head_revision
    assert repository.changed_files == tuple(sorted(HEAD_REPLACEMENTS))
    assert repository.raw_diff.count("diff --git ") == len(HEAD_REPLACEMENTS)

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


def test_expected_context_audit_requires_one_owner_and_related_path():
    changed_paths = list(EXPECTED_RELATED_PATHS)
    artifact = {
        "prompts": [
            {
                "stage": "stage_1",
                "renderedPrompt": "\n".join(
                    EXPECTED_RELATED_PATHS.values()
                ),
            },
        ],
        "promptAssemblyDiagnostics": {
            "stage1": [{
                "batchPaths": changed_paths,
                "ragChars": 500,
            }],
        },
    }

    report = audit_expected_context(artifact)

    assert report["status"] == "passed"
    assert report["failedPaths"] == []
    assert set(report["paths"]) == set(changed_paths)
    assert DUMMY_REVIEW_KEY not in json.dumps(report)


def test_expected_context_audit_fails_closed_on_missing_related_path():
    changed_path = next(iter(EXPECTED_RELATED_PATHS))
    artifact = {
        "prompts": [{
            "stage": "stage_1",
            "renderedPrompt": "no indexed relation",
        }],
        "promptAssemblyDiagnostics": {
            "stage1": [{
                "batchPaths": [changed_path],
                "ragChars": 10,
            }],
        },
    }

    report = audit_expected_context(artifact)

    assert report["status"] == "failed"
    assert changed_path in report["failedPaths"]


def test_queue_review_passes_source_bearing_payload_over_redis_stdin(
    monkeypatch,
    tmp_path,
):
    repository = build_synthetic_repository(tmp_path)
    request = build_review_request(
        repository,
        project_namespace="neutral-mixed-test",
        dry_run_id="neutral-mixed-test-run",
    )
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


def test_qdrant_cleanup_inspection_returns_only_matching_project(
    monkeypatch,
):
    monkeypatch.setattr(
        "tools.review_quality.isolated_deployed_replay._run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout='["codecrow_codecrow-quality-isolated__neutral-mixed-test"]',
            stderr="",
            returncode=0,
        ),
    )

    assert _qdrant_collections_for_project(
        "rag",
        workspace="codecrow-quality-isolated",
        project="neutral-mixed-test",
    ) == [
        "codecrow_codecrow-quality-isolated__neutral-mixed-test",
    ]


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
