from __future__ import annotations

import base64
import gzip
import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Mapping

from codecrow_plugins import (
    ArchitecturePacket,
    CandidateClaim,
    FileArtifact,
    GraphFact,
    PluginDescriptor,
    PluginOutcome,
    RepositoryAnalysis,
    RepositorySnapshot,
    ReviewContribution,
    ValidationDecision,
    ValidationResult,
)


_SNAPSHOT_KIND = "data-contract-reference-graph"
_MAX_CANDIDATES_PER_FILE = 256
_IDENTIFIER = r"[A-Za-z_$][A-Za-z0-9_$-]{2,127}"
_QUOTED_IDENTIFIER = re.compile(rf"(?P<quote>['\"])(?P<name>{_IDENTIFIER})(?P=quote)")
_MEMBER_IDENTIFIER = re.compile(rf"\.(?P<name>{_IDENTIFIER})\b")
_FIELD_DECLARATION = re.compile(rf"^\s*(?P<name>{_IDENTIFIER})\??\s*:")
_PROTO_FIELD = re.compile(
    rf"^\s*(?:(?:optional|required|repeated)\s+)?"
    rf"[A-Za-z_][A-Za-z0-9_.$<>,]*\s+(?P<name>{_IDENTIFIER})\s*=\s*\d+"
)
_CONTRACT_ROOTS = {"contract", "contracts", "schema", "schemas"}
_CONTRACT_SUFFIXES = (
    ".graphql",
    ".graphqls",
    ".proto",
    ".schema.json",
)
_RELATION_KINDS = {
    "data-contract-reference",
    "data-contract-pr-removed-reference",
}


@dataclass(frozen=True, order=True)
class FieldOccurrence:
    name: str
    line: int


@dataclass(frozen=True, order=True)
class ContractFileRecord:
    path: str
    is_contract: bool
    declarations: tuple[FieldOccurrence, ...] = ()
    references: tuple[FieldOccurrence, ...] = ()


def _is_contract_path(path: str) -> bool:
    pure = PurePosixPath(path)
    lowered = path.casefold()
    return (
        bool(pure.parts and pure.parts[0].casefold() in _CONTRACT_ROOTS)
        or lowered.endswith(_CONTRACT_SUFFIXES)
    )


def _line_for(content: str, token: str) -> int:
    offset = content.find(token)
    return content.count("\n", 0, max(offset, 0)) + 1


def _json_contract_fields(content: str) -> set[FieldOccurrence]:
    try:
        root = json.loads(content)
    except (TypeError, ValueError):
        return set()
    names: set[str] = set()

    def visit(value) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                names.update(
                    name for name in properties
                    if isinstance(name, str) and re.fullmatch(_IDENTIFIER, name)
                )
            required = value.get("required")
            if isinstance(required, list):
                names.update(
                    name for name in required
                    if isinstance(name, str) and re.fullmatch(_IDENTIFIER, name)
                )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(root)
    return {
        FieldOccurrence(name, _line_for(content, f'"{name}"'))
        for name in names
    }


def _line_occurrences(content: str) -> set[FieldOccurrence]:
    values: set[FieldOccurrence] = set()
    for line_number, line in enumerate(content.splitlines(), start=1):
        for pattern in (
            _QUOTED_IDENTIFIER,
            _MEMBER_IDENTIFIER,
            _FIELD_DECLARATION,
            _PROTO_FIELD,
        ):
            for match in pattern.finditer(line):
                values.add(FieldOccurrence(match.group("name"), line_number))
    return values


def _record(artifact: FileArtifact) -> ContractFileRecord | None:
    if artifact.deleted:
        return None
    contract = _is_contract_path(artifact.path)
    occurrences = _line_occurrences(artifact.content)
    declarations: set[FieldOccurrence] = set()
    if contract:
        declarations.update(
            occurrence
            for occurrence in occurrences
            if any(
                pattern.match(
                    artifact.content.splitlines()[occurrence.line - 1]
                )
                for pattern in (_FIELD_DECLARATION, _PROTO_FIELD)
            )
        )
        if artifact.path.casefold().endswith(".json"):
            declarations.update(_json_contract_fields(artifact.content))
    references = set() if contract else occurrences
    return ContractFileRecord(
        path=artifact.path,
        is_contract=contract,
        declarations=tuple(sorted(declarations)[:_MAX_CANDIDATES_PER_FILE]),
        references=tuple(sorted(references)[:_MAX_CANDIDATES_PER_FILE]),
    )


