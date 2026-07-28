from __future__ import annotations

import re
from dataclasses import dataclass

from codecrow_plugins import (
    CandidateClaim,
    EvidenceRequest,
    FileArtifact,
    GraphFact,
    PluginDescriptor,
    PluginOutcome,
    ReviewContribution,
    SyntaxContribution,
    ValidationDecision,
    ValidationResult,
)

from .repository import PhpRepositorySession


_NAMESPACE = re.compile(r"^\s*namespace\s+([^;{]+)", re.MULTILINE)
_USE = re.compile(r"^\s*use\s+([^;]+);", re.MULTILINE)
_TYPE = re.compile(
    r"(?P<prefix>abstract\s+|final\s+)?(?P<kind>class|interface|trait|enum)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s+extends\s+(?P<extends>[A-Za-z_\\][A-Za-z0-9_\\]*))?"
    r"(?:\s+implements\s+(?P<implements>[A-Za-z0-9_\\\s,]+))?"
)
_FUNCTION = re.compile(
    r"(?:(?:public|protected|private|static|final|abstract|readonly)\s+)*"
    r"function\s+&?\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
    re.MULTILINE,
)
_TRAIT_USE = re.compile(r"^\s*use\s+([A-Za-z_\\][A-Za-z0-9_\\]*(?:\s*,\s*[A-Za-z_\\][A-Za-z0-9_\\]*)*)\s*[;{]", re.MULTILINE)
_NEW = re.compile(r"\bnew\s+([A-Za-z_\\][A-Za-z0-9_\\]*)")
_PHP_REPOSITORY_CLAIM_KINDS = {
    "php-constructor-dependency",
    "php-construction-relation",
    "php-inheritance",
    "php-instance-call-relation",
    "php-static-call-relation",
    "php-trait-use",
}


