"""
QA Auto-Documentation prompt constants.

Three template modes are supported:
- RAW:    LLM receives raw analysis data + task context and has full creative freedom.
- BASE:   LLM follows a structured template with functional areas, test scenarios, etc.
- CUSTOM: User-provided template with safe placeholder substitution (no f-string eval).
"""

# ---------------------------------------------------------------------------
# Placeholders for safe substitution (str.format / str.replace — never eval)
# ---------------------------------------------------------------------------
# {project_name}     — Repository / project display name
# {pr_number}        — Pull request number
# {task_key}         — Jira / task management ticket key (e.g. WS-111)
# {task_summary}     — Ticket summary / title
# {source_branch}    — Source branch name
# {target_branch}    — Target branch name
# {pr_title}         — PR title
# {pr_description}   — PR description (first 500 chars)
# {issues_found}     — Number of issues found by analysis
# {files_analyzed}   — Number of files analyzed
# {analysis_summary} — Aggregated analysis summary (issues grouped by file)
# {diff}             — Raw unified diff from the VCS platform (actual code changes)
# ---------------------------------------------------------------------------


QA_DOC_SYSTEM_PROMPT = """You are an expert QA documentation assistant for CodeCrow, an AI-powered code review platform.
Your role is to generate clear, actionable QA documentation that helps testers understand what changed
in a pull request and what needs to be tested.

RULES:
1. Be concise but thorough — testers should know EXACTLY what to test.
2. Focus on functional impact — what user-facing behavior changed?
3. Group changes by functional area when possible.
4. Highlight any breaking changes or risky areas.
5. Use markdown formatting for readability.
6. Carefully analyze the PR diff to understand what actually changed in the code.
7. If the changes are trivial (typos, formatting, config-only), say so clearly.
8. Always include a "Regression Risks" section.
9. Reference specific files and functions from the diff when describing what changed."""


QA_DOC_RELEVANCE_CHECK_PROMPT = """Analyze the following PR changes and determine if QA documentation is needed.

PR #{pr_number} in {project_name}
Source Branch: {source_branch}
Target Branch: {target_branch}
Title: {pr_title}
Files analyzed: {files_analyzed}
Issues found: {issues_found}

Analysis summary:
{analysis_summary}

PR Diff (actual code changes):
{diff}

Respond with ONLY "YES" or "NO".
- YES: Changes affect functionality, behavior, APIs, UI, or security — QA testing is needed.
- NO: Changes are purely cosmetic (comments, formatting, docs-only, CI config) — no testing needed.

Answer:"""


QA_DOC_RAW_PROMPT = """Generate QA documentation for the following PR changes.
You have full creative freedom in how you structure the documentation,
but it must be useful for QA testers.

PR #{pr_number} in {project_name}
Task: {task_key} — {task_summary}
Source Branch: {source_branch} → {target_branch}
Title: {pr_title}
Description: {pr_description}
Files analyzed: {files_analyzed}
Issues found: {issues_found}

Analysis summary:
{analysis_summary}

PR Diff (actual code changes):
```
{diff}
```

Generate comprehensive QA documentation in Markdown format.
Base your analysis primarily on the actual diff above — it shows exactly what code was added, modified, or removed."""


QA_DOC_BASE_PROMPT = """Generate structured QA documentation for the following PR changes.

## Context
- **Project**: {project_name}
- **PR**: #{pr_number} — {pr_title}
- **Task**: {task_key} — {task_summary}
- **Branch**: {source_branch} → {target_branch}
- **Files Analyzed**: {files_analyzed}
- **Issues Found**: {issues_found}

### PR Description
{pr_description}

### Analysis Summary
{analysis_summary}

### PR Diff (actual code changes)
```
{diff}
```

## Required Documentation Structure

Analyze the PR diff above carefully. This is the actual code that was changed — use it as your PRIMARY source of truth.

Generate the QA documentation following this EXACT structure:

### 1. Change Summary
A 2-3 sentence overview of what this PR does from a user/tester perspective.

### 2. Functional Areas Affected
Group changes by functional area (e.g., "User Authentication", "Payment Processing").
For each area:
- What changed
- Why it changed (if apparent)
- Expected behavior after the change

### 3. Test Scenarios
For each functional area, provide specific test scenarios:
- **Scenario name**: Brief description
- **Preconditions**: What needs to be set up
- **Steps**: Numbered steps to execute
- **Expected Result**: What should happen
- **Priority**: HIGH / MEDIUM / LOW

### 4. Edge Cases & Negative Testing
List edge cases and negative scenarios that should be tested.

### 5. Regression Risks
Areas that might be indirectly affected and should be regression-tested.

### 6. Environment / Configuration Notes
Any special setup, feature flags, or configuration needed for testing.

Generate the documentation now in Markdown format."""


QA_DOC_CUSTOM_PROMPT = """Generate QA documentation for the following PR changes using the provided custom template.

## Context
- **Project**: {project_name}
- **PR**: #{pr_number} — {pr_title}
- **Task**: {task_key} — {task_summary}
- **Branch**: {source_branch} → {target_branch}
- **Files Analyzed**: {files_analyzed}
- **Issues Found**: {issues_found}

### PR Description
{pr_description}

### Analysis Summary
{analysis_summary}

### PR Diff (actual code changes)
```
{diff}
```

## Custom Template Instructions
The user has provided the following template / instructions for how the QA documentation should be structured.
Follow these instructions as closely as possible:

---
{custom_template}
---

Generate the QA documentation now in Markdown format, following the custom template above."""


# Marker appended to every QA auto-doc comment so we can find/update it later
QA_DOC_COMMENT_FOOTER = """

---
*🐦 Generated by [CodeCrow](https://codecrow.dev) QA Auto-Documentation*
<!-- codecrow-qa-autodoc -->"""
