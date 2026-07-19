"""
Multi-Stage Review Orchestrator.

Orchestrates the 4-stage AI code review pipeline:
- Stage 0: Planning & Prioritization
- Stage 1: Parallel File Review  
- Stage 2: Cross-File & Architectural Analysis
- Stage 3: Aggregation & Final Report
"""
import os
import asyncio
import hashlib
import logging
from time import monotonic_ns
from typing import Dict, Any, List, Optional, Callable

from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from model.multi_stage import CrossFileAnalysisResult
from utils.diff_processor import ProcessedDiff
from utils.prompts.prompt_builder import PromptBuilder

from service.review.orchestrator.reconciliation import (
    collapse_exact_duplicate_issues,
    reconcile_previous_issues,
    deduplicate_cross_batch_issues,
    deduplicate_final_issues,
    deduplicate_final_issues_llm,
)
from service.review.orchestrator.verification_agent import (
    run_deterministic_evidence_gate,
    run_verification_agent,
)
from service.review.orchestrator.inference_policy import (
    build_review_inference_profile,
    should_run_stage_2,
    should_use_fast_dedup,
    with_stage_output_cap,
)
from service.review.orchestrator.stages import (
    execute_branch_analysis,
    execute_branch_reconciliation_direct,
    execute_stage_0_planning,
    execute_stage_1_file_reviews,
    execute_stage_2_cross_file,
    prefetch_stage_2_cross_module_context,
    build_deterministic_stage_3_report,
    execute_stage_3_aggregation,
    _emit_status,
    _emit_progress,
    _emit_error,
)
from service.review.telemetry import (
    CandidateCounts,
    CandidateLineage,
    CoverageCounts,
    ExecutionTelemetryRecorder,
    StageOutcome,
)
from service.review.execution_context import (
    context_branch_labels,
    context_snapshot_v1,
    is_manifest_bound_v1,
)
from service.review.coverage import ExecutionCoverageTracker

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


INTERNAL_PR_INDEX_ENABLED = _env_bool("REVIEW_INTERNAL_PR_INDEX_ENABLED", True)
VERIFICATION_ENABLED = _env_bool("REVIEW_VERIFICATION_ENABLED", True)


def _review_log_id(request: ReviewRequestDto) -> str:
    return (
        f"project={getattr(request, 'projectId', 'n/a')}, "
        f"pr={getattr(request, 'pullRequestId', None) or 'n/a'}"
    )


