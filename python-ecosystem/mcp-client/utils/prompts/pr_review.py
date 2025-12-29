"""
Pull Request review prompt templates.
"""
from typing import Any, Dict, List, Optional
import json

from .constants import (
    ISSUE_CATEGORIES,
    SUGGESTED_FIX_DIFF_FORMAT,
    LOST_IN_MIDDLE_INSTRUCTIONS,
    EFFICIENCY_INSTRUCTIONS,
    LINE_NUMBER_INSTRUCTIONS,
    ANALYSIS_FOCUS,
)
from .helpers import build_legacy_rag_section, build_context_section


def build_first_review_prompt(
    pr_metadata: Dict[str, Any], 
    rag_context: Dict[str, Any] = None,
    structured_context: Optional[str] = None
) -> str:
    """
    Build prompt for the first review of a pull request.
    
    Args:
        pr_metadata: PR metadata including workspace, repoSlug, pullRequestId
        rag_context: Optional RAG context with relevant code snippets
        structured_context: Optional pre-structured context
        
    Returns:
        Formatted prompt string
    """
    print("Building first review prompt")
    workspace = pr_metadata.get("workspace", "<unknown_workspace>")
    repo = pr_metadata.get("repoSlug", "<unknown_repo>")
    pr_id = pr_metadata.get("pullRequestId", pr_metadata.get("prId", "<unknown_pr>"))

    context_section = build_context_section(structured_context, rag_context)

    prompt = f"""You are an expert code reviewer with 15+ years of experience in security, architecture, and code quality.
Workspace: {workspace}
Repository slug: {repo}
Pull Request: {pr_id}

## MCP Tool Parameters
When calling MCP tools (getPullRequestDiff, getPullRequest, etc.), use these EXACT values:
- workspace: "{workspace}" (owner/organization name only - NOT the full repo path)
- repoSlug: "{repo}"
- pullRequestId: "{pr_id}"

{context_section}Perform a PRIORITIZED code review:

{ANALYSIS_FOCUS}

{ISSUE_CATEGORIES}

{EFFICIENCY_INSTRUCTIONS}

{LINE_NUMBER_INSTRUCTIONS}

{SUGGESTED_FIX_DIFF_FORMAT}

CRITICAL: Your final response must be ONLY a valid JSON object in this exact format:
{{
  "comment": "Brief summary of the overall code review findings",
  "issues": [
    {{
      "severity": "HIGH|MEDIUM|LOW",
      "category": "SECURITY|PERFORMANCE|CODE_QUALITY|BUG_RISK|STYLE|DOCUMENTATION|BEST_PRACTICES|ERROR_HANDLING|TESTING|ARCHITECTURE",
      "file": "file-path",
      "line": "line-number-in-new-file",
      "reason": "Detailed explanation of the issue",
      "suggestedFixDescription": "Clear description of how to fix the issue",
      "suggestedFixDiff": "Unified diff showing exact code changes (MUST follow SUGGESTED_FIX_DIFF_FORMAT above)",
      "isResolved": false
    }}
  ]
}}

IMPORTANT SCHEMA RULES:
- The "issues" field MUST be a JSON array [], NOT an object with numeric keys
- Do NOT include any "id" field in issues - it will be assigned by the system
- Each issue MUST have: severity, category, file, line, reason, isResolved
- REQUIRED FOR ALL ISSUES: Include "suggestedFixDescription" AND "suggestedFixDiff" with actual code fix in unified diff format
- The suggestedFixDiff must show the exact code change to fix the issue - this is MANDATORY, not optional

If no issues are found, return:
{{
  "comment": "Code review completed successfully with no issues found",
  "issues": []
}}

Use the reportGenerator MCP tool if available to help structure this response. Do NOT include any markdown formatting, explanatory text, or other content - only the JSON object.
"""
    return prompt


