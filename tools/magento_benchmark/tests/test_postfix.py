from __future__ import annotations

import copy
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import magento2_benchmark.postfix as postfix
from magento2_benchmark.path_transition import resolve_path_transition
from magento2_benchmark.postfix import (
    ATTESTATION_FIELDS,
    LOCK_FIELDS,
    PLAN_FIELDS,
    POST_FIX_CONTROL_SET_KIND,
    POST_FIX_PROMPT_VERSION,
    POST_FIX_SYSTEM,
    _gold_fix_bindings,
    _post_fix_case,
    _validate_post_fix_call,
    derive_post_fix_outcome,
    validate_post_fix_control_set,
)
from magento2_benchmark.judge import _gold_prompt
from magento2_benchmark.util import (
    hermetic_git_environment,
    sha256_json,
    sha256_text,
)


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def _commit(repository: Path, content: str, message: str) -> str:
    source = repository / "app/code/Fixture/Example.php"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(content, encoding="utf-8")
    _git(repository, "add", "--", str(source.relative_to(repository)))
    _git(repository, "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


@pytest.fixture
def post_fix_git_case(tmp_path: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.name", "Benchmark Fixture")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    base = _commit(
        repository,
        "<?php\nfunction fixture(): bool {\n    return true;\n}\n",
        "base",
    )
    review_head = _commit(
        repository,
        (
            "<?php\nfunction fixture(): bool {\n"
            "    dangerous_call();\n    return true;\n}\n"
        ),
        "review snapshot",
    )
    final = _commit(
        repository,
        (
            "<?php\nfunction fixture(): bool {\n"
            "    safe_call();\n    return true;\n}\n"
        ),
        "fix review finding",
    )
    path = "app/code/Fixture/Example.php"
    transition, _ = resolve_path_transition(
        repository,
        checkpoint_sha=review_head,
        final_sha=final,
        source_path=path,
        git_env=hermetic_git_environment(offline=True),
    )
    case = {
        "caseId": "m2b-001",
        "snapshot": {
            "baseSha": base,
            "headSha": review_head,
        },
        "sourcePr": {
            "finalHeadSha": final,
            "mergeCommitSha": "f" * 40,
        },
        "ancestryEvidence": {
            "mergeSecondParentSha": final,
            "evidenceDigest": "a" * 64,
        },
        "goldenComments": [
            {
                "id": "m2b-001-comment-1",
                "sourceCommentId": 1,
                "path": path,
                "validity": {
                    "fixedLater": True,
                    "disposition": "fixed",
                    "fixCommitSha": final,
                    "fixEvidence": [
                        {
                            "kind": "commit",
                            "detail": "The unsafe call is replaced.",
                            "artifactDigest": "c" * 64,
                        }
                    ],
                    "pathTransition": transition,
                },
                "adjudication": {
                    "decisionDigest": "b" * 64,
                },
            }
        ],
    }
    primary = {
        "caseId": "m2b-001",
        "baseRef": "benchmark/m2b-001/base",
        "baseSha": base,
        "headRef": "benchmark/m2b-001/head",
        "headSha": review_head,
    }
    return repository, case, primary


def test_post_fix_case_reconstructs_exact_verified_f_without_labels(
    post_fix_git_case,
):
    repository, case, primary = post_fix_git_case

    result = _post_fix_case(
        case,
        primary,
        repository=repository,
    )

    assert result["baseSha"] == case["snapshot"]["baseSha"]
    assert result["reviewHeadSha"] == case["snapshot"]["headSha"]
    assert result["finalSha"] == case["sourcePr"]["finalHeadSha"]
    assert result["changedPaths"] == ["app/code/Fixture/Example.php"]
    assert result["deletedPaths"] == []
    assert not ({"fixBindings", "goldenComments"} & set(result))

    bindings = _gold_fix_bindings(
        case,
        result,
        repository=repository,
    )
    assert bindings == [
        {
            "goldId": "G001",
            "fixCommitSha": case["sourcePr"]["finalHeadSha"],
            "fixEvidenceDigest": sha256_json(
                case["goldenComments"][0]["validity"]["fixEvidence"]
            ),
            "decisionDigest": "b" * 64,
            "pathTransition": case["goldenComments"][0]["validity"][
                "pathTransition"
            ],
        }
    ]


def test_post_fix_replay_artifact_contracts_bind_execution_corpus():
    for fields in (PLAN_FIELDS, LOCK_FIELDS, ATTESTATION_FIELDS):
        assert "executionCorpusDigest" in fields


def test_post_fix_case_rejects_f_equal_h_and_fix_outside_h_to_f(
    post_fix_git_case,
):
    repository, case, primary = post_fix_git_case
    same_snapshot = copy.deepcopy(case)
    same_snapshot["sourcePr"]["finalHeadSha"] = case["snapshot"]["headSha"]
    same_snapshot["ancestryEvidence"]["mergeSecondParentSha"] = case[
        "snapshot"
    ]["headSha"]
    with pytest.raises(ValueError, match="strict B < H < verified F"):
        _post_fix_case(
            same_snapshot,
            primary,
            repository=repository,
        )

    outside_fix = copy.deepcopy(case)
    outside_fix["goldenComments"][0]["validity"]["fixCommitSha"] = case[
        "snapshot"
    ]["baseSha"]
    with pytest.raises(ValueError, match="outside H..F"):
        _gold_fix_bindings(
            outside_fix,
            _post_fix_case(case, primary, repository=repository),
            repository=repository,
        )


def _edge(candidate_id: str, verdict: str) -> dict[str, Any]:
    return {
        "candidateId": candidate_id,
        "specific_issue": "yes",
        "grounded_at_snapshot": "yes",
        "same_root_cause": "yes",
        "same_failure_or_consequence": "yes",
        "compatible_required_change": "yes",
        "location_relation": "same_symbol",
        "verdict": verdict,
        "confidence": 0.9,
        "rationale": "Fixture evidence supports this verdict.",
    }


def test_post_fix_outcome_is_conditional_on_h_true_positives():
    assert derive_post_fix_outcome(
        primary_matched=False,
        candidate_ids=["C001"],
        edges=[],
    )["outcome"] == "not_applicable_primary_unmatched"
    assert derive_post_fix_outcome(
        primary_matched=True,
        candidate_ids=[],
        edges=[],
    )["outcome"] == "disappeared"
    assert derive_post_fix_outcome(
        primary_matched=True,
        candidate_ids=["C001", "C002"],
        edges=[
            _edge("C001", "no_match"),
            _edge("C002", "substantive_match"),
        ],
    )["outcome"] == "still_detected"
    assert derive_post_fix_outcome(
        primary_matched=True,
        candidate_ids=["C001"],
        edges=[_edge("C001", "unverifiable")],
    )["outcome"] == "unverifiable"
    with pytest.raises(ValueError, match="every candidate exactly once"):
        derive_post_fix_outcome(
            primary_matched=True,
            candidate_ids=["C001", "C002"],
            edges=[_edge("C001", "no_match")],
        )
    forged = _edge("C001", "forged_verdict")
    with pytest.raises(ValueError, match="verdict is invalid"):
        derive_post_fix_outcome(
            primary_matched=True,
            candidate_ids=["C001"],
            edges=[forged],
        )


def test_post_fix_checkpoint_rejects_resealed_verdict_provider_and_time(
    tmp_path: Path,
):
    case_id = "m2b-001"
    gold_id = "G001"
    gold = {
        "path": "app/code/Fixture/Example.php",
        "originalLine": 3,
        "body": "Avoid the unsafe call.",
        "diffHunk": "@@ -1,3 +1,4 @@",
        "expectedIssue": {
            "summary": "Unsafe call",
            "rootCause": "Unsafe API use",
            "failureMode": "Runtime failure",
            "requiredChange": "Use the safe API",
        },
    }
    findings = [
        {
            "path": "app/code/Fixture/Example.php",
            "line": 3,
            "title": "Unsafe call remains",
            "description": "The unsafe API is still used.",
            "category": "bug",
            "severity": "medium",
            "suggestedFix": "Use the safe API.",
        }
    ]
    path_diff = "@@ -2,0 +3,1 @@\n+unsafe_call();\n"
    evidence = [
        {
            "inFrozenDiff": True,
            "lineOnAddedRightSide": True,
            "pathDiff": path_diff,
            "headSourceWindow": "     3 unsafe_call();",
            "pathDiffSha256": sha256_text(path_diff),
            "headSourceSha256": sha256_text("unsafe_call();"),
        }
    ]
    prompt = _gold_prompt(
        gold_label=gold_id,
        gold=gold,
        findings=findings,
        candidate_evidence=evidence,
        max_prompt_characters=100_000,
    )
    response = {
        "gold_id": gold_id,
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
                "rationale": "The same unsafe API remains.",
            }
        ],
    }
    judge_config = {
        "model": "judge-fixture",
        "expected_response_model": "judge-fixture",
        "temperature": 0,
        "repeats": 1,
        "max_prompt_characters": 100_000,
        "max_structured_retries": 3,
    }
    case_input_digest = "a" * 64
    judge_config_digest = sha256_json(judge_config)
    request = {
        "model": "judge-fixture",
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": POST_FIX_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    content = json.dumps(response)
    provider_response = {
        "id": "response-1",
        "model": "judge-fixture",
        "usage": {"total_tokens": 20},
        "choices": [{"message": {"content": content}}],
    }
    metadata = {
        "usage": provider_response["usage"],
        "responseId": provider_response["id"],
        "model": provider_response["model"],
        "promptSha256": sha256_text(POST_FIX_SYSTEM + "\n" + prompt),
        "rawContentSha256": sha256_text(content),
        "request": request,
        "requestSha256": sha256_json(request),
        "providerResponse": provider_response,
        "providerResponseSha256": sha256_json(provider_response),
    }
    binding = {
        "kind": "post_fix_pair",
        "caseId": case_id,
        "goldId": gold_id,
        "repeat": 1,
        "caseInputDigest": case_input_digest,
        "judgeConfigDigest": judge_config_digest,
        "promptVersion": POST_FIX_PROMPT_VERSION,
    }
    checkpoint = {
        "bindingDigest": sha256_json(
            {
                **binding,
                "systemSha256": sha256_text(POST_FIX_SYSTEM),
                "promptSha256": sha256_text(prompt),
            }
        ),
        "completedAt": "2026-07-29T13:30:00Z",
        "system": POST_FIX_SYSTEM,
        "prompt": prompt,
        "response": response,
        "metadata": metadata,
        "rejectedStructuredResponses": [],
    }
    checkpoint_relative = (
        Path("checkpoints")
        / case_id
        / f"post-fix-{gold_id}-1-{sha256_text(prompt)[:20]}.json"
    )

    def seal(value: dict[str, Any]) -> dict[str, Any]:
        sealed = copy.deepcopy(value)
        sealed["callDigest"] = sha256_json(sealed)
        path = tmp_path / checkpoint_relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sealed), encoding="utf-8")
        return {
            "kind": "post_fix_pair",
            "goldId": gold_id,
            "repeat": 1,
            "checkpoint": str(checkpoint_relative),
            **{
                key: item
                for key, item in sealed.items()
                if key != "metadata"
            },
            **dict(sealed["metadata"]),
        }

    def validate(call: dict[str, Any]) -> list[dict[str, Any]]:
        return _validate_post_fix_call(
            call,
            artifact_root=tmp_path,
            case_id=case_id,
            gold_id=gold_id,
            repeat=1,
            case_input_digest=case_input_digest,
            judge_config=judge_config,
            judge_config_digest=judge_config_digest,
            expected_response_model="judge-fixture",
            max_structured_retries=3,
            findings=findings,
            gold=gold,
            changed_paths={"app/code/Fixture/Example.php"},
            max_prompt_characters=100_000,
            unsealed_at=datetime(
                2026, 7, 29, 13, tzinfo=timezone.utc
            ),
            judgment_created_at=datetime(
                2026, 7, 29, 14, tzinfo=timezone.utc
            ),
        )

    assert validate(seal(checkpoint))[0]["verdict"] == "substantive_match"

    forged = copy.deepcopy(checkpoint)
    forged_response = forged["response"]
    forged_response["judgments"][0]["verdict"] = "forged_verdict"
    forged_content = json.dumps(forged_response)
    forged["metadata"]["providerResponse"]["choices"][0]["message"][
        "content"
    ] = forged_content
    forged["metadata"]["rawContentSha256"] = sha256_text(forged_content)
    forged["metadata"]["providerResponseSha256"] = sha256_json(
        forged["metadata"]["providerResponse"]
    )
    with pytest.raises(ValueError, match="match verdict"):
        validate(seal(forged))

    wrong_model = copy.deepcopy(checkpoint)
    wrong_model["metadata"]["model"] = "attacker/model"
    wrong_model["metadata"]["providerResponse"]["model"] = "attacker/model"
    wrong_model["metadata"]["providerResponseSha256"] = sha256_json(
        wrong_model["metadata"]["providerResponse"]
    )
    with pytest.raises(ValueError, match="response model drift"):
        validate(seal(wrong_model))

    pre_unseal = copy.deepcopy(checkpoint)
    pre_unseal["completedAt"] = "2026-07-29T12:59:59Z"
    with pytest.raises(ValueError, match="outside unseal"):
        validate(seal(pre_unseal))

    valid_call = seal(checkpoint)
    checkpoint_path = tmp_path / checkpoint_relative
    real_path = checkpoint_path.with_name("real-checkpoint.json")
    checkpoint_path.replace(real_path)
    checkpoint_path.symlink_to(real_path.name)
    with pytest.raises(ValueError, match="contains a symlink"):
        validate(valid_call)


