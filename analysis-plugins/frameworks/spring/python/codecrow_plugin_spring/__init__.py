from __future__ import annotations

import re
from dataclasses import dataclass

from codecrow_plugins import (
    CandidateClaim, EvidenceRequest, FileArtifact, GraphFact, PluginDescriptor,
    PluginDiagnostic, PluginOutcome, ReviewContribution, TreeSitterDocument,
    ValidationDecision, ValidationResult,
)


_TYPE_NODES = {"class_declaration", "interface_declaration", "record_declaration"}
_COMPONENTS = {
    "Component", "Configuration", "ConfigurationProperties", "Controller", "Entity",
    "Repository", "RestController", "Service",
}
_MAPPINGS = {
    "DeleteMapping": "DELETE", "GetMapping": "GET", "PatchMapping": "PATCH",
    "PostMapping": "POST", "PutMapping": "PUT", "RequestMapping": "ANY",
}
_QUOTED = re.compile(r'"((?:\\.|[^"\\])*)"')
_REQUEST_METHOD = re.compile(r"RequestMethod\.([A-Z]+)")


def _named(node, field: str):
    return node.child_by_field_name(field)


def _simple_name(text: str) -> str:
    return text.lstrip("@").split("(", 1)[0].rsplit(".", 1)[-1].strip()


def _annotations(document: TreeSitterDocument, node) -> tuple[tuple[str, str, object], ...]:
    modifiers = next((child for child in node.named_children if child.type == "modifiers"), None)
    if modifiers is None:
        return ()
    result = []
    for annotation in document.descendants(modifiers, "annotation", "marker_annotation"):
        text = document.text(annotation)
        result.append((_simple_name(text), text, annotation))
    return tuple(result)


def _annotation_path(text: str) -> str:
    values = _QUOTED.findall(text)
    return values[0] if values else ""


def _join_route(prefix: str, path: str) -> str:
    combined = "/" + "/".join(part.strip("/") for part in (prefix, path) if part.strip("/"))
    return combined if combined != "" else "/"


def _declared_type(document: TreeSitterDocument, parameter) -> str:
    type_node = _named(parameter, "type")
    return document.text(type_node).strip() if type_node is not None else ""


