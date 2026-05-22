import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.command_queue_consumer import CommandQueueConsumer


class FakeRedis:
    def __init__(self):
        self.events = []

    async def lpush(self, key, value):
        self.events.append((key, json.loads(value)))


def _ask_request():
    return {
        "projectId": 1,
        "projectVcsWorkspace": "ws",
        "projectVcsRepoSlug": "repo",
        "projectWorkspace": "workspace",
        "projectNamespace": "namespace",
        "aiProvider": "OPENAI_COMPATIBLE",
        "aiModel": "model",
        "aiApiKey": "key",
        "question": "describe this PR",
        "pullRequestId": 7,
    }


def _summarize_request():
    return {
        "projectId": 1,
        "projectVcsWorkspace": "ws",
        "projectVcsRepoSlug": "repo",
        "projectWorkspace": "workspace",
        "projectNamespace": "namespace",
        "aiProvider": "OPENAI_COMPATIBLE",
        "aiModel": "model",
        "aiApiKey": "key",
        "pullRequestId": 7,
    }


def _payload(command_type, request):
    return json.dumps({
        "job_id": f"job-{command_type}",
        "command_type": command_type,
        "request": request,
    })


def _consumer(command_service):
    consumer = CommandQueueConsumer(command_service)
    consumer._redis = FakeRedis()
    return consumer


async def _handle_and_collect_events(consumer, payload):
    await consumer._handle_job(payload)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    return [event for _, event in consumer._redis.events]


@pytest.mark.asyncio(loop_scope="function")
async def test_error_result_is_published_as_error_without_final():
    command_service = MagicMock()
    command_service.process_ask = AsyncMock(return_value={"error": "provider failed"})
    consumer = _consumer(command_service)

    events = await _handle_and_collect_events(consumer, _payload("ask", _ask_request()))

    assert any(event["type"] == "error" and event["message"] == "provider failed" for event in events)
    assert not any(event["type"] == "final" for event in events)


@pytest.mark.asyncio(loop_scope="function")
async def test_empty_ask_answer_is_published_as_error_without_final():
    command_service = MagicMock()
    command_service.process_ask = AsyncMock(return_value={"answer": None})
    consumer = _consumer(command_service)

    events = await _handle_and_collect_events(consumer, _payload("ask", _ask_request()))

    assert any(
        event["type"] == "error" and event["message"] == "AI service returned an empty answer"
        for event in events
    )
    assert not any(event["type"] == "final" for event in events)


@pytest.mark.asyncio(loop_scope="function")
async def test_successful_ask_answer_is_published_as_final():
    command_service = MagicMock()
    command_service.process_ask = AsyncMock(return_value={"answer": "42"})
    consumer = _consumer(command_service)

    events = await _handle_and_collect_events(consumer, _payload("ask", _ask_request()))

    assert {"type": "final", "result": {"answer": "42"}} in events
    assert not any(event["type"] == "error" for event in events)


@pytest.mark.asyncio(loop_scope="function")
async def test_empty_summarize_result_is_published_as_error_without_final():
    command_service = MagicMock()
    command_service.process_summarize = AsyncMock(return_value={"summary": "No output generated"})
    consumer = _consumer(command_service)

    events = await _handle_and_collect_events(consumer, _payload("summarize", _summarize_request()))

    assert any(
        event["type"] == "error" and event["message"] == "AI service returned an empty summary"
        for event in events
    )
    assert not any(event["type"] == "final" for event in events)
