from __future__ import annotations

from dataclasses import dataclass

from codecrow_plugins import (
    CandidateClaim, EvidenceRequest, FileArtifact, GraphFact, PluginDescriptor,
    ImportGraphSession, PluginDiagnostic, PluginOutcome, ReviewContribution,
    SyntaxContribution, TreeSitterDocument, ValidationDecision, ValidationResult,
)

from .repository import parse_import_record, resolve_import


_TYPE_NODES = {
    "class_declaration": "class", "interface_declaration": "interface",
    "enum_declaration": "enum", "record_declaration": "record",
    "annotation_type_declaration": "annotation",
}
_SNAPSHOT_KIND = "java-import-graph"


def _named(node, field: str):
    return node.child_by_field_name(field)


def _qualified(package: str, name: str) -> str:
    return f"{package}.{name}" if package else name


@dataclass(frozen=True)
class JavaPlugin:
    descriptor: PluginDescriptor

    def syntax(self):
        return PluginOutcome.handled(SyntaxContribution(
            plugin_id=self.descriptor.id,
            language_id="java",
            grammar_module="tree_sitter_java",
            grammar_factory="language",
            query_resource="python/resources/rag-chunks.scm",
            builtin_tags=True,
            rich_traversal_safe=False,
        ))

    def start_repository_analysis(self, revision: str):
        return PluginOutcome.handled(ImportGraphSession(
            plugin_id=self.descriptor.id,
            revision=revision,
            snapshot_kind=_SNAPSHOT_KIND,
            parser=parse_import_record,
            resolver=resolve_import,
        ))

    def restore_repository_analysis(self, revision: str, snapshots):
        return PluginOutcome.handled(ImportGraphSession.restore(
            plugin_id=self.descriptor.id,
            revision=revision,
            snapshot_kind=_SNAPSHOT_KIND,
            parser=parse_import_record,
            resolver=resolve_import,
            snapshots=snapshots,
        ))

    def index_file(self, artifact: FileArtifact):
        if not artifact.path.casefold().endswith(".java") or artifact.deleted:
            return PluginOutcome.abstained()
        try:
            document = TreeSitterDocument.parse(artifact.content, "tree_sitter_java", "language")
        except Exception as exception:
            return PluginOutcome.failed(PluginDiagnostic(
                "java-parse-failed", f"{type(exception).__name__}: {exception}", self.descriptor.id,
            ))

        package = ""
        facts: set[GraphFact] = set()
        for node in document.root.named_children:
            if node.type == "package_declaration":
                identifiers = document.descendants(node, "identifier", "scoped_identifier")
                if identifiers:
                    package = document.text(identifiers[0]).removeprefix("package ").rstrip(";").strip()
                    facts.add(GraphFact("java-package", artifact.path, "declares", package, artifact.path, document.line(node)))
            elif node.type == "import_declaration":
                imported = document.text(node).removeprefix("import ").rstrip(";").strip()
                imported = imported.removeprefix("static ")
                facts.add(GraphFact("java-import", package or artifact.path, "imports", imported, artifact.path, document.line(node)))

        for node in document.walk():
            kind = _TYPE_NODES.get(node.type)
            if kind:
                name = document.text(_named(node, "name"))
                if not name:
                    continue
                owner = _qualified(package, name)
                facts.add(GraphFact("java-type", artifact.path, "declares", owner, artifact.path, document.line(node), (("type", kind),)))
                for parent in document.descendants(node, "superclass", "super_interfaces"):
                    relation = "extends" if parent.type == "superclass" else "implements"
                    for target in document.descendants(parent, "type_identifier", "scoped_type_identifier"):
                        facts.add(GraphFact("java-inheritance", owner, relation, document.text(target), artifact.path, document.line(parent)))
                body = _named(node, "body")
                if body is not None:
                    for member in body.named_children:
                        if member.type in {"method_declaration", "constructor_declaration"}:
                            method = document.text(_named(member, "name"))
                            if method:
                                relation = "declares-constructor" if member.type == "constructor_declaration" else "declares-method"
                                facts.add(GraphFact("java-callable", owner, relation, method, artifact.path, document.line(member)))
            elif node.type == "object_creation_expression":
                target = document.text(_named(node, "type"))
                if target:
                    facts.add(GraphFact("java-construction", package or artifact.path, "constructs", target, artifact.path, document.line(node)))
            elif node.type == "method_invocation":
                name = document.text(_named(node, "name"))
                if name:
                    facts.add(GraphFact("java-call", package or artifact.path, "calls", name, artifact.path, document.line(node)))
        return PluginOutcome.handled(tuple(sorted(facts)))

    def review(self, paths: tuple[str, ...]):
        selected = tuple(sorted(path for path in paths if path.casefold().endswith(".java")))
        if not selected:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(ReviewContribution(
            rules=(
                "A java-pr-removed-relation is base-to-PR navigation evidence only; require changed-hunk proof of harm.",
                "For Java call and construction claims, require the exact declaration or resolved dependency context; do not infer behavior from a short type name.",
                "Resolve Java types through package/import and inheritance facts before asserting that a symbol or override is missing.",
                "Treat exact java-module-resolution, java-import-binding, and java-call-resolution facts as navigation evidence; relationship presence alone is not proof of a defect.",
            ),
            evidence_requests=tuple(EvidenceRequest("java-file", path, "exact Java declarations, imports, inheritance, and call facts") for path in selected),
        ))

    def validate(self, claim: CandidateClaim):
        owns_claim = claim.claim_kind == "java-file" or (
            not claim.claim_kind and claim.category.startswith("java-")
        )
        if not owns_claim or not claim.path.casefold().endswith(".java"):
            return PluginOutcome.abstained()
        if not any(fact.path == claim.path and fact.kind.startswith("java-") for fact in claim.evidence):
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE, "java-missing-local-evidence",
                "The candidate has no exact Java semantic evidence from its reported path.",
            ))
        return PluginOutcome.handled(ValidationResult(
            ValidationDecision.PASS, "java-evidence-present", "Exact Java semantic evidence is present.",
        ))


def create_plugin(descriptor: PluginDescriptor) -> JavaPlugin:
    return JavaPlugin(descriptor)
