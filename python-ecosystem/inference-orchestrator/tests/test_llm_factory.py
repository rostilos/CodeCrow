"""
Tests for LLMFactory and QaDocumentationService.

Covers: LLMFactory._normalize_provider, get_supported_providers,
        _check_unsupported_gemini_model, create_llm (all providers),
        QaDocumentationService._create_llm, _create_rag_client
"""
import pytest
from unittest.mock import MagicMock, patch

from llm.llm_factory import (
    LLMFactory,
    UnsupportedModelError,
    UnsupportedProviderError,
    SUPPORTED_PROVIDERS,
    UNSUPPORTED_GEMINI_THINKING_MODELS,
    GEMINI_MODEL_ALTERNATIVES,
    DEFAULT_TEMPERATURE,
    _coerce_openai_compatible_text_content,
    _is_cloudflare_base_url,
    _normalize_cloudflare_chat_payload,
    _normalize_openai_compatible_base_url,
)
from service.qa_documentation.qa_doc_service import QaDocumentationService


# ── LLMFactory._normalize_provider ──────────────────────────────

class TestNormalizeProvider:
    def test_openrouter(self):
        assert LLMFactory._normalize_provider("openrouter") == "openrouter"
        assert LLMFactory._normalize_provider("OPENROUTER") == "openrouter"
        assert LLMFactory._normalize_provider("open-router") == "openrouter"

    def test_openai(self):
        assert LLMFactory._normalize_provider("openai") == "openai"
        assert LLMFactory._normalize_provider("OPENAI") == "openai"

    def test_anthropic(self):
        assert LLMFactory._normalize_provider("anthropic") == "anthropic"
        assert LLMFactory._normalize_provider("ANTHROPIC") == "anthropic"

    def test_google(self):
        assert LLMFactory._normalize_provider("google") == "google"
        assert LLMFactory._normalize_provider("google-genai") == "google"
        assert LLMFactory._normalize_provider("google-vertex") == "google"
        assert LLMFactory._normalize_provider("google-ai") == "google"

    def test_openai_compatible(self):
        assert LLMFactory._normalize_provider("openai_compatible") == "openai_compatible"
        assert LLMFactory._normalize_provider("openai-compatible") == "openai_compatible"

    def test_unknown(self):
        assert LLMFactory._normalize_provider("random") == "random"

    def test_whitespace(self):
        assert LLMFactory._normalize_provider("  openai  ") == "openai"


# ── LLMFactory.get_supported_providers ───────────────────────────

class TestGetSupportedProviders:
    def test_returns_list(self):
        providers = LLMFactory.get_supported_providers()
        assert isinstance(providers, list)
        assert "OPENROUTER" in providers
        assert "OPENAI" in providers
        assert "ANTHROPIC" in providers
        assert "GOOGLE" in providers
        assert "OPENAI_COMPATIBLE" in providers


# ── LLMFactory._check_unsupported_gemini_model ──────────────────

class TestCheckUnsupportedGeminiModel:
    def test_supported_model_passes(self):
        LLMFactory._check_unsupported_gemini_model("gemini-2.0-flash")  # No exception

    def test_unsupported_thinking_model_raises(self):
        with pytest.raises(UnsupportedModelError, match="thinking model"):
            LLMFactory._check_unsupported_gemini_model("google/gemini-2.0-flash-thinking-exp")

    def test_unsupported_thinking_free_raises(self):
        with pytest.raises(UnsupportedModelError):
            LLMFactory._check_unsupported_gemini_model("google/gemini-2.0-flash-thinking-exp:free")

    def test_gpt4_passes(self):
        LLMFactory._check_unsupported_gemini_model("gpt-4o")  # No exception

    def test_claude_passes(self):
        LLMFactory._check_unsupported_gemini_model("claude-3-opus")  # No exception


# ── LLMFactory.create_llm ───────────────────────────────────────

