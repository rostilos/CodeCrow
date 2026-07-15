from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from ._util import canonical_bytes, require_sha256, sha256_bytes, sha256_file


class CorpusAdapterError(ValueError):
    """A disclosed corpus export is missing, stale, or misrepresented."""


def _verified_file(path: Path, expected_sha256: str, field: str) -> str:
    path = path.resolve()
    if not path.is_file():
        raise CorpusAdapterError(f"{field} path does not exist: {path}")
    expected = require_sha256(expected_sha256, f"{field}Sha256", CorpusAdapterError)
    actual = sha256_file(path)
    if actual != expected:
        raise CorpusAdapterError(
            f"{field}Sha256 mismatch: expected {expected}, observed {actual}"
        )
    return actual


def _safe_snapshot_file(snapshot_root: Path, relative: object, field: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise CorpusAdapterError(f"{field} must be a non-empty relative path")
    root = snapshot_root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise CorpusAdapterError(f"{field} escapes the snapshot root") from exc
    if not candidate.is_file():
        raise CorpusAdapterError(f"{field} does not exist: {relative}")
    return candidate


def import_martian_snapshot(
    *,
    descriptor_path: Path,
    snapshot_root: Path,
) -> dict[str, Any]:
    """Verify and import the public Martian golden-label snapshot as calibration data."""

    try:
        descriptor_bytes = descriptor_path.read_bytes()
        descriptor = json.loads(descriptor_bytes)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CorpusAdapterError("Martian snapshot descriptor must be UTF-8 JSON") from exc
    if not isinstance(descriptor, dict) or descriptor.get("schemaVersion") != 1:
        raise CorpusAdapterError("Martian snapshot descriptor schemaVersion must be 1")
    if descriptor.get("corpusId") != "martian-offline":
        raise CorpusAdapterError("Martian snapshot corpusId must be martian-offline")
    if descriptor.get("license") != "MIT":
        raise CorpusAdapterError("Martian snapshot license must be MIT")
    if descriptor.get("purpose") not in ("development", "calibration", "diagnostic"):
        raise CorpusAdapterError("Martian snapshot cannot be a sealed acceptance purpose")
    source_commit = descriptor.get("sourceCommit")
    if not isinstance(source_commit, str) or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise CorpusAdapterError("Martian sourceCommit must be a full lowercase Git commit")
    source_repository = descriptor.get("sourceRepository")
    if not isinstance(source_repository, str) or not source_repository.startswith("https://"):
        raise CorpusAdapterError("Martian sourceRepository must be an HTTPS URL")
    label_version = descriptor.get("labelVersion")
    oracle_id = descriptor.get("oracleId")
    if not isinstance(label_version, str) or not label_version:
        raise CorpusAdapterError("Martian labelVersion must be non-empty")
    if not isinstance(oracle_id, str) or not oracle_id:
        raise CorpusAdapterError("Martian oracleId must be non-empty")
    expected_cases = descriptor.get("expectedCases")
    expected_labels = descriptor.get("expectedLabels")
    if (
        isinstance(expected_cases, bool)
        or not isinstance(expected_cases, int)
        or expected_cases < 1
        or isinstance(expected_labels, bool)
        or not isinstance(expected_labels, int)
        or expected_labels < 1
    ):
        raise CorpusAdapterError("Martian expectedCases and expectedLabels must be positive integers")

    golden_files = descriptor.get("goldenFiles")
    if not isinstance(golden_files, list) or not golden_files:
        raise CorpusAdapterError("Martian goldenFiles must be a non-empty array")
    paths: set[str] = set()
    cases: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    severity_map = {
        "Low": "low",
        "Medium": "medium",
        "High": "high",
        "Critical": "critical",
    }
    for entry in golden_files:
        if not isinstance(entry, dict):
            raise CorpusAdapterError("Martian goldenFiles entries must be objects")
        relative = entry.get("path")
        if not isinstance(relative, str) or relative in paths:
            raise CorpusAdapterError("Martian golden file paths must be unique strings")
        paths.add(relative)
        path = _safe_snapshot_file(snapshot_root, relative, "golden file path")
        expected_sha = require_sha256(
            entry.get("sha256"), "golden file sha256", CorpusAdapterError
        )
        if sha256_file(path) != expected_sha:
            raise CorpusAdapterError(f"golden file SHA-256 mismatch: {relative}")
        try:
            raw_cases = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CorpusAdapterError(f"golden file is not UTF-8 JSON: {relative}") from exc
        if not isinstance(raw_cases, list):
            raise CorpusAdapterError(f"golden file must contain an array: {relative}")
        for raw_case in raw_cases:
            if not isinstance(raw_case, dict):
                raise CorpusAdapterError(f"golden case must be an object: {relative}")
            case_id = raw_case.get("url")
            title = raw_case.get("pr_title")
            comments = raw_case.get("comments")
            if not isinstance(case_id, str) or not case_id:
                raise CorpusAdapterError(f"golden case URL is missing: {relative}")
            if case_id in case_ids:
                raise CorpusAdapterError(f"duplicate Martian case URL: {case_id}")
            case_ids.add(case_id)
            if not isinstance(title, str) or not title:
                raise CorpusAdapterError(f"golden case title is missing: {case_id}")
            if not isinstance(comments, list) or not comments:
                raise CorpusAdapterError(f"golden case comments are missing: {case_id}")
            labels: list[dict[str, str]] = []
            for index, raw_comment in enumerate(comments):
                if not isinstance(raw_comment, dict):
                    raise CorpusAdapterError(f"golden comment must be an object: {case_id}")
                description = raw_comment.get("comment")
                raw_severity = raw_comment.get("severity")
                if not isinstance(description, str) or not description.strip():
                    raise CorpusAdapterError(f"golden comment text is missing: {case_id}")
                if raw_severity not in severity_map:
                    raise CorpusAdapterError(
                        f"golden comment severity is invalid: {case_id} comment {index}"
                    )
                severity = severity_map[raw_severity]
                label_identity = {
                    "caseId": case_id,
                    "commentIndex": index,
                    "description": description,
                    "labelVersion": label_version,
                    "severity": severity,
                }
                labels.append(
                    {
                        "description": description,
                        "labelId": sha256_bytes(canonical_bytes(label_identity)),
                        "labelVersion": label_version,
                        "oracleId": oracle_id,
                        "severity": severity,
                    }
                )
            cases.append({"caseId": case_id, "labels": labels, "prTitle": title})

    support_files = descriptor.get("supportFiles", [])
    if not isinstance(support_files, list):
        raise CorpusAdapterError("Martian supportFiles must be an array")
    for entry in support_files:
        if not isinstance(entry, dict):
            raise CorpusAdapterError("Martian supportFiles entries must be objects")
        relative = entry.get("path")
        path = _safe_snapshot_file(snapshot_root, relative, "support file path")
        expected_sha = require_sha256(
            entry.get("sha256"), "support file sha256", CorpusAdapterError
        )
        if sha256_file(path) != expected_sha:
            raise CorpusAdapterError(f"support file SHA-256 mismatch: {relative}")

    cases.sort(key=lambda item: item["caseId"])
    label_count = sum(len(case["labels"]) for case in cases)
    if len(cases) != expected_cases or label_count != expected_labels:
        raise CorpusAdapterError(
            "Martian imported case/label counts do not match the descriptor"
        )
    catalog = {
        "caseCount": len(cases),
        "cases": cases,
        "corpusId": "martian-offline",
        "descriptorSha256": sha256_bytes(descriptor_bytes),
        "labelCount": label_count,
        "labelVersion": label_version,
        "oracleId": oracle_id,
        "purpose": descriptor["purpose"],
        "schemaVersion": 1,
        "sourceCommit": source_commit,
        "sourceRepository": source_repository,
    }
    catalog["catalogSha256"] = sha256_bytes(canonical_bytes(catalog))
    return catalog


def build_martian_manifest(
    *,
    config_path: Path,
    data_path: Path,
    config_sha256: str,
    data_sha256: str,
    purpose: str,
) -> dict[str, Any]:
    if purpose not in ("development", "calibration", "diagnostic"):
        raise CorpusAdapterError(
            "the disclosed Martian adapter cannot create a sealed acceptance split"
        )
    config_digest = _verified_file(config_path, config_sha256, "config")
    data_digest = _verified_file(data_path, data_sha256, "data")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CorpusAdapterError("Martian configuration must be valid UTF-8 JSON") from exc
    if not isinstance(config, dict) or config.get("schemaVersion") != 1:
        raise CorpusAdapterError("Martian configuration schemaVersion must be 1")
    if not config.get("fileSelection") or not config.get("promptVersion"):
        raise CorpusAdapterError(
            "Martian configuration must disclose fileSelection and promptVersion"
        )
    return {
        "configurationDisclosed": True,
        "configurationSha256": config_digest,
        "corpusId": "martian",
        "dataSha256": data_digest,
        "limitations": [
            "disclosed benchmark configuration",
            "not evidence of customer performance",
            "not a sealed acceptance reserve",
        ],
        "purpose": purpose,
        "schemaVersion": 1,
        "sourceKind": "public_export",
        "supportsFalseNegatives": True,
    }


def build_goodwine_manifest(
    *,
    csv_path: Path,
    data_sha256: str,
    purpose: str = "diagnostic",
) -> dict[str, Any]:
    if purpose != "diagnostic":
        raise CorpusAdapterError(
            "the visible Goodwine issue corpus is diagnostic-only and cannot be held out"
        )
    digest = _verified_file(csv_path, data_sha256, "data")
    try:
        with csv_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            row_count = sum(1 for _ in reader)
    except (OSError, UnicodeError, StopIteration, csv.Error) as exc:
        raise CorpusAdapterError("Goodwine corpus must be a non-empty UTF-8 CSV") from exc
    if not header or row_count < 1:
        raise CorpusAdapterError("Goodwine corpus must contain a header and at least one row")
    return {
        "configurationDisclosed": True,
        "corpusId": "goodwine",
        "dataSha256": digest,
        "limitations": [
            "identities and outcomes are already visible",
            "does not establish false negatives",
            "not evidence of customer performance",
        ],
        "purpose": "diagnostic",
        "rowCount": row_count,
        "schemaVersion": 1,
        "sourceKind": "visible_issue_export",
        "supportedMetrics": [
            "duplicate_rate",
            "false_positive_rate",
            "stale_finding_rate",
            "unsupported_rate",
        ],
        "supportsFalseNegatives": False,
    }
