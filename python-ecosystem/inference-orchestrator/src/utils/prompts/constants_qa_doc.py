"""
QA Auto-Documentation prompt constants.

Three template modes are supported:
- RAW:    LLM receives raw analysis data + task context and has full creative freedom.
- BASE:   LLM follows a structured template with functional areas, test scenarios, etc.
- CUSTOM: User-provided template with safe placeholder substitution (no f-string eval).

Multi-stage prompts (ULTRATHINKING pipeline):
- STAGE_1_BATCH: Per-batch analysis of diff hunks with file context
- STAGE_2_CROSS_IMPACT: Cross-file impact analysis for test surface expansion
- STAGE_3_AGGREGATION: Final consolidation into a polished QA document
- STAGE_3_DELTA: Delta update for same-PR re-runs
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
# {task_context}     — Rich task context block (from TaskContextBuilder)
# {acceptance_criteria} — Acceptance criteria parsed from task description
# {output_language}  — Language for the generated documentation (e.g. "English", "Ukrainian")
# ---------------------------------------------------------------------------


QA_DOC_SYSTEM_PROMPT = """You are an expert QA documentation writer for CodeCrow, an AI-powered code review platform.
Your audience is **manual QA testers** — people who test the application through the UI, not developers.

OUTPUT LANGUAGE: You MUST write the entire document in **{output_language}**. All headings, descriptions, steps, and notes must be in {output_language}.

