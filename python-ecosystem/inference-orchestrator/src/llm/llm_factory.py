import os
import logging
import math
from typing import Any, Optional
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI

from llm.openai_parameters import (
    _normalize_openrouter_chat_payload,
    _openrouter_custom_extra_body,
    OPENAI_COMPATIBLE_RESERVED_DIRECT_PARAMS,
    OPENAI_COMPATIBLE_CONSTRUCTOR_PARAM_KEYS,
    OPENAI_COMPATIBLE_DIRECT_REQUEST_PARAM_KEYS,
    OUTPUT_TOKEN_LIMIT_KEYS,
    _parse_json_object,
    _parse_env_json_object,
    _merge_dict,
    _without_output_token_limits,
    _split_openai_compatible_parameters,
)
from llm.openai_adapters import (
    ChatOpenRouter,
    _is_cloudflare_base_url,
    _trim_openai_endpoint_suffix,
    _normalize_openai_compatible_base_url,
    _coerce_openai_compatible_text_content,
    _cloudflare_message_to_dict,
    _normalize_cloudflare_chat_payload,
    ChatCloudflareOpenAI,
)
from llm.vertex_config import (
    GOOGLE_VERTEX_SCOPES,
    _strip_google_vertex_model_prefix,
    _parse_google_vertex_config,
    _build_google_vertex_credentials,
)

from llm.request_capture import configure_capture

from llm.provider_guard import (
    forbid_llm_provider_construction,
    provider_construction_block_reason,
)

logger = logging.getLogger(__name__)


# Default temperature from env or 0.0 for deterministic results
DEFAULT_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.0"))
DEFAULT_ANTHROPIC_MAX_OUTPUT_TOKENS = 40_000
DEFAULT_LLM_PROVIDER_TIMEOUT_SECONDS = 120.0
DEFAULT_LLM_PROVIDER_MAX_RETRIES = 1


def _finite_provider_float(name: str, default: float) -> float:
    """Read a positive finite provider setting without restoring SDK infinity."""
    configured = os.environ.get(name)
    if configured is None or not configured.strip():
        return default
    try:
        value = float(configured)
    except ValueError:
        logger.warning("Invalid number for %s=%r; using %s", name, configured, default)
        return default
    if not math.isfinite(value) or value <= 0:
        logger.warning("Non-positive or non-finite %s=%r; using %s", name, configured, default)
        return default
    return value


def _finite_provider_retries(name: str, default: int) -> int:
    """Read the OpenAI-protocol client's finite additional retry count."""
    configured = os.environ.get(name)
    if configured is None or not configured.strip():
        return default
    try:
        value = int(configured)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, configured, default)
        return default
    if value < 0:
        logger.warning("Negative %s=%r; using %s", name, configured, default)
        return default
    return value


def _openai_protocol_transport_settings() -> dict[str, Any]:
    """Bound each OpenAI-protocol request and its SDK retry amplification.

    The OpenAI SDK otherwise defaults to a 600-second read timeout and two
    additional attempts. A single stalled Stage 1 turn can therefore occupy a
    worker for about 30 minutes before the review's own recovery path runs.
    """
    return {
        "timeout": _finite_provider_float(
            "LLM_PROVIDER_TIMEOUT_SECONDS",
            DEFAULT_LLM_PROVIDER_TIMEOUT_SECONDS,
        ),
        "max_retries": _finite_provider_retries(
            "LLM_PROVIDER_MAX_RETRIES",
            DEFAULT_LLM_PROVIDER_MAX_RETRIES,
        ),
    }


# Gemini thinking/reasoning models that DON'T work with tool calls
# These are experimental thinking models that have known issues with MCP tools.
# Standard Gemini 2.x and 3.x models work fine with thinking_level/thinking_budget settings.
UNSUPPORTED_GEMINI_THINKING_MODELS = {
    # Experimental thinking models - have known issues
    "google/gemini-2.0-flash-thinking-exp",
    "google/gemini-2.0-flash-thinking-exp:free",
    "gemini-2.0-flash-thinking-exp",
}

# Mapping from unsupported thinking models to recommended alternatives
GEMINI_MODEL_ALTERNATIVES = {
    "google/gemini-2.0-flash-thinking-exp": "google/gemini-2.0-flash",
    "google/gemini-2.0-flash-thinking-exp:free": "google/gemini-2.0-flash",
    "gemini-2.0-flash-thinking-exp": "gemini-2.0-flash",
}

