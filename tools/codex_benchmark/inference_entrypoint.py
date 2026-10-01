"""Explicit benchmark entrypoint; the ordinary service entrypoint is unchanged."""
from __future__ import annotations

import inspect
import os
from pathlib import Path
import runpy
import sys


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
