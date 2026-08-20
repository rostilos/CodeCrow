from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from codecrow_plugins import (
    CandidateClaim,
    EvidenceRequest,
    FileArtifact,
    GraphFact,
    PluginDescriptor,
    PluginDiagnostic,
    PluginOutcome,
    ReviewContribution,
    TreeSitterDocument,
    ValidationDecision,
    ValidationResult,
)


_EXTENSIONS = (".cjs", ".cts", ".js", ".jsx", ".mjs", ".mts", ".ts", ".tsx")
_GRAMMARS = {
    ".cjs": ("tree_sitter_javascript", "language"),
    ".cts": ("tree_sitter_typescript", "language_typescript"),
    ".js": ("tree_sitter_javascript", "language"),
    ".jsx": ("tree_sitter_javascript", "language"),
    ".mjs": ("tree_sitter_javascript", "language"),
    ".mts": ("tree_sitter_typescript", "language_typescript"),
    ".ts": ("tree_sitter_typescript", "language_typescript"),
    ".tsx": ("tree_sitter_typescript", "language_tsx"),
}
_EXTENSION_PATTERN = r"(?:cjs|cts|js|jsx|mjs|mts|ts|tsx)"
_PAGES_PATH = re.compile(
    rf"^(?:src/)?pages/(?P<tail>.+)\.(?P<extension>{_EXTENSION_PATTERN})$",
    re.IGNORECASE,
)
_APP_PATH = re.compile(
    rf"^(?:src/)?app/(?P<tail>.+)\.(?P<extension>{_EXTENSION_PATTERN})$",
    re.IGNORECASE,
)
_MIDDLEWARE_PATH = re.compile(
    rf"^(?:src/)?middleware\.(?:{_EXTENSION_PATTERN})$", re.IGNORECASE
)
_HTTP_METHODS = {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}
_DATA_LOADERS = {
    "generateMetadata",
    "generateStaticParams",
    "getServerSideProps",
    "getStaticPaths",
    "getStaticProps",
}
_TOKEN_CHARACTER = re.compile(r"[A-Za-z0-9_$]")
_INTERCEPT_PREFIX = re.compile(r"^(?:\((?:\.|\.\.|\.\.\.)\))+")
_FACT_KINDS = frozenset({
    "nextjs-api-route", "nextjs-client-boundary", "nextjs-data-loader",
    "nextjs-layout", "nextjs-middleware", "nextjs-page-route",
    "nextjs-route-endpoint", "nextjs-route-handler", "nextjs-server-action",
    "nextjs-server-boundary",
})
_RELATION_LABELS = {
    "nextjs-api-route": ("api route", "route"),
    "nextjs-client-boundary": ("boundary", "client boundary"),
    "nextjs-data-loader": ("data loader", "loader"),
    "nextjs-layout": ("layout",),
    "nextjs-middleware": ("middleware",),
    "nextjs-page-route": ("page", "page route", "route"),
    "nextjs-route-endpoint": ("endpoint", "route", "route endpoint"),
    "nextjs-route-handler": ("handler", "route handler"),
    "nextjs-server-action": ("action", "server action"),
    "nextjs-server-boundary": ("boundary", "server boundary"),
}
_RELATION_ACTIONS = {
    "declares": ("does not declare", "doesn't declare"),
    "defines": ("does not define", "doesn't define"),
    "handles": ("does not handle", "doesn't handle"),
    "intercepts": ("does not intercept", "doesn't intercept"),
    "uses": ("does not use", "doesn't use", "does not load", "doesn't load"),
    "wraps": ("does not wrap", "doesn't wrap"),
}
_RELATION_STATES = {
    "nextjs-data-loader": ("is not loaded", "is not used"),
    "nextjs-layout": ("is not wrapped",),
    "nextjs-middleware": ("is not intercepted",),
    "nextjs-route-handler": ("is not handled",),
}
_COMMON_RELATION_STATES = (
    "does not exist", "doesn't exist", "is absent", "is missing",
    "is not declared", "is not defined",
)
_ABSENCE_END = r"(?=$|[.!?,;:])"


