"""Integration tests: health endpoint (no auth required)."""
import pytest


@pytest.mark.asyncio(loop_scope="function")
async def test_health_returns_200(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio(loop_scope="function")
async def test_health_no_auth_needed(client):
    """Health must be reachable WITHOUT x-service-secret."""
    resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio(loop_scope="function")
async def test_health_rejects_a_stopped_consumer(client, io_app):
    io_app.state.queue_consumer.is_running = False
    try:
        resp = await client.get("/health")
    finally:
        io_app.state.queue_consumer.is_running = True

    assert resp.status_code == 503
