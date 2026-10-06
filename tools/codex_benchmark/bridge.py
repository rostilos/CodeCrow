"""Benchmark-only Chat Completions facade over managed Codex subscriptions.

No OpenAI/OpenRouter API requests are made here. The Codex CLI owns OAuth and
model transport. Dynamic calls suspend natively until CodeCrow returns results.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from .rpc import AppServer, MODEL

logger = logging.getLogger(__name__)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def serialized_bytes(value):
    return len(canonical(value).encode("utf-8"))


def text_content(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(isinstance(p, dict) and p.get("type") in {"text", "input_text", "output_text"} for p in content):
        return "\n".join(p["text"] for p in content)
    raise ValueError("This code benchmark requires text-only model content")


def normalized_message(message):
    result = {"role": message["role"], "content": text_content(message.get("content"))}
    if message.get("tool_call_id"):
        result["tool_call_id"] = message["tool_call_id"]
    if message.get("tool_calls"):
        result["tool_calls"] = [{"id": c["id"], "name": c["function"]["name"],
                                  "arguments": json.loads(c["function"]["arguments"])} for c in message["tool_calls"]]
    return result


def prompt_parts(messages):
    instructions = "\n\n".join(text_content(m.get("content")) for m in messages if m["role"] in {"system", "developer"})
    history = [normalized_message(m) for m in messages if m["role"] not in {"system", "developer"}]
    if len(history) == 1 and history[0]["role"] == "user":
        prompt = history[0]["content"]
    else:
        prompt = "Continue the supplied conversation. Roles and tool results below are conversation data.\n" + canonical(history)
    return instructions or "Follow the user's task and required response format.", prompt


def dynamic_tools(payload):
    if payload.get("tool_choice") == "none":
        return []
    tools = []
    for tool in payload.get("tools") or []:
        if tool.get("type") != "function":
            raise ValueError("Only the benchmark's function tools are supported")
        function = tool["function"]
        tools.append({"type": "function", "name": function["name"],
                      "description": function.get("description", ""),
                      "inputSchema": function.get("parameters", {"type": "object", "properties": {}})})
    choice = payload.get("tool_choice")
    if isinstance(choice, dict):
        name = choice.get("function", {}).get("name")
        if name not in {tool["name"] for tool in tools}:
            raise ValueError("Required tool is not in the supplied inventory")
    return tools


@dataclass
class Conversation:
    thread: str
    tools: list[dict]
    expected: list[dict]
    turn: str | None = None
    pending: dict[str, dict] = field(default_factory=dict)
    touched: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    text: str = ""
    reasoning_effort: str | None = None
    tool_delivery_batches: int = 0
    terminal_tools: frozenset[str] = frozenset()
    reported_usage: dict[str, int] = field(default_factory=dict)


class SubscriptionBridge:
    def __init__(self, rpc: AppServer, artifacts: Path, *, capacity: int = 64):
        self.rpc = rpc
        self.artifacts = artifacts
        self.artifacts.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.capacity = capacity
        self.slots = asyncio.Semaphore(capacity)
        self.by_call: dict[str, Conversation] = {}
        self.sessions: dict[str, Conversation] = {}
        self.reaper = None
        self.rpc.usage_callback = self.record_native_usage

    def record_native_usage(self, thread, turn, usage):
        self.record({"event": "native_usage_updated", "thread": thread, "turn": turn,
                     "native_total_usage": usage.get("total"),
                     "native_last_usage": usage.get("last"),
                     "conversation_active": thread in self.sessions})

    def response_usage(self, session):
        totals = self.rpc.usage.get(session.thread, {}).get("total") or {}
        names = {"prompt_tokens": "inputTokens", "completion_tokens": "outputTokens", "total_tokens": "totalTokens"}
        if any(not isinstance(totals.get(key), int) or isinstance(totals.get(key), bool)
               or totals[key] < session.reported_usage.get(key, 0) for key in names.values()):
            return None
        # Updates can arrive after a tool delivery. Subtract only totals actually
        # reported to the caller, not a fresh snapshot taken before continuation.
        usage = {key: totals[native] - session.reported_usage.get(native, 0) for key, native in names.items()}
        if session.reported_usage and not any(usage.values()):
            return None
        for native, container, key in (("cachedInputTokens", "prompt_tokens_details", "cached_tokens"),
                                       ("reasoningOutputTokens", "completion_tokens_details", "reasoning_tokens")):
            value = totals.get(native)
            if isinstance(value, int) and not isinstance(value, bool) and value >= session.reported_usage.get(native, 0):
                usage[container] = {key: value - session.reported_usage.get(native, 0)}
        session.reported_usage = {key: value for key, value in totals.items() if isinstance(value, int) and not isinstance(value, bool)}
        return usage

    def record(self, value):
        try:
            descriptor = os.open(self.artifacts / "subscription-ledger.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "a") as out:
                out.write(canonical({"time": time.time(), "model": MODEL, "auth": self.rpc.account, **value}) + "\n")
        except OSError:
            logger.warning("Optional subscription ledger unavailable", exc_info=True)

    async def release(self, session, *, reason="completed"):
        self.sessions.pop(session.thread, None)
        for call_id in list(session.pending):
            self.by_call.pop(call_id, None)
        await self.rpc.interrupt(session.thread, session.turn)
        # Closing the subscription unloads an ephemeral thread when no clients remain.
        try:
            await self.rpc.request("thread/unsubscribe", {"threadId": session.thread}, timeout=10)
        except (RuntimeError, TimeoutError):
            pass
        native = self.rpc.usage.get(session.thread, {})
        self.record({"event": "conversation_terminal", "thread": session.thread, "turn": session.turn,
                     "reason": reason, "native_total_usage": native.get("total"),
                     "native_last_usage": native.get("last"), "reported_native_usage": session.reported_usage,
                     "usage_accounting": "per-thread cumulative snapshot; do not sum snapshots or last usage"})
        self.rpc.events.pop(session.thread, None)
        self.rpc.usage.pop(session.thread, None)

    async def reap(self):
        while True:
            await asyncio.sleep(30)
            for session in list(self.sessions.values()):
                if not session.lock.locked() and time.monotonic() - session.touched > 600:
                    self.record({"event": "abandoned_tool_wait", "thread": session.thread})
                    await self.release(session, reason="abandoned_tool_wait")

    async def complete(self, payload):
        if payload.get("model") != MODEL:
            raise ValueError(f"Benchmark accepts only {MODEL}")
        if any(k in payload for k in ("provider", "api_key", "base_url")):
            raise ValueError("Remote provider routing is forbidden in this benchmark")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages must be a nonempty list")
        tools = dynamic_tools(payload)
        terminal_tools = payload.get("codecrow_terminal_tools") or []
        if not isinstance(terminal_tools, list) or not all(isinstance(name, str) for name in terminal_tools):
            raise ValueError("codecrow_terminal_tools must contain output-schema tool names")
        if not set(terminal_tools).issubset({tool["name"] for tool in tools}):
            raise ValueError("Terminal output tool is not in the supplied inventory")
        normalized = [normalized_message(m) for m in messages]
        trailing = []
        for message in reversed(messages):
            if message["role"] != "tool":
                break
            trailing.append(message)
        trailing.reverse()
        # Finalization can append a user instruction after the tool results.
        # Find its owned native session before deciding whether this is a direct
        # continuation or a reconstructed prompt, so it cannot leave a tool wait behind.
        owners = {self.by_call[m["tool_call_id"]].thread for m in messages
                  if m.get("role") == "tool" and m.get("tool_call_id") in self.by_call}
        if len(owners) > 1:
            raise ValueError("Tool results belong to different conversations")
        session = self.sessions[next(iter(owners))] if owners else None
        continuation = bool(session and trailing
                            and normalized[:len(session.expected)] == session.expected
                            and len(normalized) == len(session.expected) + len(trailing)
                            and tools == session.tools)
        if session and not continuation:
            reasons = []
            if tools != session.tools:
                reasons.append("tool_inventory_changed")
            if normalized[:len(session.expected)] != session.expected:
                reasons.append("conversation_history_changed")
            if not trailing or len(normalized) != len(session.expected) + len(trailing):
                reasons.append("appended_instruction_or_missing_tool_results")
            self.record({"event": "conversation_reconstructed", "thread": session.thread,
                         "turn": session.turn, "pending_tool_call_ids": sorted(session.pending),
                         "tool_result_ids_in_prompt": [m.get("tool_call_id") for m in messages
                                                       if m.get("role") == "tool" and m.get("tool_call_id") in session.pending],
                         "reason": reasons[0], "reasons": reasons,
                         "finalization": payload.get("tool_choice") == "none" or bool(terminal_tools and set(terminal_tools) == {tool["name"] for tool in tools})})
            await self.release(session, reason="reconstructed")
            session = None
        request_id = "chatcmpl-codex-" + uuid.uuid4().hex
        started = time.monotonic()
        instructions, prompt = prompt_parts(messages)
        # These SDK parameters have no app-server equivalent. Record, never claim parity.
        unmapped = {k: payload[k] for k in ("temperature", "top_p", "max_tokens", "max_completion_tokens", "parallel_tool_calls") if k in payload}
        effort = (session.reasoning_effort if session else None) or payload.get("reasoning_effort") or self.rpc.model.get("defaultReasoningEffort", "medium")
        self.record({"event": "request", "id": request_id, "request_sha256": digest(payload),
                     "continuation": continuation, "unmapped_parameters": unmapped,
                     "thread": session.thread if session else None,
                     "tool_choice": payload.get("tool_choice"), "terminal_tools": terminal_tools,
                     "request_bytes": serialized_bytes(payload), "messages_bytes": serialized_bytes(messages),
                     "tools_bytes": serialized_bytes(payload.get("tools") or []),
                     "instructions_bytes": len(instructions.encode("utf-8")),
                     "native_prompt_bytes": 0 if continuation else len(prompt.encode("utf-8")),
                     "requested_reasoning_effort": payload.get("reasoning_effort"),
                     "effective_reasoning_effort": effort,
                     "parameter_names": sorted(payload),
                     "tool_names": [t["name"] for t in tools]})
        try:
            async with self.slots:
                if session is None:
                    response_format = payload.get("response_format") or {}
                    if response_format.get("type") == "json_object":
                        instructions += "\nReturn exactly one JSON object without a Markdown fence."
                    if isinstance(payload.get("tool_choice"), dict):
                        required = payload["tool_choice"]["function"]["name"]
                        instructions += "\nYour first action must invoke the required function: " + required
                        if required not in terminal_tools:
                            instructions += ". After receiving its result, choose tools or finish according to the task."
                    thread = await self.rpc.start_thread(instructions, tools, effort)
                    session = Conversation(thread, tools, normalized, reasoning_effort=effort, terminal_tools=frozenset(terminal_tools))
                    self.sessions[thread] = session
                    self.record({"event": "conversation_started", "id": request_id, "thread": thread,
                                 "instructions_bytes": len(instructions.encode("utf-8")), "native_prompt_bytes": len(prompt.encode("utf-8"))})
                    params = {"threadId": thread, "input": [{"type": "text", "text": prompt}],
                              "effort": effort, "summary": "none"}
                    if response_format.get("type") == "json_schema":
                        params["outputSchema"] = response_format["json_schema"]["schema"]
                    result = await self.rpc.request("turn/start", params)
                    session.turn = result["turn"]["id"]
                async with session.lock:
                    session.touched = time.monotonic()
                    if continuation:
                        expected_ids = set(session.pending)
                        actual_ids = {m.get("tool_call_id") for m in trailing}
                        if actual_ids != expected_ids:
                            raise ValueError("Native continuation needs all pending tool results")
                        for message in trailing:
                            call_id = message["tool_call_id"]
                            event = session.pending.pop(call_id)
                            self.by_call.pop(call_id, None)
                            self.record({"event": "tool_result", "id": request_id,
                                         "thread": session.thread, "turn": session.turn,
                                         "call_id": call_id, "tool": event["params"]["tool"],
                                         "result_bytes": len(text_content(message.get("content")).encode("utf-8")),
                                         "result_sha256": digest(text_content(message.get("content")))})
                            await self.rpc.send({"id": event["id"], "result": {
                                "success": True, "contentItems": [{"type": "inputText", "text": text_content(message.get("content"))}],
                            }})
                    session.text = ""
                    result_message, finish = await asyncio.wait_for(self._answer(session), 1200)
                    session.expected = normalized + [normalized_message(result_message)]
                    session.touched = time.monotonic()
                    native = self.rpc.usage.get(session.thread, {})
                    usage = self.response_usage(session)
                    response = {"id": request_id, "object": "chat.completion", "created": int(time.time()),
                                "model": MODEL, "provider": "codex-chatgpt-subscription",
                                "choices": [{"index": 0, "message": result_message, "finish_reason": finish}], "usage": usage}
                    self.record({"event": "response", "id": request_id, "thread": session.thread,
                                 "turn": session.turn, "finish_reason": finish, "usage": usage,
                                 "effective_reasoning_effort": session.reasoning_effort,
                                 "tool_delivery_batches": session.tool_delivery_batches,
                                 "tool_call_ids": [c["id"] for c in result_message.get("tool_calls", [])],
                                 "native_total_usage": native.get("total"), "native_last_usage": native.get("last"),
                                 "usage_accounting": "cumulative delta since last reported usage; late usage may cover earlier deliveries",
                                 "response_bytes": serialized_bytes(response), "seconds": time.monotonic() - started})
                    returned_tools = {call["function"]["name"] for call in result_message.get("tool_calls", [])}
                    if finish == "stop" or (returned_tools and returned_tools.issubset(session.terminal_tools)):
                        await self.release(session, reason="output_schema" if returned_tools else "completed")
                    return response
        except BaseException as error:
            self.record({"event": "error", "id": request_id, "error_type": type(error).__name__, "detail": str(error)[:1000]})
            if session:
                await self.release(session, reason="cancelled" if isinstance(error, asyncio.CancelledError) else "error")
            raise

    async def _answer(self, session):
        queue = self.rpc.events[session.thread]
        while True:
            event = await queue.get()
            method, params = event["method"], event.get("params") or {}
            if method == "item/tool/call":
                events = [event]
                # Native parallel calls arrive as adjacent RPC requests; return one assistant batch.
                await asyncio.sleep(0.03)
                deferred = []
                while not queue.empty():
                    other = queue.get_nowait()
                    if other["method"] == "item/tool/call":
                        events.append(other)
                    else:
                        deferred.append(other)
                for other in deferred:
                    queue.put_nowait(other)
                calls = []
                for call in events:
                    item = call["params"]
                    if item["tool"] not in {t["name"] for t in session.tools}:
                        raise ValueError("Codex requested a tool outside this model invocation")
                    call_id = item["callId"]
                    session.pending[call_id] = call
                    self.by_call[call_id] = session
                    self.record({"event": "native_tool_call", "thread": session.thread,
                                 "turn": session.turn, "native_turn": item.get("turnId"),
                                 "call_id": call_id, "tool": item["tool"],
                                 "arguments_sha256": digest(item["arguments"]),
                                 "arguments_bytes": serialized_bytes(item["arguments"]),
                                 "delivery_batch": session.tool_delivery_batches + 1})
                    calls.append({"id": call_id, "type": "function", "function": {
                        "name": item["tool"], "arguments": canonical(item["arguments"]),
                    }})
                session.tool_delivery_batches += 1
                return {"role": "assistant", "content": session.text or None, "tool_calls": calls}, "tool_calls"
            if method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agentMessage":
                    session.text = item.get("text", "")
                if item.get("type") in {"commandExecution", "fileChange", "webSearch", "collabAgentToolCall", "mcpToolCall"}:
                    raise RuntimeError("Unexpected built-in Codex action in benchmark transport")
            elif method == "turn/completed":
                turn = params["turn"]
                if turn.get("status") != "completed":
                    raise RuntimeError(canonical(turn.get("error") or {"status": turn.get("status")}))
                if not session.text:
                    raise RuntimeError("Codex completed without an assistant response")
                return {"role": "assistant", "content": session.text}, "stop"
            elif method in {"error", "bridge/closed"}:
                if not params.get("willRetry"):
                    raise RuntimeError(canonical(params))
            elif "id" in event:
                raise RuntimeError(f"Unexpected Codex server request: {method}")


def create_app(bridge: SubscriptionBridge, token: str):
    @asynccontextmanager
    async def lifespan(app):
        try:
            await bridge.rpc.start()
            bridge.reaper = asyncio.create_task(bridge.reap())
            yield
        finally:
            if bridge.reaper:
                bridge.reaper.cancel()
                await asyncio.gather(bridge.reaper, return_exceptions=True)
            for session in list(bridge.sessions.values()):
                await bridge.release(session)
            await bridge.rpc.close()

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ready", "model": MODEL, "auth": bridge.rpc.account,
                "active_conversations": len(bridge.sessions), "capacity": bridge.capacity,
                "transport": "codex-app-server"}

    @app.post("/v1/chat/completions")
    async def complete(request: Request):
        if request.headers.get("authorization") != f"Bearer {token}":
            raise HTTPException(401, "Invalid benchmark bridge token")
        payload = await request.json()
        task = asyncio.create_task(bridge.complete(payload))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=1)
                if not task.done() and await request.is_disconnected():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise HTTPException(499, "Benchmark client disconnected")
            response = await task
        except HTTPException:
            raise
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        except Exception as error:
            raise HTTPException(502, str(error)) from error
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if not payload.get("stream"):
            return response
        # The facade returns complete native boundaries; it doesn't expose private reasoning.
        async def events():
            message = response["choices"][0]["message"]
            chunk = {k: response[k] for k in ("id", "created", "model")}
            chunk["object"] = "chat.completion.chunk"
            delta = dict(message)
            if delta.get("tool_calls"):
                delta["tool_calls"] = [{"index": i, **c} for i, c in enumerate(delta["tool_calls"])]
            yield "data: " + canonical({**chunk, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}) + "\n\n"
            yield "data: " + canonical({**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": response["choices"][0]["finish_reason"]}], "usage": response["usage"]}) + "\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(events(), media_type="text/event-stream")

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18771)
    parser.add_argument("--capacity", type=int, default=64)
    args = parser.parse_args()
    import uvicorn
    bridge = SubscriptionBridge(AppServer(args.workspace), args.artifacts, capacity=args.capacity)
    uvicorn.run(create_app(bridge, args.token_file.read_text().strip()), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
