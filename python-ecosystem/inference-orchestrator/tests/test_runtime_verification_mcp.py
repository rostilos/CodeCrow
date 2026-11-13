import importlib
import json
import os

import pytest


@pytest.mark.asyncio
async def test_real_mcp_schema_never_exposes_source_tenant_image_or_command(monkeypatch, tmp_path):
    monkeypatch.setenv("CODECROW_VERIFICATION_BASE_PATH", str(tmp_path))
    monkeypatch.setenv("CODECROW_VERIFICATION_CHECKS", json.dumps({
        "unit": {"image": "busybox:latest", "argv": ["true"], "description": "Run unit checks"},
    }))
    from service.review import runtime_verification_mcp as module
    module = importlib.reload(module)
    tools = await module.server.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "runVerificationCheck"
    properties = tools[0].inputSchema["properties"]
    assert set(properties) == {"checkName", "hypothesis"}
    assert properties["checkName"]["enum"] == ["unit"]
    result = await module.run_verification_check("not-configured", "test")
    assert result["status"] == "unavailable"
    assert module._runner._attempts == 0


@pytest.mark.asyncio
async def test_malformed_optional_runtime_keeps_valid_tool_schema_and_reports_diagnostic(monkeypatch):
    monkeypatch.delenv("CODECROW_VERIFICATION_BASE_PATH", raising=False)
    monkeypatch.setenv("CODECROW_VERIFICATION_CHECKS", "not-json")
    from service.review import runtime_verification_mcp as module
    module = importlib.reload(module)
    tools = await module.server.list_tools()
    assert "enum" not in tools[0].inputSchema["properties"]["checkName"]
    result = await module.run_verification_check("unit")
    assert result["status"] == "unavailable"
    assert "source snapshot" in result["diagnostic"]


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("CODECROW_TEST_CONTAINER_VERIFICATION") != "1", reason="requires explicit cached-image runtime")
async def test_mcp_tool_runs_bound_source_and_returns_execution_evidence(monkeypatch, tmp_path):
    (tmp_path / "evidence.txt").write_text("host-bound source\n")
    for key, value in {"WORKSPACE": "tenant", "PROJECT": "project", "BASE_PATH": str(tmp_path),
                       "BASE_REVISION": "same", "SOURCE_REVISION": "same", "OVERLAY_PATH": ""}.items():
        monkeypatch.setenv("CODECROW_VERIFICATION_" + key, value)
    monkeypatch.setenv("CODECROW_VERIFICATION_CHECKS", json.dumps({
        "read": {"image": os.environ.get("CODECROW_TEST_VERIFICATION_IMAGE", "busybox:latest"),
                 "argv": ["cat", "evidence.txt"]},
    }))
    from service.review import runtime_verification_mcp as module
    module = importlib.reload(module)
    result = await module.server.call_tool("runVerificationCheck", {"checkName": "read", "hypothesis": "observe fixture"})
    # FastMCP returns text blocks plus structured output for dict-returning tools.
    structured = result[1] if isinstance(result, tuple) else result
    assert structured["status"] == "passed", structured
    assert structured["output"] == "host-bound source\n"
    assert structured["binding"]["workspace"] == "tenant"
    assert structured["binding"]["sourceRevision"] == "same"
    assert structured["treeSha256"]
