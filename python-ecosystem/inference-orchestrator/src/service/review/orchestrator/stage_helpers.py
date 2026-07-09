"""
Shared helpers for stage execution: event emission, project rules, RAG filtering.
"""
import fnmatch
import json
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def emit_status(callback: Optional[Callable[[Dict], None]], state: str, message: str):
    if callback:
        callback({"type": "status", "state": state, "message": message})


def emit_progress(callback: Optional[Callable[[Dict], None]], percent: int, message: str):
    if callback:
        callback({"type": "progress", "percent": percent, "message": message})


def emit_error(callback: Optional[Callable[[Dict], None]], message: str):
    if callback:
        callback({"type": "error", "message": message})


def format_project_rules(
    rules_json: Optional[str],
    batch_file_paths: List[str],
) -> str:
    if not rules_json:
        return ""

    try:
        rules = json.loads(rules_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse project rules JSON — skipping custom rules")
        return ""

    if not isinstance(rules, list) or len(rules) == 0:
        return ""

    enforce_rules: List[Dict[str, Any]] = []
    suppress_rules: List[Dict[str, Any]] = []

    for rule in rules:
        patterns = rule.get("filePatterns", [])

        if not patterns:
            matches = True
        else:
            matches = any(
                fnmatch.fnmatch(fp, pat) or fnmatch.fnmatch(fp.split("/")[-1], pat)
                for fp in batch_file_paths
                for pat in patterns
            )

        if not matches:
            continue

        rule_type = rule.get("ruleType", "ENFORCE")
        if rule_type == "SUPPRESS":
            suppress_rules.append(rule)
        else:
            enforce_rules.append(rule)

    if not enforce_rules and not suppress_rules:
        return ""

    lines: List[str] = []
    lines.append("## Custom Project Rules")
    lines.append("The project maintainers have configured the following review rules.")
    lines.append("You MUST follow them — they override general guidelines when they conflict.\n")

    if enforce_rules:
        lines.append("### ENFORCE — you MUST flag violations of these rules:")
        for r in enforce_rules:
            title = r.get("title", "Untitled rule")
            desc = r.get("description", "")
            pats = r.get("filePatterns", [])
            pat_note = f" (applies to: {', '.join(pats)})" if pats else ""
            lines.append(f"- **{title}**{pat_note}: {desc}")
        lines.append("")

    if suppress_rules:
        lines.append("### SUPPRESS — you MUST NOT flag issues matching these rules:")
        for r in suppress_rules:
            title = r.get("title", "Untitled rule")
            desc = r.get("description", "")
            pats = r.get("filePatterns", [])
            pat_note = f" (applies to: {', '.join(pats)})" if pats else ""
            lines.append(f"- **{title}**{pat_note}: {desc}")
        lines.append("")

    return "\n".join(lines)


def format_project_rules_digest(rules_json: Optional[str]) -> str:
    if not rules_json:
        return ""

    try:
        rules = json.loads(rules_json)
    except (json.JSONDecodeError, TypeError):
        return ""

    if not isinstance(rules, list) or len(rules) == 0:
        return ""

    lines: List[str] = []
    for r in rules:
        rule_type = r.get("ruleType", "ENFORCE")
        title = r.get("title", "Untitled rule")
        lines.append(f"- [{rule_type}] {title}")

    return "\n".join(lines)


def filter_rag_chunks_for_batch(
    rag_context: Dict[str, Any],
    batch_file_paths: List[str],
) -> Optional[Dict[str, Any]]:
    """
    Compatibility wrapper.

    Stage 1 no longer removes fallback RAG chunks by path, basename, directory,
    or score before the LLM sees them. Stale/deleted/corrupt protections live in
    format_rag_context; semantic relevance belongs to retrieval/reranking/LLM.
    """
    return rag_context
