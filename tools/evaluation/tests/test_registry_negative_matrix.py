from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

import codecrow_evaluation.registry as registry_module
from codecrow_evaluation.registry import (
    AccessController,
    AccessDenied,
    LedgerIntegrityError,
    RegistryInputError,
    SplitRegistry,
)


FEATURES = [
    "clean_control",
    "collision",
    "cross_file",
    "hard_negative",
    "large_pr",
    "multilanguage",
    "positive",
    "rename",
]


def _public(split_id: str, purpose: str, digit: str) -> dict:
    return {
        "splitId": split_id,
        "purpose": purpose,
        "sourceKind": "public",
        "caseCount": 1,
        "contentSha256": digit * 64,
        "labelsVisible": True,
    }


def _protected(split_id: str, purpose: str, digits: tuple[str, str, str], gate: str) -> dict:
    return {
        "splitId": split_id,
        "purpose": purpose,
        "sourceKind": "internal_blinded",
        "caseCount": 1,
        "identitiesCommitmentSha256": digits[0] * 64,
        "labelsCommitmentSha256": digits[1] * 64,
        "outcomesCommitmentSha256": digits[2] * 64,
        "registeredGate": gate,
        "custodian": "custodian",
        "independentReviewer": "reviewer",
        "sealedAt": "2026-07-15T00:00:00Z",
        "featureCoverage": FEATURES,
        "featureCoverageAttestationSha256": "f" * 64,
    }


def _mapping() -> dict:
    splits = [
        _public("dev", "development", "a"),
        _public("cal", "calibration", "b"),
        _protected("primary", "primary_heldout", ("c", "d", "e"), "P5-06"),
        _protected("reserve", "confirmation_reserve", ("1", "2", "3"), "POST-P5-08"),
    ]
    return {
        "schemaVersion": 1,
        "registryId": "registry-v1",
        "registryVersion": "v1",
        "programOwner": "owner",
        "splits": splits,
        "disjointnessAttestation": {
            "coversSplitIds": sorted(item["splitId"] for item in splits),
            "custodian": "custodian",
            "independentReviewer": "reviewer",
            "membershipDigestSha256": "4" * 64,
            "signedAt": "2026-07-15T00:00:00Z",
        },
    }


def _receipt(
    *,
    split_id: str = "primary",
    gate: str = "P5-06",
    decision: str = "unblind",
    custodian: str = "custodian",
    approved_by: str = "reviewer",
) -> dict:
    value = {
        "schemaVersion": 1,
        "splitId": split_id,
        "gate": gate,
        "decision": decision,
        "custodian": custodian,
        "approvedBy": approved_by,
        "approvedAt": "2026-08-01T00:00:00Z",
        "expiresAt": "2026-08-02T00:00:00Z",
    }
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    value["receiptSha256"] = hashlib.sha256(raw).hexdigest()
    return value


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("schema", "schemaVersion"),
        ("splits_type", "splits"),
        ("splits_empty", "splits"),
        ("case_count", "caseCount"),
        ("duplicate_id", "duplicate splitId"),
        ("public_purpose", "public split purpose"),
        ("labels_hidden", "labelsVisible"),
        ("protected_purpose", "blinded split purpose"),
        ("sealed_no_z", "ending in Z"),
        ("sealed_bad_date", "valid RFC3339"),
        ("source_kind", "sourceKind"),
        ("missing_purpose", "missing development"),
        ("attestation_coverage", "coversSplitIds"),
        ("attestation_custody", "independent custodian"),
    ],
)
def test_registry_negative_matrix(mutation: str, message: str) -> None:
    value = _mapping()
    if mutation == "schema":
        value["schemaVersion"] = 2
    elif mutation == "splits_type":
        value["splits"] = {}
    elif mutation == "splits_empty":
        value["splits"] = []
    elif mutation == "case_count":
        value["splits"][0]["caseCount"] = True
    elif mutation == "duplicate_id":
        value["splits"][1]["splitId"] = "dev"
    elif mutation == "public_purpose":
        value["splits"][0]["purpose"] = "primary_heldout"
    elif mutation == "labels_hidden":
        value["splits"][0]["labelsVisible"] = False
    elif mutation == "protected_purpose":
        value["splits"][2]["purpose"] = "diagnostic"
    elif mutation == "sealed_no_z":
        value["splits"][2]["sealedAt"] = "2026-07-15T00:00:00"
    elif mutation == "sealed_bad_date":
        value["splits"][2]["sealedAt"] = "not-a-dateZ"
    elif mutation == "source_kind":
        value["splits"][0]["sourceKind"] = "secret"
    elif mutation == "missing_purpose":
        value["splits"][0]["purpose"] = "diagnostic"
    elif mutation == "attestation_coverage":
        value["disjointnessAttestation"]["coversSplitIds"] = []
    elif mutation == "attestation_custody":
        value["disjointnessAttestation"]["custodian"] = "owner"

    with pytest.raises(RegistryInputError, match=message):
        SplitRegistry.from_mapping(value)


