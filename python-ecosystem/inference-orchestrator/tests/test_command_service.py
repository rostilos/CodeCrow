"""
Tests for CommandService helper methods.

Covers: _build_summarize_prompt, _build_ask_prompt, _build_jvm_props_for_*,
        _build_platform_jvm_props, _parse_json_response, _extract_json_object,
        _extract_summary_field_fallback, _emit_event, _create_mcp_client
"""
import pytest
import json
from unittest.mock import MagicMock, patch

from service.command.command_service import CommandService


@pytest.fixture
def service():
    with patch.dict("os.environ", {
        "MCP_SERVER_JAR": "/tmp/test.jar",
        "COMMAND_TIMEOUT_SECONDS": "60",
    }):
        with patch("service.command.command_service.RagClient"):
            svc = CommandService()
    return svc


# ── _emit_event ──────────────────────────────────────────────────

class TestEmitEvent:
    def test_calls_callback(self, service):
        cb = MagicMock()
        CommandService._emit_event(cb, {"type": "test"})
        cb.assert_called_once_with({"type": "test"})

    def test_none_callback(self, service):
        CommandService._emit_event(None, {"type": "test"})  # Should not raise

    def test_callback_exception_swallowed(self, service):
        cb = MagicMock(side_effect=RuntimeError("boom"))
        CommandService._emit_event(cb, {"type": "test"})  # Should not raise


# ── _build_jvm_props_for_summarize ───────────────────────────────

class TestBuildJvmPropsForSummarize:
    def test_returns_dict(self, service):
        request = MagicMock(
            projectId=1,
            pullRequestId=42,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            oAuthClient="client_id",
            oAuthSecret="secret",
            accessToken=None,
            maxAllowedTokens=100000,
            vcsProvider="bitbucket",
        )
        result = service._build_jvm_props_for_summarize(request)
        assert isinstance(result, dict)


# ── _build_jvm_props_for_ask ─────────────────────────────────────

class TestBuildJvmPropsForAsk:
    def test_returns_dict(self, service):
        request = MagicMock(
            projectId=1,
            pullRequestId=42,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            oAuthClient="oc",
            oAuthSecret="os",
            accessToken=None,
            maxAllowedTokens=50000,
            vcsProvider="github",
        )
        result = service._build_jvm_props_for_ask(request)
        assert isinstance(result, dict)


# ── _build_platform_jvm_props ────────────────────────────────────

class TestBuildPlatformJvmProps:
    def test_basic_props(self, service):
        request = MagicMock(
            projectId=5,
            pullRequestId=10,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            accessToken="tok",
            oAuthClient=None,
            oAuthSecret=None,
            vcsProvider="github",
        )
        result = service._build_platform_jvm_props(request)
        assert "api.base.url" in result
        assert result["project.id"] == "5"

    def test_with_oauth(self, service):
        request = MagicMock(
            projectId=5,
            pullRequestId=None,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            accessToken=None,
            oAuthClient="client",
            oAuthSecret="secret",
            vcsProvider="bitbucket",
        )
        result = service._build_platform_jvm_props(request)
        assert result.get("oAuthClient") == "client"


# ── _build_summarize_prompt ──────────────────────────────────────

class TestBuildSummarizePrompt:
    def test_basic_prompt(self, service):
        request = MagicMock(
            pullRequestId=42,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            supportsMermaid=False,
            sourceBranch="feature/abc",
            targetBranch="main",
        )
        result = service._build_summarize_prompt(request, None)
        assert "PR #42" in result or "#42" in result
        assert "ws" in result
        assert "repo" in result
        assert "ASCII" in result

    def test_with_rag_context_list(self, service):
        request = MagicMock(
            pullRequestId=1,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            supportsMermaid=True,
            sourceBranch="feat",
            targetBranch="main",
        )
        rag_ctx = [{"text": "some code context"}]
        result = service._build_summarize_prompt(request, rag_ctx)
        assert "RELEVANT CODEBASE CONTEXT" in result
        assert "some code context" in result

    def test_with_rag_context_dict(self, service):
        request = MagicMock(
            pullRequestId=1,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            supportsMermaid=False,
            sourceBranch="feat",
            targetBranch="main",
        )
        rag_ctx = {"relevant_code": [{"text": "code here", "metadata": {"path": "a.py"}}]}
        result = service._build_summarize_prompt(request, rag_ctx)
        assert "code here" in result


# ── _build_ask_prompt ────────────────────────────────────────────

