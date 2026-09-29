"""Incremental path removal and repository-output reconciliation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Callable


from .shared import _interrupt_sql_on_cancel, _normalize_path

from .write_state import GraphWriteState


class GraphReconciler:
    def __init__(self, state: GraphWriteState):
        self.state = state

    def refresh_counts(self) -> None:
        """Refresh counters and file ownership after cloning a sealed graph."""

        self.state.unit_count = int(
            self.state.connection.execute("SELECT count(*) FROM units").fetchone()[0]
        )
        self.state.relation_count = int(
            self.state.connection.execute("SELECT count(*) FROM relations").fetchone()[0]
        )
        self.state.file_unit_ids = {
            str(row["path"]): str(row["unit_id"])
            for row in self.state.connection.execute(
                "SELECT path, unit_id FROM units WHERE record_type = 'structural_file'"
            )
        }

    def _remember_names_for_units(self, unit_table: str) -> None:
        if not self.state.track_mutations:
            return
        rows = self.state.connection.execute(
            "SELECT DISTINCT unit_names.normalized_name FROM unit_names "
            f"JOIN {unit_table} ON {unit_table}.unit_id = unit_names.unit_id"  # nosec B608
        ).fetchall()
        self.state.touched_names.update(str(row["normalized_name"]) for row in rows)

    def remove_paths(
        self,
        paths: Sequence[str],
        *,
        cancellation_check: Callable[[], None] | None = None,
    ) -> tuple[tuple[str, ...], frozenset[str]]:
        """Remove file-owned graph rows and return the exact reparse closure.

        Cross-file non-packet facts name every related path in ``relation_paths``.
        Their origin files join the closure so a changed dependency never leaves
        an unchanged but stale per-file fact in the cloned generation.
        """

        with _interrupt_sql_on_cancel(self.state.connection, cancellation_check):
            return self._remove_paths(paths)

    def _remove_paths(
        self,
        paths: Sequence[str],
    ) -> tuple[tuple[str, ...], frozenset[str]]:
        """Apply path removal while the public method owns cancellation setup."""

        normalized = tuple(sorted({_normalize_path(path) for path in paths if path}))
        if not normalized:
            return (), frozenset()
        self.state.connection.executescript(
            "CREATE TEMP TABLE IF NOT EXISTS delta_paths("
            "path TEXT PRIMARY KEY);"
            "DELETE FROM delta_paths;"
            "CREATE TEMP TABLE IF NOT EXISTS delta_units("
            "unit_id TEXT PRIMARY KEY, unit_rowid INTEGER UNIQUE);"
            "DELETE FROM delta_units;"
        )
        self.state.connection.executemany(
            "INSERT OR IGNORE INTO delta_paths(path) VALUES (?)",
            ((path,) for path in normalized),
        )
        # A provider may report a removed directory. Expand it to concrete
        # stored descendants before computing relation dependencies.
        self.state.connection.execute(
            "INSERT OR IGNORE INTO delta_paths(path) "
            "SELECT DISTINCT units.path FROM units JOIN delta_paths requested "
            "ON units.path = requested.path "
            "OR units.path LIKE requested.path || '/%'"
        )
        while True:
            cursor = self.state.connection.execute(
                "INSERT OR IGNORE INTO delta_paths(path) "
                "SELECT DISTINCT relations.path FROM relations "
                "JOIN relation_paths ON relation_paths.relation_id = relations.relation_id "
                "JOIN delta_paths changed ON changed.path = relation_paths.path "
                "WHERE json_type(relations.attributes_json, '$.packetKind') IS NULL"
            )
            if cursor.rowcount <= 0:
                break

        affected_paths = tuple(
            str(row["path"])
            for row in self.state.connection.execute(
                "SELECT path FROM delta_paths ORDER BY path"
            )
        )
        old_document_paths = frozenset(
            str(row["path"])
            for row in self.state.connection.execute(
                "SELECT DISTINCT units.path FROM units JOIN delta_paths "
                "ON delta_paths.path = units.path "
                "WHERE units.record_type = 'source_unit' OR ("
                "units.record_type = 'plugin_context' AND "
                "json_extract(units.metadata_json, '$.content_type') = "
                "'architecture-source')"
            )
        )
        self.state.connection.execute(
            "INSERT OR IGNORE INTO delta_units(unit_id, unit_rowid) "
            "SELECT units.unit_id, units.rowid FROM units JOIN delta_paths "
            "ON delta_paths.path = units.path"
        )
        self._remember_names_for_units("delta_units")
        self.state.touched_relation_ids.update(
            str(row["relation_id"])
            for row in self.state.connection.execute(
                "SELECT relation_id FROM relations WHERE "
                "source_unit_id IN (SELECT unit_id FROM delta_units) OR "
                "target_unit_id IN (SELECT unit_id FROM delta_units)"
            )
        )
        self.state.connection.execute(
            "DELETE FROM relations WHERE path IN (SELECT path FROM delta_paths)"
        )
        if self.state.fts_available:
            self.state.connection.execute(
                "DELETE FROM units_fts WHERE rowid IN "
                "(SELECT unit_rowid FROM delta_units)"
            )
        self.state.connection.execute(
            "DELETE FROM units WHERE unit_id IN (SELECT unit_id FROM delta_units)"
        )
        for path in affected_paths:
            self.state.file_unit_ids.pop(path, None)
        return affected_paths, old_document_paths

    def remove_repository_analysis_outputs(self) -> None:
        """Remove repository-global plugin output while retaining file facts."""

        self.state.connection.executescript(
            "CREATE TEMP TABLE IF NOT EXISTS delta_global_units("
            "unit_id TEXT PRIMARY KEY, unit_rowid INTEGER UNIQUE);"
            "DELETE FROM delta_global_units;"
        )
        self.state.connection.execute(
            "INSERT OR IGNORE INTO delta_global_units(unit_id, unit_rowid) "
            "SELECT unit_id, rowid FROM units WHERE "
            "record_type IN ('plugin_symbol', 'repository_state') OR ("
            "record_type = 'plugin_context' AND "
            "json_type(metadata_json, '$.architecture_plugin') IS NOT NULL)"
        )
        self._remember_names_for_units("delta_global_units")
        self.state.touched_relation_ids.update(
            str(row["relation_id"])
            for row in self.state.connection.execute(
                "SELECT relation_id FROM relations WHERE "
                "source_unit_id IN (SELECT unit_id FROM delta_global_units) OR "
                "target_unit_id IN (SELECT unit_id FROM delta_global_units)"
            )
        )
        self.state.connection.execute(
            "DELETE FROM relations WHERE "
            "json_type(attributes_json, '$.packetKind') IS NOT NULL OR ("
            "origin = 'plugin' AND ("
            "source_unit_id IN (SELECT unit_id FROM delta_global_units) OR "
            "target_unit_id IN (SELECT unit_id FROM delta_global_units)))"
        )
        if self.state.fts_available:
            self.state.connection.execute(
                "DELETE FROM units_fts WHERE rowid IN "
                "(SELECT unit_rowid FROM delta_global_units)"
            )
        self.state.connection.execute(
            "DELETE FROM units WHERE unit_id IN "
            "(SELECT unit_id FROM delta_global_units)"
        )
        self.state.connection.execute("DELETE FROM repository_snapshots")

    def remove_repository_state_output(self) -> None:
        """Remove the replaceable repository-state unit and its FTS document."""

        self.state.connection.executescript(
            "CREATE TEMP TABLE IF NOT EXISTS delta_repository_state("
            "unit_id TEXT PRIMARY KEY, unit_rowid INTEGER UNIQUE);"
            "DELETE FROM delta_repository_state;"
        )
        self.state.connection.execute(
            "INSERT INTO delta_repository_state(unit_id, unit_rowid) "
            "SELECT unit_id, rowid FROM units WHERE record_type = 'repository_state'"
        )
        self._remember_names_for_units("delta_repository_state")
        if self.state.fts_available:
            self.state.connection.execute(
                "DELETE FROM units_fts WHERE rowid IN "
                "(SELECT unit_rowid FROM delta_repository_state)"
            )
        self.state.connection.execute(
            "DELETE FROM units WHERE unit_id IN "
            "(SELECT unit_id FROM delta_repository_state)"
        )

    def begin_repository_analysis_reconciliation(self) -> None:
        """Capture a complete replacement set without deleting equal output."""

        if self.state.capturing_repository_outputs:
            raise RuntimeError("repository output reconciliation is already active")
        # Prepare reusable temporary tables with individual statements:
        # sqlite3.executescript() would commit the surrounding delta transaction.
        self.state.connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS keep_repository_units("
            "unit_id TEXT PRIMARY KEY)"
        )
        self.state.connection.execute("DELETE FROM keep_repository_units")
        self.state.connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS keep_repository_relations("
            "relation_id TEXT PRIMARY KEY)"
        )
        self.state.connection.execute("DELETE FROM keep_repository_relations")
        self.state.connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS keep_repository_relation_plugins("
            "relation_id TEXT NOT NULL, plugin_id TEXT NOT NULL, "
            "PRIMARY KEY(relation_id, plugin_id))"
        )
        self.state.connection.execute("DELETE FROM keep_repository_relation_plugins")
        self.state.connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS affected_repository_relations("
            "relation_id TEXT PRIMARY KEY)"
        )
        self.state.connection.execute("DELETE FROM affected_repository_relations")
        self.state.connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS keep_repository_snapshots("
            "plugin_id TEXT NOT NULL, kind TEXT NOT NULL, "
            "PRIMARY KEY(plugin_id, kind))"
        )
        self.state.connection.execute("DELETE FROM keep_repository_snapshots")
        self.state.connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS old_repository_units("
            "unit_id TEXT PRIMARY KEY, unit_rowid INTEGER UNIQUE)"
        )
        self.state.connection.execute("DELETE FROM old_repository_units")
        self.state.connection.execute("SAVEPOINT repository_analysis_reconcile")
        self.state.capturing_repository_outputs = True
        self.state.repository_output_unit_ids.clear()
        self.state.repository_output_relation_plugins.clear()
        self.state.repository_output_snapshots.clear()

    def abort_repository_analysis_reconciliation(self) -> None:
        if self.state.capturing_repository_outputs:
            self.state.connection.execute("ROLLBACK TO repository_analysis_reconcile")
            self.state.connection.execute("RELEASE repository_analysis_reconcile")
            # Counters are in Python, so SQLite rollback cannot restore them.
            # Avoid stale progress and a false unit-limit rejection after an
            # optional finalizer wrote output before failing.
            self.refresh_counts()
        self.state.capturing_repository_outputs = False
        self.state.repository_output_unit_ids.clear()
        self.state.repository_output_relation_plugins.clear()
        self.state.repository_output_snapshots.clear()

    def reconcile_repository_analysis_outputs(self) -> None:
        """Delete only repository-global output absent from the new analysis."""

        if not self.state.capturing_repository_outputs:
            raise RuntimeError("repository output reconciliation is not active")
        self.state.connection.executemany(
            "INSERT INTO keep_repository_units(unit_id) VALUES (?)",
            ((unit_id,) for unit_id in sorted(self.state.repository_output_unit_ids)),
        )
        self.state.connection.executemany(
            "INSERT INTO keep_repository_relations(relation_id) VALUES (?)",
            (
                (relation_id,)
                for relation_id in sorted(
                    self.state.repository_output_relation_plugins
                )
            ),
        )
        self.state.connection.executemany(
            "INSERT INTO keep_repository_relation_plugins("
            "relation_id, plugin_id) VALUES (?, ?)",
            (
                (relation_id, plugin_id)
                for relation_id, plugin_ids in sorted(
                    self.state.repository_output_relation_plugins.items()
                )
                for plugin_id in sorted(plugin_ids)
            ),
        )
        self.state.connection.executemany(
            "INSERT INTO keep_repository_snapshots(plugin_id, kind) VALUES (?, ?)",
            sorted(self.state.repository_output_snapshots),
        )
        self.state.connection.execute(
            "INSERT INTO old_repository_units(unit_id, unit_rowid) "
            "SELECT unit_id, rowid FROM units WHERE "
            "record_type = 'plugin_symbol' OR ("
            "record_type = 'plugin_context' AND "
            "json_type(metadata_json, '$.architecture_plugin') IS NOT NULL)"
        )

        self.state.connection.execute(
            "INSERT OR IGNORE INTO affected_repository_relations(relation_id) "
            "SELECT relation_id FROM relation_scopes WHERE scope = 'repository'"
        )
        self.state.connection.execute(
            "INSERT OR IGNORE INTO affected_repository_relations(relation_id) "
            "SELECT relation_id FROM keep_repository_relations"
        )
        self.state.connection.execute(
            "DELETE FROM relation_plugin_scopes WHERE scope = 'repository' "
            "AND NOT EXISTS (SELECT 1 FROM keep_repository_relation_plugins keep "
            "WHERE keep.relation_id = relation_plugin_scopes.relation_id "
            "AND keep.plugin_id = relation_plugin_scopes.plugin_id)"
        )
        self.state.connection.execute(
            "DELETE FROM relation_scopes WHERE scope = 'repository' "
            "AND relation_id NOT IN (SELECT relation_id FROM keep_repository_relations)"
        )
        self.state.connection.execute(
            "DELETE FROM relations WHERE relation_id IN "
            "(SELECT relation_id FROM affected_repository_relations) "
            "AND NOT EXISTS (SELECT 1 FROM relation_scopes ownership "
            "WHERE ownership.relation_id = relations.relation_id)"
        )

        # Synchronize the canonical contributor union only when ownership
        # changes. Equal rows retain their manifest-cache entry.
        current_plugins: dict[str, set[str]] = {}
        for row in self.state.connection.execute(
            "SELECT relation_plugins.relation_id, relation_plugins.plugin_id "
            "FROM relation_plugins JOIN affected_repository_relations "
            "USING (relation_id)"
        ):
            current_plugins.setdefault(str(row["relation_id"]), set()).add(
                str(row["plugin_id"])
            )
        desired_plugins: dict[str, set[str]] = {}
        for row in self.state.connection.execute(
            "SELECT relation_plugin_scopes.relation_id, "
            "relation_plugin_scopes.plugin_id FROM relation_plugin_scopes "
            "JOIN affected_repository_relations USING (relation_id)"
        ):
            desired_plugins.setdefault(str(row["relation_id"]), set()).add(
                str(row["plugin_id"])
            )
        affected_relation_ids = {
            str(row["relation_id"])
            for row in self.state.connection.execute(
                "SELECT relation_id FROM affected_repository_relations"
            )
        }
        for relation_id in sorted(affected_relation_ids):
            desired = desired_plugins.get(relation_id, set())
            if current_plugins.get(relation_id, set()) == desired:
                continue
            self.state.connection.execute(
                "DELETE FROM relation_plugins WHERE relation_id = ?",
                (relation_id,),
            )
            self.state.connection.executemany(
                "INSERT INTO relation_plugins(relation_id, plugin_id) VALUES (?, ?)",
                ((relation_id, plugin_id) for plugin_id in sorted(desired)),
            )
            self.state.connection.execute(
                "UPDATE relations SET plugin_id = ? WHERE relation_id = ?",
                (
                    next(iter(desired)) if len(desired) == 1 else None,
                    relation_id,
                ),
            )

        if self.state.fts_available:
            self.state.connection.execute(
                "DELETE FROM units_fts WHERE rowid IN ("
                "SELECT unit_rowid FROM old_repository_units WHERE unit_id NOT IN "
                "(SELECT unit_id FROM keep_repository_units))"
            )
        self.state.connection.execute(
            "DELETE FROM units WHERE unit_id IN (SELECT unit_id FROM old_repository_units) "
            "AND unit_id NOT IN (SELECT unit_id FROM keep_repository_units)"
        )
        self.state.connection.execute(
            "DELETE FROM repository_snapshots WHERE NOT EXISTS ("
            "SELECT 1 FROM keep_repository_snapshots keep WHERE "
            "keep.plugin_id = repository_snapshots.plugin_id AND "
            "keep.kind = repository_snapshots.kind)"
        )
        self.refresh_counts()
        self.state.connection.execute("RELEASE repository_analysis_reconcile")
        self.state.capturing_repository_outputs = False
        self.state.repository_output_unit_ids.clear()
        self.state.repository_output_relation_plugins.clear()
        self.state.repository_output_snapshots.clear()
