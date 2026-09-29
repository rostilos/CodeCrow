"""Complete review responses with cancellable provider transport and timing."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
import logging
import math
import os
import time
from typing import Any, Mapping, Sequence

from utils.llm_delegate import llm_class_names

logger = logging.getLogger(__name__)
DEFAULT_REVIEW_MODEL_CALL_TIMEOUT_SECONDS = 900.0


@dataclass
class _Progress:
    started: float
    first_delta_ms: float | None = None
    reasoning_fragments: int = 0
    content_characters: int = 0
    tool_fragments: int = 0
    provider: str | None = None
    generation_id: str | None = None


_PROGRESS: ContextVar[_Progress | None] = ContextVar("review_provider_progress", default=None)


def observe_openrouter_chunk(chunk: Mapping[str, Any]) -> None:
    """Measure actual model deltas, never keepalives or private reasoning text."""
    progress = _PROGRESS.get()
    if progress is None:
        return
    if isinstance(chunk.get("provider"), str):
        progress.provider = chunk["provider"]
    if isinstance(chunk.get("id"), str):
        progress.generation_id = chunk["id"]
    choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
    if not choices:
        return
    delta = choices[0].get("delta") or {}
    reasoning = bool(delta.get("reasoning_details") or delta.get("reasoning") or delta.get("reasoning_content"))
    content = delta.get("content")
    characters = len(content) if isinstance(content, str) else 0
    tools = bool(delta.get("tool_calls"))
    if progress.first_delta_ms is None and (reasoning or characters or tools):
        progress.first_delta_ms = (time.perf_counter() - progress.started) * 1000
    progress.reasoning_fragments += int(reasoning)
    progress.content_characters += characters
    progress.tool_fragments += int(tools)


def _call_timeout_seconds() -> float:
    configured = os.environ.get("REVIEW_MODEL_CALL_TIMEOUT_SECONDS", "")
    if configured.strip():
        try:
            value = float(configured)
            if math.isfinite(value) and value > 0:
                return value
        except ValueError:
            pass
        logger.warning("Invalid REVIEW_MODEL_CALL_TIMEOUT_SECONDS=%r; using %s", configured,
                       DEFAULT_REVIEW_MODEL_CALL_TIMEOUT_SECONDS)
    return DEFAULT_REVIEW_MODEL_CALL_TIMEOUT_SECONDS


async def invoke_review_model(model: Any, messages: Sequence[Any], *, request: Any, stage: str,
                              options: Mapping[str, Any], provider_model: Any = None) -> Any:
    """Aggregate one complete response; never replay a partially consumed call."""
    outgoing_options = dict(options)
    router = "ChatOpenRouter" in llm_class_names(provider_model if provider_model is not None else model)
    if router:
        # LangChain's ainvoke(stream=True) aggregates the SDK stream into the
        # ordinary final AIMessage. The stream context closes on cancellation.
        outgoing_options["stream"] = True
        if isinstance(outgoing_options.get("extra_body"), Mapping):
            outgoing_options["extra_body"] = {key: value for key, value in outgoing_options["extra_body"].items()
                                               if key != "stream"}
    timeout = _call_timeout_seconds()
    progress = _Progress(time.perf_counter())
    token = _PROGRESS.set(progress)
    outcome = "completed"
    deadline = asyncio.timeout(timeout)
    try:
        async with deadline:
            response = await model.ainvoke(messages, **outgoing_options)
            usage = getattr(response, "usage_metadata", None)
            if router and isinstance(usage, Mapping):
                output_tokens = usage.get("output_tokens")
                details = usage.get("output_token_details") or {}
                reasoning_tokens = details.get("reasoning") if isinstance(details, Mapping) else None
                if isinstance(output_tokens, int) and isinstance(reasoning_tokens, int) and reasoning_tokens > output_tokens:
                    logger.warning("OpenRouter reported inconsistent usage: PR=%s stage=%s output_tokens=%s reasoning_tokens=%s; "
                                   "provider values retained without estimating token counts",
                                   getattr(request, "pullRequestId", None), stage, output_tokens, reasoning_tokens)
            return response
    except TimeoutError as error:
        if not deadline.expired():
            outcome = type(error).__name__
            raise
        outcome = "timeout"
        raise TimeoutError(f"Review provider call exceeded the configured {timeout:g}s elapsed timeout during {stage}") from error
    except BaseException as error:
        outcome = type(error).__name__
        raise
    finally:
        _PROGRESS.reset(token)
        logger.info("Review provider invocation: PR=%s stage=%s duration_ms=%.1f streaming=%s provider=%s generation=%s "
                    "first_model_delta_ms=%s reasoning_fragments=%d content_characters=%d tool_fragments=%d outcome=%s",
                    getattr(request, "pullRequestId", None), stage, (time.perf_counter() - progress.started) * 1000,
                    router, progress.provider, progress.generation_id, progress.first_delta_ms,
                    progress.reasoning_fragments, progress.content_characters, progress.tool_fragments, outcome)
