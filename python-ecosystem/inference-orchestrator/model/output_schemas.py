"""
Output Schemas for MCP Agent.

These Pydantic models are used with MCPAgent's output_schema parameter
to ensure structured JSON output from the LLM.
"""

from typing import Optional, List, Union
from pydantic import BaseModel, Field, field_validator


class CodeReviewIssue(BaseModel):
    """Schema for a single code review issue."""
    # Optional issue identifier (preserve DB/client-side ids for reconciliation)
    id: Optional[str] = Field(default=None, description="Optional issue id to link to existing issues")
    severity: str = Field(description="Issue severity: HIGH, MEDIUM, LOW, or INFO")
    category: str = Field(description="Issue category: SECURITY, PERFORMANCE, CODE_QUALITY, BUG_RISK, STYLE, DOCUMENTATION, BEST_PRACTICES, ERROR_HANDLING, TESTING, or ARCHITECTURE")
    file: str = Field(description="File path where the issue is located")
    line: Union[int, str] = Field(description="Line number where the issue starts (integer, e.g. 42)")

    @field_validator('line', mode='before')
    @classmethod
    def parse_line_to_int(cls, v) -> int:
        """Normalize line to an integer. Handles range strings like '42-45' by taking the start."""
        if v is None:
            return 0
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
        if isinstance(v, str):
            s = v.strip()
            # Handle range "42-45" → 42
            dash_idx = s.find('-')
            if dash_idx > 0:
                s = s[:dash_idx].strip()
            try:
                return int(s)
            except ValueError:
                return 0
        return 0
    title: Optional[str] = Field(default=None, description="Short issue title, max 10 words (e.g., 'Missing null check in user lookup')")
    reason: str = Field(description="Detailed explanation of the issue, evidence, and impact")
    suggestedFixDescription: str = Field(description="Description of the suggested fix")
    suggestedFixDiff: Optional[str] = Field(default=None, description="Optional unified diff format patch for the fix")
    isResolved: bool = Field(default=False, description="Whether this issue from previous analysis is resolved")
    # Resolution tracking fields
    resolutionExplanation: Optional[str] = Field(default=None, description="Explanation of how the issue was resolved (separate from original reason)")
    resolvedInCommit: Optional[str] = Field(default=None, description="Commit hash where the issue was resolved")
    # Additional fields preserved from previous issues during reconciliation
    visibility: Optional[str] = Field(default=None, description="Issue visibility status")
    codeSnippet: str = Field(default="", description="REQUIRED: The exact single line of source code where the issue occurs, copied VERBATIM from the diff or file content. Used for content-based line anchoring. Must match an actual line in the file.")

    @field_validator('codeSnippet', mode='before')
    @classmethod
    def coerce_code_snippet(cls, v) -> str:
        """Coerce None to empty string so reconciliation code can pass None safely."""
        if v is None:
            return ""
        return str(v).strip()


class CodeReviewOutput(BaseModel):
    """Schema for the complete code review output."""
    comment: str = Field(description="High-level summary of the PR analysis with key findings and recommendations")
    issues: List[CodeReviewIssue] = Field(default_factory=list, description="List of identified issues in the code")


class SummarizeOutput(BaseModel):
    """Schema for PR summarization output."""
    summary: str = Field(description="Comprehensive summary of the PR changes, purpose, and impact")
    diagram: str = Field(default="", description="Visual diagram of the changes (Mermaid or ASCII format)")
    diagramType: str = Field(default="MERMAID", description="Type of diagram: MERMAID or ASCII")


class DeduplicatedIssueList(BaseModel):
    """Schema for LLM-driven deduplication result.

    The LLM returns the *indices* (0-based) of issues that should be **kept**.
    Any index NOT present in ``kept_indices`` is considered a duplicate and will
    be dropped.
    """
    kept_indices: List[int] = Field(
        description=(
            "0-based indices of issues to KEEP from the provided list.  "
            "Omit any index whose issue is a semantic duplicate of another "
            "kept issue."
        )
    )


class AskOutput(BaseModel):
    """Schema for ask command output."""
    answer: str = Field(description="Well-formatted markdown answer to the user's question")
