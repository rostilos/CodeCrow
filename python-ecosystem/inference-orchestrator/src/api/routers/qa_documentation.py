"""
QA Auto-Documentation Router.

Generates QA-oriented documentation for PR changes, intended to be posted
as a comment on the linked task management ticket (e.g., Jira).

Supports both single-pass (legacy / small PRs) and multi-stage ULTRATHINKING
pipeline (large PRs with enrichment data).
"""
import logging
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List, Literal

from model.enrichment import PrEnrichmentDataDto
from service.qa_documentation.qa_doc_service import QaDocumentationService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["qa-documentation"])


class QaDocumentationRequest(BaseModel):
    """Request payload for QA documentation generation."""
    project_id: int
    project_name: str
    pr_number: Optional[int] = None
    issues_found: int = 0
    files_analyzed: int = 0
    pr_metadata: Optional[Dict[str, Any]] = None
    template_mode: Literal["RAW", "BASE", "CUSTOM"] = "BASE"
    custom_template: Optional[str] = None
    task_context: Optional[Dict[str, str]] = None

    # Output language for generated documentation (e.g. "English", "Ukrainian", "Spanish")
    output_language: Optional[str] = "English"

    # Per-request AI credentials (from project's AI connection)
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    ai_api_key: Optional[str] = None
    ai_base_url: Optional[str] = None

    # Raw PR diff from VCS platform — NO truncation, full diff
    diff: Optional[str] = None

    # Delta diff for same-PR re-runs (only new changes since last analysis)
    delta_diff: Optional[str] = None

    # Previous QA documentation from earlier PRs on the same task (for multi-PR accumulation)
    previous_documentation: Optional[str] = None

    # Same-PR re-run flag (from server-side qa_doc_state table)
    is_same_pr_rerun: bool = False

    # Enrichment data (file contents + AST metadata + dependency graph)
    enrichment_data: Optional[PrEnrichmentDataDto] = None

    # Changed file paths extracted from diff
    changed_file_paths: Optional[List[str]] = None

    # VCS connection info (for RAG queries)
    vcs_provider: Optional[str] = None
    workspace_slug: Optional[str] = None
    repo_slug: Optional[str] = None
    source_branch: Optional[str] = None
    target_branch: Optional[str] = None

    # OAuth credentials (for Python-side RAG/VCS access)
    oauth_key: Optional[str] = None
    oauth_secret: Optional[str] = None
    bearer_token: Optional[str] = None


class QaDocumentationResponse(BaseModel):
    """Response containing the generated QA documentation."""
    documentation: Optional[str] = None
    documentation_needed: bool = True
    template_mode_used: Literal["RAW", "BASE", "CUSTOM"] = "BASE"


@router.post("/qa-documentation", response_model=QaDocumentationResponse)
async def generate_qa_documentation(
    request: Request,
    payload: QaDocumentationRequest,
):
    """
    Generate QA auto-documentation for a completed PR analysis.

    The endpoint:
    1. Receives analysis summary, task context, diff, enrichment data, and template configuration.
    2. Determines if documentation is needed (LLM decides).
    3. For large PRs: runs 3-stage ULTRATHINKING pipeline (batch → cross-impact → aggregate).
    4. For small PRs: runs single-pass generation.
    5. Returns the document text ready to be posted as a task comment.
    """
    logger.info(
        "QA documentation request: project=%s, pr=#%s, mode=%s, diff_size=%d, enrichment=%s, delta=%s",
        payload.project_name, payload.pr_number, payload.template_mode,
        len(payload.diff or ""),
        "yes" if payload.enrichment_data and payload.enrichment_data.has_data() else "no",
        "yes" if payload.delta_diff else "no",
    )

    qa_service = QaDocumentationService()

    result = await qa_service.generate(
        project_id=payload.project_id,
        project_name=payload.project_name,
        pr_number=payload.pr_number,
        issues_found=payload.issues_found,
        files_analyzed=payload.files_analyzed,
        pr_metadata=payload.pr_metadata or {},
        template_mode=payload.template_mode,
        custom_template=payload.custom_template,
        task_context=payload.task_context,
        ai_provider=payload.ai_provider,
        ai_model=payload.ai_model,
        ai_api_key=payload.ai_api_key,
        ai_base_url=payload.ai_base_url,
        diff=payload.diff,
        delta_diff=payload.delta_diff,
        previous_documentation=payload.previous_documentation,
        is_same_pr_rerun=payload.is_same_pr_rerun,
        enrichment_data=payload.enrichment_data,
        changed_file_paths=payload.changed_file_paths,
        workspace_slug=payload.workspace_slug,
        repo_slug=payload.repo_slug,
        source_branch=payload.source_branch,
        target_branch=payload.target_branch,
        vcs_provider=payload.vcs_provider,
        output_language=payload.output_language,
    )

    return QaDocumentationResponse(
        documentation=result.get("documentation"),
        documentation_needed=result.get("documentation_needed", True),
        template_mode_used=payload.template_mode,
    )
