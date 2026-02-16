"""
Output Schemas for MCP Agent.

These Pydantic models are used with MCPAgent's output_schema parameter
to ensure structured JSON output from the LLM.
"""

from typing import Optional, List
from pydantic import BaseModel, Field


class CodeReviewIssue(BaseModel):
    """Schema for a single code review issue."""
    # Optional issue identifier (preserve DB/client-side ids for reconciliation)
    id: Optional[str] = Field(default=None, description="Optional issue id to link to existing issues")
    severity: str = Field(description="Issue severity: HIGH, MEDIUM, LOW, or INFO")
    category: str = Field(description="Issue category: SECURITY, PERFORMANCE, CODE_QUALITY, BUG_RISK, STYLE, DOCUMENTATION, BEST_PRACTICES, ERROR_HANDLING, TESTING, or ARCHITECTURE")
    file: str = Field(description="File path where the issue is located")
    line: str = Field(description="Line number or range (e.g., '42' or '42-45')")
    reason: str = Field(description="Clear explanation of the issue")
    suggestedFixDescription: str = Field(description="Description of the suggested fix")
    suggestedFixDiff: Optional[str] = Field(default=None, description="Optional unified diff format patch for the fix")
    isResolved: bool = Field(default=False, description="Whether this issue from previous analysis is resolved")
    # Resolution tracking fields
    resolutionExplanation: Optional[str] = Field(default=None, description="Explanation of how the issue was resolved (separate from original reason)")
    resolvedInCommit: Optional[str] = Field(default=None, description="Commit hash where the issue was resolved")
    # Additional fields preserved from previous issues during reconciliation
    visibility: Optional[str] = Field(default=None, description="Issue visibility status")
    codeSnippet: Optional[str] = Field(default=None, description="Code snippet associated with the issue")


class CodeReviewOutput(BaseModel):
    """Schema for the complete code review output."""
    comment: str = Field(description="High-level summary of the PR analysis with key findings and recommendations")
    issues: List[CodeReviewIssue] = Field(default_factory=list, description="List of identified issues in the code")


class SummarizeOutput(BaseModel):
    """Schema for PR summarization output."""
    summary: str = Field(description="Comprehensive summary of the PR changes, purpose, and impact")
    diagram: str = Field(default="", description="Visual diagram of the changes (Mermaid or ASCII format)")
    diagramType: str = Field(default="MERMAID", description="Type of diagram: MERMAID or ASCII")


class AskOutput(BaseModel):
    """Schema for ask command output."""
    answer: str = Field(description="Well-formatted markdown answer to the user's question")
