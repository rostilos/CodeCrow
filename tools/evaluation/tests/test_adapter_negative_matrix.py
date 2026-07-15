from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from codecrow_evaluation.adapters import (
    CorpusAdapterError,
    build_goodwine_manifest,
    build_martian_manifest,
    import_martian_snapshot,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case() -> dict:
    return {
        "pr_title": "A title",
        "url": "https://example.test/repo/pull/1",
        "comments": [{"comment": "A defect", "severity": "High"}],
    }


def _snapshot(tmp_path: Path) -> tuple[Path, Path, dict, Path]:
    root = tmp_path / "snapshot"
    golden = root / "golden.json"
    root.mkdir(parents=True)
    golden.write_text(json.dumps([_case()]) + "\n", encoding="utf-8")
    descriptor = {
        "schemaVersion": 1,
        "corpusId": "martian-offline",
        "sourceRepository": "https://github.com/withmartian/code-review-benchmark.git",
        "sourceCommit": "a" * 40,
        "license": "MIT",
        "purpose": "calibration",
        "labelVersion": "labels-v1",
        "oracleId": "oracle-v1",
        "expectedCases": 1,
        "expectedLabels": 1,
        "goldenFiles": [{"path": "golden.json", "sha256": _sha(golden)}],
        "supportFiles": [],
    }
    descriptor_path = tmp_path / "descriptor.json"
    return root, golden, descriptor, descriptor_path


def _write_descriptor(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_descriptor", "descriptor"),
        ("bad_descriptor_json", "descriptor"),
        ("bad_schema", "schemaVersion"),
        ("bad_corpus", "corpusId"),
        ("bad_license", "license"),
        ("protected_purpose", "acceptance purpose"),
        ("bad_commit", "sourceCommit"),
        ("bad_repository", "sourceRepository"),
        ("bad_label_version", "labelVersion"),
        ("bad_oracle", "oracleId"),
        ("bad_counts", "positive integers"),
        ("bad_golden_files", "goldenFiles"),
        ("bad_golden_entry", "entries"),
        ("duplicate_golden_path", "unique strings"),
        ("absolute_path", "relative path"),
        ("escaping_path", "escapes"),
        ("missing_path", "does not exist"),
        ("bad_golden_json", "UTF-8 JSON"),
        ("golden_not_array", "array"),
        ("case_not_object", "case must be an object"),
        ("missing_url", "URL is missing"),
        ("duplicate_url", "duplicate Martian case URL"),
        ("missing_title", "title is missing"),
        ("missing_comments", "comments are missing"),
        ("comment_not_object", "comment must be an object"),
        ("missing_comment", "comment text is missing"),
        ("bad_severity", "severity is invalid"),
        ("support_not_array", "supportFiles"),
        ("support_not_object", "supportFiles entries"),
        ("support_hash_mismatch", "support file SHA-256 mismatch"),
        ("count_mismatch", "counts do not match"),
    ],
)
def test_martian_snapshot_negative_matrix(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    root, golden, descriptor, descriptor_path = _snapshot(tmp_path)
    if mutation == "missing_descriptor":
        descriptor_path = tmp_path / "missing.json"
    elif mutation == "bad_descriptor_json":
        descriptor_path.write_text("{", encoding="utf-8")
    elif mutation == "bad_schema":
        descriptor["schemaVersion"] = 2
    elif mutation == "bad_corpus":
        descriptor["corpusId"] = "other"
    elif mutation == "bad_license":
        descriptor["license"] = "unknown"
    elif mutation == "protected_purpose":
        descriptor["purpose"] = "primary_heldout"
    elif mutation == "bad_commit":
        descriptor["sourceCommit"] = "ABC"
    elif mutation == "bad_repository":
        descriptor["sourceRepository"] = "git@example.test:repo"
    elif mutation == "bad_label_version":
        descriptor["labelVersion"] = ""
    elif mutation == "bad_oracle":
        descriptor["oracleId"] = 1
    elif mutation == "bad_counts":
        descriptor["expectedCases"] = True
    elif mutation == "bad_golden_files":
        descriptor["goldenFiles"] = []
    elif mutation == "bad_golden_entry":
        descriptor["goldenFiles"] = ["golden.json"]
    elif mutation == "duplicate_golden_path":
        descriptor["goldenFiles"].append(dict(descriptor["goldenFiles"][0]))
    elif mutation == "absolute_path":
        descriptor["goldenFiles"][0]["path"] = str(golden)
    elif mutation == "escaping_path":
        outside = tmp_path / "outside.json"
        outside.write_text("[]\n", encoding="utf-8")
        descriptor["goldenFiles"][0] = {
            "path": "../outside.json",
            "sha256": _sha(outside),
        }
    elif mutation == "missing_path":
        descriptor["goldenFiles"][0]["path"] = "missing.json"
    elif mutation == "bad_golden_json":
        golden.write_text("{", encoding="utf-8")
        descriptor["goldenFiles"][0]["sha256"] = _sha(golden)
    elif mutation == "golden_not_array":
        golden.write_text("{}\n", encoding="utf-8")
        descriptor["goldenFiles"][0]["sha256"] = _sha(golden)
    elif mutation == "case_not_object":
        golden.write_text('["case"]\n', encoding="utf-8")
        descriptor["goldenFiles"][0]["sha256"] = _sha(golden)
    else:
        case = _case()
        cases = [case]
        if mutation == "missing_url":
            case["url"] = ""
        elif mutation == "duplicate_url":
            cases.append(dict(case))
        elif mutation == "missing_title":
            case["pr_title"] = ""
        elif mutation == "missing_comments":
            case["comments"] = []
        elif mutation == "comment_not_object":
            case["comments"] = ["comment"]
        elif mutation == "missing_comment":
            case["comments"][0]["comment"] = ""
        elif mutation == "bad_severity":
            case["comments"][0]["severity"] = "Blocker"
        elif mutation == "support_not_array":
            descriptor["supportFiles"] = {}
        elif mutation == "support_not_object":
            descriptor["supportFiles"] = ["support"]
        elif mutation == "support_hash_mismatch":
            support = root / "support.txt"
            support.write_text("support\n", encoding="utf-8")
            descriptor["supportFiles"] = [
                {"path": "support.txt", "sha256": "0" * 64}
            ]
        elif mutation == "count_mismatch":
            descriptor["expectedLabels"] = 2
        golden.write_text(json.dumps(cases) + "\n", encoding="utf-8")
        descriptor["goldenFiles"][0]["sha256"] = _sha(golden)
    if mutation not in ("missing_descriptor", "bad_descriptor_json"):
        _write_descriptor(descriptor_path, descriptor)

    with pytest.raises(CorpusAdapterError, match=message):
        import_martian_snapshot(
            descriptor_path=descriptor_path,
            snapshot_root=root,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("protected", "sealed acceptance"),
        ("config_hash", "configSha256 mismatch"),
        ("data_hash", "dataSha256 mismatch"),
        ("config_json", "valid UTF-8 JSON"),
        ("config_schema", "schemaVersion"),
        ("config_disclosure", "disclose"),
    ],
)
def test_martian_manifest_negative_matrix(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    config = tmp_path / "config.json"
    data = tmp_path / "data.json"
    config.write_text(
        '{"schemaVersion":1,"fileSelection":["python"],"promptVersion":"v1"}\n',
        encoding="utf-8",
    )
    data.write_text("{}\n", encoding="utf-8")
    config_sha = _sha(config)
    data_sha = _sha(data)
    purpose = "calibration"
    if mutation == "protected":
        purpose = "primary_heldout"
    elif mutation == "config_hash":
        config_sha = "0" * 64
    elif mutation == "data_hash":
        data_sha = "0" * 64
    elif mutation == "config_json":
        config.write_text("{", encoding="utf-8")
        config_sha = _sha(config)
    elif mutation == "config_schema":
        config.write_text('{"schemaVersion":2}\n', encoding="utf-8")
        config_sha = _sha(config)
    elif mutation == "config_disclosure":
        config.write_text('{"schemaVersion":1}\n', encoding="utf-8")
        config_sha = _sha(config)

    with pytest.raises(CorpusAdapterError, match=message):
        build_martian_manifest(
            config_path=config,
            data_path=data,
            config_sha256=config_sha,
            data_sha256=data_sha,
            purpose=purpose,
        )


@pytest.mark.parametrize("contents", ["", "header\n"])
def test_goodwine_rejects_empty_or_header_only_csv(tmp_path: Path, contents: str) -> None:
    data = tmp_path / "goodwine.csv"
    data.write_text(contents, encoding="utf-8")
    with pytest.raises(CorpusAdapterError, match="Goodwine corpus"):
        build_goodwine_manifest(csv_path=data, data_sha256=_sha(data))
