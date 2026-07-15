from __future__ import annotations

import hashlib
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import codecrow_evaluation.cli as cli_module
from codecrow_evaluation.cli import main


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_score_cli_writes_canonical_reproducible_result(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "result.json"
    input_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "evaluationId": "dev-run",
                "splitPurpose": "development",
                "scoringPolicyVersion": "p0-05-v1",
                "provenance": {
                    "baselineManifestSha256": "a" * 64,
                    "codecrowPublicRevision": "89287e1fce55dc9bffeca2b92ce660d8791ae6ac",
                    "codecrowStaticRevision": "d661106ecafaabcb3349676b93684246de6bdc17",
                    "dirtyStateSha256": "b" * 64,
                    "splitRegistrySha256": "c" * 64,
                    "corpusManifestSha256": "d" * 64,
                    "oracleCatalogSha256": "e" * 64,
                    "executionTelemetrySha256": "f" * 64,
                    "environmentSha256": "1" * 64,
                    "modelVersion": "offline-model-v1",
                    "promptVersion": "prompt-v1",
                    "ruleVersion": "rule-v1",
                    "indexVersion": "rag-disabled",
                    "seed": 0,
                    "command": ["codecrow-evaluation", "score"],
                    "accessGrantId": None,
                    "accessLedgerHeadSha256": None,
                },
                "cases": [
                    {
                        "caseId": "pr-a",
                        "labels": [],
                        "predictions": [],
                        "analysis": {"state": "complete", "partialReason": None},
                        "coverage": {"represented": 1, "total": 1},
                        "resource": {
                            "estimatedCostMicrousd": 0,
                            "providerReportedCostMicrousd": 0,
                            "latencyMs": 1,
                        },
                    }
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["score", "--input", str(input_path), "--output", str(output_path)]) == 0
    first = output_path.read_bytes()
    assert first.endswith(b"\n")
    assert main(["score", "--input", str(input_path), "--output", str(output_path)]) == 0
    assert output_path.read_bytes() == first
    assert json.loads(first)["aggregate"]["cleanControls"]["passed"] == 1


def test_commit_bundle_cli_emits_only_opaque_hashes(tmp_path: Path) -> None:
    identities = tmp_path / "identities.json"
    labels = tmp_path / "labels.json"
    outcomes = tmp_path / "outcomes.json"
    output = tmp_path / "commitments.json"
    identities.write_text('{"secretIdentity":"repo-42/pr-9"}\n', encoding="utf-8")
    labels.write_text('{"secretLabel":"bug-a"}\n', encoding="utf-8")
    outcomes.write_text('{"secretOutcome":"failed"}\n', encoding="utf-8")

    assert (
        main(
            [
                "commit-bundle",
                "--split-id",
                "primary-v1",
                "--identities",
                str(identities),
                "--labels",
                str(labels),
                "--outcomes",
                str(outcomes),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload == {
        "identitiesCommitmentSha256": _sha256(identities),
        "labelsCommitmentSha256": _sha256(labels),
        "outcomesCommitmentSha256": _sha256(outcomes),
        "schemaVersion": 1,
        "splitId": "primary-v1",
    }
    assert "repo-42" not in output.read_text(encoding="utf-8")


def _registry() -> dict:
    features = [
        "clean_control",
        "collision",
        "cross_file",
        "hard_negative",
        "large_pr",
        "multilanguage",
        "positive",
        "rename",
    ]
    splits = [
        {
            "splitId": "dev",
            "purpose": "development",
            "sourceKind": "public",
            "caseCount": 1,
            "contentSha256": "a" * 64,
            "labelsVisible": True,
        },
        {
            "splitId": "cal",
            "purpose": "calibration",
            "sourceKind": "public",
            "caseCount": 1,
            "contentSha256": "b" * 64,
            "labelsVisible": True,
        },
    ]
    for split_id, purpose, values, gate in (
        ("primary", "primary_heldout", ("c", "d", "e"), "P5-06"),
        ("reserve", "confirmation_reserve", ("1", "2", "3"), "POST-P5-08"),
    ):
        splits.append(
            {
                "splitId": split_id,
                "purpose": purpose,
                "sourceKind": "internal_blinded",
                "caseCount": 1,
                "identitiesCommitmentSha256": values[0] * 64,
                "labelsCommitmentSha256": values[1] * 64,
                "outcomesCommitmentSha256": values[2] * 64,
                "registeredGate": gate,
                "custodian": "custodian",
                "independentReviewer": "reviewer",
                "sealedAt": "2026-07-15T00:00:00Z",
                "featureCoverage": features,
                "featureCoverageAttestationSha256": "f" * 64,
            }
        )
    return {
        "schemaVersion": 1,
        "registryId": "registry-v1",
        "registryVersion": "v1",
        "programOwner": "owner",
        "splits": splits,
        "disjointnessAttestation": {
            "coversSplitIds": ["cal", "dev", "primary", "reserve"],
            "custodian": "custodian",
            "independentReviewer": "reviewer",
            "membershipDigestSha256": "4" * 64,
            "signedAt": "2026-07-15T00:00:00Z",
        },
    }


def test_validate_registry_and_import_martian_cli_paths(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    context = tmp_path / "context.json"
    registry.write_text(json.dumps(_registry()) + "\n", encoding="utf-8")
    assert main(["validate-registry", "--input", str(registry)]) == 0
    assert (
        main(
            [
                "validate-registry",
                "--input",
                str(registry),
                "--policy-context-output",
                str(context),
            ]
        )
        == 0
    )
    assert [item["splitId"] for item in json.loads(context.read_text())["splits"]] == [
        "cal",
        "dev",
    ]

    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    golden = snapshot / "golden.json"
    golden.write_text(
        '[{"pr_title":"Title","url":"https://example.test/pr/1","comments":[{"comment":"Bug","severity":"High"}]}]\n',
        encoding="utf-8",
    )
    descriptor = tmp_path / "descriptor.json"
    descriptor.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "corpusId": "martian-offline",
                "sourceRepository": "https://example.test/repo.git",
                "sourceCommit": "a" * 40,
                "license": "MIT",
                "purpose": "calibration",
                "labelVersion": "labels-v1",
                "oracleId": "oracle-v1",
                "expectedCases": 1,
                "expectedLabels": 1,
                "goldenFiles": [{"path": "golden.json", "sha256": _sha256(golden)}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    catalog = tmp_path / "catalog.json"
    assert (
        main(
            [
                "import-martian",
                "--descriptor",
                str(descriptor),
                "--snapshot-root",
                str(snapshot),
                "--output",
                str(catalog),
            ]
        )
        == 0
    )
    assert json.loads(catalog.read_text())["caseCount"] == 1


def test_cli_missing_bundle_component_and_atomic_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        main(
            [
                "commit-bundle",
                "--split-id",
                "primary",
                "--identities",
                str(tmp_path / "missing"),
                "--labels",
                str(tmp_path / "missing"),
                "--outcomes",
                str(tmp_path / "missing"),
                "--output",
                str(tmp_path / "output.json"),
            ]
        )

    monkeypatch.setattr(cli_module.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("replace")))
    with pytest.raises(OSError, match="replace"):
        cli_module._write_json(tmp_path / "atomic.json", {"value": 1})
    assert not list(tmp_path.glob(".atomic.json.*.tmp"))


def test_module_entrypoint_and_unreachable_command_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["codecrow-evaluation", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("codecrow_evaluation.__main__", run_name="__main__")
    assert exit_info.value.code == 0
    with pytest.raises(SystemExit) as script_exit:
        runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "bin" / "codecrow-evaluation.py"),
            run_name="__main__",
        )
    assert script_exit.value.code == 0

    class FakeParser:
        def parse_args(self, argv):
            return SimpleNamespace(command="impossible")

    monkeypatch.setattr(cli_module, "_parser", lambda: FakeParser())
    with pytest.raises(AssertionError, match="unhandled command"):
        main([])
