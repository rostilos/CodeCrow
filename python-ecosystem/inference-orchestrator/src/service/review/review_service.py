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

from model.dtos import ReviewRequestDto
from utils.mcp_config import MCPConfigBuilder
from llm.llm_factory import LLMFactory
from utils.prompts.prompt_builder import PromptBuilder
from utils.prompts import prompt_constants
from utils.response_parser import ResponseParser
from service.rag.rag_client import RagClient, RAG_DEFAULT_TOP_K
from service.rag.llm_reranker import LLMReranker
from service.review.issue_processor import post_process_analysis_result
from utils.context_builder import (RAGMetrics, get_rag_cache)
from utils.diff_processor import DiffProcessor
from utils.error_sanitizer import create_user_friendly_error
from service.review.orchestrator import MultiStageReviewOrchestrator
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
        async with self._review_semaphore:
            recorder, sink = self._create_telemetry_recorder(request)
            telemetry_token = bind_telemetry(recorder)
            started_ns = monotonic_ns()
            try:
                result = await self._process_review(
                    request=request,
                    repo_path=None,
                    event_callback=event_callback
                )
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
    def _create_telemetry_recorder(
        request: ReviewRequestDto,
    ) -> tuple[Optional[ExecutionTelemetryRecorder], MemoryTelemetrySink]:
        sink = MemoryTelemetrySink()
        try:
            prompt_version, rules_version = ReviewService._active_configuration_versions(
                request
            )
            identity = ExecutionIdentity(
                execution_id=request.executionId or "",
                base_revision=request.baseRevision or "",
                head_revision=request.headRevision or "",
            )
            versions = VersionAttribution(
                provider=request.aiProvider,
                model=request.aiModel,
                prompt_version=prompt_version,
                rules_version=rules_version,
                policy_version=request.policyVersion,
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
            # Legacy requests without both exact comparison revisions are
            # analyzed as before, but cannot emit a falsely complete terminal
            # metric. P1-01 supplies the durable identity for all executions.
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
            event_callback: Optional[Callable[[Dict], None]] = None
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
                logger.error(f"Direct reconciliation failed: {str(e)}", exc_info=True)
                sanitized_message = create_user_friendly_error(e)
                error_response = ResponseParser.create_error_response(
                    "Direct reconciliation failed", sanitized_message
                )
                self._emit_event(event_callback, {
                    "type": "error",
                    "message": sanitized_message
                })
                return {"result": error_response}

        # ── Standard path: MCP client needed ──
        if not os.path.exists(jar_path):
            error_msg = f"MCP server jar not found at path: {jar_path}"
            self._emit_event(event_callback, {"type": "error", "message": error_msg})
            return {"error": error_msg}
        
        rag_context_task = None
        try:
            async with asyncio.timeout(self.REVIEW_TIMEOUT_SECONDS):
                context = "with pre-fetched diff" if has_raw_diff else "fetching diff via MCP"
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "started",
                    "message": f"Analysis starting ({context})"
                })

                # ── Standard path: MCP client needed ──
                # Build configuration - MCP is always needed for other tools
                jvm_props = self._build_jvm_props(request, max_allowed_tokens)
                config = MCPConfigBuilder.build_config(jar_path, jvm_props)

                # Create MCP client - always needed
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "mcp_initializing",
                    "message": "Initializing MCP server"
                })
                client = self._create_mcp_client(config)

                # Create LLM instance
                llm = self._create_llm(request)
                
                # Create a per-request reranker (not shared across concurrent requests)
                llm_reranker = LLMReranker(llm_client=llm)

                # Start the global RAG query as lazy fallback for Stage 1.
                # Per-batch RAG is richer and remains the primary path; this
                # task is only awaited if a batch cannot obtain per-batch
                # context. Branch reconciliation does not need it.
                needs_multistage_review = not (
                    request.analysisType == "BRANCH_ANALYSIS"
                    and request.previousCodeAnalysisIssues
                )
                if needs_multistage_review:
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
                    "state": "mcp_initialized",
                    "message": "MCP server ready, starting analysis"
                })

                # Use the new pipeline
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "multi_stage_started",
                    "message": "Starting Multi-Stage Review Pipeline"
                })

                # This replaces the monolithic _execute_review_with_streaming call
                orchestrator = MultiStageReviewOrchestrator(
                    llm=llm,
                    mcp_client=client,
                    rag_client=self.rag_client,
                    event_callback=event_callback,
                    llm_reranker=llm_reranker,
                    telemetry=current_telemetry(),
                )

                try:
                    # Check for Branch Analysis / Reconciliation mode
                    if request.analysisType == "BRANCH_ANALYSIS":
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
                    # Always close MCP sessions to release JVM subprocesses
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

                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "completed",
                    "message": "MCP Agent has completed processing the Pull Request, report is being generated..."
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
            # Log full error for debugging, but sanitize for user display
            logger.error(f"Review processing failed: {str(e)}", exc_info=True)
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
            except Exception as e:
                # Don't let callback errors break the processing
                logger.warning(f"Event callback failed: {e}")
