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
    
    # Shared HTTP client for connection pooling
    _shared_client: Optional[httpx.AsyncClient] = None

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
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create a shared HTTP client for connection pooling."""
        if RagClient._shared_client is None or RagClient._shared_client.is_closed:
            RagClient._shared_client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5)
            )
        return RagClient._shared_client
    
    async def close(self):
        """Close the shared HTTP client."""
        if RagClient._shared_client is not None and not RagClient._shared_client.is_closed:
            await RagClient._shared_client.aclose()
            RagClient._shared_client = None

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
        min_relevance_score: float = None,
        base_branch: Optional[str] = None,
        deleted_files: Optional[List[str]] = None,
        pr_number: Optional[int] = None,
        all_pr_changed_files: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Get relevant context for PR review with multi-branch support.

        Args:
            workspace: Workspace identifier
            project: Project identifier
            branch: Target branch (PR source branch)
            changed_files: List of files changed in the PR
            diff_snippets: Code snippets extracted from diff for semantic search
            pr_title: PR title for semantic understanding
            pr_description: Optional PR description
            top_k: Number of relevant chunks to retrieve (default from RAG_DEFAULT_TOP_K)
            enable_priority_reranking: Enable priority-based score boosting
            min_relevance_score: Minimum relevance threshold (default from RAG_MIN_RELEVANCE_SCORE)
            base_branch: Base branch (PR target, e.g., 'main'). Auto-detected if not provided.
            deleted_files: Files deleted in target branch (excluded from results)
            pr_number: If set, enables hybrid query with PR-indexed data priority
            all_pr_changed_files: All files in PR (for exclusion from branch query in hybrid mode)

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
            
            # Add optional multi-branch parameters
            if base_branch:
                payload["base_branch"] = base_branch
            if deleted_files:
                payload["deleted_files"] = deleted_files
            
            # Add hybrid mode parameters
            if pr_number:
                payload["pr_number"] = pr_number
            if all_pr_changed_files:
                payload["all_pr_changed_files"] = all_pr_changed_files

            client = await self._get_client()
            response = await client.post(
                f"{self.base_url}/query/pr-context",
                json=payload
            )
            response.raise_for_status()
            result = response.json()
            
            # Log timing and result stats
            elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
            chunk_count = len(result.get("context", {}).get("relevant_code", []))
            branches_searched = result.get("context", {}).get("_branches_searched", [branch])
            logger.info(f"RAG query completed in {elapsed_ms:.2f}ms, retrieved {chunk_count} chunks from branches: {branches_searched}")
            
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

            client = await self._get_client()
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
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/health")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"RAG health check failed: {e}")
            return False

    async def get_deterministic_context(
        self,
        workspace: str,
        project: str,
        branches: List[str],
        file_paths: List[str],
        limit_per_file: int = 10
    ) -> Dict[str, Any]:
        """
        Get context using DETERMINISTIC metadata-based retrieval.
        
        Two-step process leveraging tree-sitter metadata:
        1. Get chunks for the changed file_paths
        2. Extract semantic_names/imports/extends from those chunks
        3. Find related definitions using extracted identifiers
        
        NO language-specific parsing needed - tree-sitter did it during indexing!
        Predictable: same input = same output.

        Args:
            workspace: Workspace identifier
            project: Project identifier
            branches: Branches to search (e.g., ['release/1.29', 'master'])
            file_paths: Changed file paths from diff
            limit_per_file: Max chunks per file (default 10)

        Returns:
            Dict with chunks grouped by: changed_files, related_definitions
        """
        if not self.enabled:
            logger.debug("RAG disabled, returning empty deterministic context")
            return {"context": {"chunks": [], "changed_files": {}, "related_definitions": {}}}

        start_time = datetime.now()
        
        try:
            payload = {
                "workspace": workspace,
                "project": project,
                "branches": branches,
                "file_paths": file_paths,
                "limit_per_file": limit_per_file
            }

            client = await self._get_client()
            response = await client.post(
                f"{self.base_url}/query/deterministic",
                json=payload
            )
            response.raise_for_status()
            result = response.json()
            
            # Log timing and stats
            elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
            context = result.get("context", {})
            chunk_count = len(context.get("chunks", []))
            logger.info(f"Deterministic RAG query completed in {elapsed_ms:.2f}ms, "
                       f"retrieved {chunk_count} chunks for {len(file_paths)} files")
            
            return result

        except httpx.HTTPError as e:
            logger.warning(f"Failed to retrieve deterministic context: {e}")
            return {"context": {"chunks": [], "by_identifier": {}, "by_file": {}}}
        except Exception as e:
            logger.error(f"Unexpected error in deterministic RAG query: {e}")
            return {"context": {"chunks": [], "by_identifier": {}, "by_file": {}}}

    # =========================================================================
    # PR File Indexing Methods (for PR-specific RAG layer)
    # =========================================================================

    async def index_pr_files(
        self,
        workspace: str,
        project: str,
        pr_number: int,
        branch: str,
        files: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """
        Index PR files into the main collection with PR-specific metadata.
        
        Files are indexed with metadata (pr=true, pr_number=X) to enable
        hybrid queries that prioritize PR data over branch data.
        
        Existing PR points for the same pr_number are deleted first.

        Args:
            workspace: Workspace identifier
            project: Project identifier
            pr_number: PR number for metadata tagging
            branch: Source branch name
            files: List of {path: str, content: str, change_type: str}

        Returns:
            Dict with indexing status and chunk counts
        """
        if not self.enabled:
            logger.debug("RAG disabled, skipping PR file indexing")
            return {"status": "skipped", "chunks_indexed": 0}

        if not files:
            logger.debug("No files to index for PR")
            return {"status": "skipped", "chunks_indexed": 0}

        try:
            payload = {
                "workspace": workspace,
                "project": project,
                "pr_number": pr_number,
                "branch": branch,
                "files": files
            }

            client = await self._get_client()
            response = await client.post(
                f"{self.base_url}/index/pr-files",
                json=payload,
                timeout=120.0  # Longer timeout for indexing
            )
            response.raise_for_status()
            result = response.json()
            
            logger.info(f"Indexed PR #{pr_number}: {result.get('chunks_indexed', 0)} chunks from {result.get('files_processed', 0)} files")
            return result

        except httpx.HTTPError as e:
            logger.warning(f"Failed to index PR files: {e}")
            return {"status": "error", "error": str(e)}
        except Exception as e:
            logger.error(f"Unexpected error indexing PR files: {e}")
            return {"status": "error", "error": str(e)}

    async def delete_pr_files(
        self,
        workspace: str,
        project: str,
        pr_number: int
    ) -> bool:
        """
        Delete all indexed points for a specific PR.
        
        Called after analysis completes to clean up PR-specific data.

        Args:
            workspace: Workspace identifier
            project: Project identifier
            pr_number: PR number to delete

        Returns:
            True if deleted successfully, False otherwise
        """
        if not self.enabled:
            return True

        try:
            client = await self._get_client()
            response = await client.delete(
                f"{self.base_url}/index/pr-files/{workspace}/{project}/{pr_number}"
            )
            response.raise_for_status()
            result = response.json()
            
            logger.info(f"Deleted PR #{pr_number} indexed data")
            return result.get("status") == "deleted"

        except httpx.HTTPError as e:
            logger.warning(f"Failed to delete PR files: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error deleting PR files: {e}")
            return False
