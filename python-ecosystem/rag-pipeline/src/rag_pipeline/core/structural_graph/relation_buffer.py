"""Bulk persistence for the file-ingestion phase of a fresh private graph.

The batch bounds SQL parameter arrays, not graph content. Every logical edge,
endpoint, alias and contributor is written before graph reconciliation begins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import islice
import sqlite3
from typing import Any, Iterable

from .shared import _normalized_name

_ROWS_PER_STATEMENT = 256


def _insert_rows(connection: Any, table: str, columns: str,
                 rows: Iterable[tuple[Any, ...]]) -> None:
    iterator = iter(rows)
    while batch := tuple(islice(iterator, _ROWS_PER_STATEMENT)):
        placeholders = "(" + ",".join("?" for _ in batch[0]) + ")"
        connection.execute(
            f"INSERT OR IGNORE INTO {table}({columns}) VALUES "
            + ",".join(placeholders for _ in batch),
            tuple(value for row in batch for value in row),
        )


@dataclass
class BufferedRelation:
    values: list[Any]
    paths: tuple[str, ...]
    plugins: set[str] = field(default_factory=set)


class RelationBuffer:
    def __init__(self, state: Any):
        self.state = state
        self.rows: dict[str, BufferedRelation] = {}

    def add(self, values: tuple[Any, ...], paths: tuple[str, ...],
            plugins: tuple[str, ...]) -> None:
        # Keep binding failures inside the caller's optional plugin boundary.
        # The projection has already been UTF-8 hashed, but contributors and
        # endpoint IDs are outside that hash and SQLite integers are signed64.
        if not -(1 << 63) <= values[8] < (1 << 63):
            raise OverflowError("Python int too large to convert to SQLite INTEGER")
        for value in (*plugins, values[5], values[6]):
            if isinstance(value, str):
                value.encode("utf-8")
            elif value is not None:
                raise sqlite3.ProgrammingError("relation endpoint must be text or NULL")
        relation_id = values[0]
        row = self.rows.get(relation_id)
        if row is None:
            self.rows[relation_id] = BufferedRelation(list(values), paths, set(plugins))
        else:
            # Preserve the first resolved endpoint, exactly like coalesce in
            # the ordinary writer. Equal IDs have equal canonical payloads.
            for index in (5, 6):
                if row.values[index] is None:
                    row.values[index] = values[index]
            row.plugins.update(plugins)
        if len(self.rows) >= _ROWS_PER_STATEMENT:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return
        connection = self.state.connection
        ids = tuple(self.rows)
        bindings = ",".join("?" for _ in ids)
        existing = {row[0] for row in connection.execute(
            f"SELECT relation_id FROM relations WHERE relation_id IN ({bindings})", ids,
        )}
        for row in self.rows.values():
            row.values[10] = next(iter(row.plugins)) if len(row.plugins) == 1 else None
        connection.execute(
            "INSERT INTO relations(relation_id,kind,source,relation,target,"
            "source_unit_id,target_unit_id,path,line,origin,plugin_id,attributes_json) VALUES "
            + ",".join("(?,?,?,?,?,?,?,?,?,?,?,?)" for _ in ids)
            + " ON CONFLICT(relation_id) DO UPDATE SET "
              "source_unit_id=coalesce(relations.source_unit_id,excluded.source_unit_id),"
              "target_unit_id=coalesce(relations.target_unit_id,excluded.target_unit_id) "
              "WHERE (relations.source_unit_id IS NULL AND excluded.source_unit_id IS NOT NULL) "
              "OR (relations.target_unit_id IS NULL AND excluded.target_unit_id IS NOT NULL)",
            tuple(value for row in self.rows.values() for value in row.values),
        )
        _insert_rows(connection, "relation_plugins", "relation_id,plugin_id", (
            (relation_id, plugin) for relation_id, row in self.rows.items()
            for plugin in sorted(row.plugins)
        ))
        if existing:
            # An edge emitted in an earlier batch can gain another contributor.
            # Derive the same shortcut from the complete canonical union.
            connection.execute(
                "UPDATE relations SET plugin_id=(SELECT CASE WHEN count(*)=1 "
                "THEN min(plugin_id) END FROM relation_plugins p "
                "WHERE p.relation_id=relations.relation_id) WHERE relation_id IN ("
                + ",".join("?" for _ in existing) + ")", tuple(existing),
            )
        _insert_rows(connection, "relation_paths", "relation_id,path", (
            (relation_id, path) for relation_id, row in self.rows.items() for path in row.paths
        ))
        _insert_rows(connection, "relation_names", "relation_id,role,normalized_name", (
            (relation_id, role, _normalized_name(row.values[index]))
            for relation_id, row in self.rows.items() for role, index in (("source", 2), ("target", 4))
        ))
        self.state.relation_count += len(ids) - len(existing)
        self.rows.clear()
