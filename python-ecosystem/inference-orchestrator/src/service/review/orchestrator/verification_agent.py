import logging
import os
import re
from contextvars import ContextVar
from typing import List, Dict, Any, Optional, Tuple
from langchain_core.tools import tool
from model.output_schemas import CodeReviewIssue
from model.dtos import ReviewRequestDto
from service.review.orchestrator.agents import extract_llm_response_text
from service.review.telemetry import observed_ainvoke
from service.review.execution_context import is_manifest_bound_v1
from service.review.orchestrator.json_utils import load_json_with_local_repairs
from utils.diff_processor import DiffProcessor, ProcessedDiff
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

# Compatibility fallback for direct tool callers/tests. Live reviews use a
# ContextVar below so concurrent analyses cannot overwrite each other's source.
_FILE_CONTENTS_CACHE: Dict[str, str] = {}
_ACTIVE_FILE_CONTENTS: ContextVar[Optional[Dict[str, str]]] = ContextVar(
    "verification_file_contents",
    default=None,
)

_IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9_$])[A-Za-z_$][A-Za-z0-9_$]*(?![A-Za-z0-9_$])")
_UNUSED_CLAIM_RE = re.compile(
    r"\b(?:unused|unreferenced)\b|\b(?:not|never)\s+(?:used|referenced)\b",
    re.IGNORECASE,
)
_IMPORT_CLAIM_RE = re.compile(
    r"\b(?:import|imports|dependency|dependencies|use statement|using directive)\b",
    re.IGNORECASE,
)
_NON_SYMBOL_WORDS = {
    "unused", "unreferenced", "not", "never", "used", "referenced",
    "import", "imports", "dependency", "dependencies", "use", "using",
    "statement", "directive", "class", "module", "symbol", "the", "an",
    "a", "in", "is", "but", "file", "template",
}


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
    active_contents = _ACTIVE_FILE_CONTENTS.get()
    content = (active_contents or _FILE_CONTENTS_CACHE).get(file_path)
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


def _verification_issue_id(index: int, _issue: CodeReviewIssue) -> str:
    # Producer IDs are advisory and are not unique across Stage 1 batches or
    # Stage 2. A verifier-local index keeps one rejection from deleting every
    # distinct finding that happened to reuse the same producer ID.
    return f"issue_{index}"


def _path_keys(path: str) -> List[str]:
    normalized = (path or "").lstrip("/")
    keys = [normalized] if normalized else []
    remainder = normalized
    while "/" in remainder:
        remainder = remainder.split("/", 1)[1]
        keys.append(remainder)
    return keys


def _add_path_evidence(mapping: Dict[str, Optional[str]], path: str, content: str) -> None:
    if not content:
        return
    for key in _path_keys(path):
        existing = mapping.get(key)
        if existing is None and key in mapping:
            continue
        if existing is not None and existing != content:
            # An ambiguous suffix must not be used as evidence for either file.
            mapping[key] = None
        else:
            mapping[key] = content


def _lookup_path_evidence(mapping: Dict[str, Optional[str]], path: str) -> Optional[str]:
    for key in _path_keys(path):
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _current_source_from_diff(diff_content: str) -> str:
    """Reconstruct visible post-change lines without language-specific parsing."""
    current_lines: List[str] = []
    for line in (diff_content or "").splitlines():
        if line.startswith(("diff --git ", "index ", "@@ ", "--- ", "+++ ")):
            continue
        if line.startswith("-"):
            continue
        if line.startswith(("+", " ")):
            current_lines.append(line[1:])
    return "\n".join(current_lines)


def _build_file_evidence(
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
) -> Dict[str, Optional[str]]:
    """Prefer complete current files, with current-side diff text as fallback."""
    evidence: Dict[str, Optional[str]] = {}
    enrichment = getattr(request, "enrichmentData", None)
    for file_content in getattr(enrichment, "fileContents", None) or []:
        if file_content.content and getattr(file_content, "skipped", False) is not True:
            _add_path_evidence(evidence, file_content.path, file_content.content)

    diff_source = processed_diff
    if diff_source is None:
        raw_diff = getattr(request, "deltaDiff", None) or getattr(request, "rawDiff", None)
        if raw_diff:
            diff_source = DiffProcessor().process(raw_diff)

    if diff_source:
        for diff_file in diff_source.get_included_files():
            # Complete enrichment is stronger evidence; only fill missing paths.
            if _lookup_path_evidence(evidence, diff_file.path) is None:
                current_source = _current_source_from_diff(diff_file.content)
                if current_source:
                    _add_path_evidence(evidence, diff_file.path, current_source)

    return evidence


