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
⚠️ LINE NUMBER — Best-Effort Hint (the system verifies automatically):
The "line" field should contain your best estimate of the line number in the NEW version.
An approximate line number is acceptable — the system uses your codeSnippet to find the exact position.

⚠️⚠️⚠️ CODE SNIPPET — THE MOST CRITICAL FIELD ⚠️⚠️⚠️
The "codeSnippet" field is the PRIMARY mechanism the system uses to anchor issues to real source lines.
Your "line" number is just a hint — the system finds the EXACT line by matching your codeSnippet
against the actual file content. A wrong line number with a correct codeSnippet = issue placed correctly.
A correct line number with a wrong/missing codeSnippet = issue DISCARDED.

RULES (violation = issue DISCARDED):
1. Copy the EXACT line of code from the diff — character-for-character, preserving whitespace and quotes.
2. Choose the SINGLE most representative line that identifies the issue location.
3. For function-level issues: use the function SIGNATURE line
   (e.g., "    public void processPayment(Order order) {")
4. For block-level issues: use the FIRST line of the block
   (e.g., "        if (user == null) {")
5. For file-level / architectural issues: use the most representative line
   (e.g., class declaration, key import, first line of problematic method)
6. NEVER fabricate or modify a snippet — it must exist VERBATIM in the diff or file content.
7. Issues with EMPTY or MISSING codeSnippet will be AUTOMATICALLY DISCARDED.

GOOD EXAMPLES:
  ✓ codeSnippet: "    String storeId = \\"6\\";"   (exact line, preserves indentation)
  ✓ codeSnippet: "    public void processPayment(Order order) {"   (function signature)
  ✓ codeSnippet: "    } catch (Exception e) {"   (block opener for error-handling issue)
  ✓ codeSnippet: "import com.legacy.DeprecatedUtil;"   (key import for architecture issue)

BAD EXAMPLES:
  ✗ codeSnippet: ""   (empty — issue will be discarded)
  ✗ codeSnippet: "some code here"   (fabricated — won't match any real line)
  ✗ codeSnippet: "line 42: foo()"   (annotated — won't match)
  ✗ codeSnippet: "foo()"   (too short / ambiguous — may match wrong line)

⚠️ ISSUE SCOPE (REQUIRED — "scope" field):
Every issue MUST include a "scope" field indicating the granularity of the problem:
- "LINE"     — affects a single line (default). Example: hardcoded secret, typo, missing null check.
- "BLOCK"    — affects a contiguous block of lines (e.g. an if/else, loop body, try/catch block).
- "FUNCTION" — affects an entire function/method (e.g. missing error handling, too complex, wrong return type).
- "FILE"     — affects the whole file or is an architectural concern (e.g. missing class, circular dependency).

SCOPE SELECTION RULES:
- If the issue describes a problem with a function's behavior, return type, data flow, or contract → use "FUNCTION".
- If the issue is about class design, file structure, missing patterns, or architectural concerns → use "FILE".
- ONLY use "LINE" when you can point to ONE specific line that fully captures the issue.
- If you cannot provide a codeSnippet or cannot identify a specific line → NEVER use "LINE"; use "FUNCTION" or "FILE" instead.

The system automatically determines the start/end line boundaries for BLOCK and FUNCTION scopes
using your codeSnippet as the anchor point. Do NOT manually compute line ranges or "endLine".
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
