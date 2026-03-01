"""
Multi-Stage Review Orchestrator.

Orchestrates the 4-stage AI code review pipeline:
- Stage 0: Planning & Prioritization
- Stage 1: Parallel File Review  
- Stage 2: Cross-File & Architectural Analysis
- Stage 3: Aggregation & Final Report
"""
import logging
from typing import Dict, Any, List, Optional, Callable

from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from utils.diff_processor import ProcessedDiff
from utils.prompts.prompt_builder import PromptBuilder

from service.review.orchestrator.reconciliation import reconcile_previous_issues, deduplicate_cross_batch_issues, deduplicate_final_issues_llm
from service.review.orchestrator.verification_agent import run_verification_agent
from service.review.orchestrator.stages import (
    execute_branch_analysis,
    execute_branch_reconciliation_direct,
    execute_stage_0_planning,
    execute_stage_1_file_reviews,
    execute_stage_2_cross_file,
    execute_stage_3_aggregation,
    _emit_status,
    _emit_progress,
    _emit_error,
)

logger = logging.getLogger(__name__)


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
        llm_reranker=None
    ):
        self.llm = llm
        self.client = mcp_client
        self.rag_client = rag_client
        self.event_callback = event_callback
        self.llm_reranker = llm_reranker
        self.max_parallel_stage_1 = 5
        self._pr_number: Optional[int] = None
        self._pr_indexed: bool = False

    async def _index_pr_files(
        self,
        request: ReviewRequestDto,
        processed_diff: Optional[ProcessedDiff]
    ) -> None:
        """
        Index PR files into the main RAG collection with PR-specific metadata.
        This enables hybrid queries that prioritize PR data over stale branch data.
        """
        if not self.rag_client or not processed_diff:
            return
        
        pr_number = request.pullRequestId
        if not pr_number:
            logger.info("No PR number, skipping PR file indexing")
            return
        
        files = []
        for f in processed_diff.get_included_files():
            content = f.full_content or f.content
            change_type = f.change_type.value if hasattr(f.change_type, 'value') else str(f.change_type)
            if content and change_type != "DELETED":
                files.append({
                    "path": f.path,
                    "content": content,
                    "change_type": change_type
                })
        
        if not files:
            logger.info("No files to index for PR")
            return
        
        try:
            result = await self.rag_client.index_pr_files(
                workspace=request.projectWorkspace,
                project=request.projectNamespace,
                pr_number=pr_number,
                branch=request.targetBranchName or "unknown",
                files=files
            )
            if result.get("status") == "indexed":
                self._pr_number = pr_number
                self._pr_indexed = True
                logger.info(f"Indexed PR #{pr_number}: {result.get('chunks_indexed', 0)} chunks")
            else:
                logger.warning(f"Failed to index PR files: {result}")
        except Exception as e:
            logger.warning(f"Error indexing PR files: {e}")

    async def _cleanup_pr_files(self, request: ReviewRequestDto) -> None:
        """Delete PR-indexed data after analysis completes."""
        if not self._pr_indexed or not self._pr_number or not self.rag_client:
            return
        
        try:
            await self.rag_client.delete_pr_files(
                workspace=request.projectWorkspace,
                project=request.projectNamespace,
                pr_number=self._pr_number
            )
            logger.info(f"Cleaned up PR #{self._pr_number} indexed data")
        except Exception as e:
            logger.warning(f"Failed to cleanup PR files: {e}")
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
        all_issues: List[Dict[str, Any]] = pr_metadata.get("previousCodeAnalysisIssues", [])

        if not all_issues:
            logger.info("Branch reconciliation: no previous issues — nothing to reconcile")
            return {"issues": [], "comment": "No previous issues to reconcile."}

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
            if file_contents:
                # MCP-free direct path
                prompt = PromptBuilder.build_branch_reconciliation_direct_prompt(
                    pr_metadata, file_contents, raw_diff=raw_diff,
                )
                return await execute_branch_reconciliation_direct(
                    self.llm, prompt, self.event_callback
                )
            else:
                # Legacy MCP path (fallback if no file contents provided)
                prompt = PromptBuilder.build_branch_review_prompt_with_branch_issues_data(
                    pr_metadata
                )
                return await execute_branch_analysis(
                    self.llm, self.client, prompt, self.event_callback
                )

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

    async def orchestrate_review(
        self, 
        request: ReviewRequestDto, 
        rag_context: Optional[Dict[str, Any]] = None,
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
        
        if is_incremental:
            logger.info(f"INCREMENTAL mode: reviewing delta diff, {len(request.previousCodeAnalysisIssues or [])} previous issues to reconcile")
        else:
            logger.info("FULL mode: initial PR review")

        try:
            # Index PR files into RAG for hybrid queries
            await self._index_pr_files(request, processed_diff)
            
            # === STAGE 0: Planning ===
            _emit_status(self.event_callback, "stage_0_started", "Stage 0: Planning & Prioritization...")
            review_plan = await execute_stage_0_planning(
                self.llm, request, is_incremental, processed_diff=processed_diff
            )
            
            review_plan = self._ensure_all_files_planned(review_plan, request.changedFiles or [])
            _emit_progress(self.event_callback, 10, "Stage 0 Complete: Review plan created")
            
            # === STAGE 1: File Reviews ===
            _emit_status(self.event_callback, "stage_1_started", f"Stage 1: Analyzing {self._count_files(review_plan)} files...")
            use_mcp = getattr(request, 'useMcpTools', False) or False
            file_issues = await execute_stage_1_file_reviews(
                self.llm,
                request, 
                review_plan, 
                self.rag_client,
                rag_context, 
                processed_diff, 
                is_incremental,
                self.max_parallel_stage_1,
                self.event_callback,
                self._pr_indexed,
                llm_reranker=self.llm_reranker,
            )
            
            # Cross-batch deduplication
            file_issues = deduplicate_cross_batch_issues(file_issues)
            
            _emit_progress(self.event_callback, 60, f"Stage 1 Complete: {len(file_issues)} issues found across files")

            # === STAGE 1.5: Issue Reconciliation ===
            if request.previousCodeAnalysisIssues:
                _emit_status(self.event_callback, "reconciliation_started", "Reconciling previous issues...")
                file_issues = await reconcile_previous_issues(
                    request, file_issues, processed_diff
                )
                _emit_progress(self.event_callback, 70, f"Reconciliation Complete: {len(file_issues)} total issues after reconciliation")

            # === STAGE 1.5: LLM-Driven Verification ===
            _emit_status(self.event_callback, "verification_started", "Verifying issues against file contents...")
            file_issues = await run_verification_agent(self.llm, file_issues, request)
            _emit_progress(self.event_callback, 75, f"Verification Complete: {len(file_issues)} total issues after verification")

            # === STAGE 2: Cross-File Analysis ===
            _emit_status(self.event_callback, "stage_2_started", "Stage 2: Analyzing cross-file patterns...")
            cross_file_results = await execute_stage_2_cross_file(
                self.llm, request, file_issues, review_plan,
                processed_diff=processed_diff,
                rag_client=self.rag_client,
            )
            # Merge Stage 2 cross-file issues into the issue list
            if cross_file_results.cross_file_issues:
                cross_issues_converted = _convert_cross_file_issues(cross_file_results.cross_file_issues)
                file_issues.extend(cross_issues_converted)
                logger.info(
                    f"Stage 2 contributed {len(cross_issues_converted)} cross-file issues "
                    f"(total issues now: {len(file_issues)})"
                )

            _emit_progress(self.event_callback, 85, "Stage 2 Complete: Cross-file analysis finished")

            # === FINAL DEDUP: after ALL issue-finding stages (1 + 1.5 + 2) ===
            pre_dedup_count = len(file_issues)
            file_issues = await deduplicate_final_issues_llm(self.llm, file_issues)
            if len(file_issues) != pre_dedup_count:
                logger.info(
                    f"Final dedup before Stage 3: {pre_dedup_count} → {len(file_issues)} issues"
                )

            # === STAGE 3: Aggregation ===
            _emit_status(self.event_callback, "stage_3_started", "Stage 3: Generating final report...")
            stage_3_result = await execute_stage_3_aggregation(
                self.llm, request, review_plan, file_issues, cross_file_results,
                is_incremental, processed_diff=processed_diff,
                mcp_client=self.client if use_mcp else None,
                use_mcp_tools=use_mcp,
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

            _emit_progress(self.event_callback, 100, "Stage 3 Complete: Report generated")

            return {
                "comment": final_report,
                "issues": [issue.model_dump() for issue in file_issues],
            }

        except Exception as e:
            logger.error(f"Multi-stage review failed: {e}", exc_info=True)
            _emit_error(self.event_callback, str(e))
            raise
        finally:
            await self._cleanup_pr_files(request)

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

    Cross-file issues span multiple files, so we use the first affected file
    as the primary file and mention all others in the reason text.
    """
    converted = []
    for cfi in cross_file_issues:
        # Use first affected file as the primary location
        primary_file = cfi.affected_files[0] if cfi.affected_files else "cross-file"
        other_files = cfi.affected_files[1:] if len(cfi.affected_files) > 1 else []

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

        converted.append(CodeReviewIssue(
            id=cfi.id,
            severity=cfi.severity,
            category=cfi.category,
            file=primary_file,
            line=1,  # Cross-file issues have no specific line; use 1 (VCS APIs reject 0)
            title=cfi.title,
            reason="\n".join(reason_parts),
            suggestedFixDescription=cfi.suggestion or "",
            suggestedFixDiff=None,
            isResolved=False,
        ))
    return converted
