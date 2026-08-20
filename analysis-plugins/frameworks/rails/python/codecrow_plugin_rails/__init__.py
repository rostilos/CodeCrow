from __future__ import annotations

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


_MAX_FACTS_PER_FILE = 160
_MAX_EVIDENCE_REQUESTS = 40
_HTTP_ROUTES = frozenset({"delete", "get", "head", "options", "patch", "post", "put"})
_RESOURCE_ROUTES = frozenset({"resource", "resources"})
_ASSOCIATIONS = {
    "belongs_to": "belongs-to",
    "has_and_belongs_to_many": "has-and-belongs-to-many",
    "has_many": "has-many",
    "has_one": "has-one",
}
_CALLBACKS = frozenset({
    "after_action", "after_commit", "after_create", "after_destroy", "after_find",
    "after_initialize", "after_rollback", "after_save", "after_touch", "after_update",
    "after_validation", "around_action", "around_save", "before_action", "before_create",
    "before_destroy", "before_save", "before_update", "before_validation", "prepend_after_action",
    "prepend_around_action", "prepend_before_action",
})
_JOB_POLICIES = frozenset({"discard_on", "retry_on"})
_FACT_KINDS = frozenset({
    "rails-association",
    "rails-callback",
    "rails-controller",
    "rails-controller-action",
    "rails-job",
    "rails-job-perform",
    "rails-job-policy",
    "rails-job-queue",
    "rails-model",
    "rails-mount",
    "rails-route",
})
_RELATION_LABELS = {
    "rails-association": ("association", "relation", "relationship"),
    "rails-callback": ("callback",),
    "rails-controller": ("controller",),
    "rails-controller-action": ("action", "controller action"),
    "rails-job": ("job",),
    "rails-job-perform": ("job", "perform method"),
    "rails-job-policy": ("job policy", "policy"),
    "rails-job-queue": ("job queue", "queue"),
    "rails-model": ("model",),
    "rails-mount": ("mount", "mounted application"),
    "rails-route": ("route",),
}
_RELATION_STATES = {
    "rails-association": ("is not associated", "is not declared"),
    "rails-callback": ("is not registered",),
    "rails-controller-action": ("is not exposed",),
    "rails-job-policy": ("is not configured",),
    "rails-job-queue": ("is not configured",),
    "rails-mount": ("is not mounted",),
    "rails-route": ("is not declared", "is not registered"),
}
_COMMON_RELATION_STATES = (
    "does not exist",
    "doesn't exist",
    "is absent",
    "is missing",
    "is not declared",
    "is not defined",
)
_ABSENCE_END = (
    r"(?=$|[.!?,;:]|\s+(?:and|because|despite|even|for|from|in|into|on|"
    r"so|therefore|when|while|with|without)\b)"
)


def _text(document: TreeSitterDocument, node) -> str:
    return document.text(node).strip() if node is not None else ""


def _field(node, name: str):
    return node.child_by_field_name(name) if node is not None else None


def _call_name(document: TreeSitterDocument, node) -> str:
    return _text(document, _field(node, "method")) if node is not None and node.type == "call" else ""


def _is_implicit_or_self_call(document: TreeSitterDocument, node) -> bool:
    if node is None or node.type != "call":
        return False
    receiver = _field(node, "receiver")
    return receiver is None or receiver.type == "self" or _text(document, receiver) == "self"


def _is_owned_routes_call(document: TreeSitterDocument, node) -> bool:
    if node is None or node.type != "call" or _call_name(document, node) != "routes":
        return False
    owner = _field(node, "receiver")
    if owner is None:
        return False
    if owner.type in {"constant", "scope_resolution"}:
        static_owner = "".join(_text(document, owner).split())
        if re.fullmatch(
            r"(?:::)?[A-Z][A-Za-z0-9_]*(?:::[A-Z][A-Za-z0-9_]*)*",
            static_owner,
        ) is None:
            return False
        return static_owner.removeprefix("::").rsplit("::", 1)[-1] in {
            "Application", "Engine",
        }
    if owner.type != "call" or _call_name(document, owner) != "application":
        return False
    arguments = _field(owner, "arguments")
    return (
        _text(document, _field(owner, "receiver")) == "Rails"
        and (arguments is None or not arguments.named_children)
    )


