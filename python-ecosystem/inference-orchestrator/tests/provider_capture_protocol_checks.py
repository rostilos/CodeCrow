"""Offline checks at real SDK transports; no provider calls or model evaluation."""
import asyncio
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import httpx2
import socket
import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from llm.llm_factory import LLMFactory
from llm.request_capture import (
    attach_http_capture, model_capture, queue_capture_context, review_capture,
)
from service.review.agent_calls import ReviewAgentSession

SOURCE = "def boundary(value):\n    return value / 0\n" * 2500
SCHEMAS = [{"name": "readReviewFile", "description": "Read full source", "inputSchema": {
    "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"],
}}]


def request(provider="openrouter", project=91):
    return SimpleNamespace(aiProvider=provider, aiModel="test-model", aiApiKey="private-provider-key",
        projectId=project, projectWorkspace=f"workspace-{project}", projectNamespace="project",
        pullRequestId=17, currentCommitHash="abc123")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline transport test attempted network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


@pytest.fixture
def capture(monkeypatch, tmp_path):
    root = tmp_path / "captures"
    monkeypatch.setenv("REVIEW_QUALITY_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("REVIEW_QUALITY_CAPTURE_OUTPUT_DIR", str(root))
    monkeypatch.delenv("REVIEW_QUALITY_CAPTURE_PROJECT_IDS", raising=False)
    return root


def response_body(provider):
    if provider == "anthropic":
        return {"id": "provider-generation", "type": "message", "role": "assistant", "model": "test-model",
            "content": [{"type": "tool_use", "id": "tool-1", "name": "readReviewFile", "input": {"path": "boundary.py"}}],
            "stop_reason": "tool_use", "usage": {"input_tokens": 111, "output_tokens": 17}}
    if provider in {"google", "google_vertex"}:
        return {"responseId": "provider-generation", "modelVersion": "gemini-2.5-flash",
            "candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"name": "readReviewFile", "args": {"path": "boundary.py"}}},
            ]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 111, "candidatesTokenCount": 17, "totalTokenCount": 128}}
    return {"id": "provider-generation", "object": "chat.completion", "model": "test-model",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [{"id": "tool-1", "type": "function",
                "function": {"name": "readReviewFile", "arguments": '{"path":"boundary.py"}'}}],
        }}], "usage": {"prompt_tokens": 111, "completion_tokens": 17, "total_tokens": 128}}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openrouter", "anthropic", "google", "google_vertex", "openai_compatible"])
async def test_actual_sdk_wire_payload_and_response_are_preserved(provider, capture, monkeypatch):
    from llm import ssrf_safe_transport
    monkeypatch.setattr(ssrf_safe_transport, "_ALLOW_PRIVATE", True)
    observed = []
    response_bytes = json.dumps(response_body(provider)).encode()

    async def transport(_self, outgoing):
        observed.append(outgoing.content)
        # Headers/query may contain secrets; none are part of the artifact.
        protocol = httpx2 if isinstance(outgoing, httpx2.Request) else httpx
        return protocol.Response(200, content=response_bytes, headers={"content-type": "application/json", "set-cookie": "private-cookie"})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", transport)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", transport)
    model = LLMFactory.create_llm(ai_provider=provider,
        ai_model="gemini-2.5-flash" if provider.startswith("google") else "test-model",
        ai_api_key="private-provider-key", ai_base_url="http://localhost:12345/v1" if provider == "openai_compatible" else None,
        ai_custom_parameters={"provider": {"order": ["test-route"]}} if provider == "openrouter" else None)
    req = request(provider)
    with queue_capture_context("analysis-job-32"), review_capture(req):
        session = ReviewAgentSession(model, req, SCHEMAS)
        result = await session.invoke([SystemMessage(content="Verify the changed behavior."), HumanMessage(content=SOURCE)],
                                      stage="verification", batch_ids=["batch-2"])
    assert result.tool_calls[0]["name"] == "readReviewFile"
    artifacts = list(capture.rglob("*.request.body"))
    assert len(artifacts) == 1
    assert artifacts[0].read_bytes() == observed[0]
    wire = json.loads(observed[0])
    assert SOURCE in json.dumps(wire, ensure_ascii=False).replace("\\n", "\n")
    assert "readReviewFile" in json.dumps(wire["tools"])
    if provider == "openrouter":
        assert wire["provider"] == {"order": ["test-route"]}
        assert wire["reasoning"]["effort"] == "medium"
    assert next(capture.rglob("*.response.body")).read_bytes() == response_bytes
    metadata = json.loads(next(capture.rglob("*.complete.json")).read_text())
    assert metadata["stage"] == "verification"
    assert metadata["turn"] == 1 and metadata["batch_ids"] == ["batch-2"]
    assert metadata["job_id"] == "analysis-job-32"
    assert metadata["provider_generation_id"] == "provider-generation"
    assert metadata["response_complete"] is True
    for path in capture.rglob("*"):
        assert path.stat().st_mode & 0o077 == 0
        if path.is_file():
            assert b"private-provider-key" not in path.read_bytes()
            assert b"private-cookie" not in path.read_bytes()


