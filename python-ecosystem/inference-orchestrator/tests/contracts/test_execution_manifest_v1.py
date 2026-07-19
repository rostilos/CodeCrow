from __future__ import annotations

import asyncio
import json
import random
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from model.dtos import LegacyCompatibility, ReviewRequestDto
from server.queue_consumer import RedisQueueConsumer


RAW_DIFF = "diff --git a/app.py b/app.py\n+print('immutable snapshot')\n"
TRANSPORT_JOB_ID = "redis-transport-job-0001"
EXPECTED_ARTIFACT_MANIFEST_DIGEST = (
    "ee43744de4fc054fd7d21cf124457b2bd0bdde1c8e1109fa90b33ee8df204d96"
)
LEGACY_COMPATIBILITY = {
    "kind": "legacy",
    "deadline": "2026-09-30T00:00:00Z",
}
SHARED_CONTRACT_FIXTURE = (
    Path(__file__).resolve().parents[4]
    / "java-ecosystem"
    / "libs"
    / "analysis-engine"
    / "src"
    / "test"
    / "resources"
    / "contracts"
    / "execution-manifest-v1.json"
)


def _canonical_digest(document: dict[str, object]) -> str:
    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _generated_artifact_id(prefix: str, execution_id: object, content_key: str) -> str:
    identity = f"{execution_id}\x00{content_key}".encode("utf-8")
    return f"{prefix}:{sha256(identity).hexdigest()}"


def _rag_context(index_version: str = "rag-disabled") -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "indexVersion": index_version,
        "parserVersion": "tree-sitter-v1",
        "chunkerVersion": "ast-code-splitter-v1",
        "embeddingVersion": "configured-v1",
    }


