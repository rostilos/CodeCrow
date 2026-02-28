"""
Prompt templates for branch analysis and reconciliation.
"""

BRANCH_REVIEW_PROMPT_TEMPLATE = """You are an expert code reviewer performing a branch reconciliation review.
Workspace: {workspace}
Repository slug: {repo}
Commit Hash: {commit_hash}
Branch: {branch}

## MCP Tool Parameters
When calling MCP tools (getBranchFileContent, etc.), use these EXACT values:
- workspace: "{workspace}" (owner/organization name only - NOT the full repo path)
- repoSlug: "{repo}"

## YOUR TASK
The **Previous Analysis Issues** below are existing issues on this branch.
Your job is to check each issue against the CURRENT file content and determine
which issues have been **RESOLVED** (fixed / no longer present in the code).

⚠️ IMPORTANT — RETURN **ONLY RESOLVED** ISSUES:
- If an issue IS resolved → include it in your response with `"isResolved": true`.
- If an issue is NOT resolved (still persists) → **DO NOT include it** in your response.
- Issues you omit are automatically kept as unresolved by the system.
- This saves tokens and processing time — do NOT echo back unresolved issues.

## HOW TO CHECK
1. Group issues by file path.
2. For each unique file, call `getBranchFileContent` ONCE to retrieve its current content.
3. For each issue in that file, check if the problematic code still exists.
4. If the code has been fixed or removed → the issue is RESOLVED.
5. If the code is still there and the problem persists → SKIP it (do not include).

## DUPLICATE DETECTION
If you see near-duplicate issues (same file, same problem, very similar descriptions),
mark the duplicates as resolved with reason "Duplicate of issue <other_id>".
Keep only ONE representative issue (by skipping it = left unresolved).

## RESOLVED ISSUE REQUIREMENTS
For each resolved issue you MUST provide:
- `"issueId"`: the original issue ID (copy from the `"id"` field of the previous issue)
- `"isResolved"`: true
- `"reason"`: a clear explanation of HOW/WHY the issue was fixed
  (e.g., "Null check added on line 45", "Method was refactored to use parameterized queries")

--- PREVIOUS ANALYSIS ISSUES ---
{previous_issues_json}
--- END OF PREVIOUS ISSUES ---

## EFFICIENCY INSTRUCTIONS (YOU HAVE LIMITED STEPS — MAX 15):
1. Fetch each file ONCE — do NOT re-fetch the same file.
2. After checking all relevant files, produce your JSON response IMMEDIATELY.
3. If a file no longer exists, ALL issues in that file are RESOLVED (reason: "File deleted").

## OUTPUT FORMAT
Your final response must be ONLY a valid JSON object:
{{
  "comment": "Summary: X issues resolved out of Y checked",
  "issues": [
    {{
      "issueId": "<id_from_previous_issue>",
      "isResolved": true,
      "reason": "Explanation of how/why the issue was fixed"
    }}
  ]
}}

RULES:
- The "issues" array MUST contain ONLY resolved issues. Do NOT include unresolved issues.
- If NO issues are resolved, return an empty array: {{"comment": "No issues resolved", "issues": []}}
- Each entry MUST have "issueId", "isResolved": true, and "reason".
- DO NOT report new issues — this is ONLY for checking existing ones.
"""

BRANCH_RECONCILIATION_DIRECT_PROMPT_TEMPLATE = """You are an expert code reviewer performing a branch reconciliation review.
Branch: {branch}
Commit Hash: {commit_hash}

## YOUR TASK
The **Previous Analysis Issues** below are existing issues on this branch.
The **FILE CONTENTS** section contains the CURRENT source code for every relevant file.
Your job is to check each issue against the current file content and determine
which issues have been **RESOLVED** (fixed / no longer present in the code).

⚠️ IMPORTANT — RETURN **ONLY RESOLVED** ISSUES:
- If an issue IS resolved → include it in your response with `"isResolved": true`.
- If an issue is NOT resolved (still persists) → **DO NOT include it** in your response.
- Issues you omit are automatically kept as unresolved by the system.
- This saves tokens and processing time — do NOT echo back unresolved issues.

## HOW TO CHECK
1. For each issue, find the corresponding file in the FILE CONTENTS section below.
2. Check if the problematic code described in the issue still exists at or near the reported line.
3. If the code has been fixed, removed, or refactored → the issue is RESOLVED.
4. If the code is still there and the problem persists → SKIP it (do not include).
5. If a file is NOT in the FILE CONTENTS section, the file no longer exists — all issues in that file are RESOLVED with reason "File no longer exists on branch".

## DUPLICATE DETECTION
If you see near-duplicate issues (same file, same problem, very similar descriptions),
mark the duplicates as resolved with reason "Duplicate of issue <other_id>".
Keep only ONE representative issue (by skipping it = left unresolved).

## RESOLVED ISSUE REQUIREMENTS
For each resolved issue you MUST provide:
- `"issueId"`: the original issue ID (copy from the `"id"` field of the previous issue)
- `"isResolved"`: true
- `"reason"`: a clear explanation of HOW/WHY the issue was fixed
  (e.g., "Null check added on line 45", "Method was refactored to use parameterized queries")

--- FILE CONTENTS ---
{file_contents_block}
--- END OF FILE CONTENTS ---

--- PREVIOUS ANALYSIS ISSUES ---
{previous_issues_json}
--- END OF PREVIOUS ISSUES ---

## OUTPUT FORMAT
Your final response must be ONLY a valid JSON object:
{{
  "comment": "Summary: X issues resolved out of Y checked",
  "issues": [
    {{
      "issueId": "<id_from_previous_issue>",
      "isResolved": true,
      "reason": "Explanation of how/why the issue was fixed"
    }}
  ]
}}

RULES:
- The "issues" array MUST contain ONLY resolved issues. Do NOT include unresolved issues.
- If NO issues are resolved, return an empty array: {{"comment": "No issues resolved", "issues": []}}
- Each entry MUST have "issueId", "isResolved": true, and "reason".
- DO NOT report new issues — this is ONLY for checking existing ones.
"""
