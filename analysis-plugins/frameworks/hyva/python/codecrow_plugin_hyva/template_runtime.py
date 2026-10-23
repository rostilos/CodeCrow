from __future__ import annotations

import re
from dataclasses import dataclass


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
