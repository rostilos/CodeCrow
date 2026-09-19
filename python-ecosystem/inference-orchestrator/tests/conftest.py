"""
Pre-mock heavy third-party dependencies that are pulled in transitively
by the service/__init__.py re-export chain.

The import chain is:
  service/__init__ → service.review.__init__ → review_service.py
    → mcp_use → langchain_core.*
    → llm.llm_factory → langchain_openai, langchain_anthropic, langchain_google_genai
  service/__init__ → service.command.__init__ → command_service.py
    → mcp_use, langchain_core.agents

We mock these at the sys.modules level so that pure-logic modules
(json_utils, reconciliation, agents) can be imported
and tested without installing the entire LangChain / LLM stack.
"""

import sys
from typing import TypedDict
from unittest.mock import MagicMock

from pydantic import BaseModel


class _MockPackage(MagicMock):
    """A MagicMock that behaves like a package (has __path__)."""

    def __init__(self, name: str = "", **kwargs):
        super().__init__(**kwargs)
        self.__name__ = name
        self.__package__ = name
        self.__path__ = []  # Makes it look like a package
        self.__spec__ = None


def _ensure_mock(dotted: str, attrs: dict | None = None) -> MagicMock:
    """Register a mock module in sys.modules if not already present."""
    if dotted not in sys.modules:
        mock = _MockPackage(name=dotted)
        if attrs:
            for k, v in attrs.items():
                setattr(mock, k, v)
        sys.modules[dotted] = mock
    return sys.modules[dotted]


# ---------------------------------------------------------------------------
# langchain_core  — mock as a package so that any sub-import resolves
# ---------------------------------------------------------------------------
_ensure_mock("langchain_core")
_ensure_mock("langchain_core.globals", {"set_debug": lambda x: None})
_ensure_mock("langchain_core.utils")
_ensure_mock("langchain_core.utils.utils", {
    "secret_from_env": MagicMock(return_value=lambda: "mock-key"),
})
_ensure_mock("langchain_core.agents", {"AgentAction": MagicMock()})
_ensure_mock("langchain_core.tools", {"tool": lambda f: f})
_ensure_mock("langchain_core.messages")
_ensure_mock("langchain_core.language_models")


class _MockBaseChatModel(BaseModel):
    """Class-shaped runnable boundary for capture-wrapper unit tests."""


_ensure_mock("langchain_core.language_models.chat_models", {
    "BaseChatModel": _MockBaseChatModel,
})
_ensure_mock("langchain_core.runnables")
_ensure_mock("langchain_core.callbacks")
_ensure_mock("langchain_core.callbacks.manager")
_ensure_mock("langchain_core.output_parsers")
_ensure_mock("langchain_core.prompts")

# ---------------------------------------------------------------------------
# langchain (non-core) — needed for verification_agent lazy import
# ---------------------------------------------------------------------------
_ensure_mock("langchain")
_ensure_mock("langchain.agents", {
    "AgentExecutor": MagicMock(),
    "create_agent": MagicMock(),
    "create_tool_calling_agent": MagicMock(),
})


class _MockAgentMiddleware:
    """Minimal base class for shared-agent middleware unit tests."""


def _mock_hook_config(*, can_jump_to=None):
    def decorate(function):
        if can_jump_to is not None:
            function.__can_jump_to__ = can_jump_to
        return function

    return decorate


_ensure_mock("langchain.agents.middleware", {
    "AgentMiddleware": _MockAgentMiddleware,
    "ModelCallLimitMiddleware": MagicMock(),
    "hook_config": _mock_hook_config,
})


class _MockAgentState(TypedDict):
    """Subclassable stand-in for LangChain's middleware state schema."""


_ensure_mock("langchain.agents.middleware.types", {
    "AgentState": _MockAgentState,
    "ModelRequest": MagicMock(),
    "ModelResponse": MagicMock(),
    "PrivateStateAttr": object(),
})

_ensure_mock("langgraph")
_ensure_mock("langgraph.channels", {
    "UntrackedValue": object(),
})


class _MockToolStrategy:
    """Small class-shaped stand-in for ToolStrategy boundary behavior."""

    def __init__(
            self,
            schema,
            *,
            tool_message_content=None,
            handle_errors=True,
    ):
        self.schema = schema
        self.tool_message_content = tool_message_content
        self.handle_errors = handle_errors


_ensure_mock("langchain.agents.structured_output", {
    "ToolStrategy": _MockToolStrategy,
})

# ---------------------------------------------------------------------------
# langchain_openai / langchain_anthropic / langchain_google_genai
# ---------------------------------------------------------------------------
_ensure_mock("langchain_openai", {"ChatOpenAI": MagicMock()})
_ensure_mock("langchain_anthropic", {"ChatAnthropic": MagicMock()})
_ensure_mock("langchain_google_genai", {"ChatGoogleGenerativeAI": MagicMock()})

_google_mock = _ensure_mock("google")
_google_oauth2_mock = _ensure_mock("google.oauth2")
_credentials_cls = MagicMock()
_credentials_cls.from_service_account_info = MagicMock(return_value=MagicMock())
_service_account_mock = _ensure_mock("google.oauth2.service_account", {
    "Credentials": _credentials_cls,
})
setattr(_google_mock, "oauth2", _google_oauth2_mock)
setattr(_google_oauth2_mock, "service_account", _service_account_mock)

# ---------------------------------------------------------------------------
# mcp_use — mock before the real package loads so we avoid its
# transitive langchain_core imports.  Must mock sub-modules too.
# ---------------------------------------------------------------------------
# Remove the real mcp_use from sys.modules if already imported partially
for key in list(sys.modules.keys()):
    if key == "mcp_use" or key.startswith("mcp_use."):
        del sys.modules[key]


class _MockMCPAgent:
    """Class-shaped stand-in so local MCPAgent subclasses remain testable."""


class _MockLangChainAdapter:
    """Convert test connector tools without importing the LangChain adapter."""

    def __init__(self):
        self._record_telemetry = False

    async def load_tools_for_connector(self, connector):
        return await connector.list_tools()


_mcp_mock = _ensure_mock("mcp_use", {
    "MCPClient": MagicMock(),
    "MCPAgent": _MockMCPAgent,
})
_ensure_mock("mcp_use.client")
_ensure_mock("mcp_use.agents")
_ensure_mock("mcp_use.agents.adapters", {
    "LangChainAdapter": _MockLangChainAdapter,
})
_ensure_mock("mcp_use.agents.middleware", {
    "tool_error_handler": MagicMock(),
})
_ensure_mock("mcp_use.logging", {
    "MCP_USE_DEBUG": False,
    "Logger": MagicMock(),
    "logger": MagicMock(),
})
_ensure_mock("mcp_use.telemetry")
_ensure_mock("mcp_use.telemetry.telemetry")