RULES:
1. Write in plain, non-technical language that any QA tester can follow.
2. Describe what to test from a USER perspective — screens, buttons, forms, behaviors.
3. NEVER include file paths, class names, function names, SQL queries, or code snippets in the output.
4. NEVER use markdown tables (they break in Jira). Use bullet lists and numbered lists instead.
5. Group test scenarios by functional area (e.g. "Admin Panel", "Checkout Flow").
6. Every test scenario must have clear step-by-step instructions a tester can follow.
7. Assign priority (HIGH / MEDIUM / LOW) based on user-facing risk.
8. Focus on WHAT changed from the user's perspective, not HOW it was implemented.
9. Use markdown formatting: headings (#), bold (**), bullet lists (-), numbered lists (1.).
10. Keep it actionable — testers should be able to start testing immediately after reading.

STRICTLY FORBIDDEN (never include any of these in the output):
- File names or file paths (e.g. "src/main/...", "UserService.java", "index.tsx")
- Class names, function names, method names, variable names
- SQL queries, database table names, column names
- API endpoint URLs or HTTP methods (GET, POST, PUT, DELETE)
- CLI commands, shell scripts, terminal commands
- Configuration file references (.yml, .json, .env, .properties)
- Code snippets, code blocks, or inline code
- Database operations (INSERT, UPDATE, SELECT, migration names)
- Technical stack references (Spring Boot, React, PostgreSQL, Redis, etc.)
- Import statements, package names, module names

If the code changes are purely internal (refactoring, infrastructure, CI/CD), describe the USER-VISIBLE EFFECT or write "No visible changes for the user — verify existing functionality still works."
"""


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


# ---------------------------------------------------------------------------
# Single-pass prompts (fallback when multi-stage is skipped for tiny PRs)
# ---------------------------------------------------------------------------

QA_DOC_RAW_PROMPT = """Generate QA documentation for the following PR changes.
Your audience is manual QA testers who test through the UI — not developers.
Write the ENTIRE document in **{output_language}**.

PR #{pr_number} in {project_name}
Task: {task_key} — {task_summary}
Source Branch: {source_branch} → {target_branch}
Title: {pr_title}
Description: {pr_description}
Files analyzed: {files_analyzed}
Issues found: {issues_found}

{task_context}

Analysis summary:
{analysis_summary}

PR Diff (actual code changes):
```
{diff}
```

Generate comprehensive QA documentation in Markdown format.
IMPORTANT formatting rules:
- Use headings (#), bullet lists (-), numbered lists (1.) and bold (**) only.
- NEVER use markdown tables (| col | col |) — they break in Jira.
- NEVER mention file names, class names, function names, or code in the output.
- NEVER include SQL queries, API endpoints, CLI commands, database operations, or configuration references.
- Write everything from the USER's perspective — what screens, buttons, and behaviors to test.
- Every scenario must have numbered steps a manual tester can follow.
- Translate all technical changes into user-visible behaviors. If a change is purely internal, say "Verify existing functionality still works" instead of describing the code."""


QA_DOC_BASE_PROMPT = """Generate structured QA documentation for the following PR changes.
Your audience is **manual QA testers** who test through the application UI — not developers.
Write the ENTIRE document in **{output_language}**.

## Context
- **Project**: {project_name}
- **PR**: #{pr_number} — {pr_title}
- **Task**: {task_key} — {task_summary}
- **Branch**: {source_branch} → {target_branch}

### PR Description
{pr_description}

{task_context}

### Analysis Summary
{analysis_summary}

### PR Diff (actual code changes)
```
{diff}
```

## FORMATTING RULES (STRICT)
- Use ONLY: headings (#), bullet lists (-), numbered lists (1.), bold (**), italic (*)
- NEVER use markdown tables (| col | col |) — they break in Jira
- NEVER mention file names, class names, function names, SQL, or code in the output
- NEVER include API endpoints, CLI commands, database operations, config files, import statements
- Write EVERYTHING from the user/tester perspective — screens, buttons, forms, behaviors
- If a change is purely backend/internal, describe it as: "Verify [feature] still works correctly" — do NOT describe the code

## Required Documentation Structure

Analyze the PR diff above carefully and generate QA documentation following this structure:

### 1. Change Summary
2-3 sentences describing what changed from a user's perspective. No technical details.

### 2. Functional Areas Affected
Group changes by what the user sees (e.g., "Admin Panel — Widget Settings", "Checkout Page").
For each area, use bullet points:
- What the user will notice is different
- Expected behavior after the change

### 3. Test Scenarios
For each functional area, list test scenarios using this format:

**Scenario Name** (PRIORITY)
- **Preconditions:** What needs to be set up before testing
- **Steps:**
  1. Go to [screen/page]
  2. Click [button/link]
  3. ...
- **Expected Result:** What the tester should see/verify

### 4. Edge Cases and Negative Testing
Bullet list of boundary conditions and error scenarios to verify, written as user actions.

### 5. Regression Risks
Areas of the application that might be indirectly affected. Describe by feature/screen, not by code.

### 6. Environment and Setup Notes
Any special configuration, test data, or browser requirements needed for testing.

Generate the documentation now."""


QA_DOC_CUSTOM_PROMPT = """Generate QA documentation for the following PR changes using the provided custom template.
Your audience is **manual QA testers** — write for people who test through the UI, not developers.
Write the ENTIRE document in **{output_language}**.

## Context
- **Project**: {project_name}
- **PR**: #{pr_number} — {pr_title}
- **Task**: {task_key} — {task_summary}
- **Branch**: {source_branch} → {target_branch}

### PR Description
{pr_description}

{task_context}

### Analysis Summary
{analysis_summary}

### PR Diff (actual code changes)
```
{diff}
```

## FORMATTING RULES (STRICT)
- NEVER use markdown tables (| col | col |) — they break in Jira
- NEVER mention file names, class names, function names, or code in the output
- NEVER include SQL queries, API endpoints, CLI commands, database operations, or configuration references
- Use only: headings (#), bold (**), bullet lists (-), numbered lists (1.)

## Custom Template Instructions
Follow this user-provided template as closely as possible:

---
{custom_template}
---

Generate the QA documentation now, following the custom template above."""


# ---------------------------------------------------------------------------
# Multi-stage prompts
# ---------------------------------------------------------------------------

QA_STAGE_1_BATCH_PROMPT = """You are analyzing a batch of code changes for QA documentation. Your goal is to identify what a manual QA tester needs to test.
Write all user-facing text (functional_area, change_summary, test scenario names/descriptions/steps/expected_result, edge_cases, regression_risks) in **{output_language}**.

## Context
- **Project**: {project_name}
- **PR**: #{pr_number} — {pr_title}
- **Task**: {task_key} — {task_summary}
- **Branch**: {source_branch} → {target_branch}
- **Batch**: {batch_number} of {total_batches}

{task_context}

## Files in This Batch
{file_list}

## Diff for This Batch
```
{batch_diff}
```

## File Contents
{file_contents}

## Instructions

Analyze the code changes and translate them into user-facing test scenarios. Think like a QA tester, not a developer.

For each logical change in this batch:

1. **Functional area**: Which part of the application is affected? (e.g., "Admin Panel — Category Chooser", "Checkout — Shipping Form"). Use user-visible feature names, NOT file/module names.
2. **What changed for the user**: Describe the visible behavior change in plain language. No file names, no code references, no SQL, no API endpoints.
3. **Test scenarios**: Step-by-step instructions a manual tester can follow:
   - Where to navigate in the application
   - What to click, type, or select
   - What the expected result should be
   - Priority: HIGH (core functionality), MEDIUM (secondary features), LOW (cosmetic/edge)
4. **Edge cases**: Unusual inputs, error conditions, or boundary scenarios — described as user actions, NOT as code paths.

STRICTLY FORBIDDEN in user-facing text:
- File names, class names, function names, variable names
- SQL queries, database table/column names
- API endpoints, HTTP methods
- CLI commands, shell scripts
- Configuration file references
- Code snippets or inline code
- Technical stack names (Spring, React, PostgreSQL, etc.)

If a code change is purely internal (refactoring, renaming, infrastructure), describe it as: "Verify [feature] still works correctly after internal improvements."

IMPORTANT: The JSON output below is for internal processing only. The final document shown to testers will NOT contain file paths. However, include file_path in the JSON so the system can track which changes map to which areas.

Output as JSON:
```json
{{
  "batch_id": {batch_number},
  "file_analyses": [
    {{
      "file_path": "path/to/file",
      "functional_area": "User-facing area name (e.g. Admin — Widget Settings)",
      "change_summary": "Plain-language description of what the user will notice",
      "test_scenarios": [
        {{
          "name": "Short descriptive name",
          "description": "What this scenario verifies from the user perspective",
          "preconditions": "What needs to be set up (e.g. logged in as admin, product exists)",
          "steps": ["Navigate to Admin → Widgets", "Click Add Widget", "Select the category chooser"],
          "expected_result": "What the tester should see or verify",
          "priority": "HIGH|MEDIUM|LOW",
          "acceptance_criteria_ref": "AC item this maps to, or null"
        }}
      ],
      "edge_cases": ["What happens when no categories are selected", "Saving with invalid input"],
      "regression_risks": ["Existing widgets may behave differently after this change"]
    }}
  ]
}}
```"""


QA_STAGE_2_CROSS_IMPACT_PROMPT = """You are analyzing how changes across multiple parts of the application interact, to find additional test scenarios for manual QA testers.
Write all user-facing text (scenario names, descriptions, steps, expected results, risk descriptions) in **{output_language}**.

## Context
- **Project**: {project_name}
- **PR**: #{pr_number} — {pr_title}
- **Task**: {task_key} — {task_summary}
- **Branch**: {source_branch} → {target_branch}
- **Total files changed**: {total_files_changed}

{task_context}

## Per-Area Analysis Results (from previous step)
{stage_1_results}

## Dependency Relationships
{dependency_info}

## Changed Areas
{changed_files_list}

## Instructions

Based on the per-area analyses above, identify ADDITIONAL test scenarios that involve multiple parts of the application working together. Think about end-to-end user workflows.

Focus on:
1. **End-to-end workflows**: User journeys that pass through multiple changed areas (e.g., "Admin configures a widget → Customer sees it on the storefront")
2. **Integration scenarios**: When one change depends on another (e.g., a new setting in admin must be reflected on the frontend)
3. **Side effects**: Changes in one area that could unexpectedly affect another area
4. **Acceptance criteria gaps**: Any acceptance criteria from the task not yet covered

Do NOT repeat scenarios already identified in the per-area analysis. Focus ONLY on cross-area interactions.

STRICTLY FORBIDDEN in user-facing text:
- File names, class names, function names, variable names
- SQL queries, database table/column names
- API endpoints, HTTP methods
- CLI commands, configuration references
- Code snippets or technical stack names

Describe everything from the USER's perspective — no file names, no code references.

Output as JSON:
```json
{{
  "cross_file_scenarios": [
    {{
      "name": "End-to-end scenario name",
      "description": "What to test from the user perspective",
      "affected_areas": ["Admin — Widget Settings", "Storefront — Category Display"],
      "interaction_type": "WORKFLOW|CONFIGURATION|DISPLAY|DATA_FLOW",
      "steps": ["Go to Admin → Widgets", "Configure the widget", "Go to storefront", "Verify display"],
      "expected_result": "What the tester should see",
      "priority": "HIGH|MEDIUM|LOW",
      "risk_level": "Why this matters for the user",
      "acceptance_criteria_ref": "AC item or null"
    }}
  ],
  "cascading_risks": [
    {{
      "changed_area": "Area that was modified",
      "affected_area": "Area that might be impacted",
      "risk_description": "What the user might experience",
      "suggested_test": "How to verify it still works"
    }}
  ],
  "uncovered_acceptance_criteria": [
    "Any AC items not yet addressed by test scenarios"
  ]
}}
```"""


QA_STAGE_3_AGGREGATION_PROMPT = """You are producing the FINAL QA testing guide by consolidating all previous analysis into a document for **manual QA testers**.
Write the ENTIRE document in **{output_language}**.

## Context
- **Project**: {project_name}
- **PR**: #{pr_number} — {pr_title}
- **Task**: {task_key} — {task_summary}
- **Branch**: {source_branch} → {target_branch}

{task_context}

## Per-Area Analysis Results
{stage_1_results}

## Cross-Area Impact Analysis
{stage_2_results}

## Code Review Summary
{analysis_summary}

{previous_doc_section}

## FORMATTING RULES (CRITICAL — STRICT COMPLIANCE REQUIRED)

1. **NEVER use markdown tables** (| col | col |) — they break in Jira and render as raw text
2. **NEVER mention file names, class names, function names, SQL, or code** — testers don't need them
3. **NEVER include API endpoints, CLI commands, database operations, config files, import statements, or technical stack names**
4. Use ONLY these markdown elements:
   - Headings: # ## ###
   - Bold: **text**
   - Italic: *text*
   - Bullet lists: - item
   - Numbered lists: 1. item
   - Horizontal rules: ---
5. For test scenarios, use this exact bullet-list format (NOT a table):

**Scenario Name** (PRIORITY)
- **Preconditions:** What setup is needed
- **Steps:**
  1. Navigate to [page/screen]
  2. Perform [action]
  3. Verify [result]
- **Expected Result:** What the tester should observe

6. If a change is purely internal (refactoring, infrastructure, CI/CD), write: "Verify [feature] still works correctly" — do NOT describe the code change itself.

## Instructions

Produce a polished QA testing guide by:
1. **Consolidating** all test scenarios from both analysis stages
2. **Deduplicating** — merge overlapping scenarios
3. **Prioritizing** — HIGH priority scenarios first within each section
4. **Writing for testers** — every instruction should tell them WHERE to go and WHAT to do
5. **Stripping any technical references** that may have leaked from the analysis stages — replace them with user-facing descriptions

## Required Output Structure

# QA Testing Guide — {pr_title}

## 1. What Changed
2-3 sentences describing what's different from the user's perspective. No technical jargon.

## 2. Acceptance Criteria Coverage
For each acceptance criterion from the task:
- ✅ Covered by: [scenario name]
- ⚠️ Partially covered: [what's missing]
- ❌ Not addressed by this PR

(Skip this section entirely if no acceptance criteria were provided.)

## 3. Test Scenarios by Area

Group scenarios under the functional area heading (### Area Name).
List each scenario using the bullet-list format shown above.
Order: HIGH priority first, then MEDIUM, then LOW.

## 4. Edge Cases and Negative Testing
Bullet list of unusual conditions to verify:
- What happens when [unusual user action]
- What happens when [invalid input / empty state / error condition]

## 5. Regression Risks
Areas of the application that were NOT changed but might be affected:
- [Feature/screen] — why it might be impacted, how to verify

## 6. Setup and Environment Notes
Any special requirements:
- Test data needed
- Configuration or feature flags to enable
- Browser or device requirements

Generate the FINAL document now. Remember: NO TABLES, NO FILE NAMES, NO CODE, NO SQL, NO API ENDPOINTS, NO CLI COMMANDS."""


QA_STAGE_3_DELTA_PROMPT = """You are producing an UPDATED QA testing guide for a same-PR re-run (new commits were pushed).
Write the ENTIRE document in **{output_language}**.

## Context
- **Project**: {project_name}
- **PR**: #{pr_number} — {pr_title}
- **Task**: {task_key} — {task_summary}
- **Branch**: {source_branch} → {target_branch}
- **This is a RE-RUN**: New commits were pushed since the last QA doc was generated.

{task_context}

## New Changes Since Last Analysis
```
{delta_diff}
```

## Analysis of New Changes
{stage_1_results}

## Cross-Area Impact of New Changes
{stage_2_results}

## Previous QA Testing Guide
---
{previous_documentation}
---

## FORMATTING RULES (CRITICAL — STRICT COMPLIANCE REQUIRED)
1. **NEVER use markdown tables** — they break in Jira
2. **NEVER mention file names, class names, or code** — write for manual QA testers
3. **NEVER include SQL queries, API endpoints, CLI commands, database operations, or configuration references**
4. Use ONLY: headings (#), bold (**), bullet lists (-), numbered lists (1.), horizontal rules (---)
5. Use the bullet-list format for test scenarios (see previous guide for format)

## Instructions

Produce an UPDATED QA testing guide by:
1. **Starting from** the previous guide above
2. **Adding** new test scenarios from the latest changes
3. **Updating** existing scenarios affected by the new changes
4. **Removing** scenarios that are no longer relevant
5. **Marking** new/updated sections with *(updated in latest push)* so testers see what's new
6. **Preserving** all scenarios from the previous guide that are still valid
7. **Stripping any technical references** that may have leaked — replace with user-facing descriptions

The result must be a COMPLETE, standalone QA testing guide — not just the changes.

Generate the updated document now. Remember: NO TABLES, NO FILE NAMES, NO CODE, NO SQL, NO API ENDPOINTS, NO CLI COMMANDS."""


# ---------------------------------------------------------------------------
# Update preamble — injected when previous QA documentation already exists
# for the same task from earlier PRs. The LLM merges old + new into one doc.
# ---------------------------------------------------------------------------

QA_DOC_UPDATE_PREAMBLE = """
## ⚠️ IMPORTANT: This is an UPDATE to an existing QA testing guide

A previous QA testing guide already exists on this Jira task from earlier pull request(s).
Produce a **single, consolidated QA testing guide** that:

1. **Retains** all still-relevant test scenarios from the previous guide
2. **Adds** new test scenarios from the current PR (#{pr_number})
3. **Removes** scenarios that are no longer applicable
4. **Marks which PR introduced each scenario** where practical (e.g. *(PR #{pr_number})*)
5. Maintains a coherent, non-redundant document

Remember: NO tables, NO file names, NO code. Write for manual QA testers.

### Previous QA Testing Guide
---
{previous_documentation}
---

Now generate the UPDATED, consolidated guide incorporating both the previous content and the new PR changes below.
"""

# Previous doc injection for Stage 3 aggregation
QA_STAGE_3_PREVIOUS_DOC_SECTION = """
## ⚠️ Previous QA Testing Guide (from earlier PRs)
A previous testing guide exists for this task from other PRs. You must:
1. **Retain** all still-relevant test scenarios from the previous guide
2. **Merge** new scenarios from this PR into the appropriate sections
3. **Mark** which PR introduced each scenario where practical
4. Remove any scenarios that are no longer applicable

---
{previous_documentation}
---
"""

# Marker appended to every QA auto-doc comment so we can find/update it later.
# The `prs=` attribute tracks which PRs have been documented for multi-PR detection.
QA_DOC_COMMENT_FOOTER_TEMPLATE = """

---
*🐦 Generated by [CodeCrow](https://codecrow.app) QA Auto-Documentation*
<!-- codecrow-qa-autodoc:prs={pr_numbers} -->"""

# Legacy footer without PR tracking (used as a fallback search marker)
QA_DOC_COMMENT_FOOTER = """

---
*🐦 Generated by [CodeCrow](https://codecrow.app) QA Auto-Documentation*
<!-- codecrow-qa-autodoc -->"""
