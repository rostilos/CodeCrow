"""Actual SDK reasoning round trips over offline HTTP transports only."""
from __future__ import annotations

import json
import socket
from types import SimpleNamespace

import httpx
import httpx2
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI

from llm.llm_factory import LLMFactory
from llm.openai_adapters import ChatOpenRouter
from service.review.agent_calls import ReviewAgentSession, result_message
from service.review.model_calls import invoke_json
from service.review.verifier import ReviewVerifier

MODEL = "deepseek/deepseek-v4-flash-20260731"
SCHEMAS = [{"name": "readReviewFile", "description": "Read exact source", "inputSchema": {
    "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"],
}}]
DETAILS = [
    {"type": "reasoning.text", "text": "Inspect the full caller contract. " * 500,
     "signature": "opaque-signature", "format": "unknown", "id": "reasoning-1", "index": 0},
    {"type": "reasoning.encrypted", "data": "opaque-encrypted-state", "id": "reasoning-2",
     "format": "anthropic-claude-v1", "index": 1, "provider_extension": {"retain": True}},
]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Reasoning protocol check attempted provider network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setenv("REVIEW_QUALITY_CAPTURE_ENABLED", "false")


def request():
    return SimpleNamespace(aiProvider="openrouter", aiModel=MODEL, pullRequestId="42",
                           projectRules=None, taskContext=None)


def model():
    return LLMFactory.create_llm(ai_provider="openrouter", ai_model=MODEL, ai_api_key="offline-key",
        ai_custom_parameters={"provider": {"only": ["cloudflare"], "allow_fallbacks": False}})


def completion(message):
    return {"id": "offline-generation", "object": "chat.completion", "created": 1, "model": MODEL,
            "choices": [{"index": 0, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
                         "message": {"role": "assistant", **message}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
                      "completion_tokens_details": {"reasoning_tokens": 40}}}


def read_message(**reasoning):
    return {"content": None, "tool_calls": [{"id": "source-read", "type": "function",
        "function": {"name": "readReviewFile", "arguments": json.dumps({"path": "caller.py"})}}], **reasoning}


def intercept(monkeypatch, replies):
    observed = []

    async def transport(_self, outgoing):
        observed.append(json.loads(outgoing.content))
        value = replies[len(observed) - 1]
        protocol = httpx2 if isinstance(outgoing, httpx2.Request) else httpx
        if isinstance(value, dict) and observed[-1].get("stream"):
            message = dict(value["choices"][0]["message"])
            if message.get("tool_calls"):
                message["tool_calls"] = [{"index": index, **call} for index, call in enumerate(message["tool_calls"])]
            value = stream([message], finish_reason=value["choices"][0]["finish_reason"], usage=value.get("usage"))
        if isinstance(value, bytes):
            return protocol.Response(200, content=value, headers={"content-type": "text/event-stream"})
        return protocol.Response(200, json=value)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", transport)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", transport)
    return observed


@pytest.mark.asyncio
async def test_production_verifier_replays_exact_reasoning_and_native_tool_ids(monkeypatch, tmp_path):
    target, overlay = tmp_path / "target", tmp_path / "overlay"
    target.mkdir()
    overlay.mkdir()
    (target / "caller.py").write_text("def caller():\n    changed()\n")
    (overlay / "manifest.json").write_text(json.dumps({"changedFiles": [], "deletedFiles": []}))
    part = SimpleNamespace(id="part-a", path="changed.py", side="proposed", anchors={1: "changed()"},
                           diff="@@ -1 +1 @@\n-before()\n+changed()\n")
    issue = {"partId": part.id, "file": part.path, "line": 1, "title": "Supported defect", "reason": "Concrete trigger"}
    first = read_message(reasoning_details=DETAILS, reasoning="A duplicated convenience representation")
    final = {"content": json.dumps({"decisions": [{"candidateId": "candidate-1", "verdict": "keep",
              "reason": "The caller reaches the changed operation.", "evidenceIds": ["diff:part-a", "read-1"]}]}),
             "reasoning_details": [{"type": "reasoning.text", "text": "Conclude from the source."}]}
    observed = intercept(monkeypatch, [completion(first), completion(final)])
    result = await ReviewVerifier(None).verify(llm=model(), request=request(), findings=[issue],
        summaries=[], parts=[part], binding={"target_repo_path": str(target), "review_overlay_path": str(overlay)})
    assert result.issues == [issue] and not result.diagnostics
    assert len(observed) == 2
    assert all(item["stream"] is True for item in observed)
    prior = observed[1]["messages"][-2]
    assert prior["reasoning_details"] == DETAILS
    assert "reasoning" not in prior and "reasoning_content" not in prior
    assert prior["tool_calls"] == first["tool_calls"]
    assert observed[1]["messages"][-1]["tool_call_id"] == "source-read"
    assert observed[1]["messages"][:2] == observed[0]["messages"]
    assert all(item["reasoning"] == {"effort": "medium"} for item in observed)
    assert all(item["provider"] == {"only": ["cloudflare"], "allow_fallbacks": False} for item in observed)
    assert all("max_tokens" not in item and "max_completion_tokens" not in item for item in observed)
    assert "reasoning" not in str(result.issues)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["reasoning", "reasoning_content"])
