"""
Prompt templates for CodeCrow MCP client.

This package contains modular prompt templates for different analysis types:
- pr_review: Pull request code review prompts
- branch_reconciliation: Branch issue reconciliation prompts  
- pr_reconciliation: PR-based issue reconciliation prompts
- constants: Shared constants and instruction templates
"""

from .constants import (
    ISSUE_CATEGORIES,
    SUGGESTED_FIX_DIFF_FORMAT,
    LOST_IN_MIDDLE_INSTRUCTIONS,
    JSON_RESPONSE_FORMAT,
    EFFICIENCY_INSTRUCTIONS,
    LINE_NUMBER_INSTRUCTIONS,
    ANALYSIS_FOCUS,
)

from .pr_review import (
    build_first_review_prompt,
    build_review_prompt_with_previous_analysis_data,
    build_direct_first_review_prompt,
    build_direct_review_prompt_with_previous_analysis,
)

from .branch_reconciliation import (
    build_branch_review_prompt_with_branch_issues_data,
)

from .pr_reconciliation import (
    build_pr_reconciliation_prompt,
)

from .helpers import (
    build_legacy_rag_section,
    build_structured_rag_section,
    build_context_section,
    get_additional_instructions,
)

__all__ = [
    # Constants
    'ISSUE_CATEGORIES',
    'SUGGESTED_FIX_DIFF_FORMAT', 
    'LOST_IN_MIDDLE_INSTRUCTIONS',
    'JSON_RESPONSE_FORMAT',
    'EFFICIENCY_INSTRUCTIONS',
    'LINE_NUMBER_INSTRUCTIONS',
    'ANALYSIS_FOCUS',
    # PR Review
    'build_first_review_prompt',
    'build_review_prompt_with_previous_analysis_data',
    'build_direct_first_review_prompt',
    'build_direct_review_prompt_with_previous_analysis',
    # Branch Reconciliation
    'build_branch_review_prompt_with_branch_issues_data',
    # PR Reconciliation
    'build_pr_reconciliation_prompt',
    # Helpers
    'build_legacy_rag_section',
    'build_structured_rag_section',
    'build_context_section',
    'get_additional_instructions',
]
