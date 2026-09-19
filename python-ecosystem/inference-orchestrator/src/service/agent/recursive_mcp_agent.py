"""MCP agent variant with bounded exploration and structured completion."""

import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from types import UnionType
from typing import Annotated, Any, NotRequired, Union, get_args, get_origin
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    hook_config,
)
from langchain.agents.middleware.types import (
    AgentState,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
)
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, RemoveMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.channels import UntrackedValue
from utils.mcp_runtime import configure_mcp_runtime

configure_mcp_runtime()

from mcp_use import MCPAgent
from mcp_use.agents.middleware import tool_error_handler
from pydantic import BaseModel

from service.agent.models import reject_mcp_use_model_call_limit_output
from utils.llm_delegate import llm_class_names, unwrap_llm_delegate
from utils.llm_response import extract_llm_response_text


logger = logging.getLogger(__name__)


# Avoid duplicate mcp_use log propagation when several prompt agents share a
# client. Keep the existing log format used by the inference orchestrator.
mcp_logger = logging.getLogger("mcp_use")
mcp_logger.propagate = False
if not mcp_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    mcp_logger.addHandler(handler)


_FINAL_RESPONSE_INSTRUCTION = (
    "Repository exploration is complete. Do not request more repository or "
    "retrieval tools. Return the final answer now through the required response "
    "format, following the original request's completeness requirements. Use "
    "only the conversation and tool results already available."
)
_INITIAL_REQUIRED_TOOL_METADATA_KEY = "codecrow_initial_required_tool"


class FinalResponseReserveState(AgentState):
    """Private per-run marker for the first terminal synthesis call."""

    agent_final_response_start_call: NotRequired[
        Annotated[int, UntrackedValue, PrivateStateAttr]
    ]


def agent_model_call_limit(
        exploration_model_calls: int,
        *,
        structured_output: bool,
) -> int:
    """Return the hard call ceiling without charging synthesis to exploration."""
    return exploration_model_calls + (2 if structured_output else 1)


def _supports_parallel_tool_control(model: Any) -> bool:
    """Return whether the selected adapter safely accepts the control."""

    class_names = llm_class_names(model)
    if "ChatAnthropic" in class_names:
        return True
    if "ChatOpenRouter" in class_names:
        # ChatOpenRouter accepts this OpenAI control, but an explicitly ordered
        # OpenRouter endpoint may not advertise it. Sending the control together
        # with provider.require_parameters=true would silently exclude that
        # endpoint (for example Cloudflare) before routing. The agent already
        # rejects mixed/out-of-inventory and competing schema calls locally.
        return False
    if "ChatOpenAI" not in class_names:
        return False

    # LLMFactory also uses ChatOpenAI for arbitrary OpenAI-compatible servers.
    # Those servers do not uniformly implement parallel_tool_calls. Apply the
    # control to OpenAI itself, while provider-specific subclasses above retain
    # their explicit behavior.
    delegate = unwrap_llm_delegate(model)
    base_url = getattr(delegate, "openai_api_base", None)
    if base_url is None:
        return True
    normalized_base_url = str(base_url).rstrip("/").casefold()
    return normalized_base_url in {
        "https://api.openai.com",
        "https://api.openai.com/v1",
    }