def _inside_routes_draw(document: TreeSitterDocument, node) -> bool:
    """Return whether ``node`` belongs to a statically owned Rails route set."""
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type == "call" and _call_name(document, ancestor) == "draw":
            receiver = _field(ancestor, "receiver")
            block = _field(ancestor, "block")
            if (
                receiver is not None
                and receiver.type == "call"
                and _is_owned_routes_call(document, receiver)
                and block is not None
                and block.start_byte <= node.start_byte
                and node.end_byte <= block.end_byte
            ):
                return True
        ancestor = ancestor.parent
    return False


def _arguments(node):
    return _field(node, "arguments")


def _literal(document: TreeSitterDocument, node) -> str:
    if node is None:
        return ""
    if node.type == "string":
        if any(child.type in {"interpolation", "subshell"} for child in node.named_children):
            return ""
        return "".join(
            document.text(child)
            for child in node.named_children
            if child.type == "string_content"
        )
    if node.type in {"simple_symbol", "hash_key_symbol", "delimited_symbol"}:
        value = _text(document, node)
        if value.startswith(":"):
            value = value[1:]
        return value.strip("'\"")
    if node.type in {
        "constant", "false", "identifier", "integer", "nil", "scope_resolution", "true",
    }:
        return "".join(_text(document, node).split())
    return ""


def _static_route_literal(document: TreeSitterDocument, node) -> str:
    if node is None or node.type not in {
        "delimited_symbol", "hash_key_symbol", "simple_symbol", "string",
    }:
        return ""
    return _literal(document, node)


def _static_route_literal_list(
    document: TreeSitterDocument,
    node,
) -> tuple[str, ...]:
    if node is None:
        return ()
    if node.type == "array":
        values = tuple(
            _static_route_literal(document, child)
            for child in node.named_children
        )
        return values if all(values) else ()
    value = _static_route_literal(document, node)
    return (value,) if value else ()


def _literal_list(document: TreeSitterDocument, node) -> tuple[str, ...]:
    if node is None:
        return ()
    if node.type == "array":
        return tuple(
            value
            for child in node.named_children
            if (value := _literal(document, child))
        )
    value = _literal(document, node)
    return (value,) if value else ()


def _positional_arguments(document: TreeSitterDocument, call) -> tuple[object, ...]:
    arguments = _arguments(call)
    if arguments is None:
        return ()
    return tuple(child for child in arguments.named_children if child.type != "pair")


def _keyword_nodes(document: TreeSitterDocument, call) -> dict[str, object]:
    arguments = _arguments(call)
    if arguments is None:
        return {}
    values: dict[str, object] = {}
    for child in arguments.named_children:
        if child.type != "pair":
            continue
        key = _literal(document, _field(child, "key"))
        value = _field(child, "value")
        if key and value is not None:
            values[key] = value
    return values


def _first_literal(document: TreeSitterDocument, call) -> str:
    positional = _positional_arguments(document, call)
    return _literal(document, positional[0]) if positional else ""


def _first_route_literal(document: TreeSitterDocument, call) -> str:
    positional = _positional_arguments(document, call)
    return _static_route_literal(document, positional[0]) if positional else ""


def _attributes(**values: str) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(
        (key, value)
        for key, value in values.items()
        if value
    ))


