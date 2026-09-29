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
import os
from pathlib import Path
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from .rpc import AppServer, MODEL


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


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
        tools = [t for t in tools if t["name"] == name]
        if not tools:
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


class SubscriptionBridge:
    def __init__(self, rpc: AppServer, artifacts: Path, *, capacity: int = 4):
        self.rpc = rpc
        self.artifacts = artifacts
        self.artifacts.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.slots = asyncio.Semaphore(capacity)
        self.by_call: dict[str, Conversation] = {}
        self.sessions: dict[str, Conversation] = {}
        self.reaper = None

    def record(self, value):
        descriptor = os.open(self.artifacts / "subscription-ledger.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a") as out:
            out.write(canonical({"time": time.time(), "model": MODEL, "auth": self.rpc.account, **value}) + "\n")

    async def release(self, session):
        self.sessions.pop(session.thread, None)
        for call_id in list(session.pending):
            self.by_call.pop(call_id, None)
        await self.rpc.interrupt(session.thread, session.turn)
        # Closing the subscription unloads an ephemeral thread when no clients remain.
        try:
            await self.rpc.request("thread/unsubscribe", {"threadId": session.thread}, timeout=10)
        except (RuntimeError, TimeoutError):
            pass
        self.rpc.events.pop(session.thread, None)
        self.rpc.usage.pop(session.thread, None)

    async def reap(self):
        while True:
            await asyncio.sleep(30)
            for session in list(self.sessions.values()):
                if not session.lock.locked() and time.monotonic() - session.touched > 600:
                    self.record({"event": "abandoned_tool_wait", "thread": session.thread})
                    await self.release(session)

    async def complete(self, payload):
        if payload.get("model") != MODEL:
            raise ValueError(f"Benchmark accepts only {MODEL}")
        if any(k in payload for k in ("provider", "api_key", "base_url")):
            raise ValueError("Remote provider routing is forbidden in this benchmark")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages must be a nonempty list")
        tools = dynamic_tools(payload)
        normalized = [normalized_message(m) for m in messages]
        trailing = []
        for message in reversed(messages):
            if message["role"] != "tool":
                break
            trailing.append(message)
        trailing.reverse()
        owners = {self.by_call[m["tool_call_id"]].thread for m in trailing if m.get("tool_call_id") in self.by_call}
        if len(owners) > 1:
            raise ValueError("Tool results belong to different conversations")
        session = self.sessions[next(iter(owners))] if owners else None
        continuation = bool(session and normalized[:len(session.expected)] == session.expected and tools == session.tools)
        if session and not continuation:
            self.record({"event": "conversation_reconstructed", "thread": session.thread,
                         "reason": "Caller changed the supplied history or tool inventory"})
            await self.release(session)
            session = None
        request_id = "chatcmpl-codex-" + uuid.uuid4().hex
        started = time.monotonic()
        before = dict(self.rpc.usage.get(session.thread, {}).get("total", {})) if session else {}
        # These SDK parameters have no app-server equivalent. Record, never claim parity.
        unmapped = {k: payload[k] for k in ("temperature", "top_p", "max_tokens", "max_completion_tokens", "parallel_tool_calls") if k in payload}
        self.record({"event": "request", "id": request_id, "request_sha256": digest(payload),
                     "continuation": continuation, "unmapped_parameters": unmapped,
                     "parameter_names": sorted(payload),
                     "tool_names": [t["name"] for t in tools]})
        try:
            async with self.slots:
                if session is None:
                    instructions, prompt = prompt_parts(messages)
                    effort = payload.get("reasoning_effort") or self.rpc.model.get("defaultReasoningEffort", "medium")
                    response_format = payload.get("response_format") or {}
                    if response_format.get("type") == "json_object":
                        instructions += "\nReturn exactly one JSON object without a Markdown fence."
                    if isinstance(payload.get("tool_choice"), dict):
                        instructions += "\nRespond by invoking the required function: " + tools[0]["name"]
                    thread = await self.rpc.start_thread(instructions, tools, effort)
                    session = Conversation(thread, tools, normalized)
                    self.sessions[thread] = session
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
                            await self.rpc.send({"id": event["id"], "result": {
                                "success": True, "contentItems": [{"type": "inputText", "text": text_content(message.get("content"))}],
                            }})
                    session.text = ""
                    result_message, finish = await asyncio.wait_for(self._answer(session), 1200)
                    session.expected = normalized + [normalized_message(result_message)]
                    session.touched = time.monotonic()
                    totals = self.rpc.usage.get(session.thread, {}).get("total", {})
                    usage = None
                    if totals:
                        usage = {"prompt_tokens": max(0, totals.get("inputTokens", 0) - before.get("inputTokens", 0)),
                                 "completion_tokens": max(0, totals.get("outputTokens", 0) - before.get("outputTokens", 0)),
                                 "prompt_tokens_details": {"cached_tokens": max(0, totals.get("cachedInputTokens", 0) - before.get("cachedInputTokens", 0))}}
                        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
                    response = {"id": request_id, "object": "chat.completion", "created": int(time.time()),
                                "model": MODEL, "provider": "codex-chatgpt-subscription",
                                "choices": [{"index": 0, "message": result_message, "finish_reason": finish}], "usage": usage}
                    self.record({"event": "response", "id": request_id, "thread": session.thread,
                                 "turn": session.turn, "finish_reason": finish, "usage": usage,
                                 "native_total_usage": totals, "seconds": time.monotonic() - started})
                    if finish == "stop" or isinstance(payload.get("tool_choice"), dict):
                        # Forced structured-output functions have no subsequent application tool result.
                        await self.release(session)
                    return response
        except BaseException as error:
            self.record({"event": "error", "id": request_id, "error_type": type(error).__name__, "detail": str(error)[:1000]})
            if session:
                await self.release(session)
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
                    calls.append({"id": call_id, "type": "function", "function": {
                        "name": item["tool"], "arguments": canonical(item["arguments"]),
                    }})
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
                "active_conversations": len(bridge.sessions), "transport": "codex-app-server"}

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
    parser.add_argument("--capacity", type=int, default=4)
    args = parser.parse_args()
    import uvicorn
    bridge = SubscriptionBridge(AppServer(args.workspace), args.artifacts, capacity=args.capacity)
    uvicorn.run(create_app(bridge, args.token_file.read_text().strip()), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
