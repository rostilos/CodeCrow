"""
Tests for LLMFactory and QaDocumentationService.

Covers: LLMFactory._normalize_provider, get_supported_providers,
        _check_unsupported_gemini_model, create_llm (all providers),
        QaDocumentationService._create_llm and QA orchestration wiring
"""
import asyncio
import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_google_genai import ChatGoogleGenerativeAI

from llm.llm_factory import (
    LLMFactory,
    UnsupportedModelError,
    UnsupportedProviderError,
    SUPPORTED_PROVIDERS,
    UNSUPPORTED_GEMINI_THINKING_MODELS,
    GEMINI_MODEL_ALTERNATIVES,
    DEFAULT_TEMPERATURE,
    forbid_llm_provider_construction,
    _coerce_openai_compatible_text_content,
    _anthropic_output_cap,
    _is_cloudflare_base_url,
    _normalize_cloudflare_chat_payload,
    _normalize_openrouter_chat_payload,
    _normalize_openai_compatible_base_url,
    _parse_google_vertex_config,
    _split_openai_compatible_parameters,
    _strip_google_vertex_model_prefix,
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
        assert LLMFactory._normalize_provider("google-ai") == "google"

    def test_google_vertex(self):
        assert LLMFactory._normalize_provider("google_vertex") == "google_vertex"
        assert LLMFactory._normalize_provider("google-vertex") == "google_vertex"
        assert LLMFactory._normalize_provider("vertex-ai") == "google_vertex"

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
        assert "GOOGLE_VERTEX" in providers
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
    def test_factory_accepts_an_explicit_output_token_cap(self):
        assert "max_tokens" in inspect.signature(LLMFactory.create_llm).parameters

    def test_provider_construction_guard_fails_closed_and_resets(self):
        with forbid_llm_provider_construction("test dry run"):
            with pytest.raises(
                RuntimeError,
                match="forbidden.*test dry run",
            ):
                LLMFactory.create_llm(
                    ai_model="gpt-4o",
                    ai_provider="openai",
                    ai_api_key="test-key",
                )

        with patch("llm.llm_factory.ChatOpenAI") as constructor:
            LLMFactory.create_llm(
                ai_model="gpt-4o",
                ai_provider="openai",
                ai_api_key="test-key",
            )
        constructor.assert_called_once()
        assert "model_kwargs" not in constructor.call_args.kwargs

    @pytest.mark.asyncio
    async def test_provider_construction_guard_is_task_local(self):
        guarded_task_entered = asyncio.Event()
        release_guarded_task = asyncio.Event()

        async def guarded() -> None:
            with forbid_llm_provider_construction("isolated dry run"):
                guarded_task_entered.set()
                await release_guarded_task.wait()
                with pytest.raises(RuntimeError, match="isolated dry run"):
                    LLMFactory.create_llm(
                        ai_model="gpt-4o",
                        ai_provider="openai",
                        ai_api_key="test-key",
                    )

        async def normal() -> None:
            await guarded_task_entered.wait()
            try:
                with patch("llm.llm_factory.ChatOpenAI") as constructor:
                    LLMFactory.create_llm(
                        ai_model="gpt-4o",
                        ai_provider="openai",
                        ai_api_key="test-key",
                    )
                constructor.assert_called_once()
            finally:
                release_guarded_task.set()

        await asyncio.gather(guarded(), normal())

    def test_openrouter(self):
        llm = LLMFactory.create_llm(
            ai_model="google/gemini-2.0-flash",
            ai_provider="openrouter",
            ai_api_key="test-key",
        )
        assert llm is not None

    def test_openrouter_omits_completion_cap_when_caller_does_not_request_one(self):
        with patch("llm.llm_factory.ChatOpenRouter") as constructor:
            llm = LLMFactory.create_llm(
                ai_model="deepseek/deepseek-v4-flash-0731",
                ai_provider="openrouter",
                ai_api_key="test-key",
            )

        assert llm is constructor.return_value
        assert "max_tokens" not in constructor.call_args.kwargs
        assert "max_completion_tokens" not in constructor.call_args.kwargs
        assert "model_kwargs" not in constructor.call_args.kwargs

    def test_openrouter_uses_finite_transport_defaults(self, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER_TIMEOUT_SECONDS", raising=False)
        monkeypatch.delenv("LLM_PROVIDER_MAX_RETRIES", raising=False)

        with patch("llm.llm_factory.ChatOpenRouter") as constructor:
            LLMFactory.create_llm(
                ai_model="deepseek/deepseek-v4-flash-0731",
                ai_provider="openrouter",
                ai_api_key="test-key",
            )

        assert constructor.call_args.kwargs["timeout"] == 120.0
        assert constructor.call_args.kwargs["max_retries"] == 1

    def test_openrouter_applies_explicit_provider_priority(self):
        with patch("llm.llm_factory.ChatOpenRouter") as constructor:
            LLMFactory.create_llm(
                ai_model="deepseek/deepseek-v4-flash-0731",
                ai_provider="openrouter",
                ai_api_key="test-key",
                ai_custom_parameters={
                    "provider": {
                        "order": ["cloudflare"],
                        "allow_fallbacks": True,
                    },
                },
            )

        assert constructor.call_args.kwargs["extra_body"] == {
            "provider": {
                "order": ["cloudflare"],
                "allow_fallbacks": True,
            },
        }

    def test_openai_protocol_transport_settings_are_configurable(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_TIMEOUT_SECONDS", "75.5")
        monkeypatch.setenv("LLM_PROVIDER_MAX_RETRIES", "0")

        with patch("llm.llm_factory.ChatOpenAI") as constructor:
            LLMFactory.create_llm(
                ai_model="gpt-4o",
                ai_provider="openai",
                ai_api_key="test-key",
            )

        assert constructor.call_args.kwargs["timeout"] == 75.5
        assert constructor.call_args.kwargs["max_retries"] == 0

    def test_openrouter_payload_uses_canonical_max_tokens_field(self):
        payload = _normalize_openrouter_chat_payload({
            "model": "deepseek/deepseek-v4-flash-0731",
            "max_completion_tokens": 16_384,
        })
        assert payload["max_tokens"] == 16_384
        assert "max_completion_tokens" not in payload

    def test_openai(self):
        llm = LLMFactory.create_llm(
            ai_model="gpt-4o",
            ai_provider="openai",
            ai_api_key="test-key",
        )
        assert llm is not None

    @patch("llm.llm_factory._anthropic_output_cap", return_value=18_000)
    def test_anthropic_uses_the_finite_resolved_cap(self, resolve_cap):
        with patch("llm.llm_factory.ChatAnthropic") as constructor:
            llm = LLMFactory.create_llm(
                ai_model="claude-3-sonnet",
                ai_provider="anthropic",
                ai_api_key="test-key",
                max_tokens=18_000,
            )
        assert llm is constructor.return_value
        resolve_cap.assert_called_once_with("claude-3-sonnet", 18_000)
        assert constructor.call_args.kwargs["max_tokens"] == 18_000
        assert "model_kwargs" not in constructor.call_args.kwargs

    def test_google_gemini_2x(self):
        ChatGoogleGenerativeAI.reset_mock()
        llm = LLMFactory.create_llm(
            ai_model="gemini-2.0-flash",
            ai_provider="google",
            ai_api_key="test-key",
            max_tokens=16_384,
        )
        assert llm is not None
        assert ChatGoogleGenerativeAI.call_args.kwargs["max_tokens"] == 16_384

    def test_google_gemini_3x(self):
        ChatGoogleGenerativeAI.reset_mock()
        llm = LLMFactory.create_llm(
            ai_model="gemini-3.0-flash",
            ai_provider="google",
            ai_api_key="test-key",
            max_tokens=16_384,
        )
        assert llm is not None
        assert ChatGoogleGenerativeAI.call_args.kwargs["max_tokens"] == 16_384

    def test_google_vertex_service_account_json(self):
        llm = LLMFactory.create_llm(
            ai_model="publishers/google/models/gemini-3-flash-preview",
            ai_provider="google_vertex",
            ai_api_key='{"project_id":"vertex-project"}',
            ai_base_url="vertex-project/global",
        )
        assert llm is not None

    def test_google_vertex_adc(self):
        llm = LLMFactory.create_llm(
            ai_model="gemini-2.5-flash",
            ai_provider="GOOGLE_VERTEX",
            ai_api_key="ADC",
            ai_base_url="vertex-project/global",
        )
        assert llm is not None

    def test_google_vertex_api_key(self):
        ChatGoogleGenerativeAI.reset_mock()

        llm = LLMFactory.create_llm(
            ai_model="gemini-2.5-flash",
            ai_provider="google_vertex",
            ai_api_key="AIza-test-key",
            ai_base_url="vertex-project/global",
        )
        assert llm is not None
        kwargs = ChatGoogleGenerativeAI.call_args.kwargs
        assert kwargs["vertexai"] is True
        assert kwargs["google_api_key"] == "AIza-test-key"
        assert "project" not in kwargs
        assert "location" not in kwargs

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

    def test_openai_compatible_applies_bounds_to_sdk_and_http_clients(
        self,
        monkeypatch,
    ):
        monkeypatch.setenv("LLM_PROVIDER_TIMEOUT_SECONDS", "90")
        monkeypatch.setenv("LLM_PROVIDER_MAX_RETRIES", "1")
        sync_client = MagicMock()
        async_client = MagicMock()

        with patch(
            "llm.ssrf_safe_transport.create_ssrf_safe_http_client",
            return_value=sync_client,
        ) as create_sync, patch(
            "llm.ssrf_safe_transport.create_ssrf_safe_async_http_client",
            return_value=async_client,
        ) as create_async, patch("llm.llm_factory.ChatOpenAI") as constructor:
            LLMFactory.create_llm(
                ai_model="local-model",
                ai_provider="openai_compatible",
                ai_api_key="test-key",
                ai_base_url="https://my-vllm.example.com",
            )

        create_sync.assert_called_once_with(
            "https://my-vllm.example.com",
            timeout=90.0,
        )
        create_async.assert_called_once_with(
            "https://my-vllm.example.com",
            timeout=90.0,
        )
        assert constructor.call_args.kwargs["timeout"] == 90.0
        assert constructor.call_args.kwargs["max_retries"] == 1

    def test_openai_compatible_explicit_constructor_bounds_remain_supported(
        self,
    ):
        sync_client = MagicMock()
        async_client = MagicMock()

        with patch(
            "llm.ssrf_safe_transport.create_ssrf_safe_http_client",
            return_value=sync_client,
        ) as create_sync, patch(
            "llm.ssrf_safe_transport.create_ssrf_safe_async_http_client",
            return_value=async_client,
        ) as create_async, patch("llm.llm_factory.ChatOpenAI") as constructor:
            LLMFactory.create_llm(
                ai_model="local-model",
                ai_provider="openai_compatible",
                ai_api_key="test-key",
                ai_base_url="https://my-vllm.example.com",
                ai_custom_parameters={
                    "constructor_kwargs": {
                        "timeout": 45,
                        "max_retries": 0,
                    },
                },
            )

        create_sync.assert_called_once_with(
            "https://my-vllm.example.com",
            timeout=45,
        )
        create_async.assert_called_once_with(
            "https://my-vllm.example.com",
            timeout=45,
        )
        assert constructor.call_args.kwargs["timeout"] == 45
        assert constructor.call_args.kwargs["max_retries"] == 0

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

# ── OPENAI_COMPATIBLE URL and payload helpers ───────────────────

class TestOpenAICompatibleHelpers:
    def test_output_token_limits_are_removed_from_custom_parameters(self):
        model_kwargs, constructor_kwargs, request_kwargs, _ = (
            _split_openai_compatible_parameters(
                {
                    "max_tokens": 4_096,
                    "model_kwargs": {
                        "max_output_tokens": 6_000,
                        "maxOutputLength": 7_000,
                        "max_generation_tokens": 7_500,
                        "max_length": 7_750,
                        "num_predict": 3_000,
                    },
                    "extra_body": {
                        "generation_config": {
                            "maxOutputTokens": 8_000,
                            "max_token_count": 9_000,
                            "max_tokens_to_sample": 10_000,
                        },
                    },
                }
            )
        )

        assert "max_tokens" not in model_kwargs
        assert "max_output_tokens" not in model_kwargs
        assert "maxOutputLength" not in model_kwargs
        assert "max_generation_tokens" not in model_kwargs
        assert "max_length" not in model_kwargs
        assert "num_predict" not in model_kwargs
        assert request_kwargs == {}
        assert constructor_kwargs == {"extra_body": {"generation_config": {}}}

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


class TestAnthropicFiniteOutputCap:
    @patch(
        "llm.llm_factory._anthropic_profile_max_output_tokens",
        return_value=64_000,
    )
    def test_requested_cap_wins_below_known_local_capability(self, _profile):
        assert _anthropic_output_cap("claude-sonnet-4-5", 18_000) == 18_000

    @patch(
        "llm.llm_factory._anthropic_profile_max_output_tokens",
        return_value=None,
    )
    def test_unknown_profile_uses_finite_configured_default_without_network(self, _profile):
        assert _anthropic_output_cap("claude-future-model", None) == 40_000
        assert _anthropic_output_cap("claude-future-model", 12_000) == 12_000

    @patch(
        "llm.llm_factory._anthropic_profile_max_output_tokens",
        return_value=64_000,
    )
    def test_known_provider_cap_clamps_larger_configured_cap(self, _profile):
        assert _anthropic_output_cap("claude-legacy-model", 80_000) == 64_000

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


# ── Google Vertex helpers ────────────────────────────────────────

class TestGoogleVertexHelpers:
    def test_parse_project_slash_location(self):
        assert _parse_google_vertex_config("my-project/global") == ("my-project", "global")

    def test_parse_resource_path(self):
        assert _parse_google_vertex_config("projects/my-project/locations/us-central1") == (
            "my-project",
            "us-central1",
        )

    def test_parse_full_url(self):
        assert _parse_google_vertex_config(
            "https://aiplatform.googleapis.com/v1/projects/my-project/locations/global/publishers/google/models/gemini-3-flash-preview"
        ) == ("my-project", "global")

    def test_parse_json(self):
        assert _parse_google_vertex_config('{"project_id":"my-project","location":"global"}') == (
            "my-project",
            "global",
        )

    def test_strip_model_prefix(self):
        assert _strip_google_vertex_model_prefix("models/gemini-2.5-flash") == "gemini-2.5-flash"
        assert (
            _strip_google_vertex_model_prefix("publishers/google/models/gemini-3-flash-preview")
            == "gemini-3-flash-preview"
        )


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
    def test_does_not_configure_rag_mutation_client(self):
        svc = QaDocumentationService()
        assert not hasattr(svc, "_rag_pipeline_url")
        assert not hasattr(svc, "_create_rag_client")

    @pytest.mark.asyncio(loop_scope="function")
    @patch("service.qa_documentation.qa_doc_service.QaDocOrchestrator")
    async def test_generate_never_wires_a_rag_mutation_client(
        self,
        orchestrator_type,
    ):
        svc = QaDocumentationService()
        llm = MagicMock()
        svc._create_llm = MagicMock(return_value=llm)
        orchestrator_type.return_value.run = AsyncMock(return_value={
            "documentation_needed": False,
            "documentation": None,
        })

        await svc.generate(
            project_id=1,
            project_name="project",
            pr_number=17,
            issues_found=0,
            files_analyzed=1,
            pr_metadata={},
            template_mode="BASE",
            custom_template=None,
            task_context=None,
        )

        orchestrator_type.assert_called_once_with(llm=llm)
