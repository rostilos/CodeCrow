"""Focused tests for the shared MCP agent execution lifecycle."""

import asyncio
from dataclasses import dataclass, field, replace
from inspect import signature
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.structured_output import ToolStrategy
from mcp.types import Tool
from pydantic import BaseModel, Field

from llm.reasoning_policy import ReasoningEffort
from service.agent import (
    AgentExecutionError,
    AgentExecutionRequest,
    AgentExecutionService,
    AgentModelCallLimitError,
    AgentOutputEvent,
    AgentToolEvent,
    FinalResponseReserveMiddleware,
    InitialRequiredToolMiddleware,
    ModelRequestSettingsMiddleware,
    RecursiveMCPAgent,
)
class _Output(BaseModel):
    value: str


class _BatchOutput(BaseModel):
    reviews: list[_Output]


class _OptionalBatchOutput(BaseModel):
    reviews: list[_Output] | None


class _ScalarOutput(BaseModel):
    value: str


class _ReviewContextArguments(BaseModel):
    question: str = Field(description="What repository relationship to inspect")
    focusPaths: list[str] = Field(description="Paths that scope the review batch")
    detail: int = 2


class _FakeToolMessage:
    def __init__(self, content, *, artifact=None):
        self.content = content
        self.artifact = artifact


class _RuntimeTool:
    """Small async StructuredTool stand-in for shared-boundary tests."""

    def __init__(
            self,
            *,
            coroutine,
            name,
            description,
            args_schema,
            return_direct=False,
            response_format="content",
            callbacks=None,
            tags=None,
            metadata=None,
            handle_tool_error=False,
            handle_validation_error=False,
            verbose=False,
            **_kwargs,
    ):
        self.coroutine = coroutine
        self.name = name
        self.description = description
        self.args_schema = args_schema
        self.tool_call_schema = args_schema
        self.return_direct = return_direct
        self.response_format = response_format
        self.callbacks = callbacks
        self.tags = tags
        self.metadata = metadata
        self.handle_tool_error = handle_tool_error
        self.handle_validation_error = handle_validation_error
        self.verbose = verbose

    async def ainvoke(self, tool_input, config=None):
        is_tool_call = (
            isinstance(tool_input, dict)
            and tool_input.get("type") == "tool_call"
        )
        arguments = tool_input["args"] if is_tool_call else tool_input
        validated = self.args_schema.model_validate(arguments).model_dump()
        runtime_arguments = dict(validated)
        parameters = signature(self.coroutine).parameters
        if "callbacks" in parameters:
            runtime_arguments["callbacks"] = object()
        if "config" in parameters:
            runtime_arguments["config"] = config
        result = await self.coroutine(**runtime_arguments)
        if not is_tool_call:
            return result
        if self.response_format == "content_and_artifact":
            content, artifact = result
            return _FakeToolMessage(content, artifact=artifact)
        return _FakeToolMessage(result)


class _FakeStructuredTool:
    @classmethod
    def from_function(cls, **kwargs):
        return _RuntimeTool(**kwargs)


class ChatOpenRouter:
    """Small provider-shaped double for structured agent execution tests."""

    def __init__(self, response):
        self.response = response
        self.model_name = "deepseek/deepseek-v4-flash-0731"
        self.binding = None
        self.calls = []
        self.tool_bindings = []

    def bind_tools(self, tool_definitions):
        self.tool_bindings.append(list(tool_definitions))
        return self

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


class _FakeSystemMessage:
    def __init__(self, content):
        self.content = content

    def model_copy(self, *, update):
        return _FakeSystemMessage(update.get("content", self.content))


class _FakeAIMessage:
    def __init__(
            self,
            content,
            *,
            tool_calls=None,
            invalid_tool_calls=None,
            response_metadata=None,
            id=None,
    ):
        self.content = content
        self.tool_calls = list(tool_calls or [])
        self.invalid_tool_calls = list(invalid_tool_calls or [])
        self.response_metadata = dict(response_metadata or {})
        self.id = id

    def model_copy(self, *, update):
        values = {
            "content": self.content,
            "tool_calls": self.tool_calls,
            "invalid_tool_calls": self.invalid_tool_calls,
            "response_metadata": self.response_metadata,
            "id": self.id,
        }
        values.update(update)
        return _FakeAIMessage(**values)


class _FakeModelResponse:
    def __init__(self, *, result, structured_response=None):
        self.result = result
        self.structured_response = structured_response


@dataclass(frozen=True)
class _FakeRemoveMessage:
    id: str


@dataclass(frozen=True)
class _FakeModelRequest:
    state: dict
    tools: list
    tool_choice: object
    system_message: _FakeSystemMessage | None
    messages: list
    response_format: object | None = None
    model: object | None = None
    model_settings: dict = field(default_factory=dict)

    def override(self, **overrides):
        return replace(self, **overrides)


class _FakeSession:
    def __init__(self, *tool_names: str):
        self._tools = [
            Tool(
                name=name,
                description=f"{name} test tool",
                inputSchema={"type": "object", "properties": {}},
            )
            for name in tool_names
        ]
        self.connector = self
        self.tools = self._tools
        self.list_tools_calls = 0
        self.initialize_calls = 0

    async def initialize(self):
        self.initialize_calls += 1

    async def list_tools(self):
        self.list_tools_calls += 1
        return self._tools


class _FakeClient:
    def __init__(self, sessions=None):
        self.sessions = dict(sessions or {})
        self.create_calls = 0
        self.close_all_sessions = AsyncMock()

    def get_all_active_sessions(self):
        return self.sessions

    async def create_all_sessions(self):
        self.create_calls += 1
        await asyncio.sleep(0)
        self.sessions = {
            "vcs": _FakeSession("allowedTool", "blockedTool"),
        }
        return self.sessions


class _FakeAgent:
    stream_items = []
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.stream_call = None
        type(self).instances.append(self)

    async def stream(self, prompt, **kwargs):
        self.stream_call = (prompt, kwargs)
        await asyncio.sleep(0)
        for item in type(self).stream_items:
            if isinstance(item, Exception):
                raise item
            yield item


@pytest.fixture(autouse=True)
def _reset_fake_agent():
    _FakeAgent.instances = []
    _FakeAgent.stream_items = []


