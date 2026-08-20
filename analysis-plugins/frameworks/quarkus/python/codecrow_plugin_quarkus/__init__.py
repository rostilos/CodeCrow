from __future__ import annotations

import json
import re
from dataclasses import dataclass

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


_TYPE_NODES = {
    "class_declaration",
    "enum_declaration",
    "interface_declaration",
    "record_declaration",
}
_LOCAL_TYPE_NODES = _TYPE_NODES | {"annotation_type_declaration"}
_ANNOTATION_NODES = {"annotation", "marker_annotation"}
_MAX_FACTS_PER_FILE = 160
_MAX_CONFIG_KEYS = 128
_MAX_REVIEW_PATHS = 64
_PROPERTY = re.compile(
    r"^[ \t]*(?P<key>%?[A-Za-z0-9_][A-Za-z0-9_.%-]*)"
    r"[ \t]*(?:=|:)[ \t]*"
)

_CDI_SCOPES = {
    "jakarta.enterprise.context.ApplicationScoped": "ApplicationScoped",
    "jakarta.enterprise.context.ConversationScoped": "ConversationScoped",
    "jakarta.enterprise.context.Dependent": "Dependent",
    "jakarta.enterprise.context.RequestScoped": "RequestScoped",
    "jakarta.enterprise.context.SessionScoped": "SessionScoped",
    "jakarta.inject.Singleton": "Singleton",
    "javax.enterprise.context.ApplicationScoped": "ApplicationScoped",
    "javax.enterprise.context.ConversationScoped": "ConversationScoped",
    "javax.enterprise.context.Dependent": "Dependent",
    "javax.enterprise.context.RequestScoped": "RequestScoped",
    "javax.enterprise.context.SessionScoped": "SessionScoped",
    "javax.inject.Singleton": "Singleton",
}
_INJECT = frozenset({"jakarta.inject.Inject", "javax.inject.Inject"})
_JAXRS_PATH = frozenset({"jakarta.ws.rs.Path", "javax.ws.rs.Path"})
_JAXRS_METHODS = {
    "jakarta.ws.rs.DELETE": "DELETE",
    "jakarta.ws.rs.GET": "GET",
    "jakarta.ws.rs.HEAD": "HEAD",
    "jakarta.ws.rs.OPTIONS": "OPTIONS",
    "jakarta.ws.rs.PATCH": "PATCH",
    "jakarta.ws.rs.POST": "POST",
    "jakarta.ws.rs.PUT": "PUT",
    "javax.ws.rs.DELETE": "DELETE",
    "javax.ws.rs.GET": "GET",
    "javax.ws.rs.HEAD": "HEAD",
    "javax.ws.rs.OPTIONS": "OPTIONS",
    "javax.ws.rs.PATCH": "PATCH",
    "javax.ws.rs.POST": "POST",
    "javax.ws.rs.PUT": "PUT",
}
_CONFIG_PROPERTY = frozenset({
    "org.eclipse.microprofile.config.inject.ConfigProperty",
})
_SCHEDULED = frozenset({"io.quarkus.scheduler.Scheduled"})
_INCOMING = frozenset({
    "org.eclipse.microprofile.reactive.messaging.Incoming",
})
_OUTGOING = frozenset({
    "org.eclipse.microprofile.reactive.messaging.Outgoing",
})
_PANACHE_ENTITY_BASES = frozenset({
    "io.quarkus.hibernate.orm.panache.PanacheEntity",
    "io.quarkus.hibernate.orm.panache.PanacheEntityBase",
    "io.quarkus.hibernate.reactive.panache.PanacheEntity",
    "io.quarkus.hibernate.reactive.panache.PanacheEntityBase",
    "io.quarkus.mongodb.panache.PanacheMongoEntity",
    "io.quarkus.mongodb.panache.PanacheMongoEntityBase",
    "io.quarkus.mongodb.panache.reactive.ReactivePanacheMongoEntity",
    "io.quarkus.mongodb.panache.reactive.ReactivePanacheMongoEntityBase",
})
_PANACHE_REPOSITORY_BASES = frozenset({
    "io.quarkus.hibernate.orm.panache.PanacheRepository",
    "io.quarkus.hibernate.orm.panache.PanacheRepositoryBase",
    "io.quarkus.hibernate.reactive.panache.PanacheRepository",
    "io.quarkus.hibernate.reactive.panache.PanacheRepositoryBase",
    "io.quarkus.mongodb.panache.PanacheMongoRepository",
    "io.quarkus.mongodb.panache.PanacheMongoRepositoryBase",
    "io.quarkus.mongodb.panache.reactive.ReactivePanacheMongoRepository",
    "io.quarkus.mongodb.panache.reactive.ReactivePanacheMongoRepositoryBase",
})
_KNOWN_ANNOTATIONS = frozenset({
    *_CDI_SCOPES,
    *_INJECT,
    *_JAXRS_PATH,
    *_JAXRS_METHODS,
    *_CONFIG_PROPERTY,
    *_SCHEDULED,
    *_INCOMING,
    *_OUTGOING,
})
_KNOWN_PANACHE_TYPES = _PANACHE_ENTITY_BASES | _PANACHE_REPOSITORY_BASES
_FACT_KINDS = frozenset({
    "quarkus-cdi-bean",
    "quarkus-cdi-injection",
    "quarkus-config-key",
    "quarkus-config-property",
    "quarkus-jaxrs-resource",
    "quarkus-jaxrs-route",
    "quarkus-panache-entity",
    "quarkus-panache-repository",
    "quarkus-reactive-channel",
    "quarkus-scheduled-method",
})
_RELATION_LABELS = {
    "quarkus-cdi-bean": ("bean", "cdi bean", "cdi scope", "scope"),
    "quarkus-cdi-injection": ("dependency", "injection"),
    "quarkus-config-key": ("config key", "key"),
    "quarkus-config-property": ("config property", "property"),
    "quarkus-jaxrs-resource": ("resource",),
    "quarkus-jaxrs-route": ("route",),
    "quarkus-panache-entity": ("entity",),
    "quarkus-panache-repository": ("repository",),
    "quarkus-reactive-channel": ("channel",),
    "quarkus-scheduled-method": ("method", "schedule"),
}
_RELATION_STATES = {
    "quarkus-cdi-bean": ("is not a bean", "is not scoped"),
    "quarkus-cdi-injection": ("is not injected",),
    "quarkus-config-key": ("is not configured",),
    "quarkus-config-property": ("is not configured", "is not injected"),
    "quarkus-jaxrs-resource": ("is not registered",),
    "quarkus-jaxrs-route": ("is not registered",),
    "quarkus-panache-entity": ("is not an entity",),
    "quarkus-panache-repository": ("is not registered",),
    "quarkus-reactive-channel": ("is not consumed", "is not produced"),
    "quarkus-scheduled-method": ("is not scheduled",),
}
_COMMON_RELATION_STATES = (
    "does not exist",
    "doesn't exist",
    "is absent",
    "is missing",
    "is not defined",
)
_ABSENCE_END = (
    r"(?=$|[.!?,;:]|\s+(?:and|because|despite|even|for|from|in|into|on|"
    r"so|therefore|when|while|with|without)\b)"
)


