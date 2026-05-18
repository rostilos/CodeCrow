"""
Unit tests for utils.error_sanitizer — sanitize_error_for_display, create_user_friendly_error.
"""
import pytest
from utils.error_sanitizer import sanitize_error_for_display, create_user_friendly_error


class TestSanitizeErrorForDisplay:

    # Empty / None-like
    def test_empty_string(self):
        assert "unexpected error" in sanitize_error_for_display("").lower()

    def test_none_like(self):
        result = sanitize_error_for_display("")
        assert len(result) > 0

    # Quota / rate limit
    @pytest.mark.parametrize("msg", [
        "Rate limit exceeded: 429 Too Many Requests",
        "You have exceeded your quota",
        "rate_limit_error: too many requests",
    ])
    def test_rate_limit(self, msg):
        result = sanitize_error_for_display(msg)
        assert "rate-limited" in result.lower() or "quota" in result.lower()

    # Authentication
    @pytest.mark.parametrize("msg", [
        "401 Unauthorized",
        "403 Forbidden: Invalid API key",
        "invalid_api_key: check your key",
        "authentication failed",
    ])
    def test_auth(self, msg):
        result = sanitize_error_for_display(msg)
        assert "authentication" in result.lower()

    # Model not found
    def test_model_not_found(self):
        result = sanitize_error_for_display("Model gpt-5-turbo not found")
        assert "model" in result.lower()
        assert "not available" in result.lower() or "not supported" in result.lower()

    # Unsupported model
    def test_unsupported_model_with_suggestion(self):
        msg = "Unsupported model 'o1-mini' for tool calling. Instead, such as 'gpt-4o'."
        result = sanitize_error_for_display(msg)
        assert "gpt-4o" in result

    def test_unsupported_model_generic(self):
        msg = "Unsupported model for this operation"
        result = sanitize_error_for_display(msg)
        assert "not supported" in result.lower()

    # Token limit
    def test_token_limit(self):
        result = sanitize_error_for_display("Token limit reached: maximum context length")
        assert "token" in result.lower()

    # Network
    @pytest.mark.parametrize("msg", [
        "Connection timeout after 30s",
        "Connection refused",
        "Network unreachable",
    ])
    def test_network(self, msg):
        result = sanitize_error_for_display(msg)
        assert "connect" in result.lower() or "try again" in result.lower()

    # Content filter
    def test_content_filter(self):
        result = sanitize_error_for_display("Content filter blocked the request")
        assert "content filter" in result.lower()

    # Thought signature (Gemini)
    def test_thought_signature(self):
        result = sanitize_error_for_display("thought_signature error in Gemini model")
        assert "thinking" in result.lower() or "not compatible" in result.lower()

    # MCP/tool errors
    def test_mcp_error(self):
        result = sanitize_error_for_display("MCP tool call failed")
        assert "analysis tools" in result.lower() or "tools" in result.lower()

    # AI service errors
    def test_ai_service(self):
        result = sanitize_error_for_display("LLM generation failed: OpenAI error")
        assert "ai service" in result.lower()

    # Stack traces
    def test_stack_trace(self):
        result = sanitize_error_for_display("Exception in thread: at org.example.Main.run(Main.java:42)")
        assert "internal error" in result.lower()

    def test_python_traceback(self):
        result = sanitize_error_for_display('File "/app/main.py", line 42, in process')
        assert "internal error" in result.lower()

    # JSON structure
    def test_json_structure(self):
        result = sanitize_error_for_display('{"error": "something"}')
        assert "error occurred" in result.lower()

    # Very long message
    def test_long_message_truncated(self):
        result = sanitize_error_for_display("A" * 300)
        assert "error occurred" in result.lower()

    # Safe short message
    def test_safe_message_passthrough(self):
        result = sanitize_error_for_display("Something went wrong")
        assert "Something went wrong" in result

    # API key redaction
    def test_api_key_redacted(self):
        result = sanitize_error_for_display("Error with sk-abcdefghijklmnopqrstuvwxyz1234")
        assert "sk-" not in result or "REDACTED" in result


class TestCreateUserFriendlyError:

    def test_exception(self):
        err = ValueError("quota exceeded")
        result = create_user_friendly_error(err)
        assert "rate-limited" in result.lower() or "quota" in result.lower()

    def test_generic_exception(self):
        err = RuntimeError("something happened")
        result = create_user_friendly_error(err)
        assert len(result) > 0
