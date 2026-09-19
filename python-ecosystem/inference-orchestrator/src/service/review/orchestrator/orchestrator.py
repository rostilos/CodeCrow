"""
Multi-Stage Review Orchestrator.

Orchestrates the 4-stage AI code review pipeline:
- Stage 0: Planning & Prioritization
- Stage 1: Parallel File Review  
- Stage 2: Cross-File & Architectural Analysis
- Stage 3: Aggregation & Final Report
"""
import json
import logging
import os
from typing import Dict, Any, List, Optional, Callable

from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from model.multi_stage import CrossFileAnalysisResult
from utils.diff_processor import ProcessedDiff
from utils.hunk_coverage import (
    HunkCoverageLedger,
    validate_acquired_diff_manifest,
)
from utils.prompts.prompt_builder import PromptBuilder

from service.review.orchestrator.reconciliation import (
    reconcile_previous_issues,
    issues_are_conservative_duplicates,
    deduplicate_cross_batch_issues,
    deduplicate_final_issues,
    deduplicate_final_issues_llm,
)
from service.review.orchestrator.verification_agent import (
    _resolve_historical_candidate,
    apply_candidate_provenance_gate,
    canonicalize_prompt_visible_line_anchor,
    previous_open_issue_ids,
    reviewable_hunk_ids_for_issue,
    run_deterministic_evidence_gate,
    run_verification_agent,
)
from service.review.orchestrator.inference_policy import (
    build_review_inference_profile,
    should_run_stage_2,
    should_use_fast_dedup,
    should_use_llm_dedup,
)
from service.review.orchestrator.stage_1_file_review import (
    Stage1RagState,
    Stage1ReviewUnitState,
)
from service.review.orchestrator.stage_2_cross_file import (
    Stage2GenerationError,
    stage_2_coverage_ledger,
)
from utils.path_identity import normalize_repository_path
from service.review.orchestrator.stages import (
    apply_mechanical_skip_constraints,
    execute_branch_analysis,
    execute_branch_reconciliation_direct,
    execute_stage_0_planning,
    execute_stage_1_file_reviews,
    execute_stage_2_cross_file,
    execute_stage_3_aggregation,
    _emit_status,
    _emit_progress,
)
from service.review.plugin_context import (
    apply_plugin_plan_constraints,
    apply_plugin_validation_gate,
)
from service.review.candidate_ledger import CandidateEvidenceLedger
from service.review.snapshot_identity import validate_review_snapshot_identity
from service.review.pr_evidence import (
    PrEvidenceLedger,
    build_pr_evidence_ledger,
    gate_task_coverage_candidates,
)

logger = logging.getLogger(__name__)


def _task_context_value(
    task_context: Optional[Dict[str, Any]],
    *keys: str,
) -> Optional[str]:
    if not task_context:
        return None
    for key in keys:
        value = task_context.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _task_evidence_key(request: ReviewRequestDto) -> Optional[str]:
    task_key = _task_context_value(
        request.taskContext,
        "task_key",
        "taskKey",
        "key",
    )
    if task_key:
        return task_key
    # The server-built history can remain available when the live task-provider
    # lookup is temporarily unavailable. Reuse only its explicit key header.
    history = request.taskHistoryContext or ""
    for line in history.splitlines():
        if line.startswith("Task:"):
            candidate = line.removeprefix("Task:").split(" - ", 1)[0].strip()
            if candidate:
                return candidate
    return None


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


VERIFICATION_ENABLED = _env_bool("REVIEW_VERIFICATION_ENABLED", True)

_REQUEST_RAG_BINDING_FIELDS = (
    "ragCollectionTarget",
    "ragBaseGenerationManifestSha256",
    "ragBasePluginFingerprint",
    "ragBasePluginDescriptorFingerprint",
    "ragBasePluginImplementationFingerprint",
    "ragBaseIndexRepresentationFingerprint",
)


def _clear_request_rag_bindings(request: ReviewRequestDto) -> None:
    """Remove host-provided RAG bindings when the project disables RAG."""
    for field_name in _REQUEST_RAG_BINDING_FIELDS:
        setattr(request, field_name, None)


def _review_log_id(request: ReviewRequestDto) -> str:
    return (
        f"project={getattr(request, 'projectId', 'n/a')}, "
        f"pr={getattr(request, 'pullRequestId', None) or 'n/a'}"
    )


