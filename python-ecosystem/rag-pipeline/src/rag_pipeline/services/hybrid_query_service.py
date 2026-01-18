"""
Hybrid Query Service for Hierarchical RAG System.

Combines base index and delta indexes to provide context-aware retrieval
for PR analysis targeting branches other than the base (e.g., release branches).
"""
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import logging

from llama_index.core import VectorStoreIndex
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from ..models.config import RAGConfig
from ..utils.utils import make_namespace
from ..core.openrouter_embedding import OpenRouterEmbedding
from ..models.instructions import InstructionType, format_query

logger = logging.getLogger(__name__)

# Score boosting for hybrid results
DEFAULT_DELTA_BOOST = 1.3  # Delta results get 30% boost for freshness

# File priority patterns (same as base query service)
HIGH_PRIORITY_PATTERNS = [
    'service', 'controller', 'handler', 'api', 'core', 'auth', 'security',
    'permission', 'repository', 'dao', 'migration'
]

MEDIUM_PRIORITY_PATTERNS = [
    'model', 'entity', 'dto', 'schema', 'util', 'helper', 'common',
    'shared', 'component', 'hook', 'client', 'integration'
]

LOW_PRIORITY_PATTERNS = [
    'test', 'spec', 'config', 'mock', 'fixture', 'stub'
]

CONTENT_TYPE_BOOST = {
    'functions_classes': 1.2,
    'fallback': 1.0,
    'oversized_split': 0.95,
    'simplified_code': 0.7,
}


@dataclass
class HybridQueryResult:
    """Result from hybrid query."""
    relevant_code: List[Dict]
    related_files: List[str]
    changed_files: List[str]
    hybrid_metadata: Dict


