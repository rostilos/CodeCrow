import asyncio
import json
from pathlib import Path

import pytest

from tools.codex_benchmark.bridge import Conversation, SubscriptionBridge, create_app, dynamic_tools, prompt_parts
from tools.codex_benchmark.inference_entrypoint import install_subscription_guard
from tools.codex_benchmark.rpc import AppServer, MODEL


class FakeRPC:
    def __init__(self):
        self.account = {"type": "chatgpt", "planType": "pro"}
        self.model = {"defaultReasoningEffort": "medium"}
        self.events = {}
        self.usage = {}
        self.sent = []
        self.created = []
        self.turns = []
        self.cancelled = []
        self.requests = []

    async def start_thread(self, instructions, tools, effort):
        thread = f"thread-{len(self.created)}"
        self.created.append((thread, instructions, tools, effort))
        self.events[thread] = asyncio.Queue()
        return thread

    async def request(self, method, params, **kwargs):
        self.requests.append((method, params))
        if method == "turn/start":
            self.turns.append(params)
            return {"turn": {"id": "turn-1"}}
        return {}

    async def send(self, message):
        self.sent.append(message)

    async def interrupt(self, thread, turn):
        self.cancelled.append((thread, turn))


async def tool_response(bridge, rpc, owner, call_id):
    task = asyncio.create_task(bridge.complete({
        "model": MODEL, "messages": MESSAGES, "tools": TOOLS, "codecrow_owner": owner,
    }))
    await wait_for(lambda: any(session.owner == owner and session.turn for session in bridge.sessions.values()))
    thread = next(session.thread for session in bridge.sessions.values() if session.owner == owner)
    await rpc.events[thread].put({"id": call_id, "method": "item/tool/call", "params": {
        "threadId": thread, "turnId": "turn-1", "callId": call_id,
        "tool": "read_code", "arguments": {"path": "source"},
    }})
    return thread, await task


