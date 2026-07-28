"""
Issue reconciliation and deduplication logic for incremental reviews.
"""
import logging
import difflib
import asyncio
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional

from model.output_schemas import CodeReviewIssue, DeduplicatedIssueList
from service.review.candidate_ledger import CandidateEvidenceLedger
from utils.llm_response import extract_llm_response_text
from service.review.orchestrator.json_utils import parse_llm_response, supports_structured_output
from utils.path_identity import repository_paths_match

logger = logging.getLogger(__name__)

_DEFAULT_RESOLUTION_TEXT = (
    "Resolved in the current PR review iteration; no specific resolution "
    "explanation was provided."
)


def _resolution_text(data: Dict[str, Any]) -> Optional[str]:
    """Read either Python or client-facing historical resolution field."""
    for key in ('resolutionReason', 'resolutionExplanation', 'resolvedDescription'):
        value = data.get(key)
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return None


def _previous_issue_status(data: Dict[str, Any]) -> str:
    return str(data.get('status') or '').strip().lower()


def _is_open_previous_issue(data: Dict[str, Any]) -> bool:
    """Only explicit OPEN and legacy blank statuses participate in reconciliation."""
    return _previous_issue_status(data) in {'', 'open'}


def _deduplicate_reconciled_history(
    issues: List[CodeReviewIssue],
    previous_ids: set[str],
) -> List[CodeReviewIssue]:
    """Return at most one lifecycle record per previous issue ID."""
    deduped: List[CodeReviewIssue] = []
    positions: Dict[str, int] = {}

    def preference(issue: CodeReviewIssue) -> tuple[bool, bool]:
        data = issue.model_dump()
        resolved = data.get('isResolved') is True
        resolution = _resolution_text(data)
        specific_resolution = bool(
            resolution and resolution != _DEFAULT_RESOLUTION_TEXT
        )
        return resolved, specific_resolution

    for issue in issues:
        issue_id = str(getattr(issue, 'id', '') or '').strip()
        if not issue_id or issue_id not in previous_ids:
            deduped.append(issue)
            continue

        existing_position = positions.get(issue_id)
        if existing_position is None:
            positions[issue_id] = len(deduped)
            deduped.append(issue)
            continue

        existing = deduped[existing_position]
        if preference(issue) > preference(existing):
            deduped[existing_position] = issue

    return deduped


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


def issue_matches_files(issue: Any, file_paths: List[str]) -> bool:
    """Check if an issue is related to any of the given file paths.
    
    Matches on exact path, or when one path is a suffix of the other
    (handles relative vs absolute paths). Does NOT match on basename alone
    to avoid false positives (e.g., two different utils.py files).
    """
    if hasattr(issue, 'model_dump'):
        issue_data = issue.model_dump()
    elif isinstance(issue, dict):
        issue_data = issue
    else:
        issue_data = vars(issue) if hasattr(issue, '__dict__') else {}
    
    issue_file = issue_data.get('file', issue_data.get('filePath', ''))
    if not issue_file:
        return False
    
    for fp in file_paths:
        if repository_paths_match(issue_file, fp):
            return True
    return False


def compute_issue_fingerprint(data: dict) -> str:
    """Compute a fingerprint for prompt-level issue deduplication.
    
    NOTE: This is used only for deduplicating previous issues before including
    them in LLM prompts. It is NOT related to Java's IssueFingerprint which
    uses SHA-256 of (category + lineHash + normalizedTitle) for persistent
    content-based tracking.
    
    Uses file + normalized line (±3 tolerance) + severity + truncated reason.
    """
    file_path = data.get('file', data.get('filePath', ''))
    line = data.get('line', data.get('lineNumber', 0))
    line_group = int(line) // 3 if line else 0
    severity = data.get('severity', '')
    reason = data.get('reason', data.get('description', ''))
    reason_prefix = reason[:50].lower().strip() if reason else ''
    
    return f"{file_path}::{line_group}::{severity}::{reason_prefix}"


