"""
Deterministic context retrieval module for RAG query service.

Retrieves code context using metadata-based (non-semantic) queries against
tree-sitter extracted metadata: identifiers, parent classes, namespaces, imports.
"""
import os
import re
from typing import Dict, List, Optional
import logging

from qdrant_client.http.models import Filter, FieldCondition, MatchValue, MatchAny

from .base import RAGQueryBase
from ..core.pr_overlay_manifest import PR_OVERLAY_MANIFEST_PAYLOAD_KEY
from ..core.repository_overlay import IncrementalIndexPreconditionError
from rag_pipeline.utils.path_identity import (
    normalize_repository_path,
    repository_path_suffix_candidates,
    repository_paths_match,
)

logger = logging.getLogger(__name__)

COMMON_RELATION_IDENTIFIERS = {
    "and", "array", "bool", "boolean", "call", "class", "clone", "count",
    "dict", "false", "float", "get", "hash", "int", "list", "long", "map",
    "new", "none", "null", "object", "print", "return", "run", "set",
    "str", "string", "this", "true", "void",
}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


ARCHITECTURE_SCROLL_PAGE_SIZE = max(
    32,
    _env_int("RAG_DETERMINISTIC_ARCHITECTURE_SCROLL_PAGE_SIZE", 256),
)
ARCHITECTURE_MAX_MATCHING_POINTS = max(
    ARCHITECTURE_SCROLL_PAGE_SIZE,
    _env_int("RAG_DETERMINISTIC_ARCHITECTURE_MAX_MATCHING_POINTS", 5000),
)


