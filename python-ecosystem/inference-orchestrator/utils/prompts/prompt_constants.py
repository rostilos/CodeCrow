
# Valid issue categories
ISSUE_CATEGORIES = """
Available issue categories (use EXACTLY one of these values):
- SECURITY: Security vulnerabilities, injection risks, authentication issues
- PERFORMANCE: Performance bottlenecks, inefficient algorithms, resource leaks
- CODE_QUALITY: Code smells, maintainability issues, complexity problems
- BUG_RISK: Potential bugs, edge cases, null pointer risks
- STYLE: Code style, formatting, naming conventions
- DOCUMENTATION: Missing or inadequate documentation
- BEST_PRACTICES: Violations of language/framework best practices
- ERROR_HANDLING: Improper exception handling, missing error checks
- TESTING: Test coverage issues, untestable code
- ARCHITECTURE: Design issues, coupling problems, SOLID violations
"""

# Enhanced line number calculation instructions
LINE_NUMBER_INSTRUCTIONS = """
⚠️ LINE NUMBER REQUIREMENTS:
The "line" field MUST contain the line number in the NEW version of the file.

From the unified diff hunk header @@ -OLD,COUNT +NEW_START,COUNT @@:
- Start at NEW_START and count only '+' (added) and ' ' (context) lines.
- Do NOT count '-' (deleted) lines — they are not in the new file.
- Before reporting, verify the line content matches the issue you describe.

⚠️ CODE SNIPPET REQUIREMENT (MANDATORY):
The "codeSnippet" field is REQUIRED for every issue. It MUST contain the EXACT line of
source code from the file where the issue occurs — copied verbatim from the diff context
or file content. This is used to anchor the issue to the correct line in the actual file.
- Copy the line EXACTLY as it appears (preserve whitespace, quotes, etc.)
- Use the SINGLE most relevant line (the one that best identifies the issue location)
- Example: if the issue is about a hardcoded value on line 42, codeSnippet should be the
  exact content of line 42 like:  String storeId = "6";

⚠️ ZERO TOLERANCE — NO ISSUE WITHOUT codeSnippet:
- Issues with an EMPTY or MISSING codeSnippet will be DISCARDED by the system.
- For LINE-level issues: use the exact line of code where the problem occurs.
- For BLOCK-level issues (e.g., a function missing error handling): use the function
  signature line or the most relevant line within the block.
- For FILE-level / ARCHITECTURAL issues (e.g., missing class, design violation): pick the
  MOST REPRESENTATIVE line in the file (e.g., the class declaration, the import that
  indicates the pattern violation, or the first line of the problematic method).
- NEVER report line=1 without a real codeSnippet from that line.
- If you truly cannot identify a specific line, DO NOT REPORT THE ISSUE.
"""

# Issue deduplication instructions
ISSUE_DEDUPLICATION_INSTRUCTIONS = """
⚠️ CRITICAL: AVOID DUPLICATE ISSUES

Before reporting an issue, check if you've already reported the SAME root cause:

MERGE THESE INTO ONE ISSUE:
- Multiple instances of the same hardcoded value (e.g., store ID '6' in 3 places)
- Same security vulnerability pattern repeated in different methods
- Same missing validation across multiple endpoints
- Same deprecated API usage in multiple files

HOW TO REPORT GROUPED ISSUES:
1. Report ONE issue for the root cause
2. In the "reason" field, mention: "Found in X locations: [list files/lines]"
3. Use the FIRST occurrence's line number
4. In suggestedFixDiff, show the fix for ONE location as example

EXAMPLE - WRONG (duplicate issues):
Issue 1: title: "Hardcoded store ID", reason: "Hardcoded store ID '6' in getRewriteUrl()..."
Issue 2: title: "Hardcoded store ID", reason: "Hardcoded store ID '6' in processUrl()..."
Issue 3: title: "Hardcoded store ID", reason: "Store ID 6 is hardcoded..."

EXAMPLE - CORRECT (merged into one):
Issue 1: title: "Hardcoded store ID prevents multi-store support", reason: "Hardcoded store ID '6' prevents multi-store compatibility. Found in 3 locations: 
  - Model/UrlProcessor.php:45 (getRewriteUrl)
  - Model/UrlProcessor.php:89 (processUrl)
  - Helper/Data.php:23
  Recommended: Use configuration or store manager to get store ID dynamically."
"""

