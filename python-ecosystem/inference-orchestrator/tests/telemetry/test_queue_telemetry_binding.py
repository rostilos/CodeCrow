from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.queue_consumer import RedisQueueConsumer


LEGACY_COMPATIBILITY = {
    "kind": "legacy",
    "deadline": "2026-09-30T00:00:00Z",
}


class _Pipeline:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def lpush(self, key: str, value: str) -> "_Pipeline":
        self.events.append((key, json.loads(value)))
        return self

    def expire(self, key: str, seconds: int) -> "_Pipeline":
        return self

    async def execute(self) -> None:
        return None


class _Redis:
    def __init__(self) -> None:
        self.pipelines: list[_Pipeline] = []

    def pipeline(self) -> _Pipeline:
        pipeline = _Pipeline()
        self.pipelines.append(pipeline)
        return pipeline


def _request(**revisions: object) -> dict[str, object]:
    return {
        "projectId": 1,
        "projectVcsWorkspace": "vcs-workspace",
        "projectVcsRepoSlug": "repo",
        "projectWorkspace": "workspace",
        "projectNamespace": "namespace",
        "aiProvider": "scripted",
        "aiModel": "fixture-v1",
        "aiApiKey": "credential-not-telemetry",
        "legacyCompatibility": dict(LEGACY_COMPATIBILITY),
        **revisions,
    }


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("revisions", "expected_base", "expected_head"),
    [
        (
            {"previousCommitHash": "a" * 40, "currentCommitHash": "b" * 40},
            "a" * 40,
            "b" * 40,
        ),
        ({"commitHash": "c" * 40}, None, "c" * 40),
    ],
)
async def test_explicit_legacy_compatibility_binds_observed_revision_telemetry_only(
    revisions: dict[str, str],
    expected_base: str | None,
    expected_head: str,
) -> None:
    review_service = MagicMock()
    review_service.process_review_request = AsyncMock(
        return_value={"result": {"comment": "ok", "issues": []}}
    )
    consumer = RedisQueueConsumer(review_service)
    consumer._redis = _Redis()
    payload = json.dumps(
        {
            "job_id": "execution-queue-1",
            "request": _request(**revisions),
        }
    )

    await consumer._handle_job(payload)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    request_dto = review_service.process_review_request.await_args.args[0]
    assert (
        request_dto.legacyCompatibility.model_dump(mode="json", by_alias=True)
        == LEGACY_COMPATIBILITY
    )
    assert request_dto.executionManifest is None
    assert request_dto.executionId == "execution-queue-1"
    assert request_dto.baseRevision == expected_base
    assert request_dto.headRevision == expected_head


@pytest.mark.asyncio(loop_scope="function")
async def test_queue_preserves_frozen_java_policy_context_without_reselection() -> None:
    review_service = MagicMock()
    review_service.process_review_request = AsyncMock(
        return_value={"result": {"comment": "ok", "issues": []}}
    )
    consumer = RedisQueueConsumer(review_service)
    consumer._redis = _Redis()
    payload = json.dumps(
        {
            "job_id": "redis-transport-job",
            "request": _request(
                executionId="pr:" + "a" * 64,
                policyVersion="candidate-review-v2",
                executionMode="active",
                policySelectionReason="active_rollout_selected",
                publicationAllowed=True,
            ),
        }
    )

    await consumer._handle_job(payload)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    request_dto = review_service.process_review_request.await_args.args[0]
    assert request_dto.executionId == "pr:" + "a" * 64
    assert request_dto.policyVersion == "candidate-review-v2"
    assert request_dto.executionMode == "active"
    assert request_dto.policySelectionReason == "active_rollout_selected"
    assert request_dto.publicationAllowed is True
