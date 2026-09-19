import os
import logging
import json
import math
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse
from pydantic import SecretStr
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_core.utils.utils import secret_from_env
from langchain_google_genai import ChatGoogleGenerativeAI

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


def _normalize_openrouter_chat_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Use parameter names advertised by OpenRouter Chat Completions."""

    normalized = dict(payload)
    if "max_completion_tokens" in normalized:
        normalized["max_tokens"] = normalized.pop("max_completion_tokens")
    return normalized


def _openrouter_custom_extra_body(
    request_parameters: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Translate request-scoped OpenRouter controls into its JSON body."""
    if not request_parameters:
        return {}
    removed_output_limits: list[str] = []
    incoming = _without_output_token_limits(
        request_parameters,
        path="openrouter.request",
        removed=removed_output_limits,
    )
    if not isinstance(incoming, dict):
        logger.warning(
            "Ignoring OpenRouter custom parameters because they are not a map"
        )
        return {}

    nested_extra_body = incoming.get("extra_body")
    extra_body = (
        dict(nested_extra_body)
        if isinstance(nested_extra_body, dict)
        else {}
    )
    reserved = {
        *OPENAI_COMPATIBLE_RESERVED_DIRECT_PARAMS,
        "constructor_kwargs",
        "default_headers",
        "extra_body",
        "http_async_client",
        "http_client",
        "messages",
        "model_kwargs",
        "stream",
        "tool_choice",
        "tools",
    }
    extra_body.update({
        key: value
        for key, value in incoming.items()
        if key not in reserved
    })
    if removed_output_limits:
        logger.warning(
            "Ignoring OpenRouter output-length parameters so the selected "
            "finite stage cap remains authoritative: %s",
            sorted(removed_output_limits),
        )
    return _normalize_openrouter_chat_payload(extra_body)

OPENAI_COMPATIBLE_RESERVED_DIRECT_PARAMS = {
    "api_key",
    "base_url",
    "http_async_client",
    "http_client",
    "model",
    "model_name",
    "organization",
    "temperature",
}

OPENAI_COMPATIBLE_CONSTRUCTOR_PARAM_KEYS = {
    "default_headers",
    "default_query",
    "disabled_params",
    "extra_body",
    "max_retries",
    "request_timeout",
    "timeout",
}

OPENAI_COMPATIBLE_DIRECT_REQUEST_PARAM_KEYS = {
    "frequency_penalty",
    "presence_penalty",
    "reasoning_effort",
    "top_p",
}