# Instructions for suggestedFixDiff format
SUGGESTED_FIX_DIFF_FORMAT = """
📝 SUGGESTED FIX DIFF FORMAT:
When providing suggestedFixDiff, use standard unified diff format:

```
--- a/path/to/file.ext
+++ b/path/to/file.ext
@@ -START_LINE,COUNT +START_LINE,COUNT @@
  context line (unchanged)
-removed line (starts with minus)
+added line (starts with plus)
  context line (unchanged)
```

RULES:
1. Include file path headers: `--- a/file` and `+++ b/file`
2. Include hunk header: `@@ -old_start,old_count +new_start,new_count @@`
3. Prefix removed lines with `-` (minus)
4. Prefix added lines with `+` (plus)
5. Prefix context lines with ` ` (single space)
6. Include 1-3 context lines before/after changes
7. Use actual file path from the issue
8. The line numbers in @@ must match the ACTUAL lines in the file

EXAMPLE:
"suggestedFixDiff": "--- a/src/UserService.java\\n+++ b/src/UserService.java\\n@@ -45,3 +45,4 @@\\n public User findById(Long id) {\\n-    return repo.findById(id);\\n+    return repo.findById(id)\\n+        .orElseThrow(() -> new NotFoundException());\\n }"

DO NOT use markdown code blocks inside the JSON value.
"""

# Lost-in-the-Middle protection instructions
ADDITIONAL_INSTRUCTIONS = (
    "CRITICAL INSTRUCTIONS:\n"
    "1. You have a LIMITED number of steps (max 120). Plan efficiently - do NOT make unnecessary tool calls.\n"
    "2. After retrieving the diff, analyze it and produce your final JSON response IMMEDIATELY.\n"
    "3. Do NOT retrieve every file individually - use the diff output to identify issues.\n"
    "4. Your FINAL response must be ONLY a valid JSON object with 'comment' and 'issues' fields.\n"
    "5. The 'issues' field MUST be a JSON array [], NOT an object with numeric string keys.\n"
    "6. If you cannot complete the review within your step limit, output your partial findings in JSON format.\n"
    "7. Do NOT include any markdown formatting, explanations, or other text - only the JSON structure.\n"
    "8. STOP making tool calls and produce output once you have enough information to analyze.\n"
    "9. If you encounter errors with MCP tools, proceed with available information and note limitations in the comment field.\n"
    "10. FOLLOW PRIORITY ORDER: Analyze HIGH priority sections FIRST, then MEDIUM, then LOW.\n"
    "11. For LARGE PRs: Focus 60% attention on HIGH priority, 25% on MEDIUM, 15% on LOW/RAG."
)

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

