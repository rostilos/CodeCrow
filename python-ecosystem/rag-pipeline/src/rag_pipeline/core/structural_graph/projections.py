"""Batch graph evidence hydration without repeated source-body reads."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any


def unit_to_manifest(row: sqlite3.Row, *, include_content: bool = False) -> dict[str, Any]:
    result = {
        "unitId": row["unit_id"],
        "path": row["path"],
        "kind": row["kind"],
        "name": row["name"],
        "qualifiedName": row["qualified_name"],
        "startLine": row["start_line"],
        "endLine": row["end_line"],
        "language": row["language"],
        "recordType": row["record_type"],
    }
    if include_content:
        result["content"] = row["content"]
        result["contentSha256"] = row["content_sha256"]
    return result


def _unit_by_id(connection: sqlite3.Connection, unit_id: str | None) -> sqlite3.Row | None:
    if not unit_id:
        return None
    return connection.execute(
        "SELECT * FROM units WHERE unit_id = ?",
        (unit_id,),
    ).fetchone()


def _batches(values: Sequence[str]) -> Iterable[Sequence[str]]:
    # SQL binding pages bound query overhead; they never truncate graph facts.
    for offset in range(0, len(values), 400):
        yield values[offset:offset + 400]


def relations_to_manifests(
    connection: sqlite3.Connection,
    rows: Sequence[sqlite3.Row],
) -> list[dict[str, Any]]:
    """Hydrate each endpoint/contributor set once, preserving input order."""
    if not rows:
        return []
    relation_ids = tuple(dict.fromkeys(row["relation_id"] for row in rows))
    unit_ids = tuple(sorted({
        row[column] for row in rows
        for column in ("source_unit_id", "target_unit_id") if row[column]
    }))
    units: dict[str, dict[str, Any]] = {}
    for batch in _batches(unit_ids):
        placeholders = ",".join("?" for _ in batch)
        # Evidence links need identities and locations, not source/metadata
        # bodies (which may be large and are returned only by get_unit).
        for unit in connection.execute(
            "SELECT unit_id, path, kind, name, qualified_name, start_line, "
            "end_line, language, record_type FROM units WHERE unit_id IN ("
            + placeholders + ")", batch,
        ):
            units[unit["unit_id"]] = unit_to_manifest(unit)
    paths: dict[str, list[str]] = {}
    plugins: dict[str, list[str]] = {}
    for batch in _batches(relation_ids):
        placeholders = ",".join("?" for _ in batch)
        for row in connection.execute(
            "SELECT relation_id, path FROM relation_paths WHERE relation_id IN ("
            + placeholders + ") ORDER BY relation_id, path", batch,
        ):
            paths.setdefault(row["relation_id"], []).append(row["path"])
        for row in connection.execute(
            "SELECT relation_id, plugin_id FROM relation_plugins "
            "WHERE relation_id IN (" + placeholders
            + ") ORDER BY relation_id, plugin_id", batch,
        ):
            plugins.setdefault(row["relation_id"], []).append(row["plugin_id"])
    result = []
    for row in rows:
        plugin_ids = plugins.get(row["relation_id"], [])
        result.append({
            "evidenceId": row["relation_id"],
            "kind": row["kind"],
            "source": row["source"],
            "relation": row["relation"],
            "target": row["target"],
            "origin": {
                "path": row["path"],
                "line": row["line"],
                "extractor": row["origin"],
                "plugin": plugin_ids[0] if len(plugin_ids) == 1 else None,
                "plugins": plugin_ids,
            },
            "sourceUnit": dict(units[row["source_unit_id"]]) if row["source_unit_id"] in units else None,
            "targetUnit": dict(units[row["target_unit_id"]]) if row["target_unit_id"] in units else None,
            "relatedPaths": paths.get(row["relation_id"], []),
            "attributes": json.loads(row["attributes_json"] or "{}"),
        })
    return result


def relation_to_manifest(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    return relations_to_manifests(connection, [row])[0]
