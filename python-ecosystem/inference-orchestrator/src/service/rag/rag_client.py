"""RAG client facade composing repository queries and proposed-tree queries."""
from __future__ import annotations
import asyncio
import logging
import os
from typing import Dict, List, Optional, Any
import httpx

logger = logging.getLogger(__name__)

from .bindings import structural_query_payload
from .transport import RagTransport
from .review_queries import ReviewQueries

class RagClient:
    def __init__(self, base_url: Optional[str] = None, enabled: Optional[bool] = None):
        self.transport = RagTransport(base_url=base_url, enabled=enabled)
        self.review = ReviewQueries(self.transport)

    @property
    def base_url(self) -> str:
        return self.transport.base_url

    @property
    def enabled(self) -> bool:
        return self.transport.enabled

    async def close(self):
        await self.transport.close()

    async def is_healthy(self) -> bool:
        return await self.transport.is_healthy()

    async def prepare_review_generation(self, **arguments: Any) -> Dict[str, Any]:
        return await self.review.prepare_review_generation(**arguments)

    async def explore_review_context(self, **arguments: Any) -> Dict[str, Any]:
        return await self.review.explore_review_context(**arguments)

    async def minimal_review_context(self, **arguments: Any) -> Dict[str, Any]:
        return await self.review.minimal_review_context(**arguments)

    async def review_impact_radius(self, **arguments: Any) -> Dict[str, Any]:
        return await self.review.review_impact_radius(**arguments)

    async def traverse_review_graph(self, **arguments: Any) -> Dict[str, Any]:
        return await self.review.traverse_review_graph(**arguments)

    async def query_review_graph(self, **arguments: Any) -> Dict[str, Any]:
        return await self.review.query_review_graph(**arguments)

    async def get_review_structural_unit(self, **arguments: Any) -> Dict[str, Any]:
        return await self.review.get_review_structural_unit(**arguments)



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
        payload = structural_query_payload(
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
        return await self.transport._post_structural_query(
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
        payload = structural_query_payload(
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
        return await self.transport._post_structural_query(
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
        payload = structural_query_payload(
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
        return await self.transport._post_structural_query(
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
        payload = structural_query_payload(
            workspace=workspace, project=project, branch=branch,
            repository_revision=repository_revision,
            repository_generation_manifest_sha256=repository_generation_manifest_sha256,
            collection_target=collection_target,
        )
        payload.update({"query": query, "limit": top_k})
        return await self.transport._post_structural_query(
            "/query/code-search", payload, {"results": []},
        )
