"""Evidence-first pull-request review orchestration."""
import asyncio
import hashlib
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
    issues_are_conservative_duplicates,
    deduplicate_cross_batch_issues,
    deduplicate_final_issues,
)
from service.review.orchestrator.verification_agent import (
    _resolve_historical_candidate,
    apply_candidate_provenance_gate,
    reviewable_hunk_ids_for_issue,
    run_deterministic_evidence_gate,
)
from service.review.orchestrator.inference_policy import (
    build_review_inference_profile,
    with_stage_output_cap,
)
from service.review.orchestrator.stage_1_file_review import (
    Stage1RagState,
    Stage1ReviewUnitState,
)
from service.review.orchestrator.exact_context import (
    ExactContextResolver,
    ReviewFollowupBudget,
)
from utils.path_identity import normalize_repository_path, repository_paths_match
from service.review.orchestrator.stages import (
    apply_mechanical_skip_constraints,
    execute_branch_analysis,
    execute_branch_reconciliation_direct,
    execute_stage_0_planning,
    execute_stage_1_file_reviews,
    _emit_status,
    _emit_progress,
)
from service.review.orchestrator.targeted_cross_file import (
    MAX_CALLS as MAX_FOLLOW_UP_CALLS,
    GeneratedCrossFileCandidate,
    run_targeted_cross_file,
)
from service.review.orchestrator.change_compatibility import (
    run_change_compatibility_review,
)
from service.review.orchestrator.verification_wave import run_verification_wave
from service.review.orchestrator.report_renderer import render_verified_report
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
from service.review.pr_evidence import (
    PrEvidenceLedger,
    STAGE_2_PR_EVIDENCE_CHAR_BUDGET,
    build_pr_evidence_ledger,
)

logger = logging.getLogger(__name__)


def _manifest_text_evidence_gaps(
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
) -> set[str]:
    """Return provider-attested active paths with no selected patch evidence."""
    parsed_paths = {
        normalize_repository_path(item.path)
        for item in (processed_diff.files if processed_diff is not None else ())
        if normalize_repository_path(item.path)
    }
    incremental = (
        str(getattr(request, "analysisMode", "FULL") or "FULL").upper()
        == "INCREMENTAL"
    )
    selected_paths = {
        normalize_repository_path(path)
        for path in (getattr(request, "changedFiles", None) or ())
    }
    return {
        normalize_repository_path(change.path)
        for change in (
            getattr(getattr(request, "pullRequestFileManifest", None), "changes", None)
            or ()
        )
        if str(getattr(change, "kind", "") or "").strip().upper()
        in {"ADDED", "MODIFIED", "COPIED", "UNKNOWN"}
        and normalize_repository_path(change.path)
        and normalize_repository_path(change.path) not in parsed_paths
        and (
            not incremental
            or normalize_repository_path(change.path) in selected_paths
        )
    }


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

