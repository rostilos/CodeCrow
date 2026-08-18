"""Vector storage inspection endpoints.

These endpoints are intentionally bounded and service-internal. They expose
small graph slices and point neighborhoods for the Java web server to proxy
after workspace/project authorization has already been resolved.
"""
import logging
import re
from collections import Counter, defaultdict
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from fastapi import APIRouter, HTTPException, Query
from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue

from ..models import VectorGraphRequest, VectorInspectFilters, VectorNodeRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["inspect"])

PAYLOAD_FIELDS = [
    "workspace", "project", "branch", "path", "commit", "language", "filetype",
    "pr", "pr_number", "pr_branch", "change_type", "content_type", "node_type",
    "start_line", "end_line", "chunk_index", "sub_chunk_index",
    "semantic_names", "primary_name", "parent_class", "full_path", "namespace",
    "extends", "implements", "imports", "calls", "referenced_types", "signature",
    "methods", "properties", "parameters", "return_type", "decorators", "modifiers",
    "variables", "constants", "type_parameters",
    "architecture_context", "architecture_source", "architecture_plugin",
    "architecture_kind", "architecture_source_path", "architecture_group",
    "architecture_key", "architecture_keys", "architecture_paths",
    "architecture_identifiers", "plugin_graph_facts",
    "repository_snapshot", "snapshot_plugin", "snapshot_kind",
    "repository_facts_state", "facts_part", "facts_parts",
    "facts_content_sha256", "plugin_ids", "plugin_fingerprint",
    "plugin_descriptor_fingerprint", "plugin_implementation_fingerprint",
    "repository_generation_manifest", "generation_schema",
    "generation_member_count", "generation_members_sha256",
    "generation_manifest_sha256", "generation_member_sha256",
    "indexed_at", "fragment_of", "text", "_node_content",
]
GRAPH_TEXT_LIMIT = 280
DETAIL_TEXT_LIMIT = 8000
MAX_OVERVIEW_SCAN = 20000
RELATION_FIELDS = ("imports", "calls", "referenced_types", "extends", "implements")
ARCHITECTURE_GRAPH_METADATA_FIELDS = (
    "architecture_plugin", "architecture_kind", "architecture_source_path",
    "architecture_paths",
)
MAX_GRAPH_FACTS_PER_NODE = 40
MAX_ARCHITECTURE_PATHS_PER_BRANCH = 240
MAX_ARCHITECTURE_TARGETS_PER_FACT = 8
MAX_ARCHITECTURE_TARGETS_PER_NODE = 80
DEFINITION_FIELDS = (
    "methods", "properties", "parameters", "return_type", "variables",
    "constants", "type_parameters",
)
MEMBER_DEFINITION_FIELDS = ("methods", "properties", "variables", "constants")
TOKEN_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*(?:[.#:/\\][A-Za-z_$][A-Za-z0-9_$]*)*")
COMMON_RELATION_TOKENS = {
    "a", "an", "and", "any", "array", "as", "async", "await", "bool", "boolean",
    "byte", "char", "class", "const", "def", "dict", "double", "enum", "false",
    "float", "for", "from", "function", "get", "go", "if", "import", "int",
    "integer", "interface", "let", "list", "long", "map", "new", "none", "null",
    "number", "object", "of", "optional", "or", "return", "set", "short", "str",
    "string", "super", "this", "true", "tuple", "undefined", "unknown", "var",
    "void",
}
RELATION_EDGE_CONFIG = {
    "calls": {
        "kind": "calls",
        "indexes": ("member", "type"),
        "weight": 1.85,
        "max_values": 45,
        "max_targets": 5,
        "external_kind": None,
    },
    "extends": {
        "kind": "extends",
        "indexes": ("type",),
        "weight": 2.35,
        "max_values": 12,
        "max_targets": 1,
        "external_kind": "external_type",
    },
    "implements": {
        "kind": "implements",
        "indexes": ("type",),
        "weight": 2.1,
        "max_values": 20,
        "max_targets": 1,
        "external_kind": "external_type",
    },
    "referenced_types": {
        "kind": "referenced_type",
        "indexes": ("type",),
        "weight": 1.7,
        "max_values": 35,
        "max_targets": 2,
        "external_kind": "external_type",
    },
    "imports": {
        "kind": "imports",
        "indexes": ("type",),
        "weight": 1.35,
        "max_values": 35,
        "max_targets": 1,
        "external_kind": "import",
    },
}
UNDIRECTED_EDGE_KINDS = {"file_sequence", "same_symbol", "same_parent"}
TYPELIKE_NODE_KIND_PRIORITY = {
    "class": 0,
    "interface": 0,
    "record": 0,
    "enum": 0,
    "struct": 0,
    "trait": 0,
    "type": 0,
    "constructor": 1,
    "function": 2,
    "method": 3,
}
CALL_NODE_KIND_PRIORITY = {
    "method": 0,
    "function": 0,
    "constructor": 1,
    "class": 2,
    "record": 2,
    "interface": 3,
    "enum": 3,
}


def _get_index_manager():
    from ..api import index_manager
    return index_manager


def _collection_name(index_manager, workspace: str, project: str) -> str:
    return index_manager._get_project_collection_name(workspace, project)


def _collection_exists(index_manager, collection_name: str) -> bool:
    return index_manager._collection_manager.collection_exists(collection_name)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _iter_strings(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        if value:
            yield value
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_strings(item)
        return
    if isinstance(value, (int, float, bool)):
        return
    text = str(value).strip()
    if text:
        yield text


def _first_string(value: Any) -> Optional[str]:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item:
                return item
    return None


def _truncate_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."


def _node_title(payload: Dict[str, Any]) -> str:
    if payload.get("architecture_context"):
        plugin = payload.get("architecture_plugin") or "plugin"
        kind = payload.get("architecture_kind") or "architecture"
        source = payload.get("architecture_source_path")
        return f"{plugin}: {kind}" + (f" — {source}" if source else "")
    if payload.get("repository_facts_state"):
        return "Repository detection facts"
    if payload.get("repository_generation_manifest"):
        return "Repository generation manifest"
    if payload.get("repository_snapshot"):
        return (
            f"{payload.get('snapshot_plugin') or 'plugin'}: "
            f"{payload.get('snapshot_kind') or 'repository snapshot'}"
        )
    primary = _first_string(payload.get("primary_name")) or _first_string(payload.get("semantic_names"))
    if primary:
        return primary
    path = payload.get("path")
    if isinstance(path, str) and path:
        return PurePosixPath(path).name or path
    if payload.get("pr_number"):
        return f"PR #{payload.get('pr_number')}"
    return "Vector point"


def _node_kind(payload: Dict[str, Any]) -> str:
    if payload.get("architecture_context"):
        return "architecture_context"
    if payload.get("architecture_source"):
        return "architecture_source"
    if payload.get("repository_snapshot"):
        return "repository_snapshot"
    if payload.get("repository_facts_state"):
        return "repository_facts"
    if payload.get("repository_generation_manifest"):
        return "repository_generation_manifest"
    if payload.get("pr"):
        return "pr_chunk"
    if payload.get("node_type"):
        return str(payload["node_type"])
    if payload.get("content_type"):
        return str(payload["content_type"])
    return "code_chunk"


def _node_group(payload: Dict[str, Any]) -> str:
    if payload.get("pr_number"):
        return f"PR #{payload.get('pr_number')}"
    if payload.get("branch"):
        return str(payload["branch"])
    if payload.get("language"):
        return str(payload["language"])
    return "unknown"


def _metadata_source(value: Dict[str, Any]) -> Dict[str, Any]:
    metadata = value.get("metadata")
    return metadata if isinstance(metadata, dict) else value


def _plugin_graph_facts(value: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_facts = _metadata_source(value).get("plugin_graph_facts")
    if not isinstance(raw_facts, list):
        return []
    return [fact for fact in raw_facts if isinstance(fact, dict)][:MAX_GRAPH_FACTS_PER_NODE]


def _is_architecture_fact_source(value: Dict[str, Any]) -> bool:
    metadata = _metadata_source(value)
    return (
        value.get("kind") == "architecture_context"
        or metadata.get("architecture_context") is True
        or bool(metadata.get("architecture_kind"))
    )


def _is_repository_path(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not value.startswith("__analysis_architecture__/")
        and not value.startswith("__analysis_state__/")
    )


def _architecture_paths(value: Dict[str, Any], max_paths: int = 240) -> List[str]:
    metadata = _metadata_source(value)
    paths: List[str] = []
    seen: Set[str] = set()

    def add(candidate: Any):
        if not _is_repository_path(candidate):
            return
        path = str(candidate).strip()
        if path not in seen and len(paths) < max_paths:
            seen.add(path)
            paths.append(path)

    add(metadata.get("architecture_source_path"))
    for path in _as_list(metadata.get("architecture_paths")):
        add(path)
    for fact in _plugin_graph_facts(value):
        add(fact.get("path"))
        for path in _as_list(fact.get("related_paths")):
            add(path)
    return paths


def _relation_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
    metadata = {}
    for key in (*RELATION_FIELDS, *DEFINITION_FIELDS, "decorators", "modifiers"):
        values = _as_list(payload.get(key))
        if values:
            metadata[key] = values[:60]
    for key in ARCHITECTURE_GRAPH_METADATA_FIELDS:
        value = payload.get(key)
        if value not in (None, "", []):
            metadata[key] = value[:240] if isinstance(value, list) else value
    facts = _plugin_graph_facts(payload)
    if facts:
        metadata["plugin_graph_facts"] = facts
    return metadata


def _to_graph_node(point: Any, detail: bool = False) -> Dict[str, Any]:
    payload = getattr(point, "payload", None) or {}
    text = payload.get("text") or payload.get("_node_content") or ""
    node = {
        "id": str(getattr(point, "id", "")),
        "title": _node_title(payload),
        "kind": _node_kind(payload),
        "group": _node_group(payload),
        "branch": payload.get("branch"),
        "path": payload.get("path"),
        "language": payload.get("language"),
        "filetype": payload.get("filetype"),
        "prNumber": payload.get("pr_number"),
        "startLine": payload.get("start_line"),
        "endLine": payload.get("end_line"),
        "chunkIndex": payload.get("chunk_index"),
        "subChunkIndex": payload.get("sub_chunk_index"),
        "primaryName": payload.get("primary_name"),
        "semanticNames": _as_list(payload.get("semantic_names")),
        "parentClass": payload.get("parent_class"),
        "fullPath": payload.get("full_path"),
        "namespace": payload.get("namespace"),
        "signature": payload.get("signature"),
        "indexedAt": payload.get("indexed_at"),
        "preview": _truncate_text(text, GRAPH_TEXT_LIMIT),
        "virtual": False,
    }
    relation_metadata = _relation_metadata(payload)
    if relation_metadata:
        node["metadata"] = relation_metadata
    if detail:
        node["text"] = _truncate_text(text, DETAIL_TEXT_LIMIT)
        node["metadata"] = {
            key: value
            for key, value in payload.items()
            if key not in {"text", "_node_content"} and value not in (None, "", [])
        }
    return node


def _build_qdrant_filter(filters: VectorInspectFilters) -> Optional[Filter]:
    must = []
    must_not = []

    if filters.branches:
        if len(filters.branches) == 1:
            must.append(FieldCondition(key="branch", match=MatchValue(value=filters.branches[0])))
        else:
            must.append(FieldCondition(key="branch", match=MatchAny(any=filters.branches)))

    if filters.languages:
        if len(filters.languages) == 1:
            must.append(FieldCondition(key="language", match=MatchValue(value=filters.languages[0])))
        else:
            must.append(FieldCondition(key="language", match=MatchAny(any=filters.languages)))

    if filters.path:
        must.append(FieldCondition(key="path", match=MatchValue(value=filters.path)))

    if filters.pr_number is not None:
        must.append(FieldCondition(key="pr_number", match=MatchValue(value=filters.pr_number)))

    if not filters.include_pr:
        must_not.append(FieldCondition(key="pr", match=MatchValue(value=True)))

    if not must and not must_not:
        return None
    kwargs = {}
    if must:
        kwargs["must"] = must
    if must_not:
        kwargs["must_not"] = must_not
    return Filter(**kwargs)


def _matches_post_filter(payload: Dict[str, Any], filters: VectorInspectFilters) -> bool:
    path = str(payload.get("path") or "")
    if filters.file_query and filters.file_query.lower() not in path.lower():
        return False

    if filters.semantic_query:
        query = filters.semantic_query.lower()
        searchable: List[str] = [
            path,
            str(payload.get("primary_name") or ""),
            str(payload.get("parent_class") or ""),
            str(payload.get("namespace") or ""),
            str(payload.get("signature") or ""),
            str(payload.get("node_type") or ""),
        ]
        for key in ("semantic_names", "extends", "implements", "imports", "calls", "referenced_types"):
            searchable.extend(str(item) for item in _as_list(payload.get(key)))
        if query not in " ".join(searchable).lower():
            return False

    return True


def _scroll_points(
    index_manager,
    collection_name: str,
    filters: VectorInspectFilters,
    limit: int,
    scan_limit: int,
    cursor: Optional[str] = None,
    payload_fields: Optional[List[str]] = None,
) -> Tuple[List[Any], Optional[str], int]:
    qdrant_filter = _build_qdrant_filter(filters)
    offset = cursor or None
    points: List[Any] = []
    scanned = 0
    next_cursor = None

    while len(points) < limit and scanned < scan_limit:
        batch_limit = min(1024, scan_limit - scanned)
        if batch_limit <= 0:
            break

        batch, next_offset = index_manager.qdrant_client.scroll(
            collection_name=collection_name,
            limit=batch_limit,
            offset=offset,
            scroll_filter=qdrant_filter,
            with_payload=payload_fields or PAYLOAD_FIELDS,
            with_vectors=False,
        )
        scanned += len(batch)

        for point in batch:
            payload = getattr(point, "payload", None) or {}
            if _matches_post_filter(payload, filters):
                points.append(point)
                if len(points) >= limit:
                    break

        next_cursor = str(next_offset) if next_offset is not None else None
        if next_offset is None or len(batch) < batch_limit:
            next_cursor = None
            break
        offset = next_offset

    return points, next_cursor, scanned


def _relation_tokens(node: Dict[str, Any]) -> List[str]:
    values = []
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    for key in RELATION_FIELDS:
        values.extend(_as_list(metadata.get(key)))
    return [_normalize_token(v) for v in values if v]


def _relation_lookup_names(nodes: List[Dict[str, Any]], max_names: int = 240) -> Dict[str, List[str]]:
    """Collect bounded relation names by branch for dependency-neighbor lookup."""
    names_by_branch: Dict[str, List[str]] = defaultdict(list)
    seen_by_branch: Dict[str, Set[str]] = defaultdict(set)

    def add_value(branch: str, value: Any):
        for raw_value in _iter_strings(value):
            candidates = [_display_relation_label(raw_value), _normalize_token(raw_value)]
            candidates.extend(TOKEN_RE.findall(raw_value))
            candidates.extend(
                re.split(r"[.#:/\\]", candidate)[-1]
                for candidate in list(candidates)
                if candidate
            )
            for candidate in candidates:
                candidate = candidate.strip()
                if not candidate or candidate.lower() in COMMON_RELATION_TOKENS:
                    continue
                if candidate not in seen_by_branch[branch]:
                    seen_by_branch[branch].add(candidate)
                    names_by_branch[branch].append(candidate)
                    if len(names_by_branch[branch]) >= max_names:
                        return

    for node in nodes:
        branch = str(node.get("branch") or "")
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        for field in RELATION_FIELDS:
            for value in _iter_strings(metadata.get(field)):
                add_value(branch, value)
                if len(names_by_branch[branch]) >= max_names:
                    break
            if len(names_by_branch[branch]) >= max_names:
                break
        if len(names_by_branch[branch]) >= max_names:
            continue
        architecture_facts = (
            _plugin_graph_facts(node)
            if _is_architecture_fact_source(node)
            else []
        )
        for fact in architecture_facts:
            add_value(branch, fact.get("source"))
            if len(names_by_branch[branch]) >= max_names:
                break
            add_value(branch, fact.get("target"))
            if len(names_by_branch[branch]) >= max_names:
                break

    return names_by_branch


def _architecture_lookup_paths(
    nodes: List[Dict[str, Any]],
    max_paths: int = MAX_ARCHITECTURE_PATHS_PER_BRANCH,
) -> Dict[str, List[str]]:
    paths_by_branch: Dict[str, List[str]] = defaultdict(list)
    seen_by_branch: Dict[str, Set[str]] = defaultdict(set)
    for node in nodes:
        if not _is_architecture_fact_source(node):
            continue
        branch = str(node.get("branch") or "")
        for path in _architecture_paths(node, max_paths=max_paths):
            if path in seen_by_branch[branch]:
                continue
            seen_by_branch[branch].add(path)
            paths_by_branch[branch].append(path)
            if len(paths_by_branch[branch]) >= max_paths:
                break
    return paths_by_branch


def _dependency_neighbor_filters(
    nodes: List[Dict[str, Any]],
    filters: VectorInspectFilters,
) -> Iterable[Filter]:
    """Build bounded filters that fetch likely dependency targets for graph edges."""
    def scoped_conditions(branch: str) -> Tuple[List[FieldCondition], List[FieldCondition]]:
        must: List[FieldCondition] = []
        must_not: List[FieldCondition] = []
        if branch:
            must.append(FieldCondition(key="branch", match=MatchValue(value=branch)))
        elif filters.branches:
            branch_match = (
                MatchValue(value=filters.branches[0])
                if len(filters.branches) == 1
                else MatchAny(any=filters.branches)
            )
            must.append(FieldCondition(key="branch", match=branch_match))
        if filters.languages:
            language_match = (
                MatchValue(value=filters.languages[0])
                if len(filters.languages) == 1
                else MatchAny(any=filters.languages)
            )
            must.append(FieldCondition(key="language", match=language_match))
        if filters.pr_number is not None:
            must.append(FieldCondition(
                key="pr_number",
                match=MatchValue(value=filters.pr_number),
            ))
        if not filters.include_pr:
            must_not.append(FieldCondition(key="pr", match=MatchValue(value=True)))
        return must, must_not

    for branch, paths in _architecture_lookup_paths(nodes).items():
        if filters.branches and branch and branch not in filters.branches:
            continue
        base_must, base_must_not = scoped_conditions(branch)
        for start in range(0, len(paths), 60):
            yield Filter(
                must=[
                    *base_must,
                    FieldCondition(
                        key="path",
                        match=MatchAny(any=paths[start:start + 60]),
                    ),
                ],
                must_not=base_must_not or None,
            )

    for branch, names in _relation_lookup_names(nodes).items():
        if not names:
            continue
        if filters.branches and branch and branch not in filters.branches:
            continue

        base_must, base_must_not = scoped_conditions(branch)

        for start in range(0, len(names), 60):
            batch = names[start:start + 60]
            for key, values in (
                ("primary_name", batch),
                ("semantic_names", batch),
                ("methods", batch[:40]),
            ):
                yield Filter(
                    must=[
                        *base_must,
                        FieldCondition(key=key, match=MatchAny(any=values)),
                    ],
                    must_not=base_must_not or None,
                )


def _hydrate_dependency_neighbors(
    index_manager,
    collection_name: str,
    nodes: List[Dict[str, Any]],
    filters: VectorInspectFilters,
    existing_ids: Set[str],
    limit: int,
) -> List[Any]:
    """Fetch a bounded set of target definitions referenced by the visible graph slice."""
    neighbors: Dict[str, Any] = {}
    if limit <= 0:
        return []

    per_query_limit = max(20, min(120, limit))
    for neighbor_filter in _dependency_neighbor_filters(nodes, filters):
        for candidate in _scroll_neighbor_candidates(
            index_manager,
            collection_name,
            neighbor_filter,
            per_query_limit,
        ):
            candidate_id = str(getattr(candidate, "id", ""))
            if not candidate_id or candidate_id in existing_ids or candidate_id in neighbors:
                continue
            payload = getattr(candidate, "payload", None) or {}
            if _matches_post_filter(payload, filters):
                neighbors[candidate_id] = candidate
                if len(neighbors) >= limit:
                    return list(neighbors.values())

    return list(neighbors.values())


def _normalize_token(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    return token.split(".")[-1].split("/")[-1].split("#")[-1]


def _normalize_key(value: Any) -> str:
    token = str(value or "").strip().strip("`'\";:,()[]{}<>")
    if not token:
        return ""
    token = token.replace("\\", ".").replace("/", ".").replace("#", ".").replace(":", ".")
    token = re.sub(r"\s+", "", token).strip(".").lower()
    if not token or token in COMMON_RELATION_TOKENS:
        return ""
    if len(token) == 1:
        return ""
    return token


def _candidate_tokens(value: Any) -> List[str]:
    seen: Set[str] = set()
    candidates: List[str] = []

    def add(raw: Any):
        key = _normalize_key(raw)
        if key and key not in seen:
            seen.add(key)
            candidates.append(key)

    for raw_value in _iter_strings(value):
        add(raw_value)
        for match in TOKEN_RE.findall(raw_value):
            lowered = match.lower()
            if lowered in COMMON_RELATION_TOKENS:
                continue
            add(match)
            simple = re.split(r"[.#:/\\]", match)[-1]
            add(simple)

    return candidates


def _display_relation_label(value: Any) -> str:
    for item in _iter_strings(value):
        text = " ".join(item.split()).strip("`'\";")
        if text:
            return text[:180]
    return ""


def _node_type_values(node: Dict[str, Any]) -> List[Any]:
    values: List[Any] = [
        node.get("primaryName"),
        node.get("fullPath"),
        *node.get("semanticNames", []),
    ]
    namespace = node.get("namespace")
    primary = node.get("primaryName")
    if namespace and primary:
        values.append(f"{namespace}.{primary}")
    path = node.get("path")
    if isinstance(path, str) and path:
        values.append(PurePosixPath(path).stem)
    return [value for value in values if value]


def _node_member_values(node: Dict[str, Any]) -> List[Any]:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    values: List[Any] = []
    for key in MEMBER_DEFINITION_FIELDS:
        values.extend(_as_list(metadata.get(key)))
    return values


def _add_tokens(index: Dict[str, List[Dict[str, Any]]], values: Iterable[Any], node: Dict[str, Any]):
    for value in values:
        for token in _candidate_tokens(value):
            bucket = index[token]
            if not bucket or bucket[-1]["id"] != node["id"]:
                bucket.append(node)


def _relation_values(node: Dict[str, Any], field: str) -> List[str]:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return list(_iter_strings(metadata.get(field)))


def _lookup_relation_targets(
    source: Dict[str, Any],
    relation_value: Any,
    indexes: Dict[str, Dict[str, List[Dict[str, Any]]]],
    index_names: Tuple[str, ...],
    relation_kind: str,
    max_targets: int,
) -> List[Dict[str, Any]]:
    source_id = source["id"]
    source_branch = source.get("branch")
    selected: Dict[str, Dict[str, Any]] = {}

    for token in _candidate_tokens(relation_value)[:10]:
        if len(selected) >= max_targets:
            break
        candidates: List[Dict[str, Any]] = []
        for index_name in index_names:
            candidates.extend(indexes.get(index_name, {}).get(token, []))

        if not candidates:
            continue
        unique_candidates = {candidate["id"]: candidate for candidate in candidates}
        if len(unique_candidates) >= max(30, max_targets * 6):
            continue

        ordered = list(unique_candidates.values())
        same_branch = [
            candidate
            for candidate in ordered
            if source_branch and candidate.get("branch") == source_branch
        ]
        scoped = same_branch or ordered
        scoped = sorted(
            scoped,
            key=lambda candidate: _relation_target_rank(source, candidate, relation_kind),
        )
        for candidate in scoped:
            if candidate["id"] == source_id or candidate["id"] in selected:
                continue
            selected[candidate["id"]] = candidate
            if len(selected) >= max_targets:
                break

    return list(selected.values())


def _relation_target_rank(source: Dict[str, Any], target: Dict[str, Any], relation_kind: str) -> Tuple[int, int, int, int, str]:
    kind = str(target.get("kind") or "")
    source_path = source.get("path")
    target_path = target.get("path")
    same_path_penalty = 1 if relation_kind == "imports" and source_path and source_path == target_path else 0
    if relation_kind == "calls":
        kind_rank = CALL_NODE_KIND_PRIORITY.get(kind, 8)
    else:
        kind_rank = TYPELIKE_NODE_KIND_PRIORITY.get(kind, 8)
    line = target.get("startLine") if isinstance(target.get("startLine"), int) else 10**9
    title = str(target.get("title") or target.get("primaryName") or target.get("id") or "")
    return same_path_penalty, kind_rank, line, len(title), title


def _external_relation_node(
    relation_kind: str,
    relation_value: Any,
    source: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    label = _display_relation_label(relation_value)
    if not label:
        return None
    token = _candidate_tokens(label)
    key = token[0] if token else _normalize_key(label)
    if not key:
        return None

    title = _normalize_token(label) or label
    node = _virtual_node(
        _safe_synthetic_id(relation_kind, source.get("branch") or "", key),
        title,
        relation_kind,
        "external dependencies",
        branch=source.get("branch"),
        path=source.get("path"),
        language=source.get("language"),
        metric_count=0,
    )
    node["preview"] = f"Referenced by indexed metadata: {label}"
    node["metadata"].update({
        "external": True,
        "reference": label,
    })
    return node


def _safe_synthetic_id(prefix: str, *parts: Any) -> str:
    return prefix + "::" + "::".join(str(part or "").replace("::", "/") for part in parts)


def _file_title(path: str) -> str:
    if not path:
        return "Unknown file"
    return PurePosixPath(path).name or path


def _virtual_node(
    node_id: str,
    title: str,
    kind: str,
    group: str,
    *,
    branch: Optional[str] = None,
    path: Optional[str] = None,
    language: Optional[str] = None,
    metric_count: int = 0,
) -> Dict[str, Any]:
    return {
        "id": node_id,
        "title": title,
        "kind": kind,
        "group": group,
        "branch": branch,
        "path": path,
        "language": language,
        "preview": f"{metric_count} indexed point{'s' if metric_count != 1 else ''}",
        "virtual": True,
        "metricCount": metric_count,
        "metadata": {
            "virtual": True,
            "point_count": metric_count,
            "relationship": kind,
        },
    }


def _is_default_graph_node(node: Dict[str, Any]) -> bool:
    if node.get("virtual"):
        return False
    if node.get("kind") in {
        "architecture_context",
        "architecture_source",
        "repository_snapshot",
        "repository_facts",
        "repository_generation_manifest",
    }:
        return False
    return _is_repository_path(node.get("path"))


def _plugin_fact_label(fact: Dict[str, Any]) -> str:
    parts = [
        _display_relation_label(fact.get("source")),
        _display_relation_label(fact.get("relation")),
        _display_relation_label(fact.get("target")),
    ]
    return _truncate_text(" ".join(part for part in parts if part), 180)


def _architecture_evidence_node(source: Dict[str, Any], path: str) -> Dict[str, Any]:
    branch = str(source.get("branch") or "")
    node = _virtual_node(
        _safe_synthetic_id("file", branch, path),
        _file_title(path),
        "file",
        branch or "architecture evidence",
        branch=branch,
        path=path,
        language=source.get("language"),
    )
    node["preview"] = "Repository path referenced by plugin architecture metadata"
    node["metadata"]["architecture_evidence"] = True
    return node


def _build_graph(
    nodes: List[Dict[str, Any]],
    max_edges: int = 1200,
    max_virtual_nodes: int = 240,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    edges: Dict[str, Dict[str, Any]] = {}
    virtual_nodes: Dict[str, Dict[str, Any]] = {}
    file_virtual_ids: Dict[Tuple[str, str], str] = {}

    def add_edge(source: str, target: str, kind: str, weight: float = 1.0, token: Optional[str] = None):
        if source == target or len(edges) >= max_edges:
            return
        if kind in UNDIRECTED_EDGE_KINDS:
            left, right = sorted([source, target])
        else:
            left, right = source, target
        key = f"{left}->{right}:{kind}"
        if key in edges:
            edges[key]["weight"] = min(8.0, float(edges[key].get("weight") or 1.0) + weight * 0.2)
            if token:
                tokens = edges[key].setdefault("tokens", [])
                if token not in tokens and len(tokens) < 8:
                    tokens.append(token)
            return
        edge = {
            "id": key,
            "source": source,
            "target": target,
            "kind": kind,
            "weight": weight,
        }
        if token:
            edge["tokens"] = [token]
        edges[key] = edge

    def add_virtual(node: Dict[str, Any]) -> Optional[str]:
        existing = virtual_nodes.get(node["id"])
        if existing:
            if int(node.get("metricCount") or 0) > int(existing.get("metricCount") or 0):
                metadata = {
                    **(existing.get("metadata") or {}),
                    **(node.get("metadata") or {}),
                }
                existing.update(node)
                existing["metadata"] = metadata
            return node["id"]
        if len(virtual_nodes) >= max_virtual_nodes:
            return None
        virtual_nodes[node["id"]] = node
        return node["id"]

    by_file: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    by_name: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_parent: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    type_index: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    member_index: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for node in nodes:
        branch = str(node.get("branch") or "")
        path = str(node.get("path") or "")
        if path:
            by_file[(branch, path)].append(node)

        names = [node.get("primaryName"), *node.get("semanticNames", [])]
        for raw_name in names:
            name = _normalize_token(raw_name)
            if name:
                by_name[name].append(node)

        parent = str(node.get("parentClass") or "")
        if parent:
            by_parent[(branch, parent)].append(node)

        _add_tokens(type_index, _node_type_values(node), node)
        _add_tokens(member_index, _node_member_values(node), node)

    # Reserve one real repository-path node per architecture boundary before
    # structural file/symbol nodes consume the bounded virtual-node budget.
    for node in nodes:
        if not _is_architecture_fact_source(node) or not _plugin_graph_facts(node):
            continue
        evidence_paths = _architecture_paths(node, max_paths=1)
        if not evidence_paths:
            continue
        path = evidence_paths[0]
        evidence_id = add_virtual(_architecture_evidence_node(node, path))
        if evidence_id:
            file_virtual_ids[(str(node.get("branch") or ""), path)] = evidence_id

    for (branch, path), file_nodes in by_file.items():
        language = _first_string([node.get("language") or node.get("filetype") for node in file_nodes])
        file_id = add_virtual(_virtual_node(
            _safe_synthetic_id("file", branch, path),
            _file_title(path),
            "file",
            branch or language or "file",
            branch=branch,
            path=path,
            language=language,
            metric_count=len(file_nodes),
        ))
        if file_id:
            file_virtual_ids[(branch, path)] = file_id
        ordered = sorted(
            file_nodes,
            key=lambda n: (
                n.get("startLine") if n.get("startLine") is not None else 10**9,
                n.get("chunkIndex") if n.get("chunkIndex") is not None else 10**9,
                n["id"],
            ),
        )
        if file_id:
            for node in ordered[:80]:
                add_edge(file_id, node["id"], "file_contains", 1.35)
        for current, following in zip(ordered, ordered[1:]):
            add_edge(current["id"], following["id"], "file_sequence", 0.35)

    for name, related_nodes in by_name.items():
        if 1 < len(related_nodes) <= 80:
            symbol_id = add_virtual(_virtual_node(
                _safe_synthetic_id("symbol", name),
                name,
                "symbol",
                "symbols",
                branch=_first_string([node.get("branch") for node in related_nodes]),
                language=_first_string([node.get("language") or node.get("filetype") for node in related_nodes]),
                metric_count=len(related_nodes),
            ))
            if symbol_id:
                for node in related_nodes[:40]:
                    add_edge(symbol_id, node["id"], "same_symbol", 1.1)

    for (branch, parent), related_nodes in by_parent.items():
        if 1 < len(related_nodes) <= 100:
            parent_id = add_virtual(_virtual_node(
                _safe_synthetic_id("parent", branch, parent),
                parent,
                "parent_class",
                "parent classes",
                branch=branch,
                language=_first_string([node.get("language") or node.get("filetype") for node in related_nodes]),
                metric_count=len(related_nodes),
            ))
            if parent_id:
                for node in related_nodes[:55]:
                    add_edge(parent_id, node["id"], "same_parent", 0.95)

    indexes = {
        "type": type_index,
        "member": member_index,
    }

    for node in nodes:
        if not _is_architecture_fact_source(node):
            continue
        facts = _plugin_graph_facts(node)
        if not facts:
            continue

        branch = str(node.get("branch") or "")
        linked_targets: Set[str] = set()
        for fact in facts:
            label = _plugin_fact_label(fact)
            fact_targets: Set[str] = set()

            def link(target_id: Optional[str]) -> bool:
                if not target_id:
                    return False
                if target_id in fact_targets:
                    add_edge(node["id"], target_id, "metadata_reference", 2.05, token=label)
                    return True
                if (
                    len(fact_targets) >= MAX_ARCHITECTURE_TARGETS_PER_FACT
                    or (
                        target_id not in linked_targets
                        and len(linked_targets) >= MAX_ARCHITECTURE_TARGETS_PER_NODE
                    )
                ):
                    return False
                fact_targets.add(target_id)
                linked_targets.add(target_id)
                add_edge(node["id"], target_id, "metadata_reference", 2.05, token=label)
                return True

            for endpoint in (fact.get("source"), fact.get("target")):
                for target in _lookup_relation_targets(
                    node,
                    endpoint,
                    indexes,
                    ("type", "member"),
                    relation_kind="metadata_reference",
                    max_targets=2,
                ):
                    if _is_default_graph_node(target):
                        link(target["id"])

            endpoint_tokens = set(_candidate_tokens([
                fact.get("source"),
                fact.get("target"),
            ]))
            fact_paths = _architecture_paths(
                {"plugin_graph_facts": [fact]},
                max_paths=8,
            ) or _architecture_paths(node, max_paths=8)
            for path in fact_paths:
                path_candidates = [
                    candidate
                    for candidate in by_file.get((branch, path), [])
                    if candidate["id"] != node["id"] and _is_default_graph_node(candidate)
                ]
                path_candidates.sort(key=lambda candidate: (
                    0 if endpoint_tokens.intersection(_candidate_tokens([
                        *_node_type_values(candidate),
                        *_node_member_values(candidate),
                    ])) else 1,
                    candidate.get("startLine")
                    if isinstance(candidate.get("startLine"), int) else 10**9,
                    candidate["id"],
                ))
                for candidate in path_candidates[:2]:
                    if not link(candidate["id"]):
                        break

                file_id = file_virtual_ids.get((branch, path))
                if not file_id:
                    file_id = add_virtual(_architecture_evidence_node(node, path))
                    if file_id:
                        file_virtual_ids[(branch, path)] = file_id
                link(file_id)

    for node in nodes:
        for field, config in RELATION_EDGE_CONFIG.items():
            edge_kind = str(config["kind"])
            index_names = config["indexes"]
            max_values = int(config["max_values"])
            max_targets = int(config["max_targets"])
            weight = float(config["weight"])
            external_kind = config.get("external_kind")

            for relation_value in _relation_values(node, field)[:max_values]:
                label = _display_relation_label(relation_value)
                targets = _lookup_relation_targets(
                    node,
                    relation_value,
                    indexes,
                    index_names,
                    relation_kind=edge_kind,
                    max_targets=max_targets,
                )
                if targets:
                    for target in targets:
                        add_edge(node["id"], target["id"], edge_kind, weight, token=label)
                    continue

                if external_kind:
                    external = _external_relation_node(str(external_kind), relation_value, node)
                    if external:
                        external_id = add_virtual(external)
                        if external_id:
                            add_edge(node["id"], external_id, edge_kind, weight * 0.72, token=label)

    return [*virtual_nodes.values(), *nodes], list(edges.values())


def _top(counter: Counter, limit: int) -> List[Dict[str, Any]]:
    return [{"value": key, "count": count} for key, count in counter.most_common(limit) if key]


@router.get("/inspect/{workspace}/{project}/overview")
def vector_overview(
    workspace: str,
    project: str,
    sample_limit: int = Query(default=10000, ge=100, le=MAX_OVERVIEW_SCAN),
):
    """Return a bounded overview of indexed vector metadata for one project."""
    index_manager = _get_index_manager()
    collection_name = _collection_name(index_manager, workspace, project)

    if not _collection_exists(index_manager, collection_name):
        return {
            "available": False,
            "workspace": workspace,
            "project": project,
            "collection": collection_name,
            "totalPoints": 0,
            "sampledPoints": 0,
            "sampled": False,
            "branches": [],
            "languages": [],
            "files": [],
            "prNumbers": [],
            "semanticNames": [],
        }

    try:
        total_points = getattr(index_manager.qdrant_client.get_collection(collection_name), "points_count", 0) or 0
        filters = VectorInspectFilters()
        points, _, scanned = _scroll_points(
            index_manager=index_manager,
            collection_name=collection_name,
            filters=filters,
            limit=sample_limit,
            scan_limit=sample_limit,
            payload_fields=[
                "branch", "path", "language", "filetype", "pr_number",
                "primary_name", "semantic_names", "node_type", "content_type",
            ],
        )

        branch_counts: Counter = Counter()
        language_counts: Counter = Counter()
        file_counts: Counter = Counter()
        pr_numbers: Counter = Counter()
        semantic_counts: Counter = Counter()

        for point in points:
            payload = getattr(point, "payload", None) or {}
            branch_counts.update([payload.get("branch")])
            language_counts.update([payload.get("language") or payload.get("filetype")])
            file_counts.update([payload.get("path")])
            if payload.get("pr_number"):
                pr_numbers.update([payload.get("pr_number")])
            if payload.get("primary_name"):
                semantic_counts.update([payload.get("primary_name")])
            for name in _as_list(payload.get("semantic_names"))[:5]:
                semantic_counts.update([name])

        return {
            "available": True,
            "workspace": workspace,
            "project": project,
            "collection": collection_name,
            "totalPoints": total_points,
            "sampledPoints": len(points),
            "scannedPoints": scanned,
            "sampled": total_points > len(points),
            "branches": _top(branch_counts, 80),
            "languages": _top(language_counts, 40),
            "files": _top(file_counts, 120),
            "prNumbers": _top(pr_numbers, 80),
            "semanticNames": _top(semantic_counts, 120),
        }
    except Exception as e:
        logger.error("Error building vector overview for %s/%s: %s", workspace, project, e)
        raise HTTPException(status_code=500, detail="Vector overview failed")


@router.post("/inspect/{workspace}/{project}/graph")
def vector_graph(workspace: str, project: str, request: VectorGraphRequest):
    """Return a bounded graph slice for one project collection."""
    index_manager = _get_index_manager()
    collection_name = _collection_name(index_manager, workspace, project)

    if not _collection_exists(index_manager, collection_name):
        return {
            "available": False,
            "nodes": [],
            "edges": [],
            "nextCursor": None,
            "scannedPoints": 0,
            "limit": request.limit,
        }

    try:
        points, next_cursor, scanned = _scroll_points(
            index_manager=index_manager,
            collection_name=collection_name,
            filters=request.filters,
            limit=request.limit,
            scan_limit=request.scan_limit,
            cursor=request.cursor,
        )
        point_nodes = [_to_graph_node(point, detail=False) for point in points]
        existing_ids = {node["id"] for node in point_nodes}
        dependency_points = _hydrate_dependency_neighbors(
            index_manager=index_manager,
            collection_name=collection_name,
            nodes=point_nodes,
            filters=request.filters,
            existing_ids=existing_ids,
            limit=min(1200, max(120, request.limit // 4)),
        )
        dependency_nodes = [_to_graph_node(point, detail=False) for point in dependency_points]
        nodes, edges = _build_graph(
            [*point_nodes, *dependency_nodes],
            max_edges=min(25000, max(1200, (request.limit + len(dependency_nodes)) * 5)),
            max_virtual_nodes=min(2500, max(240, (request.limit + len(dependency_nodes)) // 2)),
        )
        return {
            "available": True,
            "nodes": nodes,
            "edges": edges,
            "nextCursor": next_cursor,
            "scannedPoints": scanned,
            "limit": request.limit,
        }
    except Exception as e:
        logger.error("Error building vector graph for %s/%s: %s", workspace, project, e)
        raise HTTPException(status_code=500, detail="Vector graph failed")


def _scroll_neighbor_candidates(
    index_manager,
    collection_name: str,
    scroll_filter: Optional[Filter],
    limit: int,
) -> List[Any]:
    points, _ = index_manager.qdrant_client.scroll(
        collection_name=collection_name,
        limit=limit,
        scroll_filter=scroll_filter,
        with_payload=PAYLOAD_FIELDS,
        with_vectors=False,
    )
    return points


def _neighbor_filters_for(payload: Dict[str, Any]) -> Iterable[Optional[Filter]]:
    branch = payload.get("branch")
    path = payload.get("path")
    base_must = []
    if branch:
        base_must.append(FieldCondition(key="branch", match=MatchValue(value=branch)))

    if path:
        yield Filter(must=[*base_must, FieldCondition(key="path", match=MatchValue(value=path))])

    architecture_paths = _architecture_paths(payload, max_paths=120)
    for start in range(0, len(architecture_paths), 60):
        yield Filter(must=[
            *base_must,
            FieldCondition(
                key="path",
                match=MatchAny(any=architecture_paths[start:start + 60]),
            ),
        ])

    names = []
    if payload.get("primary_name"):
        names.append(payload["primary_name"])
    names.extend(_as_list(payload.get("semantic_names")))
    names = [str(name) for name in names if name]
    if names:
        yield Filter(must=[*base_must, FieldCondition(key="primary_name", match=MatchAny(any=names[:20]))])

    if payload.get("parent_class"):
        yield Filter(must=[*base_must, FieldCondition(key="parent_class", match=MatchValue(value=payload["parent_class"]))])

    if payload.get("namespace"):
        yield Filter(must=[*base_must, FieldCondition(key="namespace", match=MatchValue(value=payload["namespace"]))])

    lookup_node = {
        "branch": str(branch or ""),
        "kind": _node_kind(payload),
        "metadata": _relation_metadata(payload),
    }
    relation_names = _relation_lookup_names([lookup_node], max_names=60).get(
        str(branch or ""),
        [],
    )

    if relation_names:
        relation_names = relation_names[:60]
        yield Filter(must=[*base_must, FieldCondition(key="primary_name", match=MatchAny(any=relation_names))])
        yield Filter(must=[*base_must, FieldCondition(key="semantic_names", match=MatchAny(any=relation_names))])
        yield Filter(must=[*base_must, FieldCondition(key="methods", match=MatchAny(any=relation_names[:40]))])


@router.post("/inspect/{workspace}/{project}/points/{point_id}")
def vector_point(workspace: str, project: str, point_id: str, request: VectorNodeRequest):
    """Return one point plus a bounded metadata-derived neighborhood."""
    index_manager = _get_index_manager()
    collection_name = _collection_name(index_manager, workspace, project)

    if not _collection_exists(index_manager, collection_name):
        raise HTTPException(status_code=404, detail="Vector collection not found")

    try:
        points = index_manager.qdrant_client.retrieve(
            collection_name=collection_name,
            ids=[point_id],
            with_payload=PAYLOAD_FIELDS,
            with_vectors=False,
        )
        if not points:
            raise HTTPException(status_code=404, detail="Vector point not found")

        point = points[0]
        payload = getattr(point, "payload", None) or {}
        node = _to_graph_node(point, detail=True)

        neighbor_by_id: Dict[str, Any] = {}
        per_query_limit = max(20, min(request.neighbor_limit, 80))
        for neighbor_filter in _neighbor_filters_for(payload):
            for candidate in _scroll_neighbor_candidates(index_manager, collection_name, neighbor_filter, per_query_limit):
                candidate_id = str(getattr(candidate, "id", ""))
                if candidate_id and candidate_id != point_id:
                    candidate_payload = getattr(candidate, "payload", None) or {}
                    if _matches_post_filter(candidate_payload, request.filters):
                        neighbor_by_id[candidate_id] = candidate
                if len(neighbor_by_id) >= request.neighbor_limit:
                    break
            if len(neighbor_by_id) >= request.neighbor_limit:
                break

        neighbors = [_to_graph_node(candidate, detail=False) for candidate in neighbor_by_id.values()]
        graph_nodes, graph_edges = _build_graph([node, *neighbors], max_edges=260, max_virtual_nodes=80)
        returned_neighbor_ids = {neighbor["id"] for neighbor in neighbors}
        virtual_neighbors = [
            graph_node
            for graph_node in graph_nodes
            if graph_node["id"] != node["id"] and graph_node["id"] not in returned_neighbor_ids
        ]
        return {
            "node": node,
            "neighbors": [*virtual_neighbors, *neighbors],
            "edges": graph_edges,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error loading vector point %s for %s/%s: %s", point_id, workspace, project, e)
        raise HTTPException(status_code=500, detail="Vector point lookup failed")
