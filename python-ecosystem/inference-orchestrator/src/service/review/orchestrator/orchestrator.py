"""
Multi-Stage Review Orchestrator.

Orchestrates the 4-stage AI code review pipeline:
- Stage 0: Planning & Prioritization
- Stage 1: Parallel File Review  
- Stage 2: Cross-File & Architectural Analysis
- Stage 3: Aggregation & Final Report
"""
import asyncio
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
    with_stage_output_cap,
)
from service.review.orchestrator.stage_1_file_review import (
    Stage1RagState,
    Stage1ReviewUnitState,
)
from utils.path_identity import normalize_repository_path, repository_paths_match
from service.review.orchestrator.stages import (
    apply_mechanical_skip_constraints,
    execute_branch_analysis,
    execute_branch_reconciliation_direct,
    execute_stage_0_planning,
    execute_stage_1_file_reviews,
    execute_stage_2_cross_file,
    prefetch_stage_2_cross_module_context,
    execute_stage_3_aggregation,
    _emit_status,
    _emit_progress,
    _emit_error,
)
from service.review.plugin_context import (
    apply_effective_project_capabilities,
    apply_plugin_plan_constraints,
    apply_plugin_validation_gate,
)
from service.review.candidate_ledger import CandidateEvidenceLedger
from service.review.snapshot_identity import (
    ReviewSnapshotIdentity,
    ReviewSnapshotPreconditionError,
    validate_review_snapshot_identity,
)

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


def _resolve_enrichment_content(
    path: str,
    entries: list[tuple[str, str]],
) -> tuple[Optional[str], bool]:
    """Resolve one repository path without choosing an ambiguous suffix.

    The boolean reports ambiguity. Duplicate candidates with identical content
    are safe because they produce the same immutable artifact.
    """
    normalized_path = normalize_repository_path(path)
    exact_contents = {
        content
        for candidate_path, content in entries
        if normalize_repository_path(candidate_path) == normalized_path
    }
    if len(exact_contents) == 1:
        return next(iter(exact_contents)), False
    if len(exact_contents) > 1:
        return None, True

    suffix_contents = {
        content
        for candidate_path, content in entries
        if repository_paths_match(normalized_path, candidate_path)
    }
    if len(suffix_contents) == 1:
        return next(iter(suffix_contents)), False
    return None, len(suffix_contents) > 1


INTERNAL_PR_INDEX_ENABLED = _env_bool("REVIEW_INTERNAL_PR_INDEX_ENABLED", True)
VERIFICATION_ENABLED = _env_bool("REVIEW_VERIFICATION_ENABLED", True)


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
            "semanticFailures": (
                rag_state.semantic_failures if rag_state is not None else 0
            ),
            "semanticDisabled": (
                rag_state.semantic_disabled if rag_state is not None else False
            ),
            "exactEvidenceIds": len(
                rag_state.exact_evidence_by_id if rag_state is not None else {}
            ),
        },
    })


