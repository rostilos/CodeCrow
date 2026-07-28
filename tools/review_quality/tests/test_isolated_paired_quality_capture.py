from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from tools.review_quality.isolated_deployed_replay import (
    build_synthetic_repository,
    build_review_request,
)
from tools.review_quality.isolated_paired_quality_capture import (
    SPEND_ACKNOWLEDGEMENT,
    ReviewProviderConfig,
    _apply_review_provider,
    _container_json_request,
    _start_rag_container,
    load_local_capture_case,
    load_review_provider_config,
    require_spend_authorization,
)


def _provider_config(path, **updates):
    payload = {
        "provider": "OPENROUTER",
        "model": "review-model",
        "apiKey": "review-secret",
        "baseUrl": "https://openrouter.ai/api/v1",
        "customParameters": {"temperature": 0},
        "maxAllowedTokens": 20000,
        "useMcpTools": False,
    }
    payload.update(updates)
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_review_provider_config_requires_owner_only_file_and_forbids_tools(
    tmp_path,
):
    path = _provider_config(tmp_path / "review.json")
    config = load_review_provider_config(path)

    assert config.provider == "OPENROUTER"
    assert config.model == "review-model"
    assert config.api_key == "review-secret"
    assert config.custom_parameters == {"temperature": 0}
    assert config.max_allowed_tokens == 20000

    path.chmod(0o640)
    with pytest.raises(ValueError, match="owner-only"):
        load_review_provider_config(path)

    _provider_config(path, useMcpTools=True)
    with pytest.raises(ValueError, match="forbids MCP"):
        load_review_provider_config(path)

    _provider_config(path, unexpected="typo")
    with pytest.raises(ValueError, match="unknown fields"):
        load_review_provider_config(path)

    _provider_config(path, maxAllowedTokens=None)
    with pytest.raises(ValueError, match="required"):
        load_review_provider_config(path)


def test_spend_interlock_is_required_only_for_provider_backed_run():
    require_spend_authorization(
        preflight_only=True,
        acknowledgement=None,
    )
    require_spend_authorization(
        preflight_only=False,
        acknowledgement=SPEND_ACKNOWLEDGEMENT,
    )

    with pytest.raises(ValueError, match="authorize-provider-spend"):
        require_spend_authorization(
            preflight_only=False,
            acknowledgement=None,
        )


def test_loads_remote_free_local_immutable_case_with_hard_size_limits(tmp_path):
    source = build_synthetic_repository(tmp_path / "source")
    case_manifest = tmp_path / "case.json"
    case_manifest.write_text(json.dumps({
        "caseId": "neutral-polyglot",
        "repositoryPath": str(source.root),
        "baseCommit": source.base_revision,
        "headCommit": source.head_revision,
        "languages": ["java", "python", "typescript"],
        "frameworks": [],
        "candidatePlugins": ["java", "python", "typescript"],
    }), encoding="utf-8")

    case = load_local_capture_case(
        case_manifest,
        temporary_root=tmp_path / "loaded",
        maximum_files=10,
        maximum_changed_lines=100,
        maximum_repository_files=100,
        maximum_repository_bytes=100_000,
    )

    assert case.case_id == "neutral-polyglot"
    assert case.languages == ("java", "python", "typescript")
    assert case.candidate_plugins == ("java", "python", "typescript")
    assert case.repository.base_revision == source.base_revision
    assert case.repository.head_revision == source.head_revision
    assert case.repository.changed_files == source.changed_files
    assert case.changed_lines > 0
    assert (case.repository.base_tree / "shared/policy.py").is_file()

    with pytest.raises(ValueError, match="maximum is 1"):
        load_local_capture_case(
            case_manifest,
            temporary_root=tmp_path / "too-large",
            maximum_files=1,
            maximum_changed_lines=100,
            maximum_repository_files=100,
            maximum_repository_bytes=100_000,
        )

    with pytest.raises(ValueError, match="tracked files; maximum is 1"):
        load_local_capture_case(
            case_manifest,
            temporary_root=tmp_path / "repository-files-too-large",
            maximum_files=10,
            maximum_changed_lines=100,
            maximum_repository_files=1,
            maximum_repository_bytes=100_000,
        )

    with pytest.raises(ValueError, match="tracked bytes; maximum is 1"):
        load_local_capture_case(
            case_manifest,
            temporary_root=tmp_path / "repository-bytes-too-large",
            maximum_files=10,
            maximum_changed_lines=100,
            maximum_repository_files=100,
            maximum_repository_bytes=1,
        )

    payload = json.loads(case_manifest.read_text(encoding="utf-8"))
    payload["projectId"] = 352
    case_manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown fields: projectId"):
        load_local_capture_case(
            case_manifest,
            temporary_root=tmp_path / "unknown-field",
            maximum_files=10,
            maximum_changed_lines=100,
            maximum_repository_files=100,
            maximum_repository_bytes=100_000,
        )
    payload.pop("projectId")
    case_manifest.write_text(json.dumps(payload), encoding="utf-8")

    subprocess.run(
        [
            "git",
            "-C",
            str(source.root),
            "remote",
            "add",
            "origin",
            "/tmp/non-network-remote",
        ],
        check=True,
    )
    with pytest.raises(ValueError, match="remote-free"):
        load_local_capture_case(
            case_manifest,
            temporary_root=tmp_path / "remote",
            maximum_files=10,
            maximum_changed_lines=100,
            maximum_repository_files=100,
            maximum_repository_bytes=100_000,
        )