def _manifest(**overrides: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "executionId": "execution-pr-42-v1",
        "projectId": 7,
        "repositoryId": "github:codecrow/review-fixture",
        "pullRequestId": 42,
        "baseSha": "a" * 40,
        "headSha": "b" * 40,
        "mergeBaseSha": "c" * 40,
        "diffArtifactId": "diff-artifact-pr-42-v1",
        "diffDigest": sha256(RAW_DIFF.encode("utf-8")).hexdigest(),
        "diffByteLength": len(RAW_DIFF.encode("utf-8")),
        "diffArtifactKind": "raw-diff",
        "diffArtifactProducer": "java-vcs-acquisition",
        "diffArtifactProducerVersion": "p1-01-v1",
        "artifactSchemaVersion": "review-artifact-v1",
        "policyVersion": "candidate-review-v2",
        "creationFence": "creation:00000017",
        "createdAt": "2026-07-15T12:00:00Z",
    }
    supplied_digest = overrides.pop("artifactManifestDigest", None)
    supplied_artifacts = overrides.pop("inputArtifacts", None)
    manifest.update(overrides)
    if supplied_artifacts is not None:
        manifest["inputArtifacts"] = supplied_artifacts
    else:
        rag_context = _rag_context()
        rag_context_bytes = json.dumps(
            rag_context,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        manifest["inputArtifacts"] = [{
            "executionId": manifest["executionId"],
            "artifactId": manifest["diffArtifactId"],
            "contentKey": "pull-request.diff",
            "snapshotSha": manifest["headSha"],
            "contentDigest": manifest["diffDigest"],
            "byteLength": manifest["diffByteLength"],
            "kind": "raw-diff",
            "artifactSchemaVersion": manifest["artifactSchemaVersion"],
            "producer": manifest["diffArtifactProducer"],
            "producerVersion": manifest["diffArtifactProducerVersion"],
        }, {
            "executionId": manifest["executionId"],
            "artifactId": _generated_artifact_id(
                "rag-config",
                manifest["executionId"],
                "rag-execution-config-v1.json",
            ),
            "contentKey": "rag-execution-config-v1.json",
            "snapshotSha": manifest["headSha"],
            "contentDigest": sha256(rag_context_bytes).hexdigest(),
            "byteLength": len(rag_context_bytes),
            "kind": "execution-config",
            "artifactSchemaVersion": manifest["artifactSchemaVersion"],
            "producer": manifest["diffArtifactProducer"],
            "producerVersion": manifest["diffArtifactProducerVersion"],
        }]
        manifest["inputArtifacts"].sort(key=lambda artifact: artifact["artifactId"])
    manifest["artifactManifestDigest"] = supplied_digest or _canonical_digest(manifest)
    return manifest


def _v1_request(
    *,
    manifest: dict[str, object] | None = None,
    raw_diff: str = RAW_DIFF,
    **overrides: object,
) -> dict[str, object]:
    request: dict[str, object] = {
        "projectId": 7,
        "projectVcsWorkspace": "codecrow",
        "projectVcsRepoSlug": "review-fixture",
        "projectWorkspace": "Codecrow",
        "projectNamespace": "codecrow-garden",
        "pullRequestId": 42,
        "aiProvider": "scripted",
        "aiModel": "fixture-v1",
        "aiApiKey": "credential-not-telemetry",
        "analysisType": "PR_REVIEW",
        "vcsProvider": "github",
        "rawDiff": raw_diff,
        "previousCommitHash": "a" * 40,
        "currentCommitHash": "b" * 40,
        "indexVersion": "rag-disabled",
        "ragContext": _rag_context(),
        "executionManifest": manifest if manifest is not None else _manifest(),
    }
    request.update(overrides)
    return request


def _v1_request_with_rag_context(index_version: str) -> dict[str, object]:
    payload = _v1_request(
        indexVersion=index_version,
        ragContext=_rag_context(index_version),
    )
    manifest = payload["executionManifest"]
    manifest.pop("artifactManifestDigest")
    entry = next(
        item
        for item in manifest["inputArtifacts"]
        if item["kind"] == "execution-config"
    )
    config_bytes = json.dumps(
        payload["ragContext"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    entry["contentDigest"] = sha256(config_bytes).hexdigest()
    entry["byteLength"] = len(config_bytes)
    manifest["artifactManifestDigest"] = _canonical_digest(manifest)
    return payload


def _coverage_ledger(
    manifest: dict[str, object] | None = None,
) -> dict[str, object]:
    bound_manifest = manifest or _manifest()
    execution_id = str(bound_manifest["executionId"])
    anchor = {
        "anchorId": sha256(
            f"{execution_id}\0queue-contract-anchor".encode("utf-8")
        ).hexdigest(),
        "executionId": execution_id,
        "parentHunkId": sha256(b"queue-contract-parent").hexdigest(),
        "changeId": sha256(b"queue-contract-change").hexdigest(),
        "kind": "FILE_CHANGE",
        "oldPath": "app.py",
        "newPath": "app.py",
        "oldStart": 0,
        "oldLineCount": 0,
        "newStart": 0,
        "newLineCount": 0,
        "changeStatus": "MODIFY",
        "sourceArtifactId": bound_manifest["diffArtifactId"],
        "sourceDigest": bound_manifest["diffDigest"],
        "mandatory": True,
        "initialState": "PENDING",
        "reasonCode": None,
    }
    ledger: dict[str, object] = {
        "schemaVersion": 1,
        "executionId": execution_id,
        "artifactManifestDigest": bound_manifest["artifactManifestDigest"],
        "diffDigest": bound_manifest["diffDigest"],
        "diffByteLength": bound_manifest["diffByteLength"],
        "anchorCount": 1,
        "anchors": [anchor],
    }
    ledger["ledgerDigest"] = _canonical_digest(ledger)
    return ledger


def _candidate_queue_payload(
    request: dict[str, object] | None = None,
    **overrides: object,
) -> dict[str, object]:
    request_payload = deepcopy(request if request is not None else _v1_request())
    if "coverageLedger" not in request_payload:
        candidate_manifest = request_payload.get("executionManifest")
        bound_manifest = (
            candidate_manifest
            if isinstance(candidate_manifest, dict)
            and {
                "executionId",
                "artifactManifestDigest",
                "diffDigest",
                "diffByteLength",
                "diffArtifactId",
            }.issubset(candidate_manifest)
            else _manifest()
        )
        request_payload["coverageLedger"] = _coverage_ledger(bound_manifest)
    payload: dict[str, object] = {
        "schemaVersion": 2,
        "job_id": TRANSPORT_JOB_ID,
        "request": request_payload,
    }
    payload.update(overrides)
    return payload


def _remove_manifest_field(field: str) -> dict[str, object]:
    request = _v1_request()
    manifest = deepcopy(request["executionManifest"])
    assert isinstance(manifest, dict)
    manifest.pop(field)
    request["executionManifest"] = manifest
    return request


def _legacy_conflict(field: str, value: object) -> dict[str, object]:
    return _v1_request(**{field: value})


def _raw_diff_mismatch() -> dict[str, object]:
    tampered = RAW_DIFF.replace("immutable", "immutablE")
    assert len(tampered.encode("utf-8")) == len(RAW_DIFF.encode("utf-8"))
    return _v1_request(raw_diff=tampered)


def test_shared_java_fixture_is_the_python_v1_contract() -> None:
    fixture = json.loads(SHARED_CONTRACT_FIXTURE.read_text(encoding="utf-8"))

    request = ReviewRequestDto(
        **_v1_request(
            manifest=fixture["manifest"],
            raw_diff=fixture["rawDiff"],
        )
    )

    assert fixture["rawDiff"] == RAW_DIFF
    assert fixture["manifest"] == _manifest()
    assert request.executionManifest.model_dump(mode="json", by_alias=True) == fixture[
        "manifest"
    ]


def test_generated_valid_manifests_preserve_python_round_trip_equality() -> None:
    generator = random.Random(0x50101)

    for sample in range(256):
        base_sha = "".join(
            generator.choice("0123456789abcdef")
            for _ in range(40 if sample % 2 == 0 else 64)
        )
        head_sha = "".join(
            generator.choice("0123456789abcdef")
            for _ in range(64 if sample % 3 == 0 else 40)
        )
        merge_base_sha = "".join(
            generator.choice("0123456789abcdef")
            for _ in range(64 if sample % 5 == 0 else 40)
        )
        project_id = 1 + generator.randrange(1_000_000)
        pull_request_id = 1 + generator.randrange(1_000_000)
        workspace = f"workspace-{sample}"
        repository = f"repository-{generator.randrange(1_000_000)}"
        manifest = _manifest(
            executionId=f"execution:property:{sample}",
            projectId=project_id,
            repositoryId=f"github:{workspace}/{repository}",
            pullRequestId=pull_request_id,
            baseSha=base_sha,
            headSha=head_sha,
            mergeBaseSha=merge_base_sha,
            creationFence=f"creation:property:{sample}",
        )

        request = ReviewRequestDto(
            **_v1_request(
                manifest=manifest,
                projectId=project_id,
                projectVcsWorkspace=workspace,
                projectVcsRepoSlug=repository,
                pullRequestId=pull_request_id,
                previousCommitHash=base_sha,
                currentCommitHash=head_sha,
                indexVersion="rag-disabled",
            )
        )

        assert request.executionManifest.model_dump(mode="json", by_alias=True) == manifest


def _unknown_manifest_field() -> dict[str, object]:
    return _v1_request(manifest=_manifest(unexpectedCoordinate="must-not-be-ignored"))


def _v1_enrichment_request() -> dict[str, object]:
    source_content = "print('bound source π')\n"
    enrichment = {
        "fileContents": [
            {
                "path": "src/app.py",
                "content": source_content,
                "sizeBytes": len(source_content.encode("utf-8")),
                "skipped": False,
                "skipReason": None,
            }
        ],
        "fileMetadata": [],
        "relationships": [],
        "stats": {
            "totalFilesRequested": 1,
            "filesEnriched": 1,
            "filesSkipped": 0,
            "relationshipsFound": 0,
            "totalContentSizeBytes": len(source_content.encode("utf-8")),
            "processingTimeMs": 7,
            "skipReasons": {},
        },
    }
    base = _manifest()
    raw_entry = deepcopy(base["inputArtifacts"])[0]
    source_bytes = source_content.encode("utf-8")
    enrichment_bytes = json.dumps(
        enrichment,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    entries = [
        raw_entry,
        {
            "executionId": base["executionId"],
            "artifactId": _generated_artifact_id(
                "enrichment",
                base["executionId"],
                "pr-enrichment.json",
            ),
            "contentKey": "pr-enrichment.json",
            "snapshotSha": base["headSha"],
            "contentDigest": sha256(enrichment_bytes).hexdigest(),
            "byteLength": len(enrichment_bytes),
            "kind": "pr-enrichment",
            "artifactSchemaVersion": base["artifactSchemaVersion"],
            "producer": base["diffArtifactProducer"],
            "producerVersion": base["diffArtifactProducerVersion"],
        },
        {
            "executionId": base["executionId"],
            "artifactId": _generated_artifact_id(
                "source",
                base["executionId"],
                "src/app.py",
            ),
            "contentKey": "src/app.py",
            "snapshotSha": base["headSha"],
            "contentDigest": sha256(source_bytes).hexdigest(),
            "byteLength": len(source_bytes),
            "kind": "source-file",
            "artifactSchemaVersion": base["artifactSchemaVersion"],
            "producer": base["diffArtifactProducer"],
            "producerVersion": base["diffArtifactProducerVersion"],
        },
    ]
    entries.append(next(
        deepcopy(artifact)
        for artifact in base["inputArtifacts"]
        if artifact["kind"] == "execution-config"
    ))
    entries.sort(key=lambda artifact: artifact["artifactId"])
    manifest = _manifest(inputArtifacts=entries)
    return _v1_request(
        manifest=manifest,
        enrichmentData=enrichment,
        changedFiles=["src/app.py"],
    )


def _v1_skipped_enrichment_request() -> dict[str, object]:
    payload = _v1_enrichment_request()
    enrichment = payload["enrichmentData"]
    file_content = enrichment["fileContents"][0]
    file_content.update(
        {
            "content": None,
            "sizeBytes": 0,
            "skipped": True,
            "skipReason": "file_too_large",
        }
    )
    enrichment["stats"].update(
        {
            "filesEnriched": 0,
            "filesSkipped": 1,
            "totalContentSizeBytes": 0,
            "skipReasons": {"file_too_large": 1},
        }
    )
    enrichment_bytes = json.dumps(
        enrichment,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    manifest = payload["executionManifest"]
    manifest["inputArtifacts"] = [
        artifact
        for artifact in manifest["inputArtifacts"]
        if artifact["kind"] != "source-file"
    ]
    enrichment_entry = next(
        artifact
        for artifact in manifest["inputArtifacts"]
        if artifact["kind"] == "pr-enrichment"
    )
    enrichment_entry["contentDigest"] = sha256(enrichment_bytes).hexdigest()
    enrichment_entry["byteLength"] = len(enrichment_bytes)
    manifest["artifactManifestDigest"] = _canonical_digest(
        {
            key: value
            for key, value in manifest.items()
            if key != "artifactManifestDigest"
        }
    )
    return payload


def _refresh_manifest_digest(manifest: dict[str, object]) -> None:
    manifest["artifactManifestDigest"] = _canonical_digest(
        {
            key: value
            for key, value in manifest.items()
            if key != "artifactManifestDigest"
        }
    )


def _refresh_enrichment_artifact(payload: dict[str, object]) -> None:
    enrichment = payload["enrichmentData"]
    manifest = payload["executionManifest"]
    assert isinstance(enrichment, dict)
    assert isinstance(manifest, dict)
    enrichment_bytes = json.dumps(
        enrichment,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    enrichment_entry = next(
        artifact
        for artifact in manifest["inputArtifacts"]
        if artifact["kind"] == "pr-enrichment"
    )
    enrichment_entry["contentDigest"] = sha256(enrichment_bytes).hexdigest()
    enrichment_entry["byteLength"] = len(enrichment_bytes)
    _refresh_manifest_digest(manifest)


REQUIRED_MANIFEST_FIELDS = (
    "schemaVersion",
    "executionId",
    "projectId",
    "repositoryId",
    "pullRequestId",
    "baseSha",
    "headSha",
    "mergeBaseSha",
    "diffArtifactId",
    "diffDigest",
    "diffByteLength",
    "diffArtifactKind",
    "diffArtifactProducer",
    "diffArtifactProducerVersion",
    "artifactSchemaVersion",
    "policyVersion",
    "creationFence",
    "createdAt",
    "inputArtifacts",
    "artifactManifestDigest",
)


def test_v1_manifest_parses_without_defaulting_or_rewriting_coordinates() -> None:
    manifest = _manifest()

    request = ReviewRequestDto(**_v1_request(manifest=manifest))

    assert request.executionManifest.model_dump(mode="json", by_alias=True) == manifest
    assert (
        request.executionManifest.artifactManifestDigest
        == EXPECTED_ARTIFACT_MANIFEST_DIGEST
    )

    with pytest.raises(ValidationError):
        request.executionManifest.headSha = "d" * 40


@pytest.mark.parametrize("missing_field", REQUIRED_MANIFEST_FIELDS)
def test_v1_manifest_rejects_every_missing_coordinate(missing_field: str) -> None:
    with pytest.raises(ValidationError):
        ReviewRequestDto(**_remove_manifest_field(missing_field))


@pytest.mark.parametrize(
    ("field", "invalid_sha"),
    [
        ("baseSha", "A" * 40),
        ("headSha", "B" * 40),
        ("mergeBaseSha", "C" * 40),
        ("baseSha", "a" * 39),
        ("headSha", "b" * 41),
        ("mergeBaseSha", "not-a-sha"),
    ],
)
def test_v1_manifest_rejects_non_exact_lowercase_shas(
    field: str,
    invalid_sha: str,
) -> None:
    with pytest.raises(ValidationError):
        ReviewRequestDto(**_v1_request(manifest=_manifest(**{field: invalid_sha})))


@pytest.mark.parametrize(
    "created_at",
    [
        "2026-07-15T15:00:00+03:00",
        "2026-07-15T12:00:00.000Z",
        "2026-07-15T12:00:00.123000Z",
        "2026-07-15T12:00:00.123456789Z",
        1_752_580_800,
    ],
)
def test_v1_manifest_rejects_noncanonical_timestamps(created_at: object) -> None:
    with pytest.raises(ValidationError):
        ReviewRequestDto(
            **_v1_request(manifest=_manifest(createdAt=created_at))
        )


@pytest.mark.parametrize(
    "created_at",
    [
        "2026-07-15T12:00:00Z",
        "2026-07-15T12:00:00.123Z",
        "2026-07-15T12:00:00.123456Z",
    ],
)
def test_v1_manifest_accepts_canonical_timestamps(created_at: str) -> None:
    request = ReviewRequestDto(
        **_v1_request(manifest=_manifest(createdAt=created_at))
    )

    assert request.executionManifest.createdAt == created_at


def test_v1_manifest_rejects_calendar_invalid_canonical_timestamp() -> None:
    with pytest.raises(ValidationError, match="valid instant"):
        ReviewRequestDto(
            **_v1_request(
                manifest=_manifest(createdAt="2026-02-30T12:00:00Z")
            )
        )


def test_legacy_compatibility_rejects_non_string_wire_timestamp() -> None:
    with pytest.raises(ValidationError, match="ISO-8601 string"):
        LegacyCompatibility(kind="legacy", deadline=1_752_580_800)


def test_v1_manifest_rejects_noncanonical_artifact_order() -> None:
    payload = _v1_enrichment_request()
    entries = deepcopy(payload["executionManifest"]["inputArtifacts"])
    entries[0], entries[1] = entries[1], entries[0]

    with pytest.raises(ValidationError, match="canonical artifactId order"):
        ReviewRequestDto(
            **_v1_request(manifest=_manifest(inputArtifacts=entries))
        )


@pytest.mark.parametrize(
    ("duplicate_field", "expected_message"),
    [
        ("artifactId", "duplicate artifactId"),
        ("contentKey", "duplicate contentKey"),
    ],
)
def test_v1_manifest_rejects_duplicate_artifact_coordinates(
    duplicate_field: str,
    expected_message: str,
) -> None:
    raw_entry = deepcopy(_manifest()["inputArtifacts"])[0]
    duplicate = deepcopy(raw_entry)
    if duplicate_field == "artifactId":
        duplicate["contentKey"] = "second.diff"
    else:
        duplicate["artifactId"] = "z-second-diff-artifact"

    with pytest.raises(ValidationError, match=expected_message):
        ReviewRequestDto(
            **_v1_request(
                manifest=_manifest(inputArtifacts=[raw_entry, duplicate])
            )
        )


@pytest.mark.parametrize(
    ("artifact_field", "value", "expected_message"),
    [
        ("executionId", "foreign-execution", "another execution"),
        ("snapshotSha", "d" * 40, "another snapshot"),
    ],
)
def test_v1_manifest_rejects_artifact_ownership_conflicts(
    artifact_field: str,
    value: str,
    expected_message: str,
) -> None:
    artifact = deepcopy(_manifest()["inputArtifacts"])[0]
    artifact[artifact_field] = value

    with pytest.raises(ValidationError, match=expected_message):
        ReviewRequestDto(
            **_v1_request(manifest=_manifest(inputArtifacts=[artifact]))
        )


def test_v1_manifest_internal_validator_rejects_artifact_schema_conflict() -> None:
    manifest = ReviewRequestDto(**_v1_request()).executionManifest
    artifact = manifest.inputArtifacts[0].model_copy(
        update={"artifactSchemaVersion": "review-artifact-v2"}
    )
    conflicting = manifest.model_copy(update={"inputArtifacts": (artifact,)})

    with pytest.raises(ValueError, match="schema conflicts"):
        conflicting.verify_artifact_manifest_digest()


def test_v1_manifest_requires_exactly_one_raw_diff_artifact() -> None:
    artifact = deepcopy(_manifest()["inputArtifacts"])[0]
    artifact["kind"] = "source-file"

    with pytest.raises(ValidationError, match="exactly one raw diff"):
        ReviewRequestDto(
            **_v1_request(manifest=_manifest(inputArtifacts=[artifact]))
        )


def test_v1_manifest_rejects_raw_diff_coordinate_conflict() -> None:
    artifact = deepcopy(_manifest()["inputArtifacts"])[0]
    artifact["contentDigest"] = "0" * 64

    with pytest.raises(ValidationError, match="raw diff input artifact conflicts"):
        ReviewRequestDto(
            **_v1_request(manifest=_manifest(inputArtifacts=[artifact]))
        )


def test_v1_manifest_rejects_multiple_enrichment_artifacts() -> None:
    manifest = _manifest()
    raw_entry = deepcopy(manifest["inputArtifacts"])[0]
    enrichment_entries = []
    for suffix in ("a", "b"):
        enrichment_entries.append(
            {
                **deepcopy(raw_entry),
                "artifactId": f"enrichment-{suffix}",
                "contentKey": f"enrichment-{suffix}.json",
                "kind": "pr-enrichment",
            }
        )

    with pytest.raises(ValidationError, match="multiple enrichment documents"):
        ReviewRequestDto(
            **_v1_request(
                manifest=_manifest(
                    inputArtifacts=[raw_entry, *enrichment_entries]
                )
            )
        )


@pytest.mark.parametrize(
    ("version_field", "version"),
    [
        ("schemaVersion", 2),
        ("artifactSchemaVersion", "review-artifact-v2"),
    ],
)
def test_v1_manifest_rejects_unknown_versions(
    version_field: str,
    version: object,
) -> None:
    with pytest.raises(ValidationError):
        ReviewRequestDto(
            **_v1_request(manifest=_manifest(**{version_field: version}))
        )


@pytest.mark.parametrize(
    ("legacy_field", "conflicting_value"),
    [
        ("executionId", "another-execution"),
        ("baseRevision", "d" * 40),
        ("headRevision", "e" * 40),
        ("previousCommitHash", "d" * 40),
        ("currentCommitHash", "e" * 40),
        ("commitHash", "f" * 40),
        ("policyVersion", "another-policy-v1"),
    ],
)
def test_v1_manifest_rejects_conflicting_compatibility_aliases(
    legacy_field: str,
    conflicting_value: str,
) -> None:
    with pytest.raises(ValidationError):
        ReviewRequestDto(**_legacy_conflict(legacy_field, conflicting_value))


def test_v1_manifest_rejects_legacy_compatibility_envelope() -> None:
    with pytest.raises(ValidationError, match="mutually exclusive"):
        ReviewRequestDto(
            **_v1_request(legacyCompatibility=LEGACY_COMPATIBILITY)
        )


@pytest.mark.parametrize("provider", [None, "   "])
def test_v1_manifest_requires_nonblank_vcs_provider(
    provider: str | None,
) -> None:
    with pytest.raises(ValidationError, match="vcsProvider is required"):
        ReviewRequestDto(**_v1_request(vcsProvider=provider))


def test_v1_manifest_rejects_cross_repository_binding() -> None:
    with pytest.raises(ValidationError, match="repositoryId"):
        ReviewRequestDto(
            **_v1_request(projectVcsRepoSlug="another-repository")
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("projectId", 8),
        ("pullRequestId", 43),
    ],
)
def test_v1_manifest_rejects_conflicting_numeric_aliases(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError, match=field):
        ReviewRequestDto(**_v1_request(**{field: value}))


@pytest.mark.parametrize("analysis_type", [None, "BRANCH_ANALYSIS", "pr_review"])
def test_v1_manifest_requires_pr_review_analysis_type(
    analysis_type: str | None,
) -> None:
    payload = _v1_request(analysisType=analysis_type)

    with pytest.raises(ValidationError, match="analysisType"):
        ReviewRequestDto(**payload)


@pytest.mark.parametrize(
    "index_version",
    [
        "rag-commit-" + "b" * 40,
        "rag-commit-" + "a" * 41,
        "main",
        None,
    ],
)
def test_v1_manifest_rejects_index_not_bound_to_base_sha(
    index_version: str | None,
) -> None:
    with pytest.raises(ValidationError, match="indexVersion"):
        ReviewRequestDto(**_v1_request(indexVersion=index_version))


def test_v1_manifest_accepts_index_bound_to_base_sha() -> None:
    request = ReviewRequestDto(
        **_v1_request_with_rag_context("rag-commit-" + "a" * 40)
    )

    assert request.indexVersion == "rag-commit-" + request.executionManifest.baseSha


def test_v1_manifest_rejects_missing_or_unbound_rag_context() -> None:
    missing = _v1_request()
    missing.pop("ragContext")
    with pytest.raises(ValidationError, match="ragContext"):
        ReviewRequestDto(**missing)

    changed = _v1_request()
    changed["ragContext"]["parserVersion"] = "tree-sitter-v99"
    with pytest.raises(ValidationError, match="ragContext"):
        ReviewRequestDto(**changed)


def test_v1_manifest_rejects_rag_context_index_alias_conflict() -> None:
    payload = _v1_request()
    payload["ragContext"]["indexVersion"] = "rag-commit-" + "a" * 40

    with pytest.raises(ValidationError, match="indexVersion"):
        ReviewRequestDto(**payload)


def test_v1_manifest_rejects_raw_diff_digest_mismatch() -> None:
    with pytest.raises(ValidationError):
        ReviewRequestDto(**_raw_diff_mismatch())


def test_v1_manifest_requires_raw_diff_payload() -> None:
    with pytest.raises(ValidationError, match="rawDiff is required"):
        ReviewRequestDto(**_v1_request(raw_diff=None))


def test_v1_manifest_rejects_reconciliation_file_contents() -> None:
    with pytest.raises(ValidationError, match="reconciliationFileContents"):
        ReviewRequestDto(
            **_v1_request(
                reconciliationFileContents={"src/app.py": "unbound content"}
            )
        )


def test_v1_manifest_rejects_raw_diff_byte_length_mismatch() -> None:
    with pytest.raises(ValidationError):
        ReviewRequestDto(
            **_v1_request(
                manifest=_manifest(diffByteLength=len(RAW_DIFF.encode("utf-8")) + 1)
            )
        )


def test_v1_manifest_verifies_every_source_and_enrichment_artifact() -> None:
    request = ReviewRequestDto(**_v1_enrichment_request())

    assert request.enrichmentData.fileContents[0].path == "src/app.py"
    assert {item.kind for item in request.executionManifest.inputArtifacts} == {
        "raw-diff",
        "source-file",
        "pr-enrichment",
        "execution-config",
    }


def test_v1_manifest_inventory_includes_explicitly_skipped_sources() -> None:
    request = ReviewRequestDto(**_v1_skipped_enrichment_request())

    assert request.changedFiles == ["src/app.py"]
    assert request.enrichmentData.fileContents[0].skipped is True
    assert {item.kind for item in request.executionManifest.inputArtifacts} == {
        "raw-diff",
        "pr-enrichment",
        "execution-config",
    }


@pytest.mark.parametrize(
    ("mutation", "expected_message"),
    [
        ("duplicate-path", "duplicate source path"),
        ("invalid-path", "source path is invalid"),
        ("skipped-with-content", "skipped source cannot carry content"),
        ("skipped-without-reason", "explicit reason"),
        ("non-skipped-without-content", "non-skipped source must carry content"),
        ("inexact-size", "sizeBytes is not UTF-8 exact"),
    ],
)
def test_v1_manifest_rejects_invalid_enrichment_file_inventory(
    mutation: str,
    expected_message: str,
) -> None:
    payload = (
        _v1_skipped_enrichment_request()
        if mutation.startswith("skipped-")
        else _v1_enrichment_request()
    )
    file_contents = payload["enrichmentData"]["fileContents"]
    file_content = file_contents[0]
    if mutation == "duplicate-path":
        file_contents.append(deepcopy(file_content))
    elif mutation == "invalid-path":
        file_content["path"] = " \x00"
    elif mutation == "skipped-with-content":
        file_content["content"] = "unexpected"
    elif mutation == "skipped-without-reason":
        file_content["skipReason"] = "   "
    elif mutation == "non-skipped-without-content":
        file_content["content"] = None
    else:
        file_content["sizeBytes"] += 1

    with pytest.raises(ValidationError, match=expected_message):
        ReviewRequestDto(**payload)


def test_v1_manifest_requires_one_enrichment_artifact_for_payload() -> None:
    payload = _v1_enrichment_request()
    manifest = payload["executionManifest"]
    manifest["inputArtifacts"] = [
        artifact
        for artifact in manifest["inputArtifacts"]
        if artifact["kind"] != "pr-enrichment"
    ]
    _refresh_manifest_digest(manifest)

    with pytest.raises(ValidationError, match="requires one manifest artifact"):
        ReviewRequestDto(**payload)


def test_v1_manifest_rejects_noncanonical_enrichment_content_key() -> None:
    payload = _v1_enrichment_request()
    manifest = payload["executionManifest"]
    enrichment_entry = next(
        artifact
        for artifact in manifest["inputArtifacts"]
        if artifact["kind"] == "pr-enrichment"
    )
    enrichment_entry["contentKey"] = "other-enrichment.json"
    _refresh_manifest_digest(manifest)

    with pytest.raises(ValidationError, match="contentKey is invalid"):
        ReviewRequestDto(**payload)


@pytest.mark.parametrize("stats_case", ["missing", "inconsistent"])
def test_v1_manifest_requires_exact_enrichment_accounting(
    stats_case: str,
) -> None:
    payload = _v1_enrichment_request()
    if stats_case == "missing":
        payload["enrichmentData"]["stats"] = None
    else:
        payload["enrichmentData"]["stats"]["totalFilesRequested"] = 2
    _refresh_enrichment_artifact(payload)

    with pytest.raises(ValidationError, match="incomplete file accounting"):
        ReviewRequestDto(**payload)


def test_v1_manifest_rejects_untransmitted_enrichment_payload() -> None:
    payload = _v1_enrichment_request()
    payload["enrichmentData"] = None

    with pytest.raises(ValidationError, match="no request payload"):
        ReviewRequestDto(**payload)


def test_v1_manifest_rejects_untransmitted_source_artifact() -> None:
    payload = _v1_enrichment_request()
    manifest = payload["executionManifest"]
    source_entry = next(
        deepcopy(artifact)
        for artifact in manifest["inputArtifacts"]
        if artifact["kind"] == "source-file"
    )
    source_entry.update(
        {
            "artifactId": _generated_artifact_id(
                "source", manifest["executionId"], "src/untransmitted.py"
            ),
            "contentKey": "src/untransmitted.py",
        }
    )
    manifest["inputArtifacts"].append(source_entry)
    manifest["inputArtifacts"].sort(key=lambda item: item["artifactId"])
    _refresh_manifest_digest(manifest)

    with pytest.raises(ValidationError, match="untransmitted source content"):
        ReviewRequestDto(**payload)


@pytest.mark.parametrize(
    "tamper",
    [
        "source-content",
        "source-entry",
        "source-id",
        "source-producer",
        "enrichment",
    ],
)
def test_v1_manifest_rejects_tampered_or_missing_input_artifacts(
    tamper: str,
) -> None:
    payload = _v1_enrichment_request()
    if tamper == "source-content":
        payload["enrichmentData"]["fileContents"][0]["content"] += "# tampered\n"
        payload["enrichmentData"]["fileContents"][0]["sizeBytes"] = len(
            payload["enrichmentData"]["fileContents"][0]["content"].encode("utf-8")
        )
    elif tamper == "source-entry":
        manifest = payload["executionManifest"]
        manifest["inputArtifacts"] = [
            item for item in manifest["inputArtifacts"]
            if item["kind"] != "source-file"
        ]
        manifest["artifactManifestDigest"] = _canonical_digest(
            {
                key: value
                for key, value in manifest.items()
                if key != "artifactManifestDigest"
            }
        )
    elif tamper in ("source-id", "source-producer"):
        manifest = payload["executionManifest"]
        source_entry = next(
            item
            for item in manifest["inputArtifacts"]
            if item["kind"] == "source-file"
        )
        if tamper == "source-id":
            source_entry["artifactId"] = "source:" + "0" * 64
        else:
            source_entry["producer"] = "another-producer"
        manifest["artifactManifestDigest"] = _canonical_digest(
            {
                key: value
                for key, value in manifest.items()
                if key != "artifactManifestDigest"
            }
        )
    else:
        payload["enrichmentData"]["stats"]["processingTimeMs"] += 1

    with pytest.raises(ValidationError):
        ReviewRequestDto(**payload)


def test_v1_manifest_rejects_byte_length_outside_java_long_range() -> None:
    with pytest.raises(ValidationError):
        ReviewRequestDto(
            **_v1_request(
                manifest=_manifest(diffByteLength=9_223_372_036_854_775_808)
            )
        )


def test_v1_manifest_rejects_input_artifact_length_outside_java_long_range() -> None:
    manifest = _manifest()
    artifact = deepcopy(manifest["inputArtifacts"])[0]
    artifact["byteLength"] = 9_223_372_036_854_775_808
    manifest = _manifest(inputArtifacts=[artifact])

    with pytest.raises(ValidationError):
        ReviewRequestDto(**_v1_request(manifest=manifest))


def test_v1_manifest_applies_java_utf16_content_key_limit() -> None:
    manifest = _manifest()
    artifact = deepcopy(manifest["inputArtifacts"])[0]
    artifact["contentKey"] = "💡" * 513
    manifest = _manifest(inputArtifacts=[artifact])

    with pytest.raises(ValidationError, match="contentKey"):
        ReviewRequestDto(**_v1_request(manifest=manifest))


@pytest.mark.parametrize(
    "candidate_override",
    [
        pytest.param({"analysisMode": "INCREMENTAL"}, id="incremental-mode"),
        pytest.param({"deltaDiff": "+unbound candidate bytes\n"}, id="delta-diff"),
    ],
)
def test_v1_manifest_rejects_unbound_incremental_inputs(
    candidate_override: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ReviewRequestDto(**_v1_request(**candidate_override))


@pytest.mark.parametrize(
    "candidate_override",
    [
        pytest.param(
            {"previousCodeAnalysisIssues": [{"id": "legacy-issue"}]},
            id="previous-analysis-history",
        ),
        pytest.param(
            {"diffSnippets": ["unbound semantic retrieval input"]},
            id="diff-snippets",
        ),
        pytest.param(
            {"deletedFiles": ["src/deleted.py"]},
            id="deleted-files",
        ),
    ],
)
def test_v1_manifest_rejects_unbound_history_and_retrieval_inputs(
    candidate_override: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ReviewRequestDto(**_v1_request(**candidate_override))


@pytest.mark.parametrize(
    "candidate_override",
    [
        pytest.param({"prTitle": "Unbound title"}, id="pull-request-title"),
        pytest.param(
            {"prDescription": "Unbound description"},
            id="pull-request-description",
        ),
        pytest.param(
            {"taskContext": {"task_key": "CC-101"}},
            id="task-context",
        ),
        pytest.param(
            {"taskHistoryContext": "Unbound history"},
            id="task-history",
        ),
        pytest.param(
            {"sourceBranchName": "feature/mutable-name"},
            id="source-branch-label",
        ),
        pytest.param(
            {"targetBranchName": "main"},
            id="target-branch-label",
        ),
        pytest.param({"useMcpTools": True}, id="mcp-exploration"),
        pytest.param(
            {"projectRules": '[{"rule":"unbound"}]'},
            id="project-rules",
        ),
    ],
)
def test_v1_manifest_rejects_unbound_mutable_reasoning_context(
    candidate_override: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ReviewRequestDto(**_v1_request(**candidate_override))


def test_v1_manifest_treats_blank_optional_reasoning_context_as_absent() -> None:
    request = ReviewRequestDto(
        **_v1_request(
            prTitle="   ",
            prDescription="\t",
            taskHistoryContext="\n",
            sourceBranchName=" ",
            targetBranchName="\t",
            projectRules="  ",
        )
    )

    assert request.executionManifest is not None


@pytest.mark.parametrize(
    "changed_files",
    [
        [],
        ["src/replacement.py"],
        ["src/app.py", "src/app.py"],
    ],
)
def test_v1_manifest_rejects_changed_file_inventory_mismatch(
    changed_files: list[str],
) -> None:
    payload = _v1_enrichment_request()
    payload["changedFiles"] = changed_files

    with pytest.raises(ValidationError, match="changedFiles"):
        ReviewRequestDto(**payload)


def test_v1_manifest_rejects_unknown_diff_artifact_kind() -> None:
    with pytest.raises(ValidationError):
        ReviewRequestDto(
            **_v1_request(manifest=_manifest(diffArtifactKind="review-output"))
        )


def test_v1_manifest_rejects_artifact_manifest_digest_mismatch() -> None:
    manifest = _manifest()
    manifest["creationFence"] = "creation:00000018"

    with pytest.raises(ValidationError):
        ReviewRequestDto(**_v1_request(manifest=manifest))


def test_v1_manifest_rejects_unknown_nested_fields() -> None:
    with pytest.raises(ValidationError):
        ReviewRequestDto(**_unknown_manifest_field())


@pytest.mark.asyncio(loop_scope="function")
async def test_queue_preserves_v1_manifest_and_keeps_transport_identity_distinct() -> None:
    manifest = _manifest()
    review_service = MagicMock()
    review_service.process_review_request = AsyncMock(
        return_value={"result": {"comment": "ok", "issues": []}}
    )
    consumer = RedisQueueConsumer(review_service)
    consumer._publish_event = AsyncMock()

    await consumer._handle_job(
        json.dumps(_candidate_queue_payload(_v1_request(manifest=manifest)))
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    request = review_service.process_review_request.await_args.args[0]
    assert request.executionManifest.model_dump(mode="json", by_alias=True) == manifest
    assert request.executionManifest.executionId != TRANSPORT_JOB_ID
    assert request.executionId != TRANSPORT_JOB_ID


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    "mutate_payload",
    [
        pytest.param(
            lambda payload: payload.pop("schemaVersion"),
            id="missing-envelope-version",
        ),
        pytest.param(
            lambda payload: payload.__setitem__("schemaVersion", 1),
            id="retired-envelope-version",
        ),
        pytest.param(
            lambda payload: payload.__setitem__("schemaVersion", "2"),
            id="coerced-envelope-version",
        ),
        pytest.param(
            lambda payload: payload.__setitem__("unexpectedEnvelopeField", True),
            id="unknown-envelope-field",
        ),
    ],
)
async def test_queue_rejects_invalid_candidate_envelope_before_review_service(
    mutate_payload: Callable[[dict[str, object]], object],
) -> None:
    review_service = MagicMock()
    review_service.process_review_request = AsyncMock()
    consumer = RedisQueueConsumer(review_service)
    consumer._publish_event = AsyncMock()
    payload = _candidate_queue_payload()
    mutate_payload(payload)

    await consumer._handle_job(json.dumps(payload))

    review_service.process_review_request.assert_not_awaited()
    error_event = consumer._publish_event.await_args.args[1]
    manifest = payload["request"]["executionManifest"]
    assert error_event["type"] == "error"
    assert error_event["executionId"] == manifest["executionId"]
    assert error_event["artifactManifestDigest"] == manifest["artifactManifestDigest"]
    assert error_event["message"] == "Input validation error"
    assert error_event["reasonCode"] == "input_validation_error"


def _remove_execution_manifest(request: dict[str, object]) -> None:
    request.pop("executionManifest")
    request.pop("ragContext", None)


def _remove_artifact_manifest_digest(request: dict[str, object]) -> None:
    manifest = deepcopy(request["executionManifest"])
    assert isinstance(manifest, dict)
    manifest.pop("artifactManifestDigest")
    request["executionManifest"] = manifest


def _set_unknown_schema_version(request: dict[str, object]) -> None:
    request["executionManifest"] = _manifest(schemaVersion=2)


def _set_conflicting_base(request: dict[str, object]) -> None:
    request["previousCommitHash"] = "d" * 40


def _set_raw_diff_mismatch(request: dict[str, object]) -> None:
    request["rawDiff"] = RAW_DIFF + "+print('tampered')\n"


def _set_unknown_nested_field(request: dict[str, object]) -> None:
    request["executionManifest"] = _manifest(unexpectedCoordinate="ignored")


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    "make_invalid",
    [
        pytest.param(_remove_execution_manifest, id="missing-v1-manifest"),
        pytest.param(
            _remove_artifact_manifest_digest,
            id="missing-artifact-manifest-digest",
        ),
        pytest.param(_set_unknown_schema_version, id="unknown-schema-version"),
        pytest.param(_set_conflicting_base, id="conflicting-legacy-base"),
        pytest.param(_set_raw_diff_mismatch, id="raw-diff-digest-mismatch"),
        pytest.param(_set_unknown_nested_field, id="unknown-nested-field"),
    ],
)
async def test_queue_rejects_invalid_v1_before_review_service(
    make_invalid: Callable[[dict[str, object]], None],
) -> None:
    review_service = MagicMock()
    review_service.process_review_request = AsyncMock()
    consumer = RedisQueueConsumer(review_service)
    consumer._publish_event = AsyncMock()
    request = _v1_request()
    make_invalid(request)

    await consumer._handle_job(
        json.dumps(_candidate_queue_payload(request))
    )

    review_service.process_review_request.assert_not_awaited()
    error_events = [
        call.args[1]
        for call in consumer._publish_event.await_args_list
        if call.args[1].get("type") == "error"
    ]
    assert len(error_events) == 1
    assert error_events[0]["message"] == "Input validation error"
    assert error_events[0]["reasonCode"] == "input_validation_error"


@pytest.mark.asyncio(loop_scope="function")
async def test_queue_rejects_stale_source_digest_before_review_service() -> None:
    review_service = MagicMock()
    review_service.process_review_request = AsyncMock()
    consumer = RedisQueueConsumer(review_service)
    consumer._publish_event = AsyncMock()
    request = _v1_enrichment_request()
    source = request["enrichmentData"]["fileContents"][0]
    source["content"] = source["content"].replace("π", "λ")
    _refresh_enrichment_artifact(request)

    await consumer._handle_job(json.dumps(_candidate_queue_payload(request)))

    review_service.process_review_request.assert_not_awaited()
    error_event = consumer._publish_event.await_args.args[1]
    assert error_event["type"] == "error"
    assert error_event["message"] == "Input validation error"
    assert error_event["reasonCode"] == "input_validation_error"


@pytest.mark.asyncio(loop_scope="function")
async def test_queue_validation_error_preserves_independently_valid_manifest_identity() -> None:
    review_service = MagicMock()
    review_service.process_review_request = AsyncMock()
    consumer = RedisQueueConsumer(review_service)
    consumer._publish_event = AsyncMock()
    request = _v1_request(previousCommitHash="d" * 40)

    await consumer._handle_job(
        json.dumps(_candidate_queue_payload(request))
    )

    manifest = request["executionManifest"]
    error_event = consumer._publish_event.await_args.args[1]
    assert error_event["type"] == "error"
    assert error_event["executionId"] == manifest["executionId"]
    assert (
        error_event["artifactManifestDigest"]
        == manifest["artifactManifestDigest"]
    )


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize("missing_field", ["kind", "deadline"])
async def test_queue_rejects_legacy_without_complete_bounded_compatibility(
    missing_field: str,
) -> None:
    review_service = MagicMock()
    review_service.process_review_request = AsyncMock()
    consumer = RedisQueueConsumer(review_service)
    consumer._publish_event = AsyncMock()
    compatibility = dict(LEGACY_COMPATIBILITY)
    compatibility.pop(missing_field)
    legacy_request = _v1_request()
    legacy_request.pop("executionManifest")
    legacy_request.pop("ragContext")
    legacy_request["legacyCompatibility"] = compatibility

    await consumer._handle_job(
        json.dumps({"job_id": TRANSPORT_JOB_ID, "request": legacy_request})
    )

    review_service.process_review_request.assert_not_awaited()
    error_events = [
        call.args[1]
        for call in consumer._publish_event.await_args_list
        if call.args[1].get("type") == "error"
    ]
    assert len(error_events) == 1
    assert error_events[0]["message"] == "Input validation error"
    assert error_events[0]["reasonCode"] == "input_validation_error"


@pytest.mark.asyncio(loop_scope="function")
async def test_queue_keeps_unversioned_legacy_compatibility_path() -> None:
    review_service = MagicMock()
    review_service.process_review_request = AsyncMock(
        return_value={"result": {"comment": "legacy-compatible", "issues": []}}
    )
    consumer = RedisQueueConsumer(review_service)
    consumer._publish_event = AsyncMock()
    legacy_request = _v1_request()
    legacy_request.pop("executionManifest")
    legacy_request.pop("ragContext")
    legacy_request["legacyCompatibility"] = dict(LEGACY_COMPATIBILITY)

    await consumer._handle_job(
        json.dumps({"job_id": TRANSPORT_JOB_ID, "request": legacy_request})
    )
    await asyncio.sleep(0)

    review_service.process_review_request.assert_awaited_once()
    request = review_service.process_review_request.await_args.args[0]
    assert request.executionManifest is None
    assert request.legacyCompatibility.kind == "legacy"
