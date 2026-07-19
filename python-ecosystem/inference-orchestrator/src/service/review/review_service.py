import os
import asyncio
import hashlib
import inspect
import json
import logging
from datetime import datetime
from dataclasses import asdict
from time import monotonic_ns
from typing import Dict, Any, Optional, Callable
from dotenv import load_dotenv
from mcp_use import MCPClient

from model.coverage import CoverageLedgerV1
from model.dtos import ReviewRequestDto
from utils.mcp_config import MCPConfigBuilder
from llm.llm_factory import LLMFactory
from utils.prompts.prompt_builder import PromptBuilder
from utils.prompts import prompt_constants
from utils.response_parser import ResponseParser
from service.rag.rag_client import RagClient, RAG_DEFAULT_TOP_K
from service.rag.llm_reranker import LLMReranker
from service.review.issue_processor import post_process_analysis_result
from service.review.execution_context import (
    ExecutionEventBindingError,
    bind_execution_context,
    bind_owned_execution_event,
    is_manifest_bound_v1,
)
from utils.context_builder import (RAGMetrics, get_rag_cache)
from utils.diff_processor import DiffProcessor
from utils.error_sanitizer import create_user_friendly_error
from service.review.orchestrator import MultiStageReviewOrchestrator
from service.review.coverage import ExecutionCoverageTracker
from service.review.agentic.engine import (
    AgenticReviewEngine,
    agentic_prompt_attribution_material,
)
from service.review.agentic.mcp_adapter import AgenticMcpAdapter
from service.review.agentic.tool_gateway import AgenticToolGateway
from service.review.agentic.workspace import AgenticWorkspace
from service.review.telemetry import (
    CandidateCounts,
    CoverageCounts,
    ExecutionIdentity,
    ExecutionTelemetryRecorder,
    MemoryTelemetrySink,
    ModelPricing,
    StageOutcome,
    TerminalOutcome,
    VersionAttribution,
    bind_telemetry,
    current_telemetry,
    reset_telemetry,
    trace_document,
)

logger = logging.getLogger(__name__)

