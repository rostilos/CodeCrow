"""
QA Documentation Orchestrator — Multi-Stage ULTRATHINKING Pipeline.

3-stage pipeline for generating high-quality QA documentation:
- Stage 1: Batch-level analysis of diff hunks with full file context
- Stage 2: Cross-file impact analysis (how changes interact for testing)
- Stage 3: Aggregation into polished QA document (or delta update for re-runs)

Extends BaseOrchestrator for shared batching and LLM infrastructure.
"""
import asyncio
import json
import logging
import re
from typing import Dict, Any, List, Optional, Callable, Set, Sequence

from model.enrichment import PrEnrichmentDataDto
from service.qa_documentation.base_orchestrator import (
    BaseOrchestrator,
    QaPromptPackingError,
    QaSemanticRecord,
    QA_MAX_SEMANTIC_PACKETS,
    emit_status,
    emit_progress,
    emit_error,
)
from utils.task_context_builder import build_task_context_for_prompt
from utils.prompts.constants_qa_doc import (
    QA_DOC_SYSTEM_PROMPT,
    QA_DOC_ANALYSIS_SYSTEM_PROMPT,
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
    QA_DOC_SECTION_BOUNDARY_REPAIR_PROMPT,
)

logger = logging.getLogger(__name__)

# Threshold: if total diff is under this many chars, skip multi-stage and do single-pass
SINGLE_PASS_THRESHOLD = 8_000  # ~2k tokens — small PRs don't need multi-stage

TEST_CASE_SENTINELS = (
    "<!-- codecrow-test-cases:start -->",
    "<!-- codecrow-test-cases:content -->",
    "<!-- codecrow-test-cases:end -->",
)
ENVIRONMENT_SENTINELS = (
    "<!-- codecrow-environment:start -->",
    "<!-- codecrow-environment:content -->",
    "<!-- codecrow-environment:end -->",
)

QA_SEMANTIC_SHARD_NOTICE = (
    "BOUNDED QA SEMANTIC SHARD: this request owns an admitted subset of input "
    "records. A QA_COVERAGE_DIAGNOSTIC record identifies partial coverage when "
    "the finite invocation ceiling omitted evidence. Do not "
    "interpret local absence as evidence that a change or requirement does "
    "not exist."
)

QA_HIERARCHICAL_SYNTHESIS_NOTICE = (
    "QA HIERARCHICAL SYNTHESIS: consolidate every assigned child memo and retain "
    "its coverage diagnostics. Do not infer that omitted sibling evidence is absent."
)

