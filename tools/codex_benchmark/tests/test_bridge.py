import asyncio
from pathlib import Path

import pytest

from tools.codex_benchmark.bridge import SubscriptionBridge, create_app, dynamic_tools, prompt_parts
from tools.codex_benchmark.inference_entrypoint import install_subscription_guard
from tools.codex_benchmark.rpc import MODEL


class FakeRPC:
    def __init__(self):
        self.account = {"type": "chatgpt", "planType": "pro"}
        self.model = {"defaultReasoningEffort": "medium"}
        self.events = {}
        self.usage = {}
        self.sent = []
        self.created = []
        self.cancelled = []

    async def start_thread(self, instructions, tools, effort):
        thread = f"thread-{len(self.created)}"
        self.created.append((thread, instructions, tools, effort))
        self.events[thread] = asyncio.Queue()
        return thread

    async def request(self, method, params, **kwargs):
        if method == "turn/start":
            return {"turn": {"id": "turn-1"}}
        return {}

    async def send(self, message):
        self.sent.append(message)

    async def interrupt(self, thread, turn):
        self.cancelled.append((thread, turn))


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
