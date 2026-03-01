"""
Shared prompt fragments reused across multiple stages.
"""

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
Issue 1: title: "Hardcoded timeout value", reason: "Hardcoded timeout of 30s in sendRequest()..."
Issue 2: title: "Hardcoded timeout value", reason: "Hardcoded timeout of 30s in retryRequest()..."
Issue 3: title: "Hardcoded timeout value", reason: "Timeout 30s is hardcoded..."

EXAMPLE - CORRECT (merged into one):
Issue 1: title: "Hardcoded timeout prevents runtime configuration", reason: "Hardcoded timeout value '30' prevents environment-specific tuning. Found in 3 locations:
  - service/HttpClient:45 (sendRequest)
  - service/HttpClient:89 (retryRequest)
  - util/ConnectionHelper:23
  Recommended: Extract to configuration and inject as a dependency."
"""

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
"suggestedFixDiff": "--- a/src/service/user_service.ext\n+++ b/src/service/user_service.ext\n@@ -45,3 +45,4 @@\n findById(id) {\n-    return repo.findById(id);\n+    return repo.findById(id)\n+        .orElseThrow(NotFoundException());\n }"

DO NOT use markdown code blocks inside the JSON value.
"""

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
