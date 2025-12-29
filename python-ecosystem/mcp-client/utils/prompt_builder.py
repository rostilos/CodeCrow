"""
Prompt builder module - delegates to utils/prompts/ submodules.

This module provides backward compatibility by re-exporting all functions
from the new modular prompt structure.
"""
from typing import Any, Dict, List, Optional
import json
from model.models import IssueDTO

# Re-export constants from the new module structure
from utils.prompts.constants import (
    ISSUE_CATEGORIES,
    SUGGESTED_FIX_DIFF_FORMAT,
    LOST_IN_MIDDLE_INSTRUCTIONS,
    JSON_RESPONSE_FORMAT,
    EFFICIENCY_INSTRUCTIONS,
    LINE_NUMBER_INSTRUCTIONS,
    ANALYSIS_FOCUS,
)

# Import the new implementations
from utils.prompts.pr_review import (
    build_first_review_prompt as _build_first_review_prompt,
    build_review_prompt_with_previous_analysis_data as _build_review_prompt_with_previous_analysis_data,
    build_direct_first_review_prompt as _build_direct_first_review_prompt,
    build_direct_review_prompt_with_previous_analysis as _build_direct_review_prompt_with_previous_analysis,
)

from utils.prompts.branch_reconciliation import (
    build_branch_review_prompt_with_branch_issues_data as _build_branch_review_prompt_with_branch_issues_data,
)

from utils.prompts.pr_reconciliation import (
    build_pr_reconciliation_prompt as _build_pr_reconciliation_prompt,
)

from utils.prompts.helpers import (
    build_legacy_rag_section,
    build_structured_rag_section,
    build_context_section,
    get_additional_instructions,
)


class PromptBuilder:
    """
    Static prompt builder class that delegates to modular prompt implementations.
    Maintained for backward compatibility.
    """
    
    @staticmethod
    def build_first_review_prompt(
        pr_metadata: Dict[str, Any], 
        rag_context: Dict[str, Any] = None,
        structured_context: Optional[str] = None
    ) -> str:
        """Build prompt for the first review of a pull request."""
        return _build_first_review_prompt(pr_metadata, rag_context, structured_context)

    @staticmethod
    def build_review_prompt_with_previous_analysis_data(
        pr_metadata: Dict[str, Any], 
        rag_context: Dict[str, Any] = None,
        structured_context: Optional[str] = None
    ) -> str:
        """Build prompt for reviewing a PR with previous analysis data."""
        return _build_review_prompt_with_previous_analysis_data(pr_metadata, rag_context, structured_context)

    @staticmethod
    def build_branch_review_prompt_with_branch_issues_data(
        branch_metadata: Dict[str, Any], 
        rag_context: Dict[str, Any] = None,
        structured_context: Optional[str] = None
    ) -> str:
        """Build prompt for branch reconciliation."""
        return _build_branch_review_prompt_with_branch_issues_data(branch_metadata, rag_context, structured_context)

    @staticmethod
    def build_direct_first_review_prompt(
        pr_metadata: Dict[str, Any],
        diff_content: str,
        rag_context: Dict[str, Any] = None,
        structured_context: Optional[str] = None
    ) -> str:
        """Build prompt for review with embedded diff - first review."""
        return _build_direct_first_review_prompt(pr_metadata, diff_content, rag_context, structured_context)

    @staticmethod
    def build_direct_review_prompt_with_previous_analysis(
        pr_metadata: Dict[str, Any],
        diff_content: str,
        rag_context: Dict[str, Any] = None,
        structured_context: Optional[str] = None
    ) -> str:
        """Build prompt for direct review mode with previous analysis data."""
        return _build_direct_review_prompt_with_previous_analysis(pr_metadata, diff_content, rag_context, structured_context)

    @staticmethod
    def build_pr_reconciliation_prompt(
        pr_metadata: Dict[str, Any],
        diff_content: str,
        existing_issues: List[Dict[str, Any]],
        rag_context: Dict[str, Any] = None,
        structured_context: Optional[str] = None
    ) -> str:
        """Build prompt for PR reconciliation - checking if PR fixes existing issues."""
        return _build_pr_reconciliation_prompt(pr_metadata, diff_content, existing_issues, rag_context, structured_context)

    @staticmethod
    def get_additional_instructions() -> str:
        """Get additional instructions for the MCP agent."""
        return get_additional_instructions()
    
    # Keep these static method aliases for backward compatibility
    @staticmethod
    def _build_legacy_rag_section(rag_context: Dict[str, Any]) -> str:
        """Build legacy RAG section for backward compatibility."""
        return build_legacy_rag_section(rag_context)
    
    @staticmethod
    def build_structured_rag_section(
        rag_context: Dict[str, Any],
        max_chunks: int = 5,
        token_budget: int = 4000
    ) -> str:
        """Build a structured RAG section with priority markers."""
        return build_structured_rag_section(rag_context, max_chunks, token_budget)