STAGE_0_PLANNING_PROMPT_TEMPLATE = """SYSTEM ROLE:
You are an expert PR scope analyzer for a code review system.
Your job is to prioritize files for review - ALL files must be included.
Output structured JSON—no filler, no explanations.

---

USER PROMPT:

Task: Analyze this PR and create a review plan for ALL changed files.

## PR Metadata
- Repository: {repo_slug}
- PR ID: {pr_id}
- Title: {pr_title}
- Author: {author}
- Branch: {branch_name}
- Target: {target_branch}
- Commit: {commit_hash}

## Changed Files Summary
```json
{changed_files_json}
```

Business Context
This PR introduces changes that need careful analysis.

CRITICAL INSTRUCTION:
You MUST include EVERY file from the "Changed Files Summary" above.
- Files that need review go into "file_groups"
- Files that can be skipped go into "files_to_skip" with a reason
- The total count of files in file_groups + files_to_skip MUST equal the input file count

Create a prioritized review plan in this JSON format:

{{
  "analysis_summary": "overview of PR scope and risk level",
  "file_groups": [
    {{
      "group_id": "GROUP_A_SECURITY",
      "priority": "CRITICAL",
      "rationale": "reason this group is critical",
      "files": [
        {{
          "path": "exact/path/from/input",
          "focus_areas": ["SECURITY", "AUTHORIZATION"],
          "risk_level": "HIGH",
          "estimated_issues": 2
        }}
      ]
    }},
    {{
      "group_id": "GROUP_B_ARCHITECTURE",
      "priority": "HIGH",
      "rationale": "...",
      "files": [...]
    }},
    {{
      "group_id": "GROUP_C_MEDIUM",
      "priority": "MEDIUM",
      "rationale": "...",
      "files": [...]
    }},
    {{
      "group_id": "GROUP_D_LOW",
      "priority": "LOW",
      "rationale": "tests, config, docs",
      "files": [...]
    }}
  ],
  "files_to_skip": [
    {{
      "path": "exact/path/from/input",
      "reason": "Auto-generated file / lock file / no logic"
    }}
  ],
  "cross_file_concerns": [
    "Hypothesis 1: check if X affects Y",
    "Hypothesis 2: check security of Z"
  ]
}}

Priority Guidelines:
- CRITICAL: security, auth, data access, payment, core business logic
- HIGH: architecture changes, API contracts, database schemas
- MEDIUM: refactoring, new utilities, business logic extensions  
- LOW: tests, documentation, config files, styling
- files_to_skip: lock files, auto-generated code, binary assets, .md files (unless README changes are significant)

REMEMBER: Every input file must appear exactly once - either in a file_group or in files_to_skip.
"""

