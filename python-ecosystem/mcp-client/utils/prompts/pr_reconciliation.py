"""
PR Reconciliation prompt templates.
Used for checking if a PR might fix existing issues on the target branch.
"""
from typing import Any, Dict, List, Optional
import json

from .constants import (
    ISSUE_CATEGORIES,
)
from .helpers import build_context_section


def build_pr_reconciliation_prompt(
    pr_metadata: Dict[str, Any],
    diff_content: str,
    existing_issues: List[Dict[str, Any]],
    rag_context: Dict[str, Any] = None,
    structured_context: Optional[str] = None
) -> str:
    """
    Build prompt for PR reconciliation - checking if a PR might fix existing issues.
    
    This is used when a PR is created/updated to check if the changes might
    resolve existing issues on the target branch (found in previous branch analyses
    or PR reviews).
    
    Args:
        pr_metadata: PR metadata including workspace, repoSlug, pullRequestId, targetBranch
        diff_content: The PR diff content
        existing_issues: List of existing issues from the target branch
        rag_context: Optional RAG context
        structured_context: Optional pre-structured context
        
    Returns:
        Formatted prompt string
    """
    workspace = pr_metadata.get("workspace", "<unknown_workspace>")
    repo = pr_metadata.get("repoSlug", "<unknown_repo>")
    pr_id = pr_metadata.get("pullRequestId", pr_metadata.get("prId", "<unknown_pr>"))
    source_branch = pr_metadata.get("sourceBranchName", "<unknown_source>")
    target_branch = pr_metadata.get("targetBranchName", "<unknown_target>")
    
    existing_issues_json = json.dumps(existing_issues, indent=2, default=str)
    
    context_section = build_context_section(structured_context, rag_context)

    prompt = f"""You are an expert code reviewer performing a **PR reconciliation** task.
Workspace: {workspace}
Repository slug: {repo}
Pull Request: {pr_id}
Source Branch: {source_branch}
Target Branch: {target_branch}

{context_section}

## TASK DESCRIPTION
This PR is being created/updated. Before analyzing for NEW issues, we need to check 
if this PR might **resolve existing issues** on the target branch.

Your task is to:
1. Review the PR diff provided below
2. Compare with the existing issues on the target branch
3. Identify which existing issues are **potentially resolved** by this PR
4. For each potentially resolved issue, explain why you believe it's fixed

## MCP Tool Parameters
When calling MCP tools (getBranchFileContent, etc.), use these EXACT values:
- workspace: "{workspace}"
- repoSlug: "{repo}"

=== PR DIFF (ALREADY PROVIDED) ===
{diff_content}
=== END OF DIFF ===

=== EXISTING ISSUES ON TARGET BRANCH ({target_branch}) ===
These issues were found in previous analyses of the target branch or merged PRs:
{existing_issues_json}
=== END OF EXISTING ISSUES ===

## ANALYSIS INSTRUCTIONS

For each existing issue, analyze:
1. Is the file mentioned in the issue modified in this PR?
2. Does the PR change the specific code/line related to the issue?
3. Does the change actually FIX the issue (not just modify the area)?

RESOLUTION CRITERIA:
- "POTENTIALLY_RESOLVED": The PR modifies the relevant code and the fix appears correct
- "LIKELY_RESOLVED": The PR clearly addresses the issue with a proper fix
- "NOT_RESOLVED": The issue is not addressed by this PR
- "NEEDS_VERIFICATION": The PR touches related code but the fix is uncertain

{ISSUE_CATEGORIES}

CRITICAL: Your response must be ONLY a valid JSON object:
{{
  "comment": "Summary of reconciliation analysis - how many issues might be resolved",
  "reconciled_issues": [
    {{
      "original_issue_id": "id-from-existing-issues",
      "file": "file-path",
      "line": "original-line-number",
      "severity": "HIGH|MEDIUM|LOW",
      "category": "SECURITY|PERFORMANCE|...",
      "original_reason": "Original issue description",
      "resolution_status": "POTENTIALLY_RESOLVED|LIKELY_RESOLVED|NOT_RESOLVED|NEEDS_VERIFICATION",
      "resolution_explanation": "Why this issue is/isn't resolved by this PR",
      "confidence": "HIGH|MEDIUM|LOW"
    }}
  ],
  "summary": {{
    "total_issues_checked": 0,
    "potentially_resolved": 0,
    "likely_resolved": 0,
    "not_resolved": 0,
    "needs_verification": 0
  }}
}}

IMPORTANT RULES:
- Include ALL existing issues in your response with their resolution status
- Only mark as LIKELY_RESOLVED if you're confident the fix is correct
- Use POTENTIALLY_RESOLVED when the area is modified but fix quality is uncertain
- Provide clear explanation for each resolution determination
- Do NOT report new issues - that's handled by PR_ANALYSIS separately

If no existing issues were provided:
{{
  "comment": "No existing issues to reconcile",
  "reconciled_issues": [],
  "summary": {{
    "total_issues_checked": 0,
    "potentially_resolved": 0,
    "likely_resolved": 0,
    "not_resolved": 0,
    "needs_verification": 0
  }}
}}

Do NOT include any markdown formatting - only the JSON object.
"""
    return prompt
