"""
Conditional MCP tool prompt sections (appended when useMcpTools=True).
"""

STAGE_1_MCP_TOOL_SECTION = """
## Available VCS Tools (Context Gap Filling)
If the diff and RAG context are INSUFFICIENT to understand the code changes,
you may call the following tool to read related files from the target branch:

- **getBranchFileContent(branch, filePath)** — Read a file's full content from the repository.

RULES:
1. You have a MAXIMUM of {max_calls} tool calls for this batch.
2. Use tools ONLY when context is truly missing (e.g., an interface definition, a parent class, a config file referenced in the diff).
3. Do NOT call tools for files already present in the diff or RAG context above.
4. After tool calls, continue your review with the enriched context.

TARGET BRANCH: {target_branch}
"""

STAGE_3_MCP_VERIFICATION_SECTION = """
## Issue Re-verification (Optional)
Before producing the final report, you may verify HIGH/CRITICAL issues that seem uncertain
by reading actual file content from the repository.

Available tools:
- **getBranchFileContent(branch, filePath)** — Read a file to verify an issue's existence
- **getPullRequestComments(pullRequestId)** — Read PR comments for additional context

RULES:
1. You have a MAXIMUM of {max_calls} verification calls total.
2. Only verify issues you are UNCERTAIN about — do not verify every issue.
3. Focus on HIGH and CRITICAL severity issues.
4. If verification reveals a false positive, note its ID for dismissal.
5. After verification, produce the final executive summary.

TARGET BRANCH: {target_branch}
PR ID: {pr_id}

## False Positive Dismissal
After producing the executive summary markdown, if your verification revealed any false
positives, append an HTML comment at the very end of your response with the IDs of issues
that should be removed from the issue list:

<!-- DISMISSED_ISSUES: ["ISSUE_ID_1", "ISSUE_ID_2"] -->

RULES for dismissal:
- Only dismiss issues you VERIFIED as false positives via tool calls (read the actual code).
- Do NOT dismiss issues based on guessing — you must have read the relevant file.
- Architecture observations reported as HIGH severity bugs can be dismissed if they have no runtime impact.
- If no issues should be dismissed, omit the DISMISSED_ISSUES comment entirely.
"""