@dataclass(frozen=True)
class _RouteFile:
    kind: str
    route: str
    router: str
    role: str


def _parse(artifact: FileArtifact) -> TreeSitterDocument:
    extension = PurePosixPath(artifact.path.casefold()).suffix
    grammar_module, grammar_factory = _GRAMMARS[extension]
    return TreeSitterDocument.parse(artifact.content, grammar_module, grammar_factory)


def _literal(document: TreeSitterDocument, node) -> str:
    if node is None or node.type not in {"string", "template_string"}:
        return ""
    text = document.text(node)
    if len(text) < 2 or text[0] not in "'\"`" or text[-1] != text[0]:
        return ""
    if node.type == "template_string" and "${" in text:
        return ""
    return text[1:-1]


def _route(segments: list[str]) -> str:
    visible: list[str] = []
    for segment in segments:
        if not segment or (segment.startswith("(") and segment.endswith(")")) or segment.startswith("@"):
            continue
        segment = _INTERCEPT_PREFIX.sub("", segment)
        if segment:
            visible.append(segment)
    return "/" + "/".join(visible)


def _route_file(path: str) -> _RouteFile | None:
    pages_match = _PAGES_PATH.search(path)
    if pages_match is not None:
        parts = pages_match.group("tail").split("/")
        leaf = parts[-1]
        if leaf in {"_app", "_document", "_error", "_middleware"}:
            return None
        if leaf == "index":
            parts.pop()
        route = _route(parts)
        kind = "nextjs-api-route" if parts and parts[0] == "api" else "nextjs-page-route"
        return _RouteFile(kind, route, "pages", "api" if kind == "nextjs-api-route" else "page")

    app_match = _APP_PATH.search(path)
    if app_match is None:
        return None
    parts = app_match.group("tail").split("/")
    leaf = parts.pop()
    if any(part.startswith("_") for part in parts):
        return None
    route = _route(parts)
    if leaf == "page":
        return _RouteFile("nextjs-page-route", route, "app", "page")
    if leaf == "route":
        kind = "nextjs-api-route" if route == "/api" or route.startswith("/api/") else "nextjs-route-endpoint"
        return _RouteFile(kind, route, "app", "route")
    if leaf == "layout":
        return _RouteFile("nextjs-layout", route, "app", "layout")
    return None


_FUNCTION_TYPES = frozenset({
    "arrow_function",
    "function_declaration",
    "function_expression",
    "generator_function",
    "generator_function_declaration",
})


def _runtime_definitions(document: TreeSitterDocument) -> dict[str, object]:
    definitions: dict[str, object] = {}
    for top_level in document.root.named_children:
        declarations = top_level.named_children if top_level.type == "export_statement" else (top_level,)
        for declaration in declarations:
            if declaration.type in {
                "class_declaration", "function_declaration", "generator_function_declaration",
            }:
                name = document.text(declaration.child_by_field_name("name"))
                if name:
                    definitions[name] = declaration
            elif declaration.type in {"lexical_declaration", "variable_declaration"}:
                for variable in declaration.named_children:
                    if variable.type != "variable_declarator":
                        continue
                    name = document.text(variable.child_by_field_name("name"))
                    value = variable.child_by_field_name("value")
                    if name and value is not None:
                        definitions[name] = value
    return definitions


def _resolved_function(
    document: TreeSitterDocument,
    node,
    definitions: dict[str, object],
    seen: frozenset[str] = frozenset(),
):
    if node is None:
        return None
    if node.type in _FUNCTION_TYPES:
        return node
    if node.type not in {"identifier", "property_identifier"}:
        return None
    name = document.text(node)
    if not name or name in seen:
        return None
    return _resolved_function(document, definitions.get(name), definitions, seen | {name})


