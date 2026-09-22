from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from llm.reasoning_policy import ReasoningEffort
from service.agent.json_utils import resolve_structured_output
from service.agent.structured_output import (
    StructuredOutputInvocation,
    invoke_structured_output,
    output_token_request_kwargs,
    response_diagnostics,
)


class _Payload(BaseModel):
    value: str


class ChatOpenRouter:
    def __init__(self, response, *, model_name, extra_body=None):
        self.response = response
        self.model_name = model_name
        self.extra_body = extra_body
        self.binding = None
        self.calls = []

    def with_structured_output(
        self,
        schema,
        *,
        include_raw=False,
        method="json_schema",
    ):
        self.binding = {
            "schema": schema,
            "include_raw": include_raw,
            "method": method,
        }
        return self

    async def ainvoke(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return self.response


class BindingAwareChatOpenRouter(ChatOpenRouter):
    """Modern LangChain-shaped double that accepts model request kwargs."""

    def with_structured_output(
        self,
        schema,
        *,
        include_raw=False,
        method="json_schema",
        **kwargs,
    ):
        self.binding = {
            "schema": schema,
            "include_raw": include_raw,
            "method": method,
            **kwargs,
        }
        return self


@pytest.mark.asyncio(loop_scope="function")
async def test_provider_request_options_are_bound_before_structured_runnable():
    llm = BindingAwareChatOpenRouter(
        _Payload(value="bounded"),
        model_name="deepseek/deepseek-v4-flash-0731",
    )

    invocation = await invoke_structured_output(
        llm,
        "prompt",
        _Payload,
        effort=ReasoningEffort.LOW,
        label="bound-provider-options",
        max_tokens=16_384,
    )

    assert invocation.parsed == _Payload(value="bounded")
    assert llm.binding["max_tokens"] == 16_384
    assert llm.binding["extra_body"] == {
        "reasoning": {"effort": "low"},
        "provider": {"require_parameters": True},
    }
    # LangChain's include_raw runnable does not forward these invocation kwargs
    # to its internal model branch, so they must no longer be call-time options.
    assert llm.calls == [("prompt", {})]


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("provider_name", "configured_field"),
    [
        ("google", "max_output_tokens"),
        ("anthropic", "max_tokens"),
    ],
)
async def test_provider_cap_is_applied_to_structured_model_copy(
    provider_name,
    configured_field,
):
    copies = []
    invocations = []

    class ProviderBase:
        def __init__(self, configured_limit=65_536):
            setattr(self, configured_field, configured_limit)

        def model_copy(self, update=None, **_kwargs):
            copies.append(dict(update or {}))
            return self.__class__((update or {})[configured_field])

        def with_structured_output(self, _schema, *, include_raw=False, **kwargs):
            invocations.append(("binding", include_raw, dict(kwargs), self))
            return self

        async def ainvoke(self, prompt, **kwargs):
            invocations.append(("invoke", prompt, dict(kwargs), self))
            return _Payload(value="capped")

    class ChatGoogleGenerativeAI(ProviderBase):
        pass

    class ChatAnthropic(ProviderBase):
        pass

    provider_type = (
        ChatGoogleGenerativeAI
        if provider_name == "google"
        else ChatAnthropic
    )
    llm = provider_type()

    invocation = await invoke_structured_output(
        llm,
        "prompt",
        _Payload,
        effort=ReasoningEffort.LOW,
        label=f"{provider_name}-cap",
        max_tokens=16_384,
    )

    assert invocation.parsed == _Payload(value="capped")
    assert copies == [{configured_field: 16_384}]
    binding = next(record for record in invocations if record[0] == "binding")
    assert binding[1] is True
    assert binding[2] == {}
    assert getattr(binding[3], configured_field) == 16_384
    invoke = next(record for record in invocations if record[0] == "invoke")
    assert invoke[2] == {}


@pytest.mark.asyncio(loop_scope="function")
async def test_google_cap_preserves_transparent_wrapper_model_copy():
    copies = []

    class ChatGoogleGenerativeAI:
        max_output_tokens = 65_536

        def model_copy(self, update=None, **_kwargs):
            clone = ChatGoogleGenerativeAI()
            clone.max_output_tokens = (update or {}).get(
                "max_output_tokens",
                self.max_output_tokens,
            )
            return clone

        def with_structured_output(self, _schema, *, include_raw=False):
            self.include_raw = include_raw
            return self

        async def ainvoke(self, _prompt, **_kwargs):
            return _Payload(value="wrapped")

    class TransparentWrapper:
        def __init__(self, delegate):
            self.delegate = delegate

        @property
        def __codecrow_delegate__(self):
            return self.delegate

        def model_copy(self, update=None, **_kwargs):
            copies.append(dict(update or {}))
            return TransparentWrapper(self.delegate.model_copy(update=update))

        def with_structured_output(self, schema, *, include_raw=False):
            return self.delegate.with_structured_output(
                schema,
                include_raw=include_raw,
            )

    invocation = await invoke_structured_output(
        TransparentWrapper(ChatGoogleGenerativeAI()),
        "prompt",
        _Payload,
        effort=ReasoningEffort.LOW,
        label="wrapped-google-cap",
        max_tokens=4096,
    )

    assert invocation.parsed == _Payload(value="wrapped")
    assert copies == [{"max_output_tokens": 4096}]