@pytest.mark.asyncio
async def test_sdk_retries_are_separate_attempts_of_the_same_call(capture, monkeypatch):
    observed = []

    async def transport(_self, outgoing):
        observed.append(outgoing.content)
        if len(observed) == 1:
            return httpx2.Response(503, json={"error": {"message": "temporarily unavailable"}})
        return httpx2.Response(200, json=response_body("openrouter"))

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", transport)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", transport)
    model = LLMFactory.create_llm(ai_provider="openrouter", ai_model="test-model", ai_api_key="private-key")
    req = request()
    with review_capture(req):
        await ReviewAgentSession(model, req, SCHEMAS).invoke([HumanMessage(content=SOURCE)], stage="verification")
    complete = [json.loads(path.read_text()) for path in capture.rglob("*.complete.json")]
    assert len(complete) == 2
    assert {item["http_status"] for item in complete} == {200, 503}
    assert {item["attempt"] for item in complete} == {1, 2}
    assert len({item["call_id"] for item in complete}) == 1
    assert len(observed) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", [httpx, httpx2])
async def test_stream_capture_does_not_eagerly_consume_and_preserves_bytes(capture, protocol):
    consumed = []
    chunks = [b'data: {"id":"stream-id","choices":[]}\n\n', b'data: [DONE]\n\n']

    class Stream(protocol.AsyncByteStream):
        async def __aiter__(self):
            for chunk in chunks:
                consumed.append(chunk)
                yield chunk

    async def transport(outgoing):
        return protocol.Response(200, stream=Stream())

    async with protocol.AsyncClient(transport=protocol.MockTransport(transport)) as client:
        attach_http_capture(client)
        req = request()
        with review_capture(req), model_capture(req, stage="stream"):
            async with client.stream("POST", "https://provider.test/generate?key=private-query", json={"messages": [SOURCE]}) as response:
                assert consumed == []
                assert b"".join([chunk async for chunk in response.aiter_bytes()]) == b"".join(chunks)
    assert next(capture.rglob("*.response.body")).read_bytes() == b"".join(chunks)
    metadata = json.loads(next(capture.rglob("*.complete.json")).read_text())
    assert metadata["provider_generation_id"] == "stream-id"
    assert "private-query" not in json.dumps(metadata)


@pytest.mark.asyncio
async def test_concurrent_tenants_and_batch_scopes_cannot_mix(capture):
    async def transport(outgoing):
        await asyncio.sleep(0)
        return httpx.Response(200, json={"id": "generation", "echo": json.loads(outgoing.content)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        attach_http_capture(client)

        async def batch(req, number):
            with model_capture(req, stage="discovery", batch_ids=[f"batch-{number}"]):
                await client.post("https://provider.test/generate", json={"source": f"project-{req.projectId}-batch-{number}"})

        async def review(project):
            req = request(project=project)
            with queue_capture_context(f"job-{project}"), review_capture(req):
                await asyncio.gather(batch(req, 1), batch(req, 2))

        await asyncio.gather(review(91), review(92))
        # A call made outside a review invocation must not inherit a previous tenant.
        await client.post("https://provider.test/generate", json={"source": "unscoped"})
    artifacts = list(capture.rglob("*.complete.json"))
    assert len(artifacts) == 4
    groups = {}
    for path in artifacts:
        metadata = json.loads(path.read_text())
        prefix = path.name.removesuffix(".complete.json")
        source = json.loads(path.with_name(prefix + ".request.body").read_text())["source"]
        project = metadata["projectId"]
        assert f"project-{project}-" in source
        assert metadata["job_id"] == f"job-{project}"
        assert metadata["batch_ids"][0] in source
        groups.setdefault(project, set()).add(metadata["run_id"])
    assert len(groups[91]) == len(groups[92]) == 1
    assert groups[91].isdisjoint(groups[92])


@pytest.mark.asyncio
async def test_failed_sink_and_excluded_project_do_not_affect_provider(capture, monkeypatch, caplog):
    capture.write_text("not a directory")
    req = request()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"id": "ok"}))) as client:
        attach_http_capture(client)
        with review_capture(req), model_capture(req, stage="verification"):
            assert (await client.post("https://provider.test", json={"messages": [SOURCE]})).json() == {"id": "ok"}
        assert "Review wire capture unavailable" in caplog.text
        capture.unlink()
        monkeypatch.setenv("REVIEW_QUALITY_CAPTURE_PROJECT_IDS", "92")
        with review_capture(req), model_capture(req, stage="verification"):
            assert (await client.post("https://provider.test", json={"messages": [SOURCE]})).status_code == 200
        assert not capture.exists()


