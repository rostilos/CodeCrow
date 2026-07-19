"""
Semantic search module for RAG query service.

Handles single-branch and multi-branch semantic search using LlamaIndex
retrievers with Qdrant metadata filtering.
"""
from typing import Dict, List, Optional
import logging

from llama_index.core.vector_stores import MetadataFilters, MetadataFilter, FilterOperator
from qdrant_client.http.models import FieldCondition, MatchValue, MatchAny

from .base import RAGQueryBase
from ..models.instructions import InstructionType, format_query

logger = logging.getLogger(__name__)


class SemanticSearchMixin:
    """Semantic search capabilities for RAGQueryService.

    Provides single-branch and multi-branch semantic search with:
    - LlamaIndex retriever integration
    - Branch-aware metadata filtering
    - Language filtering
    - Path exclusion (for deleted/PR files)
    - Branch priority deduplication
    """

    def semantic_search(
            self: RAGQueryBase,
            query: str,
            workspace: str,
            project: str,
            branch: str,
            top_k: int = 10,
            filter_language: Optional[str] = None,
            instruction_type: InstructionType = InstructionType.GENERAL
    ) -> List[Dict]:
        """Perform semantic search in the repository for a single branch."""
        return self.semantic_search_multi_branch(
            query=query,
            workspace=workspace,
            project=project,
            branches=[branch],
            top_k=top_k,
            filter_language=filter_language,
            instruction_type=instruction_type
        )

    def semantic_search_multi_branch(
            self: RAGQueryBase,
            query: str,
            workspace: str,
            project: str,
            branches: List[str],
            top_k: int = 10,
            filter_language: Optional[str] = None,
            instruction_type: InstructionType = InstructionType.GENERAL,
            excluded_paths: Optional[List[str]] = None
    ) -> List[Dict]:
        """Perform semantic search across multiple branches with filtering.

        Args:
            branches: List of branches to search (e.g., ['feature/xyz', 'main'])
            excluded_paths: Files to exclude from results (e.g., deleted files)
        """
        collection_name = self._get_project_collection_name(workspace, project)
        excluded_paths = excluded_paths or []

        logger.info(f"Multi-branch search in {collection_name} branches={branches} for: {query[:50]}...")

        try:
            if not self._collection_or_alias_exists(collection_name):
                logger.warning(f"Collection {collection_name} does not exist")
                return []

            # Get or create cached VectorStoreIndex
            index = self._get_or_create_index(collection_name)

            # Create retriever with branch filter
            filters = []
            for branch in branches:
                filters.append(MetadataFilter(key="branch", value=branch, operator=FilterOperator.EQ))

            metadata_filters = MetadataFilters(
                filters=filters,
                condition="or" if len(filters) > 1 else "and"
            )

            retriever = index.as_retriever(
                similarity_top_k=top_k * len(branches),
                filters=metadata_filters
            )

            # Format query with instruction (model-aware)
            formatted_query = format_query(query, instruction_type, self._supports_instructions)
            logger.info(f"Using instruction: {instruction_type} (supports_instructions={self._supports_instructions})")

            nodes = retriever.retrieve(formatted_query)

            results = []
            for node in nodes:
                metadata = node.node.metadata

                if filter_language and metadata.get("language") != filter_language:
                    continue

                path = metadata.get("path", metadata.get("file_path", ""))
                if path in excluded_paths:
                    continue

                result = {
                    "text": node.node.text,
                    "score": node.score,
                    "metadata": metadata
                }
                results.append(result)

            logger.info(f"Found {len(results)} results across {len(branches)} branches")
            return results

        except Exception as e:
            logger.error(f"Error during multi-branch semantic search: {e}")
            return []

    def _dedupe_by_branch_priority(
            self,
            results: List[Dict],
            target_branch: str,
            base_branch: Optional[str] = None
    ) -> List[Dict]:
        """Deduplicate results by file path, preferring target branch version.

        When same file exists in multiple branches, keep only the TARGET branch version.
        This ensures we review the NEW code, not the OLD code.

        Strategy:
        1. First pass: collect all paths that exist in target branch
        2. Second pass: for each result, include it only if:
           - It's from target branch, OR
           - Its path doesn't exist in target branch (cross-file reference from base)
        """
        if not results:
            return results

        target_branch_paths = set()
        for result in results:
            metadata = result.get('metadata', {})
            branch = metadata.get('branch', '')
            if branch == target_branch:
                path = metadata.get('path', metadata.get('file_path', ''))
                target_branch_paths.add(path)

        logger.debug(f"Target branch '{target_branch}' has {len(target_branch_paths)} unique paths")

        deduped = []
        seen_chunks = set()

        for result in results:
            metadata = result.get('metadata', {})
            path = metadata.get('path', metadata.get('file_path', ''))
            branch = metadata.get('branch', '')

            chunk_id = f"{path}:{branch}:{hash(result.get('text', '')[:100])}"

            if chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)

            if branch == target_branch:
                deduped.append(result)
            elif path not in target_branch_paths:
                deduped.append(result)

        skipped_count = len(results) - len(deduped)
        if skipped_count > 0:
            logger.info(f"Branch priority: kept {len(deduped)} results, skipped {skipped_count} base branch duplicates")

        return deduped
