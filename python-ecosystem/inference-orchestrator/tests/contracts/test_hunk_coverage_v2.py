"""Contracts for the durable hunk-ledger queue boundary.

Versioned candidate traffic has one production shape: v2 with mandatory
coverage work. The unversioned legacy adapter is tested at the consumer edge.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

import model.dtos as dto_module
from model.multi_stage import ReviewFile, ReviewPlan
from service.review.review_service import ReviewService


EXECUTION_ID = "execution-pr-42-coverage-v2"
MANIFEST_DIGEST_PLACEHOLDER = "f" * 64
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
MERGE_BASE_SHA = "c" * 40
TRANSPORT_JOB_ID = "redis-transport-coverage-v2"
DIFF_ARTIFACT_ID = "diff-artifact-pr-42-coverage-v2"
RAW_DIFF = (
    "diff --git a/src/ok.py b/src/ok.py\n"
    "--- a/src/ok.py\n"
    "+++ b/src/ok.py\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)


def _canonical_digest(document: dict[str, object]) -> str:
    return sha256(
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _coverage_api():
    """Load the intended VS-05 API at test time so RED is not collection-wide."""

    return importlib.import_module("model.coverage")


def _coverage_service_api():
    return importlib.import_module("service.review.coverage")


def _anchor_id(ordinal: int, execution_id: str = EXECUTION_ID) -> str:
    """Anchor IDs are the stable SHA-256 identity, not display labels."""

    return sha256(f"{execution_id}\0hunk:{ordinal:03d}".encode("utf-8")).hexdigest()


def _parent_hunk_id(ordinal: int) -> str:
    return sha256(f"parent-hunk:{ordinal:03d}".encode("utf-8")).hexdigest()


def _change_id(ordinal: int) -> str:
    return sha256(f"change:{ordinal:03d}".encode("utf-8")).hexdigest()


def _anchor(
    ordinal: int,
    *,
    execution_id: str = EXECUTION_ID,
    path: str | None = None,
    source_digest: str | None = None,
    initial_state: str = "PENDING",
    reason_code: str | None = None,
) -> dict[str, object]:
    path = path or f"src/file-{ordinal}.py"
    return {
        "anchorId": _anchor_id(ordinal, execution_id),
        "executionId": execution_id,
        "parentHunkId": _parent_hunk_id(ordinal),
        "changeId": _change_id(ordinal),
        "kind": "TEXT_HUNK",
        "oldPath": path,
        "newPath": path,
        "oldStart": ordinal,
        "oldLineCount": 1,
        "newStart": ordinal,
        "newLineCount": 1,
        "changeStatus": "MODIFY",
        "sourceArtifactId": DIFF_ARTIFACT_ID,
        "sourceDigest": source_digest or sha256(RAW_DIFF.encode("utf-8")).hexdigest(),
        "mandatory": True,
        "initialState": initial_state,
        "reasonCode": reason_code,
    }


def _ledger(
    *,
    raw_diff: str = RAW_DIFF,
    anchors: list[dict[str, object]] | None = None,
    execution_id: str = EXECUTION_ID,
    artifact_manifest_digest: str = MANIFEST_DIGEST_PLACEHOLDER,
) -> dict[str, object]:
    source_digest = sha256(raw_diff.encode("utf-8")).hexdigest()
    canonical_anchors = anchors if anchors is not None else [
        _anchor(1, source_digest=source_digest),
        _anchor(2, source_digest=source_digest),
    ]
    canonical_anchors = sorted(canonical_anchors, key=lambda anchor: anchor["anchorId"])
    ledger: dict[str, object] = {
        "schemaVersion": 1,
        "executionId": execution_id,
        "artifactManifestDigest": artifact_manifest_digest,
        "diffDigest": source_digest,
        "diffByteLength": len(raw_diff.encode("utf-8")),
        "anchorCount": len(canonical_anchors),
        "anchors": canonical_anchors,
    }
    ledger["ledgerDigest"] = _canonical_digest(ledger)
    return ledger


def _refresh_ledger_digest(ledger: dict[str, object]) -> None:
    material = {
        key: value
        for key, value in ledger.items()
        if key != "ledgerDigest"
    }
    ledger["ledgerDigest"] = _canonical_digest(material)


def _manifest(raw_diff: str = RAW_DIFF) -> dict[str, object]:
    raw_digest = sha256(raw_diff.encode("utf-8")).hexdigest()
    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "executionId": EXECUTION_ID,
        "projectId": 7,
        "repositoryId": "github:codecrow/review-fixture",
        "pullRequestId": 42,
        "baseSha": BASE_SHA,
        "headSha": HEAD_SHA,
        "mergeBaseSha": MERGE_BASE_SHA,
        "diffArtifactId": DIFF_ARTIFACT_ID,
        "diffDigest": raw_digest,
        "diffByteLength": len(raw_diff.encode("utf-8")),
        "diffArtifactKind": "raw-diff",
        "diffArtifactProducer": "java-vcs-acquisition",
        "diffArtifactProducerVersion": "analysis-engine-v1",
        "artifactSchemaVersion": "review-artifact-v1",
        "policyVersion": "candidate-review-v2",
        "creationFence": "creation:coverage:0001",
        "createdAt": "2026-07-16T12:00:00Z",
    }
    manifest["inputArtifacts"] = [
        {
            "executionId": EXECUTION_ID,
            "artifactId": manifest["diffArtifactId"],
            "contentKey": "pull-request.diff",
            "snapshotSha": HEAD_SHA,
            "contentDigest": raw_digest,
            "byteLength": len(raw_diff.encode("utf-8")),
            "kind": "raw-diff",
            "artifactSchemaVersion": "review-artifact-v1",
            "producer": "java-vcs-acquisition",
            "producerVersion": "analysis-engine-v1",
        }
    ]
    manifest["artifactManifestDigest"] = _canonical_digest(manifest)
    return manifest


def _request(
    *,
    raw_diff: str = RAW_DIFF,
    ledger: dict[str, object] | None = None,
    include_coverage: bool = True,
) -> dict[str, object]:
    manifest = _manifest(raw_diff)
    request: dict[str, object] = {
        "projectId": 7,
        "projectVcsWorkspace": "codecrow",
        "projectVcsRepoSlug": "review-fixture",
        "projectWorkspace": "CodeCrow",
        "projectNamespace": "codecrow-garden",
        "pullRequestId": 42,
        "aiProvider": "scripted",
        "aiModel": "fixture-v2",
        "aiApiKey": "credential-not-coverage",
        "analysisType": "PR_REVIEW",
        "vcsProvider": "github",
        "changedFiles": [],
        "deletedFiles": [],
        "diffSnippets": [],
        "rawDiff": raw_diff,
        "analysisMode": "FULL",
        "previousCommitHash": BASE_SHA,
        "currentCommitHash": HEAD_SHA,
        "indexVersion": "rag-disabled",
        "executionManifest": manifest,
    }
    if include_coverage:
        supplied = ledger or _ledger(
            raw_diff=raw_diff,
            artifact_manifest_digest=str(manifest["artifactManifestDigest"]),
        )
        request["coverageLedger"] = supplied
    return request


def _envelope_v2(request: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "job_id": TRANSPORT_JOB_ID,
        "request": request or _request(),
    }


def _parse_envelope(payload: dict[str, object]):
    parser = getattr(dto_module, "parse_review_queue_envelope")
    return parser(payload)


def _validated_ledger(document: dict[str, object]):
    model = getattr(_coverage_api(), "CoverageLedgerV1")
    return model.model_validate(document)


def _tracker(document: dict[str, object]):
    tracker_type = getattr(_coverage_service_api(), "ExecutionCoverageTracker")
    return tracker_type(_validated_ledger(document))


def test_v2_is_the_only_supported_versioned_candidate_envelope() -> None:
    v2 = _parse_envelope(_envelope_v2())
    assert type(v2).__name__ == "ReviewQueueEnvelopeV2"
    assert v2.schemaVersion == 2
    assert v2.request.coverageLedger.anchorCount == 2

    for retired_version in (1, 3):
        with pytest.raises(ValueError, match="unsupported queue schemaVersion"):
            _parse_envelope({
                "schemaVersion": retired_version,
                "job_id": TRANSPORT_JOB_ID,
                "request": _request(include_coverage=False),
            })


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda payload: payload["request"].pop("coverageLedger"),
            id="missing-required-coverage-ledger",
        ),
        pytest.param(
            lambda payload: payload.__setitem__("unexpectedEnvelopeField", True),
            id="unknown-envelope-field",
        ),
    ],
)
def test_v2_envelope_rejects_missing_coverage_or_unknown_outer_fields(mutate) -> None:
    payload = _envelope_v2()
    mutate(payload)

    with pytest.raises(ValidationError):
        _parse_envelope(payload)


def test_ledger_is_bound_to_manifest_and_exact_raw_diff_provenance() -> None:
    request = _request()
    parsed = dto_module.ReviewRequestDto(**request)
    assert parsed.coverageLedger.executionId == parsed.executionManifest.executionId
    assert (
        parsed.coverageLedger.artifactManifestDigest
        == parsed.executionManifest.artifactManifestDigest
    )
    assert parsed.coverageLedger.diffDigest == parsed.executionManifest.diffDigest
    assert parsed.coverageLedger.diffByteLength == parsed.executionManifest.diffByteLength

    tampered = deepcopy(request)
    ledger = tampered["coverageLedger"]
    assert isinstance(ledger, dict)
    ledger["diffDigest"] = "0" * 64
    _refresh_ledger_digest(ledger)

    with pytest.raises(ValidationError, match="diffDigest|provenance"):
        dto_module.ReviewRequestDto(**tampered)


def test_ledger_requires_canonical_anchor_order_and_digest() -> None:
    request = _request()
    ledger = request["coverageLedger"]
    assert isinstance(ledger, dict)
    anchors = ledger["anchors"]
    assert isinstance(anchors, list)

    reordered = deepcopy(request)
    reordered_ledger = reordered["coverageLedger"]
    assert isinstance(reordered_ledger, dict)
    reordered_ledger["anchors"] = list(reversed(anchors))
    _refresh_ledger_digest(reordered_ledger)
    with pytest.raises(ValidationError, match="canonical|anchorId|order"):
        dto_module.ReviewRequestDto(**reordered)

    changed_without_new_digest = deepcopy(request)
    changed_ledger = changed_without_new_digest["coverageLedger"]
    assert isinstance(changed_ledger, dict)
    changed_anchors = changed_ledger["anchors"]
    assert isinstance(changed_anchors, list)
    first = changed_anchors[0]
    assert isinstance(first, dict)
    first["newStart"] = 999
    with pytest.raises(ValidationError, match="ledgerDigest|digest"):
        dto_module.ReviewRequestDto(**changed_without_new_digest)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        pytest.param(
            "duplicate",
            "duplicate|anchorId",
            id="duplicate-anchor",
        ),
        pytest.param(
            "missing",
            "anchorCount|missing",
            id="missing-anchor",
        ),
        pytest.param(
            "foreign",
            "executionId|foreign",
            id="foreign-anchor",
        ),
    ],
)
def test_duplicate_missing_and_foreign_anchors_are_rejected(
    mutation: str,
    message: str,
) -> None:
    request = _request()
    ledger = request["coverageLedger"]
    assert isinstance(ledger, dict)
    anchors = ledger["anchors"]
    assert isinstance(anchors, list)

    if mutation == "duplicate":
        anchors[1] = deepcopy(anchors[0])
        _refresh_ledger_digest(ledger)
    elif mutation == "missing":
        anchors.pop()
        # Retain the durable declared count while refreshing the content digest.
        _refresh_ledger_digest(ledger)
    else:
        foreign = anchors[0]
        assert isinstance(foreign, dict)
        foreign["executionId"] = "foreign-execution"
        _refresh_ledger_digest(ledger)

    with pytest.raises(ValidationError, match=message):
        dto_module.ReviewRequestDto(**request)


def test_tracker_returns_every_anchor_once_and_mixed_terminal_work_is_partial() -> None:
    manifest_digest = "d" * 64
    document = _ledger(
        anchors=[_anchor(1), _anchor(2), _anchor(3)],
        artifact_manifest_digest=manifest_digest,
    )
    tracker = _tracker(document)
    anchor_ids = [_anchor_id(ordinal) for ordinal in (1, 2, 3)]

    tracker.mark_examined([anchor_ids[0]])
    tracker.mark_unsupported([anchor_ids[1]], reason_code="binary_diff")
    tracker.mark_failed([anchor_ids[2]], reason_code="stage1_timeout")
    report = tracker.finalize()

    assert report.analysisState == "PARTIAL"
    assert report.total == 3
    assert report.examined == 1
    assert report.unsupported == 1
    assert report.failed == 1
    assert report.incomplete == 0
    assert report.pending == 0
    assert [item.anchorId for item in report.dispositions] == sorted(anchor_ids)
    assert len({item.anchorId for item in report.dispositions}) == 3
    assert report.schemaVersion == document["schemaVersion"]
    assert report.executionId == document["executionId"]
    assert report.artifactManifestDigest == document["artifactManifestDigest"]
    assert report.diffDigest == document["diffDigest"]
    assert report.diffByteLength == document["diffByteLength"]
    assert report.ledgerDigest == document["ledgerDigest"]


def test_partial_empty_review_cannot_claim_no_issues_found() -> None:
    tracker = _tracker(_ledger(anchors=[_anchor(1), _anchor(2)]))
    tracker.mark_examined([_anchor_id(1)])
    tracker.mark_failed([_anchor_id(2)], reason_code="stage1_timeout")

    result = ReviewService._attach_coverage_receipt(
        {"result": {"comment": "No issues found.", "issues": []}},
        tracker,
    )

    assert result["result"]["analysisState"] == "PARTIAL"
    assert result["result"]["issues"] == []
    assert result["result"]["comment"] == (
        "Analysis is incomplete because mandatory diff coverage was not completed."
    )


def test_tracker_is_idempotent_for_same_receipt_but_rejects_conflicting_terminal_state() -> None:
    document = _ledger(anchors=[_anchor(1)])
    tracker = _tracker(document)
    transition_error = getattr(_coverage_service_api(), "CoverageTransitionError")
    anchor_id = _anchor_id(1)

    tracker.mark_examined([anchor_id])
    tracker.mark_examined([anchor_id])
    with pytest.raises(transition_error):
        tracker.mark_failed([anchor_id], reason_code="late_failure")

    report = tracker.finalize()
    assert len(report.dispositions) == 1
    assert report.dispositions[0].state == "EXAMINED"


def test_segmented_file_anchor_waits_for_every_segment_and_any_failure_wins() -> None:
    document = _ledger(anchors=[_anchor(1, path="src/oversized.py")])
    tracker = _tracker(document)
    anchor_id = _anchor_id(1)
    batches = [
        [{
            "file": ReviewFile(path="src/oversized.py", risk_level="MEDIUM"),
            "priority": "MEDIUM",
            "_diff_chunk_index": 1,
            "_diff_chunk_total": 2,
        }],
        [{
            "file": ReviewFile(path="src/oversized.py", risk_level="MEDIUM"),
            "priority": "MEDIUM",
            "_diff_chunk_index": 2,
            "_diff_chunk_total": 2,
        }],
    ]

    tracker.bind_batches(batches)

    assert batches[0][0]["_coverage_anchor_ids"] == [anchor_id]
    assert batches[1][0]["_coverage_anchor_ids"] == [anchor_id]

    tracker.mark_batch_examined([anchor_id])
    assert tracker.open_mandatory_total == 1

    tracker.mark_batch_failed([anchor_id], reason_code="stage1_batch_failed")
    report = tracker.finalize()

    assert report.analysisState == "FAILED"
    assert report.examined == 0
    assert report.failed == 1
    assert report.dispositions[0].state == "FAILED"
    assert report.dispositions[0].reasonCode == "stage1_batch_failed"


def test_zero_anchor_ledger_is_authoritative_empty() -> None:
    document = _ledger(raw_diff="", anchors=[])
    report = _tracker(document).finalize()

    assert report.analysisState == "EMPTY"
    assert report.total == 0
    assert report.dispositions == []
    assert report.examined == report.unsupported == report.failed == 0
    assert report.incomplete == report.pending == 0


@pytest.mark.asyncio(loop_scope="function")
async def test_empty_v2_ledger_returns_empty_without_llm_or_mcp_work() -> None:
    raw_diff = ""
    manifest = _manifest(raw_diff)
    ledger = _ledger(
        raw_diff=raw_diff,
        anchors=[],
        artifact_manifest_digest=str(manifest["artifactManifestDigest"]),
    )
    request = _request(raw_diff=raw_diff, ledger=ledger)
    service = object.__new__(ReviewService)
    service._review_semaphore = asyncio.Semaphore(1)
    service._process_review = AsyncMock(
        side_effect=AssertionError("empty ledger must not enter the LLM pipeline")
    )
    service._create_llm = MagicMock(
        side_effect=AssertionError("empty ledger must not create an LLM")
    )
    service._create_mcp_client = MagicMock(
        side_effect=AssertionError("empty ledger must not create an MCP client")
    )

    result = await service.process_review_request(dto_module.ReviewRequestDto(**request))

    service._process_review.assert_not_awaited()
    service._create_llm.assert_not_called()
    service._create_mcp_client.assert_not_called()
    assert result["result"]["analysisState"] == "EMPTY"
    assert result["result"]["comment"]
    assert result["result"]["issues"] == []
    receipt = result["result"]["coverageReceipt"]
    assert receipt["executionId"] == ledger["executionId"]
    assert receipt["artifactManifestDigest"] == ledger["artifactManifestDigest"]
    assert receipt["diffDigest"] == ledger["diffDigest"]
    assert receipt["diffByteLength"] == ledger["diffByteLength"]
    assert receipt["ledgerDigest"] == ledger["ledgerDigest"]
    assert receipt["analysisState"] == "EMPTY"
    assert receipt["total"] == 0
    assert receipt["dispositions"] == []


def _stage_request(paths: list[str]):
    return MagicMock(
        deltaDiff=None,
        rawDiff=RAW_DIFF,
        taskContext=None,
        enrichmentData=None,
        projectRules=None,
        previousCodeAnalysisIssues=[],
        changedFiles=paths,
        deletedFiles=[],
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_stage1_empty_success_is_examined_and_batch_exception_is_failed() -> None:
    stage1 = importlib.import_module(
        "service.review.orchestrator.stage_1_file_review"
    )
    document = _ledger(
        anchors=[
            _anchor(1, path="src/clean.py"),
            _anchor(2, path="src/timed-out.py"),
            _anchor(3, path="assets/logo.bin"),
        ]
    )
    tracker = _tracker(document)
    clean_anchor_id = _anchor_id(1)
    timed_out_anchor_id = _anchor_id(2)
    binary_anchor_id = _anchor_id(3)
    tracker.mark_unsupported([binary_anchor_id], reason_code="binary_diff")
    batches = [
        [{
            "file": ReviewFile(path="src/clean.py", risk_level="MEDIUM"),
            "priority": "MEDIUM",
            "_coverage_anchor_ids": [clean_anchor_id],
        }],
        [{
            "file": ReviewFile(path="src/timed-out.py", risk_level="MEDIUM"),
            "priority": "MEDIUM",
            "_coverage_anchor_ids": [timed_out_anchor_id],
        }],
    ]

    async def run_batch(batch_idx, *_args, **_kwargs):
        if batch_idx == 1:
            return []  # Valid LLM output: examined, with no findings.
        raise asyncio.TimeoutError("injected Stage 1 timeout")

    with patch.object(
        stage1,
        "create_smart_batches_wrapper",
        new=AsyncMock(return_value=batches),
    ), patch.object(
        stage1,
        "_expand_oversized_diff_batches",
        side_effect=lambda candidate_batches, *_args, **_kwargs: candidate_batches,
    ), patch.object(
        stage1,
        "_review_batch_with_timing",
        new=AsyncMock(side_effect=run_batch),
    ):
        issues = await stage1.execute_stage_1_file_reviews(
            MagicMock(),
            _stage_request(["src/clean.py", "src/timed-out.py"]),
            ReviewPlan(analysis_summary="candidate v2", file_groups=[]),
            rag_client=None,
            coverage_tracker=tracker,
        )

    assert issues == []
    report = tracker.finalize()
    states = {item.anchorId: item.state for item in report.dispositions}
    reasons = {item.anchorId: item.reasonCode for item in report.dispositions}
    assert states == {
        clean_anchor_id: "EXAMINED",
        timed_out_anchor_id: "FAILED",
        binary_anchor_id: "UNSUPPORTED",
    }
    assert reasons[timed_out_anchor_id] == "stage1_batch_failed"
    assert report.analysisState == "PARTIAL"


@pytest.mark.asyncio(loop_scope="function")
async def test_repeated_stage1_parse_failure_is_typed_failure_not_clean_empty() -> None:
    stage1 = importlib.import_module(
        "service.review.orchestrator.stage_1_file_review"
    )
    batch_failure = getattr(stage1, "Stage1BatchFailure")
    request = _stage_request(["src/malformed.py"])
    prepared = stage1._build_stage_1_prepared_context(
        request,
        None,
        is_incremental=False,
    )
    batch = [{
        "file": ReviewFile(path="src/malformed.py", risk_level="MEDIUM"),
        "priority": "MEDIUM",
        "_coverage_anchor_ids": [_anchor_id(1)],
    }]

    with patch.object(
        stage1,
        "_invoke_stage_1_batch_llm",
        new=AsyncMock(return_value=[]),
    ):
        assert await stage1.review_file_batch(
            MagicMock(name="valid-empty-llm"),
            request,
            batch,
            rag_client=None,
            prepared_context=prepared,
            fail_closed=True,
        ) == []

    with patch.object(
        stage1,
        "_invoke_stage_1_batch_llm",
        new=AsyncMock(side_effect=[None, None]),
    ):
        with pytest.raises(batch_failure) as raised:
            await stage1.review_file_batch(
                MagicMock(name="capped-llm"),
                request,
                batch,
                rag_client=None,
                prepared_context=prepared,
                fallback_llm=MagicMock(name="uncapped-llm"),
                fail_closed=True,
            )

    assert raised.value.reason_code == "stage1_response_invalid"
