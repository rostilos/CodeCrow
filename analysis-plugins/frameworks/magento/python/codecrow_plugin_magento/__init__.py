from __future__ import annotations

from dataclasses import dataclass

from codecrow_plugins import (
    CandidateClaim,
    FileDisposition,
    PluginDescriptor,
    PluginOutcome,
    ValidationDecision,
    ValidationResult,
)

from .repository import MagentoRepositorySession


_CLAIM_KINDS = (
    ("preference", ("magento-di-effective-preference",)),
    ("virtualtype", ("magento-object-resolution",)),
    ("virtual type", ("magento-object-resolution",)),
    ("interceptor", (
        "magento-di-effective-plugin",
        "magento-di-inherited-plugin",
        "magento-intercepted-method",
        "magento-di-plugin-priority",
        "magento-interceptor-inapplicable",
    )),
    ("observer", ("magento-effective-observer",)),
    ("webapi", ("magento-webapi-route",)),
    ("acl", ("magento-acl-resource", "magento-webapi-acl")),
    ("route", (
        "magento-effective-route",
        "magento-route-controller",
        "magento-route-priority",
        "magento-route-controller-shadowed",
    )),
)

# These facts describe a concrete invalid or inapplicable effective Magento
# relationship. Other Magento facts describe topology and are useful context,
# but their presence does not prove that the relationship is defective.
_EXACT_DEFECT_FACT_KINDS = frozenset({
    "magento-db-foreign-key-invalid",
    "magento-extension-attribute-join-inapplicable",
    "magento-interceptor-inapplicable",
    "magento-message-consumer-invalid",
})


def _fact_identifiers(fact) -> frozenset[str]:
    identifiers: set[str] = set()
    for raw in (fact.source, fact.target):
        normalized = raw.casefold().strip()
        if not normalized:
            continue
        identifiers.add(normalized)
        identifiers.add(normalized.rsplit("\\", 1)[-1])
        if "::" in normalized:
            owner = normalized.rsplit("::", 1)[0]
            identifiers.add(owner)
            identifiers.add(owner.rsplit("\\", 1)[-1])
    return frozenset(identifier for identifier in identifiers if identifier)


def _fact_is_relevant(fact, message: str) -> bool:
    return any(
        identifier in message
        for identifier in _fact_identifiers(fact)
    )


