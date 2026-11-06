"""Process-wide defaults that must apply before importing mcp-use."""

import os


def configure_mcp_runtime() -> None:
    """Disable third-party telemetry unless an operator explicitly opts in."""
    os.environ.setdefault("MCP_USE_ANONYMIZED_TELEMETRY", "false")


configure_mcp_runtime()