def _record_mapping(record: ContractFileRecord) -> dict[str, object]:
    return {
        "path": record.path,
        "isContract": record.is_contract,
        "declarations": [
            {"name": item.name, "line": item.line}
            for item in record.declarations
        ],
        "references": [
            {"name": item.name, "line": item.line}
            for item in record.references
        ],
    }


def _record_from_mapping(value: object) -> ContractFileRecord:
    if not isinstance(value, dict):
        raise ValueError("data-contract snapshot record must be an object")

    def occurrences(field_name: str) -> tuple[FieldOccurrence, ...]:
        raw = value.get(field_name, [])
        if not isinstance(raw, list):
            raise ValueError(
                f"data-contract snapshot {field_name} must be a list"
            )
        return tuple(sorted(
            FieldOccurrence(str(item["name"]), int(item["line"]))
            for item in raw
            if isinstance(item, dict)
        ))

    return ContractFileRecord(
        path=str(value["path"]),
        is_contract=bool(value["isContract"]),
        declarations=occurrences("declarations"),
        references=occurrences("references"),
    )


@dataclass
class ContractGraphSession:
    plugin_id: str
    revision: str
    records: dict[str, ContractFileRecord] = field(default_factory=dict)
    baseline_records: dict[str, ContractFileRecord] = field(default_factory=dict)
    changed_paths: set[str] = field(default_factory=set)

    @classmethod
    def restore(
        cls,
        *,
        plugin_id: str,
        revision: str,
        snapshots,
    ) -> "ContractGraphSession":
        snapshot = next(
            (item for item in snapshots if item.kind == _SNAPSHOT_KIND),
            None,
        )
        if snapshot is None:
            raise ValueError("data-contract repository snapshot is missing")
        try:
            raw = gzip.decompress(
                base64.b64decode(snapshot.content.encode("ascii"))
            )
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exception:
            raise ValueError(
                "data-contract repository snapshot is invalid"
            ) from exception
        if not isinstance(payload, list):
            raise ValueError("data-contract snapshot must be a list")
        records = {
            record.path: record
            for item in payload
            for record in (_record_from_mapping(item),)
        }
        return cls(
            plugin_id=plugin_id,
            revision=revision,
            records=dict(records),
            baseline_records=dict(records),
        )

    def ingest(self, artifacts: tuple[FileArtifact, ...]) -> None:
        for artifact in artifacts:
            self.changed_paths.add(artifact.path)
            self.records.pop(artifact.path, None)
            record = _record(artifact)
            if record is not None:
                self.records[artifact.path] = record

    @staticmethod
    def _declarations(
        records: Mapping[str, ContractFileRecord],
    ) -> dict[str, tuple[tuple[str, int], ...]]:
        result: dict[str, set[tuple[str, int]]] = {}
        for record in records.values():
            for occurrence in record.declarations:
                result.setdefault(occurrence.name, set()).add(
                    (record.path, occurrence.line)
                )
        return {
            name: tuple(sorted(values))
            for name, values in sorted(result.items())
        }

    @staticmethod
    def _reference_paths(
        records: Mapping[str, ContractFileRecord],
    ) -> dict[str, tuple[str, ...]]:
        result: dict[str, set[str]] = {}
        for record in records.values():
            for occurrence in record.references:
                result.setdefault(occurrence.name, set()).add(record.path)
        return {
            name: tuple(sorted(paths))
            for name, paths in sorted(result.items())
        }

    def _packets_for(
        self,
        records: Mapping[str, ContractFileRecord],
    ) -> tuple[ArchitecturePacket, ...]:
        declarations = self._declarations(records)
        packets: list[ArchitecturePacket] = []
        for record in sorted(records.values()):
            if record.is_contract:
                continue
            facts: set[GraphFact] = set()
            paths = {record.path}
            for occurrence in record.references:
                for contract_path, _ in declarations.get(
                    occurrence.name,
                    (),
                ):
                    paths.add(contract_path)
                    facts.add(GraphFact(
                        "data-contract-reference",
                        record.path,
                        "references-declared-field",
                        f"{contract_path}::{occurrence.name}",
                        record.path,
                        occurrence.line,
                        attributes=(("field", occurrence.name),),
                        related_paths=(contract_path,),
                    ))
            if facts:
                packets.append(ArchitecturePacket(
                    plugin_id=self.plugin_id,
                    kind="data-contract-reference-graph",
                    key=record.path,
                    paths=tuple(sorted(paths)),
                    facts=tuple(sorted(facts)),
                    attributes=(("resolution", "exact-contract-field"),),
                ))
        return tuple(sorted(packets))

    @staticmethod
    def _fact_identity(fact: GraphFact) -> tuple[object, ...]:
        return (
            fact.kind,
            fact.source,
            fact.relation,
            fact.target,
            fact.path,
            fact.attributes,
            fact.related_paths,
        )

    def _removed_packets(
        self,
        current: tuple[ArchitecturePacket, ...],
    ) -> tuple[ArchitecturePacket, ...]:
        if not self.baseline_records or not self.changed_paths:
            return ()
        baseline = self._packets_for(self.baseline_records)
        current_identities = {
            self._fact_identity(fact)
            for packet in current
            for fact in packet.facts
        }
        current_references = self._reference_paths(self.records)
        removed: dict[str, set[GraphFact]] = {}
        for packet in baseline:
            for fact in packet.facts:
                if not self.changed_paths.intersection(
                    {fact.path, *fact.related_paths}
                ):
                    continue
                if self._fact_identity(fact) in current_identities:
                    continue
                field_name = dict(fact.attributes)["field"]
                related_paths = tuple(sorted({
                    *fact.related_paths,
                    *(
                        path
                        for path in current_references.get(field_name, ())
                        if path != fact.path
                    ),
                }))
                removed.setdefault(fact.path, set()).add(GraphFact(
                    "data-contract-pr-removed-reference",
                    fact.source,
                    "removed-declared-field-reference",
                    fact.target,
                    fact.path,
                    fact.line,
                    attributes=(
                        ("field", field_name),
                        ("originalKind", fact.kind),
                        ("state", "absent-in-pr-overlay"),
                    ),
                    related_paths=related_paths,
                ))
        return tuple(sorted(
            ArchitecturePacket(
                plugin_id=self.plugin_id,
                kind="data-contract-reference-delta",
                key=f"removed:{path}",
                paths=tuple(sorted({
                    path,
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
            for path, facts in sorted(removed.items())
            if facts
        ))

    def _snapshot(self) -> RepositorySnapshot:
        raw = json.dumps(
            [
                _record_mapping(record)
                for record in sorted(self.records.values())
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return RepositorySnapshot(
            self.plugin_id,
            _SNAPSHOT_KIND,
            base64.b64encode(
                gzip.compress(raw, compresslevel=6, mtime=0)
            ).decode("ascii"),
        )

    def finish(self, dependencies: RepositoryAnalysis):
        current = self._packets_for(self.records)
        return PluginOutcome.handled(RepositoryAnalysis(
            packets=tuple(sorted((*current, *self._removed_packets(current)))),
            snapshots=(self._snapshot(),),
        ))


@dataclass(frozen=True)
class DataContractsPlugin:
    descriptor: PluginDescriptor

    def start_repository_analysis(self, revision: str):
        return PluginOutcome.handled(ContractGraphSession(
            plugin_id=self.descriptor.id,
            revision=revision,
        ))

    def restore_repository_analysis(self, revision: str, snapshots):
        return PluginOutcome.handled(ContractGraphSession.restore(
            plugin_id=self.descriptor.id,
            revision=revision,
            snapshots=snapshots,
        ))

    def review(self, paths: tuple[str, ...]):
        if not paths:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(ReviewContribution(rules=(
            "A data-contract-pr-removed-reference is base-to-PR navigation evidence only; require changed-hunk proof of harm.",
            "Data-contract facts connect declared fields to cross-language references; only current source, tests, or an exact diagnostic can prove incompatibility.",
        )))

    def validate(self, claim: CandidateClaim):
        if claim.claim_kind not in _RELATION_KINDS:
            return PluginOutcome.abstained()
        matching = tuple(
            fact
            for fact in claim.evidence
            if fact.kind == claim.claim_kind
            and claim.path in {fact.path, *fact.related_paths}
        )
        if not matching:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "data-contract-relation-evidence-unavailable",
                "The candidate has no matching exact data-contract relationship evidence.",
            ))
        return PluginOutcome.handled(ValidationResult(
            ValidationDecision.INSUFFICIENT_EVIDENCE,
            "data-contract-relationship-is-navigation-only",
            "The exact data-contract relationship proves topology or change only, not a semantic incompatibility.",
        ))


def create_plugin(descriptor: PluginDescriptor) -> DataContractsPlugin:
    return DataContractsPlugin(descriptor)