# Compatibility import for existing callers. Snapshot identity and PR-overlay
# compatibility are both preconditions for the same review context boundary.
PrIndexPreconditionError = ReviewSnapshotPreconditionError


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
        self.max_parallel_stage_1 = max(1, _env_int("REVIEW_STAGE1_MAX_PARALLEL", 5))
        self._pr_number: Optional[int] = None
        self._pr_indexed: bool = False
        self._repository_review_groups: tuple[tuple[str, ...], ...] = ()

    async def _index_pr_files(
        self,
        request: ReviewRequestDto,
        processed_diff: Optional[ProcessedDiff],
        snapshot_identity: Optional[ReviewSnapshotIdentity] = None,
    ) -> None:
        """
        Index PR files into the main RAG collection with PR-specific metadata.
        This enables hybrid queries that prioritize PR data over stale branch data.
        
        Complete post-change source is eligible for PR semantic/plugin indexing.
        When enrichment does not contain it, the unified diff remains review
        evidence but is explicitly marked partial so RAG cannot parse or embed it
        as a complete repository artifact.
        """
        self._repository_review_groups = ()
        if not request.ragEnabled:
            logger.info("PR file indexing skipped because project RAG is disabled")
            return
        if not INTERNAL_PR_INDEX_ENABLED:
            logger.info("PR file indexing disabled by REVIEW_INTERNAL_PR_INDEX_ENABLED")
            return

        if not self.rag_client or not processed_diff:
            return
        
        pr_number = request.pullRequestId
        if not pr_number:
            logger.info("No PR number, skipping PR file indexing")
            return

        identity = (
            snapshot_identity
            if snapshot_identity is not None
            else validate_review_snapshot_identity(request)
        )
        
        # Build lookup from enrichment data so we can populate full_content on DiffFiles.
        # Java sends PrEnrichmentDataDto with fileContents containing the FULL source of
        # each changed file — this is what we want to index, NOT the diff hunks.
        enrichment_entries: list[tuple[str, str]] = []
        if request.enrichmentData and request.enrichmentData.fileContents:
            for fc in request.enrichmentData.fileContents:
                if fc.content is not None and not fc.skipped:
                    enrichment_entries.append((fc.path, fc.content))
            if enrichment_entries:
                logger.info(
                    "Enrichment lookup built: %s entries for PR file indexing",
                    len(enrichment_entries),
                )
        
        files = []
        for f in processed_diff.files:
            raw_change_type = (
                f.change_type.value
                if hasattr(f.change_type, "value")
                else str(f.change_type)
            )
            change_type = raw_change_type.upper()
            if f.is_skipped and change_type != "DELETED":
                continue

            # Prefer exact repository identity. A checkout-prefix suffix is
            # accepted only when all matching candidates contain identical
            # source; ambiguous monorepo paths remain explicitly partial.
            if f.full_content is None and enrichment_entries:
                resolved_content, ambiguous = _resolve_enrichment_content(
                    f.path,
                    enrichment_entries,
                )
                if resolved_content is not None:
                    f.full_content = resolved_content
                elif ambiguous:
                    logger.warning(
                        "PR indexing: ambiguous enrichment source for %s; "
                        "retaining partial diff state",
                        f.path,
                    )
            
            if change_type == "DELETED":
                files.append({
                    "path": f.path,
                    "content": "",
                    "change_type": change_type,
                    "content_state": "complete",
                })
                continue

            has_complete_source = f.full_content is not None
            content = f.full_content if has_complete_source else f.content
            if content is None:
                continue
            content_state = "complete" if has_complete_source else "partial_diff"
            if content_state == "partial_diff":
                logger.warning(
                    "PR indexing: complete source unavailable for %s; "
                    "sending explicitly partial diff evidence",
                    f.path,
                )
            files.append({
                "path": f.path,
                "content": content,
                "change_type": change_type,
                "content_state": content_state,
            })
        
        if not files:
            logger.info("No files to index for PR")
            return
        
        # Set _pr_number BEFORE the indexing call so that cleanup can always
        # run in the finally block, even if indexing partially succeeds then errors.
        self._pr_number = pr_number
        
        try:
            capabilities = request.projectCapabilities
            result = await self.rag_client.index_pr_files(
                workspace=request.projectWorkspace,
                project=request.projectNamespace,
                pr_number=pr_number,
                branch=identity.target_branch,
                base_branch=identity.target_branch,
                source_revision=identity.head_revision,
                base_revision=identity.base_revision,
                repository_plugins=(
                    list(capabilities.repositoryPlugins) if capabilities else []
                ),
                plugin_detection_evidence=(
                    dict(capabilities.detectionEvidence) if capabilities else {}
                ),
                plugin_fingerprint=(
                    capabilities.fingerprint
                    if capabilities
                    else "sha256:" + "0" * 64
                ),
                plugin_descriptor_fingerprint=(
                    capabilities.descriptorFingerprint
                    if capabilities
                    else "sha256:" + "0" * 64
                ),
                files=files
            )
            if result.get("status") in {"indexed", "reused"}:
                apply_effective_project_capabilities(
                    request,
                    result.get("effective_project_capabilities"),
                )
                self._pr_indexed = True
                self._repository_review_groups = tuple(
                    tuple(
                        path for path in group
                        if isinstance(path, str) and path.strip()
                    )
                    for group in (result.get("review_groups") or ())
                    if isinstance(group, (list, tuple))
                )
                logger.info(
                    "%s PR #%s overlay: %s chunks, %s partial files, "
                    "%s repository review groups",
                    "Reused" if result.get("status") == "reused" else "Indexed",
                    pr_number,
                    result.get("chunks_indexed", 0),
                    len(result.get("partial_files") or ()),
                    len(self._repository_review_groups),
                )
            elif result.get("status") == "skipped":
                logger.info("PR indexing skipped: %s", result)
            else:
                status_code = result.get("status_code")
                detail = result.get("error") or result
                raise PrIndexPreconditionError(
                    "PR context precondition failed"
                    f"{f' (HTTP {status_code})' if status_code else ''}: "
                    f"{detail}. No review-model stage was started."
                )
        except PrIndexPreconditionError:
            raise
        except Exception as e:
            raise PrIndexPreconditionError(
                "PR context precondition failed before review-model execution: "
                f"{type(e).__name__}: {e}"
            ) from e

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

        # ── Pre-dedup: eliminate near-duplicate issues BEFORE sending to LLM ──
        # Java may send issues from multiple analyses for the same code location
        # with slightly different titles (LLM phrasing instability).  Dedup here
        # saves tokens and prevents the LLM from producing redundant output.
        pre_dedup_count = len(all_issues)
        all_issues = self._deduplicate_previous_issues(all_issues)
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
                raise RuntimeError(
                    "Branch reconciliation failed atomically at "
                    f"{batch_label}; refusing a partial result from "
                    f"{idx - 1}/{total_batches} completed batches"
                ) from e

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
        snapshot_identity = validate_review_snapshot_identity(request)
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
        stage_2_visible_evidence_by_id: Dict[
            str, tuple[Dict[str, Any], ...]
        ] = {}
        stage_2_visible_prompt_hunk_ids: set[str] = set()
        stage_2_prompt_provenance: Dict[str, str] = {}
        hunk_coverage = HunkCoverageLedger.from_processed_diff(processed_diff)
        candidate_ledger = CandidateEvidenceLedger()

        try:
            # Establish current-source and repository-snapshot compatibility
            # before the first review-model call. A 409 or transport failure is
            # a quality precondition failure, not permission to review using
            # stale branch context. This also avoids paying for Stage 0 when the
            # deterministic context required by later stages cannot be built.
            _emit_status(
                self.event_callback,
                "pr_context_preflight_started",
                "Validating current-source and repository context compatibility...",
            )
            await self._index_pr_files(
                request,
                processed_diff,
                snapshot_identity=snapshot_identity,
            )
            _emit_status(
                self.event_callback,
                "pr_context_preflight_completed",
                "Current-source and repository context compatibility established",
            )

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
                with_stage_output_cap(self.llm, "stage_0", inference_profile),
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
                repository_group_paths=self._repository_review_groups,
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

            if not inference_profile.fast_check_enabled and self.rag_client:
                stage_2_context_task = asyncio.create_task(
                    prefetch_stage_2_cross_module_context(
                        self.rag_client,
                        request,
                        processed_diff=processed_diff,
                        visible_evidence_by_id=stage_2_visible_evidence_by_id,
                    )
                )
            
            # === STAGE 1: File Reviews ===
            stage_1_rag_state = Stage1RagState()
            stage_1_review_unit_state = Stage1ReviewUnitState()
            logger.info("[%s] Stage 1 starting with %d planned files", _review_log_id(request), self._count_files(review_plan))
            _emit_status(self.event_callback, "stage_1_started", f"Stage 1: Analyzing {self._count_files(review_plan)} files...")
            use_mcp = getattr(request, 'useMcpTools', False) or False
            file_issues = await execute_stage_1_file_reviews(
                with_stage_output_cap(self.llm, "stage_1", inference_profile),
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
                use_llm_rerank=not inference_profile.fast_check_enabled,
                fallback_llm=self.llm,
                rag_state=stage_1_rag_state,
                review_unit_state=stage_1_review_unit_state,
                candidate_ledger=candidate_ledger,
            )
            hunk_coverage.mark_reviewed_hunks(
                stage_1_review_unit_state.reviewed_hunk_ids
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
                    with_stage_output_cap(self.llm, "verification", inference_profile),
                    file_issues,
                    request,
                    processed_diff,
                    candidate_ledger,
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
                    rag_client=self.rag_client,
                    fallback_llm=self.llm,
                    prefetched_cross_module_context=prefetched_cross_module_context,
                    visible_evidence_by_id=stage_2_visible_evidence_by_id,
                    visible_prompt_hunk_ids=stage_2_visible_prompt_hunk_ids,
                    prompt_provenance=stage_2_prompt_provenance,
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
                    stage_2_visible_evidence_by_id,
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
            for evidence_id, facts in stage_2_visible_evidence_by_id.items():
                existing = exact_evidence_by_id.get(evidence_id, ())
                exact_evidence_by_id[evidence_id] = tuple(sorted(
                    {
                        json.dumps(
                            fact,
                            sort_keys=True,
                            separators=(",", ":"),
                        ): fact
                        for fact in (*existing, *facts)
                    }.values(),
                    key=lambda fact: json.dumps(
                        fact,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ))
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

            _emit_progress(self.event_callback, 85, "Stage 2 Complete: Cross-file analysis finished")

            # === FINAL DEDUP: after ALL issue-finding stages (1 + 1.5 + 2) ===
            # Historical resolutions are lifecycle updates, not competing
            # findings. Keep them out of both dedup implementations, which are
            # intentionally content-based and could otherwise discard the update.
            active_issues, resolved_lifecycle_issues = _partition_issue_lifecycle(
                file_issues
            )
            fresh_active_issues, protected_active_issues = (
                _partition_protected_active_issues(
                    active_issues,
                    protected_open_issue_ids,
                )
            )
            pre_dedup_count = len(fresh_active_issues)
            if not fresh_active_issues:
                deduplicated_fresh_issues = []
            elif should_use_llm_dedup(
                inference_profile,
                pre_dedup_count,
            ):
                _emit_status(
                    self.event_callback,
                    "final_dedup_started",
                    (
                        "Final dedup: opt-in semantic LLM dedup for "
                        f"{pre_dedup_count} issue(s)"
                    ),
                )
                deduplicated_fresh_issues = await deduplicate_final_issues_llm(
                    with_stage_output_cap(self.llm, "dedup", inference_profile),
                    fresh_active_issues,
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
                deduplicated_fresh_issues = deduplicate_final_issues(
                    fresh_active_issues
                )
            before_final_dedup = list(fresh_active_issues)
            candidate_ledger.reject_removed(
                before_final_dedup,
                deduplicated_fresh_issues,
                gate="deduplication",
                code="final_duplicate",
            )
            before_history_suppression = list(deduplicated_fresh_issues)
            deduplicated_fresh_issues = _suppress_duplicates_of_protected_history(
                deduplicated_fresh_issues,
                protected_active_issues,
            )
            candidate_ledger.reject_removed(
                before_history_suppression,
                deduplicated_fresh_issues,
                gate="deduplication",
                code="duplicate_of_open_history",
            )
            if len(deduplicated_fresh_issues) != pre_dedup_count:
                logger.info(
                    "Final dedup before Stage 3: %d → %d fresh active issues",
                    pre_dedup_count,
                    len(deduplicated_fresh_issues),
                )
            file_issues = (
                deduplicated_fresh_issues
                + protected_active_issues
                + resolved_lifecycle_issues
            )

            # Stage 3 receives the structured Stage 2 result separately from the
            # publication list. Keep both views consistent so a candidate rejected
            # by the final publication gate cannot reappear in the prose report.
            removed_cross_file_count = _retain_published_cross_file_issues(
                cross_file_results,
                file_issues,
            )
            if removed_cross_file_count:
                logger.info(
                    "Removed %d unpublished Stage 2 candidate(s) from final report context",
                    removed_cross_file_count,
                )

            # === STAGE 3: Aggregation ===
            _emit_status(self.event_callback, "stage_3_started", "Stage 3: Generating final report...")
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

            # A dismissed historical OPEN issue is a lifecycle update, not an
            # omission. Return it as resolved so the client can close the stored
            # record; only genuinely fresh candidates are removed outright.
            if dismissed_ids:
                before_stage_3_dismissals = list(file_issues)
                file_issues, resolved_count, dropped_count = (
                    _apply_stage_3_dismissals(
                        file_issues,
                        dismissed_ids,
                        protected_open_issue_ids,
                    )
                )
                logger.info(
                    "Stage 3 dismissed %d fresh issue(s) and resolved %d "
                    "historical OPEN issue(s) (IDs: %s)",
                    dropped_count,
                    resolved_count,
                    dismissed_ids,
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
            )
            logger.info("Review hunk coverage complete: %s", hunk_coverage.summary())

            return {
                "comment": final_report,
                "issues": [
                    _serialize_issue_for_client(issue)
                    for issue in file_issues
                ],
            }

        except Exception as e:
            logger.error(f"Multi-stage review failed: {e}", exc_info=True)
            _emit_error(self.event_callback, str(e))
            raise
        finally:
            if stage_2_context_task and not stage_2_context_task.done():
                stage_2_context_task.cancel()
                try:
                    await stage_2_context_task
                except asyncio.CancelledError:
                    pass
            elif stage_2_context_task and stage_2_context_task.done() and not stage_2_context_task.cancelled():
                try:
                    stage_2_context_task.exception()
                except Exception:
                    pass
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
    visible_evidence_by_id: Dict[
        str, tuple[Dict[str, Any], ...]
    ],
    prompt_provenance: Dict[str, str],
) -> None:
    """Tie cross-file candidates back to the completed Stage 1 hunk units."""
    prompt_digest = prompt_provenance.get("generationPromptDigest")
    if not prompt_digest:
        raise RuntimeError(
            "Stage 2 candidates have no exact generation prompt provenance"
        )
    for index, issue in enumerate(issues):
        anchor_hunk_ids = reviewable_hunk_ids_for_issue(
            issue,
            request,
            processed_diff,
        )
        prompt_hunk_ids = tuple(sorted(
            set(anchor_hunk_ids) & visible_prompt_hunk_ids
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
            prompt_digest=prompt_digest,
            visible_evidence_by_id=visible_evidence_by_id,
        )


def _retain_published_cross_file_issues(
    cross_file_results: CrossFileAnalysisResult,
    published_issues: List[CodeReviewIssue],
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
    """Deduplicate fresh Stage 1 findings without losing historical identity."""
    active, resolved = _partition_issue_lifecycle(issues)
    fresh, protected = _partition_protected_active_issues(
        active,
        protected_ids or set(),
    )
    deduplicated_fresh = deduplicate_cross_batch_issues(fresh)
    deduplicated_fresh = _suppress_duplicates_of_protected_history(
        deduplicated_fresh,
        protected,
    )
    return deduplicated_fresh + protected + resolved


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
) -> tuple[List[CodeReviewIssue], int, int]:
    """Close dismissed OPEN history and drop only fresh false positives."""
    normalized_dismissed_ids = {
        str(issue_id).strip()
        for issue_id in dismissed_ids
        if str(issue_id).strip()
    }
    retained: List[CodeReviewIssue] = []
    resolved_count = 0
    dropped_count = 0

    for issue in issues:
        issue_id = str(getattr(issue, "id", "") or "").strip()
        if issue_id not in normalized_dismissed_ids:
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