@dataclass(frozen=True)
class MagentoPlugin:
    descriptor: PluginDescriptor

    def start_repository_analysis(self, revision: str):
        return PluginOutcome.handled(MagentoRepositorySession(self.descriptor.id, revision))

    def restore_repository_analysis(self, revision: str, snapshots):
        return PluginOutcome.handled(
            MagentoRepositorySession.restore(self.descriptor.id, revision, snapshots)
        )

    def file_disposition(self, path: str):
        """Keep Magento architecture complete while avoiding irrelevant vendor vectors."""
        normalized = "/" + path.casefold().strip("/")
        segments = tuple(segment for segment in normalized.split("/") if segment)
        if normalized.startswith(("/generated/", "/var/", "/pub/static/")):
            return PluginOutcome.handled(FileDisposition.GENERATED)
        if normalized.startswith("/dev/"):
            return PluginOutcome.handled(FileDisposition.EXCLUDED)
        if normalized.startswith("/vendor/") and any(
            segment in {"test", "tests"} for segment in segments
        ):
            return PluginOutcome.handled(FileDisposition.EXCLUDED)
        if normalized.endswith(".graphqls"):
            return PluginOutcome.handled(FileDisposition.ARCHITECTURE_ONLY)
        if (
            normalized.rsplit("/", 1)[-1] == "db_schema_whitelist.json"
            and "/etc/" in normalized
        ):
            return PluginOutcome.handled(FileDisposition.ARCHITECTURE_ONLY)
        if normalized.endswith(".xml") and (
            "/etc/" in normalized
            or "/layout/" in normalized
            or "/ui_component/" in normalized
        ):
            return PluginOutcome.handled(FileDisposition.ARCHITECTURE_ONLY)
        if normalized.startswith("/vendor/"):
            filename = normalized.rsplit("/", 1)[-1]
            if filename in {
                "composer.json", "registration.php", "theme.xml",
                "requirejs-config.js",
            }:
                return PluginOutcome.handled(FileDisposition.ARCHITECTURE_ONLY)
            suffix = normalized.rsplit(".", 1)[-1] if "." in normalized else ""
            if suffix in {"php", "inc"}:
                return PluginOutcome.handled(FileDisposition.FULL)
            if (
                suffix in {"phtml", "js", "mjs", "ts", "css", "less", "html"}
                and ("/view/" in normalized or "/web/" in normalized)
            ):
                return PluginOutcome.handled(FileDisposition.ARCHITECTURE_ONLY)
            return PluginOutcome.handled(FileDisposition.EXCLUDED)
        return PluginOutcome.handled(FileDisposition.FULL)

    def review(self, paths: tuple[str, ...]):
        # Repository architecture packets are the contribution. Avoid injecting
        # framework checklists or path-shape grouping that can override the
        # evidence actually present at the indexed revision.
        return PluginOutcome.abstained()

    def validate(self, claim: CandidateClaim):
        expected_kinds = self._claim_kinds(claim)
        if expected_kinds is None:
            return PluginOutcome.abstained()
        matching = [
            fact
            for fact in claim.evidence
            if fact.kind in expected_kinds
            and claim.path in {fact.path, *fact.related_paths}
        ]
        message = claim.message.casefold()
        relevant = [
            fact for fact in matching
            if _fact_is_relevant(fact, message)
        ]
        absence_claim = any(
            marker in message
            for marker in ("missing", "does not exist", "doesn't exist", "not configured", "not registered", "no preference", "no observer", "no route")
        )
        if absence_claim and relevant:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.REJECT,
                "magento-absence-contradicted",
                "The candidate claims configuration is absent, but an exact matching Magento graph fact exists.",
            ))
        if expected_kinds != ("magento-interceptor-inapplicable",):
            for fact in claim.evidence:
                if (
                    fact.kind != "magento-interceptor-inapplicable"
                    or claim.path not in {fact.path, *fact.related_paths}
                    or not _fact_is_relevant(fact, message)
                ):
                    continue
                return PluginOutcome.handled(ValidationResult(
                    ValidationDecision.REJECT,
                    "magento-interceptor-inapplicable-contradiction",
                    "Exact PHP/Magento evidence proves that the claimed interceptor method cannot execute.",
                ))
        diagnostic = next(
            (
                fact for fact in relevant
                if fact.kind in _EXACT_DEFECT_FACT_KINDS
            ),
            None,
        )
        if diagnostic is not None:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.PASS,
                "magento-exact-defect-proof-present",
                (
                    "An exact Magento diagnostic fact proves the claimed "
                    "invalid or inapplicable effective relationship."
                ),
            ))
        if relevant:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "magento-presence-not-defect-proof",
                (
                    "The exact Magento relationship exists, but structural "
                    "presence alone does not prove that it is defective."
                ),
            ))
        if matching:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "magento-cited-identifier-mismatch",
                (
                    "The cited Magento fact kind exists, but its exact "
                    "relationship identifiers do not match the candidate."
                ),
            ))
        if claim.evidence:
            return PluginOutcome.handled(ValidationResult(
                ValidationDecision.INSUFFICIENT_EVIDENCE,
                "magento-cited-evidence-mismatch",
                "The cited exact evidence does not contain the Magento relationship kind claimed by the candidate.",
            ))
        return PluginOutcome.handled(ValidationResult(
            ValidationDecision.INSUFFICIENT_EVIDENCE,
            "magento-evidence-unavailable",
            "No exact Magento relationship evidence was supplied for this framework claim.",
        ))

    def _claim_kinds(self, claim: CandidateClaim) -> tuple[str, ...] | None:
        if claim.claim_kind:
            return (
                (claim.claim_kind,)
                if claim.claim_kind.startswith("magento-")
                else None
            )
        if claim.category.startswith("magento-"):
            return (claim.category,)
        normalized_path = "/" + claim.path.casefold().lstrip("/")
        message = claim.message.casefold()
        if "magento" not in message and "/etc/" not in normalized_path:
            return None
        # An event assertion is framework-specific only when it explicitly
        # references Magento. This avoids treating a generic application event
        # finding as an observer-configuration claim.
        if "event" in message and "magento" in message:
            return ("magento-effective-observer",)
        if (
            "route" in message
            and "webapi" not in message
            and any(marker in message for marker in (
                "search order",
                "route priority",
                "searched before",
                "configured before",
                "configured after",
                "before the route module",
                "after the route module",
            ))
        ):
            return ("magento-route-priority",)
        if (
            "route" in message
            and "webapi" not in message
            and any(marker in message for marker in (
                "controller shadow",
                "controller override",
                "shadowed controller",
                "overridden controller",
            ))
        ):
            return ("magento-route-controller-shadowed",)
        if (
            ("interceptor" in message or "magento plugin" in message)
            and any(marker in message for marker in (
                "sortorder",
                "sort order",
                "priority",
                "execution order",
                "executes before",
                "runs before",
            ))
        ):
            return ("magento-di-plugin-priority",)
        if (
            ("interceptor" in message or "magento plugin" in message)
            and any(marker in message for marker in (
                "cannot intercept",
                "not interceptable",
                "final class",
                "final method",
                "static method",
                "private method",
                "protected method",
                "virtual type",
            ))
        ):
            return ("magento-interceptor-inapplicable",)
        matched_kinds = {
            kind
            for marker, kinds in _CLAIM_KINDS
            if marker in message
            # "webapi route" is a Web API relationship, not evidence for a
            # frontend/admin route. Keep additional markers such as ACL so a
            # single claim can require either exact Web API relationship.
            and not (marker == "route" and "webapi" in message)
            for kind in kinds
        }
        return tuple(sorted(matched_kinds)) if matched_kinds else None


def create_plugin(descriptor: PluginDescriptor) -> MagentoPlugin:
    return MagentoPlugin(descriptor)
