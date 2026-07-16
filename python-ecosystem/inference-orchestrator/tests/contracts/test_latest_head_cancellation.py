from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.queue_consumer import RedisQueueConsumer


EXECUTION_ID = "execution-pr-42-head-a"
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
RAW_DIFF = "diff --git a/app.py b/app.py\n+print('latest head')\n"


def _canonical_digest(document: dict[str, object]) -> str:
    return sha256(
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _manifest() -> dict[str, object]:
    diff_digest = sha256(RAW_DIFF.encode("utf-8")).hexdigest()
    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "executionId": EXECUTION_ID,
        "projectId": 7,
        "repositoryId": "github:codecrow/review-fixture",
        "pullRequestId": 42,
        "baseSha": BASE_SHA,
        "headSha": HEAD_SHA,
        "mergeBaseSha": "c" * 40,
        "diffArtifactId": "diff-artifact-pr-42-head-a",
        "diffDigest": diff_digest,
        "diffByteLength": len(RAW_DIFF.encode("utf-8")),
        "diffArtifactKind": "raw-diff",
        "diffArtifactProducer": "java-vcs-acquisition",
        "diffArtifactProducerVersion": "analysis-engine-v1",
        "artifactSchemaVersion": "review-artifact-v1",
        "policyVersion": "candidate-review-v2",
        "creationFence": "creation:latest-head:0001",
        "createdAt": "2026-07-16T12:00:00Z",
    }
    manifest["inputArtifacts"] = [
        {
            "executionId": EXECUTION_ID,
            "artifactId": manifest["diffArtifactId"],
            "contentKey": "pull-request.diff",
            "snapshotSha": HEAD_SHA,
            "contentDigest": diff_digest,
            "byteLength": manifest["diffByteLength"],
            "kind": "raw-diff",
            "artifactSchemaVersion": "review-artifact-v1",
            "producer": "java-vcs-acquisition",
            "producerVersion": "analysis-engine-v1",
        }
    ]
    manifest["artifactManifestDigest"] = _canonical_digest(manifest)
    return manifest


def _coverage_ledger(manifest: dict[str, object]) -> dict[str, object]:
    anchor = {
        "anchorId": sha256(
            f"{EXECUTION_ID}\0latest-head-app.py".encode("utf-8")
        ).hexdigest(),
        "executionId": EXECUTION_ID,
        "parentHunkId": sha256(b"latest-head-parent-hunk").hexdigest(),
        "changeId": sha256(b"latest-head-change").hexdigest(),
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
        "executionId": EXECUTION_ID,
        "artifactManifestDigest": manifest["artifactManifestDigest"],
        "diffDigest": manifest["diffDigest"],
        "diffByteLength": manifest["diffByteLength"],
        "anchorCount": 1,
        "anchors": [anchor],
    }
    ledger["ledgerDigest"] = _canonical_digest(ledger)
    return ledger


def _payload() -> str:
    manifest = _manifest()
    return json.dumps(
        {
            "schemaVersion": 2,
            "job_id": "transport-latest-head-0001",
            "request": {
                "projectId": 7,
                "projectVcsWorkspace": "codecrow",
                "projectVcsRepoSlug": "review-fixture",
                "projectWorkspace": "Codecrow",
                "projectNamespace": "codecrow-garden",
                "pullRequestId": 42,
                "aiProvider": "scripted",
                "aiModel": "fixture-v1",
                "aiApiKey": "credential-not-control-state",
                "analysisType": "PR_REVIEW",
                "vcsProvider": "github",
                "changedFiles": [],
                "deletedFiles": [],
                "diffSnippets": [],
                "rawDiff": RAW_DIFF,
                "analysisMode": "FULL",
                "indexVersion": "rag-disabled",
                "executionManifest": manifest,
                "coverageLedger": _coverage_ledger(manifest),
            },
        }
    )


class _Pipeline:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self.events = events

    def lpush(self, _key: str, value: str) -> "_Pipeline":
        self.events.append(json.loads(value))
        return self

    def expire(self, _key: str, _seconds: int) -> "_Pipeline":
        return self

    async def execute(self) -> None:
        return None


class _LatestHeadRedis:
    def __init__(
        self,
        execution_id: str | None = EXECUTION_ID,
        head_sha: str | None = HEAD_SHA,
    ) -> None:
        self.execution_id = execution_id
        self.head_sha = head_sha
        self.events: list[dict[str, object]] = []
        self.mget_calls: list[tuple[str, str]] = []

    async def mget(self, execution_key: str, revision_key: str):
        self.mget_calls.append((execution_key, revision_key))
        return [self.execution_id, self.head_sha]

    def pipeline(self) -> _Pipeline:
        return _Pipeline(self.events)

    def advance(self) -> None:
        self.execution_id = "execution-pr-42-head-b"
        self.head_sha = "d" * 40


