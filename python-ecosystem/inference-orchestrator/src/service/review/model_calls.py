"""Provider-neutral JSON calls for the review workflow, without output clipping."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from llm.reasoning_policy import ReasoningEffort, reasoning_request_kwargs
from llm.request_capture import model_capture


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
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    result = json.loads(text)
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
    with model_capture(request, stage=stage, batch_ids=batch_ids):
        response = await llm.ainvoke([
            ("system", system),
            ("human", json.dumps(payload, ensure_ascii=False)),
        ], **options)
    logger.info(
        "Review model response: stage=%s PR=%s usage=%s",
        stage, request.pullRequestId, getattr(response, "usage_metadata", None),
    )
    return parse_object(response)
