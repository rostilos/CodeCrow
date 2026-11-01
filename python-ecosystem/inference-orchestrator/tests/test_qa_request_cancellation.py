import asyncio
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.routers.qa_documentation import QaDocumentationRequest, generate_qa_documentation
from service.qa_documentation.qa_doc_service import QaDocumentationService


@pytest.mark.asyncio
@pytest.mark.parametrize("caller_cancelled", [False, True])
async def test_disconnected_or_cancelled_request_stops_all_batches(monkeypatch, caller_cancelled):
    started = asyncio.Event()
    disconnected = asyncio.Event()
    stopped = []
    batches = []

    async def batch(index):
        batches.append(index)
        if len(batches) == 3:
            started.set()
        try:
            await asyncio.Future()
        finally:
            stopped.append(index)

    async def generate(**kwargs):
        await asyncio.gather(*(batch(index) for index in range(3)))

    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}

    monkeypatch.setattr(QaDocumentationService, "generate", AsyncMock(side_effect=generate))
    request = Request({"type": "http"}, receive=receive)
    task = asyncio.create_task(generate_qa_documentation(request, QaDocumentationRequest(
        project_id=7, project_name="repo", pr_number=9)))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        if caller_cancelled:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            disconnected.set()
            with pytest.raises(HTTPException) as error:
                await asyncio.wait_for(task, timeout=2)
            assert error.value.status_code == 499
        assert sorted(stopped) == [0, 1, 2]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_generation_completion_cleans_up_disconnect_listener(monkeypatch, failure):
    listening = asyncio.Event()
    closed = asyncio.Event()

    async def receive():
        listening.set()
        try:
            await asyncio.Future()
        finally:
            closed.set()

    async def generate(**kwargs):
        await listening.wait()
        if failure:
            raise RuntimeError("generation failed")
        return {"documentation": "QA guide", "documentation_needed": True}

    monkeypatch.setattr(QaDocumentationService, "generate", AsyncMock(side_effect=generate))
    result = generate_qa_documentation(Request({"type": "http"}, receive=receive),
                                      QaDocumentationRequest(project_id=7, project_name="repo"))
    if failure:
        with pytest.raises(RuntimeError, match="generation failed"):
            await asyncio.wait_for(result, timeout=2)
    else:
        assert (await asyncio.wait_for(result, timeout=2)).documentation == "QA guide"
    assert closed.is_set()


@pytest.mark.asyncio
async def test_cancellation_during_mcp_initialization_closes_sessions(monkeypatch):
    import mcp_use
    import service.agent
    from service.qa_documentation.repository_tools import qa_repository_tools
    from utils.mcp_config import MCPConfigBuilder

    client = SimpleNamespace(close_all_sessions=AsyncMock())
    agent = SimpleNamespace(initialize=AsyncMock(side_effect=asyncio.CancelledError))
    monkeypatch.setattr(MCPConfigBuilder, "build_jvm_props", lambda **kwargs: {})
    monkeypatch.setattr(MCPConfigBuilder, "build_config", lambda *args, **kwargs: {"mcpServers": {}})
    monkeypatch.setattr(mcp_use.MCPClient, "from_dict", lambda config: client)
    monkeypatch.setattr(service.agent, "AgentExecutionService", lambda **kwargs: agent)
    monkeypatch.setattr("service.qa_documentation.repository_tools.install_per_connection_tool_serialization",
                        lambda client: None)
    with pytest.raises(asyncio.CancelledError):
        async with qa_repository_tools(None, None, {"project_id": 7}):
            pytest.fail("cancelled initialization must not start generation")
    client.close_all_sessions.assert_awaited_once()
