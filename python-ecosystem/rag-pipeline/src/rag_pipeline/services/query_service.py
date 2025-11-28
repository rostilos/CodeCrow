from typing import List, Dict, Optional
import logging

from llama_index.core import VectorStoreIndex
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from ..models.config import RAGConfig
from ..utils.utils import make_namespace
from ..core.openrouter_embedding import OpenRouterEmbedding

logger = logging.getLogger(__name__)


class RAGQueryService:
    """Service for querying RAG indices using Qdrant"""

    def __init__(self, config: RAGConfig):
        self.config = config

        # Qdrant client
        self.qdrant_client = QdrantClient(url=config.qdrant_url)

        # Embedding model
        self.embed_model = OpenRouterEmbedding(
            api_key=config.openrouter_api_key,
            model=config.openrouter_model,
            api_base=config.openrouter_base_url,
            timeout=60.0,
            max_retries=3
        )

    def _get_collection_name(self, workspace: str, project: str, branch: str) -> str:
        """Generate collection name"""
        namespace = make_namespace(workspace, project, branch)
        return f"{self.config.qdrant_collection_prefix}_{namespace}"

    def semantic_search(
        self,
        query: str,
        workspace: str,
        project: str,
        branch: str,
        top_k: int = 10,
        filter_language: Optional[str] = None
    ) -> List[Dict]:
        """Perform semantic search in the repository"""
        collection_name = self._get_collection_name(workspace, project, branch)

        logger.info(f"Searching in {collection_name} for: {query[:50]}...")

        try:
            # Check if collection exists
            collections = [c.name for c in self.qdrant_client.get_collections().collections]
            if collection_name not in collections:
                logger.warning(f"Collection {collection_name} does not exist")
                return []

            # Create vector store and index
            vector_store = QdrantVectorStore(
                client=self.qdrant_client,
                collection_name=collection_name
            )

            index = VectorStoreIndex.from_vector_store(
                vector_store=vector_store,
                embed_model=self.embed_model
            )

            # Use retriever instead of query_engine (no LLM needed)
            retriever = index.as_retriever(
                similarity_top_k=top_k
            )

            # Retrieve nodes
            nodes = retriever.retrieve(query)

            # Format results
            results = []
            for node in nodes:
                # Filter by language if specified
                if filter_language and node.node.metadata.get("language") != filter_language:
                    continue

                result = {
                    "text": node.node.text,
                    "score": node.score,
                    "metadata": node.node.metadata
                }
                results.append(result)

            logger.info(f"Found {len(results)} results")
            return results

        except Exception as e:
            logger.error(f"Error during semantic search: {e}")
            return []

    def get_context_for_pr(
        self,
        workspace: str,
        project: str,
        branch: str,
        changed_files: List[str],
        diff_snippets: Optional[List[str]] = None,
        pr_title: Optional[str] = None,
        pr_description: Optional[str] = None,
        top_k: int = 10
    ) -> Dict:
        """Get relevant context for PR review using hybrid query approach"""
        logger.info(f"Getting PR context for {len(changed_files)} files with {len(diff_snippets or [])} code snippets")

        # Build intelligent hybrid query
        query = self._build_hybrid_query(
            pr_title=pr_title,
            pr_description=pr_description,
            diff_snippets=diff_snippets or [],
            changed_files=changed_files
        )

        # Search for relevant code
        results = self.semantic_search(
            query=query,
            workspace=workspace,
            project=project,
            branch=branch,
            top_k=top_k
        )

        # Group by file
        relevant_code = []
        related_files = set()

        for result in results:
            relevant_code.append({
                "text": result["text"],
                "score": result["score"],
                "metadata": result["metadata"]
            })

            if "path" in result["metadata"]:
                related_files.add(result["metadata"]["path"])

        context = {
            "relevant_code": relevant_code,
            "related_files": list(related_files),
            "changed_files": changed_files
        }

        logger.info(f"Retrieved context with {len(relevant_code)} chunks from {len(related_files)} files")

        return context

    def _build_hybrid_query(
        self,
        pr_title: Optional[str],
        pr_description: Optional[str],
        diff_snippets: List[str],
        changed_files: List[str],
        max_query_length: int = 25000
    ) -> str:
        """
        Build intelligent query combining PR metadata, code snippets, and file paths.

        Priority:
        1. PR title/description (semantic intent)
        2. Code snippets from diff (actual changes)
        3. File paths (context)
        """
        query_parts = []

        # 1. PR title and description (highest priority)
        if pr_title:
            query_parts.append(pr_title)
        if pr_description:
            desc = pr_description[:300] if len(pr_description) > 300 else pr_description
            query_parts.append(desc)

        # 2. Code snippets (function signatures, meaningful changes)
        if diff_snippets:
            snippets_text = " | ".join(diff_snippets[:10])  # Max 10 snippets
            if snippets_text:
                query_parts.append(f"Code changes: {snippets_text}")

        # 3. File paths (for additional context)
        if changed_files:
            files_text = ", ".join(changed_files[:15])  # Max 15 file names
            query_parts.append(f"Files: {files_text}")

        # Fallback if nothing provided
        if not query_parts:
            return "code review context"

        # Join and truncate to max length
        query = " ".join(query_parts)
        if len(query) > max_query_length:
            query = query[:max_query_length] + "..."

        logger.debug(f"Built hybrid query: {query[:200]}...")
        return query

