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
    return {"extra_body": extra_body}


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

    limits = [requested_limit]
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
    return min(limits)
