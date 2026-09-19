"""Bounded inspection of immutable SQLite structural generations."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections import Counter
from contextlib import contextmanager
from pathlib import PurePosixPath
from typing import Any, Iterable

from fastapi import APIRouter, HTTPException, Query

from ...core.exact_index import ExactIndexPreconditionError
from ...core.structural_store import relation_to_manifest
from ..models import (
    RepositoryIndexFilters,
    RepositoryIndexGraphRequest,
    RepositoryIndexNodeRequest,
)


logger = logging.getLogger(__name__)
router = APIRouter(tags=["inspect"])

GRAPH_TEXT_LIMIT = 280
DETAIL_TEXT_LIMIT = 8000
MAX_OVERVIEW_SCAN = 20000


def _get_index_manager():
    from ..api import index_manager

    return index_manager


@contextmanager
def _open_tenant_generation(
    manager,
    *,
    workspace: str,
    project: str,
    collection_target: str,
):
    receipt = manager.store.read_receipt(collection_target)
    if (
        receipt is None
        or receipt.get("workspace") != workspace
        or receipt.get("project") != project
    ):
        raise ExactIndexPreconditionError(
            "requested structural repository generation is unavailable"
        )
    with manager.open_reader(
        workspace=workspace,
        project=project,
        branch=receipt["branch"],
        revision=receipt["repository_revision"],
        generation_manifest_sha256=receipt["generation_manifest_sha256"],
        collection_target=collection_target,
    ) as reader:
        yield reader.connection, receipt


def _metadata(row: sqlite3.Row) -> dict[str, Any]:
    try:
        value = json.loads(row["metadata_json"] or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _as_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, (list, tuple, set)):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _compact_text(value: object, limit: int) -> str:
    normalized = " ".join(str(value or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."


def _unit_node(
    row: sqlite3.Row,
    receipt: dict[str, Any],
    *,
    detail: bool = False,
) -> dict[str, Any]:
    metadata = _metadata(row)
    name = str(row["name"] or "").strip()
    path = str(row["path"] or "")
    symbol_names = list(dict.fromkeys((
        *_as_strings(metadata.get("symbol_names")),
        *([name] if name else []),
    )))
    node = {
        "id": row["unit_id"],
        "title": name or PurePosixPath(path).name or path or "Structural unit",
        "kind": row["kind"] or row["record_type"] or "structural_unit",
        "group": receipt["branch"],
        "branch": receipt["branch"],
        "path": path,
        "language": row["language"],
        "filetype": metadata.get("filetype"),
        "startLine": row["start_line"],
        "endLine": row["end_line"],
        "chunkIndex": metadata.get("chunk_index"),
        "subChunkIndex": metadata.get("sub_chunk_index"),
        "primaryName": name or None,
        "symbolNames": symbol_names,
        "parentClass": metadata.get("parent_class"),
        "fullPath": metadata.get("full_path") or row["qualified_name"],
        "namespace": metadata.get("namespace"),
        "signature": metadata.get("signature"),
        "indexedAt": None,
        "preview": _compact_text(row["content"], GRAPH_TEXT_LIMIT),
        "virtual": False,
        "recordType": row["record_type"],
        "metadata": {
            **metadata,
            "qualified_name": row["qualified_name"],
            "content_sha256": row["content_sha256"],
        },
    }
    if detail:
        node["text"] = _compact_text(row["content"], DETAIL_TEXT_LIMIT)
    return node


def _external_node(
    value: str,
    *,
    branch: str,
    relation_kind: str,
) -> dict[str, Any]:
    identity = hashlib.sha256(
        f"{relation_kind}\0{value}".encode("utf-8")
    ).hexdigest()
    return {
        "id": f"external:{identity}",
        "title": value or "Unresolved endpoint",
        "kind": "external_symbol",
        "group": "external",
        "branch": branch,
        "path": None,
        "language": None,
        "primaryName": value or None,
        "symbolNames": [value] if value else [],
        "preview": f"Unresolved structural endpoint for {relation_kind}",
        "virtual": True,
        "metadata": {"relation_kind": relation_kind},
    }


def _top(counter: Counter, limit: int) -> list[dict[str, Any]]:
    return [
        {"value": value, "count": count}
        for value, count in counter.most_common(limit)
        if value not in (None, "")
    ]


def _filter_allows_generation(
    filters: RepositoryIndexFilters,
    receipt: dict[str, Any],
) -> bool:
    return not filters.branches or receipt["branch"] in filters.branches


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _unit_where(
    filters: RepositoryIndexFilters,
    *,
    prefix: str = "",
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    parameters: list[Any] = []
    if filters.languages:
        placeholders = ",".join("?" for _ in filters.languages)
        clauses.append(f"{prefix}language IN ({placeholders})")
        parameters.extend(filters.languages)
    if filters.path:
        clauses.append(f"{prefix}path = ?")
        parameters.append(filters.path)
    if filters.file_query:
        clauses.append(f"{prefix}path LIKE ? ESCAPE '\\'")
        parameters.append(f"%{_escape_like(filters.file_query)}%")
    if filters.text_query:
        token = f"%{_escape_like(filters.text_query.casefold())}%"
        clauses.append(
            f"(lower({prefix}path) LIKE ? ESCAPE '\\' "
            f"OR lower({prefix}name) LIKE ? ESCAPE '\\' "
            f"OR lower({prefix}qualified_name) LIKE ? ESCAPE '\\' "
            f"OR lower({prefix}content) LIKE ? ESCAPE '\\' "
            f"OR lower({prefix}metadata_json) LIKE ? ESCAPE '\\')"
        )
        parameters.extend([token] * 5)
    return (" AND ".join(clauses) if clauses else "1 = 1"), parameters


def _graph_cursor(cursor: str | None) -> tuple[int, int, int | None]:
    """Decode a unit/relation cursor while accepting legacy unit offsets."""
    if cursor is None:
        return 0, 0, None
    if cursor.isdigit():
        return max(0, int(cursor)), 0, None
    parts = cursor.split(":")
    if (
        len(parts) == 4
        and parts[0] == "u"
        and parts[1].isdigit()
        and parts[2] == "r"
        and parts[3].isdigit()
    ):
        return max(0, int(parts[1])), max(0, int(parts[3])), None
    if (
        len(parts) == 6
        and parts[0] == "u"
        and parts[1].isdigit()
        and parts[2] == "n"
        and parts[3].isdigit()
        and parts[4] == "r"
        and parts[5].isdigit()
    ):
        return (
            max(0, int(parts[1])),
            max(0, int(parts[5])),
            max(1, int(parts[3])),
        )
    return 0, 0, None


def _encode_graph_cursor(
    unit_offset: int,
    relation_offset: int = 0,
    *,
    unit_page_size: int | None = None,
) -> str:
    if relation_offset <= 0:
        return str(max(0, unit_offset))
    if unit_page_size is None:
        return f"u:{max(0, unit_offset)}:r:{max(0, relation_offset)}"
    return (
        f"u:{max(0, unit_offset)}:n:{max(1, unit_page_size)}:"
        f"r:{max(0, relation_offset)}"
    )


def _select_units(
    connection: sqlite3.Connection,
    filters: RepositoryIndexFilters,
    *,
    limit: int,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], bool]:
    where, parameters = _unit_where(filters)
    rows = connection.execute(  # nosec B608 -- clauses are fixed above
        f"SELECT * FROM units WHERE {where} "
        "ORDER BY path, start_line, end_line, unit_id LIMIT ? OFFSET ?",
        (*parameters, limit + 1, offset),
    ).fetchall()
    return rows[:limit], len(rows) > limit


def _relation_rows_for_units(
    connection: sqlite3.Connection,
    units: Iterable[sqlite3.Row],
    *,
    limit: int,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int]:
    unit_list = list(units)
    unit_ids = tuple(row["unit_id"] for row in unit_list)
    paths = tuple(sorted({row["path"] for row in unit_list if row["path"]}))
    clauses: list[str] = []
    parameters: list[Any] = []
    if unit_ids:
        placeholders = ",".join("?" for _ in unit_ids)
        clauses.extend((
            f"source_unit_id IN ({placeholders})",
            f"target_unit_id IN ({placeholders})",
        ))
        parameters.extend(unit_ids)
        parameters.extend(unit_ids)
    if paths:
        placeholders = ",".join("?" for _ in paths)
        clauses.append(
            "relation_id IN (SELECT relation_id FROM relation_paths "
            f"WHERE path IN ({placeholders}))"
        )
        parameters.extend(paths)
    if not clauses:
        return [], 0
    where = "(" + " OR ".join(clauses) + ")"
    total = connection.execute(  # nosec B608 -- placeholders only
        "SELECT count(*) AS count FROM relations WHERE " + where,
        parameters,
    ).fetchone()["count"]
    rows = connection.execute(  # nosec B608 -- placeholders only
        "SELECT * FROM relations WHERE " + where + " "
        "ORDER BY path, line, relation_id LIMIT ? OFFSET ?",
        (*parameters, max(1, limit), max(0, offset)),
    ).fetchall()
    return rows, total


def _relation_rows_for_filters(
    connection: sqlite3.Connection,
    filters: RepositoryIndexFilters,
    *,
    limit: int,
    offset: int = 0,
) -> tuple[list[sqlite3.Row], int]:
    """Page each relation once across the complete filtered unit population."""
    unit_where, unit_parameters = _unit_where(filters, prefix="candidate.")
    clauses = (
        "source_unit_id IN (SELECT candidate.unit_id FROM units AS candidate "
        f"WHERE {unit_where})",
        "target_unit_id IN (SELECT candidate.unit_id FROM units AS candidate "
        f"WHERE {unit_where})",
        "relation_id IN (SELECT relation_paths.relation_id FROM relation_paths "
        "WHERE EXISTS (SELECT 1 FROM units AS candidate "
        "WHERE candidate.path = relation_paths.path "
        f"AND {unit_where}))",
    )
    parameters = (
        *unit_parameters,
        *unit_parameters,
        *unit_parameters,
    )
    where = "(" + " OR ".join(clauses) + ")"
    total = connection.execute(  # nosec B608 -- fixed clauses, placeholders only
        "SELECT count(*) AS count FROM relations WHERE " + where,
        parameters,
    ).fetchone()["count"]
    rows = connection.execute(  # nosec B608 -- fixed clauses, placeholders only
        "SELECT * FROM relations WHERE " + where + " "
        "ORDER BY path, line, relation_id LIMIT ? OFFSET ?",
        (*parameters, max(1, limit), max(0, offset)),
    ).fetchall()
    return rows, total


def _build_relation_graph(
    connection: sqlite3.Connection,
    receipt: dict[str, Any],
    unit_rows: Iterable[sqlite3.Row],
    relation_rows: Iterable[sqlite3.Row],
    *,
    detail_unit_id: str | None = None,
    max_extra_nodes: int = 400,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: dict[str, dict[str, Any]] = {
        row["unit_id"]: _unit_node(
            row,
            receipt,
            detail=row["unit_id"] == detail_unit_id,
        )
        for row in unit_rows
    }
    edges: list[dict[str, Any]] = []
    extra_node_count = 0

    def endpoint(
        unit_id: str | None,
        logical_name: str,
        relation_kind: str,
    ) -> str | None:
        nonlocal extra_node_count
        if unit_id and unit_id in nodes:
            return unit_id
        if unit_id and extra_node_count < max_extra_nodes:
            row = connection.execute(
                "SELECT * FROM units WHERE unit_id = ?",
                (unit_id,),
            ).fetchone()
            if row is not None:
                nodes[unit_id] = _unit_node(row, receipt)
                extra_node_count += 1
                return unit_id
        external = _external_node(
            logical_name,
            branch=receipt["branch"],
            relation_kind=relation_kind,
        )
        if external["id"] not in nodes:
            if extra_node_count >= max_extra_nodes:
                return None
            nodes[external["id"]] = external
            extra_node_count += 1
        return external["id"]

    for row in relation_rows:
        source_id = endpoint(
            row["source_unit_id"], row["source"], row["kind"]
        )
        target_id = endpoint(
            row["target_unit_id"], row["target"], row["kind"]
        )
        if not source_id or not target_id:
            continue
        manifest = relation_to_manifest(connection, row)
        edges.append({
            "id": row["relation_id"],
            "source": source_id,
            "target": target_id,
            "kind": str(row["kind"] or "relation").casefold(),
            "label": row["relation"],
            "weight": 2.05 if row["origin"] == "plugin" else 1.5,
            "tokens": [row["relation"]] if row["relation"] else [],
            "metadata": {
                "origin": row["origin"],
                "plugin": manifest["origin"]["plugin"],
                "plugins": manifest["origin"]["plugins"],
                "provenance": manifest["origin"],
                "relatedPaths": manifest["relatedPaths"],
                "attributes": manifest["attributes"],
            },
            "relation": {
                "structural_record_type": "structural_relation",
                "key": row["relation_id"],
                "kind": row["kind"],
                "source": row["source"],
                "source_key": source_id,
                "target": row["target"],
                "target_key": target_id,
                "path": row["path"],
                "span": {
                    "start_line": row["line"],
                    "end_line": row["line"],
                },
                "evidence": row["origin"],
                "related_paths": manifest["relatedPaths"],
            },
        })
    return list(nodes.values()), edges


@router.get("/repository-index/{workspace}/{project}/overview")
def repository_index_overview(
    workspace: str,
    project: str,
    collection_target: str = Query(min_length=1),
    sample_limit: int = Query(default=10000, ge=100, le=MAX_OVERVIEW_SCAN),
):
    """Return a bounded structural-generation overview."""
    manager = _get_index_manager()
    try:
        with _open_tenant_generation(
            manager,
            workspace=workspace,
            project=project,
            collection_target=collection_target,
        ) as (connection, receipt):
            total_units = connection.execute(
                "SELECT count(*) AS count FROM units"
            ).fetchone()["count"]
            relation_summary = connection.execute(
                "SELECT count(*) AS total_relations, "
                "sum(source_unit_id IS NOT NULL AND target_unit_id IS NOT NULL) "
                "AS resolved_relations, "
                "sum((source_unit_id IS NOT NULL) != "
                "(target_unit_id IS NOT NULL)) AS partially_resolved_relations, "
                "sum(source_unit_id IS NULL AND target_unit_id IS NULL) "
                "AS unresolved_relations, "
                "sum(origin = 'plugin') AS plugin_relations, "
                "sum(source = target) AS self_relations "
                "FROM relations"
            ).fetchone()
            structural_file_count = connection.execute(
                "SELECT count(*) AS count FROM units "
                "WHERE record_type = 'structural_file'"
            ).fetchone()["count"]
            relation_kinds = connection.execute(
                "SELECT kind AS value, count(*) AS count FROM relations "
                "GROUP BY kind ORDER BY count DESC, kind"
            ).fetchall()
            relation_origins = connection.execute(
                "SELECT origin AS value, count(*) AS count FROM relations "
                "GROUP BY origin ORDER BY count DESC, origin"
            ).fetchall()
            relation_plugins = connection.execute(
                "SELECT plugin_id AS value, count(*) AS count "
                "FROM relation_plugins GROUP BY plugin_id "
                "ORDER BY count DESC, plugin_id"
            ).fetchall()
            relation_labels = connection.execute(
                "SELECT relation AS value, count(*) AS count FROM relations "
                "GROUP BY relation ORDER BY count DESC, relation"
            ).fetchall()
            rows = connection.execute(
                "SELECT * FROM units ORDER BY path, start_line, unit_id LIMIT ?",
                (sample_limit,),
            ).fetchall()
    except ExactIndexPreconditionError:
        return {
            "available": False,
            "workspace": workspace,
            "project": project,
            "totalPoints": 0,
            "totalRelations": 0,
            "resolvedRelations": 0,
            "partiallyResolvedRelations": 0,
            "unresolvedRelations": 0,
            "pluginRelations": 0,
            "renderableRelations": 0,
            "suppressedSelfRelations": 0,
            "selfRelations": 0,
            "indexedFileCount": 0,
            "structuralFileCount": 0,
            "sampledPoints": 0,
            "sampled": False,
            "branches": [],
            "languages": [],
            "files": [],
            "symbolNames": [],
            "recordTypes": [],
            "relationKinds": [],
            "relationOrigins": [],
            "relationPlugins": [],
            "relationLabels": [],
        }
    except Exception as exception:
        logger.error(
            "Structural overview failed for %s/%s: %s",
            workspace,
            project,
            exception,
        )
        raise HTTPException(status_code=500, detail="Repository index overview failed")

    languages: Counter = Counter()
    files: Counter = Counter()
    symbols: Counter = Counter()
    record_types: Counter = Counter()
    for row in rows:
        metadata = _metadata(row)
        languages.update([row["language"]])
        files.update([row["path"]])
        record_types.update([row["record_type"]])
        symbols.update([row["name"]])
        symbols.update(_as_strings(metadata.get("symbol_names"))[:5])
    return {
        "available": True,
        "workspace": workspace,
        "project": project,
        "collection": collection_target,
        "totalPoints": total_units,
        "totalRelations": relation_summary["total_relations"],
        "resolvedRelations": relation_summary["resolved_relations"] or 0,
        "partiallyResolvedRelations": (
            relation_summary["partially_resolved_relations"] or 0
        ),
        "unresolvedRelations": relation_summary["unresolved_relations"] or 0,
        "pluginRelations": relation_summary["plugin_relations"] or 0,
        # Every relation is renderable because unresolved endpoints are
        # represented explicitly and graph self-loops are retained.
        "renderableRelations": relation_summary["total_relations"],
        "suppressedSelfRelations": 0,
        "selfRelations": relation_summary["self_relations"] or 0,
        "indexedFileCount": int(receipt.get("document_count") or 0),
        "structuralFileCount": structural_file_count,
        "sampledPoints": len(rows),
        "scannedPoints": len(rows),
        "sampled": total_units > len(rows),
        "branches": [{"value": receipt["branch"], "count": total_units}],
        "languages": _top(languages, 40),
        "files": _top(files, 120),
        "symbolNames": _top(symbols, 120),
        "recordTypes": _top(record_types, 40),
        "relationKinds": [dict(row) for row in relation_kinds],
        "relationOrigins": [dict(row) for row in relation_origins],
        "relationPlugins": [dict(row) for row in relation_plugins],
        "relationLabels": [dict(row) for row in relation_labels],
    }


@router.post("/repository-index/{workspace}/{project}/graph")
def repository_index_graph(
    workspace: str,
    project: str,
    request: RepositoryIndexGraphRequest,
):
    """Return one bounded unit/relation graph slice."""
    manager = _get_index_manager()
    try:
        with _open_tenant_generation(
            manager,
            workspace=workspace,
            project=project,
            collection_target=request.collection_target,
        ) as (connection, receipt):
            if not _filter_allows_generation(request.filters, receipt):
                return {
                    "available": True,
                    "nodes": [],
                    "edges": [],
                    "nextCursor": None,
                    "scannedPoints": 0,
                    "limit": request.limit,
                }
            offset, relation_offset, _legacy_cursor_page_size = _graph_cursor(
                request.cursor
            )
            requested_page_limit = min(request.limit, request.scan_limit)
            # Relations are globally paged independently from unit pages. A
            # client can therefore increase its unit/relation page size after
            # the first response without restarting or skipping relation IDs.
            # Legacy cursors still decode above, but their frozen unit size no
            # longer constrains subsequent requests.
            page_limit = requested_page_limit
            rows, has_more = _select_units(
                connection,
                request.filters,
                limit=page_limit,
                offset=offset,
            )
            relation_page_limit = min(5000, max(1200, page_limit))
            relations, total_relations = _relation_rows_for_filters(
                connection,
                request.filters,
                limit=relation_page_limit,
                offset=relation_offset,
            )
            nodes, edges = _build_relation_graph(
                connection,
                receipt,
                rows,
                relations,
                # Every selected relation needs at most two endpoint nodes.
                # This is a per-page addition allowance, not a total-node cap.
                max_extra_nodes=max(400, len(relations) * 2),
            )
            next_relation_offset = relation_offset + len(relations)
            next_unit_offset = offset + len(rows)
            if next_relation_offset < total_relations or has_more:
                next_cursor = _encode_graph_cursor(
                    next_unit_offset,
                    next_relation_offset,
                )
            else:
                next_cursor = None
        return {
            "available": True,
            "nodes": nodes,
            "edges": edges,
            "nextCursor": next_cursor,
            "scannedPoints": len(rows),
            "limit": request.limit,
            "totalRelations": int(receipt.get("relation_count") or 0),
            "relationOffset": relation_offset,
            "returnedRelations": len(edges),
            "selectedRelationRows": len(relations),
            "droppedEndpointRelations": max(0, len(relations) - len(edges)),
            "eligibleRelations": total_relations,
            "relationsTruncated": next_relation_offset < total_relations,
        }
    except ExactIndexPreconditionError:
        return {
            "available": False,
            "nodes": [],
            "edges": [],
            "nextCursor": None,
            "scannedPoints": 0,
            "limit": request.limit,
        }
    except Exception as exception:
        logger.error(
            "Structural graph failed for %s/%s: %s",
            workspace,
            project,
            exception,
        )
        raise HTTPException(status_code=500, detail="Repository index graph failed")


@router.post("/repository-index/{workspace}/{project}/points/{point_id}")
def repository_index_point(
    workspace: str,
    project: str,
    point_id: str,
    request: RepositoryIndexNodeRequest,
):
    """Return one source-backed unit and its bounded structural neighborhood."""
    manager = _get_index_manager()
    try:
        with _open_tenant_generation(
            manager,
            workspace=workspace,
            project=project,
            collection_target=request.collection_target,
        ) as (connection, receipt):
            if not _filter_allows_generation(request.filters, receipt):
                raise HTTPException(status_code=404, detail="Repository record not found")
            row = connection.execute(
                "SELECT * FROM units WHERE unit_id = ?",
                (point_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Repository record not found")
            where, parameters = _unit_where(request.filters)
            matches = connection.execute(  # nosec B608 -- fixed clauses
                f"SELECT 1 FROM units WHERE unit_id = ? AND {where}",
                (point_id, *parameters),
            ).fetchone()
            if matches is None:
                raise HTTPException(status_code=404, detail="Repository record not found")
            relations, total_relations = _relation_rows_for_units(
                connection,
                [row],
                limit=max(40, request.neighbor_limit * 4),
            )
            graph_nodes, graph_edges = _build_relation_graph(
                connection,
                receipt,
                [row],
                relations,
                detail_unit_id=point_id,
                max_extra_nodes=request.neighbor_limit + 1,
            )
        node = next(item for item in graph_nodes if item["id"] == point_id)
        neighbors = [
            item for item in graph_nodes if item["id"] != point_id
        ][:request.neighbor_limit]
        neighbor_ids = {point_id, *(item["id"] for item in neighbors)}
        visible_edges = [
            edge
            for edge in graph_edges
            if edge["source"] in neighbor_ids and edge["target"] in neighbor_ids
        ]
        return {
            "node": node,
            "neighbors": neighbors,
            "edges": visible_edges,
            "eligibleRelations": total_relations,
            "selectedRelationRows": len(relations),
            "returnedRelations": len(visible_edges),
            "relationsTruncated": len(visible_edges) < total_relations,
        }
    except HTTPException:
        raise
    except ExactIndexPreconditionError:
        raise HTTPException(status_code=404, detail="Repository index not found")
    except Exception as exception:
        logger.error(
            "Structural unit detail failed for %s/%s: %s",
            workspace,
            project,
            exception,
        )
        raise HTTPException(status_code=500, detail="Repository index point failed")