def test_unknown_and_unavailable_split_selection() -> None:
    registry = SplitRegistry.from_mapping(_mapping())
    assert registry.select_acceptance_split("primary_heldout").split_id == "primary"
    with pytest.raises(RegistryInputError, match="unknown splitId"):
        registry.split("missing")
    with pytest.raises(RegistryInputError, match="no eligible"):
        registry.select_acceptance_split("diagnostic")


def test_public_access_and_protected_access_denial_matrix(tmp_path: Path) -> None:
    registry = SplitRegistry.from_mapping(_mapping())
    ledger = tmp_path / "ledger.jsonl"
    controller = AccessController(registry, ledger)
    grant = controller.authorize(
        split_id="dev",
        actor="developer",
        role="planning",
        data_classes=("labels",),
        gate_receipt=None,
        at="2026-08-01T01:00:00Z",
    )
    assert grant["gate"] is None

    with pytest.raises(RegistryInputError, match="dataClasses"):
        controller.authorize(
            split_id="primary",
            actor="runner",
            role="scorer",
            data_classes=("labels", "labels"),
            gate_receipt=None,
            at="2026-08-01T01:00:00Z",
        )
    with pytest.raises(AccessDenied, match="not authorized"):
        controller.authorize(
            split_id="primary",
            actor="runner",
            role="custodian",
            data_classes=("labels",),
            gate_receipt=None,
            at="2026-08-01T01:00:00Z",
        )


@pytest.mark.parametrize(
    ("mutation", "message", "at"),
    [
        ("digest_shape", "digest is invalid", "2026-08-01T01:00:00Z"),
        ("digest_mismatch", "does not match", "2026-08-01T01:00:00Z"),
        ("decision", "does not authorize", "2026-08-01T01:00:00Z"),
        ("custodian", "custodian", "2026-08-01T01:00:00Z"),
        ("too_early", "not active", "2026-07-31T23:00:00Z"),
    ],
)
def test_gate_receipt_denial_matrix(
    tmp_path: Path,
    mutation: str,
    message: str,
    at: str,
) -> None:
    registry = SplitRegistry.from_mapping(_mapping())
    if mutation == "decision":
        receipt = _receipt(decision="deny")
    elif mutation == "custodian":
        receipt = _receipt(custodian="other")
    else:
        receipt = _receipt()
    trusted = {receipt["receiptSha256"]}
    if mutation == "digest_shape":
        receipt["receiptSha256"] = "bad"
        trusted = set()
    elif mutation == "digest_mismatch":
        receipt["approvedBy"] = "changed"
    controller = AccessController(
        registry,
        tmp_path / "ledger.jsonl",
        trusted_receipt_sha256=trusted,
    )
    with pytest.raises(AccessDenied, match=message):
        controller.authorize(
            split_id="primary",
            actor="runner",
            role="scorer",
            data_classes=("labels",),
            gate_receipt=receipt,
            at=at,
        )