def _emit_review_evidence_completed(
    callback: Optional[Callable[[Dict], None]],
    hunk_coverage: HunkCoverageLedger,
    review_units: Optional[Stage1ReviewUnitState] = None,
    rag_state: Optional[Stage1RagState] = None,
    candidate_ledger: Optional[CandidateEvidenceLedger] = None,
    *,
    request: ReviewRequestDto,
) -> None:
    """Expose compact host-owned completion evidence without prompt/source data."""
    if callback is None:
        return
    unit_owner = review_units.unit_owner if review_units is not None else {}
    completed_units = (
        review_units.completed_unit_ids if review_units is not None else set()
    )
    callback({
        "type": "status",
        "state": "review_evidence_completed",
        "message": "Review manifest, review-unit, and retrieval accounting completed",
        "hunkCoverage": hunk_coverage.summary(),
        "reviewUnits": {
            "registered": len(unit_owner),
            "completed": len(completed_units),
        },
        "candidates": (
            candidate_ledger.summary()
            if candidate_ledger is not None
            else CandidateEvidenceLedger().summary()
        ),
        "hunkReceipts": (
            candidate_ledger.hunk_receipts(
                hunk_coverage.reviewable_hunks
            )
            if candidate_ledger is not None
            else []
        ),
        "retrieval": {
            "deterministicStates": list(
                rag_state.deterministic_retrieval_states
                if rag_state is not None
                else ()
            ),
            "exactEvidenceIds": len(
                rag_state.exact_evidence_by_id if rag_state is not None else {}
            ),
        },
        "revisionBinding": {
            "pullRequestId": request.pullRequestId,
            "targetBranch": request.targetBranchName,
            "sourceRevision": (
                request.currentCommitHash or request.commitHash
            ),
            "baseRevision": request.get_target_head_commit_hash(),
            "baseGenerationManifestSha256": request.ragBaseGenerationManifestSha256,
            "basePluginFingerprint": request.ragBasePluginFingerprint,
            "basePluginDescriptorFingerprint": (
                request.ragBasePluginDescriptorFingerprint
            ),
            "basePluginImplementationFingerprint": (
                request.ragBasePluginImplementationFingerprint
            ),
            "baseIndexRepresentationFingerprint": (
                request.ragBaseIndexRepresentationFingerprint
            ),
        },
    })


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
        agent_service=None,
    ):
        self.llm = llm
        self.client = mcp_client
        self.rag_client = rag_client
        self.event_callback = event_callback
        self.agent_service = agent_service
        self.max_parallel_stage_1 = max(1, _env_int("REVIEW_STAGE1_MAX_PARALLEL", 5))

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
            self.event_callback,
            agent_service=self.agent_service,
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

        # Lifecycle reconciliation owns every persisted issue record. Similar
        # issues may still resolve independently, but none may disappear from
        # prompt ownership merely because their prose looks alike.

        # Determine whether to use MCP-free direct path
        file_contents: Dict[str, str] = {}
        if request.reconciliationFileContents:
            file_contents = request.reconciliationFileContents
            logger.info(
                f"Branch reconciliation: using MCP-free direct path "
                f"({len(file_contents)} pre-fetched files)"
            )

        # Extract raw diff from request (per-file diffs for AI-bound files,
        # pre-filtered by Java)
        raw_diff: Optional[str] = getattr(request, 'rawDiff', None)

        from service.review.orchestrator.branch_reconciliation_packing import (
            execute_packed_branch_reconciliation,
            legacy_reconciliation_fail_open,
        )

        if not file_contents:
            return legacy_reconciliation_fail_open(
                request=request,
                issue_count=len(all_issues),
            )

        branch_profile = build_review_inference_profile(request, None)
        _emit_status(
            self.event_callback,
            "branch_reconciliation_packing",
            "Packing complete branch reconciliation evidence...",
        )
        return await execute_packed_branch_reconciliation(
            llm=self.llm,
            request=request,
            pr_metadata=pr_metadata,
            file_contents=file_contents,
            raw_diff=raw_diff,
            event_callback=self.event_callback,
            direct_executor=execute_branch_reconciliation_direct,
            max_shards=branch_profile.invocation_cap(
                "branch_reconciliation_packets"
            ),
        )

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
        processed_diff: Optional[ProcessedDiff] = None,
        full_pr_processed_diff: Optional[ProcessedDiff] = None,
    ) -> Dict[str, Any]:
        """
        Main entry point for the multi-stage review.
        Supports both FULL (initial review) and INCREMENTAL (follow-up review) modes.
        """
        request_rag_client = self.rag_client if request.ragEnabled else None
        if not request.ragEnabled:
            _clear_request_rag_bindings(request)

        validate_review_snapshot_identity(request)
        validate_acquired_diff_manifest(
            request.changedFiles or (),
            request.deletedFiles or (),
            processed_diff,
        )
        is_incremental = (
            request.analysisMode == "INCREMENTAL" 
            and request.deltaDiff
        )
        
        if is_incremental:
            logger.info(
                "[%s] INCREMENTAL mode: reviewing delta diff, %d previous issues to reconcile",
                _review_log_id(request),
                len(request.previousCodeAnalysisIssues or []),
            )
        else:
            logger.info("[%s] FULL mode: initial PR review", _review_log_id(request))

        pr_evidence_ledger: PrEvidenceLedger = build_pr_evidence_ledger(
            (
                full_pr_processed_diff
                if is_incremental
                else (full_pr_processed_diff or processed_diff)
            ),
            processed_diff,
            incremental=bool(is_incremental),
            task_context=request.taskContext,
            pr_title=request.prTitle or "",
            pr_description=request.prDescription or "",
        )
        logger.info(
            "[%s] PR evidence scopes ready: delta_files=%d, full_pr_files=%d, "
            "prompt_chars=%d, manifest_complete=%s, evidence_complete=%s",
            _review_log_id(request),
            len(processed_diff.files) if processed_diff else 0,
            len(full_pr_processed_diff.files)
            if full_pr_processed_diff is not None
            else (
                len(processed_diff.files)
                if not is_incremental and processed_diff
                else 0
            ),
            pr_evidence_ledger.prompt_chars,
            pr_evidence_ledger.manifest_complete,
            pr_evidence_ledger.full_evidence_complete,
        )

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

        stage_2_visible_prompt_hunk_ids: set[str] = set()
        stage_2_prompt_provenance: Dict[str, str] = {}
        hunk_coverage = HunkCoverageLedger.from_processed_diff(processed_diff)
        candidate_ledger = CandidateEvidenceLedger()

        try:
            if (
                processed_diff is not None
                and not hunk_coverage.reviewable_hunk_ids
                and not request.previousCodeAnalysisIssues
            ):
                hunk_coverage.complete()
                hunk_coverage.assert_complete()
                _emit_review_evidence_completed(
                    self.event_callback,
                    hunk_coverage,
                    candidate_ledger=candidate_ledger,
                    request=request,
                )
                logger.info(
                    "Review completed locally: every acquired hunk has a "
                    "deterministic non-reviewable disposition (%s)",
                    hunk_coverage.summary(),
                )
                _emit_progress(
                    self.event_callback,
                    100,
                    "Review complete: no text source hunks require model analysis",
                )
                return {
                    "comment": (
                        "No text source hunks required model review. Every changed "
                        "hunk was accounted for as generated, excluded, binary, "
                        "deleted, or another deterministic non-reviewable input."
                    ),
                    "issues": [],
                }
            
            # === STAGE 0: Planning ===
            _emit_status(self.event_callback, "stage_0_started", "Stage 0: Planning & Prioritization...")
            review_plan = await execute_stage_0_planning(
                self.llm,
                request,
                is_incremental,
                processed_diff=processed_diff,
                use_local_planning=False,
            )
            review_plan = apply_mechanical_skip_constraints(
                review_plan,
                processed_diff,
            )
            
            review_plan = apply_plugin_plan_constraints(
                review_plan,
                request,
            )
            required_paths = (
                list(hunk_coverage.reviewable_paths)
                if processed_diff is not None
                else list(request.changedFiles or [])
            )
            review_plan = self._ensure_all_files_planned(review_plan, required_paths)
            planned_paths = {
                review_file.path
                for group in review_plan.file_groups
                for review_file in group.files
            }
            hunk_coverage.mark_planned(planned_paths)
            stage_0_message = (
                "Stage 0 Complete: fast bounded review plan created"
                if inference_profile.fast_check_enabled
                else "Stage 0 Complete: Review plan created"
            )
            _emit_progress(self.event_callback, 10, stage_0_message)

            # === STAGE 1: File Reviews ===
            stage_1_rag_state = Stage1RagState()
            stage_1_review_unit_state = Stage1ReviewUnitState()
            logger.info("[%s] Stage 1 starting with %d planned files", _review_log_id(request), self._count_files(review_plan))
            _emit_status(self.event_callback, "stage_1_started", f"Stage 1: Analyzing {self._count_files(review_plan)} files...")
            use_mcp = getattr(request, 'useMcpTools', False) or False
            file_issues = await execute_stage_1_file_reviews(
                self.llm,
                request,
                review_plan,
                request_rag_client,
                processed_diff=processed_diff,
                is_incremental=is_incremental,
                max_parallel=self.max_parallel_stage_1,
                event_callback=self.event_callback,
                fallback_llm=self.llm,
                rag_state=stage_1_rag_state,
                review_unit_state=stage_1_review_unit_state,
                candidate_ledger=candidate_ledger,
                inference_profile=inference_profile,
                agent_service=self.agent_service if use_mcp else None,
            )
            omitted_stage1_hunks = tuple(sorted(
                stage_1_review_unit_state.omitted_hunk_ids
            ))
            if omitted_stage1_hunks:
                hunk_coverage.mark_budget_omitted_hunks(
                    omitted_stage1_hunks,
                    reason=(
                        "finite Stage 1 input/invocation budget: "
                        f"omitted_units={stage_1_review_unit_state.omitted_unit_count}, "
                        "omitted_context_chars="
                        f"{stage_1_review_unit_state.omitted_context_chars}"
                    ),
                )
                logger.warning(
                    "Stage 1 completed with explicit bounded omissions: "
                    "hunks=%d units=%d context_chars=%d",
                    len(omitted_stage1_hunks),
                    stage_1_review_unit_state.omitted_unit_count,
                    stage_1_review_unit_state.omitted_context_chars,
                )
            hunk_coverage.mark_reviewed_hunks(
                stage_1_review_unit_state.reviewed_hunk_ids,
                allow_excluded=True,
            )
            
            # Cross-batch deduplication applies only to active findings.
            # Historical resolutions carry lifecycle identity and must survive
            # even when their original reason resembles a current candidate.
            protected_open_issue_ids = previous_open_issue_ids(request)
            before_cross_batch_dedup = list(file_issues)
            file_issues = _deduplicate_cross_batch_issues_preserving_lifecycle(
                file_issues,
                protected_open_issue_ids,
            )
            candidate_ledger.reject_removed(
                before_cross_batch_dedup,
                file_issues,
                gate="deduplication",
                code="cross_batch_duplicate",
            )
            
            _emit_progress(self.event_callback, 60, f"Stage 1 Complete: {len(file_issues)} issues found across files")

            # === STAGE 1.5: Issue Reconciliation ===
            if request.previousCodeAnalysisIssues:
                _emit_status(self.event_callback, "reconciliation_started", "Reconciling previous issues...")
                file_issues = await reconcile_previous_issues(
                    request,
                    file_issues,
                    processed_diff,
                    candidate_ledger,
                )
                _emit_progress(self.event_callback, 70, f"Reconciliation Complete: {len(file_issues)} total issues after reconciliation")

            # === STAGE 1.5: LLM-Driven Verification ===
            file_issues = apply_candidate_provenance_gate(
                file_issues,
                request,
                processed_diff,
                candidate_ledger,
                stage_1_review_unit_state.units_by_hunk,
            )
            if VERIFICATION_ENABLED:
                _emit_status(self.event_callback, "verification_started", "Verifying issues against file contents...")
                file_issues = await run_verification_agent(
                    self.llm,
                    file_issues,
                    request,
                    processed_diff,
                    candidate_ledger,
                    inference_profile=inference_profile,
                )
                _emit_progress(self.event_callback, 75, f"Verification Complete: {len(file_issues)} total issues after verification")
            else:
                logger.info("Verification skipped by REVIEW_VERIFICATION_ENABLED")
                _emit_status(
                    self.event_callback,
                    "verification_skipped",
                    "Verification skipped by REVIEW_VERIFICATION_ENABLED",
                )

            # === STAGE 2: Cross-File Analysis ===
            run_stage_2, stage_2_reason = should_run_stage_2(
                inference_profile,
                request,
                review_plan,
                file_issues,
            )
            stage_2_degraded = False
            if run_stage_2:
                _emit_status(
                    self.event_callback,
                    "stage_2_started",
                    f"Stage 2: Analyzing cross-file patterns ({stage_2_reason})...",
                )
                try:
                    cross_file_results = await execute_stage_2_cross_file(
                        self.llm,
                        request,
                        file_issues,
                        review_plan,
                        processed_diff=processed_diff,
                        fallback_llm=self.llm,
                        visible_prompt_hunk_ids=stage_2_visible_prompt_hunk_ids,
                        prompt_provenance=stage_2_prompt_provenance,
                        pr_evidence_ledger=pr_evidence_ledger,
                        inference_profile=inference_profile,
                    )
                except Stage2GenerationError as exc:
                    stage_2_degraded = True
                    stage_2_prompt_provenance["degraded"] = "true"
                    stage_2_prompt_provenance[
                        "degradedReason"
                    ] = "response_exhausted"
                    active_severities = {
                        str(issue.severity or "").upper()
                        for issue in file_issues
                        if getattr(issue, "isResolved", False) is not True
                    }
                    risk_level = next(
                        (
                            severity
                            for severity in (
                                "CRITICAL",
                                "HIGH",
                                "MEDIUM",
                                "LOW",
                            )
                            if severity in active_severities
                        ),
                        "LOW",
                    )
                    cross_file_results = CrossFileAnalysisResult(
                        pr_risk_level=risk_level,
                        cross_file_issues=[],
                        pr_recommendation=(
                            "Cross-file synthesis unavailable after response "
                            "exhaustion; validated file-level findings were retained."
                        ),
                        confidence="LOW",
                    )
                    logger.warning(
                        "[%s] Stage 2 degraded after response exhaustion; "
                        "retaining %d validated file-level finding(s): %s",
                        _review_log_id(request),
                        len(file_issues),
                        exc,
                    )
                    _emit_status(
                        self.event_callback,
                        "stage_2_degraded",
                        (
                            "Cross-file synthesis was unavailable; continuing "
                            "with validated file-level findings."
                        ),
                    )
                coverage_gate = gate_task_coverage_candidates(
                    cross_file_results.cross_file_issues,
                    incremental=bool(is_incremental),
                    task_context=request.taskContext,
                    previous_issue_ids=(
                        issue.id
                        for issue in (request.previousCodeAnalysisIssues or ())
                    ),
                    ledger=stage_2_coverage_ledger(
                        pr_evidence_ledger,
                        stage_2_prompt_provenance,
                    ),
                )
                if coverage_gate.rejected:
                    cross_file_results.cross_file_issues = list(
                        coverage_gate.kept
                    )
                    rejection_counts: Dict[str, int] = {}
                    for _, reason in coverage_gate.rejected:
                        rejection_counts[reason] = (
                            rejection_counts.get(reason, 0) + 1
                        )
                    logger.warning(
                        "[%s] Suppressed %d unsupported task-coverage "
                        "candidate(s): %s",
                        _review_log_id(request),
                        len(coverage_gate.rejected),
                        rejection_counts,
                    )
                    _emit_status(
                        self.event_callback,
                        "task_coverage_candidates_suppressed",
                        (
                            "Withheld unsupported PR-wide task-coverage "
                            f"claim(s): {len(coverage_gate.rejected)}"
                        ),
                    )
            else:
                logger.info("Fast check: skipping Stage 2 (%s)", stage_2_reason)
                _emit_status(
                    self.event_callback,
                    "fast_check_stage_2_skipped",
                    f"Fast check: Stage 2 skipped ({stage_2_reason})",
                )
                cross_file_results = CrossFileAnalysisResult(
                    pr_risk_level="LOW",
                    cross_file_issues=[],
                    pr_recommendation="No cross-file risk signals detected in fast check.",
                    confidence="HIGH",
                )
            # Merge Stage 2 cross-file issues into the issue list
            if cross_file_results.cross_file_issues:
                cross_issues_converted = _convert_cross_file_issues(cross_file_results.cross_file_issues)
                _register_stage_2_candidates(
                    cross_issues_converted,
                    request,
                    processed_diff,
                    stage_1_review_unit_state,
                    candidate_ledger,
                    stage_2_visible_prompt_hunk_ids,
                    stage_1_rag_state.exact_evidence_by_id,
                    stage_2_prompt_provenance,
                )
                file_issues.extend(cross_issues_converted)
                logger.info(
                    f"Stage 2 contributed {len(cross_issues_converted)} cross-file issues "
                    f"(total issues now: {len(file_issues)})"
                )

            # Every issue-producing stage is subject to the same source-evidence
            # invariant. Stage 1.5 verifies file issues earlier so Stage 2 does
            # not build on false premises; this final deterministic pass also
            # covers issues newly introduced by Stage 2.
            file_issues = apply_candidate_provenance_gate(
                file_issues,
                request,
                processed_diff,
                candidate_ledger,
                stage_1_review_unit_state.units_by_hunk,
            )
            file_issues = run_deterministic_evidence_gate(
                file_issues,
                request,
                processed_diff,
                candidate_ledger,
            )
            exact_evidence_by_id = dict(
                stage_1_rag_state.exact_evidence_by_id
            )
            file_issues = apply_plugin_validation_gate(
                file_issues,
                request,
                exact_evidence_by_id=exact_evidence_by_id,
                deterministic_retrieval_states=(
                    stage_1_rag_state.deterministic_retrieval_states
                ),
                candidate_ledger=candidate_ledger,
            )
            hunk_coverage.mark_validated()

            _emit_progress(
                self.event_callback,
                85,
                (
                    "Stage 2 Degraded: file-level findings retained"
                    if stage_2_degraded
                    else "Stage 2 Complete: Cross-file analysis finished"
                ),
            )

            # === FINAL DEDUP: after ALL issue-finding stages (1 + 1.5 + 2) ===
            # Historical resolutions are lifecycle updates, not competing
            # findings. Active historical identities do participate so duplicate
            # history and fresh recreations can be consolidated. The merge keeps
            # one persisted identity and emits explicit close updates for any
            # superseded historical IDs.
            active_issues, resolved_lifecycle_issues = _partition_issue_lifecycle(
                file_issues
            )
            pre_dedup_count = len(active_issues)
            if not active_issues:
                deduplicated_active_issues = []
            elif should_use_llm_dedup(
                inference_profile,
                pre_dedup_count,
            ):
                _emit_status(
                    self.event_callback,
                    "final_dedup_started",
                    (
                        "Final dedup: grouped recall-safe semantic dedup for "
                        f"{pre_dedup_count} issue(s)"
                    ),
                )
                deduplicated_active_issues = await deduplicate_final_issues_llm(
                    self.llm,
                    active_issues,
                    max_allowed_tokens=getattr(
                        request,
                        "maxAllowedTokens",
                        None,
                    ),
                )
            else:
                fast_dedup = should_use_fast_dedup(
                    inference_profile,
                    pre_dedup_count,
                )
                _emit_status(
                    self.event_callback,
                    (
                        "fast_check_dedup"
                        if fast_dedup
                        else "deterministic_final_dedup"
                    ),
                    (
                        "Fast check: "
                        if fast_dedup
                        else "Final dedup: "
                    )
                    + (
                        "conservative deterministic dedup for "
                        f"{pre_dedup_count} issue(s)"
                    ),
                )
                deduplicated_active_issues = deduplicate_final_issues(
                    active_issues
                )
            before_final_dedup = list(active_issues)
            candidate_ledger.reject_removed(
                before_final_dedup,
                deduplicated_active_issues,
                gate="deduplication",
                code="final_duplicate",
            )

            retained_object_ids = {
                id(issue) for issue in deduplicated_active_issues
            }
            consolidated_history: List[CodeReviewIssue] = []
            for removed_issue in before_final_dedup:
                if id(removed_issue) in retained_object_ids:
                    continue
                removed_id = str(getattr(removed_issue, "id", "") or "").strip()
                if removed_id not in protected_open_issue_ids:
                    continue
                resolved_copy = _resolved_historical_copy(
                    removed_issue,
                    protected_open_issue_ids,
                    (
                        "Closed because final root-cause deduplication "
                        "consolidated this duplicate into the retained finding."
                    ),
                )
                if resolved_copy is not None:
                    consolidated_history.append(resolved_copy)

            if len(deduplicated_active_issues) != pre_dedup_count:
                logger.info(
                    "Final dedup before Stage 3: %d → %d active root findings "
                    "(%d historical duplicate(s) closed)",
                    pre_dedup_count,
                    len(deduplicated_active_issues),
                    len(consolidated_history),
                )
            file_issues = (
                deduplicated_active_issues
                + resolved_lifecycle_issues
                + consolidated_history
            )

            # Stage 3 receives the structured Stage 2 result separately from the
            # publication list. Keep both views consistent so a candidate rejected
            # by the final publication gate cannot reappear in the prose report.
            removed_cross_file_count = _retain_published_cross_file_issues(
                cross_file_results,
                file_issues,
                preserve_degraded=stage_2_degraded,
            )
            if removed_cross_file_count:
                logger.info(
                    "Removed %d unpublished Stage 2 candidate(s) from final report context",
                    removed_cross_file_count,
                )

            # === STAGE 3: Aggregation ===
            _emit_status(self.event_callback, "stage_3_started", "Stage 3: Generating final report...")
            stage_3_result = await execute_stage_3_aggregation(
                self.llm,
                request,
                review_plan,
                file_issues,
                cross_file_results,
                is_incremental, processed_diff=processed_diff,
                mcp_client=self.client if use_mcp else None,
                use_mcp_tools=use_mcp,
                fallback_llm=self.llm,
                inference_profile=inference_profile,
            )
            final_report = stage_3_result["report"]
            task_key = _task_evidence_key(request)
            task_evidence_payload = (
                pr_evidence_ledger.task_implementation_evidence_payload(task_key)
            )
            dismissed_ids = set(stage_3_result.get("dismissed_issue_ids", []))
            dismissed_object_ids = {
                int(value)
                for value in stage_3_result.get(
                    "dismissed_issue_object_ids",
                    [],
                )
            }

            # A dismissed historical OPEN issue is a lifecycle update, not an
            # omission. Return it as resolved so the client can close the stored
            # record; only genuinely fresh candidates are removed outright.
            if dismissed_ids or dismissed_object_ids:
                before_stage_3_dismissals = list(file_issues)
                file_issues, resolved_count, dropped_count = (
                    _apply_stage_3_dismissals(
                        file_issues,
                        dismissed_ids,
                        protected_open_issue_ids,
                        dismissed_object_ids=dismissed_object_ids,
                    )
                )
                logger.info(
                    "Stage 3 dismissed %d fresh issue(s) and resolved %d "
                    "historical OPEN issue(s) after evidence validation "
                    "(verification keys: %s)",
                    dropped_count,
                    resolved_count,
                    stage_3_result.get("dismissed_issue_keys", []),
                )
                candidate_ledger.reject_removed(
                    before_stage_3_dismissals,
                    file_issues,
                    gate="stage_3_verification",
                    code="dismissed",
                )

            _emit_progress(self.event_callback, 100, "Stage 3 Complete: Report generated")
            hunk_coverage.complete()
            hunk_coverage.assert_complete()
            candidate_ledger.publish(file_issues)
            candidate_ledger.assert_terminal()
            _emit_review_evidence_completed(
                self.event_callback,
                hunk_coverage,
                stage_1_review_unit_state,
                stage_1_rag_state,
                candidate_ledger,
                request=request,
            )
            logger.info("Review hunk coverage complete: %s", hunk_coverage.summary())

            response = {
                "comment": final_report,
                "issues": [
                    _serialize_issue_for_client(issue)
                    for issue in file_issues
                ],
            }
            if task_evidence_payload is not None:
                # Machine-readable auxiliary output is persisted by the Java
                # host. It must never be embedded in a PR or task comment.
                response["taskEvidence"] = task_evidence_payload
            return response

        except Exception as e:
            # ReviewService owns the single terminal diagnostic and error
            # event. Logging/emitting here as well duplicated the same failure
            # at both the orchestration and transport boundaries.
            logger.debug(
                "Multi-stage review failed; propagating to ReviewService: %s",
                e,
                exc_info=True,
            )
            raise
    def _count_files(self, plan) -> int:
        """Count total files in review plan."""
        return sum(len(g.files) for g in plan.file_groups)

    def _ensure_all_files_planned(self, plan, changed_files: List[str]):
        """
        Constrain the probabilistic plan to the host-owned reviewable manifest.

        Stage 0 may omit, duplicate, invent, or request skipping a path. Only
        parser/plugin-proven mechanical exclusions are absent from
        ``changed_files`` by this point, so every path supplied here must have
        exactly one Stage 1 owner.
        """
        from model.multi_stage import ReviewFile, FileGroup

        required_by_key = {}
        for path in changed_files:
            key = normalize_repository_path(path)
            if key and key not in required_by_key:
                required_by_key[key] = path

        planned_keys = set()
        constrained_groups = []
        removed_paths = []
        for group in plan.file_groups:
            normalized_priority = str(group.priority or "").strip().upper()
            if normalized_priority not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
                logger.warning(
                    "Stage 0 supplied unsupported priority %r for group %s; "
                    "using MEDIUM",
                    group.priority,
                    group.group_id,
                )
                normalized_priority = "MEDIUM"
            retained_files = []
            for review_file in group.files:
                key = normalize_repository_path(review_file.path)
                canonical_path = required_by_key.get(key)
                if canonical_path is None or key in planned_keys:
                    removed_paths.append(review_file.path)
                    continue
                planned_keys.add(key)
                if review_file.path != canonical_path:
                    review_file = review_file.model_copy(
                        update={"path": canonical_path}
                    )
                retained_files.append(review_file)
            if retained_files:
                constrained_groups.append(
                    group.model_copy(update={
                        "priority": normalized_priority,
                        "files": retained_files,
                    })
                )
        plan.file_groups = constrained_groups

        if removed_paths:
            logger.warning(
                "Stage 0 supplied %d duplicate or non-reviewable path entries; "
                "removed them before Stage 1",
                len(removed_paths),
            )

        skipped_files = getattr(plan, "files_to_skip", None) or []
        if not isinstance(skipped_files, (list, tuple, set)):
            skipped_files = []
        # The caller passes reviewable paths only. A Stage 0 skip for one of
        # them is advisory model output, not a coverage decision.
        plan.files_to_skip = [
            item
            for item in skipped_files
            if normalize_repository_path(getattr(item, "path", ""))
            not in required_by_key
        ]

        missing_files = [
            canonical_path
            for key, canonical_path in required_by_key.items()
            if key not in planned_keys
        ]
        
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
            evidenceRefs=list(cfi.evidenceRefs or []),
            claimKind=cfi.claimKind or "",
        ))
    return converted