def is_semantically_similar(reason1: str, reason2: str, threshold: float = 0.70) -> bool:
    """Check if two issue reasons are semantically similar using SequenceMatcher."""
    if not reason1 or not reason2:
        return False
    # Normalize strings
    r1 = reason1.lower().strip()
    r2 = reason2.lower().strip()
    
    # Quick exact match
    if r1 == r2:
        return True
        
    # Use difflib for similarity ratio
    matcher = difflib.SequenceMatcher(None, r1, r2)
    return matcher.ratio() >= threshold


def _issue_payload(issue: Any) -> Dict[str, Any]:
    if hasattr(issue, "model_dump"):
        return issue.model_dump()
    if isinstance(issue, dict):
        return issue
    return vars(issue) if hasattr(issue, "__dict__") else {}


def _normalized_anchor(value: Any) -> str:
    return " ".join(str(value or "").split())


def _issues_share_exact_anchor(
    left: Dict[str, Any],
    right: Dict[str, Any],
) -> bool:
    def line_number(data: Dict[str, Any]) -> int:
        try:
            return int(data.get("line") or data.get("lineNumber") or 0)
        except (TypeError, ValueError):
            return 0

    left_line = line_number(left)
    right_line = line_number(right)
    left_snippet = _normalized_anchor(
        left.get("codeSnippet") or left.get("code_snippet")
    )
    right_snippet = _normalized_anchor(
        right.get("codeSnippet") or right.get("code_snippet")
    )
    if left_snippet and right_snippet:
        return left_snippet == right_snippet

    return left_line > 0 and left_line == right_line


def _issues_share_exact_plugin_proof(
    left: Dict[str, Any],
    right: Dict[str, Any],
) -> bool:
    """Use plugin proof identity as a prose-independent root-cause key."""
    left_kind = str(
        left.get("claimKind") or left.get("claim_kind") or ""
    ).strip()
    right_kind = str(
        right.get("claimKind") or right.get("claim_kind") or ""
    ).strip()
    if not left_kind or left_kind != right_kind:
        return False

    def evidence_ids(data: Dict[str, Any]) -> tuple[str, ...]:
        raw = data.get("evidenceRefs")
        if raw is None:
            raw = data.get("evidence_refs")
        if not isinstance(raw, (list, tuple, set)):
            return ()
        return tuple(sorted({
            str(value).strip()
            for value in raw
            if str(value).strip()
        }))

    left_evidence = evidence_ids(left)
    right_evidence = evidence_ids(right)
    return bool(left_evidence) and left_evidence == right_evidence


def issues_are_conservative_duplicates(
    left: Any,
    right: Any,
) -> bool:
    """Require location, category, anchor, and root-cause agreement.

    Similar prose alone is not an issue identity. Repeated guard/validation
    defects in different files or at different lines remain separate findings.
    """
    left_data = _issue_payload(left)
    right_data = _issue_payload(right)
    left_file = str(
        left_data.get("file") or left_data.get("filePath") or ""
    ).replace("\\", "/")
    right_file = str(
        right_data.get("file") or right_data.get("filePath") or ""
    ).replace("\\", "/")
    if not left_file or left_file != right_file:
        return False

    # Selected plugins supply stable proof identities. An exact match ties both
    # candidates to the same framework/language relationship even when model
    # prose, category, or the chosen line anchor differs across batches.
    if _issues_share_exact_plugin_proof(left_data, right_data):
        return True

    left_category = str(left_data.get("category") or "").upper()
    right_category = str(right_data.get("category") or "").upper()
    if not left_category or left_category != right_category:
        return False
    if not _issues_share_exact_anchor(left_data, right_data):
        return False

    left_reason = left_data.get("reason") or left_data.get("description") or ""
    right_reason = (
        right_data.get("reason") or right_data.get("description") or ""
    )
    return is_semantically_similar(
        str(left_reason),
        str(right_reason),
        threshold=0.82,
    )


