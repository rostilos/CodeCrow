"""Explicit rejections may retry; accepted or uncertain work must not replay."""
import asyncio
from email.utils import formatdate
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openai import InternalServerError, RateLimitError

from llm.provider_errors import rate_limit_retry_delay, raise_if_outcome_unknown
from service.review import execution_scheduler as scheduler
from model.dtos import ReviewRequestDto
from service.review.orchestrator.stage_0_planning import execute_stage_0_planning
from service.review.orchestrator.branch_analysis import execute_branch_reconciliation_direct
from service.review.orchestrator.stage_1_file_review import _invoke_stage_1_batch_llm
from service.review.orchestrator.stage_2_cross_file import _invoke_stage_2_llm


def rejection(status=429, headers=None, body=None):
    response = httpx.Response(status, headers=headers, request=httpx.Request("POST", "https://model.invalid"))
    if status == 429:
        return RateLimitError("rate limited", response=response, body=body)
    error = RuntimeError("provider failure")
    error.status_code = status
    return error


@pytest.mark.asyncio
async def test_rejections_back_off_release_capacity_and_count_every_attempt(monkeypatch):
    capacity = scheduler.FairReviewScheduler(1)
    events, waits = [], []
    answer = SimpleNamespace(content="done", usage_metadata={"input_tokens": 10, "output_tokens": 2})
    invoke = AsyncMock(side_effect=[rejection(headers={"retry-after": "7"}), rejection(), answer])

    async def sleep(seconds):
        assert capacity._active == 0
        waits.append(seconds)
        # Other work must be able to use capacity throughout the cooldown.
        async with capacity.slot(object()):
            pass

    monkeypatch.setattr(scheduler.asyncio, "sleep", sleep)
    with scheduler.review_execution(capacity, asyncio.Semaphore(1), event_callback=events.append):
        result = await scheduler.invoke_review_model("stage_0", invoke, "same prompt", temperature=0)
    assert result is answer
    assert waits == [7, 10]
    assert invoke.await_count == 3
    assert all(call.args == ("same prompt",) and call.kwargs == {"temperature": 0} for call in invoke.await_args_list)
    assert events[-1]["inferenceUsage"]["attempts"] == 3
    assert sum(event["state"] == "provider_rate_limited" for event in events) == 2


@pytest.mark.asyncio
async def test_exhaustion_is_four_total_requests_and_cannot_enter_fallback(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(scheduler.asyncio, "sleep", sleep)
    invoke = AsyncMock(side_effect=rejection())
    with pytest.raises(RateLimitError) as caught:
        await scheduler.invoke_review_model("review", invoke, "prompt")
    assert invoke.await_count == 4
    assert [call.args[0] for call in sleep.await_args_list] == [5, 10, 20]
    with pytest.raises(RateLimitError):
        raise_if_outcome_unknown(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    TimeoutError("accepted response lost"), httpx.ReadTimeout("response lost"),
    ConnectionError("disconnected"), rejection(500), rejection(503),
    rejection(headers={"retry-after": "3600"}),
    rejection(body={"code": "insufficient_quota"}),
    asyncio.CancelledError(),
])
async def test_no_retry_for_ambiguous_outcomes_quota_or_excessive_cooldown(error):
    invoke = AsyncMock(side_effect=error)
    with pytest.raises(type(error)):
        await scheduler.invoke_review_model("review", invoke, "prompt")
    invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_during_cooldown_prevents_second_request(monkeypatch):
    sleeping = asyncio.Event()

    async def sleep(_seconds):
        sleeping.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(scheduler.asyncio, "sleep", sleep)
    invoke = AsyncMock(side_effect=rejection())
    task = asyncio.create_task(scheduler.invoke_review_model("review", invoke, "prompt"))
    await sleeping.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_remaining_review_deadline_prevents_oversized_wait():
    invoke = AsyncMock(side_effect=rejection())
    with scheduler.review_execution(scheduler.FairReviewScheduler(1), asyncio.Semaphore(1)):
        scheduler.set_review_deadline(asyncio.get_running_loop().time() + 1)
        with pytest.raises(RateLimitError):
            await scheduler.invoke_review_model("review", invoke, "prompt")
    invoke.assert_awaited_once()


