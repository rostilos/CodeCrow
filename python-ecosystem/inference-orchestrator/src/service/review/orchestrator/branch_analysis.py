"""
Branch analysis and reconciliation execution.
"""
import logging
from typing import Any, Callable, Dict, Optional

from model.output_schemas import CodeReviewOutput, ReconciliationOutput
from utils.prompts.prompt_builder import PromptBuilder

from service.review.orchestrator.agents import RecursiveMCPAgent, extract_llm_response_text
from service.review.orchestrator.json_utils import parse_llm_response
from service.review.orchestrator.stage_helpers import emit_status, emit_error

logger = logging.getLogger(__name__)


async def execute_branch_analysis(
    llm,
    client,
    prompt: str,
    event_callback: Optional[Callable[[Dict], None]] = None,
) -> Dict[str, Any]:
    emit_status(event_callback, "branch_analysis_started", "Starting Branch Analysis & Reconciliation...")

    agent = RecursiveMCPAgent(
        llm=llm,
        client=client,
        additional_instructions=PromptBuilder.get_additional_instructions(),
    )

    try:
        final_text = ""
        async for item in agent.stream(prompt, max_steps=15, output_schema=CodeReviewOutput):
            if isinstance(item, CodeReviewOutput):
                issues = [i.model_dump() for i in item.issues] if item.issues else []
                return {"issues": issues, "comment": item.comment or "Branch analysis completed."}
            if isinstance(item, str):
                final_text = item

        if final_text:
            data = await parse_llm_response(final_text, CodeReviewOutput, llm)
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

    try:
        structured_llm = llm.with_structured_output(ReconciliationOutput)
        result = await structured_llm.ainvoke(prompt)

        if result and isinstance(result, ReconciliationOutput):
            issues = [i.model_dump() for i in result.issues] if result.issues else []
            logger.info(f"Direct reconciliation: {len(issues)} resolved issues returned")
            return {"issues": issues, "comment": result.comment or "Branch reconciliation completed."}
    except Exception as structured_err:
        logger.warning(f"Structured output failed for reconciliation, falling back: {structured_err}")

    try:
        response = await llm.ainvoke(prompt)
        content = extract_llm_response_text(response)

        if content:
            data = await parse_llm_response(content, ReconciliationOutput, llm)
            issues = [i.model_dump() for i in data.issues] if data.issues else []
            return {"issues": issues, "comment": data.comment or "Branch reconciliation completed."}

        return {"issues": [], "comment": "No issues resolved."}

    except Exception as e:
        logger.error(f"Direct branch reconciliation failed: {e}", exc_info=True)
        emit_error(event_callback, str(e))
        raise
