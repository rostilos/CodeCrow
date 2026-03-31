"""
Task Context Builder.

Shared utility that extracts structured context from Jira/task-management
data — including acceptance criteria parsed from the description — and
builds a rich text block suitable for injection into LLM prompts.

Used by both the QA doc orchestrator (multi-stage) and the review orchestrator
whenever task context enrichment is needed.
"""
import re
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

# ── Acceptance-criteria extraction patterns ─────────────────────────
# Common header patterns found in Jira descriptions (case-insensitive):
#   - "Acceptance Criteria"
#   - "AC:" / "ACs:"
#   - "Definition of Done"
_AC_HEADER_PATTERNS = [
    # Standard Jira header: "h3. Acceptance Criteria" or "## Acceptance Criteria"
    r'(?:^|\n)\s*(?:h[1-6]\.\s*|#{1,6}\s*)?(acceptance\s+criteria|a\.?c\.?s?|definition\s+of\s+done)\s*[:：]?\s*\n',
    # Bold header: "*Acceptance Criteria*" or "**Acceptance Criteria**"
    r'(?:^|\n)\s*\*{1,2}(acceptance\s+criteria|a\.?c\.?s?|definition\s+of\s+done)\*{1,2}\s*[:：]?\s*\n',
]
_AC_HEADER_REGEX = re.compile(
    '|'.join(f'({p})' for p in _AC_HEADER_PATTERNS),
    re.IGNORECASE | re.MULTILINE,
)

# Patterns that signal the END of an AC block (next section starts):
_SECTION_BREAK_REGEX = re.compile(
    r'(?:^|\n)\s*(?:h[1-6]\.\s*|#{1,6}\s+|\*{1,2}[A-Z])',
    re.MULTILINE,
)


def extract_acceptance_criteria(description: Optional[str]) -> Optional[str]:
    """
    Parse acceptance criteria from a Jira task description.

    Looks for common AC header patterns and extracts the content until the
    next section break.  Returns ``None`` if no AC block is found.
    """
    if not description:
        return None

    match = _AC_HEADER_REGEX.search(description)
    if not match:
        return None

    # Start of AC content is right after the header match
    start = match.end()
    rest = description[start:]

    # Find the next section header (if any) to bound the AC block
    end_match = _SECTION_BREAK_REGEX.search(rest)
    if end_match:
        ac_text = rest[:end_match.start()]
    else:
        ac_text = rest

    ac_text = ac_text.strip()
    if not ac_text or len(ac_text) < 10:
        return None

    return ac_text


def build_task_context(
    task_context: Optional[Dict[str, str]],
    *,
    include_acceptance_criteria: bool = True,
    max_description_length: int = 2000,
) -> str:
    """
    Build a rich, LLM-friendly task context string from task metadata.

    Parameters
    ----------
    task_context
        Dictionary with keys: task_key, task_summary, description,
        status, task_type, priority  (all optional).
    include_acceptance_criteria
        If True, parse AC from description and add a dedicated section.
    max_description_length
        Truncate the raw description to this length (AC is separate).

    Returns
    -------
    A formatted Markdown block, or an empty string if no context is available.
    """
    if not task_context:
        return ""

    parts: List[str] = []
    task_key = task_context.get("task_key", "")
    task_summary = task_context.get("task_summary", "")
    description = task_context.get("description", "")
    status = task_context.get("status", "")
    task_type = task_context.get("task_type", "")
    priority = task_context.get("priority", "")

    # Header line
    if task_key or task_summary:
        header = f"**{task_key}**" if task_key else ""
        if task_summary:
            header = f"{header} — {task_summary}" if header else task_summary
        parts.append(f"### Task: {header}")

    # Metadata chips
    meta_chips: List[str] = []
    if task_type:
        meta_chips.append(f"Type: {task_type}")
    if status:
        meta_chips.append(f"Status: {status}")
    if priority:
        meta_chips.append(f"Priority: {priority}")
    if meta_chips:
        parts.append(" | ".join(meta_chips))

    # Acceptance Criteria (parsed from description)
    ac_text: Optional[str] = None
    if include_acceptance_criteria and description:
        ac_text = extract_acceptance_criteria(description)
        if ac_text:
            parts.append("")
            parts.append("#### Acceptance Criteria")
            parts.append(ac_text)

    # Full description (truncated, without the AC block to avoid duplication)
    if description:
        desc_to_show = description
        if ac_text:
            # Remove the AC block from the description to avoid duplication
            ac_start = description.lower().find("acceptance criteria")
            if ac_start == -1:
                ac_start = description.lower().find("definition of done")
            if ac_start > 0:
                desc_to_show = description[:ac_start].strip()
            elif ac_start == 0:
                # AC was at the very beginning — show everything after it
                desc_to_show = ""

        if desc_to_show:
            if len(desc_to_show) > max_description_length:
                desc_to_show = desc_to_show[:max_description_length] + "…"
            parts.append("")
            parts.append("#### Description")
            parts.append(desc_to_show)

    result = "\n".join(parts).strip()
    if not result:
        return ""

    return result


def build_task_context_for_prompt(task_context: Optional[Dict[str, str]]) -> str:
    """
    Convenience wrapper that returns a prompt-ready task context block,
    or a fallback message if no context is available.
    """
    ctx = build_task_context(task_context)
    if not ctx:
        return "No task context available."
    return ctx
