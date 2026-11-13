import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from llm.provider_lifecycle import provider_execution_scope


@pytest.mark.asyncio
async def test_unscoped_provider_is_unchanged():
    async with provider_execution_scope(SimpleNamespace()):
        result = {"issues": []}
    assert result == {"issues": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", ["same", "different_model", "child_task"])
async def test_scope_reuse_requires_identical_model_and_task(nested):
    events = []

    @asynccontextmanager
    async def scope():
        events.append("entered")
        try:
            yield
        finally:
            events.append("closed")

    model = SimpleNamespace(provider_execution_scope=scope)
    other = SimpleNamespace(provider_execution_scope=scope)

    async def inner():
        async with provider_execution_scope(other if nested == "different_model" else model):
            assert events.count("entered") == (1 if nested == "same" else 2)

    async with provider_execution_scope(model):
        if nested == "child_task":
            await asyncio.create_task(inner())
        else:
            await inner()
    assert events.count("closed") == events.count("entered")
    async with provider_execution_scope(model):
        assert events[-1] == "entered"
    assert events[-2:] == ["entered", "closed"]


@pytest.mark.asyncio
async def test_cross_context_generator_close_cannot_reuse_closed_owner():
    events = []

    @asynccontextmanager
    async def scope():
        events.append("entered")
        try:
            yield
        finally:
            events.append("closed")

    model = SimpleNamespace(provider_execution_scope=scope)

    async def stream():
        async with provider_execution_scope(model):
            yield "result"

    generator = stream()
    assert await anext(generator) == "result"
    await asyncio.create_task(generator.aclose())
    assert events == ["entered", "closed"]
    async with provider_execution_scope(model):
        assert events == ["entered", "closed", "entered"]
    assert events == ["entered", "closed"] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("body_failure", ["timeout", "error"])
async def test_new_external_cancellation_during_cleanup_overrides_prior_failure(body_failure):
    cleanup_started = asyncio.Event()
    cleanup_joined = []

    @asynccontextmanager
    async def scope():
        try:
            yield
        finally:
            cleanup_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_joined.append(True)

    async def run():
        async with provider_execution_scope(SimpleNamespace(provider_execution_scope=scope)):
            if body_failure == "timeout":
                async with asyncio.timeout(0):
                    await asyncio.Event().wait()
            raise ValueError("original body failure")

    task = asyncio.create_task(run())
    await asyncio.wait_for(cleanup_started.wait(), 1)
    task.cancel("external review cancellation")
    with pytest.raises(asyncio.CancelledError, match="external review cancellation"):
        await task
    assert cleanup_joined == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("body_error", [False, True])
async def test_self_raised_cleanup_cancellation_remains_optional(body_error, caplog):
    @asynccontextmanager
    async def scope():
        try:
            yield
        finally:
            raise asyncio.CancelledError("provider cancelled its own cleanup")

    async def run():
        async with provider_execution_scope(SimpleNamespace(provider_execution_scope=scope)):
            if body_error:
                raise ValueError("original body failure")
            return "completed result"

    if body_error:
        with pytest.raises(ValueError, match="original body failure"):
            await run()
    else:
        assert await run() == "completed result"
    assert "provider cleanup was cancelled" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["return", "error", "cancel"])
async def test_cleanup_failure_preserves_owner_outcome(outcome, caplog):
    entered = asyncio.Event()

    @asynccontextmanager
    async def scope():
        try:
            yield
        finally:
            raise RuntimeError("optional transport unavailable")

    async def run():
        async with provider_execution_scope(SimpleNamespace(provider_execution_scope=scope)):
            entered.set()
            if outcome == "error":
                raise ValueError("original tool failure")
            if outcome == "cancel":
                await asyncio.Event().wait()
            return "useful review"

    task = asyncio.create_task(run())
    await entered.wait()
    if outcome == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    elif outcome == "error":
        with pytest.raises(ValueError, match="original tool failure"):
            await task
    else:
        assert await task == "useful review"
    assert "provider cleanup unavailable" in caplog.text


@pytest.mark.asyncio
async def test_cleanup_is_bounded_without_real_sleep(monkeypatch, caplog):
    from llm import provider_lifecycle
    native_timeout = asyncio.timeout
    monkeypatch.setattr(provider_lifecycle.asyncio, "timeout", lambda seconds: native_timeout(0))
    finalized = []

    @asynccontextmanager
    async def scope():
        try:
            yield
        finally:
            try:
                await asyncio.Event().wait()
            finally:
                finalized.append(True)

    async with provider_execution_scope(SimpleNamespace(provider_execution_scope=scope)):
        result = "useful review"
    assert finalized == [True] and result == "useful review"
    assert "provider cleanup unavailable" in caplog.text


@pytest.mark.asyncio
async def test_quality_capture_delegates_scope_through_copy_and_binding():
    from copy import copy
    from service.review.quality_capture import ReviewQualityCaptureLLM
    events = []

    class ScopedModel:
        @asynccontextmanager
        async def provider_execution_scope(self):
            events.append("entered")
            try:
                yield
            finally:
                events.append("closed")

        def model_copy(self, **kwargs):
            return copy(self)

        def bind_tools(self, tools, **kwargs):
            return copy(self)

    model = ReviewQualityCaptureLLM(ScopedModel(), SimpleNamespace())
    copied = model.model_copy()
    bound = copied.bind_tools([])
    async with provider_execution_scope(bound):
        assert events == ['entered']
    assert events == ['entered', 'closed']