def _exported_bindings(document: TreeSitterDocument) -> tuple[dict[str, int], bool]:
    definitions = _runtime_definitions(document)
    bindings: dict[str, int] = {}
    has_default = False
    for node in document.root.named_children:
        if node.type != "export_statement":
            continue
        text = document.text(node)
        if re.match(r"export\s+type\b", text):
            continue
        is_default = bool(re.match(r"export\s+default\b", text))
        if is_default:
            candidate = node.child_by_field_name("declaration") or node.child_by_field_name("value")
            if candidate is None:
                candidate = next(iter(node.named_children), None)
            if candidate is not None and (
                candidate.type not in {
                    "abstract_class_declaration", "interface_declaration", "type_alias_declaration",
                }
                and (
                    candidate.type not in {"identifier", "property_identifier"}
                    or document.text(candidate) in definitions
                )
            ):
                has_default = True
        for child in node.named_children:
            if child.type in {"function_declaration", "generator_function_declaration"}:
                name = document.text(child.child_by_field_name("name"))
                if name:
                    bindings[name] = document.line(child)
            elif child.type in {"lexical_declaration", "variable_declaration"}:
                for declaration in child.named_children:
                    if declaration.type != "variable_declarator":
                        continue
                    name = document.text(declaration.child_by_field_name("name"))
                    if name:
                        bindings[name] = document.line(declaration)
            elif child.type == "export_clause":
                for specifier in child.named_children:
                    if specifier.type != "export_specifier":
                        continue
                    if document.text(specifier).lstrip().startswith("type "):
                        continue
                    local = document.text(specifier.child_by_field_name("name"))
                    exported = (
                        document.text(specifier.child_by_field_name("alias"))
                        or local
                    )
                    if local in definitions and exported:
                        bindings[exported] = document.line(specifier)
                        if exported == "default":
                            has_default = True
    return bindings, has_default


def _exported_function_bindings(document: TreeSitterDocument) -> dict[str, int]:
    definitions = _runtime_definitions(document)
    bindings: dict[str, int] = {}
    for node in document.root.named_children:
        if node.type != "export_statement":
            continue
        text = document.text(node)
        if re.match(r"export\s+type\b", text):
            continue
        for child in node.named_children:
            if child.type in {"function_declaration", "generator_function_declaration"}:
                name = document.text(child.child_by_field_name("name"))
                if name:
                    bindings[name] = document.line(child)
            elif child.type in {"lexical_declaration", "variable_declaration"}:
                for variable in child.named_children:
                    if variable.type != "variable_declarator":
                        continue
                    name = document.text(variable.child_by_field_name("name"))
                    if name and _resolved_function(
                        document, variable.child_by_field_name("value"), definitions,
                    ) is not None:
                        bindings[name] = document.line(variable)
            elif child.type == "export_clause":
                for specifier in child.named_children:
                    if specifier.type != "export_specifier":
                        continue
                    if document.text(specifier).lstrip().startswith("type "):
                        continue
                    local_node = specifier.child_by_field_name("name")
                    local = document.text(local_node)
                    exported = document.text(specifier.child_by_field_name("alias")) or local
                    if _resolved_function(
                        document, definitions.get(local), definitions,
                    ) is not None:
                        bindings[exported] = document.line(specifier)
    return bindings


def _module_directives(document: TreeSitterDocument) -> tuple[tuple[str, int], ...]:
    directives: list[tuple[str, int]] = []
    for node in document.root.named_children:
        if node.type == "comment":
            continue
        if node.type != "expression_statement":
            break
        named = tuple(node.named_children)
        value = _literal(document, named[0]) if named else ""
        if not value:
            break
        if value in {"use client", "use server"}:
            directives.append((value, document.line(node)))
    return tuple(directives)


def _function_name(document: TreeSitterDocument, node) -> str:
    name = document.text(node.child_by_field_name("name"))
    if name:
        return name
    current = node.parent
    while current is not None:
        if current.type in {"function_declaration", "generator_function_declaration"}:
            return document.text(current.child_by_field_name("name")) or f"inline@{document.line(current)}"
        if current.type == "variable_declarator":
            return document.text(current.child_by_field_name("name")) or f"inline@{document.line(current)}"
        current = current.parent
    return ""


