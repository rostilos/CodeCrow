from __future__ import annotations

import json
import subprocess

import pytest

from magento2_benchmark.judge import (
    OpenAICompatibleJudge,
    _gold_prompt,
    _majority_match,
    _majority_novel,
    _maximum_assignment,
    _path_evidence,
    _right_added_lines,
    _resolved_judge_config,
    _validate_match_response,
    _validate_novel,
    _validated_judge_call,
)

from conftest import make_git_pair


def test_candidate_source_evidence_ignores_replace_refs_and_git_environment(
    monkeypatch,
    tmp_path,
):
    repository = tmp_path / "repository"
    base, head = make_git_pair(repository)
    blob = subprocess.run(
        ["git", "-C", str(repository), "hash-object", "-w", "--stdin"],
        input="<?php\nreturn 9;\n",
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
            "A.php",
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
        ["git", "-C", str(repository), "replace", head, replacement],
        check=True,
    )
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "hostile-git-dir"))

    path_diff, head_source = _path_evidence(
        repository,
        base,
        head,
        "A.php",
        {"A.php"},
    )

    assert "+return 2;" in path_diff
    assert "return 9;" not in path_diff
    assert "return 2;" in head_source
    assert "return 9;" not in head_source


def test_judge_model_override_updates_expected_model_unless_explicit():
    config = {
        "judge": {
            "model": "old/request",
            "expected_response_model": "old/resolved",
        }
    }

    automatic, automatic_expected = _resolved_judge_config(
        config,
        model="new/request",
        expected_response_model=None,
    )
    assert automatic["model"] == "new/request"
    assert automatic["expected_response_model"] == "new/request"
    assert automatic_expected == "new/request"

    explicit, explicit_expected = _resolved_judge_config(
        config,
        model="new/request",
        expected_response_model="new/provider-resolved",
    )
    assert explicit["model"] == "new/request"
    assert explicit["expected_response_model"] == "new/provider-resolved"
    assert explicit_expected == "new/provider-resolved"

    fallback, fallback_expected = _resolved_judge_config(
        {
            "judge": {
                "model": "configured/request",
                "expected_response_model": "",
            }
        },
        model=None,
        expected_response_model=None,
    )
    assert fallback["expected_response_model"] == "configured/request"
    assert fallback_expected == "configured/request"


def match_judgment(
    *,
    gold: str,
    candidate: str,
    verdict: str = "substantive_match",
    confidence: float = 0.8,
    location: str = "same_symbol",
    root: str = "yes",
    grounded: str = "yes",
):
    return {
        "goldId": gold,
        "candidateId": candidate,
        "candidate_id": candidate,
        "specific_issue": "yes",
        "grounded_at_snapshot": grounded,
        "same_root_cause": root,
        "same_failure_or_consequence": "yes",
        "compatible_required_change": "yes",
        "location_relation": location,
        "verdict": verdict,
        "confidence": confidence,
        "repeatAgreement": 1.0,
        "rationale": "Fixture rationale.",
    }


def response_item(candidate_id: str, **overrides):
    value = {
        "candidate_id": candidate_id,
        "specific_issue": "yes",
        "grounded_at_snapshot": "yes",
        "same_root_cause": "yes",
        "same_failure_or_consequence": "yes",
        "compatible_required_change": "yes",
        "location_relation": "same_symbol",
        "verdict": "substantive_match",
        "confidence": 0.9,
        "rationale": "Same defect.",
    }
    value.update(overrides)
    return value


def test_match_response_must_cover_every_candidate_exactly_once():
    valid = {
        "gold_id": "G001",
        "judgments": [response_item("C002"), response_item("C001")],
    }
    normalized = _validate_match_response(
        valid,
        gold_label="G001",
        candidate_count=2,
    )
    assert [item["candidate_id"] for item in normalized] == ["C001", "C002"]

    duplicate = {
        "gold_id": "G001",
        "judgments": [response_item("C001"), response_item("C001")],
    }
    with pytest.raises(ValueError, match="every candidate ID exactly once"):
        _validate_match_response(
            duplicate,
            gold_label="G001",
            candidate_count=2,
        )

    invalid_confidence = {
        "gold_id": "G001",
        "judgments": [response_item("C001", confidence=True)],
    }
    with pytest.raises(ValueError, match="confidence"):
        _validate_match_response(
            invalid_confidence,
            gold_label="G001",
            candidate_count=1,
        )


