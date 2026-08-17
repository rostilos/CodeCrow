from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Generic, Mapping, Protocol, TypeVar


_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9-]*$")
_EXTENSION = re.compile(r"^\.[a-z0-9][a-z0-9.+_-]*$")
_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_LANGUAGE_ID = re.compile(r"^[a-z][a-z0-9_-]*$")
_PYTHON_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
class PluginKind(str, Enum):
    LANGUAGE = "language"
    FRAMEWORK = "framework"
    DOMAIN = "domain"


class Capability(str, Enum):
    CALIBRATION = "calibration"
    CANDIDATE_RECIPE = "candidate-recipe"
    CONTEXT = "context"
    FILE_POLICY = "file-policy"
    GRAPH = "graph"
    INDEX = "index"
    PLANNING = "planning"
    PROMPT = "prompt"
    SYNTAX = "syntax"
    VALIDATION = "validation"


class OutcomeStatus(str, Enum):
    HANDLED = "handled"
    ABSTAINED = "abstained"
    FAILED = "failed"


class ValidationDecision(str, Enum):
    PASS = "pass"
    REJECT = "reject"
    INSUFFICIENT_EVIDENCE = "insufficient-evidence"


class FileDisposition(str, Enum):
    FULL = "full"
    ARCHITECTURE_ONLY = "architecture-only"
    GENERATED = "generated"
    EXCLUDED = "excluded"


class RepositoryAnalysisMode(str, Enum):
    """Describe how repository state will be consumed by the host."""

    FULL_INDEX = "full-index"
    PERSISTENT_INCREMENTAL = "persistent-incremental"
    PR_OVERLAY = "pr-overlay"