def _register_stage_2_candidates(
    issues: List[CodeReviewIssue],
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
    review_units: Stage1ReviewUnitState,
    candidate_ledger: CandidateEvidenceLedger,
    visible_prompt_hunk_ids: set[str],
    evidence_catalog_by_id: Dict[
        str, tuple[Dict[str, Any], ...]
    ],
    prompt_provenance: Dict[str, str],
) -> None:
    """Tie cross-file candidates back to the completed Stage 1 hunk units."""
    prompt_digest = prompt_provenance.get("generationPromptDigest")
    try:
        issue_prompt_digests = json.loads(
            prompt_provenance.get("issuePromptDigests", "{}")
        )
        issue_prompt_hunks = json.loads(
            prompt_provenance.get("issuePromptHunkIds", "{}")
        )
        issue_prompt_evidence_ids = json.loads(
            prompt_provenance.get("issuePromptEvidenceIds", "{}")
        )
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Stage 2 candidate prompt provenance is malformed"
        ) from exc
    if not isinstance(issue_prompt_digests, dict):
        issue_prompt_digests = {}
    if not isinstance(issue_prompt_hunks, dict):
        issue_prompt_hunks = {}
    if not isinstance(issue_prompt_evidence_ids, dict):
        issue_prompt_evidence_ids = {}
    if not prompt_digest and not issue_prompt_digests:
        raise RuntimeError(
            "Stage 2 candidates have no exact generation prompt provenance"
        )
    for index, issue in enumerate(issues):
        issue_id = str(issue.id or "")
        exact_prompt_digest = issue_prompt_digests.get(issue_id, prompt_digest)
        if not isinstance(exact_prompt_digest, str) or not exact_prompt_digest:
            raise RuntimeError(
                "Stage 2 candidate has no issue-specific generation prompt "
                f"provenance: {issue_id or index}"
            )
        exact_visible_hunks_value = issue_prompt_hunks.get(issue_id)
        if issue_prompt_digests and not isinstance(
            exact_visible_hunks_value,
            list,
        ):
            raise RuntimeError(
                "Stage 2 candidate has no issue-specific visible-hunk "
                f"provenance: {issue_id or index}"
            )
        exact_visible_hunks = {
            str(hunk_id)
            for hunk_id in (
                exact_visible_hunks_value
                if isinstance(exact_visible_hunks_value, list)
                else visible_prompt_hunk_ids
            )
            if isinstance(hunk_id, str) and hunk_id
        }
        exact_visible_evidence_value = issue_prompt_evidence_ids.get(
            issue_id,
            [],
        )
        exact_visible_evidence_ids = {
            str(evidence_id).strip()
            for evidence_id in (
                exact_visible_evidence_value
                if isinstance(exact_visible_evidence_value, list)
                else []
            )
            if isinstance(evidence_id, str) and evidence_id.strip()
        }
        exact_visible_evidence = {
            evidence_id: evidence_catalog_by_id[evidence_id]
            for evidence_id in sorted(exact_visible_evidence_ids)
            if evidence_id in evidence_catalog_by_id
        }
        canonical_hunks = canonicalize_prompt_visible_line_anchor(
            issue,
            processed_diff,
            exact_visible_hunks,
        )
        if canonical_hunks:
            logger.info(
                "Stage 2 canonicalized candidate %s to an exact "
                "prompt-visible source line in hunk %s",
                issue_id or index,
                canonical_hunks[0],
            )
        anchor_hunk_ids = reviewable_hunk_ids_for_issue(
            issue,
            request,
            processed_diff,
        )
        prompt_hunk_ids = tuple(sorted(
            set(anchor_hunk_ids) & exact_visible_hunks
        ))
        unit_ids = tuple(sorted({
            unit_id
            for hunk_id in prompt_hunk_ids
            for unit_id in review_units.units_by_hunk.get(hunk_id, set())
        }))
        candidate_ledger.register(
            issue,
            stage="stage_2",
            source_key=str(index),
            review_unit_ids=unit_ids,
            prompt_hunk_ids=prompt_hunk_ids,
            prompt_digest=exact_prompt_digest,
            visible_evidence_by_id=exact_visible_evidence,
        )


