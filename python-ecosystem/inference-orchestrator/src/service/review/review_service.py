import os
import asyncio
import json
import logging
import re
from typing import Dict, Any, Optional, Callable
from dotenv import load_dotenv
from utils.mcp_runtime import configure_mcp_runtime

configure_mcp_runtime()

from mcp_use import MCPClient

from model.dtos import ReviewRequestDto
from utils.mcp_config import MCPConfigBuilder
from llm.llm_factory import LLMFactory
from utils.response_parser import ResponseParser
from utils.mcp_tool_serialization import (
    install_per_connection_tool_serialization,
)
from service.rag.rag_client import RagClient
from service.review.issue_processor import post_process_analysis_result
from service.review.plugin_context import apply_plugin_file_policy
from service.review.quality_capture import (
    ReviewQualityCaptureSession,
    create_quality_capture_session,
    review_response_indicates_failure,
    wrap_quality_capture_llm,
)
from service.review.evidence_scopes import (
    process_review_evidence_scopes,
    select_review_evidence_diff,
)
from utils.hunk_coverage import validate_acquired_diff_manifest
from utils.error_sanitizer import create_user_friendly_error
from service.review.orchestrator import MultiStageReviewOrchestrator
from service.review.orchestrator.stage_1_tool_inventory import (
    STAGE1_REVIEW_FILE_TOOL_NAME,
    STAGE1_STRUCTURAL_TOOL_NAMES,
)
from service.review.snapshot_identity import (
    resolve_exact_structural_base_revision,
    validate_review_snapshot_identity,
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
    MCP_SESSION_INITIALIZATION_TIMEOUT_SECONDS = float(os.environ.get(
        "MCP_SESSION_INITIALIZATION_TIMEOUT_SECONDS",
        "30",
    ))
    MCP_SESSION_CLOSE_TIMEOUT_SECONDS = float(os.environ.get(
        "MCP_SESSION_CLOSE_TIMEOUT_SECONDS",
        "10",
    ))
    def __init__(self):
        load_dotenv(interpolate=False)
        self.default_jar_path = os.environ.get(
            "MCP_SERVER_JAR",
            #"/var/www/html/codecrow/codecrow-public/java-ecosystem/mcp-servers/vcs-mcp/target/codecrow-vcs-mcp-1.0.jar",
            "/app/codecrow-vcs-mcp-1.0.jar"
        )
        self.rag_client = RagClient()
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
        # Validate before provider construction, MCP
        # startup, or dry-run dispatch. Every review mode must describe the
        # same exact immutable repository snapshot.
        validate_review_snapshot_identity(request)
        self._validate_local_only_mcp_request(request)
        self._validate_required_structural_mcp_request(request)
        async with self._review_semaphore:
            if request.promptDryRun:
                return await self._process_prompt_dry_run(request, event_callback)
            quality_capture = create_quality_capture_session(request)
            review_event_callback = (
                quality_capture.wrap_event_callback(event_callback)
                if quality_capture is not None
                else event_callback
            )
            try:
                response = await self._process_review(
                    request=request,
                    repo_path=None,
                    event_callback=review_event_callback,
                    quality_capture=quality_capture,
                )
            except BaseException as exception:
                if quality_capture is not None:
                    await quality_capture.complete(None, exception)
                raise
            if quality_capture is not None:
                await quality_capture.complete(
                    response,
                    failed=review_response_indicates_failure(response),
                )
                self._emit_event(review_event_callback, {
                    "type": "status",
                    "state": "review_quality_capture_completed",
                    "message": "Review quality capture completed",
                    "qualityCapture": quality_capture.receipt(),
                })
            return response

    async def _process_prompt_dry_run(
            self,
            request: ReviewRequestDto,
            event_callback: Optional[Callable[[Dict], None]],
    ) -> Dict[str, Any]:
        """Run real context assembly with a capturing model and store its prompts."""
        enabled = os.environ.get(
            "ANALYSIS_PROMPT_DRY_RUN_ENABLED", "false"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if not enabled:
            raise ValueError(
                "promptDryRun was requested while ANALYSIS_PROMPT_DRY_RUN_ENABLED is false"
            )

        try:
            simulated_findings = int(os.environ.get(
                "ANALYSIS_PROMPT_DRY_RUN_SYNTHETIC_FINDINGS_PER_FILE", "6"
            ))
        except ValueError as exception:
            raise ValueError(
                "ANALYSIS_PROMPT_DRY_RUN_SYNTHETIC_FINDINGS_PER_FILE must be an integer"
            ) from exception
        try:
            simulated_findings_max_total = int(os.environ.get(
                "ANALYSIS_PROMPT_DRY_RUN_SYNTHETIC_FINDINGS_MAX_TOTAL", "24"
            ))
        except ValueError as exception:
            raise ValueError(
                "ANALYSIS_PROMPT_DRY_RUN_SYNTHETIC_FINDINGS_MAX_TOTAL must be an integer"
            ) from exception

        self._emit_event(event_callback, {
            "type": "status",
            "state": "prompt_dry_run_started",
            "message": (
                "Prompt dry run started: real project context will be assembled "
                "without calling the review LLM"
            ),
        })
        logger.info(
            "prompt_dry_run_started project=%s pr=%s job=%s",
            request.projectId,
            request.pullRequestId,
            request.promptDryRunId,
        )

        from service.review.prompt_dry_run import capture_and_store_review_prompts

        summary = await capture_and_store_review_prompts(
            request,
            self._rag_client_for_request(request),
            simulated_findings_per_file=simulated_findings,
            simulated_findings_max_total=simulated_findings_max_total,
            event_callback=event_callback,
        )
        self._emit_event(event_callback, {
            "type": "status",
            "state": "prompt_dry_run_completed",
            "message": (
                "Prompt dry run completed; artifact: "
                f"{summary['promptArtifact']['containerPath']}"
            ),
            "promptArtifact": summary["promptArtifact"],
        })
        logger.info(
            "prompt_dry_run_completed project=%s pr=%s job=%s artifact=%s "
            "provider_calls=0",
            request.projectId,
            request.pullRequestId,
            request.promptDryRunId,
            summary["promptArtifact"]["containerPath"],
        )
        return {"result": summary}

    async def _process_review(
            self,
            request: ReviewRequestDto,
            repo_path: Optional[str] = None,
            event_callback: Optional[Callable[[Dict], None]] = None,
            quality_capture: Optional[ReviewQualityCaptureSession] = None,
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
        self._validate_local_only_mcp_request(request)
        self._validate_required_structural_mcp_request(request)
        jar_path = self.default_jar_path

        # An incremental execution owns the delta manifest. The full PR diff is
        # still carried as snapshot context, but must not be validated or
        # planned as though every historical PR path belonged to this run.
        review_evidence_diff = select_review_evidence_diff(request)
        has_raw_diff = bool(review_evidence_diff)

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
        if (
            request.requireStructuralMcp
            and is_branch_reconciliation
            and has_file_contents
            and has_previous_issues
        ):
            raise ValueError(
                "Required structural MCP is incompatible with the MCP-free "
                "branch-reconciliation path. No review-model stage was started."
            )
        needs_multistage_review = not (
            is_branch_reconciliation and has_previous_issues
        )

        # Parse and prove the acquired diff before MCP startup, provider
        # construction, or any repository-context query. Reconciliation
        # requests intentionally carry an issue-scoped diff rather than the
        # complete changed-file manifest, so their separate direct path is not
        # subject to this full-review equality check.
        processed_diff = None
        full_pr_processed_diff = None
        if has_raw_diff and needs_multistage_review:
            evidence_scopes = process_review_evidence_scopes(request)
            processed_diff = evidence_scopes.review
            full_pr_processed_diff = evidence_scopes.full_pr
            validate_acquired_diff_manifest(
                request.changedFiles or (),
                request.deletedFiles or (),
                processed_diff,
            )

            logger.info(
                f"Diff pre-processed: {processed_diff.total_files} files, "
                f"+{processed_diff.total_additions}/-{processed_diff.total_deletions}, "
                f"skipped: {processed_diff.skipped_files}"
            )

            # Incremental review and PR-wide reasoning use deliberately separate
            # evidence scopes. Stage 0/1, hunk coverage, and publication anchors
            # continue to use only ``processed_diff``
            # (the delta). Stage 2 receives this bounded base-to-head parse so it
            # cannot mistake a one-file delta for the complete PR state.
            if (
                request.analysisMode == "INCREMENTAL"
                and request.deltaDiff
            ):
                if full_pr_processed_diff is not None:
                    logger.info(
                        "Full PR evidence scope prepared separately: %d files; "
                        "review/publication scope remains %d delta files",
                        len(full_pr_processed_diff.files),
                        len(processed_diff.files),
                    )
                else:
                    logger.warning(
                        "Full PR evidence scope unavailable; continuing the "
                        "delta review with PR-wide omission claims disabled"
                    )
            else:
                full_pr_processed_diff = processed_diff

            if processed_diff.truncated:
                self._emit_event(event_callback, {
                    "type": "warning",
                    "message": processed_diff.truncation_reason
                })

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

                    llm = self._create_llm(request, quality_capture)
                    pr_metadata = self._build_pr_metadata(request)
                    num_issues = len(pr_metadata.get("previousCodeAnalysisIssues", []))
                    logger.info(f"Branch reconciliation: {num_issues} previous issues to process (MCP-free)")

                    orchestrator = MultiStageReviewOrchestrator(
                        llm=llm,
                        mcp_client=None,  # No MCP needed
                        rag_client=None,
                        event_callback=event_callback,
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

        use_mcp_tools = bool(request.useMcpTools)
        mcp_available = use_mcp_tools and os.path.exists(jar_path)
        if (request.mcpLocalOnly or request.requireStructuralMcp) and not mcp_available:
            raise RuntimeError(
                "Required MCP precondition failed: the request-scoped VCS MCP "
                "server is unavailable. No review-model stage was started."
            )
        if use_mcp_tools and not mcp_available:
            logger.warning(
                "Agentic repository tools requested but the VCS MCP server is "
                "unavailable at %s; continuing with direct file-review prompts",
                jar_path,
            )
            self._emit_event(event_callback, {
                "type": "status",
                "state": "mcp_degraded",
                "message": (
                    "Repository tools are unavailable; analysis will continue "
                    "with the diff and prepared context"
                ),
            })
        
        client = None
        try:
            async with asyncio.timeout(self.REVIEW_TIMEOUT_SECONDS):
                context = "with pre-fetched diff" if has_raw_diff else "fetching diff via MCP"
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "started",
                    "message": f"Analysis starting ({context})"
                })

                request_rag_client = self._rag_client_for_request(request)
                agent_rag_client = self._rag_client_for_agent_tools()
                await self._prepare_rag_review_generation(
                    request,
                    agent_rag_client,
                    event_callback,
                )
                rag_mcp_context = self._build_rag_mcp_context(
                    request,
                    agent_rag_client,
                )
                if request.requireStructuralMcp:
                    if request.ragReviewGenerationStatus != "ready":
                        raise RuntimeError(
                            "Required proposed-tree graph preparation did not "
                            "produce a sealed ready generation; no review-model "
                            "stage was started: "
                            + str(
                                request.ragReviewGenerationError
                                or request.ragReviewGenerationStatus
                            )
                        )
                    if rag_mcp_context is None:
                        raise RuntimeError(
                            "Required proposed-tree graph binding is incomplete; "
                            "no review-model stage was started"
                        )

                # Provider construction is intentionally after every local-only
                # request/binding precondition. No model invocation happens
                # until the MCP sessions and exact-source preflight pass below.
                llm = self._create_llm(request, quality_capture)
                agent_service = None

                if mcp_available:
                    try:
                        self._emit_event(event_callback, {
                            "type": "status",
                            "state": "mcp_initializing",
                            "message": "Initializing repository tools"
                        })
                        config = MCPConfigBuilder.build_config(
                            jar_path,
                            self._build_jvm_props(request),
                            rag_mcp_context=rag_mcp_context,
                        )
                        client = self._create_mcp_client(config)
                        # Keep the agent runtime lazy for MCP-disabled reviews and
                        # provider-free prompt capture.
                        from service.agent import AgentExecutionService
                        agent_service = AgentExecutionService(
                            llm=llm,
                            client=client,
                        )
                        structural_mcp_required = request.requireStructuralMcp
                        optional_errors = await agent_service.initialize(
                            required_server_names=(
                                (
                                    "codecrow-vcs-mcp",
                                    "codecrow-rag-mcp",
                                )
                                if structural_mcp_required
                                else ("codecrow-vcs-mcp",)
                            ),
                            optional_server_names=(
                                ("codecrow-rag-mcp",)
                                if (
                                    rag_mcp_context is not None
                                    and not structural_mcp_required
                                )
                                else ()
                            ),
                            session_timeout_seconds=(
                                self.MCP_SESSION_INITIALIZATION_TIMEOUT_SECONDS
                            ),
                        )
                        rag_start_error = optional_errors.get(
                            "codecrow-rag-mcp"
                        )
                        if rag_start_error is not None:
                            logger.warning(
                                "Optional structural graph tools failed to start; "
                                "continuing with repository tools without "
                                "indexed relationships: %s",
                                rag_start_error,
                            )
                            self._emit_event(event_callback, {
                                "type": "status",
                                "state": "rag_mcp_degraded",
                                "message": (
                                    "Structural graph tools are unavailable; "
                                    "local repository tools remain available"
                                ),
                            })
                        if structural_mcp_required:
                            required_tool_names = (
                                STAGE1_STRUCTURAL_TOOL_NAMES
                                | {STAGE1_REVIEW_FILE_TOOL_NAME}
                            )
                            missing_tool_names = sorted(
                                required_tool_names.difference(
                                    agent_service.available_tool_names
                                )
                            )
                            if missing_tool_names:
                                raise RuntimeError(
                                    "Required structural MCP inventory is "
                                    "incomplete; missing: "
                                    + ", ".join(missing_tool_names)
                                )
                        if request.mcpLocalOnly or request.requireStructuralMcp:
                            preflight = await self._preflight_local_only_mcp(
                                client,
                                request,
                            )
                            self._emit_event(event_callback, {
                                "type": "status",
                                "state": "local_mcp_preflight_completed",
                                "message": (
                                    "Local proposed-source MCP passed its "
                                    "request-binding preflight"
                                ),
                                "localMcpPreflight": preflight,
                            })
                        self._emit_event(event_callback, {
                            "type": "status",
                            "state": "mcp_initialized",
                            "message": "Repository tools are ready"
                        })
                    except Exception as mcp_error:
                        if request.mcpLocalOnly or request.requireStructuralMcp:
                            logger.error(
                                "Required MCP tools failed to initialize: %s",
                                mcp_error,
                                exc_info=True,
                            )
                            if client is not None:
                                await self._close_mcp_sessions(
                                    client,
                                    context="required MCP initialization failure",
                                )
                            client = None
                            raise RuntimeError(
                                "Required repository/structural MCP "
                                "tools failed to initialize; no review-model "
                                "stage was started"
                            ) from mcp_error
                        logger.warning(
                            "Optional agentic repository tools failed to "
                            "initialize; continuing with direct Stage 1 prompts: %s",
                            mcp_error,
                            exc_info=True,
                        )
                        if client is not None:
                            await self._close_mcp_sessions(
                                client,
                                context="optional MCP initialization failure",
                            )
                        client = None
                        agent_service = None
                        self._emit_event(event_callback, {
                            "type": "status",
                            "state": "mcp_degraded",
                            "message": (
                                "Repository tools could not start; analysis will "
                                "continue with the diff and prepared context"
                            ),
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
                    rag_client=request_rag_client,
                    event_callback=event_callback,
                    agent_service=agent_service,
                )

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
                             processed_diff=processed_diff,
                             full_pr_processed_diff=full_pr_processed_diff,
                         )
                else:
                    # Execute review with Multi-Stage Orchestrator
                    # Standard PR Review
                    result = await orchestrator.orchestrate_review(
                        request=request,
                        processed_diff=processed_diff,
                        full_pr_processed_diff=full_pr_processed_diff,
                    )


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
                    "message": "Pull Request analysis completed; the report is being generated..."
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
            # Log full error for debugging, but sanitize for user display
            logger.error(f"Review processing failed: {str(e)}", exc_info=True)
            sanitized_message = create_user_friendly_error(e)
            
            error_response = ResponseParser.create_error_response(
                "Review execution failed", sanitized_message
            )
            self._emit_event(event_callback, {
                "type": "error",
                "message": sanitized_message
            })
            return {"result": error_response}
        finally:
            # Client ownership begins at construction, so timeout/cancellation
            # during session startup cannot leave MCP child processes behind.
            if client is not None:
                await self._close_mcp_sessions(
                    client,
                    context="review completion",
                )

    async def _close_mcp_sessions(
            self,
            client: MCPClient,
            *,
            context: str,
    ) -> None:
        """Bound request-owned MCP teardown so it cannot strand a result."""
        try:
            async with asyncio.timeout(self.MCP_SESSION_CLOSE_TIMEOUT_SECONDS):
                await client.close_all_sessions()
        except TimeoutError:
            logger.warning(
                "MCP session cleanup timed out after %.1f seconds during %s; "
                "continuing without waiting for teardown",
                self.MCP_SESSION_CLOSE_TIMEOUT_SECONDS,
                context,
            )
        except Exception as close_error:
            logger.warning(
                "Error closing MCP sessions during %s: %s",
                context,
                close_error,
            )

    def _build_jvm_props(
            self,
            request: ReviewRequestDto,
    ) -> Dict[str, str]:
        """Build JVM properties from request."""
        return MCPConfigBuilder.build_jvm_props(
            project_id=request.projectId,
            pull_request_id=request.pullRequestId,
            workspace=request.projectVcsWorkspace,
            repo_slug=request.projectVcsRepoSlug,
            oAuthClient=request.oAuthClient,
            oAuthSecret=request.oAuthSecret,
            access_token=request.accessToken,
            max_allowed_tokens=request.maxAllowedTokens,
            vcs_provider=request.vcsProvider,
            vcs_base_url=request.vcsBaseUrl,
            local_repo_path=request.localRepoPath,
            local_repo_target_branch=request.localRepoTargetBranch,
            local_repo_revision=request.localRepoRevision,
            local_review_overlay_path=request.localReviewOverlayPath,
            local_mcp_only=request.mcpLocalOnly is True,
        )

    def _validate_local_only_mcp_request(
            self,
            request: ReviewRequestDto,
    ) -> None:
        """Reject incomplete provider-isolated reviews before model startup."""
        if request.mcpLocalOnly is not True:
            return
        if request.useMcpTools is not True:
            raise ValueError(
                "Local-only MCP precondition failed: useMcpTools must be true. "
                "No review-model stage was started."
            )
        required_text = {
            "localRepoPath": request.localRepoPath,
            "localRepoTargetBranch": request.localRepoTargetBranch,
            "localRepoRevision": request.localRepoRevision,
            "localReviewOverlayPath": request.localReviewOverlayPath,
            "projectVcsWorkspace": request.projectVcsWorkspace,
            "projectVcsRepoSlug": request.projectVcsRepoSlug,
        }
        missing = sorted(
            name
            for name, value in required_text.items()
            if not isinstance(value, str) or not value.strip()
        )
        if missing:
            raise ValueError(
                "Local-only MCP precondition failed: missing exact local/RAG "
                "binding fields: "
                + ", ".join(missing)
                + ". No review-model stage was started."
            )

        missing_paths = sorted(
            name
            for name in (
                "localRepoPath",
                "localReviewOverlayPath",
            )
            if not os.path.isdir(str(required_text[name]))
        )
        if missing_paths:
            raise ValueError(
                "Local-only MCP precondition failed: staged directories are "
                "unavailable: "
                + ", ".join(missing_paths)
                + ". No review-model stage was started."
            )

        exposed_credentials = sorted(
            name
            for name in ("accessToken", "oAuthClient", "oAuthSecret")
            if isinstance(getattr(request, name, None), str)
            and bool(getattr(request, name).strip())
        )
        if exposed_credentials:
            raise ValueError(
                "Local-only MCP precondition failed: provider credentials are "
                "not allowed: "
                + ", ".join(exposed_credentials)
                + ". No review-model stage was started."
            )

    def _validate_required_structural_mcp_request(
            self,
            request: ReviewRequestDto,
    ) -> None:
        """Reject a structurally-enforced review before any provider activity."""
        if request.requireStructuralMcp is not True:
            return
        if request.useMcpTools is not True:
            raise ValueError(
                "Required structural MCP precondition failed: useMcpTools must "
                "be true. No review-model stage was started."
            )
        if request.ragEnabled is not True:
            raise ValueError(
                "Required structural MCP precondition failed: ragEnabled must "
                "be true. No review-model stage was started."
            )

    @staticmethod
    def _mcp_tool_payload(result: Any, tool_name: str) -> Dict[str, Any]:
        if bool(
            getattr(result, "isError", False)
            or getattr(result, "is_error", False)
        ):
            raise RuntimeError(f"{tool_name} returned an MCP error")
        for attribute in ("structuredContent", "structured_content"):
            structured = getattr(result, attribute, None)
            if isinstance(structured, dict):
                return structured
        text = "\n".join(
            str(block.text)
            for block in (getattr(result, "content", None) or ())
            if isinstance(getattr(block, "text", None), str)
        ).strip()
        try:
            payload = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exception:
            raise RuntimeError(
                f"{tool_name} returned no structured JSON payload"
            ) from exception
        if not isinstance(payload, dict):
            raise RuntimeError(f"{tool_name} returned a non-object payload")
        return payload

    async def _preflight_local_only_mcp(
            self,
            client: MCPClient,
            request: ReviewRequestDto,
    ) -> Dict[str, Any]:
        """Prove the request-bound proposed-source read before LLM use."""
        sessions = client.get_all_active_sessions()
        if not isinstance(sessions, dict):
            raise RuntimeError("Local-only MCP sessions are unavailable")
        vcs_session = sessions.get("codecrow-vcs-mcp")
        if vcs_session is None:
            raise RuntimeError(
                "Local-only MCP preflight requires the repository session"
            )

        focus_paths = [
            str(path).strip().replace("\\", "/").lstrip("/")
            for path in (
                *(request.changedFiles or ()),
                *(request.deletedFiles or ()),
            )
            if isinstance(path, str) and path.strip()
        ]
        if not focus_paths:
            raise RuntimeError(
                "Local-only MCP preflight requires at least one changed path"
            )
        focus_path = focus_paths[0]

        source_result = await vcs_session.call_tool(
            "getReviewFileContent",
            {
                "workspace": request.projectVcsWorkspace,
                "repoSlug": request.projectVcsRepoSlug,
                "filePath": focus_path,
            },
        )
        source = self._mcp_tool_payload(
            source_result,
            "getReviewFileContent",
        )
        if source.get("error"):
            raise RuntimeError(
                "Local-only proposed-tree source preflight failed: "
                f"{source['error']}"
            )
        observed_path = str(source.get("filePath") or "").replace(
            "\\", "/"
        ).lstrip("/")
        if (
            observed_path != focus_path
            or source.get("unavailable") is True
            or source.get("source") not in {"review-overlay", "target-head"}
        ):
            raise RuntimeError(
                "Local-only proposed-tree source preflight returned an "
                "unbound or unavailable source"
            )

        return {
            "status": "ready",
            "focusPath": focus_path,
            "baseRevision": request.localRepoRevision,
            "sourceAuthority": source.get("source"),
        }

    def _build_rag_mcp_context(
            self,
            request: ReviewRequestDto,
            rag_client: Optional[RagClient],
    ) -> Optional[Dict[str, str]]:
        """Return the exact request binding for proposed-tree graph tools."""
        if rag_client is None:
            return None
        source_revision = (
            request.currentCommitHash
            or request.commitHash
        )
        base_revision = resolve_exact_structural_base_revision(request)
        structural_repo_path = getattr(request, "localRagRepoPath", None)
        if (
            not isinstance(structural_repo_path, str)
            or not structural_repo_path.strip()
        ):
            structural_repo_path = request.localRepoPath
        context = {
            "workspace": request.projectWorkspace,
            "project": request.projectNamespace,
            "branch": request.targetBranchName,
            "revision": base_revision,
            "source_revision": source_revision,
            "target_repo_path": structural_repo_path,
            "review_overlay_path": request.localReviewOverlayPath,
            "manifest": request.ragBaseGenerationManifestSha256,
            "collection_target": request.ragCollectionTarget,
            "review_collection_target": request.ragReviewCollectionTarget,
            "review_generation_manifest_sha256": (
                request.ragReviewGenerationManifestSha256
            ),
        }
        if not all(
            isinstance(value, str) and bool(value.strip())
            for value in context.values()
        ):
            logger.info(
                "Stage 1 proposed-tree graph tool is unavailable because the "
                "request has no complete target snapshot, review overlay, "
                "revision binding, or exact sealed base generation; repository "
                "tools remain available without indexed relationships"
            )
            return None

        return context

    async def _prepare_rag_review_generation(
            self,
            request: ReviewRequestDto,
            rag_client: Optional[RagClient],
            event_callback: Optional[Callable[[Dict], None]],
    ) -> None:
        """Prepare one sealed proposed-tree graph before MCP and Stage 1 fan-out."""

        request.ragReviewGenerationStatus = "not_eligible"
        request.ragReviewCollectionTarget = None
        request.ragReviewGenerationManifestSha256 = None
        request.ragReviewGenerationError = None
        if rag_client is None or not bool(request.useMcpTools):
            return

        source_revision = request.currentCommitHash or request.commitHash
        base_revision = resolve_exact_structural_base_revision(request)
        structural_repo_path = getattr(request, "localRagRepoPath", None)
        if (
            not isinstance(structural_repo_path, str)
            or not structural_repo_path.strip()
        ):
            structural_repo_path = request.localRepoPath
        binding = {
            "workspace": request.projectWorkspace,
            "project": request.projectNamespace,
            "target_branch": request.targetBranchName,
            "base_revision": base_revision,
            "source_revision": source_revision,
            "target_repo_path": structural_repo_path,
            "review_overlay_path": request.localReviewOverlayPath,
            "base_collection_target": request.ragCollectionTarget,
            "base_generation_manifest_sha256": (
                request.ragBaseGenerationManifestSha256
            ),
        }
        if not all(
            isinstance(value, str) and bool(value.strip())
            for value in binding.values()
        ):
            return

        request.ragReviewGenerationStatus = "preparing"
        self._emit_event(event_callback, {
            "type": "status",
            "state": "rag_review_generation_preparing",
            "message": (
                "Preparing one exact proposed-tree graph before Stage 1"
            ),
        })
        try:
            response = await rag_client.prepare_review_generation(**binding)
        except Exception as error:
            response = {
                "status": "error",
                "error": f"{type(error).__name__}: {error}",
            }

        status = str(response.get("status") or "").strip().casefold()
        collection_target = response.get("collection_target")
        generation_manifest = response.get("generation_manifest_sha256")
        response_revision = response.get("source_revision")
        ready = (
            status == "ready"
            and isinstance(collection_target, str)
            and bool(collection_target.strip())
            and isinstance(generation_manifest, str)
            and re.fullmatch(r"[0-9a-f]{64}", generation_manifest) is not None
            and response_revision == source_revision
        )
        if ready:
            request.ragReviewGenerationStatus = "ready"
            request.ragReviewCollectionTarget = collection_target
            request.ragReviewGenerationManifestSha256 = generation_manifest
            self._emit_event(event_callback, {
                "type": "status",
                "state": "rag_review_generation_ready",
                "message": (
                    "Exact proposed-tree graph is sealed and ready for "
                    "read-only Stage 1 tools"
                ),
                "ragReviewGeneration": {
                    "status": "ready",
                    "sourceRevision": source_revision,
                    "generationManifestSha256": generation_manifest,
                    "cacheHit": response.get("cache_hit") is True,
                },
            })
            return

        error = str(
            response.get("error")
            or "proposed-tree generation preparation returned no sealed receipt"
        ).strip()
        request.ragReviewGenerationStatus = "unavailable"
        request.ragReviewGenerationError = error
        structural_required = request.requireStructuralMcp is True
        logger.warning(
            "%s proposed-tree graph preparation failed%s: %s",
            "Required" if structural_required else "Optional",
            (
                "; the review will stop before model use"
                if structural_required
                else "; continuing with request-bound source review"
            ),
            error,
        )
        self._emit_event(event_callback, {
            "type": "status",
            "state": (
                "rag_review_generation_failed"
                if structural_required
                else "rag_review_generation_degraded"
            ),
            "message": (
                "Required structural graph preparation is unavailable; the "
                "review will stop before model use"
                if structural_required
                else "Structural graph preparation is unavailable; Stage 1 "
                "will continue with exact request-bound source"
            ),
            "ragReviewGeneration": {
                "status": "unavailable",
                "sourceRevision": source_revision,
                "error": error,
            },
        })

    def _rag_client_for_request(
            self,
            request: ReviewRequestDto,
    ) -> Optional[RagClient]:
        """Apply global and project-scoped RAG enablement without shared mutation."""
        if not request.ragEnabled:
            logger.info(
                "RAG disabled for project request: project=%s PR=%s",
                request.projectId,
                request.pullRequestId or "n/a",
            )
            return None
        if not bool(getattr(self.rag_client, "enabled", True)):
            return None
        return self.rag_client

    def _rag_client_for_agent_tools(self) -> Optional[RagClient]:
        """Expose on-demand structural tools whenever MCP analysis is active.

        The caller already gates MCP session creation with ``useMcpTools``.
        Project ``ragEnabled`` controls persistent branch indexing/retrieval;
        it does not control the request-scoped proposed-tree generation, which
        is built from the host's temporary snapshot and overlay.
        """
        if not bool(getattr(self.rag_client, "enabled", True)):
            return None
        return self.rag_client

    def _create_mcp_client(self, config: Dict[str, Any]) -> MCPClient:
        """Create MCP client from configuration."""
        try:
            return install_per_connection_tool_serialization(
                MCPClient.from_dict(config)
            )
        except Exception as e:
            raise Exception(f"Failed to construct MCPClient: {str(e)}")

    def _create_llm(
        self,
        request: ReviewRequestDto,
        quality_capture: Optional[ReviewQualityCaptureSession] = None,
    ):
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
            
            return wrap_quality_capture_llm(llm, quality_capture)
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
