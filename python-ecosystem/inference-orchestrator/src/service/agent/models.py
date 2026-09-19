"""Shared request, event, and result contracts for MCP agent execution."""

from dataclasses import dataclass, field
import re
from typing import Any, Generic, Mapping, Type, TypeVar, Union

from pydantic import BaseModel

from llm.reasoning_policy import ReasoningEffort


AgentOutputT = TypeVar("AgentOutputT", bound=BaseModel)


_MCP_USE_MODEL_CALL_LIMIT_OUTPUT = re.compile(
    r"Model call limits exceeded: "
    r"(?:thread|run) limit \(\d+/\d+\)"
    r"(?:, (?:thread|run) limit \(\d+/\d+\))*"
)


class AgentModelCallLimitError(RuntimeError):
    """The agent exhausted its model calls before producing a final answer."""


class AgentExecutionError(RuntimeError):
    """An agent failed after producing observable tool events.

    ``AgentExecutionService.stream`` already lets streaming callers retain every
    event yielded before a failure.  The collected ``execute`` convenience path
    carries the same partial transcript on this exception instead of silently
    discarding it while preserving the original exception as ``__cause__``.
    """

    def __init__(
            self,
            message: str,
            *,
            tool_events: tuple["AgentToolEvent", ...],
            metadata: Mapping[str, Any],
    ):
        super().__init__(message)
        self.tool_events = tool_events
        self.metadata = dict(metadata)


def reject_mcp_use_model_call_limit_output(output: Any) -> None:
    """Reject LangChain's synthetic call-limit message as terminal output."""
    if not isinstance(output, str):
        return
    normalized = output.strip()
    if _MCP_USE_MODEL_CALL_LIMIT_OUTPUT.fullmatch(normalized):
        raise AgentModelCallLimitError(
            "MCP agent reached its model-call limit before producing a final "
            f"response: {normalized}"
        )


@dataclass(frozen=True)
class AgentExecutionRequest(Generic[AgentOutputT]):
    """Everything that may vary between otherwise identical agent runs."""

    prompt: str
    allowed_tool_names: frozenset[str]
    # Repository-capable model turns. The shared agent reserves its terminal
    # response synthesis (and one schema correction when structured) separately.
    max_steps: int
    output_schema: Type[AgentOutputT] | None = None
    additional_instructions: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    recursion_limit: int = 50
    reasoning_effort: ReasoningEffort | None = None
    max_output_tokens: int | None = None
    timeout_seconds: float | None = None
    initial_required_tool_name: str | None = None
    required_tool_names: tuple[str, ...] = ()
    tool_argument_bindings: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict,
    )


@dataclass(frozen=True)
class AgentToolEvent:
    """One completed MCP tool call from the underlying agent stream."""

    action: Any
    observation: Any
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentOutputEvent(Generic[AgentOutputT]):
    """A final or intermediate non-tool value from the agent stream."""

    output: Any
    metadata: Mapping[str, Any] = field(default_factory=dict)


AgentExecutionEvent = Union[AgentToolEvent, AgentOutputEvent[AgentOutputT]]


@dataclass(frozen=True)
class AgentExecutionResult(Generic[AgentOutputT]):
    """Collected form of the same events exposed by streaming execution."""

    output: Any
    tool_events: tuple[AgentToolEvent, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
