"""No-network behavior checks for the opt-in and exact per-response receipt."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from tools.codex_benchmark.bridge import Conversation, SubscriptionBridge
from tools.codex_benchmark.rpc import AppServer, MODEL


def usage(i=100, o=10, cached=30, reasoning=4):
    return {'inputTokens': i, 'outputTokens': o, 'totalTokens': i + o,
            'cachedInputTokens': cached, 'reasoningOutputTokens': reasoning,
            'cacheWriteInputTokens': 0}


def raw(response_id, value):
    return {'method': 'rawResponse/completed', 'params': {
        'threadId': 'thread', 'turnId': 'turn', 'responseId': response_id,
        'usage': value, 'usageMetadata': {'amount': '0', 'metadata': {'source': 'provider'}}}}


async def feed(rpc, events):
    reader = asyncio.StreamReader()
    for event in events:
        reader.feed_data((json.dumps(event) + '\n').encode())
    reader.feed_eof()
    rpc.process = SimpleNamespace(stdout=reader)
    await rpc._read()


def ledger(path):
    return [json.loads(line) for line in (path / 'subscription-ledger.jsonl').read_text().splitlines()]


@pytest.mark.asyncio
async def test_native_thread_start_preserves_task_tools_and_requests_raw_events(tmp_path):
    tool = {"type": "function", "name": "read", "description": "exact source",
            "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}}}
    rpc = AppServer(tmp_path)
    rpc.model = {"supportedReasoningEfforts": [{"reasoningEffort": "low"}]}
    calls = []

    async def request(method, params):
        calls.append((method, copy.deepcopy(params)))
        return {"thread": {"id": "thread"}, "model": MODEL, "modelProvider": "openai"}

    rpc.request = request
    assert await rpc.start_thread("Exact instructions.\nNo additions.", [tool], "low") == "thread"
    assert len(calls) == 1
    method, params = calls[0]
    assert method == "thread/start"
    assert params == {
        "model": MODEL, "modelProvider": "openai", "allowProviderModelFallback": False,
        "cwd": str(tmp_path), "approvalPolicy": "never", "sandbox": "read-only",
        "ephemeral": True, "experimentalRawEvents": True, "personality": "none",
        "baseInstructions": "Exact instructions.\nNo additions.",
        "developerInstructions": "Use only the supplied benchmark task and dynamic tools. Do not access local files, run commands, browse, or delegate. Return the task's requested output without progress commentary.",
        "dynamicTools": [tool], "serviceTier": "default", "config": {"model_reasoning_effort": "low"},
    }


@pytest.mark.asyncio
async def test_raw_usage_is_durable_without_changing_response_queue_or_cumulative_counters(tmp_path):
    rpc = AppServer(tmp_path)
    bridge = SubscriptionBridge(rpc, tmp_path)
    bridge.sessions['thread'] = Conversation('thread', [{'name': 'read'}], [], owner='owner', turn='turn')
    u = usage()
    events = [raw('response-1', u), {'id': 31, 'method': 'item/tool/call', 'params': {
        'threadId': 'thread', 'turnId': 'turn', 'callId': 'call', 'tool': 'read', 'arguments': {'path': 'exact'}}}]
    await feed(rpc, events)
    rows = ledger(tmp_path)
    assert len(rows) == 1
    assert rows[0]['native_usage'] == u and rows[0]['response_id'] == 'response-1'
    assert rows[0]['owner'] == 'owner' and rows[0]['conversation_active'] is True
    assert not rpc.usage and not bridge.usage_sequences
    queued = [rpc.events['thread'].get_nowait() for _ in range(len(events))]
    assert queued == events
    assert not bridge.by_call and not bridge.sessions['thread'].reported_usage
    # feed() closes its finite mock stream; replay the two original messages
    # into the ordinary live queue without its artificial EOF marker.
    rpc.events['thread'] = asyncio.Queue()
    for event in queued:
        await rpc.events['thread'].put(event)
    answer, finish = await bridge._answer(bridge.sessions['thread'])
    assert finish == 'tool_calls'
    assert answer['tool_calls'] == [{'id': 'call', 'type': 'function',
        'function': {'name': 'read', 'arguments': '{"path":"exact"}'}}]
    assert len(ledger(tmp_path)) == 2  # one raw receipt and the unchanged tool delivery


@pytest.mark.asyncio
async def test_late_raw_receipts_survive_release_and_keep_duplicate_null_conflicting_values(tmp_path):
    rpc = AppServer(tmp_path)
    bridge = SubscriptionBridge(rpc, tmp_path)
    u1, u2 = usage(), usage(40, 5, 10, 2)
    events = [raw('response-1', u1), raw('response-1', u1),
              raw('response-2', u2), raw('response-3', None),
              raw('response-1', usage(101))]
    await feed(rpc, events)
    rows = ledger(tmp_path)
    assert len(rows) == len(events)
    assert all(not r['conversation_active'] and r['owner'] is None for r in rows)
    assert [r['native_usage'] for r in rows] == [u1, u1, u2, None, usage(101)]
    # An offline consumer can detect an inconsistent duplicate; the collector
    # must retain it rather than silently select or add it.
    grouped = {}
    for r in rows:
        grouped.setdefault(r['response_id'], []).append(r['native_usage'])
    assert len({json.dumps(v, sort_keys=True) for v in grouped['response-1']}) == 2
    assert grouped['response-3'] == [None]


@pytest.mark.asyncio
async def test_unique_response_totals_do_not_add_cumulative_or_subset_counters(tmp_path):
    rpc = AppServer(tmp_path)
    bridge = SubscriptionBridge(rpc, tmp_path)
    u1, u2 = usage(), usage(40, 5, 10, 2)
    total = {k: u1[k] + u2[k] for k in u1}
    await feed(rpc, [raw('response-1', u1), raw('response-1', u1), raw('response-2', u2),
        {'method': 'thread/tokenUsage/updated', 'params': {
            'threadId': 'thread', 'turnId': 'turn', 'tokenUsage': {'total': total, 'last': u2}}},
        {'method': 'turn/completed', 'params': {'threadId': 'thread', 'turn': {'id': 'turn', 'status': 'interrupted'}}}])
    rows = ledger(tmp_path)
    unique = {r['response_id']: r['native_usage'] for r in rows if r['event'] == 'native_response_completed'}
    summed = {k: sum(u[k] for u in unique.values()) for k in u1}
    assert summed == total == rpc.usage['thread']['total']
    assert summed['totalTokens'] == 155
    assert summed['totalTokens'] == summed['inputTokens'] + summed['outputTokens']
    assert summed['cachedInputTokens'] == 40 and summed['reasoningOutputTokens'] == 6
    assert rpc.terminals['thread']['status'] == 'interrupted'


@pytest.mark.asyncio
async def test_canceled_generation_does_not_acquire_fabricated_usage(tmp_path):
    rpc = AppServer(tmp_path)
    bridge = SubscriptionBridge(rpc, tmp_path)
    await feed(rpc, [{'method': 'turn/completed', 'params': {
        'threadId': 'thread', 'turn': {'id': 'turn', 'status': 'interrupted'}}}])
    assert not rpc.usage and not bridge.usage_sequences
    assert [r['event'] for r in ledger(tmp_path)] == ['native_turn_terminal']


@pytest.mark.asyncio
async def test_late_raw_receipt_is_joinable_without_fabricating_sdk_usage(tmp_path):
    rpc = AppServer(tmp_path)
    bridge = SubscriptionBridge(rpc, tmp_path)
    session = Conversation("thread", [], [], owner="owner", turn="turn")
    bridge.record({"event": "conversation_started", "id": "request", "thread": "thread", "owner": "owner"})
    bridge.record_native_response_completed("thread", "turn", "response", usage(), None)
    rows = ledger(tmp_path)
    assert rows[-1]["owner"] is None and rows[-1]["conversation_active"] is False
    assert rows[0]["thread"] == rows[-1]["thread"]
    assert bridge.response_usage(session) is None and not rpc.usage


@pytest.mark.asyncio
async def test_native_process_argv_uses_direct_functions_configuration(tmp_path, monkeypatch):
    from tools.codex_benchmark import rpc as rpc_module

    observed = []
    async def create_process(*args, **kwargs):
        observed.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    async def read_no_events():
        return

    async def send_no_events(message):
        return

    async def request(method, params):
        if method == "account/read":
            return {"account": {"type": "chatgpt", "planType": "pro"}}
        if method == "model/list":
            return {"data": [{"model": MODEL}]}
        return {}

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-subprocess")
    monkeypatch.setenv("CODEX_API_KEY", "must-not-reach-subprocess")
    monkeypatch.setattr(rpc_module.asyncio, "create_subprocess_exec", create_process)
    rpc = AppServer(tmp_path)
    rpc._read, rpc.send, rpc.request = read_no_events, send_no_events, request
    try:
        await rpc.start()
    finally:
        await rpc.close()
    assert len(observed) == 1
    args, kwargs = observed[0]
    config = dict(arg.split("=", 1) for i, arg in enumerate(args) if i and args[i - 1] == "-c")
    assert "features.code_mode" not in config
    assert json.loads(config["features.code_mode.enabled"]) is False
    assert json.loads(config["features.code_mode.direct_only_tool_namespaces"]) == ["functions"]
    assert json.loads(config["features.code_mode_host"]) is True
    assert args[-3:] == ("app-server", "--listen", "stdio://")
    assert kwargs["cwd"] == tmp_path
    assert "OPENAI_API_KEY" not in kwargs["env"] and "CODEX_API_KEY" not in kwargs["env"]