def _server_actions(document: TreeSitterDocument, artifact: FileArtifact) -> set[GraphFact]:
    facts: set[GraphFact] = set()
    function_types = {
        "arrow_function",
        "function_declaration",
        "function_expression",
        "generator_function",
        "generator_function_declaration",
        "method_definition",
    }
    for node in document.walk():
        if node.type not in function_types:
            continue
        body = node.child_by_field_name("body")
        if body is None or body.type != "statement_block":
            continue
        directive = None
        for statement in body.named_children:
            if statement.type == "comment":
                continue
            named = tuple(statement.named_children)
            value = _literal(document, named[0]) if statement.type == "expression_statement" and named else ""
            if not value:
                break
            if value == "use server":
                directive = statement
        if directive is None:
            continue
        name = _function_name(document, node)
        if name:
            facts.add(GraphFact(
                "nextjs-server-action", artifact.path, "declares", name,
                artifact.path, document.line(directive), (("scope", "function"),),
            ))
    return facts


def _page_api_handlers(document: TreeSitterDocument) -> tuple[object, ...]:
    function_types = {
        "arrow_function",
        "function_declaration",
        "function_expression",
        "generator_function",
        "generator_function_declaration",
    }
    definitions: dict[str, object] = {}
    for node in document.root.named_children:
        declarations = node.named_children if node.type == "export_statement" else (node,)
        for declaration in declarations:
            if declaration.type in {"function_declaration", "generator_function_declaration"}:
                name = document.text(declaration.child_by_field_name("name"))
                if name:
                    definitions[name] = declaration
            elif declaration.type in {"lexical_declaration", "variable_declaration"}:
                for variable in declaration.named_children:
                    if variable.type != "variable_declarator":
                        continue
                    name = document.text(variable.child_by_field_name("name"))
                    value = variable.child_by_field_name("value")
                    if name and value is not None and value.type in function_types:
                        definitions[name] = value

    handlers: list[object] = []
    for node in document.root.named_children:
        if node.type == "export_statement" and re.match(r"export\s+default\b", document.text(node)):
            value = node.child_by_field_name("value")
            direct = next((child for child in node.named_children if child.type in function_types), None)
            candidate = direct or value
            if candidate is not None and candidate.type in function_types:
                handlers.append(candidate)
            elif candidate is not None:
                resolved = definitions.get(document.text(candidate))
                if resolved is not None:
                    handlers.append(resolved)
        if node.type != "expression_statement":
            continue
        assignment = next(
            (child for child in node.named_children if child.type == "assignment_expression"), None
        )
        if assignment is None:
            continue
        left = document.text(assignment.child_by_field_name("left")).replace(" ", "")
        if left not in {"exports.default", "module.exports"}:
            continue
        right = assignment.child_by_field_name("right")
        if right is not None and right.type in function_types:
            handlers.append(right)
        elif right is not None:
            resolved = definitions.get(document.text(right))
            if resolved is not None:
                handlers.append(resolved)
    return tuple(handlers)


def _page_api_methods(
    document: TreeSitterDocument,
    handlers: tuple[object, ...],
) -> dict[str, int]:
    methods: dict[str, int] = {}
    request_methods = {"req.method", "request.method"}
    nested_function_types = {
        "arrow_function",
        "function_declaration",
        "function_expression",
        "generator_function",
        "generator_function_declaration",
    }
    for handler in handlers:
        for node in document.walk(handler):
            ancestor = node.parent
            nested = False
            while ancestor is not None and ancestor != handler:
                if ancestor.type in nested_function_types:
                    nested = True
                    break
                ancestor = ancestor.parent
            if nested:
                continue
            if node.type == "binary_expression":
                operator = document.text(node.child_by_field_name("operator"))
                if operator not in {"==", "==="}:
                    continue
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                left_text = document.text(left).replace(" ", "")
                right_text = document.text(right).replace(" ", "")
                method = ""
                if left_text in request_methods:
                    method = _literal(document, right).upper()
                elif right_text in request_methods:
                    method = _literal(document, left).upper()
                if method in _HTTP_METHODS:
                    methods.setdefault(method, document.line(node))
            elif node.type == "switch_case":
                parent = node.parent
                while parent is not None and parent.type != "switch_statement" and parent != handler:
                    parent = parent.parent
                if parent is None or parent == handler:
                    continue
                switch_value = document.text(parent.child_by_field_name("value")).strip("() ").replace(" ", "")
                method = _literal(document, node.child_by_field_name("value")).upper()
                if switch_value in request_methods and method in _HTTP_METHODS:
                    methods.setdefault(method, document.line(node))
    return methods


