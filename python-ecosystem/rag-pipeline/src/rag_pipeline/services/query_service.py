from typing import List, Dict, Optional
import logging

from llama_index.core import VectorStoreIndex
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http.models import Filter, FieldCondition, MatchValue, MatchAny, MatchText

from ..models.config import RAGConfig
from ..models.scoring_config import get_scoring_config
from ..utils.utils import make_project_namespace
from ..core.embedding_factory import create_embedding_model, get_embedding_model_info
from ..models.instructions import InstructionType, format_query

logger = logging.getLogger(__name__)


class RAGQueryService:
    """Service for querying RAG indices using Qdrant.
    
    Uses single-collection-per-project architecture with branch metadata filtering.
    Supports multi-branch queries for PR reviews (base + target branches).
    """

    def __init__(self, config: RAGConfig):
        self.config = config

        # Qdrant client
        self.qdrant_client = QdrantClient(url=config.qdrant_url)

        # Embedding model (supports Ollama and OpenRouter via factory)
        embed_info = get_embedding_model_info(config)
        logger.info(f"QueryService using embedding provider: {embed_info['provider']} ({embed_info['type']})")
        
        self.embed_model = create_embedding_model(config)

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
        
        This ensures:
        - Changed files are always from target branch (the PR's new code)
        - Related files from base branch are included only if they don't exist in target
        """
        if not results:
            return results

        # Step 1: Find all paths that exist in target branch
        target_branch_paths = set()
        for result in results:
            metadata = result.get('metadata', {})
            branch = metadata.get('branch', '')
            if branch == target_branch:
                path = metadata.get('path', metadata.get('file_path', ''))
                target_branch_paths.add(path)
        
        logger.debug(f"Target branch '{target_branch}' has {len(target_branch_paths)} unique paths")
        
        # Step 2: Filter results - target branch wins for same path
        deduped = []
        seen_chunks = set()  # Track (path, chunk_identity) to avoid exact duplicates
        
        for result in results:
            metadata = result.get('metadata', {})
            path = metadata.get('path', metadata.get('file_path', ''))
            branch = metadata.get('branch', '')
            
            # Create chunk identity (path + start of content)
            chunk_id = f"{path}:{branch}:{hash(result.get('text', '')[:100])}"
            
            if chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)
            
            # Include if:
            # 1. It's from target branch (always include), OR
            # 2. Path doesn't exist in target branch (cross-file reference from base)
            if branch == target_branch:
                deduped.append(result)
            elif path not in target_branch_paths:
                # This file only exists in base branch - include for cross-file context
                deduped.append(result)
            # else: skip - file exists in target branch, use that version instead
        
        skipped_count = len(results) - len(deduped)
        if skipped_count > 0:
            logger.info(f"Branch priority: kept {len(deduped)} results, skipped {skipped_count} base branch duplicates")
        
        return deduped

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

    def get_deterministic_context(
            self,
            workspace: str,
            project: str,
            branches: List[str],
            file_paths: List[str],
            limit_per_file: int = 10,
            pr_number: Optional[int] = None,
            pr_changed_files: Optional[List[str]] = None
    ) -> Dict:
        """
        Get context using DETERMINISTIC metadata-based retrieval.
        
        Leverages ALL tree-sitter metadata extracted during indexing:
        - semantic_names: function/method/class names
        - primary_name: main identifier
        - parent_class: containing class
        - full_path: qualified name (e.g., "Data.getConfigData")
        - imports: import statements
        - extends: parent classes/interfaces
        - namespace: package/namespace
        - node_type: method_declaration, class_definition, etc.
        
        Multi-step process:
        1. Query chunks for changed file_paths
        2. Extract metadata (identifiers, parent classes, namespaces, imports)
        3. Find related definitions by:
           a) primary_name match (definitions of used identifiers)
           b) parent_class match (other methods in same class)
           c) namespace match (related code in same package)
        
        NO LANGUAGE-SPECIFIC PARSING NEEDED - tree-sitter already did that!
        Same input always produces same output (deterministic).
        
        Args:
            workspace: VCS workspace
            project: Project name
            branches: Branches to search (target + base for PRs)
            file_paths: Changed file paths from diff
            limit_per_file: Max chunks per file
        
        Returns:
            Dict with chunks grouped by retrieval type and rich metadata
        """
        collection_name = self._get_project_collection_name(workspace, project)
        
        if not self._collection_or_alias_exists(collection_name):
            logger.warning(f"Collection {collection_name} does not exist")
            return {"chunks": [], "changed_files": {}, "related_definitions": {}, 
                    "class_context": {}, "namespace_context": {},
                    "_metadata": {"error": "collection_not_found"}}
        
        logger.info(f"Deterministic context: files={file_paths[:5]}, branches={branches}")
        
        def _apply_branch_priority(points: list, target: str, existing_target_paths: set) -> list:
            """Filter points to prioritize: PR-indexed > target branch > base branch.
            
            For each unique path:
            - If PR-indexed version exists (pr=True), use it exclusively (freshest data)
            - Else if path exists in target branch, keep only target branch version
            - Else if path only in base branch, keep it (cross-file reference)
            
            This ensures deterministic lookup finds CURRENT versions of files
            modified in a PR instead of stale main branch data.
            """
            if not points:
                return points
            
            # Group by path
            by_path = {}
            for p in points:
                path = p.payload.get("path", "")
                if path not in by_path:
                    by_path[path] = []
                by_path[path].append(p)
            
            # Select best version per path
            result = []
            for path, path_points in by_path.items():
                # Highest priority: PR-indexed versions (from hybrid PR mode)
                pr_points = [p for p in path_points if p.payload.get("pr") is True]
                if pr_points:
                    result.extend(pr_points)
                    continue
                
                # Standard branch priority: target > base
                branch_points = [p for p in path_points if p.payload.get("pr") is not True]
                if not target or len(branches) == 1:
                    result.extend(branch_points)
                    continue
                
                has_target = any(p.payload.get("branch") == target for p in branch_points)
                if has_target:
                    # Keep only target branch for this path
                    result.extend([p for p in branch_points if p.payload.get("branch") == target])
                elif path not in existing_target_paths:
                    # Path doesn't exist in target - keep base branch version
                    result.extend(branch_points)
                # else: skip - path exists in target but these results are from base
            
            return result
        
        all_chunks = []
        changed_files_chunks = {}
        related_definitions = {}
        class_context = {}  # Other methods in same classes
        namespace_context = {}  # Related code in same namespaces
        
        # Metadata to collect from changed files
        identifiers_to_find = set()
        parent_classes = set()
        namespaces = set()
        imports_raw = set()
        extends_raw = set()
        
        # Track changed file paths for deduplication
        changed_file_paths = set()
        seen_texts = set()
        
        # Build branch filter - NOTE: branches[0] is the target branch (has priority)
        target_branch = branches[0] if branches else None
        
        base_branch_condition = (
            FieldCondition(key="branch", match=MatchValue(value=branches[0]))
            if len(branches) == 1
            else FieldCondition(key="branch", match=MatchAny(any=branches))
        )
        
        if pr_number:
            # HYBRID MODE: search BOTH branch data AND PR-indexed data
            # This ensures deterministic lookup finds the FRESH PR versions of files
            # (e.g., updated interface signatures) instead of stale main branch versions
            branch_filter = Filter(should=[
                Filter(must=[base_branch_condition]),
                Filter(must=[
                    FieldCondition(key="pr", match=MatchValue(value=True)),
                    FieldCondition(key="pr_number", match=MatchValue(value=pr_number))
                ])
            ])
            logger.info(f"Deterministic hybrid mode: also searching PR-indexed data (pr_number={pr_number})")
        else:
            branch_filter = base_branch_condition
        
        # Track which paths exist in target branch (for priority filtering)
        target_branch_paths = set()
        
        # ========== STEP 1: Get chunks from changed files ==========
        for file_path in file_paths:
            try:
                normalized_path = file_path.lstrip("/")
                filename = normalized_path.rsplit("/", 1)[-1] if "/" in normalized_path else normalized_path
                
                # Try exact path match
                results, _ = self.qdrant_client.scroll(
                    collection_name=collection_name,
                    scroll_filter=Filter(
                        must=[
                            branch_filter,
                            FieldCondition(key="path", match=MatchValue(value=normalized_path))
                        ]
                    ),
                    limit=limit_per_file * len(branches),  # Get more to account for multiple branches
                    with_payload=True,
                    with_vectors=False
                )
                
                # Fallback: try with filename only (handles path prefix mismatches)
                if not results:
                    results, _ = self.qdrant_client.scroll(
                        collection_name=collection_name,
                        scroll_filter=Filter(
                            must=[
                                branch_filter,
                                FieldCondition(key="path", match=MatchText(text=filename))
                            ]
                        ),
                        limit=limit_per_file * len(branches),
                        with_payload=True,
                        with_vectors=False
                    )
                
                # Apply branch priority: if file exists in target branch, only keep target branch version
                if target_branch and len(branches) > 1:
                    # Check if any result is from target branch
                    has_target = any(p.payload.get("branch") == target_branch for p in results)
                    if has_target:
                        # Keep only target branch results for this file
                        results = [p for p in results if p.payload.get("branch") == target_branch]
                        logger.debug(f"Branch priority: keeping target branch '{target_branch}' for {normalized_path}")
                
                # Apply limit after filtering
                results = results[:limit_per_file]
                
                chunks_for_file = []
                for point in results:
                    payload = point.payload
                    text = payload.get("text", payload.get("_node_content", ""))
                    
                    if text in seen_texts:
                        continue
                    seen_texts.add(text)
                    
                    # Track which paths exist in target branch
                    if payload.get("branch") == target_branch:
                        target_branch_paths.add(payload.get("path", ""))
                    
                    chunk = {
                        "text": text,
                        "metadata": {k: v for k, v in payload.items() if k not in ("text", "_node_content")},
                        "score": 1.0,
                        "_match_type": "changed_file",
                        "_matched_on": file_path
                    }
                    chunks_for_file.append(chunk)
                    all_chunks.append(chunk)
                    changed_file_paths.add(payload.get("path", ""))
                    
                    # Extract ALL tree-sitter metadata for step 2-4
                    if isinstance(payload.get("semantic_names"), list):
                        identifiers_to_find.update(payload["semantic_names"])
                    
                    if payload.get("primary_name"):
                        identifiers_to_find.add(payload["primary_name"])
                    
                    if payload.get("parent_class"):
                        parent_classes.add(payload["parent_class"])
                    
                    if payload.get("namespace"):
                        namespaces.add(payload["namespace"])
                    
                    if isinstance(payload.get("imports"), list):
                        for imp in payload["imports"]:
                            # Extract class name from import statement
                            # "use Magento\Store\Model\ScopeInterface;" -> "ScopeInterface"
                            if isinstance(imp, str):
                                parts = imp.replace(";", "").split("\\")
                                if parts:
                                    imports_raw.add(parts[-1].strip())
                    
                    if isinstance(payload.get("extends"), list):
                        extends_raw.update(payload["extends"])
                    
                    if payload.get("parent_class"):
                        extends_raw.add(payload["parent_class"])
                
                changed_files_chunks[file_path] = chunks_for_file
                
            except Exception as e:
                logger.warning(f"Error querying file '{file_path}': {e}")
        
        logger.info(f"Step 1: {len(all_chunks)} chunks from changed files. "
                   f"Extracted: {len(identifiers_to_find)} identifiers, "
                   f"{len(parent_classes)} parent_classes, {len(namespaces)} namespaces, "
                   f"{len(imports_raw)} imports, {len(extends_raw)} extends")
        
        # ========== STEP 2: Find definitions by primary_name ==========
        # Find where identifiers/imports/extends are DEFINED
        all_to_find = identifiers_to_find | imports_raw | extends_raw
        if all_to_find:
            try:
                batch = list(all_to_find)[:100]
                results, _ = self.qdrant_client.scroll(
                    collection_name=collection_name,
                    scroll_filter=Filter(
                        must=[
                            branch_filter,
                            FieldCondition(key="primary_name", match=MatchAny(any=batch))
                        ]
                    ),
                    limit=200 * len(branches),  # Get more to account for multiple branches
                    with_payload=True,
                    with_vectors=False
                )
                
                # Apply branch priority filtering
                results = _apply_branch_priority(results, target_branch, target_branch_paths)
                
                for point in results:
                    payload = point.payload
                    if payload.get("path") in changed_file_paths:
                        continue
                    
                    text = payload.get("text", payload.get("_node_content", ""))
                    if text in seen_texts:
                        continue
                    seen_texts.add(text)
                    
                    primary_name = payload.get("primary_name", "")
                    chunk = {
                        "text": text,
                        "metadata": {k: v for k, v in payload.items() if k not in ("text", "_node_content")},
                        "score": 0.95,
                        "_match_type": "definition",
                        "_matched_on": primary_name
                    }
                    all_chunks.append(chunk)
                    
                    if primary_name not in related_definitions:
                        related_definitions[primary_name] = []
                    related_definitions[primary_name].append(chunk)
                
                logger.info(f"Step 2: Found {len(related_definitions)} definitions by primary_name")
                
            except Exception as e:
                logger.warning(f"Error in primary_name query: {e}")
        
        # ========== STEP 3: Find other methods in same parent_class ==========
        # If we're changing a method in class "Data", find other methods of "Data"
        if parent_classes:
            try:
                batch = list(parent_classes)[:20]
                results, _ = self.qdrant_client.scroll(
                    collection_name=collection_name,
                    scroll_filter=Filter(
                        must=[
                            branch_filter,
                            FieldCondition(key="parent_class", match=MatchAny(any=batch))
                        ]
                    ),
                    limit=100 * len(branches),  # Get more to account for multiple branches
                    with_payload=True,
                    with_vectors=False
                )
                
                # Apply branch priority filtering
                results = _apply_branch_priority(results, target_branch, target_branch_paths)
                
                for point in results:
                    payload = point.payload
                    if payload.get("path") in changed_file_paths:
                        continue
                    
                    text = payload.get("text", payload.get("_node_content", ""))
                    if text in seen_texts:
                        continue
                    seen_texts.add(text)
                    
                    parent_class = payload.get("parent_class", "")
                    chunk = {
                        "text": text,
                        "metadata": {k: v for k, v in payload.items() if k not in ("text", "_node_content")},
                        "score": 0.85,
                        "_match_type": "class_context",
                        "_matched_on": parent_class
                    }
                    all_chunks.append(chunk)
                    
                    if parent_class not in class_context:
                        class_context[parent_class] = []
                    class_context[parent_class].append(chunk)
                
                logger.info(f"Step 3: Found {sum(len(v) for v in class_context.values())} class context chunks")
                
            except Exception as e:
                logger.warning(f"Error in parent_class query: {e}")
        
        # ========== STEP 4: Find related code in same namespace ==========
        # Lower priority - only get a few for broader context
        if namespaces:
            try:
                batch = list(namespaces)[:10]
                results, _ = self.qdrant_client.scroll(
                    collection_name=collection_name,
                    scroll_filter=Filter(
                        must=[
                            branch_filter,
                            FieldCondition(key="namespace", match=MatchAny(any=batch))
                        ]
                    ),
                    limit=30 * len(branches),  # Get more to account for multiple branches
                    with_payload=True,
                    with_vectors=False
                )
                
                # Apply branch priority filtering
                results = _apply_branch_priority(results, target_branch, target_branch_paths)
                
                for point in results:
                    payload = point.payload
                    if payload.get("path") in changed_file_paths:
                        continue
                    
                    text = payload.get("text", payload.get("_node_content", ""))
                    if text in seen_texts:
                        continue
                    seen_texts.add(text)
                    
                    namespace = payload.get("namespace", "")
                    chunk = {
                        "text": text,
                        "metadata": {k: v for k, v in payload.items() if k not in ("text", "_node_content")},
                        "score": 0.75,
                        "_match_type": "namespace_context",
                        "_matched_on": namespace
                    }
                    all_chunks.append(chunk)
                    
                    if namespace not in namespace_context:
                        namespace_context[namespace] = []
                    namespace_context[namespace].append(chunk)
                
                logger.info(f"Step 4: Found {sum(len(v) for v in namespace_context.values())} namespace context chunks")
                
            except Exception as e:
                logger.warning(f"Error in namespace query: {e}")
        
        logger.info(f"Deterministic context complete: {len(all_chunks)} total chunks "
                   f"(changed: {sum(len(v) for v in changed_files_chunks.values())}, "
                   f"definitions: {sum(len(v) for v in related_definitions.values())}, "
                   f"class_ctx: {sum(len(v) for v in class_context.values())}, "
                   f"ns_ctx: {sum(len(v) for v in namespace_context.values())})")
        
        return {
            "chunks": all_chunks,
            "changed_files": changed_files_chunks,
            "related_definitions": related_definitions,
            "class_context": class_context,
            "namespace_context": namespace_context,
            "_metadata": {
                "branches_searched": branches,
                "target_branch": target_branch,
                "files_requested": file_paths,
                "identifiers_extracted": list(identifiers_to_find)[:30],
                "parent_classes_found": list(parent_classes),
                "namespaces_found": list(namespaces),
                "imports_extracted": list(imports_raw)[:30],
                "extends_extracted": list(extends_raw)[:20],
                "target_branch_paths_found": len(target_branch_paths)
            }
        }

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
            deleted_files: Optional[List[str]] = None,
            exclude_pr_files: Optional[List[str]] = None
    ) -> Dict:
        """
        Get relevant context for PR review using Smart RAG with multi-branch support.
        
        Queries both target branch and base branch to preserve cross-file relationships.
        Results are deduplicated with target branch taking priority for same files.
        
        Args:
            branch: Target branch (the PR's source branch)
            base_branch: Base branch (the PR's target, e.g., 'main'). If None, uses fallback logic.
            deleted_files: Files that were deleted in target branch (excluded from results)
            exclude_pr_files: Files indexed separately as PR data (excluded to avoid duplication)
        """
        diff_snippets = diff_snippets or []
        deleted_files = deleted_files or []
        exclude_pr_files = exclude_pr_files or []
        
        # Combine exclusion lists: deleted files + PR-indexed files
        all_excluded_paths = list(set(deleted_files + exclude_pr_files))
        
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
        
        logger.info(f"Generated {len(queries)} queries for PR context")
        for i, (q_text, q_weight, q_top_k, q_type) in enumerate(queries):
            logger.info(f"  Query {i+1}: weight={q_weight}, top_k={q_top_k}, text='{q_text[:80]}...'")

        all_results = []

        # 2. Execute queries with multi-branch search
        for i, (q_text, q_weight, q_top_k, q_instruction_type) in enumerate(queries):
            if not q_text.strip():
                continue

            results = self.semantic_search_multi_branch(
                query=q_text,
                workspace=workspace,
                project=project,
                branches=branches_to_search,
                top_k=q_top_k,
                instruction_type=q_instruction_type,
                excluded_paths=all_excluded_paths
            )
            
            logger.info(f"Query {i+1}/{len(queries)} returned {len(results)} results")

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

        # Log top results for debugging
        logger.info(f"Smart RAG: Final context has {len(relevant_code)} chunks from {len(related_files)} files")
        for i, r in enumerate(relevant_code[:5]):
            path = r["metadata"].get("path", "unknown")
            primary_name = r["metadata"].get("primary_name", "N/A")
            logger.info(f"  Chunk {i+1}: score={r['score']:.3f}, name={primary_name}, path=...{path[-60:]}")

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
        import re

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
        # Use actual changed code for semantic matching (not just context lines)
        for snippet in diff_snippets[:5]:
            # Extract meaningful code from diff - INCLUDE changed lines (+/-), they ARE the code
            lines = []
            for line in snippet.split('\n'):
                stripped = line.strip()
                if not stripped:
                    continue
                # Skip diff headers but keep actual code (including +/- prefixed lines)
                if stripped.startswith(('diff --git', '---', '+++', '@@', 'index ')):
                    continue
                # Remove the +/- prefix but keep the code content
                if stripped.startswith('+') or stripped.startswith('-'):
                    code_line = stripped[1:].strip()
                    if code_line and len(code_line) > 3:  # Skip empty/trivial lines
                        lines.append(code_line)
                elif stripped:
                    lines.append(stripped)
            
            if lines:
                # Join significant lines (function names, method calls, etc.)
                clean_snippet = " ".join(lines[:5])
                if len(clean_snippet) > 15:
                    queries.append((clean_snippet, 1.2, 8, InstructionType.DEPENDENCY))

        # D. Duplication Detection Queries - Weight 1.3 (Highest precision)
        # Find existing implementations of the same functionality elsewhere in the codebase.
        # This catches cross-module logic duplication that other query types miss.
        duplication_queries = self._generate_duplication_queries(
            diff_snippets=diff_snippets,
            changed_files=changed_files
        )
        queries.extend(duplication_queries)
        
        # Log the generated queries for debugging
        logger.debug(f"Decomposed into {len(queries)} queries: {[(q[0][:50], q[1]) for q in queries]}")

        return queries

    def _generate_duplication_queries(
            self,
            diff_snippets: List[str],
            changed_files: List[str]
    ) -> List[tuple]:
        """
        Generate duplication-oriented queries to find existing implementations
        of the same functionality elsewhere in the codebase.

        Extracts:
        - Function/method signatures from diffs
        - Plugin/observer/cron patterns (framework extension points)
        - Class names and target types from config-like patterns
        - API client calls and database table operations
        """
        import re
        import os

        queries = []
        seen_queries = set()

        def _add_query(text: str, weight: float = 1.3, top_k: int = 8):
            """Add query if non-trivial and not duplicate."""
            text = text.strip()
            if len(text) > 15 and text not in seen_queries:
                seen_queries.add(text)
                queries.append((text, weight, top_k, InstructionType.DUPLICATION))

        # --- Extract patterns from diff snippets ---
        all_diff_text = "\n".join(diff_snippets or [])

        # 1. Plugin/interceptor patterns (Magento, WordPress, etc.)
        # Match plugin method names like beforeX, afterX, aroundX
        plugin_methods = re.findall(
            r'(?:public\s+)?function\s+(before|after|around)(\w+)\s*\(',
            all_diff_text
        )
        for prefix, method_name in plugin_methods:
            _add_query(
                f"plugin {prefix} {method_name} implementation",
                weight=1.4, top_k=10
            )
            # Also search for the target method being intercepted
            _add_query(
                f"function {method_name} implementation",
                weight=1.2, top_k=8
            )

        # 2. Observer/event listener patterns
        # Match event names from observer configs or dispatch calls
        event_patterns = re.findall(
            r'(?:event[_\s]*(?:name)?["\s:=]+["\']?)(\w+(?:[_./]\w+)+)',
            all_diff_text, re.IGNORECASE
        )
        for event_name in event_patterns:
            _add_query(
                f"observer event {event_name} handler implementation",
                weight=1.4, top_k=10
            )

        # 3. Cron job patterns — search for similar scheduled tasks
        cron_patterns = re.findall(
            r'(?:DELETE\s+FROM|UPDATE|INSERT\s+INTO)\s+[`"\']?(\w+)',
            all_diff_text, re.IGNORECASE
        )
        for table_name in set(cron_patterns):
            _add_query(
                f"cron job cleanup {table_name} table scheduled task",
                weight=1.3, top_k=8
            )

        # 4. Class/interface extension points
        # Match "implements X", "extends X", "pluginize X"
        extends_matches = re.findall(
            r'(?:extends|implements)\s+([A-Z]\w+(?:\\[A-Z]\w+)*)',
            all_diff_text
        )
        for class_name in set(extends_matches):
            short_name = class_name.split('\\')[-1] if '\\' in class_name else class_name
            _add_query(
                f"class extending {short_name} implementation",
                weight=1.1, top_k=6
            )

        # 5. API client calls (Stripe, payment gateways, external services)
        api_calls = re.findall(
            r'->(\w+(?:->|\.)\w+(?:->|\.)\w+)\s*\(',
            all_diff_text
        )
        for call_chain in set(api_calls):
            if len(call_chain) > 8:  # Skip trivial chains
                _add_query(
                    f"API call {call_chain} usage implementation",
                    weight=1.2, top_k=6
                )

        # 6. Function/method signatures from diffs (general purpose)
        func_signatures = re.findall(
            r'(?:public|private|protected|static)?\s*function\s+(\w+)\s*\([^)]*\)',
            all_diff_text
        )
        for func_name in set(func_signatures):
            # Skip common names that would match too broadly
            if func_name.lower() not in (
                '__construct', '__destruct', 'execute', 'run', 'get', 'set',
                'toarray', 'tostring', 'getdata', 'setdata', 'init', 'setup',
                'before', 'after', 'around'
            ) and len(func_name) > 4:
                _add_query(
                    f"function {func_name} implementation",
                    weight=1.1, top_k=6
                )

        # 7. Config file cross-referencing from file paths
        # If changed files include config files, search for other configs
        # targeting the same extension points
        for file_path in (changed_files or []):
            basename = os.path.basename(file_path)
            if basename in ('di.xml', 'events.xml', 'crontab.xml', 'widget.xml',
                            'webapi.xml', 'routes.xml', 'system.xml'):
                # Search for all other instances of this config type
                _add_query(
                    f"{basename} configuration plugin observer definition",
                    weight=1.3, top_k=10
                )

        # 8. XML config patterns from diffs (di.xml plugins, observers, etc.)
        # Match plugin type declarations in di.xml
        xml_plugin_types = re.findall(
            r'<plugin\s+[^>]*type=["\']([^"\']+)["\']',
            all_diff_text
        )
        for plugin_type in set(xml_plugin_types):
            short_type = plugin_type.split('\\')[-1] if '\\' in plugin_type else plugin_type
            _add_query(
                f"plugin type {short_type} interceptor",
                weight=1.4, top_k=10
            )

        # Match observer class references in events.xml
        xml_observer_classes = re.findall(
            r'<observer\s+[^>]*instance=["\']([^"\']+)["\']',
            all_diff_text
        )
        for obs_class in set(xml_observer_classes):
            short_class = obs_class.split('\\')[-1] if '\\' in obs_class else obs_class
            _add_query(
                f"observer {short_class} event handler",
                weight=1.4, top_k=10
            )

        # Limit total duplication queries to avoid overwhelming the search
        if len(queries) > 8:
            # Prioritize by weight (higher weight = more important)
            queries.sort(key=lambda x: x[1], reverse=True)
            queries = queries[:8]

        logger.info(f"Generated {len(queries)} duplication detection queries")
        return queries

    def _merge_and_rank_results(self, results: List[Dict], min_score_threshold: float = 0.75) -> List[Dict]:
        """
        Deduplicate matches and filter by relevance score with priority-based reranking.

        Uses ScoringConfig for configurable boosting factors:
        1. File path priority (service/controller vs test/config)
        2. Content type priority (functions_classes vs simplified_code)
        3. Semantic name bonus (chunks with extracted function/class names)
        """
        scoring_config = get_scoring_config()
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

        # Apply multi-factor score boosting using ScoringConfig
        for result in unique_results:
            metadata = result.get('metadata', {})
            file_path = metadata.get('path', metadata.get('file_path', ''))
            content_type = metadata.get('content_type', 'fallback')
            semantic_names = metadata.get('semantic_names', [])
            has_docstring = bool(metadata.get('docstring'))
            has_signature = bool(metadata.get('signature'))

            boosted_score, priority = scoring_config.calculate_boosted_score(
                base_score=result['score'],
                file_path=file_path,
                content_type=content_type,
                has_semantic_names=bool(semantic_names),
                has_docstring=has_docstring,
                has_signature=has_signature
            )

            result['score'] = boosted_score
            result['_priority'] = priority
            result['_content_type'] = content_type
            result['_has_semantic_names'] = bool(semantic_names)

        # Filter by threshold
        filtered = [r for r in unique_results if r['score'] >= min_score_threshold]

        # Sort by score descending
        filtered.sort(key=lambda x: x['score'], reverse=True)

        return filtered

