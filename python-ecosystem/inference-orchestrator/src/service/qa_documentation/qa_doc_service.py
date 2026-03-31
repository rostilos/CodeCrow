"""
QA Documentation Generation Service.

Thin coordinator that creates the LLM instance and delegates to
the multi-stage QaDocOrchestrator for actual generation.

The orchestrator handles:
- Relevance checking (should docs be generated?)
- Multi-stage ULTRATHINKING pipeline (for large PRs with enrichment)
- Single-pass fallback (for small PRs or missing enrichment)
- Footer/marker management
"""

import os
import logging
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv

from llm.llm_factory import LLMFactory
from model.enrichment import PrEnrichmentDataDto
from service.qa_documentation.qa_doc_orchestrator import QaDocOrchestrator
from service.rag.rag_client import RagClient

logger = logging.getLogger(__name__)


class QaDocumentationService:
    """
    Generates QA-oriented documentation for completed PR analyses.

    Creates the LLM and RAG client, then delegates to QaDocOrchestrator
    which runs the 3-stage pipeline or single-pass depending on PR size.
    """

    def __init__(self):
        load_dotenv(interpolate=False)
        self._ai_provider = os.environ.get("QA_DOC_AI_PROVIDER", os.environ.get("AI_PROVIDER", "openrouter"))
        self._ai_model = os.environ.get("QA_DOC_AI_MODEL", os.environ.get("AI_MODEL", "google/gemini-2.0-flash"))
        self._ai_api_key = os.environ.get("QA_DOC_AI_API_KEY", os.environ.get("AI_API_KEY", ""))
        self._rag_pipeline_url = os.environ.get("RAG_PIPELINE_URL", "http://rag-pipeline:8020")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate(
        self,
        project_id: int,
        project_name: str,
        pr_number: Optional[int],
        issues_found: int,
        files_analyzed: int,
        pr_metadata: Dict[str, Any],
        template_mode: str,
        custom_template: Optional[str],
        task_context: Optional[Dict[str, str]],
        ai_provider: Optional[str] = None,
        ai_model: Optional[str] = None,
        ai_api_key: Optional[str] = None,
        ai_base_url: Optional[str] = None,
        diff: Optional[str] = None,
        delta_diff: Optional[str] = None,
        previous_documentation: Optional[str] = None,
        is_same_pr_rerun: bool = False,
        enrichment_data: Optional[PrEnrichmentDataDto] = None,
        changed_file_paths: Optional[List[str]] = None,
        workspace_slug: Optional[str] = None,
        repo_slug: Optional[str] = None,
        source_branch: Optional[str] = None,
        target_branch: Optional[str] = None,
        vcs_provider: Optional[str] = None,
        output_language: Optional[str] = "English",
    ) -> Dict[str, Any]:
        """
        Generate QA documentation for a completed analysis.

        Delegates to QaDocOrchestrator which decides between multi-stage
        and single-pass based on PR size and available data.

        Returns:
            {"documentation_needed": bool, "documentation": str | None}
        """
        llm = self._create_llm(
            ai_provider=ai_provider,
            ai_model=ai_model,
            ai_api_key=ai_api_key,
            ai_base_url=ai_base_url,
            max_tokens=16_384,  # QA docs need room for structured JSON output
        )

        rag_client = self._create_rag_client()

        orchestrator = QaDocOrchestrator(
            llm=llm,
            rag_client=rag_client,
        )

        result = await orchestrator.run(
            project_name=project_name,
            pr_number=pr_number,
            issues_found=issues_found,
            files_analyzed=files_analyzed,
            pr_metadata=pr_metadata,
            template_mode=template_mode,
            custom_template=custom_template,
            task_context=task_context,
            diff=diff,
            delta_diff=delta_diff,
            enrichment_data=enrichment_data,
            changed_file_paths=changed_file_paths,
            previous_documentation=previous_documentation,
            is_same_pr_rerun=is_same_pr_rerun,
            workspace_slug=workspace_slug,
            repo_slug=repo_slug,
            source_branch=source_branch,
            target_branch=target_branch,
            vcs_provider=vcs_provider,
            output_language=output_language,
        )

        logger.info(
            "QA documentation %s for PR #%s (project %s), length=%d",
            "generated" if result.get("documentation_needed") else "skipped",
            pr_number, project_name,
            len(result.get("documentation") or ""),
        )
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _create_llm(self, ai_provider=None, ai_model=None, ai_api_key=None, ai_base_url=None, max_tokens=None):
        """Create LLM instance. Prefers per-request credentials over server-level env vars."""
        provider = ai_provider or self._ai_provider
        model = ai_model or self._ai_model
        api_key = ai_api_key or self._ai_api_key
        return LLMFactory.create_llm(
            ai_model=model,
            ai_provider=provider,
            ai_api_key=api_key,
            temperature=0.3,  # Slightly creative for documentation
            ai_base_url=ai_base_url,
            max_tokens=max_tokens,
        )

    def _create_rag_client(self) -> Optional[RagClient]:
        """Create RAG client if the RAG pipeline URL is configured."""
        try:
            if self._rag_pipeline_url:
                return RagClient(base_url=self._rag_pipeline_url)
        except Exception as e:
            logger.warning("Failed to create RAG client (non-critical): %s", e)
        return None