def _unused_import_candidates(issue: CodeReviewIssue) -> List[str]:
    """
    Extract issue-named identifiers for an unused-import-like claim.

    This examines the review claim and its exact anchor, not source-language
    syntax. It therefore works across PHP, Java, Python, JS/TS, C#, and similar
    import forms without extension or framework checks.
    """
    title = _issue_field(issue, "title")
    reason = _issue_field(issue, "reason")
    snippet = _issue_field(issue, "codeSnippet")
    claim = f"{title}\n{reason}"
    if not snippet or not _UNUSED_CLAIM_RE.search(claim) or not _IMPORT_CLAIM_RE.search(claim):
        return []

    snippet_tokens = _IDENTIFIER_RE.findall(snippet)
    title_tokens = {token.casefold() for token in _IDENTIFIER_RE.findall(title)}
    candidates = []
    for token in snippet_tokens:
        normalized = token.casefold()
        if normalized in title_tokens and normalized not in _NON_SYMBOL_WORDS:
            candidates.append(token)
    return list(dict.fromkeys(candidates))


def _symbol_occurs_outside_anchor(symbol: str, snippet: str, evidence: str) -> bool:
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_$]){re.escape(symbol)}(?![A-Za-z0-9_$])"
    )
    # Compare occurrence counts instead of removing the exact anchor text. LLMs
    # occasionally preserve a unified-diff '+' prefix or normalize whitespace
    # in codeSnippet; exact replacement would then mistake the import itself for
    # a second reference and suppress a genuine finding.
    return len(pattern.findall(evidence)) > len(pattern.findall(snippet))


def _drop_deterministically_contradicted_issues(
    issues: List[CodeReviewIssue],
    evidence_by_path: Dict[str, Optional[str]],
) -> Tuple[List[CodeReviewIssue], List[str]]:
    """Drop only issues whose own named symbol is visibly used outside its import."""
    kept: List[CodeReviewIssue] = []
    dropped_ids: List[str] = []
    for index, issue in enumerate(issues):
        snippet = _issue_field(issue, "codeSnippet")
        evidence = _lookup_path_evidence(evidence_by_path, _issue_field(issue, "file"))
        candidates = _unused_import_candidates(issue)
        contradicted = bool(evidence and any(
            _symbol_occurs_outside_anchor(symbol, snippet, evidence)
            for symbol in candidates
        ))
        if contradicted:
            dropped_ids.append(_verification_issue_id(index, issue))
        else:
            kept.append(issue)
    return kept, dropped_ids


def run_deterministic_evidence_gate(
    issues: List[CodeReviewIssue],
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff] = None,
) -> List[CodeReviewIssue]:
    """Apply fail-closed contradiction checks to issues from any review stage."""
    if not issues:
        return issues

    evidence_by_path = _build_file_evidence(request, processed_diff)
    if not evidence_by_path:
        return issues

    filtered, dropped_ids = _drop_deterministically_contradicted_issues(
        issues,
        evidence_by_path,
    )
    if dropped_ids:
        logger.info(
            "Deterministic evidence gate dropped %d contradicted issue(s): %s",
            len(dropped_ids),
            dropped_ids,
        )
    return filtered


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
        response = await observed_ainvoke(
            llm_with_tools,
            messages,
            stage="verification",
            producer="verification_agent",
        )
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
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff] = None,
) -> List[CodeReviewIssue]:
    """
    Stage 1.5: LLM-Driven Verification.
    Uses an LLM with a local search tool to verify suspected false positive issues.
    """
    if not issues:
        logger.info("Stage 1.5: No issues found, skipping verification.")
        return issues

    evidence_by_path = _build_file_evidence(request, processed_diff)
    if not evidence_by_path:
        logger.info("Stage 1.5: No current-file or diff evidence available; skipping verification.")
        return issues

    issues, deterministic_drop_ids = _drop_deterministically_contradicted_issues(
        issues,
        evidence_by_path,
    )
    if deterministic_drop_ids:
        logger.info(
            "Stage 1.5: Deterministic evidence gate dropped %d contradicted issue(s): %s",
            len(deterministic_drop_ids),
            deterministic_drop_ids,
        )
    if not issues:
        return []

    enrichment = getattr(request, "enrichmentData", None)
    full_file_contents = {
        f.path: f.content
        for f in getattr(enrichment, "fileContents", None) or []
        if f.content and getattr(f, "skipped", False) is not True
    }
    if not full_file_contents:
        logger.info(
            "Stage 1.5: No complete file contents available; "
            "deterministic diff verification completed and LLM verification skipped."
        )
        return issues

    cache_token = _ACTIVE_FILE_CONTENTS.set(full_file_contents)

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
            f"Scope: {_issue_field(issue, 'scope')}\n"
            f"Exact source anchor: {_issue_field(issue, 'codeSnippet')}\n"
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
        if is_manifest_bound_v1(request):
            raise
        # Fallback: keep all issues if verification fails
        final_issues = issues
    finally:
        _ACTIVE_FILE_CONTENTS.reset(cache_token)

    return final_issues