@pytest.mark.asyncio(loop_scope="function")
async def test_execute_filters_tools_and_collects_the_stream_without_closing_client():
    client = _FakeClient()
    action = SimpleNamespace(tool="allowedTool", tool_input={"path": "a.py"})
    output = _Output(value="done")
    _FakeAgent.stream_items = [(action, "file contents"), output]
    service = AgentExecutionService(
        llm=ChatOpenRouter(SimpleNamespace(content="unused")),
        client=client,
    )
    request = AgentExecutionRequest(
        prompt="review this",
        allowed_tool_names=frozenset({"allowedTool", "unknownTool"}),
        max_steps=7,
        output_schema=_Output,
        additional_instructions="Return structured output.",
        metadata={"batch": 3},
        recursion_limit=61,
        reasoning_effort=ReasoningEffort.LOW,
        max_output_tokens=16_384,
        initial_required_tool_name="allowedTool",
    )

    with patch(
        "service.agent.agent_execution_service.RecursiveMCPAgent",
        _FakeAgent,
    ):
        result = await service.execute(request)

    assert client.create_calls == 1
    assert client.close_all_sessions.await_count == 0
    assert service.available_tool_names == frozenset({
        "allowedTool",
        "blockedTool",
    })
    assert result.output is output
    assert len(result.tool_events) == 1
    assert result.tool_events[0].action is action
    assert result.tool_events[0].observation == "file contents"
    assert result.metadata == {"batch": 3}

    agent = _FakeAgent.instances[0]
    assert agent.kwargs["disallowed_tools"] == ["blockedTool"]
    assert [
        tool.name for tool in agent.kwargs["preloaded_tools"]
    ] == ["allowedTool"]
    assert agent.kwargs["memory_enabled"] is False
    assert agent.kwargs["max_steps"] == 7
    assert agent.kwargs["recursion_limit"] == 61
    assert agent.kwargs["output_schema"] is _Output
    assert agent.kwargs["additional_instructions"] == "Return structured output."
    assert agent.kwargs["initial_required_tool_name"] == "allowedTool"
    assert agent.kwargs["model_request_settings"] == {
        "extra_body": {"reasoning": {"effort": "low"}},
        "max_tokens": 16_384,
    }
    assert agent.stream_call == (
        "review this",
        {
            "max_steps": 7,
            "manage_connector": False,
            "output_schema": None,
        },
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_execute_deadline_cancels_agent_and_preserves_completed_tool_events():
    cancelled = asyncio.Event()
    action = SimpleNamespace(tool="allowedTool", tool_input={"path": "a.py"})

    class BlockingAfterToolAgent:
        def __init__(self, **_kwargs):
            pass

        async def stream(self, _prompt, **_kwargs):
            yield action, "file contents"
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    service = AgentExecutionService(
        llm=ChatOpenRouter(SimpleNamespace(content="unused")),
        client=_FakeClient(),
    )
    request = AgentExecutionRequest(
        prompt="review this",
        allowed_tool_names=frozenset({"allowedTool"}),
        max_steps=4,
        timeout_seconds=0.01,
        metadata={"stage": "stage_1"},
    )

    with patch(
        "service.agent.agent_execution_service.RecursiveMCPAgent",
        BlockingAfterToolAgent,
    ), pytest.raises(AgentExecutionError, match="0.01s deadline") as captured:
        await service.execute(request)

    assert cancelled.is_set()
    assert len(captured.value.tool_events) == 1
    assert captured.value.tool_events[0].action is action
    assert captured.value.tool_events[0].observation == "file contents"
    assert captured.value.metadata == {"stage": "stage_1"}


@pytest.mark.asyncio(loop_scope="function")
async def test_execute_preserves_inner_timeout_before_aggregate_deadline():
    action = SimpleNamespace(tool="allowedTool", tool_input={"path": "a.py"})

    class ProviderTimeoutAgent:
        def __init__(self, **_kwargs):
            pass

        async def stream(self, _prompt, **_kwargs):
            yield action, "file contents"
            raise TimeoutError("provider attempt timed out")

    service = AgentExecutionService(
        llm=ChatOpenRouter(SimpleNamespace(content="unused")),
        client=_FakeClient(),
    )
    request = AgentExecutionRequest(
        prompt="review this",
        allowed_tool_names=frozenset({"allowedTool"}),
        max_steps=4,
        timeout_seconds=60,
        metadata={"stage": "stage_1"},
    )

    with patch(
        "service.agent.agent_execution_service.RecursiveMCPAgent",
        ProviderTimeoutAgent,
    ), pytest.raises(
        AgentExecutionError,
        match="provider attempt timed out",
    ) as captured:
        await service.execute(request)

    assert "deadline" not in str(captured.value)
    assert isinstance(captured.value.__cause__, TimeoutError)
    assert str(captured.value.__cause__) == "provider attempt timed out"
    assert len(captured.value.tool_events) == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_agent_execution_does_not_serialize_independent_review_batches():
    release = asyncio.Event()
    both_started = asyncio.Event()
    state = {"active": 0, "maximum": 0, "started": 0}

    class BlockingAgent:
        def __init__(self, **_kwargs):
            pass

        async def stream(self, _prompt, **_kwargs):
            state["active"] += 1
            state["started"] += 1
            state["maximum"] = max(state["maximum"], state["active"])
            if state["started"] == 2:
                both_started.set()
            try:
                await release.wait()
                yield "done"
            finally:
                state["active"] -= 1

    request = AgentExecutionRequest(
        prompt="review this",
        allowed_tool_names=frozenset(),
        max_steps=1,
    )
    first_service = AgentExecutionService(llm=object(), client=_FakeClient())
    second_service = AgentExecutionService(llm=object(), client=_FakeClient())

    with patch(
        "service.agent.agent_execution_service.RecursiveMCPAgent",
        BlockingAgent,
    ):
        first = asyncio.create_task(first_service.execute(request))
        second = asyncio.create_task(second_service.execute(request))
        await asyncio.wait_for(both_started.wait(), timeout=1)

        assert state["active"] == 2
        assert state["started"] == 2

        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=1)

    assert state["maximum"] == 2
    assert state["started"] == 2


@pytest.mark.asyncio(loop_scope="function")
async def test_execute_rejects_initial_tool_outside_selected_inventory():
    service = AgentExecutionService(llm=object(), client=_FakeClient())

    with (
        patch(
            "service.agent.agent_execution_service.RecursiveMCPAgent",
            _FakeAgent,
        ),
        pytest.raises(ValueError, match="required agent tool is not available"),
    ):
        await service.execute(AgentExecutionRequest(
            prompt="review this",
            allowed_tool_names=frozenset({"allowedTool"}),
            initial_required_tool_name="blockedTool",
            max_steps=6,
        ))

    assert _FakeAgent.instances == []


@pytest.mark.asyncio(loop_scope="function")
async def test_request_host_bindings_hide_arguments_and_override_model_values():
    invocations = []

    async def explore_review_context(
            question: str,
            focusPaths: list[str],
            detail: int = 2,
            config=None,
    ):
        invocations.append({
            "question": question,
            "focusPaths": focusPaths,
            "detail": detail,
            "config": config,
        })
        return "repository context"

    tool = _RuntimeTool(
        coroutine=explore_review_context,
        name="exploreReviewContext",
        description="Explore exact review context.",
        args_schema=_ReviewContextArguments,
        return_direct=True,
        tags=["repository"],
        metadata={"source": "rag"},
    )
    service = AgentExecutionService(llm=object(), client=None)
    service._initialized = True
    service._available_tools = (tool,)
    service._available_tool_names = frozenset({tool.name})
    _FakeAgent.stream_items = ["done"]

    def patch_runtime_config(config, *, callbacks):
        nested = dict(config or {})
        nested["callbacks"] = callbacks
        return nested

    with (
        patch(
            "service.agent.agent_execution_service.RecursiveMCPAgent",
            _FakeAgent,
        ),
        patch(
            "service.agent.agent_execution_service.StructuredTool",
            _FakeStructuredTool,
        ),
        patch(
            "service.agent.agent_execution_service.patch_config",
            patch_runtime_config,
        ),
        patch(
            "service.agent.agent_execution_service.ToolMessage",
            _FakeToolMessage,
        ),
    ):
        await service.execute(AgentExecutionRequest(
            prompt="review the batch",
            allowed_tool_names=frozenset({tool.name}),
            max_steps=6,
            tool_argument_bindings={
                tool.name: {"focusPaths": ("src/actual.py",)},
            },
        ))

    wrapped_tool = _FakeAgent.instances[0].kwargs["preloaded_tools"][0]
    exposed_schema = wrapped_tool.tool_call_schema.model_json_schema()
    assert set(exposed_schema["properties"]) == {"question", "detail"}
    assert exposed_schema["properties"]["detail"]["default"] == 2
    assert wrapped_tool.name == tool.name
    assert wrapped_tool.description == tool.description
    assert wrapped_tool.return_direct is True
    assert wrapped_tool.response_format == tool.response_format
    assert wrapped_tool.tags == ["repository"]
    assert wrapped_tool.metadata == {"source": "rag"}

    with (
        patch(
            "service.agent.agent_execution_service.patch_config",
            patch_runtime_config,
        ),
        patch(
            "service.agent.agent_execution_service.ToolMessage",
            _FakeToolMessage,
        ),
    ):
        result = await wrapped_tool.ainvoke(
            {
                "question": "Find affected callers",
                # Even a direct caller bypassing the exposed schema cannot
                # replace the host-owned batch scope.
                "focusPaths": ["src/unrelated.py"],
            },
            config={
                "metadata": {"request": "review-7"},
                "tags": ["stage-1"],
                "configurable": {"tenant": "workspace-4"},
            },
        )

    assert result == "repository context"
    assert invocations[0]["question"] == "Find affected callers"
    assert invocations[0]["focusPaths"] == ["src/actual.py"]
    assert invocations[0]["detail"] == 2
    assert invocations[0]["config"]["metadata"] == {
        "request": "review-7",
    }
    assert invocations[0]["config"]["tags"] == ["stage-1"]
    assert invocations[0]["config"]["configurable"] == {
        "tenant": "workspace-4",
    }
    assert invocations[0]["config"]["callbacks"] is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_request_host_bindings_preserve_artifact_response_semantics():
    async def explore_review_context(
            question: str,
            focusPaths: list[str],
            detail: int = 2,
    ):
        return "context", {"focusPaths": focusPaths, "question": question}

    tool = _RuntimeTool(
        coroutine=explore_review_context,
        name="exploreReviewContext",
        description="Explore exact review context.",
        args_schema=_ReviewContextArguments,
        response_format="content_and_artifact",
    )
    service = AgentExecutionService(llm=object(), client=None)
    service._initialized = True
    service._available_tools = (tool,)
    service._available_tool_names = frozenset({tool.name})
    _FakeAgent.stream_items = ["done"]

    with (
        patch(
            "service.agent.agent_execution_service.RecursiveMCPAgent",
            _FakeAgent,
        ),
        patch(
            "service.agent.agent_execution_service.StructuredTool",
            _FakeStructuredTool,
        ),
        patch(
            "service.agent.agent_execution_service.patch_config",
            lambda config, *, callbacks: {
                **dict(config or {}),
                "callbacks": callbacks,
            },
        ),
        patch(
            "service.agent.agent_execution_service.ToolMessage",
            _FakeToolMessage,
        ),
    ):
        await service.execute(AgentExecutionRequest(
            prompt="review the batch",
            allowed_tool_names=frozenset({tool.name}),
            max_steps=6,
            tool_argument_bindings={
                tool.name: {"focusPaths": ("src/actual.py",)},
            },
        ))

    wrapped_tool = _FakeAgent.instances[0].kwargs["preloaded_tools"][0]
    with (
        patch(
            "service.agent.agent_execution_service.patch_config",
            lambda config, *, callbacks: {
                **dict(config or {}),
                "callbacks": callbacks,
            },
        ),
        patch(
            "service.agent.agent_execution_service.ToolMessage",
            _FakeToolMessage,
        ),
    ):
        result = await wrapped_tool.ainvoke({
            "type": "tool_call",
            "name": wrapped_tool.name,
            "id": "review-context-call",
            "args": {"question": "Find affected callers"},
        })

    assert isinstance(result, _FakeToolMessage)
    assert result.content == "context"
    assert result.artifact == {
        "focusPaths": ["src/actual.py"],
        "question": "Find affected callers",
    }


