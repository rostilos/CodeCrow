import logging
import os
import re
import json
from hashlib import sha256
from contextvars import ContextVar
from typing import List, Dict, Any, Literal, Optional, Tuple
from langchain_core.tools import tool
from model.output_schemas import CodeReviewIssue
from model.dtos import ReviewRequestDto
from service.review.orchestrator.agents import extract_llm_response_text
from service.review.telemetry import observed_ainvoke
from service.review.execution_context import is_manifest_bound_v1
from service.review.publication_gate import (
    ExactPublicationClaim,
    ExactSourceProof,
    accept_exact_publication,
    changed_lines_from_diff,
)
from service.review.orchestrator.json_utils import load_json_with_local_repairs
from utils.diff_processor import DiffProcessor, ProcessedDiff
from pydantic import BaseModel, ConfigDict, Field

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
_ACTIVE_SOURCE_RECEIPTS: ContextVar[Optional[Dict[str, Dict[str, Any]]]] = ContextVar(
    "verification_source_receipts",
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
_MISSING_SYMBOL_CLAIM_RE = re.compile(
    r"\b(?:missing|undefined|unresolved|unknown|not\s+defined|does\s+not\s+exist|"
    r"cannot\s+find|can't\s+find)\b",
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


def _lookup_source_receipt(file_path: str) -> Optional[Dict[str, Any]]:
    receipts = _ACTIVE_SOURCE_RECEIPTS.get() or {}
    normalized = (file_path or "").lstrip("/")
    direct = receipts.get(normalized)
    if direct is not None:
        return direct
    matches = [
        receipt
        for path, receipt in receipts.items()
        if path.endswith("/" + normalized) or normalized.endswith("/" + path)
    ]
    return matches[0] if len(matches) == 1 else None


@tool
def read_source_span(file_path: str, start_line: int, end_line: int) -> str:
    """Read an exact, line-numbered source span with its immutable receipt."""
    receipt = _lookup_source_receipt(file_path)
    if receipt is None:
        return json.dumps({"error": "source_not_available", "file_path": file_path})
    try:
        start = int(start_line)
        end = int(end_line)
    except (TypeError, ValueError):
        return json.dumps({"error": "invalid_line_range", "file_path": file_path})
    if start < 1 or end < start or end - start + 1 > 200:
        return json.dumps({"error": "invalid_line_range", "file_path": file_path})

    lines = receipt["content"].splitlines()
    bounded_end = min(end, len(lines))
    selected = [
        {"line": index, "text": lines[index - 1]}
        for index in range(start, bounded_end + 1)
        if index <= len(lines)
    ]
    return json.dumps({
        "file_path": receipt["path"],
        "revision": receipt.get("revision"),
        "content_digest": receipt["content_digest"],
        "complete_source": receipt["complete_source"],
        "start_line": start,
        "end_line": bounded_end,
        "lines": selected,
    }, ensure_ascii=False)


@tool
def find_symbol_occurrences(file_path: str, symbol: str) -> str:
    """Find exact identifier occurrences and return line evidence plus receipt."""
    receipt = _lookup_source_receipt(file_path)
    if receipt is None:
        return json.dumps({"error": "source_not_available", "file_path": file_path})
    if not isinstance(symbol, str) or _IDENTIFIER_RE.fullmatch(symbol) is None:
        return json.dumps({"error": "invalid_identifier", "file_path": file_path})

    pattern = re.compile(
        rf"(?<![A-Za-z0-9_$]){re.escape(symbol)}(?![A-Za-z0-9_$])"
    )
    occurrences = []
    for line_number, line in enumerate(receipt["content"].splitlines(), 1):
        columns = [match.start() + 1 for match in pattern.finditer(line)]
        if columns:
            occurrences.append({
                "line": line_number,
                "columns": columns,
                "text": line,
            })
        if len(occurrences) >= 50:
            break
    return json.dumps({
        "file_path": receipt["path"],
        "revision": receipt.get("revision"),
        "content_digest": receipt["content_digest"],
        "complete_source": receipt["complete_source"],
        "symbol": symbol,
        "occurrence_count": sum(len(item["columns"]) for item in occurrences),
        "occurrences": occurrences,
    }, ensure_ascii=False)


class VerificationDropEvidence(BaseModel):
    """Machine-checkable receipt supporting one proposed exact-review drop."""
    issue_id: str
    file_path: str
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_kind: Literal["anchor_missing", "named_symbol_present", "unused_symbol_used"]
    observed: str


class VerificationDecision(BaseModel):
    """Explicit disposition and evidence for one exact-review candidate."""

    model_config = ConfigDict(extra="forbid")

    issue_id: str
    finding_type: Literal["DEFECT", "ADVISORY"]
    verification_status: Literal["CONFIRMED", "REJECTED", "INCONCLUSIVE"]
    file_path: Optional[str] = None
    line: Optional[int] = Field(default=None, ge=1)
    code_snippet: Optional[str] = None
    content_digest: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    precondition: str = ""
    reachable_path: str = ""
    failure: str = ""
    impact: str = ""
    counter_evidence: str = ""

class VerificationResult(BaseModel):
    """Result of the verification agent."""
    issue_ids_to_drop: List[str] = Field(
        default_factory=list,
        description="List of issue IDs that were verified as false positives (e.g., the symbol actually exists)."
    )
    drop_evidence: List[VerificationDropEvidence] = Field(default_factory=list)
    decisions: List[VerificationDecision] = Field(default_factory=list)


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


def _build_source_receipts(request: ReviewRequestDto) -> Dict[str, Dict[str, Any]]:
    """Build independently hash-checked receipts for complete current files."""
    manifest = getattr(request, "executionManifest", None)
    artifact_by_path = {}
    if manifest is not None:
        artifact_by_path = {
            artifact.contentKey.lstrip("/"): artifact
            for artifact in manifest.inputArtifacts
            if artifact.kind == "source-file"
        }

    legacy_revision = None
    if manifest is None:
        candidate_revision = request.get_rag_branch()
        legacy_revision = candidate_revision if isinstance(candidate_revision, str) else None

    receipts: Dict[str, Dict[str, Any]] = {}
    enrichment = getattr(request, "enrichmentData", None)
    for file_content in getattr(enrichment, "fileContents", None) or []:
        if not file_content.content or getattr(file_content, "skipped", False) is True:
            continue
        path = file_content.path.lstrip("/")
        digest = sha256(file_content.content.encode("utf-8")).hexdigest()
        artifact = artifact_by_path.get(path)
        if manifest is not None and (
            artifact is None
            or artifact.snapshotSha != manifest.headSha
            or artifact.contentDigest != digest
        ):
            logger.error("Exact verification source receipt mismatch for %s", path)
            continue
        receipts[path] = {
            "path": path,
            "content": file_content.content,
            "content_digest": digest,
            "execution_id": manifest.executionId if manifest is not None else None,
            "revision": manifest.headSha if manifest is not None else legacy_revision,
            "artifact_id": artifact.artifactId if artifact is not None else None,
            "complete_source": True,
            "snapshot_verified": manifest is not None,
        }
    return receipts


def _drop_invalid_exact_anchors(
    issues: List[CodeReviewIssue],
    receipts: Dict[str, Dict[str, Any]],
) -> Tuple[List[CodeReviewIssue], List[str]]:
    """Discard candidate findings whose claimed verbatim anchor is absent."""
    kept = []
    dropped = []
    for index, issue in enumerate(issues):
        receipt = _receipt_for_issue(receipts, issue)
        if receipt is None:
            kept.append(issue)
            continue
        snippet = _issue_field(issue, "codeSnippet")
        if not snippet or snippet not in receipt["content"]:
            dropped.append(_verification_issue_id(index, issue))
        else:
            kept.append(issue)
    return kept, dropped


def _receipt_for_issue(
    receipts: Dict[str, Dict[str, Any]],
    issue: CodeReviewIssue,
) -> Optional[Dict[str, Any]]:
    path = _issue_field(issue, "file").lstrip("/")
    direct = receipts.get(path)
    if direct is not None:
        return direct
    matches = [
        receipt
        for candidate, receipt in receipts.items()
        if candidate.endswith("/" + path) or path.endswith("/" + candidate)
    ]
    return matches[0] if len(matches) == 1 else None


def _validated_exact_drop_ids(
    result: VerificationResult,
    verification_records: List[Tuple[str, CodeReviewIssue]],
    receipts: Dict[str, Dict[str, Any]],
) -> set[str]:
    """Accept LLM drop decisions only when their receipt proves the claim type."""
    requested = {str(issue_id).strip() for issue_id in result.issue_ids_to_drop}
    issue_by_id = dict(verification_records)
    validated = set()
    for evidence in result.drop_evidence:
        issue_id = evidence.issue_id.strip()
        issue = issue_by_id.get(issue_id)
        if issue is None or issue_id not in requested:
            continue
        receipt = _receipt_for_issue(receipts, issue)
        if (
            receipt is None
            or not receipt.get("snapshot_verified")
            or evidence.file_path.lstrip("/") != receipt["path"]
            or evidence.content_digest != receipt["content_digest"]
        ):
            continue

        snippet = _issue_field(issue, "codeSnippet")
        claim = f"{_issue_field(issue, 'title')}\n{_issue_field(issue, 'reason')}"
        if evidence.evidence_kind == "anchor_missing":
            proven = bool(snippet) and snippet not in receipt["content"]
        elif evidence.evidence_kind == "unused_symbol_used":
            proven = (
                evidence.observed in _unused_import_candidates(issue)
                and _symbol_occurs_outside_anchor(
                    evidence.observed,
                    snippet,
                    receipt["content"],
                )
            )
        else:
            claim_identifiers = set(_IDENTIFIER_RE.findall(claim))
            proven = (
                _MISSING_SYMBOL_CLAIM_RE.search(claim) is not None
                and evidence.observed in claim_identifiers
                and re.search(
                    rf"(?<![A-Za-z0-9_$]){re.escape(evidence.observed)}"
                    rf"(?![A-Za-z0-9_$])",
                    receipt["content"],
                ) is not None
            )
        if proven:
            validated.add(issue_id)
    return validated


def _validated_exact_publications(
    result: VerificationResult,
    verification_records: List[Tuple[str, CodeReviewIssue]],
    receipts: Dict[str, Dict[str, Any]],
    *,
    raw_diff: str,
    execution_id: str,
    head_sha: str,
) -> List[CodeReviewIssue]:
    """Accept only uniquely decided findings with exact changed-source proof."""

    decisions_by_id: Dict[str, List[VerificationDecision]] = {}
    for decision in result.decisions:
        decisions_by_id.setdefault(decision.issue_id.strip(), []).append(decision)

    changed_lines = changed_lines_from_diff(raw_diff)
    accepted: List[CodeReviewIssue] = []
    for issue_id, issue in verification_records:
        decisions = decisions_by_id.get(issue_id, [])
        if len(decisions) != 1:
            continue
        decision = decisions[0]
        receipt = _receipt_for_issue(receipts, issue)
        proof: Optional[ExactSourceProof] = None

        if (
            receipt is not None
            and receipt.get("snapshot_verified") is True
            and receipt.get("execution_id") == execution_id
            and receipt.get("revision") == head_sha
            and decision.file_path is not None
            and decision.line is not None
            and decision.code_snippet is not None
            and decision.content_digest is not None
            and decision.file_path.lstrip("/") == receipt.get("path")
            and decision.content_digest == receipt.get("content_digest")
        ):
            source_lines = str(receipt.get("content") or "").splitlines()
            line_index = decision.line - 1
            source_matches = (
                0 <= line_index < len(source_lines)
                and source_lines[line_index].strip() == decision.code_snippet.strip()
            )
            proof = ExactSourceProof(
                execution_id=execution_id,
                head_sha=head_sha,
                path=receipt["path"],
                line=decision.line,
                code_snippet=decision.code_snippet,
                source_digest=decision.content_digest,
                verified=source_matches,
            )

        publishable = accept_exact_publication(
            issue,
            ExactPublicationClaim(
                finding_type=decision.finding_type,
                verification_status=decision.verification_status,
                precondition=decision.precondition,
                reachable_path=decision.reachable_path,
                failure=decision.failure,
                impact=decision.impact,
                counter_evidence=decision.counter_evidence,
            ),
            proof,
            changed_lines=changed_lines,
            execution_id=execution_id,
            head_sha=head_sha,
        )
        if publishable is not None:
            accepted.append(publishable)
    return accepted


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

    if is_manifest_bound_v1(request):
        issues, invalid_anchor_ids = _drop_invalid_exact_anchors(
            issues,
            _build_source_receipts(request),
        )
        if invalid_anchor_ids:
            logger.info(
                "Deterministic exact anchor gate dropped %d invalid finding(s): %s",
                len(invalid_anchor_ids),
                invalid_anchor_ids,
            )
        if not issues:
            return []

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


def _invoke_verification_tool(name: str, args: Any) -> str:
    if name == "search_file_content":
        return _invoke_search_file_content(args)
    if not isinstance(args, dict):
        return f"Error: {name} arguments must be an object."
    try:
        if name == "read_source_span":
            payload = {
                "file_path": str(args.get("file_path") or ""),
                "start_line": args.get("start_line"),
                "end_line": args.get("end_line"),
            }
            return (
                read_source_span.invoke(payload)
                if hasattr(read_source_span, "invoke")
                else read_source_span(**payload)
            )
        if name == "find_symbol_occurrences":
            payload = {
                "file_path": str(args.get("file_path") or ""),
                "symbol": str(args.get("symbol") or ""),
            }
            return (
                find_symbol_occurrences.invoke(payload)
                if hasattr(find_symbol_occurrences, "invoke")
                else find_symbol_occurrences(**payload)
            )
    except Exception as error:
        return f"Error: {name} failed validation: {type(error).__name__}."
    return f"Error: unsupported tool '{name}'."


def _parse_verification_result(content: str) -> VerificationResult:
    _, data = load_json_with_local_repairs(content)
    return VerificationResult(**data)


async def _run_verification_tool_loop(llm, prompt: str) -> VerificationResult:
    if not hasattr(llm, "bind_tools"):
        raise RuntimeError("LLM does not support tool binding")

    llm_with_tools = llm.bind_tools([
        search_file_content,
        read_source_span,
        find_symbol_occurrences,
    ])
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

            tool_result = _invoke_verification_tool(name, args)

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

    manifest_bound = is_manifest_bound_v1(request)
    evidence_by_path = _build_file_evidence(request, processed_diff)
    if not evidence_by_path:
        logger.info("Stage 1.5: No current-file or diff evidence available.")
        return [] if manifest_bound else issues

    source_receipts = _build_source_receipts(request)
    if manifest_bound:
        issues, invalid_anchor_ids = _drop_invalid_exact_anchors(
            issues,
            source_receipts,
        )
        if invalid_anchor_ids:
            logger.info(
                "Stage 1.5: Exact anchor gate dropped %d finding(s) absent from source: %s",
                len(invalid_anchor_ids),
                invalid_anchor_ids,
            )
        if not issues:
            return []

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
        return [] if manifest_bound else issues

    cache_token = _ACTIVE_FILE_CONTENTS.set(full_file_contents)
    receipt_token = _ACTIVE_SOURCE_RECEIPTS.set(source_receipts)

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

    exact_evidence_policy = ""
    if manifest_bound:
        exact_evidence_policy = """
This is an exact-snapshot accept-only review. Return exactly one `decisions`
entry for every Verification ID. Classify `finding_type` as `DEFECT` only for
an actionable correctness, security, reliability, or material performance
failure; optional hardening, style, documentation, refactoring, and test wishes
are `ADVISORY`. Classify `verification_status` as `CONFIRMED` only when the
complete exact-head source proves the defect; otherwise use `REJECTED` or
`INCONCLUSIVE`.

For every CONFIRMED DEFECT, call `read_source_span` and copy its exact file path,
changed line number, verbatim full source line, and content digest into
`file_path`, `line`, `code_snippet`, and `content_digest`. The line must be an
added line in this PR. Also provide a concrete evidence chain: `precondition`
states the runtime input or state required; `reachable_path` identifies how
execution reaches the changed line; `failure` names the violated invariant or
incorrect operation; `impact` describes the concrete resulting harm; and
`counter_evidence` states which guards, callers, tests, configuration, or
contracts were inspected and why they do not disprove the finding. If any link
cannot be established from exact source, return INCONCLUSIVE. Never invent a
digest. Rejected, inconclusive, and advisory decisions may leave chain fields
empty and source-coordinate fields null.

For SECURITY, prove attacker-controlled input, a reachable entry point, the
missing or bypassed protection, and concrete impact. For PERFORMANCE, prove the
expensive operation, its repeated execution path, relevant cache/loading state,
and plausible workload scale. For cross-file or architectural claims, inspect
both sides of the interaction and identify the conflicting contract or duplicate
side effect and its impact.
"""

    prompt = f"""You are a Verification Agent for a code review system.
Your job is to verify whether the following issues are false positives using full file content.

You have access to `search_file_content`, `read_source_span`, and
`find_symbol_occurrences`. The latter two return immutable source receipts.
For each issue, decide whether checking exact strings in the file would help verify the claim.
Use the tool only when the issue depends on whether a symbol, method, import, line, or nearby code exists in the full file.
When checks are useful, issue all `search_file_content` calls together in the same tool round.

Drop an issue only when file-content evidence clearly proves it is a false positive.
Keep the issue when evidence is inconclusive or the issue is not verifiable with exact string search.
{exact_evidence_policy}

Issues to verify:
{issues_json}

Return ONLY a JSON object containing `issue_ids_to_drop`, `drop_evidence`, and
`decisions`. Legacy reviews use `issue_ids_to_drop` and may return an empty
`decisions` list. Exact-snapshot reviews must use `decisions`; their drop fields
may be empty. Use the exact Verification ID values above.
"""

    try:
        result = await _run_verification_tool_loop(llm, prompt)
        if manifest_bound:
            manifest = request.executionManifest
            final_issues = _validated_exact_publications(
                result,
                verification_records,
                source_receipts,
                raw_diff=request.rawDiff or "",
                execution_id=manifest.executionId,
                head_sha=manifest.headSha,
            )
            logger.info(
                "Stage 1.5: Exact accept-only gate retained %d of %d candidate(s).",
                len(final_issues),
                len(verification_records),
            )
        else:
            ids_to_drop = {
                str(issue_id).strip()
                for issue_id in result.issue_ids_to_drop
                if str(issue_id).strip()
            }
            logger.info(
                "Stage 1.5: Agent identified %d false positives to drop.",
                len(ids_to_drop),
            )
            final_issues = [
                issue
                for verification_id, issue in verification_records
                if verification_id not in ids_to_drop
            ]
        
    except Exception as e:
        logger.error(
            "Stage 1.5 verification failed: error_type=%s",
            type(e).__name__,
        )
        if manifest_bound:
            raise
        # Fallback: keep all issues if verification fails
        final_issues = issues
    finally:
        _ACTIVE_FILE_CONTENTS.reset(cache_token)
        _ACTIVE_SOURCE_RECEIPTS.reset(receipt_token)

    return final_issues
