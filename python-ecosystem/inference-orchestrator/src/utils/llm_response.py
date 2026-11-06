"""Provider-neutral helpers for extracting text from model responses."""

from __future__ import annotations

from typing import Any


def extract_llm_response_text(response: Any) -> str:
    """Extract text across plain, LangChain-style, and multipart responses."""
    if response is None:
        return ""
    content = getattr(response, "content", response)
    if content is None:
        return ""
    if isinstance(content, str):
        return str(content)

    if isinstance(content, (list, tuple)):
        text_parts = []
        for item in content:
            text_parts.append(extract_llm_response_text(item))
        return "".join(text_parts)

    if isinstance(content, dict):
        for key in ("text", "content"):
            if key in content:
                return extract_llm_response_text(content[key])
        return str(content)

    if hasattr(content, "text"):
        return extract_llm_response_text(getattr(content, "text"))

    return str(content)