@dataclass(frozen=True)
class SpringPlugin:
    descriptor: PluginDescriptor

    def index_file(self, artifact: FileArtifact):
        if artifact.deleted or not artifact.path.casefold().endswith(".java"):
            return PluginOutcome.abstained()
        try:
            document = TreeSitterDocument.parse(artifact.content, "tree_sitter_java", "language")
        except Exception as exception:
            return PluginOutcome.failed(PluginDiagnostic(
                "spring-java-parse-failed", f"{type(exception).__name__}: {exception}", self.descriptor.id,
            ))

        package = ""
        for node in document.root.named_children:
            if node.type == "package_declaration":
                package = document.text(node).removeprefix("package ").rstrip(";").strip()
                break

        facts: set[GraphFact] = set()
        for declaration in document.walk():
            if declaration.type not in _TYPE_NODES:
                continue
            name = document.text(_named(declaration, "name"))
            if not name:
                continue
            owner = f"{package}.{name}" if package else name
            annotations = _annotations(document, declaration)
            annotation_names = {item[0] for item in annotations}
            for annotation_name, annotation_text, annotation_node in annotations:
                if annotation_name in _COMPONENTS:
                    facts.add(GraphFact(
                        "spring-component", artifact.path, "declares", owner, artifact.path,
                        document.line(annotation_node), (("stereotype", annotation_name),),
                    ))

            class_prefix = next(
                (_annotation_path(text) for annotation_name, text, _ in annotations if annotation_name == "RequestMapping"),
                "",
            )
            body = _named(declaration, "body")
            if body is None:
                continue

            for parent_node in document.descendants(declaration, "super_interfaces"):
                for type_node in document.descendants(parent_node, "type_identifier", "generic_type"):
                    parent = document.text(type_node)
                    if any(repository in parent for repository in ("CrudRepository", "JpaRepository", "PagingAndSortingRepository")):
                        facts.add(GraphFact("spring-repository", owner, "extends", parent, artifact.path, document.line(parent_node)))

            for member in body.named_children:
                member_annotations = _annotations(document, member)
                member_name = document.text(_named(member, "name"))
                if member.type in {"method_declaration", "constructor_declaration"}:
                    callable_name = f"{owner}#{member_name or name}"
                    for annotation_name, annotation_text, annotation_node in member_annotations:
                        if annotation_name in _MAPPINGS:
                            method = _MAPPINGS[annotation_name]
                            if annotation_name == "RequestMapping":
                                match = _REQUEST_METHOD.search(annotation_text)
                                method = match.group(1) if match else method
                            route = _join_route(class_prefix, _annotation_path(annotation_text))
                            facts.add(GraphFact(
                                "spring-route", callable_name, "handles", f"{method} {route}", artifact.path,
                                document.line(annotation_node), (("controller", owner),),
                            ))
                        elif annotation_name == "Bean":
                            bean_name = _annotation_path(annotation_text) or member_name
                            facts.add(GraphFact("spring-bean", owner, "provides", bean_name, artifact.path, document.line(annotation_node)))

                    parameters = _named(member, "parameters")
                    if parameters is not None and (member.type == "constructor_declaration" or "Autowired" in {a[0] for a in member_annotations}):
                        for parameter in parameters.named_children:
                            dependency = _declared_type(document, parameter)
                            if dependency:
                                facts.add(GraphFact(
                                    "spring-injection", owner, "depends-on", dependency, artifact.path,
                                    document.line(parameter), (("injection", "constructor" if member.type == "constructor_declaration" else "method"),),
                                ))
                elif member.type == "field_declaration" and "Autowired" in {item[0] for item in member_annotations}:
                    dependency = document.text(_named(member, "type"))
                    if dependency:
                        facts.add(GraphFact(
                            "spring-injection", owner, "depends-on", dependency, artifact.path,
                            document.line(member), (("injection", "field"),),
                        ))

            if "Configuration" in annotation_names and not any(fact.source == owner and fact.kind == "spring-bean" for fact in facts):
                facts.add(GraphFact("spring-configuration", artifact.path, "declares", owner, artifact.path, document.line(declaration)))

        if not facts:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(tuple(sorted(facts)))

    def review(self, paths: tuple[str, ...]):
        selected = tuple(sorted(path for path in paths if path.casefold().endswith(".java")))
        if not selected:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(ReviewContribution(
            rules=(
                "Resolve Spring dependencies through constructor, bean, component, repository, and configuration facts before reporting missing wiring.",
                "Resolve Spring routes by combining controller and method mappings before judging endpoint reachability or contract changes.",
            ),
            evidence_requests=tuple(EvidenceRequest(
                "spring-component", path,
                "exact Spring component, route, dependency-injection, bean, repository, and configuration facts",
            ) for path in selected),
        ))

    def validate(self, claim: CandidateClaim):
        owns_claim = claim.claim_kind == "spring-component" or (
            not claim.claim_kind and claim.category.startswith("spring-")
        )
        if not owns_claim or not claim.path.casefold().endswith(".java"):
            return PluginOutcome.abstained()
        if not any(fact.path == claim.path and fact.kind.startswith("spring-") for fact in claim.evidence):
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE, "spring-missing-architecture-evidence",
                "The candidate has no exact Spring architecture evidence from its reported path.",
            ))
        return PluginOutcome.handled(ValidationResult(
            ValidationDecision.PASS, "spring-architecture-evidence-present",
            "Exact Spring architecture evidence is present.",
        ))


def create_plugin(descriptor: PluginDescriptor) -> SpringPlugin:
    return SpringPlugin(descriptor)