def test_applies_same_explicit_byok_settings_without_vcs_or_agent_credentials(
    tmp_path,
):
    repository = build_synthetic_repository(tmp_path)
    request = build_review_request(
        repository,
        project_namespace="neutral-pair",
        dry_run_id="preflight",
    )
    config = ReviewProviderConfig(
        provider="OPENROUTER",
        model="review-model",
        api_key="review-secret",
        base_url="https://openrouter.ai/api/v1",
        custom_parameters={"temperature": 0},
        max_allowed_tokens=20000,
    )

    paid = _apply_review_provider(request, config, case_id="neutral-pair")

    assert paid.promptDryRun is False
    assert paid.promptDryRunId is None
    assert paid.aiProvider == "OPENROUTER"
    assert paid.aiModel == "review-model"
    assert paid.aiApiKey == "review-secret"
    assert paid.maxAllowedTokens == 20000
    assert paid.useMcpTools is False
    assert paid.accessToken is None
    assert paid.oAuthClient is None
    assert paid.oAuthSecret is None


def test_rag_fallback_command_uses_empty_plugin_root_and_no_web_config_poll(
    monkeypatch,
    tmp_path,
):
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(tuple(command))
        return SimpleNamespace(stdout="container-id\n", stderr="", returncode=0)

    monkeypatch.setattr(
        "tools.review_quality.isolated_paired_quality_capture._run",
        fake_run,
    )
    monkeypatch.setattr(
        "tools.review_quality.isolated_paired_quality_capture._wait_for_rag",
        lambda *_args, **_kwargs: None,
    )
    env_file = tmp_path / "rag.env"
    env_file.write_text("EMBEDDING_PROVIDER=openrouter\n", encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()

    _start_rag_container(
        container_name="isolated-rag",
        image="rag:test",
        network="isolated-network",
        rag_env_file=env_file,
        service_secret="service-secret",
        empty_plugins=empty,
    )

    command = commands[0]
    assert "CODECROW_PLUGINS_ROOT=/app/empty-plugins" in command
    assert "CODECROW_WEB_SERVER_URL=" in command
    assert "REDIS_URL=redis://redis:6379/15" in command
    assert "rag:test" == command[-1]


def test_container_rag_request_keeps_payload_out_of_process_arguments(
    monkeypatch,
):
    observed = {}

    def fake_run(command, *, input_text=None, **_kwargs):
        observed["command"] = tuple(command)
        observed["input"] = input_text
        return SimpleNamespace(
            stdout='{"document_count": 2}\n',
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(
        "tools.review_quality.isolated_paired_quality_capture._run",
        fake_run,
    )
    result = _container_json_request(
        "isolated-rag",
        method="POST",
        path="/index/repository",
        payload={"private": "source-bearing-value"},
        timeout=30,
    )

    assert result == {"document_count": 2}
    assert "source-bearing-value" not in " ".join(observed["command"])
    assert json.loads(observed["input"])["payload"] == {
        "private": "source-bearing-value",
    }
