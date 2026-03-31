"""
QA Documentation Orchestrator — Multi-Stage ULTRATHINKING Pipeline.

3-stage pipeline for generating high-quality QA documentation:
- Stage 1: Batch-level analysis of diff hunks with full file context
- Stage 2: Cross-file impact analysis (how changes interact for testing)
- Stage 3: Aggregation into polished QA document (or delta update for re-runs)

Extends BaseOrchestrator for shared RAG, batching, and LLM infrastructure.
"""
import asyncio
import json
import logging
from typing import Dict, Any, List, Optional, Callable, Set

from model.enrichment import PrEnrichmentDataDto
from service.qa_documentation.base_orchestrator import (
    BaseOrchestrator,
    emit_status,
    emit_progress,
    emit_error,
)
from utils.task_context_builder import build_task_context_for_prompt
from utils.prompts.constants_qa_doc import (
    QA_DOC_SYSTEM_PROMPT,
    QA_DOC_RELEVANCE_CHECK_PROMPT,
    QA_DOC_RAW_PROMPT,
    QA_DOC_BASE_PROMPT,
    QA_DOC_CUSTOM_PROMPT,
    QA_DOC_UPDATE_PREAMBLE,
    QA_DOC_COMMENT_FOOTER,
    QA_DOC_COMMENT_FOOTER_TEMPLATE,
    QA_STAGE_1_BATCH_PROMPT,
    QA_STAGE_2_CROSS_IMPACT_PROMPT,
    QA_STAGE_3_AGGREGATION_PROMPT,
    QA_STAGE_3_DELTA_PROMPT,
    QA_STAGE_3_PREVIOUS_DOC_SECTION,
)

logger = logging.getLogger(__name__)

# Threshold: if total diff is under this many chars, skip multi-stage and do single-pass
SINGLE_PASS_THRESHOLD = 8_000  # ~2k tokens — small PRs don't need multi-stage


