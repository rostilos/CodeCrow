"""
Deterministic context retrieval module for RAG query service.

Retrieves code context using metadata-based (non-semantic) queries against
tree-sitter extracted metadata: identifiers, parent classes, namespaces, imports.
"""
import re
from typing import Dict, List, Optional
import logging

from qdrant_client.http.models import Filter, FieldCondition, MatchValue, MatchAny, MatchText

from .base import RAGQueryBase

logger = logging.getLogger(__name__)


class DeterministicContextMixin:
    """Deterministic (metadata-based) context retrieval for RAGQueryService.

    Uses tree-sitter metadata already extracted during indexing:
    - semantic_names, primary_name → find definitions
    - parent_class → find sibling methods in same class
    - namespace → find related code in same package
    - imports, extends → find dependency definitions

    Same input always produces same output (no embedding randomness).
    """

    def get_deterministic_context(
            self: RAGQueryBase,
            workspace: str,
            project: str,
            branches: List[str],
            file_paths: List[str],
            limit_per_file: int = 10,
            pr_number: Optional[int] = None,
            pr_changed_files: Optional[List[str]] = None,
            additional_identifiers: Optional[List[str]] = None
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

        # ── Build branch filter ──
        target_branch = branches[0] if branches else None

        base_branch_condition = (
            FieldCondition(key="branch", match=MatchValue(value=branches[0]))
            if len(branches) == 1
            else FieldCondition(key="branch", match=MatchAny(any=branches))
        )

        if pr_number:
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

        # ── Tracking state ──
        all_chunks = []
        changed_files_chunks = {}
        related_definitions = {}
        class_context = {}
        namespace_context = {}

        identifiers_to_find = set()
        parent_classes = set()
        namespaces = set()
        imports_raw = set()
        extends_raw = set()

        changed_file_paths = set()
        seen_texts = set()
        target_branch_paths = set()

        # ========== STEP 1: Get chunks from changed files ==========
        for file_path in file_paths:
            try:
                chunks_for_file = self._query_changed_file(
                    collection_name, branch_filter, file_path, limit_per_file,
                    branches, target_branch, seen_texts, target_branch_paths,
                    changed_file_paths, identifiers_to_find, parent_classes,
                    namespaces, imports_raw, extends_raw, all_chunks
                )
                changed_files_chunks[file_path] = chunks_for_file
            except Exception as e:
                logger.warning(f"Error querying file '{file_path}': {e}")

        logger.info(f"Step 1: {len(all_chunks)} chunks from changed files. "
                   f"Extracted: {len(identifiers_to_find)} identifiers, "
                   f"{len(parent_classes)} parent_classes, {len(namespaces)} namespaces, "
                   f"{len(imports_raw)} imports, {len(extends_raw)} extends")

        # ── Inject enrichment-supplied identifiers (extends, implements, calls) ──
        # These come from the orchestrator's Java-side AST parse and guarantee
        # that parent types, interfaces, and called functions are looked up even
        # if they don't appear in the changed files' Qdrant payloads.
        if additional_identifiers:
            pre_count = len(identifiers_to_find | imports_raw | extends_raw)
            for name in additional_identifiers:
                name = name.strip()
                if name and len(name) > 1:
                    identifiers_to_find.add(name)
            post_count = len(identifiers_to_find | imports_raw | extends_raw)
            logger.info(f"Enrichment injection: {post_count - pre_count} new identifiers "
                       f"from {len(additional_identifiers)} additional_identifiers")

        # ========== STEP 2: Find definitions by primary_name ==========
        all_to_find = identifiers_to_find | imports_raw | extends_raw
        if all_to_find:
            self._query_definitions(
                collection_name, branch_filter, all_to_find,
                branches, target_branch, target_branch_paths,
                changed_file_paths, seen_texts, all_chunks, related_definitions
            )

        # ========== STEP 2b: Transitive parent type resolution ==========
        # Extract extends/implements/parent_class from the definitions found
        # in Step 2, then do one more hop to find THEIR parent types.
        # This ensures the full inheritance chain is visible (depth=2).
        transitive_parents = set()
        for def_name, def_chunks in related_definitions.items():
            for chunk in def_chunks:
                meta = chunk.get("metadata", {})
                if isinstance(meta.get("extends"), list):
                    transitive_parents.update(meta["extends"])
                if meta.get("parent_class"):
                    transitive_parents.add(meta["parent_class"])

        # Remove names already looked up to avoid redundant queries
        transitive_parents -= all_to_find
        transitive_parents -= changed_file_paths  # Skip changed file paths
        transitive_parents = {p for p in transitive_parents if p and len(p) > 1}

        if transitive_parents:
            self._query_transitive_parents(
                collection_name, branch_filter, transitive_parents,
                branches, target_branch, target_branch_paths,
                changed_file_paths, seen_texts, all_chunks, related_definitions
            )

        # ========== STEP 3: Find other methods in same parent_class ==========
        if parent_classes:
            self._query_class_context(
                collection_name, branch_filter, parent_classes,
                branches, target_branch, target_branch_paths,
                changed_file_paths, seen_texts, all_chunks, class_context
            )

        # ========== STEP 4: Find related code in same namespace ==========
        if namespaces:
            self._query_namespace_context(
                collection_name, branch_filter, namespaces,
                branches, target_branch, target_branch_paths,
                changed_file_paths, seen_texts, all_chunks, namespace_context
            )

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

    # ── Internal helpers ──

    def _apply_branch_priority(
            self,
            points: list,
            target: str,
            branches: List[str],
            target_branch_paths: set
    ) -> list:
        """Filter points to prioritize: PR-indexed > target branch > base branch."""
        if not points:
            return points

        by_path = {}
        for p in points:
            path = p.payload.get("path", "")
            if path not in by_path:
                by_path[path] = []
            by_path[path].append(p)

        result = []
        for path, path_points in by_path.items():
            pr_points = [p for p in path_points if p.payload.get("pr") is True]
            if pr_points:
                result.extend(pr_points)
                continue

            branch_points = [p for p in path_points if p.payload.get("pr") is not True]
            if not target or len(branches) == 1:
                result.extend(branch_points)
                continue

            has_target = any(p.payload.get("branch") == target for p in branch_points)
            if has_target:
                result.extend([p for p in branch_points if p.payload.get("branch") == target])
            elif path not in target_branch_paths:
                result.extend(branch_points)

        return result

    def _query_changed_file(
            self, collection_name, branch_filter, file_path, limit_per_file,
            branches, target_branch, seen_texts, target_branch_paths,
            changed_file_paths, identifiers_to_find, parent_classes,
            namespaces, imports_raw, extends_raw, all_chunks
    ) -> List[Dict]:
        """Query chunks for a single changed file and extract metadata."""
        normalized_path = file_path.lstrip("/")
        filename = normalized_path.rsplit("/", 1)[-1] if "/" in normalized_path else normalized_path

        # Try exact path match
        results, _ = self.qdrant_client.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(must=[
                branch_filter,
                FieldCondition(key="path", match=MatchValue(value=normalized_path))
            ]),
            limit=limit_per_file * len(branches),
            with_payload=True,
            with_vectors=False
        )

        # Fallback: try with filename only
        if not results:
            results, _ = self.qdrant_client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(must=[
                    branch_filter,
                    FieldCondition(key="path", match=MatchText(text=filename))
                ]),
                limit=limit_per_file * len(branches),
                with_payload=True,
                with_vectors=False
            )

        # Apply branch priority
        if target_branch and len(branches) > 1:
            has_target = any(p.payload.get("branch") == target_branch for p in results)
            if has_target:
                results = [p for p in results if p.payload.get("branch") == target_branch]
                logger.debug(f"Branch priority: keeping target branch '{target_branch}' for {normalized_path}")

        results = results[:limit_per_file]

        chunks_for_file = []
        for point in results:
            payload = point.payload
            text = payload.get("text", payload.get("_node_content", ""))

            if text in seen_texts:
                continue
            seen_texts.add(text)

            if payload.get("branch") == target_branch:
                target_branch_paths.add(payload.get("path", ""))

            chunk = {
                "text": text,
                "metadata": {k: v for k, v in payload.items() if k not in ("text", "_node_content")},
                "_match_type": "changed_file",
                "_match_priority": 1,
                "_matched_on": file_path
            }
            chunks_for_file.append(chunk)
            all_chunks.append(chunk)
            changed_file_paths.add(payload.get("path", ""))

            # Extract tree-sitter metadata for step 2-4
            # NOTE: We deliberately do NOT add semantic_names or primary_name
            # to identifiers_to_find. Those are the file's OWN definitions
            # (e.g., __construct, getAliases, apply, _toHtml) and looking
            # them up via primary_name MatchAny finds hundreds of unrelated
            # files with the same boilerplate method names. Actual external
            # dependencies come from imports, extends, and enrichment.
            if payload.get("parent_class"):
                parent_classes.add(payload["parent_class"])
            if payload.get("namespace"):
                namespaces.add(payload["namespace"])

            if isinstance(payload.get("imports"), list):
                for imp in payload["imports"]:
                    if isinstance(imp, str):
                        cleaned = imp.strip().rstrip(";").strip()
                        parts = re.split(r'[\\./::\s]+', cleaned)
                        for part in reversed(parts):
                            part = part.strip()
                            if part and len(part) > 1 and not part.startswith(('"', "'", '{', '*')):
                                imports_raw.add(part)
                                break

            if isinstance(payload.get("extends"), list):
                extends_raw.update(payload["extends"])
            if payload.get("parent_class"):
                extends_raw.add(payload["parent_class"])

        return chunks_for_file

    def _query_definitions(
            self, collection_name, branch_filter, all_to_find,
            branches, target_branch, target_branch_paths,
            changed_file_paths, seen_texts, all_chunks, related_definitions
    ):
        """STEP 2: Find definitions by primary_name."""
        try:
            batch = list(all_to_find)[:self.config.max_identifiers_per_query]
            results, _ = self.qdrant_client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(must=[
                    branch_filter,
                    FieldCondition(key="primary_name", match=MatchAny(any=batch))
                ]),
                limit=200 * len(branches),
                with_payload=True,
                with_vectors=False
            )

            results = self._apply_branch_priority(results, target_branch, branches, target_branch_paths)

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
                    "_match_type": "definition",
                    "_match_priority": 2,
                    "_matched_on": primary_name
                }
                all_chunks.append(chunk)

                if primary_name not in related_definitions:
                    related_definitions[primary_name] = []
                related_definitions[primary_name].append(chunk)

            logger.info(f"Step 2: Found {len(related_definitions)} definitions by primary_name")

        except Exception as e:
            logger.warning(f"Error in primary_name query: {e}")

    def _query_transitive_parents(
            self, collection_name, branch_filter, transitive_parents,
            branches, target_branch, target_branch_paths,
            changed_file_paths, seen_texts, all_chunks, related_definitions
    ):
        """STEP 2b: Second-hop lookup for parent types of definitions found in Step 2.

        Uses a single batched MatchAny query to resolve all transitive parents
        in one Qdrant round-trip instead of N sequential scrolls.
        Results are capped at 50 to control context budget.
        """
        try:
            batch = list(transitive_parents)[:50]
            results, _ = self.qdrant_client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(must=[
                    branch_filter,
                    FieldCondition(key="primary_name", match=MatchAny(any=batch))
                ]),
                limit=50 * len(branches),
                with_payload=True,
                with_vectors=False
            )

            results = self._apply_branch_priority(results, target_branch, branches, target_branch_paths)

            added = 0
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
                    "_match_type": "transitive_parent",
                    "_match_priority": 2,
                    "_matched_on": primary_name
                }
                all_chunks.append(chunk)

                if primary_name not in related_definitions:
                    related_definitions[primary_name] = []
                related_definitions[primary_name].append(chunk)
                added += 1

            logger.info(f"Step 2b: Found {added} transitive parent definitions "
                       f"from {len(transitive_parents)} parent types")

        except Exception as e:
            logger.warning(f"Error in transitive parent query: {e}")

    def _query_class_context(
            self, collection_name, branch_filter, parent_classes,
            branches, target_branch, target_branch_paths,
            changed_file_paths, seen_texts, all_chunks, class_context
    ):
        """STEP 3: Find other methods in same parent_class."""
        try:
            batch = list(parent_classes)[:self.config.max_parent_classes_per_query]
            results, _ = self.qdrant_client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(must=[
                    branch_filter,
                    FieldCondition(key="parent_class", match=MatchAny(any=batch))
                ]),
                limit=100 * len(branches),
                with_payload=True,
                with_vectors=False
            )

            results = self._apply_branch_priority(results, target_branch, branches, target_branch_paths)

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
                    "_match_type": "class_context",
                    "_match_priority": 3,
                    "_matched_on": parent_class
                }
                all_chunks.append(chunk)

                if parent_class not in class_context:
                    class_context[parent_class] = []
                class_context[parent_class].append(chunk)

            logger.info(f"Step 3: Found {sum(len(v) for v in class_context.values())} class context chunks")

        except Exception as e:
            logger.warning(f"Error in parent_class query: {e}")

    def _query_namespace_context(
            self, collection_name, branch_filter, namespaces,
            branches, target_branch, target_branch_paths,
            changed_file_paths, seen_texts, all_chunks, namespace_context
    ):
        """STEP 4: Find related code in same namespace."""
        try:
            batch = list(namespaces)[:self.config.max_namespaces_per_query]
            results, _ = self.qdrant_client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(must=[
                    branch_filter,
                    FieldCondition(key="namespace", match=MatchAny(any=batch))
                ]),
                limit=30 * len(branches),
                with_payload=True,
                with_vectors=False
            )

            results = self._apply_branch_priority(results, target_branch, branches, target_branch_paths)

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
                    "_match_type": "namespace_context",
                    "_match_priority": 4,
                    "_matched_on": namespace
                }
                all_chunks.append(chunk)

                if namespace not in namespace_context:
                    namespace_context[namespace] = []
                namespace_context[namespace].append(chunk)

            logger.info(f"Step 4: Found {sum(len(v) for v in namespace_context.values())} namespace context chunks")

        except Exception as e:
            logger.warning(f"Error in namespace query: {e}")
