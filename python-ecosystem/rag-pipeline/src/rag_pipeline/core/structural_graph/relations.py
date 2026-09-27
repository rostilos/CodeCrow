"""Canonical logical edge persistence and contributor ownership."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..documents import TextNode

from .shared import (
    _canonical_json,
    _normalize_path,
    _string_list,
    _first_string,
    _relation_id,
    _normalized_name,
)

from .write_state import GraphWriteState


class RelationWriter:
    def __init__(self, state: GraphWriteState):
        self.state = state

    def add_file_containment(
        self,
        file_unit_id: str,
        child_unit_id: str,
        node: TextNode,
    ) -> str:
        """Connect a repository file to one of its concrete structural units."""
        metadata = dict(node.metadata)
        path = _normalize_path(str(metadata.get("path") or ""))
        child_name = _first_string(
            metadata,
            "symbol_qualified_name",
            "full_path",
            "primary_name",
        ) or f"{path}:{max(1, int(metadata.get('start_line') or 1))}"
        return self.add_relation(
            kind="CONTAINS",
            source=path,
            relation="contains",
            target=child_name,
            path=path,
            line=max(1, int(metadata.get("start_line") or 1)),
            origin="structural-index",
            source_unit_id=file_unit_id,
            target_unit_id=child_unit_id,
            identity_discriminator=child_unit_id,
        )

    def add_ast_relations(self, unit_id: str, node: TextNode) -> None:
        metadata = dict(node.metadata)
        path = _normalize_path(str(metadata.get("path") or ""))
        source = _first_string(
            metadata,
            "symbol_qualified_name",
            "full_path",
            "primary_name",
        ) or Path(path).name
        line = max(1, int(metadata.get("start_line") or 1))
        mappings = (
            ("IMPORTS", "imports"),
            ("EXTENDS", "extends"),
            ("IMPLEMENTS", "implements"),
            ("CALLS", "calls"),
            ("REFERENCES", "referenced_types"),
        )
        for kind, field in mappings:
            for target in sorted(set(_string_list(metadata.get(field)))):
                self.add_relation(
                    kind=kind,
                    source=source,
                    relation=kind.lower(),
                    target=target,
                    path=path,
                    line=line,
                    origin="tree-sitter",
                    source_unit_id=unit_id,
                )

        parent_context = _string_list(metadata.get("parent_context"))
        for index, _ in enumerate(parent_context):
            parent = ".".join(parent_context[:index + 1])
            child = (
                ".".join(parent_context[:index + 2])
                if index + 1 < len(parent_context)
                else source
            )
            if not parent or not child or parent == child:
                continue
            self.add_relation(
                kind="CONTAINS",
                source=parent,
                relation="contains",
                target=child,
                path=path,
                line=line,
                origin="tree-sitter",
                target_unit_id=(
                    unit_id if index + 1 == len(parent_context) else None
                ),
            )

    def add_graph_fact(
        self,
        fact: Any,
        *,
        plugin_id: str | None,
        packet_kind: str | None = None,
        packet_key: str | None = None,
    ) -> str:
        attributes = dict(getattr(fact, "attributes", ()) or ())
        source = str(fact.source)
        target = str(fact.target)
        fact_kind = str(fact.kind)
        if fact_kind in {
            "php-intra-class-call-relation",
            "php-instance-call-relation",
            "php-static-call-relation",
        }:
            caller_method = str(attributes.get("callerMethod") or "").strip()
            target_method = str(attributes.get("targetMethod") or "").strip()
            target_declared = (
                str(attributes.get("targetMethodDeclared") or "").casefold()
                == "true"
            )
            target_owner = str(
                attributes.get("targetMethodDeclaredOn") or ""
            ).strip()
            if caller_method:
                attributes["declaringSource"] = source
                source = f"{source}::{caller_method}"
            if target_method:
                attributes["declaringTarget"] = target
                target_resolution_proven = target_declared and bool(target_owner)
                attributes["targetResolutionProven"] = target_resolution_proven
                target = (
                    f"{target_owner if target_resolution_proven else target}"
                    f"::{target_method}"
                )
        if packet_kind:
            attributes["packetKind"] = packet_kind
        if packet_key:
            attributes["packetKey"] = packet_key
        return self.add_relation(
            kind=fact_kind,
            source=source,
            relation=str(fact.relation),
            target=target,
            path=_normalize_path(str(fact.path)),
            line=max(1, int(fact.line)),
            origin="plugin",
            plugin_id=plugin_id,
            plugin_ids=tuple(
                str(value)
                for value in getattr(fact, "contributing_plugin_ids", ())
            ),
            attributes=attributes,
            related_paths=tuple(str(path) for path in fact.related_paths),
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
        normalized_path = _normalize_path(path)
        normalized_related = tuple(sorted({
            _normalize_path(value) for value in related_paths if value
        }))
        normalized_plugin_ids = tuple(sorted({
            value.strip()
            for value in (plugin_id, *plugin_ids)
            if isinstance(value, str) and value.strip()
        }))
        projection = {
            "kind": str(kind),
            "source": str(source),
            "relation": str(relation),
            "target": str(target),
            "path": normalized_path,
            "line": max(1, int(line)),
            "origin": str(origin),
            "attributes": dict(attributes or {}),
            "relatedPaths": normalized_related,
        }
        identity_projection = projection
        if identity_discriminator is not None:
            # Concrete containment edges can share every human-readable field
            # while still pointing at distinct source units. Keep that storage
            # identity out of the displayed relation payload, but include it in
            # the edge key so each concrete child remains reachable.
            identity_projection = {
                **projection,
                "_identityDiscriminator": str(identity_discriminator),
            }
        relation_id = _relation_id(identity_projection)
        if self.state.capturing_repository_outputs:
            self.state.repository_output_relation_plugins.setdefault(
                relation_id,
                set(),
            ).update(normalized_plugin_ids)
            existing_relation = self.state.connection.execute(
                "SELECT source_unit_id, target_unit_id FROM relations "
                "WHERE relation_id = ?",
                (relation_id,),
            ).fetchone()
            if (
                existing_relation is not None
                and (
                    source_unit_id is None
                    or existing_relation["source_unit_id"] is not None
                )
                and (
                    target_unit_id is None
                    or existing_relation["target_unit_id"] is not None
                )
            ):
                self._record_relation_ownership(
                    relation_id,
                    normalized_plugin_ids,
                )
                # The relation identity covers its complete logical payload and
                # related-path set. Contributor reconciliation below handles a
                # changed plugin set in bulk; a cloned equal row needs no SQL
                # writes or per-edge contributor scans here.
                return relation_id
        cursor = self.state.connection.execute(
            """INSERT OR IGNORE INTO relations(
                   relation_id, kind, source, relation, target,
                   source_unit_id, target_unit_id, path, line, origin,
                   plugin_id, attributes_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                relation_id,
                projection["kind"],
                projection["source"],
                projection["relation"],
                projection["target"],
                source_unit_id,
                target_unit_id,
                normalized_path,
                projection["line"],
                projection["origin"],
                normalized_plugin_ids[0]
                if len(normalized_plugin_ids) == 1
                else None,
                _canonical_json(projection["attributes"]),
            ),
        )
        inserted = cursor.rowcount > 0
        endpoint_changed = False
        if not inserted and (source_unit_id or target_unit_id):
            endpoint_cursor = self.state.connection.execute(
                "UPDATE relations SET "
                "source_unit_id = coalesce(source_unit_id, ?), "
                "target_unit_id = coalesce(target_unit_id, ?) "
                "WHERE relation_id = ? AND ("
                "(source_unit_id IS NULL AND ? IS NOT NULL) OR "
                "(target_unit_id IS NULL AND ? IS NOT NULL))",
                (
                    source_unit_id,
                    target_unit_id,
                    relation_id,
                    source_unit_id,
                    target_unit_id,
                ),
            )
            endpoint_changed = endpoint_cursor.rowcount > 0
        if self.state.track_mutations and (inserted or endpoint_changed):
            self.state.touched_relation_ids.add(relation_id)
            if source_unit_id:
                self.state.explicit_source_relation_ids.add(relation_id)
            if target_unit_id:
                self.state.explicit_target_relation_ids.add(relation_id)
        self._record_relation_ownership(
            relation_id,
            normalized_plugin_ids,
        )
        for contributor in normalized_plugin_ids:
            self.state.connection.execute(
                "INSERT OR IGNORE INTO relation_plugins(relation_id, plugin_id) "
                "VALUES (?, ?)",
                (relation_id, contributor),
            )
        if not inserted:
            # A fresh row already carries the correct denormalized value from
            # normalized_plugin_ids. Only a duplicate can add contributors.
            contributors = [
                row["plugin_id"]
                for row in self.state.connection.execute(
                    "SELECT plugin_id FROM relation_plugins WHERE relation_id = ? "
                    "ORDER BY plugin_id",
                    (relation_id,),
                ).fetchall()
            ]
            self.state.connection.execute(
                "UPDATE relations SET plugin_id = ? "
                "WHERE relation_id = ? AND plugin_id IS NOT ?",
                (
                    contributors[0] if len(contributors) == 1 else None,
                    relation_id,
                    contributors[0] if len(contributors) == 1 else None,
                ),
            )
        for related_path in (normalized_path, *normalized_related):
            self.state.connection.execute(
                "INSERT OR IGNORE INTO relation_paths(relation_id, path) VALUES (?, ?)",
                (relation_id, related_path),
            )
        self.state.connection.executemany(
            "INSERT OR IGNORE INTO relation_names("
            "relation_id, role, normalized_name) VALUES (?, ?, ?)",
            (
                (relation_id, "source", _normalized_name(projection["source"])),
                (relation_id, "target", _normalized_name(projection["target"])),
            ),
        )
        if inserted:
            self.state.relation_count += 1
        return relation_id

    def _record_relation_ownership(
        self,
        relation_id: str,
        plugin_ids: Sequence[str],
    ) -> None:
        scope = "repository" if self.state.capturing_repository_outputs else "file"
        if scope == "file" and self.state.defer_file_relation_ownership:
            return
        self.state.connection.execute(
            "INSERT OR IGNORE INTO relation_scopes(relation_id, scope) "
            "VALUES (?, ?)",
            (relation_id, scope),
        )
        self.state.connection.executemany(
            "INSERT OR IGNORE INTO relation_plugin_scopes("
            "relation_id, plugin_id, scope) VALUES (?, ?, ?)",
            ((relation_id, plugin_id, scope) for plugin_id in plugin_ids),
        )

    def flush_deferred_file_relation_ownership(self) -> None:
        """Materialize fresh-build file ownership from canonical edge tables."""

        if not self.state.defer_file_relation_ownership:
            return
        # A fresh full build contains only file-produced relations until the
        # repository-analysis capture starts. Deriving ownership once avoids two
        # indexed ownership writes for every edge while retaining deterministic
        # insertion order and the exact contributor union.
        self.state.connection.execute(
            "INSERT OR IGNORE INTO relation_scopes(relation_id, scope) "
            "SELECT relation_id, 'file' FROM relations ORDER BY relation_id"
        )
        self.state.connection.execute(
            "INSERT OR IGNORE INTO relation_plugin_scopes("
            "relation_id, plugin_id, scope) "
            "SELECT relation_id, plugin_id, 'file' FROM relation_plugins "
            "ORDER BY relation_id, plugin_id"
        )
        self.state.defer_file_relation_ownership = False
