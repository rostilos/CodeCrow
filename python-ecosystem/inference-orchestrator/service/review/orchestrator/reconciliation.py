"""
Issue reconciliation and deduplication logic for incremental reviews.
"""
import logging
import difflib
from typing import Any, Dict, List, Optional

from model.output_schemas import CodeReviewIssue

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
    """Compute a fingerprint for issue deduplication.
    
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
            reason = data.get('reason', data.get('description', 'No description'))
            pr_version = data.get('prVersion', '?')
            
            lines.append(f"[ID:{issue_id}] {severity} @ {file_path}:{line} (v{pr_version})")
            lines.append(f"  Issue: {reason}")
            lines.append("")
    
    if resolved_issues:
        lines.append("--- RESOLVED ISSUES (for context only, do NOT re-report) ---")
        for data in resolved_issues:
            issue_id = data.get('id', 'unknown')
            severity = data.get('severity', 'MEDIUM')
            file_path = data.get('file', data.get('filePath', 'unknown'))
            line = data.get('line', data.get('lineNumber', '?'))
            reason = data.get('reason', data.get('description', 'No description'))
            pr_version = data.get('prVersion', '?')
            resolved_desc = data.get('resolutionExplanation') or data.get('resolvedDescription', '')
            resolved_in = data.get('resolvedInPrVersion', '')
            
            lines.append(f"[ID:{issue_id}] {severity} @ {file_path}:{line} (v{pr_version}) - RESOLVED")
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
                resolution_explanation = prev_data.get('resolutionExplanation') or prev_data.get('resolvedDescription') or (new_data.get('reason') if llm_says_resolved else None)
                resolved_commit = prev_data.get('resolvedInCommit') or prev_data.get('resolvedByCommit') or (current_commit if llm_says_resolved else None)
            elif is_resolved:
                # Newly resolved by LLM
                resolution_explanation = new_data.get('reason')
                resolved_commit = current_commit
            else:
                resolution_explanation = None
                resolved_commit = None
            
            # PRESERVE original data, use LLM's reason as resolution explanation
            merged_issue = CodeReviewIssue(
                id=str(issue_id),
                severity=(prev_data.get('severity') or prev_data.get('issueSeverity') or 'MEDIUM').upper(),
                category=prev_data.get('category') or prev_data.get('issueCategory') or prev_data.get('type') or 'CODE_QUALITY',
                file=prev_data.get('file') or prev_data.get('filePath') or new_data.get('file', 'unknown'),
                line=str(prev_data.get('line') or prev_data.get('lineNumber') or new_data.get('line', '1')),
                # PRESERVE original reason and fix description
                reason=prev_data.get('reason') or prev_data.get('title') or prev_data.get('description') or '',
                suggestedFixDescription=prev_data.get('suggestedFixDescription') or prev_data.get('suggestedFix') or '',
                suggestedFixDiff=prev_data.get('suggestedFixDiff') or None,
                isResolved=is_resolved,
                resolutionExplanation=resolution_explanation,
                resolvedInCommit=resolved_commit,
                visibility=prev_data.get('visibility'),
                codeSnippet=prev_data.get('codeSnippet')
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
            reason=prev_data.get('reason') or prev_data.get('title') or prev_data.get('description') or '',
            suggestedFixDescription=prev_data.get('suggestedFixDescription') or prev_data.get('suggestedFix') or '',
            suggestedFixDiff=prev_data.get('suggestedFixDiff') or None,
            isResolved=was_resolved,
            resolutionExplanation=prev_data.get('resolutionExplanation') or prev_data.get('resolvedDescription') if was_resolved else None,
            resolvedInCommit=prev_data.get('resolvedInCommit') or prev_data.get('resolvedByCommit') if was_resolved else None,
            visibility=prev_data.get('visibility'),
            codeSnippet=prev_data.get('codeSnippet')
        )
        reconciled_issues.append(persisting_issue)
        if was_resolved:
            logger.info(f"Preserving resolved status for issue {issue_id} (not reopening manually resolved/false-positive)")
    
    resolved_kept = sum(1 for i in reconciled_issues if (hasattr(i, 'isResolved') and i.isResolved) or (isinstance(i, dict) and i.get('isResolved')))
    logger.info(f"Reconciliation complete: {len(reconciled_issues)} total issues ({resolved_kept} preserved as resolved)")
    return reconciled_issues
