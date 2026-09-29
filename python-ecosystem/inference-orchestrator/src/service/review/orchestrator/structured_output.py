"""Provider-aware structured-output invocation and response recovery.

The review stages use this module instead of binding LangChain structured
output directly.  It keeps legacy/test-double call shapes working while giving
provider-backed models an inspectable raw response when schema parsing fails.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import inspect
import json
import logging
from typing import Any, Optional

from llm.reasoning_policy import (
    ReasoningEffort,
    output_token_request_kwargs,
    reasoning_request_kwargs,
)
from utils.llm_delegate import llm_class_names, unwrap_llm_delegate
from utils.llm_response import extract_llm_response_text

logger = logging.getLogger(__name__)


# OpenRouter advertises response-format support per endpoint, not only per
# model.  The current DeepSeek V4 Flash route is materially more reliable when
# its schema is expressed as a forced function call.  Other OpenRouter models
# retain the historical json_schema transport.
_OPENROUTER_FUNCTION_CALLING_MODELS = frozenset({
    "deepseek/deepseek-v4-flash-0731",
    "deepseek/deepseek-v4-flash-20260731",
    "deepseek/deepseek-v4-flash-latest",
})


@dataclass(frozen=True)
class StructuredOutputInvocation:
    """One provider response plus LangChain's schema parsing outcome."""

    parsed: Any
    raw: Any = None
    parsing_error: Optional[BaseException] = None
    method: Optional[str] = None
    raw_included: bool = False


def _model_identifier(llm: Any) -> str:
    delegate = unwrap_llm_delegate(llm)
    for attribute in ("model_name", "model"):
        value = getattr(delegate, attribute, None)
        if isinstance(value, str) and value.strip():
            normalized = value.strip().casefold().lstrip("~")
            # OpenRouter routing variants such as ``:nitro`` and ``:exacto``
            # do not change the model's structured-output capabilities.
            return normalized.split(":", 1)[0]
    return ""


def structured_output_method(llm: Any) -> Optional[str]:
    """Choose a transport without changing established non-OpenRouter paths."""

    if "ChatOpenRouter" not in llm_class_names(llm):
        return None
    if _model_identifier(llm) in _OPENROUTER_FUNCTION_CALLING_MODELS:
        return "function_calling"
    return "json_schema"


def structured_request_kwargs(
    llm: Any,
    effort: ReasoningEffort,
) -> dict[str, Any]:
    """Build invocation kwargs and require an OpenRouter-compatible endpoint."""

    kwargs = reasoning_request_kwargs(llm, effort)
    if "ChatOpenRouter" not in llm_class_names(llm):
        return kwargs

    extra_body = dict(kwargs.get("extra_body") or {})
    configured_provider = extra_body.get("provider")
    provider = (
        dict(configured_provider)
        if isinstance(configured_provider, Mapping)
        else {}
    )
    provider["require_parameters"] = True
    extra_body["provider"] = provider
    kwargs["extra_body"] = extra_body
    return kwargs


def _structured_output_model_and_token_kwargs(
    llm: Any,
    max_tokens: Optional[int],
) -> tuple[Any, dict[str, int]]:
    """Apply caps where each structured-output adapter actually consumes them.

    Google rejects extra ``with_structured_output`` kwargs and Anthropic accepts
    but ignores them. Their cap therefore has to live on a request-scoped model
    copy before either adapter constructs its structured runnable. OpenAI-family
    adapters forward kwargs into their bound model and keep the established path.
    """

    token_kwargs = output_token_request_kwargs(llm, max_tokens)
    class_names = llm_class_names(llm)
    if not token_kwargs or not class_names.intersection({
        "ChatAnthropic",
        "ChatGoogleGenerativeAI",
    }):
        return llm, token_kwargs

    model_copy = getattr(llm, "model_copy", None)
    if not callable(model_copy):
        # A legacy/provider test double may not support request-scoped model
        # copies. Keep structured output usable; the direct recovery still gets
        # the canonical per-request cap.
        return llm, {}
    return model_copy(update=token_kwargs), {}