def deduplicate_issues(issues: List[Any]) -> List[dict]:
    """Deduplicate issues by fingerprint, keeping most recent version.
    
    If an older version is resolved but newer isn't, preserves resolved status.
    """
    if not issues:
        return []
    
    deduped: dict = {}
    
    for issue in issues:
        if hasattr(issue, 'model_dump'):
            data = issue.model_dump()
        elif isinstance(issue, dict):
            data = issue.copy()
        else:
            data = vars(issue).copy() if hasattr(issue, '__dict__') else {}

        resolution = _resolution_text(data)
        if resolution:
            data['resolutionReason'] = resolution
            data['resolutionExplanation'] = resolution
        
        fingerprint = compute_issue_fingerprint(data)
        existing = deduped.get(fingerprint)
        
        if existing is None:
            deduped[fingerprint] = data
        else:
            existing_version = existing.get('prVersion') or 0
            current_version = data.get('prVersion') or 0
            existing_closed = not _is_open_previous_issue(existing)
            current_closed = not _is_open_previous_issue(data)
            
            if current_version > existing_version:
                # Current is newer
                if existing_closed and not current_closed:
                    # Preserve a terminal resolved/ignored status from older history.
                    data['status'] = existing.get('status')
                    resolution = _resolution_text(existing)
                    data['resolutionReason'] = resolution
                    data['resolutionExplanation'] = resolution
                    data['resolvedInCommit'] = existing.get('resolvedInCommit') or existing.get('resolvedByCommit')
                    data['resolvedInPrVersion'] = existing.get('resolvedInPrVersion')
                deduped[fingerprint] = data
            elif current_version == existing_version:
                # Same version - prefer a terminal resolved/ignored record.
                if current_closed and not existing_closed:
                    deduped[fingerprint] = data
    
    return list(deduped.values())


def format_previous_issues_for_batch(issues: List[Any]) -> str:
    """Format previous issues for inclusion in batch prompt.
    
    Only OPEN (or legacy blank-status) issues belong in runtime analysis.
    RESOLVED/IGNORED history remains in storage and is intentionally omitted so
    it cannot prime the model to recreate an already-closed finding.
    """
    if not issues:
        return ""
    
    # Deduplicate issues to avoid confusing the LLM with duplicates
    deduped_issues = deduplicate_issues(issues)
    
    # Only OPEN and legacy blank statuses may be reconciled.
    open_issues = [i for i in deduped_issues if _is_open_previous_issue(i)]
    if not open_issues:
        return ""
    
    lines = ["=== PREVIOUS ISSUES HISTORY (check if resolved/persisting) ==="]
    lines.append("Only OPEN issues are included; terminal history is excluded from runtime context.")
    lines.append("")
    
    if open_issues:
        lines.append("--- OPEN ISSUES (check if now fixed) ---")
        for data in open_issues:
            issue_id = data.get('id', 'unknown')
            severity = data.get('severity', 'MEDIUM')
            file_path = data.get('file', data.get('filePath', 'unknown'))
            line = data.get('line', data.get('lineNumber', '?'))
            title = data.get('title') or ''
            reason = data.get('reason', data.get('description', 'No description'))
            pr_version = data.get('prVersion', '?')
            
            title_part = f" [{title}]" if title else ""
            lines.append(f"[ID:{issue_id}] {severity}{title_part} @ {file_path}:{line} (v{pr_version})")
            lines.append(f"  Issue: {reason}")
            lines.append("")
    
    lines.append("INSTRUCTIONS:")
    lines.append("- For OPEN issues that are now FIXED: report with 'isResolved': true (boolean)")
    lines.append("- For OPEN issues still present: report with 'isResolved': false (boolean)")
    lines.append("- RESOLVED and IGNORED history is intentionally absent; never recreate it")
    lines.append("- IMPORTANT: 'isResolved' MUST be a JSON boolean (true/false), not a string")
    lines.append("- Preserve the 'id' field for all issues you report from previous issues")
    lines.append("- ⚠️ CRITICAL: DO NOT create a NEW issue (with a new ID or no ID) for a problem that is already covered by an OPEN previous issue. You MUST reuse the existing 'id'.")
    lines.append("=== END PREVIOUS ISSUES ===")
    return "\n".join(lines)