def build_review_prompt_with_previous_analysis_data(
    pr_metadata: Dict[str, Any], 
    rag_context: Dict[str, Any] = None,
    structured_context: Optional[str] = None
) -> str:
    """
    Build prompt for reviewing a PR with previous analysis data.
    Used when re-reviewing a PR after code changes.
    
    Args:
        pr_metadata: PR metadata including previousCodeAnalysisIssues
        rag_context: Optional RAG context
        structured_context: Optional pre-structured context
        
    Returns:
        Formatted prompt string
    """
    print("Building review prompt with previous analysis data")
    workspace = pr_metadata.get("workspace", "<unknown_workspace>")
    repo = pr_metadata.get("repoSlug", "<unknown_repo>")
    pr_id = pr_metadata.get("pullRequestId", pr_metadata.get("prId", "<unknown_pr>"))
    
    previous_issues: List[Dict[str, Any]] = pr_metadata.get("previousCodeAnalysisIssues", [])
    previous_issues_json = json.dumps(previous_issues, indent=2, default=str)

    context_section = build_context_section(structured_context, rag_context)

    prompt = f"""You are an expert code reviewer with 15+ years of experience performing a review on a subsequent version of a pull request.
Workspace: {workspace}
Repository slug: {repo}
Pull Request: {pr_id}

## MCP Tool Parameters
When calling MCP tools (getPullRequestDiff, getPullRequest, etc.), use these EXACT values:
- workspace: "{workspace}" (owner/organization name only - NOT the full repo path)
- repoSlug: "{repo}"
- pullRequestId: "{pr_id}"

{context_section}CRITICAL INSTRUCTIONS FOR RECURRING REVIEW:
1. The **Previous Analysis Issues** are provided below. Use this information to determine if any of these issues have been **resolved in the current diff**.
2. If a previously reported issue is **fixed** in the new code, Report it again with the status "resolved".
3. If a previously reported issue **persists** (i.e., the relevant code wasn't changed or the fix was incomplete), you **MUST** report it again in the current review's 'issues' list.
4. Always review the **entire current diff** for **new** issues as well.

--- PREVIOUS ANALYSIS ISSUES ---
{previous_issues_json}
--- END OF PREVIOUS ISSUES ---

Perform a code review considering:
1. Code quality and best practices
2. Potential bugs and edge cases
3. Performance and maintainability
4. Security issues
5. Suggest concrete fixes in the form of DIFF Patch if applicable, and put it in suggested fix

{ISSUE_CATEGORIES}

{EFFICIENCY_INSTRUCTIONS}

{LINE_NUMBER_INSTRUCTIONS}

{SUGGESTED_FIX_DIFF_FORMAT}

CRITICAL: Your final response must be ONLY a valid JSON object in this exact format:
{{
  "comment": "Brief summary of the overall code review findings",
  "issues": [
    {{
      "severity": "HIGH|MEDIUM|LOW",
      "category": "SECURITY|PERFORMANCE|CODE_QUALITY|BUG_RISK|STYLE|DOCUMENTATION|BEST_PRACTICES|ERROR_HANDLING|TESTING|ARCHITECTURE",
      "file": "file-path",
      "line": "line-number-in-new-file",
      "reason": "Detailed explanation of the issue",
      "suggestedFixDescription": "Clear description of how to fix the issue",
      "suggestedFixDiff": "Unified diff showing exact code changes (MUST follow SUGGESTED_FIX_DIFF_FORMAT above)",
      "isResolved": false
    }}
  ]
}}

IMPORTANT SCHEMA RULES:
- The "issues" field MUST be a JSON array [], NOT an object with numeric keys
- Do NOT include any "id" field in issues - it will be assigned by the system
- Each issue MUST have: severity, category, file, line, reason, isResolved
- REQUIRED FOR ALL ISSUES: Include "suggestedFixDescription" AND "suggestedFixDiff" with actual code fix in unified diff format
- The suggestedFixDiff must show the exact code change to fix the issue - this is MANDATORY, not optional

If no issues are found, return:
{{
  "comment": "Code review completed successfully with no issues found",
  "issues": []
}}

If token limit exceeded, STOP IMMEDIATELY AND return:
{{
  "comment": "The code review process was not completed successfully due to exceeding the allowable number of tokens (fileDiff).",
  "issues": [
    {{
      "severity": "LOW",
      "category": "CODE_QUALITY",
      "file": "",
      "line": "0",
      "reason": "The code review process was not completed successfully due to exceeding the allowable number of tokens (fileDiff).",
      "suggestedFixDescription": "Increase the allowed number of tokens or choose a model with a larger context."
    }}
  ]
}}

Use the reportGenerator MCP tool if available to help structure this response. Do NOT include any markdown formatting, explanatory text, or other content - only the JSON object.
"""
    return prompt