def test_output_token_request_kwargs_uses_google_canonical_name():
    class ChatGoogleGenerativeAI:
        pass

    assert output_token_request_kwargs(
        ChatGoogleGenerativeAI(),
        4096,
    ) == {"max_output_tokens": 4096}


@pytest.mark.asyncio(loop_scope="function")
async def test_deepseek_openrouter_uses_function_calling_and_compatible_route():
    parsed = _Payload(value="ok")
    llm = ChatOpenRouter(
        {"raw": None, "parsed": parsed, "parsing_error": None},
        model_name="~DeepSeek/DeepSeek-V4-Flash-0731:nitro",
        extra_body={"provider": {"order": ["DeepInfra"]}},
    )

    invocation = await invoke_structured_output(
        llm,
        "prompt",
        _Payload,
        effort=ReasoningEffort.HIGH,
        label="test",
    )

    assert invocation.parsed == parsed
    assert invocation.method == "function_calling"
    assert invocation.raw_included is True
    assert llm.binding == {
        "schema": _Payload,
        "include_raw": True,
        "method": "function_calling",
    }
    assert llm.calls[0][1] == {
        "extra_body": {
            "provider": {
                "order": ["DeepInfra"],
                "require_parameters": True,
            },
            "reasoning": {"effort": "high"},
        }
    }


@pytest.mark.asyncio(loop_scope="function")
async def test_other_openrouter_models_keep_json_schema_transport():
    llm = ChatOpenRouter(
        _Payload(value="ok"),
        model_name="other/model",
    )

    invocation = await invoke_structured_output(
        llm,
        "prompt",
        _Payload,
        effort=ReasoningEffort.LOW,
        label="test",
    )

    assert invocation.method == "json_schema"
    assert llm.binding["method"] == "json_schema"


@pytest.mark.asyncio(loop_scope="function")
async def test_one_argument_legacy_delegate_receives_request_options_at_invoke():
    class ChatOpenRouter:
        model_name = "deepseek/deepseek-v4-flash-0731"

        def __init__(self):
            self.calls = []

        def with_structured_output(self, schema):
            self.schema = schema
            return self

        async def ainvoke(self, prompt, **kwargs):
            self.calls.append((prompt, kwargs))
            return _Payload(value="legacy")

    delegate = ChatOpenRouter()

    invocation = await invoke_structured_output(
        delegate,
        "prompt",
        _Payload,
        effort=ReasoningEffort.NONE,
        label="legacy",
        max_tokens=16_384,
    )

    assert invocation.parsed == _Payload(value="legacy")
    assert invocation.method is None
    assert invocation.raw_included is False
    assert delegate.schema is _Payload
    assert delegate.calls[0][1]["max_tokens"] == 16_384
    assert "max_completion_tokens" not in delegate.calls[0][1]
    assert delegate.calls[0][1]["extra_body"]["provider"] == {
        "require_parameters": True,
    }


@pytest.mark.asyncio(loop_scope="function")
async def test_raw_tool_arguments_are_recovered_without_another_provider_call():
    raw = SimpleNamespace(
        content="",
        tool_calls=[{"args": {"value": "from-tool"}}],
    )
    invocation = StructuredOutputInvocation(
        parsed=None,
        raw=raw,
        parsing_error=ValueError("initial validation failed"),
        method="function_calling",
        raw_included=True,
    )

    result = await resolve_structured_output(
        invocation,
        _Payload,
        SimpleNamespace(),
    )

    assert result == _Payload(value="from-tool")


@pytest.mark.asyncio(loop_scope="function")
async def test_direct_mapping_result_is_normalized_through_schema():
    result = await resolve_structured_output(
        StructuredOutputInvocation(parsed={"value": "mapping"}),
        _Payload,
        SimpleNamespace(),
    )

    assert result == _Payload(value="mapping")


def test_response_diagnostics_are_content_free_and_provider_neutral():
    response = SimpleNamespace(
        content=None,
        tool_calls=[],
        response_metadata={
            "stop_reason": "max_tokens",
            "token_usage": {
                "completion_tokens": 50,
                "completion_tokens_details": {"reasoning_tokens": 40},
            },
        },
    )

    assert response_diagnostics(response) == {
        "content_chars": 0,
        "finish_reason": "max_tokens",
        "output_tokens": 50,
        "reasoning_tokens": 40,
        "tool_calls": 0,
    }
