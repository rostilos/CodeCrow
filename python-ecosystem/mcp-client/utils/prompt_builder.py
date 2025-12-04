from typing import Any, Dict, List
import json
from model.models import IssueDTO


class PromptBuilder:
    @staticmethod
    def build_first_review_prompt(pr_metadata: Dict[str, Any], rag_context: Dict[str, Any] = None) -> str:
        print("Building first review prompt")
        workspace = pr_metadata.get("workspace", "<unknown_workspace>")
        repo = pr_metadata.get("repoSlug", "<unknown_repo>")
        pr_id = pr_metadata.get("pullRequestId", pr_metadata.get("prId", "<unknown_pr>"))

        # Build RAG context section if available
        rag_section = ""
        if rag_context and rag_context.get("relevant_code"):
            rag_section = "\n--- RELEVANT CODE CONTEXT FROM CODEBASE ---\n"
            rag_section += "The following code snippets from the repository are semantically relevant to this PR:\n\n"
            for idx, chunk in enumerate(rag_context.get("relevant_code", [])[:5], 1):
                rag_section += f"Context {idx} (from {chunk.get('metadata', {}).get('path', 'unknown')}):\n"
                rag_section += f"{chunk.get('text', '')}\n\n"
            rag_section += "--- END OF RELEVANT CONTEXT ---\n\n"

        prompt = f"""You are an expert code reviewer.
Workspace: {workspace}
Repository slug: {repo}
Pull Request: {pr_id}

{rag_section}Perform a code review considering:
1. Code quality and best practices
2. Potential bugs and edge cases
3. Performance and maintainability
4. Security issues
5. Suggest concrete fixes in the form of DIFF Patch if applicable, and put it in suggested fix

You MUST:
1. Retrieve diff and source files using available MCP tools
2. Decide which source files to retrieve via MCP server for code context
3. Use the reportGenerator MCP tool to generate the structured report
4. Do any other computations and requests via MCP servers

DO NOT:
1. Return result if u failed to retrieve the report via MCP servers

CRITICAL: Your final response must be ONLY a valid JSON object in this exact format:
{{
  "comment": "Brief summary of the overall code review findings",
  "issues": {{
    "0": {{
      "severity": "HIGH|MEDIUM|LOW",
      "file": "file-path",
      "line": "issue-start-line-number",
      "reason": "Detailed explanation of the issue",
      "suggestedFixDescription": "Optional fix suggestion description",
      "suggestedFixDiff": "Optional diff suggestion",
      "isResolved": true/false
    }},
    "1": {{
      "severity": "HIGH|MEDIUM|LOW",
      "file": "file-path",
      "line": "issue-start-line-number",
      "reason": "Detailed explanation of the issue",
      "suggestedFixDescription": "Optional fix suggestion description",
      "suggestedFixDiff": "Optional diff suggestion",
      "isResolved": true/false
    }}
  }}
}}

If no issues are found, return:
{{
  "comment": "Code review completed successfully with no issues found",
  "issues": {{}}
}}

Use the reportGenerator MCP tool if available to help structure this response. Do NOT include any markdown formatting, explanatory text, or other content - only the JSON object.
"""
        print(prompt)
        return prompt

    @staticmethod
    def build_review_prompt_with_previous_analysis_data(pr_metadata: Dict[str, Any], rag_context: Dict[str, Any] = None) -> str:
        print("Building review prompt with previous analysis data")
        workspace = pr_metadata.get("workspace", "<unknown_workspace>")
        repo = pr_metadata.get("repoSlug", "<unknown_repo>")
        pr_id = pr_metadata.get("pullRequestId", pr_metadata.get("prId", "<unknown_pr>"))
        # 🆕 Get and format previous issues data
        previous_issues: List[Dict[str, Any]] = pr_metadata.get("previousCodeAnalysisIssues", [])

        # We need a clean JSON string of the previous issues to inject into the prompt
        previous_issues_json = json.dumps(previous_issues, indent=2, default=str)

        # Build RAG context section if available
        rag_section = ""
        if rag_context and rag_context.get("relevant_code"):
            rag_section = "\n--- RELEVANT CODE CONTEXT FROM CODEBASE ---\n"
            rag_section += "The following code snippets from the repository are semantically relevant to this PR:\n\n"
            for idx, chunk in enumerate(rag_context.get("relevant_code", [])[:5], 1):
                rag_section += f"Context {idx} (from {chunk.get('metadata', {}).get('path', 'unknown')}):\n"
                rag_section += f"{chunk.get('text', '')}\n\n"
            rag_section += "--- END OF RELEVANT CONTEXT ---\n\n"

        prompt = f"""You are an expert code reviewer performing a review on a subsequent version of a pull request.
Workspace: {workspace}
Repository slug: {repo}
Pull Request: {pr_id}

{rag_section}CRITICAL INSTRUCTIONS FOR RECURRING REVIEW:
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

You MUST:
1. Retrieve diff and source files using available MCP tools
2. Decide which source files to retrieve via MCP server for code context
3. Use the reportGenerator MCP tool to generate the structured report
4. Do any other computations and requests via MCP servers

DO NOT:
1. Return result if u failed to retrieve the report via MCP servers

CRITICAL: Your final response must be ONLY a valid JSON object in this exact format:
{{
  "comment": "Brief summary of the overall code review findings",
  "issues": {{
    "0": {{
      "severity": "HIGH|MEDIUM|LOW",
      "file": "file-path",
      "line": "issue-start-line-number",
      "reason": "Detailed explanation of the issue",
      "suggestedFixDescription": "Optional fix suggestion description",
      "suggestedFixDiff": "Optional diff suggestion",
      "isResolved": true/false
      
    }},
    "1": {{
      "severity": "HIGH|MEDIUM|LOW",
      "file": "file-path",
      "line": "issue-start-line-number",
      "reason": "Detailed explanation of the issue",
      "suggestedFixDescription": "Optional fix suggestion description",
      "suggestedFixDiff": "Optional diff suggestion",
      "isResolved": true/false
    }}
  }}
}}

If no issues are found, return:
{{
  "comment": "Code review completed successfully with no issues found",
  "issues": {{}}
}}

If token limit exceeded, STOP IMMEDIATELY AND return:
{{
  "comment": "The code review process was not completed successfully due to exceeding the allowable number of tokens (fileDiff).",
  "issues": {{
    "0": {{
      "severity": "LOW",
      "file": "",
      "line": "0",
      "reason": "The code review process was not completed successfully due to exceeding the allowable number of tokens (fileDiff).",
      "suggestedFixDescription": "Increase the allowed number of tokens or choose a model with a larger context.",
      "suggestedFixDiff": "",
      "isResolved": false      
    }}
  }}
}}

Use the reportGenerator MCP tool if available to help structure this response. Do NOT include any markdown formatting, explanatory text, or other content - only the JSON object.
"""
        print(prompt)
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