def test_match_prompt_lists_the_exact_supported_verdicts():
    prompt = _gold_prompt(
        gold_label="G001",
        gold={
            "path": "Model.php",
            "originalLine": 2,
            "body": "Guard the nullable value.",
            "expectedIssue": {
                "summary": "Missing null guard",
                "rootCause": "Nullable value is dereferenced",
                "failureMode": "Runtime exception",
                "requiredChange": "Add a guard",
            },
            "diffHunk": "@@ -1 +1,2 @@\n line\n+problem();",
        },
        findings=[],
        candidate_evidence=[],
    )

    assert (
        "substantive_match|partial|related_distinct|no_match|unverifiable"
        in prompt
    )


def test_match_prompt_compacts_evidence_fairly_without_dropping_candidates():
    findings = [
        {
            "path": f"Model/{index:02d}.php",
            "line": index,
            "title": "Candidate " + ("T" * 2_000),
            "description": "D" * 4_000,
            "category": "bug",
            "severity": "medium",
            "suggestedFix": "F" * 2_000,
        }
        for index in range(1, 51)
    ]
    evidence = [
        {
            "inFrozenDiff": True,
            "lineOnAddedRightSide": True,
            "pathDiff": "P" * 12_000,
            "headSourceWindow": "S" * 8_000,
            "pathDiffSha256": "a" * 64,
            "headSourceSha256": "b" * 64,
        }
        for _ in findings
    ]

    prompt = _gold_prompt(
        gold_label="G001",
        gold={
            "path": "Model.php",
            "originalLine": 2,
            "body": "Guard the nullable value.",
            "expectedIssue": {
                "summary": "Missing null guard",
                "rootCause": "Nullable value is dereferenced",
                "failureMode": "Runtime exception",
                "requiredChange": "Add a guard",
            },
            "diffHunk": "@@ -1 +1,2 @@\n line\n+problem();",
        },
        findings=findings,
        candidate_evidence=evidence,
        max_prompt_characters=400_000,
    )

    assert len(prompt) <= 400_000
    input_text = prompt.removeprefix("INPUT:\n").split(
        "\n\nOUTPUT SCHEMA:\n", 1
    )[0]
    value = json.loads(input_text)
    assert [item["candidate_id"] for item in value["candidates"]] == [
        f"C{index:03d}" for index in range(1, 51)
    ]
    compaction = value["evidence_compaction"]
    assert compaction["policy"] == "uniform-candidate-text-prefix-v1"
    assert compaction["candidateCountPreserved"] == 50
    assert compaction["uniformCandidateTextFieldCharacters"] > 0
    assert all(
        "[truncated " in item["frozen_evidence"]["pathDiff"]
        for item in value["candidates"]
    )


def test_match_prompt_fails_if_complete_candidate_set_cannot_fit():
    findings = [
        {
            "path": f"Model/{index:03d}.php",
            "line": index,
            "title": "Issue",
            "description": "Description",
            "category": "bug",
            "severity": "medium",
            "suggestedFix": "Fix",
        }
        for index in range(1, 101)
    ]
    evidence = [
        {
            "inFrozenDiff": True,
            "lineOnAddedRightSide": True,
            "pathDiff": "diff",
            "headSourceWindow": "source",
            "pathDiffSha256": "a" * 64,
            "headSourceSha256": "b" * 64,
        }
        for _ in findings
    ]

    with pytest.raises(ValueError, match="preserve every candidate"):
        _gold_prompt(
            gold_label="G001",
            gold={
                "path": "Model.php",
                "originalLine": 2,
                "body": "Guard the nullable value.",
                "expectedIssue": {
                    "summary": "Missing null guard",
                    "rootCause": "Nullable value is dereferenced",
                    "failureMode": "Runtime exception",
                    "requiredChange": "Add a guard",
                },
                "diffHunk": "@@ -1 +1,2 @@\n line\n+problem();",
            },
            findings=findings,
            candidate_evidence=evidence,
            max_prompt_characters=10_000,
        )


def test_right_added_lines_parses_unified_diff_coordinates():
    diff = (
        "diff --git a/Model.php b/Model.php\n"
        "--- a/Model.php\n"
        "+++ b/Model.php\n"
        "@@ -10,2 +10,4 @@\n"
        " context\n"
        "+added_one();\n"
        "-removed();\n"
        "+added_two();\n"
        " tail\n"
    )

    assert _right_added_lines(diff) == {11, 12}


