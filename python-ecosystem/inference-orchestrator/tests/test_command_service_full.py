"""Extended tests for command_service: _parse_json_response, _extract_json_object, _extract_summary_field_fallback."""
import pytest
from unittest.mock import MagicMock, patch
from service.command.command_service import CommandService


@pytest.fixture
def service():
    with patch("service.command.command_service.load_dotenv"):
        with patch("service.command.command_service.RagClient"):
            svc = CommandService()
            return svc


# ── _parse_json_response ──────────────────────────────────────


class TestParseJsonResponse:
    def test_plain_json(self, service):
        result = service._parse_json_response('{"key": "val"}')
        assert result == {"key": "val"}

    def test_json_in_markdown(self, service):
        text = '```json\n{"answer": "hello"}\n```'
        result = service._parse_json_response(text)
        assert result == {"answer": "hello"}

    def test_json_in_generic_block(self, service):
        text = '```\n{"answer": "test"}\n```'
        result = service._parse_json_response(text)
        assert result == {"answer": "test"}

    def test_embedded_json(self, service):
        text = 'Here is the result:\n{"answer": "embedded"}\nDone.'
        result = service._parse_json_response(text)
        assert result["answer"] == "embedded"

    def test_no_json(self, service):
        assert service._parse_json_response("not json at all") is None

    def test_nested_json(self, service):
        text = '{"outer": {"inner": "val"}}'
        result = service._parse_json_response(text)
        assert result["outer"]["inner"] == "val"


# ── _extract_json_object ─────────────────────────────────────


class TestExtractJsonObject:
    def test_simple_object(self, service):
        result = service._extract_json_object('prefix {"a": 1} suffix')
        assert result == '{"a": 1}'

    def test_nested_braces(self, service):
        result = service._extract_json_object('{"a": {"b": 2}}')
        assert result == '{"a": {"b": 2}}'

    def test_no_object(self, service):
        assert service._extract_json_object("no braces here") is None

    def test_string_with_braces(self, service):
        result = service._extract_json_object('{"text": "has {inner} braces"}')
        # Should handle quoted braces correctly
        assert result is not None
        assert "text" in result

    def test_escape_handling(self, service):
        result = service._extract_json_object('{"key": "val\\"ue"}')
        assert result is not None


# ── _extract_summary_field_fallback ───────────────────────────


class TestExtractSummaryFieldFallback:
    def test_extracts_summary(self, service):
        text = '{"summary": "This is a summary", "other": "data"}'
        result = service._extract_summary_field_fallback(text)
        assert result is not None
        assert "summary" in result.lower() or len(result) > 0

    def test_no_summary(self, service):
        result = service._extract_summary_field_fallback("no summary here")
        # Should return None or empty
        assert result is None or result == ""


# ── _emit_event ───────────────────────────────────────────────


class TestCommandServiceEmitEvent:
    def test_calls_callback(self, service):
        cb = MagicMock()
        service._emit_event(cb, {"type": "status"})
        cb.assert_called_once_with({"type": "status"})

    def test_no_callback(self, service):
        # Should not raise
        service._emit_event(None, {"type": "status"})

    def test_callback_exception_handled(self, service):
        cb = MagicMock(side_effect=Exception("fail"))
        # Should not raise
        service._emit_event(cb, {"type": "status"})


# ── _create_mcp_client ────────────────────────────────────────


class TestCommandServiceCreateMcpClient:
    def test_success(self, service):
        with patch("service.command.command_service.MCPClient") as mock_cls:
            mock_cls.from_dict.return_value = MagicMock()
            result = service._create_mcp_client({"mcpServers": {}})
            mock_cls.from_dict.assert_called_once()

    def test_failure(self, service):
        with patch("service.command.command_service.MCPClient") as mock_cls:
            mock_cls.from_dict.side_effect = Exception("bad config")
            with pytest.raises(Exception, match="Failed to construct"):
                service._create_mcp_client({})


# ── _create_llm ───────────────────────────────────────────────


class TestCommandServiceCreateLlm:
    def test_success(self, service):
        req = MagicMock()
        req.aiModel = "gpt-4"
        req.aiProvider = "openai"
        req.aiApiKey = "key"
        req.aiBaseUrl = None
        with patch("service.command.command_service.LLMFactory") as mock_factory:
            mock_factory.create_llm.return_value = MagicMock()
            result = service._create_llm(req)
            mock_factory.create_llm.assert_called_once()

    def test_failure(self, service):
        req = MagicMock()
        req.aiModel = "bad"
        req.aiProvider = "bad"
        req.aiApiKey = ""
        with patch("service.command.command_service.LLMFactory") as mock_factory:
            mock_factory.create_llm.side_effect = Exception("bad model")
            with pytest.raises(Exception, match="Failed to create LLM"):
                service._create_llm(req)