class TestCreateLlm:
    def test_openrouter(self):
        llm = LLMFactory.create_llm(
            ai_model="google/gemini-2.0-flash",
            ai_provider="openrouter",
            ai_api_key="test-key",
        )
        assert llm is not None

    def test_openai(self):
        llm = LLMFactory.create_llm(
            ai_model="gpt-4o",
            ai_provider="openai",
            ai_api_key="test-key",
        )
        assert llm is not None

    def test_anthropic(self):
        llm = LLMFactory.create_llm(
            ai_model="claude-3-sonnet",
            ai_provider="anthropic",
            ai_api_key="test-key",
        )
        assert llm is not None

    def test_google_gemini_2x(self):
        llm = LLMFactory.create_llm(
            ai_model="gemini-2.0-flash",
            ai_provider="google",
            ai_api_key="test-key",
        )
        assert llm is not None

    def test_google_gemini_3x(self):
        llm = LLMFactory.create_llm(
            ai_model="gemini-3.0-flash",
            ai_provider="google",
            ai_api_key="test-key",
        )
        assert llm is not None

    def test_openai_compatible_no_base_url_raises(self):
        with pytest.raises(UnsupportedProviderError, match="requires a base URL"):
            LLMFactory.create_llm(
                ai_model="local-model",
                ai_provider="openai_compatible",
                ai_api_key="test",
            )

    @patch("llm.ssrf_safe_transport.create_ssrf_safe_http_client", return_value=MagicMock())
    @patch("llm.ssrf_safe_transport.create_ssrf_safe_async_http_client", return_value=MagicMock())
    def test_openai_compatible_with_url(self, mock_async, mock_sync):
        llm = LLMFactory.create_llm(
            ai_model="local-model",
            ai_provider="openai_compatible",
            ai_api_key="test",
            ai_base_url="https://my-vllm.example.com",
        )
        assert llm is not None

    def test_unsupported_provider_raises(self):
        with pytest.raises(UnsupportedProviderError, match="Unsupported AI provider"):
            LLMFactory.create_llm(
                ai_model="model",
                ai_provider="non_existent_provider",
                ai_api_key="key",
            )

    def test_unsupported_gemini_thinking_raises(self):
        with pytest.raises(UnsupportedModelError):
            LLMFactory.create_llm(
                ai_model="google/gemini-2.0-flash-thinking-exp",
                ai_provider="openrouter",
                ai_api_key="key",
            )

    def test_custom_temperature(self):
        llm = LLMFactory.create_llm(
            ai_model="gpt-4o",
            ai_provider="openai",
            ai_api_key="key",
            temperature=0.5,
        )
        assert llm is not None

    def test_max_tokens(self):
        llm = LLMFactory.create_llm(
            ai_model="gpt-4o",
            ai_provider="openai",
            ai_api_key="key",
            max_tokens=4096,
        )
        assert llm is not None


# ── OPENAI_COMPATIBLE URL and payload helpers ───────────────────

class TestOpenAICompatibleHelpers:
    def test_normalize_standard_base_url_appends_v1(self):
        assert (
            _normalize_openai_compatible_base_url("https://my-vllm.example.com")
            == "https://my-vllm.example.com/v1"
        )

    def test_normalize_strips_pasted_chat_endpoint(self):
        assert (
            _normalize_openai_compatible_base_url(
                "https://my-vllm.example.com/v1/chat/completions"
            )
            == "https://my-vllm.example.com/v1"
        )

    def test_normalize_cloudflare_workers_ai_preserves_ai_v1(self):
        base = "https://api.cloudflare.com/client/v4/accounts/account-id/ai/v1"
        assert _normalize_openai_compatible_base_url(base) == base

    def test_normalize_cloudflare_workers_ai_appends_v1_after_ai(self):
        assert (
            _normalize_openai_compatible_base_url(
                "https://api.cloudflare.com/client/v4/accounts/account-id/ai"
            )
            == "https://api.cloudflare.com/client/v4/accounts/account-id/ai/v1"
        )

    def test_normalize_cloudflare_ai_gateway_does_not_append_v1(self):
        base = "https://gateway.ai.cloudflare.com/v1/account-id/default/compat"
        assert _normalize_openai_compatible_base_url(base) == base

    def test_normalize_cloudflare_workers_ai_run_endpoint_to_openai_base(self):
        assert (
            _normalize_openai_compatible_base_url(
                "https://api.cloudflare.com/client/v4/accounts/account-id/ai/run/@cf/moonshotai/kimi-k2-instruct"
            )
            == "https://api.cloudflare.com/client/v4/accounts/account-id/ai/v1"
        )

    def test_detect_cloudflare_base_url(self):
        assert _is_cloudflare_base_url(
            "https://api.cloudflare.com/client/v4/accounts/id/ai/v1"
        )
        assert _is_cloudflare_base_url(
            "https://gateway.ai.cloudflare.com/v1/id/default/compat"
        )
        assert not _is_cloudflare_base_url("https://api.openai.com/v1")

    def test_coerce_content_blocks_to_text(self):
        assert (
            _coerce_openai_compatible_text_content([
                {"type": "text", "text": "hello"},
                "world",
                {"type": "thinking", "text": "hidden"},
            ])
            == "hello\nworld"
        )

    def test_normalize_cloudflare_payload_content_blocks_and_tool_calls(self):
        payload = {
            "parallel_tool_calls": False,
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": "sys"}]},
                {"role": "user", "content": [{"type": "text", "text": "question"}]},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
                {
                    "role": "tool",
                    "content": [{"type": "text", "text": "result"}],
                    "tool_call_id": "1",
                },
            ]
        }

        normalized = _normalize_cloudflare_chat_payload(payload)

        assert normalized["messages"][0]["content"] == "sys"
        assert normalized["messages"][1]["content"] == "question"
        assert normalized["messages"][2]["content"] is None
        assert normalized["messages"][3]["content"] == "result"
        assert "parallel_tool_calls" not in normalized

    def test_normalize_cloudflare_payload_langchain_message_objects(self):
        class MessageObject:
            type = "human"
            content = [{"type": "text", "text": "question"}]

        payload = {"messages": (MessageObject(),)}

        normalized = _normalize_cloudflare_chat_payload(payload)

        assert normalized["messages"] == [
            {"role": "user", "content": "question"}
        ]

    def test_normalize_cloudflare_payload_model_dump_message(self):
        class DumpMessage:
            def model_dump(self, **_kwargs):
                return {
                    "type": "system",
                    "content": [{"type": "text", "text": "sys"}],
                    "additional_kwargs": {"ignored": True},
                }

        normalized = _normalize_cloudflare_chat_payload(
            {"messages": [DumpMessage()]}
        )

        assert normalized["messages"] == [
            {"role": "system", "content": "sys"}
        ]


