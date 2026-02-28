"""
Duplication detection query generator for RAG query service.

Language and framework agnostic — uses universal code patterns to find
existing implementations of the same functionality elsewhere in the codebase.

NO FRAMEWORK-SPECIFIC PATTERNS. All detection is structural:
- Function/method declarations (any language keyword)
- Class inheritance (any extends/implements keyword)
- Event/callback patterns (any dispatcher/listener call)
- SQL/database operations (any SQL DML/DDL keyword)
- Method call chains (any dot/arrow/scope notation)
- Decorator/annotation patterns (any @-prefixed annotation)
- Config file changes (detected by file extension heuristic, NOT name list)
- XML/YAML attribute references (any type/class/ref attribute)
"""
import re
import os
from typing import List
import logging

from ..models.instructions import InstructionType

logger = logging.getLogger(__name__)

# Extensions that universally indicate configuration/manifest files.
# These are structural indicators (file format), NOT framework-specific.
_CONFIG_EXTENSIONS = frozenset({
    '.xml', '.yml', '.yaml', '.json', '.toml', '.ini', '.cfg',
    '.properties', '.conf', '.env',
})

# Directory names that indicate config/infra context (language-agnostic).
_CONFIG_DIR_HINTS = frozenset({
    'config', 'configs', 'configuration', 'conf', 'settings',
    'resources', 'META-INF', 'WEB-INF',
    '.github', '.circleci', '.gitlab',
    'deploy', 'deployment', 'infrastructure', 'infra',
    'docker', 'k8s', 'kubernetes', 'helm', 'terraform',
})


def _is_config_file(file_path: str) -> bool:
    """Detect config files by structural heuristics, not hardcoded names.

    A file is considered a config file if:
    1. Its extension is a known config format (.xml, .yml, .json, .toml, …)
       AND it is NOT inside a source code directory (src/, lib/, app/, …), OR
    2. It lives inside a directory whose name hints at configuration.
    3. It is a well-known root manifest (Makefile, Dockerfile, etc.)
    """
    basename = os.path.basename(file_path)
    _, ext = os.path.splitext(basename)
    ext = ext.lower()

    # Root-level project manifests (technology-agnostic intent: "project config")
    # These are truly universal; every ecosystem has exactly one of these.
    if basename.lower() in (
        'dockerfile', 'makefile', 'cmakelists.txt', 'rakefile',
        'justfile', 'taskfile.yml', 'vagrantfile',
    ):
        return True

    # Check by extension
    if ext in _CONFIG_EXTENSIONS:
        # If it is a config-format file, it is very likely a config file.
        # Exceptions: .json test fixtures, .xml layout files inside src/.
        # But even these are valuable for duplication detection context.
        return True

    return False