async def test_plaintext_provider_reasoning_alias_round_trips_without_fabrication(monkeypatch, field):
    text = "An exact, provider-returned continuation. " * 100
    observed = intercept(monkeypatch, [completion(read_message(**{field: text})), completion({"content": '{"decisions": []}'})])
    session = ReviewAgentSession(model(), request(), SCHEMAS)
    messages = [HumanMessage(content="Check a changed caller.")]
    first = await session.invoke(messages, stage="verification_validate")
    messages.extend([first.response, result_message(first.tool_calls[0], {"status": "ready", "content": "caller()"})])
    await session.invoke(messages, stage="verification_validate")
    assert observed[1]["messages"][-2][field] == text
    assert set(observed[1]["messages"][-2]) == {"role", "content", "tool_calls", field}


def stream(deltas, finish_reason="tool_calls", usage=None):
    chunks = [{"id": "offline-generation", "object": "chat.completion.chunk", "created": 1, "model": MODEL,
               "choices": [{"index": 0, "delta": delta, "finish_reason": None}]} for delta in deltas]
    chunks.append({"id": "offline-generation", "object": "chat.completion.chunk", "created": 1, "model": MODEL,
                   "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                   **({"usage": usage} if usage else {})})
    return ("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()


@pytest.mark.asyncio
async def test_streamed_reasoning_keeps_block_order_and_does_not_duplicate_metadata(monkeypatch):
    first = {"type": "reasoning.text", "text": "Inspect ", "id": "same-id", "format": "unknown", "index": 0}
    second = {**first, "text": "the caller.", "signature": "opaque-signature"}
    encrypted = {"type": "reasoning.encrypted", "data": "opaque-encrypted", "id": "encrypted-id", "index": 1}
    read = read_message()["tool_calls"][0]
    wire_stream = stream([{"role": "assistant", "reasoning_details": [first]},
                          {"reasoning_details": [second]}, {"reasoning_details": [encrypted]},
                          {"tool_calls": [{"index": 0, **read}]}])
    observed = intercept(monkeypatch, [wire_stream, stream([{"role": "assistant", "content": '{"decisions": []}'}])])
    llm = model()
    llm.streaming = True
    session = ReviewAgentSession(llm, request(), SCHEMAS)
    messages = [HumanMessage(content="Check the caller.")]
    turn = await session.invoke(messages, stage="verification_validate")
    messages.extend([turn.response, result_message(turn.tool_calls[0], {"status": "ready", "content": "caller()"})])
    await session.invoke(messages, stage="verification_validate")
    assert observed[1]["messages"][-2]["reasoning_details"] == [
        {**first, "text": "Inspect the caller.", "signature": "opaque-signature"}, encrypted]
    assert "_openrouter_reasoning_detail_fragments" not in json.dumps(observed)


@pytest.mark.asyncio
async def test_json_discovery_has_unchanged_content_options_and_no_tool_history(monkeypatch):
    observed = intercept(monkeypatch, [completion({"content": '{"findings": [], "summary": {}}',
                                                  "reasoning_details": DETAILS})])
    result = await invoke_json(model(), request(), stage="discovery", system="Review the change.", payload={"diff": "+changed()"})
    assert result == {"findings": [], "summary": {}}
    assert observed[0]["response_format"] == {"type": "json_object"}
    assert observed[0]["messages"] == [{"role": "system", "content": "Review the change."},
                                        {"role": "user", "content": '{"diff": "+changed()"}'}]
    assert "tools" not in observed[0]


def test_openrouter_reasoning_is_not_injected_into_unrelated_provider_payloads():
    assistant = AIMessage(content="", additional_kwargs={"reasoning_details": DETAILS},
                          tool_calls=[{"id": "source", "name": "readReviewFile", "args": {"path": "caller.py"}}])
    messages = [HumanMessage(content="Inspect."), assistant, ToolMessage(content="source", tool_call_id="source")]
    direct = ChatOpenAI(api_key="offline-key", model="unrelated-model")
    assert "reasoning_details" not in direct._get_request_payload(messages)["messages"][1]
    router = ChatOpenRouter(api_key="offline-key", model=MODEL)
    assert router._get_request_payload(messages)["messages"][1]["reasoning_details"] == DETAILS


@pytest.mark.asyncio
async def test_nonstreaming_adapter_still_round_trips_native_reasoning(monkeypatch):
    observed = intercept(monkeypatch, [completion(read_message(reasoning_details=DETAILS)),
                                       completion({"content": "Done"})])
    llm = model().bind_tools([{"name": "readReviewFile", "description": "Read", "parameters": {"type": "object"}}])
    messages = [HumanMessage(content="Inspect.")]
    reply = await llm.ainvoke(messages)
    await llm.ainvoke([*messages, reply, ToolMessage(content="source", tool_call_id="source-read")])
    assert observed[0]["stream"] is False
    assert observed[1]["messages"][-2]["reasoning_details"] == DETAILS


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["elapsed_timeout", "external_cancel"])
async def test_keepalives_do_not_bypass_elapsed_deadline_and_cancel_closes_one_stream(monkeypatch, caplog, ending):
    import asyncio

    monkeypatch.setenv("REVIEW_MODEL_CALL_TIMEOUT_SECONDS", "0.05" if ending == "elapsed_timeout" else "900")
    started = asyncio.Event()

    class WaitingStream(httpx.AsyncByteStream, httpx2.AsyncByteStream):
        closed = False
        keepalives = 0

        async def __aiter__(self):
            initial = {"id": "waiting-generation", "provider": "Cloudflare", "object": "chat.completion.chunk",
                       "created": 1, "model": MODEL, "choices": [{"index": 0, "finish_reason": None,
                       "delta": {"role": "assistant", "reasoning": "private-provider-reasoning"}}]}
            yield ("data: " + json.dumps(initial) + "\n\n").encode()
            started.set()
            while True:
                self.keepalives += 1
                yield b": OPENROUTER PROCESSING\n\n"
                await asyncio.sleep(0.005)

        async def aclose(self):
            self.closed = True

    waiting = WaitingStream()
    calls = []

    async def transport(_self, outgoing):
        calls.append(json.loads(outgoing.content))
        protocol = httpx2 if isinstance(outgoing, httpx2.Request) else httpx
        return protocol.Response(200, stream=waiting, headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", transport)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", transport)
    with caplog.at_level("INFO", logger="llm.review_invocation"):
        session = ReviewAgentSession(model(), request(), SCHEMAS)
        task = asyncio.create_task(session.invoke([HumanMessage(content="Inspect.")], stage="verification_validate"))
        await started.wait()
        if ending == "external_cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(TimeoutError, match="configured 0.05s elapsed timeout"):
                await task
    assert len(calls) == 1 and calls[0]["stream"] is True
    assert waiting.closed and waiting.keepalives > 0
    assert "generation=waiting-generation" in caplog.text and "reasoning_fragments=1" in caplog.text
    assert "private-provider-reasoning" not in caplog.text


@pytest.mark.asyncio
async def test_inconsistent_provider_usage_is_observable_without_changing_output_or_counts(monkeypatch, caplog):
    raw = completion({"content": '{"decisions": []}'})
    raw["usage"] = {"prompt_tokens": 0, "completion_tokens": 1, "total_tokens": 1,
                    "completion_tokens_details": {"reasoning_tokens": 80390}}
    intercept(monkeypatch, [raw])
    with caplog.at_level("WARNING", logger="llm.review_invocation"):
        turn = await ReviewAgentSession(model(), request(), SCHEMAS).invoke([HumanMessage(content="Inspect the change.")],
                                                                           stage="verification_validate")
    assert turn.output == {"decisions": []}
    assert turn.response.usage_metadata["output_tokens"] == 1
    assert turn.response.usage_metadata["output_token_details"]["reasoning"] == 80390
    assert "OpenRouter reported inconsistent usage" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_explicit_empty_reasoning_details_remains_native_continuation_state(monkeypatch, streaming):
    observed = intercept(monkeypatch, [completion(read_message(reasoning_details=[])),
                                       completion({"content": "Done"})])
    llm = model().bind_tools([{"name": "readReviewFile", "description": "Read", "parameters": {"type": "object"}}])
    messages = [HumanMessage(content="Inspect.")]
    reply = await llm.ainvoke(messages, stream=streaming)
    await llm.ainvoke([*messages, reply, ToolMessage(content="source", tool_call_id="source-read")], stream=streaming)
    assert observed[1]["messages"][-2]["reasoning_details"] == []


@pytest.mark.asyncio
async def test_native_stream_reassembles_text_and_summary_without_merging_opaque_or_separate_blocks(monkeypatch):
    raw = [
        {"type": "reasoning.text", "text": "Inspect ", "index": 0, "signature": "opaque-signature"},
        {"type": "reasoning.text", "text": "caller.", "index": 0, "signature": "opaque-signature"},
        {"type": "reasoning.encrypted", "data": "first-opaque", "index": 0},
        {"type": "reasoning.encrypted", "data": "second-opaque", "index": 0},
        {"type": "reasoning.summary", "summary": "One ", "index": 0},
        {"type": "reasoning.summary", "summary": "summary.", "index": 0},
        {"type": "reasoning.text", "text": "Conclude.", "index": 0},
    ]
    expected = [{**raw[0], "text": "Inspect caller."}, raw[2], raw[3],
                {**raw[4], "summary": "One summary."}, raw[6]]
    read = read_message()["tool_calls"][0]
    wire = stream([*({"reasoning_details": [block]} for block in raw), {"tool_calls": [{"index": 0, **read}]}])
    observed = intercept(monkeypatch, [wire, completion({"content": '{"decisions": []}'})])
    session = ReviewAgentSession(model(), request(), SCHEMAS)
    messages = [HumanMessage(content="Inspect.")]
    reply = await session.invoke(messages, stage="verification_validate")
    await session.invoke([*messages, reply.response, result_message(reply.tool_calls[0], {"content": "caller()"})],
                         stage="verification_validate")
    assert observed[1]["messages"][-2]["reasoning_details"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_provider_stream_error_is_failure_without_partial_json_acceptance_or_paid_replay(monkeypatch, partial):
    from openai import APIError

    chunks = [{"id": "error-generation", "object": "chat.completion.chunk", "created": 1, "model": MODEL,
               "choices": [{"index": 0, "delta": {"content": '{"decisions": []}'}, "finish_reason": None}]}] if partial else []
    chunks.append({"id": "error-generation", "object": "chat.completion.chunk", "created": 1, "model": MODEL,
                   "error": {"code": "server_error", "message": "Provider disconnected"},
                   "choices": [{"index": 0, "delta": {}, "finish_reason": "error"}]})
    wire = ("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
    observed = intercept(monkeypatch, [wire])
    with pytest.raises(APIError, match="Provider disconnected"):
        await ReviewAgentSession(model(), request(), SCHEMAS).invoke([HumanMessage(content="Inspect.")],
                                                                     stage="verification_validate")
    assert len(observed) == 1


@pytest.mark.asyncio
async def test_openrouter_terminal_usage_chunk_keeps_single_finish_reason_and_usage(monkeypatch):
    usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
             "completion_tokens_details": {"reasoning_tokens": 40}}
    terminal = {"id": "terminal-generation", "object": "chat.completion.chunk", "created": 1, "model": MODEL,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""},
                             "finish_reason": "stop", "native_finish_reason": "stop"}]}
    chunks = [{**terminal, "choices": [{"index": 0, "delta": {"content": '{"decisions": []}'},
                                        "finish_reason": None}]}, terminal, {**terminal, "usage": usage}]
    wire = ("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
    observed = intercept(monkeypatch, [wire])
    turn = await ReviewAgentSession(model(), request(), SCHEMAS).invoke([HumanMessage(content="Inspect.")],
                                                                       stage="verification_validate")
    assert turn.output == {"decisions": []}
    assert turn.response.usage_metadata["output_tokens"] == 50
    assert turn.response.response_metadata["finish_reason"] == "stop"
    assert len(observed) == 1
