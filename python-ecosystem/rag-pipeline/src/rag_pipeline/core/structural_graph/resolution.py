"""Unambiguous logical endpoint resolution and delta invalidation."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any, NamedTuple


from .shared import (
    _BULK_RELATION_RESOLUTION_THRESHOLD, _normalized_name, _short_name, _unit_names,
)

from .write_state import GraphWriteState


class _NamedUnit(NamedTuple):
    unit_id: str
    path: str
    qualified_name: str
    record_type: str
    aliases: frozenset[str]


def _named_unit(row: sqlite3.Row) -> _NamedUnit:
    return _NamedUnit(
        str(row["unit_id"]),
        str(row["path"]),
        str(row["qualified_name"] or ""),
        str(row["record_type"]),
        frozenset(alias.strip() for alias in _unit_names(
            str(row["name"] or ""),
            str(row["qualified_name"] or ""),
            json.loads(row["metadata_json"] or "{}"),
        ) if alias and alias.strip()),
    )


def _unambiguous_unit(rows: Sequence[_NamedUnit], requested: str) -> str | None:
    # Folded lookup is discovery only. A unique differently-cased declaration
    # is not the same identity (for example int64 versus a receiver's Int64).
    requested = requested.strip()
    exact = [row for row in rows if requested in row.aliases]
    if len(exact) == 1:
        return exact[0].unit_id
    qualified = [row for row in exact if row.qualified_name == requested]
    if len(qualified) == 1:
        return qualified[0].unit_id
    source_qualified = [row for row in qualified if row.record_type == "source_unit"]
    return source_qualified[0].unit_id if len(source_qualified) == 1 else None


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
            "SELECT DISTINCT relation_names.relation_id FROM delta_names "
            "CROSS JOIN relation_names USING (normalized_name)"
        ).fetchall()
        self.state.touched_relation_ids.update(str(row["relation_id"]) for row in rows)
        self.state.connection.execute(
            "UPDATE relations SET source_unit_id = NULL WHERE "
            "relation_id IN (SELECT relation_names.relation_id FROM "
            "delta_names CROSS JOIN relation_names USING (normalized_name) "
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
            "delta_names CROSS JOIN relation_names USING (normalized_name) "
            "WHERE relation_names.role = 'target') AND relation_id NOT IN "
            "(SELECT relation_id FROM delta_explicit_targets) AND NOT ("
            "origin = 'structural-index' OR kind = 'CONTAINS')"
        )

    def resolve_relations(
        self,
        relation_ids: Iterable[str] | None = None,
    ) -> None:
        """Resolve logical endpoints without guessing across repository files."""

        relation_from = "relations"
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
            relation_from = (
                "delta_relations CROSS JOIN relations ON "
                "delta_relations.relation_id = relations.relation_id"
            )
        # Full generations can contain hundreds of thousands of logical edges.
        # Resolve their names from one in-memory projection of the indexed name
        # table instead of issuing one or two SQLite SELECTs per endpoint.
        # Selective delta resolution intentionally keeps the smaller indexed
        # lookup path below.
        named_units: dict[str, _NamedUnit] = {}

        def resolve_name(
            value: str,
            *,
            preferred_paths: Sequence[str] = (),
            allow_short_name: bool = True,
        ) -> str | None:
            return self._unique_named_unit(
                value, preferred_paths=preferred_paths,
                allow_short_name=allow_short_name, named_units=named_units,
            )
        if (
            relation_ids is None
            or len(selected or ()) >= _BULK_RELATION_RESOLUTION_THRESHOLD
        ):
            candidates_by_name: dict[str, list[_NamedUnit]] = {}
            for row in self.state.connection.execute(
                "SELECT unit_id, path, name, qualified_name, record_type, "
                "metadata_json FROM units ORDER BY units.path, "
                "units.start_line, units.end_line, units.unit_id"
            ):
                # One metadata decode per unit, not per alias or relation.
                # Re-derive aliases because the indexed display row retains
                # only one spelling for case-colliding aliases on one unit.
                candidate = _named_unit(row)
                for name in {_normalized_name(alias) for alias in candidate.aliases}:
                    candidates_by_name.setdefault(name, []).append(candidate)

            def resolve_preloaded(
                value: str,
                *,
                preferred_paths: Sequence[str] = (),
                allow_short_name: bool = True,
            ) -> str | None:
                def candidates(
                    normalized_name: str,
                    paths: Sequence[str],
                ) -> list[_NamedUnit]:
                    rows = candidates_by_name.get(normalized_name, ())
                    if not paths:
                        return list(rows)
                    path_set = set(paths)
                    return [row for row in rows if row.path in path_set]

                exact_name = _normalized_name(value)
                if preferred_paths:
                    preferred = _unambiguous_unit(
                        candidates(exact_name, preferred_paths),
                        value,
                    )
                    if preferred:
                        return preferred
                exact_rows = candidates(exact_name, ())
                exact = _unambiguous_unit(exact_rows, value)
                if exact:
                    return exact
                if exact_rows or not allow_short_name:
                    return None
                short_name = _normalized_name(_short_name(value))
                if short_name == exact_name:
                    return None
                return _unambiguous_unit(
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
            "FROM " + relation_from + " LEFT JOIN relation_paths ON "
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
        named_units: dict[str, _NamedUnit] | None = None,
    ) -> str | None:
        if named_units is None:
            named_units = {}

        def candidates(normalized_name: str, paths: Sequence[str]) -> list[_NamedUnit]:
            parameters: list[Any] = [normalized_name]
            where = "unit_names.normalized_name = ?"
            if paths:
                placeholders = ",".join("?" for _ in paths)
                where += f" AND units.path IN ({placeholders})"
                parameters.extend(paths)
            rows = self.state.connection.execute(  # nosec B608 -- placeholders only
                "SELECT units.unit_id, units.path, units.name, units.qualified_name, "
                "units.record_type, units.metadata_json FROM unit_names JOIN units "
                "ON units.unit_id = unit_names.unit_id WHERE " + where
                + " ORDER BY units.path, units.start_line, units.end_line, units.unit_id",
                parameters,
            ).fetchall()
            result = []
            for row in rows:
                unit_id = str(row["unit_id"])
                if unit_id not in named_units:
                    named_units[unit_id] = _named_unit(row)
                result.append(named_units[unit_id])
            return result

        exact_name = _normalized_name(value)
        if preferred_paths:
            preferred = _unambiguous_unit(
                candidates(exact_name, preferred_paths),
                value,
            )
            if preferred:
                return preferred

        exact_rows = candidates(exact_name, ())
        exact = _unambiguous_unit(exact_rows, value)
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
        return _unambiguous_unit(short_rows, _short_name(value))
