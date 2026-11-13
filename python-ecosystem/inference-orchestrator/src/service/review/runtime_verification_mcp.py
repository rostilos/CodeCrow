"""Request-scoped executable evidence tools; identity is host-only environment."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from service.review.runtime_verification import (
    RuntimeVerification, VerificationBinding, VerificationConfig, VerificationUnavailable,
)


def _value(name: str) -> str:
    return os.environ.get(f"CODECROW_VERIFICATION_{name}", "").strip()


def _create_runner() -> tuple[RuntimeVerification | None, str | None]:
    try:
        # Missing source paths must not resolve to the MCP process's cwd.
        if not _value("BASE_PATH"):
            raise VerificationUnavailable("Host-bound source snapshot is unavailable")
        config = VerificationConfig.from_environment()
        binding = VerificationBinding(
            workspace=_value("WORKSPACE"), project=_value("PROJECT"),
            base_revision=_value("BASE_REVISION"), source_revision=_value("SOURCE_REVISION"),
            base_path=Path(_value("BASE_PATH")),
            overlay_path=Path(_value("OVERLAY_PATH")) if _value("OVERLAY_PATH") else None,
        )
        return RuntimeVerification(binding, config), None
    except VerificationUnavailable as exc:
        return None, str(exc)


_runner, _unavailable = _create_runner()
_catalog = _runner.catalog() if _runner else []
CheckName = Annotated[str, Field(json_schema_extra=(
    {"enum": [c["name"] for c in _catalog]} if _catalog else {}
))]
server = FastMCP(
    "CodeCrow executable verification",
    instructions=(
        "Run a configured check only when its outcome would help evaluate a concrete "
        "defect hypothesis. Source, revisions, tenant and execution settings are "
        "host-bound. No host paths, commands or images can be supplied. A failed "
        "check is evidence to inspect, not automatically a new defect; a passing "
        "check only covers that check. Unavailable/timeout evidence must not "
        "discard a source-grounded finding."
    ),
    log_level="WARNING",
)


@server.tool(
    name="runVerificationCheck",
    description=(
        "Execute one host-configured check against the exact proposed tree in an "
        "isolated container without network or host mounts. Results include the "
        "source revisions, tree digest, exit status, bounded output and diagnostics. "
        "Each request permits at most two distinct checks; repeated checks reuse "
        "the recorded outcome. Available checks: "
        + "; ".join(f"{c['name']}: {c['description']}" for c in _catalog)
    ),
    structured_output=True,
)
async def run_verification_check(checkName: CheckName, hypothesis: str = "") -> dict[str, Any]:
    if _runner is None:
        return {"status": "unavailable", "diagnostic": _unavailable,
                "evidenceKind": "runtime_check"}
    return await _runner.run(checkName, hypothesis)


if __name__ == "__main__":
    server.run("stdio")
