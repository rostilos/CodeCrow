"""Minimal import stubs for running the VS-01 worker with system Python.

The production review pipeline imports provider SDKs while loading modules, even
when the caller injects an offline LLM and MCP client.  The hermetic VS-01 worker
uses these stubs only to satisfy those import-time references; every exercised
boundary is replaced with an explicit deterministic fake by the worker itself.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock


class _MockPackage(MagicMock):
    """A mock module that Python can also treat as a package."""

    def __init__(self, name: str = "", **kwargs):
        super().__init__(**kwargs)
        self.__name__ = name
        self.__package__ = name
        self.__path__ = []
        self.__spec__ = None


def _ensure_mock(dotted: str, attrs: dict | None = None) -> MagicMock:
    if dotted not in sys.modules:
        mock = _MockPackage(name=dotted)
        for key, value in (attrs or {}).items():
            setattr(mock, key, value)
        sys.modules[dotted] = mock
    return sys.modules[dotted]


def install_third_party_stubs() -> None:
    """Install provider-only modules absent from the system-Python harness."""

    _ensure_mock("langchain_core")
    _ensure_mock("langchain_core.globals", {"set_debug": lambda _enabled: None})
    _ensure_mock("langchain_core.utils")
    _ensure_mock(
        "langchain_core.utils.utils",
        {"secret_from_env": MagicMock(return_value=lambda: "offline-key")},
    )
    _ensure_mock("langchain_core.agents", {"AgentAction": MagicMock()})
    _ensure_mock("langchain_core.tools", {"tool": lambda function: function})
    _ensure_mock("langchain_core.messages")
    _ensure_mock("langchain_core.language_models")
    _ensure_mock("langchain_core.language_models.chat_models")
    _ensure_mock("langchain_core.runnables")
    _ensure_mock("langchain_core.callbacks")
    _ensure_mock("langchain_core.callbacks.manager")
    _ensure_mock("langchain_core.output_parsers")
    _ensure_mock("langchain_core.prompts")

    _ensure_mock("langchain")
    _ensure_mock(
        "langchain.agents",
        {
            "AgentExecutor": MagicMock(),
            "create_tool_calling_agent": MagicMock(),
        },
    )
    _ensure_mock("langchain_openai", {"ChatOpenAI": MagicMock()})
    _ensure_mock("langchain_anthropic", {"ChatAnthropic": MagicMock()})
    _ensure_mock(
        "langchain_google_genai",
        {"ChatGoogleGenerativeAI": MagicMock()},
    )

    google = _ensure_mock("google")
    google_oauth2 = _ensure_mock("google.oauth2")
    credentials = MagicMock()
    credentials.from_service_account_info = MagicMock(return_value=MagicMock())
    service_account = _ensure_mock(
        "google.oauth2.service_account",
        {"Credentials": credentials},
    )
    setattr(google, "oauth2", google_oauth2)
    setattr(google_oauth2, "service_account", service_account)

    for key in tuple(sys.modules):
        if key == "mcp_use" or key.startswith("mcp_use."):
            del sys.modules[key]
    _ensure_mock(
        "mcp_use",
        {
            "MCPClient": MagicMock(),
            "MCPAgent": MagicMock(),
        },
    )
    _ensure_mock("mcp_use.client")
    _ensure_mock(
        "mcp_use.logging",
        {
            "MCP_USE_DEBUG": False,
            "Logger": MagicMock(),
            "logger": MagicMock(),
        },
    )
    _ensure_mock("mcp_use.telemetry")
    _ensure_mock("mcp_use.telemetry.telemetry")