@pytest.mark.asyncio(loop_scope="function")
async def test_request_host_bindings_reject_missing_tool_or_argument_schema():
    async def explore_review_context(question: str, focusPaths: list[str]):
        return question, focusPaths

    tool = _RuntimeTool(
        coroutine=explore_review_context,
        name="exploreReviewContext",
        description="Explore exact review context.",
        args_schema=_ReviewContextArguments,
    )
    service = AgentExecutionService(llm=object(), client=None)
    service._initialized = True
    service._available_tools = (tool,)
    service._available_tool_names = frozenset({tool.name})

    with pytest.raises(ValueError, match="unavailable agent tool"):
        await service.execute(AgentExecutionRequest(
            prompt="review",
            allowed_tool_names=frozenset({tool.name}),
            max_steps=3,
            tool_argument_bindings={
                "missingTool": {"focusPaths": ["src/actual.py"]},
            },
        ))

    with pytest.raises(ValueError, match="does not define bound argument"):
        await service.execute(AgentExecutionRequest(
            prompt="review",
            allowed_tool_names=frozenset({tool.name}),
            max_steps=3,
            tool_argument_bindings={
                tool.name: {"missingArgument": ["src/actual.py"]},
            },
        ))


@pytest.mark.asyncio(loop_scope="function")
async def test_execute_preserves_partial_tool_events_on_later_failure():
    action = SimpleNamespace(tool="allowedTool", tool_input={"path": "a.py"})
    _FakeAgent.stream_items = [
        (action, "file contents"),
        RuntimeError("model failed after tool result"),
    ]
    service = AgentExecutionService(llm=object(), client=_FakeClient())

    with (
        patch(
            "service.agent.agent_execution_service.RecursiveMCPAgent",
            _FakeAgent,
        ),
        pytest.raises(AgentExecutionError) as captured,
    ):
        await service.execute(AgentExecutionRequest(
            prompt="review this",
            allowed_tool_names=frozenset({"allowedTool"}),
            max_steps=6,
            metadata={"batch": 7},
        ))

    error = captured.value
    assert isinstance(error.__cause__, RuntimeError)
    assert error.metadata == {"batch": 7}
    assert len(error.tool_events) == 1
    assert error.tool_events[0].action is action
    assert error.tool_events[0].observation == "file contents"


@pytest.mark.asyncio(loop_scope="function")
async def test_model_session_binds_once_and_reuses_provider_settings():
    session = _FakeSession("getBranchFileContent")
    client = _FakeClient({"vcs": session})
    llm = ChatOpenRouter(SimpleNamespace(content="verified"))
    service = AgentExecutionService(llm=llm, client=client)
    tool_definitions = [{
        "type": "function",
        "function": {"name": "getBranchFileContent"},
    }]

    model_session = service.create_model_session(
        tool_definitions=tool_definitions,
        reasoning_effort=ReasoningEffort.LOW,
        max_output_tokens=16_384,
    )
    first = await model_session.ainvoke([{"role": "user", "content": "one"}])
    second = await model_session.ainvoke([{"role": "user", "content": "two"}])

    assert first.content == "verified"
    assert second.content == "verified"
    assert llm.tool_bindings == [tool_definitions]
    assert [call[0] for call in llm.calls] == [
        [{"role": "user", "content": "one"}],
        [{"role": "user", "content": "two"}],
    ]
    assert [call[1] for call in llm.calls] == [{
        "extra_body": {"reasoning": {"effort": "low"}},
        "max_tokens": 16_384,
    }] * 2
    assert client.create_calls == 0
    assert session.list_tools_calls == 0


@pytest.mark.asyncio(loop_scope="function")
async def test_recursive_request_honors_lower_openrouter_model_cap():
    llm = ChatOpenRouter(SimpleNamespace(content="done"))
    llm.max_tokens = 4096
    service = AgentExecutionService(llm=llm, client=_FakeClient())
    _FakeAgent.stream_items = ["done"]

    with patch(
        "service.agent.agent_execution_service.RecursiveMCPAgent",
        _FakeAgent,
    ):
        await service.execute(AgentExecutionRequest(
            prompt="review",
            allowed_tool_names=frozenset({"allowedTool"}),
            max_steps=4,
            reasoning_effort=ReasoningEffort.LOW,
            max_output_tokens=16_384,
        ))

    assert _FakeAgent.instances[0].kwargs["model_request_settings"] == {
        "extra_body": {"reasoning": {"effort": "low"}},
        "max_tokens": 4096,
    }


@pytest.mark.asyncio(loop_scope="function")
async def test_recursive_google_request_uses_canonical_output_cap():
    class ChatGoogleGenerativeAI:
        max_output_tokens = 2048

    service = AgentExecutionService(
        llm=ChatGoogleGenerativeAI(),
        client=_FakeClient(),
    )
    _FakeAgent.stream_items = ["done"]

    with patch(
        "service.agent.agent_execution_service.RecursiveMCPAgent",
        _FakeAgent,
    ):
        await service.execute(AgentExecutionRequest(
            prompt="review",
            allowed_tool_names=frozenset({"allowedTool"}),
            max_steps=4,
            reasoning_effort=ReasoningEffort.LOW,
            max_output_tokens=16_384,
        ))

    assert _FakeAgent.instances[0].kwargs["model_request_settings"] == {
        "max_output_tokens": 2048,
    }


@pytest.mark.asyncio(loop_scope="function")
async def test_no_tool_structured_request_uses_one_provider_aware_call():
    session = _FakeSession("readFile", "searchCode")
    client = _FakeClient({"repository": session})
    output = _Output(value="local review")
    llm = ChatOpenRouter({
        "raw": None,
        "parsed": output,
        "parsing_error": None,
    })
    service = AgentExecutionService(llm=llm, client=client)

    with patch(
        "service.agent.agent_execution_service.RecursiveMCPAgent",
    ) as recursive_agent:
        result = await service.execute(AgentExecutionRequest(
            prompt="review locally",
            allowed_tool_names=frozenset(),
            max_steps=1,
            output_schema=_Output,
            additional_instructions="Return every requested file.",
            metadata={"phase": "local"},
            reasoning_effort=ReasoningEffort.LOW,
            max_output_tokens=16_384,
        ))

    recursive_agent.assert_not_called()
    assert result.output == output
    assert result.tool_events == ()
    assert result.metadata == {"phase": "local"}
    assert llm.binding == {
        "schema": _Output,
        "include_raw": True,
        "method": "function_calling",
    }
    assert len(llm.calls) == 1
    prompt, kwargs = llm.calls[0]
    assert prompt == [
        ("system", "Return every requested file."),
        ("human", "review locally"),
    ]
    assert kwargs == {
        "extra_body": {
            "reasoning": {"effort": "low"},
            "provider": {"require_parameters": True},
        },
        "max_tokens": 16_384,
    }
    assert session.list_tools_calls == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_no_tool_structured_request_recovers_raw_args_without_second_call():
    raw = SimpleNamespace(
        content="",
        tool_calls=[{"args": {"value": "recovered"}}],
    )
    llm = ChatOpenRouter({
        "raw": raw,
        "parsed": None,
        "parsing_error": ValueError("initial validation failed"),
    })
    service = AgentExecutionService(
        llm=llm,
        client=_FakeClient({"repository": _FakeSession("readFile")}),
    )

    with patch(
        "service.agent.agent_execution_service.RecursiveMCPAgent",
    ) as recursive_agent:
        result = await service.execute(AgentExecutionRequest(
            prompt="review locally",
            allowed_tool_names=frozenset(),
            max_steps=1,
            output_schema=_Output,
        ))

    recursive_agent.assert_not_called()
    assert result.output == _Output(value="recovered")
    assert result.tool_events == ()
    assert len(llm.calls) == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_no_tool_unstructured_request_keeps_recursive_agent_behavior():
    client = _FakeClient({"repository": _FakeSession("readFile")})
    _FakeAgent.stream_items = ["plain answer"]
    service = AgentExecutionService(llm=object(), client=client)

    with patch(
        "service.agent.agent_execution_service.RecursiveMCPAgent",
        _FakeAgent,
    ):
        result = await service.execute(AgentExecutionRequest(
            prompt="answer locally",
            allowed_tool_names=frozenset(),
            max_steps=1,
        ))

    assert result.output == "plain answer"
    assert result.tool_events == ()
    agent = _FakeAgent.instances[0]
    assert agent.kwargs["preloaded_tools"] == ()
    assert agent.kwargs["output_schema"] is None


