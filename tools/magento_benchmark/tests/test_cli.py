from __future__ import annotations

from magento2_benchmark.cli import _dispatch, _parser, main
from magento2_benchmark.config import DEFAULTS, apply_model_overrides


def test_execution_corpus_cli_forwards_custodian_inputs(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "magento2_benchmark.cli.create_execution_corpus",
        lambda **kwargs: captured.update(kwargs) or {"kind": "execution"},
    )
    args = _parser().parse_args(
        [
            "execution-corpus",
            "--corpus",
            "released-corpus.json",
            "--output",
            "analysis-execution-corpus.json",
        ]
    )

    result = _dispatch(args, {"github": {}})

    assert result == {"kind": "execution"}
    assert captured["corpus_path"].name == "released-corpus.json"
    assert captured["output"].name == "analysis-execution-corpus.json"


def test_verify_current_comments_cli_uses_explicit_offline_cache(
    monkeypatch,
):
    captured = {}
    client = object()
    monkeypatch.setattr(
        "magento2_benchmark.cli._github_client",
        lambda *_args, **kwargs: (
            captured.update({"offline": kwargs["offline"]}) or client
        ),
    )
    monkeypatch.setattr(
        "magento2_benchmark.cli.attest_current_comments",
        lambda received_client, **kwargs: (
            captured.update({"client": received_client, **kwargs})
            or {
                "kind": (
                    "codecrow-magento2-current-review-comment-attestation"
                ),
                "sourceMode": "cache-only",
                "currentSelectedCommentsVerified": True,
                "caseCount": 50,
                "selectedRootCount": 121,
                "completeRestReplyCount": 67,
                "paperReady": False,
                "scoringEnabled": False,
                "attestationDigest": "a" * 64,
            }
        ),
    )
    args = _parser().parse_args(
        [
            "verify-current-comments",
            "--draft",
            "draft.json",
            "--output",
            "attestation.json",
            "--offline",
        ]
    )

    result = _dispatch(
        args,
        {"github": {"repository": "magento/magento2"}},
    )

    assert result["currentSelectedCommentsVerified"] is True
    assert result["caseCount"] == 50
    assert result["selectedRootCount"] == 121
    assert result["completeRestReplyCount"] == 67
    assert result["sourceMode"] == "cache-only"
    assert result["output"].endswith("attestation.json")
    assert captured["offline"] is True
    assert captured["client"] is client
    assert captured["repository"] == "magento/magento2"


def test_link_discovery_selection_cli_binds_explicit_inputs(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "magento2_benchmark.cli.link_discovery_selection",
        lambda **kwargs: captured.update(kwargs) or {"kind": "linkage"},
    )
    args = _parser().parse_args(
        [
            "link-discovery-selection",
            "--discovery",
            "discovery.json",
            "--selection",
            "selection.json",
            "--output",
            "linkage.json",
        ]
    )

    result = _dispatch(args, {"github": {}})

    assert result == {"kind": "linkage"}
    assert captured["discovery_path"].name == "discovery.json"
    assert captured["selection_path"].name == "selection.json"
    assert captured["output"].name == "linkage.json"


def test_operator_preflight_cli_forwards_read_only_inputs(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "magento2_benchmark.cli.operator_preflight",
        lambda **kwargs: captured.update(kwargs)
        or {"kind": "preflight", "runReady": True, "paperReady": False},
    )
    args = _parser().parse_args(
        [
            "--config",
            "config.toml",
            "operator-preflight",
            "--execution-corpus",
            "execution-corpus.json",
            "--replay-lock",
            "replay-lock.json",
            "--replay-attestation",
            "replay-attestation.json",
            "--repository-path",
            "magento2",
            "--output",
            "preflight.json",
        ]
    )

    result = _dispatch(args, {"github": {}})

    assert result["runReady"] is True
    assert result["paperReady"] is False
    assert captured["config_path"].name == "config.toml"
    assert captured["execution_corpus_path"].name == "execution-corpus.json"
    assert captured["replay_lock_path"].name == "replay-lock.json"
    assert captured["replay_attestation_path"].name == (
        "replay-attestation.json"
    )
    assert captured["repository"].name == "magento2"
    assert captured["output"].name == "preflight.json"


def test_operator_preflight_cli_exits_nonzero_when_run_is_not_ready(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        "magento2_benchmark.cli.load_config",
        lambda _path: {"github": {}},
    )
    monkeypatch.setattr(
        "magento2_benchmark.cli._dispatch",
        lambda _args, _config: {
            "kind": "preflight",
            "runReady": False,
            "paperReady": False,
        },
    )

    exit_code = main(
        [
            "operator-preflight",
            "--execution-corpus",
            "execution-corpus.json",
            "--replay-lock",
            "replay-lock.json",
            "--repository-path",
            "magento2",
            "--output",
            "preflight.json",
        ]
    )

    assert exit_code == 2
    assert '"runReady": false' in capsys.readouterr().out


def test_run_cli_forwards_requested_and_expected_analysis_models(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "magento2_benchmark.cli.run_analysis",
        lambda **kwargs: captured.update(kwargs) or {"status": "completed"},
    )
    args = _parser().parse_args(
        [
            "run",
            "--execution-corpus",
            "execution-corpus.json",
            "--replay-lock",
            "replay-lock.json",
            "--repository-path",
            "magento2",
            "--output-dir",
            "run",
            "--run-id",
            "study-2026:model-a",
            "--analysis-model",
            "requested-alias",
            "--expected-analysis-response-model",
            "provider-resolved-model",
        ]
    )

    _dispatch(args, {"github": {}})

    assert captured["model"] == "requested-alias"
    assert captured["expected_response_model"] == "provider-resolved-model"
    assert captured["run_id"] == "study-2026:model-a"
    assert captured["execution_corpus_path"].name == "execution-corpus.json"


def test_analysis_model_override_defaults_expected_model_to_same_alias():
    config = {
        "analysis": dict(DEFAULTS["analysis"]),
        "judge": dict(DEFAULTS["judge"]),
    }

    apply_model_overrides(config, analysis_model="requested-alias")

    assert config["analysis"]["model"] == "requested-alias"
    assert config["analysis"]["expected_response_model"] == "requested-alias"


def test_judge_cli_forwards_preregistered_judgment_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "magento2_benchmark.cli.judge_run",
        lambda **kwargs: captured.update(kwargs) or {"status": "completed"},
    )
    args = _parser().parse_args(
        [
            "judge",
            "--corpus",
            "corpus.json",
            "--run",
            "run.json",
            "--repository-path",
            "magento2",
            "--output-dir",
            "judgment",
            "--judgment-id",
            "study-2026:judge-a",
        ]
    )

    _dispatch(args, {"github": {}})

    assert captured["judgment_id"] == "study-2026:judge-a"
