from __future__ import annotations

import base64
import gzip
import json
import posixpath
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
from codecrow_plugins.graphql import (
    parse_operations,
    parse_schema,
    parse_schema_root_types,
)


_SNAPSHOT_KIND = "data-contract-reference-graph"
_MAX_CANDIDATES_PER_FILE = 2048
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
_JSON_REF = re.compile(
    r'(?<!\\)"\$ref"\s*:\s*(?P<value>"(?:\\.|[^"\\])*")',
)
_EMBEDDED_GRAPHQL_SIGNAL = re.compile(
    r"(?:\"\"\"|[\"'`])\s*"
    r"(?:\{|query\b|mutation\b|subscription\b)"
    r"|<script\b[^>]*\btype\s*=\s*['\"]application/(?:graphql|gql)['\"]",
    re.IGNORECASE,
)


@dataclass(frozen=True, order=True)
class FieldOccurrence:
    name: str
    line: int
    owner: str = ""
    target_type: str = ""
    contract_kind: str = "graphql"


@dataclass(frozen=True, order=True)
class ReferenceOccurrence:
    contract_kind: str
    root: str
    segments: tuple[str, ...]
    line: int


@dataclass(frozen=True, order=True)
class ContractFileRecord:
    path: str
    is_contract: bool
    root_types: tuple[tuple[str, str], ...] = ()
    declarations: tuple[FieldOccurrence, ...] = ()
    references: tuple[ReferenceOccurrence, ...] = ()


def _is_contract_path(path: str) -> bool:
    pure = PurePosixPath(path)
    lowered = path.casefold()
    return (
        bool(pure.parts and pure.parts[0].casefold() in _CONTRACT_ROOTS)
        or lowered.endswith(_CONTRACT_SUFFIXES)
    )


def _graphql_declarations(content: str) -> tuple[FieldOccurrence, ...]:
    return tuple(sorted(
        FieldOccurrence(
            name=field.name,
            line=field.line,
            owner=field.owner,
            target_type=field.target_type,
        )
        for definition in parse_schema(content)
        for field in definition.fields
    )[:_MAX_CANDIDATES_PER_FILE])


def _graphql_references(
    content: str,
    *,
    embedded_only: bool,
) -> tuple[ReferenceOccurrence, ...]:
    return tuple(
        ReferenceOccurrence("graphql", item.root, item.segments, item.line)
        for item in parse_operations(
            content,
            embedded_only=embedded_only,
        )[:_MAX_CANDIDATES_PER_FILE]
    )


def _json_references(content: str) -> tuple[ReferenceOccurrence, ...]:
    try:
        root = json.loads(content)
    except (TypeError, ValueError):
        return ()
    if not isinstance(root, (dict, list)):
        return ()
    occurrences: set[ReferenceOccurrence] = set()
    for match in _JSON_REF.finditer(content):
        try:
            reference = json.loads(match.group("value"))
        except ValueError:
            continue
        if not isinstance(reference, str) or not reference.strip():
            continue
        occurrences.add(ReferenceOccurrence(
            "json-ref",
            "",
            (reference.strip(),),
            content.count("\n", 0, match.start("value")) + 1,
        ))
    return tuple(sorted(
        occurrences,
    ))


def _record(artifact: FileArtifact) -> ContractFileRecord | None:
    if artifact.deleted:
        return None
    contract = _is_contract_path(artifact.path)
    lowered = artifact.path.casefold()
    declarations = (
        _graphql_declarations(artifact.content)
        if lowered.endswith((".graphqls", ".graphql")) and contract
        else ()
    )
    graphql_source = lowered.endswith((".graphql", ".graphqls"))
    references: tuple[ReferenceOccurrence, ...] = ()
    if lowered.endswith(".graphql"):
        references = _graphql_references(
            artifact.content,
            embedded_only=False,
        )
    elif (
        not graphql_source
        and _EMBEDDED_GRAPHQL_SIGNAL.search(artifact.content) is not None
    ):
        references = _graphql_references(
            artifact.content,
            embedded_only=True,
        )
    if lowered.endswith(".json"):
        references = tuple(sorted({*references, *_json_references(artifact.content)}))
    if not contract and not references:
        return None
    return ContractFileRecord(
        path=artifact.path,
        is_contract=contract,
        root_types=(
            parse_schema_root_types(artifact.content)
            if lowered.endswith((".graphqls", ".graphql")) and contract
            else ()
        ),
        declarations=tuple(declarations),
        references=tuple(references),
    )


