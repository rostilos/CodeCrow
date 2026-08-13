import logging
import os
import re
from contextvars import ContextVar
from typing import List, Dict, Any, Optional, Tuple
try:
    from langchain_core.tools import tool
except ModuleNotFoundError as exception:
    if exception.name != "langchain_core":
        raise

    def tool(function):
        """Keep deterministic gates importable without optional agent tooling."""
        return function
from model.output_schemas import CodeReviewIssue
from service.review.candidate_ledger import CandidateEvidenceLedger
from model.dtos import ReviewRequestDto
from utils.llm_response import extract_llm_response_text
from service.review.orchestrator.json_utils import load_json_with_local_repairs
from utils.diff_processor import (
    DiffFile,
    DiffHunk,
    DiffProcessor,
    HunkDisposition,
    ProcessedDiff,
)
from utils.path_identity import normalize_repository_path, repository_paths_match
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
VERIFICATION_PROMPT_CHAR_BUDGET = max(
    8_000,
    _env_int("REVIEW_VERIFICATION_PROMPT_CHAR_BUDGET", 64_000),
)

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
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/|<!--.*?-->", re.DOTALL)
_TRIPLE_QUOTED_LITERAL_RE = re.compile(
    r"'''(?:\\.|[^\\])*?'''|\"\"\"(?:\\.|[^\\])*?\"\"\"",
    re.DOTALL,
)
_QUOTED_LITERAL_RE = re.compile(
    r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`"
)
_APPLIED_FIX_RE = re.compile(
    r"\b(?:the\s+)?current\s+(?:diff|change|patch|code|implementation)\s+"
    r"(?:already\s+|correctly\s+|successfully\s+|fully\s+)+"
    r"(?:address(?:es|ed)?|fix(?:es|ed)?|resolv(?:e|es|ed)|"
    r"prevent(?:s|ed)?|handle(?:s|d)?|guard(?:s|ed)?|protect(?:s|ed)?)\b|"
    r"\b(?:these|the)\s+fix(?:es)?\s+(?:"
    r"(?:already\s+|correctly\s+|successfully\s+|fully\s+)+"
    r"(?:address(?:es|ed)?|fix(?:es|ed)?|resolv(?:e|es|ed)|prevent(?:s|ed)?)|"
    r"(?:address(?:es|ed)?|fix(?:es|ed)?|resolv(?:e|es|ed)|prevent(?:s|ed)?)\s+"
    r"(?:the\s+)?(?:reported|immediate|original|issue|bug|problem|crash|error|"
    r"type\s*error)\b)|"
    r"\b(?:this|it|the\s+(?:issue|bug|problem))\s+"
    r"(?:(?:has\s+)?been|is\s+already)\s+"
    r"(?:addressed|fixed|resolved|prevented|handled)\s+(?:by|in)\s+"
    r"(?:(?:the|this)\s+)?(?:current\s+)?(?:diff|change|patch|code)\b|"
    r"\b(?:the|this)\s+(?:change|patch|fix|implementation|code)\s+"
    r"(?:correctly|successfully|fully)\s+"
    r"(?:address(?:es|ed)?|fix(?:es|ed)?|resolv(?:e|es|ed)|"
    r"prevent(?:s|ed)?|handle(?:s|d)?)\b|"
    r"(?<!\bnot\s)\bis\s+(?:a\s+)?(?:defensive\s+(?:coding\s+)?"
    r"improvement|corrective\s+measure)\b|"
    r"\b(?:the|this)\s+patch\s+is\s+(?:correctly|properly)\s+formatted\b|"
    r"\b(?:the|this|current)\s+(?:change|patch|diff)\s+"
    r"(?:fix(?:es|ed)?|resolv(?:e|es|ed)|address(?:es|ed)?)\s+"
    r"(?:this\b|(?:the\s+)?(?:reported|immediate|claimed|issue|bug|problem|"
    r"crash|error|type\s*error)\b)",
    re.IGNORECASE,
)
_NO_ACTION_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:\*\*|__)?\s*(?:"
    r"no\s+(?:further\s+)?(?:code\s+)?(?:changes?|fix(?:es)?|actions?|work)\s+"
    r"(?:(?:is|are)\s+)?(?:required|needed|necessary)|"
    r"no\s+fix\s+required"
    r")\s*[.!]?\s*(?:\*\*|__)?\s*$",
    re.IGNORECASE,
)
_SPECULATIVE_CLAUSE_RE = re.compile(
    r"\b(?:may|might|could|perhaps|potentially|possibly)\b",
    re.IGNORECASE,
)
_STRONG_CONTRAST_RE = re.compile(
    r"\b(?:but|however|yet|nevertheless|although|separately)\b",
    re.IGNORECASE,
)
_WHILE_CONTRAST_RE = re.compile(r"\bwhile\b", re.IGNORECASE)
_ADVISORY_CLAUSE_RE = re.compile(
    r"\b(?:different(?:\s+[\w-]+){0,3}\s+(?:techniques?|styles?|approaches?)|"
    r"null[- ]handling\s+(?:techniques?|styles?|approaches?)|fragmented|"
    r"inconsisten(?:t|cy)|strategy|standardiz(?:e|ation)|consisten(?:t|cy)|"
    r"maintainability|best\s+practice|optional\s+hardening)\b",
    re.IGNORECASE,
)
_CONCRETE_HARM_RE = re.compile(
    r"\b(?:crash(?:es|ed|ing)?|throw(?:s|ing)?|fail(?:s|ed|ing|ure)?|"
    r"break(?:s|ing)?|corrupt(?:s|ed|ing|ion)?|overwrit(?:e|es|ten|ing)|"
    r"leak(?:s|ed|ing)?|expos(?:e|es|ed|ing)|bypass(?:es|ed|ing)?|"
    r"deadlock(?:s|ed|ing)?|data\s+loss|unauthori[sz]ed|wrong\s+(?:value|"
    r"field|record|result|address|method|rate)|incorrect\s+(?:value|result|"
    r"behavior|state)|contract\s+violation)\b",
    re.IGNORECASE,
)
_NEGATED_FIX_TAIL_RE = re.compile(
    r"^[^.!?\n]{0,160}\b(?:incorrect(?:ly)?|wrong(?:ly)?|incompletely|partially|only)\b",
    re.IGNORECASE,
)
_HARMFUL_FIX_METHOD_RE = re.compile(
    r"\bby\s+(?:bypass(?:ing)?|expos(?:ing|e)|leak(?:ing)?|delet(?:ing|e)|"
    r"corrupt(?:ing)?|overwrit(?:ing|e)|dereferenc(?:ing|e)|"
    r"returning\s+(?:the\s+)?(?:wrong|stale|first|another|unauthorized))\b",
    re.IGNORECASE,
)
_POSITIVE_HARM_CONTEXT_RE = re.compile(
    r"\b(?:no\s+longer|does\s+not|doesn't|never)\s+"
    r"(?:crash|throw|fail|break|leak|expose|overwrite|corrupt)\w*\b|"
    r"\b(?:prevent\w*|without)\b[^.!?\n]{0,80}\b"
    r"(?:crash|throw|fail|leak|expos|overwrit|corrupt)\w*\b|"
    r"\b(?:the\s+)?(?:regression\s+)?tests?\s+fail\w*\s+before\b",
    re.IGNORECASE,
)
_POSITIVE_OUTCOME_RE = re.compile(
    r"\b(?:tests?\s+(?:now\s+)?pass(?:es|ed|ing)?|"
    r"(?:checkout|request|operation|build)\s+(?:now\s+)?"
    r"(?:works?|succeeds?|completes?)\s+(?:correctly|successfully)|"
    r"expected\s+behavior\s+is\s+restored)\b",
    re.IGNORECASE,
)
_EVIDENCE_LABEL_RE = re.compile(r"^\s*(?:evidence|also\s+affects)\s*:", re.IGNORECASE)
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


def _verification_issue_id(index: int, issue: CodeReviewIssue) -> str:
    # The persisted issue ID is not unique inside one model response: a model
    # can accidentally attach the same historical ID to multiple candidates.
    # Verification uses a per-record token; Original ID is sent separately.
    return f"issue_{index}"


def _bounded_issue_field(
    issue: CodeReviewIssue,
    name: str,
    max_chars: int,
) -> str:
    value = _issue_field(issue, name)
    if len(value) <= max_chars:
        return value
    marker = " [field truncated by deterministic verification budget]"
    return value[:max(1, max_chars - len(marker))].rstrip() + marker


def _verification_record_text(
    verification_id: str,
    issue: CodeReviewIssue,
) -> str:
    return (
        f"Verification ID: {verification_id}\n"
        f"Original ID: {_bounded_issue_field(issue, 'id', 300) or '(none)'}\n"
        f"File: {_bounded_issue_field(issue, 'file', 1000)}\n"
        f"Severity: {_bounded_issue_field(issue, 'severity', 40)}\n"
        f"Category: {_bounded_issue_field(issue, 'category', 100)}\n"
        f"Title: {_bounded_issue_field(issue, 'title', 400)}\n"
        f"Reason: {_bounded_issue_field(issue, 'reason', 1600)}\n"
        f"Suggested fix: "
        f"{_bounded_issue_field(issue, 'suggestedFixDescription', 1200)}\n"
        f"Scope: {_bounded_issue_field(issue, 'scope', 40)}\n"
        f"Exact source anchor: {_bounded_issue_field(issue, 'codeSnippet', 1000)}\n"
        "---"
    )


def _verification_prompt(issues_text: str) -> str:
    return f"""You are a Verification Agent for a code review system.
Your job is to verify whether the following issues are false positives using full file content.

You have access to a tool called `search_file_content`.
For each issue, decide whether checking exact strings in the file would help verify the claim.
Use the tool only when the issue depends on whether a symbol, method, import, line, or nearby code exists in the full file.
When checks are useful, issue all `search_file_content` calls together in the same tool round.

An issue must describe a concrete defect that remains in the post-change code and
requires a code change that is not already present. Drop praise, confirmations,
defensive improvements, verification-only notes, and candidates whose own reason
or suggested fix says the current diff/change/patch already fixes, addresses,
resolves, or prevents the claimed problem. A partial fix is still a valid finding
when the candidate identifies a separate concrete defect that remains or is
introduced by the change.

Also drop an issue when file-content evidence clearly proves it is a false
positive. Otherwise keep genuinely actionable issues when evidence is
inconclusive or the claim is not verifiable with exact string search.

Issues to verify:
{issues_text}

Return ONLY a JSON object containing a list of `issue_ids_to_drop` for the issues that are false positives.
Use the exact Verification ID values above, not file names or generated explanations.
"""


def _build_verification_batches(
    verification_records: List[Tuple[str, CodeReviewIssue]],
    *,
    max_chars: Optional[int] = None,
) -> List[Tuple[List[Tuple[str, CodeReviewIssue]], str]]:
    prompt_budget = max(
        4_000,
        max_chars if max_chars is not None else VERIFICATION_PROMPT_CHAR_BUDGET,
    )
    empty_prompt_chars = len(_verification_prompt(""))
    payload_budget = max(1_000, prompt_budget - empty_prompt_chars)
    batches: List[Tuple[List[Tuple[str, CodeReviewIssue]], str]] = []
    current_records: List[Tuple[str, CodeReviewIssue]] = []
    current_parts: List[str] = []
    current_chars = 0

    for record in verification_records:
        rendered = _verification_record_text(*record)
        added = len(rendered) + (1 if current_parts else 0)
        if current_parts and current_chars + added > payload_budget:
            batches.append((
                current_records,
                _verification_prompt("\n".join(current_parts)),
            ))
            current_records = []
            current_parts = []
            current_chars = 0
            added = len(rendered)
        current_records.append(record)
        current_parts.append(rendered)
        current_chars += added

    if current_parts:
        batches.append((
            current_records,
            _verification_prompt("\n".join(current_parts)),
        ))

    return batches


def _issue_is_resolved(issue: CodeReviewIssue) -> bool:
    value = getattr(issue, "isResolved", False)
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def previous_open_issue_ids(request: ReviewRequestDto) -> set[str]:
    """Return IDs eligible for OPEN-history lifecycle reconciliation."""
    previous = getattr(request, "previousCodeAnalysisIssues", None)
    if previous is None or previous.__class__.__module__.startswith("unittest.mock"):
        return set()

    issue_ids: set[str] = set()
    for issue in previous or []:
        if isinstance(issue, dict):
            value = issue.get("id")
            status = issue.get("status")
            is_resolved = issue.get("isResolved")
        elif hasattr(issue, "model_dump"):
            data = issue.model_dump()
            value = data.get("id")
            status = data.get("status")
            is_resolved = data.get("isResolved")
        else:
            value = getattr(issue, "id", None)
            status = getattr(issue, "status", None)
            is_resolved = getattr(issue, "isResolved", None)
        normalized_status = str(status or "").strip().casefold()
        resolved_flag = is_resolved is True or (
            isinstance(is_resolved, str)
            and is_resolved.strip().casefold() == "true"
        )
        # Only current OPEN history participates in lifecycle reconciliation.
        # Legacy records without a status remain eligible; resolved, ignored,
        # and any future terminal state are context only.
        if resolved_flag or normalized_status not in {"", "open"}:
            continue
        normalized = str(value).strip() if value is not None else ""
        if normalized:
            issue_ids.add(normalized)
    return issue_ids


def _resolve_historical_candidate(
    issue: CodeReviewIssue,
    previous_open_ids: set[str],
    explanation: str,
) -> bool:
    """Turn a rejected prior OPEN candidate into an explicit close update."""
    issue_id = _issue_field(issue, "id").strip()
    if not issue_id or issue_id not in previous_open_ids:
        return False
    resolution = (
        _issue_field(issue, "resolutionReason").strip()
        or _issue_field(issue, "resolutionExplanation").strip()
        or explanation
    )
    issue.isResolved = True
    issue.resolutionReason = resolution
    issue.resolutionExplanation = resolution
    return True


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
    """Return true only for a high-confidence syntactic reference.

    A raw identifier count is unsafe here: comments, docstrings, and a local
    declaration that shadows the import all contain the same token. The LLM
    verifier handles ambiguous cases; this deterministic shortcut is limited to
    common call/type/member syntax that clearly references the imported name.
    """
    del snippet  # Import declarations are removed from the evidence below.
    source = _TRIPLE_QUOTED_LITERAL_RE.sub("", evidence or "")
    source = _BLOCK_COMMENT_RE.sub("", source)
    code_lines: List[str] = []
    for raw_line in source.splitlines():
        line = raw_line.lstrip("+ ")
        line = _QUOTED_LITERAL_RE.sub("", line)
        # This deliberately strips a few comment syntaxes aggressively. A lost
        # reference merely makes this shortcut fail open and leaves verification
        # to the model; treating comment text as code would drop a real finding.
        line = re.split(r"//|--|#(?!\[)", line, maxsplit=1)[0]
        if re.match(r"^\s*(?:import|from|use|using|#include)\b", line):
            continue
        code_lines.append(line)

    code = "\n".join(code_lines)
    escaped = re.escape(symbol)
    shadowing_declaration = re.compile(
        rf"(?m)^\s*(?:(?:class|interface|trait|enum|def|function|type)\s+"
        rf"{escaped}\b|(?:const|let|var)\s+{escaped}\b|{escaped}\s*=)",
        re.IGNORECASE,
    )
    if shadowing_declaration.search(code):
        return False

    high_confidence_reference = re.compile(
        rf"(?:\bnew\s+{escaped}\b|\b{escaped}\s*(?:::|\.|->)|"
        rf"\b{escaped}\s*\(|\b(?:extends|implements|instanceof)\s+{escaped}\b|"
        rf"(?::|->)\s*{escaped}\b|@\s*{escaped}\b|<\s*{escaped}(?:\s|/|>))"
    )
    return bool(high_confidence_reference.search(code))


def _clause_after(text: str, start: int) -> str:
    """Return one sentence-sized clause after a contrast marker."""
    tail = text[start:]
    boundary = re.search(r"[.!?\n]", tail)
    return tail[:boundary.start()] if boundary else tail


def _has_concrete_post_fix_contrast(observation: str) -> bool:
    """Fail open when the candidate asserts a distinct current defect."""
    for match in _STRONG_CONTRAST_RE.finditer(observation):
        clause = _clause_after(observation, match.end())
        advisory_only = (
            _ADVISORY_CLAUSE_RE.search(clause)
            and not _CONCRETE_HARM_RE.search(clause)
        )
        if (
            clause.strip()
            and not _SPECULATIVE_CLAUSE_RE.search(clause)
            and not advisory_only
        ):
            return True

    # `While these fixes resolve X, ...` commonly introduces either a real
    # regression or an advisory consistency observation. Inspect only the
    # immediate post-comma clause so a later speculative sentence cannot hide a
    # concrete regression (or turn an advisory into one).
    for match in _WHILE_CONTRAST_RE.finditer(observation):
        sentence = _clause_after(observation, match.end())
        comma = sentence.find(",")
        if comma < 0:
            continue
        clause = sentence[comma + 1:]
        advisory_only = (
            _ADVISORY_CLAUSE_RE.search(clause)
            and not _CONCRETE_HARM_RE.search(clause)
        )
        if clause.strip() and not _SPECULATIVE_CLAUSE_RE.search(clause) and not advisory_only:
            return True
    return False


def _has_concrete_post_fix_sentence(observation: str, fix_end: int) -> bool:
    """Detect a separate harmful assertion after a positive fix sentence."""
    tail = observation[fix_end:]
    sentences = re.split(r"[.!?;\n]+", tail)

    # A second assertion can share the fix sentence without using an explicit
    # contrast word: "fixes X and now corrupts Y". Inspect only the coordinated
    # clause so the positive object itself ("fixes the reported crash") is not
    # mistaken for a remaining defect.
    first_sentence = sentences[0] if sentences else ""
    for marker in re.finditer(r"\b(?:and|then)\s+(?:now\s+)?", first_sentence):
        clause = first_sentence[marker.end():]
        without_positive_context = _POSITIVE_HARM_CONTEXT_RE.sub("", clause)
        if (
            not _SPECULATIVE_CLAUSE_RE.search(clause)
            and _CONCRETE_HARM_RE.search(without_positive_context)
        ):
            return True

    for sentence in sentences[1:]:
        if not sentence.strip() or _SPECULATIVE_CLAUSE_RE.search(sentence):
            continue
        if _EVIDENCE_LABEL_RE.search(sentence) and not _CONCRETE_HARM_RE.search(sentence):
            continue
        advisory_only = (
            _ADVISORY_CLAUSE_RE.search(sentence)
            and not _CONCRETE_HARM_RE.search(sentence)
        )
        if (
            advisory_only
            or _POSITIVE_OUTCOME_RE.search(sentence)
            or _POSITIVE_HARM_CONTEXT_RE.search(sentence)
        ):
            continue
        if _APPLIED_FIX_RE.search(sentence):
            continue
        without_positive_context = _POSITIVE_HARM_CONTEXT_RE.sub("", sentence)
        if _CONCRETE_HARM_RE.search(without_positive_context):
            return True
        if without_positive_context.strip():
            # A distinct, non-positive behavioral assertion is ambiguous enough
            # to require normal evidence verification. Never discard it merely
            # because its vocabulary is absent from a finite harm-word list.
            return True
    return False


def _is_self_disqualifying_issue(issue: CodeReviewIssue) -> bool:
    """Detect explicit admissions that no current code change is required."""
    title = _issue_field(issue, "title")
    reason = _issue_field(issue, "reason")
    suggested_fix = _issue_field(issue, "suggestedFixDescription")
    observation = "\n".join((title, reason))

    applied_fix = _APPLIED_FIX_RE.search(observation)
    if applied_fix and _NEGATED_FIX_TAIL_RE.search(observation[applied_fix.end():]):
        applied_fix = None
    suggested_applied_fix = _APPLIED_FIX_RE.search(suggested_fix)
    if (
        suggested_applied_fix
        and _NEGATED_FIX_TAIL_RE.search(
            suggested_fix[suggested_applied_fix.end():]
        )
    ):
        suggested_applied_fix = None
    suggestion_confirms_current_code = bool(
        suggested_applied_fix
        and re.search(
            r"\b(?:current|already)\b",
            suggested_applied_fix.group(0),
            re.IGNORECASE,
        )
    )
    # Treat only a standalone no-action recommendation as self-disqualifying.
    # The same words in a defect description can describe exploitability
    # ("no action is required by an attacker"), and a longer suggestion may
    # still request a concrete change after scoping what does not need work.
    no_action = _NO_ACTION_RE.fullmatch(suggested_fix)
    if not applied_fix and not suggestion_confirms_current_code and not no_action:
        return False

    # A valid partial fix can still cause another defect. These checks are
    # intentionally fail-open: ambiguous language remains publishable for the
    # evidence verifier instead of being discarded by a brittle word list.
    if applied_fix and _has_concrete_post_fix_contrast(observation):
        return False
    if applied_fix and _has_concrete_post_fix_sentence(
        observation,
        applied_fix.end(),
    ):
        return False
    if applied_fix and _HARMFUL_FIX_METHOD_RE.search(
        observation[applied_fix.end():]
    ):
        return False
    return True


def _drop_non_publishable_issues(
    issues: List[CodeReviewIssue],
    request: ReviewRequestDto,
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
) -> Tuple[List[CodeReviewIssue], List[str]]:
    previous_ids = previous_open_issue_ids(request)
    kept: List[CodeReviewIssue] = []
    dropped_ids: List[str] = []
    for index, issue in enumerate(issues):
        issue_id = _issue_field(issue, "id").strip()
        resolved = _issue_is_resolved(issue)
        matches_history = bool(issue_id) and issue_id in previous_ids
        severity = _issue_field(issue, "severity").strip().upper()
        self_disqualifying = not resolved and _is_self_disqualifying_issue(issue)

        if resolved:
            if matches_history:
                resolution = (
                    getattr(issue, "resolutionReason", None)
                    or getattr(issue, "resolutionExplanation", None)
                )
                if resolution:
                    issue.resolutionReason = resolution
                    issue.resolutionExplanation = resolution
                kept.append(issue)
            else:
                dropped_ids.append(_verification_issue_id(index, issue))
                if candidate_ledger is not None:
                    candidate_ledger.reject(
                        issue,
                        gate="publication_policy",
                        code="unmatched_resolution",
                    )
            continue

        # A new candidate without an exact current-source anchor cannot satisfy
        # the review contract or be verified deterministically. Do not spend a
        # verifier call on it and do not let FILE scope bypass the invariant.
        # Persisted OPEN history is exempt so an older record is not silently
        # closed merely because its legacy payload lacks a snippet.
        if not _issue_field(issue, "codeSnippet").strip():
            if matches_history:
                kept.append(issue)
            else:
                dropped_ids.append(_verification_issue_id(index, issue))
                if candidate_ledger is not None:
                    candidate_ledger.reject(
                        issue,
                        gate="publication_policy",
                        code="missing_source_anchor",
                    )
            continue

        policy_non_publishable = severity == "INFO" or self_disqualifying
        if policy_non_publishable and matches_history:
            # Do not merely omit a legacy false positive: Java may preserve an
            # unmatched open issue when its old anchor still exists. Return an
            # explicit lifecycle update so it is closed without being reported.
            _resolve_historical_candidate(
                issue,
                previous_ids,
                "Closed because no actionable post-change defect remains.",
            )
            kept.append(issue)
            logger.info(
                "Marked historical non-publishable issue %s as resolved",
                issue_id,
            )
            continue

        if policy_non_publishable:
            dropped_ids.append(_verification_issue_id(index, issue))
            if candidate_ledger is not None:
                candidate_ledger.reject(
                    issue,
                    gate="publication_policy",
                    code=(
                        "info_severity"
                        if severity == "INFO"
                        else "self_disqualifying"
                    ),
                )
        else:
            kept.append(issue)
    return (kept, dropped_ids) if dropped_ids else (issues, [])


def _drop_deterministically_contradicted_issues(
    issues: List[CodeReviewIssue],
    evidence_by_path: Dict[str, Optional[str]],
    previous_open_ids: Optional[set[str]] = None,
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
) -> Tuple[List[CodeReviewIssue], List[str]]:
    """Drop only issues whose own named symbol is visibly used outside its import."""
    kept: List[CodeReviewIssue] = []
    dropped_ids: List[str] = []
    for index, issue in enumerate(issues):
        if _issue_is_resolved(issue):
            kept.append(issue)
            continue
        snippet = _issue_field(issue, "codeSnippet")
        evidence = _lookup_path_evidence(evidence_by_path, _issue_field(issue, "file"))
        candidates = _unused_import_candidates(issue)
        contradicted = bool(evidence and any(
            _symbol_occurs_outside_anchor(symbol, snippet, evidence)
            for symbol in candidates
        ))
        if contradicted:
            if _resolve_historical_candidate(
                issue,
                previous_open_ids or set(),
                "Closed because current source evidence contradicts the prior finding.",
            ):
                kept.append(issue)
            else:
                dropped_ids.append(_verification_issue_id(index, issue))
                if candidate_ledger is not None:
                    candidate_ledger.reject(
                        issue,
                        gate="source_evidence",
                        code="deterministic_contradiction",
                    )
        else:
            kept.append(issue)
    return kept, dropped_ids


def _snippet_occurs_in_current_source(snippet: str, source: str) -> bool:
    """Match the model-provided anchor without weakening the exact-copy contract."""
    normalized_snippet = snippet.replace("\r\n", "\n").replace("\r", "\n")
    normalized_source = source.replace("\r\n", "\n").replace("\r", "\n")
    return bool(normalized_snippet) and normalized_snippet in normalized_source


def _complete_file_evidence(
    request: ReviewRequestDto,
) -> Dict[str, Optional[str]]:
    evidence: Dict[str, Optional[str]] = {}
    enrichment = getattr(request, "enrichmentData", None)
    for file_content in getattr(enrichment, "fileContents", None) or []:
        if (
            file_content.content
            and getattr(file_content, "skipped", False) is not True
        ):
            _add_path_evidence(
                evidence,
                file_content.path,
                file_content.content,
            )
    return evidence


def _diff_file_for_path(
    processed_diff: ProcessedDiff,
    path: str,
) -> Optional[DiffFile]:
    normalized = (path or "").lstrip("/")
    exact = [
        diff_file
        for diff_file in processed_diff.files
        if diff_file.path.lstrip("/") == normalized
    ]
    if len(exact) == 1:
        return exact[0]
    matches = [
        diff_file
        for diff_file in processed_diff.files
        if repository_paths_match(diff_file.path, normalized)
    ]
    return matches[0] if len(matches) == 1 else None


def _current_hunk_source(hunk: DiffHunk) -> str:
    lines: List[str] = []
    for line in hunk.content.splitlines()[1:]:
        if line.startswith("-") or line.startswith("\\"):
            continue
        if line.startswith(("+", " ")):
            lines.append(line[1:])
    return "\n".join(lines)


def _reviewable_hunks(diff_file: DiffFile) -> List[DiffHunk]:
    hunks = list(diff_file.hunks)
    if not hunks and diff_file.content:
        # Some direct/internal callers construct ProcessedDiff manually. Parse
        # their exact file section instead of treating missing derived hunk
        # objects as proof that the file was unchanged.
        size = max(len(diff_file.content.encode("utf-8")) + 1, 1)
        reparsed = DiffProcessor(
            max_file_size=size,
            max_files=1,
            max_total_size=size,
            max_lines_per_file=max(diff_file.content.count("\n") + 1, 1),
        ).process(diff_file.content)
        matched = _diff_file_for_path(reparsed, diff_file.path)
        if matched is not None:
            hunks = list(matched.hunks)
    return [
        hunk
        for hunk in hunks
        if hunk.disposition is HunkDisposition.REVIEWABLE
    ]


def _snippet_line_spans(snippet: str, source: str) -> List[Tuple[int, int]]:
    normalized_snippet = snippet.replace("\r\n", "\n").replace("\r", "\n")
    normalized_source = source.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized_snippet:
        return []
    spans: List[Tuple[int, int]] = []
    offset = 0
    while True:
        index = normalized_source.find(normalized_snippet, offset)
        if index < 0:
            break
        start_line = normalized_source.count("\n", 0, index) + 1
        end_line = start_line + normalized_snippet.count("\n")
        spans.append((start_line, end_line))
        offset = index + 1
    return spans


def _anchor_reviewable_hunk_ids(
    issue: CodeReviewIssue,
    processed_diff: ProcessedDiff,
    complete_source_by_path: Dict[str, Optional[str]],
) -> tuple[str, ...]:
    path = _issue_field(issue, "file")
    diff_file = _diff_file_for_path(processed_diff, path)
    if diff_file is None:
        return ()
    reviewable_hunks = _reviewable_hunks(diff_file)
    if not reviewable_hunks:
        return ()

    snippet = _issue_field(issue, "codeSnippet")
    complete_source = _lookup_path_evidence(
        complete_source_by_path,
        path,
    )
    if complete_source is not None:
        spans = _snippet_line_spans(snippet, complete_source)
        return tuple(sorted({
            hunk.id
            for hunk in reviewable_hunks
            for start_line, end_line in spans
            if (
                hunk.new_count > 0
                and start_line <= hunk.new_start + hunk.new_count - 1
                and end_line >= hunk.new_start
            )
        }))

    # Full current source may be unavailable for a bounded large file. In that
    # case, the exact current-side hunk text is still an authoritative scope
    # boundary and avoids a provider call.
    normalized_snippet = snippet.replace("\r\n", "\n").replace("\r", "\n")
    return tuple(sorted(
        hunk.id
        for hunk in reviewable_hunks
        if normalized_snippet
        and normalized_snippet in _current_hunk_source(hunk)
    ))


def reviewable_hunk_ids_for_issue(
    issue: CodeReviewIssue,
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
) -> tuple[str, ...]:
    if processed_diff is None:
        return ()
    return _anchor_reviewable_hunk_ids(
        issue,
        processed_diff,
        _complete_file_evidence(request),
    )


def _anchor_overlaps_reviewable_hunk(
    issue: CodeReviewIssue,
    processed_diff: ProcessedDiff,
    complete_source_by_path: Dict[str, Optional[str]],
) -> bool:
    return bool(_anchor_reviewable_hunk_ids(
        issue,
        processed_diff,
        complete_source_by_path,
    ))


def is_summary_only_change_compatibility_candidate(
    issue: CodeReviewIssue,
    candidate_ledger: Optional[CandidateEvidenceLedger],
) -> bool:
    """Recognize the host-bound old-side exception to current-line anchoring."""
    if candidate_ledger is None:
        return False
    record = candidate_ledger.record_for(issue)
    if record is None or record.stage != "change_compatibility":
        return False
    try:
        line = int(getattr(issue, "line", 0) or 0)
    except (TypeError, ValueError):
        return False
    if line != 0 or str(getattr(issue, "scope", "") or "").upper() != "FILE":
        return False
    path = normalize_repository_path(_issue_field(issue, "file"))
    anchor = _issue_field(issue, "codeSnippet")
    anchor_facts = [
        fact
        for evidence_id in record.evidence_refs
        for fact in record.visible_evidence_by_id.get(evidence_id, ())
        if fact.get("kind") == "change_compatibility_anchor"
    ]
    if len(anchor_facts) != 1:
        return False
    fact = anchor_facts[0]
    return bool(
        path
        and anchor
        and path == normalize_repository_path(str(fact.get("path") or ""))
        and anchor == str(fact.get("anchor") or "")
        and tuple(sorted(fact.get("promptHunkIds") or ()))
        == tuple(sorted(record.prompt_hunk_ids))
        and any(
            fact.get("kind") != "change_compatibility_anchor"
            for evidence_id in record.evidence_refs
            for fact in record.visible_evidence_by_id.get(evidence_id, ())
        )
    )


def apply_candidate_provenance_gate(
    issues: List[CodeReviewIssue],
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
    candidate_ledger: Optional[CandidateEvidenceLedger],
    units_by_hunk: Optional[Dict[str, set[str]]] = None,
) -> List[CodeReviewIssue]:
    """Require fresh candidates to originate from the hunk unit they cite."""
    if candidate_ledger is None:
        return issues
    historical_ids = previous_open_issue_ids(request)
    unit_owners = units_by_hunk or {}
    kept: List[CodeReviewIssue] = []
    for issue in issues:
        issue_id = _issue_field(issue, "id").strip()
        historical = bool(issue_id) and issue_id in historical_ids
        record = candidate_ledger.record_for(issue)
        if _issue_is_resolved(issue) or historical:
            kept.append(issue)
            continue
        if record is None:
            raise RuntimeError(
                "fresh review candidate has no generation provenance: "
                f"{_issue_field(issue, 'file')}:{_issue_field(issue, 'line')}"
            )
        if is_summary_only_change_compatibility_candidate(issue, candidate_ledger):
            candidate_ledger.confirm_anchor_hunks(issue, record.prompt_hunk_ids)
            if not set(record.evidence_refs).issubset(
                record.visible_evidence_by_id
            ):
                candidate_ledger.reject(
                    issue,
                    gate="candidate_provenance",
                    code="evidence_outside_generation_prompt",
                )
                continue
            kept.append(issue)
            continue
        if not record.review_unit_ids or not record.prompt_hunk_ids:
            candidate_ledger.reject(
                issue,
                gate="candidate_provenance",
                code="unbound_review_unit",
            )
            continue
        anchor_hunks = reviewable_hunk_ids_for_issue(
            issue,
            request,
            processed_diff,
        )
        matching_hunks = tuple(sorted(
            set(anchor_hunks) & set(record.prompt_hunk_ids)
        ))
        if not matching_hunks:
            candidate_ledger.reject(
                issue,
                gate="candidate_provenance",
                code="anchor_outside_generation_unit",
            )
            continue
        if unit_owners and not any(
            set(record.review_unit_ids) & unit_owners.get(hunk_id, set())
            for hunk_id in matching_hunks
        ):
            candidate_ledger.reject(
                issue,
                gate="candidate_provenance",
                code="review_unit_ownership_mismatch",
            )
            continue
        candidate_ledger.confirm_anchor_hunks(issue, matching_hunks)
        if not set(record.evidence_refs).issubset(
            record.visible_evidence_by_id
        ):
            candidate_ledger.reject(
                issue,
                gate="candidate_provenance",
                code="evidence_outside_generation_prompt",
            )
            continue
        kept.append(issue)
    return kept


def _drop_out_of_hunk_anchors(
    issues: List[CodeReviewIssue],
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
) -> Tuple[List[CodeReviewIssue], List[str]]:
    """Keep new findings scoped to a reviewable changed hunk.

    Full-file and RAG context are supporting evidence, not permission to report
    unrelated pre-existing code. Historical lifecycle records remain exempt.
    """
    if processed_diff is None:
        return issues, []
    historical_ids = previous_open_issue_ids(request)
    complete_source_by_path = _complete_file_evidence(request)
    kept: List[CodeReviewIssue] = []
    dropped_ids: List[str] = []
    for index, issue in enumerate(issues):
        issue_id = _issue_field(issue, "id").strip()
        if (
            _issue_is_resolved(issue)
            or (issue_id and issue_id in historical_ids)
            or is_summary_only_change_compatibility_candidate(
                issue,
                candidate_ledger,
            )
            or _anchor_overlaps_reviewable_hunk(
                issue,
                processed_diff,
                complete_source_by_path,
            )
        ):
            kept.append(issue)
        else:
            dropped_ids.append(_verification_issue_id(index, issue))
            if candidate_ledger is not None:
                candidate_ledger.reject(
                    issue,
                    gate="source_evidence",
                    code="anchor_outside_reviewable_hunk",
                )
    return kept, dropped_ids


def _drop_unmatched_current_source_anchors(
    issues: List[CodeReviewIssue],
    evidence_by_path: Dict[str, Optional[str]],
    previous_open_ids: Optional[set[str]] = None,
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
) -> Tuple[List[CodeReviewIssue], List[str]]:
    """Reject new findings whose claimed exact anchor is absent from current code.

    Missing source evidence is not treated as a mismatch here: acquisition and
    coverage gates own that failure state. Exact OPEN history is also retained so
    a legacy record is not silently resolved merely because its old anchor moved.
    """
    historical_ids = previous_open_ids or set()
    kept: List[CodeReviewIssue] = []
    dropped_ids: List[str] = []
    for index, issue in enumerate(issues):
        if _issue_is_resolved(issue):
            kept.append(issue)
            continue
        if is_summary_only_change_compatibility_candidate(
            issue,
            candidate_ledger,
        ):
            kept.append(issue)
            continue
        issue_id = _issue_field(issue, "id").strip()
        if issue_id and issue_id in historical_ids:
            kept.append(issue)
            continue
        source = _lookup_path_evidence(
            evidence_by_path,
            _issue_field(issue, "file"),
        )
        if source is None:
            kept.append(issue)
            continue
        snippet = _issue_field(issue, "codeSnippet")
        if _snippet_occurs_in_current_source(snippet, source):
            kept.append(issue)
        else:
            dropped_ids.append(_verification_issue_id(index, issue))
            if candidate_ledger is not None:
                candidate_ledger.reject(
                    issue,
                    gate="source_evidence",
                    code="anchor_absent_current_source",
                )
    return kept, dropped_ids


def run_deterministic_evidence_gate(
    issues: List[CodeReviewIssue],
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff] = None,
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
) -> List[CodeReviewIssue]:
    """Apply fail-closed contradiction checks to issues from any review stage."""
    if not issues:
        return issues

    filtered, non_publishable_ids = _drop_non_publishable_issues(
        issues,
        request,
        candidate_ledger,
    )
    if non_publishable_ids:
        logger.info(
            "Deterministic publication gate dropped %d non-publishable issue(s): %s",
            len(non_publishable_ids),
            non_publishable_ids,
        )
    if not filtered:
        return []

    evidence_by_path = _build_file_evidence(request, processed_diff)
    if not evidence_by_path:
        return filtered

    open_issue_ids = previous_open_issue_ids(request)
    filtered, unmatched_anchor_ids = _drop_unmatched_current_source_anchors(
        filtered,
        evidence_by_path,
        open_issue_ids,
        candidate_ledger,
    )
    if unmatched_anchor_ids:
        logger.info(
            "Deterministic publication gate dropped %d issue(s) whose exact "
            "codeSnippet was absent from current source: %s",
            len(unmatched_anchor_ids),
            unmatched_anchor_ids,
        )
    if not filtered:
        return []

    filtered, out_of_hunk_ids = _drop_out_of_hunk_anchors(
        filtered,
        request,
        processed_diff,
        candidate_ledger,
    )
    if out_of_hunk_ids:
        logger.info(
            "Deterministic publication gate dropped %d new issue(s) whose "
            "current-source anchor is outside every reviewable diff hunk: %s",
            len(out_of_hunk_ids),
            out_of_hunk_ids,
        )
    if not filtered:
        return []

    filtered, dropped_ids = _drop_deterministically_contradicted_issues(
        filtered,
        evidence_by_path,
        open_issue_ids,
        candidate_ledger,
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
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff] = None,
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
) -> List[CodeReviewIssue]:
    """
    Stage 1.5: LLM-Driven Verification.
    Uses an LLM with a local search tool to verify suspected false positive issues.
    """
    if not issues:
        logger.info("Stage 1.5: No issues found, skipping verification.")
        return issues

    issues, non_publishable_ids = _drop_non_publishable_issues(
        issues,
        request,
        candidate_ledger,
    )
    if non_publishable_ids:
        logger.info(
            "Stage 1.5: Publication gate dropped %d non-publishable issue(s): %s",
            len(non_publishable_ids),
            non_publishable_ids,
        )
    if not issues:
        return []

    evidence_by_path = _build_file_evidence(request, processed_diff)
    if not evidence_by_path:
        logger.info("Stage 1.5: No current-file or diff evidence available; skipping verification.")
        return issues

    open_issue_ids = previous_open_issue_ids(request)
    issues, unmatched_anchor_ids = _drop_unmatched_current_source_anchors(
        issues,
        evidence_by_path,
        open_issue_ids,
        candidate_ledger,
    )
    if unmatched_anchor_ids:
        logger.info(
            "Stage 1.5: Dropped %d issue(s) whose exact codeSnippet was "
            "absent from current source before verifier calls: %s",
            len(unmatched_anchor_ids),
            unmatched_anchor_ids,
        )
    if not issues:
        return []

    issues, out_of_hunk_ids = _drop_out_of_hunk_anchors(
        issues,
        request,
        processed_diff,
        candidate_ledger,
    )
    if out_of_hunk_ids:
        logger.info(
            "Stage 1.5: Dropped %d new issue(s) whose current-source anchor "
            "is outside every reviewable diff hunk before verifier calls: %s",
            len(out_of_hunk_ids),
            out_of_hunk_ids,
        )
    if not issues:
        return []

    issues, deterministic_drop_ids = _drop_deterministically_contradicted_issues(
        issues,
        evidence_by_path,
        open_issue_ids,
        candidate_ledger,
    )
    if deterministic_drop_ids:
        logger.info(
            "Stage 1.5: Deterministic evidence gate dropped %d contradicted issue(s): %s",
            len(deterministic_drop_ids),
            deterministic_drop_ids,
        )
    if not issues:
        return []

    active_issues = [issue for issue in issues if not _issue_is_resolved(issue)]
    if not active_issues:
        return issues

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

    logger.info(
        "Stage 1.5: Verifying %d active issue(s) with LLM-selected checks...",
        len(active_issues),
    )

    verification_records = [
        (_verification_issue_id(index, issue), issue)
        for index, issue in enumerate(active_issues)
    ]
    verification_batches = _build_verification_batches(verification_records)
    logger.info(
        "Stage 1.5: Split %d verification record(s) into %d deterministic "
        "prompt batch(es), cap=%d chars",
        len(verification_records),
        len(verification_batches),
        VERIFICATION_PROMPT_CHAR_BUDGET,
    )

    try:
        ids_to_drop: set[str] = set()
        failed_batches = 0
        for batch_index, (batch_records, prompt) in enumerate(
            verification_batches,
            start=1,
        ):
            allowed_ids = {
                verification_id
                for verification_id, _ in batch_records
            }
            try:
                result = await _run_verification_tool_loop(llm, prompt)
            except Exception as exception:
                failed_batches += 1
                log = logger.info if failed_batches == 1 else logger.debug
                log(
                    "Stage 1.5 verification batch %d/%d failed; retaining its "
                    "%d issue(s): %s",
                    batch_index,
                    len(verification_batches),
                    len(batch_records),
                    exception,
                )
                continue
            returned_ids = {
                str(issue_id).strip()
                for issue_id in result.issue_ids_to_drop
                if str(issue_id).strip()
            }
            unknown_ids = returned_ids - allowed_ids
            if unknown_ids:
                logger.warning(
                    "Stage 1.5 verification batch %d returned out-of-batch IDs: %s",
                    batch_index,
                    sorted(unknown_ids),
                )
            ids_to_drop.update(returned_ids & allowed_ids)
        logger.info(
            "Stage 1.5: Agent identified %d false positive(s) to drop; "
            "%d/%d batch(es) failed open.",
            len(ids_to_drop),
            failed_batches,
            len(verification_batches),
        )

        previous_open_ids = previous_open_issue_ids(request)
        final_active_issues = []
        for verification_id, issue in verification_records:
            if verification_id not in ids_to_drop:
                final_active_issues.append(issue)
            else:
                _resolve_historical_candidate(
                    issue,
                    previous_open_ids,
                    "Closed because current-file verification no longer supports the prior finding.",
                )
                if not _issue_is_resolved(issue) and candidate_ledger is not None:
                    candidate_ledger.reject(
                        issue,
                        gate="llm_verification",
                        code="false_positive",
                    )
        retained_active = {id(issue) for issue in final_active_issues}
        final_issues = [
            issue
            for issue in issues
            if _issue_is_resolved(issue) or id(issue) in retained_active
        ]
        
    except Exception as e:
        logger.info("Stage 1.5 verification unavailable; retaining all issues: %s", e)
        # Fallback: keep all issues if verification fails
        final_issues = issues
    finally:
        _ACTIVE_FILE_CONTENTS.reset(cache_token)

    return final_issues