def _boundary_facts(
    document: TreeSitterDocument,
    artifact: FileArtifact,
    route_file: _RouteFile | None,
) -> set[GraphFact]:
    facts: set[GraphFact] = set()
    directives = _module_directives(document)
    module_values = {value for value, _ in directives}
    for value, line in directives:
        boundary = "client" if value == "use client" else "server"
        facts.add(GraphFact(
            f"nextjs-{boundary}-boundary", artifact.path, "declares", boundary,
            artifact.path, line, (("scope", "module"),),
        ))
    if (
        route_file is not None
        and route_file.router == "app"
        and route_file.role in {"layout", "page"}
        and "use client" not in module_values
        and "use server" not in module_values
    ):
        facts.add(GraphFact(
            "nextjs-server-boundary", artifact.path, "declares", "server",
            artifact.path, 1, (("scope", "app-router-default"),),
        ))

    facts.update(_server_actions(document, artifact))
    return facts


def _middleware_matchers(document: TreeSitterDocument) -> tuple[str, ...]:
    exported, _ = _exported_bindings(document)
    if "config" not in exported:
        return ("*",)
    config_value = None
    for top_level in document.root.named_children:
        declarations = top_level.named_children if top_level.type == "export_statement" else (top_level,)
        for declaration in declarations:
            if declaration.type not in {"lexical_declaration", "variable_declaration"}:
                continue
            for variable in declaration.named_children:
                if (
                    variable.type == "variable_declarator"
                    and document.text(variable.child_by_field_name("name")) == "config"
                ):
                    config_value = variable.child_by_field_name("value")
                    break
    if config_value is None or config_value.type != "object":
        return ()
    value = config_value
    for pair in value.named_children:
        if pair.type != "pair":
            continue
        key = document.text(pair.child_by_field_name("key")).strip("'\"")
        if key != "matcher":
            continue
        matcher_value = pair.child_by_field_name("value")
        direct = _literal(document, matcher_value)
        if direct:
            return (direct,)
        if matcher_value is not None and matcher_value.type == "array":
            values = tuple(sorted({
                literal
                for child in matcher_value.named_children
                if (literal := _literal(document, child))
            }))
            return values
        return ()
    return ("*",)


def _framework_facts(document: TreeSitterDocument, artifact: FileArtifact) -> set[GraphFact]:
    facts: set[GraphFact] = set()
    route_file = _route_file(artifact.path)
    bindings, has_runtime_default = _exported_bindings(document)
    function_bindings = _exported_function_bindings(document)
    pages_api_handlers: tuple[object, ...] = ()
    route_methods: dict[str, int] = {}
    if route_file is not None and route_file.kind == "nextjs-api-route" and route_file.router == "pages":
        pages_api_handlers = _page_api_handlers(document)
        if not pages_api_handlers:
            route_file = None
    elif route_file is not None and route_file.role == "route":
        route_methods = {
            method: function_bindings[method]
            for method in sorted(_HTTP_METHODS & set(function_bindings))
        }
        if not route_methods:
            route_file = None
    elif route_file is not None and route_file.role in {"layout", "page"}:
        if not has_runtime_default:
            route_file = None

    if route_file is not None:
        relation = "wraps" if route_file.role == "layout" else "defines"
        facts.add(GraphFact(
            route_file.kind, artifact.path, relation, route_file.route,
            artifact.path, 1,
            tuple(sorted((("role", route_file.role), ("router", route_file.router)))),
        ))
        if route_file.kind == "nextjs-api-route" and route_file.router == "pages":
            methods = _page_api_methods(document, pages_api_handlers)
            if not methods:
                methods = {"ANY": document.line(pages_api_handlers[0])}
            for method, line in sorted(methods.items()):
                facts.add(GraphFact(
                    "nextjs-route-handler", route_file.route, "handles", f"{method} {route_file.route}",
                    artifact.path, line, (("router", "pages"),),
                ))
        elif route_file.role == "route":
            for method, line in route_methods.items():
                facts.add(GraphFact(
                    "nextjs-route-handler", route_file.route, "handles", f"{method} {route_file.route}",
                    artifact.path, line, (("router", "app"),),
                ))

        for loader in sorted(_DATA_LOADERS & set(function_bindings)):
            facts.add(GraphFact(
                "nextjs-data-loader", route_file.route, "uses", loader,
                artifact.path, function_bindings[loader], (("router", route_file.router),),
            ))

    if _MIDDLEWARE_PATH.search(artifact.path):
        default_handlers = _page_api_handlers(document)
        if "middleware" in function_bindings or default_handlers:
            for matcher in _middleware_matchers(document):
                facts.add(GraphFact(
                    "nextjs-middleware", artifact.path, "intercepts", matcher,
                    artifact.path, 1,
                ))

    facts.update(_boundary_facts(document, artifact, route_file))
    return facts


