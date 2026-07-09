import logging
import os
from typing import List, Dict, Any
from langchain_core.tools import tool
from model.output_schemas import CodeReviewIssue
from model.dtos import ReviewRequestDto
from service.review.orchestrator.agents import extract_llm_response_text
from service.review.orchestrator.json_utils import load_json_with_local_repairs
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


VERIFICATION_MAX_TOOL_ROUNDS = max(1, _env_int("REVIEW_VERIFICATION_MAX_TOOL_ROUNDS", 4))

# Global state for the tool to access file contents
_FILE_CONTENTS_CACHE: Dict[str, str] = {}

@tool
def search_file_content(file_path: str, search_string: str) -> str:
    """
    Searches for a specific string within the full content of a file.
    Use this tool to verify if a variable, method, or import actually exists in the file.
    
    Args:
        file_path: The path to the file to search in.
        search_string: The exact string to search for (e.g., a variable name or method signature).
        
    Returns:
        A string indicating whether the search_string was found in the file or not.
    """
    content = _FILE_CONTENTS_CACHE.get(file_path)
    if not content:
        return f"Error: File content for '{file_path}' not available in memory."
    
    if search_string in content:
        return f"Found: The string '{search_string}' exists in '{file_path}'."
    else:
        return f"Not Found: The string '{search_string}' does NOT exist in '{file_path}'."

class VerificationResult(BaseModel):
    """Result of the verification agent."""
    issue_ids_to_drop: List[str] = Field(
        description="List of issue IDs that were verified as false positives (e.g., the symbol actually exists)."
    )


def _issue_field(issue: CodeReviewIssue, name: str) -> str:
    value = getattr(issue, name, "")
    if value is None:
        return ""
    if value.__class__.__module__.startswith("unittest.mock"):
        return ""
    return str(value)


def _verification_issue_id(index: int, issue: CodeReviewIssue) -> str:
    existing_id = _issue_field(issue, "id").strip()
    return existing_id or f"issue_{index}"


def _tool_call_attr(tool_call: Any, name: str) -> Any:
    if isinstance(tool_call, dict):
        return tool_call.get(name)
    return getattr(tool_call, name, None)


def _invoke_search_file_content(args: Any) -> str:
    if not isinstance(args, dict):
        return "Error: search_file_content arguments must be an object."

    file_path = str(args.get("file_path") or "")
    search_string = str(args.get("search_string") or "")
    if not file_path or not search_string:
        return "Error: file_path and search_string are required."

    if hasattr(search_file_content, "invoke"):
        return search_file_content.invoke({
            "file_path": file_path,
            "search_string": search_string,
        })
    return search_file_content(file_path=file_path, search_string=search_string)


def _parse_verification_result(content: str) -> VerificationResult:
    _, data = load_json_with_local_repairs(content)
    return VerificationResult(**data)


async def _run_verification_tool_loop(llm, prompt: str) -> VerificationResult:
    if not hasattr(llm, "bind_tools"):
        raise RuntimeError("LLM does not support tool binding")

    llm_with_tools = llm.bind_tools([search_file_content])
    messages: List[Any] = [
        {"role": "system", "content": "You verify code-review findings and return only valid JSON."},
        {"role": "user", "content": prompt},
    ]

    for iteration in range(VERIFICATION_MAX_TOOL_ROUNDS):
        response = await llm_with_tools.ainvoke(messages)
        messages.append(response)
        tool_calls = getattr(response, "tool_calls", None) or []

        if not tool_calls:
            content = extract_llm_response_text(response)
            if not content.strip():
                raise ValueError("verification response contained no JSON")
            logger.info("Stage 1.5: Verification completed in %d LLM call(s)", iteration + 1)
            return _parse_verification_result(content)

        for tool_call in tool_calls:
            name = _tool_call_attr(tool_call, "name")
            args = _tool_call_attr(tool_call, "args") or {}
            tool_call_id = _tool_call_attr(tool_call, "id") or f"verification_tool_{iteration}"

            if name != "search_file_content":
                tool_result = f"Error: unsupported tool '{name}'."
            else:
                tool_result = _invoke_search_file_content(args)

            messages.append({
                "role": "tool",
                "content": str(tool_result),
                "tool_call_id": tool_call_id,
            })

    raise TimeoutError(
        f"verification did not produce a final JSON response after "
        f"{VERIFICATION_MAX_TOOL_ROUNDS} tool round(s)"
    )


async def run_verification_agent(
    llm,
    issues: List[CodeReviewIssue],
    request: ReviewRequestDto
) -> List[CodeReviewIssue]:
    """
    Stage 1.5: LLM-Driven Verification.
    Uses an LLM with a local search tool to verify suspected false positive issues.
    """
    if not request.enrichmentData or not request.enrichmentData.fileContents:
        logger.info("Stage 1.5: No EnrichmentData available, skipping verification.")
        return issues

    # Populate the global cache for the tool
    global _FILE_CONTENTS_CACHE
    _FILE_CONTENTS_CACHE = {
        f.path: f.content for f in request.enrichmentData.fileContents if f.content
    }

    if not issues:
        logger.info("Stage 1.5: No issues found, skipping verification.")
        _FILE_CONTENTS_CACHE.clear()
        return issues

    logger.info(f"Stage 1.5: Verifying {len(issues)} issue(s) with LLM-selected checks...")

    verification_records = [
        (_verification_issue_id(index, issue), issue)
        for index, issue in enumerate(issues)
    ]

    # Prepare the prompt for the verification agent
    issues_json = "\n".join([
        (
            f"Verification ID: {verification_id}\n"
            f"Original ID: {_issue_field(issue, 'id') or '(none)'}\n"
            f"File: {_issue_field(issue, 'file')}\n"
            f"Severity: {_issue_field(issue, 'severity')}\n"
            f"Category: {_issue_field(issue, 'category')}\n"
            f"Title: {_issue_field(issue, 'title')}\n"
            f"Reason: {_issue_field(issue, 'reason')}\n"
            "---"
        )
        for verification_id, issue in verification_records
    ])

    prompt = f"""You are a Verification Agent for a code review system.
Your job is to verify whether the following issues are false positives using full file content.

You have access to a tool called `search_file_content`.
For each issue, decide whether checking exact strings in the file would help verify the claim.
Use the tool only when the issue depends on whether a symbol, method, import, line, or nearby code exists in the full file.
When checks are useful, issue all `search_file_content` calls together in the same tool round.

Drop an issue only when file-content evidence clearly proves it is a false positive.
Keep the issue when evidence is inconclusive or the issue is not verifiable with exact string search.

Issues to verify:
{issues_json}

Return ONLY a JSON object containing a list of `issue_ids_to_drop` for the issues that are false positives.
Use the exact Verification ID values above, not file names or generated explanations.
"""

    try:
        result = await _run_verification_tool_loop(llm, prompt)
        ids_to_drop = {
            str(issue_id).strip()
            for issue_id in result.issue_ids_to_drop
            if str(issue_id).strip()
        }
        logger.info(f"Stage 1.5: Agent identified {len(ids_to_drop)} false positives to drop.")

        final_issues = [
            issue
            for verification_id, issue in verification_records
            if verification_id not in ids_to_drop
        ]
        
    except Exception as e:
        logger.error(f"Stage 1.5 Verification failed: {e}")
        # Fallback: keep all issues if verification fails
        final_issues = issues
    finally:
        # Clean up the cache
        _FILE_CONTENTS_CACHE.clear()

    return final_issues
