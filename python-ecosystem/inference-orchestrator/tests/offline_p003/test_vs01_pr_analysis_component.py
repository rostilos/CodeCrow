from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from codecrow_test_harness.deterministic import FrozenClock
from codecrow_test_harness.fakes import ScriptedLlmFake
from codecrow_test_harness.ledger import ExternalCallLedger
from codecrow_test_harness.scenario import ScenarioStep, ScriptedScenario
from model.multi_stage import (
    FileGroup,
    FileReviewBatchOutput,
    FileReviewOutput,
    ReviewFile,
    ReviewPlan,
)
from model.output_schemas import CodeReviewIssue
from server.queue_consumer import RedisQueueConsumer
import service.review.execution_context as execution_context_module
import service.review.orchestrator.inference_policy as inference_policy_module
import service.review.orchestrator.orchestrator as orchestrator_module
import service.review.orchestrator.stage_1_file_review as stage_1_module
import service.review.review_service as review_service_module
from service.review.review_service import ReviewService


RAW_DIFF = """diff --git a/src/calc.py b/src/calc.py
index 1111111..2222222 100644
--- a/src/calc.py
+++ b/src/calc.py
@@ -1 +1,2 @@
 def ratio(total, count):
+    return total / count
"""


class _RecordingRedis:
    """Minimal async Redis boundary that preserves pipeline commit order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.expirations: list[tuple[str, int]] = []
        self.terminal_published = asyncio.Event()

    def pipeline(self) -> "_RecordingPipeline":
        return _RecordingPipeline(self)


class _RecordingPipeline:
    def __init__(self, redis: _RecordingRedis) -> None:
        self.redis = redis
        self.key: str | None = None
        self.event: dict[str, Any] | None = None
        self.expiry: tuple[str, int] | None = None

    def lpush(self, key: str, value: str) -> "_RecordingPipeline":
        self.key = key
        self.event = json.loads(value)
        return self

    def expire(self, key: str, seconds: int) -> "_RecordingPipeline":
        self.expiry = (key, seconds)
        return self

    async def execute(self) -> list[int]:
        assert self.key is not None
        assert self.event is not None
        assert self.expiry is not None
        self.redis.events.append((self.key, self.event))
        self.redis.expirations.append(self.expiry)
        if self.event.get("type") in {"final", "error"}:
            self.redis.terminal_published.set()
        return [1, 1]


class _NoopMcpClient:
    def __init__(self) -> None:
        self.close_count = 0

    async def close_all_sessions(self) -> None:
        self.close_count += 1


def _queue_payload() -> str:
    return json.dumps(
        {
            "job_id": "vs01-functional-job",
            "request": {
                "projectId": 7,
                "projectVcsWorkspace": "offline-workspace",
                "projectVcsRepoSlug": "review-fixture",
                "projectWorkspace": "Offline Workspace",
                "projectNamespace": "offline-project",
                "aiProvider": "scripted",
                "aiModel": "fixture-v1",
                "aiApiKey": "test-key",
                "pullRequestId": 42,
                "analysisType": "PR_REVIEW",
                "vcsProvider": "github",
                "prTitle": "Guard ratio calculation",
                "sourceBranchName": "feature/ratio",
                "targetBranchName": "main",
                "changedFiles": ["src/calc.py"],
                "rawDiff": RAW_DIFF,
                "previousCommitHash": "a" * 40,
                "currentCommitHash": "b" * 40,
                "commitHash": "b" * 40,
                "indexVersion": "rag-disabled",
                "legacyCompatibility": {
                    "kind": "legacy",
                    "deadline": "2026-09-30T00:00:00Z",
                },
            },
        }
    )


def _scripted_model(ledger: ExternalCallLedger) -> tuple[ScriptedLlmFake, ScriptedScenario]:
    issue = CodeReviewIssue(
        id="vs01-division-by-zero",
        severity="MEDIUM",
        category="BUG_RISK",
        file="src/calc.py",
        line=2,
        scope="LINE",
        title="Division by zero is unguarded",
        reason="`count` can be zero, causing the new ratio calculation to fail.",
        suggestedFixDescription="Reject zero before performing the division.",
        codeSnippet="return total / count",
    )
    scenario = ScriptedScenario(
        "vs01-working-pr-analysis-v1",
        (
            ScenarioStep(
                operation="llm.ainvoke",
                call=1,
                kind="structured",
                payload=ReviewPlan(
                    analysis_summary="Review the changed calculation.",
                    file_groups=[
                        FileGroup(
                            group_id="calculation",
                            priority="MEDIUM",
                            rationale="The changed calculation can fail at runtime.",
                            files=[
                                ReviewFile(
                                    path="src/calc.py",
                                    focus_areas=["correctness"],
                                    risk_level="MEDIUM",
                                )
                            ],
                        )
                    ],
                    cross_file_concerns=[],
                ),
                usage={"input_tokens": 12, "output_tokens": 8},
            ),
            ScenarioStep(
                operation="llm.ainvoke",
                call=2,
                kind="structured",
                payload=FileReviewBatchOutput(
                    reviews=[
                        FileReviewOutput(
                            file="src/calc.py",
                            analysis_summary="The new division lacks a zero guard.",
                            issues=[issue],
                            confidence="HIGH",
                        )
                    ]
                ),
                usage={"input_tokens": 24, "output_tokens": 16},
            ),
            ScenarioStep(
                operation="llm.ainvoke",
                call=3,
                kind="response",
                payload=SimpleNamespace(
                    content="One correctness issue requires attention.",
                    response_metadata={},
                    usage_metadata={"input_tokens": 10, "output_tokens": 6},
                ),
                usage={"input_tokens": 10, "output_tokens": 6},
            ),
        ),
    )
    return ScriptedLlmFake(ledger=ledger, scenario=scenario), scenario


@pytest.mark.asyncio(loop_scope="function")
async def test_vs01_worker_returns_only_after_ordered_finding_is_published(
    monkeypatch: pytest.MonkeyPatch,
    external_call_ledger: ExternalCallLedger,
) -> None:
    """A completed worker call must make its terminal event immediately observable."""

    clock = FrozenClock(datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(
        execution_context_module,
        "datetime",
        SimpleNamespace(now=lambda _tz=None: clock.now()),
    )
    monkeypatch.setenv("RAG_ENABLED", "false")
    monkeypatch.setattr(inference_policy_module, "FAST_CHECK_ENABLED", True)
    monkeypatch.setattr(inference_policy_module, "STAGE_2_ENABLED", True)
    monkeypatch.setattr(orchestrator_module, "VERIFICATION_ENABLED", True)
    monkeypatch.setattr(stage_1_module, "STRUCTURED_OUTPUT_ENABLED", True)
    # ReviewService owns configuration loading in production. The offline test
    # harness already supplied its sanitized environment, so loading the host
    # checkout's .env again would cross that boundary and reintroduce secrets.
    monkeypatch.setattr(review_service_module, "load_dotenv", lambda **_kwargs: False)

    llm, scenario = _scripted_model(external_call_ledger)
    mcp_client = _NoopMcpClient()
    service = ReviewService()
    service.rag_cache.clear()
    service.default_jar_path = str(Path(__file__).resolve())
    monkeypatch.setattr(service, "_create_llm", lambda _request: llm)
    monkeypatch.setattr(service, "_create_mcp_client", lambda _config: mcp_client)

    redis = _RecordingRedis()
    consumer = RedisQueueConsumer(service)
    consumer._redis = redis

    try:
        await consumer._handle_job(_queue_payload())
        events_at_return = tuple(event for _, event in redis.events)

        # Drain the currently fire-and-forget publications only so a RED run does
        # not leak tasks into pytest teardown. Assertions remain against the exact
        # state observed when _handle_job returned.
        await asyncio.wait_for(redis.terminal_published.wait(), timeout=1)
        await asyncio.sleep(0)
    finally:
        await service.rag_client.close()

    scenario.assert_consumed()
    assert mcp_client.close_count == 1
    assert external_call_ledger.live_call_count == 0

    acknowledged = [
        event
        for event in events_at_return
        if event.get("type") == "status" and event.get("state") == "acknowledged"
    ]
    progress = [event for event in events_at_return if event.get("type") == "progress"]
    telemetry = [event for event in events_at_return if event.get("type") == "telemetry"]
    final = [event for event in events_at_return if event.get("type") == "final"]

    assert len(acknowledged) == 1
    assert progress
    assert len(final) == 1, (
        "RedisQueueConsumer._handle_job returned before its final publication "
        f"completed; observed event types: {[event.get('type') for event in events_at_return]}"
    )
    assert len(telemetry) == 1

    finding = final[0]["result"]["issues"]
    assert len(finding) == 1
    assert finding[0]["id"] == "vs01-division-by-zero"
    assert finding[0]["file"] == "src/calc.py"
    assert finding[0]["codeSnippet"] == "return total / count"

    positions = {id(event): index for index, event in enumerate(events_at_return)}
    assert positions[id(acknowledged[0])] < positions[id(progress[0])]
    assert positions[id(progress[0])] < positions[id(telemetry[0])]
    assert positions[id(telemetry[0])] < positions[id(final[0])]
    assert all(key == "codecrow:analysis:events:vs01-functional-job" for key, _ in redis.events)
    assert all(seconds == 3600 for _, seconds in redis.expirations)
