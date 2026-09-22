"""Shared construction and execution lifecycle for prompt-scoped MCP agents."""

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from copy import copy
from typing import Any, Generic
from uuid import uuid4

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig, patch_config
from langchain_core.tools import StructuredTool
from utils.mcp_runtime import configure_mcp_runtime

configure_mcp_runtime()

from mcp_use import MCPClient
from mcp_use.agents.adapters import LangChainAdapter
from pydantic import BaseModel, create_model

from llm.reasoning_policy import (
    ReasoningEffort,
    bounded_output_token_limit,
    output_token_request_kwargs,
    reasoning_request_kwargs,
)
from service.agent.models import (
    AgentExecutionError,
    AgentExecutionEvent,
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentOutputEvent,
    AgentOutputT,
    AgentToolEvent,
    reject_mcp_use_model_call_limit_output,
)
from service.agent.recursive_mcp_agent import RecursiveMCPAgent


def _model_request_settings(
        llm: Any,
        *,
        reasoning_effort: ReasoningEffort | None,
        max_output_tokens: int | None,
) -> dict[str, Any]:
    """Resolve semantic agent controls to provider-native request settings."""
    settings: dict[str, Any] = {}
    if reasoning_effort is not None:
        settings.update(reasoning_request_kwargs(llm, reasoning_effort))
    bounded_limit = bounded_output_token_limit(llm, max_output_tokens)
    settings.update(output_token_request_kwargs(llm, bounded_limit))
    return settings


def _tool_with_host_bound_arguments(
        tool: Any,
        bindings: Mapping[str, Any],
) -> StructuredTool:
    """Hide selected arguments from the model and inject host-owned values."""
    args_schema = getattr(tool, "args_schema", None)
    if (
        not isinstance(args_schema, type)
        or not issubclass(args_schema, BaseModel)
    ):
        raise TypeError(
            f"Agent tool {tool.name!r} must expose a Pydantic argument schema "
            "before host arguments can be bound"
        )

    bound_arguments = dict(bindings)
    missing_arguments = sorted(
        set(bound_arguments).difference(args_schema.model_fields)
    )
    if missing_arguments:
        raise ValueError(
            f"Agent tool {tool.name!r} does not define bound argument(s): "
            f"{', '.join(missing_arguments)}"
        )

    exposed_fields = {
        field_name: (field_info.annotation, copy(field_info))
        for field_name, field_info in args_schema.model_fields.items()
        if field_name not in bound_arguments
    }
    exposed_schema = create_model(
        f"{args_schema.__name__}HostBound",
        __config__=args_schema.model_config,
        **exposed_fields,
    )

    async def invoke_bound_tool(
            callbacks: Any = None,
            config: RunnableConfig = None,
            **model_arguments: Any,
    ) -> Any:
        merged_arguments = dict(model_arguments)
        merged_arguments.update(bound_arguments)
        nested_config = (
            patch_config(config, callbacks=callbacks)
            if callbacks is not None
            else config
        )

        if getattr(tool, "response_format", "content") == "content_and_artifact":
            result = await tool.ainvoke(
                {
                    "type": "tool_call",
                    "name": tool.name,
                    "id": f"host-bound-{uuid4().hex}",
                    "args": merged_arguments,
                },
                config=nested_config,
            )
            if isinstance(result, ToolMessage):
                return result.content, result.artifact
            return result

        return await tool.ainvoke(
            merged_arguments,
            config=nested_config,
        )

    return StructuredTool.from_function(
        coroutine=invoke_bound_tool,
        name=tool.name,
        description=getattr(tool, "description", "") or "",
        args_schema=exposed_schema,
        infer_schema=False,
        return_direct=getattr(tool, "return_direct", False),
        response_format=getattr(tool, "response_format", "content"),
        callbacks=getattr(tool, "callbacks", None),
        tags=getattr(tool, "tags", None),
        metadata=getattr(tool, "metadata", None),
        handle_tool_error=getattr(tool, "handle_tool_error", False),
        handle_validation_error=getattr(
            tool,
            "handle_validation_error",
            False,
        ),
        verbose=getattr(tool, "verbose", False),
    )


