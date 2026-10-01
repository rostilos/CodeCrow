"""Bound structural graph queries and public evidence projections."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from ..exact_index import ExactIndexPreconditionError

from .shared import (
    _MAX_PRELOADED_ANCHOR_UNITS,
    _ENDPOINT_RELATION_KINDS,
    _sha256_text,
    _normalize_path,
    _optional_path,
    _normalized_name,
    _short_name,
    _escape_like,
    _fts_query,
)

from .projections import (
    _unit_by_id,
    unit_to_manifest,
    relations_to_manifests,
)


class StructuralGraphReader:
    """Deterministic, bounded reads over one already-bound generation."""

    def __init__(self, connection: sqlite3.Connection, receipt: Mapping[str, Any]):
        self.connection = connection
        self.receipt = dict(receipt)

    def snapshot(self) -> dict[str, Any]:
        metadata = self.receipt.get("snapshot_metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        snapshot = {
            "kind": str(metadata.get("kind") or "target_head"),
            "branch": self.receipt["branch"],
            "revision": self.receipt["repository_revision"],
            "generationManifestSha256": self.receipt[
                "generation_manifest_sha256"
            ],
        }
        for source_key, target_key in (
            ("base_revision", "baseRevision"),
            ("base_collection_target", "baseCollectionTarget"),
            (
                "base_generation_manifest_sha256",
                "baseGenerationManifestSha256",
            ),
            ("source_revision", "sourceRevision"),
            ("target_source_tree_sha256", "targetSourceTreeSha256"),
            ("overlay_sha256", "overlaySha256"),
        ):
            value = metadata.get(source_key)
            if isinstance(value, str) and value:
                snapshot[target_key] = value
        return snapshot

    def repository_facts(self) -> dict[str, Any]:
        """Return the exact repository profile sealed with this generation."""

        row = self.connection.execute(
            "SELECT content, content_sha256 FROM units "
            "WHERE path = ? AND record_type = 'repository_state' "
            "ORDER BY unit_id LIMIT 1",
            ("__analysis_state__/repository-facts.state",),
        ).fetchone()
        if row is None:
            raise ExactIndexPreconditionError(
                "sealed structural generation lacks repository facts"
            )
        content = str(row["content"] or "")
        expected_sha256 = str(
            self.receipt.get("repository_facts_sha256") or ""
        )
        if (
            not expected_sha256
            or str(row["content_sha256"] or "") != expected_sha256
            or _sha256_text(content) != expected_sha256
        ):
            raise ExactIndexPreconditionError(
                "sealed structural repository facts do not match the receipt"
            )
        try:
            facts = json.loads(content)
        except (TypeError, ValueError) as exception:
            raise ExactIndexPreconditionError(
                "sealed structural repository facts are malformed"
            ) from exception
        if not isinstance(facts, dict):
            raise ExactIndexPreconditionError(
                "sealed structural repository facts are malformed"
            )
        paths = facts.get("paths")
        if not isinstance(paths, list) or not all(
            isinstance(path, str) for path in paths
        ):
            raise ExactIndexPreconditionError(
                "sealed structural repository paths are malformed"
            )
        for field in ("projectType", "sourceRoot"):
            if facts.get(field) is not None and not isinstance(
                facts.get(field), str
            ):
                raise ExactIndexPreconditionError(
                    "sealed structural repository profile is malformed"
                )
        return facts

    def relations_for_paths(
        self,
        paths: Sequence[str],
        *,
        max_relations: int = 80,
    ) -> dict[str, Any]:
        normalized_paths = tuple(sorted({_normalize_path(path) for path in paths}))
        max_relations = max(1, min(int(max_relations), 500))
        anchors: list[dict[str, Any]] = []
        anchor_unit_ids: set[str] = set()
        per_path_limit = max(
            1,
            _MAX_PRELOADED_ANCHOR_UNITS // max(1, len(normalized_paths)),
        )
        total_anchor_units = 0
        omitted_anchor_units = 0
        for path in normalized_paths:
            units = self.connection.execute(
                "SELECT * FROM units WHERE path = ? "
                "ORDER BY start_line, end_line, unit_id LIMIT ?",
                (path, per_path_limit + 1),
            ).fetchall()
            selected_units = units[:per_path_limit]
            anchor_unit_ids.update(row["unit_id"] for row in selected_units)
            total_anchor_units += len(selected_units)
            omitted_for_path = max(0, len(units) - len(selected_units))
            if omitted_for_path:
                total_for_path = self.connection.execute(
                    "SELECT COUNT(*) AS count FROM units WHERE path = ?",
                    (path,),
                ).fetchone()["count"]
                omitted_for_path = max(0, total_for_path - len(selected_units))
            omitted_anchor_units += omitted_for_path
            anchors.append({
                "path": path,
                "symbols": [unit_to_manifest(row) for row in selected_units],
                "omittedSymbols": omitted_for_path,
            })

        relation_ids: list[str] = []
        candidate_queries: list[tuple[str, tuple[Any, ...]]] = []
        if normalized_paths:
            placeholders = ",".join("?" for _ in normalized_paths)
            path_candidates_sql = (
                f"SELECT DISTINCT relations.relation_id "
                f"FROM relations JOIN relation_paths "
                f"ON relation_paths.relation_id = relations.relation_id "
                f"WHERE relation_paths.path IN ({placeholders})"
            )
            candidate_queries.append((path_candidates_sql, normalized_paths))
            relation_ids.extend(
                row["relation_id"]
                for row in self.connection.execute(  # nosec B608
                    path_candidates_sql
                    + f" ORDER BY CASE WHEN relations.path IN ({placeholders}) "
                    f"THEN 0 ELSE 1 END, "
                    f"CASE WHEN relations.origin = 'plugin' THEN 0 ELSE 1 END, "
                    f"CASE relations.kind WHEN 'CONTAINS' THEN 2 "
                    f"WHEN 'CALLS' THEN 1 ELSE 0 END, "
                    f"relations.path, relations.line, relations.relation_id LIMIT ?",
                    (*normalized_paths, *normalized_paths, max_relations + 1),
                ).fetchall()
            )
        if anchor_unit_ids:
            unit_ids = tuple(sorted(anchor_unit_ids))
            placeholders = ",".join("?" for _ in unit_ids)
            unit_candidates_sql = (
                f"SELECT relation_id FROM relations WHERE "
                f"source_unit_id IN ({placeholders}) OR "
                f"target_unit_id IN ({placeholders})"
            )
            candidate_queries.append((unit_candidates_sql, (*unit_ids, *unit_ids)))
            if len(dict.fromkeys(relation_ids)) <= max_relations:
                relation_ids.extend(
                    row["relation_id"]
                    for row in self.connection.execute(  # nosec B608
                        unit_candidates_sql + " ORDER BY relation_id LIMIT ?",
                        (*unit_ids, *unit_ids, max_relations + 1),
                    ).fetchall()
                )
        ordered_ids = tuple(dict.fromkeys(relation_ids))
        selected_ids = ordered_ids[:max_relations]
        if candidate_queries:
            union_sql = " UNION ".join(
                query for query, _ in candidate_queries
            )
            union_parameters = tuple(
                parameter
                for _, parameters in candidate_queries
                for parameter in parameters
            )
            total_relations = self.connection.execute(  # nosec B608
                f"SELECT COUNT(*) AS count FROM ({union_sql})",
                union_parameters,
            ).fetchone()["count"]
        else:
            total_relations = 0
        rows_by_id = {}
        if selected_ids:
            placeholders = ",".join("?" for _ in selected_ids)
            rows_by_id = {
                row["relation_id"]: row
                for row in self.connection.execute(
                    "SELECT * FROM relations WHERE relation_id IN ("
                    + placeholders + ")", selected_ids,
                )
            }
        relations = relations_to_manifests(
            self.connection,
            [rows_by_id[relation_id] for relation_id in selected_ids
             if relation_id in rows_by_id],
        )
        return {
            "snapshot": self.snapshot(),
            "anchors": anchors,
            "relations": relations,
            "coverage": {
                "state": (
                    "complete"
                    if total_relations <= max_relations and omitted_anchor_units == 0
                    else "bounded"
                ),
                "totalRelations": total_relations,
                "omittedRelations": max(0, total_relations - len(relations)),
                "preloadedSymbols": total_anchor_units,
                "omittedSymbols": omitted_anchor_units,
            },
        }

    def get_unit(self, unit_id: str) -> dict[str, Any] | None:
        row = _unit_by_id(self.connection, unit_id)
        if row is None:
            return None
        source_evidence = row["record_type"] in {"source_unit", "plugin_context"}
        return {
            "snapshot": self.snapshot(),
            "unit": unit_to_manifest(row, include_content=source_evidence),
            "sourceEvidence": source_evidence,
        }

    def relations_among(
        self,
        unit_ids: Sequence[str],
        *,
        max_results: int = 10000,
    ) -> dict[str, Any]:
        """Return a deterministic bounded subgraph induced by unit IDs."""

        selected_ids = tuple(dict.fromkeys(
            str(unit_id).strip()
            for unit_id in unit_ids
            if str(unit_id).strip()
        ))
        max_results = max(1, min(int(max_results), 10000))
        if not selected_ids:
            return {
                "snapshot": self.snapshot(),
                "results": [],
                "truncated": False,
                "resultCount": 0,
            }
        values = ",".join("(?)" for _unit_id_value in selected_ids)
        rows = self.connection.execute(
            "WITH selected(unit_id) AS (VALUES " + values + ") "
            "SELECT relations.* FROM relations "
            "JOIN selected AS sources "
            "ON sources.unit_id = relations.source_unit_id "
            "JOIN selected AS targets "
            "ON targets.unit_id = relations.target_unit_id "
            "ORDER BY relations.relation_id LIMIT ?",
            (*selected_ids, max_results + 1),
        ).fetchall()
        selected = rows[:max_results]
        return {
            "snapshot": self.snapshot(),
            "results": relations_to_manifests(self.connection, selected),
            "truncated": len(rows) > max_results,
            "resultCount": len(selected),
        }

    def query_graph(
        self,
        pattern: str,
        target: str,
        *,
        max_results: int = 25,
        cursor: int = 0,
    ) -> dict[str, Any]:
        pattern = str(pattern or "").strip().casefold()
        target = str(target or "").strip()
        max_results = max(1, min(int(max_results), 100))
        cursor = max(0, int(cursor))
        if not target:
            raise ValueError("graph query target must be non-empty")

        if pattern in {"symbol_search", "symbols"}:
            rows = self.search_units(
                target,
                max_results=max_results + 1,
                offset=cursor,
            )
            selected = rows[:max_results]
            truncated = len(rows) > max_results
            return {
                "snapshot": self.snapshot(),
                "pattern": "symbol_search",
                "target": target,
                "results": selected,
                "cursor": cursor,
                "nextCursor": cursor + len(selected) if truncated else None,
                "truncated": truncated,
                "resultCount": len(selected),
            }
        if pattern == "file_summary":
            path = _normalize_path(target)
            rows = self.connection.execute(
                "SELECT * FROM units WHERE path = ? "
                "ORDER BY start_line, end_line, unit_id LIMIT ? OFFSET ?",
                (path, max_results + 1, cursor),
            ).fetchall()
            return self._unit_query_response(
                pattern,
                target,
                rows,
                max_results,
                cursor=cursor,
            )

        kind_map = {
            "callers_of": ("incoming", {
                "CALLS",
                "CALLS_INSTANCE",
                "CALLS_RESOLVED_TARGET",
                "CALLS_STATIC",
                "CALLS_UNIQUE_CO_DECLARED_DEFINITION",
            }),
            "callees_of": ("outgoing", {
                "CALLS",
                "CALLS_INSTANCE",
                "CALLS_RESOLVED_TARGET",
                "CALLS_STATIC",
                "CALLS_UNIQUE_CO_DECLARED_DEFINITION",
            }),
            "references_to": ("incoming", {
                "DEPENDS_ON",
                "DEPENDS_ON_CONFIG_FIELD",
                "DEPENDS_ON_INDEXER",
                "REFERENCES",
                "REFERENCES_DECLARED_FIELD",
                "REFERENCES_JSON_SCHEMA_TARGET",
                "USES",
            }),
            "imports_of": ("outgoing", {
                "IMPORTS",
                "IMPORTS_FROM",
                "RESOLVES_IMPORT",
            }),
            "importers_of": ("incoming", {
                "IMPORTS",
                "IMPORTS_FROM",
                "RESOLVES_IMPORT",
            }),
            "children_of": ("outgoing", {"CONTAINS"}),
            "tests_for": ("both", {"TESTED_BY", "TESTS"}),
            "inheritors_of": ("incoming", {"EXTENDS", "INHERITS", "IMPLEMENTS"}),
            # Neutral plugin facts preserve an extractor-specific ``kind`` and
            # carry the portable verb in ``relation``. These patterns therefore
            # describe vocabulary emitted by CodeCrow's current plugins rather
            # than pretending upstream-only TRIGGERS/PUBLISHES edges exist.
            "triggers_of": ("incoming", {"RUNS_ON"}),
            "triggered_by": ("outgoing", {"RUNS_ON"}),
            "publishers_of": ("incoming", {
                "DISPATCHES_EVENT",
                "DISPATCHES_TO_UNIQUE_LAYOUT_LISTENER",
                "PRODUCES",
                "PUBLISHES_THROUGH",
            }),
            "listeners_of": ("outgoing", {
                "LISTENS_TO_LAYOUT_DISPATCHERS",
                "OBSERVED_BY",
            }),
            "handlers_of": ("incoming", {"HANDLES"}),
            "endpoints_for": ("outgoing", {"HANDLES"}),
            "consumers_of": ("incoming", {"CONSUMES", "CONSUMES_QUEUE"}),
            "relations_of": ("both", set()),
            "framework_relations": ("both", {"__plugin__"}),
        }
        if pattern not in kind_map:
            return {
                "status": "error",
                "error": "Unknown graph pattern",
                "availablePatterns": sorted((*kind_map, "symbol_search", "file_summary")),
                "results": [],
            }
        direction, kinds = kind_map[pattern]
        normalized_target_path = _optional_path(target)
        candidate_page_size = 20
        candidate_units, candidate_count = self._resolve_units_with_count(
            target,
            limit=candidate_page_size,
        )
        target_is_exact_path = bool(
            candidate_units
            and normalized_target_path is not None
            and all(
                str(row["path"] or "") == normalized_target_path
                for row in candidate_units
            )
        )
        if candidate_count > 1 and not target_is_exact_path:
            candidate_page_size = min(20, max_results)
            if cursor or len(candidate_units) > candidate_page_size:
                candidate_units, _ = self._resolve_units_with_count(
                    target,
                    limit=candidate_page_size,
                    offset=cursor,
                )
            next_cursor = cursor + len(candidate_units)
            has_more_candidates = next_cursor < candidate_count
            return {
                "status": "ambiguous",
                "error": "Graph target resolves to multiple structural units",
                "snapshot": self.snapshot(),
                "pattern": pattern,
                "target": target,
                "candidates": [
                    unit_to_manifest(row) for row in candidate_units
                ],
                "candidateCount": candidate_count,
                "candidateResultCount": len(candidate_units),
                "candidatesTruncated": candidate_count > len(candidate_units),
                "hint": (
                    "Retry with a candidate unitId; request the same pattern and "
                    "target with nextCursor to inspect additional candidates."
                    if has_more_candidates
                    else "Retry with a candidate unitId."
                ),
                "results": [],
                "cursor": cursor,
                "nextCursor": next_cursor if has_more_candidates else None,
                "truncated": has_more_candidates,
                "resultCount": 0,
            }
        unit_ids = tuple(row["unit_id"] for row in candidate_units)
        short_target = _short_name(target).casefold()
        candidate_queries: list[tuple[str, tuple[Any, ...]]] = []

        def add_endpoint_candidates(column: str) -> None:
            # Resolve the exact text candidate through the normalized endpoint
            # index, then retain the original SQLite NOCASE comparison as the
            # final predicate. The latter preserves the previous matching
            # semantics for whitespace and non-ASCII text while avoiding a
            # complete relations-table scan for every graph pattern.
            candidate_queries.append((
                "SELECT relation_names.relation_id FROM relation_names "
                "JOIN relations AS exact_relation ON "
                "exact_relation.relation_id = relation_names.relation_id "
                "WHERE relation_names.normalized_name = ? "
                "AND relation_names.role = ? "
                f"AND exact_relation.{column} = ? COLLATE NOCASE",
                (_normalized_name(target), column, target),
            ))
            if unit_ids:
                placeholders = ",".join("?" for _ in unit_ids)
                candidate_queries.append((
                    "SELECT relation_id FROM relations WHERE "
                    f"{column}_unit_id IN ({placeholders})",
                    unit_ids,
                ))
                return
            # Only broaden an unresolved, already-unqualified target. A
            # qualified target is precise and must not pull in every unit that
            # happens to share its final component.
            if short_target != target.casefold():
                return
            fallback_clauses: list[str] = []
            fallback_parameters: list[str] = []
            for delimiter in (".", "/", "\\", ":"):
                fallback_clauses.append(
                    f"lower({column}) LIKE ? ESCAPE '\\'"
                )
                fallback_parameters.append(
                    "%" + _escape_like(delimiter + short_target)
                )
            candidate_queries.append((
                "SELECT relation_id FROM relations WHERE "
                + " OR ".join(fallback_clauses),
                tuple(fallback_parameters),
            ))

        if direction in {"outgoing", "both"}:
            add_endpoint_candidates("source")
        if direction in {"incoming", "both"}:
            add_endpoint_candidates("target")
        path_target = _optional_path(target)
        if direction == "both" and path_target is not None:
            candidate_queries.append((
                "SELECT relation_id FROM relation_paths WHERE path = ?",
                (path_target,),
            ))
        candidate_sql = " UNION ".join(
            query for query, _ in candidate_queries
        )
        params = [
            parameter
            for _, parameters in candidate_queries
            for parameter in parameters
        ]
        where_clauses: list[str] = []
        if kinds == {"__plugin__"}:
            where_clauses.append("relations.origin = 'plugin'")
        elif kinds:
            placeholders = ",".join("?" for _ in kinds)
            normalized_kinds = sorted(kinds)
            where_clauses.append(
                "(upper(replace(relations.kind, '-', '_')) IN ("
                f"{placeholders}) OR "
                "upper(replace(relations.relation, '-', '_')) IN ("
                f"{placeholders}))"
            )
            params.extend(normalized_kinds)
            params.extend(normalized_kinds)
        if pattern == "endpoints_for":
            endpoint_placeholders = ",".join(
                "?" for _ in _ENDPOINT_RELATION_KINDS
            )
            where_clauses.append(
                "(upper(replace(relations.kind, '-', '_')) IN ("
                f"{endpoint_placeholders}) OR EXISTS ("
                "SELECT 1 FROM units AS endpoint_units "
                "WHERE endpoint_units.unit_id = relations.target_unit_id "
                "AND upper(endpoint_units.kind) = 'ENDPOINT'))"
            )
            params.extend(sorted(_ENDPOINT_RELATION_KINDS))
        where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        rows = self.connection.execute(  # nosec B608
            "WITH candidate_relations(relation_id) AS ("
            f"{candidate_sql}) "
            "SELECT relations.* FROM candidate_relations "
            "JOIN relations ON relations.relation_id = "
            "candidate_relations.relation_id "
            f"{where} "
            "ORDER BY relations.path, relations.line, "
            "relations.relation_id LIMIT ? OFFSET ?",
            (*params, max_results + 1, cursor),
        ).fetchall()
        selected = rows[:max_results]
        truncated = len(rows) > max_results
        return {
            "snapshot": self.snapshot(),
            "pattern": pattern,
            "target": target,
            "resolvedUnits": [
                unit_to_manifest(row) for row in candidate_units
            ],
            "results": relations_to_manifests(self.connection, selected),
            "cursor": cursor,
            "nextCursor": cursor + len(selected) if truncated else None,
            "truncated": truncated,
            "resultCount": len(selected),
        }

    def search_units(
        self,
        query: str,
        *,
        max_results: int = 25,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        max_results = max(1, int(max_results))
        offset = max(0, int(offset))
        expression = _fts_query(query)
        rows: list[sqlite3.Row] = []
        fts_available = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'units_fts'"
        ).fetchone() is not None
        if expression and fts_available:
            try:
                rows = self.connection.execute(
                    "SELECT units.* FROM units_fts JOIN units "
                    "ON units.rowid = units_fts.rowid "
                    "WHERE units_fts MATCH ? "
                    "ORDER BY bm25(units_fts), units.unit_id LIMIT ? OFFSET ?",
                    (expression, max_results, offset),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            token = f"%{_escape_like(query.casefold())}%"
            rows = self.connection.execute(
                "SELECT * FROM units WHERE lower(path) LIKE ? ESCAPE '\\' "
                "OR lower(name) LIKE ? ESCAPE '\\' "
                "OR lower(qualified_name) LIKE ? ESCAPE '\\' "
                "ORDER BY path, start_line, unit_id LIMIT ? OFFSET ?",
                (token, token, token, max_results, offset),
            ).fetchall()
        return [unit_to_manifest(row) for row in rows]

    def _resolve_units(self, target: str) -> list[sqlite3.Row]:
        rows, _ = self._resolve_units_with_count(target)
        return rows

    def _resolve_units_with_count(
        self,
        target: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[sqlite3.Row], int]:
        """Resolve one stable candidate page and the exact candidate count."""

        limit = max(1, int(limit))
        offset = max(0, int(offset))
        direct = _unit_by_id(self.connection, target)
        if direct is not None:
            return [direct], 1
        path = _optional_path(target)
        if path:
            count = int(self.connection.execute(
                "SELECT COUNT(*) FROM units WHERE path = ?",
                (path,),
            ).fetchone()[0])
            rows = self.connection.execute(
                "SELECT * FROM units WHERE path = ? "
                "ORDER BY start_line, end_line, unit_id LIMIT ? OFFSET ?",
                (path, limit, offset),
            ).fetchall()
            if count:
                return rows, count

        def named_rows(normalized_name: str) -> tuple[list[sqlite3.Row], int]:
            count = int(self.connection.execute(
                "SELECT COUNT(*) FROM unit_names WHERE normalized_name = ?",
                (normalized_name,),
            ).fetchone()[0])
            if not count:
                return [], 0
            rows = self.connection.execute(
                "SELECT units.* FROM unit_names JOIN units "
                "ON units.unit_id = unit_names.unit_id "
                "WHERE unit_names.normalized_name = ? "
                "ORDER BY units.path, units.start_line, units.unit_id "
                "LIMIT ? OFFSET ?",
                (normalized_name, limit, offset),
            ).fetchall()
            return rows, count

        exact_name = _normalized_name(target)
        rows, count = named_rows(exact_name)
        if count:
            return rows, count
        short_name = _normalized_name(_short_name(target))
        if short_name != exact_name:
            return named_rows(short_name)
        return [], 0

    def _unit_query_response(
        self,
        pattern: str,
        target: str,
        rows: Sequence[sqlite3.Row],
        max_results: int,
        *,
        cursor: int = 0,
    ) -> dict[str, Any]:
        selected = rows[:max_results]
        truncated = len(rows) > max_results
        return {
            "snapshot": self.snapshot(),
            "pattern": pattern,
            "target": target,
            "results": [unit_to_manifest(row) for row in selected],
            "cursor": cursor,
            "nextCursor": cursor + len(selected) if truncated else None,
            "truncated": truncated,
            "resultCount": len(selected),
        }