def _line(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _qualified(namespace: str | None, name: str) -> str:
    return f"{namespace}\\{name}" if namespace else name


@dataclass(frozen=True)
class PhpPlugin:
    descriptor: PluginDescriptor

    def syntax(self):
        return PluginOutcome.handled(SyntaxContribution(
            plugin_id=self.descriptor.id,
            language_id="php",
            grammar_module="tree_sitter_php",
            grammar_factory="language_php",
            query_resource="python/resources/rag-chunks.scm",
            builtin_tags=True,
        ))

    def start_repository_analysis(self, revision: str):
        return PluginOutcome.handled(PhpRepositorySession(self.descriptor.id, revision))

    def restore_repository_analysis(self, revision: str, snapshots):
        return PluginOutcome.handled(
            PhpRepositorySession.restore(self.descriptor.id, revision, snapshots)
        )

    def index_file(self, artifact: FileArtifact):
        if not artifact.path.lower().endswith((".php", ".phtml", ".inc")):
            return PluginOutcome.abstained()
        content = artifact.content
        namespace_match = _NAMESPACE.search(content)
        namespace = namespace_match.group(1).strip() if namespace_match else None
        facts: set[GraphFact] = set()

        file_source = namespace or artifact.path
        if namespace_match:
            facts.add(GraphFact("php-namespace", artifact.path, "declares", namespace, artifact.path, _line(content, namespace_match.start())))

        for match in _USE.finditer(content):
            raw = match.group(1).strip()
            if raw.lower().startswith(("function ", "const ")):
                raw = raw.split(None, 1)[1]
            imported = raw.split(" as ", 1)[0].strip()
            if "{" not in imported:
                facts.add(GraphFact("php-import", file_source, "imports", imported, artifact.path, _line(content, match.start())))

        declared_types: list[str] = []
        for match in _TYPE.finditer(content):
            declared = _qualified(namespace, match.group("name"))
            declared_types.append(declared)
            facts.add(GraphFact("php-type", artifact.path, "declares", declared, artifact.path, _line(content, match.start()), (("type", match.group("kind")),)))
            if match.group("extends"):
                facts.add(GraphFact("php-inheritance", declared, "extends", match.group("extends"), artifact.path, _line(content, match.start())))
            if match.group("implements"):
                for target in sorted(value.strip() for value in match.group("implements").split(",") if value.strip()):
                    facts.add(GraphFact("php-inheritance", declared, "implements", target, artifact.path, _line(content, match.start())))

        owner = declared_types[0] if declared_types else file_source
        for match in _TRAIT_USE.finditer(content):
            for trait in sorted(value.strip() for value in match.group(1).split(",")):
                facts.add(GraphFact("php-trait", owner, "uses-trait", trait, artifact.path, _line(content, match.start())))
        for match in _FUNCTION.finditer(content):
            facts.add(GraphFact("php-callable", owner, "declares-method", match.group("name"), artifact.path, _line(content, match.start())))
        for match in _NEW.finditer(content):
            facts.add(GraphFact("php-construction", owner, "constructs", match.group(1), artifact.path, _line(content, match.start())))

        return PluginOutcome.handled(tuple(sorted(facts)))

    def review(self, paths: tuple[str, ...]):
        php_paths = tuple(sorted(path for path in paths if path.lower().endswith((".php", ".phtml", ".inc"))))
        if not php_paths:
            return PluginOutcome.abstained()
        rules = (
            "For PHP symbol claims, require the declaration or import/namespace evidence; do not infer a missing class from a short name alone.",
            "Treat dynamic calls and service-locator results as unresolved unless exact type or configuration evidence is present.",
            "Treat targetDeclaredReturnType and exact-call-return receiverCall evidence as declared contracts; do not speculate a conflicting return/null behavior unless visible changed implementation or control-flow evidence contradicts that contract, and treat absent contract attributes as unknown.",
            "Verify inheritance and trait behavior against the effective parent/trait definition before reporting a contract break.",
        )
        requests = tuple(
            EvidenceRequest("php-file", path, "changed PHP file and its exact namespace/import facts")
            for path in php_paths
        )
        return PluginOutcome.handled(ReviewContribution(rules=rules, evidence_requests=requests))

    def validate(self, claim: CandidateClaim):
        owns_claim = (
            claim.claim_kind == "php-file"
            or claim.claim_kind in _PHP_REPOSITORY_CLAIM_KINDS
        ) or (
            not claim.claim_kind and claim.category.startswith("php-")
        )
        if (
            not claim.path.lower().endswith((".php", ".phtml", ".inc"))
            or not owns_claim
        ):
            return PluginOutcome.abstained()
        if claim.claim_kind in _PHP_REPOSITORY_CLAIM_KINDS:
            matching = tuple(
                fact
                for fact in claim.evidence
                if fact.kind == claim.claim_kind
                and claim.path in {fact.path, *fact.related_paths}
            )
            message = claim.message.casefold()
            relevant = tuple(
                fact
                for fact in matching
                if any(
                    identifier and identifier in message
                    for identifier in {
                        fact.source.casefold(),
                        fact.source.rsplit("\\", 1)[-1].casefold(),
                        fact.target.casefold(),
                        fact.target.rsplit("\\", 1)[-1].casefold(),
                    }
                )
            )
            absence_claim = any(
                marker in message
                for marker in (
                    "does not extend",
                    "doesn't extend",
                    "does not implement",
                    "doesn't implement",
                    "missing dependency",
                    "missing trait",
                    "not inherited",
                    "not injected",
                    "does not construct",
                    "doesn't construct",
                    "does not call",
                    "doesn't call",
                )
            )
            if relevant and absence_claim:
                return PluginOutcome.handled(ValidationResult(
                    ValidationDecision.REJECT,
                    "php-relation-absence-contradicted",
                    "Exact PHP repository evidence contradicts the claimed missing relationship.",
                ))
            if relevant:
                return PluginOutcome.handled(ValidationResult(
                    ValidationDecision.INSUFFICIENT_EVIDENCE,
                    "php-presence-not-defect-proof",
                    (
                        "The exact PHP relationship exists, but relationship "
                        "presence alone does not prove that it is defective."
                    ),
                ))
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                (
                    "php-cited-relation-mismatch"
                    if claim.evidence
                    else "php-relation-evidence-unavailable"
                ),
                "The cited evidence does not prove the claimed PHP repository relationship.",
            ))
        evidence_paths = {fact.path for fact in claim.evidence}
        if claim.path not in evidence_paths:
            return PluginOutcome.handled(
                ValidationResult(
                    ValidationDecision.INSUFFICIENT_EVIDENCE,
                    "php-missing-local-evidence",
                    "The candidate has no exact PHP evidence from its reported path.",
                )
            )
        return PluginOutcome.handled(
            ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "php-file-not-defect-proof",
                (
                    "Exact PHP source is present, but a coarse file fact does "
                    "not deterministically prove the candidate defect. Generic "
                    "defects proved by changed source must leave claimKind empty."
                ),
            )
        )


def create_plugin(descriptor: PluginDescriptor) -> PhpPlugin:
    return PhpPlugin(descriptor)