def _retain_published_cross_file_issues(
    cross_file_results: CrossFileAnalysisResult,
    published_issues: List[CodeReviewIssue],
    *,
    preserve_degraded: bool = False,
) -> int:
    """Limit Stage 3 context to findings that passed the publication gate."""
    published_keys = {
        (
            str(issue.id or ""),
            (issue.file or "").lstrip("/"),
            issue.title or "",
        )
        for issue in published_issues
    }

    original = list(cross_file_results.cross_file_issues)
    retained = []
    for issue in original:
        primary_file = (
            issue.primary_file
            if issue.primary_file
            else (issue.affected_files[0] if issue.affected_files else "cross-file")
        )
        key = (str(issue.id or ""), primary_file.lstrip("/"), issue.title or "")
        if key in published_keys:
            retained.append(issue)

    cross_file_results.cross_file_issues = retained

    active_severities = {
        (issue.severity or "").upper()
        for issue in published_issues
        if getattr(issue, "isResolved", False) is not True
    }
    cross_file_results.pr_risk_level = next(
        (
            severity
            for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
            if severity in active_severities
        ),
        "LOW",
    )
    if preserve_degraded:
        degraded_detail = cross_file_results.pr_recommendation
        if "CRITICAL" in active_severities:
            cross_file_results.pr_recommendation = (
                f"FAIL — {degraded_detail}"
            )
        elif active_severities:
            cross_file_results.pr_recommendation = (
                f"PASS_WITH_WARNINGS — {degraded_detail}"
            )
    else:
        if "CRITICAL" in active_severities:
            cross_file_results.pr_recommendation = "FAIL"
        elif active_severities:
            cross_file_results.pr_recommendation = "PASS_WITH_WARNINGS"
        else:
            cross_file_results.pr_recommendation = "PASS"

    return len(original) - len(retained)


