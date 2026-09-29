"""Normalize optional request controls for OpenAI-compatible providers."""
import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


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
    removed_output_limits: list[str] = []
    incoming = _without_output_token_limits(
        request_parameters or {},
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
    provider = extra_body.get("provider")
    if provider is None or isinstance(provider, dict):
        provider = dict(provider or {})
        selection_keys = [key for key in ("order", "sort", "only") if key in provider]
        if not selection_keys:
            # Same model and reasoning behavior, ordered by generation speed.
            # Explicit routing/privacy/quantization/fallback settings still win.
            provider["sort"] = "throughput"
        logger.info("OpenRouter provider routing: mode=%s explicit_selection_fields=%s",
                    "explicit" if selection_keys else "default_throughput", selection_keys)
        extra_body["provider"] = provider
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