def _supported_binding_options(binding: Any, options: dict[str, Any]) -> dict[str, Any]:
    """Pass new LangChain options only when a legacy double accepts them."""

    try:
        parameters = inspect.signature(binding).parameters.values()
    except (TypeError, ValueError):
        return options
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return options
    accepted = {parameter.name for parameter in parameters}
    return {key: value for key, value in options.items() if key in accepted}


def _compatible_binding_options(llm: Any, options: dict[str, Any]) -> dict[str, Any]:
    """Filter options against both transparent wrappers and their delegate."""

    compatible = _supported_binding_options(llm.with_structured_output, options)
    delegate = unwrap_llm_delegate(llm)
    if delegate is llm:
        return compatible
    delegate_binding = getattr(delegate, "with_structured_output", None)
    if delegate_binding is None:
        return compatible
    return _supported_binding_options(delegate_binding, compatible)


def bind_structured_output(
    llm: Any,
    schema: Any,
    *,
    request_kwargs: Optional[Mapping[str, Any]] = None,
) -> tuple[Any, Optional[str], bool, frozenset[str]]:
    """Bind a schema and provider options before LangChain wraps the model.

    ``include_raw=True`` returns a ``RunnableParallel`` in current LangChain.
    That runnable does not forward call-time kwargs to its model branch, so
    provider controls such as completion and reasoning limits must be bound by
    ``with_structured_output``.  Options unsupported by legacy implementations
    remain invocation kwargs for backwards compatibility.
    """

    method = structured_output_method(llm)
    requested_options: dict[str, Any] = {"include_raw": True}
    if method is not None:
        requested_options["method"] = method
    requested_options.update(dict(request_kwargs or {}))

    binding = llm.with_structured_output
    options = _compatible_binding_options(llm, requested_options)
    structured_llm = binding(schema, **options)
    applied_method = method if options.get("method") == method else None
    bound_request_keys = frozenset(request_kwargs or {}).intersection(options)
    return (
        structured_llm,
        applied_method,
        options.get("include_raw") is True,
        bound_request_keys,
    )


async def invoke_structured_output(
    llm: Any,
    prompt: Any,
    schema: Any,
    *,
    effort: ReasoningEffort,
    label: str,
    max_tokens: Optional[int] = None,
) -> StructuredOutputInvocation:
    """Invoke one structured call and retain its raw response on parse errors."""

    request_kwargs = structured_request_kwargs(llm, effort)
    structured_source, token_kwargs = _structured_output_model_and_token_kwargs(
        llm,
        max_tokens,
    )
    request_kwargs.update(token_kwargs)
    (
        structured_llm,
        method,
        raw_requested,
        bound_request_keys,
    ) = bind_structured_output(
        structured_source,
        schema,
        request_kwargs=request_kwargs,
    )
    invocation_kwargs = {
        key: value
        for key, value in request_kwargs.items()
        if key not in bound_request_keys
    }
    response = await structured_llm.ainvoke(
        prompt,
        **invocation_kwargs,
    )

    if (
        isinstance(response, Mapping)
        and {"raw", "parsed", "parsing_error"}.issubset(response)
    ):
        invocation = StructuredOutputInvocation(
            parsed=response.get("parsed"),
            raw=response.get("raw"),
            parsing_error=response.get("parsing_error"),
            method=method,
            raw_included=True,
        )
    else:
        # Backward compatibility for existing provider wrappers and local test
        # doubles that return the parsed Pydantic model directly.
        invocation = StructuredOutputInvocation(
            parsed=response,
            method=method,
            raw_included=False,
        )

    if invocation.parsed is None or invocation.parsing_error is not None:
        logger.warning(
            "Structured output was not parsed: label=%s schema=%s method=%s "
            "%s parsing_error=%s",
            label,
            getattr(schema, "__name__", str(schema)),
            method or "provider-default",
            format_response_diagnostics(invocation.raw),
            _safe_error(invocation.parsing_error),
        )
    return invocation


