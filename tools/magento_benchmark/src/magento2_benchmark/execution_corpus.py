from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from .corpus import validate_corpus
from .util import (
    read_json,
    require_full_sha,
    require_text,
    sha256_json,
    write_json,
)


EXECUTION_CORPUS_KIND = "codecrow-magento2-analysis-execution-corpus"
SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

EXECUTION_FIELDS = {
    "kind",
    "generatedAt",
    "corpusId",
    "corpusDigest",
    "repository",
    "defaultBranch",
    "cases",
    "executionCorpusDigest",
}
CASE_FIELDS = {"caseId", "partition", "sizeBand", "snapshot", "replay"}
SNAPSHOT_FIELDS = {
    "baseSha",
    "headSha",
    "fileCount",
    "changedPaths",
    "diffSha256",
}
REPLAY_FIELDS = {"baseRef", "headRef"}

# Keys are normalized by removing punctuation and case-folding. These names
# describe label/evaluation material and are never valid in a pre-unseal
# execution value, even if nested under an otherwise innocuous wrapper.
FORBIDDEN_KEY_NAMES = {
    "adjudication",
    "bodysha256",
    "comment",
    "commentbody",
    "commentid",
    "comments",
    "decision",
    "decisionbindingdigest",
    "decisiondigest",
    "decisions",
    "disposition",
    "expectedissue",
    "expectedissues",
    "fixbinding",
    "fixbindings",
    "fixbindingsdigest",
    "fixcommitsha",
    "fixevidence",
    "fixevidencedigest",
    "goldid",
    "goldencomment",
    "goldencomments",
    "inreplytoid",
    "normalizeddecision",
    "reviewer",
    "reviewbody",
    "reviewcomment",
    "reviewcommentid",
    "reviewcomments",
    "reviewerid",
    "reviewid",
    "rootcommentid",
    "sourcecommentid",
    "sourcebody",
    "sourcereviewid",
    "validity",
}

# Source-corpus subtrees from which scalar label values are collected. Values
# already present in the explicit public projection are removed from this set;
# for example, a review anchor may repeat the public H commit SHA.
SENSITIVE_SOURCE_KEYS = {
    "adjudication",
    "body",
    "bodysha256",
    "disposition",
    "expectedissue",
    "fixevidence",
    "goldencomments",
    "reviewer",
    "sourcecommentid",
    "sourcereviewid",
    "validity",
}


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _exact_fields(value: Any, expected: set[str], field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        missing = sorted(expected - set(value)) if isinstance(value, Mapping) else []
        unexpected = (
            sorted(set(value) - expected) if isinstance(value, Mapping) else []
        )
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        suffix = f" ({'; '.join(details)})" if details else ""
        raise ValueError(f"{field} fields are invalid{suffix}")
    return value


def _timestamp(value: Any, field: str) -> str:
    text = require_text(value, field)
    if not text.endswith("Z"):
        raise ValueError(f"{field} must be a UTC ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} must be a UTC ISO-8601 timestamp") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field} must be a UTC ISO-8601 timestamp")
    return text


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or SHA256_HEX.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _safe_path(value: Any, field: str) -> str:
    text = require_text(value, field)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or text != path.as_posix()
        or text in {".", ".."}
        or ".." in path.parts
        or "\x00" in text
    ):
        raise ValueError(f"{field} must be a normalized repository-relative path")
    return text


