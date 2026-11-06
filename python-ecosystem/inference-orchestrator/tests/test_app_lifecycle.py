"""Partial startup and exceptional shutdown still retire owned resources."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.app import lifespan


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["command_start", "application"])
async def test_lifespan_cleans_all_owned_resources_after_failure(failure):
    services = [MagicMock(rag_client=SimpleNamespace(close=AsyncMock())) for _ in range(2)]
    consumers = [MagicMock(start=AsyncMock(), stop=AsyncMock()) for _ in range(2)]
    if failure == "command_start":
        consumers[1].start.side_effect = RuntimeError("startup failure")
    app = SimpleNamespace(state=SimpleNamespace())
    with (
        patch("api.app.ReviewService", return_value=services[0]),
        patch("api.app.CommandService", return_value=services[1]),
        patch("server.queue_consumer.RedisQueueConsumer", return_value=consumers[0]),
        patch("server.command_queue_consumer.CommandQueueConsumer", return_value=consumers[1]),
    ):
        with pytest.raises(RuntimeError):
            async with lifespan(app):
                raise RuntimeError("application failure")
    for consumer in consumers:
        consumer.stop.assert_awaited_once_with()
    for service in services:
        service.rag_client.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_command_construction_failure_closes_already_constructed_review_client():
    service = MagicMock(rag_client=SimpleNamespace(close=AsyncMock()))
    with (
        patch("api.app.ReviewService", return_value=service),
        patch("api.app.CommandService", side_effect=RuntimeError("configuration failure")),
    ):
        with pytest.raises(RuntimeError):
            async with lifespan(SimpleNamespace(state=SimpleNamespace())):
                pytest.fail("Startup must not succeed")
    service.rag_client.close.assert_awaited_once_with()
