import os
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Callable
from dotenv import load_dotenv
from mcp_use import MCPAgent, MCPClient
from langchain_core.agents import AgentAction

from model.models import ReviewRequestDto, CodeReviewOutput, CodeReviewIssue
from utils.mcp_config import MCPConfigBuilder
from llm.llm_factory import LLMFactory
from utils.prompt_builder import PromptBuilder
from utils.response_parser import ResponseParser
from service.rag_client import RagClient, RAG_MIN_RELEVANCE_SCORE, RAG_DEFAULT_TOP_K
from service.llm_reranker import LLMReranker
from utils.context_builder import (
    ContextBuilder, ContextBudget, RagReranker, 
    RAGMetrics, SmartChunker, get_rag_cache
)
from utils.file_classifier import FileClassifier, FilePriority
from utils.prompt_logger import PromptLogger
from utils.diff_processor import DiffProcessor, ProcessedDiff, format_diff_for_prompt

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
            #"/var/www/html/codecrow/java-bitbucket-mcp/codecrow-mcp-servers/target/codecrow-mcp-servers-1.0.jar",
            "/app/codecrow-mcp-servers-1.0.jar"
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

    async def process_review_request_with_local_repo(
            self,
            request: ReviewRequestDto,
            repo_path: str,
            max_allowed_tokens: Optional[int] = None,
            event_callback: Optional[Callable[[Dict], None]] = None
    ) -> Dict[str, Any]:
        """
        Process a review request using a local repository directory.

        Args:
            request: The review request data
            repo_path: Path to the local repository
            max_allowed_tokens: Optional token limit
            event_callback: Optional callback to receive progress events

        Returns:
            Dict with "result" key containing the analysis result or error
        """
        return await self._process_review(
            request=request,
            repo_path=repo_path,
            max_allowed_tokens=max_allowed_tokens,
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

            # Build prompt - different depending on whether we have rawDiff
            pr_metadata = self._build_pr_metadata(request)
            
            if has_raw_diff:
                # Process and embed diff directly in prompt
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
                
                prompt = self._build_prompt_with_diff(
                    request=request,
                    pr_metadata=pr_metadata,
                    processed_diff=processed_diff,
                    rag_context=rag_context
                )
            else:
                # Standard prompt - agent will fetch diff via MCP tool
                prompt = self._build_prompt(request, pr_metadata, rag_context)

            self._emit_event(event_callback, {
                "type": "status",
                "state": "mcp_initialized",
                "message": "MCP server ready, starting analysis"
            })

            # Execute review with MCP agent - always use agent for tool access
            result = await self._execute_review_with_streaming(
                llm=llm,
                client=client,
                prompt=prompt,
                event_callback=event_callback,
                request=request
            )

            self._emit_event(event_callback, {
                "type": "final",
                "result": "MCP Agent has completed processing the Pull Request, report is being generated..."
            })

            return {"result": result}

        except Exception as e:
            error_response = ResponseParser.create_error_response(
                f"Agent execution failed", str(e)
            )
            self._emit_event(event_callback, {
                "type": "error",
                "message": str(e)
            })
            return {"result": error_response}

    async def _execute_review_with_streaming(
            self,
            llm,
            client: MCPClient,
            prompt: str,
            event_callback: Optional[Callable[[Dict], None]],
            request: Optional[ReviewRequestDto] = None
    ) -> Dict[str, Any]:
        """
        Execute the code review using MCP agent with real streaming output.

        This method uses the agent's stream() method to capture intermediate outputs
        (tool calls, observations) and forwards them via event_callback in real-time.
        Uses output_schema for structured JSON output.
        """
        additional_instructions = PromptBuilder.get_additional_instructions()
        max_steps = 120

        # Create agent
        agent = MCPAgent(
            llm=llm,
            client=client,
            max_steps=max_steps,
            additional_instructions=additional_instructions,
        )

        try:
            self._emit_event(event_callback, {
                "type": "progress",
                "step": 0,
                "max_steps": max_steps,
                "message": "Agent execution started"
            })

            step_count = 0
            final_result = None

            # Use streaming with output_schema for structured JSON output
            async for item in agent.stream(
                prompt,
                max_steps=max_steps,
                output_schema=CodeReviewOutput
            ):
                # item can be:
                # - tuple[AgentAction, str]: (action, observation) for tool calls
                # - str: intermediate text
                # - CodeReviewOutput: final structured output
                
                if isinstance(item, tuple) and len(item) == 2:
                    # Tool call with observation
                    action, observation = item
                    step_count += 1
                    
                    tool_name = action.tool if hasattr(action, 'tool') else str(action)
                    tool_input = action.tool_input if hasattr(action, 'tool_input') else {}
                    
                    # Truncate observation for logging
                    obs_preview = str(observation)[:200] + "..." if len(str(observation)) > 200 else str(observation)
                    
                    logger.info(f"[Step {step_count}] Tool: {tool_name}, Input: {tool_input}")
                    logger.debug(f"[Step {step_count}] Observation: {obs_preview}")
                    
                    self._emit_event(event_callback, {
                        "type": "mcp_step",
                        "step": step_count,
                        "max_steps": max_steps,
                        "tool": tool_name,
                        "tool_input": tool_input,
                        "observation_preview": obs_preview,
                        "message": f"Executed tool: {tool_name}"
                    })
                    
                elif isinstance(item, CodeReviewOutput):
                    # Final structured output
                    final_result = item
                    logger.info(f"Received structured output with {len(item.issues)} issues")
                    
                elif isinstance(item, str):
                    # Intermediate text output
                    final_result = item
                    logger.debug(f"Intermediate output: {item[:100]}...")
                    
                    self._emit_event(event_callback, {
                        "type": "progress",
                        "step": step_count,
                        "max_steps": max_steps,
                        "message": "Processing..."
                    })

            # Emit completion progress
            self._emit_event(event_callback, {
                "type": "progress",
                "step": max_steps,
                "max_steps": max_steps,
                "message": f"Agent execution completed ({step_count} tool calls)"
            })

            # Process the result
            if isinstance(final_result, CodeReviewOutput):
                # Convert Pydantic model to dict
                result_dict = {
                    "comment": final_result.comment,
                    "issues": [issue.model_dump() for issue in final_result.issues]
                }
                logger.info(f"Structured output: {len(final_result.issues)} issues found")
                
                # Log using PromptLogger
                PromptLogger.log_llm_response(
                    str(result_dict),
                    metadata={"result_type": "structured", "issue_count": len(final_result.issues)},
                    is_raw=False
                )
                return result_dict
                
            elif isinstance(final_result, str) and final_result:
                # Fallback: parse string result
                logger.info(f"Agent returned string result, attempting to parse")
                result_preview = final_result[:500] if len(final_result) > 500 else final_result
                logger.info(f"Agent raw result preview: {result_preview}")
                
                PromptLogger.log_llm_response(
                    final_result,
                    metadata={"result_length": len(final_result)},
                    is_raw=True
                )
                
                ResponseParser.reset_retry_state()
                parsed_result = ResponseParser.extract_json_from_response(final_result)
                
                # Check if parsing failed and try LLM fix
                if parsed_result.get("_needs_retry") and final_result:
                    logger.info("Initial parsing failed, attempting LLM-based fix...")
                    self._emit_event(event_callback, {
                        "type": "progress",
                        "step": max_steps,
                        "max_steps": max_steps,
                        "message": "Attempting to fix response format..."
                    })
                    
                    fixed_result = await self._try_fix_response_with_llm(
                        final_result, llm, event_callback
                    )
                    if fixed_result:
                        fixed_result.pop("_needs_retry", None)
                        fixed_result.pop("_raw_response", None)
                        return fixed_result
                
                parsed_result.pop("_needs_retry", None)
                parsed_result.pop("_raw_response", None)
                return parsed_result
            else:
                logger.warning("Agent returned empty or None result")
                return ResponseParser.create_error_response(
                    "Empty response", "Agent returned no result"
                )

        except Exception as e:
            self._emit_event(event_callback, {
                "type": "error",
                "message": f"Agent execution error: {str(e)}"
            })
            raise
    
    async def _try_fix_response_with_llm(
            self,
            raw_response: str,
            llm,
            event_callback: Optional[Callable[[Dict], None]]
    ) -> Optional[Dict[str, Any]]:
        """
        Try to fix a malformed response using a direct LLM call.
        
        Args:
            raw_response: The raw response that failed to parse
            llm: The LLM instance to use for fixing
            event_callback: Optional callback for progress events
            
        Returns:
            Fixed and parsed response, or None if all retries failed
        """
        fix_prompt = ResponseParser.get_fix_prompt(raw_response)
        
        for attempt in range(self.MAX_FIX_RETRIES):
            try:
                logger.info(f"LLM fix attempt {attempt + 1}/{self.MAX_FIX_RETRIES}")
                self._emit_event(event_callback, {
                    "type": "progress",
                    "step": 0,
                    "max_steps": self.MAX_FIX_RETRIES,
                    "message": f"Fix attempt {attempt + 1}/{self.MAX_FIX_RETRIES}..."
                })
                
                # Make a simple direct LLM call (no MCP tools)
                response = await llm.ainvoke(fix_prompt)
                
                # Extract the content from the response
                if hasattr(response, 'content'):
                    fixed_text = response.content
                else:
                    fixed_text = str(response)
                
                logger.info(f"LLM fix response preview: {fixed_text[:300] if len(fixed_text) > 300 else fixed_text}")
                
                # Try to parse the fixed response
                # Don't track retry state for the fix attempts
                ResponseParser._last_parse_needs_retry = False
                fixed_result = ResponseParser.extract_json_from_response(fixed_text)
                
                # Check if parsing succeeded (no error markers)
                if not fixed_result.get("_needs_retry"):
                    if "comment" in fixed_result and "issues" in fixed_result:
                        # Validate it's not an error response
                        comment = fixed_result.get("comment", "")
                        if not comment.startswith("Failed to parse"):
                            logger.info(f"LLM fix succeeded on attempt {attempt + 1}")
                            self._emit_event(event_callback, {
                                "type": "status",
                                "state": "fix_succeeded",
                                "message": f"Response fixed successfully on attempt {attempt + 1}"
                            })
                            return fixed_result
                
                logger.warning(f"LLM fix attempt {attempt + 1} produced invalid result")
                
            except Exception as e:
                logger.error(f"LLM fix attempt {attempt + 1} failed with error: {e}")
                self._emit_event(event_callback, {
                    "type": "warning",
                    "message": f"Fix attempt {attempt + 1} failed: {str(e)}"
                })
        
        logger.error(f"All {self.MAX_FIX_RETRIES} LLM fix attempts failed")
        self._emit_event(event_callback, {
            "type": "warning",
            "message": f"Failed to fix response after {self.MAX_FIX_RETRIES} attempts"
        })
        return None

    async def _execute_direct_review(
            self,
            llm,
            prompt: str,
            event_callback: Optional[Callable[[Dict], None]]
    ) -> Dict[str, Any]:
        """
        Execute code review using direct LLM call (no MCP agent).
        
        This is more efficient when diff is already available.
        """
        max_steps = 10  # Simplified progress for direct mode
        
        try:
            self._emit_event(event_callback, {
                "type": "progress",
                "step": 1,
                "max_steps": max_steps,
                "message": "Sending request to LLM"
            })
            
            # Direct LLM call
            response = await llm.ainvoke(prompt)
            
            # Extract content
            if hasattr(response, 'content'):
                raw_result = response.content
            else:
                raw_result = str(response)
            
            self._emit_event(event_callback, {
                "type": "progress",
                "step": 5,
                "max_steps": max_steps,
                "message": "Processing LLM response"
            })
            
            # Log response
            if raw_result:
                result_preview = raw_result[:500] if len(raw_result) > 500 else raw_result
                logger.info(f"Direct review result preview: {result_preview}")
                
                PromptLogger.log_llm_response(
                    raw_result,
                    metadata={"mode": "direct", "result_length": len(raw_result)},
                    is_raw=True
                )
            else:
                logger.warning("LLM returned empty response")
            
            # Parse result
            ResponseParser.reset_retry_state()
            parsed_result = ResponseParser.extract_json_from_response(raw_result)
            
            # Try to fix if needed
            if parsed_result.get("_needs_retry") and raw_result:
                logger.info("Direct mode: Initial parsing failed, attempting fix...")
                fixed_result = await self._try_fix_response_with_llm(
                    raw_result, llm, event_callback
                )
                if fixed_result:
                    fixed_result.pop("_needs_retry", None)
                    fixed_result.pop("_raw_response", None)
                    return fixed_result
            
            parsed_result.pop("_needs_retry", None)
            parsed_result.pop("_raw_response", None)
            
            self._emit_event(event_callback, {
                "type": "progress",
                "step": max_steps,
                "max_steps": max_steps,
                "message": "Analysis completed"
            })
            
            return parsed_result
            
        except Exception as e:
            logger.exception("Direct review execution failed")
            self._emit_event(event_callback, {
                "type": "error",
                "message": f"Direct review failed: {str(e)}"
            })
            raise

    def _build_prompt_with_diff(
            self,
            request: ReviewRequestDto,
            pr_metadata: Dict[str, Any],
            processed_diff: ProcessedDiff,
            rag_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Build prompt for MCP agent with embedded diff.
        
        This prompt includes the actual diff content so agent doesn't need
        to call getPullRequestDiff, but still has access to all other MCP tools.
        """
        analysis_type = request.analysisType
        has_previous_analysis = bool(request.previousCodeAnalysisIssues)
        
        if analysis_type is not None and analysis_type == "BRANCH_ANALYSIS":
            return PromptBuilder.build_branch_review_prompt_with_branch_issues_data(pr_metadata)
        
        # Build structured context for Lost-in-the-Middle protection
        structured_context = None
        if request.changedFiles:
            try:
                changed_files = request.changedFiles or []
                classified_files = FileClassifier.classify_files(changed_files)
                
                if rag_context and rag_context.get("relevant_code"):
                    rag_context["relevant_code"] = RagReranker.rerank_by_file_priority(
                        rag_context["relevant_code"],
                        classified_files
                    )
                    rag_context["relevant_code"] = RagReranker.deduplicate_by_content(
                        rag_context["relevant_code"]
                    )
                    rag_context["relevant_code"] = RagReranker.filter_by_relevance_threshold(
                        rag_context["relevant_code"],
                        min_score=RAG_MIN_RELEVANCE_SCORE,
                        min_results=3
                    )
                
                budget = ContextBudget.for_model(request.aiModel)
                rag_token_budget = budget.rag_tokens
                avg_tokens_per_chunk = 600
                max_rag_chunks = max(5, min(15, rag_token_budget // avg_tokens_per_chunk))
                
                structured_context = PromptBuilder.build_structured_rag_section(
                    rag_context,
                    max_chunks=max_rag_chunks,
                    token_budget=rag_token_budget
                )
                
                stats = FileClassifier.get_priority_stats(classified_files)
                logger.info(f"File classification for direct mode: {stats}")
                
            except Exception as e:
                logger.warning(f"Failed to build structured context: {e}")
                structured_context = None
        
        # Format diff for prompt
        formatted_diff = format_diff_for_prompt(
            processed_diff,
            include_stats=True,
            max_chars=None  # Let token budget handle truncation
        )
        
        # Build the prompt using PromptBuilder with embedded diff
        if has_previous_analysis:
            prompt = PromptBuilder.build_direct_review_prompt_with_previous_analysis(
                pr_metadata=pr_metadata,
                diff_content=formatted_diff,
                rag_context=rag_context,
                structured_context=structured_context
            )
        else:
            prompt = PromptBuilder.build_direct_first_review_prompt(
                pr_metadata=pr_metadata,
                diff_content=formatted_diff,
                rag_context=rag_context,
                structured_context=structured_context
            )
        
        # Log prompt
        prompt_metadata = {
            "workspace": request.projectVcsWorkspace,
            "repo": request.projectVcsRepoSlug,
            "pr_id": request.pullRequestId,
            "model": request.aiModel,
            "provider": request.aiProvider,
            "mode": "direct",
            "has_previous_analysis": has_previous_analysis,
            "changed_files_count": processed_diff.total_files,
            "diff_size_bytes": processed_diff.processed_size_bytes,
            "rag_chunks_count": len(rag_context.get("relevant_code", [])) if rag_context else 0,
        }
        
        if rag_context:
            PromptLogger.log_rag_context(rag_context, prompt_metadata, stage="rag_direct_mode")
        
        if structured_context:
            PromptLogger.log_structured_context(structured_context, prompt_metadata)
        
        PromptLogger.log_prompt(prompt, prompt_metadata, stage="direct_full_prompt")
        
        return prompt

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
                branch=request.targetBranchName,
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
                branch=request.targetBranchName,
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
                    branch=request.targetBranchName,
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

    def _build_prompt(
            self,
            request: ReviewRequestDto,
            pr_metadata: Dict[str, Any],
            rag_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Build the appropriate prompt based on previous analysis and RAG context."""
        analysis_type = request.analysisType
        has_previous_analysis = bool(request.previousCodeAnalysisIssues)

        if analysis_type is not None and analysis_type == "BRANCH_ANALYSIS":
            return PromptBuilder.build_branch_review_prompt_with_branch_issues_data(pr_metadata)

        # Build structured context for Lost-in-the-Middle protection
        structured_context = None
        if request.changedFiles:
            try:
                # Prepare file classification and reranking
                changed_files = request.changedFiles or []
                classified_files = FileClassifier.classify_files(changed_files)
                
                # Rerank RAG results based on file priorities
                if rag_context and rag_context.get("relevant_code"):
                    rag_context["relevant_code"] = RagReranker.rerank_by_file_priority(
                        rag_context["relevant_code"],
                        classified_files
                    )
                    rag_context["relevant_code"] = RagReranker.deduplicate_by_content(
                        rag_context["relevant_code"]
                    )
                    rag_context["relevant_code"] = RagReranker.filter_by_relevance_threshold(
                        rag_context["relevant_code"],
                        min_score=RAG_MIN_RELEVANCE_SCORE,
                        min_results=3
                    )
                
                # Get dynamic token budget based on model
                budget = ContextBudget.for_model(request.aiModel)
                rag_token_budget = budget.rag_tokens
                
                # Calculate max chunks based on token budget (roughly 500-800 tokens per chunk)
                # Increase from fixed 5 to dynamic based on budget
                avg_tokens_per_chunk = 600
                max_rag_chunks = max(5, min(15, rag_token_budget // avg_tokens_per_chunk))
                
                # Build structured RAG section with dynamic budget
                structured_context = PromptBuilder.build_structured_rag_section(
                    rag_context,
                    max_chunks=max_rag_chunks,
                    token_budget=rag_token_budget
                )
                
                # Log classification stats
                stats = FileClassifier.get_priority_stats(classified_files)
                logger.info(f"File classification for Lost-in-Middle protection: {stats}")
                logger.info(f"Using token budget for model '{request.aiModel}': {budget.total_tokens} total, {rag_token_budget} for RAG, max_chunks={max_rag_chunks}")
                
            except Exception as e:
                logger.warning(f"Failed to build structured context: {e}, falling back to legacy format")
                structured_context = None

        if has_previous_analysis:
            prompt = PromptBuilder.build_review_prompt_with_previous_analysis_data(
                pr_metadata, rag_context, structured_context
            )
        else:
            prompt = PromptBuilder.build_first_review_prompt(
                pr_metadata, rag_context, structured_context
            )
        
        # Log full prompt for debugging
        prompt_metadata = {
            "workspace": request.projectVcsWorkspace,
            "repo": request.projectVcsRepoSlug,
            "pr_id": request.pullRequestId,
            "model": request.aiModel,
            "provider": request.aiProvider,
            "has_previous_analysis": has_previous_analysis,
            "changed_files_count": len(request.changedFiles or []),
            "rag_chunks_count": len(rag_context.get("relevant_code", [])) if rag_context else 0,
            "has_structured_context": structured_context is not None,
        }
        
        # Log RAG context separately (before full prompt)
        if rag_context:
            PromptLogger.log_rag_context(rag_context, prompt_metadata, stage="rag_after_reranking")
        
        # Log structured context
        if structured_context:
            PromptLogger.log_structured_context(structured_context, prompt_metadata)
        
        # Log full prompt
        PromptLogger.log_prompt(prompt, prompt_metadata, stage="full_prompt")
        
        return prompt

    def _create_mcp_client(self, config: Dict[str, Any]) -> MCPClient:
        """Create MCP client from configuration."""
        try:
            return MCPClient.from_dict(config)
        except Exception as e:
            raise Exception(f"Failed to construct MCPClient: {str(e)}")

    def _create_llm(self, request: ReviewRequestDto):
        """Create LLM instance from request parameters and initialize reranker."""
        try:
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
                print(f"Warning: Event callback failed: {e}")