def _option_attributes(
    document: TreeSitterDocument,
    call,
    names: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    keywords = _keyword_nodes(document, call)
    values = {
        name: ",".join(_literal_list(document, keywords.get(name)))
        for name in names
    }
    return _attributes(**values)


def _declaration_name(document: TreeSitterDocument, declaration) -> str:
    raw = "".join(_text(document, _field(declaration, "name")).split())
    if not raw:
        return ""
    prefixes: list[str] = []
    ancestor = declaration.parent
    while ancestor is not None:
        if ancestor.type in {"class", "module"}:
            name = "".join(_text(document, _field(ancestor, "name")).split())
            if name:
                prefixes.append(name)
        ancestor = ancestor.parent
    return "::".join((*reversed(prefixes), raw)) if prefixes else raw


def _superclass(document: TreeSitterDocument, declaration) -> str:
    value = _text(document, _field(declaration, "superclass"))
    return value.removeprefix("<").strip().replace(" ", "")


def _body(declaration):
    return _field(declaration, "body")


def _direct_calls(body) -> tuple[object, ...]:
    if body is None:
        return ()
    return tuple(child for child in body.named_children if child.type == "call")


def _join_route(*parts: str) -> str:
    components = [part.strip("/") for part in parts if part.strip("/")]
    return "/" + "/".join(components) if components else "/"


def _route_prefix(document: TreeSitterDocument, route_call) -> str | None:
    prefixes: list[str] = []
    ancestor = route_call.parent
    while ancestor is not None:
        if ancestor.type == "call":
            operation = _call_name(document, ancestor)
            if operation in _RESOURCE_ROUTES:
                # Exact member/nested-resource paths require Rails inflection,
                # `param`, and `on` semantics. A per-file extractor must not
                # turn those dynamic prefixes into a false absolute route.
                return None
            if operation == "namespace":
                if not _is_implicit_or_self_call(document, ancestor):
                    return None
                positional = _positional_arguments(document, ancestor)
                if not positional:
                    return None
                value = _static_route_literal(document, positional[0])
                if not value:
                    return None
                prefixes.append(value)
            elif operation == "scope":
                if not _is_implicit_or_self_call(document, ancestor):
                    return None
                keywords = _keyword_nodes(document, ancestor)
                positional = _positional_arguments(document, ancestor)
                path_node = positional[0] if positional else keywords.get("path")
                value = _static_route_literal(document, path_node)
                if path_node is not None and not value:
                    return None
                if value:
                    prefixes.append(value)
        ancestor = ancestor.parent
    return _join_route(*reversed(prefixes)) if prefixes else ""


def _bounded_facts(facts: set[GraphFact]) -> tuple[GraphFact, ...]:
    by_kind: dict[str, list[GraphFact]] = {}
    for fact in sorted(facts):
        by_kind.setdefault(fact.kind, []).append(fact)
    selected: list[GraphFact] = []
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


def _fact_identifiers(fact: GraphFact) -> frozenset[str]:
    values = [fact.source, fact.target, *(value for _, value in fact.attributes)]
    identifiers: set[str] = set()
    for value in values:
        normalized = value.casefold().strip()
        if not normalized:
            continue
        identifiers.add(normalized)
        separated = normalized
        for separator in ("::", "#", "/", ".", ":"):
            separated = separated.replace(separator, " ")
        identifiers.update(part.strip(" _-()[],'\"") for part in separated.split())
    return frozenset(value for value in identifiers if len(value) >= 3)


def _mentions_identifier(message: str, identifier: str) -> bool:
    if identifier.replace("_", "").isalnum():
        return re.search(
            rf"(?<![a-z0-9_]){re.escape(identifier)}(?![a-z0-9_])",
            message,
        ) is not None
    return identifier in message


def _identifier_pattern(identifier: str) -> str:
    escaped = re.escape(identifier)
    if identifier.replace("_", "").isalnum():
        return rf"(?<![a-z0-9_]){escaped}(?![a-z0-9_])"
    return escaped


def _is_absence_claim(fact: GraphFact, message: str) -> bool:
    labels = _RELATION_LABELS.get(fact.kind, ())
    label_pattern = "|".join(re.escape(label) for label in labels)
    optional_label = rf"(?:\s+(?:{label_pattern}))?" if labels else ""
    states = (*_COMMON_RELATION_STATES, *_RELATION_STATES.get(fact.kind, ()))
    state_pattern = "|".join(re.escape(state) for state in states)
    named = r"(?:named\s+)?"
    for identifier in _fact_identifiers(fact):
        identifier_pattern = _identifier_pattern(identifier)
        if re.search(
            rf"{identifier_pattern}{optional_label}\s+(?:{state_pattern}){_ABSENCE_END}",
            message,
        ):
            return True
        if labels and re.search(
            rf"(?<![a-z0-9_])(?:missing|no)\s+(?:{label_pattern})\s+"
            rf"{named}{identifier_pattern}{_ABSENCE_END}",
            message,
        ):
            return True
    return False


@dataclass(frozen=True)
class RailsPlugin:
    descriptor: PluginDescriptor

    # Rails DSL facts stay per-file. Cross-file constant resolution is not
    # snapshotted because a correct restore would require the complete Ruby
    # constant-loading and route-drawing environment.
    def index_file(self, artifact: FileArtifact):
        if artifact.deleted or not artifact.path.casefold().endswith(".rb"):
            return PluginOutcome.abstained()
        try:
            document = TreeSitterDocument.parse(
                artifact.content,
                "tree_sitter_ruby",
                "language",
            )
        except Exception as exception:
            return PluginOutcome.failed(PluginDiagnostic(
                "rails-ruby-parse-failed",
                f"{type(exception).__name__}: {exception}",
                self.descriptor.id,
                path=artifact.path,
                recoverable=True,
            ))
        if document.root.has_error:
            return PluginOutcome.failed(PluginDiagnostic(
                "rails-ruby-syntax-error",
                "Tree-sitter reported an incomplete or invalid Ruby syntax tree.",
                self.descriptor.id,
                path=artifact.path,
                recoverable=True,
            ))

        facts: set[GraphFact] = set()
        if artifact.path.casefold() == "config/routes.rb" or artifact.path.casefold().endswith("/config/routes.rb"):
            self._routes(document, artifact.path, facts)
        self._classes(document, artifact.path, facts)
        if not facts:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(_bounded_facts(facts))

    @staticmethod
    def _routes(
        document: TreeSitterDocument,
        path: str,
        facts: set[GraphFact],
    ) -> None:
        for call in document.walk():
            if call.type != "call":
                continue
            operation = _call_name(document, call)
            if operation not in {*_HTTP_ROUTES, *_RESOURCE_ROUTES, "match", "mount", "root"}:
                continue
            if not _is_implicit_or_self_call(document, call):
                continue
            if not _inside_routes_draw(document, call):
                continue
            prefix = _route_prefix(document, call)
            if prefix is None:
                continue
            keywords = _keyword_nodes(document, call)
            line = document.line(call)

            if operation in _RESOURCE_ROUTES:
                resource = _first_route_literal(document, call)
                if not resource:
                    continue
                path_node = keywords.get("path")
                route_fragment = resource
                if path_node is not None:
                    if path_node.type not in {
                        "delimited_symbol", "hash_key_symbol", "simple_symbol", "string",
                    }:
                        continue
                    route_fragment = _static_route_literal(document, path_node)
                    if not route_fragment:
                        continue
                route = _join_route(prefix, route_fragment)
                actions = ",".join(_static_route_literal_list(document, keywords.get("only")))
                excluded = ",".join(_static_route_literal_list(document, keywords.get("except")))
                controller = _static_route_literal(document, keywords.get("controller"))
                facts.add(GraphFact(
                    "rails-route",
                    path,
                    "declares",
                    f"{operation.upper()} {route}",
                    path,
                    line,
                    _attributes(actions=actions, controller=controller, excluded_actions=excluded),
                ))
                continue

            if operation == "root":
                controller_action = (
                    _first_route_literal(document, call)
                    or _static_route_literal(document, keywords.get("to"))
                )
                facts.add(GraphFact(
                    "rails-route", path, "handles", f"GET {_join_route(prefix)}", path, line,
                    _attributes(controller_action=controller_action),
                ))
                continue

            if operation == "mount":
                application = _first_literal(document, call)
                mount_path = _static_route_literal(document, keywords.get("at"))
                if application and mount_path:
                    facts.add(GraphFact(
                        "rails-mount", path, "mounts", application, path, line,
                        (("path", _join_route(prefix, mount_path)),),
                    ))
                continue

            route_fragment = _first_route_literal(document, call)
            if not route_fragment:
                continue
            route = _join_route(prefix, route_fragment)
            controller_action = _static_route_literal(document, keywords.get("to"))
            route_name = _static_route_literal(document, keywords.get("as"))
            via_node = keywords.get("via")
            via = ",".join(_static_route_literal_list(document, via_node))
            if operation == "match" and (via_node is None or not via):
                continue
            verb = operation.upper()
            if operation == "match" and via:
                verb = via.upper()
            facts.add(GraphFact(
                "rails-route", path, "handles", f"{verb} {route}", path, line,
                _attributes(controller_action=controller_action, route_name=route_name),
            ))

    @staticmethod
    def _classes(
        document: TreeSitterDocument,
        path: str,
        facts: set[GraphFact],
    ) -> None:
        for declaration in document.walk():
            if declaration.type != "class":
                continue
            owner = _declaration_name(document, declaration)
            parent = _superclass(document, declaration)
            if not owner or not parent:
                continue
            body = _body(declaration)
            is_model = parent in {"ApplicationRecord", "ActiveRecord::Base"}
            is_controller = (
                parent == "ApplicationController"
                or parent.startswith("ActionController::")
            )
            is_job = parent in {"ApplicationJob", "ActiveJob::Base"}

            if is_model:
                facts.add(GraphFact(
                    "rails-model", path, "declares", owner, path,
                    document.line(declaration), (("superclass", parent),),
                ))
            if is_controller:
                facts.add(GraphFact(
                    "rails-controller", path, "declares", owner, path,
                    document.line(declaration), (("superclass", parent),),
                ))
                RailsPlugin._controller_actions(document, body, path, owner, facts)
            if is_job:
                facts.add(GraphFact(
                    "rails-job", path, "declares", owner, path,
                    document.line(declaration), (("superclass", parent),),
                ))
                RailsPlugin._job_perform(document, body, path, owner, facts)

            if not (is_model or is_controller or is_job):
                continue
            for call in _direct_calls(body):
                if not _is_implicit_or_self_call(document, call):
                    continue
                operation = _call_name(document, call)
                if is_model and operation in _ASSOCIATIONS:
                    association = _first_literal(document, call)
                    if not association:
                        continue
                    keywords = _keyword_nodes(document, call)
                    facts.add(GraphFact(
                        "rails-association", owner, _ASSOCIATIONS[operation], association,
                        path, document.line(call),
                        _attributes(
                            class_name=_literal(document, keywords.get("class_name")),
                            dependent=_literal(document, keywords.get("dependent")),
                            foreign_key=_literal(document, keywords.get("foreign_key")),
                            inverse_of=_literal(document, keywords.get("inverse_of")),
                            optional=_literal(document, keywords.get("optional")),
                            polymorphic=_literal(document, keywords.get("polymorphic")),
                            source=_literal(document, keywords.get("source")),
                            through=_literal(document, keywords.get("through")),
                        ),
                    ))
                if operation in _CALLBACKS:
                    callback = _first_literal(document, call)
                    if callback:
                        callback_options = dict(_option_attributes(
                            document, call, ("except", "if", "on", "only", "unless"),
                        ))
                        facts.add(GraphFact(
                            "rails-callback", owner, "registers", callback,
                            path, document.line(call),
                            _attributes(macro=operation, **callback_options),
                        ))
                if is_job and operation == "queue_as":
                    queue = _first_literal(document, call)
                    if queue:
                        facts.add(GraphFact(
                            "rails-job-queue", owner, "queues-on", queue,
                            path, document.line(call),
                        ))
                if is_job and operation in _JOB_POLICIES:
                    exception = _first_literal(document, call)
                    if exception:
                        facts.add(GraphFact(
                            "rails-job-policy", owner, operation.replace("_", "-"), exception,
                            path, document.line(call),
                            _option_attributes(document, call, ("attempts", "jitter", "wait")),
                        ))

    @staticmethod
    def _controller_actions(
        document: TreeSitterDocument,
        body,
        path: str,
        owner: str,
        facts: set[GraphFact],
    ) -> None:
        if body is None:
            return
        named_visibility: dict[str, str] = {}
        for member in body.named_children:
            if member.type != "call":
                continue
            if not _is_implicit_or_self_call(document, member):
                continue
            operation = _call_name(document, member)
            if operation not in {"private", "protected", "public"}:
                continue
            for argument in _positional_arguments(document, member):
                name = _literal(document, argument)
                if name:
                    named_visibility[name] = operation
        visibility = "public"
        for member in body.named_children:
            if member.type == "identifier" and _text(document, member) in {"private", "protected", "public"}:
                visibility = _text(document, member)
                continue
            if member.type == "call" and _call_name(document, member) in {"private", "protected", "public"}:
                if not _is_implicit_or_self_call(document, member):
                    continue
                if not _positional_arguments(document, member):
                    visibility = _call_name(document, member)
                continue
            if member.type != "method":
                continue
            name = _text(document, _field(member, "name"))
            if name and named_visibility.get(name, visibility) == "public":
                facts.add(GraphFact(
                    "rails-controller-action", owner, "exposes", f"{owner}#{name}",
                    path, document.line(member),
                ))

    @staticmethod
    def _job_perform(
        document: TreeSitterDocument,
        body,
        path: str,
        owner: str,
        facts: set[GraphFact],
    ) -> None:
        if body is None:
            return
        for member in body.named_children:
            if member.type != "method" or _text(document, _field(member, "name")) != "perform":
                continue
            facts.add(GraphFact(
                "rails-job-perform", owner, "executes", f"{owner}#perform",
                path, document.line(member),
            ))

    def review(self, paths: tuple[str, ...]):
        selected = tuple(sorted(
            path for path in paths if path.casefold().endswith(".rb")
        ))[:_MAX_EVIDENCE_REQUESTS]
        if not selected:
            return PluginOutcome.abstained()
        rules = tuple(sorted((
            "Resolve Rails endpoint behavior through route DSL, controller actions, namespaces, scopes, and callbacks before judging reachability.",
            "Treat model associations, callbacks, and Active Job declarations as topology context; their presence alone is not defect proof.",
        )))
        return PluginOutcome.handled(ReviewContribution(
            rules=rules,
            evidence_requests=tuple(EvidenceRequest(
                "rails-topology",
                path,
                "exact Rails route, controller, model-association, callback, and job facts",
            ) for path in selected),
        ))

    def validate(self, claim: CandidateClaim):
        requested_kind = claim.claim_kind or claim.category
        if not requested_kind.startswith("rails-"):
            return PluginOutcome.abstained()
        if not claim.path.casefold().endswith(".rb"):
            return PluginOutcome.abstained()

        if requested_kind == "rails-topology":
            expected_kinds = _FACT_KINDS
        elif requested_kind in _FACT_KINDS:
            expected_kinds = frozenset({requested_kind})
        else:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "rails-unknown-fact-kind",
                "The Rails claim kind is not owned by an exact validator.",
            ))
        matching = tuple(
            fact for fact in claim.evidence
            if fact.kind in expected_kinds
            and claim.path in {fact.path, *fact.related_paths}
        )
        message = claim.message.casefold()
        relevant = tuple(
            fact for fact in matching
            if any(
                _mentions_identifier(message, identifier)
                for identifier in _fact_identifiers(fact)
            )
        )
        if any(_is_absence_claim(fact, message) for fact in relevant):
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.REJECT,
                "rails-absence-contradicted",
                "The candidate claims Rails topology is absent, but an exact matching framework fact exists.",
            ))
        if relevant:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "rails-topology-not-defect-proof",
                "The cited Rails relationship exists, but structural presence alone does not prove defective behavior.",
            ))
        if matching:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "rails-cited-identifier-mismatch",
                "Rails topology facts exist for the path, but their identifiers do not match the candidate message.",
            ))
        return PluginOutcome.handled(ValidationResult(
            ValidationDecision.INSUFFICIENT_EVIDENCE,
            "rails-evidence-unavailable",
            "No exact matching Rails topology evidence was supplied for this framework claim.",
        ))


def create_plugin(descriptor: PluginDescriptor) -> RailsPlugin:
    return RailsPlugin(descriptor)
