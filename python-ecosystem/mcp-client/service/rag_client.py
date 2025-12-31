"""
RAG Pipeline client for retrieving contextual information during code review.
"""
import os
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
import httpx

logger = logging.getLogger(__name__)

# RAG configuration from environment variables
RAG_MIN_RELEVANCE_SCORE = float(os.environ.get("RAG_MIN_RELEVANCE_SCORE", "0.7"))
RAG_DEFAULT_TOP_K = int(os.environ.get("RAG_DEFAULT_TOP_K", "15"))


class RagClient:
    """Client for interacting with the RAG Pipeline API."""

    def __init__(self, base_url: Optional[str] = None, enabled: Optional[bool] = None):
        """
        Initialize RAG client.

        Args:
            base_url: RAG pipeline API URL (default from env RAG_API_URL)
            enabled: Whether RAG is enabled (default from env RAG_ENABLED)
        """
        self.base_url = base_url or os.environ.get("RAG_API_URL", "http://rag-pipeline:8001")
        self.enabled = enabled if enabled is not None else os.environ.get("RAG_ENABLED", "false").lower() == "true"
        self.timeout = 30.0

        if self.enabled:
            logger.info(f"RAG client initialized: {self.base_url}")
        else:
            logger.info("RAG client disabled")

    async def get_pr_context(
        self,
        workspace: str,
        project: str,
        branch: Optional[str],
        changed_files: List[str],
        diff_snippets: Optional[List[str]] = None,
        pr_title: Optional[str] = None,
        pr_description: Optional[str] = None,
        top_k: int = None,
        enable_priority_reranking: bool = True,
        min_relevance_score: float = None
    ) -> Dict[str, Any]:
        """
        Get relevant context for PR review with Lost-in-Middle protection.

        Args:
            workspace: Workspace identifier
            project: Project identifier
            branch: Branch name (typically target branch) - required for RAG query
            changed_files: List of files changed in the PR
            diff_snippets: Code snippets extracted from diff for semantic search
            pr_title: PR title for semantic understanding
            pr_description: Optional PR description
            top_k: Number of relevant chunks to retrieve (default from RAG_DEFAULT_TOP_K)
            enable_priority_reranking: Enable priority-based score boosting
            min_relevance_score: Minimum relevance threshold (default from RAG_MIN_RELEVANCE_SCORE)

        Returns:
            Dict with context information or empty dict if RAG is disabled
        """
        if not self.enabled:
            logger.debug("RAG disabled, returning empty context")
            return {"context": {"relevant_code": []}}

        # Branch is required for RAG query
        if not branch:
            logger.warning("Branch not specified for RAG query, skipping")
            return {"context": {"relevant_code": []}}

        # Apply defaults from env vars
        if top_k is None:
            top_k = RAG_DEFAULT_TOP_K
        if min_relevance_score is None:
            min_relevance_score = RAG_MIN_RELEVANCE_SCORE

        start_time = datetime.now()
        
        try:
            payload = {
                "workspace": workspace,
                "project": project,
                "branch": branch,
                "changed_files": changed_files,
                "diff_snippets": diff_snippets or [],
                "pr_title": pr_title,
                "pr_description": pr_description,
                "top_k": top_k,
                "enable_priority_reranking": enable_priority_reranking,
                "min_relevance_score": min_relevance_score
            }

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/query/pr-context",
                    json=payload
                )
                response.raise_for_status()
                result = response.json()
                
                # Log timing and result stats
                elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
                chunk_count = len(result.get("context", {}).get("relevant_code", []))
                logger.info(f"RAG query completed in {elapsed_ms:.2f}ms, retrieved {chunk_count} chunks")
                
                return result

        except httpx.HTTPError as e:
            logger.warning(f"Failed to retrieve PR context from RAG: {e}")
            return {"context": {"relevant_code": []}}
        except Exception as e:
            logger.error(f"Unexpected error querying RAG: {e}")
            return {"context": {"relevant_code": []}}

    async def semantic_search(
        self,
        query: str,
        workspace: str,
        project: str,
        branch: str,
        top_k: int = 5,
        filter_language: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Perform semantic search in the repository.

        Args:
            query: Search query
            workspace: Workspace identifier
            project: Project identifier
            branch: Branch name
            top_k: Number of results to return
            filter_language: Optional language filter (e.g., 'python', 'java')

        Returns:
            Dict with search results or empty results if RAG is disabled
        """
        if not self.enabled:
            return {"results": []}

        try:
            payload = {
                "query": query,
                "workspace": workspace,
                "project": project,
                "branch": branch,
                "top_k": top_k
            }
            if filter_language:
                payload["filter_language"] = filter_language

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/query/search",
                    json=payload
                )
                response.raise_for_status()
                return response.json()

        except httpx.HTTPError as e:
            logger.warning(f"Semantic search failed: {e}")
            return {"results": []}
        except Exception as e:
            logger.error(f"Unexpected error in semantic search: {e}")
            return {"results": []}

    async def is_healthy(self) -> bool:
        """
        Check if RAG pipeline is healthy.

        Returns:
            True if RAG is enabled and healthy, False otherwise
        """
        if not self.enabled:
            return False

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/health")
                return response.status_code == 200
        except Exception as e:
            logger.warning(f"RAG health check failed: {e}")
            return False

