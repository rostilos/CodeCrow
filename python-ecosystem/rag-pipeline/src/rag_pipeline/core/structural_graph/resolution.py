"""Unambiguous logical endpoint resolution and delta invalidation."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any


from .shared import _BULK_RELATION_RESOLUTION_THRESHOLD, _normalized_name, _short_name

from .write_state import GraphWriteState


class RelationResolver:
    def __init__(self, state: GraphWriteState):
        self.state = state

    def invalidate_touched_name_resolutions(self) -> None:
        """Invalidate endpoints whose candidate set changed in a cloned graph."""

        if not self.state.track_mutations or not self.state.touched_names:
            return
        self.state.connection.executescript(
            "CREATE TEMP TABLE IF NOT EXISTS delta_names("
            "normalized_name TEXT PRIMARY KEY);"
            "DELETE FROM delta_names;"
            "CREATE TEMP TABLE IF NOT EXISTS delta_explicit_sources("
            "relation_id TEXT PRIMARY KEY);"
            "DELETE FROM delta_explicit_sources;"
            "CREATE TEMP TABLE IF NOT EXISTS delta_explicit_targets("
            "relation_id TEXT PRIMARY KEY);"
            "DELETE FROM delta_explicit_targets;"
        )
        self.state.connection.executemany(
            "INSERT OR IGNORE INTO delta_names(normalized_name) VALUES (?)",
            ((name,) for name in sorted(self.state.touched_names)),
        )
        self.state.connection.executemany(
            "INSERT OR IGNORE INTO delta_explicit_sources(relation_id) VALUES (?)",
            (
                (relation_id,)
                for relation_id in sorted(self.state.explicit_source_relation_ids)
            ),
        )
        self.state.connection.executemany(
            "INSERT OR IGNORE INTO delta_explicit_targets(relation_id) VALUES (?)",
            (
                (relation_id,)
                for relation_id in sorted(self.state.explicit_target_relation_ids)
            ),
        )
        rows = self.state.connection.execute(
            "SELECT DISTINCT relation_names.relation_id FROM relation_names "
            "JOIN delta_names USING (normalized_name)"
        ).fetchall()
        self.state.touched_relation_ids.update(str(row["relation_id"]) for row in rows)
        self.state.connection.execute(
            "UPDATE relations SET source_unit_id = NULL WHERE "
            "relation_id IN (SELECT relation_names.relation_id FROM "
            "relation_names JOIN delta_names USING (normalized_name) "
            "WHERE relation_names.role = 'source') AND relation_id NOT IN "
            "(SELECT relation_id FROM delta_explicit_sources) AND NOT ("
            "origin = 'structural-index' OR "
            "(origin = 'tree-sitter' AND kind != 'CONTAINS') OR "
            "(origin = 'plugin' AND kind IN ("
            "'INHERITS', 'CONSTRUCTOR_DEPENDENCY', 'CONTAINS')))"
        )
        self.state.connection.execute(
            "UPDATE relations SET target_unit_id = NULL WHERE "
            "relation_id IN (SELECT relation_names.relation_id FROM "
            "relation_names JOIN delta_names USING (normalized_name) "
            "WHERE relation_names.role = 'target') AND relation_id NOT IN "
            "(SELECT relation_id FROM delta_explicit_targets) AND NOT ("
            "origin = 'structural-index' OR kind = 'CONTAINS')"
        )

    def resolve_relations(
        self,
        relation_ids: Iterable[str] | None = None,
    ) -> None:
        """Resolve logical endpoints without guessing across repository files."""

        relation_join = ""
        selected: tuple[str, ...] | None = None
        if relation_ids is not None:
            selected = tuple(sorted(set(relation_ids)))
            if not selected:
                return
            self.state.connection.executescript(
                "CREATE TEMP TABLE IF NOT EXISTS delta_relations("
                "relation_id TEXT PRIMARY KEY);"
                "DELETE FROM delta_relations;"
            )
            self.state.connection.executemany(
                "INSERT OR IGNORE INTO delta_relations(relation_id) VALUES (?)",
                ((relation_id,) for relation_id in selected),
            )
            relation_join = (
                " JOIN delta_relations ON "
                "delta_relations.relation_id = relations.relation_id"
            )
        # Full generations can contain hundreds of thousands of logical edges.
        # Resolve their names from one in-memory projection of the indexed name
        # table instead of issuing one or two SQLite SELECTs per endpoint.
        # Selective delta resolution intentionally keeps the smaller indexed
        # lookup path below.
        resolve_name = self._unique_named_unit
        if (
            relation_ids is None
            or len(selected or ()) >= _BULK_RELATION_RESOLUTION_THRESHOLD
        ):
            candidates_by_name: dict[
                str,
                list[tuple[str, str, str, str]],
            ] = {}
            for candidate in self.state.connection.execute(
                "SELECT unit_names.normalized_name, units.unit_id, units.path, "
                "units.qualified_name, units.record_type FROM unit_names "
                "JOIN units ON units.unit_id = unit_names.unit_id "
                "ORDER BY unit_names.normalized_name, units.path, "
                "units.start_line, units.end_line, units.unit_id"
            ):
                candidates_by_name.setdefault(
                    str(candidate["normalized_name"]),
                    [],
                ).append((
                    str(candidate["unit_id"]),
                    str(candidate["path"]),
                    str(candidate["qualified_name"] or ""),
                    str(candidate["record_type"]),
                ))

            def resolve_preloaded(
                value: str,
                *,
                preferred_paths: Sequence[str] = (),
                allow_short_name: bool = True,
            ) -> str | None:
                def candidates(
                    normalized_name: str,
                    paths: Sequence[str],
                ) -> list[tuple[str, str, str, str]]:
                    rows = candidates_by_name.get(normalized_name, ())
                    if not paths:
                        return list(rows)
                    path_set = set(paths)
                    return [row for row in rows if row[1] in path_set]

                def unambiguous(
                    rows: Sequence[tuple[str, str, str, str]],
                    requested: str,
                ) -> str | None:
                    if len(rows) == 1:
                        return rows[0][0]
                    if not rows:
                        return None
                    normalized_requested = _normalized_name(requested)
                    qualified = [
                        row
                        for row in rows
                        if _normalized_name(row[2]) == normalized_requested
                    ]
                    if len(qualified) == 1:
                        return qualified[0][0]
                    source_qualified = [
                        row for row in qualified if row[3] == "source_unit"
                    ]
                    if len(source_qualified) == 1:
                        return source_qualified[0][0]
                    return None

                exact_name = _normalized_name(value)
                if preferred_paths:
                    preferred = unambiguous(
                        candidates(exact_name, preferred_paths),
                        value,
                    )
                    if preferred:
                        return preferred
                exact_rows = candidates(exact_name, ())
                exact = unambiguous(exact_rows, value)
                if exact:
                    return exact
                if exact_rows or not allow_short_name:
                    return None
                short_name = _normalized_name(_short_name(value))
                if short_name == exact_name:
                    return None
                return unambiguous(
                    candidates(short_name, ()),
                    _short_name(value),
                )

            resolve_name = resolve_preloaded

        rows = self.state.connection.execute(
            "SELECT relations.relation_id AS relation_id, "
            "relations.kind AS kind, relations.source AS source, "
            "relations.target AS target, relations.relation AS relation, "
            "relations.source_unit_id AS source_unit_id, "
            "relations.target_unit_id AS target_unit_id, "
            "relations.path AS path, relations.line AS line, "
            "relations.attributes_json AS attributes_json, "
            "group_concat(relation_paths.path, char(31)) AS indexed_paths "
            "FROM relations" + relation_join + " LEFT JOIN relation_paths ON "
            "relation_paths.relation_id = relations.relation_id "
            "GROUP BY relations.relation_id"
        )
        callable_by_path: dict[str, list[tuple[int, int, str]]] = {}

        def callable_at(path: str, line: int) -> str | None:
            if path not in callable_by_path:
                callable_by_path[path] = [
                    (int(unit["start_line"]), int(unit["end_line"]), str(unit["unit_id"]))
                    for unit in self.state.connection.execute(
                        "SELECT unit_id, start_line, end_line FROM units "
                        "WHERE path = ? AND kind IN "
                        "('method', 'function', 'constructor', 'arrow_function')",
                        (path,),
                    )
                ]
            enclosing = [
                unit for unit in callable_by_path[path]
                if unit[0] <= line <= unit[1]
            ]
            if not enclosing:
                return None
            return min(enclosing, key=lambda unit: (unit[1] - unit[0], -unit[0]))[2]

        self.state.connection.executescript(
            "CREATE TEMP TABLE IF NOT EXISTS resolved_relation_endpoints("
            "relation_id TEXT PRIMARY KEY, source_unit_id TEXT, "
            "target_unit_id TEXT);"
            "DELETE FROM resolved_relation_endpoints;"
        )
        endpoint_updates: list[tuple[str, str | None, str | None]] = []

        def flush_updates() -> None:
            if endpoint_updates:
                self.state.connection.executemany(
                    "INSERT INTO resolved_relation_endpoints("
                    "relation_id, source_unit_id, target_unit_id) "
                    "VALUES (?, ?, ?) ON CONFLICT(relation_id) DO UPDATE SET "
                    "source_unit_id = coalesce(excluded.source_unit_id, "
                    "resolved_relation_endpoints.source_unit_id), "
                    "target_unit_id = coalesce(excluded.target_unit_id, "
                    "resolved_relation_endpoints.target_unit_id)",
                    endpoint_updates,
                )
                endpoint_updates.clear()

        for row in rows:
            updates: dict[str, str] = {}
            indexed_paths = tuple(dict.fromkeys(
                path
                for path in str(row["indexed_paths"] or row["path"]).split(chr(31))
                if path
            ))
            source_paths = (row["path"],)
            target_paths = tuple(
                path for path in indexed_paths if path != row["path"]
            ) + source_paths
            is_call = str(row["relation"] or "").casefold().startswith("call")
            located_source = (
                callable_at(str(row["path"]), int(row["line"]))
                if is_call else None
            )
            if located_source and located_source != row["source_unit_id"]:
                updates["source_unit_id"] = located_source
            elif row["source_unit_id"] is None:
                resolved = resolve_name(
                    row["source"],
                    preferred_paths=source_paths,
                    allow_short_name="::" not in str(row["source"] or ""),
                )
                if resolved:
                    updates["source_unit_id"] = resolved
            if row["target_unit_id"] is None:
                target = str(row["target"] or "")
                try:
                    relation_attributes = json.loads(
                        row["attributes_json"] or "{}"
                    )
                except (TypeError, ValueError):
                    relation_attributes = {}
                if relation_attributes.get("targetResolutionProven") is False:
                    target = ""
                target_is_scoped = (
                    str(row["kind"] or "").upper() == "CONTAINS"
                    or any(delimiter in target for delimiter in (".", "/", "\\", ":"))
                )
                resolved = (
                    resolve_name(
                        target,
                        preferred_paths=(target_paths if target_is_scoped else ()),
                        allow_short_name="::" not in target,
                    )
                    if target
                    else None
                )
                if resolved:
                    updates["target_unit_id"] = resolved
            if not updates:
                continue
            source_unit_id = updates.get("source_unit_id")
            target_unit_id = updates.get("target_unit_id")
            relation_id = str(row["relation_id"])
            endpoint_updates.append((
                relation_id,
                source_unit_id,
                target_unit_id,
            ))
            if len(endpoint_updates) >= 2000:
                flush_updates()
        flush_updates()
        self.state.connection.execute(
            "UPDATE relations SET source_unit_id = ("
            "SELECT source_unit_id FROM resolved_relation_endpoints "
            "WHERE resolved_relation_endpoints.relation_id = relations.relation_id"
            ") WHERE relation_id IN (SELECT relation_id FROM "
            "resolved_relation_endpoints WHERE source_unit_id IS NOT NULL)"
        )
        self.state.connection.execute(
            "UPDATE relations SET target_unit_id = ("
            "SELECT target_unit_id FROM resolved_relation_endpoints "
            "WHERE resolved_relation_endpoints.relation_id = relations.relation_id"
            ") WHERE relation_id IN (SELECT relation_id FROM "
            "resolved_relation_endpoints WHERE target_unit_id IS NOT NULL)"
        )

    def resolve_touched_relations(self) -> None:
        """Re-resolve only endpoints affected by one cloned-generation delta."""

        self.invalidate_touched_name_resolutions()
        self.resolve_relations(self.state.touched_relation_ids)
        self.state.touched_relation_ids.clear()
        self.state.touched_names.clear()
        self.state.explicit_source_relation_ids.clear()
        self.state.explicit_target_relation_ids.clear()

    def _unique_named_unit(
        self,
        value: str,
        *,
        preferred_paths: Sequence[str] = (),
        allow_short_name: bool = True,
    ) -> str | None:
        def candidates(normalized_name: str, paths: Sequence[str]) -> list[sqlite3.Row]:
            parameters: list[Any] = [normalized_name]
            where = "unit_names.normalized_name = ?"
            if paths:
                placeholders = ",".join("?" for _ in paths)
                where += f" AND units.path IN ({placeholders})"
                parameters.extend(paths)
            return self.state.connection.execute(  # nosec B608 -- placeholders only
                "SELECT units.* FROM unit_names JOIN units "
                "ON units.unit_id = unit_names.unit_id WHERE " + where
                + " ORDER BY units.path, units.start_line, units.end_line, units.unit_id",
                parameters,
            ).fetchall()

        def unambiguous(rows: Sequence[sqlite3.Row], requested: str) -> str | None:
            if len(rows) == 1:
                return rows[0]["unit_id"]
            if not rows:
                return None
            normalized_requested = _normalized_name(requested)
            qualified = [
                row for row in rows
                if _normalized_name(str(row["qualified_name"] or ""))
                == normalized_requested
            ]
            if len(qualified) == 1:
                return qualified[0]["unit_id"]
            source_qualified = [
                row for row in qualified if row["record_type"] == "source_unit"
            ]
            if len(source_qualified) == 1:
                return source_qualified[0]["unit_id"]
            return None

        exact_name = _normalized_name(value)
        if preferred_paths:
            preferred = unambiguous(
                candidates(exact_name, preferred_paths),
                value,
            )
            if preferred:
                return preferred

        exact_rows = candidates(exact_name, ())
        exact = unambiguous(exact_rows, value)
        if exact:
            return exact
        if exact_rows:
            return None
        if not allow_short_name:
            return None

        short_name = _normalized_name(_short_name(value))
        if short_name == exact_name:
            return None
        short_rows = candidates(short_name, ())
        return unambiguous(short_rows, _short_name(value))