# ── Constants ────────────────────────────────────────────────────

class TestConstants:
    def test_unsupported_models_set(self):
        assert isinstance(UNSUPPORTED_GEMINI_THINKING_MODELS, set)
        assert len(UNSUPPORTED_GEMINI_THINKING_MODELS) > 0

    def test_alternatives_dict(self):
        assert isinstance(GEMINI_MODEL_ALTERNATIVES, dict)
        for key in GEMINI_MODEL_ALTERNATIVES:
            assert key in UNSUPPORTED_GEMINI_THINKING_MODELS

    def test_supported_providers_dict(self):
        assert isinstance(SUPPORTED_PROVIDERS, dict)
        assert "openrouter" in SUPPORTED_PROVIDERS


# ── QaDocumentationService ───────────────────────────────────────

class TestQaDocumentationService:
    @patch.dict("os.environ", {
        "QA_DOC_AI_PROVIDER": "openai",
        "QA_DOC_AI_MODEL": "gpt-4o",
        "QA_DOC_AI_API_KEY": "test-key",
        "RAG_PIPELINE_URL": "http://rag:8020",
    })
    def test_init(self):
        svc = QaDocumentationService()
        assert svc._ai_provider == "openai"
        assert svc._ai_model == "gpt-4o"
        assert svc._ai_api_key == "test-key"

    @patch.dict("os.environ", {
        "AI_PROVIDER": "anthropic",
        "AI_MODEL": "claude-3",
        "AI_API_KEY": "key",
    })
    def test_init_defaults_to_general_env(self):
        svc = QaDocumentationService()
        assert svc._ai_provider == "anthropic"
        assert svc._ai_model == "claude-3"

    @patch.dict("os.environ", {
        "QA_DOC_AI_PROVIDER": "openai",
        "QA_DOC_AI_MODEL": "gpt-4o",
        "QA_DOC_AI_API_KEY": "test",
    })
    def test_create_llm(self):
        svc = QaDocumentationService()
        llm = svc._create_llm()
        assert llm is not None

    @patch.dict("os.environ", {
        "QA_DOC_AI_PROVIDER": "openai",
        "QA_DOC_AI_MODEL": "gpt-4o",
        "QA_DOC_AI_API_KEY": "test",
        "RAG_PIPELINE_URL": "http://rag:8020",
    })
    def test_create_rag_client(self):
        svc = QaDocumentationService()
        client = svc._create_rag_client()
        # Should not be None when URL is configured
        assert client is not None

    @patch.dict("os.environ", {
        "QA_DOC_AI_PROVIDER": "openai",
        "QA_DOC_AI_MODEL": "gpt-4o",
        "QA_DOC_AI_API_KEY": "test",
        "RAG_PIPELINE_URL": "",
    })
    def test_create_rag_client_no_url(self):
        svc = QaDocumentationService()
        client = svc._create_rag_client()
        assert client is None