def _non_blank(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")
    return value


def _plugin_id(value: str, field_name: str = "plugin id") -> str:
    if not isinstance(value, str) or not _PLUGIN_ID.fullmatch(value):
        raise ValueError(f"{field_name} must match {_PLUGIN_ID.pattern}")
    return value


def _sorted_unique(values: tuple, field_name: str) -> tuple:
    if any(value is None for value in values):
        raise ValueError(f"{field_name} cannot contain null values")
    if tuple(sorted(values)) != values or len(set(values)) != len(values):
        raise ValueError(f"{field_name} must be unique and sorted")
    return values


def _unique(values: tuple, field_name: str) -> tuple:
    if any(value is None for value in values):
        raise ValueError(f"{field_name} cannot contain null values")
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must be unique")
    return values


def normalize_path(value: str) -> str:
    path = _non_blank(value, "path").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if path.startswith("/") or path.endswith("/") or "//" in path:
        raise ValueError("path must be a normalized repository-relative path")
    segments = path.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("path contains an invalid segment")
    return path


@dataclass(frozen=True, order=True)
class ContentMarker:
    path: str
    contains: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", normalize_path(self.path))
        _non_blank(self.contains, "content marker")


@dataclass(frozen=True, order=True)
class ContentPatternMarker:
    path_pattern: str
    contains: str

    def __post_init__(self) -> None:
        normalized = _path_pattern(self.path_pattern)
        if normalized != self.path_pattern:
            raise ValueError("content marker path pattern must already be normalized")
        _non_blank(self.contains, "content pattern marker")


def _path_pattern(value: str) -> str:
    pattern = _non_blank(value, "path pattern").replace("\\", "/")
    if pattern.startswith("/") or "//" in pattern:
        raise ValueError("path pattern must be repository-relative")
    if any(segment in {".", ".."} for segment in pattern.split("/")):
        raise ValueError("path pattern contains an invalid segment")
    return pattern


@dataclass(frozen=True, order=True)
class DetectionAlternative:
    files_all: tuple[str, ...] = ()
    files_any: tuple[str, ...] = ()
    path_patterns_all: tuple[str, ...] = ()
    path_patterns_any: tuple[str, ...] = ()
    content_markers: tuple[ContentMarker, ...] = ()
    content_pattern_markers: tuple[ContentPatternMarker, ...] = ()

    def __post_init__(self) -> None:
        normalized_all = tuple(normalize_path(path) for path in self.files_all)
        normalized_any = tuple(normalize_path(path) for path in self.files_any)
        if normalized_all != self.files_all or normalized_any != self.files_any:
            raise ValueError("detection paths must already be normalized")
        _sorted_unique(self.files_all, "filesAll")
        _sorted_unique(self.files_any, "filesAny")
        normalized_patterns_all = tuple(_path_pattern(pattern) for pattern in self.path_patterns_all)
        normalized_patterns_any = tuple(_path_pattern(pattern) for pattern in self.path_patterns_any)
        if normalized_patterns_all != self.path_patterns_all or normalized_patterns_any != self.path_patterns_any:
            raise ValueError("detection path patterns must already be normalized")
        _sorted_unique(self.path_patterns_all, "pathPatternsAll")
        _sorted_unique(self.path_patterns_any, "pathPatternsAny")
        _sorted_unique(self.content_markers, "contentMarkers")
        _sorted_unique(self.content_pattern_markers, "contentPatternMarkers")
        if not (
            self.files_all
            or self.files_any
            or self.path_patterns_all
            or self.path_patterns_any
            or self.content_markers
            or self.content_pattern_markers
        ):
            raise ValueError("detection alternative must contain at least one condition")


@dataclass(frozen=True)
class DetectionRules:
    extensions: tuple[str, ...] = ()
    files_all: tuple[str, ...] = ()
    files_any: tuple[str, ...] = ()
    content_markers: tuple[ContentMarker, ...] = ()
    alternatives: tuple[DetectionAlternative, ...] = ()

    def __post_init__(self) -> None:
        _sorted_unique(self.extensions, "detection extensions")
        if any(not _EXTENSION.fullmatch(value) for value in self.extensions):
            raise ValueError("detection extensions must be normalized lowercase extensions")
        normalized_all = tuple(normalize_path(path) for path in self.files_all)
        normalized_any = tuple(normalize_path(path) for path in self.files_any)
        if normalized_all != self.files_all or normalized_any != self.files_any:
            raise ValueError("detection paths must already be normalized")
        _sorted_unique(self.files_all, "filesAll")
        _sorted_unique(self.files_any, "filesAny")
        _sorted_unique(self.content_markers, "contentMarkers")
        _sorted_unique(self.alternatives, "detection alternatives")


@dataclass(frozen=True)
class PluginDescriptor:
    id: str
    kind: PluginKind
    requires: tuple[str, ...]
    capabilities: tuple[Capability, ...]
    detection: DetectionRules = field(default_factory=DetectionRules)
    entrypoints: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _plugin_id(self.id)
        if not isinstance(self.kind, PluginKind):
            raise ValueError("kind must be a PluginKind")
        for requirement in self.requires:
            _plugin_id(requirement, "required plugin id")
        _sorted_unique(self.requires, "requires")
        if self.id in self.requires:
            raise ValueError("a plugin cannot require itself")
        if any(not isinstance(value, Capability) for value in self.capabilities):
            raise ValueError("capabilities must contain Capability values")
        _sorted_unique(tuple(value.value for value in self.capabilities), "capabilities")
        normalized_entrypoints: dict[str, str] = {}
        for runtime, entrypoint in sorted(dict(self.entrypoints).items()):
            if runtime not in {"java", "python"}:
                raise ValueError(f"unsupported runtime entrypoint: {runtime}")
            normalized_entrypoints[runtime] = _non_blank(entrypoint, "entrypoint")
        object.__setattr__(self, "entrypoints", MappingProxyType(normalized_entrypoints))


class CodeCrowPlugin(Protocol):
    @property
    def descriptor(self) -> PluginDescriptor: ...


@dataclass(frozen=True)
class PluginDiagnostic:
    code: str
    message: str
    plugin_id: str | None = None
    path: str | None = None
    recoverable: bool = False

    def __post_init__(self) -> None:
        _non_blank(self.code, "diagnostic code")
        _non_blank(self.message, "diagnostic message")
        if self.plugin_id is not None:
            _plugin_id(self.plugin_id)
        if self.path is not None:
            normalized = normalize_path(self.path)
            if normalized != self.path:
                raise ValueError("diagnostic path must already be normalized")


T = TypeVar("T")


@dataclass(frozen=True)
class PluginOutcome(Generic[T]):
    status: OutcomeStatus
    value: T | None = None
    diagnostic: PluginDiagnostic | None = None

    def __post_init__(self) -> None:
        if self.status is OutcomeStatus.HANDLED:
            if self.value is None or self.diagnostic is not None:
                raise ValueError("handled outcome requires a value and no diagnostic")
        elif self.status is OutcomeStatus.ABSTAINED:
            if self.value is not None or self.diagnostic is not None:
                raise ValueError("abstained outcome carries no value or diagnostic")
        elif self.status is OutcomeStatus.FAILED:
            if self.value is not None or self.diagnostic is None:
                raise ValueError("failed outcome requires a diagnostic and no value")
        else:
            raise ValueError("unknown outcome status")

    @classmethod
    def handled(cls, value: T) -> "PluginOutcome[T]":
        return cls(status=OutcomeStatus.HANDLED, value=value)

    @classmethod
    def abstained(cls) -> "PluginOutcome[T]":
        return cls(status=OutcomeStatus.ABSTAINED)

    @classmethod
    def failed(cls, diagnostic: PluginDiagnostic) -> "PluginOutcome[T]":
        return cls(status=OutcomeStatus.FAILED, diagnostic=diagnostic)


@dataclass(frozen=True)
class RepositoryFacts:
    revision: str
    paths: tuple[str, ...]
    marker_contents: Mapping[str, str] = field(default_factory=dict)
    project_type: str | None = None
    source_root: str | None = None

    def __post_init__(self) -> None:
        _non_blank(self.revision, "revision")
        normalized_paths = tuple(normalize_path(path) for path in self.paths)
        if normalized_paths != self.paths:
            raise ValueError("repository paths must already be normalized")
        _sorted_unique(self.paths, "repository paths")
        normalized_markers: dict[str, str] = {}
        for path, content in sorted(dict(self.marker_contents).items()):
            normalized = normalize_path(path)
            if normalized != path:
                raise ValueError("marker paths must already be normalized")
            if not isinstance(content, str):
                raise ValueError("marker content must be text")
            normalized_markers[path] = content
        object.__setattr__(self, "marker_contents", MappingProxyType(normalized_markers))
        project_type = (
            self.project_type.strip().casefold()
            if isinstance(self.project_type, str) and self.project_type.strip()
            else None
        )
        if project_type == "auto":
            project_type = None
        if project_type is not None:
            _plugin_id(project_type)
        source_root = (
            normalize_path(self.source_root.strip().replace("\\", "/"))
            if isinstance(self.source_root, str)
            and self.source_root.strip() not in {"", "."}
            else None
        )
        object.__setattr__(self, "project_type", project_type)
        object.__setattr__(self, "source_root", source_root)


@dataclass(frozen=True)
class ProjectCapabilities:
    repository_plugins: tuple[str, ...]
    file_plugins: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    detection_evidence: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    unavailable_capabilities: tuple[str, ...] = ()
    fingerprint: str = ""
    descriptor_fingerprint: str = "sha256:" + "0" * 64

    def __post_init__(self) -> None:
        for plugin_id in self.repository_plugins:
            _plugin_id(plugin_id)
        _unique(self.repository_plugins, "repository plugins")
        normalized_files: dict[str, tuple[str, ...]] = {}
        for path, plugin_ids in sorted(dict(self.file_plugins).items()):
            normalized = normalize_path(path)
            if normalized != path:
                raise ValueError("file plugin paths must already be normalized")
            for plugin_id in plugin_ids:
                _plugin_id(plugin_id)
            _unique(plugin_ids, f"file plugins for {path}")
            normalized_files[path] = plugin_ids
        normalized_evidence: dict[str, tuple[str, ...]] = {}
        for plugin_id, evidence in sorted(dict(self.detection_evidence).items()):
            _plugin_id(plugin_id)
            _sorted_unique(evidence, f"detection evidence for {plugin_id}")
            normalized_evidence[plugin_id] = evidence
        _sorted_unique(self.unavailable_capabilities, "unavailable capabilities")
        if not isinstance(self.fingerprint, str) or not _FINGERPRINT.fullmatch(self.fingerprint):
            raise ValueError("fingerprint must be a lowercase SHA-256 content digest")
        if (
            not isinstance(self.descriptor_fingerprint, str)
            or not _FINGERPRINT.fullmatch(self.descriptor_fingerprint)
        ):
            raise ValueError(
                "descriptor fingerprint must be a lowercase SHA-256 content digest"
            )
        object.__setattr__(self, "file_plugins", MappingProxyType(normalized_files))
        object.__setattr__(self, "detection_evidence", MappingProxyType(normalized_evidence))


@dataclass(frozen=True)
class FileArtifact:
    path: str
    content: str
    deleted: bool = False

    def __post_init__(self) -> None:
        normalized = normalize_path(self.path)
        if normalized != self.path:
            raise ValueError("artifact path must already be normalized")
        if not isinstance(self.content, str):
            raise ValueError("artifact content must be text")
        if not isinstance(self.deleted, bool):
            raise ValueError("artifact deleted flag must be boolean")


@dataclass(frozen=True)
class SyntaxContribution:
    """Plugin-owned parser/query declaration consumed by neutral hosts."""

    plugin_id: str
    language_id: str
    grammar_module: str
    grammar_factory: str
    query_resource: str = ""
    builtin_tags: bool = False
    rich_traversal_safe: bool = True

    def __post_init__(self) -> None:
        _plugin_id(self.plugin_id)
        if (
            not isinstance(self.language_id, str)
            or not _LANGUAGE_ID.fullmatch(self.language_id)
        ):
            raise ValueError("syntax language id is invalid")
        if (
            not isinstance(self.grammar_module, str)
            or not _PYTHON_MODULE.fullmatch(self.grammar_module)
        ):
            raise ValueError("syntax grammar module is invalid")
        if (
            not isinstance(self.grammar_factory, str)
            or not self.grammar_factory.isidentifier()
        ):
            raise ValueError("syntax grammar factory is invalid")
        if not isinstance(self.query_resource, str):
            raise ValueError("syntax query resource must be text")
        if self.query_resource:
            resource = self.query_resource.replace("\\", "/")
            if (
                resource != self.query_resource
                or resource.startswith("/")
                or any(
                    segment in {"", ".", ".."}
                    for segment in resource.split("/")
                )
            ):
                raise ValueError(
                    "syntax query resource must be safe and relative"
                )
        if not isinstance(self.builtin_tags, bool):
            raise ValueError("syntax builtin-tags flag must be boolean")
        if not isinstance(self.rich_traversal_safe, bool):
            raise ValueError(
                "syntax rich-traversal flag must be boolean"
            )


@dataclass(frozen=True, order=True)
class RepositorySnapshot:
    """Opaque, plugin-owned state used for exact incremental graph overlays."""

    plugin_id: str
    kind: str
    content: str

    def __post_init__(self) -> None:
        _plugin_id(self.plugin_id)
        _non_blank(self.kind, "repository snapshot kind")
        _non_blank(self.content, "repository snapshot content")


@dataclass(frozen=True, order=True)
class RepositoryContext:
    """Exact plugin-selected source kept outside semantic embedding."""

    plugin_id: str
    kind: str
    path: str
    content: str
    attributes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _plugin_id(self.plugin_id)
        _non_blank(self.kind, "repository context kind")
        normalized = normalize_path(self.path)
        if normalized != self.path:
            raise ValueError("repository context path must already be normalized")
        _non_blank(self.content, "repository context content")
        _sorted_unique(self.attributes, "repository context attributes")


@dataclass(frozen=True, order=True)
class GraphFact:
    kind: str
    source: str
    relation: str
    target: str
    path: str
    line: int = 1
    attributes: tuple[tuple[str, str], ...] = ()
    related_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _non_blank(self.kind, "graph fact kind")
        _non_blank(self.source, "graph fact source")
        _non_blank(self.relation, "graph fact relation")
        _non_blank(self.target, "graph fact target")
        normalized = normalize_path(self.path)
        if normalized != self.path:
            raise ValueError("graph fact path must already be normalized")
        if not isinstance(self.line, int) or self.line < 1:
            raise ValueError("graph fact line must be a positive integer")
        _sorted_unique(self.attributes, "graph fact attributes")
        normalized_related = tuple(normalize_path(path) for path in self.related_paths)
        if normalized_related != self.related_paths:
            raise ValueError("graph fact related paths must already be normalized")
        _sorted_unique(self.related_paths, "graph fact related paths")
        if any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            for key, value in self.attributes
        ):
            raise ValueError("graph fact attributes must be non-blank string keys and string values")

    def as_metadata(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "kind": self.kind,
                "source": self.source,
                "relation": self.relation,
                "target": self.target,
                "path": self.path,
                "line": self.line,
                "attributes": dict(self.attributes),
                "related_paths": list(self.related_paths),
            }
        )


