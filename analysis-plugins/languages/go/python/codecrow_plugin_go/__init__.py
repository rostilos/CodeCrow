from __future__ import annotations

from dataclasses import dataclass

from codecrow_plugins import (
    CandidateClaim, EvidenceRequest, FileArtifact, GraphFact, PluginDescriptor,
    PluginDiagnostic, PluginOutcome, ReviewContribution, TreeSitterDocument,
    SyntaxContribution, ValidationDecision, ValidationResult,
)


def _named(node, field: str):
    return node.child_by_field_name(field)


@dataclass(frozen=True)
class GoPlugin:
    descriptor: PluginDescriptor

    def syntax(self):
        return PluginOutcome.handled(SyntaxContribution(
            plugin_id=self.descriptor.id,
            language_id="go",
            grammar_module="tree_sitter_go",
            grammar_factory="language",
            query_resource="python/resources/rag-chunks.scm",
            builtin_tags=True,
        ))

    def index_file(self, artifact: FileArtifact):
        if not artifact.path.casefold().endswith(".go") or artifact.deleted:
            return PluginOutcome.abstained()
        try:
            document = TreeSitterDocument.parse(artifact.content, "tree_sitter_go", "language")
        except Exception as exception:
            return PluginOutcome.failed(PluginDiagnostic(
                "go-parse-failed", f"{type(exception).__name__}: {exception}", self.descriptor.id,
            ))
        package = artifact.path
        facts: set[GraphFact] = set()
        for node in document.walk():
            if node.type == "package_clause":
                name = next((document.text(child) for child in node.named_children if child.type == "package_identifier"), "")
                if name:
                    package = name
                    facts.add(GraphFact("go-package", artifact.path, "declares", name, artifact.path, document.line(node)))
            elif node.type == "import_spec":
                path = next((document.text(child).strip('"`') for child in node.named_children if "string" in child.type), "")
                if path:
                    facts.add(GraphFact("go-import", package, "imports", path, artifact.path, document.line(node)))
            elif node.type == "type_spec":
                name = document.text(_named(node, "name"))
                value = _named(node, "type")
                if not name and node.named_children:
                    name = document.text(node.named_children[0])
                if name:
                    type_kind = value.type.removesuffix("_type") if value is not None else "alias"
                    facts.add(GraphFact("go-type", package, "declares", name, artifact.path, document.line(node), (("type", type_kind),)))
            elif node.type in {"function_declaration", "method_declaration"}:
                name = document.text(_named(node, "name"))
                if not name:
                    name = next((document.text(child) for child in node.named_children if child.type in {"identifier", "field_identifier"}), "")
                owner = package
                if node.type == "method_declaration" and node.named_children:
                    receiver = node.named_children[0]
                    types = document.descendants(receiver, "type_identifier")
                    if types:
                        owner = document.text(types[-1])
                if name:
                    facts.add(GraphFact("go-callable", owner, "declares-method" if node.type == "method_declaration" else "declares-function", name, artifact.path, document.line(node)))
            elif node.type == "call_expression":
                function = _named(node, "function")
                target = document.text(function)
                if target:
                    facts.add(GraphFact("go-call", package, "calls", target, artifact.path, document.line(node)))
        return PluginOutcome.handled(tuple(sorted(facts)))

    def review(self, paths: tuple[str, ...]):
        selected = tuple(sorted(path for path in paths if path.casefold().endswith(".go")))
        if not selected:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(ReviewContribution(
            rules=(
                "Resolve Go package imports, receiver types, and interfaces before reporting an unresolved call or contract mismatch.",
                "Treat goroutine, channel, context cancellation, and error-flow claims as requiring exact control-flow evidence from the affected function.",
            ),
            evidence_requests=tuple(EvidenceRequest("go-file", path, "exact Go package, type, receiver, and call facts") for path in selected),
        ))

    def validate(self, claim: CandidateClaim):
        owns_claim = claim.claim_kind == "go-file" or (
            not claim.claim_kind and claim.category.startswith("go-")
        )
        if not owns_claim or not claim.path.casefold().endswith(".go"):
            return PluginOutcome.abstained()
        if not any(fact.path == claim.path and fact.kind.startswith("go-") for fact in claim.evidence):
            return PluginOutcome.handled(ValidationResult(ValidationDecision.INSUFFICIENT_EVIDENCE, "go-missing-local-evidence", "The candidate has no exact Go semantic evidence from its reported path."))
        return PluginOutcome.handled(ValidationResult(ValidationDecision.PASS, "go-evidence-present", "Exact Go semantic evidence is present."))


def create_plugin(descriptor: PluginDescriptor) -> GoPlugin:
    return GoPlugin(descriptor)
