from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import api.routers.review as review_router
from api.routers.review import _drain_queue_until_final, review_endpoint
from model.dtos import ReviewRequestDto
from server.queue_consumer import RedisQueueConsumer
from server.stdin_handler import StdinHandler
from service.review.orchestrator.mcp_tool_executor import McpToolExecutor
from service.review.orchestrator.orchestrator import MultiStageReviewOrchestrator
from service.review.orchestrator.stage_1_file_review import (
    create_smart_batches_wrapper,
    fetch_batch_rag_context,
)
from service.review.orchestrator.stage_2_cross_file import (
    prefetch_stage_2_cross_module_context,
)
from service.review.review_service import ReviewService
from service.review.execution_context import (
    ExecutionContextBindingError,
    ExecutionEventBindingError,
    bind_execution_context,
    bind_manifest_file_revision,
    context_snapshot_v1,
)
from service.review.telemetry import (
    CandidateCounts,
    CoverageCounts,
    TerminalOutcome,
    UsageCounts,
    trace_document,
)


RAW_DIFF = "diff --git a/app.py b/app.py\n+print('bound snapshot')\n"
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
TRANSPORT_JOB_ID = "redis-transport-only-0001"


def _rag_context() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "indexVersion": "rag-disabled",
        "parserVersion": "tree-sitter-v1",
        "chunkerVersion": "ast-code-splitter-v1",
        "embeddingVersion": "configured-v1",
    }