def _named(node, field: str):
    return node.child_by_field_name(field)


def _is_application_properties(path: str) -> bool:
    return path.rsplit("/", 1)[-1].casefold() == "application.properties"


def _package_and_imports(document: TreeSitterDocument):
    package = ""
    imports: dict[str, str] = {}
    for node in document.root.named_children:
        if node.type == "package_declaration":
            package = (
                document.text(node)
                .removeprefix("package ")
                .rstrip(";")
                .strip()
            )
        elif node.type == "import_declaration":
            value = (
                document.text(node)
                .removeprefix("import ")
                .rstrip(";")
                .strip()
            )
            if value.startswith("static "):
                continue
            if value.endswith(".*"):
                continue
            simple_name = value.rsplit(".", 1)[-1]
            previous = imports.get(simple_name)
            imports[simple_name] = value if previous in {None, value} else ""
    return package, imports


def _local_type_names(document: TreeSitterDocument) -> frozenset[str]:
    return frozenset(
        name
        for node in document.walk()
        if node.type in _LOCAL_TYPE_NODES
        and (name := document.text(_named(node, "name")))
    )


def _resolve_known_name(
    value: str,
    imports: dict[str, str],
    local_type_names: frozenset[str],
    known: frozenset[str],
) -> str:
    normalized = value.strip()
    if normalized in known:
        return normalized
    if normalized in local_type_names:
        return ""
    imported = imports.get(normalized)
    if imported in known:
        return imported
    return ""