def _tool_argument_text(raw: Any) -> str:
    if raw is None:
        return ""

    tool_calls = getattr(raw, "tool_calls", None)
    if isinstance(tool_calls, list):
        for call in tool_calls:
            args = call.get("args") if isinstance(call, Mapping) else getattr(call, "args", None)
            if isinstance(args, str) and args.strip():
                return args
            if isinstance(args, Mapping):
                return json.dumps(args, ensure_ascii=False)

    additional = getattr(raw, "additional_kwargs", None)
    if isinstance(additional, Mapping):
        native_calls = additional.get("tool_calls")
        if isinstance(native_calls, list):
            for call in native_calls:
                if not isinstance(call, Mapping):
                    continue
                function = call.get("function")
                if not isinstance(function, Mapping):
                    continue
                arguments = function.get("arguments")
                if isinstance(arguments, str) and arguments.strip():
                    return arguments
                if isinstance(arguments, Mapping):
                    return json.dumps(arguments, ensure_ascii=False)
    return ""


def extract_structured_payload_text(invocation: StructuredOutputInvocation) -> str:
    """Recover function arguments or JSON content from the same raw response."""

    tool_arguments = _tool_argument_text(invocation.raw)
    if tool_arguments:
        return tool_arguments
    if invocation.raw is None:
        return ""
    return extract_llm_response_text(invocation.raw)


def _safe_error(error: Optional[BaseException]) -> str:
    if error is None:
        return "none"
    error_count = getattr(error, "error_count", None)
    if callable(error_count):
        try:
            return f"{type(error).__name__}(errors={error_count()})"
        except (TypeError, ValueError):
            pass
    # Provider exception messages may embed prompts, generated source, or
    # credentials. The type is sufficient here because request/response shape
    # diagnostics are logged separately without content.
    return type(error).__name__


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def response_diagnostics(response: Any) -> dict[str, Any]:
    """Return non-content diagnostics safe for normal production logs."""

    if response is None:
        return {
            "content_chars": 0,
            "finish_reason": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "tool_calls": 0,
        }

    raw_content = getattr(response, "content", "")
    content = "" if raw_content is None else extract_llm_response_text(response)
    metadata = _mapping(getattr(response, "response_metadata", None))
    generation_info = _mapping(getattr(response, "generation_info", None))
    additional = _mapping(getattr(response, "additional_kwargs", None))
    finish_reason = (
        metadata.get("finish_reason")
        or metadata.get("finishReason")
        or metadata.get("stop_reason")
        or generation_info.get("finish_reason")
        or generation_info.get("finishReason")
        or generation_info.get("stop_reason")
        or additional.get("finish_reason")
    )

    usage = _mapping(getattr(response, "usage_metadata", None))
    token_usage = _mapping(metadata.get("token_usage"))
    output_tokens = usage.get("output_tokens") or token_usage.get("completion_tokens")

    output_details = _mapping(usage.get("output_token_details"))
    completion_details = _mapping(token_usage.get("completion_tokens_details"))
    reasoning_tokens = (
        output_details.get("reasoning")
        or output_details.get("reasoning_tokens")
        or completion_details.get("reasoning_tokens")
    )
    tool_calls = getattr(response, "tool_calls", None)
    if isinstance(tool_calls, list):
        tool_call_count = len(tool_calls)
    else:
        native_tool_calls = additional.get("tool_calls")
        tool_call_count = (
            len(native_tool_calls)
            if isinstance(native_tool_calls, list)
            else 0
        )

    return {
        "content_chars": len(content),
        "finish_reason": finish_reason,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "tool_calls": tool_call_count,
    }


def format_response_diagnostics(response: Any) -> str:
    values = response_diagnostics(response)
    return " ".join(f"{key}={value}" for key, value in values.items())