OUTPUT_TOKEN_LIMIT_KEYS = {
    "generationmaxlength",
    "maxgeneratedtokens",
    "maxgenerationtokens",
    "maxgenlen",
    "maxlength",
    "maxcompletiontokens",
    "maxnewtokens",
    "maxoutputchars",
    "maxoutputcharacters",
    "maxoutputlength",
    "maxoutputtokens",
    "maxresponselength",
    "maxresponsetokens",
    "maxtokencount",
    "maxtokens",
    "maxtokenstosample",
    "numpredict",
    "outputlimit",
    "outputtokenlimit",
    "responsetokenlimit",
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

GOOGLE_VERTEX_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)


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

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Keep OpenRouter's canonical Chat Completions token field.

        ``ChatOpenAI`` rewrites ``max_tokens`` to the OpenAI-specific
        ``max_completion_tokens`` alias. OpenRouter accepts that alias for some
        routes, but its ``require_parameters`` capability filter matches the
        canonical ``max_tokens`` parameter advertised by model endpoints. The
        alias therefore produced a false "no compatible endpoint" 404 for
        bounded structured-output requests.
        """

        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        return _normalize_openrouter_chat_payload(payload)


def _parse_json_object(value: Optional[str], source_name: str) -> dict[str, Any]:
    if not value or not value.strip():
        return {}

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        logger.warning("Ignoring invalid JSON in %s: %s", source_name, exc)
        return {}

    if not isinstance(parsed, dict):
        logger.warning("Ignoring %s because it must be a JSON object", source_name)
        return {}

    return parsed


def _parse_env_json_object(*names: str) -> dict[str, Any]:
    for name in names:
        parsed = _parse_json_object(os.environ.get(name), name)
        if parsed:
            return parsed
    return {}


def _merge_dict(base: dict[str, Any], updates: Optional[dict[str, Any]]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in (updates or {}).items():
        if value is None:
            merged.pop(key, None)
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _without_output_token_limits(
    value: Any,
    *,
    path: str = "",
    removed: Optional[list[str]] = None,
) -> Any:
    """Remove provider aliases that could override the selected stage cap."""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            normalized_key = "".join(
                character
                for character in str(key).casefold()
                if character.isalnum()
            )
            if normalized_key in OUTPUT_TOKEN_LIMIT_KEYS:
                if removed is not None:
                    removed.append(child_path)
                continue
            cleaned[key] = _without_output_token_limits(
                item,
                path=child_path,
                removed=removed,
            )
        return cleaned
    if isinstance(value, list):
        return [
            _without_output_token_limits(
                item,
                path=f"{path}[{index}]",
                removed=removed,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, tuple):
        return tuple(
            _without_output_token_limits(
                item,
                path=f"{path}[{index}]",
                removed=removed,
            )
            for index, item in enumerate(value)
        )
    return value


def _split_openai_compatible_parameters(
    request_parameters: Optional[dict[str, Any]] = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """
    Split generic OpenAI-compatible tuning parameters into LangChain buckets.

    Known request keys are passed directly to ChatOpenAI, provider-specific
    unknowns go through model_kwargs, and constructor-level maps such as
    extra_body and default_headers are passed to ChatOpenAI itself.
    This keeps the provider policy generic for vLLM, Ollama, Cloudflare,
    OpenAI-compatible gateways, and self-hosted deployments.
    """
    removed_output_limits: list[str] = []
    env_custom = _without_output_token_limits(_parse_env_json_object(
        "OPENAI_COMPATIBLE_CUSTOM_PARAMS",
        "OPENAI_COMPATIBLE_CUSTOM_PARAMS_JSON",
    ), path="env.custom", removed=removed_output_limits)
    env_model_kwargs = _without_output_token_limits(_parse_env_json_object(
        "OPENAI_COMPATIBLE_MODEL_KWARGS",
        "OPENAI_COMPATIBLE_MODEL_KWARGS_JSON",
    ), path="env.model_kwargs", removed=removed_output_limits)
    env_extra_body = _without_output_token_limits(_parse_env_json_object(
        "OPENAI_COMPATIBLE_EXTRA_BODY",
        "OPENAI_COMPATIBLE_EXTRA_BODY_JSON",
    ), path="env.extra_body", removed=removed_output_limits)
    env_headers = _without_output_token_limits(_parse_env_json_object(
        "OPENAI_COMPATIBLE_DEFAULT_HEADERS",
        "OPENAI_COMPATIBLE_DEFAULT_HEADERS_JSON",
    ), path="env.default_headers", removed=removed_output_limits)
    env_constructor = _without_output_token_limits(_parse_env_json_object(
        "OPENAI_COMPATIBLE_CONSTRUCTOR_KWARGS",
        "OPENAI_COMPATIBLE_CONSTRUCTOR_KWARGS_JSON",
    ), path="env.constructor", removed=removed_output_limits)

    incoming = _without_output_token_limits(
        request_parameters or {},
        path="request",
        removed=removed_output_limits,
    )
    if not isinstance(incoming, dict):
        logger.warning("Ignoring OpenAI-compatible custom parameters because they are not a map")
        incoming = {}

    nested_model_kwargs = incoming.get("model_kwargs")
    if nested_model_kwargs is not None and not isinstance(nested_model_kwargs, dict):
        logger.warning("Ignoring aiCustomParameters.model_kwargs because it is not a map")
        nested_model_kwargs = {}

    constructor_kwargs = {}
    constructor_kwargs = _merge_dict(constructor_kwargs, env_constructor)
    if env_extra_body:
        constructor_kwargs["extra_body"] = _merge_dict(
            constructor_kwargs.get("extra_body", {}),
            env_extra_body,
        )
    if env_headers:
        constructor_kwargs["default_headers"] = _merge_dict(
            constructor_kwargs.get("default_headers", {}),
            env_headers,
        )

    model_kwargs = {}
    request_kwargs = {}
    env_nested_model_kwargs = env_custom.get("model_kwargs")
    if isinstance(env_nested_model_kwargs, dict):
        model_kwargs = _merge_dict(model_kwargs, env_nested_model_kwargs)

    for key, value in env_custom.items():
        if key in {"model_kwargs", "constructor_kwargs"}:
            continue
        if key in OPENAI_COMPATIBLE_CONSTRUCTOR_PARAM_KEYS:
            constructor_kwargs[key] = _merge_dict(
                constructor_kwargs.get(key, {}),
                value,
            ) if isinstance(value, dict) else value
        elif key in OPENAI_COMPATIBLE_RESERVED_DIRECT_PARAMS:
            logger.warning("Ignoring reserved OpenAI-compatible env custom parameter: %s", key)
        elif key in OPENAI_COMPATIBLE_DIRECT_REQUEST_PARAM_KEYS:
            request_kwargs[key] = value
        else:
            model_kwargs[key] = value

    if isinstance(env_custom.get("constructor_kwargs"), dict):
        constructor_kwargs = _merge_dict(constructor_kwargs, env_custom["constructor_kwargs"])

    model_kwargs = _merge_dict(model_kwargs, env_model_kwargs)

    for key, value in incoming.items():
        if key in {"model_kwargs", "constructor_kwargs"}:
            continue
        if key in OPENAI_COMPATIBLE_RESERVED_DIRECT_PARAMS:
            logger.warning("Ignoring reserved OpenAI-compatible custom parameter: %s", key)
            continue
        if key in OPENAI_COMPATIBLE_CONSTRUCTOR_PARAM_KEYS:
            constructor_kwargs[key] = _merge_dict(
                constructor_kwargs.get(key, {}),
                value,
            ) if isinstance(value, dict) else value
        elif key in OPENAI_COMPATIBLE_DIRECT_REQUEST_PARAM_KEYS:
            request_kwargs[key] = value
        else:
            model_kwargs[key] = value

    constructor_kwargs = _merge_dict(
        constructor_kwargs,
        incoming.get("constructor_kwargs") if isinstance(incoming.get("constructor_kwargs"), dict) else None,
    )
    model_kwargs = _merge_dict(model_kwargs, nested_model_kwargs)

    allowed_constructor_kwargs = {
        key: value
        for key, value in constructor_kwargs.items()
        if key in OPENAI_COMPATIBLE_CONSTRUCTOR_PARAM_KEYS and value is not None
    }
    extra_body = allowed_constructor_kwargs.get("extra_body")
    if isinstance(extra_body, dict):
        deduped_extra_body = {
            key: value
            for key, value in extra_body.items()
            if key not in request_kwargs
        }
        if len(deduped_extra_body) != len(extra_body):
            allowed_constructor_kwargs["extra_body"] = deduped_extra_body

    ignored_constructor_keys = sorted(set(constructor_kwargs) - set(allowed_constructor_kwargs))
    if ignored_constructor_keys:
        logger.warning(
            "Ignoring unsupported OpenAI-compatible constructor parameters: %s",
            ignored_constructor_keys,
        )

    if removed_output_limits:
        logger.warning(
            "Ignoring OpenAI-compatible output-length parameters so the "
            "selected finite stage cap remains authoritative: %s",
            sorted(removed_output_limits),
        )

    return model_kwargs, allowed_constructor_kwargs, request_kwargs, incoming


def _is_cloudflare_base_url(base_url: str) -> bool:
    """Return True for Cloudflare Workers AI and AI Gateway endpoints."""
    hostname = (urlparse(base_url).hostname or "").lower()
    return hostname == "api.cloudflare.com" or hostname.endswith(".ai.cloudflare.com")


def _trim_openai_endpoint_suffix(base_url: str) -> str:
    """Accept either an OpenAI SDK base URL or a pasted concrete endpoint URL."""
    endpoint_suffixes = (
        "/chat/completions",
        "/completions",
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
        if parsed.hostname == "api.cloudflare.com" and "/ai/run/" in parsed.path:
            ai_prefix = parsed.path.split("/ai/run/", 1)[0]
            return urlunparse(parsed._replace(path=f"{ai_prefix}/ai/v1", params="", query="", fragment=""))
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


_CLOUDFLARE_ROLE_BY_MESSAGE_TYPE = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    "tool": "tool",
    "function": "function",
}

_CLOUDFLARE_MESSAGE_KEYS = {
    "role",
    "content",
    "name",
    "tool_calls",
    "tool_call_id",
    "function_call",
}


def _cloudflare_message_to_dict(message: Any) -> Any:
    """Convert dict-like or LangChain message objects into chat message dicts."""
    if isinstance(message, dict):
        data = dict(message)
    else:
        data = None
        if hasattr(message, "model_dump"):
            try:
                data = message.model_dump(mode="json", exclude_none=True)
            except TypeError:
                data = message.model_dump()
            except Exception:
                data = None
        if not isinstance(data, dict) and hasattr(message, "dict"):
            try:
                data = message.dict()
            except Exception:
                data = None
        if not isinstance(data, dict):
            role = getattr(message, "role", None)
            message_type = getattr(message, "type", None)
            role = role or _CLOUDFLARE_ROLE_BY_MESSAGE_TYPE.get(str(message_type))
            content = getattr(message, "content", None)
            if not role and content is None:
                return message
            data = {"role": role, "content": content}
            for key in ("name", "tool_calls", "tool_call_id", "function_call"):
                value = getattr(message, key, None)
                if value:
                    data[key] = value

    message_type = data.get("type")
    if not data.get("role") and message_type:
        data["role"] = _CLOUDFLARE_ROLE_BY_MESSAGE_TYPE.get(str(message_type), str(message_type))

    return {
        key: value
        for key, value in data.items()
        if key in _CLOUDFLARE_MESSAGE_KEYS and value is not None
    }


def _normalize_cloudflare_chat_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Adapt LangChain's OpenAI chat payload to Cloudflare Workers AI's stricter schema.

    Cloudflare's OpenAI-compatible chat endpoint currently rejects multi-part content
    arrays in `messages[*].content`. It also expects tool-calling assistant messages
    to use `content: null`, matching OpenAI's own tool-call transcript shape.
    """
    payload = dict(payload)
    payload.pop("parallel_tool_calls", None)

    messages = payload.get("messages")
    if not isinstance(messages, (list, tuple)):
        return payload

    normalized_messages = []
    for message in messages:
        message = _cloudflare_message_to_dict(message)
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


