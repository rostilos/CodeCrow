from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, replace
from typing import Iterable

from .javascript import (
    RequireJsRelation,
    _mask_php,
    _parse_javascript,
    _string,
    _text,
)


_AMD_CALLS = frozenset({"define", "require", "requirejs"})
_AMD_MAGIC_DEPENDENCIES = frozenset({"exports", "module", "require"})
_SCRIPT = re.compile(
    r"<script(?P<attributes>(?:\s[^>]*)?)>(?P<body>.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
_SCRIPT_TYPE = re.compile(
    r'''\btype\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))''',
    re.IGNORECASE,
)
_EXECUTABLE_SCRIPT_TYPES = frozenset({
    "application/ecmascript",
    "application/javascript",
    "module",
    "text/ecmascript",
    "text/javascript",
})


def _literal_module_id(value: str) -> str:
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > 512
        or "<?" in candidate
        or any(character.isspace() for character in candidate)
    ):
        return ""
    return candidate


@dataclass(frozen=True, order=True)
class AmdDependency:
    consumer_kind: str
    named_module: str
    dependency: str
    line: int
    position: int


@dataclass(frozen=True, order=True)
class RequireJsDeclaration:
    kind: str
    source: str
    relation: str
    target: str
    path: str
    line: int
    scope: str = ""
    position: int = 0


@dataclass(frozen=True, order=True)
class RequireJsResolution:
    identifier: str
    config_paths: tuple[str, ...] = ()
    config_kinds: tuple[str, ...] = ()
    fallback_position: int = 0
    mapped_identifier: str = ""


@dataclass(frozen=True)
class EffectiveRequireJsConfig:
    area: str
    theme: str
    declarations: tuple[RequireJsDeclaration, ...]

    def _declarations(self, kind: str) -> tuple[RequireJsDeclaration, ...]:
        return tuple(
            declaration
            for declaration in self.declarations
            if declaration.kind == kind
        )

    def resolve(
        self,
        identifier: str,
        consumer: str = "",
    ) -> tuple[RequireJsResolution, ...]:
        """Apply exact effective map/path entries to one literal module ID."""

        requested = normalize_amd_dependency(identifier, consumer)
        if not requested:
            return ()
        if "!" in requested:
            return (RequireJsResolution(requested),)

        config_paths: list[str] = []
        config_kinds: list[str] = []
        mapped = requested
        scopes = []
        current_scope = consumer
        while current_scope:
            scopes.append(current_scope)
            current_scope = current_scope.rpartition("/")[0]
        scopes.append("*")
        mappings = self._declarations("map")
        for scope in scopes:
            candidates = tuple(
                declaration
                for declaration in mappings
                if declaration.scope == scope
                and _prefix_match(mapped, declaration.source)
            )
            if not candidates:
                continue
            longest = max(len(candidate.source) for candidate in candidates)
            selected = tuple(
                candidate
                for candidate in candidates
                if len(candidate.source) == longest
            )
            if len(selected) != 1:
                break
            declaration = selected[0]
            mapped = _replace_prefix(
                mapped,
                declaration.source,
                declaration.target,
            )
            config_paths.append(declaration.path)
            config_kinds.append("map")
            break

        paths = tuple(
            declaration
            for declaration in self._declarations("path")
            if _prefix_match(mapped, declaration.source)
        )
        if not paths:
            return (RequireJsResolution(
                mapped,
                tuple(dict.fromkeys(config_paths)),
                tuple(dict.fromkeys(config_kinds)),
            ),)
        longest = max(len(candidate.source) for candidate in paths)
        selected_paths = tuple(sorted(
            (
                candidate
                for candidate in paths
                if len(candidate.source) == longest
            ),
            key=lambda candidate: candidate.position,
        ))
        alias = selected_paths[0].source
        return tuple(
            RequireJsResolution(
                _replace_prefix(mapped, alias, declaration.target),
                tuple(dict.fromkeys((*config_paths, declaration.path))),
                tuple(dict.fromkeys((*config_kinds, "path"))),
                declaration.position,
                mapped,
            )
            for declaration in selected_paths
        )

    def mixins_for(
        self,
        *identifiers: str,
    ) -> tuple[RequireJsDeclaration, ...]:
        identities = set(identifiers)
        effective: dict[tuple[str, str], RequireJsDeclaration] = {}
        for declaration in self._declarations("mixin"):
            if declaration.source not in identities:
                continue
            identity = (declaration.source, declaration.target)
            current = effective.get(identity)
            if current is None or declaration.line >= current.line:
                effective[identity] = declaration
        return tuple(sorted(
            declaration
            for declaration in effective.values()
            if declaration.relation == "mixed-by"
        ))


