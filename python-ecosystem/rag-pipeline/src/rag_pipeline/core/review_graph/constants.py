"""Existing graph query policies and semantic aliases."""
import re

_TASK_TERM = re.compile(r"[A-Za-z_][A-Za-z0-9_:$\\.\-/]{2,}")


_STOP_TERMS = {
    "about", "after", "again", "against", "analysis", "before", "being",
    "between", "change", "changed", "changes", "could", "during", "from",
    "have", "into", "might", "please", "review", "should", "that", "their",
    "there", "these", "this", "through", "what", "when", "where", "which",
    "with", "would",
}


_IMPACT_EDGE_WEIGHTS = {
    "CALLS": 1.0,
    "EXTENDS": 0.9,
    "INHERITANCE": 0.9,
    "INHERITS": 0.9,
    "IMPLEMENTS": 0.9,
    "OVERRIDES": 0.9,
    "TESTED_BY": 0.7,
    "REFERENCES": 0.6,
    "DEPENDS_ON": 0.6,
    "IMPORTS": 0.5,
    "IMPORTS_FROM": 0.5,
    "CONTAINS": 0.3,
}


_IMPACT_EDGE_DIRECTIONS = {
    "CALLS": "incoming",
    "EXTENDS": "incoming",
    "INHERITANCE": "incoming",
    "INHERITS": "incoming",
    "IMPLEMENTS": "incoming",
    "OVERRIDES": "incoming",
    "TESTED_BY": "outgoing",
    "REFERENCES": "incoming",
    "DEPENDS_ON": "incoming",
    "IMPORTS": "incoming",
    "IMPORTS_FROM": "incoming",
    "CONTAINS": "none",
}


_IMPACT_DEFAULT_EDGE_WEIGHT = 0.5


_IMPACT_DEFAULT_EDGE_DIRECTION = "incoming"


_IMPACT_DEPTH_DECAY = 0.6


_IMPACT_SCORE_FLOOR = 0.05


_MAX_SOURCE_WINDOW_CHARACTERS = 8000


_MIN_TRAVERSAL_TOKEN_BUDGET = 512


_MAX_RELATIONS_PER_EXPANSION = 500


_MAX_IMPACT_ROOTS = 500


_MAX_IMPACT_WORK_NODES = 5000


_MAX_IMPACT_WORK_RELATIONS = 10000


_MAX_RETURNED_FRONTIER = 25


_SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp", ".html",
    ".java", ".js", ".jsx", ".kt", ".kts", ".php", ".py", ".rb", ".rs",
    ".scala", ".sql", ".swift", ".ts", ".tsx", ".vue", ".xml", ".yaml",
    ".yml",
}


_RELATION_SEMANTIC_ALIASES = {
    "CALL": "CALLS",
    "CALLS": "CALLS",
    "CALLS_INSTANCE": "CALLS",
    "CALLS_INTRA_CLASS": "CALLS",
    "CALLS_RESOLVED_TARGET": "CALLS",
    "CALLS_STATIC": "CALLS",
    "CALLS_UNIQUE_CO_DECLARED_DEFINITION": "CALLS",
    "CONSUME": "CONSUMES",
    "CONSUMES": "CONSUMES",
    "CONTAIN": "CONTAINS",
    "CONTAINS": "CONTAINS",
    "DEPEND": "DEPENDS_ON",
    "DEPENDS": "DEPENDS_ON",
    "DEPENDS_ON": "DEPENDS_ON",
    "DEPENDS_ON_CONFIG_FIELD": "DEPENDS_ON",
    "DEPENDS_ON_INDEXER": "DEPENDS_ON",
    "DISPATCH": "DISPATCHES",
    "DISPATCHES": "DISPATCHES",
    "EXTEND": "EXTENDS",
    "EXTENDS": "EXTENDS",
    "HANDLE": "HANDLES",
    "HANDLES": "HANDLES",
    "IMPLEMENT": "IMPLEMENTS",
    "IMPLEMENTS": "IMPLEMENTS",
    "IMPORT": "IMPORTS",
    "IMPORTS": "IMPORTS",
    "IMPORTS_FROM": "IMPORTS_FROM",
    "INHERIT": "INHERITS",
    "INHERITANCE": "INHERITANCE",
    "INHERITS": "INHERITS",
    "LISTEN": "LISTENS",
    "LISTENS": "LISTENS",
    "OVERRIDE": "OVERRIDES",
    "OVERRIDES": "OVERRIDES",
    "PRODUCE": "PRODUCES",
    "PRODUCES": "PRODUCES",
    "PUBLISH": "PUBLISHES",
    "PUBLISHES": "PUBLISHES",
    "REFERENCE": "REFERENCES",
    "REFERENCES": "REFERENCES",
    "REFERENCES_DECLARED_FIELD": "REFERENCES",
    "REFERENCES_JSON_SCHEMA_TARGET": "REFERENCES",
    "RESOLVES_IMPORT": "IMPORTS",
    "RUNS_ON": "RUNS_ON",
    "TESTED_BY": "TESTED_BY",
    "TESTS": "TESTS",
    "TRIGGER": "TRIGGERS",
    "TRIGGERS": "TRIGGERS",
    "USES": "REFERENCES",
}
