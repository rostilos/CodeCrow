"""Execute MCP command streams and their evidence-preserving direct fallback."""
import logging
from collections.abc import Callable
from typing import Any

from service.agent import (
    AgentExecutionRequest, AgentExecutionService, AgentOutputEvent, AgentToolEvent,
)
from service.command import results
from service.command.input_budget import (
    _CommandProviderInputGuard,
    _assert_command_input_fits,
    _jsonable,
    CommandInputLimitError,
    guarded_direct_invoke,
    install_provider_input_guard,
)
from utils.error_sanitizer import create_user_friendly_error

logger = logging.getLogger(__name__)


async def execute_command(
    *,
    llm: Any,
    client: Any,
    request: AgentExecutionRequest,
    input_token_budget: int,
    coerce_result: Callable[[Any], dict[str, Any]],
    emit_event: Callable[[dict[str, Any]], None],
    start_message: str,
    completion_message: str,
    fallback_instruction: str,
) -> dict[str, Any]:
    """Run one command, then at most one direct fallback without tool evidence.

    Once any tool result has been observed, a direct prompt would lose that
    evidence. Preserve the agent result/error instead of issuing that retry.
    """
    label = str(request.metadata["command"]).capitalize()
    schema = request.output_schema
    install_provider_input_guard(llm, _CommandProviderInputGuard(input_token_budget, schema))
    _assert_command_input_fits(
        {"prompt": request.prompt, "additional_instructions": request.additional_instructions},
        input_token_budget,
        response_schema=schema,
        label=f"{label} initial turn",
    )
    agent = AgentExecutionService(llm=llm, client=client)
    transcript: list[dict[str, Any]] = []

    def input_error(error: CommandInputLimitError) -> dict[str, Any]:
        emit_event({"type": "error", "state": "input_limit_exceeded", "message": str(error)})
        return {"error": str(error)}

    try:
        emit_event({"type": "progress", "step": 0, "max_steps": request.max_steps, "message": start_message})
        final_result = None
        async for event in agent.stream(request):
            if isinstance(event, AgentToolEvent):
                action = event.action
                tool_name = getattr(action, "tool", str(action))
                transcript.append({
                    "tool": tool_name,
                    "toolInput": _jsonable(getattr(action, "tool_input", None)),
                    "observation": _jsonable(event.observation),
                })
                _assert_command_input_fits(
                    {
                        "prompt": request.prompt,
                        "additional_instructions": request.additional_instructions,
                        "tool_transcript": transcript,
                    },
                    input_token_budget,
                    response_schema=schema,
                    label=f"{label} MCP transcript",
                )
                logger.info("[%s Step %s] Tool: %s", label, len(transcript), tool_name)
                emit_event({
                    "type": "mcp_step", "step": len(transcript), "max_steps": request.max_steps,
                    "tool": tool_name, "message": f"Executed tool: {tool_name}",
                })
            elif isinstance(event, AgentOutputEvent):
                item = event.output
                if isinstance(item, (schema, str, dict)):
                    final_result = item
                else:
                    extracted = results.extract_agent_item_text(item)
                    if extracted is not None:
                        final_result = extracted
        emit_event({
            "type": "progress", "step": request.max_steps, "max_steps": request.max_steps,
            "message": f"{completion_message} ({len(transcript)} tool calls)",
        })
        result = coerce_result(final_result)
        if "error" not in result or transcript:
            return result
    except CommandInputLimitError as error:
        return input_error(error)
    except Exception as error:
        logger.info("%s agent path failed: %s", label, error)
        if transcript:
            return {"error": create_user_friendly_error(error)}

    # Keep the fallback outside the stream exception handler: a failed direct
    # attempt must not accidentally trigger a second identical paid request.
    try:
        response = await guarded_direct_invoke(
            llm,
            request.prompt + "\n\n" + fallback_instruction,
            input_token_budget,
            label=f"{label} direct fallback",
            response_schema=schema,
        )
        return coerce_result(response)
    except CommandInputLimitError as error:
        return input_error(error)
    except Exception as error:
        return {"error": create_user_friendly_error(error)}