@pytest.mark.asyncio(loop_scope="function")
async def test_concurrent_prompts_initialize_once_and_use_fresh_agents():
    client = _FakeClient()
    _FakeAgent.stream_items = ["done"]
    service = AgentExecutionService(llm=object(), client=client)

    with patch(
        "service.agent.agent_execution_service.RecursiveMCPAgent",
        _FakeAgent,
    ):
        results = await asyncio.gather(*(
            service.execute(AgentExecutionRequest(
                prompt=f"prompt-{index}",
                allowed_tool_names=frozenset({"allowedTool"}),
                max_steps=5,
            ))
            for index in range(8)
        ))

    assert client.create_calls == 1
    assert len(_FakeAgent.instances) == 8
    assert len({id(agent) for agent in _FakeAgent.instances}) == 8
    assert all(agent.kwargs["memory_enabled"] is False for agent in _FakeAgent.instances)
    assert [result.output for result in results] == ["done"] * 8


@pytest.mark.asyncio(loop_scope="function")
async def test_stream_exposes_typed_events_and_reuses_active_sessions():
    session = _FakeSession("allowedTool", "otherTool")
    client = _FakeClient({"vcs": session})
    action = SimpleNamespace(tool="allowedTool")
    _FakeAgent.stream_items = [(action, "observation"), "answer"]
    service = AgentExecutionService(llm=object(), client=client)
    request = AgentExecutionRequest(
        prompt="prompt",
        allowed_tool_names=frozenset({"allowedTool"}),
        max_steps=3,
        metadata={"flow": "test"},
    )

    with patch(
        "service.agent.agent_execution_service.RecursiveMCPAgent",
        _FakeAgent,
    ):
        events = [event async for event in service.stream(request)]

    assert client.create_calls == 0
    assert session.list_tools_calls == 1
    assert isinstance(events[0], AgentToolEvent)
    assert isinstance(events[1], AgentOutputEvent)
    assert events[0].metadata == {"flow": "test"}
    assert events[1].output == "answer"


@pytest.mark.asyncio(loop_scope="function")
async def test_request_agents_use_cached_tools_without_retouching_connectors():
    session = _FakeSession("readFile")
    client = _FakeClient({"repository": session})
    output = _Output(value="local review")
    llm = ChatOpenRouter({
        "raw": None,
        "parsed": output,
        "parsing_error": None,
    })
    service = AgentExecutionService(llm=llm, client=client)
    await service.initialize()
    assert session.list_tools_calls == 1

    async def fail_if_reinventoried():
        raise RuntimeError("connector must not be touched after initialization")

    session.list_tools = fail_if_reinventoried
    _FakeAgent.stream_items = [output]

    with patch(
        "service.agent.agent_execution_service.RecursiveMCPAgent",
        _FakeAgent,
    ):
        local = await service.execute(AgentExecutionRequest(
            prompt="review locally",
            allowed_tool_names=frozenset(),
            max_steps=1,
            output_schema=_Output,
        ))
        repository = await service.execute(AgentExecutionRequest(
            prompt="inspect repository",
            allowed_tool_names=frozenset({"readFile"}),
            max_steps=3,
            output_schema=_Output,
        ))

    assert local.output == _Output(value="local review")
    assert repository.output == _Output(value="local review")
    assert [
        tool.name
        for tool in _FakeAgent.instances[0].kwargs["preloaded_tools"]
    ] == ["readFile"]
    assert len(llm.calls) == 1
    assert session.list_tools_calls == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_execute_rejects_mcp_use_model_call_limit_as_final_output():
    client = _FakeClient()
    _FakeAgent.stream_items = [
        "Model call limits exceeded: run limit (12/12)",
    ]
    service = AgentExecutionService(llm=object(), client=client)

    with (
        patch(
            "service.agent.agent_execution_service.RecursiveMCPAgent",
            _FakeAgent,
        ),
        pytest.raises(
            AgentModelCallLimitError,
            match="before producing a final response",
        ),
    ):
        await service.execute(AgentExecutionRequest(
            prompt="prompt",
            allowed_tool_names=frozenset({"allowedTool"}),
            max_steps=12,
        ))


@pytest.mark.asyncio(loop_scope="function")
async def test_similar_user_text_is_not_treated_as_model_call_limit():
    client = _FakeClient()
    output = "Model call limits exceeded: this is quoted documentation"
    _FakeAgent.stream_items = [output]
    service = AgentExecutionService(llm=object(), client=client)

    with patch(
        "service.agent.agent_execution_service.RecursiveMCPAgent",
        _FakeAgent,
    ):
        result = await service.execute(AgentExecutionRequest(
            prompt="prompt",
            allowed_tool_names=frozenset({"allowedTool"}),
            max_steps=12,
        ))

    assert result.output == output


@pytest.mark.asyncio(loop_scope="function")
async def test_initial_required_tool_is_the_only_first_turn_choice_then_expires():
    middleware = InitialRequiredToolMiddleware(tool_name="getStructuralRelations")
    graph_tool = SimpleNamespace(name="getStructuralRelations")
    file_tool = SimpleNamespace(name="getReviewFileContent")
    first_request = _FakeModelRequest(
        state={"run_model_call_count": 0},
        tools=[graph_tool, file_tool],
        tool_choice=None,
        system_message=_FakeSystemMessage("review"),
        messages=["prompt"],
    )
    later_request = replace(
        first_request,
        state={"run_model_call_count": 1},
        messages=[
            "prompt",
            _FakeAIMessage(
                "",
                tool_calls=[{
                    "name": "getStructuralRelations",
                    "args": {},
                    "id": "required-call",
                    "type": "tool_call",
                }],
            ),
        ],
    )

    async def return_request(prepared_request):
        return prepared_request

    with patch(
        "service.agent.recursive_mcp_agent.AIMessage",
        _FakeAIMessage,
    ):
        first = await middleware.awrap_model_call(
            first_request,
            return_request,
        )
        later = await middleware.awrap_model_call(
            later_request,
            return_request,
        )

    assert first.tools == [graph_tool]
    assert first.tool_choice == "getStructuralRelations"
    assert later is later_request
    assert later.tools == [graph_tool, file_tool]
    assert later.tool_choice is None


@pytest.mark.asyncio(loop_scope="function")
async def test_required_tool_workflow_narrows_each_turn_in_order():
    middleware = InitialRequiredToolMiddleware(tool_names=(
        "getMinimalReviewContext",
        "getImpactRadius",
        "queryCodeGraph",
    ))
    tools = [
        SimpleNamespace(name=name)
        for name in (
            "getMinimalReviewContext",
            "getImpactRadius",
            "queryCodeGraph",
            "getReviewFileContent",
        )
    ]

    def call(name):
        return _FakeAIMessage("", tool_calls=[{
            "name": name,
            "args": {},
            "id": f"{name}-call",
            "type": "tool_call",
        }])

    async def return_request(prepared_request):
        return prepared_request

    first_request = _FakeModelRequest(
        state={"run_model_call_count": 0},
        tools=tools,
        tool_choice=None,
        system_message=_FakeSystemMessage("review"),
        messages=["prompt"],
    )
    second_request = replace(
        first_request,
        messages=["prompt", call("getMinimalReviewContext")],
    )
    third_request = replace(
        first_request,
        messages=[
            "prompt",
            call("getMinimalReviewContext"),
            call("getImpactRadius"),
        ],
    )
    completed_request = replace(
        first_request,
        messages=[
            "prompt",
            call("getMinimalReviewContext"),
            call("getImpactRadius"),
            call("queryCodeGraph"),
        ],
    )

    with patch(
        "service.agent.recursive_mcp_agent.AIMessage",
        _FakeAIMessage,
    ):
        first = await middleware.awrap_model_call(first_request, return_request)
        second = await middleware.awrap_model_call(second_request, return_request)
        third = await middleware.awrap_model_call(third_request, return_request)
        completed = await middleware.awrap_model_call(
            completed_request,
            return_request,
        )

    assert [tool.name for tool in first.tools] == ["getMinimalReviewContext"]
    assert [tool.name for tool in second.tools] == ["getImpactRadius"]
    assert [tool.name for tool in third.tools] == ["queryCodeGraph"]
    assert completed is completed_request
    assert completed.tools == tools


