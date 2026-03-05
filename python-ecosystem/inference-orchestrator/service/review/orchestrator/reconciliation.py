"""
Issue reconciliation and deduplication logic for incremental reviews.
"""
import logging
import difflib
from collections import defaultdict
from typing import Any, Dict, List, Optional

from model.output_schemas import CodeReviewIssue, DeduplicatedIssueList

logger = logging.getLogger(__name__)


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
        if issue_file == fp or issue_file.endswith('/' + fp) or fp.endswith('/' + issue_file):
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
        
        fingerprint = compute_issue_fingerprint(data)
        existing = deduped.get(fingerprint)
        
        if existing is None:
            deduped[fingerprint] = data
        else:
            existing_version = existing.get('prVersion') or 0
            current_version = data.get('prVersion') or 0
            existing_resolved = existing.get('status', '').lower() == 'resolved'
            current_resolved = data.get('status', '').lower() == 'resolved'
            
            if current_version > existing_version:
                # Current is newer
                if existing_resolved and not current_resolved:
                    # Preserve resolved status from older version
                    data['status'] = 'resolved'
                    data['resolutionExplanation'] = existing.get('resolutionExplanation') or existing.get('resolvedDescription')
                    data['resolvedInCommit'] = existing.get('resolvedInCommit') or existing.get('resolvedByCommit')
                    data['resolvedInPrVersion'] = existing.get('resolvedInPrVersion')
                deduped[fingerprint] = data
            elif current_version == existing_version:
                # Same version - prefer resolved
                if current_resolved and not existing_resolved:
                    deduped[fingerprint] = data
    
    return list(deduped.values())


def format_previous_issues_for_batch(issues: List[Any]) -> str:
    """Format previous issues for inclusion in batch prompt.
    
    Deduplicates issues first, then formats with resolution tracking so LLM knows:
    - Which issues were previously found
    - Which have been resolved (and how)
    - Which PR version each issue was found/resolved in
    """
    if not issues:
        return ""
    
    # Deduplicate issues to avoid confusing the LLM with duplicates
    deduped_issues = deduplicate_issues(issues)
    
    # Separate OPEN and RESOLVED issues
    open_issues = [i for i in deduped_issues if i.get('status', '').lower() != 'resolved']
    resolved_issues = [i for i in deduped_issues if i.get('status', '').lower() == 'resolved']
    
    lines = ["=== PREVIOUS ISSUES HISTORY (check if resolved/persisting) ==="]
    lines.append("Issues have been deduplicated. Only check OPEN issues - RESOLVED ones are for context only.")
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
    
    if resolved_issues:
        lines.append("--- RESOLVED ISSUES (for context only, do NOT re-report) ---")
        for data in resolved_issues:
            issue_id = data.get('id', 'unknown')
            severity = data.get('severity', 'MEDIUM')
            file_path = data.get('file', data.get('filePath', 'unknown'))
            line = data.get('line', data.get('lineNumber', '?'))
            title = data.get('title') or ''
            reason = data.get('reason', data.get('description', 'No description'))
            pr_version = data.get('prVersion', '?')
            resolved_desc = data.get('resolutionExplanation') or data.get('resolvedDescription', '')
            resolved_in = data.get('resolvedInPrVersion', '')
            
            title_part = f" [{title}]" if title else ""
            lines.append(f"[ID:{issue_id}] {severity}{title_part} @ {file_path}:{line} (v{pr_version}) - RESOLVED")
            if resolved_desc:
                lines.append(f"  Resolution: {resolved_desc}")
            if resolved_in:
                lines.append(f"  Resolved in: v{resolved_in}")
            lines.append(f"  Original issue: {reason}")
            lines.append("")
    
    lines.append("INSTRUCTIONS:")
    lines.append("- For OPEN issues that are now FIXED: report with 'isResolved': true (boolean)")
    lines.append("- For OPEN issues still present: report with 'isResolved': false (boolean)")
    lines.append("- Do NOT re-report RESOLVED issues - they are only shown for context")
    lines.append("- IMPORTANT: 'isResolved' MUST be a JSON boolean (true/false), not a string")
    lines.append("- Preserve the 'id' field for all issues you report from previous issues")
    lines.append("- ⚠️ CRITICAL: DO NOT create a NEW issue (with a new ID or no ID) for a problem that is already covered by an OPEN previous issue. You MUST reuse the existing 'id'.")
    lines.append("=== END PREVIOUS ISSUES ===")
    return "\n".join(lines)


