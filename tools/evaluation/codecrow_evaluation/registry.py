from __future__ import annotations

import fcntl
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ._util import (
    canonical_bytes,
    parse_utc,
    require_mapping,
    require_sha256,
    require_string,
    sha256_bytes,
)


class RegistryInputError(ValueError):
    """The split registry does not meet its fail-closed contract."""


class AccessDenied(PermissionError):
    """Protected split access was denied and recorded."""


class LedgerIntegrityError(ValueError):
    """The access ledger is malformed or has been altered."""


_PUBLIC_PURPOSES = {"development", "calibration", "diagnostic"}
_PROTECTED_PURPOSES = {"primary_heldout", "confirmation_reserve"}
_REQUIRED_FEATURES = {
    "clean_control",
    "collision",
    "cross_file",
    "hard_negative",
    "large_pr",
    "multilanguage",
    "positive",
    "rename",
}
_DATA_CLASSES = ("identities", "labels", "outcomes")
_BEHAVIOR_ROLES = {"implementation", "planning", "policy_selector", "pruning"}
_ACCESS_ROLES = {"scorer", "independent_reviewer"}
_ZERO_HASH = "0" * 64


def _integer(value: object, field: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RegistryInputError(f"{field} must be an integer >= {minimum}")
    return value


@dataclass
class SplitRegistration:
    split_id: str
    purpose: str
    source_kind: str
    case_count: int
    content_sha256: str | None = None
    identities_commitment_sha256: str | None = None
    labels_commitment_sha256: str | None = None
    outcomes_commitment_sha256: str | None = None
    registered_gate: str | None = None
    custodian: str | None = None
    independent_reviewer: str | None = None
    sealed_at: str | None = None
    feature_coverage: tuple[str, ...] = ()
    feature_coverage_attestation_sha256: str | None = None

    @property
    def protected(self) -> bool:
        return self.source_kind == "internal_blinded"


class SplitRegistry:
    def __init__(
        self,
        *,
        registry_id: str,
        registry_version: str,
        program_owner: str,
        splits: list[SplitRegistration],
        disjointness_attestation: Mapping[str, Any],
    ) -> None:
        self.registry_id = registry_id
        self.registry_version = registry_version
        self.program_owner = program_owner
        self._splits = {split.split_id: split for split in splits}
        self.disjointness_attestation = dict(disjointness_attestation)
        self.fresh_reserve_required = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SplitRegistry":
        mapping = require_mapping(value, "split registry", RegistryInputError)
        if mapping.get("schemaVersion") != 1:
            raise RegistryInputError("schemaVersion must be 1")
        registry_id = require_string(mapping.get("registryId"), "registryId", RegistryInputError)
        registry_version = require_string(
            mapping.get("registryVersion"), "registryVersion", RegistryInputError
        )
        program_owner = require_string(
            mapping.get("programOwner"), "programOwner", RegistryInputError
        )
        raw_splits = mapping.get("splits")
        if not isinstance(raw_splits, list) or not raw_splits:
            raise RegistryInputError("splits must be a non-empty array")

        splits: list[SplitRegistration] = []
        split_ids: set[str] = set()
        commitments: list[str] = []
        purposes: set[str] = set()
        for raw in raw_splits:
            item = require_mapping(raw, "splits[]", RegistryInputError)
            split_id = require_string(item.get("splitId"), "splitId", RegistryInputError)
            if split_id in split_ids:
                raise RegistryInputError(f"duplicate splitId {split_id}")
            split_ids.add(split_id)
            purpose = require_string(item.get("purpose"), f"{split_id}.purpose", RegistryInputError)
            source_kind = require_string(
                item.get("sourceKind"), f"{split_id}.sourceKind", RegistryInputError
            )
            case_count = _integer(item.get("caseCount"), f"{split_id}.caseCount")
            purposes.add(purpose)
            if source_kind == "public":
                if purpose not in _PUBLIC_PURPOSES:
                    raise RegistryInputError(
                        f"{split_id} public split purpose must be development, calibration, or diagnostic"
                    )
                content_sha = require_sha256(
                    item.get("contentSha256"), f"{split_id}.contentSha256", RegistryInputError
                )
                if item.get("labelsVisible") is not True:
                    raise RegistryInputError(f"{split_id}.labelsVisible must be true")
                split = SplitRegistration(
                    split_id=split_id,
                    purpose=purpose,
                    source_kind=source_kind,
                    case_count=case_count,
                    content_sha256=content_sha,
                )
            elif source_kind == "internal_blinded":
                if purpose not in _PROTECTED_PURPOSES:
                    raise RegistryInputError(
                        f"{split_id} blinded split purpose must be primary_heldout or confirmation_reserve"
                    )
                custodian = require_string(
                    item.get("custodian"), f"{split_id}.custodian", RegistryInputError
                )
                reviewer = require_string(
                    item.get("independentReviewer"),
                    f"{split_id}.independentReviewer",
                    RegistryInputError,
                )
                if custodian == program_owner or reviewer == program_owner or custodian == reviewer:
                    raise RegistryInputError(
                        f"{split_id} requires an independent custodian and reviewer distinct from the program owner and each other"
                    )
                identity = require_sha256(
                    item.get("identitiesCommitmentSha256"),
                    f"{split_id}.identitiesCommitmentSha256",
                    RegistryInputError,
                )
                labels = require_sha256(
                    item.get("labelsCommitmentSha256"),
                    f"{split_id}.labelsCommitmentSha256",
                    RegistryInputError,
                )
                outcomes = require_sha256(
                    item.get("outcomesCommitmentSha256"),
                    f"{split_id}.outcomesCommitmentSha256",
                    RegistryInputError,
                )
                features = item.get("featureCoverage")
                if not isinstance(features, list) or set(features) != _REQUIRED_FEATURES:
                    raise RegistryInputError(
                        f"{split_id}.featureCoverage must contain exactly {sorted(_REQUIRED_FEATURES)}"
                    )
                feature_attestation = require_sha256(
                    item.get("featureCoverageAttestationSha256"),
                    f"{split_id}.featureCoverageAttestationSha256",
                    RegistryInputError,
                )
                sealed_at = require_string(
                    item.get("sealedAt"), f"{split_id}.sealedAt", RegistryInputError
                )
                parse_utc(sealed_at, f"{split_id}.sealedAt", RegistryInputError)
                split = SplitRegistration(
                    split_id=split_id,
                    purpose=purpose,
                    source_kind=source_kind,
                    case_count=case_count,
                    identities_commitment_sha256=identity,
                    labels_commitment_sha256=labels,
                    outcomes_commitment_sha256=outcomes,
                    registered_gate=require_string(
                        item.get("registeredGate"),
                        f"{split_id}.registeredGate",
                        RegistryInputError,
                    ),
                    custodian=custodian,
                    independent_reviewer=reviewer,
                    sealed_at=sealed_at,
                    feature_coverage=tuple(sorted(features)),
                    feature_coverage_attestation_sha256=feature_attestation,
                )
                commitments.extend((identity, labels, outcomes))
            else:
                raise RegistryInputError(
                    f"{split_id}.sourceKind must be public or internal_blinded"
                )
            splits.append(split)

        if len(commitments) != len(set(commitments)):
            raise RegistryInputError("all protected commitment values must be unique")
        for required_purpose in (
            "development",
            "calibration",
            "primary_heldout",
            "confirmation_reserve",
        ):
            if required_purpose not in purposes:
                raise RegistryInputError(f"registry is missing {required_purpose} split")

        attestation = require_mapping(
            mapping.get("disjointnessAttestation"),
            "disjointnessAttestation",
            RegistryInputError,
        )
        covered = attestation.get("coversSplitIds")
        if not isinstance(covered, list) or covered != sorted(split_ids):
            raise RegistryInputError(
                "disjointnessAttestation.coversSplitIds must exactly cover all sorted split IDs"
            )
        attestation_custodian = require_string(
            attestation.get("custodian"),
            "disjointnessAttestation.custodian",
            RegistryInputError,
        )
        attestation_reviewer = require_string(
            attestation.get("independentReviewer"),
            "disjointnessAttestation.independentReviewer",
            RegistryInputError,
        )
        if (
            attestation_custodian == program_owner
            or attestation_reviewer == program_owner
            or attestation_custodian == attestation_reviewer
        ):
            raise RegistryInputError(
                "disjointness attestation requires an independent custodian and reviewer"
            )
        require_sha256(
            attestation.get("membershipDigestSha256"),
            "disjointnessAttestation.membershipDigestSha256",
            RegistryInputError,
        )
        parse_utc(
            attestation.get("signedAt"),
            "disjointnessAttestation.signedAt",
            RegistryInputError,
        )
        return cls(
            registry_id=registry_id,
            registry_version=registry_version,
            program_owner=program_owner,
            splits=splits,
            disjointness_attestation=attestation,
        )

    def split(self, split_id: str) -> SplitRegistration:
        try:
            return self._splits[split_id]
        except KeyError as exc:
            raise RegistryInputError(f"unknown splitId {split_id}") from exc

    def policy_context(self) -> dict[str, Any]:
        """Return only disclosed splits; protected existence and bytes cannot steer behavior."""

        public = [split for split in self._splits.values() if not split.protected]
        return {
            "registryId": self.registry_id,
            "registryVersion": self.registry_version,
            "schemaVersion": 1,
            "splits": [
                {
                    "caseCount": split.case_count,
                    "contentSha256": split.content_sha256,
                    "purpose": split.purpose,
                    "splitId": split.split_id,
                }
                for split in sorted(public, key=lambda item: (item.purpose, item.split_id))
            ],
        }

    def select_acceptance_split(self, purpose: str) -> SplitRegistration:
        candidates = [
            split
            for split in self._splits.values()
            if split.protected and split.purpose == purpose
        ]
        if not candidates:
            if purpose == "confirmation_reserve" and self.fresh_reserve_required:
                raise RegistryInputError("a fresh untouched confirmation reserve is required")
            raise RegistryInputError(f"no eligible protected {purpose} split")
        return sorted(candidates, key=lambda item: item.split_id)[0]


class _AccessLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.head_path = path.with_name(f"{path.name}.head.json")

    @staticmethod
    def _parse_events(raw: bytes) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        previous = _ZERO_HASH
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LedgerIntegrityError("ledger is not valid UTF-8") from exc
        for line_number, line in enumerate(text.splitlines(), start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LedgerIntegrityError(
                    f"ledger line {line_number} is not valid JSON"
                ) from exc
            if not isinstance(event, dict):
                raise LedgerIntegrityError(f"ledger line {line_number} must be an object")
            event_hash = event.get("eventHash")
            if event.get("sequence") != line_number:
                raise LedgerIntegrityError(f"ledger line {line_number} has invalid sequence")
            if event.get("previousEventHash") != previous:
                raise LedgerIntegrityError(
                    f"ledger line {line_number} has invalid previousEventHash"
                )
            unsigned = dict(event)
            unsigned.pop("eventHash", None)
            expected = sha256_bytes(canonical_bytes(unsigned))
            if event_hash != expected:
                raise LedgerIntegrityError(f"ledger line {line_number} has invalid eventHash")
            previous = str(event_hash)
            events.append(event)
        return events

    def _verify_head(self, events: list[dict[str, Any]]) -> None:
        if not events:
            if self.head_path.exists():
                raise LedgerIntegrityError("ledger head exists for an empty or missing ledger")
            return
        if not self.head_path.is_file():
            raise LedgerIntegrityError("ledger head is missing")
        try:
            head = json.loads(self.head_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LedgerIntegrityError("ledger head is not valid UTF-8 JSON") from exc
        expected = {
            "eventCount": len(events),
            "eventHash": events[-1]["eventHash"],
            "schemaVersion": 1,
        }
        if head != expected:
            raise LedgerIntegrityError("ledger head does not match the append-only ledger")

    @staticmethod
    def _read_descriptor(descriptor: int) -> bytes:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def verify(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            self._verify_head([])
            return []
        descriptor = os.open(self.path, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            events = self._parse_events(self._read_descriptor(descriptor))
            self._verify_head(events)
            return events
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _write_head(self, event: Mapping[str, Any], event_count: int) -> None:
        value = {
            "eventCount": event_count,
            "eventHash": event["eventHash"],
            "schemaVersion": 1,
        }
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.head_path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(canonical_bytes(value) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.head_path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary.exists():
                temporary.unlink()

    def append(self, event: Mapping[str, Any]) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path,
            os.O_APPEND | os.O_CREAT | os.O_RDWR,
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            events = self._parse_events(self._read_descriptor(descriptor))
            self._verify_head(events)
            unsigned = {
                **dict(event),
                "previousEventHash": events[-1]["eventHash"] if events else _ZERO_HASH,
                "schemaVersion": 1,
                "sequence": len(events) + 1,
            }
            complete = {
                **unsigned,
                "eventHash": sha256_bytes(canonical_bytes(unsigned)),
            }
            os.write(descriptor, canonical_bytes(complete) + b"\n")
            os.fsync(descriptor)
            self._write_head(complete, len(events) + 1)
            return complete
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


class AccessController:
    def __init__(
        self,
        registry: SplitRegistry,
        ledger_path: Path | str,
        *,
        trusted_receipt_sha256: Sequence[str] | set[str] | frozenset[str] = (),
    ) -> None:
        self.registry = registry
        self._ledger = _AccessLedger(Path(ledger_path))
        self._trusted_receipts = frozenset(
            require_sha256(value, "trustedReceiptSha256", RegistryInputError)
            for value in trusted_receipt_sha256
        )
        events = self._ledger.verify()
        for event in events:
            if event.get("decision") != "demoted":
                continue
            split = self.registry.split(str(event.get("splitId")))
            prior_purpose = split.purpose
            split.purpose = "diagnostic"
            if prior_purpose == "confirmation_reserve":
                self.registry.fresh_reserve_required = True

    def verify_ledger(self) -> int:
        return len(self._ledger.verify())

    def _record(
        self,
        *,
        split_id: str,
        actor: str,
        role: str,
        data_classes: Sequence[str],
        gate: str | None,
        decision: str,
        reason: str,
        occurred_at: str,
        receipt_sha256: str | None,
    ) -> dict[str, Any]:
        return self._ledger.append(
            {
                "actor": actor,
                "dataClasses": list(data_classes),
                "decision": decision,
                "gate": gate,
                "occurredAt": occurred_at,
                "reason": reason,
                "receiptSha256": receipt_sha256,
                "role": role,
                "splitId": split_id,
            }
        )

    def _deny(
        self,
        *,
        message: str,
        split_id: str,
        actor: str,
        role: str,
        data_classes: Sequence[str],
        gate: str | None,
        at: str,
        receipt_sha256: str | None = None,
    ) -> None:
        self._record(
            split_id=split_id,
            actor=actor,
            role=role,
            data_classes=data_classes,
            gate=gate,
            decision="denied",
            reason=message,
            occurred_at=at,
            receipt_sha256=receipt_sha256,
        )
        raise AccessDenied(message)

    def authorize(
        self,
        *,
        split_id: str,
        actor: str,
        role: str,
        data_classes: Sequence[str],
        gate_receipt: Mapping[str, Any] | None,
        at: str,
    ) -> dict[str, Any]:
        split = self.registry.split(split_id)
        actor = require_string(actor, "actor", RegistryInputError)
        role = require_string(role, "role", RegistryInputError)
        requested = tuple(data_classes)
        if not requested or len(requested) != len(set(requested)) or any(
            item not in _DATA_CLASSES for item in requested
        ):
            raise RegistryInputError(
                f"dataClasses must be unique values from {list(_DATA_CLASSES)}"
            )
        requested = tuple(item for item in _DATA_CLASSES if item in requested)
        parse_utc(at, "at", RegistryInputError)
        if not split.protected:
            event = self._record(
                split_id=split_id,
                actor=actor,
                role=role,
                data_classes=requested,
                gate=None,
                decision="granted",
                reason="disclosed split",
                occurred_at=at,
                receipt_sha256=None,
            )
            return {
                "dataClasses": list(requested),
                "gate": None,
                "grantId": event["eventHash"],
                "schemaVersion": 1,
                "splitId": split_id,
            }
        if role in _BEHAVIOR_ROLES:
            self._deny(
                message="protected data is never available to a behavior-affecting role",
                split_id=split_id,
                actor=actor,
                role=role,
                data_classes=requested,
                gate=split.registered_gate,
                at=at,
            )
        if role not in _ACCESS_ROLES:
            self._deny(
                message="role is not authorized for protected evaluation access",
                split_id=split_id,
                actor=actor,
                role=role,
                data_classes=requested,
                gate=split.registered_gate,
                at=at,
            )
        if gate_receipt is None:
            self._deny(
                message="protected data requires its registered unblinding gate receipt",
                split_id=split_id,
                actor=actor,
                role=role,
                data_classes=requested,
                gate=split.registered_gate,
                at=at,
            )

        receipt = require_mapping(gate_receipt, "gateReceipt", RegistryInputError)
        receipt_sha = receipt.get("receiptSha256")
        try:
            require_sha256(receipt_sha, "gateReceipt.receiptSha256", RegistryInputError)
        except RegistryInputError:
            self._deny(
                message="gate receipt digest is invalid",
                split_id=split_id,
                actor=actor,
                role=role,
                data_classes=requested,
                gate=split.registered_gate,
                at=at,
            )
        unsigned = dict(receipt)
        unsigned.pop("receiptSha256", None)
        if sha256_bytes(canonical_bytes(unsigned)) != receipt_sha:
            self._deny(
                message="gate receipt digest does not match its contents",
                split_id=split_id,
                actor=actor,
                role=role,
                data_classes=requested,
                gate=split.registered_gate,
                at=at,
                receipt_sha256=str(receipt_sha),
            )
        if receipt_sha not in self._trusted_receipts:
            self._deny(
                message="gate receipt digest is not trusted by the protected gate context",
                split_id=split_id,
                actor=actor,
                role=role,
                data_classes=requested,
                gate=split.registered_gate,
                at=at,
                receipt_sha256=str(receipt_sha),
            )
        if receipt.get("splitId") != split_id or receipt.get("gate") != split.registered_gate:
            self._deny(
                message="gate receipt is not bound to the split's registered gate",
                split_id=split_id,
                actor=actor,
                role=role,
                data_classes=requested,
                gate=split.registered_gate,
                at=at,
                receipt_sha256=str(receipt_sha),
            )
        if receipt.get("decision") != "unblind":
            self._deny(
                message="gate receipt decision does not authorize unblinding",
                split_id=split_id,
                actor=actor,
                role=role,
                data_classes=requested,
                gate=split.registered_gate,
                at=at,
                receipt_sha256=str(receipt_sha),
            )
        if (
            receipt.get("custodian") != split.custodian
            or receipt.get("approvedBy") != split.independent_reviewer
        ):
            self._deny(
                message="gate receipt custodian or independent approver does not match registration",
                split_id=split_id,
                actor=actor,
                role=role,
                data_classes=requested,
                gate=split.registered_gate,
                at=at,
                receipt_sha256=str(receipt_sha),
            )
        approved_at = parse_utc(receipt.get("approvedAt"), "gateReceipt.approvedAt", RegistryInputError)
        expires_at = parse_utc(receipt.get("expiresAt"), "gateReceipt.expiresAt", RegistryInputError)
        requested_at = parse_utc(at, "at", RegistryInputError)
        if requested_at < approved_at:
            self._deny(
                message="gate receipt is not active yet",
                split_id=split_id,
                actor=actor,
                role=role,
                data_classes=requested,
                gate=split.registered_gate,
                at=at,
                receipt_sha256=str(receipt_sha),
            )
        if requested_at > expires_at:
            self._deny(
                message="gate receipt has expired",
                split_id=split_id,
                actor=actor,
                role=role,
                data_classes=requested,
                gate=split.registered_gate,
                at=at,
                receipt_sha256=str(receipt_sha),
            )
        event = self._record(
            split_id=split_id,
            actor=actor,
            role=role,
            data_classes=requested,
            gate=split.registered_gate,
            decision="granted",
            reason="registered unblinding gate authorized protected scorer access",
            occurred_at=at,
            receipt_sha256=str(receipt_sha),
        )
        return {
            "dataClasses": list(requested),
            "gate": split.registered_gate,
            "grantId": event["eventHash"],
            "schemaVersion": 1,
            "splitId": split_id,
        }

    def record_behavior_change(
        self,
        *,
        split_id: str,
        actor: str,
        reason: str,
        at: str,
    ) -> None:
        split = self.registry.split(split_id)
        if not split.protected:
            raise RegistryInputError(f"{split_id} is not a protected split")
        if actor != split.custodian:
            raise RegistryInputError("only the registered custodian can record demotion")
        reason = require_string(reason, "reason", RegistryInputError)
        parse_utc(at, "at", RegistryInputError)
        was_unblinded = any(
            event.get("splitId") == split_id and event.get("decision") == "granted"
            for event in self._ledger.verify()
        )
        if not was_unblinded:
            raise RegistryInputError("cannot demote a protected set before recorded unblinding")
        prior_purpose = split.purpose
        split.purpose = "diagnostic"
        if prior_purpose == "confirmation_reserve":
            self.registry.fresh_reserve_required = True
        self._record(
            split_id=split_id,
            actor=actor,
            role="custodian",
            data_classes=(),
            gate=split.registered_gate,
            decision="demoted",
            reason=reason,
            occurred_at=at,
            receipt_sha256=None,
        )