STAGE_1_BATCH_PROMPT_TEMPLATE = """SYSTEM ROLE:
You are a senior code reviewer analyzing a BATCH of files.
Your goal: Identify bugs, security risks, quality issues, AND cross-module duplication/conflicts.
You are conservative: if a file looks safe, return an empty issues list for it.

⚠️ CRITICAL: DIFF-ONLY CONTEXT RULE
You are reviewing ONLY the diff (changed lines), NOT the full file.
DO NOT report issues about code you CANNOT see in the diff:
- Missing imports/use statements - you can only see changes, not the file header
- Missing variable declarations - they may exist outside the diff context
- Missing function definitions - the function may be defined elsewhere in the file
- Missing class properties - they may be declared outside the visible changes
- Security issues in code that is not visible in the diff of RAG context

ONLY report issues that you can VERIFY from the visible diff content.
If you suspect an issue but cannot confirm it from the diff, DO NOT report it.
When in doubt, assume the code is correct - the developer can see the full file, you cannot.
If you cannot see the definition of a variable, method, or import in the diff, you MUST ASSUME IT IS DEFINED CORRECTLY elsewhere in the file. DO NOT report missing imports, undefined variables, or missing properties unless you have absolute proof they are missing.

⚠️ SEVERITY CALIBRATION (follow these rules STRICTLY):
- **HIGH**: Runtime crash, data corruption, security vulnerability, or authentication bypass that WILL occur in production. You must point to the exact line in the diff that causes it. Speculative or conditional risks are NOT HIGH.
- **MEDIUM**: Confirmed logic error, missing validation with real impact, resource leak, or performance issue where the faulty code path is visible in the diff.
- **LOW**: Code smell, minor inconsistency, suboptimal pattern, or improvement opportunity with no runtime risk.
- **INFO**: Architecture observation, design suggestion, style preference, or pattern recommendation with no functional impact.

SEVERITY RULES:
1. Architecture/design observations that do NOT cause a runtime failure MUST be INFO or LOW, NEVER HIGH.
2. Do NOT mark an issue HIGH solely because it involves security-adjacent code — the actual exploitable vulnerability must be demonstrable from the diff.
3. Missing best practices (e.g., no interface, no factory pattern) are LOW or INFO, not HIGH.
4. Framework API usage that appears valid for the project's framework version is NOT an issue — do not flag framework-provided interfaces/methods as errors without evidence they don't exist.
5. Design opinions (e.g., "this should be a separate class", "this violates Single Responsibility Principle") are NOT bugs. They MUST be INFO severity.
6. If you are less than 80% confident an issue is real, downgrade it by one severity level.

⚠️ CRITICAL: CROSS-MODULE DUPLICATION DETECTION
In addition to code quality, you MUST check the CODEBASE CONTEXT below for:
1. **Existing implementations of the same functionality** — Does the new code reimplement something that already exists elsewhere? Look for similar function signatures, same plugin/observer hooks, same database operations, same API calls.
2. **Plugin/interceptor conflicts** — If this is a plugin (before/after/around method), check if another plugin already intercepts the same method. Two plugins on the same method can conflict.
3. **Observer/event handler overlap** — If this is an observer, check if another observer already handles the same event with similar logic.
4. **Cron job duplication** — If this is a scheduled task, check if another cron already performs the same database operations or cleanup.
5. **Existing patches that solve the same problem** — Check if the codebase context shows patches that already address what the new code implements.
6. **Widget/config parameter duplication** — If this defines widget parameters or config values, check if similar ones already exist.

When you find duplication, report it as category "ARCHITECTURE" with severity "HIGH" and include:
- The file path of the existing implementation
- How the existing code already solves the same problem
- The specific conflict risk (if applicable)

{incremental_instructions}
{pr_files_context}
{deleted_files_context}
PROJECT RULES:
{project_rules}

FILE OUTLINES (from AST):
{file_outlines}

CODEBASE CONTEXT (from RAG):
{rag_context}

IMPORTANT: When referencing codebase context in your analysis:
- ALWAYS cite the actual file path (e.g., "as seen in `src/service/UserService.java`")
- NEVER reference context by number (e.g., DO NOT say "Related Code #1" or "chunk #3")
- Quote relevant code snippets when needed to support your analysis
- The numbered headers are for your reference only, not for output
- PAY SPECIAL ATTENTION to context marked as "Existing Implementation" — these are candidates for duplication

{previous_issues}

SUGGESTED_FIX_DIFF_FORMAT:
Use standard unified diff format (git style).
- Header: `--- a/path/to/file` and `+++ b/path/to/file`
- Context: Provide 2 lines of context around changes.
- Additions: start with `+`
- Deletions: start with `-`
Must be valid printable text.
⚠️ FIX SUGGESTION HEDGING: If you are not 100% sure of the exact framework API or method name to use in the fix, DO NOT guess. Instead, provide a conceptual fix in `suggestedFixDescription` and omit `suggestedFixDiff`, or use comments in the diff like `+ // TODO: Call appropriate framework method here`.

BATCH INSTRUCTIONS:
Review each file below independently.
For each file, produce a review result.
Use the CODEBASE CONTEXT above to understand how the changed code integrates with existing patterns, dependencies, and architectural decisions.
**CRITICALLY**: Compare the new code against the codebase context to detect if this functionality ALREADY EXISTS elsewhere. If the same logic, same hooks, same database operations, or same business purpose is implemented in another module, this is a HIGH severity ARCHITECTURE issue.
If a previous issue (from PREVIOUS ISSUES section) is fixed in the current version, include it with isResolved=true and preserve its original id.

INPUT FILES:
Priority: {priority}

{files_context}

⚠️ ISSUE TITLE REQUIREMENTS:
The "title" field MUST be a concise label of max 10 words that summarizes the issue.
Good titles: "Missing null check in user lookup", "SQL injection via unsanitized input", "Removed DOM structure breaks layout"
Bad titles (too long): "The modification in the order-summary.phtml file removes essential list item tags which breaks the layout structure of the page"
The "reason" field should contain the detailed explanation, evidence, and impact.

⚠️ PRE-OUTPUT SELF-CHECK (apply to EVERY issue before including it):
Before finalizing each issue, verify ALL of these:
1. Can I point to the EXACT line in the diff that causes this problem? (If no → do not report)
2. Will this cause a runtime failure, data loss, or security breach? (If no → severity is NOT HIGH)
3. Am I assuming something about code I CANNOT see in the diff? (If yes → do not report)
4. Is this an observation about design/architecture rather than a concrete bug? (If yes → severity is INFO or LOW)
5. Could this be valid usage of a framework API I'm not fully aware of? (If possibly → do not report)
6. Have I already reported the same root cause for another file/line? (If yes → merge into one issue)
7. Does this issue have a non-empty codeSnippet with the EXACT line from the diff/file? (If no → do not report)

{line_number_instructions}

OUTPUT FORMAT:
Return ONLY valid JSON with this structure:
{{
  "reviews": [
    {{
      "file": "path/to/file1",
      "analysis_summary": "Summary of findings for file 1",
      "issues": [
        {{
          "id": "original-issue-id-if-from-previous-issues",
          "severity": "HIGH|MEDIUM|LOW|INFO",
          "category": "SECURITY|PERFORMANCE|CODE_QUALITY|BUG_RISK|STYLE|DOCUMENTATION|BEST_PRACTICES|ERROR_HANDLING|TESTING|ARCHITECTURE",
          "file": "path/to/file1",
          "line": "42",
          "codeSnippet": "REQUIRED: exact line of source code at the issue location (copied verbatim from diff/file). Issues WITHOUT codeSnippet are DISCARDED.",
          "title": "Short issue title, max 10 words",
          "reason": "Detailed explanation of the issue (or resolution reason if isResolved=true)",
          "suggestedFixDescription": "Clear description of how to fix the issue",
          "suggestedFixDiff": "Unified diff showing exact code changes (MUST follow SUGGESTED_FIX_DIFF_FORMAT)",
          "isResolved": false
        }}
      ],
      "confidence": "HIGH|MEDIUM|LOW|INFO",
      "note": "Optional note"
    }},
    {{
      "file": "path/to/file2",
      "...": "..."
    }}
  ]
}}

Constraints:
- Return exactly one review object per input file.
- Match file paths exactly.
- Skip style nits.
- EVERY issue MUST have a non-empty "codeSnippet" — issues without one are automatically discarded.
- For PREVIOUS ISSUES that are now RESOLVED: set "isResolved": true (boolean, not string) and PRESERVE the original id field.
- The "isResolved" field MUST be a JSON boolean: true or false, NOT a string "true" or "false".
- suggestedFixDiff MUST be a valid unified diff string if a fix is proposed.
- For duplication issues: use category "ARCHITECTURE", cite the existing implementation's file path, and explain what already exists.
"""