def _prefix_match(identifier: str, prefix: str) -> bool:
    return identifier == prefix or identifier.startswith(prefix + "/")


def _replace_prefix(identifier: str, prefix: str, target: str) -> str:
    suffix = identifier[len(prefix):]
    return target.rstrip("/") + suffix


def normalize_amd_dependency(identifier: str, consumer: str = "") -> str:
    literal = _literal_module_id(identifier)
    if not literal or literal in _AMD_MAGIC_DEPENDENCIES:
        return ""
    loader, marker, resource = literal.partition("!")
    candidate = resource if marker else loader
    if candidate.startswith("."):
        if not consumer:
            return ""
        base = consumer.rpartition("/")[0]
        candidate = posixpath.normpath(posixpath.join(base, candidate))
        if candidate == ".." or candidate.startswith("../"):
            return ""
    return f"{loader}!{candidate}" if marker else candidate


def extract_amd_dependencies(content: str) -> tuple[AmdDependency, ...]:
    """Extract literal AMD dependency-array entries from direct loader calls."""

    source, tree = _parse_javascript(content)
    dependencies: set[AmdDependency] = set()
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        if node.type == "call_expression":
            function = node.child_by_field_name("function")
            arguments = node.child_by_field_name("arguments")
            call = (
                _text(function, source).strip()
                if function is not None and function.type == "identifier"
                else ""
            )
            argument_nodes = (
                arguments.named_children if arguments is not None else ()
            )
            if call in _AMD_CALLS:
                named_module = (
                    _literal_module_id(_string(argument_nodes[0], source))
                    if call == "define"
                    and argument_nodes
                    and argument_nodes[0].type == "string"
                    else ""
                )
                dependency_array = next(
                    (
                        argument
                        for argument in argument_nodes[:2]
                        if argument.type == "array"
                    ),
                    None,
                )
                if dependency_array is not None:
                    for position, dependency in enumerate(
                        dependency_array.named_children
                    ):
                        literal = _literal_module_id(
                            _string(dependency, source)
                        )
                        if literal and literal not in _AMD_MAGIC_DEPENDENCIES:
                            dependencies.add(AmdDependency(
                                call,
                                named_module,
                                literal,
                                dependency.start_point[0] + 1,
                                position,
                            ))
        pending.extend(node.named_children)
    return tuple(sorted(dependencies))


def extract_template_amd_dependencies(
    content: str,
) -> tuple[AmdDependency, ...]:
    """Extract direct AMD arrays from executable inline PHTML scripts."""

    dependencies: set[AmdDependency] = set()
    for script in _SCRIPT.finditer(content):
        type_match = _SCRIPT_TYPE.search(script.group("attributes"))
        if type_match is not None:
            script_type = next(
                value
                for value in type_match.groups()
                if value is not None
            ).strip().casefold()
            if script_type not in _EXECUTABLE_SCRIPT_TYPES:
                continue
        line_offset = content.count("\n", 0, script.start("body"))
        for dependency in extract_amd_dependencies(
            _mask_php(script.group("body"))
        ):
            dependencies.add(replace(
                dependency,
                line=line_offset + dependency.line,
            ))
    return tuple(sorted(dependencies))


def build_effective_requirejs_config(
    area: str,
    theme: str,
    records: Iterable[tuple[str, tuple[RequireJsRelation, ...]]],
) -> EffectiveRequireJsConfig:
    """Apply Magento's ordered config replacement semantics per identity."""

    effective: dict[
        tuple[str, ...],
        tuple[str, tuple[RequireJsRelation, ...]],
    ] = {}
    for path, relations in records:
        grouped: dict[tuple[str, ...], list[RequireJsRelation]] = {}
        for relation in relations:
            if relation.kind == "path":
                identity = ("path", relation.source)
            elif relation.kind == "map":
                identity = ("map", relation.scope, relation.source)
            elif relation.kind == "mixin":
                identity = ("mixin", relation.source, relation.target)
            else:
                continue
            grouped.setdefault(identity, []).append(relation)
        for identity, grouped_relations in grouped.items():
            effective[identity] = (path, tuple(sorted(grouped_relations)))

    declarations = tuple(sorted(
        RequireJsDeclaration(
            relation.kind,
            relation.source,
            relation.relation,
            relation.target,
            path,
            relation.line,
            relation.scope,
            relation.position,
        )
        for path, relations in effective.values()
        for relation in relations
    ))
    return EffectiveRequireJsConfig(area, theme, declarations)