_REQUEST_RAG_BINDING_FIELDS = (
    "ragCollectionTarget",
    "ragBaseGenerationManifestSha256",
    "ragPrGenerationFingerprint",
    "ragPrOverlayGenerationManifestSha256",
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
    pr_indexed: bool,
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
        "revisionBinding": {
            "prIndexed": pr_indexed,
            "pullRequestId": request.pullRequestId,
            "targetBranch": request.targetBranchName,
            "sourceRevision": (
                request.currentCommitHash or request.commitHash
            ),
            "baseRevision": request.baseCommitHash,
            "baseGenerationManifestSha256": (
                request.ragBaseGenerationManifestSha256
                if pr_indexed else None
            ),
            "prGenerationFingerprint": (
                request.ragPrGenerationFingerprint
                if pr_indexed else None
            ),
            "prOverlayGenerationManifestSha256": (
                request.ragPrOverlayGenerationManifestSha256
                if pr_indexed else None
            ),
            "basePluginFingerprint": (
                request.ragBasePluginFingerprint
                if pr_indexed else None
            ),
            "basePluginDescriptorFingerprint": (
                request.ragBasePluginDescriptorFingerprint
                if pr_indexed else None
            ),
            "basePluginImplementationFingerprint": (
                request.ragBasePluginImplementationFingerprint
                if pr_indexed else None
            ),
            "baseIndexRepresentationFingerprint": (
                request.ragBaseIndexRepresentationFingerprint
                if pr_indexed else None
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
        self._pr_indexed = False
        request.ragPrGenerationFingerprint = None
        request.ragPrOverlayGenerationManifestSha256 = None
        if not request.ragEnabled:
            _clear_request_rag_bindings(request)
            logger.info("PR file indexing skipped because project RAG is disabled")
            return
        if not INTERNAL_PR_INDEX_ENABLED:
            logger.info("PR file indexing disabled by REVIEW_INTERNAL_PR_INDEX_ENABLED")
            return

        if not self.rag_client or not processed_diff:
            return

        manifest = request.pullRequestFileManifest
        if request.fullPrManifestComplete is not True:
            logger.warning(
                "PR context overlay not mutated because the provider did not "
                "attest a complete base-to-head path manifest (%s); continuing "
                "with local review evidence and the target-branch index",
                (
                    manifest.receipt or manifest.completeness
                    if manifest is not None
                    else "manifest-unavailable"
                ),
            )
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
        emitted_paths: set[str] = set()
        unavailable_current_paths: list[str] = []
        for f in processed_diff.files:
            raw_change_type = (
                f.change_type.value
                if hasattr(f.change_type, "value")
                else str(f.change_type)
            )
            change_type = raw_change_type.upper()
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
                emitted_paths.add(normalize_repository_path(f.path))
                continue

            has_complete_source = f.full_content is not None
            if not has_complete_source:
                skip_reason = str(f.skip_reason or "").lower()
                is_source_free_artifact = (
                    bool(f.is_binary or f.is_gitlink)
                    or f.plugin_disposition in {"excluded", "generated"}
                    or any(
                        marker in skip_reason
                        for marker in (
                            "binary",
                            "gitlink",
                            "generated",
                            "excluded",
                            "unsupported_source",
                            "file_size_limit_exceeded",
                            "total_size_limit_exceeded",
                        )
                    )
                )
                if is_source_free_artifact:
                    files.append({
                        "path": f.path,
                        "content": "",
                        "change_type": change_type,
                        "content_state": "complete",
                    })
                    emitted_paths.add(normalize_repository_path(f.path))
                    continue
                unavailable_current_paths.append(f.path)
                continue
            files.append({
                "path": f.path,
                "content": f.full_content,
                "change_type": change_type,
                "content_state": "complete",
            })
            emitted_paths.add(normalize_repository_path(f.path))

        # A provider manifest represents rename old paths and files deleted in
        # earlier incremental runs even when the current raw diff has no patch
        # for them. Explicit tombstones prevent the base index from resurfacing
        # those stale paths inside the current PR overlay.
        for deleted_path in request.fullPrDeletedFiles or ():
            normalized_deleted = normalize_repository_path(deleted_path)
            if not normalized_deleted or normalized_deleted in emitted_paths:
                continue
            files.append({
                "path": deleted_path,
                "content": "",
                "change_type": "DELETED",
                "content_state": "complete",
            })
            emitted_paths.add(normalized_deleted)

        if unavailable_current_paths:
            logger.warning(
                "PR context overlay not mutated because exact current-head source "
                "is unavailable for %d active manifest path(s): %s; continuing "
                "without binding an incomplete overlay",
                len(unavailable_current_paths),
                ", ".join(unavailable_current_paths[:20]),
            )
            return
        
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
                collection_target=request.ragCollectionTarget,
                base_generation_manifest_sha256=(
                    request.ragBaseGenerationManifestSha256
                ),
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
                base_generation_manifest = (
                    result.get("base_generation_manifest_sha256")
                    or request.ragBaseGenerationManifestSha256
                )
                pr_generation_fingerprint = result.get(
                    "generation_fingerprint"
                )
                overlay_generation_manifest = result.get(
                    "overlay_generation_manifest_sha256"
                )
                request.ragBaseGenerationManifestSha256 = (
                    base_generation_manifest
                )
                request.ragPrGenerationFingerprint = None
                request.ragPrOverlayGenerationManifestSha256 = None
                request.ragBasePluginFingerprint = (
                    result.get("plugin_fingerprint")
                    or request.ragBasePluginFingerprint
                )
                request.ragBasePluginDescriptorFingerprint = (
                    result.get("plugin_descriptor_fingerprint")
                    or request.ragBasePluginDescriptorFingerprint
                )
                request.ragBasePluginImplementationFingerprint = (
                    result.get("plugin_implementation_fingerprint")
                    or request.ragBasePluginImplementationFingerprint
                )
                request.ragBaseIndexRepresentationFingerprint = (
                    result.get("index_representation_fingerprint")
                    or request.ragBaseIndexRepresentationFingerprint
                )
                self._repository_review_groups = tuple(
                    tuple(
                        path for path in group
                        if isinstance(path, str) and path.strip()
                    )
                    for group in (result.get("review_groups") or ())
                    if isinstance(group, (list, tuple))
                )
                complete_overlay_binding = all(
                    isinstance(value, str) and bool(value.strip())
                    for value in (
                        identity.head_revision,
                        identity.base_revision,
                        request.ragCollectionTarget,
                        base_generation_manifest,
                        pr_generation_fingerprint,
                        overlay_generation_manifest,
                    )
                ) and not (result.get("partial_files") or ())
                if complete_overlay_binding:
                    request.ragPrGenerationFingerprint = (
                        pr_generation_fingerprint
                    )
                    request.ragPrOverlayGenerationManifestSha256 = (
                        overlay_generation_manifest
                    )
                    self._pr_indexed = True
                    logger.info(
                        "%s PR #%s overlay: %s chunks, %s partial files, "
                        "%s repository review groups",
                        "Reused" if result.get("status") == "reused" else "Indexed",
                        pr_number,
                        result.get("chunks_indexed", 0),
                        len(result.get("partial_files") or ()),
                        len(self._repository_review_groups),
                    )
                else:
                    self._pr_indexed = False
                    self._repository_review_groups = ()
                    logger.info(
                        "PR #%s overlay was prepared without a complete generation "
                        "lease; continuing with target-branch and local evidence",
                        pr_number,
                    )
            elif result.get("status") == "skipped":
                logger.info("PR indexing skipped: %s", result)
            else:
                status_code = result.get("status_code")
                detail = result.get("error") or result
                log_unavailable = (
                    logger.info if status_code == 409 else logger.warning
                )
                log_unavailable(
                    "PR context indexing unavailable%s; continuing review without "
                    "the PR overlay: %s",
                    f" (HTTP {status_code})" if status_code else "",
                    detail,
                )
        except Exception as e:
            logger.warning(
                "PR context indexing failed before model execution; continuing "
                "without the PR overlay: %s: %s",
                type(e).__name__,
                e,
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
            deleted = await self.rag_client.delete_pr_files(
                workspace=request.projectWorkspace,
                project=request.projectNamespace,
                pr_number=self._pr_number,
                collection_target=request.ragCollectionTarget,
            )
            if deleted:
                logger.info("Cleaned up PR #%s indexed data", self._pr_number)
            else:
                logger.info(
                    "PR #%s indexed-data cleanup did not complete; the RAG "
                    "client recorded the failure detail",
                    self._pr_number,
                )
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
        processed_diff: Optional[ProcessedDiff] = None,
        full_pr_processed_diff: Optional[ProcessedDiff] = None,
    ) -> Dict[str, Any]:
        """
        Main entry point for the multi-stage review.
        Supports both FULL (initial review) and INCREMENTAL (follow-up review) modes.
        """
        request_rag_client = self.rag_client if request.ragEnabled else None
        request_rag_context = rag_context if request.ragEnabled else None
        if not request.ragEnabled:
            _clear_request_rag_bindings(request)

        snapshot_identity = validate_review_snapshot_identity(request)
        if not request.prContextMaintenanceRequired:
            validate_acquired_diff_manifest(
                request.changedFiles or (),
                request.deletedFiles or (),
                processed_diff,
            )
        manifest_text_evidence_gaps = _manifest_text_evidence_gaps(
            request,
            processed_diff,
        )
        is_incremental = (
            request.analysisMode == "INCREMENTAL" 
            and request.deltaDiff
        )
        
        if is_incremental:
            logger.info(
                "[%s] INCREMENTAL mode: reviewing the newest compatible delta; "
                "%d historical issue occurrence(s) remain outside discovery",
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
            provider_manifest_complete=(request.fullPrManifestComplete is True),
            task_context=request.taskContext,
            pr_title=request.prTitle or "",
            pr_description=request.prDescription or "",
        )
        logger.info(
            "[%s] PR evidence scopes ready: delta_files=%d, full_pr_files=%d, "
            "prompt_chars=%d/%d, manifest_complete=%s, evidence_complete=%s",
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
            STAGE_2_PR_EVIDENCE_CHAR_BUDGET,
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
                    "conditional cross-file analysis, and exact candidate deduplication."
                ),
            )
        else:
            logger.info("Fast check not enabled: %s", inference_profile.describe())

        hunk_coverage = HunkCoverageLedger.from_processed_diff(processed_diff)
        candidate_ledger = CandidateEvidenceLedger()

        try:
            # Build the optional current-source overlay before the first model
            # call. RAG/index failures are reported but do not block diff review.
            _emit_status(
                self.event_callback,
                "pr_context_enrichment_started",
                "Preparing optional repository context...",
            )
            await self._index_pr_files(
                request,
                full_pr_processed_diff or processed_diff,
                snapshot_identity=snapshot_identity,
            )
            _emit_status(
                self.event_callback,
                "pr_context_enrichment_completed",
                "Optional repository context preparation completed",
            )

            followup_budget = ReviewFollowupBudget(
                max_calls=MAX_FOLLOW_UP_CALLS,
            )
            exact_context_resolver = ExactContextResolver(
                request,
                file_metadata=(
                    getattr(
                        getattr(request, "enrichmentData", None),
                        "fileMetadata",
                        None,
                    )
                    or ()
                ),
                rag_client=request_rag_client,
                mcp_client=self.client,
            )

            # Deleted files and metadata-only renames do not own a truthful
            # current-side inline hunk. Review those compatibility effects for
            # both deletion-only and mixed PRs so an unrelated text edit cannot
            # hide a broken unchanged caller.
            compatibility = await run_change_compatibility_review(
                with_stage_output_cap(
                    self.llm,
                    "stage_2",
                    inference_profile,
                ),
                request,
                processed_diff,
                exact_context_resolver=exact_context_resolver,
                followup_budget=followup_budget,
                candidate_ledger=candidate_ledger,
            )
            compatibility_issues = list(compatibility.issues)
            _clear_discovery_lifecycle_fields(compatibility_issues)
            if compatibility.changes_considered:
                _emit_status(
                    self.event_callback,
                    "change_compatibility_completed",
                    (
                        "Deletion/rename compatibility check completed: "
                        f"{len(compatibility_issues)} candidate(s), "
                        f"{len(compatibility.incomplete_changes)} incomplete "
                        "change(s)"
                    ),
                )

            if (
                processed_diff is not None
                and not hunk_coverage.reviewable_hunk_ids
            ):
                if compatibility_issues:
                    compatibility_issues = apply_candidate_provenance_gate(
                        compatibility_issues,
                        request,
                        processed_diff,
                        candidate_ledger,
                    )
                    compatibility_issues = run_deterministic_evidence_gate(
                        compatibility_issues,
                        request,
                        processed_diff,
                        candidate_ledger,
                    )
                    compatibility_issues = apply_plugin_validation_gate(
                        compatibility_issues,
                        request,
                        exact_evidence_by_id={},
                        deterministic_retrieval_states=(),
                        candidate_ledger=candidate_ledger,
                    )
                    verification_result = await run_verification_wave(
                        with_stage_output_cap(
                            self.llm,
                            "verification",
                            inference_profile,
                        ),
                        compatibility_issues,
                        request,
                        processed_diff,
                        candidate_ledger,
                    )
                    confirmed = list(verification_result.confirmed)
                    hunk_coverage.mark_validated()
                    final_report = render_verified_report(
                        request,
                        confirmed,
                        incomplete_candidates=(
                            verification_result.incomplete_count
                            + len(compatibility.incomplete_changes)
                            + len(manifest_text_evidence_gaps)
                        ),
                        rejected_candidates=verification_result.rejected_count,
                    )
                    hunk_coverage.complete()
                    hunk_coverage.assert_complete()
                    candidate_ledger.publish(confirmed)
                    candidate_ledger.assert_terminal()
                    _emit_review_evidence_completed(
                        self.event_callback,
                        hunk_coverage,
                        candidate_ledger=candidate_ledger,
                        request=request,
                        pr_indexed=self._pr_indexed,
                    )
                    _emit_progress(
                        self.event_callback,
                        100,
                        "Deletion/rename compatibility review complete",
                    )
                    return {
                        "comment": final_report,
                        "issues": [
                            _serialize_issue_for_client(issue)
                            for issue in confirmed
                        ],
                    }
                hunk_coverage.complete()
                hunk_coverage.assert_complete()
                _emit_review_evidence_completed(
                    self.event_callback,
                    hunk_coverage,
                    candidate_ledger=candidate_ledger,
                    request=request,
                    pr_indexed=self._pr_indexed,
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
                        "No text source hunks required model review. "
                        + (
                            "Provider metadata identified "
                            f"{len(manifest_text_evidence_gaps)} active source "
                            "change(s) whose patch evidence was unavailable; those "
                            "changes were not reviewed. "
                            if manifest_text_evidence_gaps
                            else
                            "Every acquired hunk was accounted for as generated, "
                            "excluded, binary, deleted, or another deterministic "
                            "non-reviewable input. "
                        )
                        + (
                            "Deletion/rename compatibility could not be established "
                            f"for {len(compatibility.incomplete_changes)} change(s) "
                            "because exact current-head related evidence was "
                            "unavailable or the bounded check could not complete."
                            if compatibility.incomplete_changes
                            else
                            "No deletion or rename compatibility defect was proven "
                            "from exact current-head related source."
                        )
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
                request_rag_client,
                request_rag_context,
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
                followup_budget=followup_budget,
                exact_context_resolver=exact_context_resolver,
                mcp_client=self.client,
            )
            hunk_coverage.mark_reviewed_hunks(
                stage_1_review_unit_state.reviewed_hunk_ids
            )
            _clear_discovery_lifecycle_fields(file_issues)
            _emit_progress(
                self.event_callback,
                55,
                f"Stage 1 Complete: {len(file_issues)} candidate(s)",
            )

            # === TARGETED CROSS-FILE INVESTIGATION ===
            _emit_status(
                self.event_callback,
                "cross_file_investigation_started",
                "Investigating exact cross-file hypotheses...",
            )
            targeted_result = await run_targeted_cross_file(
                with_stage_output_cap(self.llm, "stage_2", inference_profile),
                request,
                processed_diff,
                review_plan,
                file_issues,
                context_requests=(
                    stage_1_review_unit_state.cross_file_context_requests
                ),
                exact_context_resolver=exact_context_resolver,
                followup_budget=followup_budget,
                available_calls=followup_budget.remaining,
            )
            _register_targeted_cross_file_candidates(
                targeted_result.candidates,
                request,
                processed_diff,
                stage_1_review_unit_state,
                candidate_ledger,
            )
            targeted_issues = targeted_result.issues
            _clear_discovery_lifecycle_fields(targeted_issues)
            file_issues.extend(targeted_issues)
            file_issues.extend(compatibility_issues)
            if targeted_result.incomplete_tickets:
                _emit_status(
                    self.event_callback,
                    "cross_file_investigation_incomplete",
                    (
                        "Cross-file evidence unavailable or budget-exhausted for "
                        f"{len(targeted_result.incomplete_tickets)} ticket(s); "
                        "dependent claims were withheld"
                    ),
                )
            _emit_progress(
                self.event_callback,
                70,
                (
                    "Cross-file investigation complete: "
                    f"{len(targeted_issues)} candidate(s) from "
                    f"{targeted_result.admitted_tickets} admitted ticket(s)"
                ),
            )

            # Every issue-producing stage now shares one deterministic and
            # adversarial verification path. Historical prose/IDs never enter it.
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
            file_issues = apply_plugin_validation_gate(
                file_issues,
                request,
                exact_evidence_by_id=dict(stage_1_rag_state.exact_evidence_by_id),
                deterministic_retrieval_states=(
                    stage_1_rag_state.deterministic_retrieval_states
                ),
                candidate_ledger=candidate_ledger,
            )
            _emit_status(
                self.event_callback,
                "verification_started",
                f"Verifying {len(file_issues)} candidate(s) against exact source...",
            )
            verification_result = await run_verification_wave(
                with_stage_output_cap(self.llm, "verification", inference_profile),
                file_issues,
                request,
                processed_diff,
                candidate_ledger,
            )
            file_issues = list(verification_result.confirmed)
            hunk_coverage.mark_validated()
            _emit_progress(
                self.event_callback,
                90,
                (
                    f"Verification complete: {len(file_issues)} confirmed, "
                    f"{verification_result.rejected_count} rejected, "
                    f"{verification_result.incomplete_count} incomplete"
                ),
            )

            task_key = _task_evidence_key(request)
            task_evidence_payload = (
                pr_evidence_ledger.task_implementation_evidence_payload(task_key)
            )
            final_report = render_verified_report(
                request,
                file_issues,
                incomplete_candidates=(
                    verification_result.incomplete_count
                    + len(compatibility.incomplete_changes)
                    + len(stage_1_review_unit_state.incomplete_followups)
                    + len(set(targeted_result.incomplete_tickets))
                    + len(manifest_text_evidence_gaps)
                ),
                rejected_candidates=verification_result.rejected_count,
            )
            _emit_progress(self.event_callback, 100, "Verified report rendered")
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
                pr_indexed=self._pr_indexed,
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
        finally:
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


def _clear_discovery_lifecycle_fields(issues: List[CodeReviewIssue]) -> None:
    """Treat model output as fresh candidates, never as database lifecycle input."""
    for issue in issues:
        issue.id = None
        issue.isResolved = False
        issue.resolutionReason = None
        issue.resolutionExplanation = None
        issue.resolvedInCommit = None
        issue.visibility = None


def _register_targeted_cross_file_candidates(
    generated_candidates: tuple[GeneratedCrossFileCandidate, ...],
    request: ReviewRequestDto,
    processed_diff: Optional[ProcessedDiff],
    review_units: Stage1ReviewUnitState,
    candidate_ledger: CandidateEvidenceLedger,
) -> None:
    """Register targeted cross-file output against its exact changed trigger."""
    for generated in generated_candidates:
        issue = generated.issue
        evidence_digest = hashlib.sha256(
            generated.ticket.related_source.encode("utf-8")
        ).hexdigest()
        evidence_id = "XCTX-" + evidence_digest[:20]
        issue.evidenceRefs = list(dict.fromkeys([
            *(getattr(issue, "evidenceRefs", None) or ()),
            evidence_id,
        ]))
        anchor_hunk_ids = reviewable_hunk_ids_for_issue(
            issue,
            request,
            processed_diff,
        )
        unit_ids = tuple(sorted({
            unit_id
            for hunk_id in anchor_hunk_ids
            for unit_id in review_units.units_by_hunk.get(hunk_id, set())
        }))
        candidate_ledger.register(
            issue,
            stage="cross_file_investigation",
            source_key=generated.ticket.ticket_id,
            review_unit_ids=unit_ids,
            prompt_hunk_ids=anchor_hunk_ids,
            prompt_digest=generated.prompt_digest,
            visible_evidence_by_id={
                evidence_id: ({
                    "path": generated.ticket.related_file,
                    "revision": (
                        request.currentCommitHash or request.commitHash or ""
                    ),
                    "startLine": generated.ticket.related_start_line,
                    "endLine": (
                        generated.ticket.related_start_line
                        + max(
                            0,
                            len(generated.ticket.related_source.splitlines()) - 1,
                        )
                    ),
                    "contentDigest": "sha256:" + evidence_digest,
                    "source": "cross-file-exact-context",
                    "content": generated.ticket.related_source,
                },),
            },
        )


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
    """Serialize the verified internal issue contract consumed by Java.

    Causal evidence is excluded from the public Pydantic dump and report, but
    Java needs it to derive a category-independent lineage fingerprint.
    """
    data = issue.model_dump()
    data.update({
        "triggerCondition": issue.triggerCondition,
        "causalPath": issue.causalPath,
        "observableImpact": issue.observableImpact,
    })
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
