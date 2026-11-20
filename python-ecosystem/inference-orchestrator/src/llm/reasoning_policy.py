"""Provider-safe reasoning controls for individual model invocations."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Optional

from utils.llm_delegate import llm_class_names, unwrap_llm_delegate


class ReasoningEffort(str, Enum):
    """OpenRouter's normalized reasoning-effort levels used by review calls."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def stage_one_direct_effort(llm: Any) -> ReasoningEffort:
    """Keep DeepSeek V4.1 direct completion out of repeated deliberation."""
    delegate = unwrap_llm_delegate(llm)
    name = getattr(delegate, "model_name", None) or getattr(delegate, "model", "")
    if "ChatOpenRouter" in llm_class_names(llm) and isinstance(name, str):
        if name.casefold().lstrip("~").split("/")[-1].startswith("deepseek-v4.1-flash"):
            return ReasoningEffort.NONE
    return ReasoningEffort.LOW


def reasoning_request_kwargs(llm: Any, effort: ReasoningEffort) -> dict[str, Any]:
    """Return per-invocation OpenRouter reasoning parameters.

    LangChain's ``reasoning`` model field selects the Responses API. CodeCrow's
    OpenRouter integration uses Chat Completions, so the normalized OpenRouter
    object must travel through ``extra_body`` on the individual invocation.
    Other providers are left untouched.
    """
    if "ChatOpenRouter" not in llm_class_names(llm):
        return {}

    delegate = unwrap_llm_delegate(llm)
    configured_body = getattr(delegate, "extra_body", None)
    extra_body = (
        dict(configured_body)
        if isinstance(configured_body, Mapping)
        else {}
    )
    # The semantic call site owns effort. Replace a configured reasoning object
    # so incompatible settings such as reasoning.max_tokens cannot travel next
    # to reasoning.effort. Other OpenRouter fields remain intact.
    extra_body["reasoning"] = {"effort": effort.value}
    if "reasoning_effort" in extra_body:
        extra_body["reasoning_effort"] = effort.value
    kwargs: dict[str, Any] = {"extra_body": extra_body}
    if getattr(delegate, "reasoning_effort", None) is not None:
        kwargs["reasoning_effort"] = effort.value
    return kwargs


def output_token_request_kwargs(
    llm: Any,
    max_output_tokens: Optional[int],
) -> dict[str, int]:
    """Return the provider's canonical per-request output-token parameter."""

    if (
        not isinstance(max_output_tokens, int)
        or isinstance(max_output_tokens, bool)
        or max_output_tokens <= 0
    ):
        return {}
    if "ChatGoogleGenerativeAI" in llm_class_names(llm):
        return {"max_output_tokens": max_output_tokens}
    return {"max_tokens": max_output_tokens}


def bounded_output_token_limit(
    llm: Any,
    requested_limit: Optional[int],
) -> Optional[int]:
    """Honor a request ceiling without raising a lower configured model cap."""

    if (
        not isinstance(requested_limit, int)
        or isinstance(requested_limit, bool)
        or requested_limit <= 0
    ):
        return None

    return min(requested_limit, _configured_output_token_limit(llm) or requested_limit)


def review_output_token_limit(llm: Any, default_limit: int = 16_384) -> int:
    """Use the configured budget; DeepSeek needs room for reasoning and JSON."""
    configured = _configured_output_token_limit(llm)
    if configured is not None:
        return configured
    delegate = unwrap_llm_delegate(llm)
    name = getattr(delegate, "model_name", None) or getattr(delegate, "model", "")
    if isinstance(name, str) and name.casefold().lstrip("~").split("/")[-1].startswith("deepseek-v4.1-flash"):
        return 131_072
    return default_limit


def _configured_output_token_limit(llm: Any) -> Optional[int]:
    limits = []
    delegate = unwrap_llm_delegate(llm)
    candidates = (llm,) if delegate is llm else (llm, delegate)
    for candidate in candidates:
        for attribute in ("max_tokens", "max_output_tokens"):
            configured = getattr(candidate, attribute, None)
            if (
                isinstance(configured, int)
                and not isinstance(configured, bool)
                and configured > 0
            ):
                limits.append(configured)
    return min(limits) if limits else None
