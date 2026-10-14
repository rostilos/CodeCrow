"""Managed ChatGPT authentication and native Codex app-server conversations."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

MODEL = os.environ.get("CODECROW_CODEX_BENCHMARK_MODEL", "gpt-6-luna")
CONFIG = {
    "model_provider": "openai",
    "forced_login_method": "chatgpt",
    "service_tier": "default",
    "features.apps": False,
    "features.multi_agent": False,
    "agents.enabled": False,
    "features.shell_tool": False,
    "features.code_mode.enabled": False,
    "features.code_mode.direct_only_tool_namespaces": ["functions"],
    "features.code_mode_host": True,
    "features.skill_search": False,
    "features.skip_host_skill_discovery": True,
    "web_search": "disabled",
    "project_doc_max_bytes": 0,
}


class AppServer:
    def __init__(self, workspace: Path, *, executable: str = "codex"):
        self.workspace = workspace
        self.executable = executable
        self.pending: dict[int, asyncio.Future] = {}
        self.events: dict[str, asyncio.Queue] = {}
        self.usage: dict[str, dict] = {}
        self.usage_callback = None
        self.response_completed_callback = None
        self.terminal_callback = None
        self.terminals: dict[str, dict] = {}
        self.raw_calls: dict[tuple[str, str], set[str]] = {}
        self.call_responses: dict[tuple[str, str, str], str] = {}
        self.completed_responses: set[tuple[str, str, str]] = set()
        self.response_changes: dict[str, asyncio.Event] = {}
        self.reader_failure: Exception | None = None
        self.counter = 0
        self.process = None
        self.reader = None
        self.account: dict = {}
        self.model: dict = {}
        self._stderr = None

    async def start(self):
        self.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        env = {k: v for k, v in os.environ.items() if k not in {
            "OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "OPENROUTER_API_KEY",
            "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "MARTIAN_API_KEY",
            "CODECROW_AI_API_KEY",
        }}
        args = [self.executable]
        for key, value in CONFIG.items():
            args += ["-c", f"{key}={json.dumps(value)}"]
        args += ["app-server", "--listen", "stdio://"]
        self._stderr = (self.workspace / "app-server.log").open("a")
        self.process = await asyncio.create_subprocess_exec(
            *args, cwd=self.workspace, env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=self._stderr, limit=64 * 1024 * 1024,
        )
        self.reader = asyncio.create_task(self._read())
        await self.request("initialize", {
            "clientInfo": {"name": "codecrow_benchmark", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True},
        })
        await self.send({"method": "initialized", "params": {}})
        state = await self.request("account/read", {"refreshToken": False})
        account = state.get("account") or {}
        if account.get("type") != "chatgpt":
            raise RuntimeError("Benchmark requires the existing ChatGPT subscription login")
        self.account = {"type": account["type"], "planType": account.get("planType")}
        models = await self.request("model/list", {"includeHidden": True, "limit": 100})
        self.model = next((m for m in models.get("data", []) if m.get("model") == MODEL), {})
        if not self.model:
            raise RuntimeError(f"Requested subscription model is unavailable: {MODEL}")
        return self

    async def send(self, message: dict):
        if self.process is None or self.process.returncode is not None:
            raise RuntimeError("Codex app-server is not running")
        self.process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
        await self.process.stdin.drain()

    async def request(self, method: str, params: dict, *, timeout: float = 60):
        self.counter += 1
        request_id = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(request_id, None)

    async def _read(self):
        failure = RuntimeError("Codex app-server closed its output")
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if "method" not in message and "id" in message:
                    future = self.pending.get(message["id"])
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(RuntimeError(json.dumps(message["error"])))
                        else:
                            future.set_result(message.get("result"))
                    continue
                params = message.get("params") or {}
                thread = params.get("threadId") or (params.get("thread") or {}).get("id")
                self._track_response(message, thread)
                if message.get("method") == "rawResponse/completed" and thread:
                    if self.response_completed_callback is not None:
                        self.response_completed_callback(
                            thread, params.get("turnId"), params.get("responseId"),
                            params.get("usage"), params.get("usageMetadata"),
                        )
                if message.get("method") == "thread/tokenUsage/updated":
                    self.usage[thread] = params.get("tokenUsage") or {}
                    if self.usage_callback is not None:
                        self.usage_callback(thread, params.get("turnId"), self.usage[thread])
                if message.get("method") == "turn/completed" and thread:
                    turn = params.get("turn") or {}
                    status = {"turn": turn.get("id"), "status": turn.get("status")}
                    self.terminals[thread] = status
                    if self.terminal_callback is not None:
                        self.terminal_callback(thread, turn.get("id"), turn.get("status"))
                if thread:
                    await self.events.setdefault(thread, asyncio.Queue()).put(message)
                elif "id" in message:
                    await self.send({"id": message["id"], "error": {
                        "code": -32601, "message": "Unsupported benchmark client request",
                    }})
        except Exception as error:
            failure = error
        finally:
            self.reader_failure = failure
            for changed in self.response_changes.values():
                changed.set()
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(failure)
            for queue in self.events.values():
                await queue.put({"method": "bridge/closed", "params": {"error": str(failure)}})

    def _track_response(self, message, thread):
        params = message.get("params") or {}
        turn = params.get("turnId")
        if not thread or not turn:
            return
        boundary = (thread, turn)
        if message.get("method") == "rawResponseItem/completed":
            item = params.get("item") or {}
            call = item.get("call_id")
            if item.get("type") in {"function_call", "custom_tool_call"} and isinstance(call, str) and call:
                if (*boundary, call) not in self.call_responses:
                    self.raw_calls.setdefault(boundary, set()).add(call)
        elif message.get("method") == "rawResponse/completed":
            response = params.get("responseId")
            if not isinstance(response, str) or not response or (*boundary, response) in self.completed_responses:
                return
            # Raw items have no response ID. The native ordered stream closes
            # these exact calls at this response boundary, including null usage.
            self.completed_responses.add((*boundary, response))
            for call in self.raw_calls.pop(boundary, set()):
                self.call_responses[(*boundary, call)] = response
            self.response_changes.setdefault(thread, asyncio.Event()).set()

    def completed_call_responses(self, thread, turn, calls):
        matched = {call: self.call_responses.get((thread, turn, call)) for call in calls}
        return matched if matched and all(matched.values()) else {}

    async def wait_call_responses(self, thread, turn, calls):
        changed = self.response_changes.setdefault(thread, asyncio.Event())
        while True:
            matched = self.completed_call_responses(thread, turn, calls)
            if matched:
                return matched
            if self.reader_failure is not None:
                raise RuntimeError("Native response stream closed before matching completion") from self.reader_failure
            changed.clear()
            await changed.wait()

    def forget_response_tracking(self, thread):
        for mapping in (self.raw_calls, self.call_responses):
            for key in list(mapping):
                if key[0] == thread:
                    del mapping[key]
        self.completed_responses.difference_update(key for key in list(self.completed_responses) if key[0] == thread)
        self.response_changes.pop(thread, None)

    async def start_thread(self, instructions: str, tools: list[dict], effort: str):
        supported = {e["reasoningEffort"] for e in self.model["supportedReasoningEfforts"]}
        if effort not in supported:
            raise ValueError(f"Unsupported {MODEL} reasoning effort: {effort}")
        response = await self.request("thread/start", {
            "model": MODEL, "modelProvider": "openai", "allowProviderModelFallback": False,
            "cwd": str(self.workspace), "approvalPolicy": "never", "sandbox": "read-only",
            "ephemeral": True, "experimentalRawEvents": True,
            "personality": "none", "baseInstructions": instructions,
            "developerInstructions": "Use only the supplied benchmark task and dynamic tools. Do not access local files, run commands, browse, or delegate. Return the task's requested output without progress commentary.",
            "dynamicTools": tools, "serviceTier": "default",
            "config": {"model_reasoning_effort": effort},
        })
        if response["model"] != MODEL or response["modelProvider"] != "openai":
            raise RuntimeError("Codex selected a different model or provider")
        if response.get("instructionSources"):
            raise RuntimeError("Unexpected repository instructions in benchmark model session")
        thread = response["thread"]["id"]
        self.events.setdefault(thread, asyncio.Queue())
        return thread

    async def interrupt(self, thread: str, turn: str | None):
        if turn:
            try:
                await self.request("turn/interrupt", {"threadId": thread, "turnId": turn}, timeout=10)
            except (RuntimeError, TimeoutError):
                return False
        return True

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.stdin.close()
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 10)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.reader:
            await asyncio.gather(self.reader, return_exceptions=True)
        if self._stderr:
            self._stderr.close()
