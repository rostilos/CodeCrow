from __future__ import annotations

import re
from dataclasses import dataclass

from codecrow_plugins import (
    CandidateClaim, FileArtifact, PluginDescriptor,
    PluginDiagnostic, PluginOutcome, ReviewContribution,
    SyntaxContribution, ValidationDecision, ValidationResult,
)

from .repository import (
    JAVASCRIPT_EXTENSIONS,
    JavascriptRepositorySession,
    analyze_javascript_artifact,
)

_RELATION_KINDS = {
    "javascript-call",
    "javascript-callable",
    "javascript-component-prop",
    "javascript-component-resolution",
    "javascript-construction",
    "javascript-export",
    "javascript-import",
    "javascript-import-binding",
    "javascript-inheritance",
    "javascript-jsx-prop",
    "javascript-jsx-prop-contract",
    "javascript-jsx-render",
    "javascript-jsx-required-prop-missing",
    "javascript-type",
}
_TOKEN_BOUNDARY = re.compile(r"[A-Za-z0-9_$]")


def _identifier_variants(value: str) -> tuple[str, ...]:
    normalized = value.casefold()
    values = {normalized}
    for separator in ("::", "\\", "/", ".", "#"):
        values.add(normalized.rsplit(separator, 1)[-1])
    return tuple(sorted(
        item for item in values
        if len(item) >= 3 and item not in {"default", "module", "class"}
    ))


def _message_mentions(message: str, value: str) -> bool:
    for identifier in _identifier_variants(value):
        offset = message.find(identifier)
        while offset >= 0:
            before = message[offset - 1] if offset else ""
            after_offset = offset + len(identifier)
            after = message[after_offset] if after_offset < len(message) else ""
            if (
                (not before or _TOKEN_BOUNDARY.fullmatch(before) is None)
                and (not after or _TOKEN_BOUNDARY.fullmatch(after) is None)
            ):
                return True
            offset = message.find(identifier, offset + 1)
    return False


@dataclass(frozen=True)
class JavascriptPlugin:
    descriptor: PluginDescriptor

    def syntax(self):
        return PluginOutcome.handled(SyntaxContribution(
            plugin_id=self.descriptor.id,
            language_id="javascript",
            grammar_module="tree_sitter_javascript",
            grammar_factory="language",
            query_resource="python/resources/rag-chunks.scm",
            builtin_tags=True,
        ))

    def start_repository_analysis(self, revision: str):
        return PluginOutcome.handled(
            JavascriptRepositorySession(self.descriptor.id, revision)
        )

    def restore_repository_analysis(self, revision: str, snapshots):
        return PluginOutcome.handled(
            JavascriptRepositorySession.restore(
                self.descriptor.id,
                revision,
                snapshots,
            )
        )

    def index_file(self, artifact: FileArtifact):
        if (
            not artifact.path.casefold().endswith(JAVASCRIPT_EXTENSIONS)
            or artifact.deleted
        ):
            return PluginOutcome.abstained()
        try:
            facts, _ = analyze_javascript_artifact(artifact)
        except Exception as exception:
            return PluginOutcome.failed(PluginDiagnostic(
                "javascript-parse-failed", f"{type(exception).__name__}: {exception}", self.descriptor.id,
            ))
        return PluginOutcome.handled(facts)

    def review(self, paths: tuple[str, ...]):
        selected = tuple(sorted(
            path for path in paths
            if path.casefold().endswith(JAVASCRIPT_EXTENSIONS)
        ))
        if not selected:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(ReviewContribution(
            rules=(
                "For asynchronous JavaScript claims, require exact promise, await, callback, or event evidence; do not infer ordering from names.",
                "For cross-file JSX component or prop claims, require exact resolved component and prop-contract facts from both the caller and component; a prop name in one file is not proof of a mismatch.",
                "Resolve JavaScript imports, exports, class inheritance, and call targets before reporting a missing symbol or broken module contract.",
                "Use the exact bracketed javascript-* relationship kind as claimKind with matching Evidence IDs; never use the coarse javascript-file class. Leave claimKind empty for defects proved entirely by changed source.",
            ),
        ))

    def validate(self, claim: CandidateClaim):
        if (
            not claim.claim_kind.startswith("javascript-")
            or not claim.path.casefold().endswith(JAVASCRIPT_EXTENSIONS)
        ):
            return PluginOutcome.abstained()
        if claim.claim_kind == "javascript-file":
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "javascript-coarse-evidence-class",
                "A file-level JavaScript fact cannot prove a concrete relationship claim.",
            ))
        if claim.claim_kind not in _RELATION_KINDS:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "javascript-unknown-relation-kind",
                "The JavaScript claim kind is not owned by an exact relationship validator.",
            ))
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
            if _message_mentions(message, fact.source)
            or _message_mentions(message, fact.target)
        )
        if not relevant:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                (
                    "javascript-cited-relation-mismatch"
                    if claim.evidence
                    else "javascript-relation-evidence-unavailable"
                ),
                "The cited evidence does not identify the JavaScript relationship asserted by the candidate.",
            ))
        absence_claim = any(
            marker in message
            for marker in (
                "does not import",
                "doesn't import",
                "not imported",
                "missing import",
                "does not export",
                "doesn't export",
                "not exported",
                "missing export",
                "does not pass",
                "doesn't pass",
                "not passed",
                "missing prop",
                "undefined component",
                "unresolved component",
            )
        )
        presence_kinds = _RELATION_KINDS - {
            "javascript-jsx-required-prop-missing",
        }
        if absence_claim and claim.claim_kind in presence_kinds:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.REJECT,
                "javascript-relation-absence-contradicted",
                "Exact JavaScript repository evidence contradicts the claimed missing relationship.",
            ))
        if claim.claim_kind in presence_kinds:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "javascript-presence-is-not-defect-proof",
                (
                    "The exact JavaScript relationship proves presence only; "
                    "it cannot by itself prove the candidate's semantic defect."
                ),
            ))
        return PluginOutcome.handled(ValidationResult(
            ValidationDecision.PASS,
            "javascript-exact-defect-proof-present",
            "Exact JavaScript repository evidence proves the reported defect.",
        ))


def create_plugin(descriptor: PluginDescriptor) -> JavascriptPlugin:
    return JavascriptPlugin(descriptor)
