"""Request-aware command input estimation and provider invocation boundary."""
import json
import os
from typing import Any, Dict

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# Provider input and generated output are bounded independently.  Complete
# responses are still read atomically and parsed locally; the model is simply
# prevented from generating an arbitrarily large command response.
COMMAND_INPUT_TOKEN_TARGET = max(
    10_000,
    _env_int("COMMAND_INPUT_TOKEN_TARGET", 60_000),
)
COMMAND_CONTEXT_RESERVE_TOKENS = 20_000
COMMAND_ESTIMATOR_SAFETY_TOKENS = 256
COMMAND_MAX_OUTPUT_TOKENS = max(
    1_024,
    _env_int("COMMAND_MAX_OUTPUT_TOKENS", 16_384),
)


class CommandInputLimitError(RuntimeError):
    """Raised before a provider call whose complete input cannot fit safely."""


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "content"):
        return {
            "type": type(value).__name__,
            "content": _jsonable(getattr(value, "content")),
            "additional_kwargs": _jsonable(
                getattr(value, "additional_kwargs", None)
            ),
        }
    return str(value)


def _schema_declaration(schema: Any) -> Any:
    try:
        return schema.model_json_schema()
    except (AttributeError, TypeError, ValueError):
        return schema


def _estimated_command_input_tokens(
        messages: Any,
        *,
        tool_definitions: Any = None,
        response_schema: Any = None,
) -> int:
    """Conservatively estimate the complete rendered UTF-8 request."""
    payload: Dict[str, Any] = {"messages": _jsonable(messages)}
    if tool_definitions is not None:
        payload["tools"] = _jsonable(tool_definitions)
    if response_schema is not None:
        payload["response_schema"] = _jsonable(
            _schema_declaration(response_schema)
        )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    # Three bytes/token plus a fixed envelope is intentionally stricter than
    # the usual four-byte heuristic, especially for non-ASCII source text.
    return max(
        1,
        (len(encoded) + 2) // 3 + COMMAND_ESTIMATOR_SAFETY_TOKENS,
    )


def _command_input_token_budget(request: Any) -> int:
    """Derive a request-aware input target while reserving output/context room."""
    declared = getattr(request, "maxAllowedTokens", None)
    try:
        context_tokens = int(declared) if declared is not None else 200_000
    except (TypeError, ValueError):
        context_tokens = 200_000
    if context_tokens <= 0:
        context_tokens = 200_000
    if context_tokens > COMMAND_CONTEXT_RESERVE_TOKENS:
        safe_input = context_tokens - COMMAND_CONTEXT_RESERVE_TOKENS
    else:
        safe_input = max(1, context_tokens // 2)
    return min(COMMAND_INPUT_TOKEN_TARGET, safe_input)


def _assert_command_input_fits(
        messages: Any,
        token_budget: int,
        *,
        tool_definitions: Any = None,
        response_schema: Any = None,
        label: str = "command",
) -> None:
    estimated = _estimated_command_input_tokens(
        messages,
        tool_definitions=tool_definitions,
        response_schema=response_schema,
    )
    if estimated > token_budget:
        raise CommandInputLimitError(
            f"{label} complete input cannot fit the request-aware provider "
            f"target ({estimated} estimated tokens > {token_budget}); no "
            "evidence was truncated"
        )


class _CommandProviderInputGuard:
    """LangChain callback that checks the exact message/tool invocation."""

    raise_error = True
    run_inline = True
    ignore_llm = False
    ignore_chat_model = False

    def __init__(self, token_budget: int, response_schema: Any):
        self.token_budget = token_budget
        self.response_schema = response_schema

    def on_chat_model_start(
            self,
            serialized: Any,
            messages: Any,
            **kwargs: Any,
    ) -> None:
        _assert_command_input_fits(
            {"serialized": serialized, "messages": messages, "kwargs": kwargs},
            self.token_budget,
            response_schema=self.response_schema,
            label="MCP command turn",
        )

    def on_llm_start(
            self,
            serialized: Any,
            prompts: Any,
            **kwargs: Any,
    ) -> None:
        _assert_command_input_fits(
            {"serialized": serialized, "prompts": prompts, "kwargs": kwargs},
            self.token_budget,
            response_schema=self.response_schema,
            label="MCP command turn",
        )


def install_provider_input_guard(
        llm: Any,
        guard: _CommandProviderInputGuard,
) -> None:
    """Attach a callback inherited by LangChain bound-tool invocations."""
    callbacks = getattr(llm, "callbacks", None)
    if callbacks is None:
        try:
            llm.callbacks = [guard]
            return
        except Exception:
            pass
    elif isinstance(callbacks, (list, tuple)):
        try:
            llm.callbacks = [*callbacks, guard]
            return
        except Exception:
            pass
    elif hasattr(callbacks, "add_handler"):
        callbacks.add_handler(guard)
        return

    callback_manager = getattr(llm, "callback_manager", None)
    if callback_manager is not None and hasattr(callback_manager, "add_handler"):
        callback_manager.add_handler(guard)
        return
    raise CommandInputLimitError(
        "Cannot install the provider-boundary command input guard; refusing "
        "an unguarded MCP conversation"
    )


async def guarded_direct_invoke(
        llm: Any,
        prompt: str,
        input_token_budget: int,
        *,
        label: str,
        response_schema: Any = None,
) -> Any:
    _assert_command_input_fits(
        prompt,
        input_token_budget,
        response_schema=response_schema,
        label=label,
    )
    return await llm.ainvoke(prompt)