def test_retry_after_http_date_and_invalid_values(monkeypatch):
    monkeypatch.setattr("llm.provider_errors.time.time", lambda: 1000)
    assert rate_limit_retry_delay(rejection(headers={"retry-after": formatdate(1045, usegmt=True)}), 0) == 45
    assert rate_limit_retry_delay(rejection(headers={"retry-after": "invalid"}), 1) == 10
    assert rate_limit_retry_delay(rejection(headers={"retry-after": "nan"}), 0) is None
    assert rate_limit_retry_delay(rejection(headers={"retry-after": "61"}), 0) is None


def test_google_rejection_code_and_wrapped_exhaustion():
    error = RuntimeError("rate exceeded")
    error.code = 429
    assert rate_limit_retry_delay(error, 0) == 5
    wrapper = RuntimeError("agent failed")
    wrapper.__cause__ = error
    with pytest.raises(RuntimeError, match="agent failed"):
        raise_if_outcome_unknown(wrapper)


@pytest.mark.asyncio
async def test_nested_invocations_share_one_allowance(monkeypatch):
    monkeypatch.setattr(scheduler.asyncio, "sleep", AsyncMock())
    invoke = AsyncMock(side_effect=rejection())

    async def wrapped(prompt):
        return await scheduler.invoke_review_model("inner", invoke, prompt)

    with pytest.raises(RateLimitError):
        await scheduler.invoke_review_model("outer", wrapped, "prompt")
    assert invoke.await_count == 4


def test_summary_budget_contains_provider_timeout_and_all_cooldowns(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_TIMEOUT_SECONDS", "600")
    assert scheduler.review_model_call_budget() == 780
    wrapped = SimpleNamespace(__codecrow_delegate__=SimpleNamespace(request_timeout=900))
    assert scheduler.review_model_call_budget(wrapped) == 1080


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["planner", "branch", "stage1_raw", "stage2_raw"])
@pytest.mark.parametrize("error,attempts", [
    (rejection(), 4),
    (rejection(headers={"retry-after": "3600"}), 1),
    (TimeoutError("response lost"), 1),
    (httpx.ReadError("disconnected"), 1),
    (InternalServerError("unavailable", response=httpx.Response(
        503, request=httpx.Request("POST", "https://model.invalid")), body=None), 1),
])
async def test_stage_callers_do_not_restart_exhausted_or_uncertain_work(monkeypatch, operation, error, attempts):
    monkeypatch.setattr(scheduler.asyncio, "sleep", AsyncMock())
    llm = MagicMock()
    structured = MagicMock()
    structured.ainvoke = AsyncMock(side_effect=error)
    llm.with_structured_output.return_value = structured
    llm.ainvoke = AsyncMock(side_effect=error)

    if operation == "planner":
        request = ReviewRequestDto(
            projectId=1, projectVcsWorkspace="workspace", projectVcsRepoSlug="repo",
            projectWorkspace="workspace", projectNamespace="project",
            aiProvider="openai", aiModel="fixture", aiApiKey="fixture",
            changedFiles=["a.py", "b.py"], currentCommitHash="a" * 40,
        )
        plan = await execute_stage_0_planning(llm, request)
        assert {file.path for group in plan.file_groups for file in group.files} == {"a.py", "b.py"}
    else:
        with pytest.raises(type(error)):
            if operation == "branch":
                await execute_branch_reconciliation_direct(llm, "fixture")
            elif operation == "stage1_raw":
                await _invoke_stage_1_batch_llm(llm, "fixture", ["a.py"], force_unstructured=True)
            else:
                await _invoke_stage_2_llm(llm, "fixture", "fixture", force_unstructured=True)

    if operation.endswith("_raw"):
        assert llm.ainvoke.await_count == attempts
        structured.ainvoke.assert_not_awaited()
    else:
        assert structured.ainvoke.await_count == attempts
        llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_malformed_reconciliation_can_still_use_format_fallback():
    llm = MagicMock()
    structured = MagicMock()
    structured.ainvoke = AsyncMock(side_effect=ValueError("completed response has invalid shape"))
    llm.with_structured_output.return_value = structured
    llm.ainvoke = AsyncMock(return_value=SimpleNamespace(content='{"issues": [], "comment": "done"}'))
    assert await execute_branch_reconciliation_direct(llm, "fixture") == {"issues": [], "comment": "done"}
    structured.ainvoke.assert_awaited_once()
    llm.ainvoke.assert_awaited_once()
