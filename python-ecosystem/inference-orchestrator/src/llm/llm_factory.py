import os
import logging
import json
from typing import Any, Optional
from urllib.parse import urlparse
from pydantic import SecretStr
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_core.utils.utils import secret_from_env
from langchain_google_genai import ChatGoogleGenerativeAI


logger = logging.getLogger(__name__)

# Default temperature from env or 0.0 for deterministic results
DEFAULT_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.0"))

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
    "google": ["google", "google-genai", "google-vertex", "google-ai"],
    "openai_compatible": ["openai_compatible", "openai-compatible"],
}


class UnsupportedModelError(Exception):
    """Raised when an unsupported model is requested."""
    pass


class UnsupportedProviderError(Exception):
    """Raised when an unsupported provider is requested."""
    pass


class ChatOpenRouter(ChatOpenAI):
    """
    Small wrapper to support OpenRouter-style configuration via api_key.
    Keeps compatibility with the previous sample.
    """
    api_key: Optional[SecretStr] = SecretStr(
        secret_from_env("OPENROUTER_API_KEY", default=None) or ""
    )

    @property
    def lc_secrets(self) -> dict[str, str]:
        return {"api_key": "OPENROUTER_API_KEY"}

    def __init__(self,
                 api_key: Optional[str] = None,
                 **kwargs):
        api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        super().__init__(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            **kwargs
        )


def _is_cloudflare_base_url(base_url: str) -> bool:
    """Return True for Cloudflare Workers AI and AI Gateway endpoints."""
    hostname = (urlparse(base_url).hostname or "").lower()
    return hostname == "api.cloudflare.com" or hostname.endswith(".ai.cloudflare.com")


def _trim_openai_endpoint_suffix(base_url: str) -> str:
    """Accept either an OpenAI SDK base URL or a pasted concrete endpoint URL."""
    endpoint_suffixes = (
        "/chat/completions",
        "/completions",
        "/embeddings",
        "/responses",
    )
    for suffix in endpoint_suffixes:
        if base_url.endswith(suffix):
            return base_url[: -len(suffix)]
    return base_url


def _normalize_openai_compatible_base_url(ai_base_url: str) -> str:
    """
    Normalize OpenAI-compatible base URLs for the OpenAI SDK.

    Most providers expect a `/v1` base path. Cloudflare is an exception: Workers AI
    uses `/client/v4/accounts/{account_id}/ai/v1`, while AI Gateway routes can have
    provider-specific path segments after `/v1` such as `/compat` or `/openai`.
    """
    base_url = _trim_openai_endpoint_suffix(ai_base_url.rstrip("/"))

    if _is_cloudflare_base_url(base_url):
        parsed = urlparse(base_url)
        if parsed.hostname == "api.cloudflare.com" and parsed.path.endswith("/ai"):
            return f"{base_url}/v1"
        return base_url

    if not base_url.endswith("/v1"):
        base_url += "/v1"
    return base_url


def _coerce_openai_compatible_text_content(content: Any) -> str:
    """Convert LangChain/OpenAI content blocks to text-only message content."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if block is None:
                continue
            if isinstance(block, str):
                parts.append(block)
                continue
            if isinstance(block, dict):
                if block.get("type") in {
                    "tool_use",
                    "function_call",
                    "thinking",
                    "reasoning_content",
                }:
                    continue
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
                    continue
                parts.append(json.dumps(block, ensure_ascii=False))
                continue
            parts.append(str(block))
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def _normalize_cloudflare_chat_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Adapt LangChain's OpenAI chat payload to Cloudflare Workers AI's stricter schema.

    Cloudflare's OpenAI-compatible chat endpoint currently rejects multi-part content
    arrays in `messages[*].content`. It also expects tool-calling assistant messages
    to use `content: null`, matching OpenAI's own tool-call transcript shape.
    """
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return payload

    normalized_messages = []
    for message in messages:
        if not isinstance(message, dict):
            normalized_messages.append(message)
            continue

        normalized = dict(message)
        has_tool_call = "tool_calls" in normalized or "function_call" in normalized

        if normalized.get("role") == "assistant" and has_tool_call:
            normalized["content"] = None
        else:
            normalized["content"] = _coerce_openai_compatible_text_content(
                normalized.get("content")
            )

        normalized_messages.append(normalized)

    return {**payload, "messages": normalized_messages}


class ChatCloudflareOpenAI(ChatOpenAI):
    """ChatOpenAI variant for Cloudflare's stricter OpenAI-compatible schema."""

    def _get_request_payload(self, *args, **kwargs):
        payload = super()._get_request_payload(*args, **kwargs)
        return _normalize_cloudflare_chat_payload(payload)


