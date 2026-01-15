from typing import Any, Dict, List, Optional
import json
from model.models import IssueDTO
from utils.prompts.prompt_constants import (
    ADDITIONAL_INSTRUCTIONS,
    BRANCH_REVIEW_PROMPT_TEMPLATE,
    STAGE_0_PLANNING_PROMPT_TEMPLATE,
    STAGE_1_BATCH_PROMPT_TEMPLATE,
    STAGE_2_CROSS_FILE_PROMPT_TEMPLATE,
    STAGE_3_AGGREGATION_PROMPT_TEMPLATE
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
        rag_context: str = "",
        is_incremental: bool = False,
        previous_issues: str = ""
    ) -> str:
        """
        Build prompt for Stage 1: Batch File Review.
        In incremental mode, includes previous issues context and focuses on delta changes.
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

        return STAGE_1_BATCH_PROMPT_TEMPLATE.format(
            project_rules=project_rules,
            priority=priority,
            files_context=files_context,
            rag_context=rag_context or "(No additional codebase context available)",
            incremental_instructions=incremental_instructions,
            previous_issues=previous_issues
        )

    @staticmethod
    def build_stage_2_cross_file_prompt(
        repo_slug: str,
        pr_title: str,
        commit_hash: str,
        stage_1_findings_json: str,
        architecture_context: str,
        migrations: str,
        cross_file_concerns: List[str]
    ) -> str:
        """
        Build prompt for Stage 2: Cross-File & Architectural Review.
        """
        concerns_text = "\n".join([f"- {c}" for c in cross_file_concerns])
        
        return STAGE_2_CROSS_FILE_PROMPT_TEMPLATE.format(
            repo_slug=repo_slug,
            pr_title=pr_title,
            commit_hash=commit_hash,
            concerns_text=concerns_text,
            stage_1_findings_json=stage_1_findings_json,
            architecture_context=architecture_context,
            migrations=migrations
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
        recommendation: str
    ) -> str:
        """
        Build prompt for Stage 3: Aggregation & Final Report.
        """
        return STAGE_3_AGGREGATION_PROMPT_TEMPLATE.format(
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
            recommendation=recommendation
        )