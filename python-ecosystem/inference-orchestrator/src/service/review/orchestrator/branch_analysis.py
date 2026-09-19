"""
Branch analysis and reconciliation execution.
"""
import logging
from typing import Any, Callable, Dict, Optional

from model.output_schemas import CodeReviewOutput, ReconciliationOutput
from utils.llm_response import extract_llm_response_text
from utils.prompts.prompt_builder import PromptBuilder

from service.review.orchestrator.json_utils import (
    parse_llm_response,
    resolve_structured_output,
    supports_structured_output,
)
from service.review.orchestrator.structured_output import (
    format_response_diagnostics,
    invoke_structured_output,
)
from service.review.orchestrator.stage_helpers import emit_status, emit_error
from llm.reasoning_policy import ReasoningEffort, reasoning_request_kwargs

logger = logging.getLogger(__name__)


BRANCH_ANALYSIS_ALLOWED_MCP_TOOLS = frozenset({"getBranchFileContent"})
BRANCH_ANALYSIS_MAX_OUTPUT_TOKENS = 16_384


async def execute_branch_analysis(
    llm,
    client,
    prompt: str,
    event_callback: Optional[Callable[[Dict], None]] = None,
    agent_service=None,
) -> Dict[str, Any]:
    emit_status(event_callback, "branch_analysis_started", "Starting Branch Analysis & Reconciliation...")

    # The MCP runtime is required only for the legacy branch-analysis path.
    # PR review, direct reconciliation, and provider-free prompt capture must
    # remain importable without initializing an agent runtime.
    from service.agent import (
        AgentExecutionRequest,
        AgentExecutionService,
        AgentOutputEvent,
    )

    agent_service = agent_service or AgentExecutionService(
        llm=llm,
        client=client,
    )
    execution_request = AgentExecutionRequest(
        prompt=prompt,
        allowed_tool_names=BRANCH_ANALYSIS_ALLOWED_MCP_TOOLS,
        max_steps=15,
        reasoning_effort=ReasoningEffort.LOW,
        max_output_tokens=BRANCH_ANALYSIS_MAX_OUTPUT_TOKENS,
        output_schema=CodeReviewOutput,
        additional_instructions=PromptBuilder.get_additional_instructions(),
        metadata={"flow": "review", "stage": "branch_analysis"},
    )

    try:
        final_text = ""
        async for event in agent_service.stream(execution_request):
            if not isinstance(event, AgentOutputEvent):
                continue
            item = event.output
            if isinstance(item, CodeReviewOutput):
                issues = [i.model_dump() for i in item.issues] if item.issues else []
                return {"issues": issues, "comment": item.comment or "Branch analysis completed."}
            if isinstance(item, str):
                final_text = item

        if final_text:
            data = await parse_llm_response(
                final_text,
                CodeReviewOutput,
                llm,
                max_provider_repairs=0,
            )
            issues = [i.model_dump() for i in data.issues] if data.issues else []
            return {"issues": issues, "comment": data.comment or "Branch analysis completed."}

        return {"issues": [], "comment": "No issues found."}

    except Exception as e:
        logger.error(f"Branch analysis failed: {e}", exc_info=True)
        emit_error(event_callback, str(e))
        raise


async def execute_branch_reconciliation_direct(
    llm,
    prompt: str,
    event_callback: Optional[Callable[[Dict], None]] = None,
) -> Dict[str, Any]:
    emit_status(event_callback, "branch_reconciliation_started",
                "Starting direct branch reconciliation (no MCP)...")

    if supports_structured_output(llm):
        try:
            invocation = await invoke_structured_output(
                llm,
                prompt,
                ReconciliationOutput,
                effort=ReasoningEffort.LOW,
                label="branch-reconciliation",
            )
            result = await resolve_structured_output(
                invocation,
                ReconciliationOutput,
                llm,
            )

            if result and isinstance(result, ReconciliationOutput):
                issues = [i.model_dump() for i in result.issues] if result.issues else []
                logger.info(f"Direct reconciliation: {len(issues)} resolved issues returned")
                return {"issues": issues, "comment": result.comment or "Branch reconciliation completed."}
        except Exception as structured_err:
            logger.warning(
                "Structured output failed for reconciliation; falling back: "
                "error_type=%s",
                type(structured_err).__name__,
            )
    else:
        logger.info("Structured output skipped for reconciliation; using prompt JSON parsing")

    try:
        response = await llm.ainvoke(
            prompt,
            **reasoning_request_kwargs(llm, ReasoningEffort.LOW),
        )
        content = extract_llm_response_text(response)

        if not content.strip():
            logger.warning(
                "Direct branch reconciliation returned no content: %s",
                format_response_diagnostics(response),
            )

        if content:
            data = await parse_llm_response(
                content,
                ReconciliationOutput,
                llm,
                max_provider_repairs=0,
            )
            issues = [i.model_dump() for i in data.issues] if data.issues else []
            return {"issues": issues, "comment": data.comment or "Branch reconciliation completed."}

        return {"issues": [], "comment": "No issues resolved."}

    except Exception as e:
        logger.error(f"Direct branch reconciliation failed: {e}", exc_info=True)
        emit_error(event_callback, str(e))
        raise