def _strip_google_vertex_model_prefix(ai_model: str) -> str:
    """Accept common Vertex resource forms and return the bare model id."""
    model = ai_model.strip()
    prefixes = (
        "publishers/google/models/",
        "models/",
    )
    if "/publishers/google/models/" in model:
        model = model.rsplit("/publishers/google/models/", 1)[1]
    for prefix in prefixes:
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


def _parse_google_vertex_config(ai_base_url: Optional[str]) -> tuple[Optional[str], str]:
    """
    Parse Vertex project/location metadata from the existing aiBaseUrl field.

    Accepted values include:
    - project-id/global
    - project-id:global
    - projects/project-id/locations/global
    - a full Vertex API URL containing /projects/{project}/locations/{location}
    - JSON with project/project_id and location/region
    """
    project = (
        os.environ.get("GOOGLE_VERTEX_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCLOUD_PROJECT")
    )
    location = (
        os.environ.get("GOOGLE_VERTEX_LOCATION")
        or os.environ.get("GOOGLE_CLOUD_LOCATION")
        or "global"
    )

    if not ai_base_url or not ai_base_url.strip():
        return project, location

    value = ai_base_url.strip()

    if value.startswith("{"):
        data = json.loads(value)
        project = data.get("project") or data.get("project_id") or project
        location = data.get("location") or data.get("region") or location
        return project, location

    parsed_url = urlparse(value)
    if parsed_url.scheme and parsed_url.netloc:
        value = parsed_url.path.strip("/")

    parts = [part for part in value.strip("/").split("/") if part]
    if "projects" in parts and "locations" in parts:
        project_index = parts.index("projects") + 1
        location_index = parts.index("locations") + 1
        if project_index < len(parts):
            project = parts[project_index]
        if location_index < len(parts):
            location = parts[location_index]
        return project, location

    if "/" in value:
        project_part, location_part = value.split("/", 1)
        return project_part.strip() or project, location_part.strip() or location

    if ":" in value:
        project_part, location_part = value.split(":", 1)
        return project_part.strip() or project, location_part.strip() or location

    return value, location


def _build_google_vertex_credentials(ai_api_key: str) -> tuple[Any, Optional[str], Optional[str]]:
    """Build Vertex auth from service-account JSON, ADC, or an express API key."""
    credential_value = (ai_api_key or "").strip()
    if credential_value.lower() in {"adc", "application_default", "application-default"}:
        return None, None, None

    if credential_value.startswith("{"):
        from google.oauth2 import service_account

        service_account_info = json.loads(credential_value)
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=list(GOOGLE_VERTEX_SCOPES),
        )
        return credentials, service_account_info.get("project_id"), None

    return None, None, credential_value or None


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
            return ChatOpenRouter(**kwargs)
        
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
            return ChatOpenAI(**kwargs)
        
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
                return ChatGoogleGenerativeAI(**kwargs)
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
                return ChatGoogleGenerativeAI(**kwargs)
        
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
            return ChatGoogleGenerativeAI(**kwargs)

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
            return chat_model(**kwargs)
        
        # Unknown provider - raise error with helpful message
        supported = ", ".join(LLMFactory.get_supported_providers())
        error_msg = f"Unsupported AI provider: '{ai_provider}'. Supported providers: {supported}"
        logger.error(error_msg)
        raise UnsupportedProviderError(error_msg)