@pytest.mark.asyncio(loop_scope="function")
async def test_initial_required_tool_retries_wrong_single_call_before_execution():
    middleware = InitialRequiredToolMiddleware(
        tool_name="exploreReviewContext",
    )
    graph_tool = SimpleNamespace(name="exploreReviewContext")
    file_tool = SimpleNamespace(name="getReviewFileContent")
    request = _FakeModelRequest(
        state={"run_model_call_count": 0},
        tools=[graph_tool, file_tool],
        tool_choice=None,
        system_message=_FakeSystemMessage("review"),
        messages=["prompt"],
    )
    prepared_requests = []

    async def wrong_tool_response(prepared_request):
        prepared_requests.append(prepared_request)
        return _FakeModelResponse(result=[_FakeAIMessage(
            "",
            tool_calls=[{
                "name": "getReviewFileContent",
                "args": {"filePath": "src/unrelated.ts"},
                "id": "wrong-single-call",
                "type": "tool_call",
            }],
        )])

    with (
        patch(
            "service.agent.recursive_mcp_agent.AIMessage",
            _FakeAIMessage,
        ),
        patch(
            "service.agent.recursive_mcp_agent.ModelResponse",
            _FakeModelResponse,
        ),
        patch(
            "service.agent.recursive_mcp_agent.RemoveMessage",
            _FakeRemoveMessage,
        ),
    ):
        response = await middleware.awrap_model_call(
            request,
            wrong_tool_response,
        )
        decision = middleware.after_model(
            state={
                "run_model_call_count": 1,
                "messages": response.result,
            },
            runtime=None,
        )

    assert prepared_requests[0].tools == [graph_tool]
    assert prepared_requests[0].tool_choice == "exploreReviewContext"
    assert decision == {
        "messages": [_FakeRemoveMessage(id=response.result[0].id)],
        "jump_to": "model",
    }
    assert getattr(
        InitialRequiredToolMiddleware.after_model,
        "__can_jump_to__",
    ) == ["model"]


@pytest.mark.asyncio(loop_scope="function")
async def test_initial_required_tool_discards_parallel_response_with_wrong_name():
    middleware = InitialRequiredToolMiddleware(
        tool_name="exploreReviewContext",
    )
    graph_tool = SimpleNamespace(name="exploreReviewContext")
    file_tool = SimpleNamespace(name="getReviewFileContent")
    request = _FakeModelRequest(
        state={"run_model_call_count": 1},
        tools=[graph_tool, file_tool],
        tool_choice=None,
        system_message=_FakeSystemMessage("review"),
        # The rejected first response has already been removed by the graph.
        messages=["prompt"],
    )

    async def mixed_parallel_response(prepared_request):
        assert prepared_request.tools == [graph_tool]
        assert prepared_request.tool_choice == "exploreReviewContext"
        return _FakeModelResponse(result=[_FakeAIMessage(
            "",
            tool_calls=[
                {
                    "name": "exploreReviewContext",
                    "args": {"question": "dependencies"},
                    "id": "required-parallel-call",
                    "type": "tool_call",
                },
                {
                    "name": "getReviewFileContent",
                    "args": {"filePath": "src/unrelated.ts"},
                    "id": "wrong-parallel-call",
                    "type": "tool_call",
                },
            ],
        )])

    with (
        patch(
            "service.agent.recursive_mcp_agent.AIMessage",
            _FakeAIMessage,
        ),
        patch(
            "service.agent.recursive_mcp_agent.ModelResponse",
            _FakeModelResponse,
        ),
        patch(
            "service.agent.recursive_mcp_agent.RemoveMessage",
            _FakeRemoveMessage,
        ),
    ):
        response = await middleware.awrap_model_call(
            request,
            mixed_parallel_response,
        )
        decision = middleware.after_model(
            state={
                "run_model_call_count": 2,
                "messages": response.result,
            },
            runtime=None,
        )

    assert decision == {
        "messages": [_FakeRemoveMessage(id=response.result[0].id)],
        "jump_to": "model",
    }


@pytest.mark.asyncio(loop_scope="function")
async def test_initial_required_tool_keeps_openrouter_provider_preferences_and_capability_requirement():
    class ChatOpenRouter:
        extra_body = {
            "provider": {
                "order": ["model-provider"],
                "data_collection": "deny",
            },
            "model_flag": True,
        }

    settings = ModelRequestSettingsMiddleware(settings={
        "extra_body": {
            "reasoning": {"effort": "low"},
            "provider": {
                "order": ["preferred-provider"],
                "allow_fallbacks": False,
            },
        },
    })
    reserve = FinalResponseReserveMiddleware(
        exploration_model_calls=4,
        structured_output=True,
    )
    initial = InitialRequiredToolMiddleware(tool_name="exploreReviewContext")
    response_format = ToolStrategy(schema=_Output)
    graph_tool = SimpleNamespace(name="exploreReviewContext")
    file_tool = SimpleNamespace(name="getReviewFileContent")
    request = _FakeModelRequest(
        state={"run_model_call_count": 0},
        tools=[graph_tool, file_tool],
        tool_choice=None,
        system_message=_FakeSystemMessage("review"),
        messages=["prompt"],
        response_format=response_format,
        model=ChatOpenRouter(),
        model_settings={"temperature": 0},
    )

    async def apply_reserve(prepared_request):
        async def apply_initial(reserved_request):
            async def return_request(inner_request):
                return inner_request

            return await initial.awrap_model_call(
                reserved_request,
                return_request,
            )

        return await reserve.awrap_model_call(
            prepared_request,
            apply_initial,
        )

    with patch(
        "service.agent.recursive_mcp_agent.AIMessage",
        _FakeAIMessage,
    ):
        prepared = await settings.awrap_model_call(
            request,
            apply_reserve,
        )

    assert prepared.tools == [graph_tool]
    assert prepared.tool_choice == "exploreReviewContext"
    assert prepared.response_format is response_format
    assert prepared.model_settings == {
        "temperature": 0,
        "extra_body": {
            "reasoning": {"effort": "low"},
            "model_flag": True,
            "provider": {
                "order": ["preferred-provider"],
                "allow_fallbacks": False,
                "data_collection": "deny",
                "require_parameters": True,
            },
        },
    }
    assert request.model_settings == {"temperature": 0}


@pytest.mark.asyncio(loop_scope="function")
async def test_final_reserve_removes_tools_before_initial_choice_when_no_exploration_call():
    reserve = FinalResponseReserveMiddleware(
        exploration_model_calls=0,
        structured_output=True,
    )
    initial = InitialRequiredToolMiddleware(tool_name="getStructuralRelations")
    response_format = ToolStrategy(schema=_Output)
    request = _FakeModelRequest(
        state={"run_model_call_count": 0},
        tools=[SimpleNamespace(name="getStructuralRelations")],
        tool_choice=None,
        system_message=_FakeSystemMessage("review"),
        messages=["prompt"],
        response_format=response_format,
    )

    async def apply_initial(prepared_request):
        async def return_request(inner_request):
            return inner_request

        return await initial.awrap_model_call(
            prepared_request,
            return_request,
        )

    prepared = await reserve.awrap_model_call(request, apply_initial)

    assert prepared.tools == []
    assert prepared.tool_choice == "_Output"
    assert prepared.response_format.schema is _Output


@pytest.mark.asyncio(loop_scope="function")
async def test_last_structured_synthesis_disables_tools_and_requires_schema_now():
    middleware = FinalResponseReserveMiddleware(
        exploration_model_calls=4,
        structured_output=True,
    )
    response_format = ToolStrategy(schema=_Output)
    request = _FakeModelRequest(
        state={
            "run_model_call_count": 3,
            "agent_final_response_start_call": 2,
        },
        tools=["repository-tool"],
        tool_choice="auto",
        system_message=_FakeSystemMessage("local review"),
        messages=["local prompt"],
        response_format=response_format,
    )

    async def return_request(prepared_request):
        return prepared_request

    prepared = await middleware.awrap_model_call(request, return_request)

    assert prepared.tools == []
    assert prepared.tool_choice == "_Output"
    assert prepared.response_format is not response_format
    assert prepared.response_format.schema is _Output
    assert prepared.response_format.handle_errors is False
    assert "Return the final answer now" in prepared.system_message.content


