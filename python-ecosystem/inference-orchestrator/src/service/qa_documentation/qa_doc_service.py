"""
QA Documentation Generation Service.

Stateless service — each call creates its own LLM instance from the
credentials stored in the inference-orchestrator environment (server-level AI key).
This endpoint is called by the Java pipeline-agent, which does NOT forward
per-user AI keys; instead the orchestrator uses its own configured provider.
"""

import os
import re
import logging
from typing import Dict, Any, Optional
from dotenv import load_dotenv

from llm.llm_factory import LLMFactory
from utils.prompts.constants_qa_doc import (
    QA_DOC_SYSTEM_PROMPT,
    QA_DOC_RELEVANCE_CHECK_PROMPT,
    QA_DOC_RAW_PROMPT,
    QA_DOC_BASE_PROMPT,
    QA_DOC_CUSTOM_PROMPT,
    QA_DOC_COMMENT_FOOTER,
    QA_DOC_COMMENT_FOOTER_TEMPLATE,
    QA_DOC_UPDATE_PREAMBLE,
)

logger = logging.getLogger(__name__)


class QaDocumentationService:
    """
    Generates QA-oriented documentation for completed PR analyses.

    Flow:
    1. Relevance check — LLM decides if changes warrant QA documentation.
    2. Documentation generation — Uses one of three template modes (RAW / BASE / CUSTOM).
    3. Footer appended — A hidden marker for find-and-update on subsequent runs.
    """

    # Timeout for individual LLM calls (seconds)
    LLM_TIMEOUT_SECONDS = int(os.environ.get("QA_DOC_LLM_TIMEOUT", "120"))

    def __init__(self):
        load_dotenv(interpolate=False)
        self._ai_provider = os.environ.get("QA_DOC_AI_PROVIDER", os.environ.get("AI_PROVIDER", "openrouter"))
        self._ai_model = os.environ.get("QA_DOC_AI_MODEL", os.environ.get("AI_MODEL", "google/gemini-2.0-flash"))
        self._ai_api_key = os.environ.get("QA_DOC_AI_API_KEY", os.environ.get("AI_API_KEY", ""))

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
        diff: Optional[str] = None,
        previous_documentation: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate QA documentation for a completed analysis.

        When ``previous_documentation`` is provided (from an earlier PR on the
        same task), the LLM is instructed to produce a consolidated document
        that merges the old and new content.

        Returns:
            {"documentation_needed": bool, "documentation": str | None}
        """
        placeholders = self._build_placeholders(
            project_name=project_name,
            pr_number=pr_number,
            issues_found=issues_found,
            files_analyzed=files_analyzed,
            pr_metadata=pr_metadata,
            task_context=task_context,
            diff=diff,
        )

        llm = self._create_llm(
            ai_provider=ai_provider,
            ai_model=ai_model,
            ai_api_key=ai_api_key,
        )

        # Step 1: Relevance check
        if not await self._is_documentation_needed(llm, placeholders):
            logger.info("QA documentation not needed for PR #%s (project %s)", pr_number, project_name)
            return {"documentation_needed": False, "documentation": None}

        # Step 2: Generate documentation (with previous context if available)
        documentation = await self._generate_documentation(
            llm=llm,
            template_mode=template_mode.upper(),
            custom_template=custom_template,
            placeholders=placeholders,
            previous_documentation=previous_documentation,
        )

        # Step 3: Build footer with PR tracking
        # Extract previously documented PR numbers from the old comment marker
        documented_prs = self._extract_documented_prs(previous_documentation)
        if pr_number:
            documented_prs.add(pr_number)
        pr_numbers_str = ",".join(str(p) for p in sorted(documented_prs))
        footer = QA_DOC_COMMENT_FOOTER_TEMPLATE.format(pr_numbers=pr_numbers_str) if pr_numbers_str else QA_DOC_COMMENT_FOOTER
        documentation = documentation.rstrip() + footer

        logger.info(
            "QA documentation generated for PR #%s (project %s), mode=%s, length=%d, documented_prs=%s",
            pr_number, project_name, template_mode, len(documentation), pr_numbers_str,
        )
        return {"documentation_needed": True, "documentation": documentation}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _create_llm(self, ai_provider=None, ai_model=None, ai_api_key=None):
        """Create LLM instance. Prefers per-request credentials over server-level env vars."""
        provider = ai_provider or self._ai_provider
        model = ai_model or self._ai_model
        api_key = ai_api_key or self._ai_api_key
        return LLMFactory.create_llm(
            ai_model=model,
            ai_provider=provider,
            ai_api_key=api_key,
            temperature=0.3,  # Slightly creative for documentation
        )

    def _build_placeholders(
        self,
        project_name: str,
        pr_number: Optional[int],
        issues_found: int,
        files_analyzed: int,
        pr_metadata: Dict[str, Any],
        task_context: Optional[Dict[str, str]],
        diff: Optional[str] = None,
    ) -> Dict[str, str]:
        """Build the placeholder dictionary used for prompt formatting."""
        task_ctx = task_context or {}
        return {
            "project_name": project_name or "Unknown",
            "pr_number": str(pr_number) if pr_number else "N/A",
            "task_key": task_ctx.get("task_key", "N/A"),
            "task_summary": task_ctx.get("task_summary", "N/A"),
            "source_branch": pr_metadata.get("sourceBranch", "N/A"),
            "target_branch": pr_metadata.get("targetBranch", "N/A"),
            "pr_title": pr_metadata.get("prTitle", "N/A"),
            "pr_description": self._truncate(pr_metadata.get("prDescription", ""), 500),
            "issues_found": str(issues_found),
            "files_analyzed": str(files_analyzed),
            "analysis_summary": pr_metadata.get("analysisSummary", "No analysis summary available."),
            "diff": diff or "No diff available.",
        }

    async def _is_documentation_needed(self, llm, placeholders: Dict[str, str]) -> bool:
        """Ask the LLM whether QA documentation is warranted."""
        try:
            prompt = QA_DOC_RELEVANCE_CHECK_PROMPT.format(**placeholders)
            response = await llm.ainvoke(prompt)
            content = self._extract_text(response)
            answer = content.strip().upper()
            logger.debug("Relevance check answer: %s", answer)
            return answer.startswith("YES")
        except Exception as e:
            logger.warning("Relevance check failed, defaulting to YES: %s", e)
            return True  # If in doubt, generate the documentation

    @staticmethod
    def _extract_documented_prs(previous_documentation: Optional[str]) -> set:
        """Extract PR numbers from the ``<!-- codecrow-qa-autodoc:prs=... -->`` marker
        embedded in a previous QA doc comment.

        Returns a set of ints (empty if no marker or no previous doc).
        """
        if not previous_documentation:
            return set()
        match = re.search(r'<!-- codecrow-qa-autodoc:prs=([\d,]+) -->', previous_documentation)
        if match:
            try:
                return {int(p) for p in match.group(1).split(',') if p.strip()}
            except ValueError:
                return set()
        return set()

    async def _generate_documentation(
        self,
        llm,
        template_mode: str,
        custom_template: Optional[str],
        placeholders: Dict[str, str],
        previous_documentation: Optional[str] = None,
    ) -> str:
        """Generate the documentation using the appropriate template mode.

        When ``previous_documentation`` is provided, an update preamble is
        prepended to the prompt so the LLM merges old + new into a single
        consolidated document.
        """
        if template_mode == "RAW":
            prompt_template = QA_DOC_RAW_PROMPT
        elif template_mode == "CUSTOM" and custom_template:
            prompt_template = QA_DOC_CUSTOM_PROMPT
            placeholders = {**placeholders, "custom_template": custom_template}
        else:
            # Default to BASE
            prompt_template = QA_DOC_BASE_PROMPT

        user_prompt = prompt_template.format(**placeholders)

        # If previous documentation exists from earlier PRs, inject update preamble
        if previous_documentation and previous_documentation.strip():
            update_preamble = QA_DOC_UPDATE_PREAMBLE.format(
                previous_documentation=previous_documentation,
                pr_number=placeholders.get("pr_number", "N/A"),
            )
            user_prompt = update_preamble + "\n\n" + user_prompt
            logger.info(
                "Injecting update preamble — previous doc length=%d",
                len(previous_documentation),
            )

        messages = [
            {"role": "system", "content": QA_DOC_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        response = await llm.ainvoke(messages)
        content = self._extract_text(response)

        if not content or len(content.strip()) < 50:
            logger.warning("LLM returned empty/short documentation, falling back to BASE")
            if template_mode != "BASE":
                return await self._generate_documentation(llm, "BASE", None, placeholders)
            return "⚠️ QA documentation could not be generated for this PR."

        return content

    @staticmethod
    def _extract_text(response) -> str:
        """Extract text content from LangChain response object.

        Google Gemini models may return ``content`` as a list of content blocks
        (e.g. ``[{"type": "text", "text": "..."}]``) instead of a plain string.
        This helper normalises both cases to a single string.
        """
        if hasattr(response, "content"):
            content = response.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # Concatenate text parts from content blocks
                parts = []
                for block in content:
                    if isinstance(block, str):
                        parts.append(block)
                    elif isinstance(block, dict) and "text" in block:
                        parts.append(block["text"])
                return "\n".join(parts)
            return str(content)
        if isinstance(response, str):
            return response
        return str(response)

    @staticmethod
    def _truncate(text: str, max_length: int) -> str:
        """Truncate text to max_length, appending ellipsis if truncated."""
        if not text or len(text) <= max_length:
            return text or ""
        return text[:max_length] + "…"
