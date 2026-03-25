"""
QA Auto-Documentation Router.

Generates QA-oriented documentation for PR changes, intended to be posted
as a comment on the linked task management ticket (e.g., Jira).
"""
import logging
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Literal

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
    # Per-request AI credentials (from project's AI connection)
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    ai_api_key: Optional[str] = None
    # Raw PR diff from VCS platform — critical context for QA documentation
    diff: Optional[str] = None


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
    1. Receives analysis summary, task context, and template configuration.
    2. Determines if documentation is needed (LLM decides).
    3. Generates structured QA documentation using the specified template mode.
    4. Returns the document text ready to be posted as a task comment.
    """
    logger.info(
        "QA documentation request: project=%s, pr=#%s, mode=%s",
        payload.project_name, payload.pr_number, payload.template_mode,
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
        diff=payload.diff,
    )
    
    return QaDocumentationResponse(
        documentation=result.get("documentation"),
        documentation_needed=result.get("documentation_needed", True),
        template_mode_used=payload.template_mode,
    )