@pytest.mark.asyncio(loop_scope="function")
async def test_structured_exploration_keeps_schema_available_for_direct_completion():
    middleware = FinalResponseReserveMiddleware(
        exploration_model_calls=3,
        structured_output=True,
    )
    response_format = ToolStrategy(schema=_Output)
    request = _FakeModelRequest(
        state={"run_model_call_count": 0},
        tools=["repository-tool"],
        tool_choice="auto",
        system_message=_FakeSystemMessage("repository review"),
        messages=["repository prompt"],
        response_format=response_format,
    )

    async def return_request(prepared_request):
        return prepared_request

    exploring = await middleware.awrap_model_call(request, return_request)

    assert exploring is request
    assert exploring.tools is request.tools
    assert exploring.response_format is response_format


@pytest.mark.asyncio(loop_scope="function")
async def test_final_response_reserve_keeps_one_schema_correction_call():
    middleware = FinalResponseReserveMiddleware(
        exploration_model_calls=4,
        structured_output=True,
    )
    transcript = [object(), object()]
    tools = [object(), object()]
    response_format = ToolStrategy(
        schema=_Output,
        tool_message_content="complete",
    )
    original_system_message = _FakeSystemMessage("Original instructions")
    exploring_request = _FakeModelRequest(
        state={"run_model_call_count": 3},
        tools=tools,
        tool_choice="auto",
        system_message=original_system_message,
        messages=transcript,
        response_format=response_format,
    )
    synthesis_request = replace(
        exploring_request,
        state={
            "run_model_call_count": 4,
            "agent_final_response_start_call": 4,
        },
    )
    correction_request = replace(
        exploring_request,
        state={
            "run_model_call_count": 5,
            "agent_final_response_start_call": 4,
        },
    )

    async def return_request(request):
        return request

    with patch(
        "service.agent.recursive_mcp_agent.SystemMessage",
        _FakeSystemMessage,
    ):
        exploring = await middleware.awrap_model_call(
            exploring_request,
            return_request,
        )
        synthesis = await middleware.awrap_model_call(
            synthesis_request,
            return_request,
        )
        correction = await middleware.awrap_model_call(
            correction_request,
            return_request,
        )

    assert exploring is exploring_request
    assert exploring.tools is tools
    assert exploring.messages is transcript
    assert exploring.response_format is response_format
    assert synthesis is not synthesis_request
    assert synthesis.tools == []
    assert synthesis.tool_choice == "_Output"
    assert synthesis.messages is transcript
    assert synthesis.state is synthesis_request.state
    assert synthesis.response_format is response_format
    assert synthesis.system_message.content.startswith("Original instructions")
    assert "Do not request more repository" in synthesis.system_message.content
    assert correction is not correction_request
    assert correction.tools == []
    assert correction.tool_choice == "_Output"
    assert correction.messages is transcript
    assert correction.state is correction_request.state
    assert correction.response_format is not response_format
    assert correction.response_format.schema is _Output
    assert correction.response_format.tool_message_content == "complete"
    assert correction.response_format.handle_errors is False
    assert correction_request.tools is tools
    assert correction_request.tool_choice == "auto"
    assert correction_request.system_message is original_system_message


