"""
RAG Pipeline client for retrieving contextual information during code review.
"""
import os
import logging
from typing import Dict, List, Optional, Any
import httpx

logger = logging.getLogger(__name__)


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
        branch: str,
        changed_files: List[str],
        diff_snippets: Optional[List[str]] = None,
        pr_title: Optional[str] = None,
        pr_description: Optional[str] = None,
        top_k: int = 10
    ) -> Dict[str, Any]:
        """
        Get relevant context for PR review.

        Args:
            workspace: Workspace identifier
            project: Project identifier
            branch: Branch name (typically target branch)
            changed_files: List of files changed in the PR
            diff_snippets: Code snippets extracted from diff for semantic search
            pr_title: PR title for semantic understanding
            pr_description: Optional PR description
            top_k: Number of relevant chunks to retrieve

        Returns:
            Dict with context information or empty dict if RAG is disabled
        """
        if not self.enabled:
            logger.debug("RAG disabled, returning empty context")
            return {"context": {"relevant_code": []}}

        try:
            payload = {
                "workspace": workspace,
                "project": project,
                "branch": branch,
                "changed_files": changed_files,
                "diff_snippets": diff_snippets or [],
                "pr_title": pr_title,
                "pr_description": pr_description,
                "top_k": top_k
            }

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/query/pr-context",
                    json=payload
                )
                response.raise_for_status()
                return response.json()

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

