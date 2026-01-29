import os
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Callable
from dotenv import load_dotenv
from mcp_use import MCPClient

from model.models import ReviewRequestDto
from utils.mcp_config import MCPConfigBuilder
from llm.llm_factory import LLMFactory
from utils.prompts.prompt_builder import PromptBuilder
from utils.response_parser import ResponseParser
from service.rag_client import RagClient, RAG_DEFAULT_TOP_K
from service.llm_reranker import LLMReranker
from service.issue_post_processor import post_process_analysis_result
from utils.context_builder import (RAGMetrics, get_rag_cache)
from utils.diff_processor import DiffProcessor
from utils.error_sanitizer import create_user_friendly_error
from service.multi_stage_orchestrator import MultiStageReviewOrchestrator

logger = logging.getLogger(__name__)

class ReviewService:
    """Service class for handling code review requests with streaming support."""
    
    # Maximum retries for LLM-based response fixing
    MAX_FIX_RETRIES = 2
    
    # Threshold for using LLM reranking (number of changed files)
    LLM_RERANK_FILE_THRESHOLD = 20

    def __init__(self):
        load_dotenv()
        self.default_jar_path = os.environ.get(
            "MCP_SERVER_JAR",
            #"/var/www/html/codecrow/codecrow-public/java-ecosystem/mcp-servers/vcs-mcp/target/codecrow-vcs-mcp-1.0.jar",
            "/app/codecrow-vcs-mcp-1.0.jar"
        )
        self.rag_client = RagClient()
        self.rag_cache = get_rag_cache()
        self.llm_reranker = None  # Initialized lazily with LLM

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
        return await self._process_review(
            request=request,
            repo_path=None,
            event_callback=event_callback
        )

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
        if not os.path.exists(jar_path):
            error_msg = f"MCP server jar not found at path: {jar_path}"
            self._emit_event(event_callback, {"type": "error", "message": error_msg})
            return {"error": error_msg}

        # Check if we have rawDiff - changes prompt building, not MCP usage
        has_raw_diff = bool(request.rawDiff)
        
        try:
            context = "with pre-fetched diff" if has_raw_diff else "fetching diff via MCP"
            self._emit_event(event_callback, {
                "type": "status",
                "state": "started",
                "message": f"Analysis starting ({context})"
            })

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

            # Fetch RAG context if enabled
            rag_context = await self._fetch_rag_context(request, event_callback)

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
                event_callback=event_callback
            )

            # Check for Branch Analysis / Reconciliation mode
            if request.analysisType == "BRANCH_ANALYSIS":
                 logger.info("Executing Branch Analysis & Reconciliation mode")
                 # Build specific prompt for branch analysis
                 pr_metadata = self._build_pr_metadata(request)
                 prompt = PromptBuilder.build_branch_review_prompt_with_branch_issues_data(pr_metadata)
                 
                 result = await orchestrator.execute_branch_analysis(prompt)
            else:
                # Execute review with Multi-Stage Orchestrator
                # Standard PR Review
                result = await orchestrator.orchestrate_review(
                    request=request, 
                    rag_context=rag_context,
                    processed_diff=processed_diff
                )


            # Post-process issues to fix line numbers and merge duplicates
            if result and 'issues' in result:
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "post_processing",
                    "message": "Post-processing issues (fixing line numbers, merging duplicates)..."
                })
                
                # Get diff content for line validation
                diff_content = request.rawDiff if has_raw_diff else None
                
                # For branch reconciliation, pass previous issues to restore missing diffs
                previous_issues = None
                if request.previousCodeAnalysisIssues:
                    previous_issues = [
                        issue.model_dump() if hasattr(issue, 'model_dump') else issue
                        for issue in request.previousCodeAnalysisIssues
                    ]
                
                result = post_process_analysis_result(
                    result, 
                    diff_content=diff_content,
                    previous_issues=previous_issues
                )
                
                original_count = result.get('_original_issue_count', len(result.get('issues', [])))
                final_count = result.get('_final_issue_count', len(result.get('issues', [])))
                
                if original_count != final_count:
                    logger.info(f"Post-processing: {original_count} issues -> {final_count} issues (merged duplicates)")

            self._emit_event(event_callback, {
                "type": "status",
                "state": "completed",
                "message": "MCP Agent has completed processing the Pull Request, report is being generated..."
            })

            return {"result": result}

        except Exception as e:
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
            event_callback: Optional[Callable[[Dict], None]]
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch relevant context from RAG pipeline.

        Returns:
            Dict with RAG context or None if RAG is disabled/failed
        """
        start_time = datetime.now()
        cache_hit = False
        
        # Determine branch for RAG query
        # For PR analysis: use target branch (where code will be merged)
        # For branch analysis: targetBranchName is set to the analyzed branch
        rag_branch = request.targetBranchName
        if not rag_branch:
            logger.warning("No target branch specified for RAG query, skipping RAG context")
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
                return cached_result

            # Fetch from RAG service
            rag_response = await self.rag_client.get_pr_context(
                workspace=request.projectWorkspace,
                project=request.projectNamespace,
                branch=rag_branch,
                changed_files=changed_files,
                diff_snippets=diff_snippets,
                pr_title=request.prTitle,
                pr_description=request.prDescription,
                top_k=RAG_DEFAULT_TOP_K  # Fetch more for reranking
            )

            if rag_response and rag_response.get("context"):
                context = rag_response.get("context")
                relevant_code = context.get("relevant_code", [])
                
                # Apply LLM reranking for large PRs
                if len(changed_files) >= self.LLM_RERANK_FILE_THRESHOLD and self.llm_reranker:
                    reranked, rerank_result = await self.llm_reranker.rerank(
                        relevant_code,
                        pr_title=request.prTitle,
                        pr_description=request.prDescription,
                        changed_files=changed_files
                    )
                    context["relevant_code"] = reranked
                    logger.info(f"LLM reranking result: {rerank_result}")
                
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
                    reranking_applied=len(changed_files) >= self.LLM_RERANK_FILE_THRESHOLD,
                    cache_hit=False
                )
                logger.info(f"RAG metrics: {metrics.to_dict()}")
                
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "rag_retrieved",
                    "message": f"Retrieved {len(relevant_code)} context chunks from RAG",
                    "metrics": metrics.to_dict()
                })
                return context

            return None

        except Exception as e:
            logger.warning(f"Failed to fetch RAG context: {e}")
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
        """Create LLM instance from request parameters and initialize reranker."""
        try:
            # Log the model being used for this request
            logger.info(f"Creating LLM for project {request.projectId}: provider={request.aiProvider}, model={request.aiModel}")
            
            llm = LLMFactory.create_llm(
                request.aiModel,
                request.aiProvider,
                request.aiApiKey
            )
            
            # Initialize LLM reranker for large PRs
            self.llm_reranker = LLMReranker(llm_client=llm)
            
            return llm
        except Exception as e:
            raise Exception(f"Failed to create LLM instance: {str(e)}")

    def _build_pr_metadata(self, request: ReviewRequestDto) -> Dict[str, Any]:
        """Build pull request metadata dictionary from request."""
        return {
            "branch": request.targetBranchName,
            "commitHash": request.commitHash,
            "pullRequestId": request.pullRequestId,
            "repoSlug": request.projectVcsRepoSlug,
            "workspace": request.projectVcsWorkspace,
            "previousCodeAnalysisIssues": [
                issue.dict(by_alias=True, exclude_none=True)
                for issue in (request.previousCodeAnalysisIssues or [])
            ]
        }

    @staticmethod
    def _emit_event(callback: Optional[Callable[[Dict], None]], event: Dict[str, Any]) -> None:
        """Safely emit an event via the callback."""
        if callback:
            try:
                callback(event)
            except Exception as e:
                # Don't let callback errors break the processing
                logger.warning(f"Event callback failed: {e}")