def _partition_issue_lifecycle(
    issues: List[CodeReviewIssue],
) -> tuple[List[CodeReviewIssue], List[CodeReviewIssue]]:
    """Separate active findings from historical resolution updates."""
    active: List[CodeReviewIssue] = []
    resolved: List[CodeReviewIssue] = []
    resolved_positions: Dict[str, int] = {}
    for issue in issues:
        if getattr(issue, "isResolved", False) is True:
            issue_id = str(getattr(issue, "id", "") or "").strip()
            existing_position = resolved_positions.get(issue_id) if issue_id else None
            if existing_position is None:
                if issue_id:
                    resolved_positions[issue_id] = len(resolved)
                resolved.append(issue)
            elif (
                _normalized_issue_resolution(issue)
                and not _normalized_issue_resolution(resolved[existing_position])
            ):
                resolved[existing_position] = issue
        else:
            active.append(issue)
    return active, resolved


def _normalized_issue_resolution(issue: CodeReviewIssue) -> Optional[str]:
    for field in ("resolutionReason", "resolutionExplanation"):
        value = getattr(issue, field, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _resolved_historical_copy(
    issue: CodeReviewIssue,
    previous_open_ids: set[str],
    explanation: str,
) -> Optional[CodeReviewIssue]:
    """Create a lifecycle close update without re-publishing rejected provenance.

    Reconciled historical objects can be bound to a generated-candidate ledger
    record. Dedup rejects the superseded candidate object, while this unbound copy
    is returned solely so persistence can close the old database identity.
    """
    if hasattr(issue, "model_copy"):
        resolved = issue.model_copy(deep=True)
    else:
        resolved = CodeReviewIssue(**issue.model_dump())
    if not _resolve_historical_candidate(
        resolved,
        previous_open_ids,
        explanation,
    ):
        return None
    return resolved


def _partition_protected_active_issues(
    active_issues: List[CodeReviewIssue],
    protected_ids: set[str],
) -> tuple[List[CodeReviewIssue], List[CodeReviewIssue]]:
    """Separate fresh candidates from persisted OPEN-history records."""
    fresh: List[CodeReviewIssue] = []
    protected: List[CodeReviewIssue] = []
    for issue in active_issues:
        issue_id = str(getattr(issue, "id", "") or "").strip()
        (protected if issue_id in protected_ids else fresh).append(issue)
    return fresh, protected


def _issues_are_deterministic_duplicates(
    candidate: CodeReviewIssue,
    historical: CodeReviewIssue,
) -> bool:
    return issues_are_conservative_duplicates(candidate, historical)


def _suppress_duplicates_of_protected_history(
    fresh_issues: List[CodeReviewIssue],
    protected_issues: List[CodeReviewIssue],
) -> List[CodeReviewIssue]:
    """Prefer persisted OPEN identity over equivalent fresh candidates."""
    retained: List[CodeReviewIssue] = []
    for candidate in fresh_issues:
        if any(
            _issues_are_deterministic_duplicates(candidate, historical)
            for historical in protected_issues
        ):
            logger.info(
                "Suppressed fresh duplicate of protected historical issue: %s",
                getattr(candidate, "title", None) or candidate.reason[:60],
            )
            continue
        retained.append(candidate)
    return retained


def _deduplicate_cross_batch_issues_preserving_lifecycle(
    issues: List[CodeReviewIssue],
    protected_ids: Optional[set[str]] = None,
) -> List[CodeReviewIssue]:
    """Deduplicate Stage 1 findings while retaining lifecycle close updates.

    Active history participates in exact merging so its persisted identity can
    absorb a fresh, better source anchor. If two persisted OPEN records collapse
    into one root finding, the superseded ID is returned as an explicit resolved
    update instead of disappearing.
    """
    active, resolved = _partition_issue_lifecycle(issues)
    fresh, protected = _partition_protected_active_issues(
        active,
        protected_ids or set(),
    )
    # Preserve the established publication order (fresh, protected, resolved)
    # while still allowing exact merging across the fresh/history boundary.
    ordered_active = fresh + protected
    deduplicated_active = deduplicate_cross_batch_issues(ordered_active)
    retained_object_ids = {id(issue) for issue in deduplicated_active}
    consolidated_history: List[CodeReviewIssue] = []
    for issue in ordered_active:
        if id(issue) in retained_object_ids:
            continue
        resolved_copy = _resolved_historical_copy(
            issue,
            protected_ids or set(),
            (
                "Closed because exact root-cause deduplication consolidated "
                "this duplicate into the retained finding."
            ),
        )
        if resolved_copy is not None:
            consolidated_history.append(resolved_copy)
    return deduplicated_active + resolved + consolidated_history


def _serialize_issue_for_client(issue: CodeReviewIssue) -> Dict[str, Any]:
    """Serialize lifecycle metadata using the field name consumed by Java."""
    data = issue.model_dump()
    if data.get("isResolved") is not True:
        data.pop("resolutionReason", None)
        data.pop("resolutionExplanation", None)
        data.pop("resolvedInCommit", None)
        return data

    resolution = None
    for candidate in (
        data.get("resolutionReason"),
        data.get("resolutionExplanation"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            resolution = candidate.strip()
            break
    if resolution is not None:
        data["resolutionReason"] = resolution
        data["resolutionExplanation"] = resolution
    return data


def _apply_stage_3_dismissals(
    issues: List[CodeReviewIssue],
    dismissed_ids: set[str],
    previous_open_ids: set[str],
    *,
    dismissed_object_ids: Optional[set[int]] = None,
) -> tuple[List[CodeReviewIssue], int, int]:
    """Close verified OPEN history and drop only verified fresh false positives.

    Object identities are preferred because Stage 3 verification IDs cover fresh
    findings that do not have a database ID and avoid touching resolved lifecycle
    records that happen to share a persisted ID.
    """
    normalized_dismissed_ids = {
        str(issue_id).strip()
        for issue_id in dismissed_ids
        if str(issue_id).strip()
    }
    retained: List[CodeReviewIssue] = []
    resolved_count = 0
    dropped_count = 0
    use_object_identity = bool(dismissed_object_ids)

    for issue in issues:
        issue_id = str(getattr(issue, "id", "") or "").strip()
        is_dismissed = (
            id(issue) in (dismissed_object_ids or set())
            if use_object_identity
            else issue_id in normalized_dismissed_ids
        )
        if not is_dismissed:
            retained.append(issue)
            continue

        if _resolve_historical_candidate(
            issue,
            previous_open_ids,
            "Closed because final verification no longer supports the prior finding.",
        ):
            retained.append(issue)
            resolved_count += 1
        else:
            dropped_count += 1

    return retained, resolved_count, dropped_count