@pytest.mark.asyncio(loop_scope="function")
async def test_stale_queued_candidate_never_starts_model_work() -> None:
    service = MagicMock()
    service.process_review_request = AsyncMock()
    redis = _LatestHeadRedis(
        execution_id="execution-pr-42-head-b",
        head_sha="d" * 40,
    )
    consumer = RedisQueueConsumer(service)
    consumer._redis = redis

    outcome = await consumer._handle_job(_payload())

    assert outcome == "superseded"
    service.process_review_request.assert_not_awaited()
    assert [event["type"] for event in redis.events] == ["superseded"]
    assert redis.events[0] == {
        "type": "superseded",
        "reasonCode": "latest_head_advanced",
        "computeState": "not_started",
        "message": "A newer pull-request head superseded this analysis",
        "executionId": EXECUTION_ID,
        "artifactManifestDigest": _manifest()["artifactManifestDigest"],
    }
    scope_id = sha256(b"github:7:42").hexdigest()
    assert redis.mget_calls == [
        (
            "codecrow:llm-handoff:policy:v1:"
            f"{{pr-{scope_id}}}:latest-execution",
            "codecrow:llm-handoff:policy:v1:"
            f"{{pr-{scope_id}}}:latest-revision",
        )
    ]


@pytest.mark.asyncio(loop_scope="function")
async def test_newer_head_cancels_in_flight_review_and_emits_no_final() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def block_until_cancelled(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    service = MagicMock()
    service.process_review_request = AsyncMock(side_effect=block_until_cancelled)
    redis = _LatestHeadRedis()
    consumer = RedisQueueConsumer(service)
    consumer._redis = redis
    consumer.latest_head_poll_seconds = 0.001

    handling = asyncio.create_task(consumer._handle_job(_payload()))
    await asyncio.wait_for(started.wait(), timeout=1)
    redis.advance()

    outcome = await asyncio.wait_for(handling, timeout=1)

    assert outcome == "superseded"
    assert cancelled.is_set()
    assert [event["type"] for event in redis.events] == [
        "status",
        "superseded",
    ]
    assert redis.events[-1]["computeState"] == "cancelled"
    assert redis.events[-1]["reasonCode"] == "latest_head_advanced"
    assert all(event["type"] != "final" for event in redis.events)


@pytest.mark.asyncio(loop_scope="function")
async def test_head_advance_after_model_completion_discards_result_without_final() -> None:
    redis = _LatestHeadRedis()
    model_completed = False

    def advance_after_completion() -> None:
        assert model_completed
        redis.advance()

    async def complete_model(*_args, **_kwargs):
        nonlocal model_completed
        asyncio.get_running_loop().call_soon(advance_after_completion)
        model_completed = True
        return {"result": {"comment": "stale result", "issues": []}}

    service = MagicMock()
    service.process_review_request = AsyncMock(side_effect=complete_model)
    consumer = RedisQueueConsumer(service)
    consumer._redis = redis
    consumer.latest_head_poll_seconds = 0.001

    outcome = await asyncio.wait_for(consumer._handle_job(_payload()), timeout=1)

    assert outcome == "superseded"
    service.process_review_request.assert_awaited_once()
    assert [event["type"] for event in redis.events] == [
        "status",
        "superseded",
    ]
    assert redis.events[-1]["computeState"] == "completed_discarded"
    assert all(event["type"] != "final" for event in redis.events)


@pytest.mark.asyncio(loop_scope="function")
async def test_current_head_completes_and_stops_its_monitor() -> None:
    service = MagicMock()
    service.process_review_request = AsyncMock(
        return_value={"result": {"comment": "current", "issues": []}}
    )
    redis = _LatestHeadRedis()
    consumer = RedisQueueConsumer(service)
    consumer._redis = redis
    consumer.latest_head_poll_seconds = 0.001

    outcome = await asyncio.wait_for(
        consumer._handle_job(_payload()),
        timeout=1,
    )

    assert outcome == "complete"
    service.process_review_request.assert_awaited_once()
    assert [event["type"] for event in redis.events] == ["status", "final"]
    assert redis.events[-1]["result"] == {"comment": "current", "issues": []}
    assert len(redis.mget_calls) >= 2


@pytest.mark.asyncio(loop_scope="function")
async def test_missing_candidate_fence_fails_closed_without_model_work() -> None:
    service = MagicMock()
    service.process_review_request = AsyncMock()
    redis = _LatestHeadRedis(execution_id=None, head_sha=None)
    consumer = RedisQueueConsumer(service)
    consumer._redis = redis

    outcome = await consumer._handle_job(_payload())

    assert outcome == "failed"
    service.process_review_request.assert_not_awaited()
    assert [event["type"] for event in redis.events] == ["error"]
    assert "latest-head fence is missing" in str(redis.events[0]["message"])
    assert redis.events[0]["executionId"] == EXECUTION_ID
