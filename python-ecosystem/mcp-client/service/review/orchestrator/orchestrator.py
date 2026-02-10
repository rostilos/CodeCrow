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
from utils.diff_processor import ProcessedDiff

from service.review.orchestrator.reconciliation import reconcile_previous_issues
from service.review.orchestrator.stages import (
    execute_branch_analysis,
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
        event_callback: Optional[Callable[[Dict], None]] = None
    ):
        self.llm = llm
        self.client = mcp_client
        self.rag_client = rag_client
        self.event_callback = event_callback
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
                self._pr_indexed
            )
            _emit_progress(self.event_callback, 60, f"Stage 1 Complete: {len(file_issues)} issues found across files")

            # === STAGE 1.5: Issue Reconciliation ===
            if request.previousCodeAnalysisIssues:
                _emit_status(self.event_callback, "reconciliation_started", "Reconciling previous issues...")
                file_issues = await reconcile_previous_issues(
                    request, file_issues, processed_diff
                )
                _emit_progress(self.event_callback, 70, f"Reconciliation Complete: {len(file_issues)} total issues after reconciliation")

            # === STAGE 2: Cross-File Analysis ===
            _emit_status(self.event_callback, "stage_2_started", "Stage 2: Analyzing cross-file patterns...")
            cross_file_results = await execute_stage_2_cross_file(
                self.llm, request, file_issues, review_plan,
                processed_diff=processed_diff,
            )
            _emit_progress(self.event_callback, 85, "Stage 2 Complete: Cross-file analysis finished")
            
            # === STAGE 3: Aggregation ===
            _emit_status(self.event_callback, "stage_3_started", "Stage 3: Generating final report...")
            final_report = await execute_stage_3_aggregation(
                self.llm, request, review_plan, file_issues, cross_file_results,
                is_incremental, processed_diff=processed_diff
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
