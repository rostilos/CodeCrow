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

CODE_SNIPPET_AND_SCOPE_INSTRUCTIONS = """
LINE, CODE SNIPPET, AND SCOPE CONTRACT:
- "line" is a best-effort line number in the new version; the system re-anchors using codeSnippet.
- "codeSnippet" is mandatory. Copy one exact, verbatim source line from the visible diff/file context, preserving whitespace and quotes. Never fabricate, annotate, shorten, or paraphrase it.
- Choose the most representative anchor line: the faulty line for LINE, the first line of the block for BLOCK, the function signature for FUNCTION, or the most relevant declaration/changed line for FILE.
- Empty or non-matching codeSnippet values cause the issue to be discarded.
- "scope" is required and must be one of LINE, BLOCK, FUNCTION, FILE. Use LINE only when one line fully captures the problem; use BLOCK/FUNCTION/FILE for wider issues.
- Do not compute endLine or line ranges.
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
    "10. Do not infer risk solely from filename, extension, or directory labels; use the diff, metadata, task context, and retrieved code evidence."
)