You MUST:
1. Retrieve the PR diff using available MCP tools
2. For each previous issue, check if the changes in the current file content resolve it
3. If you see similar errors, you can group them together. Set the duplicate to isResolved: true, and leave one of the errors in its original status.

DO NOT:
1. Report new issues - focus ONLY on the provided previous issues
2. Return a result if you failed to retrieve the diff via MCP servers

CRITICAL: Your final response must be ONLY a valid JSON object in this exact format:
{{
  "comment": "Summary of branch reconciliation - how many issues were resolved vs persisting",
  "issues": {{
    "0": {{
      "issueId": "<id_from_previous_issue>",
      "severity": "HIGH|MEDIUM|LOW",
      "file": "file-path",
      "line": "issue-start-line-number",
      "reason": "Explanation of resolution status",
      "suggestedFixDescription": "Optional",
      "suggestedFixDiff": "Optional",
      "isResolved": true
    }},
    "1": {{
      "issueId": "<id_from_previous_issue>",
      "severity": "HIGH|MEDIUM|LOW",
      "file": "file-path",
      "line": "issue-start-line-number",
      "reason": "Explanation of why issue persists",
      "suggestedFixDescription": "Optional",
      "suggestedFixDiff": "Optional",
      "isResolved": false
    }}
  }}
}}

IMPORTANT: 
- You MUST include ALL previous issues in your response
- Each issue MUST have the "issueId" field matching the original issue ID
- Each issue MUST have "isResolved" as either true or false

Use the reportGenerator MCP tool if available to help structure this response. Do NOT include any markdown formatting, explanatory text, or other content - only the JSON object.
"""
        print(prompt)
        return prompt


    @staticmethod
    def get_additional_instructions() -> str:
        """
        Get additional instructions for the MCP agent focusing on structured JSON output.

        Returns:
            String with additional instructions for the agent
        """
        return (
            "CRITICAL: You must return ONLY a valid JSON object with 'comment' and 'issues' fields. "
            "Use the reportGenerator MCP tool if available to structure your response. "
            "Do NOT include any markdown, explanations, or other text - only the JSON structure specified in the prompt. "
            "If you encounter any errors or cannot complete the review, still return the JSON format with appropriate error messages in the comment field."
        )