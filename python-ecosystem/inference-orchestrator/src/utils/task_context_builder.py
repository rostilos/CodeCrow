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
from dataclasses import dataclass
from typing import Optional, Dict, List, Any

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


@dataclass(frozen=True)
class _AcceptanceCriteriaBlock:
    header_start: int
    content_start: int
    content_end: int
    text: str


def _find_acceptance_criteria_block(description: Optional[str]) -> Optional[_AcceptanceCriteriaBlock]:
    if not description:
        return None

    match = _AC_HEADER_REGEX.search(description)
    if not match:
        return None

    content_start = match.end()
    rest = description[content_start:]
    end_match = _SECTION_BREAK_REGEX.search(rest)
    content_end = content_start + (end_match.start() if end_match else len(rest))
    ac_text = description[content_start:content_end].strip()
    if not ac_text or len(ac_text) < 10:
        return None

    return _AcceptanceCriteriaBlock(
        header_start=match.start(),
        content_start=content_start,
        content_end=content_end,
        text=ac_text,
    )


def extract_acceptance_criteria(description: Optional[str]) -> Optional[str]:
    """
    Parse acceptance criteria from a Jira task description.

    Looks for common AC header patterns and extracts the content until the
    next section break.  Returns ``None`` if no AC block is found.
    """
    block = _find_acceptance_criteria_block(description)
    return block.text if block else None


def _value(task_context: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = task_context.get(key)
        if value is not None:
            return str(value)
    return ""


def build_task_context(
    task_context: Optional[Dict[str, Any]],
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
    task_key = _value(task_context, "task_key", "taskKey", "key")
    task_summary = _value(task_context, "task_summary", "taskSummary", "summary")
    description = _value(task_context, "description")
    status = _value(task_context, "status")
    task_type = _value(task_context, "task_type", "taskType", "type")
    priority = _value(task_context, "priority")
    assignee = _value(task_context, "assignee")
    reporter = _value(task_context, "reporter")
    web_url = _value(task_context, "web_url", "webUrl", "url")

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
    if assignee:
        meta_chips.append(f"Assignee: {assignee}")
    if reporter:
        meta_chips.append(f"Reporter: {reporter}")
    if meta_chips:
        parts.append(" | ".join(meta_chips))
    if web_url:
        parts.append(f"URL: {web_url}")

    # Acceptance Criteria (parsed from description)
    ac_block: Optional[_AcceptanceCriteriaBlock] = None
    ac_text: Optional[str] = None
    if include_acceptance_criteria and description:
        ac_block = _find_acceptance_criteria_block(description)
        ac_text = ac_block.text if ac_block else None
        if ac_text:
            parts.append("")
            parts.append("#### Acceptance Criteria")
            parts.append(ac_text)

    # Full description (truncated, without the AC block to avoid duplication)
    if description:
        desc_to_show = description
        if ac_block:
            # Remove only the extracted AC block to avoid duplication while
            # preserving later technical notes or implementation details.
            desc_to_show = (
                description[:ac_block.header_start]
                + description[ac_block.content_end:]
            ).strip()

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


def build_task_context_for_prompt(task_context: Optional[Dict[str, Any]]) -> str:
    """
    Convenience wrapper that returns a prompt-ready task context block,
    or a fallback message if no context is available.
    """
    ctx = build_task_context(task_context)
    if not ctx:
        return "No task context available."
    return ctx
