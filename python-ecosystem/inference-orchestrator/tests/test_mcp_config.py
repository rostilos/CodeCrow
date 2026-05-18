"""
Unit tests for utils.mcp_config — MCPConfigBuilder.
"""
import os
import pytest
from unittest.mock import patch
from utils.mcp_config import MCPConfigBuilder


class TestBuildConfig:

    def test_basic_config(self):
        result = MCPConfigBuilder.build_config("/path/to/server.jar")
        assert "mcpServers" in result
        assert "codecrow-vcs-mcp" in result["mcpServers"]
        cfg = result["mcpServers"]["codecrow-vcs-mcp"]
        assert cfg["command"] == "java"
        assert "-jar" in cfg["args"]
        assert "/path/to/server.jar" in cfg["args"]

    def test_jvm_props(self):
        props = {"project.id": "123", "workspace": "myws"}
        result = MCPConfigBuilder.build_config("/server.jar", jvm_props=props)
        args = result["mcpServers"]["codecrow-vcs-mcp"]["args"]
        assert "-Dproject.id=123" in args
        assert "-Dworkspace=myws" in args

    def test_jvm_props_sanitize_newlines(self):
        props = {"key": "line1\nline2"}
        result = MCPConfigBuilder.build_config("/server.jar", jvm_props=props)
        args = result["mcpServers"]["codecrow-vcs-mcp"]["args"]
        assert any("line1 line2" in a for a in args)

    @patch.dict(os.environ, {"MCP_DEBUG_PORT": "5005"})
    def test_debug_port(self):
        result = MCPConfigBuilder.build_config("/server.jar")
        args = result["mcpServers"]["codecrow-vcs-mcp"]["args"]
        assert any("5005" in a for a in args)
        assert any("agentlib" in a for a in args)

    @patch("os.path.exists", return_value=True)
    def test_platform_mcp_included(self, mock_exists):
        result = MCPConfigBuilder.build_config(
            "/vcs.jar",
            include_platform_mcp=True,
            platform_mcp_jar_path="/platform.jar",
            platform_jvm_props={"key": "val"},
        )
        assert "codecrow-platform-mcp" in result["mcpServers"]
        pcfg = result["mcpServers"]["codecrow-platform-mcp"]
        assert "-jar" in pcfg["args"]
        assert "/platform.jar" in pcfg["args"]

    @patch("os.path.exists", return_value=False)
    def test_platform_mcp_not_included_if_jar_missing(self, mock_exists):
        result = MCPConfigBuilder.build_config(
            "/vcs.jar",
            include_platform_mcp=True,
            platform_mcp_jar_path="/missing.jar",
        )
        assert "codecrow-platform-mcp" not in result["mcpServers"]

    def test_no_platform_by_default(self):
        result = MCPConfigBuilder.build_config("/vcs.jar")
        assert "codecrow-platform-mcp" not in result["mcpServers"]


class TestBuildJvmProps:

    def test_basic_props(self):
        result = MCPConfigBuilder.build_jvm_props(
            project_id=1, pull_request_id=42,
            workspace="ws", repo_slug="repo",
        )
        assert result["project.id"] == "1"
        assert result["pullRequest.id"] == "42"
        assert result["workspace"] == "ws"
        assert result["repo.slug"] == "repo"

    def test_access_token(self):
        result = MCPConfigBuilder.build_jvm_props(
            project_id=1, pull_request_id=1,
            workspace="ws", repo_slug="r",
            access_token="tok123",
        )
        assert result["accessToken"] == "tok123"
        assert "oAuthClient" not in result

    def test_oauth(self):
        result = MCPConfigBuilder.build_jvm_props(
            project_id=1, pull_request_id=1,
            workspace="ws", repo_slug="r",
            oAuthClient="client", oAuthSecret="secret",
        )
        assert result["oAuthClient"] == "client"
        assert result["oAuthSecret"] == "secret"

    def test_max_allowed_tokens(self):
        result = MCPConfigBuilder.build_jvm_props(
            project_id=1, pull_request_id=1,
            workspace="ws", repo_slug="r",
            max_allowed_tokens=5000,
        )
        assert result["max.allowed.tokens"] == "5000"

    def test_vcs_provider(self):
        result = MCPConfigBuilder.build_jvm_props(
            project_id=1, pull_request_id=1,
            workspace="ws", repo_slug="r",
            vcs_provider="github",
        )
        assert result["vcs.provider"] == "github"

    def test_none_values_excluded(self):
        result = MCPConfigBuilder.build_jvm_props(
            project_id=None, pull_request_id=None,
            workspace=None, repo_slug=None,
        )
        assert "project.id" not in result
        assert "workspace" not in result
