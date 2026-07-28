"""Neutral deterministic import/reference graph composition for language plugins.

Language plugins own parsing and module resolution.  This helper owns only the
bounded snapshot and exact cross-file packet mechanics shared by those plugins.
It deliberately does not guess package-manager, classpath, or alias semantics.
"""

from __future__ import annotations

import base64
import gzip
import json
from dataclasses import dataclass, field
from typing import Callable, Mapping

from .api import (
    ArchitecturePacket,
    FileArtifact,
    GraphFact,
    PluginOutcome,
    RepositoryAnalysis,
    RepositoryAnalysisMode,
    RepositorySnapshot,
)


@dataclass(frozen=True, order=True)
class ImportBinding:
    module: str
    imported: str
    local: str
    line: int

    def __post_init__(self) -> None:
        if not self.local or self.line < 1:
            raise ValueError("import binding requires a local name and positive line")


@dataclass(frozen=True, order=True)
class ImportedCall:
    local: str
    member: str
    line: int

    def __post_init__(self) -> None:
        if not self.local or self.line < 1:
            raise ValueError("imported call requires a local name and positive line")


@dataclass(frozen=True, order=True)
class ImportFileRecord:
    path: str
    module: str
    exports: tuple[str, ...] = ()
    imports: tuple[ImportBinding, ...] = ()
    calls: tuple[ImportedCall, ...] = ()

    def __post_init__(self) -> None:
        if not self.path or not self.module:
            raise ValueError("import record requires path and module")
        if self.exports != tuple(sorted(set(self.exports))):
            raise ValueError("import record exports must be sorted and unique")
        if self.imports != tuple(sorted(set(self.imports))):
            raise ValueError("import record imports must be sorted and unique")
        if self.calls != tuple(sorted(set(self.calls))):
            raise ValueError("import record calls must be sorted and unique")


RecordParser = Callable[[FileArtifact], ImportFileRecord | None]
ModuleResolver = Callable[
    [ImportFileRecord, ImportBinding, Mapping[str, ImportFileRecord]],
    str,
]


def _record_to_mapping(record: ImportFileRecord) -> dict[str, object]:
    return {
        "path": record.path,
        "module": record.module,
        "exports": list(record.exports),
        "imports": [
            {
                "module": binding.module,
                "imported": binding.imported,
                "local": binding.local,
                "line": binding.line,
            }
            for binding in record.imports
        ],
        "calls": [
            {
                "local": call.local,
                "member": call.member,
                "line": call.line,
            }
            for call in record.calls
        ],
    }


def _record_from_mapping(value: object) -> ImportFileRecord:
    if not isinstance(value, dict):
        raise ValueError("import graph snapshot record must be an object")
    raw_imports = value.get("imports", [])
    raw_calls = value.get("calls", [])
    raw_exports = value.get("exports", [])
    if (
        not isinstance(raw_imports, list)
        or not isinstance(raw_calls, list)
        or not isinstance(raw_exports, list)
    ):
        raise ValueError("import graph snapshot arrays are invalid")
    return ImportFileRecord(
        path=str(value["path"]),
        module=str(value["module"]),
        exports=tuple(sorted(str(item) for item in raw_exports)),
        imports=tuple(sorted(
            ImportBinding(
                module=str(item["module"]),
                imported=str(item["imported"]),
                local=str(item["local"]),
                line=int(item["line"]),
            )
            for item in raw_imports
            if isinstance(item, dict)
        )),
        calls=tuple(sorted(
            ImportedCall(
                local=str(item["local"]),
                member=str(item["member"]),
                line=int(item["line"]),
            )
            for item in raw_calls
            if isinstance(item, dict)
        )),
    )