class MultiStageReviewOrchestrator:
    """
    Orchestrates the 4-stage AI code review pipeline:
    Stage 0: Planning & Prioritization
    Stage 1: Parallel File Review
    Stage 2: Cross-File & Architectural Analysis
    Stage 3: Aggregation & Final Report
    """

    def __init__(
        self, 
        llm, 
        mcp_client, 
        rag_client=None,
        event_callback: Optional[Callable[[Dict], None]] = None,
        llm_reranker=None,
        telemetry: Optional[ExecutionTelemetryRecorder] = None,
        coverage_tracker: Optional[ExecutionCoverageTracker] = None,
    ):
        self.llm = llm
        self.client = mcp_client
        self.rag_client = rag_client
        self.event_callback = event_callback
        self.llm_reranker = llm_reranker
        self.telemetry = telemetry
        self.coverage_tracker = coverage_tracker
        self.max_parallel_stage_1 = max(1, _env_int("REVIEW_STAGE1_MAX_PARALLEL", 5))
        self._pr_number: Optional[int] = None
        self._pr_indexed: bool = False

    @staticmethod
    def _elapsed_ms(started_ns: int) -> int:
        return max(0, (monotonic_ns() - started_ns) // 1_000_000)

    @staticmethod
    def _hunk_coverage(
        processed_diff: Optional[ProcessedDiff],
        represented_paths: Optional[set[str]] = None,
    ) -> CoverageCounts:
        if processed_diff is None:
            return CoverageCounts()
        try:
            inventory = sum(len(diff_file.hunks) for diff_file in processed_diff.files)
            represented = sum(
                len(diff_file.hunks)
                for diff_file in processed_diff.files
                if not diff_file.is_skipped
                and represented_paths is not None
                and diff_file.path in represented_paths
            )
            return CoverageCounts(
                inventory=inventory,
                represented=min(represented, inventory),
                unrepresented=max(0, inventory - represented),
            )
        except Exception:
            return CoverageCounts()

    @staticmethod
    def _planned_paths(review_plan) -> set[str]:
        try:
            paths = {
                review_file.path
                for group in review_plan.file_groups
                for review_file in group.files
            }
            return paths
        except Exception:
            return set()

    def _record_stage(
        self,
        *,
        name: str,
        producer: str,
        outcome: StageOutcome,
        started_ns: int,
        candidates: CandidateCounts | None = None,
        coverage: CoverageCounts | None = None,
        reason: str | None = None,
    ) -> None:
        if self.telemetry is None:
            return
        try:
            self.telemetry.record_stage(
                name=name,
                producer=producer,
                outcome=outcome,
                duration_ms=self._elapsed_ms(started_ns),
                usage=self.telemetry.model_usage_for(producer=producer),
                candidates=candidates,
                coverage=coverage,
                reason=reason,
            )
        except Exception as error:
            logger.warning("Stage telemetry rejected: %s", type(error).__name__)

    @staticmethod
    def _candidate_artifact_id(candidate: Any) -> str:
        if hasattr(candidate, "model_dump_json"):
            material = candidate.model_dump_json(exclude_none=False)
        else:
            material = repr(candidate)
        return "candidate:" + hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _record_lineage(
        self,
        *,
        producer: str,
        inputs: List[Any],
        outputs: List[Any],
    ) -> None:
        if self.telemetry is None:
            return
        try:
            self.telemetry.record_lineage(
                CandidateLineage(
                    producer=producer,
                    input_artifact_ids=tuple(
                        self._candidate_artifact_id(candidate) for candidate in inputs
                    ),
                    output_artifact_ids=tuple(
                        self._candidate_artifact_id(candidate) for candidate in outputs
                    ),
                )
            )
        except Exception as error:
            logger.warning("Candidate lineage rejected: %s", type(error).__name__)

    async def _run_optional_shared_verification(
        self,
        *,
        file_issues: List[CodeReviewIssue],
        request: ReviewRequestDto,
        processed_diff: Optional[ProcessedDiff],
        inference_profile: Any,
        planned_paths: set[str],
        started_ns: int,
    ) -> List[CodeReviewIssue]:
        """Verify one closed producer set, or record the optional verifier skip."""
        # Exact executions have an accept-only publication contract, so their
        # verifier is mandatory. The feature switch remains a legacy control.
        if VERIFICATION_ENABLED or is_manifest_bound_v1(request):
            verification_inputs = list(file_issues)
            verification_input = len(verification_inputs)
            _emit_status(
                self.event_callback,
                "verification_started",
                "Verifying issues against file contents...",
            )
            verified_issues = await run_verification_agent(
                with_stage_output_cap(
                    self.llm,
                    "verification",
                    inference_profile,
                ),
                file_issues,
                request,
                processed_diff,
            )
            self._record_lineage(
                producer="verification_agent",
                inputs=verification_inputs,
                outputs=verified_issues,
            )
            self._record_stage(
                name="verification",
                producer="verification_agent",
                outcome=StageOutcome.COMPLETE,
                started_ns=started_ns,
                candidates=CandidateCounts(
                    input=verification_input,
                    produced=0,
                    retained=len(verified_issues),
                ),
                coverage=self._hunk_coverage(processed_diff, planned_paths),
            )
            _emit_progress(
                self.event_callback,
                75,
                (
                    "Verification Complete: "
                    f"{len(verified_issues)} total issues after verification"
                ),
            )
            return verified_issues

        logger.info("Verification skipped by REVIEW_VERIFICATION_ENABLED")
        self._record_stage(
            name="verification",
            producer="verification_agent",
            outcome=StageOutcome.SKIPPED,
            started_ns=started_ns,
            candidates=CandidateCounts(
                input=len(file_issues),
                retained=len(file_issues),
            ),
            coverage=self._hunk_coverage(processed_diff, planned_paths),
            reason="policy_skipped",
        )
        _emit_status(
            self.event_callback,
            "verification_skipped",
            "Verification skipped by REVIEW_VERIFICATION_ENABLED",
        )
        return file_issues

    async def _index_pr_files(
        self,
        request: ReviewRequestDto,
        processed_diff: Optional[ProcessedDiff]
    ) -> None:
        """
        Index PR files into the main RAG collection with PR-specific metadata.
        This enables hybrid queries that prioritize PR data over stale branch data.
        
        IMPORTANT: We index FULL file content (from enrichment data), not just the diff.
        Indexing only diff hunks leads to false-positives because the RAG context
        is incomplete — the LLM needs the full file to understand the change properly.
        """
        if not INTERNAL_PR_INDEX_ENABLED:
            logger.info("PR file indexing disabled by REVIEW_INTERNAL_PR_INDEX_ENABLED")
            return

        if not self.rag_client or not processed_diff:
            return
        
        pr_number = request.pullRequestId
        if not pr_number:
            logger.info("No PR number, skipping PR file indexing")
            return
        
        # Build lookup from enrichment data so we can populate full_content on DiffFiles.
        # Java sends PrEnrichmentDataDto with fileContents containing the FULL source of
        # each changed file — this is what we want to index, NOT the diff hunks.
        enrichment_lookup: Dict[str, str] = {}
        if request.enrichmentData and request.enrichmentData.fileContents:
            for fc in request.enrichmentData.fileContents:
                if fc.content and not fc.skipped:
                    enrichment_lookup[fc.path] = fc.content
                    # Also map without leading directory prefix for flexible matching
                    # (enrichment paths may have repo-root prefix like "magento/app/...")
                    parts = fc.path.split("/", 1)
                    if len(parts) > 1:
                        enrichment_lookup[parts[1]] = fc.content
            if enrichment_lookup:
                logger.info(f"Enrichment lookup built: {len(enrichment_lookup)} entries for PR file indexing")
        
        files = []
        for f in processed_diff.get_included_files():
            # Populate full_content from enrichment data if not already set.
            # Try exact match first, then suffix-based matching for path variations.
            if not f.full_content and enrichment_lookup:
                if f.path in enrichment_lookup:
                    f.full_content = enrichment_lookup[f.path]
                else:
                    # Try matching by suffix (handles path prefix differences)
                    for enrich_path, enrich_content in enrichment_lookup.items():
                        if f.path.endswith(enrich_path) or enrich_path.endswith(f.path):
                            f.full_content = enrich_content
                            break
            
            content = f.full_content or f.content
            change_type = f.change_type.value if hasattr(f.change_type, 'value') else str(f.change_type)
            if content and change_type.upper() != "DELETED":
                content_source = "full_file" if f.full_content else "diff_only"
                if content_source == "diff_only":
                    logger.warning(f"PR indexing: no full content for {f.path}, falling back to diff content")
                files.append({
                    "path": f.path,
                    "content": content,
                    "change_type": change_type
                })
        
        if not files:
            logger.info("No files to index for PR")
            return
        
        # Set _pr_number BEFORE the indexing call so that cleanup can always
        # run in the finally block, even if indexing partially succeeds then errors.
        self._pr_number = pr_number
        
        try:
            rag_branch, _ = context_branch_labels(request)
            rag_branch = rag_branch or request.get_rag_branch() or "unknown"
            snapshot = context_snapshot_v1(request)
            if snapshot is not None:
                workspace = request.projectVcsWorkspace
                project = request.projectVcsRepoSlug
                execution_id = request.executionManifest.executionId
            else:
                workspace = request.projectWorkspace
                project = request.projectNamespace
                execution_id = None
            result = await self.rag_client.index_pr_files(
                workspace=workspace,
                project=project,
                pr_number=pr_number,
                branch=rag_branch,
                files=files,
                snapshot=snapshot,
                execution_id=execution_id,
            )
            if result.get("status") == "indexed":
                self._pr_indexed = True
                logger.info(f"Indexed PR #{pr_number}: {result.get('chunks_indexed', 0)} chunks")
            else:
                logger.warning(f"Failed to index PR files: {result}")
        except Exception as e:
            logger.warning(
                "Error indexing PR files: error_type=%s",
                type(e).__name__,
            )

    async def _cleanup_pr_files(self, request: ReviewRequestDto) -> None:
        """Delete PR-indexed data after analysis completes.
        
        Always attempts cleanup when pr_number is set, regardless of whether
        _pr_indexed flag is True. This handles edge cases where indexing partially
        succeeded (some points upserted) but _pr_indexed was never set to True.
        The RAG delete endpoint is idempotent — calling it for a non-existent PR
        returns 'skipped', so this is safe.
        """
        if not self._pr_number or not self.rag_client:
            return
        
        try:
            snapshot = context_snapshot_v1(request)
            if snapshot is not None:
                workspace = request.projectVcsWorkspace
                project = request.projectVcsRepoSlug
                execution_id = request.executionManifest.executionId
                head_sha = request.executionManifest.headSha
            else:
                workspace = request.projectWorkspace
                project = request.projectNamespace
                execution_id = None
                head_sha = None
            await self.rag_client.delete_pr_files(
                workspace=workspace,
                project=project,
                pr_number=self._pr_number,
                execution_id=execution_id,
                head_sha=head_sha,
            )
            logger.info(f"Cleaned up PR #{self._pr_number} indexed data")
        except Exception as e:
            logger.warning(
                "Failed to cleanup PR files: error_type=%s",
                type(e).__name__,
            )
        finally:
            self._pr_number = None
            self._pr_indexed = False

    # ── Token-budget constants for branch reconciliation batching ──
    # Rough ratio: 1 token ≈ 4 chars.  We reserve headroom for the prompt
    # template itself (~4 k tokens) and the MCP tool-call overhead.
    _BRANCH_BATCH_TOKEN_BUDGET = 30_000        # tokens for issue payload per batch
    _CHARS_PER_TOKEN           = 4
    _BRANCH_BATCH_CHAR_BUDGET  = _BRANCH_BATCH_TOKEN_BUDGET * _CHARS_PER_TOKEN  # ~120 k chars
    _BRANCH_BATCH_MAX_ISSUES   = 30            # hard cap regardless of token budget

    async def execute_branch_analysis(self, prompt: str) -> Dict[str, Any]:
        """
        Execute a single-pass branch analysis using the provided prompt.
        """
        return await execute_branch_analysis(
            self.llm,
            self.client,
            prompt,
            self.event_callback
        )

    # ── Batched branch reconciliation ────────────────────────────────

    async def execute_batched_branch_analysis(
        self,
        request: ReviewRequestDto,
        pr_metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Split a large set of previous issues into token-safe batches,
        run each batch through direct LLM reconciliation (MCP-free when
        file contents are available), and merge the results.

        When ``request.reconciliationFileContents`` is provided (non-empty dict),
        the system uses a direct LLM call with file contents inlined in the
        prompt — no MCP agent or tool calls needed.  This is the preferred path
        for branch reconciliation because Java has already fetched the files.

        Batches are formed by grouping issues per file, then packing
        file-groups into batches that stay under the token budget.
        """
        import json
        reconciliation_started_ns = monotonic_ns()
        all_issues: List[Dict[str, Any]] = pr_metadata.get("previousCodeAnalysisIssues", [])

        if not all_issues:
            logger.info("Branch reconciliation: no previous issues — nothing to reconcile")
            self._record_stage(
                name="reconciliation",
                producer="branch_reconciliation",
                outcome=StageOutcome.SKIPPED,
                started_ns=reconciliation_started_ns,
                candidates=CandidateCounts(),
                reason="no_candidates",
            )
            return {"issues": [], "comment": "No previous issues to reconcile."}

        # ── Pre-dedup: eliminate near-duplicate issues BEFORE sending to LLM ──
        # Java may send issues from multiple analyses for the same code location
        # with slightly different titles (LLM phrasing instability).  Dedup here
        # saves tokens and prevents the LLM from producing redundant output.
        pre_dedup_started_ns = monotonic_ns()
        pre_dedup_inputs = list(all_issues)
        pre_dedup_count = len(all_issues)
        all_issues = self._deduplicate_previous_issues(all_issues)
        self._record_lineage(
            producer="branch_pre_dedup",
            inputs=pre_dedup_inputs,
            outputs=all_issues,
        )
        self._record_stage(
            name="pre_dedup",
            producer="branch_pre_dedup",
            outcome=StageOutcome.COMPLETE,
            started_ns=pre_dedup_started_ns,
            candidates=CandidateCounts(
                input=pre_dedup_count,
                produced=0,
                retained=len(all_issues),
            ),
        )
        if len(all_issues) != pre_dedup_count:
            logger.info(
                f"Branch reconciliation pre-dedup: {pre_dedup_count} → {len(all_issues)} issues "
                f"({pre_dedup_count - len(all_issues)} duplicates removed)"
            )
            # Update pr_metadata so downstream prompt builders see the deduped list
            pr_metadata = {**pr_metadata, "previousCodeAnalysisIssues": all_issues}

        # Determine whether to use MCP-free direct path
        file_contents: Dict[str, str] = {}
        if request.reconciliationFileContents:
            file_contents = request.reconciliationFileContents
            logger.info(
                f"Branch reconciliation: using MCP-free direct path "
                f"({len(file_contents)} pre-fetched files)"
            )

        batches = self._split_issues_into_batches(all_issues)
        total_batches = len(batches)

        # Extract raw diff from request (per-file diffs for AI-bound files,
        # pre-filtered by Java)
        raw_diff: Optional[str] = getattr(request, 'rawDiff', None)

        if total_batches == 1:
            # Fast path — single batch, no overhead
            logger.info(
                f"Branch reconciliation: {len(all_issues)} issues fit in a single batch"
            )
            try:
                if file_contents:
                    # MCP-free direct path
                    prompt = PromptBuilder.build_branch_reconciliation_direct_prompt(
                        pr_metadata, file_contents, raw_diff=raw_diff,
                    )
                    result = await execute_branch_reconciliation_direct(
                        self.llm, prompt, self.event_callback
                    )
                else:
                    # Legacy MCP path (fallback if no file contents provided)
                    prompt = PromptBuilder.build_branch_review_prompt_with_branch_issues_data(
                        pr_metadata
                    )
                    result = await execute_branch_analysis(
                        self.llm, self.client, prompt, self.event_callback
                    )
            except Exception:
                self._record_stage(
                    name="reconciliation",
                    producer="branch_reconciliation",
                    outcome=StageOutcome.FAILED,
                    started_ns=reconciliation_started_ns,
                    candidates=CandidateCounts(input=len(all_issues)),
                    reason="reconciliation_failed",
                )
                raise
            reconciled_issues = result.get("issues", [])
            if not isinstance(reconciled_issues, list):
                reconciled_issues = []
            self._record_lineage(
                producer="branch_reconciliation",
                inputs=all_issues,
                outputs=reconciled_issues,
            )
            self._record_stage(
                name="reconciliation",
                producer="branch_reconciliation",
                outcome=(
                    StageOutcome.COMPLETE if file_contents else StageOutcome.PARTIAL
                ),
                started_ns=reconciliation_started_ns,
                candidates=CandidateCounts(
                    input=len(all_issues),
                    produced=max(0, len(reconciled_issues) - len(all_issues)),
                    retained=len(reconciled_issues),
                ),
                reason=None if file_contents else "agent_usage_unavailable",
            )
            return result

        logger.info(
            f"Branch reconciliation: splitting {len(all_issues)} issues "
            f"into {total_batches} batches"
        )
        _emit_status(
            self.event_callback,
            "branch_reconciliation_batching",
            f"Splitting {len(all_issues)} issues into {total_batches} batches...",
        )

        merged_issues: List[Dict[str, Any]] = []
        comments: List[str] = []
        failed_batches = 0

        for idx, batch in enumerate(batches, start=1):
            batch_label = f"Batch {idx}/{total_batches}"
            logger.info(
                f"Branch reconciliation {batch_label}: {len(batch)} issues"
            )
            _emit_progress(
                self.event_callback,
                int((idx - 1) / total_batches * 100),
                f"Reconciling {batch_label} ({len(batch)} issues)...",
            )

            # Build a per-batch metadata dict with only this batch's issues
            batch_metadata = {
                **pr_metadata,
                "previousCodeAnalysisIssues": batch,
            }

            try:
                if file_contents:
                    # Filter file contents to only files referenced by this batch
                    batch_files = {
                        issue.get("file")
                        for issue in batch
                        if issue.get("file")
                    }
                    batch_file_contents = {
                        fp: content
                        for fp, content in file_contents.items()
                        if fp in batch_files
                    }
                    # Filter raw diff to only per-file diffs for this batch's files
                    batch_diff = self._filter_diff_for_files(raw_diff, batch_files) if raw_diff else None
                    prompt = PromptBuilder.build_branch_reconciliation_direct_prompt(
                        batch_metadata, batch_file_contents,
                        batch_number=idx, total_batches=total_batches,
                        raw_diff=batch_diff,
                    )
                    result = await execute_branch_reconciliation_direct(
                        self.llm, prompt, self.event_callback
                    )
                else:
                    # Legacy MCP path
                    prompt = PromptBuilder.build_branch_review_prompt_with_branch_issues_data(
                        batch_metadata,
                        batch_number=idx,
                        total_batches=total_batches,
                    )
                    result = await execute_branch_analysis(
                        self.llm, self.client, prompt, self.event_callback
                    )

                merged_issues.extend(result.get("issues", []))
                if result.get("comment"):
                    comments.append(f"[{batch_label}] {result['comment']}")
            except Exception as e:
                failed_batches += 1
                logger.error(
                    f"Branch reconciliation {batch_label} failed: {e}",
                    exc_info=True,
                )
                comments.append(
                    f"[{batch_label}] FAILED — {len(batch)} issues left as unresolved"
                )

        summary = (
            f"Branch reconciliation completed in {total_batches} batches.\n"
            + "\n".join(comments)
        )
        logger.info(
            f"Branch reconciliation merged: {len(merged_issues)} total issues "
            f"from {total_batches} batches"
        )
        self._record_lineage(
            producer="branch_reconciliation",
            inputs=all_issues,
            outputs=merged_issues,
        )
        reconciliation_outcome = (
            StageOutcome.PARTIAL
            if failed_batches or not file_contents
            else StageOutcome.COMPLETE
        )
        reconciliation_reason = (
            "batch_failed"
            if failed_batches
            else "agent_usage_unavailable" if not file_contents else None
        )
        self._record_stage(
            name="reconciliation",
            producer="branch_reconciliation",
            outcome=reconciliation_outcome,
            started_ns=reconciliation_started_ns,
            candidates=CandidateCounts(
                input=len(all_issues),
                produced=max(0, len(merged_issues) - len(all_issues)),
                retained=len(merged_issues),
            ),
            reason=reconciliation_reason,
        )
        return {"issues": merged_issues, "comment": summary}

    @staticmethod
    def _filter_diff_for_files(
        raw_diff: str, file_paths: set
    ) -> Optional[str]:
        """
        Filter a unified diff to include only hunks for the given file paths.
        Returns None if no relevant hunks are found.
        """
        import re
        if not raw_diff or not file_paths:
            return None

        # Split diff into per-file sections using diff header pattern
        # Each section starts with "diff --git a/... b/..."
        sections = re.split(r'(?=^diff --git )', raw_diff, flags=re.MULTILINE)
        relevant = []

        for section in sections:
            if not section.strip():
                continue
            # Extract file path from diff header: "diff --git a/path b/path"
            header_match = re.match(r'diff --git a/(.+?) b/(.+?)(?:\n|$)', section)
            if header_match:
                a_path = header_match.group(1)
                b_path = header_match.group(2)
                if a_path in file_paths or b_path in file_paths:
                    relevant.append(section)

        return "\n".join(relevant) if relevant else None

    def _split_issues_into_batches(
        self, issues: List[Dict[str, Any]]
    ) -> List[List[Dict[str, Any]]]:
        """
        Group issues by file, then pack file-groups into batches that respect
        both the token budget and the hard issue-count cap.
        """
        import json
        from collections import OrderedDict

        # 1. Group issues by file path (preserve insertion order)
        by_file: OrderedDict[str, List[Dict[str, Any]]] = OrderedDict()
        for issue in issues:
            fp = issue.get("file") or "_unknown_"
            by_file.setdefault(fp, []).append(issue)

        batches: List[List[Dict[str, Any]]] = []
        current_batch: List[Dict[str, Any]] = []
        current_chars = 0

        for file_path, file_issues in by_file.items():
            group_json = json.dumps(file_issues, indent=2, default=str)
            group_chars = len(group_json)

            # If a single file-group already exceeds the budget, it gets its
            # own batch (we can't split issues for the same file).
            if (
                current_batch
                and (
                    current_chars + group_chars > self._BRANCH_BATCH_CHAR_BUDGET
                    or len(current_batch) + len(file_issues) > self._BRANCH_BATCH_MAX_ISSUES
                )
            ):
                batches.append(current_batch)
                current_batch = []
                current_chars = 0

            current_batch.extend(file_issues)
            current_chars += group_chars

        if current_batch:
            batches.append(current_batch)

        return batches

    @staticmethod
    def _deduplicate_previous_issues(
        issues: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Pre-deduplicate previous issues before sending to the LLM.

        Uses a two-tier approach:
          1. **Location fingerprint** (file + lineHash + category): catches issues
             where the LLM produced different titles for the same problem at the
             same code location across separate analyses.
          2. **Semantic similarity** on the title/reason within the same file:
             catches near-duplicate phrasings even when lineHash differs.

        Keeps the issue with the highest severity or, if tied, the most recent one
        (highest ``id`` or ``prVersion``).
        """
        import difflib

        if not issues:
            return []

        SEVERITY_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}

        def _sort_key(issue: Dict[str, Any]):
            sev = SEVERITY_RANK.get((issue.get("severity") or "").upper(), 0)
            version = issue.get("prVersion") or 0
            return (sev, version)

        # Sort highest-priority first so we keep the best representative
        sorted_issues = sorted(issues, key=_sort_key, reverse=True)

        # Tier 1: Location fingerprint (file + lineHash + category)
        seen_locations: Set[str] = set()
        tier1_result: List[Dict[str, Any]] = []

        for issue in sorted_issues:
            file_path = issue.get("file") or issue.get("filePath") or ""
            line_hash = issue.get("lineHash") or ""
            category = (issue.get("category") or "").upper()

            if line_hash:
                loc_key = f"{file_path}::{line_hash}::{category}"
                if loc_key in seen_locations:
                    continue
                seen_locations.add(loc_key)

            tier1_result.append(issue)

        # Tier 2: Semantic similarity within same file (title-based)
        from collections import OrderedDict
        by_file: OrderedDict[str, List[Dict[str, Any]]] = OrderedDict()
        for issue in tier1_result:
            fp = issue.get("file") or issue.get("filePath") or "_unknown_"
            by_file.setdefault(fp, []).append(issue)

        final: List[Dict[str, Any]] = []
        for file_path, file_issues in by_file.items():
            kept: List[Dict[str, Any]] = []
            for issue in file_issues:
                title = (issue.get("title") or issue.get("reason") or "").lower().strip()
                is_dup = False
                for existing in kept:
                    existing_title = (existing.get("title") or existing.get("reason") or "").lower().strip()
                    if title and existing_title:
                        ratio = difflib.SequenceMatcher(None, title, existing_title).ratio()
                        if ratio >= 0.75:
                            is_dup = True
                            break
                if not is_dup:
                    kept.append(issue)
            final.extend(kept)

        return final

    async def orchestrate_review(
        self, 
        request: ReviewRequestDto, 
        rag_context: Optional[Any] = None,
        processed_diff: Optional[ProcessedDiff] = None
    ) -> Dict[str, Any]:
        """
        Main entry point for the multi-stage review.
        Supports both FULL (initial review) and INCREMENTAL (follow-up review) modes.
        """
        is_incremental = (
            request.analysisMode == "INCREMENTAL" 
            and request.deltaDiff
        )
        manifest_bound = is_manifest_bound_v1(request)
        
        if is_incremental:
            logger.info(
                "[%s] INCREMENTAL mode: reviewing delta diff, %d previous issues to reconcile",
                _review_log_id(request),
                len(request.previousCodeAnalysisIssues or []),
            )
        else:
            logger.info("[%s] FULL mode: initial PR review", _review_log_id(request))

        inference_profile = build_review_inference_profile(request, processed_diff)
        if inference_profile.fast_check_enabled:
            _emit_status(
                self.event_callback,
                "fast_check_enabled",
                (
                    "Fast check enabled for small PR "
                    f"({inference_profile.describe()}): bounded planning, "
                    "conditional cross-file analysis, and deterministic small-issue dedup."
                ),
            )
        else:
            logger.info("Fast check not enabled: %s", inference_profile.describe())

        stage_2_context_task: Optional[asyncio.Task] = None
        planned_paths: set[str] = set()
        active_stage = "initialization"
        active_started_ns = monotonic_ns()
        review_rag_client = self.rag_client

        # The PR-index task is an invariant of every pipeline execution. Create
        # it before the guarded stages so cleanup never needs an unreachable
        # "task absent" branch.
        indexing_started_ns = monotonic_ns()
        indexing_task = asyncio.create_task(self._index_pr_files(request, processed_diff))

        try:
            # Stage 0 does not depend on PR-indexed RAG. Start indexing now and
            # await it before Stage 1, where stale-content protection is needed.
            # === STAGE 0: Planning ===
            active_stage = "planning"
            active_started_ns = monotonic_ns()
            _emit_status(self.event_callback, "stage_0_started", "Stage 0: Planning & Prioritization...")
            review_plan = await execute_stage_0_planning(
                with_stage_output_cap(self.llm, "stage_0", inference_profile),
                request,
                is_incremental,
                processed_diff=processed_diff,
                use_local_planning=manifest_bound,
            )
            
            review_plan = self._ensure_all_files_planned(review_plan, request.changedFiles or [])
            planned_paths = self._planned_paths(review_plan)
            self._record_stage(
                name="planning",
                producer="stage_0",
                outcome=StageOutcome.COMPLETE,
                started_ns=active_started_ns,
                candidates=CandidateCounts(
                    input=len(request.changedFiles or []),
                    produced=len(planned_paths),
                    retained=len(planned_paths),
                ),
                coverage=self._hunk_coverage(processed_diff, planned_paths),
            )
            stage_0_message = (
                "Stage 0 Complete: fast bounded review plan created"
                if inference_profile.fast_check_enabled
                else "Stage 0 Complete: Review plan created"
            )
            _emit_progress(self.event_callback, 10, stage_0_message)

            active_stage = "retrieval"
            active_started_ns = indexing_started_ns
            await indexing_task
            indexing_outcome = (
                StageOutcome.COMPLETE if self._pr_indexed else StageOutcome.SKIPPED
            )
            self._record_stage(
                name="retrieval",
                producer="pr_index",
                outcome=indexing_outcome,
                started_ns=indexing_started_ns,
                coverage=self._hunk_coverage(processed_diff, planned_paths),
                reason=(
                    None
                    if self._pr_indexed
                    else "exact_pr_overlay_not_available"
                    if manifest_bound
                    else "pr_index_not_available"
                ),
            )

            if not inference_profile.fast_check_enabled and review_rag_client:
                stage_2_context_task = asyncio.create_task(
                    prefetch_stage_2_cross_module_context(
                        review_rag_client,
                        request,
                        processed_diff=processed_diff,
                    )
                )
            
            # === STAGE 1: File Reviews ===
            active_stage = "generation"
            active_started_ns = monotonic_ns()
            logger.info("[%s] Stage 1 starting with %d planned files", _review_log_id(request), self._count_files(review_plan))
            _emit_status(self.event_callback, "stage_1_started", f"Stage 1: Analyzing {self._count_files(review_plan)} files...")
            use_mcp = getattr(request, 'useMcpTools', False) or False
            file_issues = await execute_stage_1_file_reviews(
                with_stage_output_cap(self.llm, "stage_1", inference_profile),
                request, 
                review_plan, 
                review_rag_client,
                rag_context, 
                processed_diff, 
                is_incremental,
                self.max_parallel_stage_1,
                self.event_callback,
                self._pr_indexed,
                llm_reranker=self.llm_reranker,
                use_llm_rerank=not inference_profile.fast_check_enabled,
                fallback_llm=self.llm,
                coverage_tracker=self.coverage_tracker,
            )
            self._record_lineage(
                producer="stage_1",
                inputs=[],
                outputs=file_issues,
            )
            self._record_stage(
                name="generation",
                producer="stage_1",
                outcome=StageOutcome.COMPLETE,
                started_ns=active_started_ns,
                candidates=CandidateCounts(
                    input=0,
                    produced=len(file_issues),
                    retained=len(file_issues),
                ),
                coverage=self._hunk_coverage(processed_diff, planned_paths),
            )
            
            # Cross-batch deduplication
            active_stage = "pre_dedup"
            active_started_ns = monotonic_ns()
            pre_cross_batch_issues = list(file_issues)
            pre_cross_batch_count = len(file_issues)
            if manifest_bound:
                logger.info(
                    "Manifest-bound review retained %d Stage 1 candidate(s); "
                    "lossy cross-batch dedup is retired",
                    pre_cross_batch_count,
                )
            else:
                file_issues = deduplicate_cross_batch_issues(file_issues)
            self._record_lineage(
                producer="cross_batch_dedup",
                inputs=pre_cross_batch_issues,
                outputs=file_issues,
            )
            self._record_stage(
                name="pre_dedup",
                producer="cross_batch_dedup",
                outcome=(
                    StageOutcome.SKIPPED
                    if manifest_bound
                    else StageOutcome.COMPLETE
                ),
                started_ns=active_started_ns,
                candidates=CandidateCounts(
                    input=pre_cross_batch_count,
                    produced=0,
                    retained=len(file_issues),
                ),
                coverage=self._hunk_coverage(processed_diff, planned_paths),
                reason="retired_lossy_dedup" if manifest_bound else None,
            )
            
            _emit_progress(self.event_callback, 60, f"Stage 1 Complete: {len(file_issues)} issues found across files")

            # === STAGE 1.5: Issue Reconciliation ===
            if request.previousCodeAnalysisIssues:
                active_stage = "reconciliation"
                active_started_ns = monotonic_ns()
                reconciliation_inputs = list(file_issues)
                reconciliation_input = len(file_issues)
                _emit_status(self.event_callback, "reconciliation_started", "Reconciling previous issues...")
                file_issues = await reconcile_previous_issues(
                    request, file_issues, processed_diff
                )
                self._record_lineage(
                    producer="previous_issue_reconciliation",
                    inputs=reconciliation_inputs,
                    outputs=file_issues,
                )
                self._record_stage(
                    name="reconciliation",
                    producer="previous_issue_reconciliation",
                    outcome=StageOutcome.COMPLETE,
                    started_ns=active_started_ns,
                    candidates=CandidateCounts(
                        input=reconciliation_input,
                        produced=max(0, len(file_issues) - reconciliation_input),
                        retained=len(file_issues),
                    ),
                    coverage=self._hunk_coverage(processed_diff, planned_paths),
                )
                _emit_progress(self.event_callback, 70, f"Reconciliation Complete: {len(file_issues)} total issues after reconciliation")
            else:
                self._record_stage(
                    name="reconciliation",
                    producer="previous_issue_reconciliation",
                    outcome=StageOutcome.SKIPPED,
                    started_ns=monotonic_ns(),
                    candidates=CandidateCounts(
                        input=len(file_issues), retained=len(file_issues)
                    ),
                    coverage=self._hunk_coverage(processed_diff, planned_paths),
                    reason="no_previous_issues",
                )

            # Legacy executions retain their characterized Stage 1.5 ordering.
            # Manifest-bound executions defer the optional verifier until the
            # Stage 1/Stage 2 producer set is closed below.
            if not manifest_bound:
                active_stage = "verification"
                active_started_ns = monotonic_ns()
                file_issues = await self._run_optional_shared_verification(
                    file_issues=file_issues,
                    request=request,
                    processed_diff=processed_diff,
                    inference_profile=inference_profile,
                    planned_paths=planned_paths,
                    started_ns=active_started_ns,
                )

            # === STAGE 2: Cross-File Analysis ===
            active_stage = "generation"
            active_started_ns = monotonic_ns()
            stage_2_inputs = list(file_issues)
            stage_2_input = len(stage_2_inputs)
            run_stage_2, stage_2_reason = should_run_stage_2(
                inference_profile,
                request,
                review_plan,
                file_issues,
            )
            if run_stage_2:
                _emit_status(
                    self.event_callback,
                    "stage_2_started",
                    f"Stage 2: Analyzing cross-file patterns ({stage_2_reason})...",
                )
                prefetched_cross_module_context = (
                    await stage_2_context_task
                    if stage_2_context_task is not None
                    else None
                )
                cross_file_results = await execute_stage_2_cross_file(
                    with_stage_output_cap(self.llm, "stage_2", inference_profile),
                    request,
                    file_issues,
                    review_plan,
                    processed_diff=processed_diff,
                    rag_client=review_rag_client,
                    fallback_llm=self.llm,
                    prefetched_cross_module_context=prefetched_cross_module_context,
                )
            else:
                if stage_2_context_task and not stage_2_context_task.done():
                    stage_2_context_task.cancel()
                logger.info("Fast check: skipping Stage 2 (%s)", stage_2_reason)
                _emit_status(
                    self.event_callback,
                    "fast_check_stage_2_skipped",
                    f"Fast check: Stage 2 skipped ({stage_2_reason})",
                )
                cross_file_results = CrossFileAnalysisResult(
                    pr_risk_level="LOW",
                    cross_file_issues=[],
                    data_flow_concerns=[],
                    pr_recommendation="No cross-file risk signals detected in fast check.",
                    confidence="HIGH",
                )
            # Merge Stage 2 cross-file issues into the issue list
            if cross_file_results.cross_file_issues:
                cross_issues_converted = _convert_cross_file_issues(cross_file_results.cross_file_issues)
                file_issues.extend(cross_issues_converted)
                logger.info(
                    f"Stage 2 contributed {len(cross_issues_converted)} cross-file issues "
                    f"(total issues now: {len(file_issues)})"
                )

            self._record_lineage(
                producer="stage_2",
                inputs=stage_2_inputs,
                outputs=file_issues,
            )

            self._record_stage(
                name="generation",
                producer="stage_2",
                outcome=StageOutcome.COMPLETE if run_stage_2 else StageOutcome.SKIPPED,
                started_ns=active_started_ns,
                candidates=CandidateCounts(
                    input=stage_2_input,
                    produced=max(0, len(file_issues) - stage_2_input),
                    retained=len(file_issues),
                ),
                coverage=self._hunk_coverage(processed_diff, planned_paths),
                reason=None if run_stage_2 else "policy_skipped",
            )

            # Manifest work verifies the closed Stage 1/Stage 2 producer union
            # with exact source receipts. Legacy retains its earlier
            # characterized Stage 1.5 ordering.
            if manifest_bound:
                active_stage = "verification"
                active_started_ns = monotonic_ns()
                file_issues = await self._run_optional_shared_verification(
                    file_issues=file_issues,
                    request=request,
                    processed_diff=processed_diff,
                    inference_profile=inference_profile,
                    planned_paths=planned_paths,
                    started_ns=active_started_ns,
                )

            # The deterministic evidence gate is the final verifier for both
            # paths and therefore runs only after the optional verifier has
            # completed (or its policy skip has been recorded).
            active_stage = "verification"
            active_started_ns = monotonic_ns()
            deterministic_inputs = list(file_issues)
            deterministic_input = len(file_issues)
            file_issues = run_deterministic_evidence_gate(
                file_issues,
                request,
                processed_diff,
            )
            self._record_lineage(
                producer="deterministic_evidence_gate",
                inputs=deterministic_inputs,
                outputs=file_issues,
            )
            self._record_stage(
                name="verification",
                producer="deterministic_evidence_gate",
                outcome=StageOutcome.COMPLETE,
                started_ns=active_started_ns,
                candidates=CandidateCounts(
                    input=deterministic_input,
                    produced=0,
                    retained=len(file_issues),
                ),
                coverage=self._hunk_coverage(processed_diff, planned_paths),
            )

            _emit_progress(self.event_callback, 85, "Stage 2 Complete: Cross-file analysis finished")

            # === FINAL DEDUP: after ALL issue-finding stages (1 + 1.5 + 2) ===
            pre_dedup_count = len(file_issues)
            post_dedup_inputs = list(file_issues)
            active_stage = "post_dedup"
            active_started_ns = monotonic_ns()
            if manifest_bound:
                file_issues = collapse_exact_duplicate_issues(file_issues)
            elif should_use_fast_dedup(inference_profile, pre_dedup_count):
                _emit_status(
                    self.event_callback,
                    "fast_check_dedup",
                    f"Fast check: deterministic final dedup for {pre_dedup_count} issue(s)",
                )
                file_issues = deduplicate_final_issues(file_issues)
            else:
                _emit_status(
                    self.event_callback,
                    "final_dedup_started",
                    f"Final dedup: semantic LLM dedup for {pre_dedup_count} issue(s)",
                )
                file_issues = await deduplicate_final_issues_llm(
                    with_stage_output_cap(self.llm, "dedup", inference_profile),
                    file_issues,
                )
            if len(file_issues) != pre_dedup_count:
                logger.info(
                    f"Final dedup before Stage 3: {pre_dedup_count} → {len(file_issues)} issues"
                )
            self._record_lineage(
                producer=(
                    "exact_content_dedup" if manifest_bound else "final_dedup"
                ),
                inputs=post_dedup_inputs,
                outputs=file_issues,
            )
            self._record_stage(
                name="post_dedup",
                producer=(
                    "exact_content_dedup" if manifest_bound else "final_dedup"
                ),
                outcome=StageOutcome.COMPLETE,
                started_ns=active_started_ns,
                candidates=CandidateCounts(
                    input=pre_dedup_count,
                    produced=0,
                    retained=len(file_issues),
                ),
                coverage=self._hunk_coverage(processed_diff, planned_paths),
            )

            # === STAGE 3: Aggregation ===
            active_stage = "aggregation"
            active_started_ns = monotonic_ns()
            aggregation_inputs = list(file_issues)
            _emit_status(self.event_callback, "stage_3_started", "Stage 3: Generating final report...")
            if manifest_bound:
                stage_3_result = build_deterministic_stage_3_report(
                    request,
                    review_plan,
                    file_issues,
                    cross_file_results,
                    processed_diff=processed_diff,
                )
            else:
                stage_3_result = await execute_stage_3_aggregation(
                    with_stage_output_cap(self.llm, "stage_3", inference_profile),
                    request,
                    review_plan,
                    file_issues,
                    cross_file_results,
                    is_incremental, processed_diff=processed_diff,
                    mcp_client=self.client if use_mcp else None,
                    use_mcp_tools=use_mcp,
                    fallback_llm=self.llm,
                )
            final_report = stage_3_result["report"]
            dismissed_ids = set(stage_3_result.get("dismissed_issue_ids", []))

            # Filter out issues dismissed by Stage 3 MCP verification
            if dismissed_ids:
                pre_count = len(file_issues)
                file_issues = [
                    issue for issue in file_issues
                    if getattr(issue, 'id', '') not in dismissed_ids
                ]
                logger.info(
                    f"Stage 3 dismissed {pre_count - len(file_issues)} false-positive issues "
                    f"(IDs: {dismissed_ids})"
                )

            self._record_lineage(
                producer="stage_3",
                inputs=aggregation_inputs,
                outputs=file_issues,
            )

            self._record_stage(
                name="aggregation",
                producer="stage_3",
                outcome=StageOutcome.COMPLETE,
                started_ns=active_started_ns,
                candidates=CandidateCounts(
                    input=len(file_issues) + len(dismissed_ids),
                    produced=0,
                    retained=len(file_issues),
                ),
                coverage=self._hunk_coverage(processed_diff, planned_paths),
            )

            _emit_progress(self.event_callback, 100, "Stage 3 Complete: Report generated")

            result = {
                "comment": final_report,
                "issues": [issue.model_dump() for issue in file_issues],
            }
            return result

        except Exception as e:
            logger.error(
                "Multi-stage review failed: error_type=%s",
                type(e).__name__,
            )
            self._record_stage(
                name=active_stage,
                producer="pipeline",
                outcome=StageOutcome.FAILED,
                started_ns=active_started_ns,
                coverage=self._hunk_coverage(processed_diff, planned_paths),
                reason="stage_exception",
            )
            _emit_error(self.event_callback, str(e))
            raise
        finally:
            if not indexing_task.done():
                indexing_task.cancel()
                try:
                    await indexing_task
                except asyncio.CancelledError:
                    pass
            if not indexing_task.cancelled():
                indexing_task.exception()
            if stage_2_context_task and not stage_2_context_task.done():
                stage_2_context_task.cancel()
                try:
                    await stage_2_context_task
                except asyncio.CancelledError:
                    pass
            elif stage_2_context_task and stage_2_context_task.done() and not stage_2_context_task.cancelled():
                stage_2_context_task.exception()
            # PR-indexed data is intentionally NOT cleaned up here.
            # It persists so that subsequent PR context queries can use it.
            # Cleanup happens via:
            #   - Webhook handlers on PR close/merge (Java side)
            #   - Re-analysis re-indexes (pr.py deletes old data first)
            pass

    def _count_files(self, plan) -> int:
        """Count total files in review plan."""
        return sum(len(g.files) for g in plan.file_groups)

    def _ensure_all_files_planned(self, plan, changed_files: List[str]):
        """
        Ensure all changed files are included in the review plan.
        LLM may miss some files, so we add them to a catch-all group.
        """
        from model.multi_stage import ReviewFile, FileGroup
        
        planned_files = set()
        for group in plan.file_groups:
            for f in group.files:
                planned_files.add(f.path)

        skipped_files = getattr(plan, "files_to_skip", None) or []
        if not isinstance(skipped_files, (list, tuple, set)):
            skipped_files = []
        for f in skipped_files:
            path = getattr(f, "path", None)
            if path:
                planned_files.add(path)
        
        missing_files = [f for f in changed_files if f not in planned_files]
        
        if missing_files:
            logger.warning(f"Stage 0 missed {len(missing_files)} files, adding to catch-all group")
            catch_all_files = [
                ReviewFile(path=f, focus_areas=["general review"], risk_level="MEDIUM")
                for f in missing_files
            ]
            plan.file_groups.append(
                FileGroup(
                    group_id="uncategorized",
                    priority="MEDIUM",
                    rationale="Files not categorized by initial planning",
                    files=catch_all_files
                )
            )
        
        return plan


def _convert_cross_file_issues(cross_file_issues) -> List[CodeReviewIssue]:
    """
    Convert Stage 2 CrossFileIssue objects into CodeReviewIssue objects
    so they are included in the final issue list posted to the PR.

    Cross-file issues span multiple files. We use the primary_file (or first
    affected file) as the annotation target, and include the codeSnippet for
    server-side line anchoring.
    """
    converted = []
    for cfi in cross_file_issues:
        # Use primary_file if the LLM provided it, otherwise first affected file
        primary_file = (
            cfi.primary_file
            if cfi.primary_file
            else (cfi.affected_files[0] if cfi.affected_files else "cross-file")
        )
        other_files = [f for f in cfi.affected_files if f != primary_file]

        # Build a comprehensive reason from the cross-file issue fields
        reason_parts = [cfi.title]
        if cfi.description:
            reason_parts.append(cfi.description)
        if cfi.evidence:
            reason_parts.append(f"Evidence: {cfi.evidence}")
        if cfi.business_impact:
            reason_parts.append(f"Business impact: {cfi.business_impact}")
        if other_files:
            reason_parts.append(f"Also affects: {', '.join(other_files)}")

        # Use LLM-provided line (hint) and codeSnippet for anchoring.
        # If no line was provided, fall back to 1 — but the codeSnippet
        # will allow SnippetAnchoringService to find the real position.
        issue_line = cfi.line if cfi.line and cfi.line > 0 else 1
        issue_snippet = cfi.codeSnippet or ""

        converted.append(CodeReviewIssue(
            id=cfi.id,
            severity=cfi.severity,
            category=cfi.category,
            file=primary_file,
            line=issue_line,
            title=cfi.title,
            reason="\n".join(reason_parts),
            suggestedFixDescription=cfi.suggestion or "",
            suggestedFixDiff=None,
            isResolved=False,
            codeSnippet=issue_snippet,
        ))
    return converted
