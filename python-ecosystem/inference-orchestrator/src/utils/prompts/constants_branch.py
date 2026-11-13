"""Prompt templates for existing branch issue reconciliation."""

_RECONCILIATION_RULES = """
CRITICAL INSTRUCTIONS FOR BRANCH RECONCILIATION:
Check each original finding against the CURRENT source and relevant changes.
Read the full available file and related evidence, not just the old line number.
Return one decision for EVERY supplied issue; do not discover new issues.

- isResolved: true means current evidence proves the ORIGINAL defect was fixed.
- isResolved: false means that same defect still exists. Explain its current
  trigger and return filePath, lineNumber, endLineNumber and codeSnippet.
- isResolved: null means evidence is missing or inconclusive. Explain why.

Use the original issue's id as issueId. resolutionReason must explain the fix,
current defect evidence, or uncertainty. A changed or removed anchor alone is
not proof of a fix: the same faulty behavior can survive elsewhere. A different
new defect does not keep the original finding open. Similar descriptions alone
are not proof that two findings are duplicates; assess each original claim.

For still-present HEAD findings, return a complete verbatim contiguous excerpt
from current source. Its 1-based lineNumber/endLineNumber must cover exactly
codeSnippet. Never use blank lines, ellipses, diff markers, old source or unrelated
surviving lines as an anchor. For BASE findings, assess the removal's effects on
surviving source without inventing a HEAD anchor. An unavailable file is not
proof of deletion or resolution. Keep uncertain issues explicitly unverified.

Return ONLY valid JSON, for example:
{{"comment":"summary","issues":[{{"issueId":"original id","isResolved":false,"resolutionReason":"current evidence for the original defect","filePath":"src/example.py","lineNumber":12,"endLineNumber":12,"codeSnippet":"exact current source"}}]}}
Anchor fields may be null for fixed or uncertain findings. Never omit an issue.
"""

BRANCH_REVIEW_PROMPT_TEMPLATE = """You are performing a branch reconciliation review.
Workspace: {workspace}
Repository slug: {repo}
Commit Hash: {commit_hash}
Branch: {branch}

For getBranchFileContent use workspace="{workspace}", repoSlug="{repo}".
Group issues by file and fetch each file once at the supplied commit.
""" + _RECONCILIATION_RULES + """
--- PREVIOUS ANALYSIS ISSUES ---
{previous_issues_json}
--- END OF PREVIOUS ISSUES ---
"""

BRANCH_RECONCILIATION_DIRECT_PROMPT_TEMPLATE = """You are performing a branch reconciliation review.
Branch: {branch}
Commit Hash: {commit_hash}
""" + _RECONCILIATION_RULES + """
--- FILE CONTENTS ---
{file_contents_block}
--- END OF FILE CONTENTS ---

{recent_changes_block}

--- PREVIOUS ANALYSIS ISSUES ---
{previous_issues_json}
--- END OF PREVIOUS ISSUES ---
"""
