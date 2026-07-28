from __future__ import annotations

from dataclasses import dataclass

from codecrow_plugins import (
    CandidateClaim,
    PluginDescriptor,
    PluginOutcome,
    ReviewContribution,
    ValidationDecision,
    ValidationResult,
)

from .repository import HyvaRepositorySession


_RELATION_KINDS = frozenset({
    "hyva-alpine-component-reference",
    "hyva-alpine-event-dispatch",
    "hyva-template-runtime-variable",
    "hyva-template-webapi-reference",
    "hyva-view-model-requirement",
})
_ABSENCE_MARKERS = (
    "component is undefined",
    "does not dispatch",
    "does not exist",
    "doesn't dispatch",
    "doesn't exist",
    "endpoint is missing",
    "endpoint does not exist",
    "endpoint doesn't exist",
    "missing constructor",
    "missing dispatch",
    "missing listener",
    "missing provider",
    "missing route",
    "missing view model",
    "never dispatched",
    "no listener",
    "not defined",
    "not dispatched",
    "not listened",
    "not registered",
    "provider is undefined",
    "undefined component",
    "undefined provider",
    "viewmodel is missing",
)
def _identifier_variants(value: str) -> tuple[str, ...]:
    normalized = value.casefold().strip()
    values = {normalized}
    for separator in ("::", "\\", "/", ".", "#"):
        values.add(normalized.rsplit(separator, 1)[-1])
    return tuple(sorted(
        item for item in values
        if len(item) >= 3 and item not in {"component", "template"}
    ))


def _fact_is_relevant(fact, message: str) -> bool:
    return any(
        identifier in message
        for value in (fact.source, fact.target)
        for identifier in _identifier_variants(value)
    )


@dataclass(frozen=True)
class HyvaPlugin:
    descriptor: PluginDescriptor

    def start_repository_analysis(self, revision: str):
        return PluginOutcome.handled(
            HyvaRepositorySession(self.descriptor.id, revision)
        )

    def restore_repository_analysis(self, revision: str, snapshots):
        return PluginOutcome.handled(
            HyvaRepositorySession.restore(
                self.descriptor.id,
                revision,
                snapshots,
            )
        )

    def review(self, paths: tuple[str, ...]):
        selected = tuple(sorted(
            path for path in paths
            if path.casefold().endswith(".phtml")
        ))
        if not selected:
            return PluginOutcome.abstained()
        return PluginOutcome.handled(ReviewContribution(rules=(
            (
                "For a Hyva template-runtime, cross-template ViewModel, REST, "
                "Alpine provider, or Alpine event assertion, cite the exact "
                "hyva-* Evidence ID and use that relationship kind as "
                "claimKind."
            ),
            (
                "Leave claimKind empty for defects proved by changed source "
                "alone; a topology relationship proves presence and context, "
                "not that the relationship is defective."
            ),
        )))

    def validate(self, claim: CandidateClaim):
        if not claim.claim_kind.startswith("hyva-"):
            return PluginOutcome.abstained()
        if claim.claim_kind not in _RELATION_KINDS:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "hyva-unknown-relation-kind",
                "The Hyva claim kind is not owned by an exact validator.",
            ))
        matching = tuple(
            fact
            for fact in claim.evidence
            if fact.kind == claim.claim_kind
            and claim.path in {fact.path, *fact.related_paths}
        )
        message = claim.message.casefold()
        relevant = tuple(
            fact for fact in matching
            if _fact_is_relevant(fact, message)
        )
        if not relevant:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                (
                    "hyva-cited-relation-mismatch"
                    if claim.evidence
                    else "hyva-relation-evidence-unavailable"
                ),
                (
                    "The cited evidence does not identify the exact Hyva "
                    "relationship asserted by the candidate."
                ),
            ))
        absence_contradicted = (
            any(marker in message for marker in _ABSENCE_MARKERS)
            or (
                "undefined variable" in message
                and any(
                    fact.kind == "hyva-template-runtime-variable"
                    for fact in relevant
                )
            )
        )
        if absence_contradicted:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.REJECT,
                "hyva-relation-absence-contradicted",
                (
                    "Exact Hyva repository evidence contradicts the claimed "
                    "missing relationship."
                ),
            ))
        return PluginOutcome.handled(ValidationResult(
            ValidationDecision.INSUFFICIENT_EVIDENCE,
            "hyva-presence-is-not-defect-proof",
            (
                "The exact Hyva relationship proves presence and context "
                "only; it cannot by itself prove a semantic defect."
            ),
        ))


def create_plugin(descriptor: PluginDescriptor) -> HyvaPlugin:
    return HyvaPlugin(descriptor)
