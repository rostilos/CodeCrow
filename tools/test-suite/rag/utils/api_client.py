"""
RAG API Client - HTTP client wrapper for RAG Pipeline API.

Provides typed methods for all RAG API endpoints with error handling.
"""
import logging
import time
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
import requests
from requests.exceptions import RequestException, Timeout

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import RAG_API_URL, API_TIMEOUT, INDEXING_TIMEOUT

logger = logging.getLogger(__name__)


@dataclass
class APIResponse:
    """Standardized API response wrapper."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    status_code: int = 0
    response_time_ms: float = 0.0
    raw_response: Optional[dict] = None


@dataclass
class IndexStats:
    """Index statistics from RAG pipeline."""
    workspace: str
    project: str
    branch: str
    document_count: int = 0
    chunk_count: int = 0
    indexed_at: Optional[str] = None


class RAGAPIClient:
    """
    Client for RAG Pipeline API.
    
    Provides methods for:
    - Repository indexing
    - Semantic search
    - PR context retrieval
    - Deterministic context retrieval
    - Index management
    """
    
    def __init__(self, base_url: str = RAG_API_URL, timeout: int = API_TIMEOUT):
        """
        Initialize RAG API client.
        
        Args:
            base_url: RAG Pipeline API URL
            timeout: Default request timeout in seconds
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
        logger.info(f"RAGAPIClient initialized with base_url={base_url}")
    
    def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[dict] = None,
        params: Optional[dict] = None,
        timeout: Optional[int] = None
    ) -> APIResponse:
        """
        Make HTTP request to RAG API.
        
        Args:
            method: HTTP method (GET, POST, DELETE)
            endpoint: API endpoint path
            data: Request body for POST
            params: Query parameters
            timeout: Request timeout override
            
        Returns:
            APIResponse with result or error
        """
        url = f"{self.base_url}{endpoint}"
        timeout = timeout or self.timeout
        
        start_time = time.time()
        
        try:
            if method == 'GET':
                response = self.session.get(url, params=params, timeout=timeout)
            elif method == 'POST':
                response = self.session.post(url, json=data, timeout=timeout)
            elif method == 'DELETE':
                response = self.session.delete(url, params=params, timeout=timeout)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            response_time = (time.time() - start_time) * 1000
            
            try:
                response_data = response.json()
            except Exception:
                response_data = {"raw_text": response.text}
            
            if response.ok:
                return APIResponse(
                    success=True,
                    data=response_data,
                    status_code=response.status_code,
                    response_time_ms=response_time,
                    raw_response=response_data
                )
            else:
                error_msg = response_data.get('detail', response_data.get('error', str(response_data)))
                return APIResponse(
                    success=False,
                    error=error_msg,
                    status_code=response.status_code,
                    response_time_ms=response_time,
                    raw_response=response_data
                )
                
        except Timeout:
            response_time = (time.time() - start_time) * 1000
            return APIResponse(
                success=False,
                error=f"Request timed out after {timeout}s",
                response_time_ms=response_time
            )
        except RequestException as e:
            response_time = (time.time() - start_time) * 1000
            return APIResponse(
                success=False,
                error=f"Request failed: {str(e)}",
                response_time_ms=response_time
            )
    
    # ==================== Health & Info ====================
    
    def health_check(self) -> APIResponse:
        """Check API health status."""
        return self._request('GET', '/health')
    
    def get_limits(self) -> APIResponse:
        """Get indexing limits configuration."""
        return self._request('GET', '/limits')
    
    # ==================== Indexing ====================
    
    def index_repository(
        self,
        repo_path: str,
        workspace: str,
        project: str,
        branch: str,
        commit: str = "test-commit",
        exclude_patterns: Optional[List[str]] = None
    ) -> APIResponse:
        """
        Index a repository.
        
        Args:
            repo_path: Path to repository on server
            workspace: Workspace identifier
            project: Project identifier
            branch: Branch name
            commit: Commit hash
            exclude_patterns: Patterns to exclude
            
        Returns:
            APIResponse with IndexStats
        """
        data = {
            "repo_path": repo_path,
            "workspace": workspace,
            "project": project,
            "branch": branch,
            "commit": commit,
            "exclude_patterns": exclude_patterns or []
        }
        return self._request('POST', '/index/repository', data=data, timeout=INDEXING_TIMEOUT)
    
    def estimate_repository(
        self,
        repo_path: str,
        exclude_patterns: Optional[List[str]] = None
    ) -> APIResponse:
        """
        Estimate repository size before indexing.
        
        Args:
            repo_path: Path to repository
            exclude_patterns: Patterns to exclude
            
        Returns:
            APIResponse with file/chunk estimates
        """
        data = {
            "repo_path": repo_path,
            "exclude_patterns": exclude_patterns or []
        }
        return self._request('POST', '/index/estimate', data=data)
    
    def update_files(
        self,
        file_paths: List[str],
        repo_base: str,
        workspace: str,
        project: str,
        branch: str,
        commit: str = "update-commit"
    ) -> APIResponse:
        """Update specific files in index."""
        data = {
            "file_paths": file_paths,
            "repo_base": repo_base,
            "workspace": workspace,
            "project": project,
            "branch": branch,
            "commit": commit
        }
        return self._request('POST', '/index/update-files', data=data)
    
    def delete_files(
        self,
        file_paths: List[str],
        workspace: str,
        project: str,
        branch: str
    ) -> APIResponse:
        """Delete specific files from index."""
        data = {
            "file_paths": file_paths,
            "workspace": workspace,
            "project": project,
            "branch": branch
        }
        return self._request('POST', '/index/delete-files', data=data)
    
    def delete_index(self, workspace: str, project: str, branch: str) -> APIResponse:
        """Delete entire index for a branch."""
        return self._request('DELETE', f'/index/{workspace}/{project}/{branch}')
    
    def list_indices(self) -> APIResponse:
        """List all indices."""
        return self._request('GET', '/index/list')
    
    def get_index_stats(self, workspace: str, project: str, branch: str) -> APIResponse:
        """Get statistics for a specific index."""
        return self._request('GET', f'/index/stats/{workspace}/{project}/{branch}')
    
    # ==================== Branch Management ====================
    
    def list_branches(self, workspace: str, project: str) -> APIResponse:
        """List all indexed branches for a project."""
        return self._request('GET', f'/branch/list/{workspace}/{project}')
    
    def delete_branch(self, workspace: str, project: str, branch: str) -> APIResponse:
        """Delete all index data for a branch."""
        data = {
            "workspace": workspace,
            "project": project,
            "branch": branch
        }
        return self._request('POST', '/branch/delete', data=data)
    
    def get_branch_stats(self, workspace: str, project: str, branch: str) -> APIResponse:
        """Get statistics for a specific branch."""
        return self._request('GET', f'/branch/stats/{workspace}/{project}/{branch}')
    
    # ==================== Search & Retrieval ====================
    
    def semantic_search(
        self,
        query: str,
        workspace: str,
        project: str,
        branch: str,
        top_k: int = 10,
        filter_language: Optional[str] = None
    ) -> APIResponse:
        """
        Perform semantic search.
        
        Args:
            query: Search query text
            workspace: Workspace identifier
            project: Project identifier
            branch: Branch to search
            top_k: Maximum results to return
            filter_language: Filter by programming language
            
        Returns:
            APIResponse with search results
        """
        data = {
            "query": query,
            "workspace": workspace,
            "project": project,
            "branch": branch,
            "top_k": top_k,
            "filter_language": filter_language
        }
        return self._request('POST', '/query/search', data=data)
    
    def get_pr_context(
        self,
        workspace: str,
        project: str,
        branch: str,
        changed_files: List[str],
        diff_snippets: Optional[List[str]] = None,
        pr_title: Optional[str] = None,
        pr_description: Optional[str] = None,
        top_k: int = 15,
        enable_priority_reranking: bool = True,
        min_relevance_score: float = 0.7,
        base_branch: Optional[str] = None,
        deleted_files: Optional[List[str]] = None
    ) -> APIResponse:
        """
        Get context for PR review.
        
        Args:
            workspace: Workspace identifier
            project: Project identifier
            branch: Target branch (PR source)
            changed_files: List of changed file paths
            diff_snippets: Code snippets from diff
            pr_title: PR title for query context
            pr_description: PR description for query context
            top_k: Maximum results
            enable_priority_reranking: Enable file priority boosting
            min_relevance_score: Minimum score threshold
            base_branch: Base branch (PR target)
            deleted_files: Files deleted in PR
            
        Returns:
            APIResponse with PR context
        """
        data = {
            "workspace": workspace,
            "project": project,
            "branch": branch,
            "changed_files": changed_files,
            "diff_snippets": diff_snippets or [],
            "pr_title": pr_title,
            "pr_description": pr_description,
            "top_k": top_k,
            "enable_priority_reranking": enable_priority_reranking,
            "min_relevance_score": min_relevance_score,
            "base_branch": base_branch,
            "deleted_files": deleted_files or []
        }
        return self._request('POST', '/query/pr-context', data=data)
    
    def get_deterministic_context(
        self,
        workspace: str,
        project: str,
        branches: List[str],
        file_paths: List[str],
        limit_per_file: int = 10
    ) -> APIResponse:
        """
        Get deterministic context using metadata-based retrieval.
        
        Args:
            workspace: Workspace identifier
            project: Project identifier
            branches: Branches to search
            file_paths: Changed file paths
            limit_per_file: Max chunks per file
            
        Returns:
            APIResponse with deterministic context
        """
        data = {
            "workspace": workspace,
            "project": project,
            "branches": branches,
            "file_paths": file_paths,
            "limit_per_file": limit_per_file
        }
        return self._request('POST', '/query/deterministic', data=data)
    
    # ==================== System ====================
    
    def force_gc(self) -> APIResponse:
        """Force garbage collection."""
        return self._request('POST', '/system/gc')
    
    def get_memory_usage(self) -> APIResponse:
        """Get current memory usage."""
        return self._request('GET', '/system/memory')


# Singleton instance
_client: Optional[RAGAPIClient] = None


def get_client() -> RAGAPIClient:
    """Get or create singleton API client instance."""
    global _client
    if _client is None:
        _client = RAGAPIClient()
    return _client
