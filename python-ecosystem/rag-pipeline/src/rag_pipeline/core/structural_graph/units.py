"""Source units, exact metadata and plugin source persistence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..documents import TextNode

from .shared import (
    _canonical_json,
    _sha256_text,
    _normalize_path,
    _string_list,
    _first_string,
    _unit_id,
    _normalized_name,
    _short_name,
)

from .write_state import GraphWriteState
from .relations import RelationWriter


class UnitWriter:
    def __init__(self, state: GraphWriteState, relations: RelationWriter):
        self.state = state
        self.relations = relations

    def add_unit(
        self,
        node: TextNode,
        *,
        record_type: str = "source_unit",
        metadata_override: Mapping[str, Any] | None = None,
    ) -> str:
        metadata = dict(node.metadata)
        if metadata_override:
            metadata.update(metadata_override)
        path = _normalize_path(str(metadata.get("path") or ""))
        content = str(node.text or "")
        start_line = max(1, int(metadata.get("start_line") or 1))
        end_line = max(start_line, int(metadata.get("end_line") or start_line))
        name = _first_string(
            metadata,
            "primary_name",
            "symbol_qualified_name",
            "full_path",
            "parent_class",
        ) or Path(path).name
        qualified_name = _first_string(
            metadata,
            "symbol_qualified_name",
            "full_path",
        ) or f"{path}:{start_line}-{end_line}:{name}"
        kind = _first_string(
            metadata,
            "symbol_kind",
            "node_type",
            "content_type",
        ) or record_type
        language = str(metadata.get("language") or "")
        content_sha256 = _sha256_text(content)
        unit_id = _unit_id(
            record_type,
            path,
            qualified_name,
            start_line,
            end_line,
            content_sha256,
        )
        names = {
            name,
            qualified_name,
            _short_name(name),
            _short_name(qualified_name),
            *_string_list(metadata.get("symbol_names")),
            *_string_list(metadata.get("architecture_identifiers")),
        }
        namespace = str(metadata.get("namespace") or "").strip(" \\")
        parent_class = str(metadata.get("parent_class") or "").strip()
        if namespace and parent_class and name:
            qualified_owner = f"{namespace}\\{parent_class}"
            names.update({
                qualified_owner,
                f"{qualified_owner}::{name}",
                f"{qualified_owner}.{name}",
            })
        elif namespace and name:
            names.add(f"{namespace}\\{name}")
        encoded_metadata = _canonical_json(metadata)
        capture_repository_unit = self.state.capturing_repository_outputs and (
            record_type == "plugin_symbol"
            or (
                record_type == "plugin_context"
                and metadata.get("architecture_plugin") is not None
            )
        )
        if capture_repository_unit:
            self.state.repository_output_unit_ids.add(unit_id)
        stored_values = (
            record_type,
            path,
            language,
            kind,
            name,
            qualified_name,
            start_line,
            end_line,
            content,
            content_sha256,
            encoded_metadata,
        )
        # The external-content layout stores the exact alias stream once on
        # its owning unit. Old cloned databases have no such derived column.
        search_names = " ".join(sorted(names))
        search_column = ", search_names" if self.state.external_fts else ""
        search_placeholder = ", ?" if self.state.external_fts else ""
        search_value = (search_names,) if self.state.external_fts else ()
        unit_row = self.state.connection.execute(
            "INSERT INTO units("
            "unit_id, record_type, path, language, kind, name, "
            "qualified_name, start_line, end_line, content, "
            "content_sha256, metadata_json" + search_column + ") "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?" + search_placeholder + ") "
            "ON CONFLICT(unit_id) DO NOTHING RETURNING rowid",
            (unit_id, *stored_values, *search_value),
        ).fetchone()
        if unit_row is not None:
            inserted = True
            unit_rowid = int(unit_row["rowid"])
        else:
            inserted = False
            existing_unit = self.state.connection.execute(
                "SELECT rowid, record_type, path, language, kind, name, "
                "qualified_name, start_line, end_line, content, content_sha256, "
                "metadata_json FROM units WHERE unit_id = ?",
                (unit_id,),
            ).fetchone()
            if existing_unit is None:
                raise RuntimeError(
                    "structural unit insert conflicted but its row is unavailable"
                )
            if tuple(
                existing_unit[key]
                for key in (
                    "record_type",
                    "path",
                    "language",
                    "kind",
                    "name",
                    "qualified_name",
                    "start_line",
                    "end_line",
                    "content",
                    "content_sha256",
                    "metadata_json",
                )
            ) == stored_values:
                # Repository finalizers emit a complete deterministic snapshot.
                # A cloned generation already contains most of those rows; avoid
                # rewriting their names and FTS documents into the WAL.
                return unit_id
            if self.state.fts_available:
                # External-content FTS must read the old source and aliases
                # before their owning row changes. Rowid is a direct lookup.
                self.state.connection.execute(
                    "DELETE FROM units_fts WHERE rowid = ?",
                    (int(existing_unit["rowid"]),),
                )
            search_assignment = ", search_names = ?" if self.state.external_fts else ""
            unit_row = self.state.connection.execute(
                """UPDATE units SET
                       record_type = ?, path = ?, language = ?, kind = ?, name = ?,
                       qualified_name = ?, start_line = ?, end_line = ?, content = ?,
                       content_sha256 = ?, metadata_json = ?"""
                + search_assignment + " WHERE unit_id = ? RETURNING rowid",
                (*stored_values, *search_value, unit_id),
            ).fetchone()
            if unit_row is None:
                raise RuntimeError("structural unit update did not return its rowid")
            unit_rowid = int(unit_row["rowid"])
            self.state.connection.execute(
                "DELETE FROM unit_names WHERE unit_id = ?",
                (unit_id,),
            )
        if self.state.track_mutations:
            self.state.touched_names.update(
                _normalized_name(value)
                for value in names
                if value and value.strip()
            )
        name_rows = tuple(
            (_normalized_name(value), value, unit_id)
            for value in sorted(
                item.strip() for item in names if item and item.strip()
            )
        )
        self.state.connection.executemany(
            "INSERT OR IGNORE INTO unit_names(normalized_name, display_name, unit_id) "
            "VALUES (?, ?, ?)",
            name_rows,
        )
        if self.state.fts_available:
            # unit_id is intentionally UNINDEXED in FTS5. Sharing the stable
            # units rowid makes both first writes and duplicate replacement
            # constant-time without a full virtual-table delete scan.
            self.state.connection.execute(
                "INSERT OR REPLACE INTO units_fts("
                "rowid, unit_id, path, name, qualified_name, symbols, content) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    unit_rowid,
                    unit_id,
                    path,
                    name,
                    qualified_name,
                    search_names,
                    content,
                ),
            )
        if inserted:
            self.state.unit_count += 1
        return unit_id

    def add_file(self, node: TextNode) -> str:
        """Add the repository file that owns source/plugin structural units."""
        metadata = dict(node.metadata)
        path = _normalize_path(str(metadata.get("path") or ""))
        file_unit_id = self.add_unit(
            TextNode(
                text=f"Repository file {path}",
                metadata={
                    **metadata,
                    "path": path,
                    "start_line": 1,
                    "end_line": max(1, str(node.text or "").count("\n") + 1),
                    "primary_name": Path(path).name,
                    "symbol_qualified_name": path,
                    "symbol_kind": "file",
                },
            ),
            record_type="structural_file",
        )
        self.state.file_unit_ids[path] = file_unit_id
        return file_unit_id

    def add_symbol(
        self,
        symbol: Any,
        *,
        plugin_id: str | None = None,
        plugin_ids: Sequence[str] = (),
    ) -> str:
        normalized_plugin_ids = tuple(sorted({
            value.strip()
            for value in (plugin_id, *plugin_ids)
            if isinstance(value, str) and value.strip()
        }))
        metadata = {
            "path": str(symbol.path),
            "language": "structural-symbol",
            "start_line": int(symbol.line),
            "end_line": int(symbol.line),
            "primary_name": _short_name(str(symbol.qualified_name)),
            "symbol_qualified_name": str(symbol.qualified_name),
            "symbol_kind": str(symbol.kind),
            "symbol_names": [
                _short_name(str(symbol.qualified_name)),
                str(symbol.qualified_name),
            ],
            "symbol_parents": list(symbol.parents),
            "symbol_methods": list(symbol.methods),
            "symbol_constructor_types": list(symbol.constructor_types),
            "symbol_attributes": dict(symbol.attributes),
            "plugin_id": (
                normalized_plugin_ids[0]
                if len(normalized_plugin_ids) == 1
                else None
            ),
            "plugin_ids": list(normalized_plugin_ids),
        }
        unit_id = self.add_unit(
            TextNode(
                text=f"{symbol.kind} {symbol.qualified_name}",
                metadata=metadata,
            ),
            record_type="plugin_symbol",
        )
        for parent in symbol.parents:
            self.relations.add_relation(
                kind="INHERITS",
                source=str(symbol.qualified_name),
                relation="inherits",
                target=str(parent),
                path=str(symbol.path),
                line=int(symbol.line),
                origin="plugin",
                plugin_ids=normalized_plugin_ids,
                source_unit_id=unit_id,
            )
        for dependency in symbol.constructor_types:
            self.relations.add_relation(
                kind="CONSTRUCTOR_DEPENDENCY",
                source=str(symbol.qualified_name),
                relation="constructor-depends-on",
                target=str(dependency),
                path=str(symbol.path),
                line=int(symbol.line),
                origin="plugin",
                plugin_ids=normalized_plugin_ids,
                source_unit_id=unit_id,
            )
        normalized_path = _normalize_path(str(symbol.path))
        file_unit_id = self.state.file_unit_ids.get(normalized_path)
        if file_unit_id:
            self.relations.add_relation(
                kind="CONTAINS",
                source=normalized_path,
                relation="contains",
                target=str(symbol.qualified_name),
                path=normalized_path,
                line=int(symbol.line),
                origin="plugin",
                plugin_ids=normalized_plugin_ids,
                source_unit_id=file_unit_id,
                target_unit_id=unit_id,
                identity_discriminator=unit_id,
            )
        return unit_id

    def add_context(self, context: Any) -> str:
        line_count = str(context.content).count("\n") + 1
        unit_id = self.add_unit(
            TextNode(
                text=str(context.content),
                metadata={
                    "path": str(context.path),
                    "language": "architecture-source",
                    "start_line": 1,
                    "end_line": line_count,
                    "primary_name": f"{context.plugin_id}:{context.kind}",
                    "architecture_plugin": str(context.plugin_id),
                    "architecture_source_kind": str(context.kind),
                    "architecture_attributes": dict(context.attributes),
                },
            ),
            record_type="plugin_context",
        )
        normalized_path = _normalize_path(str(context.path))
        file_unit_id = self.state.file_unit_ids.get(normalized_path)
        if file_unit_id:
            self.relations.add_relation(
                kind="CONTAINS",
                source=normalized_path,
                relation="contains",
                target=f"{context.plugin_id}:{context.kind}",
                path=normalized_path,
                line=1,
                origin="plugin",
                plugin_id=str(context.plugin_id),
                source_unit_id=file_unit_id,
                target_unit_id=unit_id,
                identity_discriminator=unit_id,
            )
        return unit_id

    def add_snapshot(self, snapshot: Any) -> None:
        content = str(snapshot.content)
        self.state.connection.execute(
            "INSERT INTO repository_snapshots("
            "plugin_id, kind, content, content_sha256) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(plugin_id, kind) DO UPDATE SET "
            "content = excluded.content, "
            "content_sha256 = excluded.content_sha256 "
            "WHERE repository_snapshots.content IS NOT excluded.content OR "
            "repository_snapshots.content_sha256 IS NOT excluded.content_sha256",
            (
                str(snapshot.plugin_id),
                str(snapshot.kind),
                content,
                _sha256_text(content),
            ),
        )
        if self.state.capturing_repository_outputs:
            self.state.repository_output_snapshots.add((
                str(snapshot.plugin_id),
                str(snapshot.kind),
            ))