def test_behavior_change_preconditions_and_primary_restart_replay(tmp_path: Path) -> None:
    registry = SplitRegistry.from_mapping(_mapping())
    ledger = tmp_path / "ledger.jsonl"
    receipt = _receipt()
    controller = AccessController(
        registry,
        ledger,
        trusted_receipt_sha256={receipt["receiptSha256"]},
    )
    with pytest.raises(RegistryInputError, match="not a protected split"):
        controller.record_behavior_change(
            split_id="dev", actor="custodian", reason="x", at="2026-08-01T01:00:00Z"
        )
    with pytest.raises(RegistryInputError, match="registered custodian"):
        controller.record_behavior_change(
            split_id="primary", actor="other", reason="x", at="2026-08-01T01:00:00Z"
        )
    with pytest.raises(RegistryInputError, match="before recorded unblinding"):
        controller.record_behavior_change(
            split_id="primary", actor="custodian", reason="x", at="2026-08-01T01:00:00Z"
        )
    controller.authorize(
        split_id="primary",
        actor="runner",
        role="scorer",
        data_classes=("labels",),
        gate_receipt=receipt,
        at="2026-08-01T01:00:00Z",
    )
    controller.record_behavior_change(
        split_id="primary",
        actor="custodian",
        reason="policy changed",
        at="2026-08-01T02:00:00Z",
    )
    controller.authorize(
        split_id="dev",
        actor="developer",
        role="planning",
        data_classes=("labels",),
        gate_receipt=None,
        at="2026-08-01T03:00:00Z",
    )
    restarted = SplitRegistry.from_mapping(_mapping())
    AccessController(restarted, ledger)
    assert restarted.split("primary").purpose == "diagnostic"
    assert restarted.fresh_reserve_required is False


@pytest.mark.parametrize(
    ("contents", "head_contents", "message"),
    [
        (b"\xff", None, "UTF-8"),
        (b"{\n", None, "valid JSON"),
        (b"[]\n", None, "must be an object"),
        (b'{"sequence":2}\n', None, "invalid sequence"),
        (
            b'{"sequence":1,"previousEventHash":"1"}\n',
            None,
            "previousEventHash",
        ),
    ],
)
def test_ledger_corruption_matrix(
    tmp_path: Path,
    contents: bytes,
    head_contents: bytes | None,
    message: str,
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_bytes(contents)
    if head_contents is not None:
        (tmp_path / "ledger.jsonl.head.json").write_bytes(head_contents)
    with pytest.raises(LedgerIntegrityError, match=message):
        AccessController(SplitRegistry.from_mapping(_mapping()), ledger)


def test_ledger_missing_malformed_and_orphan_heads_fail_closed(tmp_path: Path) -> None:
    registry = SplitRegistry.from_mapping(_mapping())
    ledger = tmp_path / "ledger.jsonl"
    controller = AccessController(registry, ledger)
    controller.authorize(
        split_id="dev",
        actor="developer",
        role="planning",
        data_classes=("labels",),
        gate_receipt=None,
        at="2026-08-01T01:00:00Z",
    )
    head = tmp_path / "ledger.jsonl.head.json"
    saved = head.read_bytes()
    head.unlink()
    with pytest.raises(LedgerIntegrityError, match="head is missing"):
        AccessController(registry, ledger)
    head.write_text("{", encoding="utf-8")
    with pytest.raises(LedgerIntegrityError, match="head is not valid"):
        AccessController(registry, ledger)
    ledger.unlink()
    head.write_bytes(saved)
    with pytest.raises(LedgerIntegrityError, match="head exists"):
        AccessController(registry, ledger)


def test_head_temp_file_is_removed_when_atomic_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SplitRegistry.from_mapping(_mapping())
    ledger = tmp_path / "ledger.jsonl"

    def fail_replace(*args, **kwargs):
        raise OSError("injected replace failure")

    monkeypatch.setattr(registry_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        AccessController(registry, ledger).authorize(
            split_id="dev",
            actor="developer",
            role="planning",
            data_classes=("labels",),
            gate_receipt=None,
            at="2026-08-01T01:00:00Z",
        )
    assert not list(tmp_path.glob(".ledger.jsonl.head.json.*.tmp"))
