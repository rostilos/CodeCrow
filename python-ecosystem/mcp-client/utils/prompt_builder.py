from typing import Any, Dict, List, Optional
import json
from model.models import IssueDTO

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

class PromptBuilder:
    @staticmethod
    def build_first_review_prompt(
        pr_metadata: Dict[str, Any],
        rag_context: Dict[str, Any] = None,
        structured_context: Optional[str] = None
    ) -> str:
        print("Building first review prompt")
        workspace = pr_metadata.get("workspace", "<unknown_workspace>")
        repo = pr_metadata.get("repoSlug", "<unknown_repo>")
        pr_id = pr_metadata.get("pullRequestId", pr_metadata.get("prId", "<unknown_pr>"))

        # Build RAG context section (legacy format for backward compatibility)
        rag_section = ""
        if not structured_context and rag_context and rag_context.get("relevant_code"):
            rag_section = PromptBuilder._build_legacy_rag_section(rag_context)

        # Use structured context if provided (new Lost-in-Middle protected format)
        context_section = ""
        if structured_context:
            context_section = f"""
{LOST_IN_MIDDLE_INSTRUCTIONS}

{structured_context}
"""
        elif rag_section:
            context_section = rag_section

        prompt = f"""You are an expert code reviewer with 15+ years of experience in security, architecture, and code quality.
Workspace: {workspace}
Repository slug: {repo}
Pull Request: {pr_id}

## MCP Tool Parameters
When calling MCP tools (getPullRequestDiff, getPullRequest, etc.), use these EXACT values:
- workspace: "{workspace}" (owner/organization name only - NOT the full repo path)
- repoSlug: "{repo}"
- pullRequestId: "{pr_id}"

{context_section}Perform a PRIORITIZED code review:

🎯 ANALYSIS FOCUS (in order of importance):
1. SECURITY: SQL injection, XSS, auth bypass, hardcoded secrets
2. ARCHITECTURE: Design issues, breaking changes, SOLID violations
3. PERFORMANCE: N+1 queries, memory leaks, inefficient algorithms
4. BUG_RISK: Edge cases, null checks, type mismatches
5. CODE_QUALITY: Maintainability, complexity, code smells

{ISSUE_CATEGORIES}

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


IMPORTANT LINE NUMBER INSTRUCTIONS:
The "line" field MUST contain the line number in the NEW version of the file (after changes).
When reading unified diff format, use the line number from the '+' side of hunk headers: @@ -old_start,old_count +NEW_START,new_count @@
Calculate the actual line number by: NEW_START + offset within the hunk (counting only context and added lines, not removed lines).
For added lines (+), count from NEW_START. For context lines (no prefix), also count from NEW_START.
If you retrieve the full source file content, use the line number as it appears in that file.

{SUGGESTED_FIX_DIFF_FORMAT}

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
      "suggestedFixDiff": "Unified diff showing exact code changes (MUST follow SUGGESTED_FIX_DIFF_FORMAT above)",
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

Use the reportGenerator MCP tool if available to help structure this response. Do NOT include any markdown formatting, explanatory text, or other content - only the JSON object.
"""
        return prompt

    @staticmethod
    def build_review_prompt_with_previous_analysis_data(
        pr_metadata: Dict[str, Any],
        rag_context: Dict[str, Any] = None,
        structured_context: Optional[str] = None
    ) -> str:
        print("Building review prompt with previous analysis data")
        workspace = pr_metadata.get("workspace", "<unknown_workspace>")
        repo = pr_metadata.get("repoSlug", "<unknown_repo>")
        pr_id = pr_metadata.get("pullRequestId", pr_metadata.get("prId", "<unknown_pr>"))
        # 🆕 Get and format previous issues data
        previous_issues: List[Dict[str, Any]] = pr_metadata.get("previousCodeAnalysisIssues", [])

        # We need a clean JSON string of the previous issues to inject into the prompt
        previous_issues_json = json.dumps(previous_issues, indent=2, default=str)

        # Build RAG context section (legacy format for backward compatibility)
        rag_section = ""
        if not structured_context and rag_context and rag_context.get("relevant_code"):
            rag_section = PromptBuilder._build_legacy_rag_section(rag_context)

        # Use structured context if provided (new Lost-in-Middle protected format)
        context_section = ""
        if structured_context:
            context_section = f"""
{LOST_IN_MIDDLE_INSTRUCTIONS}

{structured_context}
"""
        elif rag_section:
            context_section = rag_section

        prompt = f"""You are an expert code reviewer with 15+ years of experience performing a review on a subsequent version of a pull request.
Workspace: {workspace}
Repository slug: {repo}
Pull Request: {pr_id}

## MCP Tool Parameters
When calling MCP tools (getPullRequestDiff, getPullRequest, etc.), use these EXACT values:
- workspace: "{workspace}" (owner/organization name only - NOT the full repo path)
- repoSlug: "{repo}"
- pullRequestId: "{pr_id}"

{context_section}CRITICAL INSTRUCTIONS FOR RECURRING REVIEW:
1. The **Previous Analysis Issues** are provided below. Use this information to determine if any of these issues have been **resolved in the current diff**.
2. If a previously reported issue is **fixed** in the new code, Report it again with the status “resolved”.
3. If a previously reported issue **persists** (i.e., the relevant code wasn't changed or the fix was incomplete), you **MUST** report it again in the current review's 'issues' list.
4. Always review the **entire current diff** for **new** issues as well.

--- PREVIOUS ANALYSIS ISSUES ---
{previous_issues_json}
--- END OF PREVIOUS ISSUES ---

Perform a code review considering:
1. Code quality and best practices
2. Potential bugs and edge cases
3. Performance and maintainability
4. Security issues
5. Suggest concrete fixes in the form of DIFF Patch if applicable, and put it in suggested fix

{ISSUE_CATEGORIES}

EFFICIENCY INSTRUCTIONS (YOU HAVE LIMITED STEPS - MAX 120):
1. First, retrieve the PR diff using getPullRequestDiff tool
2. Analyze the diff content directly - do NOT fetch each file individually unless absolutely necessary
3. After analysis, produce your JSON response IMMEDIATELY
4. Do NOT make redundant tool calls - each tool call uses one of your limited steps

You MUST:
1. Retrieve diff using getPullRequestDiff MCP tool (this gives you all changes)
2. Analyze the diff to identify issues and check previous issues
3. STOP making tool calls and produce your final JSON response
4. Assign a category from the list above to EVERY issue

DO NOT:
1. Fetch files one by one when the diff already shows the changes
2. Make more than 10-15 tool calls total
3. Continue making tool calls indefinitely

CRITICAL INSTRUCTION FOR LARGE PRs:
Report ALL issues found. Do not group them or omit them for brevity. If you find many issues, report ALL of them. The user wants a comprehensive list, no matter how long the output is.


IMPORTANT LINE NUMBER INSTRUCTIONS:
The "line" field MUST contain the line number in the NEW version of the file (after changes).
When reading unified diff format, use the line number from the '+' side of hunk headers: @@ -old_start,old_count +NEW_START,new_count @@
Calculate the actual line number by: NEW_START + offset within the hunk (counting only context and added lines, not removed lines).
For added lines (+), count from NEW_START. For context lines (no prefix), also count from NEW_START.
If you retrieve the full source file content, use the line number as it appears in that file.

{SUGGESTED_FIX_DIFF_FORMAT}

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
      "suggestedFixDiff": "Unified diff showing exact code changes (MUST follow SUGGESTED_FIX_DIFF_FORMAT above)",
      "isResolved": false|true
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

If token limit exceeded, STOP IMMEDIATELY AND return:
{{
  "comment": "The code review process was not completed successfully due to exceeding the allowable number of tokens (fileDiff).",
  "issues": [
    {{
      "severity": "LOW",
      "category": "CODE_QUALITY",
      "file": "",
      "line": "0",
      "reason": "The code review process was not completed successfully due to exceeding the allowable number of tokens (fileDiff).",
      "suggestedFixDescription": "Increase the allowed number of tokens or choose a model with a larger context."
    }}
  ]
}}

Use the reportGenerator MCP tool if available to help structure this response. Do NOT include any markdown formatting, explanatory text, or other content - only the JSON object.
"""
        return prompt

    @staticmethod
    def build_branch_review_prompt_with_branch_issues_data(pr_metadata: Dict[str, Any]) -> str:
        print("Building branch review prompt with branch issues data")
        workspace = pr_metadata.get("workspace", "<unknown_workspace>")
        repo = pr_metadata.get("repoSlug", "<unknown_repo>")
        commit_hash = pr_metadata.get("commitHash", "<unknown_commit_hash>")
        branch = pr_metadata.get("branch", "<unknown_branch>")
        # Get and format previous issues data
        previous_issues: List[Dict[str, Any]] = pr_metadata.get("previousCodeAnalysisIssues", [])

        # We need a clean JSON string of the previous issues to inject into the prompt
        previous_issues_json = json.dumps(previous_issues, indent=2, default=str)
        prompt = f"""You are an expert code reviewer performing a branch reconciliation review after a PR merge.
Workspace: {workspace}
Repository slug: {repo}
Commit Hash: {commit_hash}
Branch: {branch}

## MCP Tool Parameters
When calling MCP tools (getBranchFileContent, etc.), use these EXACT values:
- workspace: "{workspace}" (owner/organization name only - NOT the full repo path)
- repoSlug: "{repo}"

CRITICAL INSTRUCTIONS FOR BRANCH RECONCILIATION:
1. The **Previous Analysis Issues** are provided below - these are issues that existed on the branch BEFORE this PR.
2. Your task is to determine if any of these pre-existing issues have been **resolved based on the current content of the file(s) on the branch**.
3. For EACH issue in the previous analysis, you MUST include it in your response with:
   - "issueId": "<ORIGINAL_ISSUE_ID>" (copy the 'id' field from the previous issue)
   - "isResolved": true (if the issue is fixed by this PR's changes)
   - "isResolved": false (if the issue still persists)
   - "reason": "Explanation of why it's resolved or still present"
4. DO NOT report new issues - this is ONLY for checking resolution status of existing issues.
5. You MUST retrieve the current PR diff using MCP tools to compare against the previous issues ( e.g. via getBranchFileContent tool ).

--- PREVIOUS ANALYSIS ISSUES ---
{previous_issues_json}
--- END OF PREVIOUS ISSUES ---

EFFICIENCY INSTRUCTIONS (YOU HAVE LIMITED STEPS - MAX 120):
1. For each file with issues, retrieve content using getBranchFileContent
2. Analyze content to determine if issues are resolved
3. After checking all relevant files, produce your JSON response IMMEDIATELY
4. Do NOT make redundant tool calls - each tool call uses one of your limited steps

You MUST:
1. Retrieve file content for files with issues using getBranchFileContent MCP tool
2. For each previous issue, check if the current file content shows it resolved
3. STOP making tool calls and produce your final JSON response once you have analyzed all relevant files
4. If you see similar errors, you can group them together. Set the duplicate to isResolved: true, even if the issue has not been resolved, and leave one of the errors in its original status.

DO NOT:
1. Report new issues - focus ONLY on the provided previous issues
2. Make more than necessary tool calls - be efficient
3. Continue making tool calls indefinitely

IMPORTANT LINE NUMBER INSTRUCTIONS:
The "line" field MUST contain the line number in the current version of the file on the branch.
If you retrieve the full source file content via getBranchFileContent, use the line number as it appears in that file.

CRITICAL: Your final response must be ONLY a valid JSON object in this exact format:
{{
  "comment": "Summary of branch reconciliation - how many issues were resolved vs persisting",
  "issues": [
    {{
      "issueId": "<id_from_previous_issue>",
      "severity": "HIGH|MEDIUM|LOW",
      "category": "SECURITY|PERFORMANCE|CODE_QUALITY|BUG_RISK|STYLE|DOCUMENTATION|BEST_PRACTICES|ERROR_HANDLING|TESTING|ARCHITECTURE",
      "file": "file-path",
      "line": "line-number-in-current-file",
      "reason": "Explanation of resolution status",
      "suggestedFixDescription": "Clear description of how to fix the issue",
      "suggestedFixDiff": "Unified diff showing exact code changes (follow standard diff format with --- +++ and @@ headers)",
      "isResolved": true
    }}
  ]
}}

IMPORTANT:
- The "issues" field MUST be a JSON array [], NOT an object with numeric keys.
- You MUST include ALL previous issues in your response
- Each issue MUST have the "issueId" field matching the original issue ID
- Each issue MUST have "isResolved" as either true or false
- Each issue MUST have a "category" field from the allowed list
- REQUIRED FOR ALL UNRESOLVED ISSUES: Include "suggestedFixDescription" AND "suggestedFixDiff" with actual code fix

Use the reportGenerator MCP tool if available to help structure this response. Do NOT include any markdown formatting, explanatory text, or other content - only the JSON object.
"""
        return prompt


    @staticmethod
    def get_additional_instructions() -> str:
        """
        Get additional instructions for the MCP agent focusing on structured JSON output.
        Note: Curly braces must be doubled to escape them for LangChain's ChatPromptTemplate.

        Returns:
            String with additional instructions for the agent
        """
        return (
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

    @staticmethod
    def _build_legacy_rag_section(rag_context: Dict[str, Any]) -> str:
        """Build legacy RAG section for backward compatibility."""
        rag_section = "\n--- RELEVANT CODE CONTEXT FROM CODEBASE ---\n"
        rag_section += "The following code snippets from the repository are semantically relevant to this PR:\n\n"
        for idx, chunk in enumerate(rag_context.get("relevant_code", [])[:5], 1):
            rag_section += f"Context {idx} (from {chunk.get('metadata', {}).get('path', 'unknown')}):\n"
            rag_section += f"{chunk.get('text', '')}\n\n"
        rag_section += "--- END OF RELEVANT CONTEXT ---\n\n"
        return rag_section

    @staticmethod
    def build_structured_rag_section(
        rag_context: Dict[str, Any],
        max_chunks: int = 5,
        token_budget: int = 4000
    ) -> str:
        """
        Build a structured RAG section with priority markers.

        Args:
            rag_context: RAG query results
            max_chunks: Maximum number of chunks to include
            token_budget: Approximate token budget for RAG section

        Returns:
            Formatted RAG section string
        """
        if not rag_context or not rag_context.get("relevant_code"):
            return ""

        relevant_code = rag_context.get("relevant_code", [])
        related_files = rag_context.get("related_files", [])

        section_parts = []
        section_parts.append("=== RAG CONTEXT: Additional Relevant Code (5% attention) ===")
        section_parts.append(f"Related files discovered: {len(related_files)}")
        section_parts.append("")

        current_tokens = 0
        tokens_per_char = 0.25

        for idx, chunk in enumerate(relevant_code[:max_chunks], 1):
            chunk_text = chunk.get("text", "")
            chunk_tokens = int(len(chunk_text) * tokens_per_char)

            if current_tokens + chunk_tokens > token_budget:
                section_parts.append(f"[Remaining {len(relevant_code) - idx + 1} chunks omitted for token budget]")
                break

            chunk_path = chunk.get("metadata", {}).get("path", "unknown")
            chunk_score = chunk.get("score", 0)

            section_parts.append(f"### RAG Chunk {idx}: {chunk_path}")
            section_parts.append(f"Relevance: {chunk_score:.3f}")
            section_parts.append("```")
            section_parts.append(chunk_text)
            section_parts.append("```")
            section_parts.append("")

            current_tokens += chunk_tokens

        section_parts.append("=== END RAG CONTEXT ===")
        return "\n".join(section_parts)

    @staticmethod
    def build_direct_first_review_prompt(
        pr_metadata: Dict[str, Any],
        diff_content: str,
        rag_context: Dict[str, Any] = None,
        structured_context: Optional[str] = None
    ) -> str:
        """
        Build prompt for review with embedded diff - first review.

        The diff is already embedded in the prompt.
        Agent still has access to other MCP tools (getFile, getComments, etc.)
        but should NOT call getPullRequestDiff.
        """
        workspace = pr_metadata.get("workspace", "<unknown_workspace>")
        repo = pr_metadata.get("repoSlug", "<unknown_repo>")
        pr_id = pr_metadata.get("pullRequestId", pr_metadata.get("prId", "<unknown_pr>"))

        # Build context section
        context_section = ""
        if structured_context:
            context_section = f"""
{LOST_IN_MIDDLE_INSTRUCTIONS}

{structured_context}
"""
        elif rag_context and rag_context.get("relevant_code"):
            context_section = PromptBuilder._build_legacy_rag_section(rag_context)

        prompt = f"""You are an expert code reviewer with 15+ years of experience in security, architecture, and code quality.
Workspace: {workspace}
Repository slug: {repo}
Pull Request: {pr_id}

{context_section}

=== PR DIFF (ALREADY PROVIDED - DO NOT CALL getPullRequestDiff) ===
IMPORTANT: The diff is embedded below. Do NOT call getPullRequestDiff tool.
You may use other MCP tools (getBranchFileContent, getPullRequestComments, etc.) if needed.

{diff_content}

=== END OF DIFF ===

Perform a PRIORITIZED code review of the diff above:

🎯 ANALYSIS FOCUS (in order of importance):
1. SECURITY: SQL injection, XSS, auth bypass, hardcoded secrets
2. ARCHITECTURE: Design issues, breaking changes, SOLID violations
3. PERFORMANCE: N+1 queries, memory leaks, inefficient algorithms
4. BUG_RISK: Edge cases, null checks, type mismatches
5. CODE_QUALITY: Maintainability, complexity, code smells

{ISSUE_CATEGORIES}

IMPORTANT LINE NUMBER INSTRUCTIONS:
The "line" field MUST contain the line number in the NEW version of the file (after changes).
When reading unified diff format, use the line number from the '+' side of hunk headers: @@ -old_start,old_count +NEW_START,new_count @@
Calculate the actual line number by: NEW_START + offset within the hunk.

{SUGGESTED_FIX_DIFF_FORMAT}

CRITICAL: Report ALL issues found. Do not group them or omit them for brevity.

Your response must be ONLY a valid JSON object in this exact format:
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
      "suggestedFixDiff": "Unified diff showing exact code changes (MUST follow SUGGESTED_FIX_DIFF_FORMAT above)",
      "isResolved": false
    }}
  ]
}}

IMPORTANT: REQUIRED FOR ALL ISSUES - Include "suggestedFixDescription" AND "suggestedFixDiff" with actual code fix in unified diff format.

If no issues are found, return:
{{
  "comment": "Code review completed successfully with no issues found",
  "issues": []
}}

Do NOT include any markdown formatting, explanatory text, or other content - only the JSON object.
"""
        return prompt

    @staticmethod
    def build_direct_review_prompt_with_previous_analysis(
        pr_metadata: Dict[str, Any],
        diff_content: str,
        rag_context: Dict[str, Any] = None,
        structured_context: Optional[str] = None
    ) -> str:
        """
        Build prompt for direct review mode with previous analysis data.
        """
        workspace = pr_metadata.get("workspace", "<unknown_workspace>")
        repo = pr_metadata.get("repoSlug", "<unknown_repo>")
        pr_id = pr_metadata.get("pullRequestId", pr_metadata.get("prId", "<unknown_pr>"))
        previous_issues: List[Dict[str, Any]] = pr_metadata.get("previousCodeAnalysisIssues", [])
        previous_issues_json = json.dumps(previous_issues, indent=2, default=str)

        # Build context section
        context_section = ""
        if structured_context:
            context_section = f"""
{LOST_IN_MIDDLE_INSTRUCTIONS}

{structured_context}
"""
        elif rag_context and rag_context.get("relevant_code"):
            context_section = PromptBuilder._build_legacy_rag_section(rag_context)

        prompt = f"""You are an expert code reviewer with 15+ years of experience in security, architecture, and code quality.
Workspace: {workspace}
Repository slug: {repo}
Pull Request: {pr_id}

{context_section}

=== PR DIFF (ALREADY PROVIDED - DO NOT CALL getPullRequestDiff) ===
IMPORTANT: The diff is embedded below. Do NOT call getPullRequestDiff tool.
You may use other MCP tools (getBranchFileContent, getPullRequestComments, etc.) if needed.

{diff_content}

=== END OF DIFF ===

=== PREVIOUS ANALYSIS ISSUES ===
The following issues were found in a previous review. Check if they are still present or have been resolved:
{previous_issues_json}
=== END OF PREVIOUS ISSUES ===

Perform a PRIORITIZED code review of the diff above:

🎯 TASKS:
1. Check if each previous issue is still present in the code
2. Mark resolved issues with "isResolved": true
3. Find NEW issues introduced in this PR version
4. Prioritize by security > architecture > performance > quality

{ISSUE_CATEGORIES}

IMPORTANT LINE NUMBER INSTRUCTIONS:
For existing issues, update line numbers if code moved.
For new issues, use line numbers from the NEW version of files.

{SUGGESTED_FIX_DIFF_FORMAT}

Your response must be ONLY a valid JSON object:
{{
  "comment": "Summary of changes since last review",
  "issues": [
    {{
      "severity": "HIGH|MEDIUM|LOW",
      "category": "SECURITY|PERFORMANCE|...",
      "file": "file-path",
      "line": "line-number-in-new-file",
      "reason": "Explanation",
      "suggestedFixDescription": "Clear description of how to fix the issue",
      "suggestedFixDiff": "Unified diff showing exact code changes (MUST follow SUGGESTED_FIX_DIFF_FORMAT above)",
      "isResolved": false|true
    }}
  ]
}}

IMPORTANT: REQUIRED FOR ALL ISSUES - Include "suggestedFixDescription" AND "suggestedFixDiff" with actual code fix in unified diff format.

Do NOT include any markdown formatting - only the JSON object.
"""
        return prompt

    @staticmethod
    def build_incremental_review_prompt(
        pr_metadata: Dict[str, Any],
        delta_diff_content: str,
        full_diff_content: str,
        rag_context: Dict[str, Any] = None,
        structured_context: Optional[str] = None
    ) -> str:
        """
        Build prompt for INCREMENTAL analysis mode.

        This is used when re-reviewing a PR after new commits have been pushed.
        The delta_diff contains only changes since the last analyzed commit,
        while full_diff provides the complete PR diff for reference.

        Focus is on:
        1. Reviewing new/changed code in delta_diff
        2. Checking if previous issues are resolved
        3. Finding new issues introduced since last review
        """
        print("Building INCREMENTAL review prompt with delta diff")
        workspace = pr_metadata.get("workspace", "<unknown_workspace>")
        repo = pr_metadata.get("repoSlug", "<unknown_repo>")
        pr_id = pr_metadata.get("pullRequestId", pr_metadata.get("prId", "<unknown_pr>"))
        previous_commit = pr_metadata.get("previousCommitHash", "<unknown>")
        current_commit = pr_metadata.get("currentCommitHash", "<unknown>")
        previous_issues: List[Dict[str, Any]] = pr_metadata.get("previousCodeAnalysisIssues", [])
        previous_issues_json = json.dumps(previous_issues, indent=2, default=str)

        # Build context section
        context_section = ""
        if structured_context:
            context_section = f"""
{LOST_IN_MIDDLE_INSTRUCTIONS}

{structured_context}
"""
        elif rag_context and rag_context.get("relevant_code"):
            context_section = PromptBuilder._build_legacy_rag_section(rag_context)

        prompt = f"""You are an expert code reviewer performing an INCREMENTAL review of changes since the last analysis.
Workspace: {workspace}
Repository slug: {repo}
Pull Request: {pr_id}

## INCREMENTAL REVIEW MODE
This is a RE-REVIEW after new commits were pushed to the PR.
- Previous analyzed commit: {previous_commit}
- Current commit: {current_commit}

{context_section}

=== DELTA DIFF (CHANGES SINCE LAST REVIEW - PRIMARY FOCUS) ===
IMPORTANT: This diff shows ONLY the changes made since the last analyzed commit.
Focus your review primarily on this delta diff as it contains the new code to review.

{delta_diff_content}

=== END OF DELTA DIFF ===

=== PREVIOUS ANALYSIS ISSUES ===
These issues were found in the previous review iteration.
Check if each one has been RESOLVED in the new commits (delta diff):
{previous_issues_json}
=== END OF PREVIOUS ISSUES ===

## INCREMENTAL REVIEW TASKS (in order of priority):

1. **DELTA DIFF ANALYSIS (80% attention)**:
   - Focus on reviewing the DELTA DIFF (changes since last commit)
   - Find NEW issues introduced in these changes
   - These are the most important findings as they represent untested code

2. **PREVIOUS ISSUES RESOLUTION CHECK (15% attention)**:
   - Check each previous issue against the delta diff
   - If the problematic code was modified/fixed in delta → mark "isResolved": true
   - If the code is unchanged in delta → issue persists, report it again with "isResolved": false
   - UPDATE line numbers if code has moved

3. **CONTEXT VERIFICATION (5% attention)**:
   - Use full PR diff only when needed to understand delta changes ( retrieve it via MCP tools ONLY if necessary )
   - Do NOT re-review code that hasn't changed

{ISSUE_CATEGORIES}

IMPORTANT LINE NUMBER INSTRUCTIONS:
The "line" field MUST contain the line number in the NEW version of the file (after changes).
For issues found in delta diff, calculate line numbers from the delta hunk headers.
For persisting issues, update line numbers if the code has moved.

{SUGGESTED_FIX_DIFF_FORMAT}

CRITICAL: Report ALL issues found in delta diff. Do not group them or omit them for brevity.

Your response must be ONLY a valid JSON object in this exact format:
{{
  "comment": "Summary of incremental review: X new issues found in delta, Y previous issues resolved, Z issues persist",
  "issues": [
    {{
      "issueId": "<id_from_previous_issue>",
      "severity": "HIGH|MEDIUM|LOW",
      "category": "SECURITY|PERFORMANCE|CODE_QUALITY|BUG_RISK|STYLE|DOCUMENTATION|BEST_PRACTICES|ERROR_HANDLING|TESTING|ARCHITECTURE",
      "file": "file-path",
      "line": "line-number-in-new-file",
      "reason": "Detailed explanation of the issue",
      "suggestedFixDescription": "Clear description of how to fix the issue",
      "suggestedFixDiff": "Unified diff showing exact code changes (MUST follow SUGGESTED_FIX_DIFF_FORMAT above)",
      "isResolved": false|true
    }}
  ]
}}

IMPORTANT SCHEMA RULES:
- The "issues" field MUST be a JSON array [], NOT an object with numeric keys
- Each issue MUST have: severity, category, file, line, reason, isResolved
- For resolved previous issues, still include them with "isResolved": true
- For new issues from delta diff, set "isResolved": false
- REQUIRED FOR ALL UNRESOLVED ISSUES: Include "suggestedFixDescription" AND "suggestedFixDiff"

If no issues are found, return:
{{
  "comment": "Incremental review completed: All previous issues resolved, no new issues found",
  "issues": []
}}

Do NOT include any markdown formatting, explanatory text, or other content - only the JSON object.
"""
        return prompt