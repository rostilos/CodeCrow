"""Exact native completion association; synthetic streams, no native model calls."""
import asyncio
from copy import deepcopy
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from tools.codex_benchmark.bridge import (
    MODEL, SubscriptionBridge, create_app, digest, receipt_deadline,
)
from tools.codex_benchmark.rpc import AppServer


TOOLS = [
    {'type': 'function', 'function': {'name': name, 'parameters': {'type': 'object'}}}
    for name in ('Final', 'read')
]
PAYLOAD = {
    'model': MODEL, 'messages': [{'role': 'user', 'content': 'unchanged task'}],
    'tools': TOOLS, 'codecrow_terminal_tools': ['Final'], 'codecrow_owner': 'owner',
    'reasoning_effort': 'low',
}
USAGE = {
    'inputTokens': 100, 'outputTokens': 7, 'totalTokens': 107,
    'cachedInputTokens': 20, 'reasoningOutputTokens': 2,
}


class NativeRPC(AppServer):
    def __init__(self, path):
        super().__init__(path)
        self.account = {'type': 'chatgpt'}
        self.model = {'defaultReasoningEffort': 'low'}
        self.created = []
        self.requests = []
        self.sent = []
        self.cancelled = []
        self.waiting = asyncio.Event()
        self.stream = asyncio.StreamReader()
        self.process = SimpleNamespace(stdout=self.stream)
        self.reader = asyncio.create_task(self._read())

    async def start_thread(self, instructions, tools, effort):
        thread = f'thread-{len(self.created)}'
        self.created.append((thread, instructions, deepcopy(tools), effort))
        self.events[thread] = asyncio.Queue()
        return thread

    async def request(self, method, params, **kwargs):
        self.requests.append((method, deepcopy(params)))
        return {'turn': {'id': 'turn-1'}} if method == 'turn/start' else {}

    async def send(self, message):
        self.sent.append(deepcopy(message))

    async def interrupt(self, thread, turn):
        self.cancelled.append((thread, turn))
        return True

    async def wait_call_responses(self, *args):
        self.waiting.set()
        return await super().wait_call_responses(*args)

    def feed(self, method, params, **extra):
        self.stream.feed_data((json.dumps({'method': method, 'params': params, **extra}) + '\n').encode())

    def raw(self, call, thread='thread-0', turn='turn-1'):
        self.feed('rawResponseItem/completed', {
            'threadId': thread, 'turnId': turn,
            'item': {'type': 'function_call', 'call_id': call, 'name': 'Final', 'arguments': '{}'},
        })

    def completed(self, response, usage=USAGE, thread='thread-0', turn='turn-1'):
        self.feed('rawResponse/completed', {
            'threadId': thread, 'turnId': turn, 'responseId': response, 'usage': usage,
        })

    def tool(self, call='final', name='Final', thread='thread-0'):
        self.feed('item/tool/call', {
            'threadId': thread, 'turnId': 'turn-1', 'callId': call,
            'tool': name, 'arguments': {'value': 'answer'},
        }, id=call)

    async def finish(self):
        self.stream.feed_eof()
        await self.reader


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


def rows(path):
    return [json.loads(s) for s in (path / 'subscription-ledger.jsonl').read_text().splitlines()]


async def start(path, deadline=True, payload=None):
    rpc = NativeRPC(path)
    bridge = SubscriptionBridge(rpc, path)
    end = time.monotonic() + 120 if deadline is True else deadline
    task = asyncio.create_task(bridge.complete(deepcopy(payload or PAYLOAD), response_deadline=end))
    await until(lambda: bool(rpc.created) and bridge.sessions['thread-0'].turn is not None)
    return (rpc, bridge, task)


def assert_answer(value):
    choice = value['choices'][0]
    assert choice['finish_reason'] == 'tool_calls'
    assert choice['message']['tool_calls'] == [{'id': 'final', 'type': 'function', 'function': {'name': 'Final', 'arguments': '{"value":"answer"}'}}]