def _record_mapping(record: ContractFileRecord) -> dict[str, object]:
    return {
        "path": record.path,
        "isContract": record.is_contract,
        "rootTypes": dict(record.root_types),
        "declarations": [
            {
                "name": item.name,
                "line": item.line,
                "owner": item.owner,
                "targetType": item.target_type,
                "contractKind": item.contract_kind,
            }
            for item in record.declarations
        ],
        "references": [
            {
                "contractKind": item.contract_kind,
                "root": item.root,
                "segments": list(item.segments),
                "line": item.line,
            }
            for item in record.references
        ],
    }


def _record_from_mapping(value: object) -> ContractFileRecord:
    if not isinstance(value, dict):
        raise ValueError("data-contract snapshot record must be an object")

    def declarations() -> tuple[FieldOccurrence, ...]:
        raw = value.get("declarations", [])
        if not isinstance(raw, list):
            raise ValueError("data-contract snapshot declarations must be a list")
        return tuple(sorted(
            FieldOccurrence(
                str(item["name"]),
                int(item["line"]),
                str(item.get("owner", "")),
                str(item.get("targetType", "")),
                str(item.get(
                    "contractKind",
                    "graphql" if "owner" in item else "legacy-name",
                )),
            )
            for item in raw
            if isinstance(item, dict)
        ))

    def references() -> tuple[ReferenceOccurrence, ...]:
        raw = value.get("references", [])
        if not isinstance(raw, list):
            raise ValueError("data-contract snapshot references must be a list")
        return tuple(sorted(
            ReferenceOccurrence(
                str(item.get(
                    "contractKind",
                    "legacy-name" if "name" in item else "",
                )),
                str(item.get("root", "")),
                (
                    (str(item["name"]),)
                    if "name" in item and "segments" not in item
                    else tuple(
                        str(segment) for segment in item.get("segments", [])
                    )
                ),
                int(item["line"]),
            )
            for item in raw
            if isinstance(item, dict)
        ))

    raw_root_types = value.get("rootTypes", {})
    if not isinstance(raw_root_types, dict):
        raise ValueError("data-contract snapshot rootTypes must be an object")
    return ContractFileRecord(
        path=str(value["path"]),
        is_contract=bool(value["isContract"]),
        root_types=tuple(sorted(
            (str(operation), str(type_name))
            for operation, type_name in raw_root_types.items()
        )),
        declarations=declarations(),
        references=references(),
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
    ) -> dict[tuple[str, str], tuple[tuple[str, int, str], ...]]:
        result: dict[tuple[str, str], set[tuple[str, int, str]]] = {}
        for record in records.values():
            for occurrence in record.declarations:
                if occurrence.contract_kind != "graphql" or not occurrence.owner:
                    continue
                result.setdefault((occurrence.owner, occurrence.name), set()).add(
                    (record.path, occurrence.line, occurrence.target_type)
                )
        return {
            name: tuple(sorted(values))
            for name, values in sorted(result.items())
        }

    @staticmethod
    def _legacy_declarations(
        records: Mapping[str, ContractFileRecord],
    ) -> dict[str, tuple[tuple[str, int], ...]]:
        result: dict[str, set[tuple[str, int]]] = {}
        for record in records.values():
            if not record.is_contract:
                continue
            for occurrence in record.declarations:
                result.setdefault(occurrence.name, set()).add(
                    (record.path, occurrence.line)
                )
        return {
            name: tuple(sorted(values))
            for name, values in sorted(result.items())
        }

    @staticmethod
    def _graphql_roots(
        records: Mapping[str, ContractFileRecord],
    ) -> dict[str, str]:
        declared: dict[str, set[str]] = {}
        for record in records.values():
            for operation, type_name in record.root_types:
                declared.setdefault(operation, set()).add(type_name)
        defaults = {
            "query": "Query",
            "mutation": "Mutation",
            "subscription": "Subscription",
        }
        return {
            defaults[operation]: next(iter(type_names))
            for operation, type_names in declared.items()
            if operation in defaults and len(type_names) == 1
        }

    @staticmethod
    def _json_reference_target(source_path: str, reference: str) -> tuple[str, str]:
        file_part, separator, fragment = reference.partition("#")
        if not file_part:
            target_path = source_path
        else:
            target_path = posixpath.normpath(
                posixpath.join(posixpath.dirname(source_path), file_part)
            )
        if target_path.startswith("../") or target_path.startswith("/"):
            return "", ""
        return target_path, ("#" + fragment if separator else "")

    def _packets_for(
        self,
        records: Mapping[str, ContractFileRecord],
    ) -> tuple[ArchitecturePacket, ...]:
        declarations = self._declarations(records)
        legacy_declarations = self._legacy_declarations(records)
        graphql_roots = self._graphql_roots(records)
        contract_paths = {
            record.path for record in records.values() if record.is_contract
        }
        packets: list[ArchitecturePacket] = []
        for record in sorted(records.values()):
            if record.is_contract and not record.references:
                continue
            facts: set[GraphFact] = set()
            paths = {record.path}
            for occurrence in record.references:
                if occurrence.contract_kind == "graphql":
                    operation_root = graphql_roots.get(
                        occurrence.root,
                        occurrence.root,
                    )
                    owner = operation_root
                    resolved = None
                    for field in occurrence.segments:
                        candidates = declarations.get((owner, field), ())
                        if len(candidates) != 1:
                            resolved = None
                            break
                        contract_path, _, target_type = candidates[0]
                        resolved = (contract_path, owner, field, target_type)
                        owner = target_type
                    if resolved is None:
                        continue
                    contract_path, field_owner, field, target_type = resolved
                    paths.add(contract_path)
                    facts.add(GraphFact(
                        "data-contract-reference",
                        f"{record.path}::{operation_root}"
                        f".{'.'.join(occurrence.segments)}",
                        "selects-graphql-field",
                        f"{contract_path}::{field_owner}.{field}",
                        record.path,
                        occurrence.line,
                        attributes=(
                            ("contractKind", "graphql"),
                            ("field", field),
                            ("ownerType", field_owner),
                            ("targetType", target_type),
                        ),
                        related_paths=(contract_path,),
                    ))
                elif (
                    occurrence.contract_kind == "legacy-name"
                    and occurrence.segments
                ):
                    field = occurrence.segments[0]
                    for contract_path, _ in legacy_declarations.get(field, ()):
                        paths.add(contract_path)
                        facts.add(GraphFact(
                            "data-contract-reference",
                            record.path,
                            "references-declared-field",
                            f"{contract_path}::{field}",
                            record.path,
                            occurrence.line,
                            attributes=(
                                ("contractKind", "legacy-name"),
                                ("field", field),
                            ),
                            related_paths=(contract_path,),
                        ))
                elif occurrence.contract_kind == "json-ref":
                    target_path, fragment = self._json_reference_target(
                        record.path, occurrence.segments[0]
                    )
                    if target_path not in contract_paths:
                        continue
                    paths.add(target_path)
                    facts.add(GraphFact(
                        "data-contract-reference",
                        record.path,
                        "references-json-schema-target",
                        f"{target_path}{fragment}",
                        record.path,
                        occurrence.line,
                        attributes=(
                            ("contractKind", "json-schema"),
                            ("reference", occurrence.segments[0]),
                        ),
                        related_paths=(target_path,),
                    ))
            if facts:
                packets.append(ArchitecturePacket(
                    plugin_id=self.plugin_id,
                    kind="data-contract-reference-graph",
                    key=record.path,
                    paths=tuple(sorted(paths)),
                    facts=tuple(sorted(facts)),
                    attributes=(("resolution", "typed-structural-contract"),),
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
        removed: dict[str, set[GraphFact]] = {}
        for packet in baseline:
            for fact in packet.facts:
                if not self.changed_paths.intersection(
                    {fact.path, *fact.related_paths}
                ):
                    continue
                if self._fact_identity(fact) in current_identities:
                    continue
                attributes = dict(fact.attributes)
                field_name = attributes.get("field", attributes.get("reference", ""))
                related_paths = fact.related_paths
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
            "Data-contract facts require typed GraphQL traversal or an explicit schema reference; only current source, tests, or an exact diagnostic can prove incompatibility.",
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