@dataclass(frozen=True, order=True)
class SymbolDefinition:
    qualified_name: str
    kind: str
    path: str
    line: int = 1
    parents: tuple[str, ...] = ()
    methods: tuple[str, ...] = ()
    constructor_types: tuple[str, ...] = ()
    attributes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _non_blank(self.qualified_name, "qualified symbol name")
        _non_blank(self.kind, "symbol kind")
        normalized = normalize_path(self.path)
        if normalized != self.path:
            raise ValueError("symbol path must already be normalized")
        if not isinstance(self.line, int) or self.line < 1:
            raise ValueError("symbol line must be a positive integer")
        _sorted_unique(self.parents, "symbol parents")
        _sorted_unique(self.methods, "symbol methods")
        _sorted_unique(self.constructor_types, "constructor types")
        _sorted_unique(self.attributes, "symbol attributes")


@dataclass(frozen=True, order=True)
class ArchitecturePacket:
    plugin_id: str
    kind: str
    key: str
    paths: tuple[str, ...]
    facts: tuple[GraphFact, ...]
    attributes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _plugin_id(self.plugin_id)
        _non_blank(self.kind, "architecture packet kind")
        _non_blank(self.key, "architecture packet key")
        normalized = tuple(normalize_path(path) for path in self.paths)
        if normalized != self.paths:
            raise ValueError("architecture packet paths must already be normalized")
        _sorted_unique(self.paths, "architecture packet paths")
        _sorted_unique(self.facts, "architecture packet facts")
        _sorted_unique(self.attributes, "architecture packet attributes")
        if not self.paths or not self.facts:
            raise ValueError("architecture packet requires paths and facts")