@pytest.mark.asyncio
async def test_delayed_current_completion_waits_then_preserves_answer_and_wire(tmp_path):
    rpc, b, task = await start(tmp_path)
    rpc.raw('final')
    rpc.tool()
    await rpc.waiting.wait()
    assert not task.done() and (not rpc.cancelled)
    rpc.completed('current')
    value = await task
    assert_answer(value)
    events = rows(tmp_path)
    wait = next((e for e in events if e['event'] == 'terminal_response_wait'))
    assert wait['status'] == 'matched' and wait['response_ids_by_call'] == {'final': 'current'} and (wait['limit_seconds'] == 1)
    assert [e['event'] for e in events].index('native_response_completed') < [e['event'] for e in events].index('response') < [e['event'] for e in events].index('conversation_terminal')
    assert next((e for e in events if e['event'] == 'request'))['request_sha256'] == digest(PAYLOAD)
    assert rpc.created[0][1:] == ("Follow the user's task and required response format.", [{'type': 'function', 'name': x['function']['name'], 'description': '', 'inputSchema': {'type': 'object'}} for x in TOOLS], 'low')
    assert len([x for x in rpc.requests if x[0] == 'turn/start']) == 1 and (not rpc.sent)
    assert not b.sessions and (not b.by_call) and (not rpc.call_responses) and (not rpc.raw_calls) and (not rpc.completed_responses)
    await rpc.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize('usage', [USAGE, None])
async def test_completion_before_dynamic_delivery_and_null_usage(tmp_path, usage):
    rpc, b, task = await start(tmp_path)
    rpc.raw('final')
    rpc.completed('already', usage)
    rpc.tool()
    assert_answer(await task)
    events = rows(tmp_path)
    assert next((e for e in events if e['event'] == 'terminal_response_wait'))['status'] == 'matched'
    assert next((e for e in events if e['event'] == 'native_response_completed'))['native_usage'] == usage
    if usage is None:
        assert next((e for e in events if e['event'] == 'response'))['usage'] is None
    await rpc.finish()


@pytest.mark.asyncio
async def test_stale_same_turn_duplicate_and_other_thread_never_match_current_call(tmp_path):
    rpc, b, task = await start(tmp_path)
    rpc.raw('old')
    rpc.completed('old-response')
    rpc.raw('final')
    rpc.tool()
    await rpc.waiting.wait()
    rpc.completed('old-response')
    rpc.raw('other', thread='other-thread')
    rpc.completed('other-response', thread='other-thread')
    await until(lambda: ('other-thread', 'turn-1', 'other') in rpc.call_responses)
    assert not task.done() and (not rpc.cancelled)
    rpc.completed('new-response')
    assert_answer(await task)
    wait = next((e for e in rows(tmp_path) if e['event'] == 'terminal_response_wait'))
    assert wait['response_ids_by_call'] == {'final': 'new-response'}
    assert rpc.call_responses['other-thread', 'turn-1', 'other'] == 'other-response'
    await rpc.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize('raw_present', [False, True])
async def test_missing_receipt_or_missing_call_mapping_is_bounded_and_fail_open(tmp_path, raw_present):
    rpc, b, task = await start(tmp_path, deadline=time.monotonic() + 20.09)
    if raw_present:
        rpc.raw('final')
    else:
        rpc.completed('unassociated')
    rpc.tool()
    assert_answer(await task)
    wait = next((e for e in rows(tmp_path) if e['event'] == 'terminal_response_wait'))
    assert wait['status'] == 'timeout' and 0 < wait['limit_seconds'] < 0.09 and (wait['elapsed_seconds'] < 0.3)
    assert rpc.cancelled and (not b.sessions)
    await rpc.finish()