def _message_mentions(message: str, value: str) -> bool:
    normalized = value.casefold()
    variants = {normalized}
    if " " in normalized:
        variants.add(normalized.split(" ", 1)[-1])
    for separator in ("::", "/", ".", "#"):
        variants.add(normalized.rsplit(separator, 1)[-1])
    for identifier in sorted(variants, key=len, reverse=True):
        if len(identifier) < 3:
            continue
        offset = message.find(identifier)
        while offset >= 0:
            before = message[offset - 1] if offset else ""
            end = offset + len(identifier)
            after = message[end] if end < len(message) else ""
            if (
                (not before or _TOKEN_CHARACTER.fullmatch(before) is None)
                and (not after or _TOKEN_CHARACTER.fullmatch(after) is None)
            ):
                return True
            offset = message.find(identifier, offset + 1)
    return False


def _fact_identifiers(value: str) -> frozenset[str]:
    normalized = value.casefold().strip()
    if not normalized:
        return frozenset()
    identifiers = {normalized}
    if " " in normalized:
        identifiers.add(normalized.split(" ", 1)[-1])
    for separator in ("::", "/", ".", "#"):
        identifiers.add(normalized.rsplit(separator, 1)[-1])
    return frozenset(identifier for identifier in identifiers if len(identifier) >= 3)


def _identifier_pattern(identifier: str) -> str:
    prefix = r"(?<![a-z0-9_$])" if _TOKEN_CHARACTER.fullmatch(identifier[0]) else ""
    suffix = r"(?![a-z0-9_$])" if _TOKEN_CHARACTER.fullmatch(identifier[-1]) else ""
    return f"{prefix}{re.escape(identifier)}{suffix}"


def _is_absence_claim(fact: GraphFact, message: str) -> bool:
    labels = _RELATION_LABELS.get(fact.kind, ())
    label_pattern = "|".join(re.escape(label) for label in labels)
    optional_label = rf"(?:\s+(?:{label_pattern}))?" if labels else ""
    states = (*_COMMON_RELATION_STATES, *_RELATION_STATES.get(fact.kind, ()))
    state_pattern = "|".join(re.escape(state) for state in states)
    identifiers = _fact_identifiers(fact.source) | _fact_identifiers(fact.target)
    for identifier in identifiers:
        identifier_pattern = _identifier_pattern(identifier)
        if re.search(
            rf"{identifier_pattern}{optional_label}\s+(?:{state_pattern}){_ABSENCE_END}",
            message,
        ):
            return True
        if labels and re.search(
            rf"(?<![a-z0-9_$])(?:missing|no)\s+(?:{label_pattern})\s+"
            rf"(?:named\s+)?{identifier_pattern}{_ABSENCE_END}",
            message,
        ):
            return True

    actions = _RELATION_ACTIONS.get(fact.relation, ())
    action_pattern = "|".join(re.escape(action) for action in actions)
    if not action_pattern:
        return False
    for source in _fact_identifiers(fact.source):
        for target in _fact_identifiers(fact.target):
            if re.search(
                rf"{_identifier_pattern(source)}\s+(?:{action_pattern})\s+(?:the\s+)?"
                rf"{_identifier_pattern(target)}{optional_label}{_ABSENCE_END}",
                message,
            ):
                return True
    return False


