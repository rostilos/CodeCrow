from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from codecrow_evaluation.adapters import (
    CorpusAdapterError,
    build_goodwine_manifest,
    build_martian_manifest,
    import_martian_snapshot,
)
from codecrow_evaluation.oracles import (
    OracleInputError,
    OracleSpec,
    run_executable_oracle,
    validate_label_record,
)


EVALUATION_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = EVALUATION_ROOT / "schema"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)


def test_executable_oracle_runs_pinned_argv_and_emits_versioned_result(tmp_path: Path) -> None:
    script = tmp_path / "oracle.py"
    _write_executable(
        script,
        """import json, pathlib, sys
case_root, output = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
payload = json.loads((case_root / 'case.json').read_text())
output.write_text(json.dumps({
    'schemaVersion': 1,
    'oracleId': 'compile-oracle',
    'oracleVersion': 'v1',
    'caseId': payload['caseId'],
    'status': 'pass',
    'observedLabelIds': ['bug-a'],
}, sort_keys=True) + '\\n')
""",
    )
    case_root = tmp_path / "case"
    case_root.mkdir()
    (case_root / "case.json").write_text('{"caseId":"pr-a"}\n', encoding="utf-8")
    output = tmp_path / "oracle-result.json"
    wrapper = tmp_path / "run-offline.sh"
    _write_executable(wrapper, '#!/bin/sh\nexec "$@"\n')
    spec = OracleSpec.from_mapping(
        {
            "schemaVersion": 1,
            "oracleId": "compile-oracle",
            "oracleVersion": "v1",
            "kind": "executable",
            "executablePath": sys.executable,
            "executableSha256": _sha256(Path(sys.executable)),
            "argv": [str(script), "{case_root}", "{output}"],
            "artifacts": [{"path": str(script), "sha256": _sha256(script)}],
            "timeoutSeconds": 5,
        }
    )

    result = run_executable_oracle(
        spec,
        case_root=case_root,
        output_path=output,
        offline_runner=wrapper,
        offline_runner_sha256=_sha256(wrapper),
    )

    assert result["caseId"] == "pr-a"
    assert result["status"] == "pass"
    assert result["observedLabelIds"] == ["bug-a"]
    assert result["execution"]["offlineRunnerSha256"] == _sha256(wrapper)
    assert result["execution"]["exitCode"] == 0
    assert result["execution"]["durationMs"] >= 0
    assert len(result["execution"]["oracleSpecSha256"]) == 64
    Draft202012Validator(
        json.loads((SCHEMA_ROOT / "oracle-result-v1.schema.json").read_text(encoding="utf-8"))
    ).validate(result)

    assert run_executable_oracle(
        spec,
        case_root=case_root,
        output_path=output,
        offline_runner=wrapper,
        offline_runner_sha256=_sha256(wrapper),
    )["status"] == "pass"

    script.write_text(script.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    with pytest.raises(OracleInputError, match="oracle artifact SHA-256"):
        run_executable_oracle(
            spec,
            case_root=case_root,
            output_path=output,
            offline_runner=wrapper,
            offline_runner_sha256=_sha256(wrapper),
        )


def test_oracle_rejects_unpinned_runtime_and_timeout_is_not_a_clean_result(tmp_path: Path) -> None:
    wrapper = tmp_path / "run-offline.sh"
    _write_executable(wrapper, '#!/bin/sh\nexec "$@"\n')
    spec_mapping = {
        "schemaVersion": 1,
        "oracleId": "slow-oracle",
        "oracleVersion": "v1",
        "kind": "executable",
        "executablePath": sys.executable,
        "executableSha256": "0" * 64,
        "argv": ["-c", "import time; time.sleep(1)"],
        "artifacts": [],
        "timeoutSeconds": 0.01,
    }
    spec = OracleSpec.from_mapping(spec_mapping)
    with pytest.raises(OracleInputError, match="executableSha256"):
        run_executable_oracle(
            spec,
            case_root=tmp_path,
            output_path=tmp_path / "out.json",
            offline_runner=wrapper,
            offline_runner_sha256=_sha256(wrapper),
        )

    spec_mapping["executableSha256"] = _sha256(Path(sys.executable))
    spec = OracleSpec.from_mapping(spec_mapping)
    with pytest.raises(OracleInputError, match="timed out"):
        run_executable_oracle(
            spec,
            case_root=tmp_path,
            output_path=tmp_path / "out.json",
            offline_runner=wrapper,
            offline_runner_sha256=_sha256(wrapper),
        )


def test_subjective_label_disagreement_requires_independent_adjudication() -> None:
    valid = {
        "schemaVersion": 1,
        "caseId": "pr-a",
        "labelVersion": "labels-v2",
        "oracleKind": "subjective",
        "labelers": ["labeler-a", "labeler-b"],
        "adjudicator": "reviewer-c",
        "adjudication": "bug-a is valid because the changed call violates the API contract",
        "labels": [{"labelId": "bug-a", "severity": "high"}],
    }
    assert validate_label_record(valid)["labelVersion"] == "labels-v2"

    invalid = dict(valid, adjudicator="labeler-a")
    with pytest.raises(OracleInputError, match="independent adjudicator"):
        validate_label_record(invalid)


def test_martian_adapter_binds_disclosed_configuration_and_local_data(tmp_path: Path) -> None:
    config = tmp_path / "martian-config.json"
    data = tmp_path / "martian-cases.jsonl"
    config.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "fileSelection": ["java", "python"],
                "promptVersion": "legacy-disclosed-v1",
                "notes": "manual benchmark scope; not primary acceptance",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    data.write_text('{"caseId":"martian-1"}\n', encoding="utf-8")

    manifest = build_martian_manifest(
        config_path=config,
        data_path=data,
        config_sha256=_sha256(config),
        data_sha256=_sha256(data),
        purpose="calibration",
    )

    assert manifest["corpusId"] == "martian"
    assert manifest["configurationDisclosed"] is True
    assert manifest["purpose"] == "calibration"
    assert manifest["limitations"] == [
        "disclosed benchmark configuration",
        "not evidence of customer performance",
        "not a sealed acceptance reserve",
    ]

    with pytest.raises(CorpusAdapterError, match="data path does not exist"):
        build_martian_manifest(
            config_path=config,
            data_path=tmp_path / "missing.jsonl",
            config_sha256=_sha256(config),
            data_sha256="0" * 64,
            purpose="calibration",
        )


def test_martian_snapshot_import_verifies_every_golden_file_and_versions_labels(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot"
    golden = snapshot / "offline" / "golden_comments"
    golden.mkdir(parents=True)
    labels = golden / "python.json"
    labels.write_text(
        json.dumps(
            [
                {
                    "pr_title": "Fix pagination",
                    "url": "https://example.test/repo/pull/7",
                    "comments": [
                        {"comment": "Negative slicing crashes", "severity": "High"},
                        {"comment": "Missing clean-up", "severity": "Low"},
                    ],
                }
            ],
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    descriptor = tmp_path / "martian-snapshot.json"
    support_a = snapshot / "LICENSE"
    support_b = snapshot / "README.md"
    support_a.write_text("MIT\n", encoding="utf-8")
    support_b.write_text("benchmark\n", encoding="utf-8")
    descriptor.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "corpusId": "martian-offline",
                "sourceRepository": "https://github.com/withmartian/code-review-benchmark.git",
                "sourceCommit": "a" * 40,
                "license": "MIT",
                "purpose": "calibration",
                "labelVersion": "martian-a",
                "oracleId": "martian-human-golden-v1",
                "expectedCases": 1,
                "expectedLabels": 2,
                "goldenFiles": [
                    {
                        "path": "offline/golden_comments/python.json",
                        "sha256": _sha256(labels),
                    }
                ],
                "supportFiles": [
                    {"path": "LICENSE", "sha256": _sha256(support_a)},
                    {"path": "README.md", "sha256": _sha256(support_b)},
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    catalog = import_martian_snapshot(descriptor_path=descriptor, snapshot_root=snapshot)

    assert catalog["caseCount"] == 1
    assert catalog["labelCount"] == 2
    assert catalog["sourceCommit"] == "a" * 40
    assert catalog["cases"][0]["caseId"] == "https://example.test/repo/pull/7"
    assert [label["severity"] for label in catalog["cases"][0]["labels"]] == [
        "high",
        "low",
    ]
    assert all(label["labelVersion"] == "martian-a" for label in catalog["cases"][0]["labels"])
    assert all(len(label["labelId"]) == 64 for label in catalog["cases"][0]["labels"])
    Draft202012Validator(
        json.loads((SCHEMA_ROOT / "martian-catalog-v1.schema.json").read_text(encoding="utf-8"))
    ).validate(catalog)

    labels.write_text("[]\n", encoding="utf-8")
    with pytest.raises(CorpusAdapterError, match="golden file SHA-256 mismatch"):
        import_martian_snapshot(descriptor_path=descriptor, snapshot_root=snapshot)


def test_goodwine_adapter_is_permanently_diagnostic_and_cannot_claim_recall(tmp_path: Path) -> None:
    csv_path = tmp_path / "goodwine.csv"
    csv_path.write_text("issue_id,is_duplicate,is_false_positive\n1,false,true\n", encoding="utf-8")

    manifest = build_goodwine_manifest(csv_path=csv_path, data_sha256=_sha256(csv_path))
    assert manifest["purpose"] == "diagnostic"
    assert manifest["supportsFalseNegatives"] is False
    assert "recall" not in manifest["supportedMetrics"]

    with pytest.raises(CorpusAdapterError, match="diagnostic-only"):
        build_goodwine_manifest(
            csv_path=csv_path,
            data_sha256=_sha256(csv_path),
            purpose="primary_heldout",
        )


@pytest.mark.parametrize(
    "schema_name",
    [
        "evaluation-input-v1.schema.json",
        "evaluation-result-v1.schema.json",
        "split-registry-v1.schema.json",
        "access-ledger-event-v1.schema.json",
        "access-ledger-head-v1.schema.json",
        "martian-catalog-v1.schema.json",
        "oracle-result-v1.schema.json",
        "oracle-spec-v1.schema.json",
        "corpus-manifest-v1.schema.json",
    ],
)
def test_checked_in_schemas_are_valid_draft_2020_12(schema_name: str) -> None:
    schema = json.loads((SCHEMA_ROOT / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
