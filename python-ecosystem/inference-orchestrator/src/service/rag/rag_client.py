"""Client for structural repository context and code search."""
import asyncio
import logging
import os
from typing import Dict, List, Optional, Any
import httpx

logger = logging.getLogger(__name__)

_REVIEW_CONTEXT_MUTATION_CONFLICT_PREFIX = (
    "another RAG mutation is active for "
)
_REVIEW_CONTEXT_MUTATION_RETRY_SECONDS = 1.0


def _http_error_detail(error: httpx.HTTPError) -> tuple[Optional[int], str]:
    if not isinstance(error, httpx.HTTPStatusError):
        detail = str(error).strip()
        if not detail:
            request = getattr(error, "request", None)
            request_target = (
                f" for {request.method} {request.url}"
                if request is not None
                else ""
            )
            detail = f"{type(error).__name__}{request_target}"
        return None, detail
    response = error.response
    detail = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = str(payload.get("detail") or payload.get("error") or "")
    except (ValueError, TypeError):
        detail = response.text.strip()
    return response.status_code, detail or str(error)


def _is_review_context_mutation_conflict(result: Dict[str, Any]) -> bool:
    """Identify the one transient 409 emitted by mutation coordination."""
    return (
        result.get("status_code") == 409
        and str(result.get("error") or "").startswith(
            _REVIEW_CONTEXT_MUTATION_CONFLICT_PREFIX
        )
    )


