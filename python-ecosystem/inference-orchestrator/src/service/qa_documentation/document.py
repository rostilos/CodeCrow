"""QA document format, titles, provenance markers, and response adaptation."""
import re
from typing import Any, Dict, Optional

from utils.llm_json import parse_json_object


TEST_CASE_SENTINELS = (
    "<!-- codecrow-test-cases:start -->",
    "<!-- codecrow-test-cases:content -->",
    "<!-- codecrow-test-cases:end -->",
)
ENVIRONMENT_SENTINELS = (
    "<!-- codecrow-environment:start -->",
    "<!-- codecrow-environment:content -->",
    "<!-- codecrow-environment:end -->",
)

def has_complete_shareable_sections(documentation: Optional[str]) -> bool:
    test_cases = extract_sentinel_section(documentation, TEST_CASE_SENTINELS)
    environment = extract_sentinel_section(documentation, ENVIRONMENT_SENTINELS)
    if test_cases is None or environment is None or test_cases[1] > environment[0]:
        return False
    return re.search(
        r"(?mi)^\s*\*\*.+?\*\*\s*\((?:HIGH|MEDIUM|LOW)\)",
        test_cases[3],
    ) is not None


def contains_extractable_test_cases(documentation: Optional[str]) -> bool:
    test_cases = extract_sentinel_section(documentation, TEST_CASE_SENTINELS)
    if test_cases is None:
        return False
    return re.search(
        r"(?mi)^\s*\*\*.+?\*\*\s*\((?:HIGH|MEDIUM|LOW)\)",
        test_cases[3],
    ) is not None


def extract_sentinel_section(
    documentation: Optional[str],
    sentinels: tuple[str, str, str],
) -> Optional[tuple[int, int, str, str]]:
    """Extract one exact sentinel block without interpreting its localized heading."""
    if not documentation:
        return None
    start_marker, content_marker, end_marker = sentinels
    if any(documentation.count(marker) != 1 for marker in sentinels):
        return None

    start = documentation.find(start_marker)
    content_start = documentation.find(content_marker, start + len(start_marker))
    end = documentation.find(end_marker, content_start + len(content_marker))
    if start < 0 or content_start < 0 or end < 0:
        return None

    heading = documentation[start + len(start_marker):content_start].strip()
    content = documentation[content_start + len(content_marker):end].strip()
    if not heading.startswith("#") or "\n" in heading or not content:
        return None
    return start, end + len(end_marker), heading, content


def normalize_document_title(documentation: str, fallback_title: str) -> str:
    """Replace a leaked empty-title sentinel in the rendered guide heading."""
    return re.sub(
        r"(?mi)^(#\s+QA Testing Guide\s*[—–-]\s*)(?:N\s*/?\s*A|None|null)\s*$",
        lambda match: f"{match.group(1)}{fallback_title}",
        documentation,
        count=1,
    )


def display_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized or normalized.casefold() in {"n/a", "na", "none", "null"}:
        return None
    return normalized


def build_placeholders(
    project_name: str,
    pr_number: Optional[int],
    issues_found: int,
    files_analyzed: int,
    pr_metadata: Dict[str, Any],
    task_context_dict: Optional[Dict[str, str]],
    task_context_block: str,
    diff: Optional[str],
    source_branch: str = "N/A",
    target_branch: str = "N/A",
    output_language: Optional[str] = "English",
) -> Dict[str, str]:
    """Build the placeholder dictionary used for prompt formatting."""
    task_ctx = task_context_dict or {}
    effective_language = output_language if output_language and output_language.strip() else "English"
    normalized_project_name = display_value(project_name)
    task_key = display_value(task_ctx.get("task_key"))
    task_summary = display_value(task_ctx.get("task_summary"))
    pr_title = (
        display_value(pr_metadata.get("prTitle"))
        or task_summary
        or task_key
        or (f"PR #{pr_number}" if pr_number is not None else None)
        or normalized_project_name
        or "QA documentation"
    )
    return {
        "project_name": normalized_project_name or "Unknown",
        "pr_number": str(pr_number) if pr_number else "N/A",
        "task_key": task_key or "N/A",
        "task_summary": task_summary or "N/A",
        "source_branch": source_branch,
        "target_branch": target_branch,
        "pr_title": pr_title,
        "pr_description": pr_metadata.get("prDescription", "") or "",
        "issues_found": str(issues_found),
        "files_analyzed": str(files_analyzed),
        "analysis_summary": pr_metadata.get("analysisSummary", "No analysis summary available."),
        "diff": diff or "No diff available.",
        "task_context": task_context_block,
        "output_language": effective_language,
    }


def extract_documented_prs(previous_documentation: Optional[str]) -> set:
    """Extract PR numbers from the tracking marker in previous doc."""
    import re
    if not previous_documentation:
        return set()
    match = re.search(r'<!-- codecrow-qa-autodoc:prs=([\d,]+) -->', previous_documentation)
    if match:
        try:
            return {int(p) for p in match.group(1).split(',') if p.strip()}
        except ValueError:
            return set()
    return set()


def extract_text(response) -> str:
    """Extract text from LangChain response (handles Gemini list content)."""
    if hasattr(response, "content"):
        content = response.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and "text" in block:
                    parts.append(block["text"])
            return "\n".join(parts)
        return str(content)
    if isinstance(response, str):
        return response
    return str(response)


def parse_json_from_response(text: str) -> Optional[Dict[str, Any]]:
    return parse_json_object(text, allow_trailing_commas=True)