def build_direct_first_review_prompt(
    pr_metadata: Dict[str, Any],
    diff_content: str,
    rag_context: Dict[str, Any] = None,
    structured_context: Optional[str] = None
) -> str:
    """
    Build prompt for review with embedded diff - first review.
    
    The diff is already embedded in the prompt.
    Agent still has access to other MCP tools (getFile, getComments, etc.)
    but should NOT call getPullRequestDiff.
    
    Args:
        pr_metadata: PR metadata
        diff_content: The PR diff content to embed
        rag_context: Optional RAG context
        structured_context: Optional pre-structured context
        
    Returns:
        Formatted prompt string
    """
    workspace = pr_metadata.get("workspace", "<unknown_workspace>")
    repo = pr_metadata.get("repoSlug", "<unknown_repo>")
    pr_id = pr_metadata.get("pullRequestId", pr_metadata.get("prId", "<unknown_pr>"))
    
    context_section = build_context_section(structured_context, rag_context)

    prompt = f"""You are an expert code reviewer with 15+ years of experience in security, architecture, and code quality.
Workspace: {workspace}
Repository slug: {repo}
Pull Request: {pr_id}

{context_section}

=== PR DIFF (ALREADY PROVIDED - DO NOT CALL getPullRequestDiff) ===
IMPORTANT: The diff is embedded below. Do NOT call getPullRequestDiff tool.
You may use other MCP tools (getBranchFileContent, getPullRequestComments, etc.) if needed.

{diff_content}

=== END OF DIFF ===

Perform a PRIORITIZED code review of the diff above:

{ANALYSIS_FOCUS}

{ISSUE_CATEGORIES}

IMPORTANT LINE NUMBER INSTRUCTIONS:
The "line" field MUST contain the line number in the NEW version of the file (after changes).
When reading unified diff format, use the line number from the '+' side of hunk headers: @@ -old_start,old_count +NEW_START,new_count @@
Calculate the actual line number by: NEW_START + offset within the hunk.

{SUGGESTED_FIX_DIFF_FORMAT}

CRITICAL: Report ALL issues found. Do not group them or omit them for brevity.

Your response must be ONLY a valid JSON object in this exact format:
{{
  "comment": "Brief summary of the overall code review findings",
  "issues": [
    {{
      "severity": "HIGH|MEDIUM|LOW",
      "category": "SECURITY|PERFORMANCE|CODE_QUALITY|BUG_RISK|STYLE|DOCUMENTATION|BEST_PRACTICES|ERROR_HANDLING|TESTING|ARCHITECTURE",
      "file": "file-path",
      "line": "line-number-in-new-file",
      "reason": "Detailed explanation of the issue",
      "suggestedFixDescription": "Clear description of how to fix the issue",
      "suggestedFixDiff": "Unified diff showing exact code changes (MUST follow SUGGESTED_FIX_DIFF_FORMAT above)",
      "isResolved": false
    }}
  ]
}}

IMPORTANT: REQUIRED FOR ALL ISSUES - Include "suggestedFixDescription" AND "suggestedFixDiff" with actual code fix in unified diff format.

If no issues are found, return:
{{
  "comment": "Code review completed successfully with no issues found",
  "issues": []
}}

Do NOT include any markdown formatting, explanatory text, or other content - only the JSON object.
"""
    return prompt


def build_direct_review_prompt_with_previous_analysis(
    pr_metadata: Dict[str, Any],
    diff_content: str,
    rag_context: Dict[str, Any] = None,
    structured_context: Optional[str] = None
) -> str:
    """
    Build prompt for direct review mode with previous analysis data.
    
    Args:
        pr_metadata: PR metadata including previousCodeAnalysisIssues
        diff_content: The PR diff content to embed
        rag_context: Optional RAG context
        structured_context: Optional pre-structured context
        
    Returns:
        Formatted prompt string
    """
    workspace = pr_metadata.get("workspace", "<unknown_workspace>")
    repo = pr_metadata.get("repoSlug", "<unknown_repo>")
    pr_id = pr_metadata.get("pullRequestId", pr_metadata.get("prId", "<unknown_pr>"))
    previous_issues: List[Dict[str, Any]] = pr_metadata.get("previousCodeAnalysisIssues", [])
    previous_issues_json = json.dumps(previous_issues, indent=2, default=str)
    
    context_section = build_context_section(structured_context, rag_context)

    prompt = f"""You are an expert code reviewer with 15+ years of experience in security, architecture, and code quality.
Workspace: {workspace}
Repository slug: {repo}
Pull Request: {pr_id}

{context_section}

=== PR DIFF (ALREADY PROVIDED - DO NOT CALL getPullRequestDiff) ===
IMPORTANT: The diff is embedded below. Do NOT call getPullRequestDiff tool.
You may use other MCP tools (getBranchFileContent, getPullRequestComments, etc.) if needed.

{diff_content}

=== END OF DIFF ===

=== PREVIOUS ANALYSIS ISSUES ===
The following issues were found in a previous review. Check if they are still present or have been resolved:
{previous_issues_json}
=== END OF PREVIOUS ISSUES ===

Perform a PRIORITIZED code review of the diff above:

🎯 TASKS:
1. Check if each previous issue is still present in the code
2. Mark resolved issues with "isResolved": true
3. Find NEW issues introduced in this PR version
4. Prioritize by security > architecture > performance > quality

{ISSUE_CATEGORIES}

IMPORTANT LINE NUMBER INSTRUCTIONS:
For existing issues, update line numbers if code moved.
For new issues, use line numbers from the NEW version of files.

{SUGGESTED_FIX_DIFF_FORMAT}

Your response must be ONLY a valid JSON object:
{{
  "comment": "Summary of changes since last review",
  "issues": [
    {{
      "severity": "HIGH|MEDIUM|LOW",
      "category": "SECURITY|PERFORMANCE|...",
      "file": "file-path",
      "line": "line-number-in-new-file",
      "reason": "Explanation",
      "suggestedFixDescription": "Clear description of how to fix the issue",
      "suggestedFixDiff": "Unified diff showing exact code changes (MUST follow SUGGESTED_FIX_DIFF_FORMAT above)",
      "isResolved": false
    }}
  ]
}}

IMPORTANT: REQUIRED FOR ALL ISSUES - Include "suggestedFixDescription" AND "suggestedFixDiff" with actual code fix in unified diff format.

Do NOT include any markdown formatting - only the JSON object.
"""
    return prompt
