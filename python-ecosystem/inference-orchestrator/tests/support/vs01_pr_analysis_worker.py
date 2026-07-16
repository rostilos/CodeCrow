#!/usr/bin/env python3
"""One-job offline worker for the working PR-analysis integration gate.

The production Redis consumer, review service, and orchestration stages remain
in the path. Only network-facing model and RAG clients are deterministic
in-process fakes; manifest work must not create an MCP client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit


SCRIPT_PATH = Path(__file__).resolve()
ORCHESTRATOR_ROOT = SCRIPT_PATH.parents[2]
PYTHON_ECOSYSTEM_ROOT = ORCHESTRATOR_ROOT.parent
for import_root in (
    ORCHESTRATOR_ROOT / "src",
    PYTHON_ECOSYSTEM_ROOT / "test-support",
    SCRIPT_PATH.parent,
):
    sys.path.insert(0, str(import_root))

# Keep this gate deterministic and focused on the normal shipping pipeline.
os.environ.setdefault("RAG_ENABLED", "false")
os.environ.setdefault("LLM_RERANK_ENABLED", "false")
os.environ.setdefault("REVIEW_FAST_CHECK_ENABLED", "true")
os.environ.setdefault("REVIEW_STAGE_2_ENABLED", "true")
os.environ.setdefault("REVIEW_VERIFICATION_ENABLED", "false")
os.environ.setdefault("REVIEW_DUPLICATION_RAG_ENABLED", "false")
os.environ.setdefault("REVIEW_STRUCTURED_OUTPUT_ENABLED", "true")
os.environ.setdefault("MAX_CONCURRENT_REVIEWS", "1")

from third_party_stubs import install_third_party_stubs

install_third_party_stubs()

import redis.asyncio as redis

from codecrow_test_harness.fakes import ScriptedLlmFake
from codecrow_test_harness.ledger import ExternalCallLedger
from codecrow_test_harness.scenario import ScenarioStep, ScriptedScenario
from model.multi_stage import (
    CrossFileAnalysisResult,
    FileGroup,
    FileReviewBatchOutput,
    FileReviewOutput,
    ReviewFile,
    ReviewPlan,
)
from model.output_schemas import CodeReviewIssue
from server.queue_consumer import RedisQueueConsumer
from service.review.review_service import ReviewService


LOGGER = logging.getLogger("codecrow.working_pr.worker")
DEFAULT_JOB_TIMEOUT_SECONDS = 45


class OfflineRagClient:
    """Network-free RAG boundary for a request that disables retrieval."""

    def __init__(self, ledger: ExternalCallLedger) -> None:
        self._ledger = ledger

    def _record(self, operation: str) -> None:
        self._ledger.record(
            boundary="rag",
            operation=operation,
            outcome="response",
            phase="SIMULATED",
            target="fake-rag:443",
            simulated=True,
        )

    async def get_pr_context(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self._record("rag.get_pr_context")
        return {"context": {"relevant_code": []}}

    async def get_deterministic_context(
        self, *_args: Any, **_kwargs: Any
    ) -> dict[str, Any]:
        self._record("rag.get_deterministic_context")
        return {"context": {"relevant_code": []}}

    async def search_for_duplicates(
        self, *_args: Any, **_kwargs: Any
    ) -> list[dict[str, Any]]:
        self._record("rag.search_for_duplicates")
        return []

    async def index_pr_files(self, *_args: Any, **_kwargs: Any) -> bool:
        self._record("rag.index_pr_files")
        return False

    async def delete_pr_files(self, *_args: Any, **_kwargs: Any) -> None:
        self._record("rag.delete_pr_files")

    async def close(self) -> None:
        return None


def redis_url_from_environment() -> str:
    configured = os.environ.get("REDIS_URL")
    if configured:
        return configured
    host = os.environ.get("VS01_REDIS_HOST", "127.0.0.1")
    port = os.environ.get("VS01_REDIS_PORT", "6379")
    database = os.environ.get("VS01_REDIS_DB", "1")
    return f"redis://{host}:{port}/{database}"


def safe_redis_endpoint(redis_url: str) -> str:
    parsed = urlsplit(redis_url)
    return f"{parsed.hostname or 'UNKNOWN'}:{parsed.port or 6379}{parsed.path or '/0'}"


def build_scenario(
    ledger: ExternalCallLedger,
) -> tuple[ScriptedLlmFake, ScriptedScenario]:
    finding = CodeReviewIssue(
        id="fixture-finding-1",
        severity="MEDIUM",
        category="BUG_RISK",
        file="src/App.java",
        line=5,
        scope="LINE",
        title="Risky call remains",
        reason="The new method executes `riskyCall()` without a safe guard.",
        suggestedFixDescription="Use the safe project operation.",
        suggestedFixDiff="-        riskyCall();\n+        safeCall();",
        codeSnippet="riskyCall();",
    )
    scenario = ScriptedScenario(
        "working-pr-analysis-v1",
        (
            ScenarioStep(
                operation="llm.ainvoke",
                call=1,
                kind="structured",
                payload=ReviewPlan(
                    analysis_summary="Review the newly added App implementation.",
                    file_groups=[
                        FileGroup(
                            group_id="application-code",
                            priority="MEDIUM",
                            rationale="The new method invokes a risky operation.",
                            files=[
                                ReviewFile(
                                    path="src/App.java",
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
                            file="src/App.java",
                            analysis_summary="One risky call remains.",
                            issues=[finding],
                            confidence="HIGH",
                        )
                    ]
                ),
                usage={"input_tokens": 24, "output_tokens": 16},
            ),
            ScenarioStep(
                operation="llm.ainvoke",
                call=3,
                kind="structured",
                payload=CrossFileAnalysisResult(
                    pr_risk_level="MEDIUM",
                    cross_file_issues=[],
                    data_flow_concerns=[],
                    pr_recommendation="REQUEST_CHANGES",
                    confidence="HIGH",
                ),
                usage={"input_tokens": 14, "output_tokens": 7},
            ),
            ScenarioStep(
                operation="llm.ainvoke",
                call=4,
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


async def consume_one_job() -> int:
    redis_url = redis_url_from_environment()
    timeout_seconds = positive_int(
        "VS01_WORKER_JOB_TIMEOUT_SECONDS", DEFAULT_JOB_TIMEOUT_SECONDS
    )
    ledger = ExternalCallLedger()
    llm, scenario = build_scenario(ledger)
    rag_client = OfflineRagClient(ledger)

    service = ReviewService()
    service.default_jar_path = "/missing/manifest-review-does-not-need-mcp.jar"
    service.rag_client = rag_client
    service._create_llm = lambda _request: llm

    def reject_mcp_creation(_config: object) -> object:
        raise RuntimeError("manifest-bound analysis attempted to create MCP")

    service._create_mcp_client = reject_mcp_creation

    redis_client = redis.from_url(redis_url, decode_responses=True)
    consumer = RedisQueueConsumer(service)
    consumer.redis_url = redis_url
    consumer._redis = redis_client
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, shutdown.set)
            installed_signals.append(signal_name)
        except NotImplementedError:
            pass

    try:
        await redis_client.ping()
        emit(
            {
                "event": "working_pr_worker_ready",
                "queue": consumer.job_queue_keys[0],
                "queues": consumer.job_queue_keys,
                "redis": safe_redis_endpoint(redis_url),
            }
        )

        pop_task = asyncio.create_task(
            redis_client.brpop(consumer.job_queue_keys, timeout=timeout_seconds)
        )
        shutdown_task = asyncio.create_task(shutdown.wait())
        done, pending = await asyncio.wait(
            {pop_task, shutdown_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        if shutdown_task in done and shutdown_task.result():
            emit({"event": "working_pr_worker_stopped", "reason": "signal"})
            return 130

        queue_item = pop_task.result()
        if queue_item is None:
            emit(
                {
                    "event": "working_pr_worker_timeout",
                    "timeout_seconds": timeout_seconds,
                }
            )
            return 2

        queue_name, payload = queue_item
        job_id = job_id_from_payload(payload)
        await consumer._bounded_handle_job(payload)

        scenario.assert_consumed()
        ledger.assert_zero_live_calls()
        prompt_text = "\n".join(repr(value) for value in llm.invocations)
        for expected_context in (
            "Working PR context",
            "Exercise the exact snapshot review path.",
            "Author: manifest-author-sentinel",
            "CC-42 previously introduced the request handler.",
            "Reject risky calls",
        ):
            if expected_context not in prompt_text:
                raise RuntimeError(
                    f"bound review context did not reach model prompts: {expected_context}"
                )

        ledger_path = os.environ.get("VS01_WORKER_LEDGER_PATH")
        if ledger_path:
            ledger.write(ledger_path)
        emit(
            {
                "event": "working_pr_worker_complete",
                "job_id": job_id,
                "processed_jobs": 1,
                "queue": queue_name,
                "live_external_calls": ledger.live_call_count,
                "simulated_external_calls": ledger.simulated_call_count,
            }
        )
        return 0
    finally:
        for signal_name in installed_signals:
            loop.remove_signal_handler(signal_name)
        await rag_client.close()
        await redis_client.aclose()


def job_id_from_payload(payload: str) -> str:
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        return "UNKNOWN"
    return str(decoded.get("job_id") or "UNKNOWN")


def positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def emit(document: dict[str, Any]) -> None:
    print(json.dumps(document, sort_keys=True), flush=True)


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("VS01_WORKER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    try:
        return asyncio.run(consume_one_job())
    except Exception as error:
        LOGGER.exception("working PR worker failed")
        emit(
            {
                "event": "working_pr_worker_failed",
                "error_type": type(error).__name__,
                "message": str(error),
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
