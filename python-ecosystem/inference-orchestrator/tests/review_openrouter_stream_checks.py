"""Offline SDK transport checks using synthetic responses, not production replays.

These checks establish parser and wire-contract behavior, not model quality.
Both HTTP client families use an in-memory transport.
"""
import json
from types import SimpleNamespace

import httpx
import httpx2
import pytest
from langchain_core.messages import HumanMessage

from llm.openai_adapters import ChatOpenRouter
from service.review.agent_calls import ReviewAgentSession


SCHEMAS = [{"name": "readReviewFile", "description": "Read a source range.", "inputSchema": {
    "type": "object", "properties": {
        "path": {"type": "string"}, "startLine": {"type": "integer"},
        "endLine": {"type": "integer"},
    }, "required": ["path"],
}}]
REQUEST = SimpleNamespace(aiProvider="openrouter", pullRequestId="offline-sdk-check")
USAGE = {"prompt_tokens": 101, "completion_tokens": 23, "total_tokens": 124,
         "prompt_tokens_details": {"cached_tokens": 80},
         "completion_tokens_details": {"reasoning_tokens": 1}}
CALLS = [
    {"id": "call-first", "type": "function", "function": {
        "name": "readReviewFile", "arguments": json.dumps({
            "path": "src/booking.py", "startLine": 20, "endLine": 40,
        }),
    }},
    {"id": "call-second", "type": "function", "function": {
        "name": "readReviewFile", "arguments": json.dumps({"path": "src/caller.py"}),
    }},
]


def completion(message, finish_reason):
    return {"id": "gen-offline-sdk", "object": "chat.completion", "created": 1,
            "model": "offline-review-model", "choices": [{
                "index": 0, "message": message, "finish_reason": finish_reason,
            }], "usage": USAGE}


def event(delta, finish_reason=None):
    return {"id": "gen-offline-sdk", "object": "chat.completion.chunk", "created": 1,
            "model": "offline-review-model", "choices": [{
                "index": 0, "delta": delta, "finish_reason": finish_reason,
            }]}


def tool_events(calls):
    yield event({"role": "assistant", "content": None, "reasoning_content": "Inspect source."})
    # Interleave argument fragments to exercise indexed SDK chunk aggregation.
    for index, call in enumerate(calls):
        yield event({"content": None, "tool_calls": [{
            "index": index, "id": call["id"], "type": "function",
            "function": {"name": call["function"]["name"],
                         "arguments": call["function"]["arguments"][:11]},
        }]})
    for index, call in reversed(list(enumerate(calls))):
        yield event({"tool_calls": [{"index": index, "function": {
            "arguments": call["function"]["arguments"][11:],
        }}]})
    yield event({}, "tool_calls")


def sse_body(events):
    usage = {"id": "gen-offline-sdk", "object": "chat.completion.chunk", "created": 1,
             "model": "offline-review-model", "choices": [], "usage": USAGE}
    return (": OPENROUTER PROCESSING\n\n" + "".join(
        "data: " + json.dumps(chunk) + "\n\n" for chunk in [*events, usage]
    ) + "data: [DONE]\n\n").encode()


class ResponseStream(httpx2.AsyncByteStream):
    def __init__(self, body):
        self.body = body

    async def __aiter__(self):
        # HTTP chunks need not align with SSE lines or JSON/tool boundaries.
        for offset in range(0, len(self.body), 37):
            yield self.body[offset:offset + 37]