@dataclass(frozen=True)
class RepositoryAnalysis:
    symbols: tuple[SymbolDefinition, ...] = ()
    packets: tuple[ArchitecturePacket, ...] = ()
    snapshots: tuple[RepositorySnapshot, ...] = ()
    contexts: tuple[RepositoryContext, ...] = ()
    diagnostics: tuple[PluginDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        _sorted_unique(self.symbols, "repository symbols")
        _sorted_unique(self.packets, "architecture packets")
        _sorted_unique(self.snapshots, "repository snapshots")
        _sorted_unique(self.contexts, "repository contexts")
        if any(
            not isinstance(diagnostic, PluginDiagnostic)
            for diagnostic in self.diagnostics
        ):
            raise ValueError(
                "repository diagnostics must contain PluginDiagnostic values"
            )


@dataclass(frozen=True, order=True)
class EvidenceRequest:
    kind: str
    identifier: str
    reason: str
    required: bool = True

    def __post_init__(self) -> None:
        _non_blank(self.kind, "evidence request kind")
        _non_blank(self.identifier, "evidence request identifier")
        _non_blank(self.reason, "evidence request reason")


@dataclass(frozen=True)
class ReviewContribution:
    rules: tuple[str, ...] = ()
    evidence_requests: tuple[EvidenceRequest, ...] = ()
    group_paths: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        _sorted_unique(self.rules, "review rules")
        _sorted_unique(self.evidence_requests, "evidence requests")
        normalized_groups: list[tuple[str, ...]] = []
        for group in self.group_paths:
            normalized = tuple(normalize_path(path) for path in group)
            if normalized != group:
                raise ValueError("group paths must already be normalized")
            _sorted_unique(group, "group paths")
            normalized_groups.append(group)
        if tuple(sorted(normalized_groups)) != tuple(normalized_groups):
            raise ValueError("path groups must be sorted")


@dataclass(frozen=True)
class CandidateClaim:
    category: str
    path: str
    line: int
    message: str
    evidence: tuple[GraphFact, ...] = ()
    claim_kind: str = ""

    def __post_init__(self) -> None:
        _non_blank(self.category, "candidate category")
        normalized = normalize_path(self.path)
        if normalized != self.path:
            raise ValueError("candidate path must already be normalized")
        if not isinstance(self.line, int) or self.line < 1:
            raise ValueError("candidate line must be a positive integer")
        _non_blank(self.message, "candidate message")
        if not isinstance(self.claim_kind, str):
            raise ValueError("candidate claim kind must be text")
        if self.claim_kind and not self.claim_kind.strip():
            raise ValueError("candidate claim kind must be blank or non-blank text")
        if self.claim_kind != self.claim_kind.strip():
            raise ValueError("candidate claim kind must already be normalized")
        _sorted_unique(self.evidence, "candidate evidence")


@dataclass(frozen=True)
class ValidationResult:
    decision: ValidationDecision
    code: str
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.decision, ValidationDecision):
            raise ValueError("validation decision is invalid")
        _non_blank(self.code, "validation code")
        _non_blank(self.message, "validation message")
