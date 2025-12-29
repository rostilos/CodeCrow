"""
Shared constants and instruction templates for prompts.
"""

# Define valid issue categories
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

EXAMPLE:
"suggestedFixDiff": "--- a/src/UserService.java\\n+++ b/src/UserService.java\\n@@ -45,3 +45,4 @@\\n public User findById(Long id) {\\n-    return repo.findById(id);\\n+    return repo.findById(id)\\n+        .orElseThrow(() -> new NotFoundException());\\n }"

DO NOT use markdown code blocks inside the JSON value.
"""

# Lost-in-the-Middle protection instructions
LOST_IN_MIDDLE_INSTRUCTIONS = """
⚠️ CRITICAL: LOST-IN-THE-MIDDLE PROTECTION ACTIVE

The context below is STRUCTURED BY PRIORITY. Follow this analysis order STRICTLY:

📋 ANALYSIS PRIORITY ORDER (MANDATORY):
1️⃣ HIGH PRIORITY (60% attention): Core business logic, security, auth - analyze FIRST
2️⃣ MEDIUM PRIORITY (25% attention): Dependencies, shared utils, models
3️⃣ LOW PRIORITY (10% attention): Tests, configs - quick scan only
4️⃣ RAG CONTEXT (5% attention): Additional context from codebase

🎯 FOCUS HIERARCHY:
- Security issues > Architecture problems > Performance > Code quality > Style
- Business impact > Technical details
- Root cause > Symptoms

🛡️ BLOCK PR IMMEDIATELY IF FOUND:
- SQL Injection / XSS / Command Injection
- Hardcoded secrets/API keys
- Authentication bypass
- Remote Code Execution possibilities
"""

# Standard JSON response format for issues
JSON_RESPONSE_FORMAT = """
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
      "suggestedFixDiff": "Unified diff showing exact code changes",
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
"""

# Efficiency instructions for AI agent
EFFICIENCY_INSTRUCTIONS = """
EFFICIENCY INSTRUCTIONS (YOU HAVE LIMITED STEPS - MAX 120):
1. First, retrieve the PR diff using getPullRequestDiff tool
2. Analyze the diff content directly - do NOT fetch each file individually unless absolutely necessary
3. After analysis, produce your JSON response IMMEDIATELY
4. Do NOT make redundant tool calls - each tool call uses one of your limited steps

You MUST:
1. Retrieve diff using getPullRequestDiff MCP tool (this gives you all changes)
2. Analyze the diff to identify issues
3. STOP making tool calls and produce your final JSON response
4. Assign a category from the list above to EVERY issue

DO NOT:
1. Fetch files one by one when the diff already shows the changes
2. Make more than 10-15 tool calls total
3. Continue making tool calls indefinitely

CRITICAL INSTRUCTION FOR LARGE PRs:
Report ALL issues found. Do not group them or omit them for brevity. If you find many issues, report ALL of them. The user wants a comprehensive list, no matter how long the output is.
"""

# Line number instructions
LINE_NUMBER_INSTRUCTIONS = """
IMPORTANT LINE NUMBER INSTRUCTIONS:
The "line" field MUST contain the line number in the NEW version of the file (after changes).
When reading unified diff format, use the line number from the '+' side of hunk headers: @@ -old_start,old_count +NEW_START,new_count @@
Calculate the actual line number by: NEW_START + offset within the hunk (counting only context and added lines, not removed lines).
For added lines (+), count from NEW_START. For context lines (no prefix), also count from NEW_START.
If you retrieve the full source file content, use the line number as it appears in that file.
"""

# Analysis focus priorities
ANALYSIS_FOCUS = """
🎯 ANALYSIS FOCUS (in order of importance):
1. SECURITY: SQL injection, XSS, auth bypass, hardcoded secrets
2. ARCHITECTURE: Design issues, breaking changes, SOLID violations
3. PERFORMANCE: N+1 queries, memory leaks, inefficient algorithms
4. BUG_RISK: Edge cases, null checks, type mismatches
5. CODE_QUALITY: Maintainability, complexity, code smells
"""