TOOLS = [{"type": "function", "function": {"name": "read_code", "description": "Read bound source", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]
MESSAGES = [{"role": "system", "content": "Review this change."}, {"role": "user", "content": "Check foo.py"}]


async def wait_for(test):
    async with asyncio.timeout(2):
        while not test():
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_native_call_continuation_preserves_session_and_exact_tool_output(tmp_path):
    rpc = FakeRPC()
    bridge = SubscriptionBridge(rpc, tmp_path)
    first = asyncio.create_task(bridge.complete({"model": MODEL, "messages": MESSAGES, "tools": TOOLS}))
    await wait_for(lambda: bool(rpc.created))
    thread = rpc.created[0][0]
    await rpc.events[thread].put({"id": 41, "method": "item/tool/call", "params": {"threadId": thread, "turnId": "turn-1", "callId": "native-call", "tool": "read_code", "arguments": {"path": "foo.py"}}})
    response = await first
    assistant = response["choices"][0]["message"]
    assert response["choices"][0]["finish_reason"] == "tool_calls"
    assert assistant["tool_calls"][0]["id"] == "native-call"
    assert not rpc.sent
    source = 'def f():\n    return "full source"\n'
    second = asyncio.create_task(bridge.complete({"model": MODEL, "tools": TOOLS, "messages": MESSAGES + [assistant, {"role": "tool", "tool_call_id": "native-call", "content": source}]}))
    await wait_for(lambda: bool(rpc.sent))
    assert rpc.sent == [{"id": 41, "result": {"success": True, "contentItems": [{"type": "inputText", "text": source}]}}]
    await rpc.events[thread].put({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": '{"issues":[]}'}}})
    await rpc.events[thread].put({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
    assert (await second)["choices"][0]["message"]["content"] == '{"issues":[]}'
    assert len(rpc.created) == 1
    assert not bridge.sessions and not bridge.by_call


@pytest.mark.asyncio
async def test_forced_ordinary_tool_keeps_inventory_session_and_model_arguments(tmp_path):
    rpc = FakeRPC()
    bridge = SubscriptionBridge(rpc, tmp_path)
    tools = TOOLS + [{"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}}]
    choice = {"type": "function", "function": {"name": "read_code"}}
    first = asyncio.create_task(bridge.complete({"model": MODEL, "messages": MESSAGES, "tools": tools, "tool_choice": choice}))
    await wait_for(lambda: bool(rpc.created))
    thread = rpc.created[0][0]
    assert [tool["name"] for tool in rpc.created[0][2]] == ["read_code", "search"]
    await rpc.events[thread].put({"id": 1, "method": "item/tool/call", "params": {
        "threadId": thread, "callId": "first", "tool": "read_code", "arguments": {"path": "model-selected.py"}}})
    first_response = await first
    assert bridge.by_call["first"].thread == thread and not rpc.cancelled
    assistant = first_response["choices"][0]["message"]
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"path": "model-selected.py"}
    following = asyncio.create_task(bridge.complete({"model": MODEL, "tools": tools, "messages": MESSAGES + [assistant,
        {"role": "tool", "tool_call_id": "first", "content": "exact source"}]}))
    await wait_for(lambda: bool(rpc.sent))
    await rpc.events[thread].put({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "done"}}})
    await rpc.events[thread].put({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
    await following
    assert len(rpc.created) == 1
    records = [json.loads(line) for line in (tmp_path / "subscription-ledger.jsonl").read_text().splitlines()]
    request = next(record for record in records if record["event"] == "request")
    assert request["tool_choice"] == choice and request["messages_bytes"] > 0 and request["tools_bytes"] > 0
    assert not any(record["event"] == "conversation_reconstructed" for record in records)


@pytest.mark.asyncio
async def test_declared_output_schema_call_releases_without_tool_result(tmp_path):
    rpc = FakeRPC()
    bridge = SubscriptionBridge(rpc, tmp_path)
    task = asyncio.create_task(bridge.complete({"model": MODEL, "messages": MESSAGES, "tools": TOOLS,
        "codecrow_terminal_tools": ["read_code"]}))
    await wait_for(lambda: bool(rpc.created))
    thread = rpc.created[0][0]
    await rpc.events[thread].put({"id": 1, "method": "item/tool/call", "params": {
        "threadId": thread, "callId": "final", "tool": "read_code", "arguments": {"path": "answer"}}})
    assert (await task)["choices"][0]["finish_reason"] == "tool_calls"
    assert not bridge.sessions and not bridge.by_call and rpc.cancelled


@pytest.mark.asyncio
async def test_late_usage_is_not_lost_or_counted_twice_across_deliveries(tmp_path):
    rpc = FakeRPC()
    bridge = SubscriptionBridge(rpc, tmp_path)
    history = list(MESSAGES)
    responses = []
    for index in range(3):
        if index == 1:
            # Arrives after the first HTTP response but before its continuation.
            rpc.usage[thread] = {"total": {"inputTokens": 100, "outputTokens": 10, "totalTokens": 110}, "last": {"inputTokens": 100}}
            rpc.usage_callback(thread, "turn-1", rpc.usage[thread])
        task = asyncio.create_task(bridge.complete({"model": MODEL, "messages": history, "tools": TOOLS}))
        await wait_for(lambda: bool(rpc.created))
        thread = rpc.created[0][0]
        if index:
            await wait_for(lambda: len(rpc.sent) == index)
        if index == 2:
            rpc.usage[thread] = {"total": {"inputTokens": 140, "outputTokens": 15, "totalTokens": 155}}
            rpc.usage_callback(thread, "turn-1", rpc.usage[thread])
            await rpc.events[thread].put({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "done"}}})
            await rpc.events[thread].put({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
        else:
            await rpc.events[thread].put({"id": index, "method": "item/tool/call", "params": {
                "threadId": thread, "callId": f"call-{index}", "tool": "read_code", "arguments": {"path": "source"}}})
        response = await task
        responses.append(response)
        history += [response["choices"][0]["message"], {"role": "tool", "tool_call_id": f"call-{index}", "content": "source"}]
    assert responses[0]["usage"] is None
    assert responses[1]["usage"] == {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
    assert responses[2]["usage"] == {"prompt_tokens": 40, "completion_tokens": 5, "total_tokens": 45}
    records = [json.loads(line) for line in (tmp_path / "subscription-ledger.jsonl").read_text().splitlines()]
    terminal = next(record for record in records if record["event"] == "conversation_terminal")
    assert terminal["native_total_usage"]["totalTokens"] == 155
    assert len([record for record in records if record["event"] == "native_usage_updated"]) == 2


@pytest.mark.asyncio
async def test_rpc_records_late_usage_even_after_conversation_released(tmp_path):
    from types import SimpleNamespace
    rpc = AppServer(tmp_path)
    bridge = SubscriptionBridge(rpc, tmp_path)
    reader = asyncio.StreamReader()
    native = {"total": {"inputTokens": 9, "outputTokens": 2, "totalTokens": 11}, "last": {"inputTokens": 9}}
    reader.feed_data((json.dumps({"method": "thread/tokenUsage/updated", "params": {
        "threadId": "released-thread", "turnId": "turn-1", "tokenUsage": native}}) + "\n").encode())
    reader.feed_eof()
    rpc.process = SimpleNamespace(stdout=reader)
    await rpc._read()
    event = json.loads((tmp_path / "subscription-ledger.jsonl").read_text().splitlines()[0])
    assert event["thread"] == "released-thread" and event["conversation_active"] is False
    assert event["native_total_usage"] == native["total"] and event["native_last_usage"] == native["last"]


def test_benchmark_guard_marks_only_explicit_structured_function_bindings():
    class Model:
        def bind_tools(self, tools, **kwargs):
            return kwargs
    class Factory:
        @staticmethod
        def create_llm(ai_provider, ai_model, ai_api_key, ai_base_url=None):
            return Model()
    install_subscription_guard(Factory, "http://bridge/v1", "token", MODEL)
    model = Factory.create_llm("openai_compatible", MODEL, "token", "http://bridge/v1")
    assert "extra_body" not in model.bind_tools(TOOLS, tool_choice="read_code")
    options = model.bind_tools(TOOLS, tool_choice="read_code", ls_structured_output_format={"schema": {}})
    assert options["extra_body"]["codecrow_terminal_tools"] == ["read_code"]


def test_missing_and_unchanged_usage_remain_unknown(tmp_path):
    rpc = FakeRPC()
    bridge = SubscriptionBridge(rpc, tmp_path)
    session = Conversation("thread", [], [])
    rpc.usage["thread"] = {"total": {"inputTokens": 10}}
    assert bridge.response_usage(session) is None and session.reported_usage == {}
    rpc.usage["thread"] = {"total": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12, "cachedInputTokens": 3}}
    assert bridge.response_usage(session)["prompt_tokens_details"]["cached_tokens"] == 3
    assert bridge.response_usage(session) is None


def test_structured_binding_preserves_copied_model_parameters():
    from langchain_openai import ChatOpenAI
    class Factory:
        @staticmethod
        def create_llm(ai_provider, ai_model, ai_api_key, ai_base_url=None):
            return ChatOpenAI(model=ai_model, api_key=ai_api_key, base_url=ai_base_url)
    install_subscription_guard(Factory, "http://bridge/v1", "local-token", MODEL)
    original = Factory.create_llm("openai_compatible", MODEL, "local-token", "http://bridge/v1")
    copied = original.model_copy(update={"max_tokens": 321})
    bound = copied.bind_tools(TOOLS, tool_choice="read_code", ls_structured_output_format={"schema": {}})
    assert bound.bound.max_tokens == 321
    assert bound.kwargs["extra_body"]["codecrow_terminal_tools"] == ["read_code"]


@pytest.mark.asyncio
async def test_delayed_native_calls_are_delivery_batches_and_can_finalize_without_tools(tmp_path):
    """One native turn can consume four caller response slots without final JSON."""
    rpc = FakeRPC()
    bridge = SubscriptionBridge(rpc, tmp_path)
    history = list(MESSAGES)
    for index in range(4):
        task = asyncio.create_task(bridge.complete({"model": MODEL, "messages": history, "tools": TOOLS}))
        await wait_for(lambda: bool(rpc.created))
        thread = rpc.created[0][0]
        # Simulate native parallel calls arriving after the 30ms collection window.
        await asyncio.sleep(0.04)
        await rpc.events[thread].put({"id": 100 + index, "method": "item/tool/call", "params": {
            "threadId": thread, "turnId": "turn-1", "callId": f"delayed-{index}",
            "tool": "read_code", "arguments": {"path": f"file-{index}.py"},
        }})
        response = await task
        assert response["choices"][0]["finish_reason"] == "tool_calls"
        history += [response["choices"][0]["message"], {
            "role": "tool", "tool_call_id": f"delayed-{index}", "content": f"observed source {index}",
        }]
    assert len(rpc.created) == 1
    assert bridge.sessions[thread].tool_delivery_batches == 4
    # A reserved tools-disabled finalization reconstructs all received evidence.
    history.append({"role": "user", "content": "Return your final assessment from the collected evidence now."})
    final = asyncio.create_task(bridge.complete({"model": MODEL, "messages": history, "tools": TOOLS, "tool_choice": "none"}))
    await wait_for(lambda: len(rpc.created) == 2)
    final_thread = rpc.created[-1][0]
    assert thread not in bridge.sessions
    assert not bridge.by_call
    assert (thread, "turn-1") in rpc.cancelled
    assert "observed source 3" in rpc.turns[-1]["input"][0]["text"]
    assert "Return your final assessment" in rpc.turns[-1]["input"][0]["text"]
    await rpc.events[final_thread].put({"method": "item/completed", "params": {"item": {
        "type": "agentMessage", "text": '{"assessments":[]}',
    }}})
    await rpc.events[final_thread].put({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
    assert (await final)["choices"][0]["finish_reason"] == "stop"
    assert rpc.created[-1][2] == []
    assert not bridge.sessions
    records = [json.loads(line) for line in (tmp_path / "subscription-ledger.jsonl").read_text().splitlines()]
    calls = [r for r in records if r["event"] == "native_tool_call"]
    assert [r["call_id"] for r in calls] == [f"delayed-{i}" for i in range(4)]
    assert [r["delivery_batch"] for r in calls] == [1, 2, 3, 4]
    reconstruction = next(r for r in records if r["event"] == "conversation_reconstructed")
    assert reconstruction["pending_tool_call_ids"] == ["delayed-3"]
    assert reconstruction["tool_result_ids_in_prompt"] == ["delayed-3"]
    assert reconstruction["finalization"] is True
    assert "tool_inventory_changed" in reconstruction["reasons"]
    assert "appended_instruction_or_missing_tool_results" in reconstruction["reasons"]
    assert {r["effective_reasoning_effort"] for r in records if r["event"] == "request"} == {"medium"}


def test_optional_bridge_ledger_failure_does_not_fail_inference(tmp_path, monkeypatch, caplog):
    bridge = SubscriptionBridge(FakeRPC(), tmp_path)
    def unavailable(*args, **kwargs):
        raise OSError("disk unavailable")
    monkeypatch.setattr("tools.codex_benchmark.bridge.os.open", unavailable)
    bridge.record({"event": "request", "id": "test"})
    assert "Optional subscription ledger unavailable" in caplog.text


@pytest.mark.asyncio
async def test_model_and_provider_routes_rejected_before_start(tmp_path):
    rpc = FakeRPC()
    bridge = SubscriptionBridge(rpc, tmp_path)
    for override in [{"model": "deepseek/foo"}, {"provider": {"order": ["wafer"]}}, {"base_url": "https://openrouter.ai/api/v1"}]:
        with pytest.raises(ValueError):
            await bridge.complete({"model": MODEL, "messages": MESSAGES, **override})
    assert rpc.created == []


@pytest.mark.asyncio
async def test_cancellation_interrupts_owned_native_turn(tmp_path):
    rpc = FakeRPC()
    bridge = SubscriptionBridge(rpc, tmp_path)
    task = asyncio.create_task(bridge.complete({"model": MODEL, "messages": MESSAGES}))
    await wait_for(lambda: bool(bridge.sessions))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rpc.cancelled == [("thread-0", "turn-1")]
    assert not bridge.sessions


@pytest.mark.asyncio
async def test_owner_timeout_after_successful_tool_response_stops_native_work(tmp_path):
    rpc = FakeRPC()
    bridge = SubscriptionBridge(rpc, tmp_path)
    response_delivered = asyncio.Event()
    native_retry_boundary = asyncio.Event()
    extra_inferences = []

    async def specialist():
        try:
            thread, response = await tool_response(bridge, rpc, "specialist", "pending-source")
            assert response["choices"][0]["finish_reason"] == "tool_calls"
            response_delivered.set()
            await asyncio.Event().wait()  # Host source acquisition outlives its budget.
        finally:
            await bridge.release_owner("specialist")

    async def native_retry():
        await native_retry_boundary.wait()
        if not rpc.cancelled:
            extra_inferences.append("unconsumed retry")

    retry = asyncio.create_task(native_retry())
    task = asyncio.create_task(specialist())
    await response_delivered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    native_retry_boundary.set()
    await retry
    assert not extra_inferences and rpc.cancelled == [("thread-0", "turn-1")]
    assert not bridge.sessions and not bridge.by_call
    assert len(rpc.turns) == 1
    await bridge.release_owner("specialist")
    assert len(rpc.cancelled) == 1


@pytest.mark.asyncio
async def test_release_during_thread_creation_disposes_late_thread_without_turn(tmp_path):
    rpc = FakeRPC()
    creation_started, finish_creation = asyncio.Event(), asyncio.Event()
    start_thread = rpc.start_thread

    async def delayed_creation(*args):
        creation_started.set()
        await finish_creation.wait()
        return await start_thread(*args)

    rpc.start_thread = delayed_creation
    bridge = SubscriptionBridge(rpc, tmp_path)
    request = asyncio.create_task(bridge.complete({"model": MODEL, "messages": MESSAGES,
                                                  "codecrow_owner": "ending"}))
    await creation_started.wait()
    release = asyncio.create_task(bridge.release_owner("ending"))
    await wait_for(lambda: bridge.owners["ending"].closed)
    finish_creation.set()
    await release
    with pytest.raises(asyncio.CancelledError):
        await request
    assert rpc.created and not rpc.turns
    assert not bridge.sessions and not bridge.by_call and not bridge.owners["ending"].creations
    assert ("thread/unsubscribe", {"threadId": "thread-0"}) in rpc.requests
    with pytest.raises(ValueError, match="already ended"):
        await bridge.complete({"model": MODEL, "messages": MESSAGES, "codecrow_owner": "ending"})
    assert len(rpc.created) == 1


@pytest.mark.asyncio
async def test_owner_cleanup_leaves_concurrent_tool_continuation_intact(tmp_path):
    rpc = FakeRPC()
    bridge = SubscriptionBridge(rpc, tmp_path)
    first_thread, _ = await tool_response(bridge, rpc, "ending", "first")
    second_thread, response = await tool_response(bridge, rpc, "continuing", "second")
    await bridge.release_owner("ending")
    history = MESSAGES + [response["choices"][0]["message"],
                          {"role": "tool", "tool_call_id": "second", "content": "exact source"}]
    with pytest.raises(ValueError, match="another execution owner"):
        await bridge.complete({"model": MODEL, "messages": history, "tools": TOOLS, "codecrow_owner": "unrelated"})
    continuing = asyncio.create_task(bridge.complete({
        "model": MODEL, "messages": history, "tools": TOOLS, "codecrow_owner": "continuing"}))
    await wait_for(lambda: bool(rpc.sent))
    await rpc.events[second_thread].put({"method": "item/completed", "params": {
        "item": {"type": "agentMessage", "text": "review complete"}}})
    await rpc.events[second_thread].put({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
    assert (await continuing)["choices"][0]["message"]["content"] == "review complete"
    await bridge.release_owner("continuing")
    assert rpc.cancelled == [(first_thread, "turn-1"), (second_thread, "turn-1")]
    assert len(rpc.created) == 2 and not bridge.by_call and not bridge.sessions


@pytest.mark.asyncio
async def test_release_http_disconnect_does_not_cancel_native_cleanup(tmp_path):
    import httpx
    rpc = FakeRPC()
    bridge = SubscriptionBridge(rpc, tmp_path)
    await tool_response(bridge, rpc, "ending", "source")
    interrupt_started, finish_interrupt = asyncio.Event(), asyncio.Event()
    interrupt = rpc.interrupt

    async def delayed_interrupt(*args):
        interrupt_started.set()
        await finish_interrupt.wait()
        await interrupt(*args)

    rpc.interrupt = delayed_interrupt
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(bridge, "token")), base_url="http://local") as client:
        release = asyncio.create_task(client.post("/v1/conversations/release", json={"owner": "ending"},
                                                 headers={"Authorization": "Bearer token"}))
        await interrupt_started.wait()
        release.cancel()
        with pytest.raises(asyncio.CancelledError):
            await release
        assert not bridge.owners["ending"].release_task.done()
        finish_interrupt.set()
        await bridge.owners["ending"].release_task
    assert rpc.cancelled == [("thread-0", "turn-1")]
    assert not bridge.sessions and not bridge.by_call


@pytest.mark.asyncio
async def test_native_cleanup_failure_keeps_completed_result(tmp_path, caplog):
    rpc = FakeRPC()

    async def failing_interrupt(*args):
        raise RuntimeError("transport unavailable")

    rpc.interrupt = failing_interrupt
    bridge = SubscriptionBridge(rpc, tmp_path)
    task = asyncio.create_task(bridge.complete({"model": MODEL, "messages": MESSAGES, "codecrow_owner": "owner"}))
    await wait_for(lambda: bool(bridge.sessions))
    thread = next(iter(bridge.sessions))
    await rpc.events[thread].put({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "useful review"}}})
    await rpc.events[thread].put({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
    assert (await task)["choices"][0]["message"]["content"] == "useful review"
    await bridge.release_owner("owner")
    assert "Native interrupt unavailable" in caplog.text
    records = [json.loads(line) for line in (tmp_path / "subscription-ledger.jsonl").read_text().splitlines()]
    assert next(r for r in records if r['event'] == 'conversation_terminal')['cleanup_errors'] == ['interrupt']


@pytest.mark.asyncio
async def test_subscription_scope_survives_model_copy_and_binding(monkeypatch):
    from langchain_openai import ChatOpenAI
    from tools.codex_benchmark import inference_entrypoint as entrypoint
    released = []

    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            from types import SimpleNamespace
            released.append(kwargs['json']['owner'])
            return SimpleNamespace(raise_for_status=lambda: None, status_code=200)

    class Factory:
        @staticmethod
        def create_llm(ai_provider, ai_model, ai_api_key, ai_base_url=None):
            return ChatOpenAI(model=ai_model, api_key=ai_api_key, base_url=ai_base_url)

    monkeypatch.setattr(entrypoint.httpx, 'AsyncClient', Client)
    install_subscription_guard(Factory, "http://bridge/v1", "local-token", MODEL)
    original = Factory.create_llm("openai_compatible", MODEL, "local-token", "http://bridge/v1")
    copied = original.model_copy(update={"max_tokens": 321})
    bound = copied.bind_tools(TOOLS)
    async with bound.provider_execution_scope():
        payload = bound.bound._get_request_payload(MESSAGES, **bound.kwargs)
        owner = payload['extra_body']['codecrow_owner']
        assert original._get_request_payload(MESSAGES)['extra_body']['codecrow_owner'] == owner
        assert bound.bound.max_tokens == 321
    assert released == [owner]
    assert 'codecrow_owner' not in original._get_request_payload(MESSAGES).get('extra_body', {})


@pytest.mark.asyncio
async def test_generator_close_from_other_context_still_releases_owner(monkeypatch):
    from tools.codex_benchmark import inference_entrypoint as entrypoint
    from types import SimpleNamespace
    released = []

    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            released.append(kwargs['json']['owner'])
            return SimpleNamespace(raise_for_status=lambda: None, status_code=200)

    monkeypatch.setattr(entrypoint.httpx, 'AsyncClient', Client)
    async def stream():
        async with entrypoint.subscription_execution_scope('http://bridge/v1', 'token'):
            yield entrypoint._OWNER.get()[1]

    iterator = stream()
    owner = await asyncio.create_task(anext(iterator))
    await iterator.aclose()
    assert released == [owner] and entrypoint._OWNER.get() is None


@pytest.mark.asyncio
async def test_default_capacity_admits_64_calls_and_releases_waiting_work(tmp_path):
    rpc = FakeRPC()
    bridge = SubscriptionBridge(rpc, tmp_path)
    tasks = [asyncio.create_task(bridge.complete({"model": MODEL, "messages": MESSAGES})) for _ in range(65)]
    try:
        await wait_for(lambda: len(rpc.created) == 64)
        assert not tasks[-1].done()
        first = rpc.created[0][0]
        await rpc.events[first].put({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "done"}}})
        await rpc.events[first].put({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
        await tasks[0]
        await wait_for(lambda: len(rpc.created) == 65)
        health = next(route.endpoint for route in create_app(bridge, "test-token").routes if route.path == "/health")
        assert (await health())["capacity"] == 64
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert not bridge.sessions


def test_prompt_and_function_schema_are_not_truncated():
    code = "x\n" * 50000
    instructions, prompt = prompt_parts([MESSAGES[0], {"role": "user", "content": code}])
    assert instructions == "Review this change."
    assert prompt == code
    tool = dynamic_tools({"tools": TOOLS})[0]
    assert tool["inputSchema"] == TOOLS[0]["function"]["parameters"]


def test_benchmark_entrypoint_blocks_every_other_factory_route():
    calls = []
    class Factory:
        @staticmethod
        def create_llm(ai_provider, ai_model, ai_api_key, ai_base_url=None, temperature=None):
            calls.append((ai_provider, ai_model, ai_api_key, ai_base_url, temperature))
            return "original model"
    url = "http://bridge:18771/v1"
    install_subscription_guard(Factory, url, "local-only-token")
    assert Factory.create_llm("openai_compatible", MODEL, "local-only-token", url, 0.6) == "original model"
    assert len(calls) == 1 and calls[0][-1] == 0.6
    for provider, model, key, endpoint in [("openrouter", MODEL, "secret", url), ("openai", MODEL, "secret", None), ("openai_compatible", "other", "local-only-token", url), ("openai_compatible", MODEL, "local-only-token", "https://openrouter.ai/api/v1")]:
        with pytest.raises(ValueError):
            Factory.create_llm(provider, model, key, endpoint)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_startup_authentication_failure_closes_owned_app_server(tmp_path):
    class FailingRPC(FakeRPC):
        closed = False
        async def start(self):
            raise RuntimeError("ChatGPT subscription login required")
        async def close(self):
            self.closed = True
    rpc = FailingRPC()
    app = create_app(SubscriptionBridge(rpc, tmp_path), "local-token")
    with pytest.raises(RuntimeError, match="subscription"):
        async with app.router.lifespan_context(app):
            pytest.fail("Startup should fail before serving model requests")
    assert rpc.closed


def test_benchmark_factory_uses_chat_facade_despite_gpt6_sdk_default():
    class Model:
        use_responses_api = True
    class Factory:
        @staticmethod
        def create_llm(ai_provider, ai_model, ai_api_key, ai_base_url=None):
            return Model()
    install_subscription_guard(Factory, "http://bridge/v1", "local-token")
    model = Factory.create_llm("openai_compatible", MODEL, "local-token", "http://bridge/v1")
    assert model.use_responses_api is False


@pytest.mark.parametrize("selected", ["gpt-6-sol", "gpt-5.6-luna"])
def test_selected_model_blocks_luna6_and_other_provider_routes(selected):
    class Factory:
        @staticmethod
        def create_llm(ai_provider, ai_model, ai_api_key, ai_base_url=None):
            return ai_model
    install_subscription_guard(Factory, "http://bridge/v1", "local-token", selected)
    assert Factory.create_llm("openai_compatible", selected, "local-token", "http://bridge/v1") == selected
    with pytest.raises(ValueError):
        Factory.create_llm("openai_compatible", "gpt-6-luna", "local-token", "http://bridge/v1")
    with pytest.raises(ValueError):
        Factory.create_llm("openrouter", selected, "local-token", "http://bridge/v1")


@pytest.mark.asyncio
async def test_failed_native_disposal_is_pending_and_existing_reaper_retries(tmp_path, monkeypatch):
    import httpx
    from tools.codex_benchmark import bridge as bridge_module
    rpc = AppServer(tmp_path)
    bridge = SubscriptionBridge(rpc, tmp_path)
    calls = []
    recovered = False

    async def request(method, params, **kwargs):
        calls.append(method)
        if not recovered:
            raise RuntimeError("temporary native transport failure")
        return {}

    rpc.request = request
    session = Conversation("thread", [], [], owner="owner", turn="turn", touched=0)
    session.pending["call"] = {"id": 7}
    bridge.sessions[session.thread] = session
    bridge.by_call["call"] = session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(bridge, "token")), base_url="http://local") as client:
        response = await client.post("/v1/conversations/release", json={"owner": "owner"},
                                     headers={"Authorization": "Bearer token"})
        assert response.status_code == 202 and response.json()["status"] == "cleanup_pending"
        assert calls == ["turn/interrupt", "thread/unsubscribe"]
        assert bridge.sessions["thread"] is session and bridge.by_call["call"] is session
        assert session.release_task is None and bridge.owners["owner"].closed
        records = [json.loads(line) for line in (tmp_path / "subscription-ledger.jsonl").read_text().splitlines()]
        assert not any(r["event"] in {"owner_closed", "conversation_terminal"} for r in records)
        assert next(r for r in records if r["event"] == "conversation_cleanup_pending")["cleanup_errors"] == ["interrupt", "unsubscribe"]
        recovered = True
        first_tick, next_tick = True, asyncio.Event()

        async def reaper_tick(seconds):
            nonlocal first_tick
            if first_tick:
                first_tick = False
                return
            next_tick.set()
            await asyncio.Event().wait()

        # Advance only the existing reaper timer; no inference or wall-clock wait.
        with monkeypatch.context() as patcher:
            patcher.setattr(bridge_module.asyncio, "sleep", reaper_tick)
            patcher.setattr(bridge_module.time, "monotonic", lambda: 1000)
            reaper = asyncio.create_task(bridge.reap())
            await next_tick.wait()
            reaper.cancel()
            with pytest.raises(asyncio.CancelledError):
                await reaper
        assert not bridge.sessions and not bridge.by_call
        response = await client.post("/v1/conversations/release", json={"owner": "owner"},
                                     headers={"Authorization": "Bearer token"})
        assert response.status_code == 200 and response.json()["status"] == "closed"
    assert calls == ["turn/interrupt", "thread/unsubscribe"] * 2


@pytest.mark.asyncio
async def test_native_terminal_receipt_records_authoritative_status_and_usage_sequence(tmp_path):
    from types import SimpleNamespace
    rpc = AppServer(tmp_path)
    bridge = SubscriptionBridge(rpc, tmp_path)
    reader = asyncio.StreamReader()
    events = [
        {"method": "thread/tokenUsage/updated", "params": {"threadId": "thread", "turnId": "turn",
         "tokenUsage": {"total": {"totalTokens": 11}}}},
        {"method": "turn/completed", "params": {"threadId": "thread", "turn": {"id": "turn", "status": "interrupted"}}},
        {"method": "thread/tokenUsage/updated", "params": {"threadId": "thread", "turnId": "turn",
         "tokenUsage": {"total": {"totalTokens": 15}}}},
    ]
    for event in events:
        reader.feed_data((json.dumps(event) + "\n").encode())
    reader.feed_eof()
    rpc.process = SimpleNamespace(stdout=reader)
    await rpc._read()
    records = [json.loads(line) for line in (tmp_path / "subscription-ledger.jsonl").read_text().splitlines()]
    terminal = next(r for r in records if r["event"] == "native_turn_terminal")
    assert terminal["status"] == "interrupted" and terminal["usage_sequence"] == 1
    assert records[-1]["usage_sequence"] == 2 and records[-1]["native_total_usage"]["totalTokens"] == 15
    assert rpc.terminals["thread"] == {"turn": "turn", "status": "interrupted"}
