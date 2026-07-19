"""
RAG Pipeline client for retrieving contextual information during code review.
"""
import asyncio
import os
import logging
from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
from typing import Dict, List, Optional, Any
import httpx

logger = logging.getLogger(__name__)

# RAG configuration from environment variables
RAG_MIN_RELEVANCE_SCORE = float(os.environ.get("RAG_MIN_RELEVANCE_SCORE", "0.7"))
RAG_DEFAULT_TOP_K = int(os.environ.get("RAG_DEFAULT_TOP_K", "15"))


def _snapshot_identity(snapshot: Dict[str, Any]) -> str:
    return sha256(
        json.dumps(
            snapshot,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _exact_chunk_rejection_reason(
    chunk: Any,
    *,
    branches: List[str],
    snapshot: Dict[str, Any],
    execution_id: Optional[str],
) -> Optional[str]:
    if not isinstance(chunk, dict):
        return "malformed_chunk"
    metadata = chunk.get("metadata")
    if not isinstance(metadata, dict):
        return "metadata_missing"
    text = chunk.get("text", chunk.get("content", ""))
    if not isinstance(text, str) or not text:
        return "content_missing"

    branch = metadata.get("branch")
    revision = metadata.get("snapshot_sha")
    source_branch = branches[0] if branches else None
    base_branches = set(branches[1:])
    if metadata.get("pr") is True:
        if branch != source_branch or revision != snapshot["head_sha"]:
            return "pr_overlay_coordinate_mismatch"
        if not execution_id or metadata.get("execution_id") != execution_id:
            return "pr_overlay_execution_mismatch"
    elif branch == source_branch:
        if revision != snapshot["head_sha"]:
            return "source_revision_mismatch"
    elif branch in base_branches:
        if revision != snapshot["base_sha"]:
            return "base_revision_mismatch"
    else:
        return "unknown_branch_coordinate"

    observed_snapshot_id = metadata.get("context_snapshot_id")
    if (
        observed_snapshot_id is not None
        and observed_snapshot_id != _snapshot_identity(snapshot)
    ):
        return "snapshot_receipt_mismatch"
    for key in ("parser_version", "chunker_version", "embedding_version"):
        if metadata.get(key) != snapshot.get(key):
            return f"{key}_mismatch"
    content_digest = metadata.get("content_digest")
    if (
        not isinstance(content_digest, str)
        or sha256(text.encode("utf-8")).hexdigest() != content_digest
    ):
        return "content_digest_mismatch"
    return None


def _filter_exact_deterministic_response(
    result: Dict[str, Any],
    *,
    branches: List[str],
    snapshot: Dict[str, Any],
    execution_id: Optional[str],
) -> Dict[str, Any]:
    """Reject unproven deterministic chunks before any inference consumer sees them."""
    context = result.get("context")
    if not isinstance(context, dict):
        return {"context": {"chunks": [], "changed_files": {}, "related_definitions": {}}}

    rejected = Counter()

    def filter_chunks(values: Any) -> List[Dict[str, Any]]:
        accepted = []
        if not isinstance(values, list):
            return accepted
        for chunk in values:
            reason = _exact_chunk_rejection_reason(
                chunk,
                branches=branches,
                snapshot=snapshot,
                execution_id=execution_id,
            )
            if reason:
                rejected[reason] += 1
            else:
                accepted.append(chunk)
        return accepted

    filtered = dict(context)
    filtered["chunks"] = filter_chunks(context.get("chunks"))
    for group_name in (
        "changed_files",
        "related_definitions",
        "class_context",
        "namespace_context",
    ):
        raw_group = context.get(group_name)
        group = {}
        if isinstance(raw_group, dict):
            for key, values in raw_group.items():
                accepted = filter_chunks(values)
                if accepted:
                    group[key] = accepted
        filtered[group_name] = group
    metadata = dict(context.get("_metadata") or {})
    metadata["receipt_rejected_count"] = sum(rejected.values())
    metadata["receipt_rejection_reasons"] = dict(sorted(rejected.items()))
    filtered["_metadata"] = metadata
    return {**result, "context": filtered}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid float for %s=%r; using %s", name, value, default)
        return default


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
        self.enabled = enabled if enabled is not None else os.environ.get("RAG_ENABLED", "true").lower() == "true"
        self.timeout = 30.0
        self._client: Optional[httpx.AsyncClient] = None
        self._service_secret = (
            os.environ.get("SERVICE_SECRET")
            or os.environ.get("CODECROW_RAG_API_SECRET", "")
        )

        if self.enabled:
            # RAG_API_URL may contain basic-auth credentials or query tokens.
            logger.info("RAG client initialized")
        else:
            logger.info("RAG client disabled")
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create an HTTP client for connection pooling (instance-level)."""
        if self._client is None or self._client.is_closed:
            headers = {}
            if self._service_secret:
                headers["x-service-secret"] = self._service_secret
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                headers=headers,
            )
        return self._client
    
    async def close(self):
        """Close this instance's HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

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
        all_pr_changed_files: Optional[List[str]] = None,
        snapshot: Optional[Dict[str, Any]] = None,
        execution_id: Optional[str] = None,
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
            enable_priority_reranking: Provider-side ordering hint
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
            if snapshot:
                payload["snapshot"] = snapshot
            if execution_id:
                payload["execution_id"] = execution_id

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
            logger.warning(
                "Failed to retrieve PR context from RAG: error_type=%s",
                type(e).__name__,
            )
            return {"context": {"relevant_code": []}}
        except Exception as e:
            logger.error("Unexpected RAG query error: error_type=%s", type(e).__name__)
            return {"context": {"relevant_code": []}}

    async def semantic_search(
        self,
        query: str,
        workspace: str,
        project: str,
        branch: str,
        top_k: int = 5,
        filter_language: Optional[str] = None,
        revision: Optional[str] = None,
        snapshot: Optional[Dict[str, Any]] = None,
        execution_id: Optional[str] = None,
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
            if revision:
                payload["revision"] = revision
            if snapshot:
                payload["snapshot"] = snapshot
            if execution_id:
                payload["execution_id"] = execution_id

            client = await self._get_client()
            response = await client.post(
                f"{self.base_url}/query/search",
                json=payload
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPError as e:
            logger.warning("Semantic search failed: error_type=%s", type(e).__name__)
            return {"results": []}
        except Exception as e:
            logger.error(
                "Unexpected semantic search error: error_type=%s",
                type(e).__name__,
            )
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
            logger.warning("RAG health check failed: error_type=%s", type(e).__name__)
            return False

    async def search_for_duplicates(
        self,
        workspace: str,
        project: str,
        branch: str,
        queries: List[str],
        top_k: int = 8,
        base_branch: Optional[str] = None,
        snapshot: Optional[Dict[str, Any]] = None,
        execution_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Perform duplication-oriented semantic search to find existing implementations
        of the same functionality elsewhere in the codebase.
        
        Uses specialized embedding instructions optimized for finding similar
        implementations rather than just related code.

        Args:
            workspace: Workspace identifier
            project: Project identifier
            branch: Target branch
            queries: List of duplication-oriented search queries
            top_k: Number of results per query
            base_branch: Optional base branch to also search

        Returns:
            List of result dicts with text, score, metadata, and _source="duplication"
        """
        if not self.enabled or not queries:
            return []

        max_queries = max(1, _env_int("REVIEW_DUPLICATION_RAG_MAX_QUERIES", 8))
        query_timeout = max(
            0.1,
            _env_float("REVIEW_DUPLICATION_RAG_QUERY_TIMEOUT_SECONDS", 5.0),
        )
        query_concurrency = max(
            1,
            _env_int("REVIEW_DUPLICATION_RAG_QUERY_CONCURRENCY", min(max_queries, 8)),
        )
        query_concurrency = min(query_concurrency, max_queries)
        selected_queries = [
            q for q in queries[:max_queries]
            if q and len(q.strip()) >= 10
        ]
        if not selected_queries:
            return []

        try:
            client = await self._get_client()
            semaphore = asyncio.Semaphore(query_concurrency)

            coordinates = [(branch, None)]
            if snapshot:
                if not snapshot.get("head_sha") or (
                    base_branch and not snapshot.get("base_sha")
                ):
                    logger.error("Exact duplication search received incomplete snapshot")
                    return []
                coordinates = [(branch, snapshot.get("head_sha"))]
                if base_branch:
                    coordinates.append((base_branch, snapshot.get("base_sha")))

            async def _run_query(
                query_text: str,
                query_branch: str,
                revision: Optional[str],
            ) -> List[Dict[str, Any]]:
                payload = {
                    "query": query_text,
                    "workspace": workspace,
                    "project": project,
                    "branch": query_branch,
                    "top_k": top_k
                }
                if revision:
                    payload["revision"] = revision
                if snapshot:
                    payload["snapshot"] = snapshot
                if execution_id:
                    payload["execution_id"] = execution_id

                async with semaphore:
                    started_at = datetime.now()
                    try:
                        response = await asyncio.wait_for(
                            client.post(
                                f"{self.base_url}/query/search",
                                json=payload,
                            ),
                            timeout=query_timeout,
                        )
                        response.raise_for_status()
                        result = response.json()
                    except asyncio.TimeoutError:
                        elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000
                        logger.debug(
                            "Duplication search query timed out after %.0fms",
                            elapsed_ms,
                        )
                        return []
                    except Exception as e:
                        logger.debug(
                            "Duplication search query failed: error_type=%s",
                            type(e).__name__,
                        )
                        return []

                query_results = []
                for r in result.get("results", []):
                    r["_source"] = "duplication"
                    r["_query"] = query_text[:80]
                    query_results.append(r)
                return query_results

            result_groups = await asyncio.gather(
                *(
                    _run_query(query_text, query_branch, revision)
                    for query_text in selected_queries
                    for query_branch, revision in coordinates
                ),
                return_exceptions=True,
            )
            all_results: List[Dict[str, Any]] = []
            for group in result_groups:
                if isinstance(group, Exception):
                    logger.debug(f"Duplication search query failed: {group}")
                    continue
                all_results.extend(group)

            logger.info(
                "Duplication search: %d total results from %d/%d queries "
                "(timeout=%.1fs, concurrency=%d)",
                len(all_results),
                len(selected_queries),
                len(queries),
                query_timeout,
                query_concurrency,
            )
            return all_results

        except Exception as e:
            logger.warning("Duplication search failed: error_type=%s", type(e).__name__)
            return []

    async def get_deterministic_context(
        self,
        workspace: str,
        project: str,
        branches: List[str],
        file_paths: List[str],
        limit_per_file: int = 10,
        pr_number: Optional[int] = None,
        pr_changed_files: Optional[List[str]] = None,
        additional_identifiers: Optional[List[str]] = None,
        snapshot: Optional[Dict[str, Any]] = None,
        execution_id: Optional[str] = None,
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
            pr_number: If set, also search PR-indexed chunks and prefer them over branch data
            pr_changed_files: All files changed in the PR (for stale data replacement)

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
            
            # Enable hybrid PR mode for deterministic lookup
            if pr_number:
                payload["pr_number"] = pr_number
            if pr_changed_files:
                payload["pr_changed_files"] = pr_changed_files
            if additional_identifiers:
                payload["additional_identifiers"] = additional_identifiers
            if snapshot:
                payload["snapshot"] = snapshot
            if execution_id:
                payload["execution_id"] = execution_id

            client = await self._get_client()
            response = await client.post(
                f"{self.base_url}/query/deterministic",
                json=payload
            )
            response.raise_for_status()
            result = response.json()
            if snapshot:
                result = _filter_exact_deterministic_response(
                    result,
                    branches=branches,
                    snapshot=snapshot,
                    execution_id=execution_id,
                )
            
            # Log timing and stats
            elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
            context = result.get("context", {})
            chunk_count = len(context.get("chunks", []))
            logger.info(f"Deterministic RAG query completed in {elapsed_ms:.2f}ms, "
                       f"retrieved {chunk_count} chunks for {len(file_paths)} files")
            
            return result

        except httpx.HTTPError as e:
            logger.warning(
                "Failed to retrieve deterministic context: error_type=%s",
                type(e).__name__,
            )
            return {"context": {"chunks": [], "changed_files": {}, "related_definitions": {}}}
        except Exception as e:
            logger.error(
                "Unexpected deterministic RAG query error: error_type=%s",
                type(e).__name__,
            )
            return {"context": {"chunks": [], "changed_files": {}, "related_definitions": {}}}

    # =========================================================================
    # PR File Indexing Methods (for PR-specific RAG layer)
    # =========================================================================

    async def index_pr_files(
        self,
        workspace: str,
        project: str,
        pr_number: int,
        branch: str,
        files: List[Dict[str, str]],
        snapshot: Optional[Dict[str, Any]] = None,
        execution_id: Optional[str] = None,
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
            if snapshot:
                payload["snapshot"] = snapshot
            if execution_id:
                payload["execution_id"] = execution_id

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
            logger.warning("Failed to index PR files: error_type=%s", type(e).__name__)
            return {"status": "error", "error": str(e)}
        except Exception as e:
            logger.error(
                "Unexpected PR indexing error: error_type=%s",
                type(e).__name__,
            )
            return {"status": "error", "error": str(e)}

    async def delete_pr_files(
        self,
        workspace: str,
        project: str,
        pr_number: int,
        execution_id: Optional[str] = None,
        head_sha: Optional[str] = None,
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
                f"{self.base_url}/index/pr-files/{workspace}/{project}/{pr_number}",
                params={
                    key: value
                    for key, value in {
                        "execution_id": execution_id,
                        "head_sha": head_sha,
                    }.items()
                    if value
                },
            )
            response.raise_for_status()
            result = response.json()
            
            logger.info(f"Deleted PR #{pr_number} indexed data")
            return result.get("status") == "deleted"

        except httpx.HTTPError as e:
            logger.warning("Failed to delete PR files: error_type=%s", type(e).__name__)
            return False
        except Exception as e:
            logger.error(
                "Unexpected PR cleanup error: error_type=%s",
                type(e).__name__,
            )
            return False