@pytest.fixture(autouse=True)
def isolate_transport(monkeypatch):
    monkeypatch.setenv("REVIEW_QUALITY_CAPTURE_ENABLED", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")

    def unexpected_network(*args, **kwargs):
        raise AssertionError("Offline SDK checks must use the supplied MockTransport")

    for protocol in (httpx, httpx2):
        monkeypatch.setattr(protocol.HTTPTransport, "handle_request", unexpected_network)
        monkeypatch.setattr(protocol.AsyncHTTPTransport, "handle_async_request", unexpected_network)


async def invoke_response(*, streaming, message, finish_reason, events):
    outgoing = []

    async def transport(request):
        outgoing.append(json.loads(request.content))
        assert request.url.path == "/api/v1/chat/completions"
        if streaming:
            return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                                   stream=ResponseStream(sse_body(events)))
        return httpx2.Response(200, json=completion(message, finish_reason))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
        model = ChatOpenRouter(api_key="offline-test-key", model="offline-review-model",
                               http_async_client=client, max_retries=0,
                               streaming=streaming, stream_usage=True, temperature=0,
                               extra_body={"provider": {"order": ["offline-provider"],
                                                        "allow_fallbacks": False}})
        turn = await ReviewAgentSession(model, REQUEST, SCHEMAS).invoke(
            [HumanMessage(content="Resolve the supplied review question.")], stage="verification")
    assert len(outgoing) == 1  # A parse outcome must not replay the model request.
    wire = outgoing[0]
    assert wire["stream"] is streaming
    assert wire["tools"][0]["function"]["name"] == "readReviewFile"
    assert wire["reasoning"] == {"effort": "medium"}
    assert wire["provider"] == {"order": ["offline-provider"], "allow_fallbacks": False}
    assert "response_format" not in wire
    assert turn.response.response_metadata["finish_reason"] == finish_reason
    assert turn.response.usage_metadata["input_tokens"] == 101
    assert turn.response.usage_metadata["output_tokens"] == 23
    assert turn.response.usage_metadata["input_token_details"]["cache_read"] == 80
    return turn


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True], ids=["json", "sse"])
async def test_native_tool_only_response_keeps_calls_with_null_content(streaming):
    turn = await invoke_response(streaming=streaming, finish_reason="tool_calls",
        message={"role": "assistant", "content": None, "tool_calls": CALLS},
        events=tool_events(CALLS))
    assert turn.response.content == ""
    assert turn.output is None and turn.output_error is None
    assert not turn.response.invalid_tool_calls
    assert not turn.diagnostics
    assert turn.tool_calls == [{
        "id": call["id"], "name": call["function"]["name"],
        "arguments": json.loads(call["function"]["arguments"]),
    } for call in CALLS]
    assert [call["id"] for call in turn.response.tool_calls] == ["call-first", "call-second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True], ids=["json", "sse"])
async def test_prose_final_is_preserved_as_recoverable_output_error(streaming):
    prose = "The caller uses a numeric booking identifier; the concern is disproved."
    turn = await invoke_response(streaming=streaming, finish_reason="stop",
        message={"role": "assistant", "content": prose},
        events=[event({"role": "assistant", "content": prose[:29]}),
                event({"content": prose[29:]}), event({}, "stop")])
    assert turn.response.content == prose
    assert not turn.tool_calls and not turn.response.invalid_tool_calls
    assert turn.output is None
    assert turn.output_error  # The controller can request the missing structured result.


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True], ids=["json", "sse"])
async def test_malformed_native_arguments_remain_a_matching_error_receipt(streaming):
    calls = [{"id": "call-invalid", "type": "function", "function": {
        "name": "readReviewFile", "arguments": "this is not a JSON object",
    }}]
    turn = await invoke_response(streaming=streaming, finish_reason="tool_calls",
        message={"role": "assistant", "content": None, "tool_calls": calls},
        events=tool_events(calls))
    assert turn.output is None and turn.output_error is None
    assert turn.response.invalid_tool_calls[0]["id"] == "call-invalid"
    assert turn.tool_calls[0]["id"] == "call-invalid"
    assert turn.tool_calls[0]["arguments"] == {}
    assert turn.tool_calls[0]["error"]
    assert turn.diagnostics


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True], ids=["json", "sse"])
async def test_verifier_serializes_scoped_tools_and_decodes_actual_sdk_assessment(streaming):
    from service.review.review_step import STEP_TOOL
    from service.review.verifier import ReviewVerifier

    part = SimpleNamespace(id="part", path="service.py", side="proposed", anchors={2: "return value / 0"},
                           diff="@@ -2 +2 @@\n-return value\n+return value / 0\n")
    candidate = {"partId": "part", "file": "service.py", "line": 2,
                 "title": "Zero divisor", "reason": "The changed expression always divides by zero"}
    step_calls = [{"id": "structured-outcome", "type": "function", "function": {
        "name": STEP_TOOL, "arguments": json.dumps({"assessments": [{
            "workId": "work-1", "verdict": "confirmed", "reason": "The changed return divides by zero",
            "evidenceIds": ["diff:part"],
        }], "findings": []})}}]
    outgoing = []

    async def transport(request):
        outgoing.append(json.loads(request.content))
        if streaming:
            return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                                   stream=ResponseStream(sse_body(tool_events(step_calls))))
        return httpx2.Response(200, json=completion({"role": "assistant", "content": None, "tool_calls": step_calls}, "tool_calls"))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
        model = ChatOpenRouter(api_key="offline-test-key", model="offline-review-model", http_async_client=client,
                               max_retries=0, streaming=streaming, stream_usage=True, temperature=0)
        result = await ReviewVerifier(None).verify(llm=model, request=REQUEST, findings=[candidate],
                                                   summaries=[], parts=[part], binding={})
    assert result.issues == [{**candidate, "reason": "The changed return divides by zero"}]
    assert not result.diagnostics
    assert len(outgoing) == 1
    wire = outgoing[0]
    assert wire["tool_choice"] == {"type": "function", "function": {"name": STEP_TOOL}}
    schemas = {tool["function"]["name"]: tool["function"]["parameters"] for tool in wire["tools"]}
    assert set(schemas) == {STEP_TOOL}
    assert schemas[STEP_TOOL]["properties"]["assessments"]["minItems"] == 1
    assert "evidenceRequests" not in schemas[STEP_TOOL]["properties"]
    assert not {"max_tokens", "max_completion_tokens"}.intersection(wire)
    assert [message["role"] for message in wire["messages"]] == ["system", "user"]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True], ids=["json", "sse"])
