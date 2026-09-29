"""Proposed-tree query payloads and operation-specific degraded results."""
from __future__ import annotations
from typing import Dict, List, Optional, Any


from .bindings import review_query_payload
from .transport import RagTransport

class ReviewQueries:
    def __init__(self, transport: RagTransport):
        self.transport = transport

    async def prepare_review_generation(self, **binding: Any) -> Dict[str, Any]:
        """Prepare one exact review graph before any Stage 1 batch starts."""

        policy = binding.pop("index_policy", None)
        candidates = binding.pop("base_generation_candidates", None)
        payload = review_query_payload(**binding)
        if policy is not None:
            payload["index_policy"] = policy
        if candidates:
            payload["base_generation_candidates"] = candidates
        return await self.transport._post_review_query(
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
            timeout_seconds=self.transport._review_preparation_timeout_seconds(),
        )


    async def explore_review_context(self, *, question: str, focus_symbols: Optional[List[str]] = None, max_relations: int = 32, max_source_windows: int = 6, max_source_characters: int = 12000, **binding: Any) -> Dict[str, Any]:
        """Query one sealed proposed-tree review generation.

        Repository paths and revision identity come from the request host, not
        from model-controlled tool arguments. Preparation seals the host-owned proposed tree before these reads. A transient repository mutation-
        coordination conflict is retried within the timeout; other failures
        remain ordinary structured tool observations and do not fail the core
        source review.
        """
        payload = review_query_payload(**binding)
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
        return await self.transport._post_review_query(
            "/query/review-context",
            payload,
            empty_result,
            operation="review context",
        )


    async def minimal_review_context(self, *, question: str, focus_symbols: Optional[List[str]] = None, max_relations: int = 25, detail_level: str = 'minimal', include_source: bool = True, max_source_windows: int = 4, max_source_characters: int = 8000, **binding: Any) -> Dict[str, Any]:
        """Return a compact exact proposed-tree orientation for this review."""
        payload = review_query_payload(**binding)
        payload.update({
            "question": question,
            "focus_symbols": focus_symbols or [],
            "max_relations": max_relations,
            "detail_level": detail_level,
            "include_source": include_source,
            "max_source_windows": max_source_windows,
            "max_source_characters": max_source_characters,
        })
        return await self.transport._post_review_query(
            "/query/review-minimal-context",
            payload,
            {
                "status": "unavailable",
                "operation": "minimal_review_context",
                "snapshot": {},
                "question": question,
                "focusPaths": list(payload.get("focus_paths") or []),
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


    async def review_impact_radius(self, *, targets: Optional[List[str]] = None, max_depth: int = 2, max_results: int = 100, detail_level: str = 'standard', include_source: bool = True, max_source_windows: int = 6, max_source_characters: int = 12000, **binding: Any) -> Dict[str, Any]:
        """Return the bounded impact radius in the exact proposed tree."""
        payload = review_query_payload(**binding)
        payload.update({
            "targets": targets or [],
            "max_depth": max_depth,
            "max_results": max_results,
            "detail_level": detail_level,
            "include_source": include_source,
            "max_source_windows": max_source_windows,
            "max_source_characters": max_source_characters,
        })
        return await self.transport._post_review_query(
            "/query/review-impact-radius",
            payload,
            {
                "status": "unavailable",
                "operation": "review_impact_radius",
                "snapshot": {},
                "targets": list(targets or payload.get("focus_paths") or []),
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


    async def traverse_review_graph(self, *, start: str, direction: str = 'both', strategy: str = 'bfs', relation_kinds: Optional[List[str]] = None, max_depth: int = 3, max_results: int = 100, token_budget: int | None = None, detail_level: str = 'standard', include_source: bool = True, max_source_windows: int = 6, max_source_characters: int = 12000, **binding: Any) -> Dict[str, Any]:
        """Traverse the exact proposed graph from one bounded start target."""
        payload = review_query_payload(**binding)
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
        return await self.transport._post_review_query(
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


    async def query_review_graph(self, *, pattern: str, target: str, max_results: int = 25, cursor: int = 0, detail_level: str = 'standard', include_source: bool = True, max_source_windows: int = 6, max_source_characters: int = 12000, **binding: Any) -> Dict[str, Any]:
        """Run one named query against the exact proposed review graph."""
        payload = review_query_payload(**binding)
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
        return await self.transport._post_review_query(
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


    async def get_review_structural_unit(self, *, unit_id: str, offset: int = 0, max_characters: int = 12000, **binding: Any) -> Dict[str, Any]:
        """Read one exact source-backed unit from the proposed review tree."""
        payload = review_query_payload(**binding)
        payload.update({
            "unit_id": unit_id,
            "offset": offset,
            "max_characters": max_characters,
        })
        return await self.transport._post_review_query(
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
