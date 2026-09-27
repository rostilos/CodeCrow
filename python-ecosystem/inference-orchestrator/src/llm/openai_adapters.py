"""Provider-specific OpenAI protocol payload and endpoint adapters."""
import json
import os
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

from langchain_openai import ChatOpenAI
from langchain_core.utils.utils import secret_from_env
from pydantic import SecretStr

from llm.openai_parameters import _normalize_openrouter_chat_payload


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


class ChatCloudflareOpenAI(ChatOpenAI):
    """ChatOpenAI variant for Cloudflare's stricter OpenAI-compatible schema."""

    def _get_request_payload(self, *args, **kwargs):
        payload = super()._get_request_payload(*args, **kwargs)
        return _normalize_cloudflare_chat_payload(payload)