def deduplicate_final_issues(issues: List[CodeReviewIssue]) -> List[CodeReviewIssue]:
    """
    Final deduplication pass after ALL issue-finding stages complete
    (Stage 1, Reconciliation, Verification, Stage 2 cross-file).

    Three-tier, file-aware dedup:
      1. Exact structural: same file + line + category → keep first
      2. Whole-file wildcard: line ≤ 1 absorbs same file + category at specific lines
      3. File-scoped semantic: same file + similar reason (>0.75) → keep first

    NOTE: This is the lightweight/fallback dedup.  The primary dedup is
    ``deduplicate_final_issues_llm`` which uses an LLM to detect semantic
    duplicates.  This function is only called when the LLM path fails or is
    unavailable.
    """
    if not issues:
        return []

    # ── Tier 1: Exact structural key  (file + line + category) ──
    seen_keys: set = set()
    tier1: List[CodeReviewIssue] = []
    for issue in issues:
        data = issue.model_dump() if hasattr(issue, 'model_dump') else issue
        file_path = data.get('file', '')
        line = str(data.get('line', '1'))
        category = (data.get('category') or '').upper()
        key = f"{file_path}:{line}:{category}"
        if key not in seen_keys:
            seen_keys.add(key)
            tier1.append(issue)
        else:
            logger.info(f"Final dedup (structural): suppressed duplicate {key}")

    # ── Tier 2: Whole-file wildcard  (line ≤ 1 absorbs specific lines) ──
    whole_file_keys: set = set()
    for issue in tier1:
        data = issue.model_dump() if hasattr(issue, 'model_dump') else issue
        line = str(data.get('line', '1'))
        if line in ('0', '1', ''):
            file_path = data.get('file', '')
            category = (data.get('category') or '').upper()
            whole_file_keys.add(f"{file_path}:{category}")

    tier2: List[CodeReviewIssue] = []
    for issue in tier1:
        data = issue.model_dump() if hasattr(issue, 'model_dump') else issue
        line = str(data.get('line', '1'))
        if line not in ('0', '1', ''):
            file_path = data.get('file', '')
            category = (data.get('category') or '').upper()
            if f"{file_path}:{category}" in whole_file_keys:
                logger.info(
                    f"Final dedup (whole-file): suppressed {file_path}:{line}:{category} "
                    f"(absorbed by whole-file issue)"
                )
                continue
        tier2.append(issue)

    # ── Tier 3: File-scoped semantic similarity ──
    file_groups: Dict[str, List[CodeReviewIssue]] = defaultdict(list)
    for issue in tier2:
        data = issue.model_dump() if hasattr(issue, 'model_dump') else issue
        file_groups[data.get('file', '')].append(issue)

    tier3: List[CodeReviewIssue] = []
    for file_path, group in file_groups.items():
        deduped_group: List[CodeReviewIssue] = []
        for issue in group:
            data = issue.model_dump() if hasattr(issue, 'model_dump') else issue
            reason = data.get('reason') or data.get('description') or ''
            is_dup = False
            for existing in deduped_group:
                existing_data = existing.model_dump() if hasattr(existing, 'model_dump') else existing
                existing_reason = existing_data.get('reason') or existing_data.get('description') or ''
                if is_semantically_similar(reason, existing_reason, threshold=0.75):
                    logger.info(
                        f"Final dedup (semantic): suppressed similar issue in "
                        f"{file_path}: {reason[:60]}..."
                    )
                    is_dup = True
                    break
            if not is_dup:
                deduped_group.append(issue)
        tier3.extend(deduped_group)

    original = len(issues)
    final = len(tier3)
    if original != final:
        logger.info(f"Final dedup: {original} → {final} issues ({original - final} duplicates removed)")
    return tier3


# ---------------------------------------------------------------------------
#  LLM-based deduplication
# ---------------------------------------------------------------------------

