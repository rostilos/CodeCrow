"""Select the review workflow independently of graph availability."""
import os
from typing import Any


def review_execution_mode(request: Any) -> tuple[str, list[str]]:
    diagnostics = []
    requested = getattr(request, "reviewExecutionMode", None)
    configured = os.environ.get("REVIEW_EXECUTION_MODE", "pipeline")
    for origin, value in (("request", requested), ("configuration", configured)):
        if value is None or value == "":
            continue
        if isinstance(value, str) and value.strip().lower() in {"pipeline", "mcp_only"}:
            return value.strip().lower(), diagnostics
        diagnostics.append(f"Unknown {origin} review execution mode; using the next available default.")
    return "pipeline", diagnostics
