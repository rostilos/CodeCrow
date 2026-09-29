"""Native provider tool turns for the request-bound review agent.

The caller owns evidence selection and tool execution. This adapter preserves
provider assistant messages (including reasoning signatures) and never retries a
paid request in another protocol after a provider failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from typing import Any, Mapping, Sequence

from langchain_core.messages import SystemMessage, ToolMessage

from llm.reasoning_policy import ReasoningEffort, reasoning_request_kwargs
from llm.request_capture import model_capture
from llm.review_invocation import invoke_review_model
from service.review.model_calls import parse_object
from service.review.execution_scheduler import review_model_slot

logger = logging.getLogger(__name__)


@dataclass
class AgentTurn:
    response: Any
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    output: dict[str, Any] | None = None
    diagnostics: list[str] = field(default_factory=list)


def native_tool_definitions(schemas: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Translate MCP definitions into the format accepted by all chat adapters."""
    return [{"type": "function", "function": {
        "name": schema["name"], "description": schema.get("description") or "",
        "parameters": schema["inputSchema"],
    }} for schema in schemas]


def result_message(call: Mapping[str, Any], result: Mapping[str, Any]) -> ToolMessage:
    """Pair a host-produced observation with its native assistant tool call."""
    return ToolMessage(content=json.dumps(result, ensure_ascii=False),
                       tool_call_id=str(call["id"]), name=str(call["name"]),
                       status="error" if call.get("error") else "success")


class ReviewAgentSession:
    """Bind native tools once; use JSON only for models lacking tool binding."""

    def __init__(self, llm: Any, request: Any, schemas: Sequence[Mapping[str, Any]],
                 effort: ReasoningEffort = ReasoningEffort.MEDIUM):
        self.llm = llm
        self.request = request
        self.schemas = list(schemas)
        self.options = reasoning_request_kwargs(llm, effort)
        self.diagnostics: list[str] = []
        binder = getattr(llm, "bind_tools", None)
        self.native_tools = callable(binder)
        if self.native_tools:
            try:
                self.model = binder(native_tool_definitions(schemas))
            except NotImplementedError:
                self.native_tools = False
        if not self.native_tools:
            self.model = llm
            message = "Review model has no native tool binding; using JSON tool requests."
            self.diagnostics.append(message)
            logger.warning(message)
        logger.info("Review tool binding: protocol=%s tools=%s",
                    "native_tools" if self.native_tools else "json_fallback",
                    [schema["name"] for schema in schemas])
        self._turn = 0

    async def invoke(self, messages: Sequence[Any], *, stage: str,
                     batch_ids: list[str] | None = None) -> AgentTurn:
        self._turn += 1
        outgoing = list(messages)
        if not self.native_tools:
            outgoing.insert(0, SystemMessage(content=(
                "This model uses JSON tool requests. Return one JSON object. "
                "To retrieve evidence include toolCalls:[{name,arguments}]; "
                "otherwise return the requested final review object. Available tools: "
                + json.dumps(self.schemas, ensure_ascii=False)
            )))
        async with review_model_slot(stage):
            with model_capture(self.request, stage=stage, turn=self._turn, batch_ids=batch_ids):
                response = await invoke_review_model(self.model, outgoing, request=self.request, stage=stage,
                                                     options=self.options, provider_model=self.llm)
        logger.info("Review model response: stage=%s PR=%s protocol=%s usage=%s",
                    stage, getattr(self.request, "pullRequestId", None),
                    "native_tools" if self.native_tools else "json_fallback",
                    getattr(response, "usage_metadata", None))
        turn = AgentTurn(response=response)
        if self.native_tools:
            def native_call(call: Any, *, malformed: bool = False) -> None:
                if not isinstance(call, Mapping):
                    turn.diagnostics.append("Model returned a malformed native tool request without a usable call record.")
                    return
                invalid = malformed or not isinstance(call.get("args"), dict)
                request = {
                    "id": str(call.get("id") or f"review-{self._turn}-{len(turn.tool_calls)}"),
                    "name": str(call.get("name") or ""),
                    "arguments": {} if invalid else dict(call["args"]),
                }
                if invalid:
                    # The original assistant message still contains this call.
                    # Preserve its ID so the host can return a matching error
                    # receipt without executing malformed arguments.
                    request["error"] = "Tool arguments must be a valid JSON object matching the tool schema. This call was not executed."
                    turn.diagnostics.append("Model returned invalid native tool arguments; a matching error receipt was supplied.")
                turn.tool_calls.append(request)

            for call in getattr(response, "tool_calls", ()) or ():
                native_call(call)
            for call in getattr(response, "invalid_tool_calls", ()) or ():
                native_call(call, malformed=True)
            if turn.tool_calls:
                try:
                    turn.output = parse_object(response)
                except (TypeError, ValueError):
                    pass  # Native calls commonly accompany ordinary commentary.
                return turn
        turn.output = parse_object(response)
        # Compatibility for models that emit a JSON tool request despite a
        # native binding. It is observable and does not make another model call.
        json_calls = turn.output.get("toolCalls") or []
        if isinstance(json_calls, dict):
            json_calls = [json_calls]
        if isinstance(json_calls, list):
            for call in json_calls:
                if isinstance(call, Mapping) and isinstance(call.get("arguments"), dict):
                    turn.tool_calls.append({"id": f"json-{self._turn}-{len(turn.tool_calls)}",
                                            "name": str(call.get("name") or ""),
                                            "arguments": dict(call["arguments"]), "protocol": "json"})
        if self.native_tools and turn.tool_calls:
            turn.diagnostics.append("Model emitted JSON tool requests despite native tool binding.")
            logger.warning("Native review model emitted JSON tool requests: stage=%s", stage)
        return turn