@dataclass
class ImportGraphSession:
    plugin_id: str
    revision: str
    snapshot_kind: str
    parser: RecordParser = field(compare=False, repr=False)
    resolver: ModuleResolver = field(compare=False, repr=False)
    _records: dict[str, ImportFileRecord] = field(default_factory=dict)
    _baseline_records: dict[str, ImportFileRecord] = field(default_factory=dict)
    _changed_paths: set[str] = field(default_factory=set)
    _analysis_mode: RepositoryAnalysisMode = RepositoryAnalysisMode.FULL_INDEX

    @classmethod
    def restore(
        cls,
        *,
        plugin_id: str,
        revision: str,
        snapshot_kind: str,
        parser: RecordParser,
        resolver: ModuleResolver,
        snapshots,
    ) -> "ImportGraphSession":
        snapshot = next(
            (item for item in snapshots if item.kind == snapshot_kind),
            None,
        )
        if snapshot is None:
            raise ValueError(
                f"{plugin_id} repository snapshot is missing {snapshot_kind}"
            )
        try:
            raw = gzip.decompress(
                base64.b64decode(snapshot.content.encode("ascii"))
            )
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exception:
            raise ValueError(
                f"{plugin_id} repository snapshot is invalid"
            ) from exception
        if not isinstance(payload, list):
            raise ValueError(
                f"{plugin_id} repository snapshot must be a list"
            )
        records = {
            record.path: record
            for value in payload
            for record in (_record_from_mapping(value),)
        }
        return cls(
            plugin_id=plugin_id,
            revision=revision,
            snapshot_kind=snapshot_kind,
            parser=parser,
            resolver=resolver,
            _records=dict(records),
            _baseline_records=dict(records),
        )

    def set_analysis_mode(self, mode: RepositoryAnalysisMode) -> None:
        if not isinstance(mode, RepositoryAnalysisMode):
            raise ValueError("repository analysis mode is invalid")
        self._analysis_mode = mode

    def ingest(self, artifacts: tuple[FileArtifact, ...]) -> None:
        for artifact in artifacts:
            self._changed_paths.add(artifact.path)
            self._records.pop(artifact.path, None)
            if artifact.deleted:
                continue
            record = self.parser(artifact)
            if record is not None:
                self._records[artifact.path] = record

    def _snapshot(self) -> RepositorySnapshot:
        raw = json.dumps(
            [
                _record_to_mapping(record)
                for record in sorted(self._records.values())
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return RepositorySnapshot(
            self.plugin_id,
            self.snapshot_kind,
            base64.b64encode(
                gzip.compress(raw, compresslevel=6, mtime=0)
            ).decode("ascii"),
        )

    def _packets_for(
        self,
        records: Mapping[str, ImportFileRecord],
    ) -> tuple[ArchitecturePacket, ...]:
        packets: list[ArchitecturePacket] = []
        prefix = self.plugin_id
        for source_path, record in sorted(records.items()):
            facts: set[GraphFact] = set()
            packet_paths: set[str] = {source_path}
            resolved_by_local: dict[str, tuple[ImportBinding, str]] = {}
            for binding in record.imports:
                target_path = self.resolver(record, binding, records)
                if not target_path or target_path == source_path:
                    continue
                target = records.get(target_path)
                if target is None:
                    continue
                if (
                    binding.imported not in {"", "*", "default"}
                    and binding.imported not in target.exports
                ):
                    continue
                packet_paths.add(target_path)
                resolved_by_local[binding.local] = (binding, target_path)
                facts.add(GraphFact(
                    f"{prefix}-module-resolution",
                    record.module,
                    "resolves-import",
                    target.module,
                    source_path,
                    binding.line,
                    attributes=(
                        ("imported", binding.imported),
                        ("local", binding.local),
                        ("specifier", binding.module),
                    ),
                    related_paths=(target_path,),
                ))
                facts.add(GraphFact(
                    f"{prefix}-import-binding",
                    f"{record.module}::{binding.local}",
                    "resolves-to",
                    (
                        target.module
                        if binding.imported in {"", "*"}
                        else f"{target.module}::{binding.imported}"
                    ),
                    source_path,
                    binding.line,
                    related_paths=(target_path,),
                ))

            for call in record.calls:
                resolved = resolved_by_local.get(call.local)
                if resolved is None:
                    continue
                binding, target_path = resolved
                target = records[target_path]
                target_name = (
                    call.member
                    or (
                        binding.imported
                        if binding.imported not in {"", "*", "default"}
                        else binding.local
                    )
                )
                facts.add(GraphFact(
                    f"{prefix}-call-resolution",
                    f"{record.module}::{call.local}",
                    "calls-resolved-target",
                    f"{target.module}::{target_name}",
                    source_path,
                    call.line,
                    related_paths=(target_path,),
                ))

            if facts:
                packets.append(ArchitecturePacket(
                    plugin_id=self.plugin_id,
                    kind=f"{prefix}-import-graph",
                    key=source_path,
                    paths=tuple(sorted(packet_paths)),
                    facts=tuple(sorted(facts)),
                    attributes=(("resolution", "exact-plugin-owned"),),
                ))
        return tuple(sorted(packets))

    @staticmethod
    def _relation_identity(fact: GraphFact) -> tuple[object, ...]:
        """Identify one relationship independently from source line movement."""
        return (
            fact.kind,
            fact.source,
            fact.relation,
            fact.target,
            fact.path,
            fact.attributes,
            fact.related_paths,
        )

    def _removed_relation_packets(
        self,
        current_packets: tuple[ArchitecturePacket, ...],
    ) -> tuple[ArchitecturePacket, ...]:
        """Preserve exact navigation when a PR removes a base relationship.

        Prompt assembly correctly rejects a base architecture packet touching
        a changed file as stale.  A PR-owned transition fact keeps the exact
        path to unchanged related source without claiming the removal is a
        defect.
        """
        if (
            self._analysis_mode is not RepositoryAnalysisMode.PR_OVERLAY
            or not self._baseline_records
            or not self._changed_paths
        ):
            return ()

        baseline_packets = self._packets_for(self._baseline_records)
        current_identities = {
            self._relation_identity(fact)
            for packet in current_packets
            for fact in packet.facts
        }
        removed_by_path: dict[str, set[GraphFact]] = {}
        for packet in baseline_packets:
            for fact in packet.facts:
                if fact.path not in self._changed_paths:
                    continue
                if self._relation_identity(fact) in current_identities:
                    continue
                removed_by_path.setdefault(fact.path, set()).add(GraphFact(
                    f"{self.plugin_id}-pr-removed-relation",
                    fact.source,
                    "removed-from-pr-overlay",
                    fact.target,
                    fact.path,
                    fact.line,
                    attributes=tuple(sorted((
                        ("originalKind", fact.kind),
                        ("originalRelation", fact.relation),
                        ("state", "absent-in-pr-overlay"),
                    ))),
                    related_paths=fact.related_paths,
                ))

        return tuple(sorted(
            ArchitecturePacket(
                plugin_id=self.plugin_id,
                kind=f"{self.plugin_id}-import-graph-delta",
                key=f"removed:{source_path}",
                paths=tuple(sorted({
                    source_path,
                    *(
                        related_path
                        for fact in facts
                        for related_path in fact.related_paths
                    ),
                })),
                facts=tuple(sorted(facts)),
                attributes=(
                    ("evidenceRole", "navigation"),
                    ("state", "base-to-pr-transition"),
                ),
            )
            for source_path, facts in sorted(removed_by_path.items())
            if facts
        ))

    def _packets(self) -> tuple[ArchitecturePacket, ...]:
        current = self._packets_for(self._records)
        return tuple(sorted((*current, *self._removed_relation_packets(current))))

    def finish(self, dependencies: RepositoryAnalysis):
        return PluginOutcome.handled(RepositoryAnalysis(
            packets=self._packets(),
            snapshots=(self._snapshot(),),
        ))