@pytest.mark.asyncio
async def test_reader_error_after_valid_answer_preserves_payload(tmp_path):
    rpc, b, task = await start(tmp_path)
    rpc.raw('final')
    rpc.tool()
    await rpc.waiting.wait()
    await rpc.finish()
    assert_answer(await task)
    assert next((e for e in rows(tmp_path) if e['event'] == 'terminal_response_wait'))['status'] == 'unavailable'
    assert not b.sessions


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel_owner', [False, True])
async def test_external_cancel_or_owner_close_propagates_and_cleans(tmp_path, cancel_owner):
    rpc, b, task = await start(tmp_path)
    rpc.raw('final')
    rpc.tool()
    await rpc.waiting.wait()
    if cancel_owner:
        assert await b.release_owner('owner')
    else:
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rpc.cancelled and (not b.sessions) and (not b.by_call) and (not rpc.call_responses)
    assert next((e for e in rows(tmp_path) if e['event'] == 'terminal_response_wait'))['status'] == 'cancelled'
    await rpc.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize('extra_name', ['Final', 'read'])
async def test_late_call_remains_queued_without_changing_original_answer(tmp_path, extra_name):
    rpc, b, task = await start(tmp_path)
    rpc.raw('final')
    rpc.tool()
    await rpc.waiting.wait()
    queue = rpc.events['thread-0']
    rpc.raw('late')
    rpc.tool('late', extra_name)
    rpc.completed('all-calls')
    assert_answer(await task)
    remaining = list(queue._queue)
    assert any((e['method'] == 'item/tool/call' and e['params']['callId'] == 'late' for e in remaining))
    assert len(rpc.created) == 1 and (not rpc.sent) and (not b.sessions) and rpc.cancelled
    assert next((e for e in rows(tmp_path) if e['event'] == 'terminal_response_wait'))['call_ids'] == ['final']
    await rpc.finish()


@pytest.mark.asyncio
async def test_mixed_batch_waits_for_both_receipts_and_keeps_pending_calls(tmp_path):
    rpc, b, task = await start(tmp_path)
    rpc.raw('final')
    rpc.completed('first')
    rpc.raw('ordinary')
    rpc.tool()
    rpc.tool('ordinary', 'read')
    await rpc.waiting.wait()
    assert not task.done() and not rpc.cancelled
    rpc.completed('second')
    answer = await task
    assert [call['id'] for call in answer['choices'][0]['message']['tool_calls']] == ['final', 'ordinary']
    wait = next(e for e in rows(tmp_path) if e['event'] == 'terminal_response_wait')
    assert wait['response_ids_by_call'] == {'final': 'first', 'ordinary': 'second'}
    assert set(b.sessions['thread-0'].pending) == {'final', 'ordinary'}
    assert not rpc.cancelled and not rpc.sent
    await b.release_owner('owner')
    await rpc.finish()


@pytest.mark.parametrize('header,expected', [(None, None), ('', None), ('nan', None), ('inf', None), ('-1', None), ('0', None), ('bad', None), ('120', 220), ('0.5', 100.5)])
def test_sdk_header_optional_allowance(header, expected):
    assert receipt_deadline(header, 100) == expected


@pytest.mark.asyncio
async def test_http_header_is_outside_model_payload_and_elapsed_budget(tmp_path):
    rpc = NativeRPC(tmp_path)
    b = SubscriptionBridge(rpc, tmp_path)
    app = create_app(b, 'test')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local') as client:
        task = asyncio.create_task(client.post('/v1/chat/completions', json=PAYLOAD, headers={'Authorization': 'Bearer test', 'x-stainless-read-timeout': '20.09'}))
        await until(lambda: bool(rpc.created) and b.sessions['thread-0'].turn is not None)
        rpc.raw('final')
        rpc.tool()
        response = await task
    assert response.status_code == 200
    assert_answer(response.json())
    events = rows(tmp_path)
    assert next((e for e in events if e['event'] == 'request'))['request_sha256'] == digest(PAYLOAD)
    assert 0 < next((e for e in events if e['event'] == 'terminal_response_wait'))['limit_seconds'] < 0.09
    await rpc.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize('remaining', [None, 0, 10, 20])