def test_control_set_validator_requires_semantic_context_for_every_pair(
    monkeypatch,
):
    control_id = "judgment-a:post-fix"
    registration = {
        "registrationDigest": "a" * 64,
        "postFixPlan": {
            "judgmentPairs": [
                {"postFixJudgmentId": control_id},
            ]
        },
    }
    corpus = {"corpusDigest": "b" * 64}
    control = {
        "controlId": control_id,
        "controlDigest": "c" * 64,
    }
    context_fields = {
        "sealLedger",
        "primaryReplayLock",
        "primaryRun",
        "primaryJudgment",
        "postFixRun",
        "postFixLock",
        "postFixAttestation",
        "postFixJudgment",
        "postFixRunArtifactRoot",
        "postFixJudgmentArtifactRoot",
    }
    contexts = {
        control_id: {
            field: (
                Path("/tmp")
                if field.endswith("ArtifactRoot")
                else {}
            )
            for field in context_fields
        },
    }
    observed: list[str] = []

    def semantic_validator(value, **kwargs):
        observed.append(str(value["controlId"]))
        assert set(kwargs) == {
            "corpus",
            "registration",
            "seal_ledger",
            "primary_replay_lock",
            "primary_run",
            "primary_judgment",
            "post_fix_run",
            "post_fix_lock",
            "post_fix_attestation",
            "post_fix_judgment",
            "post_fix_run_artifact_root",
            "post_fix_judgment_artifact_root",
            "repository",
        }
        return {
            "controlId": value["controlId"],
            "controlDigest": value["controlDigest"],
            "summary": {"disappeared": 1},
        }

    monkeypatch.setattr(
        postfix,
        "validate_post_fix_control",
        semantic_validator,
    )
    value = {
        "kind": POST_FIX_CONTROL_SET_KIND,
        "createdAt": "2026-07-29T15:00:00Z",
        "registrationDigest": registration["registrationDigest"],
        "corpusDigest": corpus["corpusDigest"],
        "controls": [
            {
                "controlId": control_id,
                "controlDigest": control["controlDigest"],
            }
        ],
    }
    value["controlSetDigest"] = sha256_json(value)

    result = validate_post_fix_control_set(
        value,
        controls=[control],
        corpus=corpus,
        registration=registration,
        control_contexts=contexts,
    )
    assert result["controlSetDigest"] == value["controlSetDigest"]
    assert observed == [control_id]

    hostile = copy.deepcopy(value)
    hostile["controls"][0]["controlDigest"] = "d" * 64
    hostile["controlSetDigest"] = sha256_json(
        {
            key: item
            for key, item in hostile.items()
            if key != "controlSetDigest"
        }
    )
    with pytest.raises(ValueError, match="projection drift"):
        validate_post_fix_control_set(
            hostile,
            controls=[control],
            corpus=corpus,
            registration=registration,
            control_contexts=contexts,
        )

    with pytest.raises(ValueError, match="cover every registered control"):
        validate_post_fix_control_set(
            value,
            controls=[control],
            corpus=corpus,
            registration=registration,
            control_contexts={},
        )