def _simple_relation_identifier(value: object) -> Optional[str]:
    """Normalize an indexed relation value into a primary-name lookup token."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip().rstrip(";").strip().strip("'\"")
    if not cleaned:
        return None
    parts = re.split(r'[\\./::\s]+', cleaned)
    for part in reversed(parts):
        part = part.strip().strip("{}()[]<>")
        if (
            len(part) > 1
            and not part.startswith(("{", "*", "$"))
            and part.lower() not in COMMON_RELATION_IDENTIFIERS
        ):
            return part
    return None


def _point_sort_key(point) -> tuple:
    payload = point.payload or {}
    return (
        str(payload.get("path", "")),
        int(payload.get("start_line", 0) or 0),
        int(payload.get("end_line", 0) or 0),
        str(payload.get("primary_name", "")),
        str(getattr(point, "id", "")),
    )


def _failure(stage: str, exception: Exception, path: Optional[str] = None) -> Dict[str, str]:
    result = {
        "stage": stage,
        "error_type": type(exception).__name__,
        "message": str(exception)[:500],
    }
    if path:
        result["path"] = path
    return result


def _graph_fact_paths(fact: object) -> set[str]:
    if not isinstance(fact, dict):
        return set()
    paths = set()
    path = fact.get("path")
    if isinstance(path, str) and path:
        paths.add(path)
    related_paths = fact.get("related_paths")
    if isinstance(related_paths, (list, tuple)):
        paths.update(
            value for value in related_paths
            if isinstance(value, str) and value
        )
    return paths


def _graph_fact_retrieval_identifiers(fact: object) -> set[str]:
    """
    Read neutral plugin-nominated exact lookup identifiers.

    Plugins may attach ``retrievalIdentifier:<stable-name>`` attributes to a
    graph fact. RAG treats only the values as primary-name lookup candidates;
    it does not interpret a plugin-specific attribute name.
    """
    if not isinstance(fact, dict):
        return set()
    attributes = fact.get("attributes")
    if not isinstance(attributes, dict):
        return set()
    return {
        value
        for key, value in attributes.items()
        if (
            isinstance(key, str)
            and key.startswith("retrievalIdentifier:")
            and isinstance(value, str)
            and value.strip()
        )
    }


def _focused_architecture_payload(
        payload: Dict,
        requested_paths: set[str],
) -> Optional[Dict]:
    """Project a compacted graph node onto facts touching the requested paths.

    Architecture storage intentionally packs multiple graph facts into one
    Qdrant point. A metadata match therefore identifies a candidate point, not
    permission to forward every co-located fact into the review prompt.
    """
    facts = payload.get("plugin_graph_facts")
    if not isinstance(facts, list):
        return dict(payload)

    focused_facts = [
        fact for fact in facts
        if _graph_fact_paths(fact).intersection(requested_paths)
    ]
    if not focused_facts:
        return None

    focused_paths = sorted({
        path for fact in focused_facts for path in _graph_fact_paths(fact)
    })
    focused_identifiers = sorted({
        value
        for fact in focused_facts
        for value in (
            fact.get("source"),
            fact.get("target"),
            *_graph_fact_retrieval_identifiers(fact),
        )
        if isinstance(value, str) and value
    })
    packet_keys = sorted({
        str(fact.get("packetKey"))
        for fact in focused_facts
        if fact.get("packetKey")
    })

    focused = dict(payload)
    focused["plugin_graph_facts"] = focused_facts
    focused["architecture_paths"] = focused_paths
    focused["architecture_identifiers"] = focused_identifiers
    if packet_keys:
        focused["architecture_keys"] = packet_keys
    return focused


def _render_focused_architecture_text(payload: Dict, matched_paths: List[str]) -> str:
    """Render only exact selected graph facts, preserving their provenance."""
    facts = payload.get("plugin_graph_facts")
    if not isinstance(facts, list):
        return str(payload.get("text", payload.get("_node_content", "")))

    lines = [
        "Deterministic repository architecture context",
        f"Plugin: {payload.get('architecture_plugin', 'unknown')}",
        f"Kind: {payload.get('architecture_kind', 'unknown')}",
        "Matched paths: " + ", ".join(matched_paths),
        "Facts:",
    ]
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        attributes = fact.get("attributes")
        attribute_text = ""
        if isinstance(attributes, dict) and attributes:
            attribute_text = " {" + ", ".join(
                f"{key}={attributes[key]}" for key in sorted(attributes)
            ) + "}"
        packet_key = fact.get("packetKey")
        packet_text = f"Packet {packet_key}: " if packet_key else ""
        lines.append(
            f"- {packet_text}[{fact.get('kind', 'relation')}] "
            f"{fact.get('source', '')} {fact.get('relation', '')} "
            f"{fact.get('target', '')} "
            f"({fact.get('path', 'unknown')}:{fact.get('line', 1)})"
            f"{attribute_text}"
        )
    return "\n".join(lines)


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
            additional_identifiers: Optional[List[str]] = None,
            expected_revisions: Optional[Dict[str, str]] = None,
            pr_source_revision: Optional[str] = None,
            pr_base_revision: Optional[str] = None,
            pr_base_generation_manifest_sha256: Optional[str] = None,
            pr_generation_fingerprint: Optional[str] = None,
            collection_target: Optional[str] = None,
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
        if pr_generation_fingerprint and not all((
            pr_number,
            pr_source_revision,
            pr_base_revision,
            pr_base_generation_manifest_sha256,
        )):
            raise IncrementalIndexPreconditionError(
                "PR generation fingerprint requires PR number and complete "
                "source/base generation identity"
            )
        if pr_generation_fingerprint and len([
            branch for branch in branches if branch
        ]) != 1:
            raise IncrementalIndexPreconditionError(
                "revision-bound deterministic context requires exactly one "
                "authoritative branch"
            )

        collection_name = (
            collection_target
            or self._get_project_collection_name(workspace, project)
        )

        if not self._collection_or_alias_exists(collection_name):
            logger.warning(f"Collection {collection_name} does not exist")
            return {"chunks": [], "changed_files": {}, "related_definitions": {},
                    "class_context": {}, "namespace_context": {},
                    "_metadata": {
                        "error": "collection_not_found",
                        "retrieval_state": "unavailable",
                        "failures": [{
                            "stage": "collection",
                            "error_type": "CollectionNotFound",
                            "message": f"Collection {collection_name} does not exist",
                        }],
                    }}

        file_paths = sorted({path.lstrip("/") for path in file_paths if path})
        pr_changed_path_set = {
            normalize_repository_path(path)
            for path in (pr_changed_files or [])
            if normalize_repository_path(path)
        }
        branches = list(dict.fromkeys(branch for branch in branches if branch))
        self._observe_branches(collection_name, branches)
        logger.info(f"Deterministic context: files={file_paths[:5]}, branches={branches}")

        # ── Build branch filter ──
        target_branch = branches[0] if branches else None

        repository_branch_filters = []
        for branch in branches:
            conditions = [
                FieldCondition(
                    key="branch",
                    match=MatchValue(value=branch),
                ),
            ]
            if expected_revisions and branch in expected_revisions:
                conditions.append(FieldCondition(
                    key="commit",
                    match=MatchValue(value=expected_revisions[branch]),
                ))
            repository_branch_filters.append(Filter(must=conditions))
        base_branch_condition = (
            repository_branch_filters[0]
            if len(repository_branch_filters) == 1
            else Filter(should=repository_branch_filters)
        )

        if pr_number:
            pr_conditions = [
                FieldCondition(key="pr", match=MatchValue(value=True)),
                FieldCondition(
                    key="pr_number",
                    match=MatchValue(value=pr_number),
                ),
            ]
            if pr_generation_fingerprint:
                pr_conditions.extend([
                    FieldCondition(
                        key="pr_source_revision",
                        match=MatchValue(value=pr_source_revision),
                    ),
                    FieldCondition(
                        key="pr_base_revision",
                        match=MatchValue(value=pr_base_revision),
                    ),
                    FieldCondition(
                        key="pr_base_generation_manifest_sha256",
                        match=MatchValue(
                            value=pr_base_generation_manifest_sha256
                        ),
                    ),
                    FieldCondition(
                        key="pr_generation_fingerprint",
                        match=MatchValue(value=pr_generation_fingerprint),
                    ),
                ])
            base_conditions = [base_branch_condition]
            base_exclusions = (
                [
                    FieldCondition(
                        key="path",
                        match=MatchAny(any=sorted(pr_changed_path_set)),
                    ),
                ]
                if pr_changed_path_set
                else []
            )
            branch_filter = Filter(should=[
                Filter(
                    must=base_conditions,
                    must_not=base_exclusions,
                ),
                Filter(
                    must=pr_conditions,
                    must_not=[
                        FieldCondition(
                            key=PR_OVERLAY_MANIFEST_PAYLOAD_KEY,
                            match=MatchValue(value=True),
                        ),
                    ],
                ),
            ])
            logger.info(f"Deterministic hybrid mode: also searching PR-indexed data (pr_number={pr_number})")
        else:
            branch_filter = base_branch_condition

        # ── Tracking state ──
        all_chunks = []
        changed_files_chunks = {}
        architecture_context = {}
        architecture_related = {}
        related_definitions = {}
        class_context = {}
        namespace_context = {}
        failures = []
        file_status = {}

        identifiers_to_find = set()
        parent_classes = set()
        namespaces = set()
        imports_raw = set()
        extends_raw = set()

        # The request is the authority for invalidating materialized branch
        # context.  Do not rely on finding a PR-indexed chunk: deleted files and
        # architecture-only files legitimately have no replacement code chunk.
        changed_file_paths = set(pr_changed_path_set)
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
                file_status[file_path] = "hit" if chunks_for_file else "miss"
            except Exception as e:
                logger.warning(f"Error querying file '{file_path}': {e}")
                file_status[file_path] = "error"
                failures.append(_failure("changed_file", e, file_path))

        logger.info(f"Step 1: {len(all_chunks)} chunks from changed files. "
                   f"Extracted: {len(identifiers_to_find)} identifiers, "
                   f"{len(parent_classes)} parent_classes, {len(namespaces)} namespaces, "
                   f"{len(imports_raw)} imports, {len(extends_raw)} extends")

        # ========== STEP 1b: Expand exact repository architecture edges ==========
        # Framework plugins index bounded architecture packets keyed by every path
        # participating in a relation. This query is metadata-only and introduces
        # no model call or similarity-dependent behavior.
        architecture_retrieval = {}
        if file_paths:
            try:
                architecture_retrieval = self._query_architecture_context(
                    collection_name,
                    branch_filter,
                    file_paths,
                    limit_per_file,
                    branches,
                    target_branch,
                    target_branch_paths,
                    changed_file_paths,
                    seen_texts,
                    all_chunks,
                    architecture_context,
                    architecture_related,
                    identifiers_to_find,
                )
                if architecture_retrieval.get("truncated"):
                    failures.append({
                        "stage": "architecture_context",
                        "error_type": "ResultLimit",
                        "message": (
                            "Exact architecture retrieval reached its configured "
                            "matching-point limit; context is explicitly partial"
                        ),
                    })
            except Exception as exception:
                failures.append(_failure("architecture_context", exception))

        # ── Inject enrichment-supplied identifiers (extends, implements, calls) ──
        # These come from the orchestrator's Java-side AST parse and guarantee
        # that parent types, interfaces, and called functions are looked up even
        # if they don't appear in the changed files' Qdrant payloads.
        if additional_identifiers:
            pre_count = len(identifiers_to_find | imports_raw | extends_raw)
            for name in sorted(additional_identifiers):
                name = name.strip()
                if name and len(name) > 1:
                    identifiers_to_find.add(name)
            post_count = len(identifiers_to_find | imports_raw | extends_raw)
            logger.info(f"Enrichment injection: {post_count - pre_count} new identifiers "
                       f"from {len(additional_identifiers)} additional_identifiers")

        # ========== STEP 2: Find definitions by primary_name ==========
        all_to_find = identifiers_to_find | imports_raw | extends_raw
        if all_to_find:
            try:
                self._query_definitions(
                    collection_name, branch_filter, all_to_find,
                    branches, target_branch, target_branch_paths,
                    changed_file_paths, seen_texts, all_chunks, related_definitions
                )
            except Exception as exception:
                failures.append(_failure("definitions", exception))

        # ========== STEP 2b: Transitive parent type resolution ==========
        # Extract extends/implements/parent_class from the definitions found
        # in Step 2, then do one more hop to find THEIR parent types.
        # This ensures the full inheritance chain is visible (depth=2).
        transitive_parents = set()
        for def_name, def_chunks in sorted(related_definitions.items()):
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
            try:
                self._query_transitive_parents(
                    collection_name, branch_filter, transitive_parents,
                    branches, target_branch, target_branch_paths,
                    changed_file_paths, seen_texts, all_chunks, related_definitions
                )
            except Exception as exception:
                failures.append(_failure("transitive_parents", exception))

        # ========== STEP 3: Find other methods in same parent_class ==========
        if parent_classes:
            try:
                self._query_class_context(
                    collection_name, branch_filter, parent_classes,
                    branches, target_branch, target_branch_paths,
                    changed_file_paths, seen_texts, all_chunks, class_context
                )
            except Exception as exception:
                failures.append(_failure("class_context", exception))

        # ========== STEP 4: Find related code in same namespace ==========
        if namespaces:
            try:
                self._query_namespace_context(
                    collection_name, branch_filter, namespaces,
                    branches, target_branch, target_branch_paths,
                    changed_file_paths, seen_texts, all_chunks, namespace_context
                )
            except Exception as exception:
                failures.append(_failure("namespace_context", exception))

        logger.info(f"Deterministic context complete: {len(all_chunks)} total chunks "
                   f"(changed: {sum(len(v) for v in changed_files_chunks.values())}, "
                   f"definitions: {sum(len(v) for v in related_definitions.values())}, "
                   f"class_ctx: {sum(len(v) for v in class_context.values())}, "
                   f"ns_ctx: {sum(len(v) for v in namespace_context.values())})")

        for chunk in all_chunks:
            metadata = chunk.get("metadata") or {}
            if metadata.get("pr") is True:
                if pr_generation_fingerprint and (
                    metadata.get("pr_generation_fingerprint")
                    != pr_generation_fingerprint
                    or metadata.get("pr_source_revision")
                    != pr_source_revision
                    or metadata.get("pr_base_revision") != pr_base_revision
                    or metadata.get(
                        "pr_base_generation_manifest_sha256"
                    ) != pr_base_generation_manifest_sha256
                ):
                    raise IncrementalIndexPreconditionError(
                        "deterministic retrieval returned a PR point outside "
                        "the requested overlay generation"
                    )
                continue
            expected_revision = (
                expected_revisions.get(metadata.get("branch"))
                if expected_revisions
                else None
            )
            if (
                expected_revision is not None
                and metadata.get("commit") != expected_revision
            ):
                raise IncrementalIndexPreconditionError(
                    "deterministic retrieval returned a repository point "
                    "outside the requested immutable revision"
                )

        return {
            "chunks": all_chunks,
            "changed_files": changed_files_chunks,
            "architecture_context": architecture_context,
            "architecture_related": architecture_related,
            "related_definitions": related_definitions,
            "class_context": class_context,
            "namespace_context": namespace_context,
            "_metadata": {
                "branches_searched": branches,
                "target_branch": target_branch,
                "files_requested": file_paths,
                "identifiers_extracted": sorted(identifiers_to_find)[:30],
                "parent_classes_found": sorted(parent_classes),
                "namespaces_found": sorted(namespaces),
                "imports_extracted": sorted(imports_raw)[:30],
                "extends_extracted": sorted(extends_raw)[:20],
                "architecture_packets_found": sum(len(value) for value in architecture_context.values()),
                "architecture_related_found": sum(len(value) for value in architecture_related.values()),
                "architecture_retrieval": architecture_retrieval,
                "target_branch_paths_found": len(target_branch_paths),
                "file_status": file_status,
                "retrieval_state": (
                    "complete" if not failures else "partial" if all_chunks else "failed"
                ),
                "failures": failures,
            }
        }

    # ── Internal helpers ──

    def _query_architecture_context(
            self, collection_name, branch_filter, file_paths, limit_per_file,
            branches, target_branch, target_branch_paths, changed_file_paths,
            seen_texts, all_chunks, architecture_context, architecture_related,
            identifiers_to_find
    ) -> Dict[str, object]:
        """Retrieve exact framework relations and their concrete source files."""
        packet_points = {}
        batch_size = 64
        packet_scan_truncated = False
        for offset in range(0, len(file_paths), batch_size):
            path_batch = file_paths[offset:offset + batch_size]
            remaining = ARCHITECTURE_MAX_MATCHING_POINTS - len(packet_points)
            if remaining <= 0:
                packet_scan_truncated = True
                break
            results, truncated = self._scroll_bounded(
                collection_name,
                Filter(must=[
                    branch_filter,
                    FieldCondition(
                        key="architecture_paths",
                        match=MatchAny(any=path_batch),
                    ),
                ]),
                remaining,
            )
            packet_scan_truncated = packet_scan_truncated or truncated
            for point in results:
                packet_points[str(point.id)] = point

        selected_packets = self._apply_branch_priority(
            list(packet_points.values()),
            target_branch,
            branches,
            target_branch_paths,
        )
        related_paths = set()
        preferred_identifiers_by_path = {}
        requested_paths = set(file_paths)
        for point in selected_packets:
            payload = _focused_architecture_payload(
                point.payload or {},
                requested_paths,
            )
            if payload is None:
                continue
            packet_paths = {
                path for path in payload.get("architecture_paths", [])
                if isinstance(path, str) and path
            }
            # A branch architecture packet is a materialized view of every
            # source path listed in its payload.  If the PR replaces any one
            # of those paths, the packet is no longer evidence for the reviewed
            # revision.  PR-scoped architecture packets, when present, are the
            # only safe replacement.
            if (
                not payload.get("pr")
                and packet_paths.intersection(changed_file_paths)
            ):
                continue
            matched_paths = sorted(packet_paths & requested_paths)
            text = _render_focused_architecture_text(payload, matched_paths)
            if not text or text in seen_texts:
                continue
            seen_texts.add(text)
            related_paths.update(packet_paths - requested_paths)
            for fact in payload.get("plugin_graph_facts", []) or []:
                if not isinstance(fact, dict):
                    continue
                identifiers = {
                    identifier.casefold()
                    for identifier in _graph_fact_retrieval_identifiers(fact)
                    if identifier
                }
                if not identifiers:
                    continue
                for path in _graph_fact_paths(fact) - requested_paths:
                    preferred_identifiers_by_path.setdefault(
                        path,
                        set(),
                    ).update(identifiers)
            for identifier in payload.get("architecture_identifiers", []):
                name = _simple_relation_identifier(identifier)
                if name:
                    identifiers_to_find.add(name)
            key = str(payload.get("architecture_key", "architecture"))
            chunk = {
                "text": text,
                "metadata": {
                    key: value for key, value in payload.items()
                    if key not in ("text", "_node_content")
                },
                "_match_type": "architecture_relation",
                "_match_priority": 0,
                "_matched_on": ",".join(matched_paths),
            }
            all_chunks.append(chunk)
            architecture_context.setdefault(key, []).append(chunk)

        related_paths = sorted(
            path for path in related_paths
            if not path.startswith("__analysis_architecture__/")
        )
        if not related_paths:
            return {
                "packet_candidates": len(packet_points),
                "packet_chunks": sum(len(value) for value in architecture_context.values()),
                "related_candidates": 0,
                "related_chunks": 0,
                "truncated": packet_scan_truncated,
            }

        related_points = {}
        related_scan_truncated = False
        for offset in range(0, len(related_paths), batch_size):
            path_batch = related_paths[offset:offset + batch_size]
            remaining = ARCHITECTURE_MAX_MATCHING_POINTS - len(related_points)
            if remaining <= 0:
                related_scan_truncated = True
                break
            results, truncated = self._scroll_bounded(
                collection_name,
                Filter(must=[
                    branch_filter,
                    FieldCondition(key="path", match=MatchAny(any=path_batch)),
                ]),
                remaining,
            )
            related_scan_truncated = related_scan_truncated or truncated
            for point in results:
                related_points[str(point.id)] = point

        selected_related = self._apply_branch_priority(
            list(related_points.values()),
            target_branch,
            branches,
            target_branch_paths,
        )
        selected_related = sorted(
            selected_related,
            key=lambda point: (
                str((point.payload or {}).get("path", "")),
                (
                    0
                    if str(
                        (point.payload or {}).get("primary_name", "")
                    ).casefold()
                    in preferred_identifiers_by_path.get(
                        str((point.payload or {}).get("path", "")),
                        set(),
                    )
                    else 1
                ),
                _point_sort_key(point),
            ),
        )
        per_path_counts = {}
        for point in selected_related:
            payload = point.payload or {}
            path = payload.get("path", "")
            if not path or per_path_counts.get(path, 0) >= limit_per_file:
                continue
            text = payload.get("text", payload.get("_node_content", ""))
            if not text or text in seen_texts:
                continue
            seen_texts.add(text)
            per_path_counts[path] = per_path_counts.get(path, 0) + 1
            chunk = {
                "text": text,
                "metadata": {
                    key: value for key, value in payload.items()
                    if key not in ("text", "_node_content")
                },
                "_match_type": "architecture_related",
                "_match_priority": 1,
                "_matched_on": path,
            }
            all_chunks.append(chunk)
            architecture_related.setdefault(path, []).append(chunk)

        logger.info(
            "Architecture expansion: %s relation chunks, %s related code chunks from %s paths",
            sum(len(value) for value in architecture_context.values()),
            sum(len(value) for value in architecture_related.values()),
            len(related_paths),
        )
        return {
            "packet_candidates": len(packet_points),
            "packet_chunks": sum(len(value) for value in architecture_context.values()),
            "related_candidates": len(related_points),
            "related_chunks": sum(len(value) for value in architecture_related.values()),
            "truncated": packet_scan_truncated or related_scan_truncated,
        }

    def _scroll_bounded(
            self,
            collection_name: str,
            scroll_filter,
            max_points: int,
    ) -> tuple[list, bool]:
        """Paginate an exact Qdrant lookup and report an explicit safety cap."""
        points = []
        offset = None
        seen_offsets = set()

        while len(points) < max_points:
            page_limit = min(
                ARCHITECTURE_SCROLL_PAGE_SIZE,
                max_points - len(points),
            )
            kwargs = {
                "collection_name": collection_name,
                "scroll_filter": scroll_filter,
                "limit": page_limit,
                "with_payload": True,
                "with_vectors": False,
            }
            if offset is not None:
                kwargs["offset"] = offset
            page, next_offset = self.qdrant_client.scroll(**kwargs)
            points.extend(self._accept_stored_points(page))
            if next_offset is None:
                return points, False
            offset_key = repr(next_offset)
            if offset_key in seen_offsets:
                logger.warning(
                    "Qdrant exact scroll repeated offset %s; returning partial context",
                    offset_key,
                )
                return points, True
            seen_offsets.add(offset_key)
            offset = next_offset

        return points, offset is not None

    def _apply_branch_priority(
            self,
            points: list,
            target: str,
            branches: List[str],
            target_branch_paths: set
    ) -> list:
        """Filter points to prioritize: PR-indexed > target branch > base branch."""
        points = self._accept_stored_points(points)
        if not points:
            return points

        by_path = {}
        for p in sorted(points, key=_point_sort_key):
            path = p.payload.get("path", "")
            if path not in by_path:
                by_path[path] = []
            by_path[path].append(p)

        result = []
        for path, path_points in sorted(by_path.items()):
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

        return sorted(result, key=_point_sort_key)

    def _query_changed_file(
            self, collection_name, branch_filter, file_path, limit_per_file,
            branches, target_branch, seen_texts, target_branch_paths,
            changed_file_paths, identifiers_to_find, parent_classes,
            namespaces, imports_raw, extends_raw, all_chunks
    ) -> List[Dict]:
        """Query chunks for a single changed file and extract metadata."""
        normalized_path = normalize_repository_path(file_path)

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

        # If the caller included an archive/checkout root, try only exact
        # multi-segment suffixes. A basename query is unsafe in framework
        # repositories where hundreds of modules may contain ``etc/di.xml``.
        if not results:
            suffix_candidates = repository_path_suffix_candidates(
                normalized_path
            )[1:]
            if suffix_candidates:
                results, _ = self.qdrant_client.scroll(
                    collection_name=collection_name,
                    scroll_filter=Filter(must=[
                        branch_filter,
                        FieldCondition(
                            key="path",
                            match=MatchAny(any=suffix_candidates),
                        ),
                    ]),
                    limit=limit_per_file * len(branches),
                    with_payload=True,
                    with_vectors=False,
                )

        results = [
            point
            for point in results
            if repository_paths_match(
                point.payload.get("path", ""),
                normalized_path,
            )
        ]

        results = sorted(
            self._accept_stored_points(results),
            key=_point_sort_key,
        )

        # A revision-bound PR request's changed-path manifest is authoritative.
        # Modified paths may have an exact overlay member; deleted and
        # architecture-only paths legitimately may not. In either case, never
        # fall back to the pre-PR target-branch source for that path.
        if any(
            repository_paths_match(normalized_path, changed_path)
            for changed_path in changed_file_paths
        ):
            results = [
                point
                for point in results
                if (point.payload or {}).get("pr") is True
            ]

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
                    name = _simple_relation_identifier(imp)
                    if name:
                        imports_raw.add(name)

            if isinstance(payload.get("extends"), list):
                for value in payload["extends"]:
                    name = _simple_relation_identifier(value)
                    if name:
                        extends_raw.add(name)
            if isinstance(payload.get("implements"), list):
                for value in payload["implements"]:
                    name = _simple_relation_identifier(value)
                    if name:
                        extends_raw.add(name)
            if isinstance(payload.get("referenced_types"), list):
                for type_name in payload["referenced_types"][:30]:
                    name = _simple_relation_identifier(type_name)
                    if name:
                        extends_raw.add(name)
            if isinstance(payload.get("calls"), list):
                for call_name in payload["calls"][:30]:
                    name = _simple_relation_identifier(call_name)
                    if name:
                        identifiers_to_find.add(name)
            if payload.get("parent_class"):
                extends_raw.add(payload["parent_class"])
            if isinstance(payload.get("plugin_graph_facts"), list):
                for fact in payload["plugin_graph_facts"]:
                    if not isinstance(fact, dict):
                        continue
                    for value in (fact.get("source"), fact.get("target")):
                        name = _simple_relation_identifier(value)
                        if name:
                            identifiers_to_find.add(name)

        return chunks_for_file

    def _query_definitions(
            self, collection_name, branch_filter, all_to_find,
            branches, target_branch, target_branch_paths,
            changed_file_paths, seen_texts, all_chunks, related_definitions
    ):
        """STEP 2: Find definitions by primary_name."""
        try:
            batch = sorted(all_to_find)[:self.config.max_identifiers_per_query]
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
            raise

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
            batch = sorted(transitive_parents)[:50]
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
            raise

    def _query_class_context(
            self, collection_name, branch_filter, parent_classes,
            branches, target_branch, target_branch_paths,
            changed_file_paths, seen_texts, all_chunks, class_context
    ):
        """STEP 3: Find other methods in same parent_class."""
        try:
            batch = sorted(parent_classes)[:self.config.max_parent_classes_per_query]
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
            raise

    def _query_namespace_context(
            self, collection_name, branch_filter, namespaces,
            branches, target_branch, target_branch_paths,
            changed_file_paths, seen_texts, all_chunks, namespace_context
    ):
        """STEP 4: Find related code in same namespace."""
        try:
            batch = sorted(namespaces)[:self.config.max_namespaces_per_query]
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
            raise