def _annotations(
    document: TreeSitterDocument,
    node,
    imports: dict[str, str],
    local_type_names: frozenset[str],
) -> tuple[tuple[str, object], ...]:
    modifiers = next(
        (child for child in node.named_children if child.type == "modifiers"),
        None,
    )
    if modifiers is None:
        return ()
    result = []
    for annotation in modifiers.named_children:
        if annotation.type not in _ANNOTATION_NODES:
            continue
        name = document.text(_named(annotation, "name"))
        qualified = _resolve_known_name(
            name,
            imports,
            local_type_names,
            _KNOWN_ANNOTATIONS,
        )
        if qualified:
            result.append((qualified, annotation))
    return tuple(result)


def _string_literal(document: TreeSitterDocument, node) -> str | None:
    if node is None or node.type != "string_literal":
        return None
    try:
        value = json.loads(document.text(node))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, str) else None


def _literal_argument(
    document: TreeSitterDocument,
    annotation,
    key: str = "value",
) -> str | None:
    arguments = _named(annotation, "arguments")
    if arguments is None:
        return None
    for child in arguments.named_children:
        if child.type != "element_value_pair":
            if key == "value":
                literal = _string_literal(document, child)
                if literal is not None:
                    return literal
            continue
        if document.text(_named(child, "key")) != key:
            continue
        return _string_literal(document, _named(child, "value"))
    return None


def _raw_arguments(
    document: TreeSitterDocument,
    annotation,
    accepted: frozenset[str],
) -> tuple[tuple[str, str], ...]:
    arguments = _named(annotation, "arguments")
    if arguments is None:
        return ()
    values: dict[str, str] = {}
    for child in arguments.named_children:
        if child.type != "element_value_pair":
            continue
        key = document.text(_named(child, "key"))
        if key not in accepted:
            continue
        value_node = _named(child, "value")
        literal = _string_literal(document, value_node)
        value = literal if literal is not None else " ".join(
            document.text(value_node).split()
        )
        if value and len(value) <= 256:
            values[key] = value
    return tuple(sorted(values.items()))


def _type_owner(document: TreeSitterDocument, declaration, package: str) -> str:
    names = []
    current = declaration
    while current is not None:
        if current.type in _TYPE_NODES:
            name = document.text(_named(current, "name"))
            if name:
                names.append(name)
        current = current.parent
    qualified = ".".join(reversed(names))
    return f"{package}.{qualified}" if package and qualified else qualified


def _join_route(prefix: str, suffix: str) -> str:
    parts = [part.strip("/") for part in (prefix, suffix) if part.strip("/")]
    return "/" + "/".join(parts) if parts else "/"


def _field_names(document: TreeSitterDocument, field) -> tuple[str, ...]:
    return tuple(
        name
        for child in field.named_children
        if child.type == "variable_declarator"
        and (name := document.text(_named(child, "name")))
    )


def _parameters(callable_node) -> tuple[object, ...]:
    parameters = _named(callable_node, "parameters")
    if parameters is None:
        return ()
    return tuple(
        parameter
        for parameter in parameters.named_children
        if parameter.type in {"formal_parameter", "spread_parameter"}
    )


def _declared_type(document: TreeSitterDocument, parameter) -> str:
    return document.text(_named(parameter, "type")).strip()


def _parent_type_nodes(declaration) -> tuple[object, ...]:
    result = []
    for child in declaration.named_children:
        if child.type == "superclass":
            if child.named_child_count:
                result.append(child.named_child(0))
        elif child.type in {"extends_interfaces", "super_interfaces"}:
            type_list = next(
                (candidate for candidate in child.named_children if candidate.type == "type_list"),
                None,
            )
            if type_list is not None:
                result.extend(type_list.named_children)
    return tuple(result)


