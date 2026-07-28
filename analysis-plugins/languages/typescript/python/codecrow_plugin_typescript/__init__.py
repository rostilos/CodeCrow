from __future__ import annotations

import re
from dataclasses import dataclass

from codecrow_plugins import (
    CandidateClaim,
    EvidenceRequest,
    FileArtifact,
    ImportGraphSession,
    PluginDescriptor,
    PluginDiagnostic,
    PluginOutcome,
    ReviewContribution,
    SyntaxContribution,
    ValidationDecision,
    ValidationResult,
)

from .repository import (
    TYPESCRIPT_EXTENSIONS,
    analyze_typescript_artifact,
    parse_import_record,
    resolve_import,
)


_SNAPSHOT_KIND = "typescript-import-graph"
_RELATION_KINDS = {
    "typescript-call",
    "typescript-call-resolution",
    "typescript-callable",
    "typescript-construction",
    "typescript-export",
    "typescript-import",
    "typescript-import-binding",
    "typescript-module-resolution",
    "typescript-type",
}
_TOKEN_BOUNDARY = re.compile(r"[A-Za-z0-9_$]")


def _identifiers(value: str) -> tuple[str, ...]:
    normalized = value.casefold()
    values = {normalized}
    for separator in ("::", "/", ".", "#"):
        values.add(normalized.rsplit(separator, 1)[-1])
    return tuple(sorted(
        item
        for item in values
        if len(item) >= 3 and item not in {"default", "module", "class"}
    ))


def _message_mentions(message: str, value: str) -> bool:
    for identifier in _identifiers(value):
        offset = message.find(identifier)
        while offset >= 0:
            before = message[offset - 1] if offset else ""
            end = offset + len(identifier)
            after = message[end] if end < len(message) else ""
            if (
                (not before or _TOKEN_BOUNDARY.fullmatch(before) is None)
                and (not after or _TOKEN_BOUNDARY.fullmatch(after) is None)
            ):
                return True
            offset = message.find(identifier, offset + 1)
    return False


@dataclass(frozen=True)
class TypescriptPlugin:
    descriptor: PluginDescriptor

    def syntax(self):
        return PluginOutcome.handled(SyntaxContribution(
            plugin_id=self.descriptor.id,
            language_id="typescript",
            grammar_module="tree_sitter_typescript",
            grammar_factory="language_typescript",
            query_resource="python/resources/rag-chunks.scm",
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
        if (
            artifact.deleted
            or not artifact.path.casefold().endswith(TYPESCRIPT_EXTENSIONS)
        ):
            return PluginOutcome.abstained()
        try:
            analyzed = analyze_typescript_artifact(artifact)
        except Exception as exception:
            return PluginOutcome.failed(PluginDiagnostic(
                "typescript-parse-failed",
                f"{type(exception).__name__}: {exception}",
                self.descriptor.id,
            ))
        return PluginOutcome.handled(analyzed[0])

    def review(self, paths: tuple[str, ...]):
        selected = tuple(sorted(
            path
            for path in paths
            if path.casefold().endswith(TYPESCRIPT_EXTENSIONS)
        ))
        if not selected:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(ReviewContribution(
            rules=(
                "A typescript-pr-removed-relation is base-to-PR navigation evidence only; require changed-hunk proof of harm.",
                "Resolve TypeScript relative imports, exported declarations, and imported call targets before reporting a missing symbol or cross-file contract defect.",
                "Treat exact typescript-module-resolution, typescript-import-binding, and typescript-call-resolution facts as navigation evidence; relationship presence alone is not proof of a defect.",
                "Use an exact bracketed typescript-* relationship kind as claimKind only when the claim depends on that relationship and cite its Evidence ID; leave claimKind empty for defects proved entirely by changed source.",
            ),
            evidence_requests=tuple(
                EvidenceRequest(
                    "typescript-file",
                    path,
                    "exact TypeScript declarations, imports, exports, and resolved call facts",
                )
                for path in selected
            ),
        ))

    def validate(self, claim: CandidateClaim):
        if (
            not claim.claim_kind.startswith("typescript-")
            or not claim.path.casefold().endswith(TYPESCRIPT_EXTENSIONS)
        ):
            return PluginOutcome.abstained()
        if claim.claim_kind == "typescript-file":
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "typescript-coarse-evidence-class",
                "A file-level TypeScript fact cannot prove a concrete relationship claim.",
            ))
        if claim.claim_kind not in _RELATION_KINDS:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "typescript-unknown-relation-kind",
                "The TypeScript claim kind is not owned by an exact relationship validator.",
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
                    "typescript-cited-relation-mismatch"
                    if claim.evidence
                    else "typescript-relation-evidence-unavailable"
                ),
                "The cited evidence does not identify the TypeScript relationship asserted by the candidate.",
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
                "unresolved import",
                "undefined import",
            )
        )
        if absence_claim:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.REJECT,
                "typescript-relation-absence-contradicted",
                "Exact TypeScript repository evidence contradicts the claimed missing relationship.",
            ))
        return PluginOutcome.handled(ValidationResult(
            ValidationDecision.INSUFFICIENT_EVIDENCE,
            "typescript-presence-is-not-defect-proof",
            "The exact TypeScript relationship proves presence only; it cannot by itself prove the candidate's semantic defect.",
        ))


def create_plugin(descriptor: PluginDescriptor) -> TypescriptPlugin:
    return TypescriptPlugin(descriptor)
