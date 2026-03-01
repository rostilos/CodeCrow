"""
Shared, language-agnostic patterns for extracting code symbols from diffs and source text.

Used by:
- stage_1_file_review (duplication query building)
- stage_2_cross_file  (cross-module RAG query building)
- diff_processor      (oversized diff summarisation)

All patterns are intentionally generic — no framework or language-specific logic.
"""

import re
from typing import List, Set, Tuple

# ─── Compiled patterns for extracting signatures from diff lines (+/- prefixed) ──

DIFF_SIGNATURE_PATTERNS = [
    # def/class declarations (Python, Ruby, etc.)
    re.compile(r'^[+-]\s*((?:async\s+)?(?:def|class)\s+\w+[^:]*)', re.MULTILINE),
    # Access-modified method/function declarations (Java, C#, TypeScript, etc.)
    re.compile(
        r'^[+-]\s*((?:public|private|protected|static|final|abstract|async|export|override)\s+.*?\w+\s*\([^)]*\))',
        re.MULTILINE,
    ),
    # Receiver-style method declarations (Go: func (r *Type) Method(...))
    re.compile(r'^[+-]\s*(func\s+(?:\([^)]+\)\s+)?\w+\s*\([^)]*\))', re.MULTILINE),
    # Interface/trait/enum/struct/type declarations
    re.compile(r'^[+-]\s*((?:interface|trait|enum|struct|type)\s+\w+)', re.MULTILINE),
]

# ─── Raw-text patterns (no +/- prefix) for extracting names from source ─────

# Function/method name extractor (multi-language)
_RE_FUNC_NAME = re.compile(
    r'(?:public|private|protected|static|async|export)?\s*'
    r'(?:def|function|func|fn)\s+(\w+)\s*\(',
)

# Class/interface/trait/struct/enum name extractor
_RE_CLASS_NAME = re.compile(
    r'(?:class|interface|trait|struct|enum)\s+(\w+)',
)

# Import/require/use statement module extractor
_RE_IMPORT_REF = re.compile(
    r'(?:import|from|require|use|include)\s+["\']?([.\w/]+)',
)

# Decorator/annotation extractor
_RE_DECORATOR = re.compile(r'@(\w+)')

# Event/observer string pattern (snake_case with ≥3 segments)
_RE_EVENT_STRING = re.compile(r'["\'](\w+(?:_\w+){2,})["\']')

# SQL table operations
_RE_SQL_TABLE = re.compile(
    r'(?:DELETE\s+FROM|SELECT\s+.*?FROM|UPDATE)\s+[`"\']?(\w+)',
    re.IGNORECASE,
)

# ─── Common skip-lists ──────────────────────────────────────────────────────

# Function names too generic to produce useful RAG queries
SKIP_FUNC_NAMES: Set[str] = {
    '__construct', '__destruct', '__init__', '__str__', '__repr__',
    'execute', 'run', 'get', 'set', 'init', 'setup', 'teardown',
    'main', 'test', 'handle', 'process',
}

# Decorators that are standard boilerplate, not domain-specific
SKIP_DECORATORS: Set[str] = {
    'override', 'test', 'param', 'return', 'staticmethod',
    'classmethod', 'property', 'abstractmethod',
}

# Event keywords that signal an event/observer pattern
EVENT_KEYWORDS: Tuple[str, ...] = (
    'save', 'load', 'delete', 'submit', 'create', 'update',
    'dispatch', 'emit', 'notify', 'publish', 'trigger',
)

# Config file extensions
CONFIG_EXTENSIONS: Tuple[str, ...] = (
    '.xml', '.yml', '.yaml', '.json', '.toml', '.properties', '.ini', '.cfg',
)


def extract_function_names(text: str, min_length: int = 4) -> List[str]:
    """Extract unique function/method names from raw source or diff text."""
    names = _RE_FUNC_NAME.findall(text)
    return [
        n for n in dict.fromkeys(names)
        if n.lower() not in SKIP_FUNC_NAMES and len(n) > min_length
    ]


def extract_class_names(text: str, min_length: int = 3) -> List[str]:
    """Extract unique class/interface/trait/struct/enum names."""
    names = _RE_CLASS_NAME.findall(text)
    return [n for n in dict.fromkeys(names) if len(n) > min_length]


def extract_import_modules(text: str, min_length: int = 3) -> List[str]:
    """Extract unique short module names from import statements."""
    refs = _RE_IMPORT_REF.findall(text)
    shorts = []
    seen = set()
    for imp in refs:
        short = imp.split('/')[-1].split('.')[-1] if '/' in imp or '.' in imp else imp
        if len(short) > min_length and short not in seen:
            seen.add(short)
            shorts.append(short)
    return shorts


def extract_decorators(text: str) -> List[str]:
    """Extract unique domain-specific decorator/annotation names."""
    names = _RE_DECORATOR.findall(text)
    return [n for n in dict.fromkeys(names) if n.lower() not in SKIP_DECORATORS]


def extract_event_names(text: str) -> List[str]:
    """Extract event/observer pattern strings (snake_case with ≥3 segments)."""
    names = _RE_EVENT_STRING.findall(text)
    return [
        n for n in dict.fromkeys(names)
        if any(kw in n.lower() for kw in EVENT_KEYWORDS)
    ]


def extract_sql_tables(text: str, min_length: int = 3) -> List[str]:
    """Extract table names from SQL operations."""
    names = _RE_SQL_TABLE.findall(text)
    return [n for n in dict.fromkeys(names) if len(n) > min_length]
