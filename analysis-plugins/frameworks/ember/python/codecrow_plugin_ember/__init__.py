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


_SCRIPT_EXTENSIONS = (".cjs", ".cts", ".js", ".jsx", ".mjs", ".mts", ".ts", ".tsx")
_SUPPORTED_EXTENSIONS = (*_SCRIPT_EXTENSIONS, ".hbs")
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
_PATH_ROLE = re.compile(
    r"(?:^|/)app/(?P<role>routes|controllers|components|services|models)/"
    r"(?P<name>.+)\.(?:cjs|cts|js|jsx|mjs|mts|ts|tsx)$",
    re.IGNORECASE,
)
_ROUTE_TEMPLATE = re.compile(r"(?:^|/)app/templates/(?P<name>(?!components/).+)\.hbs$", re.IGNORECASE)
_COMPONENT_TEMPLATE = re.compile(
    r"(?:^|/)app/(?:templates/)?components/(?P<name>.+)\.hbs$", re.IGNORECASE
)
_APP_ROUTER_PATH = re.compile(
    r"^app/router\.(?:cjs|cts|js|jsx|mjs|mts|ts|tsx)$", re.IGNORECASE,
)
_SERVICE_DECORATOR = re.compile(
    r"@service(?:\s*\(\s*(['\"])(?P<explicit>[^'\"]+)\1\s*\))?"
    r"\s+(?:declare\s+)?(?P<property>[A-Za-z_$][\w$]*)"
)
_SERVICE_PROPERTY = re.compile(
    r"(?P<property>[A-Za-z_$][\w$]*)\s*=\s*(?:service|inject\.service)\s*\("
    r"\s*(?:(['\"])(?P<explicit>[^'\"]+)\2\s*)?\)"
)
_DATA_DECORATOR = re.compile(
    r"@(?P<relation>belongsTo|hasMany)\s*\(\s*(['\"])(?P<target>[^'\"]+)\2[^)]*\)"
    r"\s+(?:declare\s+)?(?P<property>[A-Za-z_$][\w$]*)",
    re.DOTALL,
)
_DATA_PROPERTY = re.compile(
    r"(?P<property>[A-Za-z_$][\w$]*)\s*(?::|=)\s*"
    r"(?P<relation>belongsTo|hasMany)\s*\(\s*(['\"])(?P<target>[^'\"]+)\3",
    re.DOTALL,
)
_ANGLE_COMPONENT = re.compile(r"<(?P<name>[A-Z][A-Za-z0-9]*(?:::[A-Z][A-Za-z0-9]*)*)\b")
_CLASSIC_COMPONENT = re.compile(r"{{\s*(?P<name>[a-z][a-z0-9]*(?:[-/][a-z0-9-]+)+)\b")
_COMPONENT_HELPER = re.compile(r"{{\s*component\s+(['\"])(?P<name>[^'\"]+)\1")
_TEMPLATE_COMMENT = re.compile(r"{{!--.*?--}}|{{!.*?}}|<!--.*?-->", re.DOTALL)
_TOKEN_CHARACTER = re.compile(r"[A-Za-z0-9_$]")
_FACT_KINDS = frozenset({
    "ember-component", "ember-controller", "ember-data-model",
    "ember-data-relationship", "ember-route", "ember-route-controller",
    "ember-route-module", "ember-service", "ember-service-injection",
    "ember-template", "ember-template-association", "ember-template-component",
})
_RELATION_LABELS = {
    "ember-component": ("component",),
    "ember-controller": ("controller",),
    "ember-data-model": ("model",),
    "ember-data-relationship": ("relation", "relationship"),
    "ember-route": ("route",),
    "ember-route-controller": ("controller",),
    "ember-route-module": ("route", "route module"),
    "ember-service": ("service",),
    "ember-service-injection": ("injection", "service", "service injection"),
    "ember-template": ("template",),
    "ember-template-association": ("template", "template association"),
    "ember-template-component": ("component",),
}
_RELATION_ACTIONS = {
    "belongs-to": ("does not belong to", "doesn't belong to"),
    "declares": ("does not declare", "doesn't declare"),
    "defines": ("does not define", "doesn't define"),
    "depends-on": ("does not inject", "doesn't inject", "does not use", "doesn't use"),
    "has-many": ("does not have", "doesn't have"),
    "invokes": ("does not invoke", "doesn't invoke", "does not render", "doesn't render"),
    "renders-component": ("does not render", "doesn't render"),
    "renders-route": ("does not render", "doesn't render"),
    "uses-controller": ("does not use", "doesn't use"),
}
_RELATION_STATES = {
    "ember-data-relationship": ("is not associated", "is not declared"),
    "ember-route": ("is not declared", "is not nested", "is not registered"),
    "ember-route-controller": ("is not associated",),
    "ember-service-injection": ("is not injected",),
    "ember-template-association": ("is not associated", "is not rendered"),
    "ember-template-component": ("is not invoked", "is not rendered"),
}
_COMMON_RELATION_STATES = (
    "does not exist", "doesn't exist", "is absent", "is missing",
    "is not declared", "is not defined",
)
_ABSENCE_END = r"(?=$|[.!?,;:])"