class AgentModelSession:
    """Provider-aware model binding for a caller-owned bounded tool loop.

    Some flows need host-side tool argument rewriting and evidence accounting
    that cannot be delegated to the generic recursive agent.  This session
    keeps model/tool binding and provider invocation settings on the shared
    construction path while the caller retains those flow-specific semantics.
    """

    def __init__(
            self,
            *,
            bound_model: Any,
            invocation_kwargs: Mapping[str, Any],
    ):
        self._bound_model = bound_model
        self._invocation_kwargs = dict(invocation_kwargs)

    async def ainvoke(self, messages: Sequence[Any]) -> Any:
        return await self._bound_model.ainvoke(
            messages,
            **self._invocation_kwargs,
        )


class AgentExecutionService(Generic[AgentOutputT]):
    """
    Run independent prompt agents over one caller-owned MCP client.

    The service initializes the client's shared sessions once, but deliberately
    does not close them. The command/review job that created the client remains
    responsible for its lifecycle. A caller-owned bounded local-tool loop may
    omit the MCP client and use only ``create_model_session``.
    """

    def __init__(self, *, llm: Any, client: MCPClient | None):
        self._llm = llm
        self._client = client
        self._initialization_lock = asyncio.Lock()
        self._initialized = False
        self._available_tool_names: frozenset[str] = frozenset()
        self._available_tools: tuple[Any, ...] = ()

    @property
    def available_tool_names(self) -> frozenset[str]:
        return self._available_tool_names

    def create_model_session(
            self,
            *,
            tool_definitions: Sequence[Any],
            reasoning_effort: ReasoningEffort,
            max_output_tokens: int | None = None,
    ) -> AgentModelSession:
        """Bind a model once for a caller-owned, flow-specific tool loop.

        This deliberately does not initialize or inventory MCP connectors. The
        caller's executor remains responsible for invoking its already-scoped
        tools; the shared service owns only common model-session construction.
        """
        return AgentModelSession(
            bound_model=self._llm.bind_tools(list(tool_definitions)),
            invocation_kwargs=_model_request_settings(
                self._llm,
                reasoning_effort=reasoning_effort,
                max_output_tokens=max_output_tokens,
            ),
        )

    async def initialize(
            self,
            *,
            required_server_names: Sequence[str] | None = None,
            optional_server_names: Sequence[str] = (),
            session_timeout_seconds: float | None = None,
    ) -> Mapping[str, Exception]:
        """Create shared MCP sessions and inventory their tools exactly once.

        ``required_server_names`` lets a caller initialize a known-good core
        server before optional enrichment servers. Optional startup errors are
        returned to the caller so they can be reported without disabling the
        sessions that did start.
        """
        if self._initialized:
            return {}

        async with self._initialization_lock:
            if self._initialized:
                return {}

            sessions = self._client.get_all_active_sessions()
            optional_errors: dict[str, Exception] = {}
            if required_server_names is None and not sessions:
                sessions = await self._client.create_all_sessions()
            elif required_server_names is not None:
                for server_name in required_server_names:
                    if server_name in sessions:
                        continue
                    await self._create_session(
                        server_name,
                        session_timeout_seconds,
                    )
                    sessions = self._client.get_all_active_sessions()

                for server_name in optional_server_names:
                    if server_name in sessions:
                        continue
                    try:
                        await self._create_session(
                            server_name,
                            session_timeout_seconds,
                        )
                    except Exception as exception:
                        optional_errors[server_name] = exception
                    sessions = self._client.get_all_active_sessions()

            adapter = LangChainAdapter()
            adapter._record_telemetry = False
            available_tools: list[Any] = []
            optional_names = set(optional_server_names)
            named_tool_servers = set(required_server_names or ()) | optional_names
            for server_name, session in list(sessions.items()):
                try:
                    tools = await self._await_with_timeout(
                        adapter.load_tools_for_connector(session.connector),
                        session_timeout_seconds,
                    )
                    if server_name in named_tool_servers and not tools:
                        raise RuntimeError(
                            f"MCP server {server_name!r} exposed no usable tools"
                        )
                except Exception as exception:
                    if server_name not in optional_names:
                        raise
                    optional_errors[server_name] = exception
                    await self._close_optional_session(
                        server_name,
                        session_timeout_seconds,
                    )
                    continue
                available_tools.extend(tools)

            self._available_tools = tuple(available_tools)
            self._available_tool_names = frozenset(
                tool.name
                for tool in available_tools
                if isinstance(getattr(tool, "name", None), str)
            )
            self._initialized = True
            return optional_errors

    async def _create_session(
            self,
            server_name: str,
            timeout_seconds: float | None,
    ) -> Any:
        # Register the connector with the client before initialization so a
        # timeout or cancellation still has a concrete session to disconnect.
        session = await self._client.create_session(
            server_name,
            auto_initialize=False,
        )
        if session is None:
            return None
        try:
            await self._await_with_timeout(
                session.initialize(),
                timeout_seconds,
            )
            return session
        except BaseException:
            await self._close_optional_session(
                server_name,
                timeout_seconds,
            )
            raise

    @staticmethod
    async def _await_with_timeout(
            awaitable: Any,
            timeout_seconds: float | None,
    ) -> Any:
        if timeout_seconds is None:
            return await awaitable
        return await asyncio.wait_for(awaitable, timeout=timeout_seconds)

    async def _close_optional_session(
            self,
            server_name: str,
            timeout_seconds: float | None,
    ) -> None:
        try:
            await self._await_with_timeout(
                self._client.close_session(server_name),
                timeout_seconds,
            )
        except Exception:
            # The startup error remains the useful diagnostic. Client teardown
            # at the owning request boundary gets another chance to close it.
            pass

    async def stream(
            self,
            request: AgentExecutionRequest[AgentOutputT],
    ) -> AsyncIterator[AgentExecutionEvent[AgentOutputT]]:
        """Stream one prompt agent within the caller's review concurrency."""
        await self.initialize()

        metadata: Mapping[str, Any] = dict(request.metadata)
        allowed_tool_names = set(request.allowed_tool_names)
        selected_tools = tuple(
            tool
            for tool in self._available_tools
            if getattr(tool, "name", None) in allowed_tool_names
        )
        selected_tool_names = {
            getattr(tool, "name", None) for tool in selected_tools
        }
        missing_binding_tools = sorted(
            set(request.tool_argument_bindings).difference(selected_tool_names)
        )
        if missing_binding_tools:
            raise ValueError(
                "Host argument bindings reference unavailable agent tool(s): "
                f"{', '.join(missing_binding_tools)}"
            )
        selected_tools = tuple(
            _tool_with_host_bound_arguments(
                tool,
                request.tool_argument_bindings[tool.name],
            )
            if tool.name in request.tool_argument_bindings
            else tool
            for tool in selected_tools
        )
        disallowed_tools = sorted(
            self._available_tool_names.difference(allowed_tool_names)
        )
        required_tool_names = tuple(request.required_tool_names)
        if not required_tool_names and request.initial_required_tool_name:
            required_tool_names = (request.initial_required_tool_name,)
        if (
            request.initial_required_tool_name is not None
            and required_tool_names
            and request.initial_required_tool_name != required_tool_names[0]
        ):
            raise ValueError(
                "Initial required agent tool must match the first required "
                "workflow tool"
            )
        missing_required_tools = [
            name
            for name in required_tool_names
            if name not in selected_tool_names
        ]
        if missing_required_tools:
            if (
                len(required_tool_names) == 1
                and required_tool_names[0]
                == request.initial_required_tool_name
            ):
                raise ValueError(
                    "Initial required agent tool is not available in this "
                    f"request: {required_tool_names[0]}"
                )
            raise ValueError(
                "Required agent tool workflow includes unavailable tool(s): "
                f"{', '.join(missing_required_tools)}"
            )
        if len(required_tool_names) > request.max_steps:
            raise ValueError(
                "Required agent tool workflow exceeds the request max_steps"
            )

        if not request.allowed_tool_names and request.output_schema is not None:
            output = await self._invoke_structured_without_tools(request)
            yield AgentOutputEvent(output=output, metadata=metadata)
            return

        agent = RecursiveMCPAgent(
            llm=self._llm,
            client=self._client,
            max_steps=request.max_steps,
            recursion_limit=request.recursion_limit,
            output_schema=request.output_schema,
            additional_instructions=request.additional_instructions,
            disallowed_tools=disallowed_tools,
            preloaded_tools=selected_tools,
            initial_required_tool_name=request.initial_required_tool_name,
            required_tool_names=required_tool_names,
            model_request_settings=_model_request_settings(
                self._llm,
                reasoning_effort=request.reasoning_effort,
                max_output_tokens=request.max_output_tokens,
            ),
            memory_enabled=False,
        )

        async for item in agent.stream(
            request.prompt,
            max_steps=request.max_steps,
            manage_connector=False,
            # The schema is bound inside the existing agent graph. mcp-use's
            # output_schema path would make a separate post-formatting model
            # call over only the raw answer and is deliberately bypassed.
            output_schema=None,
        ):
            # Keep the shared execution boundary safe even if a future agent
            # implementation bypasses RecursiveMCPAgent's upstream workaround.
            reject_mcp_use_model_call_limit_output(item)
            if isinstance(item, tuple) and len(item) == 2:
                action, observation = item
                yield AgentToolEvent(
                    action=action,
                    observation=observation,
                    metadata=metadata,
                )
            else:
                yield AgentOutputEvent(output=item, metadata=metadata)

    async def _invoke_structured_without_tools(
            self,
            request: AgentExecutionRequest[AgentOutputT],
    ) -> AgentOutputT:
        """Run an explicit no-tool request through one provider-aware call."""
        # Keep review-specific response recovery lazy so importing the shared
        # agent API does not eagerly initialize the review orchestration package.
        from llm.reasoning_policy import ReasoningEffort
        from service.agent.json_utils import (
            resolve_structured_output,
        )
        from service.agent.structured_output import (
            invoke_structured_output,
        )

        prompt: Any = request.prompt
        if request.additional_instructions:
            prompt = [
                ("system", request.additional_instructions),
                ("human", request.prompt),
            ]

        invocation = await invoke_structured_output(
            self._llm,
            prompt,
            request.output_schema,
            effort=request.reasoning_effort or ReasoningEffort.LOW,
            label="agent-no-tool-structured",
            max_tokens=bounded_output_token_limit(
                self._llm,
                request.max_output_tokens,
            ),
        )
        return await resolve_structured_output(
            invocation,
            request.output_schema,
            self._llm,
        )

    async def execute(
            self,
            request: AgentExecutionRequest[AgentOutputT],
    ) -> AgentExecutionResult[AgentOutputT]:
        """Collect ``stream`` without introducing a second execution path."""
        final_output: Any = None
        tool_events: list[AgentToolEvent] = []

        async def collect_events() -> None:
            nonlocal final_output
            async for event in self.stream(request):
                if isinstance(event, AgentToolEvent):
                    tool_events.append(event)
                else:
                    final_output = event.output

        deadline_expired = False
        try:
            if request.timeout_seconds is None:
                await collect_events()
            else:
                deadline = asyncio.timeout(request.timeout_seconds)
                try:
                    async with deadline:
                        await collect_events()
                except TimeoutError:
                    # A provider or MCP operation can raise TimeoutError before
                    # this aggregate deadline. Preserve that concrete failure;
                    # only the timeout context knows whether it cancelled us.
                    deadline_expired = deadline.expired()
                    raise
        except Exception as exception:
            if deadline_expired:
                message = (
                    "Agent execution exceeded its "
                    f"{request.timeout_seconds}s deadline"
                )
                if not tool_events:
                    raise TimeoutError(message) from exception
                message += (
                    f" after {len(tool_events)} completed tool call(s)"
                )
                raise AgentExecutionError(
                    message,
                    tool_events=tuple(tool_events),
                    metadata=request.metadata,
                ) from exception
            if not tool_events:
                raise
            raise AgentExecutionError(
                "Agent execution failed after "
                f"{len(tool_events)} completed tool call(s): {exception}",
                tool_events=tuple(tool_events),
                metadata=request.metadata,
            ) from exception

        return AgentExecutionResult(
            output=final_output,
            tool_events=tuple(tool_events),
            metadata=dict(request.metadata),
        )
