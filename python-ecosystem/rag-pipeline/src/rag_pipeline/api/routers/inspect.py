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
    "indexed_at", "fragment_of", "text", "_node_content",
]
GRAPH_TEXT_LIMIT = 280
DETAIL_TEXT_LIMIT = 8000
MAX_OVERVIEW_SCAN = 20000
RELATION_FIELDS = ("imports", "calls", "referenced_types", "extends", "implements")
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


def _relation_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
    metadata = {}
    for key in (*RELATION_FIELDS, *DEFINITION_FIELDS, "decorators", "modifiers"):
        values = _as_list(payload.get(key))
        if values:
            metadata[key] = values[:60]
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


def _build_graph(
    nodes: List[Dict[str, Any]],
    max_edges: int = 1200,
    max_virtual_nodes: int = 240,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    edges: Dict[str, Dict[str, Any]] = {}
    virtual_nodes: Dict[str, Dict[str, Any]] = {}

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
        if node["id"] in virtual_nodes:
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
        nodes, edges = _build_graph(
            point_nodes,
            max_edges=min(25000, max(1200, request.limit * 5)),
            max_virtual_nodes=min(2500, max(240, request.limit // 2)),
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

    relation_names: List[str] = []
    seen_names: Set[str] = set()
    for field in RELATION_FIELDS:
        for value in _iter_strings(payload.get(field)):
            for candidate in (_display_relation_label(value), _normalize_token(value)):
                candidate = candidate.strip()
                if not candidate or candidate.lower() in COMMON_RELATION_TOKENS:
                    continue
                if candidate not in seen_names:
                    seen_names.add(candidate)
                    relation_names.append(candidate)
            for token in TOKEN_RE.findall(value):
                simple = _normalize_token(token)
                if simple and simple.lower() not in COMMON_RELATION_TOKENS and simple not in seen_names:
                    seen_names.add(simple)
                    relation_names.append(simple)
                if len(relation_names) >= 40:
                    break
            if len(relation_names) >= 40:
                break
        if len(relation_names) >= 40:
            break

    if relation_names:
        relation_names = relation_names[:40]
        yield Filter(must=[*base_must, FieldCondition(key="primary_name", match=MatchAny(any=relation_names))])
        yield Filter(must=[*base_must, FieldCondition(key="semantic_names", match=MatchAny(any=relation_names))])
        yield Filter(must=[*base_must, FieldCondition(key="methods", match=MatchAny(any=relation_names[:30]))])


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