class QaDocOrchestrator(BaseOrchestrator):
    """
    Multi-stage QA documentation pipeline.

    Stage 1: Per-batch file analysis (parallel-safe, dependency-grouped batches)
    Stage 2: Cross-file impact analysis
    Stage 3: Final aggregation or delta update
    """

    def __init__(
        self,
        llm,
        rag_client=None,
        event_callback: Optional[Callable[[Dict], None]] = None,
    ):
        super().__init__(llm, rag_client, event_callback)

    async def run(
        self,
        *,
        project_name: str,
        pr_number: Optional[int],
        issues_found: int,
        files_analyzed: int,
        pr_metadata: Dict[str, Any],
        template_mode: str,
        custom_template: Optional[str],
        task_context: Optional[Dict[str, str]],
        diff: Optional[str],
        delta_diff: Optional[str],
        enrichment_data: Optional[PrEnrichmentDataDto],
        changed_file_paths: Optional[List[str]],
        previous_documentation: Optional[str],
        is_same_pr_rerun: bool,
        workspace_slug: Optional[str] = None,
        repo_slug: Optional[str] = None,
        source_branch: Optional[str] = None,
        target_branch: Optional[str] = None,
        vcs_provider: Optional[str] = None,
        output_language: Optional[str] = "English",
    ) -> Dict[str, Any]:
        """
        Main entry point — runs the multi-stage QA doc pipeline.

        Returns:
            {"documentation_needed": bool, "documentation": str | None}
        """
        # Build base placeholders (shared across all prompts)
        task_ctx = task_context or {}
        task_context_block = build_task_context_for_prompt(task_context)
        placeholders = self._build_placeholders(
            project_name=project_name,
            pr_number=pr_number,
            issues_found=issues_found,
            files_analyzed=files_analyzed,
            pr_metadata=pr_metadata,
            task_context_dict=task_ctx,
            task_context_block=task_context_block,
            diff=diff,
            source_branch=source_branch or pr_metadata.get("sourceBranch", "N/A"),
            target_branch=target_branch or pr_metadata.get("targetBranch", "N/A"),
            output_language=output_language,
        )

        # ── Relevance gate ───────────────────────────────────────────
        emit_status(self.event_callback, "relevance_check", "Checking if QA documentation is needed...")
        if not await self._is_documentation_needed(placeholders):
            logger.info("QA documentation not needed for PR #%s (project %s)", pr_number, project_name)
            return {"documentation_needed": False, "documentation": None}

        # ── Decide: multi-stage vs. single-pass ──────────────────────
        effective_diff = diff or ""
        use_multi_stage = len(effective_diff) > SINGLE_PASS_THRESHOLD and changed_file_paths

        if use_multi_stage:
            logger.info(
                "QA doc: using multi-stage pipeline (diff=%d chars, %d files)",
                len(effective_diff), len(changed_file_paths or []),
            )
            documentation = await self._run_multi_stage(
                placeholders=placeholders,
                diff=effective_diff,
                delta_diff=delta_diff,
                enrichment_data=enrichment_data,
                changed_file_paths=changed_file_paths or [],
                previous_documentation=previous_documentation,
                is_same_pr_rerun=is_same_pr_rerun,
                workspace_slug=workspace_slug,
                repo_slug=repo_slug,
                pr_number=pr_number,
                source_branch=source_branch,
            )
        else:
            logger.info(
                "QA doc: small PR — using single-pass (diff=%d chars)",
                len(effective_diff),
            )
            documentation = await self._run_single_pass(
                template_mode=template_mode.upper(),
                custom_template=custom_template,
                placeholders=placeholders,
                previous_documentation=previous_documentation,
            )

        if not documentation or len(documentation.strip()) < 50:
            logger.warning("QA doc generation produced empty/short output")
            return {"documentation_needed": False, "documentation": None}

        # ── Footer with PR tracking ──────────────────────────────────
        documented_prs = self._extract_documented_prs(previous_documentation)
        if pr_number:
            documented_prs.add(pr_number)
        pr_numbers_str = ",".join(str(p) for p in sorted(documented_prs))
        footer = QA_DOC_COMMENT_FOOTER_TEMPLATE.format(pr_numbers=pr_numbers_str) if pr_numbers_str else QA_DOC_COMMENT_FOOTER
        documentation = documentation.rstrip() + footer

        logger.info(
            "QA doc generated for PR #%s (project %s), length=%d, mode=%s",
            pr_number, project_name, len(documentation),
            "multi-stage" if use_multi_stage else "single-pass",
        )
        return {"documentation_needed": True, "documentation": documentation}

    # ==================================================================
    # Multi-stage pipeline
    # ==================================================================

    async def _run_multi_stage(
        self,
        *,
        placeholders: Dict[str, str],
        diff: str,
        delta_diff: Optional[str],
        enrichment_data: Optional[PrEnrichmentDataDto],
        changed_file_paths: List[str],
        previous_documentation: Optional[str],
        is_same_pr_rerun: bool,
        workspace_slug: Optional[str],
        repo_slug: Optional[str],
        pr_number: Optional[int],
        source_branch: Optional[str],
    ) -> str:
        """Execute the 3-stage ULTRATHINKING pipeline."""
        try:
            # Index files into RAG if available
            if self.rag_client and pr_number and workspace_slug and repo_slug:
                await self.index_pr_files(
                    workspace=workspace_slug,
                    project=repo_slug,
                    pr_number=pr_number,
                    branch=source_branch or "unknown",
                    enrichment_data=enrichment_data,
                    changed_file_paths=changed_file_paths,
                    diff=diff,
                )

            # For same-PR re-runs, analyze the delta diff — but only if
            # it contains actual hunks (@@).  A delta that is truthy but
            # header-only (no @@) would starve the whole pipeline of context.
            has_real_delta = (
                is_same_pr_rerun
                and delta_diff
                and len(delta_diff.strip()) > 100
                and '@@' in delta_diff
            )
            analysis_diff = delta_diff if has_real_delta else diff
            if is_same_pr_rerun and not has_real_delta:
                logger.info(
                    "delta_diff %s — using full diff for analysis",
                    "is empty/header-only" if delta_diff else "not provided",
                )

            # ── STAGE 1: Batch Analysis ──────────────────────────────
            emit_status(self.event_callback, "stage_1_started", "Stage 1: Analyzing file batches...")
            logger.info(
                "Multi-stage pipeline: analysis_diff=%d chars, full_diff=%d chars, "
                "delta_diff=%s, is_same_pr_rerun=%s, files=%d",
                len(analysis_diff) if analysis_diff else 0,
                len(diff) if diff else 0,
                f"{len(delta_diff)} chars" if delta_diff else "None",
                is_same_pr_rerun,
                len(changed_file_paths),
            )
            batches = self.build_dependency_batches(
                changed_file_paths, enrichment_data, analysis_diff,
            )
            stage_1_results = await self._execute_stage_1(
                batches=batches,
                diff=analysis_diff,
                enrichment_data=enrichment_data,
                placeholders=placeholders,
            )
            s1_file_analyses = sum(
                len(r.get("file_analyses", [])) for r in stage_1_results
            )
            s1_errors = sum(1 for r in stage_1_results if r.get("error"))
            logger.info(
                "Stage 1 complete: %d batches, %d file analyses, %d errors",
                len(stage_1_results), s1_file_analyses, s1_errors,
            )
            emit_progress(self.event_callback, 40, f"Stage 1 Complete: {len(stage_1_results)} batch analyses")

            # ── STAGE 2: Cross-Impact Analysis ───────────────────────
            emit_status(self.event_callback, "stage_2_started", "Stage 2: Cross-file impact analysis...")
            stage_2_results = await self._execute_stage_2(
                stage_1_results=stage_1_results,
                enrichment_data=enrichment_data,
                changed_file_paths=changed_file_paths,
                placeholders=placeholders,
            )
            emit_progress(self.event_callback, 70, "Stage 2 Complete: Cross-impact analysis finished")

            # ── STAGE 3: Aggregation / Delta ─────────────────────────
            emit_status(self.event_callback, "stage_3_started", "Stage 3: Generating final document...")
            if is_same_pr_rerun and delta_diff and previous_documentation:
                documentation = await self._execute_stage_3_delta(
                    stage_1_results=stage_1_results,
                    stage_2_results=stage_2_results,
                    delta_diff=delta_diff,
                    previous_documentation=previous_documentation,
                    placeholders=placeholders,
                )
            else:
                documentation = await self._execute_stage_3_aggregation(
                    stage_1_results=stage_1_results,
                    stage_2_results=stage_2_results,
                    previous_documentation=previous_documentation,
                    placeholders=placeholders,
                )
            logger.info(
                "Stage 3 complete: document length=%d chars",
                len(documentation) if documentation else 0,
            )
            emit_progress(self.event_callback, 100, "Stage 3 Complete: Document generated")
            return documentation

        except Exception as e:
            logger.error("Multi-stage QA doc pipeline failed: %s", e, exc_info=True)
            emit_error(self.event_callback, str(e))
            # Fallback to single-pass
            logger.info("Falling back to single-pass after multi-stage failure")
            return await self._run_single_pass(
                template_mode="BASE",
                custom_template=None,
                placeholders=placeholders,
                previous_documentation=previous_documentation,
            )
        finally:
            if workspace_slug and repo_slug:
                await self.cleanup_pr_files(workspace_slug, repo_slug)

    # ── Stage 1: Batch Analysis ──────────────────────────────────────

    async def _execute_stage_1(
        self,
        *,
        batches: List[List[Dict[str, Any]]],
        diff: str,
        enrichment_data: Optional[PrEnrichmentDataDto],
        placeholders: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """Run Stage 1: per-batch file analysis (parallel, max 5 concurrent)."""
        total_batches = len(batches)
        enrichment_lookup = self.build_enrichment_lookup(enrichment_data)

        MAX_CONCURRENCY = 5
        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        completed_count = 0

        # Model input budget: keep conservative to avoid 400 errors
        # from models behind proxies with tighter limits
        MAX_INPUT_CHARS = 200_000

        async def _process_batch(idx: int, batch: List[Dict[str, Any]]) -> Dict[str, Any]:
            nonlocal completed_count
            async with semaphore:
                # Extract file paths for this batch
                batch_files: Set[str] = set()
                for item in batch:
                    fi = item.get("file_info")
                    if fi and hasattr(fi, "path"):
                        batch_files.add(fi.path)

                # Filter diff to only this batch's files
                batch_diff = self.filter_diff_for_files(diff, batch_files) or "(no diff for this batch)"

                # Build file contents section
                file_contents_parts: List[str] = []
                for fp in sorted(batch_files):
                    fc = enrichment_lookup.get(fp, "")
                    if not fc:
                        for ep, ec in enrichment_lookup.items():
                            if fp.endswith(ep) or ep.endswith(fp):
                                fc = ec
                                break
                    if fc:
                        file_contents_parts.append(f"#### {fp}\n```\n{fc}\n```")
                    else:
                        file_contents_parts.append(f"#### {fp}\n(file content not available)")

                file_list = "\n".join(f"- {fp}" for fp in sorted(batch_files))
                file_contents_str = "\n\n".join(file_contents_parts) or "(no enrichment data available)"

                # Guard: if raw sections are too large, cap them BEFORE
                # building the prompt so we never exceed the model window.
                # Split the budget: ~60% diff, ~40% file content (diff is more
                # important for QA analysis).
                max_section = MAX_INPUT_CHARS - 20_000  # leave room for template + system
                max_diff_chars = int(max_section * 0.6)
                max_content_chars = int(max_section * 0.4)

                if len(batch_diff) > max_diff_chars:
                    logger.warning(
                        "Stage 1 batch %d/%d: batch_diff too large (%dK chars), "
                        "capping to %dK chars",
                        idx, total_batches, len(batch_diff) // 1000,
                        max_diff_chars // 1000,
                    )
                    batch_diff = (
                        batch_diff[:max_diff_chars]
                        + f"\n\n... (diff capped — original {len(batch_diff)} chars)"
                    )

                if len(file_contents_str) > max_content_chars:
                    logger.warning(
                        "Stage 1 batch %d/%d: file_contents too large (%dK chars), "
                        "capping to %dK chars",
                        idx, total_batches, len(file_contents_str) // 1000,
                        max_content_chars // 1000,
                    )
                    file_contents_str = (
                        file_contents_str[:max_content_chars]
                        + f"\n\n... (content capped — original {len(file_contents_str)} chars)"
                    )

                prompt = QA_STAGE_1_BATCH_PROMPT.format(
                    **placeholders,
                    batch_number=idx,
                    total_batches=total_batches,
                    file_list=file_list,
                    batch_diff=batch_diff,
                    file_contents=file_contents_str,
                )

                est_tokens = (len(prompt) + len(QA_DOC_SYSTEM_PROMPT)) // 4
                logger.info(
                    "Stage 1 batch %d/%d: prompt_size=%d chars (~%dK tokens), "
                    "files_with_content=%d/%d",
                    idx, total_batches, len(prompt), est_tokens // 1000,
                    sum(1 for p in file_contents_parts if "(file content not available)" not in p),
                    len(batch_files),
                )

                try:
                    response = await self.llm.ainvoke([
                        {"role": "system", "content": QA_DOC_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ])
                    text = self._extract_text(response)
                    parsed = self._parse_json_from_response(text)
                    if parsed:
                        fa_count = len(parsed.get("file_analyses", []))
                        logger.info(
                            "Stage 1 batch %d/%d: parsed OK, file_analyses=%d, keys=%s",
                            idx, total_batches, fa_count, list(parsed.keys()),
                        )
                        return parsed
                    else:
                        logger.warning(
                            "Stage 1 batch %d/%d: JSON parse failed, response_length=%d, "
                            "first_200_chars=%s",
                            idx, total_batches, len(text) if text else 0,
                            repr((text or "")[:200]),
                        )
                        return {"batch_id": idx, "raw_analysis": text, "file_analyses": []}
                except Exception as e:
                    logger.error("Stage 1 batch %d/%d failed: %s", idx, total_batches, e)
                    return {"batch_id": idx, "error": str(e), "file_analyses": []}
                finally:
                    completed_count += 1
                    emit_progress(
                        self.event_callback,
                        int(10 + (30 * completed_count / total_batches)),
                        f"Stage 1: {completed_count}/{total_batches} batches complete",
                    )

        # Launch all batches in parallel (semaphore limits concurrency)
        logger.info(
            "Stage 1: launching %d batches with max_concurrency=%d",
            total_batches, MAX_CONCURRENCY,
        )
        tasks = [_process_batch(idx, batch) for idx, batch in enumerate(batches, start=1)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle any unexpected exceptions from gather
        processed: List[Dict[str, Any]] = []
        for idx, result in enumerate(results, start=1):
            if isinstance(result, BaseException):
                logger.error("Stage 1 batch %d/%d unexpected failure: %s", idx, total_batches, result)
                processed.append({"batch_id": idx, "error": str(result), "file_analyses": []})
            else:
                processed.append(result)

        return processed

    # ── Stage 2: Cross-Impact Analysis ───────────────────────────────

    async def _execute_stage_2(
        self,
        *,
        stage_1_results: List[Dict[str, Any]],
        enrichment_data: Optional[PrEnrichmentDataDto],
        changed_file_paths: List[str],
        placeholders: Dict[str, str],
    ) -> Dict[str, Any]:
        """Run Stage 2: cross-file impact analysis."""
        # Build dependency info from enrichment
        dependency_info = "No dependency data available."
        if enrichment_data and enrichment_data.relationships:
            dep_lines = []
            for rel in enrichment_data.relationships:
                dep_lines.append(
                    f"- {rel.sourceFile} --[{rel.relationshipType.value}]--> {rel.targetFile}"
                    + (f" (matched: {rel.matchedOn})" if rel.matchedOn else "")
                )
            if dep_lines:
                dependency_info = "\n".join(dep_lines)

        # Serialize Stage 1 results (compact) and cap to fit model context
        MAX_STAGE2_CHARS = 200_000
        probe = QA_STAGE_2_CROSS_IMPACT_PROMPT.format(
            **placeholders,
            total_files_changed=len(changed_file_paths),
            stage_1_results="",
            dependency_info=dependency_info,
            changed_files_list=", ".join(changed_file_paths[:50]),
        )
        overhead = len(probe) + len(QA_DOC_SYSTEM_PROMPT) + 2000
        s1_budget = max(MAX_STAGE2_CHARS - overhead, 20_000)
        stage_1_str = self._slim_stage_results(stage_1_results, max_chars=s1_budget)

        prompt = QA_STAGE_2_CROSS_IMPACT_PROMPT.format(
            **placeholders,
            total_files_changed=len(changed_file_paths),
            stage_1_results=stage_1_str,
            dependency_info=dependency_info,
            changed_files_list=", ".join(changed_file_paths[:50]),
        )
        total_chars = len(prompt) + len(QA_DOC_SYSTEM_PROMPT)
        logger.info(
            "Stage 2: prompt=%dK chars (~%dK tokens), s1_budget=%dK",
            total_chars // 1000, total_chars // 4000, s1_budget // 1000,
        )

        try:
            response = await self.llm.ainvoke([
                {"role": "system", "content": QA_DOC_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ])
            content = self._extract_text(response)
            parsed = self._parse_json_from_response(content)
            return parsed or {"cross_file_scenarios": [], "cascading_risks": [], "raw_analysis": content}
        except Exception as e:
            logger.error("Stage 2 cross-impact failed: %s", e)
            return {"cross_file_scenarios": [], "cascading_risks": [], "error": str(e)}

    # ── Stage 3: Aggregation ─────────────────────────────────────────

    async def _execute_stage_3_aggregation(
        self,
        *,
        stage_1_results: List[Dict[str, Any]],
        stage_2_results: Dict[str, Any],
        previous_documentation: Optional[str],
        placeholders: Dict[str, str],
    ) -> str:
        """Run Stage 3: final aggregation into polished QA document.

        Uses compact JSON and progressively tighter budgets.  If the first
        attempt fails (model 400 / context overflow), retries once with
        a halved budget before giving up.
        """
        prev_doc_section = ""
        if previous_documentation and previous_documentation.strip():
            prev_doc_section = QA_STAGE_3_PREVIOUS_DOC_SECTION.format(
                previous_documentation=previous_documentation
            )

        # --- attempt helper ---------------------------------------------------
        async def _attempt(budget: int) -> str:
            # Compute overhead (prompt template + system prompt minus the two
            # variable slots) so we know how much room is left for results.
            probe = QA_STAGE_3_AGGREGATION_PROMPT.format(
                **placeholders,
                stage_1_results="",
                stage_2_results="",
                previous_doc_section=prev_doc_section,
            )
            overhead = len(probe) + len(QA_DOC_SYSTEM_PROMPT) + 2000  # safety margin
            remaining = max(budget - overhead, 20_000)
            s1_budget = int(remaining * 0.7)
            s2_budget = int(remaining * 0.3)

            stage_1_str = self._slim_stage_results(stage_1_results, max_chars=s1_budget)
            stage_2_str = self._slim_stage_results(stage_2_results, max_chars=s2_budget)

            prompt = QA_STAGE_3_AGGREGATION_PROMPT.format(
                **placeholders,
                stage_1_results=stage_1_str,
                stage_2_results=stage_2_str,
                previous_doc_section=prev_doc_section,
            )
            total = len(prompt) + len(QA_DOC_SYSTEM_PROMPT)
            logger.info(
                "Stage 3 aggregation: budget=%dK, prompt=%dK chars (~%dK tokens), "
                "s1=%dK, s2=%dK",
                budget // 1000, total // 1000, total // 4000,
                len(stage_1_str) // 1000, len(stage_2_str) // 1000,
            )

            response = await self.llm.ainvoke([
                {"role": "system", "content": QA_DOC_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ])
            return self._extract_text(response)

        # --- try with normal budget, then retry at half -----------------------
        BUDGET_NORMAL = 200_000
        BUDGET_TIGHT  = 100_000
        try:
            return await _attempt(BUDGET_NORMAL)
        except Exception as first_err:
            logger.warning(
                "Stage 3 aggregation failed at %dK budget: %s — retrying at %dK",
                BUDGET_NORMAL // 1000, first_err, BUDGET_TIGHT // 1000,
            )
            return await _attempt(BUDGET_TIGHT)

    # ── Stage 3 Delta: Same-PR re-run ────────────────────────────────

    async def _execute_stage_3_delta(
        self,
        *,
        stage_1_results: List[Dict[str, Any]],
        stage_2_results: Dict[str, Any],
        delta_diff: str,
        previous_documentation: str,
        placeholders: Dict[str, str],
    ) -> str:
        """Run Stage 3 delta: targeted update for same-PR re-runs."""

        async def _attempt(budget: int) -> str:
            # Measure template overhead (everything except stage results)
            probe = QA_STAGE_3_DELTA_PROMPT.format(
                **placeholders,
                delta_diff=delta_diff,
                stage_1_results="",
                stage_2_results="",
                previous_documentation=previous_documentation,
            )
            overhead = len(probe) + len(QA_DOC_SYSTEM_PROMPT) + 2000
            remaining = max(budget - overhead, 20_000)
            s1_budget = int(remaining * 0.7)
            s2_budget = int(remaining * 0.3)

            stage_1_str = self._slim_stage_results(stage_1_results, max_chars=s1_budget)
            stage_2_str = self._slim_stage_results(stage_2_results, max_chars=s2_budget)

            prompt = QA_STAGE_3_DELTA_PROMPT.format(
                **placeholders,
                delta_diff=delta_diff,
                stage_1_results=stage_1_str,
                stage_2_results=stage_2_str,
                previous_documentation=previous_documentation,
            )
            total = len(prompt) + len(QA_DOC_SYSTEM_PROMPT)
            logger.info(
                "Stage 3 delta: budget=%dK, prompt=%dK chars (~%dK tokens)",
                budget // 1000, total // 1000, total // 4000,
            )

            response = await self.llm.ainvoke([
                {"role": "system", "content": QA_DOC_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ])
            return self._extract_text(response)

        BUDGET_NORMAL = 200_000
        BUDGET_TIGHT  = 100_000
        try:
            return await _attempt(BUDGET_NORMAL)
        except Exception as first_err:
            logger.warning(
                "Stage 3 delta failed at %dK budget: %s — retrying at %dK",
                BUDGET_NORMAL // 1000, first_err, BUDGET_TIGHT // 1000,
            )
            return await _attempt(BUDGET_TIGHT)

        response = await self.llm.ainvoke([
            {"role": "system", "content": QA_DOC_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ])
        return self._extract_text(response)

    # ==================================================================
    # Single-pass fallback (small PRs)
    # ==================================================================

    async def _run_single_pass(
        self,
        *,
        template_mode: str,
        custom_template: Optional[str],
        placeholders: Dict[str, str],
        previous_documentation: Optional[str],
    ) -> str:
        """Single-pass generation for small PRs (same as legacy flow).

        For large diffs that land here via the multi-stage fallback, the diff
        is truncated to ~120K chars (~30K tokens) so it stays within model
        context limits while preserving as much context as possible.
        """
        # Guard: truncate diff to avoid blowing context window on fallback
        max_single_pass_diff = 120_000
        sp_placeholders = dict(placeholders)
        raw_diff = sp_placeholders.get("diff", "")
        if len(raw_diff) > max_single_pass_diff:
            sp_placeholders["diff"] = (
                raw_diff[:max_single_pass_diff]
                + f"\n\n... (diff truncated — {len(raw_diff)} chars total, showing first {max_single_pass_diff})"
            )
            logger.info(
                "Single-pass: truncated diff from %d to %d chars to fit context window",
                len(raw_diff), max_single_pass_diff,
            )

        if template_mode == "RAW":
            prompt_template = QA_DOC_RAW_PROMPT
        elif template_mode == "CUSTOM" and custom_template:
            prompt_template = QA_DOC_CUSTOM_PROMPT
            sp_placeholders = {**sp_placeholders, "custom_template": custom_template}
        else:
            prompt_template = QA_DOC_BASE_PROMPT

        user_prompt = prompt_template.format(**sp_placeholders)

        if previous_documentation and previous_documentation.strip():
            update_preamble = QA_DOC_UPDATE_PREAMBLE.format(
                previous_documentation=previous_documentation,
                pr_number=placeholders.get("pr_number", "N/A"),
            )
            user_prompt = update_preamble + "\n\n" + user_prompt

        messages = [
            {"role": "system", "content": QA_DOC_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        response = await self.llm.ainvoke(messages)
        content = self._extract_text(response)

        if not content or len(content.strip()) < 50:
            if template_mode != "BASE":
                return await self._run_single_pass(
                    template_mode="BASE",
                    custom_template=None,
                    placeholders=placeholders,
                    previous_documentation=previous_documentation,
                )
            return ""

        return content

    # ==================================================================
    # Shared helpers
    # ==================================================================

    @staticmethod
    def _slim_stage_results(results, max_chars: int = 0) -> str:
        """Compact-serialize stage results, stripping verbose/redundant fields.

        Removes raw_analysis, error, batch_id and empty lists to
        significantly shrink the payload before passing to later stages.
        If *max_chars* > 0, truncates the serialized string to that budget.
        """
        def _strip(obj):
            if isinstance(obj, dict):
                return {
                    k: _strip(v)
                    for k, v in obj.items()
                    if k not in ("raw_analysis", "error", "batch_id")
                    and v not in (None, "", [])
                }
            if isinstance(obj, list):
                return [_strip(i) for i in obj]
            return obj

        slimmed = _strip(results)
        # compact separators save ~35% vs indent=2
        text = json.dumps(slimmed, separators=(",", ":"), default=str)
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars] + "\n... (capped for model context)"
        return text

    def _build_placeholders(
        self,
        project_name: str,
        pr_number: Optional[int],
        issues_found: int,
        files_analyzed: int,
        pr_metadata: Dict[str, Any],
        task_context_dict: Optional[Dict[str, str]],
        task_context_block: str,
        diff: Optional[str],
        source_branch: str = "N/A",
        target_branch: str = "N/A",
        output_language: Optional[str] = "English",
    ) -> Dict[str, str]:
        """Build the placeholder dictionary used for prompt formatting."""
        task_ctx = task_context_dict or {}
        effective_language = output_language if output_language and output_language.strip() else "English"
        return {
            "project_name": project_name or "Unknown",
            "pr_number": str(pr_number) if pr_number else "N/A",
            "task_key": task_ctx.get("task_key", "N/A"),
            "task_summary": task_ctx.get("task_summary", "N/A"),
            "source_branch": source_branch,
            "target_branch": target_branch,
            "pr_title": pr_metadata.get("prTitle", "N/A"),
            "pr_description": self._truncate(pr_metadata.get("prDescription", ""), 500),
            "issues_found": str(issues_found),
            "files_analyzed": str(files_analyzed),
            "analysis_summary": pr_metadata.get("analysisSummary", "No analysis summary available."),
            "diff": diff or "No diff available.",
            "task_context": task_context_block,
            "output_language": effective_language,
        }

    async def _is_documentation_needed(self, placeholders: Dict[str, str]) -> bool:
        """Relevance check — LLM decides if QA docs are warranted.

        Uses a truncated diff (max ~10K chars) because the relevance gate
        only needs a representative sample, not every line of a 1 MB diff.
        """
        try:
            # Build a lightweight copy with truncated diff for the relevance check
            max_relevance_diff = 10_000
            relevance_placeholders = dict(placeholders)
            raw_diff = relevance_placeholders.get("diff", "")
            if len(raw_diff) > max_relevance_diff:
                relevance_placeholders["diff"] = (
                    raw_diff[:max_relevance_diff]
                    + f"\n\n... (diff truncated — {len(raw_diff)} chars total, showing first {max_relevance_diff})"
                )
            prompt = QA_DOC_RELEVANCE_CHECK_PROMPT.format(**relevance_placeholders)
            response = await self.llm.ainvoke(prompt)
            content = self._extract_text(response)
            answer = content.strip().upper()
            logger.debug("Relevance check answer: %s", answer)
            return answer.startswith("YES")
        except Exception as e:
            logger.warning("Relevance check failed, defaulting to YES: %s", e)
            return True

    @staticmethod
    def _extract_documented_prs(previous_documentation: Optional[str]) -> set:
        """Extract PR numbers from the tracking marker in previous doc."""
        import re
        if not previous_documentation:
            return set()
        match = re.search(r'<!-- codecrow-qa-autodoc:prs=([\d,]+) -->', previous_documentation)
        if match:
            try:
                return {int(p) for p in match.group(1).split(',') if p.strip()}
            except ValueError:
                return set()
        return set()

    @staticmethod
    def _extract_text(response) -> str:
        """Extract text from LangChain response (handles Gemini list content)."""
        if hasattr(response, "content"):
            content = response.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
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
    def _parse_json_from_response(text: str) -> Optional[Dict[str, Any]]:
        """
        Attempt to parse JSON from an LLM response.
        Handles markdown code fences, trailing commas, and leading/trailing text.
        """
        import re
        if not text:
            return None

        def _try_parse(s: str) -> Optional[Dict[str, Any]]:
            """Try json.loads, also with trailing-comma cleanup."""
            try:
                return json.loads(s)
            except (json.JSONDecodeError, TypeError):
                pass
            # Strip trailing commas before } or ] (common LLM mistake)
            cleaned = re.sub(r',\s*([}\]])', r'\1', s)
            try:
                return json.loads(cleaned)
            except (json.JSONDecodeError, TypeError):
                return None

        # Try direct parse first
        result = _try_parse(text)
        if result:
            return result

        # Try extracting from markdown code fence (flexible whitespace)
        fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
        if fence_match:
            result = _try_parse(fence_match.group(1).strip())
            if result:
                return result

        # Try finding the first { ... } block (brace-depth tracking)
        brace_start = text.find('{')
        if brace_start >= 0:
            depth = 0
            for i in range(brace_start, len(text)):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        result = _try_parse(text[brace_start:i + 1])
                        if result:
                            return result
                        break

        return None

    @staticmethod
    def _truncate(text: str, max_length: int) -> str:
        if not text or len(text) <= max_length:
            return text or ""
        return text[:max_length] + "…"
