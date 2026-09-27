"""Public pending-graph writer composed from focused persistence services."""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Callable

from ..documents import TextNode
from .write_state import GraphWriteState
from .units import UnitWriter
from .relations import RelationWriter
from .reconciliation import GraphReconciler
from .resolution import RelationResolver
from .shared import _canonical_json, _interrupt_sql_on_cancel


class StructuralGraphWriter:
    """Coordinate a pending graph while collaborators own individual concerns."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        track_mutations: bool = False,
        defer_file_relation_ownership: bool = False,
    ):
        self._state = GraphWriteState(
            connection,
            track_mutations=track_mutations,
            defer_file_relation_ownership=defer_file_relation_ownership,
        )
        self._relations = RelationWriter(self._state)
        self._units = UnitWriter(self._state, self._relations)
        self._reconciliation = GraphReconciler(self._state)
        self._resolution = RelationResolver(self._state)

    @property
    def connection(self):
        return self._state.connection

    @property
    def unit_count(self):
        return self._state.unit_count

    @property
    def relation_count(self):
        return self._state.relation_count

    @property
    def _file_unit_ids(self):
        return self._state.file_unit_ids

    @property
    def _track_mutations(self):
        return self._state.track_mutations

    @property
    def _defer_file_relation_ownership(self):
        return self._state.defer_file_relation_ownership

    @property
    def _touched_relation_ids(self):
        return self._state.touched_relation_ids

    @property
    def _touched_names(self):
        return self._state.touched_names

    @property
    def _explicit_source_relation_ids(self):
        return self._state.explicit_source_relation_ids

    @property
    def _explicit_target_relation_ids(self):
        return self._state.explicit_target_relation_ids

    @property
    def _capturing_repository_outputs(self):
        return self._state.capturing_repository_outputs

    @property
    def _repository_output_unit_ids(self):
        return self._state.repository_output_unit_ids

    @property
    def _repository_output_relation_plugins(self):
        return self._state.repository_output_relation_plugins

    @property
    def _repository_output_snapshots(self):
        return self._state.repository_output_snapshots

    @property
    def fts_available(self):
        return self._state.fts_available

    def add_unit(
        self,
        node: TextNode,
        *,
        record_type: str = "source_unit",
        metadata_override: Mapping[str, Any] | None = None,
    ) -> str:
        return self._units.add_unit(
            node,
            record_type=record_type,
            metadata_override=metadata_override,
        )

    def add_file(self, node: TextNode) -> str:
        return self._units.add_file(node)

    def add_symbol(
        self,
        symbol: Any,
        *,
        plugin_id: str | None = None,
        plugin_ids: Sequence[str] = (),
    ) -> str:
        return self._units.add_symbol(symbol, plugin_id=plugin_id, plugin_ids=plugin_ids)

    def add_context(self, context: Any) -> str:
        return self._units.add_context(context)

    def add_snapshot(self, snapshot: Any) -> None:
        return self._units.add_snapshot(snapshot)

    def add_file_containment(
        self,
        file_unit_id: str,
        child_unit_id: str,
        node: TextNode,
    ) -> str:
        return self._relations.add_file_containment(file_unit_id, child_unit_id, node)

    def add_ast_relations(self, unit_id: str, node: TextNode) -> None:
        return self._relations.add_ast_relations(unit_id, node)

    def add_graph_fact(
        self,
        fact: Any,
        *,
        plugin_id: str | None,
        packet_kind: str | None = None,
        packet_key: str | None = None,
    ) -> str:
        return self._relations.add_graph_fact(
            fact,
            plugin_id=plugin_id,
            packet_kind=packet_kind,
            packet_key=packet_key,
        )

    def add_relation(
        self,
        *,
        kind: str,
        source: str,
        relation: str,
        target: str,
        path: str,
        line: int,
        origin: str,
        plugin_id: str | None = None,
        plugin_ids: Sequence[str] = (),
        attributes: Mapping[str, Any] | None = None,
        related_paths: Sequence[str] = (),
        source_unit_id: str | None = None,
        target_unit_id: str | None = None,
        identity_discriminator: str | None = None,
    ) -> str:
        return self._relations.add_relation(
            kind=kind,
            source=source,
            relation=relation,
            target=target,
            path=path,
            line=line,
            origin=origin,
            plugin_id=plugin_id,
            plugin_ids=plugin_ids,
            attributes=attributes,
            related_paths=related_paths,
            source_unit_id=source_unit_id,
            target_unit_id=target_unit_id,
            identity_discriminator=identity_discriminator,
        )

    def _record_relation_ownership(
        self,
        relation_id: str,
        plugin_ids: Sequence[str],
    ) -> None:
        return self._relations._record_relation_ownership(relation_id, plugin_ids)

    def flush_deferred_file_relation_ownership(self) -> None:
        return self._relations.flush_deferred_file_relation_ownership()

    def refresh_counts(self) -> None:
        return self._reconciliation.refresh_counts()

    def _remember_names_for_units(self, unit_table: str) -> None:
        return self._reconciliation._remember_names_for_units(unit_table)

    def remove_paths(
        self,
        paths: Sequence[str],
        *,
        cancellation_check: Callable[[], None] | None = None,
    ) -> tuple[tuple[str, ...], frozenset[str]]:
        with _interrupt_sql_on_cancel(self.connection, cancellation_check):
            return self._remove_paths(paths)

    def _remove_paths(
        self,
        paths: Sequence[str],
    ) -> tuple[tuple[str, ...], frozenset[str]]:
        return self._reconciliation._remove_paths(paths)

    def remove_repository_analysis_outputs(self) -> None:
        return self._reconciliation.remove_repository_analysis_outputs()

    def remove_repository_state_output(self) -> None:
        return self._reconciliation.remove_repository_state_output()

    def begin_repository_analysis_reconciliation(self) -> None:
        return self._reconciliation.begin_repository_analysis_reconciliation()

    def abort_repository_analysis_reconciliation(self) -> None:
        return self._reconciliation.abort_repository_analysis_reconciliation()

    def reconcile_repository_analysis_outputs(self) -> None:
        return self._reconciliation.reconcile_repository_analysis_outputs()

    def invalidate_touched_name_resolutions(self) -> None:
        return self._resolution.invalidate_touched_name_resolutions()

    def resolve_relations(
        self,
        relation_ids: Iterable[str] | None = None,
    ) -> None:
        return self._resolution.resolve_relations(relation_ids)

    def resolve_touched_relations(self) -> None:
        return self._resolution.resolve_touched_relations()

    def _unique_named_unit(
        self,
        value: str,
        *,
        preferred_paths: Sequence[str] = (),
        allow_short_name: bool = True,
    ) -> str | None:
        return self._resolution._unique_named_unit(
            value,
            preferred_paths=preferred_paths,
            allow_short_name=allow_short_name,
        )

    def seal(self, receipt: Mapping[str, Any]) -> None:
        encoded = _canonical_json(dict(receipt))
        self.connection.execute(
            "INSERT INTO generation(singleton, receipt_json, sealed_at) VALUES (1, ?, ?)",
            (encoded, time.time()),
        )
        self.connection.commit()
        self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.connection.commit()
