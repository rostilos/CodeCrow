from unittest.mock import MagicMock, patch

import pytest

from utils.mcp_pool import McpProcessPool


@pytest.mark.asyncio(loop_scope="function")
async def test_pool_keeps_credentials_out_of_loggable_process_args():
    process = MagicMock(pid=321)
    process.poll.return_value = None
    pool = McpProcessPool("/server.jar", pool_size=1)

    with (
        patch("utils.mcp_pool.subprocess.Popen", return_value=process) as popen,
        patch("utils.mcp_pool.asyncio.sleep"),
    ):
        await pool._create_process({
            "workspace": "tenant-workspace",
            "accessToken": "pooled-token-sentinel",
        })

    command = popen.call_args.args[0]
    child_env = popen.call_args.kwargs["env"]
    assert "-Dworkspace=tenant-workspace" in command
    assert "pooled-token-sentinel" not in " ".join(command)
    assert child_env["CODECROW_MCP_ACCESS_TOKEN"] == "pooled-token-sentinel"
