"""Provider-neutral helpers for extracting text from model responses."""

from __future__ import annotations

from typing import Any


def extract_llm_response_text(response: Any) -> str:
    """Extract text across plain, LangChain-style, and multipart responses."""
    if not hasattr(response, "content"):
        return str(response)
    content = response.content
    if not isinstance(content, list):
        return str(content)

    text_parts = []
    for item in content:
        if isinstance(item, str):
            text_parts.append(item)
        elif isinstance(item, dict):
            if "text" in item:
                text_parts.append(item["text"])
            elif "content" in item:
                text_parts.append(item["content"])
        elif hasattr(item, "text"):
            text_parts.append(item.text)
    return "".join(text_parts)