def test_majority_uses_strict_majority_and_highest_confidence_evidence():
    substantive_low = response_item(
        "C001", verdict="substantive_match", confidence=0.6
    )
    substantive_high = response_item(
        "C001", verdict="substantive_match", confidence=0.95
    )
    no_match = response_item("C001", verdict="no_match", confidence=0.99)

    result = _majority_match(
        [substantive_low, no_match, substantive_high]
    )

    assert result["verdict"] == "substantive_match"
    assert result["confidence"] == 0.95
    assert result["repeatAgreement"] == pytest.approx(2 / 3)
    assert result["repeatVerdicts"] == {
        "no_match": 1,
        "substantive_match": 2,
    }

    tied = _majority_match(
        [
            response_item("C001", verdict="substantive_match"),
            response_item("C001", verdict="no_match"),
        ]
    )
    assert tied["verdict"] == "unverifiable"


def test_one_to_one_assignment_rematches_to_maximize_cardinality():
    judgments = [
        match_judgment(
            gold="G001",
            candidate="C001",
            confidence=0.99,
            location="exact_line",
        ),
        match_judgment(
            gold="G001",
            candidate="C002",
            confidence=0.60,
            location="same_symbol",
        ),
        match_judgment(
            gold="G002",
            candidate="C001",
            confidence=0.70,
            location="same_symbol",
        ),
    ]

    result = _maximum_assignment(2, 2, judgments)

    assert {
        (item["goldId"], item["candidateId"]) for item in result
    } == {("G001", "C002"), ("G002", "C001")}
    assert len({item["goldId"] for item in result}) == 2
    assert len({item["candidateId"] for item in result}) == 2


def test_assignment_ignores_non_substantive_or_ungrounded_edges():
    judgments = [
        match_judgment(
            gold="G001",
            candidate="C001",
            verdict="partial",
        ),
        match_judgment(
            gold="G001",
            candidate="C002",
            grounded="no",
        ),
        match_judgment(
            gold="G002",
            candidate="C001",
            root="no",
        ),
    ]
    assert _maximum_assignment(2, 2, judgments) == []


def test_novel_response_and_majority_are_strictly_validated():
    valid = {
        "candidate_id": "C001",
        "verdict": "valid_in_scope_novel",
        "grounded_at_snapshot": "yes",
        "actionable": "yes",
        "confidence": 0.8,
        "rationale": "Grounded in the changed branch.",
    }
    assert _validate_novel(valid, "C001")["verdict"] == "valid_in_scope_novel"

    invalid = dict(valid, confidence=1.5)
    with pytest.raises(ValueError, match="confidence"):
        _validate_novel(invalid, "C001")

    invalid_novel = dict(valid, grounded_at_snapshot="no")
    with pytest.raises(ValueError, match="grounded and actionable"):
        _validate_novel(invalid_novel, "C001")

    result = _majority_novel(
        [
            valid,
            dict(valid, verdict="invalid", confidence=0.99),
            dict(valid, confidence=0.9),
        ]
    )
    assert result["verdict"] == "valid_in_scope_novel"
    assert result["confidence"] == 0.9


def test_judge_custom_parameters_cannot_override_prompt_contract(
    monkeypatch,
):
    monkeypatch.setenv("BENCHMARK_JUDGE_KEY", "secret")
    config = {
        "api_key_env": "BENCHMARK_JUDGE_KEY",
        "base_url": "https://example.invalid/v1",
        "model": "fixture-model",
        "custom_parameters": {"messages": [{"role": "user", "content": "leak"}]},
    }
    with pytest.raises(ValueError, match="reserved request fields: messages"):
        OpenAICompatibleJudge(config)


def test_openai_compatible_judge_archives_exact_request_and_response(
    monkeypatch,
):
    monkeypatch.setenv("BENCHMARK_JUDGE_KEY", "secret")
    provider_response = {
        "id": "response-123",
        "model": "provider-model-immutable",
        "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        "choices": [
            {
                "message": {
                    "content": json.dumps({"valid": True}),
                }
            }
        ],
    }

    class FixtureResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(provider_response).encode("utf-8")

    def urlopen(request, timeout):
        assert request.get_header("Authorization") == "Bearer secret"
        assert timeout == 15
        return FixtureResponse()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = OpenAICompatibleJudge(
        {
            "api_key_env": "BENCHMARK_JUDGE_KEY",
            "base_url": "https://example.invalid/v1",
            "model": "fixture-model",
            "temperature": 0,
            "timeout_seconds": 15,
            "custom_parameters": {
                "seed": 42,
                "session_token": "request-secret",
            },
        }
    )

    value, metadata = client.call(system="system", user="prompt")

    assert value == {"valid": True}
    assert metadata["request"]["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "prompt"},
    ]
    assert metadata["request"]["seed"] == 42
    assert metadata["request"]["session_token"] == "<redacted>"
    assert metadata["providerResponse"] == provider_response
    assert metadata["responseId"] == "response-123"
    assert metadata["model"] == "provider-model-immutable"