def _parent_base(document: TreeSitterDocument, parent) -> str:
    if parent.type != "generic_type":
        return document.text(parent).strip()
    return document.text(parent.named_child(0)).strip() if parent.named_child_count else ""


def _first_type_argument(document: TreeSitterDocument, parent) -> str:
    if parent.type != "generic_type":
        return ""
    arguments = next(
        (child for child in parent.named_children if child.type == "type_arguments"),
        None,
    )
    if arguments is None or not arguments.named_child_count:
        return ""
    return document.text(arguments.named_child(0)).strip()


def _bounded_facts(facts: set[GraphFact]) -> tuple[GraphFact, ...]:
    by_kind: dict[str, list[GraphFact]] = {}
    for fact in sorted(facts):
        by_kind.setdefault(fact.kind, []).append(fact)
    selected = []
    offset = 0
    kinds = tuple(sorted(by_kind))
    while len(selected) < _MAX_FACTS_PER_FILE:
        added = False
        for kind in kinds:
            values = by_kind[kind]
            if offset < len(values):
                selected.append(values[offset])
                added = True
                if len(selected) == _MAX_FACTS_PER_FILE:
                    break
        if not added:
            break
        offset += 1
    return tuple(sorted(selected))


def _config_key_facts(artifact: FileArtifact) -> tuple[GraphFact, ...]:
    facts: set[GraphFact] = set()
    lines = artifact.content.splitlines()
    index = 0
    while index < len(lines) and len(facts) < _MAX_CONFIG_KEYS:
        line = lines[index]
        start_line = index + 1
        index += 1
        stripped = line.lstrip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        trailing_slashes = len(line) - len(line.rstrip("\\"))
        if trailing_slashes % 2:
            while index < len(lines):
                continuation = lines[index]
                index += 1
                trailing_slashes = len(continuation) - len(
                    continuation.rstrip("\\")
                )
                if trailing_slashes % 2 == 0:
                    break
            continue
        match = _PROPERTY.match(line)
        if match is None:
            continue
        key = match.group("key")
        attributes = ()
        if key.startswith("%") and "." in key:
            profile = key[1:].split(".", 1)[0]
            if profile:
                attributes = (("profile", profile),)
        facts.add(GraphFact(
            "quarkus-config-key",
            artifact.path,
            "defines",
            key,
            artifact.path,
            start_line,
            attributes,
        ))
    return tuple(sorted(facts))


def _identifier_variants(fact: GraphFact) -> frozenset[str]:
    values: set[str] = set()
    source = fact.source.casefold().strip()
    target = fact.target.casefold().strip()
    if source:
        values.add(source)
        local_source = source.rsplit(".", 1)[-1]
        values.add(local_source)
        if "#" in source:
            owner, member = source.rsplit("#", 1)
            values.add(member)
            values.add(f"{owner.rsplit('.', 1)[-1]}#{member}")
    if target:
        values.add(target)
        if fact.kind == "quarkus-jaxrs-route" and " " in target:
            values.add(target.split(" ", 1)[1])
        elif fact.kind not in {
            "quarkus-config-key",
            "quarkus-config-property",
            "quarkus-reactive-channel",
        }:
            values.add(target.rsplit(".", 1)[-1])
    ignored = {
        "defines",
        "delete",
        "get",
        "head",
        "options",
        "patch",
        "post",
        "put",
        "read",
        "resource",
        "route",
        "run",
        "scheduled",
        "value",
        "write",
    }
    return frozenset(
        value for value in values
        if len(value) >= 2 and value not in ignored
    )


def _fact_is_relevant(fact: GraphFact, message: str) -> bool:
    for identifier in _identifier_variants(fact):
        if identifier.replace("_", "").isalnum():
            if re.search(
                rf"(?<![a-z0-9_]){re.escape(identifier)}(?![a-z0-9_])",
                message,
            ):
                return True
        elif identifier in message:
            return True
    return False


