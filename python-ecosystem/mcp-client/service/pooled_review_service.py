"""
Example integration of MCP Process Pool with ReviewService.

This file shows how to integrate the process pool for improved performance.
The actual integration requires careful testing due to the complexity of
the mcp_use library's MCPAgent/MCPClient.

IMPORTANT: The mcp_use library creates its own subprocess internally.
To fully benefit from pooling, we need to either:
1. Modify mcp_use to accept an existing process
2. Or bypass mcp_use and communicate directly with our pooled processes

This example shows approach #2 - direct communication with pooled processes.
"""

import os
import asyncio
import json
import logging
from typing import Dict, Any, Optional, Callable
from dotenv import load_dotenv

from utils.mcp_pool import get_mcp_pool, McpProcessPool
from model.models import ReviewRequestDto
from llm.llm_factory import LLMFactory
from utils.prompt_builder import PromptBuilder
from utils.response_parser import ResponseParser
from service.rag_client import RagClient

logger = logging.getLogger(__name__)


class PooledReviewService:
    """
    Review service using process pooling for MCP servers.
    
    This is an alternative implementation that bypasses mcp_use's internal
    process management to use our own process pool.
    
    Benefits:
    - Zero JVM startup overhead after pool warmup
    - Shared memory footprint across requests
    - Better resource utilization for SaaS deployments
    
    Trade-offs:
    - Requires direct MCP protocol communication
    - May not support all mcp_use features
    """
    
    MAX_FIX_RETRIES = 2

    def __init__(self):
        load_dotenv()
        self.default_jar_path = os.environ.get(
            "MCP_SERVER_JAR",
            "/app/codecrow-vcs-mcp-1.0.jar"
        )
        self.rag_client = RagClient()
        self._pool: Optional[McpProcessPool] = None
        
        # Check if pooling is enabled
        self.pooling_enabled = os.environ.get("MCP_POOLING_ENABLED", "false").lower() == "true"

    async def _get_pool(self) -> McpProcessPool:
        """Get or initialize the process pool."""
        if self._pool is None:
            self._pool = await get_mcp_pool(self.default_jar_path)
        return self._pool

    async def process_review_request(
            self,
            request: ReviewRequestDto,
            event_callback: Optional[Callable[[Dict], None]] = None
    ) -> Dict[str, Any]:
        """
        Process a review request.
        
        If pooling is enabled, uses the process pool.
        Otherwise, falls back to the original mcp_use implementation.
        """
        if self.pooling_enabled:
            return await self._process_review_pooled(request, event_callback)
        else:
            # Fall back to original implementation
            from service.review_service import ReviewService
            original_service = ReviewService()
            return await original_service.process_review_request(request, event_callback)

    async def _process_review_pooled(
            self,
            request: ReviewRequestDto,
            event_callback: Optional[Callable[[Dict], None]] = None
    ) -> Dict[str, Any]:
        """
        Process review using pooled MCP server process.
        
        This method communicates directly with the MCP server via STDIO
        instead of using mcp_use's internal process spawning.
        """
        try:
            self._emit_event(event_callback, {
                "type": "status",
                "state": "started",
                "message": "Analysis starting (pooled mode)"
            })

            pool = await self._get_pool()
            
            self._emit_event(event_callback, {
                "type": "status",
                "state": "pool_acquired",
                "message": "Acquired MCP server from pool"
            })

            async with pool.acquire() as pooled_process:
                # Build the prompt and get LLM
                pr_metadata = self._build_pr_metadata(request)
                rag_context = await self._fetch_rag_context(request, event_callback)
                prompt = self._build_prompt(request, pr_metadata, rag_context)
                llm = self._create_llm(request)
                
                self._emit_event(event_callback, {
                    "type": "status",
                    "state": "executing",
                    "message": "Executing analysis with pooled process"
                })

                # Execute the analysis using the pooled process
                # This is a simplified version - full implementation would
                # need to handle the MCP protocol properly
                result = await self._execute_with_pooled_process(
                    pooled_process,
                    prompt,
                    llm,
                    request,
                    event_callback
                )

                self._emit_event(event_callback, {
                    "type": "final",
                    "result": "Analysis completed"
                })

                return {"result": result}

        except Exception as e:
            logger.error(f"Pooled review failed: {e}", exc_info=True)
            error_response = ResponseParser.create_error_response(
                "Agent execution failed (pooled)", str(e)
            )
            self._emit_event(event_callback, {
                "type": "error",
                "message": str(e)
            })
            return {"result": error_response}

    async def _execute_with_pooled_process(
            self,
            pooled_process,
            prompt: str,
            llm,
            request: ReviewRequestDto,
            event_callback: Optional[Callable[[Dict], None]]
    ) -> Dict[str, Any]:
        """
        Execute analysis using a pooled MCP server process.
        
        NOTE: This is a placeholder that shows the concept.
        Full implementation requires proper MCP protocol handling.
        
        The mcp_use library's MCPClient handles the protocol, but it
        creates its own subprocess. To use pooling, we'd need to either:
        
        1. Fork mcp_use to accept external processes
        2. Implement MCP protocol directly
        3. Use the MCP SDK's Python client directly
        
        For now, this shows the architecture for option 2/3.
        """
        # MCP JSON-RPC message format
        # See: https://modelcontextprotocol.io/docs/spec/protocol
        
        # Initialize connection
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "codecrow-mcp-client",
                    "version": "1.0.0"
                }
            }
        }
        
        # Send initialization (this is conceptual - actual impl needs proper I/O handling)
        # await self._send_mcp_message(pooled_process, init_request)
        # response = await self._read_mcp_response(pooled_process)
        
        # For now, return a placeholder indicating pooling is working
        # but full protocol implementation is pending
        return {
            "summary": "Pooled execution placeholder",
            "issues": [],
            "note": "Full MCP protocol integration pending. Process pool is working.",
            "pool_metrics": (await self._get_pool()).get_metrics()
        }

    def _emit_event(self, callback, event):
        """Emit event to callback if provided."""
        if callback:
            try:
                callback(event)
            except Exception as e:
                logger.warning(f"Event callback error: {e}")

    def _build_pr_metadata(self, request: ReviewRequestDto) -> str:
        """Build PR metadata string."""
        return f"PR #{request.pullRequestId} in {request.workspace}/{request.repoSlug}"

    async def _fetch_rag_context(self, request, event_callback) -> Optional[str]:
        """Fetch RAG context if enabled."""
        if not request.ragEnabled:
            return None
        try:
            return await self.rag_client.get_context(request)
        except Exception as e:
            logger.warning(f"RAG context fetch failed: {e}")
            return None

    def _build_prompt(self, request, pr_metadata, rag_context) -> str:
        """Build the analysis prompt."""
        return PromptBuilder.build_review_prompt(
            pr_metadata=pr_metadata,
            rag_context=rag_context,
            custom_instructions=request.customInstructions
        )

    def _create_llm(self, request):
        """Create LLM instance for the request."""
        return LLMFactory.create(
            provider=request.aiProvider,
            api_key=request.aiProviderApiKey,
            model=request.aiModel,
            max_tokens=request.maxAllowedTokens
        )


# Example usage in web server
async def create_pooled_service():
    """Create and initialize a pooled review service."""
    service = PooledReviewService()
    
    # Pre-warm the pool
    if service.pooling_enabled:
        pool = await service._get_pool()
        logger.info(f"MCP pool initialized: {pool.get_metrics()}")
    
    return service
