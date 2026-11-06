"""QA failures remain attributable and cannot restart uncertain paid work."""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from openai import InternalServerError, RateLimitError

from api.routers.qa_documentation import router
from service.qa_documentation.qa_doc_service import QaDocumentationService
from service.qa_documentation.qa_doc_orchestrator import QaDocOrchestrator


def provider_failure(status):
    response = httpx.Response(status, request=httpx.Request("POST", "https://provider.invalid"))
    error_type = RateLimitError if status == 429 else InternalServerError
    return error_type("private provider response", response=response, body=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [False, True])
async def test_http_failure_correlates_traceback_without_exposing_provider_body(monkeypatch, caplog, wrapped):
    error = provider_failure(503)
    if wrapped:
        cause = error
        error = RuntimeError("stage failed")
        error.__cause__ = cause
    generate = AsyncMock(side_effect=error)
    monkeypatch.setattr(QaDocumentationService, "generate", generate)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/qa-documentation", json={
            "project_id": 42, "project_name": "repo", "pr_number": 7,
            "ai_provider": "openai", "ai_model": "test-model",
            "ai_api_key": "private-api-key", "diff": "private-source",
        })
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert str(UUID(detail["error_id"])) == detail["error_id"]
    assert detail["error_type"] == "InternalServerError"
    assert detail["upstream_status"] == 503
    assert "private" not in response.text
    record = next(record for record in caplog.records if detail["error_id"] in record.getMessage())
    assert "project_id=42 pr=7 provider=openai model=test-model" in record.getMessage()
    assert record.exc_info[1] is error
    assert "private-api-key" not in caplog.text and "private-source" not in caplog.text
    generate.assert_awaited_once()


def run_args():
    return dict(project_name="repo", pr_number=7, issues_found=0, files_analyzed=1,
                pr_metadata={}, template_mode="BASE", custom_template=None, task_context=None,
                delta_diff=None, enrichment_data=None, previous_documentation=None, is_same_pr_rerun=False,
                diff="diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-old\n+new\n" + " context\n" * 1_000,
                changed_file_paths=["a.py"])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["BASE", "RAW", "CUSTOM"])
@pytest.mark.parametrize("structured", [False, True])
async def test_document_returns_success_without_paid_format_repair(monkeypatch, caplog, mode, structured):
    document = "# QA guide\n\n## Checkout\n**Place an order** (HIGH)\nVerify the order appears after payment."
    if structured:
        document = json.dumps({"sections": [
            {"kind": "overview", "heading": "# QA guide", "content": "Checkout now supports saved cards."},
            {"kind": "test_cases", "heading": "Checkout", "content": "**Place an order** (HIGH)\nVerify the order appears after payment."},
            {"kind": "environment", "heading": "Setup", "content": "Use the QA payment account."},
        ]})
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
        SimpleNamespace(content="YES"), SimpleNamespace(content=document),
    ]))
    monkeypatch.setattr(QaDocumentationService, "_create_llm", lambda *args, **kwargs: llm)

    @asynccontextmanager
    async def no_repository_tools(*args):
        yield None

    monkeypatch.setattr("service.qa_documentation.repository_tools.qa_repository_tools", no_repository_tools)
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/qa-documentation", json={
            "project_id": 402, "project_name": "repo", "pr_number": 10172,
            "template_mode": mode, "custom_template": "Describe checkout testing",
            "diff": "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-old\n+new\n",
        })

    assert response.status_code == 200, response.text
    assert response.json()["documentation_needed"] is True
    result = response.json()["documentation"]
    if structured:
        assert QaDocOrchestrator._has_complete_shareable_sections(result)
        assert "Verify the order appears after payment." in result
        assert "Use the QA payment account." in result
        assert "public preview unavailable" not in caplog.text
    else:
        assert result.startswith(document)
        assert "public preview unavailable" in caplog.text
    assert response.json()["template_mode_used"] == mode
    assert llm.ainvoke.await_count == 2  # Relevance and generation only.


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["503", "429", "timeout", "disconnect"])
@pytest.mark.parametrize("stage", ["relevance", "batch", "cross_impact", "cross_shards", "aggregate"])
async def test_uncertain_stage_failure_never_starts_another_generation(monkeypatch, stage, failure):
    error = (provider_failure(int(failure)) if failure.isdigit() else
             TimeoutError("lost result") if failure == "timeout" else httpx.ReadError("disconnected"))
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=error))
    orchestrator = QaDocOrchestrator(llm)
    if stage != "relevance":
        monkeypatch.setattr(orchestrator, "_is_documentation_needed", AsyncMock(return_value=True))
    monkeypatch.setattr(orchestrator, "build_dependency_batches",
                        lambda *args, **kwargs: [[{"file_info": SimpleNamespace(path="a.py")}]])
    if stage in {"cross_impact", "cross_shards", "aggregate"}:
        prior = [{"raw_analysis": "change evidence\n" * 40_000}] if stage == "cross_shards" else [{"file_analyses": []}]
        monkeypatch.setattr(orchestrator, "_execute_stage_1", AsyncMock(return_value=prior))
    if stage == "aggregate":
        monkeypatch.setattr(orchestrator, "_execute_stage_2", AsyncMock(return_value={}))
    fallback = AsyncMock()
    monkeypatch.setattr(orchestrator, "_run_single_pass", fallback)
    with pytest.raises(type(error)) as caught:
        await orchestrator.run(**run_args())
    assert caught.value is error
    llm.ainvoke.assert_awaited_once()
    fallback.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_batch_cancels_active_batches_and_does_not_start_queued_batches(monkeypatch):
    started, stopped = [], []
    all_active = asyncio.Event()
    error = provider_failure(503)

    async def invoke(messages):
        index = len(started)
        started.append(index)
        if len(started) == 5:
            all_active.set()
        try:
            await all_active.wait()
            if index == 0:
                raise error
            await asyncio.Future()
        finally:
            stopped.append(index)

    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=invoke))
    orchestrator = QaDocOrchestrator(llm)
    monkeypatch.setattr(orchestrator, "_is_documentation_needed", AsyncMock(return_value=True))
    monkeypatch.setattr(orchestrator, "build_dependency_batches", lambda *args, **kwargs: [
        [{"file_info": SimpleNamespace(path="a.py")}] for _ in range(12)
    ])
    fallback = AsyncMock()
    monkeypatch.setattr(orchestrator, "_run_single_pass", fallback)
    with pytest.raises(InternalServerError) as caught:
        await asyncio.wait_for(orchestrator.run(**run_args()), timeout=2)
    assert caught.value is error
    assert llm.ainvoke.await_count == 5
    assert sorted(stopped) == started == list(range(5))
    fallback.assert_not_awaited()