class ReviewService:
    """Service class for handling code review requests with streaming support."""
    
    # Maximum retries for LLM-based response fixing
    MAX_FIX_RETRIES = 2

    # Maximum concurrent reviews (each spawns a JVM subprocess + LLM calls)
    MAX_CONCURRENT_REVIEWS = int(os.environ.get("MAX_CONCURRENT_REVIEWS", "4"))

    # Hard timeout ceiling per review (seconds). Configurable via .env
    REVIEW_TIMEOUT_SECONDS = int(os.environ.get("REVIEW_TIMEOUT_SECONDS", "1500"))
    GLOBAL_RAG_QUERY_TIMEOUT_SECONDS = int(os.environ.get("REVIEW_GLOBAL_RAG_QUERY_TIMEOUT_SECONDS", "5"))
    agentic_workspace_root = os.environ.get(
        "AGENTIC_WORKSPACE_ROOT", "/tmp/codecrow-agentic"
    )
    AGENTIC_WORKSPACE_TTL_SECONDS = int(
        os.environ.get("AGENTIC_WORKSPACE_TTL_SECONDS", "21600")
    )

    def __init__(self):
        load_dotenv(interpolate=False)
        self.default_jar_path = os.environ.get(
            "MCP_SERVER_JAR",
            #"/var/www/html/codecrow/codecrow-public/java-ecosystem/mcp-servers/vcs-mcp/target/codecrow-vcs-mcp-1.0.jar",
            "/app/codecrow-vcs-mcp-1.0.jar"
        )
        self.rag_client = RagClient()
        self.rag_cache = get_rag_cache()
        self._review_semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_REVIEWS)
        try:
            AgenticWorkspace.cleanup_stale(
                self.agentic_workspace_root,
                ttl_seconds=self.AGENTIC_WORKSPACE_TTL_SECONDS,
            )
        except Exception as error:
            logger.warning(
                "Agentic workspace startup cleanup failed: %s",
                type(error).__name__,
            )

    async def process_review_request(
            self,
            request: ReviewRequestDto,
            event_callback: Optional[Callable[[Dict], None]] = None
    ) -> Dict[str, Any]:
        """
        Process a review request with optional event streaming.

        Args:
            request: The review request data
            event_callback: Optional callback to receive progress events
                          Expected signature: callback(event: Dict) -> None
                          Events have structure: {"type": "status|progress|error|final", ...}

        Returns:
            Dict with "result" key containing the analysis result or error
        """
        request = bind_execution_context(request)
        event_callback = self._owned_execution_event_callback(
            request,
            event_callback,
        )
        coverage_ledger = getattr(request, "coverageLedger", None)
        coverage_tracker = (
            ExecutionCoverageTracker(coverage_ledger)
            if isinstance(coverage_ledger, CoverageLedgerV1)
            else None
        )
        if (
            coverage_tracker is not None
            and coverage_tracker.open_mandatory_total == 0
            and not (
                self._is_agentic(request)
                and self._has_bound_previous_findings(request)
            )
        ):
            # An AGENTIC archive has already been staged by Java. Consume the
            # context even when the ledger is empty so no execution directory
            # is left behind waiting for stale cleanup.
            skipped_entries: list[dict[str, object]] = []
            if self._is_agentic(request):
                async with self._review_semaphore:
                    workspace = self._agentic_workspace(request)
                    async with workspace:
                        pass
                    raw_skipped = getattr(workspace, "skipped_entries", [])
                    skipped_entries = (
                        list(raw_skipped) if isinstance(raw_skipped, list) else []
                    )
            receipt = coverage_tracker.finalize().model_dump(mode="json")
            approach = "AGENTIC" if self._is_agentic(request) else "CLASSIC"
            cleanup = (
                {
                    "agenticReview": {
                        "workItems": 0,
                        "reviewedWorkItems": 0,
                        "failedWorkItems": 0,
                        "publishedFindings": 0,
                        "filteredFindings": 0,
                        "failedBatches": 0,
                        "hypotheses": [],
                        "toolUsage": {},
                        "workspaceCleanup": "complete",
                        "skippedArchiveEntries": len(skipped_entries),
                        "skippedArchiveBytes": sum(
                            int(item["byteLength"]) for item in skipped_entries
                        ),
                        "skippedArchivePaths": [
                            str(item["path"]) for item in skipped_entries[:20]
                        ],
                    }
                }
                if approach == "AGENTIC"
                else {}
            )
            return {
                "result": {
                    "analysisState": receipt["analysisState"],
                    "comment": (
                        "No mandatory coverage anchors were present in this pull request."
                        if receipt["analysisState"] == "EMPTY"
                        else "The exact diff contains no text anchors the model can examine."
                    ),
                    "issues": [],
                    "coverageReceipt": receipt,
                    "reviewApproach": approach,
                    **cleanup,
                }
            }
        async with self._review_semaphore:
            recorder, sink = self._create_telemetry_recorder(request)
            telemetry_token = bind_telemetry(recorder)
            started_ns = monotonic_ns()
            try:
                if self._is_agentic(request):
                    workspace = self._agentic_workspace(request)
                    async with workspace as repo_path:
                        result = await self._process_review(
                            request=request,
                            repo_path=str(repo_path),
                            event_callback=event_callback,
                            coverage_tracker=coverage_tracker,
                        )
                    result = self._attach_agentic_cleanup(result)
                    result = self._attach_agentic_workspace_diagnostics(
                        result,
                        workspace,
                    )
                else:
                    result = await self._process_review(
                        request=request,
                        repo_path=None,
                        event_callback=event_callback,
                        coverage_tracker=coverage_tracker,
                    )
                result = self._attach_coverage_receipt(result, coverage_tracker)
            except asyncio.CancelledError:
                try:
                    self._attach_terminal_telemetry(
                        request=request,
                        result={"result": {"status": "cancelled", "issues": []}},
                        recorder=recorder,
                        sink=sink,
                        started_ns=started_ns,
                        event_callback=event_callback,
                        forced_outcome=TerminalOutcome.CANCELLED,
                        forced_reason="analysis_cancelled",
                    )
                except Exception as error:
                    logger.warning(
                        "Cancellation telemetry rejected: %s", type(error).__name__
                    )
                raise
            finally:
                reset_telemetry(telemetry_token)
            return self._attach_terminal_telemetry(
                request=request,
                result=result,
                recorder=recorder,
                sink=sink,
                started_ns=started_ns,
                event_callback=event_callback,
            )

    @staticmethod
    def _is_agentic(request: ReviewRequestDto) -> bool:
        approach = getattr(request, "reviewApproach", "CLASSIC")
        value = getattr(approach, "value", approach)
        return value == "AGENTIC"

    @staticmethod
    def _has_bound_previous_findings(request: ReviewRequestDto) -> bool:
        enrichment = getattr(request, "enrichmentData", None)
        review_context = getattr(enrichment, "reviewContext", None)
        previous_findings = getattr(review_context, "previousFindings", None)
        return (
            isinstance(previous_findings, (list, tuple))
            and len(previous_findings) > 0
        )

    def _agentic_workspace(self, request: ReviewRequestDto) -> AgenticWorkspace:
        manifest = request.executionManifest
        descriptor = request.agenticRepository
        if manifest is None or descriptor is None:
            raise ValueError(
                "AGENTIC review requires an exact manifest and repository archive"
            )
        return AgenticWorkspace(
            self.agentic_workspace_root,
            descriptor,
            expected_head_sha=manifest.headSha,
        )

    @staticmethod
    def _attach_agentic_cleanup(result: Dict[str, Any]) -> Dict[str, Any]:
        current = result.get("result") if isinstance(result, dict) else None
        if not isinstance(current, dict):
            return result
        attached = dict(result)
        analysis = dict(current)
        diagnostics = analysis.get("agenticReview")
        diagnostics = dict(diagnostics) if isinstance(diagnostics, dict) else {}
        diagnostics["workspaceCleanup"] = "complete"
        analysis["agenticReview"] = diagnostics
        attached["result"] = analysis
        return attached

    @staticmethod
    def _attach_agentic_workspace_diagnostics(
        result: Dict[str, Any],
        workspace: AgenticWorkspace,
    ) -> Dict[str, Any]:
        current = result.get("result") if isinstance(result, dict) else None
        if not isinstance(current, dict):
            return result
        attached = dict(result)
        analysis = dict(current)
        diagnostics = analysis.get("agenticReview")
        diagnostics = dict(diagnostics) if isinstance(diagnostics, dict) else {}
        skipped = list(workspace.skipped_entries)
        diagnostics["skippedArchiveEntries"] = len(skipped)
        diagnostics["skippedArchiveBytes"] = sum(
            int(item["byteLength"]) for item in skipped
        )
        diagnostics["skippedArchivePaths"] = [
            str(item["path"]) for item in skipped[:20]
        ]
        analysis["agenticReview"] = diagnostics
        attached["result"] = analysis
        return attached

    @staticmethod
    def _attach_coverage_receipt(
        result: Dict[str, Any],
        tracker: Optional[ExecutionCoverageTracker],
    ) -> Dict[str, Any]:
        if tracker is None:
            return result
        receipt = tracker.finalize().model_dump(mode="json")
        current = result.get("result") if isinstance(result, dict) else None
        analysis_result = dict(current) if isinstance(current, dict) else {}
        analysis_result.setdefault("issues", [])
        analysis_result["analysisState"] = receipt["analysisState"]
        analysis_result["coverageReceipt"] = receipt
        if receipt["analysisState"] == "PARTIAL" and not analysis_result["issues"]:
            analysis_result["comment"] = (
                "Analysis is incomplete because mandatory diff coverage "
                "was not completed."
            )
        attached = dict(result)
        attached["result"] = analysis_result
        return attached

    @staticmethod
    def _owned_execution_event_callback(
        request: ReviewRequestDto,
        callback: Optional[Callable[[Dict], None]],
    ) -> Optional[Callable[[Dict], None]]:
        """Bind events constructed by the local review pipeline before egress."""

        if callback is None or request.executionManifest is None:
            return callback
        manifest = request.executionManifest

        def emit_owned(event: Dict[str, Any]) -> None:
            callback(bind_owned_execution_event(event, manifest))

        return emit_owned

    @staticmethod
    def _create_telemetry_recorder(
        request: ReviewRequestDto,
    ) -> tuple[Optional[ExecutionTelemetryRecorder], MemoryTelemetrySink]:
        sink = MemoryTelemetrySink()
        manifest = request.executionManifest
        try:
            prompt_version, rules_version = ReviewService._active_configuration_versions(
                request
            )
            if manifest is not None:
                identity = ExecutionIdentity(
                    execution_id=manifest.executionId,
                    base_revision=manifest.baseSha,
                    head_revision=manifest.headSha,
                    artifact_manifest_digest=manifest.artifactManifestDigest,
                    review_approach=(
                        "AGENTIC"
                        if ReviewService._is_agentic(request)
                        else "CLASSIC"
                    ),
                )
                policy_version = manifest.policyVersion
            else:
                identity = ExecutionIdentity(
                    execution_id=request.executionId or "",
                    base_revision=request.baseRevision or "",
                    head_revision=request.headRevision or "",
                    review_approach=(
                        "AGENTIC"
                        if ReviewService._is_agentic(request)
                        else "CLASSIC"
                    ),
                )
                policy_version = request.policyVersion
            versions = VersionAttribution(
                provider=request.aiProvider,
                model=request.aiModel,
                prompt_version=prompt_version,
                rules_version=rules_version,
                policy_version=policy_version,
                index_version=request.indexVersion,
            )
            return (
                ExecutionTelemetryRecorder(
                    identity=identity,
                    versions=versions,
                    sink=sink,
                    default_deadline_ms=ReviewService.REVIEW_TIMEOUT_SECONDS * 1000,
                    model_pricing=ModelPricing.from_values(
                        request.inputPricePerMillion,
                        request.outputPricePerMillion,
                    ),
                ),
                sink,
            )
        except Exception as error:
            logger.warning(
                "Telemetry recorder initialization rejected: %s",
                type(error).__name__,
            )
            return None, sink

    @staticmethod
    def _active_configuration_versions(
        request: ReviewRequestDto,
    ) -> tuple[str, str]:
        """Hash the prompt implementation and the effective project-rule input.

        These identities are derived at execution time.  Request-supplied
        labels cannot claim a prompt/rule version that was not actually used.
        """

        prompt_material = {
            name: value
            for name, value in vars(prompt_constants).items()
            if name.isupper() and isinstance(value, str)
        }
        prompt_material["PromptBuilder"] = inspect.getsource(PromptBuilder)
        if ReviewService._is_agentic(request):
            prompt_material["AgenticReview"] = (
                agentic_prompt_attribution_material()
            )
        prompt_bytes = json.dumps(
            prompt_material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

        raw_rules = request.projectRules or "[]"
        try:
            rules_material: Any = json.loads(raw_rules)
        except (TypeError, ValueError):
            rules_material = {"invalid_rules_sha256": hashlib.sha256(
                str(raw_rules).encode("utf-8")
            ).hexdigest()}
        rules_bytes = json.dumps(
            rules_material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return (
            "prompt-sha256-" + hashlib.sha256(prompt_bytes).hexdigest(),
            "rules-sha256-" + hashlib.sha256(rules_bytes).hexdigest(),
        )

    def _attach_terminal_telemetry(
        self,
        *,
        request: ReviewRequestDto,
        result: Dict[str, Any],
        recorder: Optional[ExecutionTelemetryRecorder],
        sink: MemoryTelemetrySink,
        started_ns: int,
        event_callback: Optional[Callable[[Dict], None]],
        forced_outcome: Optional[TerminalOutcome] = None,
        forced_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        if recorder is None:
            self._emit_event(
                event_callback,
                {
                    "type": "telemetry",
                    "state": "not_emitted",
                    "reason": "exact_revision_identity_unavailable",
                },
            )
            return result

        coverage_inventory_available = bool(request.rawDiff)
        coverage = recorder.latest_coverage
        if coverage is None:
            try:
                processed_diff = DiffProcessor().process(request.rawDiff or "")
                inventory = sum(
                    len(diff_file.hunks) for diff_file in processed_diff.files
                )
                # Without a planner/stage receipt no hunk is assumed to have
                # been represented merely because preprocessing retained it.
                coverage = CoverageCounts(
                    inventory=inventory,
                    represented=0,
                    unrepresented=inventory,
                )
            except Exception as error:
                logger.warning("Coverage telemetry rejected: %s", type(error).__name__)
                coverage_inventory_available = False
                coverage = CoverageCounts()
        analysis_result = result.get("result") if isinstance(result, dict) else None
        error_result = (
            isinstance(result, dict)
            and "error" in result
            or isinstance(analysis_result, dict)
            and analysis_result.get("status") == "error"
        )
        issues = analysis_result.get("issues", []) if isinstance(analysis_result, dict) else []
        issue_count = len(issues) if isinstance(issues, list) else 0
        usage = recorder.model_usage

        outcome = TerminalOutcome.COMPLETE
        reason = None
        if error_result:
            outcome = TerminalOutcome.FAILED
            reason = "analysis_failed"
        elif not coverage_inventory_available:
            outcome = TerminalOutcome.PARTIAL
            reason = "coverage_inventory_unavailable"
        elif coverage.unrepresented:
            outcome = TerminalOutcome.PARTIAL
            reason = "coverage_incomplete"
        elif recorder.has_incomplete_operations:
            outcome = TerminalOutcome.PARTIAL
            reason = "stage_or_call_incomplete"
        elif usage.provider_usage_missing_calls:
            outcome = TerminalOutcome.PARTIAL
            reason = "provider_usage_unavailable"
        elif usage.cost_estimate_missing_calls:
            outcome = TerminalOutcome.PARTIAL
            reason = "cost_estimate_unavailable"
        elif request.indexVersion in (None, "legacy-index-unversioned", "rag-version-unavailable"):
            outcome = TerminalOutcome.PARTIAL
            reason = "index_version_unavailable"
        if forced_outcome is not None:
            outcome = forced_outcome
            reason = forced_reason

        try:
            trace = recorder.provisional_snapshot(
                outcome=outcome,
                duration_ms=max(0, (monotonic_ns() - started_ns) // 1_000_000),
                usage=usage,
                candidates=CandidateCounts(
                    input=len(request.previousCodeAnalysisIssues or []),
                    produced=issue_count,
                    retained=issue_count,
                ),
                coverage=coverage,
                reason=reason,
            )
        except Exception as error:
            logger.warning("Terminal telemetry rejected: %s", type(error).__name__)
            return result

        try:
            telemetry_document = {
                "schemaVersion": 1,
                "finalizationState": "pending_java",
                "trace": trace_document(trace),
                "metric": None,
                "sinkErrors": list(recorder.sink_errors),
            }
        except Exception as error:
            logger.warning("Telemetry artifact rejected: %s", type(error).__name__)
            return result
        if isinstance(analysis_result, dict):
            analysis_result = dict(analysis_result)
            analysis_result["telemetry"] = telemetry_document
            result = dict(result)
            result["result"] = analysis_result
        self._emit_event(
            event_callback,
            {
                "type": "telemetry",
                "state": "provisional",
                "outcome": outcome.value,
                "reason": reason,
            },
        )
        return result

    @staticmethod
    def _record_retrieval_telemetry(
        *,
        outcome: StageOutcome,
        started_ns: int,
        input_count: int,
        output_count: int,
        reason: Optional[str] = None,
    ) -> None:
        recorder = current_telemetry()
        if recorder is None:
            return
        try:
            recorder.record_stage(
                name="retrieval",
                producer="global_rag",
                outcome=outcome,
                duration_ms=max(0, (monotonic_ns() - started_ns) // 1_000_000),
                candidates=CandidateCounts(
                    input=max(0, input_count),
                    produced=max(0, output_count),
                    retained=max(0, output_count),
                ),
                reason=reason,
            )
        except Exception as error:
            logger.warning("RAG telemetry rejected: %s", type(error).__name__)

    async def _process_review(
            self,
            request: ReviewRequestDto,
            repo_path: Optional[str] = None,
            max_allowed_tokens: Optional[int] = None,
            event_callback: Optional[Callable[[Dict], None]] = None,
            coverage_tracker: Optional[ExecutionCoverageTracker] = None,
    ) -> Dict[str, Any]:
        """
        Internal method that handles both regular and local repo reviews.
        
        When rawDiff is provided:
        - Diff is embedded directly in prompt (no need to call getPullRequestDiff)
        - MCP agent still has access to all other tools (getFile, getComments, etc.)
        
        When rawDiff is not provided:
        - MCP agent fetches diff via getPullRequestDiff tool

        Emits events via event_callback:
        - {"type": "status", "state": "started", "message": "..."}
        - {"type": "status", "state": "mcp_initialized", "message": "..."}
        - {"type": "progress", "step": N, "max_steps": M, "message": "..."}
        - {"type": "mcp_output", "content": "...", "step": N}
        - {"type": "final", "result": {...}}
        - {"type": "error", "message": "..."}
        """
        jar_path = self.default_jar_path
        manifest_bound = is_manifest_bound_v1(request)
        agentic_review = self._is_agentic(request)

        if agentic_review and (not manifest_bound or repo_path is None):
            error_response = ResponseParser.create_error_response(
                "Agentic workspace unavailable",
                "The exact repository workspace could not be prepared for this review.",
            )
            self._emit_event(
                event_callback,
                {
                    "type": "error",
                    "message": "The exact repository workspace is unavailable.",
                },
            )
            return {"result": error_response}

        # Check if we have rawDiff - changes prompt building, not MCP usage
        has_raw_diff = bool(request.rawDiff)

        # ── MCP-free branch reconciliation fast path ──
        # When Java provides pre-fetched file contents AND there are previous
        # issues to reconcile, skip MCP entirely: no JVM subprocess, no tool
        # calls — just a direct LLM call.
        # This check is done BEFORE the jar existence check since MCP-free
        # reconciliation doesn't need the jar at all.
        # NOTE: When there are no previous issues (e.g. direct push with no
        # prior review history), we fall through to the standard path which
        # runs a full multi-stage review of the diff.
        is_branch_reconciliation = request.analysisType == "BRANCH_ANALYSIS"
        has_file_contents = bool(request.reconciliationFileContents)
        has_previous_issues = bool(request.previousCodeAnalysisIssues)

        if is_branch_reconciliation and has_file_contents and has_previous_issues:
            try:
                async with asyncio.timeout(self.REVIEW_TIMEOUT_SECONDS):
                    logger.info(
                        "Branch reconciliation with %d pre-fetched files — skipping MCP",
                        len(request.reconciliationFileContents),
                    )
                    self._emit_event(event_callback, {
                        "type": "status",
                        "state": "direct_reconciliation",
                        "message": f"Direct reconciliation mode ({len(request.reconciliationFileContents)} files pre-fetched)"
                    })

                    llm = self._create_llm(request)
                    pr_metadata = self._build_pr_metadata(request)
                    num_issues = len(pr_metadata.get("previousCodeAnalysisIssues", []))
                    logger.info(f"Branch reconciliation: {num_issues} previous issues to process (MCP-free)")

                    orchestrator = MultiStageReviewOrchestrator(
                        llm=llm,
                        mcp_client=None,  # No MCP needed
                        rag_client=None,
                        event_callback=event_callback,
                        telemetry=current_telemetry(),
                        coverage_tracker=coverage_tracker,
                    )

                    result = await orchestrator.execute_batched_branch_analysis(
                        request, pr_metadata
                    )

                    # Post-process
                    if result and 'issues' in result:
                        result = post_process_analysis_result(result)

                    self._emit_event(event_callback, {
                        "type": "status",
                        "state": "completed",
                        "message": "Branch reconciliation completed (MCP-free)"
                    })
                    return {"result": result}

            except TimeoutError:
                timeout_msg = f"Review timed out after {self.REVIEW_TIMEOUT_SECONDS} seconds"
                logger.error(timeout_msg)
                self._emit_event(event_callback, {"type": "error", "message": timeout_msg})
                error_response = ResponseParser.create_error_response(
                    "Review timed out", timeout_msg
                )
                return {"result": error_response}

            except Exception as e:
                logger.error(
                    "Direct reconciliation failed: error_type=%s",
                    type(e).__name__,
                )
                sanitized_message = create_user_friendly_error(e)
                error_response = ResponseParser.create_error_response(
                    "Direct reconciliation failed", sanitized_message
                )
                self._emit_event(event_callback, {
                    "type": "error",
                    "message": sanitized_message
                })
                return {"result": error_response}

        # Exact manifest reviews already carry the immutable diff and source
        # bundle.  Only legacy reviews need the live VCS MCP process.
        if not manifest_bound and not os.path.exists(jar_path):
            error_msg = f"MCP server jar not found at path: {jar_path}"
            self._emit_event(event_callback, {"type": "error", "message": error_msg})
            return {"error": error_msg}
        
        rag_context_task = None
        try:
            async with asyncio.timeout(self.REVIEW_TIMEOUT_SECONDS):
                if manifest_bound:
                    context = "from immutable review snapshot"
                else:
                    context = "with pre-fetched diff" if has_raw_diff else "fetching diff via MCP"
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "started",
                    "message": f"Analysis starting ({context})"
                })

                client = None
                llm_reranker = None
                if not manifest_bound:
                    jvm_props = self._build_jvm_props(request, max_allowed_tokens)
                    config = MCPConfigBuilder.build_config(jar_path, jvm_props)
                    self._emit_event(event_callback, {
                        "type": "status",
                        "state": "mcp_initializing",
                        "message": "Initializing MCP server"
                    })
                    client = self._create_mcp_client(config)

                # Create LLM instance
                llm = self._create_llm(request)
                
                # Exact reviews use revision-bound RAG calls inside the selected
                # engine. A reranker is useful only for the legacy global query.
                if not manifest_bound:
                    llm_reranker = LLMReranker(llm_client=llm)

                # Start the global RAG query as lazy fallback for Stage 1.
                # Per-batch RAG is richer and remains the primary path; this
                # task is only awaited if a batch cannot obtain per-batch
                # context. Branch reconciliation does not need it.
                needs_multistage_review = not agentic_review and not (
                    request.analysisType == "BRANCH_ANALYSIS"
                    and request.previousCodeAnalysisIssues
                )
                review_rag_client = self.rag_client
                if (
                    needs_multistage_review
                    and review_rag_client is not None
                    and not manifest_bound
                ):
                    rag_context_task = asyncio.create_task(
                        self._fetch_rag_context(
                            request,
                            event_callback,
                            llm_reranker=llm_reranker,
                        )
                    )

                # Build processed_diff if rawDiff is available to optimize Stage 1
                processed_diff = None
                if has_raw_diff:
                    diff_processor = DiffProcessor()
                    processed_diff = diff_processor.process(request.rawDiff)
                    
                    logger.info(
                        f"Diff pre-processed: {processed_diff.total_files} files, "
                        f"+{processed_diff.total_additions}/-{processed_diff.total_deletions}, "
                        f"skipped: {processed_diff.skipped_files}"
                    )
                    
                    if processed_diff.truncated:
                        self._emit_event(event_callback, {
                            "type": "warning",
                            "message": processed_diff.truncation_reason
                        })

                self._emit_event(event_callback, {
                    "type": "status",
                    "state": (
                        "agentic_workspace_ready"
                        if agentic_review
                        else ("snapshot_ready" if manifest_bound else "mcp_initialized")
                    ),
                    "message": (
                        "Exact repository workspace ready, starting agentic analysis"
                        if agentic_review
                        else "Immutable review snapshot ready, starting analysis"
                        if manifest_bound
                        else "MCP server ready, starting analysis"
                    )
                })

                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "agentic_review_started" if agentic_review else "multi_stage_started",
                    "message": (
                        "Starting bounded agentic review"
                        if agentic_review
                        else "Starting Multi-Stage Review Pipeline"
                    ),
                })

                orchestrator = None
                agentic_engine = None
                if agentic_review:
                    gateway = AgenticToolGateway(
                        workspace_root=repo_path,
                        request=request,
                        rag_client=review_rag_client,
                        processed_diff=processed_diff,
                    )
                    mcp_gateway = AgenticMcpAdapter(gateway)
                    agentic_engine = AgenticReviewEngine(
                        llm=llm,
                        gateway=mcp_gateway,
                        request=request,
                        processed_diff=processed_diff,
                        coverage_tracker=coverage_tracker,
                        event_callback=event_callback,
                    )
                else:
                    orchestrator = MultiStageReviewOrchestrator(
                        llm=llm,
                        mcp_client=client,
                        rag_client=review_rag_client,
                        event_callback=event_callback,
                        llm_reranker=llm_reranker,
                        telemetry=current_telemetry(),
                        coverage_tracker=coverage_tracker,
                    )

                try:
                    if agentic_engine is not None:
                        result = await agentic_engine.review()
                    # Check for Branch Analysis / Reconciliation mode
                    elif request.analysisType == "BRANCH_ANALYSIS":
                         logger.info("Executing Branch Analysis & Reconciliation mode")
                         pr_metadata = self._build_pr_metadata(request)
                         num_issues = len(pr_metadata.get("previousCodeAnalysisIssues", []))
                         logger.info(f"Branch reconciliation: {num_issues} previous issues to process")

                         if num_issues > 0:
                             # Use batched execution — splits large issue sets into
                             # token-safe batches automatically.  Single-batch fast
                             # path is handled inside execute_batched_branch_analysis.
                             result = await orchestrator.execute_batched_branch_analysis(
                                 request, pr_metadata
                             )
                         else:
                             # No previous issues to reconcile — this is a fresh
                             # branch analysis (e.g. direct push with no prior
                             # review history).  Run the full multi-stage review
                             # pipeline on the diff instead of short-circuiting.
                             logger.info(
                                 "Branch analysis: no previous issues — running "
                                 "fresh multi-stage review on the diff"
                             )
                             result = await orchestrator.orchestrate_review(
                                 request=request,
                                 rag_context=rag_context_task,
                                 processed_diff=processed_diff,
                             )
                    else:
                        # Execute review with Multi-Stage Orchestrator
                        # Standard PR Review
                        result = await orchestrator.orchestrate_review(
                            request=request, 
                            rag_context=rag_context_task,
                            processed_diff=processed_diff
                        )
                finally:
                    if rag_context_task and not rag_context_task.done():
                        rag_context_task.cancel()
                        try:
                            await rag_context_task
                        except asyncio.CancelledError:
                            pass
                    elif rag_context_task and rag_context_task.done() and not rag_context_task.cancelled():
                        rag_context_task.exception()
                    if client is not None:
                        try:
                            await client.close_all_sessions()
                        except Exception as close_err:
                            logger.warning(f"Error closing MCP sessions: {close_err}")


                # Post-process issues (no-op pass-through — Java handles all processing)
                if result and 'issues' in result:
                    self._emit_event(event_callback, {
                        "type": "status",
                        "state": "post_processing",
                        "message": "Finalizing issues (Java-side post-processing handles line correction, dedup, diff cleanup)..."
                    })
                    
                    result = post_process_analysis_result(result)

                if isinstance(result, dict):
                    result["reviewApproach"] = (
                        "AGENTIC" if agentic_review else "CLASSIC"
                    )

                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "completed",
                    "message": (
                        "Agentic snapshot analysis completed; generating the report"
                        if agentic_review
                        else "Snapshot analysis completed; generating the report"
                        if manifest_bound
                        else "MCP Agent has completed processing the Pull Request, report is being generated..."
                    )
                })

                return {"result": result}

        except TimeoutError:
            if rag_context_task and not rag_context_task.done():
                rag_context_task.cancel()
                try:
                    await rag_context_task
                except asyncio.CancelledError:
                    pass
            elif rag_context_task and rag_context_task.done() and not rag_context_task.cancelled():
                rag_context_task.exception()
            timeout_msg = f"Review timed out after {self.REVIEW_TIMEOUT_SECONDS} seconds"
            logger.error(timeout_msg)
            self._emit_event(event_callback, {"type": "error", "message": timeout_msg})
            error_response = ResponseParser.create_error_response(
                "Review timed out", timeout_msg
            )
            return {"result": error_response}

        except Exception as e:
            if rag_context_task and not rag_context_task.done():
                rag_context_task.cancel()
                try:
                    await rag_context_task
                except asyncio.CancelledError:
                    pass
            elif rag_context_task and rag_context_task.done() and not rag_context_task.cancelled():
                rag_context_task.exception()
            logger.error(
                "Review processing failed: error_type=%s",
                type(e).__name__,
            )
            sanitized_message = create_user_friendly_error(e)
            
            error_response = ResponseParser.create_error_response(
                f"Agent execution failed", sanitized_message
            )
            self._emit_event(event_callback, {
                "type": "error",
                "message": sanitized_message
            })
            return {"result": error_response}

    def _build_jvm_props(
            self,
            request: ReviewRequestDto,
            max_allowed_tokens: Optional[int]
    ) -> Dict[str, str]:
        """Build JVM properties from request."""
        return MCPConfigBuilder.build_jvm_props(
            request.projectId,
            request.pullRequestId,
            request.projectVcsWorkspace,
            request.projectVcsRepoSlug,
            request.oAuthClient,
            request.oAuthSecret,
            request.accessToken,
            request.maxAllowedTokens or max_allowed_tokens,
            request.vcsProvider
        )

    async def _fetch_rag_context(
            self,
            request: ReviewRequestDto,
            event_callback: Optional[Callable[[Dict], None]],
            llm_reranker: Optional[LLMReranker] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch relevant context from RAG pipeline.

        Returns:
            Dict with RAG context or None if RAG is disabled/failed
        """
        start_time = datetime.now()
        started_ns = monotonic_ns()
        cache_hit = False

        if is_manifest_bound_v1(request):
            # P1-01 binds repository and revisions, but not the internal RAG
            # workspace/namespace or an immutable index generation. Never let
            # the live request aliases select cached or remote index data.
            self._record_retrieval_telemetry(
                outcome=StageOutcome.SKIPPED,
                started_ns=started_ns,
                input_count=len(request.changedFiles or []),
                output_count=0,
                reason="manifest_rag_disabled",
            )
            return None
        
        rag_branch = request.get_rag_branch()
        base_branch = request.get_rag_base_branch()
        if not rag_branch:
            logger.warning("No branch specified for RAG query, skipping RAG context")
            self._record_retrieval_telemetry(
                outcome=StageOutcome.SKIPPED,
                started_ns=started_ns,
                input_count=len(request.changedFiles or []),
                output_count=0,
                reason="rag_branch_unavailable",
            )
            return None
        
        try:
            self._emit_event(event_callback, {
                "type": "status",
                "state": "rag_querying",
                "message": "Fetching relevant code context from RAG"
            })

            # Use changed files and diff snippets from pipeline-agent
            changed_files = request.changedFiles or []
            diff_snippets = request.diffSnippets or []
            
            # Check cache first
            cached_result = self.rag_cache.get(
                workspace=request.projectWorkspace,
                project=request.projectNamespace,
                branch=rag_branch,
                changed_files=changed_files,
                pr_title=request.prTitle or "",
                pr_description=request.prDescription or ""
            )
            
            if cached_result:
                cache_hit = True
                elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
                
                # Add cache hit to metrics
                if "relevant_code" in cached_result:
                    metrics = RAGMetrics.from_results(
                        cached_result.get("relevant_code", []),
                        processing_time_ms=elapsed_ms,
                        cache_hit=True
                    )
                    logger.info(f"RAG cache hit: {metrics.to_dict()}")
                
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "rag_cache_hit",
                    "message": f"Retrieved {len(cached_result.get('relevant_code', []))} chunks from cache"
                })
                self._record_retrieval_telemetry(
                    outcome=StageOutcome.COMPLETE,
                    started_ns=started_ns,
                    input_count=len(changed_files),
                    output_count=len(cached_result.get("relevant_code", [])),
                )
                return cached_result

            # Fetch from RAG service
            rag_response = await asyncio.wait_for(
                self.rag_client.get_pr_context(
                    workspace=request.projectWorkspace,
                    project=request.projectNamespace,
                    branch=rag_branch,
                    changed_files=changed_files,
                    diff_snippets=diff_snippets,
                    pr_title=request.prTitle,
                    pr_description=request.prDescription,
                    top_k=RAG_DEFAULT_TOP_K,
                    base_branch=base_branch,
                ),
                timeout=self.GLOBAL_RAG_QUERY_TIMEOUT_SECONDS,
            )

            if rag_response and rag_response.get("context"):
                context = rag_response.get("context")
                relevant_code = context.get("relevant_code", [])
                
                # LLM reranking is now applied per-batch in stages.py
                # via the orchestrator, not at the global level
                
                # Cache the result
                self.rag_cache.set(
                    workspace=request.projectWorkspace,
                    project=request.projectNamespace,
                    branch=rag_branch,
                    changed_files=changed_files,
                    result=context,
                    pr_title=request.prTitle or "",
                    pr_description=request.prDescription or ""
                )
                
                # Calculate and log metrics
                elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
                metrics = RAGMetrics.from_results(
                    relevant_code,
                    processing_time_ms=elapsed_ms,
                    reranking_applied=False,  # Reranking now happens per-batch in stages.py
                    cache_hit=False
                )
                logger.info(f"RAG metrics: {metrics.to_dict()}")
                
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "rag_retrieved",
                    "message": f"Retrieved {len(relevant_code)} context chunks from RAG",
                    "metrics": metrics.to_dict()
                })
                self._record_retrieval_telemetry(
                    outcome=StageOutcome.COMPLETE,
                    started_ns=started_ns,
                    input_count=len(changed_files),
                    output_count=len(relevant_code),
                )
                return context

            self._record_retrieval_telemetry(
                outcome=StageOutcome.SKIPPED,
                started_ns=started_ns,
                input_count=len(changed_files),
                output_count=0,
                reason="rag_context_empty",
            )
            return None

        except asyncio.CancelledError:
            self._record_retrieval_telemetry(
                outcome=StageOutcome.SKIPPED,
                started_ns=started_ns,
                input_count=len(request.changedFiles or []),
                output_count=0,
                reason="rag_fallback_not_required",
            )
            raise
        except Exception as e:
            logger.warning("Failed to fetch RAG context: %s", type(e).__name__)
            self._record_retrieval_telemetry(
                outcome=StageOutcome.FAILED,
                started_ns=started_ns,
                input_count=len(request.changedFiles or []),
                output_count=0,
                reason="rag_retrieval_failed",
            )
            self._emit_event(event_callback, {
                "type": "status",
                "state": "rag_skipped",
                "message": "RAG context retrieval skipped (non-critical)"
            })
            return None

    def _create_mcp_client(self, config: Dict[str, Any]) -> MCPClient:
        """Create MCP client from configuration."""
        try:
            return MCPClient.from_dict(config)
        except Exception as e:
            raise Exception(f"Failed to construct MCPClient: {str(e)}")

    def _create_llm(self, request: ReviewRequestDto):
        """Create LLM instance from request parameters."""
        try:
            # Log the model being used for this request
            logger.info(
                "Creating LLM for project %s PR %s: provider=%s, model=%s",
                request.projectId,
                request.pullRequestId or "n/a",
                request.aiProvider,
                request.aiModel,
            )
            
            llm = LLMFactory.create_llm(
                request.aiModel,
                request.aiProvider,
                request.aiApiKey,
                ai_base_url=getattr(request, 'aiBaseUrl', None),
                ai_custom_parameters=getattr(request, 'aiCustomParameters', None),
            )
            
            return llm
        except Exception as e:
            raise Exception(f"Failed to create LLM instance: {str(e)}")

    def _build_pr_metadata(self, request: ReviewRequestDto) -> Dict[str, Any]:
        """Build pull request metadata dictionary from request."""
        metadata = {
            "branch": request.get_rag_branch(),
            "baseBranch": request.get_rag_base_branch(),
            "commitHash": request.commitHash,
            "pullRequestId": request.pullRequestId,
            "repoSlug": request.projectVcsRepoSlug,
            "workspace": request.projectVcsWorkspace,
            "previousCodeAnalysisIssues": [
                issue.dict(by_alias=True, exclude_none=True)
                for issue in (request.previousCodeAnalysisIssues or [])
            ]
        }
        return metadata

    @staticmethod
    def _emit_event(callback: Optional[Callable[[Dict], None]], event: Dict[str, Any]) -> None:
        """Safely emit an event via the callback."""
        if callback:
            try:
                callback(event)
            except ExecutionEventBindingError:
                # Candidate identity violations are correctness failures, not
                # best-effort telemetry failures.  Let the active review abort.
                raise
            except Exception as e:
                # Don't let callback errors break the processing
                logger.warning(
                    "Event callback failed: error_type=%s",
                    type(e).__name__,
                )
