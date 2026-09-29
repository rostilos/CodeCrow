"""Provider-neutral JSON calls for the review workflow, without output clipping."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any

from llm.reasoning_policy import ReasoningEffort, reasoning_request_kwargs
from llm.request_capture import model_capture
from llm.review_invocation import invoke_review_model
from service.review.execution_scheduler import review_model_slot


logger = logging.getLogger(__name__)


def parse_object(response: Any) -> dict[str, Any]:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "\n".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, Mapping) and block.get("type") == "text"
        )
    text = str(content).strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # Native tool models may wrap the final object in an explanation. Read
        # explicit JSON fences instead of discarding an otherwise usable plan or
        # verdict. Do not salvage arbitrary nested braces from malformed output.
        blocks = re.findall(r"```(?:json)?[ \t]*\r?\n(.*?)```", text, re.IGNORECASE | re.DOTALL)
        for block in reversed(blocks):
            try:
                result = json.loads(block.strip())
                break
            except json.JSONDecodeError:
                continue
        else:
            raise
    if not isinstance(result, dict):
        raise ValueError("review model did not return a JSON object")
    return result


async def invoke_json(
    llm: Any, request: Any, *, stage: str, system: str,
    payload: dict[str, Any], effort: ReasoningEffort = ReasoningEffort.MEDIUM,
    batch_ids: list[str] | None = None,
) -> dict[str, Any]:
    options = reasoning_request_kwargs(llm, effort)
    if request.aiProvider.lower() in {"openrouter", "openai"}:
        options["response_format"] = {"type": "json_object"}
    async with review_model_slot(stage):
        with model_capture(request, stage=stage, batch_ids=batch_ids):
            response = await invoke_review_model(llm, [
                ("system", system),
                ("human", json.dumps(payload, ensure_ascii=False)),
            ], request=request, stage=stage, options=options)
    logger.info(
        "Review model response: stage=%s PR=%s usage=%s",
        stage, request.pullRequestId, getattr(response, "usage_metadata", None),
    )
    return parse_object(response)
