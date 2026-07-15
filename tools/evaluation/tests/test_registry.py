from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from codecrow_evaluation.registry import (
    AccessController,
    AccessDenied,
    LedgerIntegrityError,
    RegistryInputError,
    SplitRegistry,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64


def _public_split(split_id: str, purpose: str, digest: str) -> dict:
    return {
        "caseCount": 2,
        "contentSha256": digest,
        "labelsVisible": True,
        "purpose": purpose,
        "sourceKind": "public",
        "splitId": split_id,
    }


def _protected_split(
    split_id: str,
    purpose: str,
    *,
    identity: str,
    label: str,
    outcome: str,
    gate: str,
) -> dict:
    return {
        "caseCount": 17,
        "custodian": "evaluation-custodian",
        "featureCoverage": [
            "clean_control",
            "collision",
            "cross_file",
            "hard_negative",
            "large_pr",
            "multilanguage",
            "positive",
            "rename",
        ],
        "featureCoverageAttestationSha256": SHA_F,
        "identitiesCommitmentSha256": identity,
        "independentReviewer": "evaluation-reviewer",
        "labelsCommitmentSha256": label,
        "outcomesCommitmentSha256": outcome,
        "purpose": purpose,
        "registeredGate": gate,
        "sealedAt": "2026-07-15T00:00:00Z",
        "sourceKind": "internal_blinded",
        "splitId": split_id,
    }


def _registry_mapping(*, primary_identity: str = SHA_C, reserve_identity: str = SHA_D) -> dict:
    splits = [
        _public_split("development-v1", "development", SHA_A),
        _public_split("calibration-v1", "calibration", SHA_B),
        _protected_split(
            "primary-v1",
            "primary_heldout",
            identity=primary_identity,
            label=SHA_E,
            outcome=SHA_F,
            gate="P5-06",
        ),
        _protected_split(
            "reserve-v1",
            "confirmation_reserve",
            identity=reserve_identity,
            label="1" * 64,
            outcome="2" * 64,
            gate="POST-P5-08",
        ),
    ]
    return {
        "schemaVersion": 1,
        "registryId": "p0-05-registry-v1",
        "registryVersion": "2026-07-15.1",
        "programOwner": "Codex /root",
        "splits": splits,
        "disjointnessAttestation": {
            "coversSplitIds": sorted(item["splitId"] for item in splits),
            "custodian": "evaluation-custodian",
            "independentReviewer": "evaluation-reviewer",
            "membershipDigestSha256": "3" * 64,
            "signedAt": "2026-07-15T00:00:00Z",
        },
    }


def _receipt(split_id: str, gate: str) -> dict:
    payload = {
        "schemaVersion": 1,
        "splitId": split_id,
        "gate": gate,
        "decision": "unblind",
        "custodian": "evaluation-custodian",
        "approvedBy": "evaluation-reviewer",
        "approvedAt": "2026-08-01T00:00:00Z",
        "expiresAt": "2026-08-02T00:00:00Z",
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["receiptSha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def test_registry_requires_disjoint_splits_independent_custody_and_complete_blinded_features() -> None:
    mapping = _registry_mapping()
    registry = SplitRegistry.from_mapping(mapping)

    assert registry.split("primary-v1").purpose == "primary_heldout"
    assert registry.split("reserve-v1").registered_gate == "POST-P5-08"

    duplicate = _registry_mapping(reserve_identity=SHA_C)
    with pytest.raises(RegistryInputError, match="commitment.*unique"):
        SplitRegistry.from_mapping(duplicate)

    same_custodian = _registry_mapping()
    same_custodian["splits"][2]["custodian"] = "Codex /root"
    with pytest.raises(RegistryInputError, match="independent custodian"):
        SplitRegistry.from_mapping(same_custodian)

    missing_feature = _registry_mapping()
    missing_feature["splits"][2]["featureCoverage"].remove("rename")
    with pytest.raises(RegistryInputError, match="featureCoverage"):
        SplitRegistry.from_mapping(missing_feature)


def test_protected_values_cannot_change_planning_pruning_or_policy_context() -> None:
    first = SplitRegistry.from_mapping(_registry_mapping())
    second = SplitRegistry.from_mapping(
        _registry_mapping(primary_identity="4" * 64, reserve_identity="5" * 64)
    )

    assert first.policy_context() == second.policy_context()
    serialized = json.dumps(first.policy_context(), sort_keys=True)
    for secret_value in (SHA_C, SHA_D, SHA_E, SHA_F, "primary-v1", "reserve-v1"):
        assert secret_value not in serialized
    assert [item["purpose"] for item in first.policy_context()["splits"]] == [
        "calibration",
        "development",
    ]


def test_access_fails_closed_before_gate_and_for_behavior_affecting_roles(tmp_path: Path) -> None:
    registry = SplitRegistry.from_mapping(_registry_mapping())
    ledger = tmp_path / "access-ledger.jsonl"
    controller = AccessController(registry, ledger)

    with pytest.raises(AccessDenied, match="registered unblinding gate"):
        controller.authorize(
            split_id="primary-v1",
            actor="evaluation-runner",
            role="scorer",
            data_classes=("identities", "labels", "outcomes"),
            gate_receipt=None,
            at="2026-08-01T01:00:00Z",
        )

    with pytest.raises(AccessDenied, match="behavior-affecting role"):
        controller.authorize(
            split_id="primary-v1",
            actor="implementation-agent",
            role="implementation",
            data_classes=("identities", "labels", "outcomes"),
            gate_receipt=_receipt("primary-v1", "P5-06"),
            at="2026-08-01T01:00:00Z",
        )

    events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [event["decision"] for event in events] == ["denied", "denied"]
    assert all("commitment" not in json.dumps(event).lower() for event in events)


def test_unblinding_grant_is_gate_bound_tamper_evident_and_restart_safe(tmp_path: Path) -> None:
    registry = SplitRegistry.from_mapping(_registry_mapping())
    ledger = tmp_path / "access-ledger.jsonl"
    receipt = _receipt("primary-v1", "P5-06")
    controller = AccessController(
        registry,
        ledger,
        trusted_receipt_sha256={receipt["receiptSha256"]},
    )

    grant = controller.authorize(
        split_id="primary-v1",
        actor="evaluation-runner",
        role="scorer",
        data_classes=("identities", "labels", "outcomes"),
        gate_receipt=receipt,
        at="2026-08-01T01:00:00Z",
    )
    assert grant["splitId"] == "primary-v1"
    assert grant["gate"] == "P5-06"
    assert grant["dataClasses"] == ["identities", "labels", "outcomes"]
    assert "labelsCommitmentSha256" not in grant

    restarted = AccessController(registry, ledger)
    assert restarted.verify_ledger() == 1

    original = ledger.read_text(encoding="utf-8")
    ledger.write_text(original.replace("evaluation-runner", "policy-runner"), encoding="utf-8")
    with pytest.raises(LedgerIntegrityError, match="eventHash"):
        AccessController(registry, ledger)


def test_behavior_change_after_unblind_demotes_set_and_requires_fresh_reserve(tmp_path: Path) -> None:
    registry = SplitRegistry.from_mapping(_registry_mapping())
    ledger = tmp_path / "access-ledger.jsonl"
    receipt = _receipt("reserve-v1", "POST-P5-08")
    controller = AccessController(
        registry,
        ledger,
        trusted_receipt_sha256={receipt["receiptSha256"]},
    )
    controller.authorize(
        split_id="reserve-v1",
        actor="evaluation-runner",
        role="scorer",
        data_classes=("identities", "labels", "outcomes"),
        gate_receipt=receipt,
        at="2026-08-01T01:00:00Z",
    )

    controller.record_behavior_change(
        split_id="reserve-v1",
        actor="evaluation-custodian",
        reason="failed confirmation changed policy-v2",
        at="2026-08-01T02:00:00Z",
    )

    assert registry.split("reserve-v1").purpose == "diagnostic"
    assert registry.fresh_reserve_required is True
    with pytest.raises(RegistryInputError, match="fresh untouched confirmation reserve"):
        registry.select_acceptance_split("confirmation_reserve")

    restarted_registry = SplitRegistry.from_mapping(_registry_mapping())
    AccessController(restarted_registry, ledger)
    assert restarted_registry.split("reserve-v1").purpose == "diagnostic"
    assert restarted_registry.fresh_reserve_required is True


def test_gate_receipt_is_bound_to_split_gate_approver_and_expiry(tmp_path: Path) -> None:
    registry = SplitRegistry.from_mapping(_registry_mapping())
    wrong_gate = _receipt("primary-v1", "POST-P5-08")
    expired = _receipt("primary-v1", "P5-06")
    controller = AccessController(
        registry,
        tmp_path / "access-ledger.jsonl",
        trusted_receipt_sha256={
            wrong_gate["receiptSha256"],
            expired["receiptSha256"],
        },
    )
    with pytest.raises(AccessDenied, match="registered gate"):
        controller.authorize(
            split_id="primary-v1",
            actor="evaluation-runner",
            role="scorer",
            data_classes=("labels",),
            gate_receipt=wrong_gate,
            at="2026-08-01T01:00:00Z",
        )

    with pytest.raises(AccessDenied, match="expired"):
        controller.authorize(
            split_id="primary-v1",
            actor="evaluation-runner",
            role="scorer",
            data_classes=("labels",),
            gate_receipt=expired,
            at="2026-08-03T01:00:00Z",
        )


def test_self_hashed_but_untrusted_gate_receipt_is_denied(tmp_path: Path) -> None:
    registry = SplitRegistry.from_mapping(_registry_mapping())
    controller = AccessController(registry, tmp_path / "access-ledger.jsonl")

    with pytest.raises(AccessDenied, match="not trusted"):
        controller.authorize(
            split_id="primary-v1",
            actor="evaluation-runner",
            role="scorer",
            data_classes=("labels",),
            gate_receipt=_receipt("primary-v1", "P5-06"),
            at="2026-08-01T01:00:00Z",
        )


def test_ledger_head_detects_valid_prefix_truncation(tmp_path: Path) -> None:
    registry = SplitRegistry.from_mapping(_registry_mapping())
    ledger = tmp_path / "access-ledger.jsonl"
    receipt = _receipt("primary-v1", "P5-06")
    controller = AccessController(
        registry,
        ledger,
        trusted_receipt_sha256={receipt["receiptSha256"]},
    )
    for actor in ("evaluation-runner-a", "evaluation-runner-b"):
        controller.authorize(
            split_id="primary-v1",
            actor=actor,
            role="scorer",
            data_classes=("labels",),
            gate_receipt=receipt,
            at="2026-08-01T01:00:00Z",
        )

    lines = ledger.read_text(encoding="utf-8").splitlines(keepends=True)
    ledger.write_text(lines[0], encoding="utf-8")
    with pytest.raises(LedgerIntegrityError, match="ledger head"):
        AccessController(registry, ledger)
