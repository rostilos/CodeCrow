"""Explicit benchmark entrypoint; the ordinary service entrypoint is unchanged."""
from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from contextvars import ContextVar
import logging
import os
from pathlib import Path
import runpy
import sys
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)
_OWNER = ContextVar("codecrow_subscription_owner", default=None)


@asynccontextmanager
async def subscription_execution_scope(bridge_url, bridge_token):
    owner = uuid4().hex
    binding = (bridge_url, owner)
    token = _OWNER.set(binding)
    try:
        yield
    finally:
        try:
            _OWNER.reset(token)
        except ValueError:
            # An async generator may be closed by its consumer's context.
            if _OWNER.get() == binding:
                _OWNER.set(None)
            logger.debug("Subscription owner scope closed from another context")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    bridge_url.rstrip("/") + "/conversations/release",
                    headers={"Authorization": "Bearer " + bridge_token},
                    json={"owner": owner},
                )
                response.raise_for_status()
                if response.status_code == 202:
                    logger.warning("Subscription conversation cleanup pending; bridge reaper remains active")
        except Exception:
            logger.warning("Subscription conversation cleanup unavailable; bridge reaper remains active", exc_info=True)


def install_subscription_guard(factory, bridge_url: str, bridge_token: str, model_name: str = "gpt-6-luna"):
    original = factory.create_llm
    signature = inspect.signature(original)

    def create_llm(*args, **kwargs):
        arguments = signature.bind(*args, **kwargs)
        arguments.apply_defaults()
        values = arguments.arguments
        provider = str(values.get("ai_provider", "")).lower().replace("-", "_")
        if (
            provider != "openai_compatible"
            or values.get("ai_model") != model_name
            or str(values.get("ai_base_url", "")).rstrip("/") != bridge_url.rstrip("/")
            or values.get("ai_api_key") != bridge_token
        ):
            raise ValueError(f"This benchmark permits only the local Codex subscription bridge with {model_name}")
        model = original(*args, **kwargs)
        # LangChain auto-selects Responses for GPT-6. This local facade implements
        # Chat Completions; app-server remains the actual subscription transport.
        if hasattr(model, "use_responses_api"):
            model.use_responses_api = False
        # Direct with_structured_output() bindings expose their intent here;
        # ordinary forced tool calls have no structured-output metadata.
        model_type = type(model)
        bind_tools = getattr(model_type, "bind_tools", None)
        if callable(bind_tools):
            def subscription_bind_tools(self, tools, **options):
                structured = options.get("ls_structured_output_format") or options.get("structured_output_format")
                choice = options.get("tool_choice")
                name = choice.get("function", {}).get("name") if isinstance(choice, dict) else choice
                if structured and isinstance(name, str) and name not in {"auto", "none", "any", "required"}:
                    options["extra_body"] = {
                        **options.get("extra_body", {}),
                        "codecrow_terminal_tools": [name],
                    }
                return bind_tools(self, tools, **options)
            # Use a class method so provider model_copy() retains updated token
            # settings instead of calling a closure bound to the original model.
            def execution_scope(self):
                return subscription_execution_scope(bridge_url, bridge_token)

            overrides = {"bind_tools": subscription_bind_tools,
                         "provider_execution_scope": execution_scope, "__module__": __name__}
            payload_builder = getattr(model_type, "_get_request_payload", None)
            if callable(payload_builder):
                def request_payload(self, *args, **kwargs):
                    payload = payload_builder(self, *args, **kwargs)
                    owner = _OWNER.get()
                    if owner is not None and owner[0] == bridge_url:
                        payload["extra_body"] = {**(payload.get("extra_body") or {}), "codecrow_owner": owner[1]}
                    return payload
                overrides["_get_request_payload"] = request_payload
            subscription_type = type(f"Subscription{model_type.__name__}", (model_type,), overrides)
            object.__setattr__(model, "__class__", subscription_type)
        return model

    factory.create_llm = staticmethod(create_llm)


def main():
    if os.environ.get("CODECROW_CODEX_BENCHMARK") != "1":
        raise SystemExit("This entrypoint requires explicit CODECROW_CODEX_BENCHMARK=1")
    bridge_url = os.environ["CODECROW_SUBSCRIPTION_BRIDGE_URL"]
    bridge_token = os.environ["CODECROW_SUBSCRIPTION_BRIDGE_TOKEN"]
    if not bridge_token:
        raise SystemExit("Benchmark bridge token is empty")
    application = Path(os.environ.get("CODECROW_BENCHMARK_APP", "/app"))
    sys.path.insert(0, str(application))
    from dotenv import load_dotenv
    load_dotenv(application / ".env", interpolate=False)
    for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        os.environ.pop(name, None)
    from llm.llm_factory import LLMFactory
    install_subscription_guard(LLMFactory, bridge_url, bridge_token, os.environ.get("CODECROW_CODEX_BENCHMARK_MODEL", "gpt-6-luna"))
    sys.argv = [str(application / "main.py")]
    runpy.run_path(sys.argv[0], run_name="__main__")


if __name__ == "__main__":
    main()
