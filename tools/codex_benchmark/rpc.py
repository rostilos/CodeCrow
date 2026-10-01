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
    "features.code_mode": False,
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
                if message.get("method") == "thread/tokenUsage/updated":
                    self.usage[thread] = params.get("tokenUsage") or {}
                if thread:
                    await self.events.setdefault(thread, asyncio.Queue()).put(message)
                elif "id" in message:
                    await self.send({"id": message["id"], "error": {
                        "code": -32601, "message": "Unsupported benchmark client request",
                    }})
        except Exception as error:
            failure = error
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(failure)
            for queue in self.events.values():
                await queue.put({"method": "bridge/closed", "params": {"error": str(failure)}})

    async def start_thread(self, instructions: str, tools: list[dict], effort: str):
        supported = {e["reasoningEffort"] for e in self.model["supportedReasoningEfforts"]}
        if effort not in supported:
            raise ValueError(f"Unsupported {MODEL} reasoning effort: {effort}")
        response = await self.request("thread/start", {
            "model": MODEL, "modelProvider": "openai", "allowProviderModelFallback": False,
            "cwd": str(self.workspace), "approvalPolicy": "never", "sandbox": "read-only",
            "ephemeral": True, "personality": "none", "baseInstructions": instructions,
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
                pass

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
