from typing import Any, Dict, List, Optional
import json
from model.dtos import IssueDTO
from utils.prompts.prompt_constants import (
    ADDITIONAL_INSTRUCTIONS,
    BRANCH_REVIEW_PROMPT_TEMPLATE,
    STAGE_0_PLANNING_PROMPT_TEMPLATE,
    STAGE_1_BATCH_PROMPT_TEMPLATE,
    STAGE_2_CROSS_FILE_PROMPT_TEMPLATE,
    STAGE_3_AGGREGATION_PROMPT_TEMPLATE,
    STAGE_1_MCP_TOOL_SECTION,
    STAGE_3_MCP_VERIFICATION_SECTION,
)

class PromptBuilder:
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
        
        return BRANCH_REVIEW_PROMPT_TEMPLATE.format(
            workspace=workspace,
            repo=repo,
            commit_hash=commit_hash,
            branch=branch,
            previous_issues_json=previous_issues_json
        )

    @staticmethod
    def get_additional_instructions() -> str:
        """
        Get additional instructions for the MCP agent focusing on structured JSON output.
        Returns:
            String with additional instructions for the agent
        """
        return ADDITIONAL_INSTRUCTIONS

    @staticmethod
    def build_stage_0_planning_prompt(
        repo_slug: str,
        pr_id: str,
        pr_title: str,
        author: str,
        branch_name: str,
        target_branch: str,
        commit_hash: str,
        changed_files_json: str
    ) -> str:
        """
        Build prompt for Stage 0: Planning & Prioritization.
        """
        return STAGE_0_PLANNING_PROMPT_TEMPLATE.format(
            repo_slug=repo_slug,
            pr_id=pr_id,
            pr_title=pr_title,
            author=author,
            branch_name=branch_name,
            target_branch=target_branch,
            commit_hash=commit_hash,
            changed_files_json=changed_files_json
        )

    @staticmethod
    def build_stage_1_batch_prompt(
        files: List[Dict[str, str]], # List of {path, diff, type, old_code, focus_areas}
        priority: str,
        project_rules: str = "",
        file_outlines: str = "",
        rag_context: str = "",
        is_incremental: bool = False,
        previous_issues: str = "",
        all_pr_files: List[str] = None,  # All files in this PR for cross-file awareness
        deleted_files: List[str] = None,  # Files being deleted in this PR
        use_mcp_tools: bool = False,
        target_branch: str = "",
    ) -> str:
        """
        Build prompt for Stage 1: Batch File Review.
        In incremental mode, includes previous issues context and focuses on delta changes.
        When use_mcp_tools=True, appends MCP tool instructions.
        """
        files_context = ""
        for i, f in enumerate(files):
            diff_label = "Delta Diff (NEW CHANGES ONLY)" if is_incremental else "Diff"
            files_context += f"""
---
FILE #{i+1}: {f['path']}
Type: {f.get('type', 'MODIFIED')}
Focus Areas: {', '.join(f.get('focus_areas', []))}
Context:
{f.get('old_code', '')}

{diff_label}:
{f.get('diff', '')}
---
"""
        
        # Add incremental mode instructions if applicable
        incremental_instructions = ""
        if is_incremental:
            incremental_instructions = """
## INCREMENTAL REVIEW MODE
This is a follow-up review after the PR was updated with new commits.
The diff above shows ONLY the changes since the last review - focus on these NEW changes.
For any previous issues listed below, check if they are RESOLVED in the new changes.
"""

        # Add PR-wide file list for cross-batch awareness
        pr_files_context = ""
        if all_pr_files:
            current_batch_files = [f['path'] for f in files]
            other_files = [fp for fp in all_pr_files if fp not in current_batch_files]
            if other_files:
                pr_files_context = f"""
## OTHER FILES IN THIS PR (for cross-file awareness)
This PR also modifies these files (reviewed in other batches):
{chr(10).join('- ' + fp for fp in other_files[:20])}
{'... and ' + str(len(other_files) - 20) + ' more files' if len(other_files) > 20 else ''}
Consider potential interactions with these files when reviewing.
"""

        # Add deleted files section so LLM knows which files are being removed
        deleted_files_context = ""
        if deleted_files:
            deleted_files_context = f"""
## FILES BEING DELETED IN THIS PR
The following files are being DELETED/REMOVED in this PR. Any RAG context referencing these files is STALE.
Do NOT flag duplication or conflicts with code from these files — the code is being intentionally removed:
{chr(10).join('- ' + fp for fp in deleted_files[:30])}
{'... and ' + str(len(deleted_files) - 30) + ' more' if len(deleted_files) > 30 else ''}
"""

        prompt = STAGE_1_BATCH_PROMPT_TEMPLATE.format(
            project_rules=project_rules,
            file_outlines=file_outlines,
            priority=priority,
            files_context=files_context,
            rag_context=rag_context or "(No additional codebase context available)",
            incremental_instructions=incremental_instructions,
            previous_issues=previous_issues,
            pr_files_context=pr_files_context,
            deleted_files_context=deleted_files_context
        )

        # Conditionally append MCP tool instructions
        if use_mcp_tools and target_branch:
            from service.review.orchestrator.mcp_tool_executor import McpToolExecutor
            max_calls = McpToolExecutor.STAGE_CONFIG["stage_1"]["max_calls"]
            prompt += STAGE_1_MCP_TOOL_SECTION.format(
                max_calls=max_calls,
                target_branch=target_branch
            )

        return prompt

    @staticmethod
    def build_stage_2_cross_file_prompt(
        repo_slug: str,
        pr_title: str,
        commit_hash: str,
        stage_1_findings_json: str,
        architecture_context: str,
        migrations: str,
        cross_file_concerns: List[str],
        cross_module_context: str = "",
        project_rules: str = ""
    ) -> str:
        """
        Build prompt for Stage 2: Cross-File & Architectural Review.
        Includes cross-module RAG context for duplication detection.
        ``project_rules`` is a compact digest of custom project rules
        (titles + types only) so Stage 2 can respect ENFORCE/SUPPRESS at
        the architectural level.
        """
        concerns_text = "\n".join([f"- {c}" for c in cross_file_concerns])

        # Build a compact digest for Stage 2 (titles + types only)
        project_rules_digest = ""
        if project_rules:
            project_rules_digest = (
                "Custom Project Rules (apply at architectural level too):\n"
                + project_rules
            )
        
        return STAGE_2_CROSS_FILE_PROMPT_TEMPLATE.format(
            repo_slug=repo_slug,
            pr_title=pr_title,
            commit_hash=commit_hash,
            concerns_text=concerns_text,
            stage_1_findings_json=stage_1_findings_json,
            architecture_context=architecture_context,
            migrations=migrations,
            cross_module_context=cross_module_context or "No cross-module context available (RAG not configured or no similar implementations found).",
            project_rules_digest=project_rules_digest
        )

    @staticmethod
    def build_stage_3_aggregation_prompt(
        repo_slug: str,
        pr_id: str,
        author: str,
        pr_title: str,
        total_files: int,
        additions: int,
        deletions: int,
        stage_0_plan: str,
        stage_1_issues_json: str,
        stage_2_findings_json: str,
        recommendation: str,
        incremental_context: str = "",
        use_mcp_tools: bool = False,
        target_branch: str = "",
    ) -> str:
        """
        Build prompt for Stage 3: Aggregation & Final Report.
        When use_mcp_tools=True, appends MCP verification instructions.
        """
        prompt = STAGE_3_AGGREGATION_PROMPT_TEMPLATE.format(
            repo_slug=repo_slug,
            pr_id=pr_id,
            author=author,
            pr_title=pr_title,
            total_files=total_files,
            additions=additions,
            deletions=deletions,
            stage_0_plan=stage_0_plan,
            stage_1_issues_json=stage_1_issues_json,
            stage_2_findings_json=stage_2_findings_json,
            recommendation=recommendation,
            incremental_context=incremental_context
        )

        # Conditionally append MCP verification instructions
        if use_mcp_tools and target_branch:
            from service.review.orchestrator.mcp_tool_executor import McpToolExecutor
            max_calls = McpToolExecutor.STAGE_CONFIG["stage_3"]["max_calls"]
            prompt += STAGE_3_MCP_VERIFICATION_SECTION.format(
                max_calls=max_calls,
                target_branch=target_branch,
                pr_id=pr_id
            )

        return prompt