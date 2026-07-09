"""
Compatibility helpers for older imports that used to extract language-specific
signatures from source text.

The review pipeline must not infer semantics from hardcoded language keywords,
framework terms, filenames, or extension lists. Active review stages now pass raw
diff snippets and structured parser metadata to retrieval/LLM layers instead.
These helpers therefore expose neutral identifier-token extraction only.
"""

import re
from typing import List, Set, Tuple

DIFF_SIGNATURE_PATTERNS = []
SKIP_FUNC_NAMES: Set[str] = set()
SKIP_DECORATORS: Set[str] = set()
EVENT_KEYWORDS: Tuple[str, ...] = ()
CONFIG_EXTENSIONS: Tuple[str, ...] = ()

_IDENTIFIER_TOKEN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_.:/-]{1,}\b")


def extract_identifier_tokens(text: str, min_length: int = 3, limit: int = 50) -> List[str]:
    """Return neutral identifier-like tokens without assigning semantic meaning."""
    if not text:
        return []
    tokens = [
        token
        for token in _IDENTIFIER_TOKEN.findall(text)
        if len(token) >= min_length
    ]
    return list(dict.fromkeys(tokens))[:limit]


def extract_function_names(text: str, min_length: int = 4) -> List[str]:
    """Compatibility wrapper; no longer detects functions by language keywords."""
    return extract_identifier_tokens(text, min_length=min_length)


def extract_class_names(text: str, min_length: int = 3) -> List[str]:
    """Compatibility wrapper; no longer detects classes by language keywords."""
    return extract_identifier_tokens(text, min_length=min_length)


def extract_import_modules(text: str, min_length: int = 3) -> List[str]:
    """Compatibility wrapper; no longer parses import syntax."""
    return extract_identifier_tokens(text, min_length=min_length)


def extract_decorators(text: str) -> List[str]:
    """Compatibility wrapper; no longer treats decorators specially."""
    return extract_identifier_tokens(text, min_length=1)


def extract_event_names(text: str) -> List[str]:
    """Compatibility wrapper; no longer detects event names by keyword."""
    return extract_identifier_tokens(text, min_length=3)


def extract_sql_tables(text: str, min_length: int = 3) -> List[str]:
    """Compatibility wrapper; no longer parses SQL operations."""
    return extract_identifier_tokens(text, min_length=min_length)