class HybridQueryService:
    """
    Service for hybrid RAG queries combining base and delta indexes.
    
    Used when:
    1. PR targets a branch that has a delta index (e.g., release/1.0)
    2. Base index exists for the main branch (e.g., master)
    3. We want to combine general context with branch-specific changes
    """

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

    def _get_base_collection_name(self, workspace: str, project: str, branch: str) -> str:
        """Generate collection name for base index."""
        namespace = make_namespace(workspace, project, branch)
        return f"{self.config.qdrant_collection_prefix}_{namespace}"

    def _get_delta_collection_name(self, workspace: str, project: str, branch: str) -> str:
        """Generate collection name for delta index."""
        namespace = make_namespace(workspace, project, f"delta_{branch}")
        return f"{self.config.qdrant_collection_prefix}_{namespace}"

    def _collection_exists(self, collection_name: str) -> bool:
        """Check if collection or alias exists."""
        try:
            collections = [c.name for c in self.qdrant_client.get_collections().collections]
            if collection_name in collections:
                return True
            
            aliases = self.qdrant_client.get_aliases()
            if any(a.alias_name == collection_name for a in aliases.aliases):
                return True
            
            return False
        except Exception as e:
            logger.warning(f"Error checking collection existence: {e}")
            return False

    def _query_collection(
        self,
        collection_name: str,
        query: str,
        top_k: int = 10,
        instruction_type: InstructionType = InstructionType.GENERAL
    ) -> List[Dict]:
        """Query a single collection and return results."""
        try:
            if not self._collection_exists(collection_name):
                return []
            
            vector_store = QdrantVectorStore(
                client=self.qdrant_client,
                collection_name=collection_name
            )
            
            index = VectorStoreIndex.from_vector_store(
                vector_store=vector_store,
                embed_model=self.embed_model
            )
            
            retriever = index.as_retriever(similarity_top_k=top_k)
            
            formatted_query = format_query(query, instruction_type)
            nodes = retriever.retrieve(formatted_query)
            
            results = []
            for node in nodes:
                results.append({
                    "text": node.node.text,
                    "score": node.score,
                    "metadata": node.node.metadata
                })
            
            return results
            
        except Exception as e:
            logger.error(f"Error querying collection {collection_name}: {e}")
            return []

    def _decompose_queries(
        self,
        pr_title: Optional[str],
        pr_description: Optional[str],
        diff_snippets: List[str],
        changed_files: List[str]
    ) -> List[Tuple[str, float, int, InstructionType]]:
        """
        Generate decomposed queries for better recall.
        Returns list of (query_text, weight, top_k, instruction_type).
        """
        from collections import defaultdict
        import os

        queries = []

        # Intent query from PR title/description
        intent_parts = []
        if pr_title:
            intent_parts.append(pr_title)
        if pr_description:
            intent_parts.append(pr_description[:500])

        if intent_parts:
            queries.append((" ".join(intent_parts), 1.0, 10, InstructionType.GENERAL))

        # File context queries by directory
        dir_groups = defaultdict(list)
        for f in changed_files:
            d = os.path.dirname(f)
            d = d if d else "root"
            dir_groups[d].append(os.path.basename(f))

        sorted_dirs = sorted(dir_groups.items(), key=lambda x: len(x[1]), reverse=True)

        for dir_path, files in sorted_dirs[:5]:
            display_files = files[:10]
            files_str = ", ".join(display_files)
            if len(files) > 10:
                files_str += "..."

            clean_path = "root directory" if dir_path == "root" else dir_path
            q = f"logic in {clean_path} related to {files_str}"
            queries.append((q, 0.8, 5, InstructionType.LOGIC))

        # Snippet queries
        for snippet in diff_snippets[:3]:
            lines = [l.strip() for l in snippet.split('\n') if l.strip() and not l.startswith(('+', '-'))]
            if lines:
                clean_snippet = " ".join(lines[:3])
                if len(clean_snippet) > 10:
                    queries.append((clean_snippet, 1.2, 5, InstructionType.DEPENDENCY))

        return queries

    def _merge_hybrid_results(
        self,
        base_results: List[Dict],
        delta_results: List[Dict],
        delta_boost: float = DEFAULT_DELTA_BOOST
    ) -> List[Dict]:
        """
        Merge results from base and delta indexes.
        
        Strategy:
        1. Delta results get score boost (freshness preference)
        2. If same file appears in both, prefer delta version
        3. Deduplicate by content hash
        """
        merged = {}
        
        # Add base results first
        for r in base_results:
            file_path = r['metadata'].get('path', r['metadata'].get('file_path', ''))
            content_hash = hash(r['text'][:200]) if r['text'] else 0
            key = f"{file_path}:{content_hash}"
            merged[key] = {**r, "_source": "base"}
        
        # Overlay delta results with boost
        for r in delta_results:
            file_path = r['metadata'].get('path', r['metadata'].get('file_path', ''))
            content_hash = hash(r['text'][:200]) if r['text'] else 0
            key = f"{file_path}:{content_hash}"
            
            r_boosted = {
                **r, 
                "score": min(1.0, r["score"] * delta_boost), 
                "_source": "delta"
            }
            
            # Delta always wins for same content
            if key in merged:
                if r_boosted["score"] >= merged[key]["score"]:
                    merged[key] = r_boosted
            else:
                merged[key] = r_boosted
        
        return list(merged.values())

    def _apply_priority_reranking(
        self,
        results: List[Dict],
        min_score_threshold: float = 0.7
    ) -> List[Dict]:
        """Apply file priority and content type boosting."""
        for result in results:
            metadata = result.get('metadata', {})
            file_path = metadata.get('path', metadata.get('file_path', '')).lower()
            content_type = metadata.get('content_type', 'fallback')
            semantic_names = metadata.get('semantic_names', [])

            base_score = result['score']

            # File path priority
            if any(p in file_path for p in HIGH_PRIORITY_PATTERNS):
                base_score *= 1.3
                result['_priority'] = 'HIGH'
            elif any(p in file_path for p in MEDIUM_PRIORITY_PATTERNS):
                base_score *= 1.1
                result['_priority'] = 'MEDIUM'
            elif any(p in file_path for p in LOW_PRIORITY_PATTERNS):
                base_score *= 0.8
                result['_priority'] = 'LOW'
            else:
                result['_priority'] = 'MEDIUM'

            # Content type boost
            content_boost = CONTENT_TYPE_BOOST.get(content_type, 1.0)
            base_score *= content_boost
            result['_content_type'] = content_type

            # Semantic names bonus
            if semantic_names:
                base_score *= 1.1
                result['_has_semantic_names'] = True

            # Docstring bonus
            if metadata.get('docstring'):
                base_score *= 1.05

            result['score'] = min(1.0, base_score)

        # Filter and sort
        filtered = [r for r in results if r['score'] >= min_score_threshold]
        filtered.sort(key=lambda x: x['score'], reverse=True)

        return filtered

    def get_hybrid_context_for_pr(
        self,
        workspace: str,
        project: str,
        base_branch: str,
        target_branch: str,
        changed_files: List[str],
        diff_snippets: Optional[List[str]] = None,
        pr_title: Optional[str] = None,
        pr_description: Optional[str] = None,
        top_k: int = 15,
        enable_priority_reranking: bool = True,
        min_relevance_score: float = 0.7,
        delta_boost: float = DEFAULT_DELTA_BOOST
    ) -> HybridQueryResult:
        """
        Get PR context using hybrid retrieval from base + delta indexes.
        
        Args:
            workspace: Workspace identifier
            project: Project identifier
            base_branch: The base RAG index branch (e.g., "master")
            target_branch: The PR target branch (e.g., "release/1.0")
            changed_files: Files changed in the PR
            diff_snippets: Code snippets from the diff
            pr_title: PR title for semantic understanding
            pr_description: PR description
            top_k: Number of results to retrieve
            enable_priority_reranking: Apply priority-based boosting
            min_relevance_score: Minimum score threshold
            delta_boost: Score multiplier for delta results
            
        Returns:
            HybridQueryResult with combined context
        """
        diff_snippets = diff_snippets or []
        
        logger.info(
            f"Hybrid RAG query: base={base_branch}, target={target_branch}, "
            f"files={len(changed_files)}"
        )

        # Decompose queries
        queries = self._decompose_queries(
            pr_title=pr_title,
            pr_description=pr_description,
            diff_snippets=diff_snippets,
            changed_files=changed_files
        )

        # Collection names
        base_collection = self._get_base_collection_name(workspace, project, base_branch)
        delta_collection = self._get_delta_collection_name(workspace, project, target_branch)

        # Check what's available
        base_exists = self._collection_exists(base_collection)
        delta_exists = self._collection_exists(delta_collection)

        logger.info(f"Collections: base={base_exists}, delta={delta_exists}")

        all_base_results = []
        all_delta_results = []

        # Execute queries against both indexes
        for q_text, q_weight, q_top_k, q_instruction_type in queries:
            if not q_text.strip():
                continue

            # Query base index
            if base_exists:
                base_results = self._query_collection(
                    base_collection, q_text, q_top_k, q_instruction_type
                )
                for r in base_results:
                    r["_query_weight"] = q_weight
                all_base_results.extend(base_results)

            # Query delta index (with reduced top_k since it's supplementary)
            if delta_exists:
                delta_results = self._query_collection(
                    delta_collection, q_text, max(3, q_top_k // 2), q_instruction_type
                )
                for r in delta_results:
                    r["_query_weight"] = q_weight
                all_delta_results.extend(delta_results)

        # Merge results
        merged_results = self._merge_hybrid_results(
            base_results=all_base_results,
            delta_results=all_delta_results,
            delta_boost=delta_boost
        )

        # Apply priority reranking
        if enable_priority_reranking:
            final_results = self._apply_priority_reranking(
                merged_results,
                min_score_threshold=min_relevance_score
            )
        else:
            final_results = [r for r in merged_results if r['score'] >= min_relevance_score]
            final_results.sort(key=lambda x: x['score'], reverse=True)

        # Fallback if too strict
        if not final_results and merged_results:
            logger.info("Hybrid RAG: threshold too strict, using top raw results")
            seen = set()
            unique_fallback = []
            for r in sorted(merged_results, key=lambda x: x['score'], reverse=True):
                content_hash = f"{r['metadata'].get('path', '')}:{r['text'][:100]}"
                if content_hash not in seen:
                    seen.add(content_hash)
                    unique_fallback.append(r)
            final_results = unique_fallback[:5]

        # Collect related files
        related_files = set()
        for result in final_results:
            if "path" in result["metadata"]:
                related_files.add(result["metadata"]["path"])

        # Build response
        relevant_code = []
        for result in final_results:
            relevant_code.append({
                "text": result["text"],
                "score": result["score"],
                "metadata": result["metadata"],
                "_source": result.get("_source", "unknown")
            })

        # Count sources
        base_count = sum(1 for r in relevant_code if r.get("_source") == "base")
        delta_count = sum(1 for r in relevant_code if r.get("_source") == "delta")

        logger.info(
            f"Hybrid RAG: {len(relevant_code)} results "
            f"({base_count} base, {delta_count} delta) from {len(related_files)} files"
        )

        return HybridQueryResult(
            relevant_code=relevant_code,
            related_files=list(related_files),
            changed_files=changed_files,
            hybrid_metadata={
                "base_branch": base_branch,
                "target_branch": target_branch,
                "base_collection_used": base_exists,
                "delta_collection_used": delta_exists,
                "base_results_count": len(all_base_results),
                "delta_results_count": len(all_delta_results),
                "merged_results_count": len(merged_results),
                "final_results_count": len(final_results),
                "delta_boost_applied": delta_boost
            }
        )

    def should_use_hybrid(
        self,
        workspace: str,
        project: str,
        base_branch: str,
        target_branch: str
    ) -> Tuple[bool, str]:
        """
        Determine if hybrid query should be used.
        
        Returns:
            Tuple of (should_use_hybrid, reason)
        """
        if base_branch == target_branch:
            return False, "target_is_base"

        base_collection = self._get_base_collection_name(workspace, project, base_branch)
        delta_collection = self._get_delta_collection_name(workspace, project, target_branch)

        base_exists = self._collection_exists(base_collection)
        delta_exists = self._collection_exists(delta_collection)

        if not base_exists:
            return False, "no_base_index"

        if delta_exists:
            return True, "delta_available"
        else:
            return False, "no_delta_index"