class TestBuildAskPrompt:
    def test_basic_prompt(self, service):
        request = MagicMock(
            question="What does this PR do?",
            pullRequestId=10,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            analysisContext=None,
            issueReferences=None,
        )
        result = service._build_ask_prompt(request, None)
        assert "What does this PR do?" in result
        assert "ws" in result

    def test_with_analysis_context(self, service):
        request = MagicMock(
            question="Q?",
            pullRequestId=10,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            analysisContext="This PR fixes a bug",
            issueReferences=None,
        )
        result = service._build_ask_prompt(request, None)
        assert "ANALYSIS CONTEXT" in result
        assert "This PR fixes a bug" in result

    def test_with_issue_references_and_platform(self, service):
        request = MagicMock(
            question="Tell me about issue 312",
            pullRequestId=10,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            analysisContext=None,
            issueReferences=["312", "313"],
        )
        result = service._build_ask_prompt(request, None, has_platform_mcp=True)
        assert "#312" in result
        assert "getIssueDetails" in result

    def test_with_rag_context_list(self, service):
        request = MagicMock(
            question="Q?",
            pullRequestId=None,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            analysisContext=None,
            issueReferences=None,
        )
        rag = [{"text": "relevant code", "path": "file.py"}]
        result = service._build_ask_prompt(request, rag)
        assert "relevant code" in result

    def test_no_pr_context(self, service):
        request = MagicMock(
            question="Q?",
            pullRequestId=None,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            analysisContext=None,
            issueReferences=None,
        )
        result = service._build_ask_prompt(request, None)
        assert "Q?" in result


# ── _parse_json_response ─────────────────────────────────────────

class TestParseJsonResponse:
    def test_direct_json(self, service):
        data = {"summary": "test", "diagram": "", "diagramType": "ASCII"}
        result = service._parse_json_response(json.dumps(data))
        assert result["summary"] == "test"

    def test_json_in_code_block(self, service):
        text = '```json\n{"answer": "hello"}\n```'
        result = service._parse_json_response(text)
        assert result["answer"] == "hello"

    def test_json_with_surrounding_text(self, service):
        text = 'Here is the result: {"answer": "test"} done.'
        result = service._parse_json_response(text)
        assert result["answer"] == "test"

    def test_empty_response(self, service):
        assert service._parse_json_response("") is None
        assert service._parse_json_response(None) is None

    def test_invalid_json(self, service):
        assert service._parse_json_response("not json at all") is None

    def test_plain_code_block(self, service):
        text = '```\n{"key": "value"}\n```'
        result = service._parse_json_response(text)
        assert result["key"] == "value"


# ── _extract_json_object ─────────────────────────────────────────

class TestExtractJsonObject:
    def test_simple(self, service):
        text = 'prefix {"a": 1} suffix'
        result = service._extract_json_object(text)
        assert result == '{"a": 1}'

    def test_nested(self, service):
        text = '{"a": {"b": 1}}'
        result = service._extract_json_object(text)
        assert result == '{"a": {"b": 1}}'

    def test_no_json(self, service):
        assert service._extract_json_object("no braces here") is None

    def test_with_string_braces(self, service):
        text = '{"a": "value with {inner} braces"}'
        result = service._extract_json_object(text)
        assert result is not None

    def test_unbalanced_braces(self, service):
        text = '{"a": 1'
        result = service._extract_json_object(text)
        assert result is None


# ── _extract_summary_field_fallback ──────────────────────────────

class TestExtractSummaryFieldFallback:
    def test_extracts_summary(self, service):
        text = '{"summary": "This is the summary", "other": "data"}'
        result = service._extract_summary_field_fallback(text)
        assert result == "This is the summary"

    def test_with_escaped_quotes(self, service):
        text = '{"summary": "He said \\"hello\\"", "x": 1}'
        result = service._extract_summary_field_fallback(text)
        assert 'hello' in result

    def test_empty_text(self, service):
        assert service._extract_summary_field_fallback("") is None
        assert service._extract_summary_field_fallback(None) is None

    def test_no_summary_field(self, service):
        text = '{"answer": "something"}'
        result = service._extract_summary_field_fallback(text)
        assert result is None

    def test_with_newlines(self, service):
        text = '{"summary": "line1\\nline2"}'
        result = service._extract_summary_field_fallback(text)
        assert "line1" in result


# ── _create_mcp_client ───────────────────────────────────────────

class TestCreateMcpClient:
    def test_creates_client(self, service):
        with patch("service.command.command_service.MCPClient") as mock_cls:
            mock_cls.from_dict.return_value = MagicMock()
            client = service._create_mcp_client({"servers": {}})
            mock_cls.from_dict.assert_called_once()

    def test_raises_on_failure(self, service):
        with patch("service.command.command_service.MCPClient") as mock_cls:
            mock_cls.from_dict.side_effect = Exception("fail")
            with pytest.raises(Exception, match="Failed to construct"):
                service._create_mcp_client({})


# ── _create_llm ──────────────────────────────────────────────────

class TestCreateLlm:
    def test_creates_llm(self, service):
        request = MagicMock(
            aiModel="gpt-4",
            aiProvider="openai",
            aiApiKey="key",
            aiBaseUrl=None,
        )
        with patch("service.command.command_service.LLMFactory") as mock_factory:
            mock_factory.create_llm.return_value = MagicMock()
            llm = service._create_llm(request)
            mock_factory.create_llm.assert_called_once()

    def test_raises_on_failure(self, service):
        request = MagicMock(
            aiModel="gpt-4",
            aiProvider="openai",
            aiApiKey="key",
            aiBaseUrl=None,
        )
        with patch("service.command.command_service.LLMFactory") as mock_factory:
            mock_factory.create_llm.side_effect = Exception("bad")
            with pytest.raises(Exception, match="Failed to create LLM"):
                service._create_llm(request)