async def test_no_remaining_allowance_adds_no_wait(tmp_path, remaining):
    deadline = time.monotonic() + remaining if remaining is not None else None
    rpc, b, task = await start(tmp_path, deadline=deadline)
    rpc.tool()
    assert_answer(await task)
    assert not rpc.waiting.is_set()
    assert next((e for e in rows(tmp_path) if e['event'] == 'terminal_response_wait'))['status'] == 'no_allowance'
    await rpc.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize('remaining', [None, 0, 10, 20])
@pytest.mark.parametrize('usage', [USAGE, None])
async def test_cached_exact_completion_needs_no_wait_allowance(tmp_path, remaining, usage):
    deadline = time.monotonic() + remaining if remaining is not None else None
    rpc, bridge, task = await start(tmp_path, deadline=deadline)
    rpc.raw('final')
    rpc.completed('cached', usage)
    rpc.tool()
    assert_answer(await task)
    assert not rpc.waiting.is_set()
    event = next((e for e in rows(tmp_path) if e['event'] == 'terminal_response_wait'))
    assert event['status'] == 'matched' and event['limit_seconds'] == 0
    assert event['response_ids_by_call'] == {'final': 'cached'}
    assert not bridge.sessions
    await rpc.finish()


@pytest.mark.asyncio
async def test_direct_raw_function_id_matches_returned_native_id(tmp_path):
    rpc, bridge, task = await start(tmp_path)
    call_id = 'call_CG4L3ILBLSw5WeaIdqABLT5T'
    rpc.raw(call_id)
    rpc.tool(call_id)
    await rpc.waiting.wait()
    rpc.completed('resp_001e00726bdad716016acbaf15afd887d29ab6517429a63b23')
    response = await task
    assert response['choices'][0]['message']['tool_calls'][0]['id'] == call_id
    event = next((e for e in rows(tmp_path) if e['event'] == 'terminal_response_wait'))
    assert event['response_ids_by_call'] == {call_id: 'resp_001e00726bdad716016acbaf15afd887d29ab6517429a63b23'}
    assert not rpc.sent and (not bridge.sessions)
    await rpc.finish()


@pytest.mark.asyncio
async def test_nested_child_id_is_not_certified_by_parent_raw_completion(tmp_path):
    rpc, bridge, task = await start(tmp_path, deadline=time.monotonic() + 20.12)
    rpc.feed('rawResponseItem/completed', {'threadId': 'thread-0', 'turnId': 'turn-1', 'item': {'type': 'custom_tool_call', 'call_id': 'call_parent', 'name': 'exec', 'input': 'opaque'}})
    rpc.completed('parent-response')
    child = 'exec-33230000-0000-0000-0000-000000000000'
    rpc.tool(child)
    response = await task
    assert response['choices'][0]['message']['tool_calls'][0]['id'] == child
    event = next((e for e in rows(tmp_path) if e['event'] == 'terminal_response_wait'))
    assert event['status'] == 'timeout' and event['response_ids_by_call'] == {}
    assert response['usage'] is None and (not bridge.sessions) and (not rpc.sent)
    await rpc.finish()


@pytest.mark.asyncio
async def test_ordinary_tool_waits_for_receipt_without_closing_conversation(tmp_path):
    rpc, bridge, task = await start(tmp_path)
    rpc.raw('read-call')
    rpc.tool('read-call', 'read')
    await rpc.waiting.wait()
    assert not task.done() and not rpc.cancelled
    rpc.completed('ordinary-response')
    response = await task
    assert response['choices'][0]['message']['tool_calls'][0]['id'] == 'read-call'
    wait = next(e for e in rows(tmp_path) if e['event'] == 'terminal_response_wait')
    assert wait['response_ids_by_call'] == {'read-call': 'ordinary-response'}
    assert set(bridge.sessions['thread-0'].pending) == {'read-call'}
    assert len([method for method, _ in rpc.requests if method == 'turn/start']) == 1
    assert not rpc.cancelled and not rpc.sent
    await bridge.release_owner('owner')
    await rpc.finish()
