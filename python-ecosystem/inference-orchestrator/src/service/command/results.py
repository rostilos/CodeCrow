"""Shared command output text semantics for service and queue boundaries."""
from typing import Any

EMPTY_RESULT_SENTINELS = {
    "null",
    "none",
    "no output generated",
    "failed to generate summary",
    "i couldn't generate an answer. please try rephrasing your question.",
}


def has_usable_text(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text.lower() not in EMPTY_RESULT_SENTINELS


def string_or_empty(value: Any) -> str:
    return "" if value is None else str(value)