QA_CHILD_PACKET_CEILING = QA_MAX_SEMANTIC_PACKETS - 1
QA_SYNTHESIS_PACKET_CEILING = 1


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
        event_callback: Optional[Callable[[Dict], None]] = None,
    ):
        super().__init__(llm, event_callback)

    @staticmethod
    def _messages(system_prompt: str, user_prompt: str) -> List[Dict[str, str]]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    @staticmethod
    def _record_text(
        records: Sequence[QaSemanticRecord],
        *sections: str,
        empty: str,
    ) -> str:
        selected = [
            record.envelope()
            for record in records
            if record.section in sections
        ]
        if not selected:
            return empty
        return QA_SEMANTIC_SHARD_NOTICE + "\n\n" + "\n\n".join(selected)

    @staticmethod
    def _record_paths(records: Sequence[QaSemanticRecord]) -> List[str]:
        return sorted({path for record in records for path in record.paths if path})

    @staticmethod
    def _text_record(
        key: str,
        section: str,
        text: Any,
        *,
        paths: Sequence[str] = (),
    ) -> QaSemanticRecord:
        normalized = text if isinstance(text, str) else str(text)
        return QaSemanticRecord(
            key=key,
            section=section,
            text=normalized,
            paths=tuple(paths),
            character_end=len(normalized),
            source_character_count=len(normalized),
        )

    @classmethod
    def _json_leaf_records(
        cls,
        value: Any,
        *,
        key_prefix: str,
        section: str,
        path: tuple[Any, ...] = (),
    ) -> List[QaSemanticRecord]:
        """Represent every JSON leaf/empty container as a complete path record."""
        if isinstance(value, dict) and value:
            return [
                record
                for key in sorted(value, key=str)
                for record in cls._json_leaf_records(
                    value[key],
                    key_prefix=key_prefix,
                    section=section,
                    path=(*path, key),
                )
            ]
        if isinstance(value, (list, tuple)) and value:
            return [
                record
                for index, item in enumerate(value)
                for record in cls._json_leaf_records(
                    item,
                    key_prefix=key_prefix,
                    section=section,
                    path=(*path, index),
                )
            ]

        pointer = "/" + "/".join(
            str(item).replace("~", "~0").replace("/", "~1")
            for item in path
        )
        # String leaves are free text: keep their exact bytes directly under
        # the JSON-pointer-bearing record key so line/whitespace packing can
        # split them semantically. Other scalar/empty values remain explicit
        # typed JSON records.
        payload = (
            value
            if isinstance(value, str)
            else json.dumps(
                {"jsonPointer": pointer or "/", "value": value},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        )
        suffix = pointer or "/"
        return [cls._text_record(
            f"{key_prefix}:{suffix}",
            section,
            payload,
        )]

    @classmethod
    def _diff_records(
        cls,
        diff: str,
        *,
        key_prefix: str,
        section: str = "diff",
    ) -> List[QaSemanticRecord]:
        if not diff:
            return []
        sections = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
        records: List[QaSemanticRecord] = []
        for index, chunk in enumerate(sections, start=1):
            if not chunk:
                continue
            match = re.match(r"diff --git a/(.+?) b/(.+?)(?:\n|$)", chunk)
            paths = tuple(sorted(set(match.groups()))) if match else ()
            records.append(cls._text_record(
                f"{key_prefix}:{index:06d}",
                section,
                chunk,
                paths=paths,
            ))
        return records

    @classmethod
    def _shared_records(
        cls,
        placeholders: Dict[str, str],
        fields: Sequence[str],
    ) -> tuple[Dict[str, str], List[QaSemanticRecord]]:
        detached = dict(placeholders)
        records: List[QaSemanticRecord] = []
        for field in fields:
            value = detached.get(field)
            if not isinstance(value, str) or not value:
                continue
            records.append(cls._text_record(
                f"shared:{field}",
                f"shared:{field}",
                value,
            ))
            detached[field] = (
                f"[Complete {field} is assigned once in QA semantic records.]"
            )
        return detached, records

    @staticmethod
    def _stable_union(values: Sequence[Any]) -> List[Any]:
        result: List[Any] = []
        seen: set[str] = set()
        for value in values:
            identity = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            if identity in seen:
                continue
            seen.add(identity)
            result.append(value)
        return result

    @classmethod
    def _merge_stage_2_results(
        cls,
        results: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        list_fields = (
            "cross_file_scenarios",
            "cascading_risks",
            "uncovered_acceptance_criteria",
        )
        merged: Dict[str, Any] = {
            field: cls._stable_union([
                item
                for result in results
                for item in (result.get(field) or [])
            ])
            for field in list_fields
        }
        diagnostics = [
            str(result.get("error"))
            for result in results
            if result.get("error")
        ]
        if diagnostics:
            merged["partial_errors"] = cls._stable_union(diagnostics)
        raw = [
            str(result.get("raw_analysis"))
            for result in results
            if result.get("raw_analysis")
        ]
        if raw:
            merged["raw_analysis"] = "\n\n".join(raw)
        return merged

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
        max_allowed_tokens: Optional[int] = None,
        maxAllowedTokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Main entry point — runs the multi-stage QA doc pipeline.

        Returns:
            {"documentation_needed": bool, "documentation": str | None}
        """
        request_token_hint = (
            max_allowed_tokens
            if max_allowed_tokens is not None
            else maxAllowedTokens
        )
        if request_token_hint is None:
            request_token_hint = pr_metadata.get(
                "maxAllowedTokens",
                pr_metadata.get("max_allowed_tokens"),
            )
        token_target = self.set_request_input_token_target(request_token_hint)
        logger.info(
            "QA documentation request-aware input target: %d tokens",
            token_target,
        )

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

        documentation = await self._ensure_shareable_sections(documentation, placeholders)
        documentation = self._normalize_document_title(
            documentation,
            placeholders["pr_title"],
        )

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
    ) -> str:
        """Execute the 3-stage ULTRATHINKING pipeline."""
        try:
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
                changed_file_paths,
                enrichment_data,
                analysis_diff,
                max_batch_tokens=self.input_token_target(),
            )
            stage_1_results = await self._execute_stage_1(
                batches=batches,
                diff=analysis_diff,
                enrichment_data=enrichment_data,
                placeholders=placeholders,
            )
            dependency_coverage = self.dependency_batch_coverage_diagnostic()
            if dependency_coverage:
                stage_1_results.append({
                    "batch_id": "coverage-diagnostic",
                    "file_analyses": [],
                    "coverage_diagnostic": dependency_coverage,
                })
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

    # ── Stage 1: Batch Analysis ──────────────────────────────────────

    async def _execute_stage_1(
        self,
        *,
        batches: List[List[Dict[str, Any]]],
        diff: str,
        enrichment_data: Optional[PrEnrichmentDataDto],
        placeholders: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """Run Stage 1 with at most four dependency-batch LLM calls."""
        total_batches = len(batches)
        enrichment_lookup = self.build_enrichment_lookup(enrichment_data)
        analysis_system_prompt = QA_DOC_ANALYSIS_SYSTEM_PROMPT.format(**placeholders)
        token_target = self.input_token_target()

        MAX_CONCURRENCY = 5
        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        completed_count = 0

        async def _process_batch(
            idx: int,
            batch: List[Dict[str, Any]],
        ) -> List[Dict[str, Any]]:
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

                # Build file contents section for the unchanged one-call path.
                file_contents_parts: List[str] = []
                source_by_path: Dict[str, str] = {}
                for fp in sorted(batch_files):
                    fc = enrichment_lookup.get(fp, "")
                    if not fc:
                        for ep, ec in enrichment_lookup.items():
                            if fp.endswith(ep) or ep.endswith(fp):
                                fc = ec
                                break
                    if fc:
                        source_by_path[fp] = fc
                        file_contents_parts.append(f"#### {fp}\n```\n{fc}\n```")
                    else:
                        file_contents_parts.append(f"#### {fp}\n(file content not available)")

                file_list = "\n".join(f"- {fp}" for fp in sorted(batch_files))
                file_contents_str = "\n\n".join(file_contents_parts) or "(no enrichment data available)"

                complete_prompt = QA_STAGE_1_BATCH_PROMPT.format(
                    **placeholders,
                    batch_number=idx,
                    total_batches=total_batches,
                    file_list=file_list,
                    batch_diff=batch_diff,
                    file_contents=file_contents_str,
                )
                complete_messages = self._messages(
                    analysis_system_prompt,
                    complete_prompt,
                )
                complete_tokens = self.estimate_rendered_input_tokens(
                    complete_messages
                )

                packet_messages: List[
                    tuple[List[Dict[str, str]], tuple[str, ...]]
                ]
                if complete_tokens <= token_target:
                    packet_messages = [(complete_messages, ())]
                else:
                    shard_placeholders, shared_records = self._shared_records(
                        placeholders,
                        ("task_context",),
                    )
                    records = [
                        *shared_records,
                        *self._diff_records(
                            batch_diff,
                            key_prefix=f"stage1:{idx}:diff",
                        ),
                        *(
                            self._text_record(
                                f"stage1:{idx}:source:{source_index:06d}",
                                "source",
                                source_by_path.get(
                                    path,
                                    "(file content not available)",
                                ),
                                paths=(path,),
                            )
                            for source_index, path in enumerate(
                                sorted(batch_files),
                                start=1,
                            )
                        ),
                    ]

                    def render_packet(
                        packet: Sequence[QaSemanticRecord],
                    ) -> List[Dict[str, str]]:
                        packet_paths = self._record_paths(packet)
                        prompt = QA_STAGE_1_BATCH_PROMPT.format(
                            **shard_placeholders,
                            batch_number=idx,
                            total_batches=total_batches,
                            file_list=(
                                "\n".join(f"- {path}" for path in packet_paths)
                                or "(shared context record; no file-local record)"
                            ),
                            batch_diff=self._record_text(
                                packet,
                                "diff",
                                empty=(
                                    "No diff record is assigned to this shard; "
                                    "other shards own the omitted diff evidence."
                                ),
                            ),
                            file_contents=self._record_text(
                                packet,
                                "source",
                                "shared:task_context",
                                empty=(
                                    "No source/shared record is assigned to this "
                                    "shard; other shards own that evidence."
                                ),
                            ),
                        )
                        return self._messages(analysis_system_prompt, prompt)

                    packets = self.pack_semantic_records(
                        records,
                        render_packet,
                        token_target,
                        max_packets=1,
                    )
                    packet_messages = [
                        (
                            render_packet(packet),
                            tuple(record.display_key for record in packet),
                        )
                        for packet in packets
                    ]

                logger.info(
                    "Stage 1 batch %d/%d: complete_estimated_tokens=%d "
                    "target_tokens=%d semantic_shards=%d files_with_content=%d/%d",
                    idx,
                    total_batches,
                    complete_tokens,
                    token_target,
                    len(packet_messages),
                    sum(1 for p in file_contents_parts if "(file content not available)" not in p),
                    len(batch_files),
                )

                results: List[Dict[str, Any]] = []
                try:
                    for shard_index, (messages, record_keys) in enumerate(
                        packet_messages,
                        start=1,
                    ):
                        response = await self.llm.ainvoke(messages)
                        text = self._extract_text(response)
                        parsed = self._parse_json_from_response(text)
                        if parsed:
                            if record_keys:
                                parsed["qa_semantic_record_keys"] = list(
                                    record_keys
                                )
                            results.append(parsed)
                            continue
                        logger.warning(
                            "Stage 1 batch %d/%d shard %d/%d JSON parse "
                            "failed; preserving uncapped raw analysis; preview=%s",
                            idx,
                            total_batches,
                            shard_index,
                            len(packet_messages),
                            repr((text or "")[:200]),
                        )
                        results.append({
                            "batch_id": idx,
                            "raw_analysis": text,
                            "file_analyses": [],
                            "qa_semantic_record_keys": list(record_keys),
                        })
                    return results
                except Exception as e:
                    logger.error(
                        "Stage 1 batch %d/%d required semantic evidence failed: %s",
                        idx,
                        total_batches,
                        e,
                    )
                    return [{
                        "batch_id": idx,
                        "error": str(e),
                        "file_analyses": [],
                        "required_evidence_failed": True,
                    }]
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
                processed.append({
                    "batch_id": idx,
                    "error": str(result),
                    "file_analyses": [],
                    "required_evidence_failed": True,
                })
            else:
                processed.extend(result)

        required_failures = [
            result for result in processed if result.get("required_evidence_failed")
        ]
        if required_failures:
            raise RuntimeError(
                "QA Stage 1 failed atomically because a required semantic "
                "evidence shard was not analyzed"
            )

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
        """Run Stage 2 with at most three child calls and one synthesis call."""
        analysis_system_prompt = QA_DOC_ANALYSIS_SYSTEM_PROMPT.format(**placeholders)
        token_target = self.input_token_target()

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

        stage_1_str = self._slim_stage_results(stage_1_results)

        prompt = QA_STAGE_2_CROSS_IMPACT_PROMPT.format(
            **placeholders,
            total_files_changed=len(changed_file_paths),
            stage_1_results=stage_1_str,
            dependency_info=dependency_info,
            changed_files_list=", ".join(changed_file_paths),
        )
        complete_messages = self._messages(analysis_system_prompt, prompt)
        complete_tokens = self.estimate_rendered_input_tokens(complete_messages)
        logger.info(
            "Stage 2: complete_estimated_tokens=%d target_tokens=%d",
            complete_tokens,
            token_target,
        )

        if complete_tokens <= token_target:
            try:
                response = await self.llm.ainvoke(complete_messages)
                content = self._extract_text(response)
                parsed = self._parse_json_from_response(content)
                return parsed or {
                    "cross_file_scenarios": [],
                    "cascading_risks": [],
                    "raw_analysis": content,
                }
            except Exception as e:
                logger.error("Stage 2 cross-impact failed: %s", e)
                return {
                    "cross_file_scenarios": [],
                    "cascading_risks": [],
                    "error": str(e),
                }

        shard_placeholders, shared_records = self._shared_records(
            placeholders,
            ("task_context",),
        )
        dependency_records: List[QaSemanticRecord] = []
        if enrichment_data and enrichment_data.relationships:
            for index, relationship in enumerate(
                enrichment_data.relationships,
                start=1,
            ):
                if hasattr(relationship, "model_dump"):
                    relationship_payload = relationship.model_dump(mode="json")
                else:
                    relationship_payload = vars(relationship)
                dependency_records.append(self._text_record(
                    f"stage2:dependency:{index:06d}",
                    "dependency",
                    json.dumps(
                        relationship_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ),
                    paths=tuple(
                        str(value)
                        for value in (
                            getattr(relationship, "sourceFile", ""),
                            getattr(relationship, "targetFile", ""),
                        )
                        if value
                    ),
                ))
        records = [
            *shared_records,
            *self._json_leaf_records(
                stage_1_results,
                key_prefix="stage2:stage1",
                section="stage1",
            ),
            *dependency_records,
            *(
                self._text_record(
                    f"stage2:changed-path:{index:06d}",
                    "changed_path",
                    path,
                    paths=(path,),
                )
                for index, path in enumerate(changed_file_paths, start=1)
            ),
        ]

        def render_packet(
            packet: Sequence[QaSemanticRecord],
        ) -> List[Dict[str, str]]:
            user_prompt = QA_STAGE_2_CROSS_IMPACT_PROMPT.format(
                **shard_placeholders,
                total_files_changed=len(changed_file_paths),
                stage_1_results=self._record_text(
                    packet,
                    "stage1",
                    "stage2_memo",
                    "shared:task_context",
                    empty="No prior-analysis record is assigned to this shard.",
                ),
                dependency_info=self._record_text(
                    packet,
                    "dependency",
                    empty="No dependency record is assigned to this shard.",
                ),
                changed_files_list=self._record_text(
                    packet,
                    "changed_path",
                    empty="No changed-path record is assigned to this shard.",
                ),
            )
            return self._messages(analysis_system_prompt, user_prompt)

        async def invoke_packets(
            semantic_records: Sequence[QaSemanticRecord],
            *,
            synthesis: bool,
        ) -> tuple[List[Dict[str, Any]], int]:
            empty_tokens = self.estimate_rendered_input_tokens(render_packet([]))
            fragment_target = None
            if synthesis and empty_tokens < token_target:
                fragment_target = empty_tokens + max(
                    1,
                    (token_target - empty_tokens) // 3,
                )
            packets = self.pack_semantic_records(
                semantic_records,
                render_packet,
                token_target,
                fragment_token_target=fragment_target,
                max_packets=(
                    QA_SYNTHESIS_PACKET_CEILING
                    if synthesis
                    else QA_CHILD_PACKET_CEILING
                ),
            )
            successful: List[Dict[str, Any]] = []
            failures = 0
            for packet_index, packet in enumerate(packets, start=1):
                try:
                    response = await self.llm.ainvoke(render_packet(packet))
                    content = self._extract_text(response)
                    parsed = self._parse_json_from_response(content)
                    successful.append(parsed or {
                        "cross_file_scenarios": [],
                        "cascading_risks": [],
                        "raw_analysis": content,
                    })
                except Exception as exception:
                    failures += 1
                    logger.warning(
                        "Optional QA Stage 2 semantic shard %d/%d failed open: %s",
                        packet_index,
                        len(packets),
                        exception,
                    )
            return successful, failures

        current, failures = await invoke_packets(records, synthesis=False)
        if not current:
            return {
                "cross_file_scenarios": [],
                "cascading_risks": [],
                "error": "all optional Stage 2 semantic shards failed",
            }
        if failures:
            current.append({
                "cross_file_scenarios": [],
                "cascading_risks": [],
                "error": f"{failures} optional Stage 2 shard(s) failed open",
            })

        level = 1
        while len(current) > 1:
            memo_records = [
                self._text_record(
                    f"stage2:synthesis:{level}:{index:06d}",
                    "stage2_memo",
                    QA_HIERARCHICAL_SYNTHESIS_NOTICE
                    + "\n"
                    + json.dumps(
                        result,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ),
                )
                for index, result in enumerate(current, start=1)
            ]
            next_results, synthesis_failures = await invoke_packets(
                memo_records,
                synthesis=True,
            )
            if synthesis_failures or not next_results:
                logger.warning(
                    "QA Stage 2 hierarchy stopped at level %d after optional "
                    "synthesis failure; retaining deterministic complete union",
                    level,
                )
                return self._merge_stage_2_results(current)
            if len(next_results) >= len(current):
                logger.warning(
                    "QA Stage 2 hierarchy could not reduce %d complete memos at "
                    "level %d; retaining deterministic complete union",
                    len(current),
                    level,
                )
                return self._merge_stage_2_results(current)
            current = next_results
            level += 1

        return current[0]

    # ── Stage 3: Aggregation ─────────────────────────────────────────

    async def _invoke_required_document_records(
        self,
        records: Sequence[QaSemanticRecord],
        render_packet: Callable[
            [Sequence[QaSemanticRecord]],
            List[Dict[str, str]],
        ],
        *,
        label: str,
        synthesis: bool = False,
        max_packets: int = QA_CHILD_PACKET_CEILING,
    ) -> List[str]:
        """Invoke every required document shard or fail the stage atomically."""
        token_target = self.input_token_target()
        empty_tokens = self.estimate_rendered_input_tokens(render_packet([]))
        fragment_target = None
        if synthesis and empty_tokens < token_target:
            fragment_target = empty_tokens + max(
                1,
                (token_target - empty_tokens) // 3,
            )
        packets = self.pack_semantic_records(
            records,
            render_packet,
            token_target,
            fragment_token_target=fragment_target,
            max_packets=max_packets,
        )
        outputs: List[str] = []
        for index, packet in enumerate(packets, start=1):
            try:
                response = await self.llm.ainvoke(render_packet(packet))
            except Exception as exception:
                raise RuntimeError(
                    f"{label} failed atomically at required semantic shard "
                    f"{index} of {len(packets)}"
                ) from exception
            content = self._extract_text(response)
            if not content.strip():
                raise ValueError(
                    f"{label} returned empty content for required semantic "
                    f"shard {index} of {len(packets)}"
                )
            outputs.append(content)
        return outputs

    async def _synthesize_qa_documents(
        self,
        documents: Sequence[str],
        placeholders: Dict[str, str],
        *,
        label: str,
    ) -> str:
        """Hierarchically consolidate complete child documents under target."""
        current = list(documents)
        if not current:
            raise ValueError(f"{label} produced no document shard")
        if len(current) == 1:
            return current[0]

        shard_placeholders, _ = self._shared_records(
            placeholders,
            ("task_context", "analysis_summary"),
        )
        system_prompt = QA_DOC_SYSTEM_PROMPT.format(**shard_placeholders)

        def render_packet(
            packet: Sequence[QaSemanticRecord],
        ) -> List[Dict[str, str]]:
            prompt = QA_STAGE_3_AGGREGATION_PROMPT.format(
                **shard_placeholders,
                stage_1_results=self._record_text(
                    packet,
                    "document_memo",
                    empty="No child document memo is assigned to this packet.",
                ),
                stage_2_results=(
                    QA_HIERARCHICAL_SYNTHESIS_NOTICE
                    + " No separate Stage 2 record is assigned at this level."
                ),
                previous_doc_section="",
            )
            return self._messages(system_prompt, prompt)

        level = 1
        while len(current) > 1:
            records = [
                *(
                    self._text_record(
                        f"document-synthesis:{level}:{index:06d}",
                        "document_memo",
                        QA_HIERARCHICAL_SYNTHESIS_NOTICE + "\n" + document,
                    )
                    for index, document in enumerate(current, start=1)
                ),
            ]
            next_documents = await self._invoke_required_document_records(
                records,
                render_packet,
                label=f"{label} synthesis level {level}",
                synthesis=True,
                max_packets=QA_SYNTHESIS_PACKET_CEILING,
            )
            if len(next_documents) >= len(current):
                raise QaPromptPackingError(
                    f"{label} hierarchy could not reduce {len(current)} complete "
                    f"document memos at synthesis level {level}"
                )
            current = next_documents
            level += 1
        return current[0]

    async def _execute_stage_3_aggregation(
        self,
        *,
        stage_1_results: List[Dict[str, Any]],
        stage_2_results: Dict[str, Any],
        previous_documentation: Optional[str],
        placeholders: Dict[str, str],
    ) -> str:
        """Run Stage 3 with every prior-stage result intact and packed."""
        prev_doc_section = ""
        if previous_documentation and previous_documentation.strip():
            prev_doc_section = QA_STAGE_3_PREVIOUS_DOC_SECTION.format(
                previous_documentation=previous_documentation
            )

        stage_1_str = self._slim_stage_results(stage_1_results)
        stage_2_str = self._slim_stage_results(stage_2_results)
        prompt = QA_STAGE_3_AGGREGATION_PROMPT.format(
            **placeholders,
            stage_1_results=stage_1_str,
            stage_2_results=stage_2_str,
            previous_doc_section=prev_doc_section,
        )
        total = len(prompt) + len(QA_DOC_SYSTEM_PROMPT)
        logger.info(
            "Stage 3 aggregation: prompt=%dK chars (~%dK tokens), s1=%dK, s2=%dK",
            total // 1000,
            total // 4000,
            len(stage_1_str) // 1000,
            len(stage_2_str) // 1000,
        )
        system_prompt = QA_DOC_SYSTEM_PROMPT.format(**placeholders)
        complete_messages = self._messages(system_prompt, prompt)
        complete_tokens = self.estimate_rendered_input_tokens(complete_messages)
        if complete_tokens <= self.input_token_target():
            response = await self.llm.ainvoke(complete_messages)
            return self._extract_text(response)

        shard_placeholders, shared_records = self._shared_records(
            placeholders,
            ("task_context", "analysis_summary"),
        )
        shard_system_prompt = QA_DOC_SYSTEM_PROMPT.format(**shard_placeholders)
        records = [
            *shared_records,
            *self._json_leaf_records(
                stage_1_results,
                key_prefix="stage3:stage1",
                section="stage1",
            ),
            *self._json_leaf_records(
                stage_2_results,
                key_prefix="stage3:stage2",
                section="stage2",
            ),
        ]
        if previous_documentation and previous_documentation.strip():
            records.append(self._text_record(
                "stage3:previous-documentation",
                "previous_documentation",
                previous_documentation,
            ))

        def render_packet(
            packet: Sequence[QaSemanticRecord],
        ) -> List[Dict[str, str]]:
            previous = self._record_text(
                packet,
                "previous_documentation",
                empty="",
            )
            previous_section = (
                QA_STAGE_3_PREVIOUS_DOC_SECTION.format(
                    previous_documentation=previous
                )
                if previous
                else ""
            )
            shard_prompt = QA_STAGE_3_AGGREGATION_PROMPT.format(
                **shard_placeholders,
                stage_1_results=self._record_text(
                    packet,
                    "stage1",
                    "shared:task_context",
                    empty="No Stage 1 record is assigned to this shard.",
                ),
                stage_2_results=self._record_text(
                    packet,
                    "stage2",
                    "shared:analysis_summary",
                    empty="No Stage 2 record is assigned to this shard.",
                ),
                previous_doc_section=previous_section,
            )
            return self._messages(shard_system_prompt, shard_prompt)

        documents = await self._invoke_required_document_records(
            records,
            render_packet,
            label="QA Stage 3 aggregation",
        )
        return await self._synthesize_qa_documents(
            documents,
            placeholders,
            label="QA Stage 3 aggregation",
        )

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

        stage_1_str = self._slim_stage_results(stage_1_results)
        stage_2_str = self._slim_stage_results(stage_2_results)
        prompt = QA_STAGE_3_DELTA_PROMPT.format(
            **placeholders,
            delta_diff=delta_diff,
            stage_1_results=stage_1_str,
            stage_2_results=stage_2_str,
            previous_documentation=previous_documentation,
        )
        system_prompt = QA_DOC_SYSTEM_PROMPT.format(**placeholders)
        complete_messages = self._messages(system_prompt, prompt)
        if self.prompt_fits(complete_messages):
            response = await self.llm.ainvoke(complete_messages)
            return self._extract_text(response)

        shard_placeholders, shared_records = self._shared_records(
            placeholders,
            ("task_context",),
        )
        shard_system_prompt = QA_DOC_SYSTEM_PROMPT.format(**shard_placeholders)
        records = [
            *shared_records,
            *self._diff_records(
                delta_diff,
                key_prefix="stage3-delta:diff",
                section="delta_diff",
            ),
            *self._json_leaf_records(
                stage_1_results,
                key_prefix="stage3-delta:stage1",
                section="stage1",
            ),
            *self._json_leaf_records(
                stage_2_results,
                key_prefix="stage3-delta:stage2",
                section="stage2",
            ),
            self._text_record(
                "stage3-delta:previous-documentation",
                "previous_documentation",
                previous_documentation,
            ),
        ]

        def render_packet(
            packet: Sequence[QaSemanticRecord],
        ) -> List[Dict[str, str]]:
            shard_prompt = QA_STAGE_3_DELTA_PROMPT.format(
                **shard_placeholders,
                delta_diff=self._record_text(
                    packet,
                    "delta_diff",
                    empty="No delta-diff record is assigned to this shard.",
                ),
                stage_1_results=self._record_text(
                    packet,
                    "stage1",
                    "shared:task_context",
                    empty="No Stage 1 record is assigned to this shard.",
                ),
                stage_2_results=self._record_text(
                    packet,
                    "stage2",
                    empty="No Stage 2 record is assigned to this shard.",
                ),
                previous_documentation=self._record_text(
                    packet,
                    "previous_documentation",
                    empty=(
                        "No previous-document record is assigned to this shard; "
                        "another shard owns it."
                    ),
                ),
            )
            return self._messages(shard_system_prompt, shard_prompt)

        documents = await self._invoke_required_document_records(
            records,
            render_packet,
            label="QA Stage 3 delta",
        )
        return await self._synthesize_qa_documents(
            documents,
            placeholders,
            label="QA Stage 3 delta",
        )

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
        """Generate directly when it fits, otherwise synthesize semantic shards."""
        sp_placeholders = dict(placeholders)

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

        system_prompt = QA_DOC_SYSTEM_PROMPT.format(**sp_placeholders)
        messages = self._messages(system_prompt, user_prompt)
        if self.prompt_fits(messages):
            response = await self.llm.ainvoke(messages)
            content = self._extract_text(response)
        else:
            heavy_fields = [
                "task_context",
                "analysis_summary",
                "pr_description",
            ]
            if "custom_template" in sp_placeholders:
                heavy_fields.append("custom_template")
            shard_placeholders, records = self._shared_records(
                sp_placeholders,
                heavy_fields,
            )
            shard_placeholders["diff"] = (
                "[Complete diff is assigned once in QA semantic records.]"
            )
            records.extend(self._diff_records(
                str(sp_placeholders.get("diff") or ""),
                key_prefix="single-pass:diff",
                section="shared:diff",
            ))
            if previous_documentation and previous_documentation.strip():
                records.append(self._text_record(
                    "single-pass:previous-documentation",
                    "previous_documentation",
                    previous_documentation,
                ))
            shard_system = QA_DOC_SYSTEM_PROMPT.format(**shard_placeholders)

            def render_packet(
                packet: Sequence[QaSemanticRecord],
            ) -> List[Dict[str, str]]:
                packet_placeholders = dict(shard_placeholders)
                for field in (*heavy_fields, "diff"):
                    packet_placeholders[field] = self._record_text(
                        packet,
                        f"shared:{field}",
                        empty=(
                            f"No {field} record is assigned to this shard; "
                            "another shard owns it."
                        ),
                    )
                packet_prompt = prompt_template.format(**packet_placeholders)
                previous = self._record_text(
                    packet,
                    "previous_documentation",
                    empty="",
                )
                if previous:
                    packet_prompt = (
                        QA_DOC_UPDATE_PREAMBLE.format(
                            previous_documentation=previous,
                            pr_number=placeholders.get("pr_number", "N/A"),
                        )
                        + "\n\n"
                        + packet_prompt
                    )
                return self._messages(shard_system, packet_prompt)

            documents = await self._invoke_required_document_records(
                records,
                render_packet,
                label="QA single-pass generation",
            )
            content = await self._synthesize_qa_documents(
                documents,
                placeholders,
                label="QA single-pass generation",
            )

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

    async def _ensure_shareable_sections(
        self,
        documentation: str,
        placeholders: Dict[str, str],
    ) -> str:
        """Require exact, language-independent boundaries for both shareable sections."""
        if self._has_complete_shareable_sections(documentation):
            return documentation

        repair_placeholders = dict(placeholders)
        repair_placeholders["documentation"] = documentation

        logger.warning(
            "QA doc violated the shareable-section sentinel contract; "
            "running structural repair generation"
        )
        prompt = QA_DOC_SECTION_BOUNDARY_REPAIR_PROMPT.format(**repair_placeholders)
        system_prompt = QA_DOC_SYSTEM_PROMPT.format(
            output_language=repair_placeholders.get("output_language", "English")
        )
        complete_messages = self._messages(system_prompt, prompt)
        if self.prompt_fits(complete_messages):
            response = await self.llm.ainvoke(complete_messages)
            repaired = self._extract_text(response).strip()
        else:
            shard_placeholders, records = self._shared_records(
                repair_placeholders,
                ("task_context", "analysis_summary"),
            )
            shard_placeholders["diff"] = (
                "[Complete diff is assigned once in QA semantic records.]"
            )
            records.extend(self._diff_records(
                str(repair_placeholders.get("diff") or ""),
                key_prefix="sentinel-repair:diff",
                section="shared:diff",
            ))
            documentation_record = self._text_record(
                "sentinel-repair:documentation",
                "documentation",
                documentation,
            )
            records.append(documentation_record)
            shard_system = QA_DOC_SYSTEM_PROMPT.format(
                output_language=shard_placeholders.get(
                    "output_language",
                    "English",
                )
            )

            def render_packet(
                packet: Sequence[QaSemanticRecord],
            ) -> List[Dict[str, str]]:
                packet_placeholders = dict(shard_placeholders)
                for field in ("task_context", "analysis_summary", "diff"):
                    packet_placeholders[field] = self._record_text(
                        packet,
                        f"shared:{field}",
                        empty=(
                            f"No {field} record is assigned to this repair shard."
                        ),
                    )
                packet_placeholders["documentation"] = self._record_text(
                    packet,
                    "documentation",
                    empty=(
                        "No guide fragment is assigned to this repair shard; "
                        "another shard owns it."
                    ),
                )
                packet_prompt = QA_DOC_SECTION_BOUNDARY_REPAIR_PROMPT.format(
                    **packet_placeholders
                )
                return self._messages(shard_system, packet_prompt)

            repaired_documents = await self._invoke_required_document_records(
                records,
                render_packet,
                label="QA sentinel repair",
            )
            repaired = (
                await self._synthesize_qa_documents(
                    repaired_documents,
                    placeholders,
                    label="QA sentinel repair",
                )
            ).strip()
        if not repaired:
            raise ValueError("QA documentation sentinel repair returned no content")
        if not self._has_complete_shareable_sections(repaired):
            raise ValueError(
                "QA documentation is missing complete test-case or environment sentinel sections"
            )
        return repaired

    @classmethod
    def _has_complete_shareable_sections(cls, documentation: Optional[str]) -> bool:
        test_cases = cls._extract_sentinel_section(documentation, TEST_CASE_SENTINELS)
        environment = cls._extract_sentinel_section(documentation, ENVIRONMENT_SENTINELS)
        if test_cases is None or environment is None or test_cases[1] > environment[0]:
            return False
        return re.search(
            r"(?mi)^\s*\*\*.+?\*\*\s*\((?:HIGH|MEDIUM|LOW)\)",
            test_cases[3],
        ) is not None

    @classmethod
    def _contains_extractable_test_cases(cls, documentation: Optional[str]) -> bool:
        test_cases = cls._extract_sentinel_section(documentation, TEST_CASE_SENTINELS)
        if test_cases is None:
            return False
        return re.search(
            r"(?mi)^\s*\*\*.+?\*\*\s*\((?:HIGH|MEDIUM|LOW)\)",
            test_cases[3],
        ) is not None

    @staticmethod
    def _extract_sentinel_section(
        documentation: Optional[str],
        sentinels: tuple[str, str, str],
    ) -> Optional[tuple[int, int, str, str]]:
        """Extract one exact sentinel block without interpreting its localized heading."""
        if not documentation:
            return None
        start_marker, content_marker, end_marker = sentinels
        if any(documentation.count(marker) != 1 for marker in sentinels):
            return None

        start = documentation.find(start_marker)
        content_start = documentation.find(content_marker, start + len(start_marker))
        end = documentation.find(end_marker, content_start + len(content_marker))
        if start < 0 or content_start < 0 or end < 0:
            return None

        heading = documentation[start + len(start_marker):content_start].strip()
        content = documentation[content_start + len(content_marker):end].strip()
        if not heading.startswith("#") or "\n" in heading or not content:
            return None
        return start, end + len(end_marker), heading, content

    @staticmethod
    def _normalize_document_title(documentation: str, fallback_title: str) -> str:
        """Replace a leaked empty-title sentinel in the rendered guide heading."""
        return re.sub(
            r"(?mi)^(#\s+QA Testing Guide\s*[—–-]\s*)(?:N\s*/?\s*A|None|null)\s*$",
            lambda match: f"{match.group(1)}{fallback_title}",
            documentation,
            count=1,
        )

    @staticmethod
    def _display_value(value: Any) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized or normalized.casefold() in {"n/a", "na", "none", "null"}:
            return None
        return normalized

    # ==================================================================
    # Shared helpers
    # ==================================================================

    @staticmethod
    def _slim_stage_results(results) -> str:
        """Compact-serialize complete stage results without dropping fields."""
        return json.dumps(results, separators=(",", ":"), default=str)

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
        normalized_project_name = self._display_value(project_name)
        task_key = self._display_value(task_ctx.get("task_key"))
        task_summary = self._display_value(task_ctx.get("task_summary"))
        pr_title = (
            self._display_value(pr_metadata.get("prTitle"))
            or task_summary
            or task_key
            or (f"PR #{pr_number}" if pr_number is not None else None)
            or normalized_project_name
            or "QA documentation"
        )
        return {
            "project_name": normalized_project_name or "Unknown",
            "pr_number": str(pr_number) if pr_number else "N/A",
            "task_key": task_key or "N/A",
            "task_summary": task_summary or "N/A",
            "source_branch": source_branch,
            "target_branch": target_branch,
            "pr_title": pr_title,
            "pr_description": pr_metadata.get("prDescription", "") or "",
            "issues_found": str(issues_found),
            "files_analyzed": str(files_analyzed),
            "analysis_summary": pr_metadata.get("analysisSummary", "No analysis summary available."),
            "diff": diff or "No diff available.",
            "task_context": task_context_block,
            "output_language": effective_language,
        }

    async def _is_documentation_needed(self, placeholders: Dict[str, str]) -> bool:
        """Relevance check — LLM decides using the complete supplied diff."""
        try:
            prompt = QA_DOC_RELEVANCE_CHECK_PROMPT.format(**placeholders)
            estimated_tokens = self.estimate_rendered_input_tokens(prompt)
            if estimated_tokens > self.input_token_target():
                logger.warning(
                    "QA relevance check skipped without clipping: complete "
                    "prompt estimates %d tokens above target %d; conservatively "
                    "requiring documentation",
                    estimated_tokens,
                    self.input_token_target(),
                )
                return True
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