def deduplicate_final_issues(issues: List[CodeReviewIssue]) -> List[CodeReviewIssue]:
    """
    Final deduplication pass after ALL issue-finding stages complete
    (Stage 1, Reconciliation, Verification, Stage 2 cross-file).

    Conservative deterministic dedup requires the same normalized file,
    category, exact line or exact current-source snippet, and closely matching
    root-cause text. Similar prose by itself never suppresses another finding.
    """
    if not issues:
        return []

    deduped: List[CodeReviewIssue] = []
    for issue in issues:
        if any(
            issues_are_conservative_duplicates(issue, existing)
            for existing in deduped
        ):
            data = _issue_payload(issue)
            logger.info(
                "Final deterministic dedup: suppressed anchored duplicate at "
                "%s:%s",
                data.get("file", ""),
                data.get("line", ""),
            )
            continue
        deduped.append(issue)

    original = len(issues)
    final = len(deduped)
    if original != final:
        logger.info(f"Final dedup: {original} → {final} issues ({original - final} duplicates removed)")
    return deduped


# ---------------------------------------------------------------------------
#  LLM-based deduplication
# ---------------------------------------------------------------------------

_DEDUP_BATCH_SIZE = 50
_DEDUP_MAX_PARALLEL = max(1, _env_int("REVIEW_DEDUP_MAX_PARALLEL", 4))

_DEDUP_SYSTEM_PROMPT = (
    "You are a code review deduplication assistant.  You will receive a list of "
    "code-review issues (each with an index, file, line, severity, category, and "
    "reason).  Your task is to identify **semantic duplicates** — issues that "
    "describe the same underlying problem even if they use different wording, "
    "slightly different line numbers in the same file, or were found by different "
    "analysis stages.\n\n"
    "Rules:\n"
    "1. Two issues are duplicates if they point to the SAME root cause in the "
    "SAME file (small line-number differences are OK).\n"
    "2. When you find duplicates, KEEP the one with the most detailed/useful "
    "reason text and DROP the rest.\n"
    "3. Issues in DIFFERENT files are NEVER duplicates of each other.\n"
    "4. Return ONLY the 0-based indices of the issues you decide to KEEP.\n"
    "5. If there are no duplicates at all, return every index."
)


def _format_issues_for_prompt(issues: List[CodeReviewIssue]) -> str:
    """Render a numbered list of issues for the deduplication prompt."""
    lines: List[str] = []
    for idx, issue in enumerate(issues):
        data = issue.model_dump() if hasattr(issue, 'model_dump') else issue
        file_path = data.get('file', '?')
        line = data.get('line', '?')
        severity = data.get('severity', '?')
        category = data.get('category', '?')
        title = data.get('title') or ''
        reason = data.get('reason', data.get('description', ''))
        title_part = f" | {title}" if title else ""
        lines.append(
            f"[{idx}] {severity} | {category}{title_part} | {file_path}:{line}\n"
            f"    Reason: {reason}"
        )
    return "\n".join(lines)