_DEDUP_BATCH_SIZE = 50

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
        structured_llm = llm.with_structured_output(DeduplicatedIssueList)
        result: DeduplicatedIssueList = await structured_llm.ainvoke(prompt)

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
        f"(sizes: {[len(b) for b in batches]})"
    )

    kept_issues: List[CodeReviewIssue] = []
    for batch_idx, batch in enumerate(batches):
        logger.info(f"LLM dedup: processing batch {batch_idx + 1}/{len(batches)} ({len(batch)} issues)")
        kept = await _dedup_batch_with_llm(llm, batch)
        kept_issues.extend(kept)

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
    Deduplicate issues found across different batches in Stage 1.
    If two issues have very similar reasons (>0.75 similarity), keep only one.
    """
    if not issues:
        return []
        
    deduped = []
    for issue in issues:
        issue_data = issue.model_dump() if hasattr(issue, 'model_dump') else issue
        reason = issue_data.get('reason') or issue_data.get('description') or ''
        
        is_duplicate = False
        for existing in deduped:
            existing_data = existing.model_dump() if hasattr(existing, 'model_dump') else existing
            existing_reason = existing_data.get('reason') or existing_data.get('description') or ''
            
            if is_semantically_similar(reason, existing_reason, threshold=0.75):
                logger.info(f"Cross-batch dedup: Suppressing duplicate issue: {reason[:50]}...")
                is_duplicate = True
                break
                
        if not is_duplicate:
            deduped.append(issue)
            
    return deduped

async def reconcile_previous_issues(
    request,
    new_issues: List[CodeReviewIssue],
    processed_diff = None
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
    for prev_issue in request.previousCodeAnalysisIssues:
        if hasattr(prev_issue, 'model_dump'):
            prev_data = prev_issue.model_dump()
        else:
            prev_data = prev_issue if isinstance(prev_issue, dict) else vars(prev_issue)
        issue_id = prev_data.get('id')
        if issue_id:
            prev_issues_by_id[str(issue_id)] = prev_data
    
    reconciled_issues = []
    processed_prev_ids = set()  # Track which previous issues we've handled
    
    # Process new issues from LLM - merge with previous issue data if they reference same ID
    for new_issue in new_issues:
        new_data = new_issue.model_dump() if hasattr(new_issue, 'model_dump') else new_issue
        issue_id = new_data.get('id')
        
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
            
            # Check if the previous issue was already resolved (manually or by auto-reconciliation)
            prev_was_resolved = prev_data.get('status', '').lower() == 'resolved'
            
            # Check if LLM marked it resolved
            llm_says_resolved = new_data.get('isResolved', False)
            
            # NEVER reopen a previously resolved issue during PR analysis.
            # Resolved issues (whether manual false-positive dismissals or auto-reconciled fixes)
            # should only be reopened explicitly by a user, not by LLM re-analysis.
            if prev_was_resolved and not llm_says_resolved:
                logger.info(f"Preventing reopen of previously resolved issue {issue_id} — LLM said isResolved=false but original status was 'resolved'")
            
            is_resolved = prev_was_resolved or llm_says_resolved
            
            # Determine resolution metadata
            if is_resolved and prev_was_resolved:
                # Preserve original resolution metadata
                resolution_explanation = prev_data.get('resolutionExplanation') or prev_data.get('resolvedDescription') or (new_data.get('resolutionReason') or new_data.get('reason') if llm_says_resolved else None)
                resolved_commit = prev_data.get('resolvedInCommit') or prev_data.get('resolvedByCommit') or (current_commit if llm_says_resolved else None)
            elif is_resolved:
                # Newly resolved by LLM
                resolution_explanation = new_data.get('resolutionReason') or new_data.get('reason') or 'Resolved in PR review iteration'
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
            # Use the LLM's line if it provided a meaningful one (> 1),
            # otherwise fall back to previous data, then default to 1.
            if new_line is not None and int(new_line) > 1:
                merged_line = new_line
            elif prev_line is not None and int(prev_line) > 0:
                merged_line = prev_line
            else:
                merged_line = new_line or prev_line or 1

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
        
        # Preserve the original resolved status — do NOT reopen manually resolved issues
        # (e.g., false positives marked resolved by users should stay resolved)
        original_status = prev_data.get('status', '').lower()
        was_resolved = original_status == 'resolved'
        
        # Preserve all original issue data
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
            isResolved=was_resolved,
            resolutionExplanation=prev_data.get('resolutionExplanation') or prev_data.get('resolvedDescription') if was_resolved else None,
            resolvedInCommit=prev_data.get('resolvedInCommit') or prev_data.get('resolvedByCommit') if was_resolved else None,
            visibility=prev_data.get('visibility'),
            codeSnippet=prev_data.get('codeSnippet') or ''
        )
        reconciled_issues.append(persisting_issue)
        if was_resolved:
            logger.info(f"Preserving resolved status for issue {issue_id} (not reopening manually resolved/false-positive)")
    
    resolved_kept = sum(1 for i in reconciled_issues if (hasattr(i, 'isResolved') and i.isResolved) or (isinstance(i, dict) and i.get('isResolved')))
    logger.info(f"Reconciliation complete: {len(reconciled_issues)} total issues ({resolved_kept} preserved as resolved)")
    return reconciled_issues