def test_sync_capture_keeps_original_transport_and_excludes_headers(capture):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"id": "sync"}))) as client:
        attach_http_capture(client)
        attach_http_capture(client)
        req = request()
        with review_capture(req), model_capture(req, stage="sync"):
            response = client.post("https://provider.test", json={"source": SOURCE}, headers={"Authorization": "Bearer private-key"})
        assert response.json()["id"] == "sync"
    assert len(list(capture.rglob("*.request.body"))) == 1
    assert next(capture.rglob("*.response.body")).read_bytes() == response.content



@pytest.mark.asyncio
async def test_transport_failure_records_attempt_without_masking_error(capture):
    async def transport(outgoing):
        raise httpx.ReadTimeout("simulated timeout", request=outgoing)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        attach_http_capture(client)
        req = request()
        with pytest.raises(httpx.ReadTimeout, match="simulated timeout"):
            with review_capture(req), model_capture(req, stage="verification"):
                await client.post("https://provider.test", json={"source": SOURCE})
    metadata = json.loads(next(capture.rglob("*.complete.json")).read_text())
    assert metadata["response_complete"] is False
    assert metadata["capture_or_transport_error_type"] == "ReadTimeout"
    assert json.loads(next(capture.rglob("*.request.body")).read_text())["source"] == SOURCE


@pytest.mark.parametrize("kind", ["symlink", "shared"])
def test_insecure_capture_root_is_skipped_without_writing_source(capture, tmp_path, kind):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    if kind == "symlink":
        capture.symlink_to(target, target_is_directory=True)
    else:
        capture.mkdir(mode=0o755)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"id": "ok"}))) as client:
        attach_http_capture(client)
        req = request()
        with review_capture(req), model_capture(req, stage="verification"):
            assert client.post("https://provider.test", json={"source": SOURCE}).status_code == 200
    assert not list(target.iterdir())
    assert not list(capture.iterdir())


@pytest.mark.asyncio
async def test_native_verification_and_final_json_reconciliation_capture_distinct_wire_calls(capture, monkeypatch):
    from service.review.model_calls import invoke_json

    observed = []
    groups = {"groups": [{"memberIds": ["issue-1", "issue-2"], "representativeId": "issue-1",
                          "rationale": "Same zero denominator and repair"}]}

    async def transport(_self, outgoing):
        payload = json.loads(outgoing.content)
        observed.append(payload)
        value = response_body("openrouter")
        if "tools" not in payload:
            value["choices"] = [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps(groups),
            }}]
        return httpx2.Response(200, json=value)

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", transport)
    model = LLMFactory.create_llm(ai_provider="openrouter", ai_model="test-model", ai_api_key="private-key")
    req = request()
    with review_capture(req):
        await ReviewAgentSession(model, req, SCHEMAS).invoke([HumanMessage(content=SOURCE)], stage="verification_validate")
        result = await invoke_json(model, req, stage="reconciliation", system="Group confirmed issues.",
                                   payload={"issues": [{"issueId": "issue-1", "reason": "Zero denominator"},
                                                       {"issueId": "issue-2", "reason": "Empty input divides by zero"}]})
    assert result == groups
    assert observed[0]["tools"] and "tools" not in observed[1]
    assert SOURCE not in json.dumps(observed[1])
    assert observed[1]["response_format"] == {"type": "json_object"}
    manifests = [json.loads(path.read_text()) for path in capture.rglob("*.complete.json")]
    assert {item["stage"] for item in manifests} == {"verification_validate", "reconciliation"}
    assert len({item["run_id"] for item in manifests}) == 1
    assert len({item["call_id"] for item in manifests}) == 2
    for manifest in capture.rglob("*.complete.json"):
        record = json.loads(manifest.read_text())
        prefix = manifest.name.removesuffix(".complete.json")
        request_body = json.loads(manifest.with_name(prefix + ".request.body").read_text())
        response = json.loads(manifest.with_name(prefix + ".response.body").read_text())
        assert request_body in observed
        assert response["usage"]["total_tokens"] == 128
        if record["stage"] == "reconciliation":
            assert json.loads(response["choices"][0]["message"]["content"]) == groups


@pytest.mark.asyncio
async def test_compressed_response_keeps_wire_body_and_extracts_generation(capture):
    body = gzip.compress(b'{"id":"compressed-generation","usage":{"total_tokens":99}}')

    class CompressedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield body[:7]
            yield body[7:]

    async def transport(outgoing):
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=CompressedStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        attach_http_capture(client)
        req = request()
        with review_capture(req), model_capture(req, stage="verification"):
            response = await client.post("https://provider.test", json={"source": SOURCE})
        assert response.json()["id"] == "compressed-generation"
    assert next(capture.rglob("*.response.body")).read_bytes() == body
    manifest = json.loads(next(capture.rglob("*.complete.json")).read_text())
    assert manifest["provider_generation_id"] == "compressed-generation"
    assert manifest["body_encoding"] == "gzip" and manifest["response_complete"] is True
