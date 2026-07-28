from __future__ import annotations

import base64
import gzip
import json
import re
from collections import deque
from dataclasses import asdict, dataclass

from codecrow_plugins import (
    ArchitecturePacket,
    FileArtifact,
    GraphFact,
    PluginOutcome,
    RepositoryAnalysis,
    RepositorySnapshot,
)


_VIEW_MODEL_REGISTRY = r"Hyva\Theme\Model\ViewModelRegistry"
_PHP_REGION = re.compile(r"<\?(?:php|=)(?P<body>.*?)\?>", re.DOTALL)
_SCRIPT_REGION = re.compile(
    r"<script(?:\s[^>]*)?>(?P<body>.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
_USE = re.compile(
    r"(?m)^[ \t]*use[ \t]+"
    r"(?P<class>\\?[A-Za-z_][A-Za-z0-9_\\]*)"
    r"(?:[ \t]+as[ \t]+(?P<alias>[A-Za-z_][A-Za-z0-9_]*))?"
    r"[ \t]*;"
)
_VAR_ANNOTATION = re.compile(
    r"@var\s+(?P<class>\\?[A-Za-z_][A-Za-z0-9_\\]*)"
    r"\s+\$(?P<variable>[A-Za-z_][A-Za-z0-9_]*)"
)
_REGISTRY_REQUIRE = re.compile(
    r"(?:(?P<assigned>\$[A-Za-z_][A-Za-z0-9_]*)\s*=\s*)?"
    r"(?P<registry>\$[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*(?:->|\?->)\s*require\s*\(\s*"
    r"(?P<class>\\?[A-Za-z_][A-Za-z0-9_\\]*)::class"
)
_HYVA_CSP_CALL = re.compile(
    r"(?P<variable>\$hyvaCsp)"
    r"\s*(?:->|\?->)\s*"
    r"(?P<method>(?i:registerInlineScript))\s*\("
)
_FETCH = re.compile(r"\bfetch\s*\(")
_VIEW_MODEL_REST_URL = re.compile(
    r"\$(?P<variable>[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*(?:->|\?->)\s*getRestUrl\s*\(\s*"
    r"(?P<quote>['\"])(?P<path>/?rest/[^'\"]+)(?P=quote)"
)
_HTTP_METHOD = re.compile(
    r"\bmethod\s*:\s*['\"](?P<method>GET|POST|PUT|PATCH|DELETE)['\"]",
    re.IGNORECASE,
)
_STATE_WRITE = re.compile(
    r"\bthis\.(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*="
)
_ALPINE_ATTRIBUTE = re.compile(
    r"(?:\bx-[A-Za-z0-9_.:-]+|(?<![A-Za-z0-9_])[:@][A-Za-z0-9_.:-]+)"
    r"\s*=\s*(?P<quote>['\"])(?P<expression>.*?)(?P=quote)",
    re.DOTALL,
)
_X_DATA_ATTRIBUTE = re.compile(
    r"\bx-data\s*=\s*(?P<quote>['\"])(?P<expression>.*?)(?P=quote)",
    re.DOTALL,
)
_ALPINE_EVENT_ATTRIBUTE = re.compile(
    r"(?P<directive>@|x-on:)"
    r"(?P<event>[A-Za-z_][A-Za-z0-9_:-]*)"
    r"(?P<modifiers>(?:\.[A-Za-z0-9_:-]+)*)"
    r"\s*=\s*(?P<quote>['\"])(?P<expression>.*?)(?P=quote)",
    re.DOTALL,
)
_ALPINE_DISPATCH = re.compile(
    r"\$dispatch\s*\(\s*"
    r"(?P<quote>['\"])(?P<event>[A-Za-z_][A-Za-z0-9_:-]*)(?P=quote)"
)
_EXACT_ALPINE_PROVIDER = re.compile(
    r"^\s*(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?P<call>\s*\([^)]*\))?\s*$",
    re.DOTALL,
)
_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_CALL_FACT_KINDS = frozenset({
    "php-instance-call-relation",
    "php-static-call-relation",
    "php-intra-class-call-relation",
})


@dataclass(frozen=True, order=True)
class ViewModelRequirement:
    class_name: str
    registry_variable: str
    assigned_variable: str
    line: int


@dataclass(frozen=True, order=True)
class WebApiReference:
    view_model_variable: str
    http_method: str
    route: str
    line: int
    state_identifiers: tuple[str, ...] = ()


@dataclass(frozen=True, order=True)
class AlpineProviderDefinition:
    provider_name: str
    factory_name: str
    line: int
    resolution: str


@dataclass(frozen=True, order=True)
class AlpineProviderUse:
    provider_name: str
    invocation: str
    line: int


@dataclass(frozen=True, order=True)
class AlpineEventDispatch:
    event_name: str
    line: int


@dataclass(frozen=True, order=True)
class AlpineEventListener:
    event_name: str
    line: int
    window: bool


@dataclass(frozen=True, order=True)
class TemplateRuntimeVariable:
    variable_name: str
    class_name: str
    method_name: str
    line: int


@dataclass(frozen=True)
class TemplateRuntime:
    requirements: tuple[ViewModelRequirement, ...] = ()
    webapi_references: tuple[WebApiReference, ...] = ()
    alpine_identifiers: tuple[str, ...] = ()
    alpine_provider_definitions: tuple[AlpineProviderDefinition, ...] = ()
    alpine_provider_uses: tuple[AlpineProviderUse, ...] = ()
    alpine_event_dispatches: tuple[AlpineEventDispatch, ...] = ()
    alpine_event_listeners: tuple[AlpineEventListener, ...] = ()
    runtime_variables: tuple[TemplateRuntimeVariable, ...] = ()


def _line(content: str, offset: int) -> int:
    return content.count("\n", 0, max(0, offset)) + 1


def _resolve_class(reference: str, imports: dict[str, str]) -> str:
    normalized = reference.strip().lstrip("\\")
    if not normalized:
        return ""
    head, separator, tail = normalized.partition("\\")
    imported = imports.get(head.casefold())
    if imported:
        return imported + (f"\\{tail}" if separator else "")
    # PHTML executes in the global namespace. A qualified name is therefore
    # already exact; an unimported short name is not.
    return normalized if separator else ""


def _canonical_rest_route(value: str) -> str:
    parts = [part for part in value.strip().strip("/").split("/") if part]
    if not parts or parts[0].casefold() != "rest":
        return ""
    try:
        api_offset = next(
            index for index, part in enumerate(parts)
            if part.casefold() == "v1"
        )
    except StopIteration:
        return ""
    if api_offset == len(parts) - 1:
        return ""
    return "/" + "/".join(parts[api_offset:])


def _mask_php(content: str) -> str:
    return _PHP_REGION.sub(
        lambda match: _blank_preserving_lines(match.group(0)),
        content,
    )


def _blank_preserving_lines(value: str) -> str:
    return "".join(
        "\n" if character == "\n" else " "
        for character in value
    )


def _static_markup(content: str) -> str:
    without_scripts = _SCRIPT_REGION.sub(
        lambda match: _blank_preserving_lines(match.group(0)),
        content,
    )
    return _PHP_REGION.sub(
        lambda match: _blank_preserving_lines(match.group(0)),
        without_scripts,
    )


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode(
        "utf-8",
        errors="replace",
    )


def _string_value(node, source: bytes) -> str:
    if node is None or node.type != "string":
        return ""
    value = _node_text(node, source).strip()
    if len(value) < 2 or value[0] not in {'"', "'"}:
        return ""
    return value[1:-1] if value[-1] == value[0] else ""


def _is_alpine_data_call(node, source: bytes) -> bool:
    if node is None or node.type != "member_expression":
        return False
    owner = node.child_by_field_name("object")
    property_node = node.child_by_field_name("property")
    return bool(
        owner is not None
        and property_node is not None
        and _node_text(owner, source).strip() == "Alpine"
        and _node_text(property_node, source).strip() == "data"
    )


def _extract_alpine_provider_definitions(
    content: str,
) -> tuple[AlpineProviderDefinition, ...]:
    """Parse live constructor definitions from PHTML JavaScript regions."""
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_javascript
    except ImportError as exception:
        raise RuntimeError(
            "Hyva Alpine analysis requires tree-sitter-javascript"
        ) from exception

    declarations: dict[str, set[int]] = {}
    registrations: list[tuple[str, str, int, str]] = []
    for script in _SCRIPT_REGION.finditer(content):
        body = _mask_php(script.group("body"))
        source = body.encode("utf-8")
        tree = Parser(Language(tree_sitter_javascript.language())).parse(
            source
        )
        line_offset = content.count("\n", 0, script.start("body"))
        pending = [tree.root_node]
        while pending:
            node = pending.pop()
            if node.type == "function_declaration":
                name_node = node.child_by_field_name("name")
                name = (
                    _node_text(name_node, source).strip()
                    if name_node is not None
                    else ""
                )
                if _IDENTIFIER.fullmatch(name):
                    declarations.setdefault(name, set()).add(
                        line_offset + node.start_point[0] + 1
                    )
            elif node.type == "call_expression" and _is_alpine_data_call(
                node.child_by_field_name("function"),
                source,
            ):
                arguments = node.child_by_field_name("arguments")
                values = (
                    arguments.named_children if arguments is not None else ()
                )
                if len(values) < 2:
                    pending.extend(node.named_children)
                    continue
                provider_name = _string_value(values[0], source)
                factory_node = values[1]
                if not _IDENTIFIER.fullmatch(provider_name):
                    pending.extend(node.named_children)
                    continue
                if factory_node.type == "identifier":
                    factory_name = _node_text(
                        factory_node,
                        source,
                    ).strip()
                    resolution = "exact-alpine-data-named-factory"
                elif factory_node.type in {
                    "arrow_function",
                    "function_expression",
                }:
                    factory_name = "<inline>"
                    resolution = "exact-alpine-data-inline-factory"
                else:
                    pending.extend(node.named_children)
                    continue
                registrations.append((
                    provider_name,
                    factory_name,
                    line_offset + node.start_point[0] + 1,
                    resolution,
                ))
            pending.extend(node.named_children)

    definitions: set[AlpineProviderDefinition] = {
        AlpineProviderDefinition(
            provider_name=name,
            factory_name=name,
            line=next(iter(lines)),
            resolution="exact-global-function",
        )
        for name, lines in declarations.items()
        if len(lines) == 1
    }
    for provider_name, factory_name, line, resolution in registrations:
        if (
            factory_name != "<inline>"
            and len(declarations.get(factory_name, ())) != 1
        ):
            continue
        if provider_name == factory_name:
            definitions = {
                definition
                for definition in definitions
                if not (
                    definition.provider_name == provider_name
                    and definition.factory_name == factory_name
                    and definition.resolution == "exact-global-function"
                )
            }
        definitions.add(AlpineProviderDefinition(
            provider_name=provider_name,
            factory_name=factory_name,
            line=line,
            resolution=resolution,
        ))
    return tuple(sorted(definitions))


def extract_template_runtime(content: str) -> TemplateRuntime:
    """Extract only source-proven Hyva registry and REST-literal relations."""
    regions = tuple(_PHP_REGION.finditer(content))
    imports: dict[str, str] = {}
    for region in regions:
        body = region.group("body")
        for match in _USE.finditer(body):
            class_name = match.group("class").lstrip("\\")
            alias = match.group("alias") or class_name.rsplit("\\", 1)[-1]
            imports[alias.casefold()] = class_name

    registry_variables: set[str] = set()
    for region in regions:
        body = region.group("body")
        for match in _VAR_ANNOTATION.finditer(body):
            if (
                _resolve_class(match.group("class"), imports).casefold()
                == _VIEW_MODEL_REGISTRY.casefold()
            ):
                registry_variables.add("$" + match.group("variable"))

    requirements: set[ViewModelRequirement] = set()
    assigned_classes: dict[str, set[str]] = {}
    for region in regions:
        body = region.group("body")
        body_offset = region.start("body")
        for match in _REGISTRY_REQUIRE.finditer(body):
            if match.group("registry") not in registry_variables:
                continue
            class_name = _resolve_class(match.group("class"), imports)
            if not class_name:
                continue
            assigned = match.group("assigned") or ""
            requirement = ViewModelRequirement(
                class_name=class_name,
                registry_variable=match.group("registry"),
                assigned_variable=assigned,
                line=_line(content, body_offset + match.start()),
            )
            requirements.add(requirement)
            if assigned:
                assigned_classes.setdefault(assigned, set()).add(class_name)

    exact_assignments = {
        variable: next(iter(classes))
        for variable, classes in assigned_classes.items()
        if len(classes) == 1
    }
    webapi_references: set[WebApiReference] = set()
    for fetch in _FETCH.finditer(content):
        # The route expression and inline method option must be local to this
        # fetch call. The bounded window intentionally abstains from variables,
        # helper-built options, interpolated routes, and distant syntax.
        window = content[fetch.end():fetch.end() + 1_200]
        route_match = _VIEW_MODEL_REST_URL.search(window)
        if route_match is None or route_match.start() > 500:
            continue
        variable = "$" + route_match.group("variable")
        if variable not in exact_assignments:
            continue
        route = _canonical_rest_route(route_match.group("path"))
        if not route:
            continue
        method_match = _HTTP_METHOD.search(
            window[route_match.end():route_match.end() + 500]
        )
        method = method_match.group("method").upper() if method_match else "GET"
        state_identifiers = tuple(sorted({
            match.group("name")
            for match in _STATE_WRITE.finditer(window)
        }))
        webapi_references.add(WebApiReference(
            view_model_variable=variable,
            http_method=method,
            route=route,
            line=_line(content, fetch.start()),
            state_identifiers=state_identifiers,
        ))

    markup = _static_markup(content)
    alpine_identifiers = tuple(sorted({
        identifier
        for attribute in _ALPINE_ATTRIBUTE.finditer(markup)
        for identifier in _IDENTIFIER.findall(attribute.group("expression"))
    }))
    provider_uses: set[AlpineProviderUse] = set()
    for attribute in _X_DATA_ATTRIBUTE.finditer(markup):
        expression = attribute.group("expression")
        exact = _EXACT_ALPINE_PROVIDER.fullmatch(expression)
        if exact is None:
            continue
        provider_uses.add(AlpineProviderUse(
            provider_name=exact.group("name"),
            invocation=(
                "call" if exact.group("call") is not None else "reference"
            ),
            line=_line(markup, attribute.start()),
        ))
    event_dispatches: set[AlpineEventDispatch] = set()
    event_listeners: set[AlpineEventListener] = set()
    for attribute in _ALPINE_EVENT_ATTRIBUTE.finditer(markup):
        event_name = attribute.group("event")
        modifiers = {
            value.casefold()
            for value in attribute.group("modifiers").split(".")
            if value
        }
        event_listeners.add(AlpineEventListener(
            event_name=event_name,
            line=_line(markup, attribute.start()),
            window="window" in modifiers,
        ))
        for dispatch in _ALPINE_DISPATCH.finditer(
            attribute.group("expression")
        ):
            event_dispatches.add(AlpineEventDispatch(
                event_name=dispatch.group("event"),
                line=_line(
                    markup,
                    attribute.start("expression") + dispatch.start(),
                ),
            ))
    runtime_variables: set[TemplateRuntimeVariable] = set()
    for region in regions:
        body = region.group("body")
        body_offset = region.start("body")
        for match in _HYVA_CSP_CALL.finditer(body):
            runtime_variables.add(TemplateRuntimeVariable(
                variable_name=match.group("variable"),
                class_name="Hyva\\Theme\\ViewModel\\HyvaCsp",
                method_name="registerInlineScript",
                line=_line(content, body_offset + match.start()),
            ))
    return TemplateRuntime(
        requirements=tuple(sorted(requirements)),
        webapi_references=tuple(sorted(webapi_references)),
        alpine_identifiers=alpine_identifiers,
        alpine_provider_definitions=_extract_alpine_provider_definitions(
            content
        ),
        alpine_provider_uses=tuple(sorted(provider_uses)),
        alpine_event_dispatches=tuple(sorted(event_dispatches)),
        alpine_event_listeners=tuple(sorted(event_listeners)),
        runtime_variables=tuple(sorted(runtime_variables)),
    )


def _normalized_route(value: str) -> str:
    return "/" + "/".join(
        part for part in value.strip().strip("/").split("/") if part
    )


class HyvaRepositorySession:
    SNAPSHOT_KIND = "hyva-template-runtime"
    MAX_CALL_GRAPH_STATES = 64

    def __init__(
        self,
        plugin_id: str,
        revision: str,
        templates: dict[str, TemplateRuntime] | None = None,
    ) -> None:
        self.plugin_id = plugin_id
        self.revision = revision
        self.templates = dict(templates or {})

    @classmethod
    def restore(
        cls,
        plugin_id: str,
        revision: str,
        snapshots,
    ) -> "HyvaRepositorySession":
        snapshot = next(
            (
                item for item in snapshots
                if item.kind == cls.SNAPSHOT_KIND
            ),
            None,
        )
        if snapshot is None:
            raise ValueError(
                "Hyva repository snapshot is missing hyva-template-runtime"
            )
        raw = gzip.decompress(base64.b64decode(snapshot.content.encode("ascii")))
        records = json.loads(raw.decode("utf-8"))
        if not isinstance(records, dict):
            raise ValueError("Hyva template snapshot must contain an object")
        templates = {}
        for path, record in records.items():
            if not isinstance(path, str) or not isinstance(record, dict):
                raise ValueError("Hyva template snapshot contains invalid data")
            templates[path] = TemplateRuntime(
                requirements=tuple(sorted(
                    ViewModelRequirement(**value)
                    for value in record.get("requirements", ())
                )),
                webapi_references=tuple(sorted(
                    WebApiReference(
                        view_model_variable=value["view_model_variable"],
                        http_method=value["http_method"],
                        route=value["route"],
                        line=value["line"],
                        state_identifiers=tuple(
                            value.get("state_identifiers", ())
                        ),
                    )
                    for value in record.get("webapiReferences", ())
                )),
                alpine_identifiers=tuple(sorted(
                    record.get("alpineIdentifiers", ())
                )),
                alpine_provider_definitions=tuple(sorted(
                    AlpineProviderDefinition(**value)
                    for value in record.get(
                        "alpineProviderDefinitions",
                        (),
                    )
                )),
                alpine_provider_uses=tuple(sorted(
                    AlpineProviderUse(**value)
                    for value in record.get("alpineProviderUses", ())
                )),
                alpine_event_dispatches=tuple(sorted(
                    AlpineEventDispatch(**value)
                    for value in record.get("alpineEventDispatches", ())
                )),
                alpine_event_listeners=tuple(sorted(
                    AlpineEventListener(**value)
                    for value in record.get("alpineEventListeners", ())
                )),
                runtime_variables=tuple(sorted(
                    TemplateRuntimeVariable(**value)
                    for value in record.get("runtimeVariables", ())
                )),
            )
        return cls(plugin_id, revision, templates)

    def ingest(self, artifacts: tuple[FileArtifact, ...]) -> None:
        for artifact in artifacts:
            if not artifact.path.casefold().endswith(".phtml"):
                continue
            if artifact.deleted:
                self.templates.pop(artifact.path, None)
                continue
            runtime = extract_template_runtime(artifact.content)
            if (
                runtime.requirements
                or runtime.webapi_references
                or runtime.alpine_identifiers
                or runtime.alpine_provider_definitions
                or runtime.alpine_provider_uses
                or runtime.alpine_event_dispatches
                or runtime.alpine_event_listeners
                or runtime.runtime_variables
            ):
                self.templates[artifact.path] = runtime
            else:
                self.templates.pop(artifact.path, None)

    def _snapshot(self) -> RepositorySnapshot:
        records = {
            path: {
                "requirements": [
                    asdict(value) for value in runtime.requirements
                ],
                "webapiReferences": [
                    asdict(value) for value in runtime.webapi_references
                ],
                "alpineIdentifiers": list(runtime.alpine_identifiers),
                "alpineProviderDefinitions": [
                    asdict(value)
                    for value in runtime.alpine_provider_definitions
                ],
                "alpineProviderUses": [
                    asdict(value) for value in runtime.alpine_provider_uses
                ],
                "alpineEventDispatches": [
                    asdict(value) for value in runtime.alpine_event_dispatches
                ],
                "alpineEventListeners": [
                    asdict(value) for value in runtime.alpine_event_listeners
                ],
                "runtimeVariables": [
                    asdict(value) for value in runtime.runtime_variables
                ],
            }
            for path, runtime in sorted(self.templates.items())
        }
        raw = json.dumps(
            records,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        content = base64.b64encode(
            gzip.compress(raw, compresslevel=6, mtime=0)
        ).decode("ascii")
        return RepositorySnapshot(
            self.plugin_id,
            self.SNAPSHOT_KIND,
            content,
        )

    @staticmethod
    def _layout_topology(
        dependencies: RepositoryAnalysis,
    ) -> tuple[
        dict[str, dict[str, dict[str, str]]],
        dict[str, set[str]],
    ]:
        blocks_by_source: dict[str, dict[str, dict[str, str]]] = {}
        sources_by_template: dict[str, set[str]] = {}
        for packet in dependencies.packets:
            if packet.plugin_id != "magento" or packet.kind != "magento-layout":
                continue
            for fact in packet.facts:
                if fact.kind != "magento-layout-block":
                    continue
                selected = dict(fact.attributes).get(
                    "selectedTemplatePath",
                    "",
                )
                attributes = dict(fact.attributes)
                name = attributes.get("name", "")
                if not name:
                    continue
                block = {
                    "alias": attributes.get("alias", ""),
                    "name": name,
                    "parent": attributes.get("parentName", ""),
                    "template": selected,
                }
                prior = blocks_by_source.setdefault(
                    fact.path,
                    {},
                ).get(name)
                if prior is None:
                    blocks_by_source[fact.path][name] = block
                elif prior != block:
                    # Multiple contradictory declarations in one physical XML
                    # source are not a stable render edge.
                    blocks_by_source[fact.path].pop(name, None)
                if selected:
                    sources_by_template.setdefault(
                        selected,
                        set(),
                    ).add(fact.path)
        return blocks_by_source, sources_by_template

    @staticmethod
    def _hyva_theme_templates(
        dependencies: RepositoryAnalysis,
    ) -> dict[str, tuple[str, tuple[str, ...]]]:
        """Map exact theme roots to a Hyva identity and inheritance proof."""
        roots: dict[tuple[str, str], set[tuple[str, str]]] = {}
        parents: dict[tuple[str, str], set[str]] = {}
        theme_paths: dict[tuple[str, str], set[str]] = {}
        for packet in dependencies.packets:
            if packet.plugin_id != "magento" or packet.kind != "magento-theme":
                continue
            for fact in packet.facts:
                if fact.kind != "magento-theme":
                    continue
                attributes = dict(fact.attributes)
                area = attributes.get("area", "")
                if area != "frontend" or not fact.path.endswith("/theme.xml"):
                    continue
                identity = (area, fact.source)
                root = fact.path.removesuffix("/theme.xml")
                roots.setdefault(identity, set()).add((root, fact.path))
                theme_paths.setdefault(identity, set()).add(fact.path)
                if fact.relation == "inherits":
                    parents.setdefault(identity, set()).add(fact.target)

        def proof(
            identity: tuple[str, str],
        ) -> tuple[str, ...] | None:
            visited: set[tuple[str, str]] = set()
            paths: set[str] = set()
            current = identity
            while current not in visited:
                visited.add(current)
                if current in roots and len(roots[current]) != 1:
                    return None
                paths.update(theme_paths.get(current, ()))
                if current[1].casefold().startswith("hyva/"):
                    return tuple(sorted(paths))
                candidates = parents.get(current, ())
                if len(candidates) != 1:
                    return None
                current = (current[0], next(iter(candidates)))
            return None

        candidates_by_root: dict[
            str,
            set[tuple[str, tuple[str, ...]]],
        ] = {}
        for identity, values in sorted(roots.items()):
            if len(values) != 1:
                continue
            inheritance_paths = proof(identity)
            if inheritance_paths is None:
                continue
            root, theme_xml = next(iter(values))
            candidates_by_root.setdefault(root, set()).add((
                identity[1],
                tuple(sorted({theme_xml, *inheritance_paths})),
            ))
        return {
            root: next(iter(candidates))
            for root, candidates in sorted(candidates_by_root.items())
            if len(candidates) == 1
        }

    @staticmethod
    def _template_hyva_theme(
        template_path: str,
        themes: dict[str, tuple[str, tuple[str, ...]]],
    ) -> tuple[str, tuple[str, ...]] | None:
        matches = tuple(
            value
            for root, value in themes.items()
            if template_path.startswith(f"{root}/")
        )
        return matches[0] if len(matches) == 1 else None

    def _runtime_scope_templates(
        self,
        source_template: str,
        state_identifiers: tuple[str, ...],
        layout_source: str,
        blocks_by_source: dict[str, dict[str, dict[str, str]]],
    ) -> set[str]:
        """Select only sibling subtrees that read state written by the route."""
        if not layout_source or not state_identifiers:
            return set()
        blocks = blocks_by_source.get(layout_source, {})
        source_blocks = tuple(
            block for block in blocks.values()
            if block["template"] == source_template
        )
        if len(source_blocks) != 1:
            return set()
        source_parent = source_blocks[0]["parent"]
        if not source_parent:
            return set()

        state = set(state_identifiers)
        roots = {
            block["name"]
            for block in blocks.values()
            if (
                block["parent"] == source_parent
                and block["template"] != source_template
                and state.intersection(
                    self.templates.get(
                        block["template"],
                        TemplateRuntime(),
                    ).alpine_identifiers
                )
            )
        }
        if not roots:
            return set()

        selected_templates: set[str] = set()
        pending = deque(sorted(roots))
        visited: set[str] = set()
        while pending:
            name = pending.popleft()
            if name in visited:
                continue
            visited.add(name)
            block = blocks.get(name)
            if block is None:
                continue
            if block["template"]:
                selected_templates.add(block["template"])
            pending.extend(sorted(
                child["name"]
                for child in blocks.values()
                if child["parent"] == name
            ))
        return selected_templates

    @staticmethod
    def _webapi_routes(
        dependencies: RepositoryAnalysis,
    ) -> dict[tuple[str, str], tuple[GraphFact, ...]]:
        routes: dict[tuple[str, str], list[GraphFact]] = {}
        for packet in dependencies.packets:
            if packet.plugin_id != "magento" or packet.kind != "magento-webapi":
                continue
            for fact in packet.facts:
                if fact.kind != "magento-webapi-route":
                    continue
                method, separator, route = fact.source.partition(" ")
                if not separator:
                    continue
                key = (
                    method.strip().upper(),
                    _normalized_route(route).casefold(),
                )
                routes.setdefault(key, []).append(fact)
        return {
            key: tuple(sorted(facts))
            for key, facts in routes.items()
        }

    @staticmethod
    def _call_edges(
        dependencies: RepositoryAnalysis,
    ) -> dict[tuple[str, str], tuple[GraphFact, ...]]:
        edges: dict[tuple[str, str], list[GraphFact]] = {}
        for packet in dependencies.packets:
            if packet.plugin_id != "php":
                continue
            for fact in packet.facts:
                if fact.kind not in _CALL_FACT_KINDS:
                    continue
                attributes = dict(fact.attributes)
                caller = attributes.get("callerMethod", "")
                target_method = attributes.get("targetMethod", "")
                if (
                    not caller
                    or not target_method
                    or attributes.get("targetMethodDeclared") != "true"
                ):
                    continue
                edges.setdefault(
                    (fact.source.casefold(), caller.casefold()),
                    [],
                ).append(fact)
        return {
            key: tuple(sorted(facts))
            for key, facts in edges.items()
        }

    def _call_graph_context(
        self,
        owner: str,
        method: str,
        edges: dict[tuple[str, str], tuple[GraphFact, ...]],
    ) -> tuple[set[str], set[str]]:
        paths: set[str] = set()
        identifiers: set[str] = {method}
        pending = deque([(owner, method)])
        visited: set[tuple[str, str]] = set()
        while pending and len(visited) < self.MAX_CALL_GRAPH_STATES:
            state = pending.popleft()
            normalized = (state[0].casefold(), state[1].casefold())
            if normalized in visited:
                continue
            visited.add(normalized)
            for fact in edges.get(normalized, ()):
                attributes = dict(fact.attributes)
                target_method = attributes["targetMethod"]
                paths.add(fact.path)
                paths.update(fact.related_paths)
                identifiers.add(attributes["callerMethod"])
                identifiers.add(target_method)
                pending.append((fact.target, target_method))
        return paths, identifiers

    @staticmethod
    def _symbols(
        dependencies: RepositoryAnalysis,
    ) -> dict[str, tuple]:
        by_name: dict[str, list] = {}
        for symbol in dependencies.symbols:
            by_name.setdefault(
                symbol.qualified_name.casefold(),
                [],
            ).append(symbol)
        return {
            name: tuple(sorted(values))
            for name, values in by_name.items()
        }

    def _packets(
        self,
        dependencies: RepositoryAnalysis,
    ) -> tuple[ArchitecturePacket, ...]:
        blocks_by_source, sources_by_template = self._layout_topology(
            dependencies
        )
        webapi_routes = self._webapi_routes(dependencies)
        call_edges = self._call_edges(dependencies)
        symbols = self._symbols(dependencies)
        hyva_themes = self._hyva_theme_templates(dependencies)
        packets: list[ArchitecturePacket] = []
        provider_definitions: dict[
            str,
            list[tuple[str, AlpineProviderDefinition]],
        ] = {}
        for path, runtime in sorted(self.templates.items()):
            for definition in runtime.alpine_provider_definitions:
                provider_definitions.setdefault(
                    definition.provider_name,
                    [],
                ).append((path, definition))
        event_listeners: dict[
            str,
            list[tuple[str, AlpineEventListener]],
        ] = {}
        for path, runtime in sorted(self.templates.items()):
            for listener in runtime.alpine_event_listeners:
                event_listeners.setdefault(
                    listener.event_name,
                    [],
                ).append((path, listener))

        for template_path, runtime in sorted(self.templates.items()):
            facts: set[GraphFact] = set()
            packet_paths: set[str] = {template_path}
            requirements_by_variable = {
                requirement.assigned_variable: requirement
                for requirement in runtime.requirements
                if requirement.assigned_variable
            }
            layout_sources = tuple(sorted(
                sources_by_template.get(template_path, ())
            ))
            contexts = layout_sources or ("",)
            hyva_theme = self._template_hyva_theme(
                template_path,
                hyva_themes,
            )

            if hyva_theme is not None:
                theme_name, theme_paths = hyva_theme
                for variable in runtime.runtime_variables:
                    facts.add(GraphFact(
                        kind="hyva-template-runtime-variable",
                        source=template_path,
                        relation="receives-hyva-runtime-variable",
                        target=variable.class_name,
                        path=template_path,
                        line=variable.line,
                        attributes=tuple(sorted((
                            ("method", variable.method_name),
                            ("resolution", "hyva-theme-template-contract"),
                            ("semanticRole", "topology"),
                            ("theme", theme_name),
                            ("variable", variable.variable_name),
                        ))),
                        related_paths=theme_paths,
                    ))
                    packet_paths.update(theme_paths)

            for dispatch in runtime.alpine_event_dispatches:
                candidates = event_listeners.get(dispatch.event_name, ())
                for layout_source in contexts:
                    listener_paths = {
                        listener_path
                        for listener_path, listener in candidates
                        if (
                            listener_path == template_path
                            or (
                                listener.window
                                and layout_source
                                and layout_source in sources_by_template.get(
                                    listener_path,
                                    (),
                                )
                            )
                        )
                    }
                    if not listener_paths:
                        continue
                    crosses_templates = any(
                        path != template_path for path in listener_paths
                    )
                    related_paths = tuple(sorted({
                        *listener_paths,
                        *((layout_source,) if layout_source else ()),
                    }))
                    facts.add(GraphFact(
                        kind="hyva-alpine-event-dispatch",
                        source=template_path,
                        relation="dispatches-to-exact-alpine-listener",
                        target=dispatch.event_name,
                        path=template_path,
                        line=dispatch.line,
                        attributes=tuple(sorted((
                            ("listenerCount", str(len(listener_paths))),
                            *(
                                (("layoutSource", layout_source),)
                                if layout_source
                                else ()
                            ),
                            (
                                "resolution",
                                (
                                    "exact-layout-window-event"
                                    if crosses_templates
                                    else "exact-local-event"
                                ),
                            ),
                            ("semanticRole", "topology"),
                        ))),
                        related_paths=related_paths,
                    ))
                    packet_paths.update(related_paths)

            for use in runtime.alpine_provider_uses:
                definitions = provider_definitions.get(
                    use.provider_name,
                    (),
                )
                if len(definitions) != 1:
                    continue
                definition_path, definition = definitions[0]
                for layout_source in contexts:
                    related_paths = tuple(sorted({
                        definition_path,
                        *((layout_source,) if layout_source else ()),
                    }))
                    facts.add(GraphFact(
                        kind="hyva-alpine-component-reference",
                        source=template_path,
                        relation="uses-exact-alpine-provider",
                        target=use.provider_name,
                        path=template_path,
                        line=use.line,
                        attributes=tuple(sorted((
                            ("definitionPath", definition_path),
                            ("factoryName", definition.factory_name),
                            ("invocation", use.invocation),
                            *(
                                (("layoutSource", layout_source),)
                                if layout_source
                                else ()
                            ),
                            ("resolution", definition.resolution),
                            ("semanticRole", "topology"),
                        ))),
                        related_paths=related_paths,
                    ))
                    packet_paths.update(related_paths)

            for requirement in runtime.requirements:
                candidates = symbols.get(
                    requirement.class_name.casefold(),
                    (),
                )
                view_model_path = (
                    candidates[0].path if len(candidates) == 1 else ""
                )
                for layout_source in contexts:
                    related_paths = tuple(sorted({
                        *((layout_source,) if layout_source else ()),
                        *((view_model_path,) if view_model_path else ()),
                    }))
                    facts.add(GraphFact(
                        kind="hyva-view-model-requirement",
                        source=template_path,
                        relation="requires-view-model",
                        target=requirement.class_name,
                        path=template_path,
                        line=requirement.line,
                        attributes=tuple(sorted((
                            ("registryVariable", requirement.registry_variable),
                            *(
                                (("assignedVariable", requirement.assigned_variable),)
                                if requirement.assigned_variable
                                else ()
                            ),
                            *((("layoutSource", layout_source),) if layout_source else ()),
                            (
                                "resolution",
                                (
                                    "exact-registry-and-layout-source"
                                    if layout_source
                                    else "exact-registry-call"
                                ),
                            ),
                            ("semanticRole", "topology"),
                            ("viewModelPath", view_model_path),
                        ))),
                        related_paths=related_paths,
                    ))
                    packet_paths.update(related_paths)

            for reference in runtime.webapi_references:
                requirement = requirements_by_variable.get(
                    reference.view_model_variable
                )
                if requirement is None:
                    continue
                route_candidates = webapi_routes.get((
                    reference.http_method,
                    _normalized_route(reference.route).casefold(),
                ), ())
                if len(route_candidates) != 1:
                    continue
                route_fact = route_candidates[0]
                route_attributes = dict(route_fact.attributes)
                contract, separator, service_method = route_fact.target.rpartition(
                    "::"
                )
                implementation = route_attributes.get("implementation", "")
                if not separator or not implementation:
                    continue
                call_paths, call_identifiers = self._call_graph_context(
                    implementation,
                    service_method,
                    call_edges,
                )
                view_model_candidates = symbols.get(
                    requirement.class_name.casefold(),
                    (),
                )
                view_model_path = (
                    view_model_candidates[0].path
                    if len(view_model_candidates) == 1
                    else ""
                )
                identifiers = tuple(sorted({
                    service_method,
                    *call_identifiers,
                }))
                for layout_source in contexts:
                    scope_templates = self._runtime_scope_templates(
                        template_path,
                        reference.state_identifiers,
                        layout_source,
                        blocks_by_source,
                    )
                    related_paths = tuple(sorted({
                        *scope_templates,
                        *route_fact.related_paths,
                        *call_paths,
                        route_fact.path,
                        *((layout_source,) if layout_source else ()),
                        *((view_model_path,) if view_model_path else ()),
                    }))
                    retrieval_attributes = tuple(
                        (
                            f"retrievalIdentifier:{index:04d}",
                            identifier,
                        )
                        for index, identifier in enumerate(identifiers)
                    )
                    facts.add(GraphFact(
                        kind="hyva-template-webapi-reference",
                        source=template_path,
                        relation="references-exact-webapi-route-literal",
                        target=route_fact.source,
                        path=template_path,
                        line=reference.line,
                        attributes=tuple(sorted((
                            ("httpMethod", reference.http_method),
                            ("implementation", implementation),
                            *((("layoutSource", layout_source),) if layout_source else ()),
                            ("resolution", "exact-registry-route-literal"),
                            ("route", reference.route),
                            ("semanticRole", "topology"),
                            ("service", f"{contract}::{service_method}"),
                            ("viewModelClass", requirement.class_name),
                            ("viewModelVariable", reference.view_model_variable),
                            *retrieval_attributes,
                        ))),
                        related_paths=related_paths,
                    ))
                    packet_paths.update(related_paths)

            if facts:
                packets.append(ArchitecturePacket(
                    plugin_id=self.plugin_id,
                    kind="hyva-template-runtime",
                    key=template_path,
                    paths=tuple(sorted(packet_paths)),
                    facts=tuple(sorted(facts)),
                    attributes=(("resolution", "exact-source-topology"),),
                ))
        return tuple(sorted(packets))

    def finish(self, dependencies: RepositoryAnalysis):
        return PluginOutcome.handled(RepositoryAnalysis(
            packets=self._packets(dependencies),
            snapshots=(self._snapshot(),),
        ))