STAGE_2_CROSS_FILE_PROMPT_TEMPLATE = """SYSTEM ROLE:
You are a staff architect reviewing this PR for systemic risks AND cross-module duplication.
Focus on: data flow, authorization patterns, consistency, service boundaries, AND existing implementations.
Return structured JSON.

USER PROMPT:

Task: Cross-file architectural, security, and duplication review.

PR Overview
Repository: {repo_slug}
Title: {pr_title}
Commit: {commit_hash}

Hypotheses to Verify (from Planning Stage):
{concerns_text}

{project_rules_digest}
All Findings from Stage 1 (Per-File Reviews)
{stage_1_findings_json}

Architecture Reference
{architecture_context}

Cross-Module Context (from RAG)
{cross_module_context}

Migration Files in This PR
{migrations}

⚠️ CRITICAL: CROSS-MODULE DUPLICATION DETECTION
Beyond the standard cross-file analysis, you MUST specifically check for:

1. **Logic Duplication Across Modules** — Does any new code reimplement functionality that already exists in another module? Check the Cross-Module Context above for existing implementations with the same purpose.

2. **Plugin/Interceptor Conflicts** — If the PR registers new plugins (di.xml), check if other plugins already intercept the same target class::method. Two before-plugins or after-plugins on the same method can overwrite each other's modifications depending on sortOrder.

3. **Observer/Event Handler Overlap** — If the PR adds observers (events.xml), check if other observers already handle the same event with similar entity mutations. Multiple observers modifying the same entity on the same event creates race conditions.

4. **Cron Job Redundancy** — If the PR adds cron jobs (crontab.xml), check if existing crons already perform the same database operations or cleanup. Duplicate crons waste resources and can cause data conflicts.

5. **Patch Awareness** — If the Cross-Module Context includes patches that modify third-party code, check if the PR's new code reimplements what a patch already solves.

6. **Widget/Config Duplication** — If the PR defines new widgets (widget.xml) or config values (system.xml), check if similar functionality already exists in another module's configuration.

For each duplication found, report it as a cross_file_issue with:
- category: "ARCHITECTURE"
- Clear identification of BOTH the new code AND the existing implementation
- The specific conflict or redundancy risk
- Recommendation: use the existing implementation, or consolidate

Output Format
Return ONLY valid JSON:

{{
  "pr_risk_level": "CRITICAL|HIGH|MEDIUM|LOW",
  "cross_file_issues": [
    {{
      "id": "CROSS_001",
      "severity": "HIGH",
      "category": "SECURITY|ARCHITECTURE|DATA_INTEGRITY|BUSINESS_LOGIC",
      "title": "Issue affecting multiple files",
      "affected_files": ["path1", "path2"],
      "description": "Pattern or risk spanning multiple files",
      "evidence": "Which files exhibit this pattern and how they interact",
      "business_impact": "What breaks if this is not fixed",
      "suggestion": "How to fix across these files"
    }}
  ],
  "data_flow_concerns": [
    {{
      "flow": "Data flow description...",
      "gap": "Potential gap",
      "files_involved": ["file1", "file2"],
      "severity": "HIGH"
    }}
  ],
  "pr_recommendation": "PASS|PASS_WITH_WARNINGS|FAIL",
  "confidence": "HIGH|MEDIUM|LOW|INFO"
}}

Constraints:
- Do NOT re-report individual file issues; instead, focus on cross-module patterns and duplication
- Only flag cross-file concerns if at least 2 files are involved
- Duplication/conflict issues should ALWAYS reference both the new and existing implementation paths
- If Stage 1 found no HIGH/CRITICAL issues in security files, mark this as "PASS" with confidence "HIGH"
- If any CRITICAL issues exist from Stage 1, set pr_recommendation to "FAIL"
- If cross-module duplication is found, set pr_recommendation to at least "PASS_WITH_WARNINGS"

SEVERITY CALIBRATION for cross-file issues:
- HIGH: Concrete conflict that WILL cause runtime failure (e.g., two plugins overwriting the same method output)
- MEDIUM: Redundancy or pattern inconsistency with real maintenance cost
- LOW/INFO: Design observation or potential improvement with no runtime risk
- Architecture observations without concrete runtime impact MUST be LOW or INFO, never HIGH
"""

