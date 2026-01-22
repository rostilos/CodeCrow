from typing import List, Dict, Optional
import logging
import os

from llama_index.core import VectorStoreIndex
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http.models import Filter, FieldCondition, MatchValue, MatchAny

from ..models.config import RAGConfig
from ..utils.utils import make_namespace, make_project_namespace
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

# Content type priorities for AST-based chunks
# functions_classes are more valuable than simplified_code (placeholders)
CONTENT_TYPE_BOOST = {
    'functions_classes': 1.2,  # Full function/class definitions - highest value
    'fallback': 1.0,  # Regex-based split - normal value
    'oversized_split': 0.95,  # Large chunks that were split - slightly lower
    'simplified_code': 0.7,  # Code with placeholders - lower value (context only)
}


class RAGQueryService:
    """Service for querying RAG indices using Qdrant.
    
    Uses single-collection-per-project architecture with branch metadata filtering.
    Supports multi-branch queries for PR reviews (base + target branches).
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

    def _collection_or_alias_exists(self, name: str) -> bool:
        """Check if a collection or alias with the given name exists."""
        try:
            collections = [c.name for c in self.qdrant_client.get_collections().collections]
            if name in collections:
                return True
            
            aliases = self.qdrant_client.get_aliases()
            if any(a.alias_name == name for a in aliases.aliases):
                return True
            
            return False
        except Exception as e:
            logger.warning(f"Error checking collection/alias existence: {e}")
            return False

    def _get_project_collection_name(self, workspace: str, project: str) -> str:
        """Generate collection name for a project (single collection for all branches)"""
        namespace = make_project_namespace(workspace, project)
        return f"{self.config.qdrant_collection_prefix}_{namespace}"

    def _get_collection_name(self, workspace: str, project: str, branch: str) -> str:
        """Generate collection name (legacy - kept for backward compatibility)"""
        namespace = make_namespace(workspace, project, branch)
        return f"{self.config.qdrant_collection_prefix}_{namespace}"

    def _dedupe_by_branch_priority(
            self, 
            results: List[Dict], 
            target_branch: str,
            base_branch: Optional[str] = None
    ) -> List[Dict]:
        """Deduplicate results by file path, preferring target branch version.
        
        When same file exists in multiple branches, keep only one version:
        - Prefer target_branch version (it's the latest)
        - Fall back to base_branch version if target doesn't have it
        
        This preserves cross-file relationships while avoiding duplicates.
        """
        if not results:
            return results

        # Group by path + chunk position (approximate by content hash)
        grouped = {}
        
        for result in results:
            metadata = result.get('metadata', {})
            path = metadata.get('path', metadata.get('file_path', ''))
            branch = metadata.get('branch', '')
            
            # Create a key based on path and approximate content position
            # Using text hash to distinguish different chunks from same file
            text_hash = hash(result.get('text', '')[:200])  # First 200 chars for identity
            key = f"{path}:{text_hash}"
            
            if key not in grouped:
                grouped[key] = result
            else:
                existing_branch = grouped[key].get('metadata', {}).get('branch', '')
                
                # Prefer target branch, then base branch, then whatever has higher score
                if branch == target_branch and existing_branch != target_branch:
                    grouped[key] = result
                elif (branch == base_branch and 
                      existing_branch != target_branch and 
                      existing_branch != base_branch):
                    grouped[key] = result
                elif result['score'] > grouped[key]['score'] and branch == existing_branch:
                    # Same branch, keep higher score
                    grouped[key] = result

        return list(grouped.values())

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
        """Perform semantic search in the repository for a single branch"""
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
            self,
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
            # Check if collection exists
            if not self._collection_or_alias_exists(collection_name):
                logger.warning(f"Collection {collection_name} does not exist")
                return []

            # Build Qdrant filter for branch(es)
            must_conditions = []
            
            if len(branches) == 1:
                must_conditions.append(
                    FieldCondition(key="branch", match=MatchValue(value=branches[0]))
                )
            else:
                must_conditions.append(
                    FieldCondition(key="branch", match=MatchAny(any=branches))
                )

            # Create vector store with filter
            vector_store = QdrantVectorStore(
                client=self.qdrant_client,
                collection_name=collection_name
            )

            index = VectorStoreIndex.from_vector_store(
                vector_store=vector_store,
                embed_model=self.embed_model
            )

            # Create retriever with branch filter
            from llama_index.core.vector_stores import MetadataFilters, MetadataFilter, FilterOperator
            
            filters = []
            for branch in branches:
                filters.append(MetadataFilter(key="branch", value=branch, operator=FilterOperator.EQ))
            
            # Use OR logic for multiple branches
            metadata_filters = MetadataFilters(
                filters=filters,
                condition="or" if len(filters) > 1 else "and"
            )

            retriever = index.as_retriever(
                similarity_top_k=top_k * len(branches),  # Get more results to account for filtering
                filters=metadata_filters
            )

            # Format query with instruction
            formatted_query = format_query(query, instruction_type)
            logger.info(f"Using instruction: {instruction_type}")

            # Retrieve nodes
            nodes = retriever.retrieve(formatted_query)

            # Format results
            results = []
            for node in nodes:
                metadata = node.node.metadata
                
                # Filter by language if specified
                if filter_language and metadata.get("language") != filter_language:
                    continue

                # Filter excluded paths
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

    def _get_fallback_branch(self, workspace: str, project: str, requested_branch: str) -> Optional[str]:
        """Find a fallback branch when requested branch has no data."""
        fallback_branches = ['main', 'master', 'develop']
        collection_name = self._get_project_collection_name(workspace, project)
        
        if not self._collection_or_alias_exists(collection_name):
            return None
        
        for fallback in fallback_branches:
            if fallback == requested_branch:
                continue
            
            # Check if this branch has any points in the collection
            try:
                count_result = self.qdrant_client.count(
                    collection_name=collection_name,
                    count_filter=Filter(
                        must=[FieldCondition(key="branch", match=MatchValue(value=fallback))]
                    )
                )
                if count_result.count > 0:
                    logger.info(f"Found fallback branch '{fallback}' with {count_result.count} points")
                    return fallback
            except Exception as e:
                logger.debug(f"Error checking fallback branch '{fallback}': {e}")
        
        return None

    def get_context_for_pr(
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
    ) -> Dict:
        """
        Get relevant context for PR review using Smart RAG with multi-branch support.
        
        Queries both target branch and base branch to preserve cross-file relationships.
        Results are deduplicated with target branch taking priority for same files.
        
        Args:
            branch: Target branch (the PR's source branch)
            base_branch: Base branch (the PR's target, e.g., 'main'). If None, uses fallback logic.
            deleted_files: Files that were deleted in target branch (excluded from results)
        """
        diff_snippets = diff_snippets or []
        deleted_files = deleted_files or []
        
        # Determine branches to search
        branches_to_search = [branch]
        effective_base_branch = base_branch
        
        collection_name = self._get_project_collection_name(workspace, project)
        
        if not self._collection_or_alias_exists(collection_name):
            logger.warning(f"Collection {collection_name} does not exist")
            return {
                "relevant_code": [],
                "related_files": [],
                "changed_files": changed_files,
                "_error": "collection_not_found"
            }
        
        # Add base branch to search if provided or find fallback
        if base_branch:
            branches_to_search.append(base_branch)
        else:
            # Try to find a base branch (main/master/develop)
            fallback = self._get_fallback_branch(workspace, project, branch)
            if fallback:
                branches_to_search.append(fallback)
                effective_base_branch = fallback
        
        # Remove duplicates while preserving order
        branches_to_search = list(dict.fromkeys(branches_to_search))
        
        logger.info(
            f"Smart RAG: Multi-branch query for {len(changed_files)} files "
            f"(branches={branches_to_search}, priority_reranking={enable_priority_reranking})")

        # 1. Decompose into multiple targeted queries
        queries = self._decompose_queries(
            pr_title=pr_title,
            pr_description=pr_description,
            diff_snippets=diff_snippets,
            changed_files=changed_files
        )

        all_results = []

        # 2. Execute queries with multi-branch search
        for q_text, q_weight, q_top_k, q_instruction_type in queries:
            if not q_text.strip():
                continue

            results = self.semantic_search_multi_branch(
                query=q_text,
                workspace=workspace,
                project=project,
                branches=branches_to_search,
                top_k=q_top_k,
                instruction_type=q_instruction_type,
                excluded_paths=deleted_files
            )

            for r in results:
                r["_query_weight"] = q_weight

            all_results.extend(results)

        # 3. Deduplicate by branch priority (target branch wins)
        deduped_results = self._dedupe_by_branch_priority(
            all_results, 
            target_branch=branch,
            base_branch=effective_base_branch
        )

        # 4. Merge, filter, and rank with priority boosting
        final_results = self._merge_and_rank_results(
            deduped_results,
            min_score_threshold=min_relevance_score if enable_priority_reranking else 0.5
        )

        # 5. Fallback if smart filtering was too aggressive
        if not final_results and deduped_results:
            logger.info("Smart RAG: threshold too strict, falling back to top raw results")
            raw_sorted = sorted(deduped_results, key=lambda x: x['score'], reverse=True)
            seen = set()
            unique_fallback = []
            for r in raw_sorted:
                content_hash = f"{r['metadata'].get('file_path', '')}:{r['text']}"
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

        logger.info(
            f"Smart RAG: Final context has {len(relevant_code)} chunks "
            f"from {len(related_files)} files across {len(branches_to_search)} branches")

        result = {
            "relevant_code": relevant_code,
            "related_files": list(related_files),
            "changed_files": changed_files,
            "_branches_searched": branches_to_search
        }
        
        return result

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

        Applies three types of boosting:
        1. File path priority (service/controller vs test/config)
        2. Content type priority (functions_classes vs simplified_code)
        3. Semantic name bonus (chunks with extracted function/class names)
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

        # Apply multi-factor score boosting
        for result in unique_results:
            metadata = result.get('metadata', {})
            file_path = metadata.get('path', metadata.get('file_path', '')).lower()
            content_type = metadata.get('content_type', 'fallback')
            semantic_names = metadata.get('semantic_names', [])

            base_score = result['score']

            # 1. File path priority boosting
            if any(p in file_path for p in HIGH_PRIORITY_PATTERNS):
                base_score *= 1.3
                result['_priority'] = 'HIGH'
            elif any(p in file_path for p in MEDIUM_PRIORITY_PATTERNS):
                base_score *= 1.1
                result['_priority'] = 'MEDIUM'
            elif any(p in file_path for p in LOW_PRIORITY_PATTERNS):
                base_score *= 0.8  # Penalize test/config files
                result['_priority'] = 'LOW'
            else:
                result['_priority'] = 'MEDIUM'

            # 2. Content type boosting (AST-based metadata)
            content_boost = CONTENT_TYPE_BOOST.get(content_type, 1.0)
            base_score *= content_boost
            result['_content_type'] = content_type

            # 3. Semantic name bonus - chunks with extracted names are more valuable
            if semantic_names:
                base_score *= 1.1  # 10% bonus for having semantic names
                result['_has_semantic_names'] = True

            # 4. Docstring bonus - chunks with docstrings provide better context
            if metadata.get('docstring'):
                base_score *= 1.05  # 5% bonus for having docstring

            result['score'] = min(1.0, base_score)

        # Filter by threshold
        filtered = [r for r in unique_results if r['score'] >= min_score_threshold]

        # Sort by score descending
        filtered.sort(key=lambda x: x['score'], reverse=True)

        return filtered