# Supported AI providers with their identifiers
SUPPORTED_PROVIDERS = {
    "openrouter": ["openrouter", "open-router"],
    "openai": ["openai"],
    "anthropic": ["anthropic"],
    "google": ["google", "google-genai", "google-ai"],
    "google_vertex": [
        "google_vertex",
        "google-vertex",
        "google_vertex_ai",
        "google-vertex-ai",
        "vertex",
        "vertexai",
        "vertex-ai",
    ],
    "openai_compatible": ["openai_compatible", "openai-compatible"],
}

class UnsupportedModelError(Exception):
    """Raised when an unsupported model is requested."""
    pass


class UnsupportedProviderError(Exception):
    """Raised when an unsupported provider is requested."""
    pass


def _anthropic_profile_max_output_tokens(ai_model: str) -> Optional[int]:
    """Read a local LangChain capability profile without provider I/O."""
    try:
        from langchain_anthropic.chat_models import _get_default_model_profile

        profile = _get_default_model_profile(ai_model)
    except (ImportError, AttributeError, TypeError):
        return None
    value = profile.get("max_output_tokens") if isinstance(profile, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _anthropic_output_cap(ai_model: str, requested: Optional[int]) -> int:
    """Resolve a finite request cap without a network lookup or fail-closed gate."""
    configured = (
        requested
        if isinstance(requested, int) and not isinstance(requested, bool) and requested > 0
        else DEFAULT_ANTHROPIC_MAX_OUTPUT_TOKENS
    )
    profile_max = _anthropic_profile_max_output_tokens(ai_model)
    if profile_max is None:
        logger.warning(
            "Anthropic model %s is absent from the local LangChain capability "
            "profile; using the finite configured output cap max_tokens=%d",
            ai_model,
            configured,
        )
        return configured
    if configured > profile_max:
        logger.warning(
            "Configured Anthropic output cap max_tokens=%d exceeds the local "
            "model capability %d for %s; using the provider-supported boundary",
            configured,
            profile_max,
            ai_model,
        )
        return profile_max
    return configured





class LLMFactory:
    """
    Factory for creating LLM instances for different AI providers.
    
    Supported providers:
    - OPENROUTER: Access to multiple models via OpenRouter API (recommended)
    - OPENAI: Direct OpenAI API access (gpt-4o, gpt-4-turbo, etc.)
    - ANTHROPIC: Direct Anthropic API access (claude-3-opus, claude-3-sonnet, etc.)
    - GOOGLE: Direct Google AI API access (gemini-pro, gemini-1.5-pro, etc.)
    - GOOGLE_VERTEX: Google Vertex AI Gemini access via service account JSON, ADC, or Vertex API key
    - OPENAI_COMPATIBLE: Any OpenAI-API-compatible endpoint (vLLM, Ollama, Cloudflare Workers AI, etc.)
    """

    @staticmethod
    def get_supported_providers() -> list[str]:
        """Return list of supported provider keys."""
        return ["OPENROUTER", "OPENAI", "ANTHROPIC", "GOOGLE", "GOOGLE_VERTEX", "OPENAI_COMPATIBLE"]

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        """Normalize provider string to standard format."""
        provider_lower = provider.lower().strip()
        for standard, aliases in SUPPORTED_PROVIDERS.items():
            if provider_lower in aliases:
                return standard
        return provider_lower

    @staticmethod
    def _check_unsupported_gemini_model(ai_model: str) -> None:
        """Check if model is an unsupported Gemini thinking model."""
        model_lower = ai_model.lower()
        for unsupported in UNSUPPORTED_GEMINI_THINKING_MODELS:
            if model_lower == unsupported.lower() or model_lower.startswith(unsupported.lower()):
                alternative = GEMINI_MODEL_ALTERNATIVES.get(unsupported, "gemini-2.0-flash")
                error_msg = (
                    f"Model '{ai_model}' is a Gemini thinking model that requires thought_signature "
                    f"preservation for tool calls. This is not supported by the current LangChain integration. "
                    f"Please use a non-thinking variant instead, such as '{alternative}'."
                )
                logger.error(error_msg)
                raise UnsupportedModelError(error_msg)

    @staticmethod
    def create_llm(
        ai_model: str,
        ai_provider: str,
        ai_api_key: str,
        temperature: Optional[float] = None,
        ai_base_url: Optional[str] = None,
        max_tokens: Optional[int] = None,
        ai_custom_parameters: Optional[dict[str, Any]] = None,
    ):
        """
        Create LLM instance for the specified provider.
        
        Args:
            ai_model: Model name/identifier
            ai_provider: Provider key (OPENROUTER, OPENAI, ANTHROPIC, GOOGLE, GOOGLE_VERTEX, OPENAI_COMPATIBLE)
            ai_api_key: API key for the provider
            temperature: LLM temperature. If None, uses LLM_TEMPERATURE env var or 0.0.
                        0.0 = deterministic results (recommended for code review)
                        0.1-0.3 = more creative but less consistent
            ai_base_url: Base URL for OPENAI_COMPATIBLE provider, or Vertex project/location metadata
            max_tokens: Maximum output tokens. If None, uses the provider default.
            ai_custom_parameters: Optional provider-specific request parameters for
                                  OPENAI_COMPATIBLE endpoints. Direct keys are sent
                                  as model/request kwargs; nested extra_body,
                                  default_headers, and constructor_kwargs are passed
                                  to the OpenAI-compatible client constructor.
                        
        Raises:
            UnsupportedModelError: If the model is unsupported (e.g., Gemini thinking models)
            UnsupportedProviderError: If the provider is not supported
            
        Returns:
            LangChain chat model instance
        """
        blocked_reason = provider_construction_block_reason()
        if blocked_reason is not None:
            raise RuntimeError(
                "LLM provider construction is forbidden in this execution "
                f"context: {blocked_reason}"
            )

        if temperature is None:
            temperature = DEFAULT_TEMPERATURE
        
        # Normalize provider
        provider = LLMFactory._normalize_provider(ai_provider)
        
        # CRITICAL: Log the model being used for debugging
        logger.info(f"Creating LLM instance: provider={provider}, model={ai_model}, temperature={temperature}")
        
        # Check for unsupported Gemini thinking models (applies to all providers)
        LLMFactory._check_unsupported_gemini_model(ai_model)
        
        # OpenRouter provider - access multiple models via single API
        if provider == "openrouter":
            extra_headers = {
                "HTTP-Referer": "https://codecrow.cloud",
                "X-Title": "CodeCrow AI"
            }
            kwargs = dict(
                api_key=ai_api_key,
                model_name=ai_model,
                temperature=temperature,
                organization="Codecrow",
                default_headers=extra_headers,
                **_openai_protocol_transport_settings(),
            )
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            custom_extra_body = _openrouter_custom_extra_body(
                ai_custom_parameters
            )
            if custom_extra_body:
                kwargs["extra_body"] = custom_extra_body
                logger.info(
                    "Applying OpenRouter custom request fields: %s",
                    sorted(custom_extra_body),
                )
            return configure_capture(ChatOpenRouter(**kwargs), provider)
        
        # Direct OpenAI provider
        if provider == "openai":
            kwargs = dict(
                api_key=ai_api_key,
                model=ai_model,
                temperature=temperature,
                **_openai_protocol_transport_settings(),
            )
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            return configure_capture(ChatOpenAI(**kwargs), provider)
        
        # Direct Anthropic provider
        if provider == "anthropic":
            anthropic_max_tokens = _anthropic_output_cap(ai_model, max_tokens)
            kwargs = dict(
                api_key=ai_api_key,
                model=ai_model,
                temperature=temperature,
                # Anthropic requires an explicit max_tokens value. Keep the
                # factory finite even before a review stage binds its narrower
                # profile; unknown/new model IDs remain fail-open and observable.
                max_tokens=anthropic_max_tokens,
            )
            return configure_capture(ChatAnthropic(**kwargs), provider)
        
        # Google AI provider (Gemini models)
        # langchain-google-genai >= 4.0.0 automatically handles thought signatures
        if provider == "google":
            model_lower = ai_model.lower()
            is_gemini_3 = "gemini-3" in model_lower or "gemini3" in model_lower
            
            # Read thinking level from env or default per model family
            thinking_level = os.environ.get("GEMINI_THINKING_LEVEL", None)
            
            if is_gemini_3:
                # Gemini 3 models use thinking_level parameter:
                #   "minimal" - nearly off, minimises latency (Flash only)
                #   "low"     - low latency (Flash + Pro minimum)
                #   "medium"  - balanced reasoning
                #   "high"    - deep reasoning (default if unset!)
                #
                # Temperature: Use the explicitly provided value (0.0-0.1 recommended
                # for code review).  Earlier versions omitted temperature, letting the
                # SDK default to 1.0 which produced inconsistent results.
                effective_thinking = thinking_level or "low"
                kwargs = dict(
                    google_api_key=ai_api_key,
                    model=ai_model,
                    temperature=temperature,
                    thinking_level=effective_thinking,
                )
                if max_tokens:
                    # ChatGoogleGenerativeAI accepts ``max_tokens`` as the
                    # constructor alias for its canonical
                    # ``max_output_tokens`` field.
                    kwargs["max_tokens"] = max_tokens
                return configure_capture(ChatGoogleGenerativeAI(**kwargs), provider)
            else:
                # Gemini 2.x models use thinking_budget parameter:
                #   0  = disable thinking (2.5 Flash) or use model minimum (2.5 Pro min=128)
                #   -1 = dynamic thinking (model decides)
                kwargs = dict(
                    google_api_key=ai_api_key,
                    model=ai_model,
                    temperature=temperature,
                    thinking_budget=0,
                )
                if max_tokens:
                    kwargs["max_tokens"] = max_tokens
                return configure_capture(ChatGoogleGenerativeAI(**kwargs), provider)
        
        # Google Vertex AI provider (Gemini models through Google Cloud)
        if provider == "google_vertex":
            project, location = _parse_google_vertex_config(ai_base_url)
            credentials, credentials_project, vertex_api_key = _build_google_vertex_credentials(ai_api_key)
            project = project or credentials_project

            if not project and vertex_api_key is None:
                raise UnsupportedProviderError(
                    "GOOGLE_VERTEX requires a project ID in the Vertex project/location field, "
                    "in GOOGLE_VERTEX_PROJECT/GOOGLE_CLOUD_PROJECT, in the service account JSON, "
                    "or a Vertex API key for express mode."
                )

            kwargs = dict(
                model=_strip_google_vertex_model_prefix(ai_model),
                vertexai=True,
                temperature=temperature,
            )
            if vertex_api_key:
                kwargs["google_api_key"] = vertex_api_key
            else:
                kwargs["location"] = location
                if project:
                    kwargs["project"] = project
            if credentials is not None:
                kwargs["credentials"] = credentials
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            return configure_capture(ChatGoogleGenerativeAI(**kwargs), provider)

        # OpenAI-compatible custom endpoint (vLLM, Ollama, Cloudflare Workers AI, etc.)
        if provider == "openai_compatible":
            if not ai_base_url:
                raise UnsupportedProviderError(
                    "OPENAI_COMPATIBLE provider requires a base URL. "
                    "Please configure the endpoint URL in your AI connection settings."
                )
            base_url = _normalize_openai_compatible_base_url(ai_base_url)

            (
                custom_model_kwargs,
                custom_constructor_kwargs,
                custom_request_kwargs,
                raw_custom_parameters,
            ) = _split_openai_compatible_parameters(
                ai_custom_parameters
            )
            transport_settings = _openai_protocol_transport_settings()
            if not {
                "timeout",
                "request_timeout",
            }.intersection(custom_constructor_kwargs):
                custom_constructor_kwargs["timeout"] = transport_settings["timeout"]
            if "max_retries" not in custom_constructor_kwargs:
                custom_constructor_kwargs["max_retries"] = transport_settings["max_retries"]

            # SSRF validation — blocks private/reserved IPs unless
            # ALLOW_PRIVATE_ENDPOINTS=true. Keep the explicitly configured
            # compatible-endpoint timeout aligned with its underlying httpx
            # clients rather than leaving their independent default.
            from llm.ssrf_safe_transport import (
                create_ssrf_safe_http_client,
                create_ssrf_safe_async_http_client,
            )
            compatible_timeout = custom_constructor_kwargs.get(
                "timeout",
                custom_constructor_kwargs.get(
                    "request_timeout",
                    transport_settings["timeout"],
                ),
            )
            http_client = create_ssrf_safe_http_client(
                ai_base_url,
                timeout=compatible_timeout,
            )
            async_http_client = create_ssrf_safe_async_http_client(
                ai_base_url,
                timeout=compatible_timeout,
            )
            openai_compatible_model_kwargs = _merge_dict(
                {},
                custom_model_kwargs,
            )
            logger.info(
                "Creating OPENAI_COMPATIBLE LLM: base_url=%s, model=%s, custom_param_keys=%s, constructor_param_keys=%s, request_param_keys=%s",
                base_url,
                ai_model,
                sorted(raw_custom_parameters.keys()) if raw_custom_parameters else sorted(custom_model_kwargs.keys()),
                sorted(custom_constructor_kwargs.keys()),
                sorted(custom_request_kwargs.keys()),
            )
            kwargs = dict(
                api_key=ai_api_key,
                model=ai_model,
                base_url=base_url,
                temperature=temperature,
                model_kwargs=openai_compatible_model_kwargs,
                http_client=http_client,
                http_async_client=async_http_client,
            )
            kwargs.update(custom_constructor_kwargs)
            kwargs.update(custom_request_kwargs)
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            chat_model = (
                ChatCloudflareOpenAI
                if _is_cloudflare_base_url(base_url)
                else ChatOpenAI
            )
            return configure_capture(chat_model(**kwargs), provider)
        
        # Unknown provider - raise error with helpful message
        supported = ", ".join(LLMFactory.get_supported_providers())
        error_msg = f"Unsupported AI provider: '{ai_provider}'. Supported providers: {supported}"
        logger.error(error_msg)
        raise UnsupportedProviderError(error_msg)
