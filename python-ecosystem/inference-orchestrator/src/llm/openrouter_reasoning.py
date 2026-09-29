"""Lossless OpenRouter reasoning continuity at the Chat Completions boundary."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

# Keep stream fragments out of LangChain's generic indexed-dict merge: it also
# concatenates repeated format/ID strings, corrupting provider reasoning blocks.
STREAM_DETAILS_KEY = "_openrouter_reasoning_detail_fragments"
_TEXT_FIELD_BY_TYPE = {"reasoning.text": "text", "reasoning.summary": "summary"}


def reasoning_fields(message: Mapping[str, Any]) -> dict[str, Any]:
    """Return one complete provider representation, without duplicating its text."""
    details = message.get("reasoning_details")
    if isinstance(details, list):
        # An explicit empty array is meaningful continuation state for DeepSeek.
        return {"reasoning_details": deepcopy(details)}
    for name in ("reasoning", "reasoning_content"):
        if isinstance(message.get(name), str):
            return {name: message[name]}
    return {}


def assembled_details(fragments: list[Any]) -> list[Any]:
    """Assemble streamed text fields while keeping opaque block metadata intact."""
    blocks: list[Any] = []
    for fragment in fragments:
        for block in fragment.get("blocks", []) if isinstance(fragment, dict) else ():
            if not isinstance(block, dict):
                blocks.append(deepcopy(block))
                continue
            text_field = _TEXT_FIELD_BY_TYPE.get(block.get("type"))
            previous = blocks[-1] if blocks else None
            # Match OpenRouter's consecutive text/summary assembly. Providers
            # may reuse an index across different blocks; encrypted payloads and
            # signatures are opaque and must never be concatenated.
            # https://github.com/OpenRouterTeam/ai-sdk-provider/blob/main/src/chat/index.ts
            if text_field and isinstance(previous, dict) and previous.get("type") == block.get("type") and all(
                name == text_field or previous.get(name) in (None, value) or value is None
                for name, value in block.items()
            ):
                for name, value in block.items():
                    if name == text_field and isinstance(value, str) and isinstance(previous.get(name), str):
                        previous[name] += value
                    elif value is not None or name not in previous:
                        previous[name] = deepcopy(value)
            else:
                blocks.append(deepcopy(block))
    return blocks


def replay_reasoning(additional: Mapping[str, Any]) -> dict[str, Any]:
    """Recover both complete responses and accumulated streaming responses."""
    fragments = additional.get(STREAM_DETAILS_KEY)
    if isinstance(fragments, list):
        return {"reasoning_details": assembled_details(fragments)}
    return reasoning_fields(additional)
