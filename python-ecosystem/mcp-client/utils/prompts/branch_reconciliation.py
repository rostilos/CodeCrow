"""
Branch reconciliation prompt templates.
"""
from typing import Any, Dict, List, Optional
import json

from .constants import (
    ISSUE_CATEGORIES,
    SUGGESTED_FIX_DIFF_FORMAT,
    LOST_IN_MIDDLE_INSTRUCTIONS,
)
from .helpers import build_legacy_rag_section, build_context_section


def build_branch_review_prompt_with_branch_issues_data(
    branch_metadata: Dict[str, Any], 
    rag_context: Dict[str, Any] = None,
    structured_context: Optional[str] = None
) -> str:
    """
    Build prompt for branch reconciliation - checking which issues are fixed 
    after merging a PR to the branch.
    
    Args:
        branch_metadata: Branch metadata including branchIssues
        rag_context: Optional RAG context
        structured_context: Optional pre-structured context
        
    Returns:
        Formatted prompt string
    """
    print("Building branch review prompt with branch issues data")
    workspace = branch_metadata.get("workspace", "<unknown_workspace>")
    repo = branch_metadata.get("repoSlug", "<unknown_repo>")
    branch_name = branch_metadata.get("branchName", "<unknown_branch>")
    commit_hash = branch_metadata.get("commitHash", "<unknown_commit>")
    
    branch_issues: List[Dict[str, Any]] = branch_metadata.get("branchIssues", [])
    branch_issues_json = json.dumps(branch_issues, indent=2, default=str)
    
    context_section = build_context_section(structured_context, rag_context)

    prompt = f"""You are an expert code reviewer performing a **branch reconciliation** task.
Workspace: {workspace}
Repository slug: {repo}
Branch: {branch_name}
Commit: {commit_hash}

## MCP Tool Parameters
When calling MCP tools (getBranchFileContent, etc.), use these EXACT values:
- workspace: "{workspace}"
- repoSlug: "{repo}"
- branchName: "{branch_name}"
- commitHash: "{commit_hash}"

{context_section}IMPORTANT CONTEXT:
A PR has been merged to this branch. Your task is to:
1. Retrieve current file contents from the branch (using getBranchFileContent)
2. Check each issue below to see if it has been **resolved** by the merged changes
3. Report which issues are now resolved and which remain unresolved

--- CURRENT BRANCH ISSUES ---
{branch_issues_json}
--- END OF BRANCH ISSUES ---

{LOST_IN_MIDDLE_INSTRUCTIONS}

REVIEW INSTRUCTIONS:
For EACH issue in the list above, you MUST:
1. Use getBranchFileContent to get the current content of the file mentioned in the issue
2. Navigate to the specific line number mentioned
3. Check if the issue still exists OR has been fixed
4. Include the issue in your response with "isResolved": true if fixed, "isResolved": false if still present

{ISSUE_CATEGORIES}

IMPORTANT LINE NUMBER INSTRUCTIONS:
- The "line" field should contain the NEW line number in the current version of the file
- If the code was moved or the file was restructured, update the line number accordingly
- If the issue is resolved (code no longer exists), use the original line or "0"

{SUGGESTED_FIX_DIFF_FORMAT}

CRITICAL: Your final response must be ONLY a valid JSON object in this exact format:
{{
  "comment": "Summary of which issues were resolved and which remain",
  "issues": [
    {{
      "id": "original-issue-id-if-available",
      "severity": "HIGH|MEDIUM|LOW",
      "category": "SECURITY|PERFORMANCE|CODE_QUALITY|BUG_RISK|STYLE|DOCUMENTATION|BEST_PRACTICES|ERROR_HANDLING|TESTING|ARCHITECTURE",
      "file": "file-path",
      "line": "current-line-number",
      "reason": "Original issue description",
      "suggestedFixDescription": "Description of how to fix (if still present)",
      "suggestedFixDiff": "Unified diff showing fix (if still present)",
      "isResolved": true|false
    }}
  ]
}}

IMPORTANT RULES:
- You MUST include ALL issues from the input list in your response
- Set "isResolved": true only if the code fix is confirmed in the current file content
- Set "isResolved": false if the issue still exists or if you cannot confirm it's fixed
- If the file no longer exists, mark as resolved
- Do NOT invent new issues - only report on the issues provided

If no issues were provided or all issues are resolved:
{{
  "comment": "All issues have been resolved",
  "issues": []
}}

Do NOT include any markdown formatting, explanatory text, or other content - only the JSON object.
"""
    return prompt