class RagClient:
    """Client for interacting with the RAG Pipeline API."""

    def __init__(self, base_url: Optional[str] = None, enabled: Optional[bool] = None):
        """
        Initialize RAG client.

        Args:
            base_url: RAG pipeline API URL (default from env RAG_API_URL)
            enabled: Whether RAG is enabled (default from env RAG_ENABLED)
        """
        self.base_url = base_url or os.environ.get(
            "RAG_API_URL", "http://codecrow-rag-pipeline:8001"
        )
        self.enabled = enabled if enabled is not None else os.environ.get("RAG_ENABLED", "true").lower() == "true"
        self.timeout = 30.0
        self._client: Optional[httpx.AsyncClient] = None
        self._service_secret = (
            os.environ.get("SERVICE_SECRET")
            or os.environ.get("CODECROW_RAG_API_SECRET", "")
        )

        if self.enabled:
            logger.info(f"RAG client initialized: {self.base_url}")
        else:
            logger.info("RAG client disabled")
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get the query/health pool, isolated from long PR mutations."""
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

    @staticmethod
    def _structural_query_payload(
        *,
        workspace: str,
        project: str,
        branch: str,
        repository_revision: Optional[str],
        repository_generation_manifest_sha256: Optional[str],
        collection_target: Optional[str],
    ) -> Dict[str, Any]:
        """Build the server-owned repository-generation binding."""
        payload: Dict[str, Any] = {
            "workspace": workspace,
            "project": project,
            "branch": branch,
        }
        if repository_revision:
            payload["repository_revision"] = repository_revision
        if repository_generation_manifest_sha256:
            payload["repository_generation_manifest_sha256"] = (
                repository_generation_manifest_sha256
            )
        if collection_target:
            payload["collection_target"] = collection_target
        return payload

    @staticmethod
    def _review_query_payload(
        *,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        target_repo_path: str,
        review_overlay_path: str,
        focus_paths: Optional[List[str]] = None,
        base_collection_target: Optional[str] = None,
        base_generation_manifest_sha256: Optional[str] = None,
        review_collection_target: Optional[str] = None,
        review_generation_manifest_sha256: Optional[str] = None,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        project_type: Optional[str] = None,
        source_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build the host-controlled exact proposed-tree binding."""
        payload: Dict[str, Any] = {
            "workspace": workspace,
            "project": project,
            "target_branch": target_branch,
            "base_revision": base_revision,
            "source_revision": source_revision,
            "target_repo_path": target_repo_path,
            "review_overlay_path": review_overlay_path,
        }
        if focus_paths is not None:
            payload["focus_paths"] = focus_paths
        for key, value in (
            ("base_collection_target", base_collection_target),
            (
                "base_generation_manifest_sha256",
                base_generation_manifest_sha256,
            ),
            ("review_collection_target", review_collection_target),
            (
                "review_generation_manifest_sha256",
                review_generation_manifest_sha256,
            ),
            ("include_patterns", include_patterns),
            ("exclude_patterns", exclude_patterns),
            ("project_type", project_type),
            ("source_root", source_root),
        ):
            if value is not None:
                payload[key] = value
        return payload

    @staticmethod
    def _review_query_timeout_seconds() -> float:
        try:
            configured_timeout = os.environ.get(
                "RAG_REVIEW_CONTEXT_TIMEOUT_SECONDS",
                "120",
            )
            return max(30.0, float(configured_timeout))
        except (TypeError, ValueError):
            return 120.0

    @staticmethod
    def _review_preparation_timeout_seconds() -> float:
        try:
            configured_timeout = os.environ.get(
                "RAG_REVIEW_PREPARATION_TIMEOUT_SECONDS",
                "1800",
            )
            return max(60.0, float(configured_timeout))
        except (TypeError, ValueError):
            return 1800.0

    async def _post_structural_query(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        empty_result: Dict[str, Any],
        *,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Run an optional structural query without failing the core review."""
        if not self.enabled:
            return dict(empty_result)

        try:
            client = await self._get_client()
            response = await client.post(
                f"{self.base_url}{endpoint}",
                json=payload,
                timeout=(
                    timeout_seconds
                    if timeout_seconds is not None
                    else self.timeout
                ),
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as error:
            status_code, detail = _http_error_detail(error)
            logger.debug(
                "Structural repository query failed: endpoint=%s status=%s "
                "detail=%s",
                endpoint,
                status_code or "transport-error",
                detail,
            )
            return {
                **empty_result,
                "status": "error",
                "status_code": status_code,
                "error": detail,
            }
        except Exception as error:
            logger.debug(
                "Unexpected structural repository query failure: endpoint=%s "
                "error=%s",
                endpoint,
                error,
                exc_info=True,
            )
            return {
                **empty_result,
                "status": "error",
                "status_code": None,
                "error": str(error),
            }

    async def _post_review_query(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        empty_result: Dict[str, Any],
        *,
        operation: str,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Run one proposed-tree query, waiting out active graph mutations."""
        timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else self._review_query_timeout_seconds()
        )
        last_mutation_conflict: Optional[Dict[str, Any]] = None
        mutation_conflicts = 0
        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    result = await self._post_structural_query(
                        endpoint,
                        payload,
                        empty_result,
                        timeout_seconds=timeout_seconds,
                    )
                    result_status = str(
                        result.get("status") or ""
                    ).strip().casefold()
                    if (
                        result_status
                        in {"error", "failed", "unavailable", "disabled"}
                        or result.get("error")
                        or result.get("unavailable") is True
                    ):
                        degraded_result = {**empty_result, **result}
                        default_coverage = empty_result.get("coverage")
                        observed_coverage = result.get("coverage")
                        if isinstance(default_coverage, dict):
                            merged_coverage = {
                                **default_coverage,
                                **(
                                    observed_coverage
                                    if isinstance(observed_coverage, dict)
                                    else {}
                                ),
                            }
                            for state_key in (
                                "state",
                                "graphState",
                                "treeState",
                            ):
                                if state_key in default_coverage:
                                    merged_coverage[state_key] = (
                                        default_coverage[state_key]
                                    )
                            degraded_result["coverage"] = merged_coverage
                        result = degraded_result
                    if not _is_review_context_mutation_conflict(result):
                        if result.get("status") == "error":
                            logger.warning(
                                "Proposed-tree %s failed: status=%s detail=%s",
                                operation,
                                result.get("status_code")
                                or "transport-error",
                                result.get("error") or "unknown error",
                            )
                        return result
                    last_mutation_conflict = result
                    log_conflict = (
                        logger.warning
                        if mutation_conflicts == 0
                        else logger.debug
                    )
                    mutation_conflicts += 1
                    log_conflict(
                        "Waiting for active RAG mutation before retrying "
                        "proposed-tree %s: workspace=%s project=%s status=%s "
                        "detail=%s",
                        operation,
                        payload.get("workspace"),
                        payload.get("project"),
                        result.get("status_code"),
                        result.get("error"),
                    )
                    await asyncio.sleep(
                        _REVIEW_CONTEXT_MUTATION_RETRY_SECONDS
                    )
        except TimeoutError:
            if last_mutation_conflict is not None:
                logger.debug(
                    "Proposed-tree %s remained blocked by an active RAG "
                    "mutation until its timeout: workspace=%s project=%s",
                    operation,
                    payload.get("workspace"),
                    payload.get("project"),
                )
                return last_mutation_conflict
            return {
                **empty_result,
                "status": "error",
                "status_code": None,
                "error": (
                    f"proposed-tree {operation} timed out after "
                    f"{timeout_seconds:g} seconds"
                ),
            }

    async def prepare_review_generation(
        self,
        *,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        target_repo_path: str,
        review_overlay_path: str,
        base_collection_target: Optional[str] = None,
        base_generation_manifest_sha256: Optional[str] = None,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        project_type: Optional[str] = None,
        source_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Prepare one exact review graph before any Stage 1 batch starts."""

        payload = self._review_query_payload(
            workspace=workspace,
            project=project,
            target_branch=target_branch,
            base_revision=base_revision,
            source_revision=source_revision,
            target_repo_path=target_repo_path,
            review_overlay_path=review_overlay_path,
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=(
                base_generation_manifest_sha256
            ),
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            project_type=project_type,
            source_root=source_root,
        )
        return await self._post_review_query(
            "/query/review-generation",
            payload,
            {
                "status": "unavailable",
                "collection_target": None,
                "generation_manifest_sha256": None,
                "changed_paths": [],
                "deleted_paths": [],
                "cache_hit": False,
            },
            operation="review generation preparation",
            timeout_seconds=self._review_preparation_timeout_seconds(),
        )

    async def explore_review_context(
        self,
        *,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        target_repo_path: str,
        review_overlay_path: str,
        focus_paths: List[str],
        question: str,
        base_collection_target: Optional[str] = None,
        base_generation_manifest_sha256: Optional[str] = None,
        review_collection_target: Optional[str] = None,
        review_generation_manifest_sha256: Optional[str] = None,
        focus_symbols: Optional[List[str]] = None,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        project_type: Optional[str] = None,
        source_root: Optional[str] = None,
        max_relations: int = 32,
        max_source_windows: int = 6,
        max_source_characters: int = 12_000,
    ) -> Dict[str, Any]:
        """Build/query one sealed proposed-tree review generation.

        Repository paths and revision identity come from the request host, not
        from model-controlled tool arguments. The service clones the exact
        sealed base, applies the host-owned changed/deleted overlay, then reads
        that immutable proposed generation. A transient repository mutation-
        coordination conflict is retried within the timeout; other failures
        remain ordinary structured tool observations and do not fail the core
        source review.
        """
        payload = self._review_query_payload(
            workspace=workspace,
            project=project,
            target_branch=target_branch,
            base_revision=base_revision,
            source_revision=source_revision,
            target_repo_path=target_repo_path,
            review_overlay_path=review_overlay_path,
            focus_paths=focus_paths,
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=(
                base_generation_manifest_sha256
            ),
            review_collection_target=review_collection_target,
            review_generation_manifest_sha256=(
                review_generation_manifest_sha256
            ),
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            project_type=project_type,
            source_root=source_root,
        )
        payload.update({
            "question": question,
            "focus_symbols": focus_symbols or [],
            "max_relations": max_relations,
            "max_source_windows": max_source_windows,
            "max_source_characters": max_source_characters,
        })
        empty_result = {
            "status": "unavailable",
            "snapshot": {},
            "changed": {},
            "evidence": {"relations": []},
            "sourceWindows": [],
            "coverage": {
                "treeState": "unavailable",
                "graphState": "unavailable",
                "partialReasons": ["review_context_unavailable"],
            },
            "provenance": {},
            "omittedFollowups": [],
        }
        return await self._post_review_query(
            "/query/review-context",
            payload,
            empty_result,
            operation="review context",
        )

    async def minimal_review_context(
        self,
        *,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        target_repo_path: str,
        review_overlay_path: str,
        focus_paths: List[str],
        question: str,
        base_collection_target: Optional[str] = None,
        base_generation_manifest_sha256: Optional[str] = None,
        review_collection_target: Optional[str] = None,
        review_generation_manifest_sha256: Optional[str] = None,
        focus_symbols: Optional[List[str]] = None,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        project_type: Optional[str] = None,
        source_root: Optional[str] = None,
        max_relations: int = 25,
        detail_level: str = "minimal",
        include_source: bool = True,
        max_source_windows: int = 4,
        max_source_characters: int = 8_000,
    ) -> Dict[str, Any]:
        """Return a compact exact proposed-tree orientation for this review."""
        payload = self._review_query_payload(
            workspace=workspace,
            project=project,
            target_branch=target_branch,
            base_revision=base_revision,
            source_revision=source_revision,
            target_repo_path=target_repo_path,
            review_overlay_path=review_overlay_path,
            focus_paths=focus_paths,
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=(
                base_generation_manifest_sha256
            ),
            review_collection_target=review_collection_target,
            review_generation_manifest_sha256=(
                review_generation_manifest_sha256
            ),
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            project_type=project_type,
            source_root=source_root,
        )
        payload.update({
            "question": question,
            "focus_symbols": focus_symbols or [],
            "max_relations": max_relations,
            "detail_level": detail_level,
            "include_source": include_source,
            "max_source_windows": max_source_windows,
            "max_source_characters": max_source_characters,
        })
        return await self._post_review_query(
            "/query/review-minimal-context",
            payload,
            {
                "status": "unavailable",
                "operation": "minimal_review_context",
                "snapshot": {},
                "question": question,
                "focusPaths": list(focus_paths),
                "focusSymbols": list(focus_symbols or []),
                "summary": "",
                "nodes": [],
                "edges": [],
                "sourceWindows": [],
                "coverage": {
                    "state": "unavailable",
                    "truncated": True,
                    "returnedNodes": 0,
                    "returnedRelations": 0,
                    "sourceIncluded": include_source,
                },
                "nextOperations": [],
            },
            operation="minimal review context",
        )

    async def review_impact_radius(
        self,
        *,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        target_repo_path: str,
        review_overlay_path: str,
        focus_paths: List[str],
        base_collection_target: Optional[str] = None,
        base_generation_manifest_sha256: Optional[str] = None,
        review_collection_target: Optional[str] = None,
        review_generation_manifest_sha256: Optional[str] = None,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        project_type: Optional[str] = None,
        source_root: Optional[str] = None,
        targets: Optional[List[str]] = None,
        max_depth: int = 2,
        max_results: int = 100,
        detail_level: str = "standard",
        include_source: bool = True,
        max_source_windows: int = 6,
        max_source_characters: int = 12_000,
    ) -> Dict[str, Any]:
        """Return the bounded impact radius in the exact proposed tree."""
        payload = self._review_query_payload(
            workspace=workspace,
            project=project,
            target_branch=target_branch,
            base_revision=base_revision,
            source_revision=source_revision,
            target_repo_path=target_repo_path,
            review_overlay_path=review_overlay_path,
            focus_paths=focus_paths,
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=(
                base_generation_manifest_sha256
            ),
            review_collection_target=review_collection_target,
            review_generation_manifest_sha256=(
                review_generation_manifest_sha256
            ),
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            project_type=project_type,
            source_root=source_root,
        )
        payload.update({
            "targets": targets or [],
            "max_depth": max_depth,
            "max_results": max_results,
            "detail_level": detail_level,
            "include_source": include_source,
            "max_source_windows": max_source_windows,
            "max_source_characters": max_source_characters,
        })
        return await self._post_review_query(
            "/query/review-impact-radius",
            payload,
            {
                "status": "unavailable",
                "operation": "review_impact_radius",
                "snapshot": {},
                "targets": list(targets or focus_paths),
                "unresolvedTargets": [],
                "roots": [],
                "nodes": [],
                "edges": [],
                "connections": [],
                "impactScores": {},
                "impactedFiles": [],
                "frontier": [],
                "sourceWindows": [],
                "coverage": {
                    "state": "unavailable",
                    "truncated": True,
                    "partialReasons": ["review_context_unavailable"],
                    "maxDepth": max_depth,
                    "depthReached": 0,
                    "maxResults": max_results,
                    "returnedNodes": 0,
                    "returnedImpacted": 0,
                    "totalDiscovered": 0,
                    "returnedRelations": 0,
                    "sourceIncluded": include_source,
                },
                "scorePolicy": {},
            },
            operation="review impact radius",
        )

    async def traverse_review_graph(
        self,
        *,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        target_repo_path: str,
        review_overlay_path: str,
        focus_paths: List[str],
        start: str,
        base_collection_target: Optional[str] = None,
        base_generation_manifest_sha256: Optional[str] = None,
        review_collection_target: Optional[str] = None,
        review_generation_manifest_sha256: Optional[str] = None,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        project_type: Optional[str] = None,
        source_root: Optional[str] = None,
        direction: str = "both",
        strategy: str = "bfs",
        relation_kinds: Optional[List[str]] = None,
        max_depth: int = 3,
        max_results: int = 100,
        token_budget: int = 2_000,
        detail_level: str = "standard",
        include_source: bool = True,
        max_source_windows: int = 6,
        max_source_characters: int = 12_000,
    ) -> Dict[str, Any]:
        """Traverse the exact proposed graph from one bounded start target."""
        payload = self._review_query_payload(
            workspace=workspace,
            project=project,
            target_branch=target_branch,
            base_revision=base_revision,
            source_revision=source_revision,
            target_repo_path=target_repo_path,
            review_overlay_path=review_overlay_path,
            focus_paths=focus_paths,
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=(
                base_generation_manifest_sha256
            ),
            review_collection_target=review_collection_target,
            review_generation_manifest_sha256=(
                review_generation_manifest_sha256
            ),
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            project_type=project_type,
            source_root=source_root,
        )
        payload.update({
            "start": start,
            "direction": direction,
            "strategy": strategy,
            "relation_kinds": relation_kinds or [],
            "max_depth": max_depth,
            "max_results": max_results,
            "token_budget": token_budget,
            "detail_level": detail_level,
            "include_source": include_source,
            "max_source_windows": max_source_windows,
            "max_source_characters": max_source_characters,
        })
        return await self._post_review_query(
            "/query/review-traverse",
            payload,
            {
                "status": "unavailable",
                "operation": "traverse_review_graph",
                "snapshot": {},
                "targets": [start],
                "unresolvedTargets": [],
                "roots": [],
                "nodes": [],
                "edges": [],
                "frontier": [],
                "sourceWindows": [],
                "coverage": {
                    "state": "unavailable",
                    "truncated": True,
                    "partialReasons": ["review_context_unavailable"],
                    "maxDepth": max_depth,
                    "depthReached": 0,
                    "maxResults": max_results,
                    "tokenBudget": token_budget,
                    "returnedNodes": 0,
                    "returnedRelations": 0,
                    "sourceIncluded": include_source,
                },
                "strategy": strategy,
                "direction": direction,
                "relationKinds": list(relation_kinds or []),
            },
            operation="review graph traversal",
        )

    async def query_review_graph(
        self,
        *,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        target_repo_path: str,
        review_overlay_path: str,
        focus_paths: List[str],
        pattern: str,
        target: str,
        base_collection_target: Optional[str] = None,
        base_generation_manifest_sha256: Optional[str] = None,
        review_collection_target: Optional[str] = None,
        review_generation_manifest_sha256: Optional[str] = None,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        project_type: Optional[str] = None,
        source_root: Optional[str] = None,
        max_results: int = 25,
        cursor: int = 0,
        detail_level: str = "standard",
        include_source: bool = True,
        max_source_windows: int = 6,
        max_source_characters: int = 12_000,
    ) -> Dict[str, Any]:
        """Run one named query against the exact proposed review graph."""
        payload = self._review_query_payload(
            workspace=workspace,
            project=project,
            target_branch=target_branch,
            base_revision=base_revision,
            source_revision=source_revision,
            target_repo_path=target_repo_path,
            review_overlay_path=review_overlay_path,
            focus_paths=focus_paths,
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=(
                base_generation_manifest_sha256
            ),
            review_collection_target=review_collection_target,
            review_generation_manifest_sha256=(
                review_generation_manifest_sha256
            ),
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            project_type=project_type,
            source_root=source_root,
        )
        payload.update({
            "pattern": pattern,
            "target": target,
            "max_results": max_results,
            "cursor": cursor,
            "detail_level": detail_level,
            "include_source": include_source,
            "max_source_windows": max_source_windows,
            "max_source_characters": max_source_characters,
        })
        return await self._post_review_query(
            "/query/review-graph",
            payload,
            {
                "status": "unavailable",
                "operation": "query_review_graph",
                "snapshot": {},
                "pattern": pattern,
                "target": target,
                "cursor": cursor,
                "nextCursor": None,
                "results": [],
                "sourceWindows": [],
                "coverage": {
                    "state": "unavailable",
                    "truncated": True,
                    "maxResults": max_results,
                    "returnedResults": 0,
                    "sourceIncluded": include_source,
                },
            },
            operation="review graph query",
        )

    async def get_review_structural_unit(
        self,
        *,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        target_repo_path: str,
        review_overlay_path: str,
        focus_paths: List[str],
        unit_id: str,
        offset: int = 0,
        max_characters: int = 12_000,
        base_collection_target: Optional[str] = None,
        base_generation_manifest_sha256: Optional[str] = None,
        review_collection_target: Optional[str] = None,
        review_generation_manifest_sha256: Optional[str] = None,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        project_type: Optional[str] = None,
        source_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read one exact source-backed unit from the proposed review tree."""
        payload = self._review_query_payload(
            workspace=workspace,
            project=project,
            target_branch=target_branch,
            base_revision=base_revision,
            source_revision=source_revision,
            target_repo_path=target_repo_path,
            review_overlay_path=review_overlay_path,
            focus_paths=focus_paths,
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=(
                base_generation_manifest_sha256
            ),
            review_collection_target=review_collection_target,
            review_generation_manifest_sha256=(
                review_generation_manifest_sha256
            ),
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            project_type=project_type,
            source_root=source_root,
        )
        payload.update({
            "unit_id": unit_id,
            "offset": offset,
            "max_characters": max_characters,
        })
        return await self._post_review_query(
            "/query/review-unit",
            payload,
            {
                "status": "unavailable",
                "operation": "get_review_structural_unit",
                "snapshot": {},
                "unit": None,
                "sourceEvidence": False,
            },
            operation="review structural unit",
        )

    async def get_review_file_content(
        self,
        *,
        path: str,
        side: str = "proposed",
        start_line: int = 1,
        end_line: Optional[int] = None,
        **binding: Any,
    ) -> Dict[str, Any]:
        """Read line-numbered source from one sealed proposed-tree generation."""
        payload = self._review_query_payload(**binding)
        payload.update({
            "path": path,
            "side": side,
            "start_line": start_line,
            "end_line": end_line,
        })
        return await self._post_review_query(
            "/query/review-file", payload,
            {"status": "unavailable", "path": path, "side": side},
            operation="review file content",
        )

    async def search_review_code(
        self,
        *,
        query: str,
        cursor: int = 0,
        **binding: Any,
    ) -> Dict[str, Any]:
        """Search literal source in the sealed proposed tree."""
        payload = self._review_query_payload(**binding)
        payload.update({"query": query, "cursor": cursor})
        return await self._post_review_query(
            "/query/review-search", payload,
            {"status": "unavailable", "query": query, "results": []},
            operation="review source search",
        )

    async def get_structural_relations(
        self,
        paths: List[str],
        workspace: str,
        project: str,
        branch: str,
        max_relations: int = 80,
        repository_revision: Optional[str] = None,
        repository_generation_manifest_sha256: Optional[str] = None,
        collection_target: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return compact AST/plugin relation metadata for exact file paths."""
        payload = self._structural_query_payload(
            workspace=workspace,
            project=project,
            branch=branch,
            repository_revision=repository_revision,
            repository_generation_manifest_sha256=(
                repository_generation_manifest_sha256
            ),
            collection_target=collection_target,
        )
        payload.update({
            "paths": paths,
            "max_relations": max_relations,
        })
        return await self._post_structural_query(
            "/query/relations",
            payload,
            {"anchors": [], "relations": []},
        )

    async def query_code_graph(
        self,
        pattern: str,
        target: str,
        workspace: str,
        project: str,
        branch: str,
        max_results: int = 25,
        repository_revision: Optional[str] = None,
        repository_generation_manifest_sha256: Optional[str] = None,
        collection_target: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Traverse a named graph relation around a symbol, unit, or path."""
        payload = self._structural_query_payload(
            workspace=workspace,
            project=project,
            branch=branch,
            repository_revision=repository_revision,
            repository_generation_manifest_sha256=(
                repository_generation_manifest_sha256
            ),
            collection_target=collection_target,
        )
        payload.update({
            "pattern": pattern,
            "target": target,
            "max_results": max_results,
        })
        return await self._post_structural_query(
            "/query/graph",
            payload,
            {"results": []},
        )

    async def get_structural_unit(
        self,
        unit_id: str,
        workspace: str,
        project: str,
        branch: str,
        repository_revision: Optional[str] = None,
        repository_generation_manifest_sha256: Optional[str] = None,
        collection_target: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read one exact source-backed AST/plugin unit from the bound graph."""
        payload = self._structural_query_payload(
            workspace=workspace,
            project=project,
            branch=branch,
            repository_revision=repository_revision,
            repository_generation_manifest_sha256=(
                repository_generation_manifest_sha256
            ),
            collection_target=collection_target,
        )
        payload["unit_id"] = unit_id
        return await self._post_structural_query(
            "/query/unit",
            payload,
            {"unit": None},
        )

    async def search_code(
        self,
        query: str,
        workspace: str,
        project: str,
        branch: str,
        top_k: int = 8,
        repository_revision: Optional[str] = None,
        repository_generation_manifest_sha256: Optional[str] = None,
        collection_target: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Find code with deterministic lexical, symbol, and metadata matching.

        Search is optional for Ask. Transport, index, or binding failures are
        returned as an empty result set so the existing exact VCS MCP path can
        still answer questions when the repository search service is degraded.
        ``match_reasons`` explains the concrete fields that matched. Ordering
        weights remain an implementation detail and are never presented as
        relevance or confidence.
        """
        if not self.enabled:
            return {"results": []}

        try:
            payload = {
                "query": query,
                "workspace": workspace,
                "project": project,
                "branch": branch,
                "limit": top_k,
            }
            if repository_revision:
                payload["repository_revision"] = repository_revision
            if repository_generation_manifest_sha256:
                payload["repository_generation_manifest_sha256"] = (
                    repository_generation_manifest_sha256
                )
            if collection_target:
                payload["collection_target"] = collection_target

            client = await self._get_client()
            response = await client.post(
                f"{self.base_url}/query/code-search",
                json=payload
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPError as e:
            status_code, detail = _http_error_detail(e)
            logger.debug(
                "Code search failed: status=%s detail=%s",
                status_code or "transport-error",
                detail,
            )
            return {
                "status": "error",
                "status_code": status_code,
                "error": detail,
                "results": [],
            }
        except Exception as e:
            logger.debug("Unexpected error in code search: %s", e, exc_info=True)
            return {
                "status": "error",
                "status_code": None,
                "error": str(e),
                "results": [],
            }

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
