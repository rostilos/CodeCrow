import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

import server.command_queue_consumer as command_queue_module
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

    assert any(
        event["type"] == "error"
        and event["message"] == "AI command failed"
        and event["reasonCode"] == "command_processing_failed"
        for event in events
    )
    assert not any(event["type"] == "final" for event in events)


@pytest.mark.asyncio(loop_scope="function")
async def test_command_failures_do_not_disclose_credentials_or_model_output(caplog):
    credential = "COMMAND-CREDENTIAL-SENTINEL-a0386c"
    source = "COMMAND-SOURCE-SENTINEL-538a91"
    request = _ask_request()
    request["aiApiKey"] = credential
    command_service = MagicMock()
    command_service.process_ask = AsyncMock(
        return_value={"error": f"provider echoed {source}"}
    )
    consumer = _consumer(command_service)
    caplog.set_level(logging.DEBUG, logger=command_queue_module.__name__)

    events = await _handle_and_collect_events(
        consumer,
        _payload("ask", request),
    )

    observable = caplog.text + json.dumps(events)
    assert credential not in observable
    assert source not in observable
    assert any(
        event.get("reasonCode") == "command_processing_failed"
        for event in events
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_command_consumer_start_does_not_log_redis_credentials(
    monkeypatch,
    caplog,
):
    credential = "COMMAND-REDIS-CREDENTIAL-SENTINEL-c1408f"
    redis_client = MagicMock()
    redis_client.aclose = AsyncMock()
    monkeypatch.setenv(
        "REDIS_URL",
        f"redis://worker:{credential}@redis.internal:6379/1",
    )
    monkeypatch.setattr(
        command_queue_module.redis,
        "from_url",
        MagicMock(return_value=redis_client),
    )
    consumer = CommandQueueConsumer(MagicMock())
    consumer._consume_loop = AsyncMock()
    caplog.set_level(logging.INFO, logger=command_queue_module.__name__)

    await consumer.start()
    await asyncio.sleep(0)
    await consumer.stop()

    assert credential not in caplog.text


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