@pytest.mark.asyncio(loop_scope="function")
async def test_structured_openrouter_exploration_preserves_endpoint_compatibility():
    class ChatOpenRouter:
        pass

    middleware = FinalResponseReserveMiddleware(
        exploration_model_calls=2,
        structured_output=True,
    )
    request = _FakeModelRequest(
        state={"run_model_call_count": 1},
        tools=["repository-tool"],
        tool_choice="auto",
        system_message=_FakeSystemMessage("system"),
        messages=["transcript"],
        response_format=ToolStrategy(schema=_Output),
        model=ChatOpenRouter(),
        model_settings={
            "temperature": 0,
            "extra_body": {
                "provider": {
                    "order": ["preferred-provider"],
                    "allow_fallbacks": False,
                },
                "trace": "review-7",
            },
        },
    )

    async def return_request(prepared_request):
        return prepared_request

    prepared = await middleware.awrap_model_call(request, return_request)

    assert prepared.tools == ["repository-tool"]
    assert prepared.tool_choice == "auto"
    assert prepared.response_format is request.response_format
    assert prepared.model_settings == {
        "temperature": 0,
        "extra_body": {
            "provider": {
                "order": ["preferred-provider"],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
            "trace": "review-7",
        },
    }
    assert request.model_settings == {
        "temperature": 0,
        "extra_body": {
            "provider": {
                "order": ["preferred-provider"],
                "allow_fallbacks": False,
            },
            "trace": "review-7",
        },
    }


def test_final_response_reserve_uses_exactly_one_missing_schema_retry():
    middleware = FinalResponseReserveMiddleware(
        exploration_model_calls=4,
        structured_output=True,
    )
    missing_after_synthesis = {
        "run_model_call_count": 5,
        "agent_final_response_start_call": 4,
        "messages": [_FakeAIMessage("")],
    }

    assert middleware.after_model(
        state=missing_after_synthesis,
        runtime=None,
    ) == {
        "jump_to": "model",
    }
    assert middleware.after_model(
        state={
            **missing_after_synthesis,
            "structured_response": _Output(value="done"),
        },
        runtime=None,
    ) is None
    assert middleware.after_model(
        state={**missing_after_synthesis, "run_model_call_count": 6},
        runtime=None,
    ) == {
        "jump_to": "end",
    }
    assert FinalResponseReserveMiddleware(
        exploration_model_calls=4,
    ).after_model(
        state=missing_after_synthesis,
        runtime=None,
    ) is None
    assert getattr(
        FinalResponseReserveMiddleware.after_model,
        "__can_jump_to__",
    ) == ["model", "end"]


def test_schema_retry_removes_only_noncompliant_free_form_response():
    middleware = FinalResponseReserveMiddleware(
        exploration_model_calls=4,
        structured_output=True,
    )
    free_form = _FakeAIMessage(
        "A prose answer that ignored the schema",
        id="unstructured-synthesis",
    )

    with (
        patch(
            "service.agent.recursive_mcp_agent.AIMessage",
            _FakeAIMessage,
        ),
        patch(
            "service.agent.recursive_mcp_agent.RemoveMessage",
            _FakeRemoveMessage,
        ),
    ):
        retry = middleware.after_model(
            state={
                "run_model_call_count": 5,
                "agent_final_response_start_call": 4,
                "messages": [free_form],
            },
            runtime=None,
        )

    assert retry == {
        "jump_to": "model",
        "messages": [_FakeRemoveMessage(id="unstructured-synthesis")],
    }


def test_final_response_reserve_ends_before_static_tool_node_can_execute_terminal_read():
    middleware = FinalResponseReserveMiddleware(
        exploration_model_calls=4,
        structured_output=True,
    )

    assert middleware.after_model(
        state={
            "run_model_call_count": 6,
            "agent_final_response_start_call": 4,
            "messages": [
                _FakeAIMessage(
                    "",
                    tool_calls=[{
                        "name": "getReviewFileContent",
                        "args": {"filePath": "src/stale-request.ts"},
                    }],
                ),
            ],
        },
        runtime=None,
    ) == {
        "jump_to": "end",
    }


def test_noncompliant_free_form_turn_is_removed_before_forced_recovery():
    middleware = FinalResponseReserveMiddleware(
        exploration_model_calls=4,
        structured_output=True,
    )

    with (
        patch(
            "service.agent.recursive_mcp_agent.AIMessage",
            _FakeAIMessage,
        ),
        patch(
            "service.agent.recursive_mcp_agent.RemoveMessage",
            _FakeRemoveMessage,
        ),
    ):
        assert middleware.after_model(
            state={
                "run_model_call_count": 2,
                "messages": [_FakeAIMessage(
                    "analysis without a schema response",
                    id="discarded-prose",
                )],
            },
            runtime=None,
        ) == {
            "agent_final_response_start_call": 2,
            "messages": [_FakeRemoveMessage(id="discarded-prose")],
            "jump_to": "model",
        }

        # A repository tool selected before the exploration allowance is spent
        # executes normally and is never removed.
        assert middleware.after_model(
            state={
                "run_model_call_count": 2,
                "messages": [
                    _FakeAIMessage(
                        "",
                        id="repository-read",
                        tool_calls=[{
                            "name": "getReviewFileContent",
                            "args": {"filePath": "changed.php"},
                        }],
                    ),
                ],
            },
            runtime=None,
        ) is None

        # The final allowed exploration turn may still request repository reads;
        # they execute before the already-marked recovery phase begins.
        assert middleware.after_model(
            state={
                "run_model_call_count": 4,
                "messages": [
                    _FakeAIMessage(
                        "",
                        id="last-repository-read",
                        tool_calls=[{
                            "name": "getReviewFileContent",
                            "args": {"filePath": "changed.php"},
                        }],
                    ),
                ],
            },
            runtime=None,
        ) == {
            "agent_final_response_start_call": 4,
        }


@pytest.mark.asyncio(loop_scope="function")
async def test_request_settings_reach_exploration_and_reserved_final_calls():
    settings = ModelRequestSettingsMiddleware(settings={
        "extra_body": {"reasoning": {"effort": "low"}},
        "max_tokens": 16_384,
    })
    reserve = FinalResponseReserveMiddleware(
        exploration_model_calls=2,
        structured_output=True,
    )
    response_format = ToolStrategy(schema=_Output)
    base_request = _FakeModelRequest(
        state={"run_model_call_count": 0},
        tools=["repository-tool"],
        tool_choice="auto",
        system_message=_FakeSystemMessage("system"),
        messages=["transcript"],
        response_format=response_format,
        model=type("ChatOpenRouter", (), {})(),
        model_settings={"temperature": 0},
    )

    async def return_request(prepared_request):
        return prepared_request

    async def apply_reserve(prepared_request):
        return await reserve.awrap_model_call(
            prepared_request,
            return_request,
        )

    exploration = await settings.awrap_model_call(
        base_request,
        apply_reserve,
    )
    final = await settings.awrap_model_call(
        replace(base_request, state={
            "run_model_call_count": 2,
            "agent_final_response_start_call": 2,
        }),
        apply_reserve,
    )

    expected_common = {
        "temperature": 0,
        "extra_body": {
            "reasoning": {"effort": "low"},
            "provider": {"require_parameters": True},
        },
        "max_tokens": 16_384,
    }
    assert exploration.model_settings == expected_common
    assert exploration.tools == ["repository-tool"]
    assert exploration.response_format is response_format
    assert final.model_settings == exploration.model_settings


@pytest.mark.asyncio(loop_scope="function")
async def test_final_response_reserve_does_not_assume_custom_openai_compatibility():
    class ChatOpenAI:
        openai_api_base = "https://llm.example.test/v1"

    middleware = FinalResponseReserveMiddleware(
        exploration_model_calls=2,
        structured_output=True,
    )
    request = _FakeModelRequest(
        state={"run_model_call_count": 1},
        tools=["repository-tool"],
        tool_choice="auto",
        system_message=_FakeSystemMessage("system"),
        messages=["transcript"],
        response_format=ToolStrategy(schema=_Output),
        model=ChatOpenAI(),
        model_settings={"temperature": 0},
    )

    async def return_request(prepared_request):
        return prepared_request

    prepared = await middleware.awrap_model_call(request, return_request)

    assert prepared.model_settings == {"temperature": 0}
    assert prepared.tools == ["repository-tool"]
    assert prepared.response_format is request.response_format


@pytest.mark.asyncio(loop_scope="function")
async def test_unstructured_final_response_still_uses_only_the_last_call():
    middleware = FinalResponseReserveMiddleware(exploration_model_calls=12)
    tools = [object()]
    request = _FakeModelRequest(
        state={"run_model_call_count": 10},
        tools=tools,
        tool_choice="auto",
        system_message=_FakeSystemMessage("system"),
        messages=[object()],
        response_format=None,
    )

    async def return_request(prepared_request):
        return prepared_request

    exploring = await middleware.awrap_model_call(
        request,
        return_request,
    )
    final = await middleware.awrap_model_call(
        replace(request, state={"run_model_call_count": 12}),
        return_request,
    )

    assert exploring is request
    assert exploring.tools is tools
    assert final.tools == []


@pytest.mark.asyncio(loop_scope="function")
async def test_final_response_reserve_is_concurrency_isolated_and_handles_missing_system_prompt():
    middleware = FinalResponseReserveMiddleware(exploration_model_calls=3)
    early_request = _FakeModelRequest(
        state={"run_model_call_count": 1},
        tools=["early-tool"],
        tool_choice="auto",
        system_message=_FakeSystemMessage([{"type": "text", "text": "base"}]),
        messages=["early transcript"],
    )
    final_request = _FakeModelRequest(
        state={"run_model_call_count": 3},
        tools=["final-tool"],
        tool_choice="auto",
        system_message=None,
        messages=["final transcript"],
    )

    async def return_after_yield(request):
        await asyncio.sleep(0)
        return request

    with patch(
        "service.agent.recursive_mcp_agent.SystemMessage",
        _FakeSystemMessage,
    ):
        early, final = await asyncio.gather(
            middleware.awrap_model_call(early_request, return_after_yield),
            middleware.awrap_model_call(final_request, return_after_yield),
        )

    assert early is early_request
    assert early.tools == ["early-tool"]
    assert final.tools == []
    assert final.messages == ["final transcript"]
    assert "Return the final answer now" in final.system_message.content


@pytest.mark.asyncio(loop_scope="function")
async def test_structured_exploration_completes_directly_and_exposes_validated_json():
    middleware = FinalResponseReserveMiddleware(
        exploration_model_calls=3,
        structured_output=True,
    )
    response_format = ToolStrategy(schema=_Output)
    request = _FakeModelRequest(
        state={"run_model_call_count": 1},
        tools=["repository-tool"],
        tool_choice="auto",
        system_message=_FakeSystemMessage("system"),
        messages=["tool transcript"],
        response_format=response_format,
    )
    structured = _Output(value="done")
    original_message = _FakeAIMessage(
        "",
        tool_calls=[{"name": "_Output", "args": {"value": "done"}}],
        response_metadata={"model": "test"},
    )

    async def structured_handler(prepared_request):
        assert prepared_request.tools == ["repository-tool"]
        assert prepared_request.response_format is response_format
        return _FakeModelResponse(
            result=[original_message, object()],
            structured_response=structured,
        )

    with (
        patch(
            "service.agent.recursive_mcp_agent.AIMessage",
            _FakeAIMessage,
        ),
        patch(
            "service.agent.recursive_mcp_agent.ModelResponse",
            _FakeModelResponse,
        ),
    ):
        response = await middleware.awrap_model_call(
            request,
            structured_handler,
        )

    assert response.structured_response is structured
    assert len(response.result) == 1
    visible_message = response.result[0]
    assert visible_message.content == '{"value":"done"}'
    assert visible_message.tool_calls == []
    assert visible_message.invalid_tool_calls == []
    assert visible_message.response_metadata == {"model": "test"}
    assert original_message.tool_calls != []
    assert middleware.after_model(
        state={
            "run_model_call_count": 2,
            "structured_response": structured,
            "messages": response.result,
        },
        runtime=None,
    ) is None


@pytest.mark.asyncio(loop_scope="function")
async def test_free_form_exploration_response_is_identified_then_removed():
    middleware = FinalResponseReserveMiddleware(
        exploration_model_calls=3,
        structured_output=True,
    )
    response_format = ToolStrategy(schema=_Output)
    request = _FakeModelRequest(
        state={"run_model_call_count": 1},
        tools=["repository-tool"],
        tool_choice="auto",
        system_message=_FakeSystemMessage("system"),
        messages=["tool transcript"],
        response_format=response_format,
    )

    async def free_form_handler(prepared_request):
        assert prepared_request.tools == ["repository-tool"]
        assert prepared_request.response_format is response_format
        return _FakeModelResponse(result=[_FakeAIMessage(
            "The conclusion was returned as prose.",
        )])

    with (
        patch(
            "service.agent.recursive_mcp_agent.AIMessage",
            _FakeAIMessage,
        ),
        patch(
            "service.agent.recursive_mcp_agent.ModelResponse",
            _FakeModelResponse,
        ),
        patch(
            "service.agent.recursive_mcp_agent.RemoveMessage",
            _FakeRemoveMessage,
        ),
    ):
        response = await middleware.awrap_model_call(
            request,
            free_form_handler,
        )
        decision = middleware.after_model(
            state={
                "run_model_call_count": 2,
                "messages": response.result,
            },
            runtime=None,
        )

    identified_message = response.result[0]
    assert identified_message.content == "The conclusion was returned as prose."
    assert identified_message.id.startswith("codecrow-unstructured-response-")
    assert decision == {
        "agent_final_response_start_call": 2,
        "messages": [_FakeRemoveMessage(id=identified_message.id)],
        "jump_to": "model",
    }


@pytest.mark.asyncio(loop_scope="function")
async def test_final_response_reserve_recovers_provider_root_array_locally():
    middleware = FinalResponseReserveMiddleware(
        exploration_model_calls=4,
        structured_output=True,
    )
    response_format = ToolStrategy(schema=_BatchOutput)
    request = _FakeModelRequest(
        state={
            "run_model_call_count": 4,
            "agent_final_response_start_call": 4,
        },
        tools=["repository-tool"],
        tool_choice="auto",
        system_message=_FakeSystemMessage("system"),
        messages=["repository transcript"],
        response_format=response_format,
    )
    provider_message = _FakeAIMessage(
        "```json\n[{\"value\": \"reviewed\"}]\n```",
        response_metadata={"model": "test"},
    )

    async def raw_json_handler(prepared_request):
        assert prepared_request.tools == []
        return _FakeModelResponse(result=[provider_message])

    with (
        patch(
            "service.agent.recursive_mcp_agent.AIMessage",
            _FakeAIMessage,
        ),
        patch(
            "service.agent.recursive_mcp_agent.ModelResponse",
            _FakeModelResponse,
        ),
    ):
        response = await middleware.awrap_model_call(
            request,
            raw_json_handler,
        )

    assert response.structured_response == _BatchOutput(
        reviews=[_Output(value="reviewed")],
    )
    assert response.result[0].content == (
        '{"reviews":[{"value":"reviewed"}]}'
    )
    assert response.result[0].tool_calls == []


def test_root_array_recovery_requires_one_explicitly_list_typed_field():
    candidate = [{"value": "reviewed"}]

    assert FinalResponseReserveMiddleware._validate_structured_candidate(
        _OptionalBatchOutput,
        candidate,
    ) == _OptionalBatchOutput(reviews=[_Output(value="reviewed")])
    with pytest.raises(ValueError):
        FinalResponseReserveMiddleware._validate_structured_candidate(
            _ScalarOutput,
            candidate,
        )


def test_recursive_agent_validates_terminal_stream_value_before_exposing_it():
    assert RecursiveMCPAgent._validated_stream_output(
        '[{"value":"reviewed"}]',
        _BatchOutput,
    ) == _BatchOutput(reviews=[_Output(value="reviewed")])

    with pytest.raises(ValueError, match="required _Output schema"):
        RecursiveMCPAgent._validated_stream_output(
            "No output generated",
            _Output,
        )


@pytest.mark.asyncio(loop_scope="function")
async def test_recursive_agent_initializes_from_preloaded_tools_without_client_access():
    read_tool = SimpleNamespace(name="readFile")
    graph = object()
    subject = SimpleNamespace(
        _preloaded_tools=(read_tool,),
        _tools=[],
        _agent_executor=None,
        _initialized=False,
        _create_system_message_from_tools=AsyncMock(),
        _create_agent=MagicMock(return_value=graph),
    )

    await RecursiveMCPAgent.initialize(subject)

    assert subject._tools == [read_tool]
    subject._create_system_message_from_tools.assert_awaited_once_with([read_tool])
    subject._create_agent.assert_called_once_with()
    assert subject._agent_executor is graph
    assert subject._initialized is True


def test_create_agent_adds_terminal_synthesis_after_exploration_ceiling():
    graph = MagicMock()
    configured_graph = object()
    graph.with_config.return_value = configured_graph
    create_agent = MagicMock(return_value=graph)
    limit_middleware = object()
    limit_factory = MagicMock(return_value=limit_middleware)
    tool_error_middleware = object()
    response_strategy = object()
    tool_strategy_factory = MagicMock(return_value=response_strategy)
    subject = SimpleNamespace(
        _tools=[SimpleNamespace(name="readFile")],
        _system_message=_FakeSystemMessage("system"),
        retry_on_error=True,
        max_steps=12,
        llm=object(),
        verbose=False,
        recursion_limit=24,
        callbacks=[],
        _output_schema=_Output,
        _initial_required_tool_name=None,
        _model_request_settings={"max_tokens": 16_384},
    )

    with (
        patch(
            "service.agent.recursive_mcp_agent.create_agent",
            create_agent,
        ),
        patch(
            "service.agent.recursive_mcp_agent.ModelCallLimitMiddleware",
            limit_factory,
        ),
        patch(
            "service.agent.recursive_mcp_agent.tool_error_handler",
            tool_error_middleware,
        ),
        patch(
            "service.agent.recursive_mcp_agent.BaseChatModel",
            object,
        ),
        patch(
            "service.agent.recursive_mcp_agent.ToolStrategy",
            tool_strategy_factory,
        ),
    ):
        result = RecursiveMCPAgent._create_agent(subject)

    assert result is configured_graph
    limit_factory.assert_called_once_with(
        run_limit=14,
        exit_behavior="error",
    )
    create_kwargs = create_agent.call_args.kwargs
    assert create_kwargs["tools"] is subject._tools
    assert create_kwargs["system_prompt"] is subject._system_message
    assert create_kwargs["response_format"] is response_strategy
    assert create_kwargs["middleware"][0] is tool_error_middleware
    assert isinstance(
        create_kwargs["middleware"][1],
        ModelRequestSettingsMiddleware,
    )
    assert create_kwargs["middleware"][1]._settings == {
        "max_tokens": 16_384,
    }
    assert isinstance(
        create_kwargs["middleware"][2],
        FinalResponseReserveMiddleware,
    )
    assert create_kwargs["middleware"][2]._exploration_model_calls == 12
    assert create_kwargs["middleware"][2].max_model_calls == 14
    assert create_kwargs["middleware"][2]._structured_output is True
    assert create_kwargs["middleware"][3] is limit_middleware
    tool_strategy_factory.assert_called_once_with(schema=_Output)
    graph.with_config.assert_called_once_with({"recursion_limit": 24})


@pytest.mark.asyncio(loop_scope="function")
async def test_optional_server_failure_keeps_required_server_tools_available():
    class SelectiveClient(_FakeClient):
        def __init__(self):
            super().__init__()
            self.session_calls = []

        async def create_session(self, server_name, auto_initialize=True):
            self.session_calls.append(server_name)
            if server_name == "rag":
                class FailingSession(_FakeSession):
                    async def initialize(self):
                        raise RuntimeError("RAG startup failed")

                session = FailingSession("searchRepositoryCode")
            else:
                session = _FakeSession("getBranchFileContent")
            self.sessions[server_name] = session
            if auto_initialize:
                await session.initialize()
            return session

        async def close_session(self, server_name):
            self.sessions.pop(server_name, None)

    client = SelectiveClient()
    service = AgentExecutionService(llm=object(), client=client)

    optional_errors = await service.initialize(
        required_server_names=("vcs",),
        optional_server_names=("rag",),
        session_timeout_seconds=1,
    )

    assert client.session_calls == ["vcs", "rag"]
    assert set(optional_errors) == {"rag"}
    assert isinstance(optional_errors["rag"], RuntimeError)
    assert "rag" not in client.sessions
    assert service.available_tool_names == frozenset({
        "getBranchFileContent",
    })


@pytest.mark.asyncio(loop_scope="function")
async def test_named_server_empty_tool_inventory_is_not_silent():
    required_client = _FakeClient({"vcs": _FakeSession()})
    required_service = AgentExecutionService(
        llm=object(),
        client=required_client,
    )

    with pytest.raises(RuntimeError, match="exposed no usable tools"):
        await required_service.initialize(
            required_server_names=("vcs",),
            session_timeout_seconds=1,
        )

    class CloseableClient(_FakeClient):
        async def close_session(self, server_name):
            self.sessions.pop(server_name, None)

    optional_client = CloseableClient({
        "vcs": _FakeSession("getBranchFileContent"),
        "rag": _FakeSession(),
    })
    optional_service = AgentExecutionService(
        llm=object(),
        client=optional_client,
    )

    optional_errors = await optional_service.initialize(
        required_server_names=("vcs",),
        optional_server_names=("rag",),
        session_timeout_seconds=1,
    )

    assert isinstance(optional_errors["rag"], RuntimeError)
    assert "rag" not in optional_client.sessions
    assert optional_service.available_tool_names == frozenset({
        "getBranchFileContent",
    })


def test_review_local_agent_module_remains_a_compatibility_export():
    from service.review.orchestrator.agents import (
        RecursiveMCPAgent as ReviewRecursiveMCPAgent,
    )

    assert ReviewRecursiveMCPAgent is RecursiveMCPAgent