def _require_openrouter_request_parameters(
        model: Any,
        model_settings: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep OpenRouter agent turns on endpoints supporting their controls."""
    settings = dict(model_settings)
    if "ChatOpenRouter" not in llm_class_names(model):
        return settings

    delegate = unwrap_llm_delegate(model)
    configured_model_extra_body = getattr(delegate, "extra_body", None)
    extra_body = (
        dict(configured_model_extra_body)
        if isinstance(configured_model_extra_body, Mapping)
        else {}
    )
    configured_extra_body = settings.get("extra_body")
    request_extra_body = (
        dict(configured_extra_body)
        if isinstance(configured_extra_body, Mapping)
        else {}
    )
    configured_provider = extra_body.get("provider")
    provider = (
        dict(configured_provider)
        if isinstance(configured_provider, Mapping)
        else {}
    )
    request_provider = request_extra_body.pop("provider", None)
    if isinstance(request_provider, Mapping):
        provider.update(request_provider)
    extra_body.update(request_extra_body)
    provider["require_parameters"] = True
    extra_body["provider"] = provider
    settings["extra_body"] = extra_body
    return settings


def _tool_name(tool: Any) -> str | None:
    if isinstance(tool, dict):
        direct_name = tool.get("name")
        if isinstance(direct_name, str):
            return direct_name
        function = tool.get("function")
        if isinstance(function, dict):
            function_name = function.get("name")
            if isinstance(function_name, str):
                return function_name
        return None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


def _is_ai_message(message: Any) -> bool:
    """Keep the runtime type check tolerant of lightweight test adapters."""
    try:
        return isinstance(message, AIMessage)
    except TypeError:
        return False


def _single_structured_output_tool_name(
        response_format: ToolStrategy,
) -> str | None:
    """Return the one schema-tool name that can be required safely."""
    schema_specs = getattr(response_format, "schema_specs", None)
    if schema_specs is not None:
        try:
            specs = list(schema_specs)
        except TypeError:
            return None
        if len(specs) != 1:
            return None
        name = getattr(specs[0], "name", None)
        return name if isinstance(name, str) and name else None

    # LangChain 1.x exposes ``schema_specs``. Keep the small fallback for the
    # class-shaped test boundary and compatible ToolStrategy implementations.
    name = getattr(getattr(response_format, "schema", None), "__name__", None)
    return name if isinstance(name, str) and name else None


class InitialRequiredToolMiddleware(AgentMiddleware):
    """Require an exact ordered tool workflow before exposing all tools."""

    def __init__(
            self,
            *,
            tool_name: str | None = None,
            tool_names: Sequence[str] = (),
    ):
        super().__init__()
        resolved_tool_names = tuple(tool_names)
        if tool_name is not None:
            if resolved_tool_names and resolved_tool_names[0] != tool_name:
                raise ValueError(
                    "tool_name must match the first required tool name"
                )
            if not resolved_tool_names:
                resolved_tool_names = (tool_name,)
        if not resolved_tool_names:
            raise ValueError("At least one required tool name is required")
        if any(not name for name in resolved_tool_names):
            raise ValueError("Required tool names must be non-empty")
        self._tool_names = resolved_tool_names
        # Preserve the original single-tool attribute for compatible callers.
        self._tool_name = resolved_tool_names[0]

    def _next_required_tool(self, messages: Sequence[Any]) -> str | None:
        next_index = 0
        for observed_name in (
            _tool_name(tool_call)
            for message in messages
            for tool_call in (getattr(message, "tool_calls", None) or ())
        ):
            if (
                next_index < len(self._tool_names)
                and observed_name == self._tool_names[next_index]
            ):
                next_index += 1
        if next_index >= len(self._tool_names):
            return None
        return self._tool_names[next_index]

    def _prepare_request(self, request: ModelRequest) -> ModelRequest:
        required_tool_name = self._next_required_tool(request.messages)
        if required_tool_name is None:
            return request

        selected_tools = [
            tool
            for tool in request.tools
            if _tool_name(tool) == required_tool_name
        ]
        if not selected_tools:
            # AgentExecutionService validates the selected inventory before the
            # graph is built. Keep the middleware fail-open for direct users and
            # for a final-response middleware that has deliberately removed tools.
            return request

        logger.info(
            "MCP agent requiring tool workflow step %d/%d: %s",
            self._tool_names.index(required_tool_name) + 1,
            len(self._tool_names),
            required_tool_name,
        )
        return request.override(
            tools=selected_tools,
            tool_choice=required_tool_name,
        )

    def _mark_narrowed_response(
            self,
            response: Any,
            required_tool_name: str,
    ) -> Any:
        """Mark the AI response whose request had one exact tool inventory."""

        def mark_message(message: AIMessage) -> AIMessage:
            response_metadata = dict(message.response_metadata)
            response_metadata[_INITIAL_REQUIRED_TOOL_METADATA_KEY] = (
                required_tool_name
            )
            return message.model_copy(update={
                "id": message.id or f"codecrow-required-tool-{uuid4()}",
                "response_metadata": response_metadata,
            })

        if isinstance(response, AIMessage):
            return mark_message(response)

        result = list(getattr(response, "result", ()) or ())
        for index in range(len(result) - 1, -1, -1):
            if not isinstance(result[index], AIMessage):
                continue
            result[index] = mark_message(result[index])
            return ModelResponse(
                result=result,
                structured_response=getattr(
                    response,
                    "structured_response",
                    None,
                ),
            )
        return response

    def wrap_model_call(
            self,
            request: ModelRequest,
            handler: Callable[[ModelRequest], Any],
    ) -> Any:
        prepared_request = self._prepare_request(request)
        response = handler(prepared_request)
        if prepared_request is request:
            return response
        required_tool_name = self._next_required_tool(request.messages)
        return self._mark_narrowed_response(response, required_tool_name)

    async def awrap_model_call(
            self,
            request: ModelRequest,
            handler: Callable[[ModelRequest], Awaitable[Any]],
    ) -> Any:
        prepared_request = self._prepare_request(request)
        response = await handler(prepared_request)
        if prepared_request is request:
            return response
        required_tool_name = self._next_required_tool(request.messages)
        return self._mark_narrowed_response(response, required_tool_name)

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: Mapping[str, Any], runtime: Any) -> Any:
        """Retry a narrowed turn before its out-of-inventory call can run."""
        messages = state.get("messages") or ()
        latest_message = messages[-1] if messages else None
        response_metadata = getattr(
            latest_message,
            "response_metadata",
            None,
        )
        if (
            not isinstance(response_metadata, Mapping)
            or response_metadata.get(_INITIAL_REQUIRED_TOOL_METADATA_KEY)
            not in self._tool_names
        ):
            return None

        required_tool_name = response_metadata[
            _INITIAL_REQUIRED_TOOL_METADATA_KEY
        ]

        tool_calls = tuple(
            getattr(latest_message, "tool_calls", None) or ()
        )
        tool_names = tuple(_tool_name(tool_call) for tool_call in tool_calls)
        if tool_names and all(
            tool_name == required_tool_name
            for tool_name in tool_names
        ):
            return None

        logger.warning(
            "MCP provider ignored required tool inventory; retrying before "
            "tool execution: required=%s returned=%s",
            required_tool_name,
            list(tool_names),
        )
        return {
            "messages": [RemoveMessage(id=latest_message.id)],
            "jump_to": "model",
        }


class ModelRequestSettingsMiddleware(AgentMiddleware):
    """Apply one request's provider settings to every recursive model turn."""

    def __init__(self, *, settings: Mapping[str, Any]):
        super().__init__()
        self._settings = dict(settings)

    def _prepare_request(self, request: ModelRequest) -> ModelRequest:
        model_settings = dict(request.model_settings)
        for key, value in self._settings.items():
            if (
                isinstance(value, Mapping)
                and isinstance(model_settings.get(key), Mapping)
            ):
                merged_value = dict(model_settings[key])
                merged_value.update(value)
                model_settings[key] = merged_value
            else:
                model_settings[key] = value
        return request.override(model_settings=(
            _require_openrouter_request_parameters(
                request.model,
                model_settings,
            )
        ))

    def wrap_model_call(
            self,
            request: ModelRequest,
            handler: Callable[[ModelRequest], Any],
    ) -> Any:
        return handler(self._prepare_request(request))

    async def awrap_model_call(
            self,
            request: ModelRequest,
            handler: Callable[[ModelRequest], Awaitable[Any]],
    ) -> Any:
        return await handler(self._prepare_request(request))


class FinalResponseReserveMiddleware(AgentMiddleware):
    """Allow direct structured completion with bounded terminal recovery."""

    state_schema = FinalResponseReserveState

    def __init__(
            self,
            *,
            exploration_model_calls: int,
            structured_output: bool = False,
    ):
        super().__init__()
        self._exploration_model_calls = exploration_model_calls
        self._structured_output = structured_output
        self._max_model_calls = agent_model_call_limit(
            exploration_model_calls,
            structured_output=structured_output,
        )

    @property
    def max_model_calls(self) -> int:
        return self._max_model_calls

    @staticmethod
    def _state_synthesis_start(state: Mapping[str, Any]) -> int | None:
        value = state.get("agent_final_response_start_call")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _prepare_request(self, request: ModelRequest) -> ModelRequest:
        completed_calls = request.state.get("run_model_call_count", 0)
        synthesis_start = self._state_synthesis_start(request.state)
        response_format = request.response_format
        model_settings = _require_openrouter_request_parameters(
            request.model,
            request.model_settings,
        )
        if (
            isinstance(response_format, ToolStrategy)
            and _supports_parallel_tool_control(request.model)
        ):
            # One schema tool call is one terminal response. Keep repository
            # tools available for exploration, but do not let a provider mix a
            # repository read with the response schema or emit competing schema
            # calls in the same model turn.
            model_settings["parallel_tool_calls"] = False

        in_synthesis = (
            synthesis_start is not None
            or completed_calls >= self._exploration_model_calls
        )
        if not in_synthesis:
            # Keep ToolStrategy available alongside repository tools so the
            # model's terminal reasoning turn can return the structured answer
            # directly. The reserved tools-disabled phase below is only a
            # ceiling/noncompliance fallback.
            if model_settings == request.model_settings:
                return request
            return request.override(model_settings=model_settings)

        if synthesis_start is None:
            synthesis_start = self._exploration_model_calls
        synthesis_attempt = completed_calls - synthesis_start + 1
        is_last_synthesis_call = (
            self._structured_output and synthesis_attempt >= 2
        )
        if is_last_synthesis_call and isinstance(response_format, ToolStrategy):
            # ToolStrategy normally turns a validation error into a ToolMessage
            # and asks the model to try again. The second terminal attempt is the
            # last synthesis call even when exploration ended early, so surface
            # its validation error instead of requesting an impossible third one.
            response_format = ToolStrategy(
                schema=response_format.schema,
                tool_message_content=response_format.tool_message_content,
                handle_errors=False,
            )

        logger.info(
            "MCP agent entering reserved final-response call: "
            "model_call=%s/%s synthesis_attempt=%s repository_tools_disabled=%s "
            "structured_output=%s exploration_calls=%s",
            completed_calls + 1,
            self._max_model_calls,
            synthesis_attempt,
            len(request.tools),
            request.response_format is not None,
            self._exploration_model_calls,
        )
        schema_tool_name = (
            _single_structured_output_tool_name(response_format)
            if isinstance(response_format, ToolStrategy)
            else None
        )
        return request.override(
            tools=[],
            # ToolStrategy otherwise leaves tool choice automatic. Some
            # OpenAI-compatible providers can return an empty assistant turn
            # instead of the schema call, which mcp-use reports as the
            # misleading "No output generated" sentinel. Once repository
            # exploration is over, the single schema tool is the only valid
            # action, so require it just as the initial repository tool is
            # required above.
            tool_choice=schema_tool_name,
            response_format=response_format,
            model_settings=model_settings,
            system_message=self._append_final_response_instruction(
                request.system_message,
            ),
        )

    @staticmethod
    def _append_final_response_instruction(
            system_message: SystemMessage | None,
    ) -> SystemMessage:
        if system_message is None:
            return SystemMessage(content=_FINAL_RESPONSE_INSTRUCTION)

        separator = "\n\n"
        if isinstance(system_message.content, str):
            content: str | list[Any] = (
                f"{system_message.content}{separator}"
                f"{_FINAL_RESPONSE_INSTRUCTION}"
            )
        else:
            content = [
                *system_message.content,
                f"{separator}{_FINAL_RESPONSE_INSTRUCTION}",
            ]
        return system_message.model_copy(update={"content": content})

    @staticmethod
    def _json_payload(text: str) -> Any:
        """Load exact JSON, optionally inside one Markdown response fence."""
        stripped = text.strip()
        candidates = [stripped]
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            candidates.append("\n".join(lines).strip())

        for candidate in candidates:
            if not candidate:
                continue
            try:
                return json.loads(candidate)
            except (TypeError, ValueError):
                continue
        raise ValueError("response content is not exact JSON")

    @staticmethod
    def _schema_model(response_format: Any) -> type[BaseModel] | None:
        if not isinstance(response_format, ToolStrategy):
            return None
        schema = getattr(response_format, "schema", None)
        if (
            isinstance(schema, type)
            and issubclass(schema, BaseModel)
        ):
            return schema
        return None

    @classmethod
    def _validate_structured_candidate(
            cls,
            schema: type[BaseModel],
            candidate: Any,
    ) -> BaseModel:
        if isinstance(candidate, str):
            candidate = cls._json_payload(candidate)
        try:
            return schema.model_validate(candidate)
        except (TypeError, ValueError):
            # Providers occasionally return the value of a schema's only list
            # property as the JSON root. There is only one unambiguous wrapper
            # in that case; Pydantic still validates the complete nested shape.
            if isinstance(candidate, list) and len(schema.model_fields) == 1:
                field_name, field = next(iter(schema.model_fields.items()))
                if cls._annotation_accepts_list(field.annotation):
                    return schema.model_validate({field_name: candidate})
            raise

    @classmethod
    def _annotation_accepts_list(cls, annotation: Any) -> bool:
        """Return whether a field explicitly accepts a JSON-array value."""
        origin = get_origin(annotation)
        if annotation is list or origin is list:
            return True
        if origin is Annotated:
            arguments = get_args(annotation)
            return bool(arguments) and cls._annotation_accepts_list(arguments[0])
        if origin in {Union, UnionType}:
            return any(
                argument is not type(None)
                and cls._annotation_accepts_list(argument)
                for argument in get_args(annotation)
            )
        return False

    @classmethod
    def _recover_structured_response(
            cls,
            response: Any,
            response_format: Any,
    ) -> tuple[BaseModel | None, AIMessage | None]:
        """Validate provider JSON from the same call without another model turn."""
        schema = cls._schema_model(response_format)
        if schema is None:
            return None, None

        response_messages = tuple(getattr(response, "result", ()) or ())
        for message in reversed(response_messages):
            if not isinstance(message, AIMessage):
                continue

            schema_tool_name = _single_structured_output_tool_name(
                response_format,
            )
            for tool_call in getattr(message, "tool_calls", ()) or ():
                if (
                    isinstance(tool_call, Mapping)
                    and tool_call.get("name") == schema_tool_name
                ):
                    try:
                        recovered = cls._validate_structured_candidate(
                            schema,
                            tool_call.get("args"),
                        )
                    except (TypeError, ValueError):
                        continue
                    return recovered, message

            content = extract_llm_response_text(message)
            if not content.strip():
                continue
            try:
                recovered = cls._validate_structured_candidate(schema, content)
            except (TypeError, ValueError):
                continue
            return recovered, message
        return None, None

    @classmethod
    def _expose_structured_response(
            cls,
            response: Any,
            response_format: Any,
    ) -> Any:
        """Make LangChain's in-graph result visible to mcp-use as JSON."""
        structured_response = getattr(response, "structured_response", None)
        source_message: AIMessage | None = None
        if isinstance(structured_response, BaseModel):
            source_message = next((
                message
                for message in reversed(response.result)
                if isinstance(message, AIMessage)
            ), None)
        else:
            structured_response, source_message = (
                cls._recover_structured_response(
                    response,
                    response_format,
                )
            )
            if structured_response is not None:
                logger.info(
                    "MCP agent recovered structured response locally from "
                    "provider JSON: schema=%s",
                    type(structured_response).__name__,
                )

        if not isinstance(structured_response, BaseModel):
            return cls._identify_noncompliant_free_form_response(
                response,
                response_format,
            )
        if source_message is None:
            return response

        # ToolStrategy represents the final value as a schema tool call.
        # mcp-use 1.7 ignores the parallel ``structured_response`` state and
        # otherwise reports that call as a normal tool event followed by
        # "No output generated". Replace only the middleware response view;
        # the already validated structured value remains graph state.
        visible_message = source_message.model_copy(update={
            "content": structured_response.model_dump_json(),
            "tool_calls": [],
            "invalid_tool_calls": [],
        })
        logger.info(
            "MCP agent completed in-graph structured response: schema=%s",
            type(structured_response).__name__,
        )
        return ModelResponse(
            result=[visible_message],
            structured_response=structured_response,
        )

    @staticmethod
    def _identify_noncompliant_free_form_response(
            response: Any,
            response_format: Any,
    ) -> Any:
        """Give a non-schema prose response an id so recovery can remove it."""
        if not isinstance(response_format, ToolStrategy):
            return response

        def identify(message: AIMessage) -> AIMessage:
            if (
                message.id
                or message.tool_calls
                or message.invalid_tool_calls
                or not extract_llm_response_text(message).strip()
            ):
                return message
            return message.model_copy(update={
                "id": f"codecrow-unstructured-response-{uuid4()}",
            })

        if _is_ai_message(response):
            return identify(response)

        result = list(getattr(response, "result", ()) or ())
        for index in range(len(result) - 1, -1, -1):
            if not _is_ai_message(result[index]):
                continue
            identified = identify(result[index])
            if identified is result[index]:
                return response
            result[index] = identified
            return ModelResponse(
                result=result,
                structured_response=getattr(
                    response,
                    "structured_response",
                    None,
                ),
            )
        return response

    @staticmethod
    def _noncompliant_free_form_removal(
            message: Any,
    ) -> RemoveMessage | None:
        """Remove only prose that ignored an active structured response."""
        if (
            not _is_ai_message(message)
            or not message.id
            or message.tool_calls
            or message.invalid_tool_calls
            or not extract_llm_response_text(message).strip()
        ):
            return None
        return RemoveMessage(id=message.id)

    def wrap_model_call(
            self,
            request: ModelRequest,
            handler: Callable[[ModelRequest], Any],
    ) -> Any:
        prepared_request = self._prepare_request(request)
        response = handler(prepared_request)
        return self._expose_structured_response(
            response,
            prepared_request.response_format,
        )

    async def awrap_model_call(
            self,
            request: ModelRequest,
            handler: Callable[[ModelRequest], Awaitable[Any]],
    ) -> Any:
        prepared_request = self._prepare_request(request)
        response = await handler(prepared_request)
        return self._expose_structured_response(
            response,
            prepared_request.response_format,
        )

    @hook_config(can_jump_to=["model", "end"])
    def after_model(self, state: Mapping[str, Any], runtime: Any) -> Any:
        """Enter synthesis, correct once, then terminate the tool graph."""
        if not self._structured_output:
            return None
        if state.get("structured_response") is not None:
            return None

        completed_calls = state.get("run_model_call_count", 0)
        synthesis_start = self._state_synthesis_start(state)
        messages = state.get("messages") or ()
        latest_message = messages[-1] if messages else None
        latest_tool_calls = getattr(latest_message, "tool_calls", None)

        if synthesis_start is None:
            exploration_finished = (
                completed_calls >= self._exploration_model_calls
                or not latest_tool_calls
            )
            if not exploration_finished:
                return None

            # Keep the phase transition in graph state rather than mutable
            # middleware fields. Each prompt agent can therefore finish early,
            # run concurrently, or execute a final parallel tool-read turn
            # without sharing synthesis accounting with another request.
            transition: dict[str, Any] = {
                "agent_final_response_start_call": completed_calls,
            }
            if latest_tool_calls:
                # Execute the last legitimate exploration tool calls first. The
                # normal tool route returns to a model request, where the marker
                # above disables repository tools and exposes the schema.
                return transition

            logger.info(
                "MCP agent returned free-form output instead of the required "
                "schema; entering forced final-response recovery call %s/%s",
                completed_calls + 1,
                self._max_model_calls,
            )
            removal = self._noncompliant_free_form_removal(latest_message)
            if removal is not None:
                # The discarded prose must not anchor the schema recovery turn.
                # Prompt and repository tool results remain in the transcript.
                transition["messages"] = [removal]
            transition["jump_to"] = "model"
            return transition

        synthesis_calls_completed = completed_calls - synthesis_start
        if synthesis_calls_completed == 1:
            # ModelCallLimitMiddleware runs first on the reverse after-model
            # chain, so this count includes the first failed synthesis call.
            # Jump exactly once; the next request disables ToolStrategy's
            # internal retry and surfaces a validation failure directly.
            logger.warning(
                "MCP agent schema synthesis returned no structured response; "
                "using separate correction call %s/%s",
                completed_calls + 1,
                self._max_model_calls,
            )
            retry: dict[str, Any] = {"jump_to": "model"}
            removal = self._noncompliant_free_form_removal(latest_message)
            if removal is not None:
                retry["messages"] = [removal]
            return retry

        if synthesis_calls_completed >= 2:
            # A provider may ignore the final schema tool and repeat a repository
            # tool name from the transcript. LangChain's ToolNode inventory is
            # static, so request-time tools=[] alone does not prevent that stale
            # call from executing and routing to an implicit third synthesis
            # call. End the terminal phase explicitly; the shared execution
            # boundary will reject any non-schema value and its caller can use
            # the established direct recovery path without a misleading 6/6
            # model-limit failure or another repository read.
            logger.warning(
                "MCP agent schema correction returned no structured response; "
                "ending terminal phase after %s synthesis calls",
                synthesis_calls_completed,
            )
            return {"jump_to": "end"}

        return None


class RecursiveMCPAgent(MCPAgent):
    """Apply shared call and recursion behavior to the internal agent graph."""

    def __init__(
            self,
            *args: Any,
            recursion_limit: int = 50,
            output_schema: type[BaseModel] | None = None,
            preloaded_tools: Sequence[BaseTool] | None = None,
            initial_required_tool_name: str | None = None,
            required_tool_names: Sequence[str] = (),
            model_request_settings: Mapping[str, Any] | None = None,
            **kwargs: Any,
    ):
        self._custom_recursion_limit = recursion_limit
        self._output_schema = output_schema
        self._initial_required_tool_name = initial_required_tool_name
        self._required_tool_names = tuple(required_tool_names)
        if (
            not self._required_tool_names
            and initial_required_tool_name is not None
        ):
            self._required_tool_names = (initial_required_tool_name,)
        self._model_request_settings = dict(model_request_settings or {})
        self._preloaded_tools = (
            None
            if preloaded_tools is None
            else tuple(preloaded_tools)
        )
        super().__init__(*args, **kwargs)

    async def initialize(self) -> None:
        """Build a request graph from the shared service's cached tool set."""
        if self._preloaded_tools is None:
            await super().initialize()
            return

        self._tools = list(self._preloaded_tools)
        await self._create_system_message_from_tools(self._tools)
        self._agent_executor = self._create_agent()
        self._initialized = True
        logger.info(
            "RecursiveMCPAgent initialized from %d preloaded tool(s)",
            len(self._tools),
        )

    def _create_agent(self):
        """Build the pinned mcp-use graph with a final-response reserve."""
        logger.debug("Creating MCP agent with %s tools", len(self._tools))
        system_prompt: SystemMessage | str = (
            self._system_message or "You are a helpful assistant"
        )
        tool_names = [tool.name for tool in self._tools]
        logger.info("MCP agent ready with tools: %s", ", ".join(tool_names))

        middleware: list[Any] = []
        if self.retry_on_error:
            middleware.append(tool_error_handler)
        model_request_settings = getattr(
            self,
            "_model_request_settings",
            {},
        )
        # OpenRouter's endpoint capability requirement belongs to every agent
        # turn, including an exact required first tool. Keep this middleware in
        # the graph even when the caller has no additional model settings.
        middleware.append(ModelRequestSettingsMiddleware(
            settings=model_request_settings,
        ))
        structured_output = self._output_schema is not None
        final_response_middleware = FinalResponseReserveMiddleware(
            exploration_model_calls=self.max_steps,
            structured_output=structured_output,
        )
        middleware.append(final_response_middleware)
        required_tool_names = getattr(self, "_required_tool_names", ())
        if not required_tool_names:
            initial_required_tool_name = getattr(
                self,
                "_initial_required_tool_name",
                None,
            )
            if initial_required_tool_name:
                required_tool_names = (initial_required_tool_name,)
        if required_tool_names:
            # FinalResponseReserveMiddleware stays outside this middleware. If a
            # caller configures too few calls to explore, its tool removal wins;
            # normal exploration gets one exact first call and later turns are
            # reconstructed with the complete automatic-choice tool inventory.
            middleware.append(InitialRequiredToolMiddleware(
                tool_names=required_tool_names,
            ))
        middleware.append(ModelCallLimitMiddleware(
            run_limit=final_response_middleware.max_model_calls,
            exit_behavior="error",
        ))

        llm_model = self.llm
        assert isinstance(llm_model, BaseChatModel), (
            "LLM must be a BaseChatModel instance"
        )
        agent = create_agent(
            model=llm_model,
            tools=self._tools,
            system_prompt=system_prompt,
            middleware=middleware,
            response_format=(
                ToolStrategy(schema=self._output_schema)
                if self._output_schema is not None
                else None
            ),
            debug=self.verbose,
        ).with_config({"recursion_limit": self.recursion_limit})
        logger.debug(
            "Created MCP agent with max_steps=%s and %s callbacks",
            self.max_steps,
            len(self.callbacks),
        )
        return agent

    async def _attempt_structured_output(
            self,
            raw_result: str,
            *args: Any,
            **kwargs: Any,
    ):
        # mcp-use treats LangChain's artificial model-call-limit message as a
        # normal final AI message and otherwise sends it through another model
        # call for schema conversion. Reject it before it can become fabricated
        # structured output.
        reject_mcp_use_model_call_limit_output(raw_result)
        return await super()._attempt_structured_output(
            raw_result,
            *args,
            **kwargs,
        )

    @staticmethod
    def _validated_stream_output(
            output: Any,
            schema: type[BaseModel],
    ) -> BaseModel:
        """Return one typed terminal value or reject the adapter's sentinel."""
        if isinstance(output, schema):
            return output

        candidate: Any = output
        if not isinstance(candidate, (str, Mapping, list)):
            candidate = extract_llm_response_text(candidate)
        try:
            return FinalResponseReserveMiddleware._validate_structured_candidate(
                schema,
                candidate,
            )
        except (TypeError, ValueError, AttributeError) as exception:
            raise ValueError(
                "MCP agent terminal response did not satisfy the required "
                f"{schema.__name__} schema"
            ) from exception

    async def stream(self, *args: Any, **kwargs: Any):
        if self._agent_executor is None:
            await self.initialize()

        executor = self._agent_executor
        if executor and not getattr(executor, "_is_patched_recursion", False):
            original_astream = executor.astream
            limit = self._custom_recursion_limit

            async def patched_astream(
                    input_data: Any,
                    config: dict[str, Any] | None = None,
                    **astream_kwargs: Any,
            ):
                graph_config = dict(config or {})
                graph_config["recursion_limit"] = limit
                async for chunk in original_astream(
                    input_data,
                    config=graph_config,
                    **astream_kwargs,
                ):
                    yield chunk

            executor.astream = patched_astream
            executor._is_patched_recursion = True
            logger.info(
                "RecursiveMCPAgent: patched recursion limit to %s",
                limit,
            )

        async for item in super().stream(*args, **kwargs):
            reject_mcp_use_model_call_limit_output(item)
            if self._output_schema is not None and not (
                isinstance(item, tuple) and len(item) == 2
            ):
                item = self._validated_stream_output(
                    item,
                    self._output_schema,
                )
            yield item
