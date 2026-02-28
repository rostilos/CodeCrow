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
    chunks = rag_context.get("relevant_code", []) or rag_context.get("chunks", [])
    if not chunks:
        return rag_context

    batch_basenames = {p.rsplit("/", 1)[-1] if "/" in p else p for p in batch_file_paths}
    batch_dirs = set()
    for p in batch_file_paths:
        parts = p.rsplit("/", 1)
        if len(parts) == 2:
            batch_dirs.add(parts[0])

    filtered = []
    for chunk in chunks:
        meta = chunk.get("metadata", {})
        chunk_path = meta.get("path") or chunk.get("path") or chunk.get("file_path", "")
        if not chunk_path:
            filtered.append(chunk)
            continue

        chunk_basename = chunk_path.rsplit("/", 1)[-1] if "/" in chunk_path else chunk_path
        chunk_dir = chunk_path.rsplit("/", 1)[0] if "/" in chunk_path else ""

        score = chunk.get("score", chunk.get("relevance_score", 0))
        if (chunk_basename in batch_basenames
                or chunk_dir in batch_dirs
                or any(chunk_path.endswith(bp) or bp.endswith(chunk_path) for bp in batch_file_paths)
                or score >= 0.8):
            filtered.append(chunk)

    if not filtered:
        return rag_context

    result = dict(rag_context)
    result["relevant_code"] = filtered
    return result
