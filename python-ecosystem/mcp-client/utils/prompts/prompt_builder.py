from typing import Any, Dict, List, Optional
import json
from model.models import IssueDTO
from utils.prompts.prompt_constants import (
    ISSUE_CATEGORIES,
    LINE_NUMBER_INSTRUCTIONS,
    ISSUE_DEDUPLICATION_INSTRUCTIONS,
    SUGGESTED_FIX_DIFF_FORMAT,
    LOST_IN_MIDDLE_INSTRUCTIONS,
    ADDITIONAL_INSTRUCTIONS,
    FIRST_REVIEW_PROMPT_TEMPLATE,
    REVIEW_WITH_PREVIOUS_ANALYSIS_DATA_TEMPLATE,
    BRANCH_REVIEW_PROMPT_TEMPLATE,
    DIRECT_FIRST_REVIEW_PROMPT_TEMPLATE,
    DIRECT_REVIEW_WITH_PREVIOUS_ANALYSIS_PROMPT_TEMPLATE,
    INCREMENTAL_REVIEW_PROMPT_TEMPLATE,
    STAGE_0_PLANNING_PROMPT_TEMPLATE,
    STAGE_1_BATCH_PROMPT_TEMPLATE,
    STAGE_2_CROSS_FILE_PROMPT_TEMPLATE,
    STAGE_3_AGGREGATION_PROMPT_TEMPLATE
)

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

        return FIRST_REVIEW_PROMPT_TEMPLATE.format(
            workspace=workspace,
            repo=repo,
            pr_id=pr_id,
            context_section=context_section,
            issue_categories=ISSUE_CATEGORIES,
            issue_deduplication_instructions=ISSUE_DEDUPLICATION_INSTRUCTIONS,
            line_number_instructions=LINE_NUMBER_INSTRUCTIONS,
            suggested_fix_diff_format=SUGGESTED_FIX_DIFF_FORMAT
        )

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

        return REVIEW_WITH_PREVIOUS_ANALYSIS_DATA_TEMPLATE.format(
            workspace=workspace,
            repo=repo,
            pr_id=pr_id,
            context_section=context_section,
            previous_issues_json=previous_issues_json,
            issue_categories=ISSUE_CATEGORIES,
            issue_deduplication_instructions=ISSUE_DEDUPLICATION_INSTRUCTIONS,
            line_number_instructions=LINE_NUMBER_INSTRUCTIONS,
            suggested_fix_diff_format=SUGGESTED_FIX_DIFF_FORMAT
        )

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