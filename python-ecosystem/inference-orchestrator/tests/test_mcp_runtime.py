import os

from utils.mcp_runtime import configure_mcp_runtime


def test_mcp_use_telemetry_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MCP_USE_ANONYMIZED_TELEMETRY", raising=False)

    configure_mcp_runtime()

    assert os.environ["MCP_USE_ANONYMIZED_TELEMETRY"] == "false"


def test_explicit_mcp_use_telemetry_choice_is_preserved(monkeypatch):
    monkeypatch.setenv("MCP_USE_ANONYMIZED_TELEMETRY", "true")

    configure_mcp_runtime()

    assert os.environ["MCP_USE_ANONYMIZED_TELEMETRY"] == "true"
