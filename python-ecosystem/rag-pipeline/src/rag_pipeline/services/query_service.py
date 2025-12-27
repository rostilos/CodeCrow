from typing import List, Dict, Optional
import logging
import os

from llama_index.core import VectorStoreIndex
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from ..models.config import RAGConfig
from ..utils.utils import make_namespace
from ..core.openrouter_embedding import OpenRouterEmbedding
from ..models.instructions import InstructionType, format_query

logger = logging.getLogger(__name__)


# File priority patterns for smart RAG
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
        filter_language: Optional[str] = None,
        instruction_type: InstructionType = InstructionType.GENERAL
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

            # Format query with instruction
            formatted_query = format_query(query, instruction_type)
            logger.info(f"Using instruction: {instruction_type}")

            # Retrieve nodes
            nodes = retriever.retrieve(formatted_query)

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
        top_k: int = 15,  # Increased default top_k since we filter later
        enable_priority_reranking: bool = True,
        min_relevance_score: float = 0.7
    ) -> Dict:
        """
        Get relevant context for PR review using Smart RAG (Query Decomposition).
        Executes multiple targeted queries and merges results with intelligent filtering.
        
        Lost-in-the-Middle protection features:
        - Priority-based score boosting for core files
        - Configurable relevance threshold
        - Deduplication of similar chunks
        """
        diff_snippets = diff_snippets or []
        logger.info(f"Smart RAG: Decomposing queries for {len(changed_files)} files (priority_reranking={enable_priority_reranking})")

        # 1. Decompose into multiple targeted queries
        queries = self._decompose_queries(
            pr_title=pr_title,
            pr_description=pr_description,
            diff_snippets=diff_snippets,
            changed_files=changed_files
        )

        all_results = []
        
        # 2. Execute queries (sequentially for now, could be parallelized)
        for q_text, q_weight, q_top_k, q_instruction_type in queries:
            if not q_text.strip():
                continue
                
            results = self.semantic_search(
                query=q_text,
                workspace=workspace,
                project=project,
                branch=branch,
                top_k=q_top_k,
                instruction_type=q_instruction_type
            )
            
            # Attach weight metadata to results for ranking
            for r in results:
                r["_query_weight"] = q_weight
                
            all_results.extend(results)

        # 3. Merge, Deduplicate, and Rank (with priority boosting if enabled)
        final_results = self._merge_and_rank_results(
            all_results, 
            min_score_threshold=min_relevance_score if enable_priority_reranking else 0.5
        )
        
        # 4. Fallback if smart filtering was too aggressive
        if not final_results and all_results:
            logger.info("Smart RAG: threshold too strict, falling back to top raw results")
            # Sort by raw score descent
            raw_sorted = sorted(all_results, key=lambda x: x['score'], reverse=True)
            # Remove duplicates by unique content
            seen = set()
            unique_fallback = []
            for r in raw_sorted:
                content_hash = f"{r['metadata'].get('file_path','')}:{r['text']}"
                if content_hash not in seen:
                    seen.add(content_hash)
                    unique_fallback.append(r)
            final_results = unique_fallback[:5]

        # Group by file for final output
        relevant_code = []
        related_files = set()

        for result in final_results:
            relevant_code.append({
                "text": result["text"],
                "score": result["score"],
                "metadata": result["metadata"]
            })

            if "path" in result["metadata"]:
                related_files.add(result["metadata"]["path"])
        
        logger.info(f"Smart RAG: Final context has {len(relevant_code)} chunks from {len(related_files)} files")

        return {
            "relevant_code": relevant_code,
            "related_files": list(related_files),
            "changed_files": changed_files
        }

    def _decompose_queries(
        self,
        pr_title: Optional[str],
        pr_description: Optional[str],
        diff_snippets: List[str],
        changed_files: List[str]
    ) -> List[tuple]:
        """
        Generate a list of (query_text, weight, top_k) tuples.
        """
        from collections import defaultdict
        import os

        queries = []

        # A. Intent Query (High Level) - Weight 1.0
        intent_parts = []
        if pr_title: intent_parts.append(pr_title)
        if pr_description: intent_parts.append(pr_description[:500])
        
        if intent_parts:
            queries.append((" ".join(intent_parts), 1.0, 10, InstructionType.GENERAL))

        # B. File Context Queries (Mid Level) - Weight 0.8
        # Strategy: Cluster files by directory to handle large PRs.
        # Instead of picking random 5 files, we pick top 5 most impacted DIRECTORIES.
        dir_groups = defaultdict(list)
        for f in changed_files:
            # removing filename to get dir
            d = os.path.dirname(f) 
            # if root file, group under 'root'
            d = d if d else "root"
            dir_groups[d].append(os.path.basename(f))

        # Sort directories by number of changed files (descending)
        # Identify the "Hotspots" of this PR
        sorted_dirs = sorted(dir_groups.items(), key=lambda x: len(x[1]), reverse=True)

        for dir_path, files in sorted_dirs[:5]:
            # Construct a query for this cluster
            # "logic related to src/auth involving: Login.tsx, Register.tsx, User.ts..."
            
            # If too many files in one dir, truncate list to avoid embedding overflow
            display_files = files[:10]
            files_str = ", ".join(display_files)
            if len(files) > 10:
                files_str += "..."
            
            clean_path = "root directory" if dir_path == "root" else dir_path
            q = f"logic in {clean_path} related to {files_str}"
            
            queries.append((q, 0.8, 5, InstructionType.LOGIC))

        # C. Snippet Queries (Low Level) - Weight 1.2 (High precision)
        for snippet in diff_snippets[:3]:
            # Clean snippet: remove +/- markers, take first few lines
            lines = [l.strip() for l in snippet.split('\n') if l.strip() and not l.startswith(('+', '-'))]
            if lines:
                # Join first 2-3 significant lines
                clean_snippet = " ".join(lines[:3]) 
                if len(clean_snippet) > 10:
                    queries.append((clean_snippet, 1.2, 5, InstructionType.DEPENDENCY))

        return queries

    def _merge_and_rank_results(self, results: List[Dict], min_score_threshold: float = 0.75) -> List[Dict]:
        """
        Deduplicate matches and filter by relevance score with priority-based reranking.
        """
        grouped = {}
        
        # Deduplicate by file_path + content hash
        for r in results:
            key = f"{r['metadata'].get('file_path', 'unknown')}_{hash(r['text'])}"
            
            # Keep the highest scoring occurrence
            if key not in grouped:
                grouped[key] = r
            else:
                if r['score'] > grouped[key]['score']:
                    grouped[key] = r
        
        unique_results = list(grouped.values())
        
        # Apply priority-based score boosting
        for result in unique_results:
            file_path = result['metadata'].get('path', result['metadata'].get('file_path', '')).lower()
            
            # Boost high-priority files
            if any(p in file_path for p in HIGH_PRIORITY_PATTERNS):
                result['score'] = min(1.0, result['score'] * 1.3)
                result['_priority'] = 'HIGH'
            elif any(p in file_path for p in MEDIUM_PRIORITY_PATTERNS):
                result['score'] = min(1.0, result['score'] * 1.1)
                result['_priority'] = 'MEDIUM'
            elif any(p in file_path for p in LOW_PRIORITY_PATTERNS):
                result['score'] = result['score'] * 0.8  # Penalize test/config files
                result['_priority'] = 'LOW'
            else:
                result['_priority'] = 'MEDIUM'
        
        # Filter by threshold
        filtered = [r for r in unique_results if r['score'] >= min_score_threshold]
        
        # Sort by score descending
        filtered.sort(key=lambda x: x['score'], reverse=True)
        
        return filtered