def _canonical_digest(document: dict[str, object]) -> str:
    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _manifest() -> dict[str, object]:
    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "executionId": "execution-pr-42-bound",
        "projectId": 7,
        "repositoryId": "github:codecrow/review-fixture",
        "pullRequestId": 42,
        "baseSha": BASE_SHA,
        "headSha": HEAD_SHA,
        "mergeBaseSha": "c" * 40,
        "diffArtifactId": "diff-artifact-pr-42-bound",
        "diffDigest": sha256(RAW_DIFF.encode("utf-8")).hexdigest(),
        "diffByteLength": len(RAW_DIFF.encode("utf-8")),
        "diffArtifactKind": "raw-diff",
        "diffArtifactProducer": "java-vcs-acquisition",
        "diffArtifactProducerVersion": "p1-01-v1",
        "artifactSchemaVersion": "review-artifact-v1",
        "policyVersion": "candidate-review-v2",
        "creationFence": "creation:00000042",
        "createdAt": "2026-07-15T12:00:00Z",
    }
    manifest["inputArtifacts"] = [
        {
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
        }
    ]
    config_bytes = json.dumps(
        _rag_context(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    config_key = "rag-execution-config-v1.json"
    manifest["inputArtifacts"].append({
        "executionId": manifest["executionId"],
        "artifactId": "rag-config:" + sha256(
            f"{manifest['executionId']}\0{config_key}".encode("utf-8")
        ).hexdigest(),
        "contentKey": config_key,
        "snapshotSha": manifest["headSha"],
        "contentDigest": sha256(config_bytes).hexdigest(),
        "byteLength": len(config_bytes),
        "kind": "execution-config",
        "artifactSchemaVersion": manifest["artifactSchemaVersion"],
        "producer": manifest["diffArtifactProducer"],
        "producerVersion": manifest["diffArtifactProducerVersion"],
    })
    manifest["inputArtifacts"].sort(key=lambda artifact: artifact["artifactId"])
    manifest["artifactManifestDigest"] = _canonical_digest(manifest)
    return manifest


def _request_payload(
    *,
    manifest: bool = True,
    compatibility_deadline: str | None = None,
    bind_legacy_aliases: bool = False,
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
        "changedFiles": [],
        "diffSnippets": [],
        "rawDiff": RAW_DIFF,
        "indexVersion": "rag-disabled",
    }
    if manifest:
        request["executionManifest"] = _manifest()
        request["ragContext"] = _rag_context()
    else:
        request.update(
            {
                "sourceBranchName": "feature/mutable-name",
                "targetBranchName": "main",
            }
        )
    if compatibility_deadline is not None:
        request["legacyCompatibility"] = {
            "kind": "legacy",
            "deadline": compatibility_deadline,
        }
    if bind_legacy_aliases:
        request.update(
            {
                "executionId": "execution-pr-42-bound",
                "baseRevision": BASE_SHA,
                "headRevision": HEAD_SHA,
                "previousCommitHash": BASE_SHA,
                "currentCommitHash": HEAD_SHA,
                "commitHash": HEAD_SHA,
                "policyVersion": "candidate-review-v2",
            }
        )
    return request


def _request_payload_with_bound_review_context() -> dict[str, object]:
    context = {
        "schemaVersion": 2,
        "reviewApproach": "CLASSIC",
        "prTitle": "Fix cross-file authorization",
        "prDescription": "Keep the service and repository checks aligned.",
        "prAuthor": "review-author",
        "taskContext": {"taskKey": "CC-42", "summary": "Authorization fix"},
        "taskHistoryContext": "A prior CC-42 change introduced the repository check.",
        "sourceBranchName": "feature/cc-42",
        "targetBranchName": "main",
        "projectRules": '[{"name":"Protect authorization boundaries"}]',
    }
    enrichment = {
        "fileContents": [],
        "fileMetadata": [],
        "relationships": [],
        "stats": {
            "totalFilesRequested": 0,
            "filesEnriched": 0,
            "filesSkipped": 0,
            "relationshipsFound": 0,
            "totalContentSizeBytes": 0,
            "processingTimeMs": 0,
            "skipReasons": {},
        },
        "reviewContext": context,
    }
    enrichment_bytes = json.dumps(
        enrichment,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    manifest = _manifest()
    manifest.pop("artifactManifestDigest")
    content_key = "pr-enrichment.json"
    manifest["inputArtifacts"].append({
        "executionId": manifest["executionId"],
        "artifactId": "enrichment:" + sha256(
            f"{manifest['executionId']}\0{content_key}".encode("utf-8")
        ).hexdigest(),
        "contentKey": content_key,
        "snapshotSha": manifest["headSha"],
        "contentDigest": sha256(enrichment_bytes).hexdigest(),
        "byteLength": len(enrichment_bytes),
        "kind": "pr-enrichment",
        "artifactSchemaVersion": manifest["artifactSchemaVersion"],
        "producer": manifest["diffArtifactProducer"],
        "producerVersion": manifest["diffArtifactProducerVersion"],
    })
    manifest["inputArtifacts"].sort(key=lambda artifact: artifact["artifactId"])
    manifest["artifactManifestDigest"] = _canonical_digest(manifest)

    request = _request_payload()
    request.update(context)
    request["executionManifest"] = manifest
    request["enrichmentData"] = enrichment
    return request


def test_exact_snapshot_uses_manifest_bound_versions_not_worker_environment(
    monkeypatch,
):
    payload = _request_payload()
    payload["ragContext"] = {
        **_rag_context(),
        "parserVersion": "bound-parser-v7",
        "chunkerVersion": "bound-chunker-v5",
        "embeddingVersion": "bound-embedding-v3",
    }
    manifest = payload["executionManifest"]
    manifest.pop("artifactManifestDigest")
    config = next(
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
    config["contentDigest"] = sha256(config_bytes).hexdigest()
    config["byteLength"] = len(config_bytes)
    manifest["artifactManifestDigest"] = _canonical_digest(manifest)
    monkeypatch.setenv("RAG_PARSER_VERSION", "mutable-parser")
    monkeypatch.setenv("RAG_CHUNKER_VERSION", "mutable-chunker")
    monkeypatch.setenv("RAG_EMBEDDING_VERSION", "mutable-embedding")

    request = ReviewRequestDto(**payload)
    snapshot = context_snapshot_v1(request)

    assert snapshot["parser_version"] == "bound-parser-v7"
    assert snapshot["chunker_version"] == "bound-chunker-v5"
    assert snapshot["embedding_version"] == "bound-embedding-v3"


def _coverage_ledger(manifest: dict[str, object]) -> dict[str, object]:
    execution_id = str(manifest["executionId"])
    anchor = {
        "anchorId": sha256(
            f"{execution_id}\0queue-boundary-app.py".encode("utf-8")
        ).hexdigest(),
        "executionId": execution_id,
        "parentHunkId": sha256(b"queue-boundary-parent-hunk").hexdigest(),
        "changeId": sha256(b"queue-boundary-change").hexdigest(),
        "kind": "FILE_CHANGE",
        "oldPath": "app.py",
        "newPath": "app.py",
        "oldStart": 0,
        "oldLineCount": 0,
        "newStart": 0,
        "newLineCount": 0,
        "changeStatus": "MODIFY",
        "sourceArtifactId": manifest["diffArtifactId"],
        "sourceDigest": manifest["diffDigest"],
        "mandatory": True,
        "initialState": "PENDING",
        "reasonCode": None,
    }
    ledger: dict[str, object] = {
        "schemaVersion": 1,
        "executionId": execution_id,
        "artifactManifestDigest": manifest["artifactManifestDigest"],
        "diffDigest": manifest["diffDigest"],
        "diffByteLength": manifest["diffByteLength"],
        "anchorCount": 1,
        "anchors": [anchor],
    }
    ledger["ledgerDigest"] = _canonical_digest(ledger)
    return ledger


def _candidate_queue_payload() -> str:
    request = _request_payload()
    manifest = request["executionManifest"]
    assert isinstance(manifest, dict)
    request["coverageLedger"] = _coverage_ledger(manifest)
    return json.dumps({
        "schemaVersion": 2,
        "job_id": TRANSPORT_JOB_ID,
        "request": request,
    })


def _assert_manifest_bound(request: ReviewRequestDto) -> None:
    manifest = request.executionManifest
    assert manifest is not None
    assert request.executionId == manifest.executionId
    assert request.baseRevision == manifest.baseSha
    assert request.headRevision == manifest.headSha
    assert request.previousCommitHash == manifest.baseSha
    assert request.currentCommitHash == manifest.headSha
    assert request.commitHash == manifest.headSha
    assert request.sourceBranchName == manifest.headSha
    assert request.targetBranchName == manifest.baseSha
    assert request.policyVersion == manifest.policyVersion


def test_review_context_is_manifest_bound_and_survives_identity_binding() -> None:
    payload = _request_payload_with_bound_review_context()

    request = ReviewRequestDto(**payload)
    bound = bind_execution_context(request)

    assert bound.prTitle == "Fix cross-file authorization"
    assert bound.prAuthor == "review-author"
    assert bound.taskContext["taskKey"] == "CC-42"
    assert bound.sourceBranchName == "feature/cc-42"
    assert bound.targetBranchName == "main"
    assert "Protect authorization boundaries" in bound.projectRules
    assert bound.enrichmentData.reviewContext.prTitle == bound.prTitle
    assert bind_manifest_file_revision(bound, "feature/cc-42") == HEAD_SHA
    assert bind_manifest_file_revision(bound, "main") == BASE_SHA

    tampered = json.loads(json.dumps(payload))
    tampered["prTitle"] = "Unbound replacement title"
    with pytest.raises(ValueError, match="prTitle conflicts with bound reviewContext"):
        ReviewRequestDto(**tampered)


def test_review_approach_cannot_drift_from_the_manifest_bound_context() -> None:
    payload = _request_payload_with_bound_review_context()
    payload["reviewApproach"] = "AGENTIC"

    with pytest.raises(
        ValueError, match="reviewApproach conflicts with bound reviewContext"
    ):
        ReviewRequestDto(**payload)


def test_exact_agentic_request_accepts_only_manifest_bound_previous_findings() -> None:
    payload = _request_payload_with_bound_review_context()
    previous_finding = {
        "id": "issue-42",
        "type": "security",
        "severity": "HIGH",
        "title": "Authorization bypass",
        "reason": "The endpoint does not verify resource ownership.",
        "suggestedFixDescription": "Verify ownership before returning the resource.",
        "suggestedFixDiff": None,
        "file": "src/auth.py",
        "line": 42,
        "branch": "feature/cc-42",
        "pullRequestId": "42",
        "status": "open",
        "category": "SECURITY",
        "prVersion": 1,
        "resolvedDescription": None,
        "resolvedByCommit": None,
        "resolvedInAnalysisId": None,
        "codeSnippet": "return resource",
    }
    enrichment = payload["enrichmentData"]
    assert isinstance(enrichment, dict)
    review_context = enrichment["reviewContext"]
    assert isinstance(review_context, dict)
    review_context["reviewApproach"] = "AGENTIC"
    review_context["previousFindings"] = [previous_finding]

    enrichment_bytes = json.dumps(
        enrichment,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    manifest = payload["executionManifest"]
    assert isinstance(manifest, dict)
    enrichment_entry = next(
        artifact
        for artifact in manifest["inputArtifacts"]
        if artifact["kind"] == "pr-enrichment"
    )
    enrichment_entry["contentDigest"] = sha256(enrichment_bytes).hexdigest()
    enrichment_entry["byteLength"] = len(enrichment_bytes)
    manifest_without_digest = {
        key: value
        for key, value in manifest.items()
        if key != "artifactManifestDigest"
    }
    manifest["artifactManifestDigest"] = _canonical_digest(manifest_without_digest)

    payload.update({
        "reviewApproach": "AGENTIC",
        "agenticRepository": {
            "schemaVersion": 1,
            "workspaceKey": "d" * 64,
            "snapshotSha": HEAD_SHA,
            "contentDigest": "e" * 64,
            "byteLength": 1024,
        },
    })
    request = ReviewRequestDto(**payload)

    assert request.previousCodeAnalysisIssues == []
    assert request.enrichmentData.reviewContext.previousFindings[0].id == "issue-42"

    injected = json.loads(json.dumps(payload))
    injected["previousCodeAnalysisIssues"] = [previous_finding]
    with pytest.raises(
        ValueError, match="previousCodeAnalysisIssues are not bound"
    ):
        ReviewRequestDto(**injected)


def test_context_free_manifest_rejects_injected_pr_author() -> None:
    payload = _request_payload()
    payload["prAuthor"] = "unbound-prompt-injection"

    with pytest.raises(ValueError, match="prAuthor is not bound by executionManifest"):
        ReviewRequestDto(**payload)


@pytest.mark.asyncio(loop_scope="function")
async def test_queue_binds_only_manifest_identity_and_emits_it_on_final() -> None:
    review_service = MagicMock()
    review_service.process_review_request = AsyncMock(
        return_value={"result": {"comment": "ok", "issues": []}}
    )
    consumer = RedisQueueConsumer(review_service)
    consumer._publish_event = AsyncMock()

    await consumer._handle_job(_candidate_queue_payload())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    bound = review_service.process_review_request.await_args.args[0]
    _assert_manifest_bound(bound)
    manifest = bound.executionManifest
    assert manifest.executionId != TRANSPORT_JOB_ID

    final_events = [
        call.args[1]
        for call in consumer._publish_event.await_args_list
        if call.args[1].get("type") == "final"
    ]
    assert final_events == [
        {
            "type": "final",
            "executionId": manifest.executionId,
            "artifactManifestDigest": manifest.artifactManifestDigest,
            "result": {"comment": "ok", "issues": []},
        }
    ]


@pytest.mark.asyncio(loop_scope="function")
async def test_queue_error_terminal_keeps_manifest_identity() -> None:
    review_service = MagicMock()
    review_service.process_review_request = AsyncMock(
        return_value={"result": {"status": "error", "message": "candidate failed"}}
    )
    consumer = RedisQueueConsumer(review_service)
    consumer._publish_event = AsyncMock()

    await consumer._handle_job(_candidate_queue_payload())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    manifest = review_service.process_review_request.await_args.args[0].executionManifest
    error_events = [
        call.args[1]
        for call in consumer._publish_event.await_args_list
        if call.args[1].get("type") == "error"
    ]
    assert error_events == [
        {
            "type": "error",
            "message": "Review processing failed",
            "reasonCode": "review_processing_failed",
            "executionId": manifest.executionId,
            "artifactManifestDigest": manifest.artifactManifestDigest,
        }
    ]


@pytest.mark.asyncio(loop_scope="function")
async def test_queue_forwards_valid_manifest_bound_progress_without_rewriting() -> None:
    produced: dict[str, object] = {}

    async def process(request, event_callback):
        manifest = request.executionManifest
        produced.update(
            {
                "type": "progress",
                "percent": 25,
                "message": "candidate progress",
                "executionId": manifest.executionId,
                "artifactManifestDigest": manifest.artifactManifestDigest,
            }
        )
        event_callback(produced)
        return {"result": {"comment": "ok", "issues": []}}

    review_service = MagicMock()
    review_service.process_review_request = AsyncMock(side_effect=process)
    consumer = RedisQueueConsumer(review_service)
    consumer._publish_event = AsyncMock()

    await consumer._handle_job(_candidate_queue_payload())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    progress_events = [
        call.args[1]
        for call in consumer._publish_event.await_args_list
        if call.args[1].get("type") == "progress"
    ]
    assert progress_events == [produced]


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    "identity_case",
    [
        "missing_execution",
        "missing_digest",
        "conflicting_execution",
        "conflicting_digest",
        "malformed_digest",
    ],
)
async def test_queue_rejects_invalid_progress_identity_without_laundering(
    identity_case: str,
) -> None:
    async def process(request, event_callback):
        manifest = request.executionManifest
        event = {
            "type": "progress",
            "percent": 25,
            "executionId": manifest.executionId,
            "artifactManifestDigest": manifest.artifactManifestDigest,
        }
        if identity_case == "missing_execution":
            event.pop("executionId")
        elif identity_case == "missing_digest":
            event.pop("artifactManifestDigest")
        elif identity_case == "conflicting_execution":
            event["executionId"] = "foreign-execution"
        elif identity_case == "conflicting_digest":
            event["artifactManifestDigest"] = "0" * 64
        else:
            event["artifactManifestDigest"] = "NOT-A-SHA256"
        event_callback(event)
        return {"result": {"comment": "must not complete", "issues": []}}

    review_service = MagicMock()
    review_service.process_review_request = AsyncMock(side_effect=process)
    consumer = RedisQueueConsumer(review_service)
    consumer._publish_event = AsyncMock()

    await consumer._handle_job(_candidate_queue_payload())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    events = [call.args[1] for call in consumer._publish_event.await_args_list]
    assert not any(event.get("type") == "progress" for event in events)
    error_events = [event for event in events if event.get("type") == "error"]
    assert len(error_events) == 1
    assert error_events[0]["message"] == "Input validation error"
    assert error_events[0]["reasonCode"] == "input_validation_error"
    manifest = review_service.process_review_request.await_args.args[0].executionManifest
    assert error_events[0]["executionId"] == manifest.executionId
    assert (
        error_events[0]["artifactManifestDigest"]
        == manifest.artifactManifestDigest
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_review_service_binds_trusted_progress_before_egress() -> None:
    observed: list[dict[str, object]] = []
    service = object.__new__(ReviewService)
    service._review_semaphore = asyncio.Semaphore(1)
    service._create_telemetry_recorder = MagicMock(
        return_value=(None, MagicMock())
    )

    async def process_review(*, event_callback, **_kwargs):
        ReviewService._emit_event(
            event_callback,
            {"type": "progress", "percent": 50},
        )
        return {"result": {"issues": []}}

    service._process_review = AsyncMock(side_effect=process_review)
    service._attach_terminal_telemetry = MagicMock(
        side_effect=lambda **kwargs: kwargs["result"]
    )

    await service.process_review_request(
        ReviewRequestDto(**_request_payload()),
        observed.append,
    )

    manifest = ReviewRequestDto(**_request_payload()).executionManifest
    assert observed == [{
        "type": "progress",
        "percent": 50,
        "executionId": manifest.executionId,
        "artifactManifestDigest": manifest.artifactManifestDigest,
    }]


def test_review_service_propagates_execution_event_binding_failure() -> None:
    def reject(_event):
        raise ExecutionEventBindingError("foreign execution")

    with pytest.raises(ExecutionEventBindingError, match="foreign execution"):
        ReviewService._emit_event(reject, {"type": "progress"})


async def _streamed_events(response) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8")
        events.append(json.loads(chunk))
    return events


def _http_request(review_service, *, streaming: bool = True):
    return SimpleNamespace(
        headers={
            "accept": "application/x-ndjson" if streaming else "application/json"
        },
        app=SimpleNamespace(
            state=SimpleNamespace(review_service=review_service)
        ),
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_deprecated_http_stream_forwards_only_exact_candidate_identity() -> None:
    produced: dict[str, object] = {}

    async def process(request, event_callback):
        manifest = request.executionManifest
        produced.update({
            "type": "progress",
            "percent": 75,
            "executionId": manifest.executionId,
            "artifactManifestDigest": manifest.artifactManifestDigest,
        })
        event_callback(produced)
        return {"result": {"issues": []}}

    review_service = MagicMock()
    review_service.process_review_request = AsyncMock(side_effect=process)
    response = await review_endpoint(
        ReviewRequestDto(**_request_payload()),
        _http_request(review_service),
    )

    events = await _streamed_events(response)
    assert [event["type"] for event in events] == ["status", "progress", "final"]
    assert events[1] == produced
    manifest = ReviewRequestDto(**_request_payload()).executionManifest
    assert all(event["executionId"] == manifest.executionId for event in events)
    assert all(
        event["artifactManifestDigest"] == manifest.artifactManifestDigest
        for event in events
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_deprecated_http_stream_rejects_unbound_candidate_progress() -> None:
    async def process(_request, event_callback):
        event_callback({"type": "progress", "percent": 75})
        return {"result": {"issues": []}}

    review_service = MagicMock()
    review_service.process_review_request = AsyncMock(side_effect=process)
    response = await review_endpoint(
        ReviewRequestDto(**_request_payload()),
        _http_request(review_service),
    )

    events = await _streamed_events(response)
    assert [event["type"] for event in events] == ["status", "error"]
    assert "executionId" in events[-1]["message"]
    manifest = ReviewRequestDto(**_request_payload()).executionManifest
    assert events[-1]["executionId"] == manifest.executionId
    assert (
        events[-1]["artifactManifestDigest"]
        == manifest.artifactManifestDigest
    )


class _QueueFullOnce(asyncio.Queue):
    def __init__(self):
        super().__init__()
        self._reject_next_nowait = True

    def put_nowait(self, item):
        if self._reject_next_nowait:
            self._reject_next_nowait = False
            raise asyncio.QueueFull
        return super().put_nowait(item)


@pytest.mark.asyncio(loop_scope="function")
async def test_deprecated_http_stream_drops_queue_full_progress() -> None:
    async def process(_request, event_callback):
        event_callback(
            {
                "type": "progress",
                "executionId": _manifest()["executionId"],
                "artifactManifestDigest": _manifest()["artifactManifestDigest"],
            }
        )
        return {"result": {"issues": []}}

    review_service = MagicMock()
    review_service.process_review_request = AsyncMock(side_effect=process)
    with patch.object(review_router.asyncio, "Queue", _QueueFullOnce):
        response = await review_endpoint(
            ReviewRequestDto(**_request_payload()),
            _http_request(review_service),
        )
        events = await _streamed_events(response)

    assert [event["type"] for event in events] == ["status", "final"]


class _TimeoutQueue:
    def __init__(self, remaining):
        self.remaining = list(remaining)

    async def get(self):
        raise asyncio.TimeoutError

    def get_nowait(self):
        if self.remaining:
            return self.remaining.pop(0)
        raise asyncio.QueueEmpty


class _SequencedDone:
    def __init__(self, *states: bool):
        self.states = list(states)

    def done(self) -> bool:
        return self.states.pop(0)


@pytest.mark.asyncio(loop_scope="function")
async def test_stream_drain_timeout_covers_terminal_and_nonterminal_remainders() -> None:
    queue = _TimeoutQueue(
        [
            {"type": "progress"},
            {"type": "final"},
        ]
    )
    events = [
        event
        async for event in _drain_queue_until_final(
            queue,
            _SequencedDone(True, True),
        )
    ]

    assert events == [{"type": "progress"}, {"type": "final"}]


@pytest.mark.asyncio(loop_scope="function")
async def test_stream_drain_continues_when_task_state_changes_after_timeout() -> None:
    queue = _TimeoutQueue([{"type": "progress"}])
    events = [
        event
        async for event in _drain_queue_until_final(
            queue,
            _SequencedDone(False, True, False, True, True),
        )
    ]

    assert events == [{"type": "progress"}]


@pytest.mark.asyncio(loop_scope="function")
async def test_stream_drain_yields_trailing_event_after_terminal() -> None:
    queue = asyncio.Queue()
    queue.put_nowait({"type": "final"})
    queue.put_nowait({"type": "trailing"})

    with patch.object(review_router.asyncio, "sleep", new=AsyncMock()):
        events = [
            event
            async for event in _drain_queue_until_final(
                queue,
                _SequencedDone(True),
            )
        ]

    assert events == [{"type": "final"}, {"type": "trailing"}]


@pytest.mark.asyncio(loop_scope="function")
async def test_deprecated_http_rejects_expired_legacy_before_service() -> None:
    review_service = MagicMock()
    review_service.process_review_request = AsyncMock()

    response = await review_endpoint(
        ReviewRequestDto(**_legacy_boundary_payload("expired")),
        _http_request(review_service, streaming=False),
    )

    review_service.process_review_request.assert_not_awaited()
    assert response.result["status"] == "error"
    assert "legacyCompatibility" in response.result["comment"]


def test_stdin_binds_manifest_before_review_service(capsys) -> None:
    review_service = MagicMock()
    review_service.process_review_request = AsyncMock(
        return_value={"result": {"issues": []}}
    )
    handler = object.__new__(StdinHandler)
    handler.review_service = review_service
    handler.read_request_from_stdin = MagicMock(return_value=_request_payload())

    handler.process_stdin_request()

    bound = review_service.process_review_request.await_args.args[0]
    _assert_manifest_bound(bound)
    assert json.loads(capsys.readouterr().out)["result"] == {"issues": []}


def test_stdin_handler_constructs_review_service() -> None:
    with patch("server.stdin_handler.ReviewService") as review_service_type:
        handler = StdinHandler()

    assert handler.review_service is review_service_type.return_value


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", None),
        ("  \n", None),
        ('{"projectId": 7}', {"projectId": 7}),
    ],
)
def test_stdin_reader_handles_empty_and_valid_json(raw, expected) -> None:
    handler = object.__new__(StdinHandler)
    with patch("server.stdin_handler.sys.stdin") as stdin:
        stdin.read.return_value = raw
        assert handler.read_request_from_stdin() == expected


def test_stdin_reader_reports_invalid_json(capsys) -> None:
    handler = object.__new__(StdinHandler)
    with patch("server.stdin_handler.sys.stdin") as stdin:
        stdin.read.return_value = "{invalid-json"
        assert handler.read_request_from_stdin() is None

    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "Failed reading JSON from stdin"
    assert output["exception"]


def test_stdin_process_reports_missing_request(capsys) -> None:
    handler = object.__new__(StdinHandler)
    handler.review_service = MagicMock()
    handler.read_request_from_stdin = MagicMock(return_value=None)

    handler.process_stdin_request()

    assert json.loads(capsys.readouterr().out) == {
        "error": "No input request provided (expecting JSON on stdin)"
    }


@pytest.mark.asyncio(loop_scope="function")
async def test_direct_review_service_binds_manifest_before_processing() -> None:
    service = object.__new__(ReviewService)
    service._review_semaphore = asyncio.Semaphore(1)
    service._create_telemetry_recorder = MagicMock(
        return_value=(None, MagicMock())
    )
    service._process_review = AsyncMock(return_value={"result": {"issues": []}})
    service._attach_terminal_telemetry = MagicMock(
        return_value={"result": {"issues": []}}
    )

    await service.process_review_request(ReviewRequestDto(**_request_payload()))

    bound = service._process_review.await_args.kwargs["request"]
    _assert_manifest_bound(bound)


@pytest.mark.asyncio(loop_scope="function")
async def test_manifest_review_does_not_query_unbound_rag_coordinates() -> None:
    request = ReviewRequestDto(**_request_payload())
    service = object.__new__(ReviewService)
    service.rag_cache = MagicMock()
    service.rag_client = MagicMock()
    service.rag_client.get_pr_context = AsyncMock()

    assert await service._fetch_rag_context(request, None) is None

    assert request.get_rag_branch() == HEAD_SHA
    assert request.get_rag_base_branch() == BASE_SHA
    service.rag_cache.get.assert_not_called()
    service.rag_cache.set.assert_not_called()
    service.rag_client.get_pr_context.assert_not_awaited()


@pytest.mark.asyncio(loop_scope="function")
async def test_legacy_review_keeps_existing_rag_query_coordinates() -> None:
    request = ReviewRequestDto(
        **_request_payload(
            manifest=False,
            compatibility_deadline="2026-09-30T00:00:00Z",
        )
    )
    service = object.__new__(ReviewService)
    service.rag_cache = MagicMock()
    service.rag_cache.get.return_value = None
    service.rag_client = MagicMock()
    service.rag_client.get_pr_context = AsyncMock(return_value=None)

    await service._fetch_rag_context(request, None)

    rag_call = service.rag_client.get_pr_context.await_args.kwargs
    assert rag_call["workspace"] == "Codecrow"
    assert rag_call["project"] == "codecrow-garden"
    assert rag_call["branch"] == "feature/mutable-name"
    assert rag_call["base_branch"] == "main"


def test_legacy_caller_cannot_extend_the_server_compatibility_sunset() -> None:
    request = ReviewRequestDto(
        **_request_payload(
            manifest=False,
            compatibility_deadline="2999-09-30T00:00:00Z",
        )
    )

    with pytest.raises(ExecutionContextBindingError, match="sunset"):
        bind_execution_context(
            request,
            now=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio(loop_scope="function")
async def test_manifest_review_cleanup_is_scoped_to_exact_execution() -> None:
    request = bind_execution_context(ReviewRequestDto(**_request_payload()))
    rag_client = MagicMock()
    rag_client.index_pr_files = AsyncMock()
    rag_client.delete_pr_files = AsyncMock()
    orchestrator = MultiStageReviewOrchestrator(
        MagicMock(), MagicMock(), rag_client=rag_client
    )

    await orchestrator._index_pr_files(request, MagicMock())
    orchestrator._pr_number = request.pullRequestId
    orchestrator._pr_indexed = True
    await orchestrator._cleanup_pr_files(request)

    rag_client.index_pr_files.assert_not_awaited()
    rag_client.delete_pr_files.assert_awaited_once()
    cleanup = rag_client.delete_pr_files.await_args.kwargs
    assert cleanup["workspace"] == "codecrow"
    assert cleanup["project"] == "review-fixture"
    assert cleanup["execution_id"] == request.executionManifest.executionId
    assert cleanup["head_sha"] == request.executionManifest.headSha
    assert orchestrator._pr_number is None
    assert orchestrator._pr_indexed is False


@pytest.mark.asyncio(loop_scope="function")
async def test_manifest_review_queries_only_exact_snapshot_context() -> None:
    request = bind_execution_context(ReviewRequestDto(**_request_payload()))
    rag_client = MagicMock()
    rag_client.get_deterministic_context = AsyncMock(
        return_value={"context": {"chunks": []}}
    )
    rag_client.get_pr_context = AsyncMock(
        return_value={"context": {"relevant_code": []}}
    )
    rag_client.search_for_duplicates = AsyncMock(return_value=[])

    exact_context = await fetch_batch_rag_context(
        rag_client,
        request,
        batch_file_paths=["app.py"],
        batch_diff_snippets=["+print('bound')"],
    )
    assert exact_context["relevant_code"] == []
    assert exact_context["related_context_pack_v1"]["mode"] == "exact"
    gap_codes = {
        gap["code"]
        for gap in exact_context["related_context_pack_v1"]["gaps"]
    }
    assert "exact_base_index_unavailable" in gap_codes
    assert "related_context_empty" in gap_codes
    stage_2_context = await prefetch_stage_2_cross_module_context(
        rag_client,
        request,
    )
    stage_2_pack = stage_2_context["related_context_pack_v1"]
    assert stage_2_pack["mode"] == "exact"
    assert stage_2_pack["execution_id"] == request.executionManifest.executionId
    assert stage_2_pack["items"] == []
    assert "related_context_empty" in {
        gap["code"] for gap in stage_2_pack["gaps"]
    }

    deterministic = rag_client.get_deterministic_context.await_args.kwargs
    semantic = rag_client.get_pr_context.await_args.kwargs
    assert deterministic["snapshot"]["base_sha"] == BASE_SHA
    assert deterministic["snapshot"]["head_sha"] == HEAD_SHA
    assert deterministic["execution_id"] == request.executionManifest.executionId
    assert semantic["snapshot"] == deterministic["snapshot"]
    assert semantic["workspace"] == "codecrow"
    assert semantic["project"] == "review-fixture"
    duplicate = rag_client.search_for_duplicates.await_args.kwargs
    assert duplicate["snapshot"] == deterministic["snapshot"]
    assert duplicate["execution_id"] == request.executionManifest.executionId


@pytest.mark.asyncio(loop_scope="function")
async def test_manifest_batching_uses_exact_graph_coordinates() -> None:
    request = bind_execution_context(
        ReviewRequestDto(**_request_payload_with_bound_review_context())
    )
    create_batches = AsyncMock(return_value=[])
    rag_client = MagicMock()

    with patch(
        "service.review.orchestrator.stage_1_file_review.create_smart_batches_async",
        new=create_batches,
    ):
        assert await create_smart_batches_wrapper(
            [], None, request, rag_client, pr_indexed=True
        ) == []

    call = create_batches.await_args.kwargs
    assert call["workspace"] == "codecrow"
    assert call["project"] == "review-fixture"
    assert call["rag_client"] is rag_client
    assert call["branches"] == ["feature/cc-42", "main"]
    assert call["snapshot"]["base_sha"] == BASE_SHA
    assert call["snapshot"]["head_sha"] == HEAD_SHA
    assert call["execution_id"] == request.executionManifest.executionId
    assert call["pr_number"] == request.pullRequestId
    assert call["pr_changed_files"] == request.changedFiles


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("requested_branch", "expected_sha"),
    [
        (HEAD_SHA, HEAD_SHA),
        (BASE_SHA, BASE_SHA),
    ],
)
async def test_mcp_file_reads_replace_manifest_branch_refs_with_exact_shas(
    requested_branch: str,
    expected_sha: str,
) -> None:
    request = ReviewRequestDto(**_request_payload())
    client = MagicMock()
    client.session.call_tool = AsyncMock(
        return_value=SimpleNamespace(content=[])
    )
    executor = McpToolExecutor(client, request, "stage_1")

    await executor.execute_tool(
        "getBranchFileContent",
        {"branch": requested_branch, "filePath": "app.py"},
    )

    assert client.session.call_tool.await_args.args[1]["branch"] == expected_sha


@pytest.mark.asyncio(loop_scope="function")
async def test_mcp_file_reads_reject_unbound_mutable_refs_before_tool_call() -> None:
    request = ReviewRequestDto(**_request_payload())
    client = MagicMock()
    client.session.call_tool = AsyncMock()
    executor = McpToolExecutor(client, request, "stage_1")

    result = await executor.execute_tool(
        "getBranchFileContent",
        {"branch": "other-feature", "filePath": "app.py"},
    )

    assert result == "Tool call rejected: revision is outside the bound snapshot."
    client.session.call_tool.assert_not_awaited()


def test_v1_telemetry_identity_and_trace_include_manifest_digest() -> None:
    request = ReviewRequestDto(
        **_request_payload(bind_legacy_aliases=True)
    )
    recorder, _ = ReviewService._create_telemetry_recorder(request)

    assert recorder is not None
    digest = request.executionManifest.artifactManifestDigest
    assert recorder.identity.artifact_manifest_digest == digest
    trace = recorder.provisional_snapshot(
        outcome=TerminalOutcome.FAILED,
        duration_ms=0,
        usage=UsageCounts(),
        candidates=CandidateCounts(),
        coverage=CoverageCounts(),
        reason="contract_probe",
    )
    assert trace_document(trace)["artifact_manifest_digest"] == digest


def test_v1_telemetry_accepts_manifest_maximum_execution_id() -> None:
    payload = _request_payload()
    manifest = dict(payload["executionManifest"])
    manifest["executionId"] = "e" * 160
    manifest["inputArtifacts"] = [
        {
            **artifact,
            "executionId": manifest["executionId"],
        }
        for artifact in manifest["inputArtifacts"]
    ]
    manifest["artifactManifestDigest"] = _canonical_digest(
        {
            key: value
            for key, value in manifest.items()
            if key != "artifactManifestDigest"
        }
    )
    payload["executionManifest"] = manifest
    request = ReviewRequestDto(**payload)

    recorder, _ = ReviewService._create_telemetry_recorder(request)

    assert recorder is not None
    assert recorder.identity.execution_id == "e" * 160


def test_manifest_review_survives_telemetry_recorder_failure() -> None:
    request = ReviewRequestDto(
        **_request_payload(bind_legacy_aliases=True)
    )

    with patch.object(
        ReviewService,
        "_active_configuration_versions",
        return_value=("prompt-v1", "rules-v1"),
    ), patch(
        "service.review.review_service.ExecutionTelemetryRecorder",
        side_effect=RuntimeError("recorder unavailable"),
    ):
        recorder, sink = ReviewService._create_telemetry_recorder(request)

    assert recorder is None
    assert sink is not None


@pytest.mark.parametrize("failure_boundary", ["snapshot", "serialization"])
def test_manifest_review_survives_terminal_telemetry_failure(
    failure_boundary: str,
) -> None:
    request = ReviewRequestDto(**_request_payload(bind_legacy_aliases=True))
    service = object.__new__(ReviewService)
    recorder = MagicMock()
    recorder.latest_coverage = CoverageCounts()
    recorder.model_usage = UsageCounts()
    recorder.has_incomplete_operations = False
    recorder.sink_errors = []
    if failure_boundary == "snapshot":
        recorder.provisional_snapshot.side_effect = RuntimeError("snapshot unavailable")
    else:
        recorder.provisional_snapshot.return_value = MagicMock()

    with patch(
        "service.review.review_service.trace_document",
        side_effect=(
            RuntimeError("serialization unavailable")
            if failure_boundary == "serialization"
            else None
        ),
    ):
        result = service._attach_terminal_telemetry(
            request=request,
            result={"result": {"issues": []}},
            recorder=recorder,
            sink=MagicMock(),
            started_ns=0,
            event_callback=None,
        )

    assert result == {"result": {"issues": []}}


def _legacy_boundary_payload(state: str) -> dict[str, object]:
    if state == "missing":
        return _request_payload(manifest=False)
    return _request_payload(
        manifest=False,
        compatibility_deadline="2000-01-01T00:00:00Z",
    )


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize("compatibility_state", ["missing", "expired"])
async def test_queue_rejects_unbounded_legacy_at_boundary(
    compatibility_state: str,
) -> None:
    review_service = MagicMock()
    review_service.process_review_request = AsyncMock()
    consumer = RedisQueueConsumer(review_service)
    consumer._publish_event = AsyncMock()

    await consumer._handle_job(
        json.dumps(
            {
                "job_id": TRANSPORT_JOB_ID,
                "request": _legacy_boundary_payload(compatibility_state),
            }
        )
    )

    review_service.process_review_request.assert_not_awaited()
    assert any(
        call.args[1].get("type") == "error"
        for call in consumer._publish_event.await_args_list
    )


@pytest.mark.parametrize("compatibility_state", ["missing", "expired"])
def test_stdin_rejects_unbounded_legacy_at_boundary(
    compatibility_state: str,
    capsys,
) -> None:
    review_service = MagicMock()
    review_service.process_review_request = AsyncMock(
        return_value={"result": {"issues": []}}
    )
    handler = object.__new__(StdinHandler)
    handler.review_service = review_service
    handler.read_request_from_stdin = MagicMock(
        return_value=_legacy_boundary_payload(compatibility_state)
    )

    handler.process_stdin_request()

    review_service.process_review_request.assert_not_awaited()
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "Failed to process request"
    assert "legacyCompatibility" in output["exception"]


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize("compatibility_state", ["missing", "expired"])
async def test_direct_review_service_rejects_unbounded_legacy_at_boundary(
    compatibility_state: str,
) -> None:
    service = object.__new__(ReviewService)
    service._review_semaphore = asyncio.Semaphore(1)
    service._create_telemetry_recorder = MagicMock(
        return_value=(None, MagicMock())
    )
    service._process_review = AsyncMock(return_value={"result": {"issues": []}})
    service._attach_terminal_telemetry = MagicMock(
        return_value={"result": {"issues": []}}
    )

    with pytest.raises(ValueError, match="legacyCompatibility"):
        await service.process_review_request(
            ReviewRequestDto(**_legacy_boundary_payload(compatibility_state))
        )

    service._process_review.assert_not_awaited()