def _build_batches(issues: List[CodeReviewIssue],
                   max_batch_size: int = _DEDUP_BATCH_SIZE,
                   ) -> List[List[CodeReviewIssue]]:
    """Group issues by filepath, then pack filepath-groups into batches ≤ max_batch_size.

    • Issues belonging to the same file are NEVER split across batches.
    • If a single file has more issues than *max_batch_size*, it gets its own
      (oversized) batch so that all issues for a file are always evaluated
      together.
    """
    file_groups: Dict[str, List[CodeReviewIssue]] = defaultdict(list)
    for issue in issues:
        data = issue.model_dump() if hasattr(issue, 'model_dump') else issue
        file_groups[data.get('file', '')].append(issue)

    batches: List[List[CodeReviewIssue]] = []
    current_batch: List[CodeReviewIssue] = []

    for _file_path, group in file_groups.items():
        # If adding this whole file-group would exceed the limit, flush first
        if current_batch and len(current_batch) + len(group) > max_batch_size:
            batches.append(current_batch)
            current_batch = []
        current_batch.extend(group)

    if current_batch:
        batches.append(current_batch)

    return batches


async def _dedup_batch_with_llm(
    llm,
    batch: List[CodeReviewIssue],
) -> List[CodeReviewIssue]:
    """Send one batch to the LLM and return the kept issues."""
    issues_text = _format_issues_for_prompt(batch)
    prompt = (
        f"{_DEDUP_SYSTEM_PROMPT}\n\n"
        f"Here are the issues to deduplicate:\n\n{issues_text}\n\n"
        "Return the kept_indices list."
    )

    try:
        if supports_structured_output(llm):
            structured_llm = llm.with_structured_output(DeduplicatedIssueList)
            result: DeduplicatedIssueList = await structured_llm.ainvoke(prompt)
        else:
            logger.info("Structured output skipped for LLM dedup batch; using prompt JSON parsing")
            response = await llm.ainvoke(prompt)
            result = await parse_llm_response(
                extract_llm_response_text(response),
                DeduplicatedIssueList,
                llm,
            )

        kept_indices = set(result.kept_indices)
        # Sanity-check: indices must be within range
        valid = {i for i in kept_indices if 0 <= i < len(batch)}
        if not valid:
            logger.warning(
                "LLM dedup returned no valid indices — keeping all issues in batch"
            )
            return batch

        kept = [batch[i] for i in sorted(valid)]
        dropped = len(batch) - len(kept)
        if dropped:
            logger.info(f"LLM dedup batch: kept {len(kept)}/{len(batch)} issues (dropped {dropped})")
        return kept

    except Exception as exc:
        logger.warning(f"LLM dedup batch failed ({exc}); falling back to algorithmic dedup")
        return deduplicate_final_issues(batch)


async def deduplicate_final_issues_llm(
    llm,
    issues: List[CodeReviewIssue],
) -> List[CodeReviewIssue]:
    """Primary LLM-driven deduplication.

    1. Groups issues by filepath.
    2. Packs filepath-groups into batches of ≤ 50 issues.
    3. Sends each batch to the LLM to identify semantic duplicates.
    4. Returns the union of kept issues from all batches.

    Falls back to ``deduplicate_final_issues`` (algorithmic) for any batch
    where the LLM call fails.
    """
    if not issues:
        return []

    if len(issues) <= 1:
        return issues

    batches = _build_batches(issues, max_batch_size=_DEDUP_BATCH_SIZE)
    logger.info(
        f"LLM dedup: {len(issues)} issues split into {len(batches)} batch(es) "
        f"(sizes: {[len(b) for b in batches]}, concurrency={_DEDUP_MAX_PARALLEL})"
    )

    semaphore = asyncio.Semaphore(_DEDUP_MAX_PARALLEL)
    batch_results: Dict[int, List[CodeReviewIssue]] = {}

    async def _run_batch(batch_idx: int, batch: List[CodeReviewIssue]) -> tuple[int, List[CodeReviewIssue]]:
        async with semaphore:
            logger.info(
                f"LLM dedup: processing batch {batch_idx + 1}/{len(batches)} "
                f"({len(batch)} issues)"
            )
            kept = await _dedup_batch_with_llm(llm, batch)
            return batch_idx, kept

    tasks = [
        asyncio.create_task(_run_batch(batch_idx, batch))
        for batch_idx, batch in enumerate(batches)
    ]

    for completed_task in asyncio.as_completed(tasks):
        batch_idx, kept = await completed_task
        batch_results[batch_idx] = kept

    kept_issues: List[CodeReviewIssue] = []
    for batch_idx in range(len(batches)):
        kept_issues.extend(batch_results.get(batch_idx, []))

    original = len(issues)
    final = len(kept_issues)
    if original != final:
        logger.info(
            f"LLM dedup total: {original} → {final} issues "
            f"({original - final} duplicates removed)"
        )
    return kept_issues