def _is_absence_claim(fact: GraphFact, message: str) -> bool:
    labels = _RELATION_LABELS.get(fact.kind, ())
    optional_label = (
        rf"(?:\s+(?:{'|'.join(re.escape(label) for label in labels)}))?"
        if labels
        else ""
    )
    states = (*_COMMON_RELATION_STATES, *_RELATION_STATES.get(fact.kind, ()))
    state_pattern = "|".join(re.escape(state) for state in states)
    for identifier in _identifier_variants(fact):
        boundary_start = (
            r"(?<![a-z0-9_])"
            if identifier.replace("_", "").isalnum()
            else ""
        )
        boundary_end = (
            r"(?![a-z0-9_])"
            if identifier.replace("_", "").isalnum()
            else ""
        )
        if re.search(
            rf"{boundary_start}{re.escape(identifier)}{boundary_end}"
            rf"{optional_label}\s+(?:{state_pattern}){_ABSENCE_END}",
            message,
        ):
            return True
    return False


@dataclass(frozen=True)
class QuarkusPlugin:
    descriptor: PluginDescriptor

    def index_file(self, artifact: FileArtifact):
        if artifact.deleted:
            return PluginOutcome.abstained()
        if _is_application_properties(artifact.path):
            facts = _config_key_facts(artifact)
            return PluginOutcome.handled(facts) if facts else PluginOutcome.abstained()
        if not artifact.path.casefold().endswith(".java"):
            return PluginOutcome.abstained()
        try:
            document = TreeSitterDocument.parse(
                artifact.content,
                "tree_sitter_java",
                "language",
            )
        except Exception as exception:
            return PluginOutcome.failed(PluginDiagnostic(
                "quarkus-java-parse-failed",
                f"{type(exception).__name__}: {exception}",
                self.descriptor.id,
                artifact.path,
                True,
            ))
        if document.root.has_error:
            return PluginOutcome.failed(PluginDiagnostic(
                "quarkus-java-parse-incomplete",
                "tree-sitter could not produce a complete Java syntax tree",
                self.descriptor.id,
                artifact.path,
                True,
            ))

        package, imports = _package_and_imports(document)
        local_type_names = _local_type_names(document)
        facts: set[GraphFact] = set()
        for declaration in document.walk():
            if declaration.type not in _TYPE_NODES:
                continue
            owner = _type_owner(document, declaration, package)
            if not owner:
                continue
            annotations = _annotations(
                document,
                declaration,
                imports,
                local_type_names,
            )
            for qualified, annotation in annotations:
                scope = _CDI_SCOPES.get(qualified)
                if scope:
                    facts.add(GraphFact(
                        "quarkus-cdi-bean",
                        owner,
                        "scoped-as",
                        scope,
                        artifact.path,
                        document.line(annotation),
                    ))

            class_path = None
            path_annotation = next(
                (
                    annotation
                    for qualified, annotation in annotations
                    if qualified in _JAXRS_PATH
                ),
                None,
            )
            if path_annotation is not None:
                class_path = _literal_argument(document, path_annotation)
                if class_path is not None:
                    canonical_path = _join_route(class_path, "")
                    facts.add(GraphFact(
                        "quarkus-jaxrs-resource",
                        owner,
                        "serves",
                        canonical_path,
                        artifact.path,
                        document.line(path_annotation),
                    ))

            for parent in _parent_type_nodes(declaration):
                raw_base = _parent_base(document, parent)
                base = _resolve_known_name(
                    raw_base,
                    imports,
                    local_type_names,
                    _KNOWN_PANACHE_TYPES,
                )
                if base in _PANACHE_ENTITY_BASES:
                    facts.add(GraphFact(
                        "quarkus-panache-entity",
                        owner,
                        "extends",
                        base,
                        artifact.path,
                        document.line(parent),
                    ))
                elif base in _PANACHE_REPOSITORY_BASES:
                    entity = _first_type_argument(document, parent)
                    if entity:
                        facts.add(GraphFact(
                            "quarkus-panache-repository",
                            owner,
                            "manages",
                            entity,
                            artifact.path,
                            document.line(parent),
                            (("base", base),),
                        ))

            body = _named(declaration, "body")
            if body is None:
                continue
            for member in body.named_children:
                member_annotations = _annotations(
                    document,
                    member,
                    imports,
                    local_type_names,
                )
                annotation_names = {
                    qualified for qualified, _ in member_annotations
                }
                if member.type == "field_declaration":
                    field_type = document.text(_named(member, "type")).strip()
                    field_names = _field_names(document, member)
                    if annotation_names & _INJECT and field_type:
                        for field_name in field_names:
                            facts.add(GraphFact(
                                "quarkus-cdi-injection",
                                owner,
                                "depends-on",
                                field_type,
                                artifact.path,
                                document.line(member),
                                (("injection", "field"), ("member", field_name)),
                            ))
                    for qualified, annotation in member_annotations:
                        if qualified not in _CONFIG_PROPERTY:
                            continue
                        key = _literal_argument(document, annotation, "name")
                        if key is None or not key:
                            continue
                        default = _literal_argument(
                            document,
                            annotation,
                            "defaultValue",
                        )
                        attributes = (
                            (("default", default),)
                            if default is not None
                            else ()
                        )
                        for field_name in field_names:
                            facts.add(GraphFact(
                                "quarkus-config-property",
                                f"{owner}#{field_name}",
                                "reads",
                                key,
                                artifact.path,
                                document.line(annotation),
                                attributes,
                            ))
                    continue

                if member.type not in {
                    "constructor_declaration",
                    "method_declaration",
                }:
                    continue
                member_name = document.text(_named(member, "name"))
                if not member_name:
                    continue
                callable_name = f"{owner}#{member_name}"

                if annotation_names & _INJECT:
                    injection_kind = (
                        "constructor"
                        if member.type == "constructor_declaration"
                        else "method"
                    )
                    for parameter in _parameters(member):
                        dependency = _declared_type(document, parameter)
                        if dependency:
                            facts.add(GraphFact(
                                "quarkus-cdi-injection",
                                owner,
                                "depends-on",
                                dependency,
                                artifact.path,
                                document.line(parameter),
                                (
                                    ("injection", injection_kind),
                                    ("member", member_name),
                                ),
                            ))

                for parameter in _parameters(member):
                    parameter_name = document.text(_named(parameter, "name"))
                    for qualified, annotation in _annotations(
                        document,
                        parameter,
                        imports,
                        local_type_names,
                    ):
                        if qualified not in _CONFIG_PROPERTY:
                            continue
                        key = _literal_argument(document, annotation, "name")
                        if key is None or not key:
                            continue
                        default = _literal_argument(
                            document,
                            annotation,
                            "defaultValue",
                        )
                        attributes = (
                            (("default", default),)
                            if default is not None
                            else ()
                        )
                        source = (
                            f"{callable_name}:{parameter_name}"
                            if parameter_name
                            else callable_name
                        )
                        facts.add(GraphFact(
                            "quarkus-config-property",
                            source,
                            "reads",
                            key,
                            artifact.path,
                            document.line(annotation),
                            attributes,
                        ))

                if member.type != "method_declaration":
                    continue
                if class_path is not None:
                    method_path = ""
                    method_path_annotation = next(
                        (
                            annotation
                            for qualified, annotation in member_annotations
                            if qualified in _JAXRS_PATH
                        ),
                        None,
                    )
                    if method_path_annotation is not None:
                        literal_path = _literal_argument(
                            document,
                            method_path_annotation,
                        )
                        if literal_path is None:
                            method_path = None
                        else:
                            method_path = literal_path
                    if method_path is not None:
                        route = _join_route(class_path, method_path)
                        for qualified, annotation in member_annotations:
                            method = _JAXRS_METHODS.get(qualified)
                            if method:
                                facts.add(GraphFact(
                                    "quarkus-jaxrs-route",
                                    callable_name,
                                    "handles",
                                    f"{method} {route}",
                                    artifact.path,
                                    document.line(annotation),
                                    (("resource", owner),),
                                ))

                for qualified, annotation in member_annotations:
                    if qualified in _SCHEDULED:
                        arguments = _raw_arguments(
                            document,
                            annotation,
                            frozenset({
                                "concurrentExecution",
                                "cron",
                                "delay",
                                "delayUnit",
                                "delayed",
                                "every",
                                "executionMaxDelay",
                                "identity",
                                "skipExecutionIf",
                                "timeZone",
                            }),
                        )
                        argument_map = dict(arguments)
                        schedule = next(
                            (
                                f"{key}={argument_map[key]}"
                                for key in ("cron", "every", "delay")
                                if argument_map.get(key)
                            ),
                            "",
                        )
                        if schedule:
                            facts.add(GraphFact(
                                "quarkus-scheduled-method",
                                callable_name,
                                "runs-on",
                                schedule,
                                artifact.path,
                                document.line(annotation),
                                arguments,
                            ))
                    elif qualified in _INCOMING:
                        channel = _literal_argument(document, annotation)
                        if channel:
                            facts.add(GraphFact(
                                "quarkus-reactive-channel",
                                callable_name,
                                "consumes",
                                channel,
                                artifact.path,
                                document.line(annotation),
                            ))
                    elif qualified in _OUTGOING:
                        channel = _literal_argument(document, annotation)
                        if channel:
                            facts.add(GraphFact(
                                "quarkus-reactive-channel",
                                callable_name,
                                "produces",
                                channel,
                                artifact.path,
                                document.line(annotation),
                            ))

        if not facts:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(_bounded_facts(facts))

    def review(self, paths: tuple[str, ...]):
        selected = tuple(sorted(
            path
            for path in paths
            if path.casefold().endswith(".java")
            or _is_application_properties(path)
        ))[:_MAX_REVIEW_PATHS]
        if not selected:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(ReviewContribution(
            rules=(
                "Cite the exact quarkus-* relationship and identifier before asserting that a CDI bean, injection, route, configuration key, schedule, channel, entity, or repository is absent.",
                "Treat Quarkus graph relationships as topology and execution context; their presence alone is not positive proof of a semantic defect.",
            ),
            evidence_requests=tuple(EvidenceRequest(
                "quarkus-file",
                path,
                "exact Quarkus CDI, JAX-RS, configuration, scheduling, reactive messaging, and Panache facts",
            ) for path in selected),
        ))

    def validate(self, claim: CandidateClaim):
        requested_kind = claim.claim_kind or claim.category
        if requested_kind == "quarkus-file":
            expected_kinds = _FACT_KINDS
        elif requested_kind.startswith("quarkus-"):
            if requested_kind not in _FACT_KINDS:
                return PluginOutcome.handled(ValidationResult(
                    ValidationDecision.INSUFFICIENT_EVIDENCE,
                    "quarkus-unknown-fact-kind",
                    "The Quarkus claim kind is not owned by an exact validator.",
                ))
            expected_kinds = frozenset({requested_kind})
        else:
            return PluginOutcome.abstained()

        matching = tuple(
            fact
            for fact in claim.evidence
            if fact.kind in expected_kinds
            and claim.path in {fact.path, *fact.related_paths}
        )
        message = claim.message.casefold()
        relevant = tuple(
            fact for fact in matching if _fact_is_relevant(fact, message)
        )
        contradiction = next(
            (
                fact
                for fact in relevant
                if _is_absence_claim(fact, message)
            ),
            None,
        )
        if contradiction is not None:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.REJECT,
                "quarkus-absence-contradicted",
                "An exact same-path Quarkus fact contradicts the claimed missing relationship.",
            ))
        if relevant:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "quarkus-topology-not-defect-proof",
                "The exact Quarkus relationship proves topology and context only, not a semantic defect.",
            ))
        if matching:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "quarkus-cited-identifier-mismatch",
                "The fact kind and path match, but the candidate does not cite the exact relationship identifier.",
            ))
        return PluginOutcome.handled(ValidationResult(
            ValidationDecision.INSUFFICIENT_EVIDENCE,
            (
                "quarkus-cited-evidence-mismatch"
                if claim.evidence
                else "quarkus-evidence-unavailable"
            ),
            "No exact same-path Quarkus relationship evidence supports this candidate.",
        ))


def create_plugin(descriptor: PluginDescriptor) -> QuarkusPlugin:
    return QuarkusPlugin(descriptor)
