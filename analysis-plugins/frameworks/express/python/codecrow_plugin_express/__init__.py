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
_HTTP_METHODS = {
    "all": "ANY",
    "delete": "DELETE",
    "get": "GET",
    "head": "HEAD",
    "options": "OPTIONS",
    "patch": "PATCH",
    "post": "POST",
    "put": "PUT",
}
_IMPORT_DEFAULT = re.compile(
    r"\bimport\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?:,\s*{[^}]*})?\s*from\s*(['\"])express\2"
)
_IMPORT_NAMED = re.compile(
    r"\bimport\s+(?:[A-Za-z_$][\w$]*\s*,\s*)?"
    r"{(?P<bindings>[^}]*)}\s*from\s*(['\"])express\2",
    re.DOTALL,
)
_IMPORT_NAMESPACE = re.compile(
    r"\bimport\s*\*\s*as\s*(?P<name>[A-Za-z_$][\w$]*)\s*from\s*(['\"])express\2"
)
_IDENTIFIER = re.compile(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$")
_TOKEN_CHARACTER = re.compile(r"[A-Za-z0-9_$]")
_FACT_KINDS = frozenset({
    "express-application", "express-error-handler", "express-middleware",
    "express-mount", "express-route", "express-route-handler", "express-router",
})
_RELATION_LABELS = {
    "express-application": ("application", "express application"),
    "express-error-handler": ("error handler",),
    "express-middleware": ("middleware",),
    "express-mount": ("mount", "router mount"),
    "express-route": ("route",),
    "express-route-handler": ("handler", "route handler"),
    "express-router": ("router",),
}
_RELATION_ACTIONS = {
    "declares": ("does not declare", "doesn't declare"),
    "handled-by": ("is not handled by",),
    "handles": ("does not handle", "doesn't handle"),
    "mounts": ("does not mount", "doesn't mount"),
    "uses": ("does not use", "doesn't use", "does not register", "doesn't register"),
    "uses-error-handler": (
        "does not use", "doesn't use", "does not register", "doesn't register",
    ),
}
_RELATION_STATES = {
    "express-error-handler": ("is not registered", "is not used"),
    "express-middleware": ("is not registered", "is not used"),
    "express-mount": ("is not mounted",),
    "express-route": ("is not handled", "is not registered"),
    "express-route-handler": ("is not handled", "is not registered"),
}
_COMMON_RELATION_STATES = (
    "does not exist", "doesn't exist", "is absent", "is missing",
    "is not declared", "is not defined",
)
_ABSENCE_END = r"(?=$|[.!?,;:])"


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


def _arguments(node) -> tuple[object, ...]:
    arguments = node.child_by_field_name("arguments")
    return tuple(arguments.named_children) if arguments is not None else ()


def _call_target(document: TreeSitterDocument, node) -> str:
    return document.text(node.child_by_field_name("function")).replace(" ", "")


def _member_call(document: TreeSitterDocument, node) -> tuple[str, str] | None:
    function = node.child_by_field_name("function")
    if function is None or function.type not in {"member_expression", "subscript_expression"}:
        return None
    if function.type == "subscript_expression":
        return None
    owner = document.text(function.child_by_field_name("object")).replace(" ", "")
    operation = document.text(function.child_by_field_name("property")).casefold()
    return (owner, operation) if owner and operation else None


def _binding_aliases(bindings: str, imported_name: str) -> set[str]:
    aliases: set[str] = set()
    for item in bindings.split(","):
        parts = re.split(r"\s+as\s+|\s*:\s*", item.strip())
        if parts and parts[0].strip() == imported_name:
            alias = parts[-1].strip()
            if re.fullmatch(r"[A-Za-z_$][\w$]*", alias):
                aliases.add(alias)
    return aliases


def _function_arity(node) -> int | None:
    if node is None or node.type not in {
        "arrow_function", "function", "function_declaration", "function_expression",
        "generator_function", "generator_function_declaration",
    }:
        return None
    parameters = node.child_by_field_name("parameters")
    if parameters is not None:
        return len(parameters.named_children)
    parameter = node.child_by_field_name("parameter")
    return 1 if parameter is not None else 0


def _handler_name(document: TreeSitterDocument, node) -> str:
    text = document.text(node).strip()
    if _IDENTIFIER.fullmatch(text):
        return text
    if node.type in {"call_expression", "new_expression"} and len(text) <= 120:
        return text
    return f"inline@{document.line(node)}"


def _route_chain(document: TreeSitterDocument, owner_node) -> tuple[str, str] | None:
    if owner_node is None or owner_node.type != "call_expression":
        return None
    member = _member_call(document, owner_node)
    if member is None:
        return None
    if member[1] == "route":
        arguments = _arguments(owner_node)
        path = _literal(document, arguments[0]) if arguments else ""
        return (member[0], path) if path else None
    if member[1] not in _HTTP_METHODS:
        return None
    function = owner_node.child_by_field_name("function")
    return _route_chain(document, function.child_by_field_name("object"))


def _declared_functions(document: TreeSitterDocument) -> dict[str, int]:
    arities: dict[str, int] = {}
    for node in document.walk():
        if node.type in {"function_declaration", "generator_function_declaration"}:
            name = document.text(node.child_by_field_name("name"))
            arity = _function_arity(node)
            if name and arity is not None:
                arities[name] = arity
        elif node.type == "variable_declarator":
            name = document.text(node.child_by_field_name("name"))
            value = node.child_by_field_name("value")
            arity = _function_arity(value)
            if name and arity is not None:
                arities[name] = arity
    return arities


def _arity(document: TreeSitterDocument, node, declared: dict[str, int]) -> int | None:
    direct = _function_arity(node)
    if direct is not None:
        return direct
    return declared.get(document.text(node).strip())


@dataclass(frozen=True)
class _LexicalBinding:
    name: str
    scope_start: int
    scope_end: int
    declaration_start: int | None


def _binding_names(document: TreeSitterDocument, node) -> set[str]:
    if node is None:
        return set()
    if node.type in {"identifier", "shorthand_property_identifier_pattern"}:
        return {document.text(node)}
    names: set[str] = set()
    for child in node.named_children:
        names.update(_binding_names(document, child))
    return names


def _binding_scope(document: TreeSitterDocument, node):
    current = node.parent
    while current is not None:
        if current.type in {"program", "statement_block"}:
            return current
        current = current.parent
    return document.root


def _scope_declarations(
    document: TreeSitterDocument,
    scope,
    name: str,
) -> set[int]:
    declarations: set[int] = set()
    for statement in scope.named_children:
        candidates = statement.named_children if statement.type == "export_statement" else (statement,)
        for candidate in candidates:
            if candidate.type in {"lexical_declaration", "variable_declaration"}:
                for variable in candidate.named_children:
                    if (
                        variable.type == "variable_declarator"
                        and name in _binding_names(document, variable.child_by_field_name("name"))
                    ):
                        declarations.add(variable.start_byte)
            elif candidate.type in {
                "class_declaration", "function_declaration", "generator_function_declaration",
            }:
                if document.text(candidate.child_by_field_name("name")) == name:
                    declarations.add(candidate.start_byte)
    return declarations


def _binding_visible(
    document: TreeSitterDocument,
    use,
    binding: _LexicalBinding,
) -> bool:
    if not (binding.scope_start <= use.start_byte < binding.scope_end):
        return False
    if binding.declaration_start is not None and use.start_byte <= binding.declaration_start:
        return False
    ancestor = use.parent
    reached_scope = False
    while ancestor is not None:
        if ancestor.type in {
            "arrow_function", "function_declaration", "function_expression",
            "generator_function", "generator_function_declaration", "method_definition",
        }:
            parameters = ancestor.child_by_field_name("parameters")
            parameter = ancestor.child_by_field_name("parameter")
            if binding.name in _binding_names(document, parameters or parameter):
                return False
        if ancestor.type in {"program", "statement_block"}:
            declarations = _scope_declarations(document, ancestor, binding.name)
            declarations.discard(binding.declaration_start)
            if declarations:
                return False
            if ancestor.start_byte == binding.scope_start and ancestor.end_byte == binding.scope_end:
                reached_scope = True
                break
        ancestor = ancestor.parent
    return reached_scope


def _visible_provider(
    document: TreeSitterDocument,
    use,
    name: str,
    bindings: dict[str, list[_LexicalBinding]],
) -> bool:
    return any(
        _binding_visible(document, use, binding)
        for binding in bindings.get(name, ())
    )


def _owner_rebound(
    document: TreeSitterDocument,
    binding: _LexicalBinding,
) -> bool:
    for node in document.walk():
        if node.start_byte <= (binding.declaration_start or -1):
            continue
        if node.type not in {"assignment_expression", "augmented_assignment_expression"}:
            continue
        left = node.child_by_field_name("left")
        if binding.name not in _binding_names(document, left):
            continue
        if _binding_visible(document, node, binding):
            return True
    return False


def _framework_declarations(
    document: TreeSitterDocument,
    artifact: FileArtifact,
) -> tuple[set[str], set[str], dict[str, _LexicalBinding], set[GraphFact]]:
    import_statements = tuple(
        document.text(node) for node in document.walk() if node.type == "import_statement"
    )
    factory_names = {
        match.group("name")
        for statement in import_statements
        for pattern in (_IMPORT_DEFAULT, _IMPORT_NAMESPACE)
        for match in pattern.finditer(statement)
    }
    router_factory_names: set[str] = set()
    for statement in import_statements:
        for match in _IMPORT_NAMED.finditer(statement):
            router_factory_names.update(_binding_aliases(match.group("bindings"), "Router"))

    factories: dict[str, list[_LexicalBinding]] = {
        name: [_LexicalBinding(name, document.root.start_byte, document.root.end_byte, None)]
        for name in factory_names
    }
    router_factories: dict[str, list[_LexicalBinding]] = {
        name: [_LexicalBinding(name, document.root.start_byte, document.root.end_byte, None)]
        for name in router_factory_names
    }

    for node in document.walk():
        if node.type != "variable_declarator":
            continue
        name_node = node.child_by_field_name("name")
        name = document.text(name_node)
        value = node.child_by_field_name("value")
        if not name or value is None or value.type != "call_expression":
            continue
        if _call_target(document, value) == "require":
            arguments = _arguments(value)
            if arguments and _literal(document, arguments[0]) == "express":
                implicit_require = _LexicalBinding(
                    "require", document.root.start_byte, document.root.end_byte, None,
                )
                if not _binding_visible(document, value, implicit_require):
                    continue
                scope = _binding_scope(document, node)
                if name_node.type == "identifier":
                    factories.setdefault(name, []).append(_LexicalBinding(
                        name, scope.start_byte, scope.end_byte, node.start_byte,
                    ))
                elif name_node.type == "object_pattern":
                    for alias in _binding_aliases(name.strip("{}"), "Router"):
                        router_factories.setdefault(alias, []).append(_LexicalBinding(
                            alias, scope.start_byte, scope.end_byte, node.start_byte,
                        ))

    candidates: list[tuple[str, str, object, _LexicalBinding]] = []
    facts: set[GraphFact] = set()
    for node in document.walk():
        if node.type != "variable_declarator":
            continue
        name = document.text(node.child_by_field_name("name"))
        value = node.child_by_field_name("value")
        if not name or value is None or value.type != "call_expression":
            continue
        target = _call_target(document, value)
        kind = ""
        provider = target
        provider_bindings = factories
        if _visible_provider(document, value, target, factories):
            kind = "application"
        elif _visible_provider(document, value, target, router_factories):
            kind = "router"
            provider_bindings = router_factories
        elif target.endswith(".Router"):
            provider = target.removesuffix(".Router")
            if _visible_provider(document, value, provider, factories):
                kind = "router"
        if not kind:
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None or name_node.type != "identifier":
            continue
        scope = _binding_scope(document, node)
        binding = _LexicalBinding(name, scope.start_byte, scope.end_byte, node.start_byte)
        candidates.append((name, kind, node, binding))

    counts: dict[str, int] = {}
    for name, _, _, _ in candidates:
        counts[name] = counts.get(name, 0) + 1
    applications: set[str] = set()
    routers: set[str] = set()
    owner_bindings: dict[str, _LexicalBinding] = {}
    for name, kind, node, binding in candidates:
        if counts[name] != 1 or _owner_rebound(document, binding):
            continue
        owner_bindings[name] = binding
        if kind == "application":
            applications.add(name)
            fact_kind = "express-application"
        else:
            routers.add(name)
            fact_kind = "express-router"
        facts.add(GraphFact(
                fact_kind, artifact.path, "declares", name,
                artifact.path, document.line(node),
            ))
    return applications, routers, owner_bindings, facts


def _route_facts(
    document: TreeSitterDocument,
    artifact: FileArtifact,
    applications: set[str],
    routers: set[str],
    owner_bindings: dict[str, _LexicalBinding],
) -> set[GraphFact]:
    facts: set[GraphFact] = set()
    declared_functions = _declared_functions(document)
    owners = applications | routers
    for node in document.walk():
        if node.type != "call_expression":
            continue
        member = _member_call(document, node)
        if member is None:
            continue
        owner, operation = member
        arguments = _arguments(node)
        path = ""
        handlers: tuple[object, ...] = ()
        actual_owner = owner
        chained = _route_chain(document, node.child_by_field_name("function").child_by_field_name("object"))
        if operation in _HTTP_METHODS and chained is not None:
            actual_owner, path = chained
            handlers = arguments
        elif operation in _HTTP_METHODS and owner in owners:
            path = _literal(document, arguments[0]) if arguments else ""
            handlers = arguments[1:] if path else ()
        owner_binding = owner_bindings.get(actual_owner)
        owner_visible = (
            owner_binding is not None and _binding_visible(document, node, owner_binding)
        )
        if operation in _HTTP_METHODS and actual_owner in owners and owner_visible and path and handlers:
            endpoint = f"{_HTTP_METHODS[operation]} {path}"
            facts.add(GraphFact(
                "express-route", actual_owner, "handles", endpoint,
                artifact.path, document.line(node),
                (("ownerKind", "application" if actual_owner in applications else "router"),),
            ))
            for position, handler in enumerate(handlers, start=1):
                handler_name = _handler_name(document, handler)
                facts.add(GraphFact(
                    "express-route-handler", endpoint, "handled-by", handler_name,
                    artifact.path, document.line(handler), (("position", str(position)),),
                ))
            continue

        owner_binding = owner_bindings.get(owner)
        if (
            operation != "use"
            or owner not in owners
            or owner_binding is None
            or not _binding_visible(document, node, owner_binding)
            or not arguments
        ):
            continue
        mount_path = _literal(document, arguments[0])
        middleware = arguments[1:] if mount_path else arguments
        mount_path = mount_path or "/"
        for position, handler in enumerate(middleware, start=1):
            handler_name = _handler_name(document, handler)
            handler_arity = _arity(document, handler, declared_functions)
            router_binding = owner_bindings.get(handler_name)
            if (
                handler_name in routers
                and router_binding is not None
                and _binding_visible(document, handler, router_binding)
            ):
                facts.add(GraphFact(
                    "express-mount", owner, "mounts", handler_name,
                    artifact.path, document.line(node),
                    tuple(sorted((("mountPath", mount_path), ("position", str(position))))),
                ))
            if handler_arity == 4:
                facts.add(GraphFact(
                    "express-error-handler", owner, "uses-error-handler", handler_name,
                    artifact.path, document.line(handler), (("mountPath", mount_path),),
                ))
            else:
                facts.add(GraphFact(
                    "express-middleware", owner, "uses", handler_name,
                    artifact.path, document.line(handler),
                    tuple(sorted((("mountPath", mount_path), ("position", str(position))))),
                ))
    return facts


def _message_mentions(message: str, value: str) -> bool:
    normalized = value.casefold()
    variants = {normalized}
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
class ExpressPlugin:
    descriptor: PluginDescriptor

    def index_file(self, artifact: FileArtifact):
        if artifact.deleted or PurePosixPath(artifact.path.casefold()).suffix not in _EXTENSIONS:
            return PluginOutcome.abstained()
        try:
            document = _parse(artifact)
        except Exception as exception:
            return PluginOutcome.failed(PluginDiagnostic(
                "express-script-parse-failed",
                f"{type(exception).__name__}: {exception}",
                self.descriptor.id,
                artifact.path,
                True,
            ))
        if document.root.has_error:
            return PluginOutcome.failed(PluginDiagnostic(
                "express-script-syntax-error",
                "Tree-sitter reported a syntax error; exact Express facts were not emitted.",
                self.descriptor.id,
                artifact.path,
                True,
            ))
        applications, routers, owner_bindings, facts = _framework_declarations(document, artifact)
        facts.update(_route_facts(
            document, artifact, applications, routers, owner_bindings,
        ))
        return PluginOutcome.handled(tuple(sorted(facts))) if facts else PluginOutcome.abstained()

    def review(self, paths: tuple[str, ...]):
        selected = tuple(sorted(
            path for path in paths if PurePosixPath(path.casefold()).suffix in _EXTENSIONS
        ))
        if not selected:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(ReviewContribution(
            rules=tuple(sorted({
                "Express topology facts establish declarations, routes, handler order, mounts, middleware, and four-argument error-handler registration; require changed-source evidence before concluding that this topology is defective.",
                "Resolve an Express endpoint through its application or router, mount path, ordered handlers, middleware, and error handlers before reporting missing or unreachable request handling.",
                "Use an exact express-* fact kind as claimKind when a claim depends on Express topology, and cite the matching fact from the reported path.",
            })),
            evidence_requests=tuple(sorted(EvidenceRequest(
                "express-framework",
                path,
                "exact Express application, router, HTTP route, mount, middleware, and error-handler facts",
            ) for path in selected[:80])),
        ))

    def validate(self, claim: CandidateClaim):
        requested_kind = claim.claim_kind or claim.category
        if (
            not requested_kind.startswith("express-")
            or PurePosixPath(claim.path.casefold()).suffix not in _EXTENSIONS
        ):
            return PluginOutcome.abstained()
        if requested_kind == "express-framework":
            expected_kinds = _FACT_KINDS
        elif requested_kind in _FACT_KINDS:
            expected_kinds = frozenset({requested_kind})
        else:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "express-unknown-fact-kind",
                "The Express claim kind is not owned by an exact validator.",
            ))
        matching = tuple(
            fact for fact in claim.evidence
            if fact.kind in expected_kinds
            and claim.path in {fact.path, *fact.related_paths}
        )
        if not matching:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "express-framework-evidence-unavailable",
                "No matching exact Express framework fact is cited from the reported path.",
            ))
        message = claim.message.casefold()
        relevant = tuple(
            fact for fact in matching
            if _message_mentions(message, fact.source) or _message_mentions(message, fact.target)
        )
        if any(_is_absence_claim(fact, message) for fact in relevant):
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.REJECT,
                "express-absence-claim-contradicted",
                "Exact Express framework evidence contradicts the claimed missing relationship.",
            ))
        return PluginOutcome.handled(ValidationResult(
            ValidationDecision.INSUFFICIENT_EVIDENCE,
            "express-topology-is-not-defect-proof",
            "Express topology establishes framework wiring, but topology alone does not prove a defect.",
        ))


def create_plugin(descriptor: PluginDescriptor) -> ExpressPlugin:
    return ExpressPlugin(descriptor)