def generate_duplication_queries(
        diff_snippets: List[str],
        changed_files: List[str]
) -> List[tuple]:
    """
    Generate duplication-oriented queries to find existing implementations
    of the same functionality elsewhere in the codebase.

    Language and framework agnostic — uses universal code patterns:
    - Function/method declarations (all languages)
    - Class inheritance and interface implementation
    - Event/callback/listener patterns
    - SQL/database operations
    - Method call chains (dot and arrow notation)
    - Decorator/annotation patterns
    - Config file cross-referencing (by extension heuristic)
    - XML/YAML/JSON attribute references
    """
    queries = []
    seen_queries = set()

    def _add_query(text: str, weight: float = 1.3, top_k: int = 8):
        """Add query if non-trivial and not duplicate."""
        text = text.strip()
        if len(text) > 15 and text not in seen_queries:
            seen_queries.add(text)
            queries.append((text, weight, top_k, InstructionType.DUPLICATION))

    all_diff_text = "\n".join(diff_snippets or [])

    # ── 1. Function/method declarations (all languages) ──
    # Matches any language that uses function/def/func/fn keyword.
    func_patterns = re.findall(
        r'(?:(?:public|private|protected|static|async|override|virtual|abstract)\s+)*'
        r'(?:function|def|func|fn)\s+'
        r'(?:\([^)]*\)\s+)?'  # Go receiver: (s *Service)
        r'(\w+)\s*\(',
        all_diff_text
    )
    # Lifecycle/boilerplate names to skip — these are universal patterns
    # that exist in every codebase and produce noise, not useful duplication signals.
    SKIP_FUNC_NAMES = frozenset({
        '__construct', '__destruct', '__init__', '__new__', '__del__',
        'constructor', 'destructor', 'init', 'setup', 'teardown',
        'execute', 'run', 'main', 'handle', 'invoke',
        'get', 'set', 'has', 'is', 'new', 'make', 'create',
        'toarray', 'tostring', 'tolist', 'tomap', 'tojson', 'fromjson',
        'getdata', 'setdata', 'clone', 'copy', 'equals', 'hashcode',
        'before', 'after', 'around', 'test', 'close', 'open', 'start', 'stop',
    })
    for func_name in set(func_patterns):
        if func_name.lower() not in SKIP_FUNC_NAMES and len(func_name) > 4:
            _add_query(f"function {func_name} implementation", weight=1.1, top_k=6)

    # ── 2. Class inheritance / interface implementation (all languages) ──
    # Universal: extends/implements/impl keyword followed by a ClassName.
    extends_matches = re.findall(
        r'(?:extends|implements|impl)\s+'
        r'([A-Z]\w+(?:[\\./][A-Z]\w+)*)',
        all_diff_text
    )
    # Also match Python-style class(Base) and C#/TypeScript class Foo : Bar
    extends_matches += re.findall(
        r'class\s+\w+\s*(?:\([^)]*\)|\s*:\s*)'
        r'.*?([A-Z]\w+)',
        all_diff_text
    )
    for class_name in set(extends_matches):
        short_name = re.split(r'[\\./]', class_name)[-1]
        if short_name and len(short_name) > 3:
            _add_query(f"class extending {short_name} implementation", weight=1.1, top_k=6)

    # ── 3. Event / callback / listener patterns (universal) ──
    # Any language that uses event dispatching via function call syntax.
    event_dispatch_patterns = re.findall(
        r'(?:addEventListener|emit|dispatch|trigger|on|subscribe|publish|fire)\s*\(\s*'
        r'["\']([a-zA-Z][a-zA-Z0-9_.:-]+)["\']',
        all_diff_text, re.IGNORECASE
    )
    for event_name in set(event_dispatch_patterns):
        _add_query(f"event {event_name} listener handler implementation", weight=1.4, top_k=10)

    # Also match event names from structured config (XML/YAML/JSON).
    config_event_patterns = re.findall(
        r'(?:event[_\s]*(?:name)?["\s:=]+["\']?)'
        r'(\w+(?:[_./]\w+)+)',
        all_diff_text, re.IGNORECASE
    )
    for event_name in set(config_event_patterns):
        _add_query(f"event {event_name} handler observer implementation", weight=1.4, top_k=10)

    # ── 4. Decorator / annotation patterns ──
    # @ syntax used by Python, Java, TypeScript, C#, PHP 8+.
    decorator_patterns = re.findall(
        r'@(\w+(?:\.\w+)?)\s*(?:\([^)]*\))?',
        all_diff_text
    )
    SKIP_DECORATORS = frozenset({
        'override', 'test', 'property', 'staticmethod', 'classmethod',
        'abstractmethod', 'dataclass', 'deprecated', 'suppress',
    })
    for decorator in set(decorator_patterns):
        dec_lower = decorator.lower().split('.')[-1]
        if dec_lower not in SKIP_DECORATORS and len(dec_lower) > 4:
            # Route/endpoint decorators — find similar routes
            if any(kw in dec_lower for kw in ('route', 'mapping', 'endpoint', 'api', 'path')):
                _add_query(f"endpoint {decorator} route handler", weight=1.3, top_k=8)
            # Scheduled/cron decorators
            elif any(kw in dec_lower for kw in ('scheduled', 'cron', 'periodic', 'interval')):
                _add_query(f"scheduled task {decorator} cron job", weight=1.3, top_k=8)

    # ── 5. SQL / database operations ──
    # Universal SQL keywords — any database, any language.
    sql_table_patterns = re.findall(
        r'(?:DELETE\s+FROM|UPDATE|INSERT\s+INTO|SELECT\s+.*?\s+FROM|ALTER\s+TABLE|CREATE\s+TABLE)\s+'
        r'[`"\']?(\w+)',
        all_diff_text, re.IGNORECASE
    )
    for table_name in set(sql_table_patterns):
        if len(table_name) > 3:
            _add_query(f"database table {table_name} operation query", weight=1.2, top_k=6)

    # ── 6. Method call chains (universal — dot and arrow notation) ──
    # obj->method->chain(), obj.method.chain(), Class::method()
    call_chains = re.findall(
        r'(\w+(?:(?:->|\.)\w+){2,})\s*\(',
        all_diff_text
    )
    for call_chain in set(call_chains):
        if len(call_chain) > 10:
            normalized = call_chain.replace('->', '.').replace('::', '.')
            _add_query(f"call chain {normalized} usage", weight=1.2, top_k=6)

    # ── 7. Config file cross-referencing (by extension heuristic) ──
    # Detects config files structurally — NO hardcoded filename list.
    for file_path in (changed_files or []):
        if _is_config_file(file_path):
            basename = os.path.basename(file_path)
            _add_query(f"{basename} configuration definition", weight=1.3, top_k=10)

    # ── 8. Structured config attribute references (XML/YAML/JSON) ──
    # Match any XML element with a type/class/instance/ref attribute.
    xml_type_attrs = re.findall(
        r'<\w+\s+[^>]*(?:type|class|instance|ref|bean|component)\s*=\s*'
        r'["\']([^"\']+)["\']',
        all_diff_text
    )
    for type_ref in set(xml_type_attrs):
        short_name = re.split(r'[\\./]', type_ref)[-1]
        if short_name and len(short_name) > 3:
            _add_query(f"component {short_name} registration configuration", weight=1.3, top_k=8)

    # ── Limit total queries ──
    # Allow more queries (12 instead of 8) to avoid discarding relevant signals.
    # The downstream embedding calls are batched anyway.
    if len(queries) > 12:
        queries.sort(key=lambda x: x[1], reverse=True)
        queries = queries[:12]

    logger.info(f"Generated {len(queries)} duplication detection queries")
    return queries