async def test_actual_sdk_alternates_assessment_and_scoped_evidence_without_restarting_work(streaming, tmp_path, monkeypatch):
    """Replay the read-only starvation shape through the real provider adapter."""
    from llm.request_capture import attach_http_capture, review_capture
    from service.review.review_step import STEP_TOOL
    from service.review.verifier import ReviewVerifier

    captures = tmp_path / "captures"
    monkeypatch.setenv("REVIEW_QUALITY_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("REVIEW_QUALITY_CAPTURE_OUTPUT_DIR", str(captures))
    monkeypatch.delenv("REVIEW_QUALITY_CAPTURE_PROJECT_IDS", raising=False)
    target, overlay = tmp_path / "target", tmp_path / "overlay"
    target.mkdir()
    (overlay / "files").mkdir(parents=True)
    (overlay / "manifest.json").write_text(json.dumps({"changedFiles": ["service.py"], "deletedFiles": []}))
    caller = "def caller(value):\n    return service(value)  # no validation before the changed return\n"
    (target / "caller.py").write_text(caller)
    part = SimpleNamespace(id="part", path="service.py", side="proposed", anchors={2: "return value / 0"},
                           diff="@@ -2 +2 @@\n-return value\n+return value / 0\n")
    candidate = {"partId": "part", "file": "service.py", "line": 2,
                 "title": "Zero divisor", "reason": "The changed expression divides by zero"}
    missing = "Whether the caller excludes inputs reaching the changed return"
    replies = [
        (STEP_TOOL, {"assessments": [{"workId": "work-1", "verdict": "needs_evidence",
            "reason": missing, "evidenceIds": []}]}),
        ("readReviewFile", {"path": "caller.py", "workIds": ["work-1"], "missingFact": missing}),
        (STEP_TOOL, {"assessments": [{"workId": "work-1", "verdict": "confirmed",
            "reason": "The actual caller reaches the changed zero divisor without validation.",
            "evidenceIds": ["diff:part", "read-1"]}]}),
    ]
    outgoing = []

    async def transport(request):
        wire = json.loads(request.content)
        outgoing.append(wire)
        index = len(outgoing) - 1
        assert index < len(replies), "A completed source check must not open another retrieval round"
        name, arguments = replies[index]
        functions = {tool["function"]["name"] for tool in wire["tools"]}
        packet = json.loads(wire["messages"][-1]["content"])
        assert packet["reviewWork"]["phase"] == ("evidence" if index == 1 else "assessment")
        if index == 1:
            assert "readReviewFile" in functions
            assert wire["tool_choice"] == "required"
            assert packet["reviewWork"]["missingFacts"] == [{"workId": "work-1", "missingFact": missing}]
        else:
            assert functions == {STEP_TOOL}
            assert wire["tool_choice"] == {"type": "function", "function": {"name": STEP_TOOL}}
        if index == 2:
            observed = next(item for item in packet["evidence"] if item["id"] == "read-1")
            assert observed["result"]["content"] == caller
        tool_calls = [{"id": f"step-{index}", "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments)}}]
        if streaming:
            return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                stream=ResponseStream(sse_body(tool_events(tool_calls))))
        return httpx2.Response(200, json=completion({"role": "assistant", "content": None,
            "tool_calls": tool_calls}, "tool_calls"))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
        model = ChatOpenRouter(api_key="offline-test-key", model="offline-review-model", http_async_client=client,
            max_retries=0, streaming=streaming, stream_usage=True, temperature=0)
        attach_http_capture(client)
        with review_capture(REQUEST):
            result = await ReviewVerifier(None).verify(llm=model, request=REQUEST, findings=[candidate],
                summaries=[], parts=[part], binding={"target_repo_path": str(target), "review_overlay_path": str(overlay)})
    manifests = [json.loads(path.read_text()) for path in captures.rglob("*.complete.json")]
    assert sorted(item["turn"] for item in manifests) == [1, 2, 3]
    assert {item["stage"] for item in manifests} == {"verification_validate"}
    assert len({item["run_id"] for item in manifests}) == 1
    assert len(outgoing) == 3
    assert not result.diagnostics
    assert result.issues == [{**candidate, "reason": replies[2][1]["assessments"][0]["reason"]}]
    assert all(not {"max_tokens", "max_completion_tokens"}.intersection(wire) for wire in outgoing)
