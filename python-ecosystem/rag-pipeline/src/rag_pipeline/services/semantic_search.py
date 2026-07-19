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
from ..models.snapshot import ContextSnapshotV1

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
            instruction_type: InstructionType = InstructionType.GENERAL,
            revision: Optional[str] = None,
            snapshot: Optional[ContextSnapshotV1] = None,
            execution_id: Optional[str] = None,
    ) -> List[Dict]:
        """Perform semantic search in the repository for a single branch."""
        return self.semantic_search_multi_branch(
            query=query,
            workspace=workspace,
            project=project,
            branches=[branch],
            top_k=top_k,
            filter_language=filter_language,
            instruction_type=instruction_type,
            branch_revisions={branch: revision} if revision else None,
            processing_snapshot=snapshot,
            execution_id=execution_id,
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
            excluded_paths: Optional[List[str]] = None,
            branch_revisions: Optional[Dict[str, str]] = None,
            processing_snapshot: Optional[ContextSnapshotV1] = None,
            execution_id: Optional[str] = None,
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

            # Bind branch routing labels to immutable revisions when exact mode
            # is requested. Missing coordinates are a hard miss rather than a
            # fallback to mutable branch data.
            if branch_revisions is not None:
                missing = [branch for branch in branches if not branch_revisions.get(branch)]
                if missing:
                    logger.error(
                        "Exact semantic search missing revisions for branches: %s",
                        missing,
                    )
                    return []
                coordinate_filters = [
                    MetadataFilters(
                        filters=[
                            MetadataFilter(
                                key="branch",
                                value=branch,
                                operator=FilterOperator.EQ,
                            ),
                            MetadataFilter(
                                key="snapshot_sha",
                                value=branch_revisions[branch],
                                operator=FilterOperator.EQ,
                            ),
                            *(
                                [
                                    MetadataFilter(
                                        key="parser_version",
                                        value=processing_snapshot.parser_version,
                                        operator=FilterOperator.EQ,
                                    ),
                                    MetadataFilter(
                                        key="chunker_version",
                                        value=processing_snapshot.chunker_version,
                                        operator=FilterOperator.EQ,
                                    ),
                                    MetadataFilter(
                                        key="embedding_version",
                                        value=processing_snapshot.embedding_version,
                                        operator=FilterOperator.EQ,
                                    ),
                                ]
                                if processing_snapshot is not None
                                else []
                            ),
                        ],
                        condition="and",
                    )
                    for branch in branches
                ]
                metadata_filters = MetadataFilters(
                    filters=coordinate_filters,
                    condition="or" if len(coordinate_filters) > 1 else "and",
                )
            else:
                filters = [
                    MetadataFilter(
                        key="branch",
                        value=branch,
                        operator=FilterOperator.EQ,
                    )
                    for branch in branches
                ]
                metadata_filters = MetadataFilters(
                    filters=filters,
                    condition="or" if len(filters) > 1 else "and",
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

                if branch_revisions is not None:
                    result_branch = metadata.get("branch")
                    expected_revision = branch_revisions.get(result_branch)
                    if (
                        expected_revision is None
                        or metadata.get("snapshot_sha") != expected_revision
                    ):
                        logger.error(
                            "Discarding context outside exact snapshot: branch=%r revision=%r",
                            result_branch,
                            metadata.get("snapshot_sha"),
                        )
                        continue
                    if metadata.get("pr") is True and (
                        not execution_id
                        or metadata.get("execution_id") != execution_id
                    ):
                        logger.error(
                            "Discarding PR overlay context from another execution: branch=%r",
                            result_branch,
                        )
                        continue
                    if processing_snapshot is not None and any(
                        metadata.get(key) != expected
                        for key, expected in (
                            ("parser_version", processing_snapshot.parser_version),
                            ("chunker_version", processing_snapshot.chunker_version),
                            ("embedding_version", processing_snapshot.embedding_version),
                        )
                    ):
                        logger.error(
                            "Discarding context with mismatched processing identity: branch=%r",
                            result_branch,
                        )
                        continue

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