def test_openai_compatible_judge_refuses_credential_echo(
    monkeypatch,
):
    monkeypatch.setenv("BENCHMARK_JUDGE_KEY", "provider-secret")
    provider_response = {
        "id": "response-credential-echo",
        "model": "fixture-model",
        "echo": "Bearer provider-secret",
        "choices": [
            {
                "message": {
                    "content": json.dumps({"valid": True}),
                }
            }
        ],
    }

    class FixtureResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(provider_response).encode("utf-8")

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: FixtureResponse(),
    )
    client = OpenAICompatibleJudge(
        {
            "api_key_env": "BENCHMARK_JUDGE_KEY",
            "base_url": "https://example.invalid/v1",
            "model": "fixture-model",
            "max_retries": 1,
        }
    )

    with pytest.raises(RuntimeError, match="refusing to persist"):
        client.call(system="system", user="prompt")


def test_openai_compatible_judge_rejects_provider_model_drift(
    monkeypatch,
):
    monkeypatch.setenv("BENCHMARK_JUDGE_KEY", "provider-secret")
    provider_response = {
        "id": "response-model-drift",
        "model": "provider/other-model",
        "choices": [
            {
                "message": {
                    "content": json.dumps({"valid": True}),
                }
            }
        ],
    }

    class FixtureResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(provider_response).encode("utf-8")

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: FixtureResponse(),
    )
    client = OpenAICompatibleJudge(
        {
            "api_key_env": "BENCHMARK_JUDGE_KEY",
            "base_url": "https://example.invalid/v1",
            "model": "provider/requested-model",
            "expected_response_model": "provider/expected-model",
            "max_retries": 1,
        }
    )

    with pytest.raises(RuntimeError, match="resolved model mismatch"):
        client.call(system="system", user="prompt")


def test_structured_validation_retries_and_reuses_atomic_checkpoint(tmp_path):
    class FixtureClient:
        def __init__(self):
            self.calls = 0

        def call(self, *, system, user):
            self.calls += 1
            value = {"valid": self.calls >= 2}
            return value, {"responseId": f"response-{self.calls}"}

    client = FixtureClient()
    checkpoint = tmp_path / "calls" / "call.json"

    def validate(value):
        if value != {"valid": True}:
            raise ValueError("invalid fixture response")
        return value

    normalized, record = _validated_judge_call(
        judge_client=client,
        system="system",
        prompt="prompt",
        validator=validate,
        checkpoint_path=checkpoint,
        binding={"caseId": "m2b-001"},
        max_structured_retries=3,
    )

    assert normalized == {"valid": True}
    assert client.calls == 2
    assert len(record["rejectedStructuredResponses"]) == 1
    assert checkpoint.is_file()

    resumed, resumed_record = _validated_judge_call(
        judge_client=client,
        system="system",
        prompt="prompt",
        validator=validate,
        checkpoint_path=checkpoint,
        binding={"caseId": "m2b-001"},
        max_structured_retries=3,
    )
    assert resumed == normalized
    assert resumed_record == record
    assert client.calls == 2


def test_structured_call_rejects_wrong_provider_model_before_checkpoint(tmp_path):
    class FixtureClient:
        def call(self, *, system, user):
            return {"valid": True}, {
                "responseId": "response-1",
                "model": "routed-to-unexpected-model",
            }

    checkpoint = tmp_path / "call.json"

    with pytest.raises(RuntimeError, match="resolved model mismatch"):
        _validated_judge_call(
            judge_client=FixtureClient(),
            system="system",
            prompt="prompt",
            validator=lambda value: value,
            checkpoint_path=checkpoint,
            binding={"caseId": "m2b-001"},
            max_structured_retries=3,
            expected_response_model="expected-model",
        )

    assert not checkpoint.exists()