def _public_case(case: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = case["snapshot"]
    replay = case["replay"]
    return {
        "caseId": case["caseId"],
        "partition": case["partition"],
        "sizeBand": case["sizeBand"],
        "snapshot": {
            "baseSha": snapshot["baseSha"],
            "headSha": snapshot["headSha"],
            "fileCount": snapshot["fileCount"],
            "changedPaths": list(snapshot["changedPaths"]),
            "diffSha256": snapshot["diffSha256"],
        },
        "replay": {
            "baseRef": replay["baseRef"],
            "headRef": replay["headRef"],
        },
    }


def _scalars(value: Any) -> set[Any]:
    result: set[Any] = set()
    if isinstance(value, Mapping):
        for child in value.values():
            result.update(_scalars(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_scalars(child))
    elif (
        isinstance(value, str)
        and value
        or isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 1_000
    ):
        result.add(value)
    return result


def known_label_values(corpus: Mapping[str, Any]) -> set[Any]:
    """Return non-public scalar values derived from label-bearing subtrees.

    The function is intended for the label custodian at projection time. It
    deliberately subtracts values in the approved execution projection so an
    H commit repeated by a review anchor remains usable while a reviewer name,
    comment ID/body, decision digest, or fix-only identity cannot be renamed
    into a different key and smuggled into a pre-unseal artifact.
    """

    sensitive: set[Any] = set()

    def visit(value: Any, *, sensitive_parent: bool = False) -> None:
        if isinstance(value, Mapping):
            for raw_key, child in value.items():
                child_sensitive = (
                    sensitive_parent
                    or _normalized_key(raw_key) in SENSITIVE_SOURCE_KEYS
                )
                if child_sensitive:
                    sensitive.update(_scalars(child))
                else:
                    visit(child, sensitive_parent=False)
        elif isinstance(value, list):
            for child in value:
                visit(child, sensitive_parent=sensitive_parent)

    visit(corpus)
    public = {
        "kind": EXECUTION_CORPUS_KIND,
        "generatedAt": corpus.get("generatedAt"),
        "corpusId": corpus.get("corpusId"),
        "corpusDigest": corpus.get("corpusDigest"),
        "repository": corpus.get("repository"),
        "defaultBranch": corpus.get("defaultBranch"),
        "cases": [
            _public_case(case)
            for case in corpus.get("cases", [])
            if isinstance(case, Mapping)
        ],
    }
    return sensitive - _scalars(public)


def assert_label_free_execution_value(
    value: Any,
    *,
    forbidden_values: Iterable[Any] = (),
    context: str = "pre-unseal execution value",
) -> None:
    """Recursively reject label-shaped keys and known non-public values."""

    forbidden = set(forbidden_values)

    def visit(child: Any, path: str) -> None:
        if isinstance(child, Mapping):
            for raw_key, nested in child.items():
                key = str(raw_key)
                if _normalized_key(key) in FORBIDDEN_KEY_NAMES:
                    raise ValueError(
                        f"{context} contains forbidden label key at {path}.{key}"
                    )
                visit(nested, f"{path}.{key}")
            return
        if isinstance(child, list):
            for index, nested in enumerate(child):
                visit(nested, f"{path}[{index}]")
            return
        if child in forbidden and not isinstance(child, bool):
            raise ValueError(
                f"{context} contains a known label value at {path}"
            )

    visit(value, "$")


def build_execution_corpus(
    corpus: Mapping[str, Any],
    *,
    require_paper_ready: bool = True,
) -> dict[str, Any]:
    """Project a strict released corpus into the label-free H execution view."""

    summary = validate_corpus(
        corpus,
        paper_ready=require_paper_ready,
        required_cases=50,
    )
    if require_paper_ready and summary.get("paperReady") is not True:
        raise ValueError("execution corpus requires a strict paper-ready release")
    result = {
        "kind": EXECUTION_CORPUS_KIND,
        # Keep the projection reproducible from the immutable release. The
        # custodian command's filesystem/ledger record establishes when the
        # projection was handed to the blinded operator.
        "generatedAt": corpus["generatedAt"],
        "corpusId": summary["corpusId"],
        "corpusDigest": summary["corpusDigest"],
        "repository": corpus["repository"],
        "defaultBranch": corpus["defaultBranch"],
        "cases": [_public_case(case) for case in corpus["cases"]],
    }
    assert_label_free_execution_value(
        result,
        forbidden_values=known_label_values(corpus),
        context="generated analysis execution corpus",
    )
    result["executionCorpusDigest"] = sha256_json(result)
    validate_execution_corpus(result)
    return result


def validate_execution_corpus(value: Any) -> dict[str, Any]:
    """Validate the standalone, label-free pre-unseal execution contract."""

    artifact = _exact_fields(value, EXECUTION_FIELDS, "execution corpus")
    if artifact.get("kind") != EXECUTION_CORPUS_KIND:
        raise ValueError("execution corpus kind is invalid")
    _timestamp(artifact.get("generatedAt"), "execution corpus generatedAt")
    corpus_id = require_text(artifact.get("corpusId"), "execution corpus corpusId")
    corpus_digest = _sha256(
        artifact.get("corpusDigest"), "execution corpus corpusDigest"
    )
    if artifact.get("repository") != "magento/magento2":
        raise ValueError("execution corpus repository must be magento/magento2")
    if artifact.get("defaultBranch") != "2.4-develop":
        raise ValueError("execution corpus defaultBranch must be 2.4-develop")
    digest_payload = dict(artifact)
    declared_digest = digest_payload.pop("executionCorpusDigest", None)
    _sha256(declared_digest, "execution corpus executionCorpusDigest")
    if declared_digest != sha256_json(digest_payload):
        raise ValueError("execution corpus digest mismatch")
    assert_label_free_execution_value(artifact, context="execution corpus")

    cases = artifact.get("cases")
    if not isinstance(cases, list) or len(cases) != 50:
        raise ValueError("execution corpus must contain exactly 50 cases")
    case_ids: set[str] = set()
    partitions: Counter[str] = Counter()
    size_bands: Counter[str] = Counter()
    for index, case_value in enumerate(cases):
        field = f"execution corpus cases[{index}]"
        case = _exact_fields(case_value, CASE_FIELDS, field)
        case_id = require_text(case.get("caseId"), f"{field}.caseId")
        if SAFE_CASE_ID.fullmatch(case_id) is None:
            raise ValueError(f"{field}.caseId is not a safe identifier")
        if case_id in case_ids:
            raise ValueError("execution corpus case IDs must be unique")
        case_ids.add(case_id)
        partition = case.get("partition")
        if partition not in {"development", "sealed"}:
            raise ValueError(f"{field}.partition is invalid")
        partitions[str(partition)] += 1
        size_band = case.get("sizeBand")
        if size_band not in {"small", "medium", "large"}:
            raise ValueError(f"{field}.sizeBand is invalid")
        size_bands[str(size_band)] += 1

        snapshot = _exact_fields(
            case.get("snapshot"), SNAPSHOT_FIELDS, f"{field}.snapshot"
        )
        base_sha = require_full_sha(
            snapshot.get("baseSha"), f"{field}.snapshot.baseSha"
        )
        head_sha = require_full_sha(
            snapshot.get("headSha"), f"{field}.snapshot.headSha"
        )
        if base_sha == head_sha:
            raise ValueError(f"{field} base/head snapshots must be distinct")
        file_count = snapshot.get("fileCount")
        if (
            isinstance(file_count, bool)
            or not isinstance(file_count, int)
            or file_count < 3
            or file_count > 80
        ):
            raise ValueError(f"{field}.snapshot.fileCount must be from 3 to 80")
        paths = snapshot.get("changedPaths")
        if (
            not isinstance(paths, list)
            or len(paths) != file_count
            or len(paths) != len(set(paths))
        ):
            raise ValueError(
                f"{field}.snapshot.changedPaths must exactly match fileCount"
            )
        normalized_paths = [
            _safe_path(path, f"{field}.snapshot.changedPaths[{path_index}]")
            for path_index, path in enumerate(paths)
        ]
        if normalized_paths != sorted(normalized_paths):
            raise ValueError(f"{field}.snapshot.changedPaths must be sorted")
        expected_band = (
            "small"
            if file_count <= 10
            else "medium"
            if file_count <= 30
            else "large"
        )
        if size_band != expected_band:
            raise ValueError(f"{field}.sizeBand must be {expected_band}")
        _sha256(snapshot.get("diffSha256"), f"{field}.snapshot.diffSha256")

        replay = _exact_fields(
            case.get("replay"), REPLAY_FIELDS, f"{field}.replay"
        )
        base_ref = require_text(replay.get("baseRef"), f"{field}.replay.baseRef")
        head_ref = require_text(replay.get("headRef"), f"{field}.replay.headRef")
        if base_ref == head_ref:
            raise ValueError(f"{field}.replay refs must be distinct")

    if partitions != {"development": 30, "sealed": 20}:
        raise ValueError(
            "execution corpus partition must contain 30 development and 20 sealed"
        )
    if any(size_bands[band] == 0 for band in ("small", "medium", "large")):
        raise ValueError("execution corpus must contain every size band")
    return {
        "corpusId": corpus_id,
        "corpusDigest": corpus_digest,
        "executionCorpusDigest": declared_digest,
        "cases": 50,
        "partitionCounts": dict(sorted(partitions.items())),
        "sizeBands": dict(sorted(size_bands.items())),
    }


def create_execution_corpus(
    *,
    corpus_path: Path,
    output: Path,
) -> dict[str, Any]:
    corpus = read_json(corpus_path)
    if not isinstance(corpus, Mapping):
        raise ValueError("released corpus must be an object")
    result = build_execution_corpus(corpus, require_paper_ready=True)
    write_json(output, result)
    return result