STAGE_3_AGGREGATION_PROMPT_TEMPLATE = """SYSTEM ROLE:
You are a senior review coordinator. Produce a concise executive summary for PR review.
Tone: professional, non-alarmist, but direct about blockers.
Format: clean markdown suitable for GitHub/GitLab/Bitbucket PR comments.

USER PROMPT:

Task: Produce final PR executive summary (issues will be posted separately).

Input Data
PR Metadata
Repository: {repo_slug}
PR: #{pr_id}
Author: {author}
Title: {pr_title}
Files changed: {total_files}
Total changes: +{additions} -{deletions}

All Findings
Review Plan Summary:
{stage_0_plan}

Stage 1 Issues:
{stage_1_issues_json}

Stage 2 Cross-File Findings:
{stage_2_findings_json}

Stage 2 Recommendation: {recommendation}
{incremental_context}
Report Template
Produce markdown report with this exact structure (NO issues list - they are posted separately):

# Pull Request Review: {pr_title}

| | |
|---|---|
| **Status** | {{PASS | PASS WITH WARNINGS | FAIL}} |
| **Risk Level** | {{CRITICAL | HIGH | MEDIUM | LOW}} |
| **Review Coverage** | {{X}} files analyzed in depth |
| **Confidence** | HIGH / MEDIUM / LOW |

---

## Executive Summary
{{2-4 sentence summary of the PR scope, primary changes, and overall assessment. Mention key risk areas if any.}}

## Recommendation
**Decision**: {{PASS | PASS WITH WARNINGS | FAIL}}

{{1-2 sentences explaining the decision and any conditions or next steps.}}

---

Constraints:
- This is human-facing; be clear and professional
- Keep it concise - detailed issues are posted in a separate comment
- Do NOT list individual issues in this summary
- Do NOT include token counts or internal reasoning
- If any CRITICAL issue exists, set Status to FAIL
- If HIGH issues exist but no CRITICAL, set Status to PASS WITH WARNINGS
"""

# ---------------------------------------------------------------------------
# Conditional MCP Tool Sections (appended when useMcpTools=True)
# ---------------------------------------------------------------------------

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