@dataclass(frozen=True)
class NextJsPlugin:
    descriptor: PluginDescriptor

    def index_file(self, artifact: FileArtifact):
        if artifact.deleted or PurePosixPath(artifact.path.casefold()).suffix not in _EXTENSIONS:
            return PluginOutcome.abstained()
        try:
            document = _parse(artifact)
        except Exception as exception:
            return PluginOutcome.failed(PluginDiagnostic(
                "nextjs-script-parse-failed",
                f"{type(exception).__name__}: {exception}",
                self.descriptor.id,
                artifact.path,
                True,
            ))
        if document.root.has_error:
            return PluginOutcome.failed(PluginDiagnostic(
                "nextjs-script-syntax-error",
                "Tree-sitter reported a syntax error; exact Next.js facts were not emitted.",
                self.descriptor.id,
                artifact.path,
                True,
            ))
        facts = _framework_facts(document, artifact)
        return PluginOutcome.handled(tuple(sorted(facts))) if facts else PluginOutcome.abstained()

    def review(self, paths: tuple[str, ...]):
        selected = tuple(sorted(
            path for path in paths if PurePosixPath(path.casefold()).suffix in _EXTENSIONS
        ))
        if not selected:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(ReviewContribution(
            rules=tuple(sorted({
                "Next.js topology facts establish file-system routes, route handlers, layouts, middleware matchers, rendering boundaries, and named data loaders; require changed-source evidence before concluding that this topology is defective.",
                "Resolve a Next.js change through its Pages or App Router route, applicable layout or middleware, exported HTTP handler, rendering boundary, and statically declared data loader before reporting missing framework behavior.",
                "Use an exact nextjs-* fact kind as claimKind when a claim depends on Next.js topology, and cite the matching fact from the reported path.",
            })),
            evidence_requests=tuple(sorted(EvidenceRequest(
                "nextjs-framework",
                path,
                "exact Next.js file-system route, handler, layout, middleware, boundary, and data-loader facts",
            ) for path in selected[:80])),
        ))

    def validate(self, claim: CandidateClaim):
        requested_kind = claim.claim_kind or claim.category
        if (
            not requested_kind.startswith("nextjs-")
            or PurePosixPath(claim.path.casefold()).suffix not in _EXTENSIONS
        ):
            return PluginOutcome.abstained()
        if requested_kind == "nextjs-framework":
            expected_kinds = _FACT_KINDS
        elif requested_kind in _FACT_KINDS:
            expected_kinds = frozenset({requested_kind})
        else:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "nextjs-unknown-fact-kind",
                "The Next.js claim kind is not owned by an exact validator.",
            ))
        matching = tuple(
            fact for fact in claim.evidence
            if fact.kind in expected_kinds
            and claim.path in {fact.path, *fact.related_paths}
        )
        if not matching:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "nextjs-framework-evidence-unavailable",
                "No matching exact Next.js framework fact is cited from the reported path.",
            ))
        message = claim.message.casefold()
        relevant = tuple(
            fact for fact in matching
            if _message_mentions(message, fact.source) or _message_mentions(message, fact.target)
        )
        if any(_is_absence_claim(fact, message) for fact in relevant):
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.REJECT,
                "nextjs-absence-claim-contradicted",
                "Exact Next.js framework evidence contradicts the claimed missing relationship.",
            ))
        return PluginOutcome.handled(ValidationResult(
            ValidationDecision.INSUFFICIENT_EVIDENCE,
            "nextjs-topology-is-not-defect-proof",
            "Next.js topology establishes framework wiring, but topology alone does not prove a defect.",
        ))


def create_plugin(descriptor: PluginDescriptor) -> NextJsPlugin:
    return NextJsPlugin(descriptor)