def deduplicate_cross_batch_issues(issues: List[CodeReviewIssue]) -> List[CodeReviewIssue]:
    """
    Deduplicate repeated anchored findings from overlapping Stage 1 batches.

    Different files, categories, and source anchors are never duplicates even
    when their prose is similar.
    """
    if not issues:
        return []
        
    deduped = []
    for issue in issues:
        if any(
            issues_are_conservative_duplicates(issue, existing)
            for existing in deduped
        ):
            issue_data = _issue_payload(issue)
            logger.info(
                "Cross-batch dedup: suppressed anchored duplicate at %s:%s",
                issue_data.get("file", ""),
                issue_data.get("line", ""),
            )
            continue
        deduped.append(issue)
            
    return deduped

async def reconcile_previous_issues(
    request,
    new_issues: List[CodeReviewIssue],
    processed_diff = None,
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
) -> List[CodeReviewIssue]:
    """
    Reconcile previous issues with new findings in incremental mode.
    - Mark resolved issues as isResolved=true
    - Update line numbers for persisting issues
    - Merge with new issues found in delta diff
    - PRESERVE original issue data (reason, suggestedFixDescription, suggestedFixDiff)
    """
    if not request.previousCodeAnalysisIssues:
        return new_issues
    
    logger.info(f"Reconciling {len(request.previousCodeAnalysisIssues)} previous issues with {len(new_issues)} new issues")
    
    # Current commit for resolution tracking
    current_commit = request.currentCommitHash or request.commitHash
    
    # Get the delta diff content to check what files/lines changed
    delta_diff = request.deltaDiff or ""
    
    # Build a set of files that changed in the delta
    changed_files_in_delta = set()
    if processed_diff:
        for f in processed_diff.files:
            changed_files_in_delta.add(f.path)
    
    # Build lookup of previous issues by ID for merging with LLM results
    prev_issues_by_id = {}
    closed_prev_ids = set()
    for prev_issue in request.previousCodeAnalysisIssues:
        if hasattr(prev_issue, 'model_dump'):
            prev_data = prev_issue.model_dump()
        else:
            prev_data = prev_issue if isinstance(prev_issue, dict) else vars(prev_issue)
        issue_id = prev_data.get('id')
        if issue_id:
            normalized_id = str(issue_id)
            if _is_open_previous_issue(prev_data):
                prev_issues_by_id[normalized_id] = prev_data
            else:
                closed_prev_ids.add(normalized_id)
    
    reconciled_issues = []
    processed_prev_ids = set()  # Track which previous issues we've handled
    
    # Process new issues from LLM - merge with previous issue data if they reference same ID
    for new_issue in new_issues:
        new_data = new_issue.model_dump() if hasattr(new_issue, 'model_dump') else new_issue
        issue_id = new_data.get('id')

        if issue_id and str(issue_id) in closed_prev_ids:
            logger.info(
                "Ignoring model output for terminal previous issue %s",
                issue_id,
            )
            if candidate_ledger is not None:
                candidate_ledger.reject(
                    new_issue,
                    gate="reconciliation",
                    code="terminal_history_id",
                )
            continue
        
        # If no ID provided, check if it's semantically similar to an OPEN previous issue
        if not issue_id:
            new_reason = new_data.get('reason', '')
            new_file = new_data.get('file', '')
            for prev_id, prev_data in prev_issues_by_id.items():
                if prev_data.get('status', '').lower() == 'resolved':
                    continue
                prev_file = prev_data.get('file') or prev_data.get('filePath') or ''
                if new_file == prev_file:
                    prev_reason = prev_data.get('reason') or prev_data.get('title') or prev_data.get('description') or ''
                    if is_semantically_similar(new_reason, prev_reason, threshold=0.70):
                        logger.info(f"Semantic match found: mapping new issue to previous ID {prev_id}")
                        issue_id = prev_id
                        break
        
        # If this issue references a previous issue ID, merge data
        if issue_id and str(issue_id) in prev_issues_by_id:
            prev_data = prev_issues_by_id[str(issue_id)]
            processed_prev_ids.add(str(issue_id))
            
            # Check if LLM marked it resolved
            llm_says_resolved = new_data.get('isResolved', False)

            is_resolved = llm_says_resolved
            
            # Determine resolution metadata
            if is_resolved:
                # Newly resolved by LLM
                resolution_explanation = _resolution_text(new_data) or _DEFAULT_RESOLUTION_TEXT
                resolved_commit = current_commit
            else:
                resolution_explanation = None
                resolved_commit = None
            
            # PRESERVE original identity & description, but use LLM's UPDATED
            # positional data (line, codeSnippet, scope).  The LLM re-analyzed
            # the current code — its line/snippet reflect where the issue IS NOW,
            # not where it was in a previous iteration.
            #
            # Positional fields: prefer new_data → prev_data (LLM re-anchored)
            # Identity/description fields: prefer prev_data → new_data (stable)

            # ── Positional data: LLM re-analysis wins ──
            new_line = new_data.get('line')
            prev_line = prev_data.get('line') or prev_data.get('lineNumber')

            # Safely parse line numbers — the LLM may return non-numeric values
            try:
                new_line_int = int(new_line) if new_line is not None else None
            except (ValueError, TypeError):
                new_line_int = None
                logger.warning(f"Non-numeric new_line value '{new_line}' for issue {issue_id}, ignoring")

            try:
                prev_line_int = int(prev_line) if prev_line is not None else None
            except (ValueError, TypeError):
                prev_line_int = None
                logger.warning(f"Non-numeric prev_line value '{prev_line}' for issue {issue_id}, ignoring")

            # Use the LLM's line if it provided a meaningful one (>= 1),
            # otherwise fall back to previous data, then default to 1.
            # NOTE: Line 1 IS valid — e.g., license header issues, package declarations.
            # The old check (> 1) incorrectly discarded legitimate line-1 findings.
            if new_line_int is not None and new_line_int >= 1:
                merged_line = new_line_int
            elif prev_line_int is not None and prev_line_int >= 1:
                merged_line = prev_line_int
            else:
                merged_line = new_line_int or prev_line_int or 1

            new_snippet = new_data.get('codeSnippet', '')
            prev_snippet = prev_data.get('codeSnippet', '')
            # Prefer non-empty new snippet (LLM just produced it from current code)
            merged_snippet = new_snippet if new_snippet else prev_snippet

            merged_scope = new_data.get('scope') or prev_data.get('scope') or 'LINE'

            merged_issue = CodeReviewIssue(
                id=str(issue_id),
                severity=(prev_data.get('severity') or prev_data.get('issueSeverity') or 'MEDIUM').upper(),
                category=prev_data.get('category') or prev_data.get('issueCategory') or prev_data.get('type') or 'CODE_QUALITY',
                file=prev_data.get('file') or prev_data.get('filePath') or new_data.get('file', 'unknown'),
                line=str(merged_line),
                scope=merged_scope,
                # PRESERVE original title, reason and fix description
                title=prev_data.get('title') or new_data.get('title'),
                reason=prev_data.get('reason') or prev_data.get('title') or prev_data.get('description') or '',
                suggestedFixDescription=prev_data.get('suggestedFixDescription') or prev_data.get('suggestedFix') or '',
                suggestedFixDiff=prev_data.get('suggestedFixDiff') or None,
                isResolved=is_resolved,
                resolutionReason=resolution_explanation,
                resolutionExplanation=resolution_explanation,
                resolvedInCommit=resolved_commit,
                visibility=prev_data.get('visibility'),
                codeSnippet=merged_snippet,
            )
            logger.info(
                f"Reconciled issue {issue_id}: line {prev_line}→{merged_line}, "
                f"snippet={'YES' if merged_snippet else 'NONE'} "
                f"(source={'LLM' if new_snippet else 'prev'}), "
                f"scope={merged_scope}, resolved={is_resolved}"
            )
            if candidate_ledger is not None:
                candidate_ledger.transfer(new_issue, merged_issue)
            reconciled_issues.append(merged_issue)
        else:
            # New issue not referencing previous - keep as is
            reconciled_issues.append(new_issue)
    
    # Process remaining previous issues not handled by LLM
    for prev_issue in request.previousCodeAnalysisIssues:
        if hasattr(prev_issue, 'model_dump'):
            prev_data = prev_issue.model_dump()
        else:
            prev_data = prev_issue if isinstance(prev_issue, dict) else vars(prev_issue)

        if not _is_open_previous_issue(prev_data):
            continue
        
        issue_id = prev_data.get('id')
        if issue_id and str(issue_id) in processed_prev_ids:
            continue  # Already handled above
        
        file_path = prev_data.get('file', prev_data.get('filePath', ''))
        
        # Check if this issue was already found in new issues (by file+line)
        already_reported = False
        for new_issue in new_issues:
            new_data = new_issue.model_dump() if hasattr(new_issue, 'model_dump') else new_issue
            if (new_data.get('file') == file_path and 
                str(new_data.get('line')) == str(prev_data.get('line', prev_data.get('lineNumber')))):
                already_reported = True
                break
        
        if already_reported:
            continue
        
        # Preserve unhandled OPEN history so it remains active until explicitly
        # matched and resolved. Terminal history was skipped above.
        persisting_issue = CodeReviewIssue(
            id=str(issue_id) if issue_id else None,
            severity=(prev_data.get('severity') or prev_data.get('issueSeverity') or 'MEDIUM').upper(),
            category=prev_data.get('category') or prev_data.get('issueCategory') or prev_data.get('type') or 'CODE_QUALITY',
            file=file_path or prev_data.get('file') or prev_data.get('filePath') or 'unknown',
            line=str(prev_data.get('line') or prev_data.get('lineNumber') or '1'),
            scope=prev_data.get('scope') or prev_data.get('issueScope') or 'LINE',
            title=prev_data.get('title'),
            reason=prev_data.get('reason') or prev_data.get('title') or prev_data.get('description') or '',
            suggestedFixDescription=prev_data.get('suggestedFixDescription') or prev_data.get('suggestedFix') or '',
            suggestedFixDiff=prev_data.get('suggestedFixDiff') or None,
            isResolved=False,
            resolutionReason=None,
            resolutionExplanation=None,
            resolvedInCommit=None,
            visibility=prev_data.get('visibility'),
            codeSnippet=prev_data.get('codeSnippet') or ''
        )
        reconciled_issues.append(persisting_issue)
    
    before_history_dedup = list(reconciled_issues)
    reconciled_issues = _deduplicate_reconciled_history(
        reconciled_issues,
        set(prev_issues_by_id),
    )
    if candidate_ledger is not None:
        candidate_ledger.reject_removed(
            before_history_dedup,
            reconciled_issues,
            gate="reconciliation",
            code="duplicate_history_identity",
        )
    resolved_kept = sum(1 for i in reconciled_issues if (hasattr(i, 'isResolved') and i.isResolved) or (isinstance(i, dict) and i.get('isResolved')))
    logger.info(f"Reconciliation complete: {len(reconciled_issues)} total issues ({resolved_kept} preserved as resolved)")
    return reconciled_issues