def _line(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _parse_script(artifact: FileArtifact) -> TreeSitterDocument:
    extension = PurePosixPath(artifact.path.casefold()).suffix
    grammar_module, grammar_factory = _GRAMMARS[extension]
    return TreeSitterDocument.parse(artifact.content, grammar_module, grammar_factory)


def _call_target(document: TreeSitterDocument, node) -> str:
    return document.text(node.child_by_field_name("function")).replace(" ", "")


def _arguments(node) -> tuple[object, ...]:
    arguments = node.child_by_field_name("arguments")
    return tuple(arguments.named_children) if arguments is not None else ()


def _literal(document: TreeSitterDocument, node) -> str:
    if node is None or node.type not in {"string", "template_string"}:
        return ""
    text = document.text(node)
    if len(text) < 2 or text[0] not in "'\"`" or text[-1] != text[0]:
        return ""
    if node.type == "template_string" and "${" in text:
        return ""
    return text[1:-1]


def _route_path_option(document: TreeSitterDocument, call) -> str:
    for argument in _arguments(call)[1:]:
        if argument.type != "object":
            continue
        for pair in argument.named_children:
            if pair.type != "pair":
                continue
            key = document.text(pair.child_by_field_name("key")).strip("'\"")
            if key == "path":
                return _literal(document, pair.child_by_field_name("value"))
    return ""


def _code_matches(pattern: re.Pattern[str], document: TreeSitterDocument, content: str):
    excluded = tuple(
        (node.start_byte, node.end_byte)
        for node in document.walk()
        if node.type in {"comment", "regex", "string", "template_string"}
    )
    for match in pattern.finditer(content):
        byte_offset = len(content[:match.start()].encode("utf-8"))
        if not any(start <= byte_offset < end for start, end in excluded):
            yield match


def _without_template_comments(content: str) -> str:
    return _TEMPLATE_COMMENT.sub(
        lambda match: "".join("\n" if character == "\n" else " " for character in match.group()),
        content,
    )


def _path_role(path: str) -> tuple[str, str] | None:
    match = _PATH_ROLE.search(path)
    if match is None:
        return None
    role = match.group("role").casefold()
    name = match.group("name").replace("\\", "/")
    if role in {"routes", "controllers"}:
        name = name.replace("/", ".")
    return role, name


def _import_bindings(
    document: TreeSitterDocument,
    source: str,
) -> tuple[set[str], dict[str, str]]:
    defaults: set[str] = set()
    named: dict[str, str] = {}
    for statement in document.root.named_children:
        if statement.type != "import_statement":
            continue
        if _literal(document, statement.child_by_field_name("source")) != source:
            continue
        clause = next(
            (child for child in statement.named_children if child.type == "import_clause"),
            None,
        )
        if clause is None:
            continue
        for child in clause.named_children:
            if child.type == "identifier":
                defaults.add(document.text(child))
        for specifier in document.descendants(clause, "import_specifier"):
            imported = document.text(specifier.child_by_field_name("name"))
            local = document.text(specifier.child_by_field_name("alias")) or imported
            if imported and local:
                named[local] = imported
    return defaults, named


def _binding_names(document: TreeSitterDocument, node) -> set[str]:
    if node is None:
        return set()
    if node.type in {"identifier", "property_identifier", "shorthand_property_identifier_pattern"}:
        return {document.text(node)}
    names: set[str] = set()
    for child in node.named_children:
        names.update(_binding_names(document, child))
    return names


def _import_is_visible(document: TreeSitterDocument, use, name: str) -> bool:
    ancestor = use.parent
    while ancestor is not None and ancestor != document.root:
        if ancestor.type in {
            "arrow_function", "function_declaration", "function_expression",
            "generator_function", "generator_function_declaration", "method_definition",
        }:
            parameters = ancestor.child_by_field_name("parameters")
            parameter = ancestor.child_by_field_name("parameter")
            if name in _binding_names(document, parameters or parameter):
                return False
        if ancestor.type == "statement_block":
            for statement in ancestor.named_children:
                declarations = statement.named_children if statement.type == "export_statement" else (statement,)
                for declaration in declarations:
                    if declaration.type in {"lexical_declaration", "variable_declaration"}:
                        for variable in declaration.named_children:
                            if (
                                variable.type == "variable_declarator"
                                and name in _binding_names(
                                    document, variable.child_by_field_name("name"),
                                )
                            ):
                                return False
                    elif declaration.type in {"class_declaration", "function_declaration"}:
                        if document.text(declaration.child_by_field_name("name")) == name:
                            return False
        if ancestor.type in {"class_declaration", "class"}:
            if document.text(ancestor.child_by_field_name("name")) == name:
                return False
        ancestor = ancestor.parent
    return True


def _module_router_owners(document: TreeSitterDocument) -> set[str]:
    router_bases, _ = _import_bindings(document, "@ember/routing/router")
    if not router_bases:
        return set()

    default_exports: set[str] = set()
    for statement in document.root.named_children:
        if statement.type != "export_statement":
            continue
        text = document.text(statement)
        if re.match(r"export\s+default\b", text):
            value = statement.child_by_field_name("value")
            if value is not None and value.type in {"identifier", "type_identifier"}:
                default_exports.add(document.text(value))
            for child in statement.named_children:
                if child.type in {"class", "class_declaration"}:
                    name = document.text(child.child_by_field_name("name"))
                    if name:
                        default_exports.add(name)
        for clause in (child for child in statement.named_children if child.type == "export_clause"):
            for specifier in clause.named_children:
                if specifier.type != "export_specifier":
                    continue
                if document.text(specifier.child_by_field_name("alias")) != "default":
                    continue
                name = document.text(specifier.child_by_field_name("name"))
                if name:
                    default_exports.add(name)

    candidates: set[str] = set()
    for statement in document.root.named_children:
        declarations = statement.named_children if statement.type == "export_statement" else (statement,)
        for declaration in declarations:
            if declaration.type in {"class", "class_declaration"}:
                name = document.text(declaration.child_by_field_name("name"))
                heritage = next(
                    (child for child in declaration.named_children if child.type == "class_heritage"),
                    None,
                )
                base = (
                    document.text(heritage.named_children[0])
                    if heritage is not None and len(heritage.named_children) == 1
                    else ""
                )
                if name and base in router_bases:
                    candidates.add(name)
            elif declaration.type in {"lexical_declaration", "variable_declaration"}:
                for variable in declaration.named_children:
                    if variable.type != "variable_declarator":
                        continue
                    name = document.text(variable.child_by_field_name("name"))
                    value = variable.child_by_field_name("value")
                    target = _call_target(document, value) if value is not None and value.type == "call_expression" else ""
                    if name and any(target == f"{base}.extend" for base in router_bases):
                        candidates.add(name)
    return candidates & default_exports


def _top_level_expression_call(document: TreeSitterDocument, call) -> bool:
    return (
        call.parent is not None
        and call.parent.type == "expression_statement"
        and call.parent.parent == document.root
    )


def _router_routes(document: TreeSitterDocument, artifact: FileArtifact) -> set[GraphFact]:
    facts: set[GraphFact] = set()
    if _APP_ROUTER_PATH.fullmatch(artifact.path) is None:
        return facts
    router_owners = _module_router_owners(document)
    if not router_owners:
        return facts
    for node in document.walk():
        if node.type != "call_expression" or _call_target(document, node) != "this.route":
            continue
        arguments = _arguments(node)
        name = _literal(document, arguments[0]) if arguments else ""
        if not name:
            continue

        ancestors: list[object] = []
        map_owner = ""
        parent = node.parent
        while parent is not None:
            if parent.type == "call_expression":
                target = _call_target(document, parent)
                if target == "this.route":
                    ancestors.append(parent)
                elif target.endswith(".map"):
                    candidate = target[:-4]
                    if candidate in router_owners and _top_level_expression_call(document, parent):
                        map_owner = candidate
                    break
            parent = parent.parent
        if not map_owner:
            continue

        chain: list[tuple[str, str]] = []
        for ancestor in reversed(ancestors):
            ancestor_arguments = _arguments(ancestor)
            ancestor_name = _literal(document, ancestor_arguments[0]) if ancestor_arguments else ""
            if ancestor_name:
                option = _route_path_option(document, ancestor)
                chain.append((ancestor_name, option or ancestor_name))
        option = _route_path_option(document, node)
        chain.append((name, option or name))

        route_name = ".".join(item[0] for item in chain)
        route_path = "/" + "/".join(
            segment.strip("/") for _, segment in chain if segment.strip("/")
        )
        attributes = [("path", route_path or "/")]
        if len(chain) > 1:
            attributes.append(("parent", ".".join(item[0] for item in chain[:-1])))
        facts.add(GraphFact(
            "ember-route",
            map_owner,
            "declares",
            route_name,
            artifact.path,
            document.line(node),
            tuple(sorted(attributes)),
        ))
    return facts


def _script_facts(document: TreeSitterDocument, artifact: FileArtifact) -> set[GraphFact]:
    facts = _router_routes(document, artifact)
    path_role = _path_role(artifact.path)
    owner = artifact.path
    if path_role is not None:
        role, name = path_role
        owner = name
        kind_by_role = {
            "components": "ember-component",
            "controllers": "ember-controller",
            "models": "ember-data-model",
            "routes": "ember-route-module",
            "services": "ember-service",
        }
        facts.add(GraphFact(kind_by_role[role], artifact.path, "defines", name, artifact.path, 1))
        if role == "controllers":
            facts.add(GraphFact("ember-route-controller", name, "uses-controller", name, artifact.path, 1))
            facts.add(GraphFact(
                "ember-template-association", name, "uses-conventional-template", name,
                artifact.path, 1, (("ownerKind", "controller"),),
            ))
        elif role == "routes":
            facts.add(GraphFact(
                "ember-template-association", name, "uses-conventional-template", name,
                artifact.path, 1, (("ownerKind", "route"),),
            ))
        elif role == "components":
            facts.add(GraphFact(
                "ember-template-association", name, "uses-conventional-template", name,
                artifact.path, 1, (("ownerKind", "component"),),
            ))

    _, service_imports = _import_bindings(document, "@ember/service")
    service_bindings = {
        local for local, imported in service_imports.items()
        if imported in {"inject", "service"}
    }
    _, data_imports = _import_bindings(document, "@ember-data/model")
    data_bindings = {
        local: imported for local, imported in data_imports.items()
        if imported in {"belongsTo", "hasMany"}
    }
    field_types = {"field_definition", "public_field_definition"}
    for field in document.walk():
        if field.type not in field_types:
            continue
        property_node = field.child_by_field_name("name") or field.child_by_field_name("property")
        property_name = document.text(property_node)
        if not re.fullmatch(r"[A-Za-z_$][\w$]*", property_name):
            continue

        expressions: list[object] = []
        expressions.extend(
            decorator.named_children[0]
            for decorator in field.named_children
            if decorator.type == "decorator" and decorator.named_children
        )
        value = field.child_by_field_name("value")
        if value is not None:
            expressions.append(value)
        for expression in expressions:
            call = expression if expression.type == "call_expression" else None
            binding = (
                _call_target(document, call)
                if call is not None
                else document.text(expression).lstrip("@")
            )
            if "." in binding or not _import_is_visible(document, expression, binding):
                continue
            arguments = _arguments(call) if call is not None else ()
            explicit = _literal(document, arguments[0]) if arguments else ""
            if binding in service_bindings:
                facts.add(GraphFact(
                    "ember-service-injection", owner, "depends-on", explicit or property_name,
                    artifact.path, document.line(expression), (("property", property_name),),
                ))
            imported_relation = data_bindings.get(binding)
            if (
                imported_relation is not None
                and path_role is not None
                and path_role[0] == "models"
                and explicit
            ):
                facts.add(GraphFact(
                    "ember-data-relationship", path_role[1],
                    "belongs-to" if imported_relation == "belongsTo" else "has-many",
                    explicit, artifact.path, document.line(expression),
                    (("property", property_name),),
                ))
    return facts


def _template_facts(artifact: FileArtifact) -> set[GraphFact]:
    facts: set[GraphFact] = set()
    content = _without_template_comments(artifact.content)
    component_match = _COMPONENT_TEMPLATE.search(artifact.path)
    route_match = _ROUTE_TEMPLATE.search(artifact.path)
    if component_match is not None:
        name = component_match.group("name")
        facts.add(GraphFact("ember-template", artifact.path, "defines", name, artifact.path, 1,
                            (("templateKind", "component"),)))
        facts.add(GraphFact("ember-template-association", name, "renders-component", name, artifact.path, 1,
                            (("ownerKind", "component"),)))
    elif route_match is not None:
        name = route_match.group("name").replace("/", ".")
        facts.add(GraphFact("ember-template", artifact.path, "defines", name, artifact.path, 1,
                            (("templateKind", "route"),)))
        facts.add(GraphFact("ember-template-association", name, "renders-route", name, artifact.path, 1,
                            (("ownerKind", "route"),)))

    for pattern, syntax in ((_ANGLE_COMPONENT, "angle"), (_CLASSIC_COMPONENT, "classic"),
                            (_COMPONENT_HELPER, "component-helper")):
        for match in pattern.finditer(content):
            facts.add(GraphFact(
                "ember-template-component", artifact.path, "invokes", match.group("name"),
                artifact.path, _line(artifact.content, match.start()), (("syntax", syntax),),
            ))
    return facts


def _mentions(message: str, value: str) -> bool:
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
    for separator in ("::", "/", ".", "#"):
        identifiers.add(normalized.rsplit(separator, 1)[-1])
    return frozenset(identifier for identifier in identifiers if len(identifier) >= 3)


def _identifier_pattern(identifier: str) -> str:
    prefix = r"(?<![a-z0-9_$])" if _TOKEN_CHARACTER.fullmatch(identifier[0]) else ""
    suffix = r"(?![a-z0-9_$])" if _TOKEN_CHARACTER.fullmatch(identifier[-1]) else ""
    return f"{prefix}{re.escape(identifier)}{suffix}"


def _is_absence_claim(fact: GraphFact, message: str) -> bool:
    if fact.relation == "uses-conventional-template":
        return False
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
class EmberPlugin:
    descriptor: PluginDescriptor

    def index_file(self, artifact: FileArtifact):
        extension = PurePosixPath(artifact.path.casefold()).suffix
        if artifact.deleted or extension not in _SUPPORTED_EXTENSIONS:
            return PluginOutcome.abstained()
        if extension == ".hbs":
            facts = _template_facts(artifact)
        else:
            try:
                document = _parse_script(artifact)
            except Exception as exception:
                return PluginOutcome.failed(PluginDiagnostic(
                    "ember-script-parse-failed",
                    f"{type(exception).__name__}: {exception}",
                    self.descriptor.id,
                    artifact.path,
                    True,
                ))
            if document.root.has_error:
                return PluginOutcome.failed(PluginDiagnostic(
                    "ember-script-syntax-error",
                    "Tree-sitter reported a syntax error; exact Ember facts were not emitted.",
                    self.descriptor.id,
                    artifact.path,
                    True,
                ))
            facts = _script_facts(document, artifact)
        return PluginOutcome.handled(tuple(sorted(facts))) if facts else PluginOutcome.abstained()

    def review(self, paths: tuple[str, ...]):
        selected = tuple(sorted(
            path for path in paths
            if PurePosixPath(path.casefold()).suffix in _SUPPORTED_EXTENSIONS
        ))
        if not selected:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(ReviewContribution(
            rules=tuple(sorted({
                "Ember topology facts identify exact routing, ownership, injection, data relationships, and template wiring; require changed-source evidence before concluding that the topology is defective.",
                "Resolve nested Ember routes, their conventional route/controller/template associations, and explicit service or Ember Data relationships before reporting missing framework wiring.",
                "Use an exact ember-* fact kind as claimKind when a claim depends on framework topology, and cite the matching fact from the reported path.",
            })),
            evidence_requests=tuple(sorted(EvidenceRequest(
                "ember-framework",
                path,
                "exact Ember route, controller, component, service, Ember Data, and template facts",
            ) for path in selected[:80])),
        ))

    def validate(self, claim: CandidateClaim):
        requested_kind = claim.claim_kind or claim.category
        if (
            not requested_kind.startswith("ember-")
            or PurePosixPath(claim.path.casefold()).suffix not in _SUPPORTED_EXTENSIONS
        ):
            return PluginOutcome.abstained()
        if requested_kind == "ember-framework":
            expected_kinds = _FACT_KINDS
        elif requested_kind in _FACT_KINDS:
            expected_kinds = frozenset({requested_kind})
        else:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "ember-unknown-fact-kind",
                "The Ember claim kind is not owned by an exact validator.",
            ))
        matching = tuple(
            fact for fact in claim.evidence
            if fact.kind in expected_kinds
            and claim.path in {fact.path, *fact.related_paths}
        )
        if not matching:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "ember-framework-evidence-unavailable",
                "No matching exact Ember framework fact is cited from the reported path.",
            ))
        message = claim.message.casefold()
        relevant = tuple(
            fact for fact in matching
            if _mentions(message, fact.source) or _mentions(message, fact.target)
        )
        if any(_is_absence_claim(fact, message) for fact in relevant):
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.REJECT,
                "ember-absence-claim-contradicted",
                "Exact Ember framework evidence contradicts the claimed missing relationship.",
            ))
        return PluginOutcome.handled(ValidationResult(
            ValidationDecision.INSUFFICIENT_EVIDENCE,
            "ember-topology-is-not-defect-proof",
            "Ember topology establishes framework wiring, but topology alone does not prove a defect.",
        ))


def create_plugin(descriptor: PluginDescriptor) -> EmberPlugin:
    return EmberPlugin(descriptor)