class LLMFactory:
    """
    Factory for creating LLM instances for different AI providers.
    
    Supported providers:
    - OPENROUTER: Access to multiple models via OpenRouter API (recommended)
    - OPENAI: Direct OpenAI API access (gpt-4o, gpt-4-turbo, etc.)
    - ANTHROPIC: Direct Anthropic API access (claude-3-opus, claude-3-sonnet, etc.)
    - GOOGLE: Direct Google AI API access (gemini-pro, gemini-1.5-pro, etc.)
    - OPENAI_COMPATIBLE: Any OpenAI-API-compatible endpoint (vLLM, Ollama, Cloudflare Workers AI, etc.)
    """

    @staticmethod
    def get_supported_providers() -> list[str]:
        """Return list of supported provider keys."""
        return ["OPENROUTER", "OPENAI", "ANTHROPIC", "GOOGLE", "OPENAI_COMPATIBLE"]

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
    def create_llm(ai_model: str, ai_provider: str, ai_api_key: str, temperature: Optional[float] = None, ai_base_url: Optional[str] = None, max_tokens: Optional[int] = None):
        """
        Create LLM instance for the specified provider.
        
        Args:
            ai_model: Model name/identifier
            ai_provider: Provider key (OPENROUTER, OPENAI, ANTHROPIC, GOOGLE, OPENAI_COMPATIBLE)
            ai_api_key: API key for the provider
            temperature: LLM temperature. If None, uses LLM_TEMPERATURE env var or 0.0.
                        0.0 = deterministic results (recommended for code review)
                        0.1-0.3 = more creative but less consistent
            ai_base_url: Base URL for OPENAI_COMPATIBLE provider (e.g. https://my-vllm.example.com)
            max_tokens: Maximum output tokens. If None, uses the provider default.
                        
        Raises:
            UnsupportedModelError: If the model is unsupported (e.g., Gemini thinking models)
            UnsupportedProviderError: If the provider is not supported
            
        Returns:
            LangChain chat model instance
        """
        if temperature is None:
            temperature = DEFAULT_TEMPERATURE
        
        # Normalize provider
        provider = LLMFactory._normalize_provider(ai_provider)
        
        # CRITICAL: Log the model being used for debugging
        logger.info(f"Creating LLM instance: provider={provider}, model={ai_model}, temperature={temperature}")
        
        # Check for unsupported Gemini thinking models (applies to all providers)
        LLMFactory._check_unsupported_gemini_model(ai_model)
        
        # model_kwargs to disable parallel tool calls at the API level
        # This prevents stdio transport concurrency issues with MCP servers
        model_kwargs = {
            "parallel_tool_calls": False
        }
        
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
                model_kwargs=model_kwargs,
                default_headers=extra_headers,
            )
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            return ChatOpenRouter(**kwargs)
        
        # Direct OpenAI provider
        if provider == "openai":
            kwargs = dict(
                api_key=ai_api_key,
                model=ai_model,
                temperature=temperature,
                model_kwargs=model_kwargs,
            )
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            return ChatOpenAI(**kwargs)
        
        # Direct Anthropic provider
        if provider == "anthropic":
            kwargs = dict(
                api_key=ai_api_key,
                model=ai_model,
                temperature=temperature,
                # Disable parallel tool use at the API level.
                # Anthropic uses tool_choice.disable_parallel_tool_use
                # instead of the OpenAI-style parallel_tool_calls param.
                # This prevents Claude from returning multiple tool_use blocks
                # in a single response, which would overwhelm the MCP stdio
                # transport's unicast outbound sink.
                model_kwargs={
                    "tool_choice": {
                        "type": "auto",
                        "disable_parallel_tool_use": True,
                    }
                },
            )
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            return ChatAnthropic(**kwargs)
        
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
                return ChatGoogleGenerativeAI(
                    google_api_key=ai_api_key,
                    model=ai_model,
                    temperature=temperature,
                    thinking_level=effective_thinking,
                )
            else:
                # Gemini 2.x models use thinking_budget parameter:
                #   0  = disable thinking (2.5 Flash) or use model minimum (2.5 Pro min=128)
                #   -1 = dynamic thinking (model decides)
                return ChatGoogleGenerativeAI(
                    google_api_key=ai_api_key,
                    model=ai_model,
                    temperature=temperature,
                    thinking_budget=0,
                )
        
        # OpenAI-compatible custom endpoint (vLLM, Ollama, Cloudflare Workers AI, etc.)
        if provider == "openai_compatible":
            if not ai_base_url:
                raise UnsupportedProviderError(
                    "OPENAI_COMPATIBLE provider requires a base URL. "
                    "Please configure the endpoint URL in your AI connection settings."
                )
            # SSRF validation — blocks private/reserved IPs unless ALLOW_PRIVATE_ENDPOINTS=true
            from llm.ssrf_safe_transport import (
                create_ssrf_safe_http_client,
                create_ssrf_safe_async_http_client,
            )
            http_client = create_ssrf_safe_http_client(ai_base_url)
            async_http_client = create_ssrf_safe_async_http_client(ai_base_url)

            base_url = _normalize_openai_compatible_base_url(ai_base_url)

            logger.info(f"Creating OPENAI_COMPATIBLE LLM: base_url={base_url}, model={ai_model}")
            kwargs = dict(
                api_key=ai_api_key,
                model=ai_model,
                base_url=base_url,
                temperature=temperature,
                model_kwargs=model_kwargs,
                http_client=http_client,
                http_async_client=async_http_client,
            )
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            chat_model = (
                ChatCloudflareOpenAI
                if _is_cloudflare_base_url(base_url)
                else ChatOpenAI
            )
            return chat_model(**kwargs)
        
        # Unknown provider - raise error with helpful message
        supported = ", ".join(LLMFactory.get_supported_providers())
        error_msg = f"Unsupported AI provider: '{ai_provider}'. Supported providers: {supported}"
        logger.error(error_msg)
        raise UnsupportedProviderError(error_msg)
