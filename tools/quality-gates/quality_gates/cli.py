"""Command-line entry points for deterministic quality evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from .baseline import (
    aggregate_normalized_reports,
    capture_coverage_baseline,
    capture_source_snapshot,
    verify_source_snapshot,
)
from .changed_coverage import (
    GateInputError,
    _evidence_reference,
    _junit_counts,
    _matching_exclusion,
    _read_approved_runtime,
    _read_trusted_repository_file,
    _validate_exclusions,
    _validate_zero_live_ledger,
    evaluate_gate,
)
from .correctness_policy import load_correctness_policy
from .git_changes import (
    _read_contract_file,
    load_comparison_base_attestation,
    resolve_git_changes,
)
from .mutation_gate import run_mutation_profile
from .normalized_reports import (
    normalize_coveragepy_json,
    normalize_jacoco_aggregate_xml,
    normalize_jacoco_xml,
)
from .source_inventory import (
    load_and_resolve_source_inventory,
    source_inventory_digest,
    validate_source_inventory,
)
from .trust_bundle import create_trust_bundle, verify_trust_bundle


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise GateInputError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


_MAX_JSON_INPUT_BYTES = 64 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _read_json_bytes(
    path: Path,
    *,
    repository_root: Path | None = None,
    field: str = "JSON input",
) -> bytes:
    """Read bounded contract bytes through a stable no-follow descriptor walk."""

    return _read_contract_file(
        path,
        repository_root=repository_root,
        field=field,
        size_limit=_MAX_JSON_INPUT_BYTES,
    )


def _decode_json(raw: bytes, *, path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                GateInputError(f"invalid JSON constant: {constant}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateInputError(f"cannot read JSON input: {path}") from error
    if not isinstance(value, Mapping):
        raise GateInputError(f"JSON input must be an object: {path}")
    return value


def _read_json(
    path: Path, *, repository_root: Path | None = None
) -> Mapping[str, Any]:
    return _decode_json(
        _read_json_bytes(path, repository_root=repository_root),
        path=path,
    )


def _read_bound_json(
    path: Path,
    *,
    repository_root: Path,
    field: str,
) -> tuple[Mapping[str, Any], str]:
    raw = _read_json_bytes(path, repository_root=repository_root, field=field)
    return _decode_json(raw, path=path), hashlib.sha256(raw).hexdigest()


def _canonical_json_sha256(value: Mapping[str, Any], *, field: str) -> str:
    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise GateInputError(f"{field} cannot be canonically hashed") from error
    return hashlib.sha256(raw).hexdigest()


def _revalidate_bound_inputs(
    bindings: Sequence[tuple[Path, str, str]], *, repository_root: Path
) -> None:
    for path, expected_sha256, field in bindings:
        raw = _read_json_bytes(
            path,
            repository_root=repository_root,
            field=field,
        )
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise GateInputError(f"{field} changed during gate evaluation")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise GateInputError(f"refusing symlink output: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _domain_policy(
    path: Path, *, repository_root: Path | None = None
) -> set[str]:
    policy = _read_json(path, repository_root=repository_root)
    domains = policy.get("domains")
    if (
        policy.get("schemaVersion") != 1
        or not isinstance(domains, list)
        or not domains
        or any(not isinstance(domain, str) or not domain for domain in domains)
        or domains != sorted(set(domains))
    ):
        raise GateInputError("coverage domain policy is malformed")
    return set(domains)


def _resolved_source_inventory(path: Path, *, repository_root: Path) -> Mapping[str, Any]:
    return load_and_resolve_source_inventory(
        path,
        repository_root=repository_root,
    )


def _java_module_policy(
    path: Path, *, repository_root: Path
) -> dict[str, tuple[str, Path]]:
    policy = _read_json(path, repository_root=repository_root)
    modules = policy.get("modules")
    if policy.get("schemaVersion") != 1 or not isinstance(modules, list) or not modules:
        raise GateInputError("Java module policy is malformed")
    result: dict[str, tuple[str, Path]] = {}
    groups: set[str] = set()
    for entry in modules:
        if not isinstance(entry, Mapping):
            raise GateInputError("Java module policy entry is malformed")
        module = entry.get("module")
        group = entry.get("reportGroup")
        source_root = entry.get("sourceRoot")
        if (
            not isinstance(module, str)
            or not module
            or module in result
            or not isinstance(group, str)
            or not group
            or group in groups
            or not isinstance(source_root, str)
            or not source_root
            or "\\" in source_root
            or Path(source_root).is_absolute()
            or ".." in Path(source_root).parts
        ):
            raise GateInputError("Java module policy entry is malformed")
        groups.add(group)
        result[module] = (group, repository_root / source_root)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codecrow-quality-gate")
    subcommands = parser.add_subparsers(dest="command", required=True)

    evaluate = subcommands.add_parser("evaluate")
    evaluate.add_argument("--changes", type=Path, required=True)
    evaluate.add_argument("--report", type=Path, action="append", required=True)
    evaluate.add_argument("--baseline", type=Path, required=True)
    evaluate.add_argument("--exclusions", type=Path, required=True)
    evaluate.add_argument("--as-of", required=True)
    evaluate.add_argument("--repository-root", type=Path, required=True)
    evaluate.add_argument("--source-inventory-policy", type=Path, required=True)
    evaluate.add_argument("--pinned-source-inventory", type=Path, required=True)
    evaluate.add_argument(
        "--pinned-source-inventory-artifact-sha256", required=True
    )
    evaluate.add_argument("--correctness-policy", type=Path, required=True)
    evaluate.add_argument("--base-attestation", type=Path, required=True)
    evaluate.add_argument("--base-attestation-sha256", required=True)
    evaluate.add_argument("--baseline-manifest-sha256", required=True)
    evaluate.add_argument("--output", type=Path, required=True)

    capture = subcommands.add_parser("capture-baseline")
    capture.add_argument("--report", type=Path, action="append", required=True)
    capture.add_argument("--comparison-base", required=True)
    capture.add_argument("--source-snapshot-sha256", required=True)
    capture.add_argument("--domain-policy", type=Path, required=True)
    capture.add_argument("--repository-root", type=Path, required=True)
    capture.add_argument("--source-inventory-policy", type=Path, required=True)
    capture.add_argument("--output", type=Path, required=True)

    aggregate = subcommands.add_parser("aggregate")
    aggregate.add_argument("--report", type=Path, action="append", required=True)
    aggregate.add_argument("--language", choices=("java", "python"), required=True)
    aggregate.add_argument("--output", type=Path, required=True)

    jacoco = subcommands.add_parser("normalize-jacoco")
    jacoco.add_argument("--input", type=Path, required=True)
    jacoco.add_argument("--module", required=True)
    jacoco.add_argument("--source-root", type=Path, action="append", required=True)
    jacoco.add_argument("--repository-root", type=Path, required=True)
    jacoco.add_argument("--tool-version", required=True)
    jacoco.add_argument("--source-inventory-sha256", required=True)
    jacoco.add_argument("--output", type=Path, required=True)

    coveragepy = subcommands.add_parser("normalize-coveragepy")
    coveragepy.add_argument("--input", type=Path, required=True)
    coveragepy.add_argument("--module", required=True)
    coveragepy.add_argument("--source-prefix", required=True)
    coveragepy.add_argument("--repository-root", type=Path, required=True)
    coveragepy.add_argument("--source-inventory-sha256", required=True)
    coveragepy.add_argument("--output", type=Path, required=True)

    jacoco_aggregate = subcommands.add_parser("normalize-jacoco-aggregate")
    jacoco_aggregate.add_argument("--input", type=Path, required=True)
    jacoco_aggregate.add_argument("--module-policy", type=Path, required=True)
    jacoco_aggregate.add_argument("--repository-root", type=Path, required=True)
    jacoco_aggregate.add_argument("--tool-version", required=True)
    jacoco_aggregate.add_argument("--source-inventory-sha256", required=True)
    jacoco_aggregate.add_argument("--aggregate-output", type=Path, required=True)
    jacoco_aggregate.add_argument("--module-output-root", type=Path, required=True)

    snapshot = subcommands.add_parser("capture-source-snapshot")
    snapshot.add_argument("--report", type=Path, action="append", required=True)
    snapshot.add_argument("--repository-root", type=Path, required=True)
    snapshot.add_argument("--source-inventory-policy", type=Path, required=True)
    snapshot.add_argument("--output", type=Path, required=True)

    verify_snapshot = subcommands.add_parser("verify-source-snapshot")
    verify_snapshot.add_argument("--snapshot", type=Path, required=True)
    verify_snapshot.add_argument("--snapshot-sha256", required=True)
    verify_snapshot.add_argument("--repository-root", type=Path, required=True)
    verify_snapshot.add_argument("--source-inventory-policy", type=Path, required=True)

    changes = subcommands.add_parser("resolve-changes")
    changes.add_argument("--repository-root", type=Path, required=True)
    base_source = changes.add_mutually_exclusive_group(required=True)
    base_source.add_argument("--baseline-manifest", type=Path)
    base_source.add_argument("--base-attestation", type=Path)
    changes.add_argument("--baseline-manifest-sha256", required=True)
    changes.add_argument("--base-attestation-sha256")
    changes.add_argument("--include-worktree", action="store_true")
    changes.add_argument("--correctness-policy", type=Path)
    changes.add_argument("--output", type=Path, required=True)

    mutations = subcommands.add_parser("run-mutations")
    mutations.add_argument("--repository-root", type=Path, required=True)
    mutations.add_argument("--profile", type=Path, required=True)
    mutations.add_argument("--artifact-root", type=Path, required=True)
    mutations.add_argument("--python-runtime", type=Path, required=True)
    mutations.add_argument("--offline-runner", type=Path, required=True)
    mutations.add_argument("--offline-runner-sha256", required=True)
    mutations.add_argument("--output", type=Path, required=True)

    inventory = subcommands.add_parser("resolve-source-inventory")
    inventory.add_argument("--policy", type=Path, required=True)
    inventory.add_argument("--repository-root", type=Path, required=True)
    inventory.add_argument("--output", type=Path, required=True)

    trust = subcommands.add_parser("verify-trust-bundle")
    trust.add_argument("--bundle", type=Path, required=True)
    trust.add_argument("--bundle-sha256", required=True)
    trust.add_argument("--repository-root", type=Path, required=True)

    capture_trust = subcommands.add_parser("capture-trust-bundle")
    capture_trust.add_argument("--repository-root", type=Path, required=True)
    capture_trust.add_argument("--output", type=Path, required=True)

    capture_receipts = subcommands.add_parser("capture-exclusion-receipts")
    capture_receipts.add_argument("--changes", type=Path, required=True)
    capture_receipts.add_argument("--exclusions", type=Path, required=True)
    capture_receipts.add_argument("--junit", type=Path)
    capture_receipts.add_argument("--ledger", type=Path)
    capture_receipts.add_argument(
        "--selector-evidence",
        action="append",
        nargs=3,
        metavar=("SELECTOR", "JUNIT", "LEDGER"),
    )
    capture_receipts.add_argument("--as-of", required=True)
    capture_receipts.add_argument("--repository-root", type=Path, required=True)
    capture_receipts.add_argument("--output", type=Path, required=True)
    return parser


def _evaluate(arguments: argparse.Namespace) -> int:
    if (
        not _SHA256.fullmatch(arguments.pinned_source_inventory_artifact_sha256)
        or not _SHA256.fullmatch(arguments.base_attestation_sha256)
        or not _SHA256.fullmatch(arguments.baseline_manifest_sha256)
    ):
        raise GateInputError("evaluate provenance digest is malformed")

    pinned_source_inventory, pinned_inventory_artifact_sha256 = _read_bound_json(
        arguments.pinned_source_inventory,
        repository_root=arguments.repository_root,
        field="pinned pre-test source inventory",
    )
    if (
        pinned_inventory_artifact_sha256
        != arguments.pinned_source_inventory_artifact_sha256
    ):
        raise GateInputError("pinned pre-test source inventory artifact digest mismatch")
    validate_source_inventory(pinned_source_inventory)

    source_inventory = _resolved_source_inventory(
        arguments.source_inventory_policy,
        repository_root=arguments.repository_root,
    )
    if pinned_source_inventory != source_inventory:
        raise GateInputError("pinned pre-test source inventory is stale")
    source_inventory_sha256 = source_inventory_digest(source_inventory)

    _, source_policy_artifact_sha256 = _read_bound_json(
        arguments.source_inventory_policy,
        repository_root=arguments.repository_root,
        field="source inventory policy",
    )
    if source_policy_artifact_sha256 != source_inventory["policySha256"]:
        raise GateInputError("source inventory policy identity is inconsistent")

    correctness_policy, correctness_path, correctness_sha256 = (
        load_correctness_policy(
            arguments.correctness_policy,
            repository_root=arguments.repository_root,
        )
    )
    _, correctness_artifact_sha256 = _read_bound_json(
        arguments.correctness_policy,
        repository_root=arguments.repository_root,
        field="correctness policy",
    )
    if correctness_artifact_sha256 != correctness_sha256:
        raise GateInputError("correctness policy identity is inconsistent")

    base_contract = load_comparison_base_attestation(
        arguments.base_attestation,
        expected_attestation_sha256=arguments.base_attestation_sha256,
        expected_manifest_sha256=arguments.baseline_manifest_sha256,
        with_dirty_state=True,
        repository_root=arguments.repository_root,
    )
    if not isinstance(base_contract, tuple):
        raise GateInputError("comparison-base dirty state is missing")
    base, baseline_dirty_entries = base_contract
    _, attestation_artifact_sha256 = _read_bound_json(
        arguments.base_attestation,
        repository_root=arguments.repository_root,
        field="comparison-base attestation",
    )
    if attestation_artifact_sha256 != arguments.base_attestation_sha256:
        raise GateInputError("comparison-base attestation digest mismatch")

    provided_changes, changes_artifact_sha256 = _read_bound_json(
        arguments.changes,
        repository_root=arguments.repository_root,
        field="resolved change inventory",
    )
    reports_with_digests = [
        _read_bound_json(
            path,
            repository_root=arguments.repository_root,
            field=f"normalized coverage report {index}",
        )
        for index, path in enumerate(arguments.report)
    ]
    reports = [value for value, _ in reports_with_digests]
    report_artifact_sha256 = [digest for _, digest in reports_with_digests]
    baseline, baseline_artifact_sha256 = _read_bound_json(
        arguments.baseline,
        repository_root=arguments.repository_root,
        field="coverage baseline",
    )
    exclusions, exclusions_artifact_sha256 = _read_bound_json(
        arguments.exclusions,
        repository_root=arguments.repository_root,
        field="coverage exclusions",
    )

    bound_inputs: list[tuple[Path, str, str]] = [
        (
            arguments.pinned_source_inventory,
            pinned_inventory_artifact_sha256,
            "pinned pre-test source inventory",
        ),
        (
            arguments.source_inventory_policy,
            source_policy_artifact_sha256,
            "source inventory policy",
        ),
        (
            arguments.correctness_policy,
            correctness_artifact_sha256,
            "correctness policy",
        ),
        (
            arguments.base_attestation,
            attestation_artifact_sha256,
            "comparison-base attestation",
        ),
        (arguments.changes, changes_artifact_sha256, "resolved change inventory"),
        (arguments.baseline, baseline_artifact_sha256, "coverage baseline"),
        (arguments.exclusions, exclusions_artifact_sha256, "coverage exclusions"),
    ]
    bound_inputs.extend(
        (path, digest, f"normalized coverage report {index}")
        for index, (path, digest) in enumerate(
            zip(arguments.report, report_artifact_sha256, strict=True)
        )
    )

    def resolve_current_changes() -> Mapping[str, Any]:
        return resolve_git_changes(
            arguments.repository_root,
            base_commit=base,
            include_worktree=True,
            correctness_policy=correctness_policy,
            correctness_policy_path=correctness_path,
            correctness_policy_sha256=correctness_sha256,
            baseline_dirty_entries=baseline_dirty_entries,
        )

    if resolve_current_changes() != provided_changes:
        raise GateInputError("change inventory is stale")
    result = evaluate_gate(
        changes=provided_changes,
        reports=reports,
        baseline=baseline,
        exclusions=exclusions,
        as_of=arguments.as_of,
        repository_root=arguments.repository_root,
        source_inventory=source_inventory,
        correctness_policy=correctness_policy,
        correctness_policy_path=correctness_path,
        correctness_policy_sha256=correctness_sha256,
    )
    bound_inputs.extend(
        (
            arguments.repository_root / receipt["artifact"],
            receipt["sha256"],
            f"compensating receipt {index}",
        )
        for index, receipt in enumerate(result.compensating_receipts)
    )
    final_source_inventory = _resolved_source_inventory(
        arguments.source_inventory_policy,
        repository_root=arguments.repository_root,
    )
    if final_source_inventory != source_inventory:
        raise GateInputError("source inventory changed during gate evaluation")
    if resolve_current_changes() != provided_changes:
        raise GateInputError("change inventory changed during gate evaluation")
    _revalidate_bound_inputs(
        bound_inputs,
        repository_root=arguments.repository_root,
    )
    output = {
        "schemaVersion": 1,
        "passed": result.passed,
        "changedLines": result.changed_lines,
        "changedBranches": result.changed_branches,
        "failures": list(result.failures),
        "excludedFiles": list(result.excluded_files),
        "provenance": {
            "sourceInventorySha256": source_inventory_sha256,
            "sourceInventoryPolicySha256": source_policy_artifact_sha256,
            "pinnedSourceInventoryArtifactSha256": pinned_inventory_artifact_sha256,
            "changeInventorySha256": _canonical_json_sha256(
                provided_changes,
                field="change inventory",
            ),
            "changesArtifactSha256": changes_artifact_sha256,
            "baselineArtifactSha256": baseline_artifact_sha256,
            "exclusionsArtifactSha256": exclusions_artifact_sha256,
            "compensatingReceipts": list(result.compensating_receipts),
            "reportArtifactSha256": report_artifact_sha256,
            "correctnessPolicySha256": correctness_sha256,
            "baseAttestationSha256": arguments.base_attestation_sha256,
            "baselineManifestSha256": arguments.baseline_manifest_sha256,
        },
    }
    _write_json(arguments.output, output)
    return 0 if result.passed else 1


def _artifact_reference(
    path: Path, *, repository_root: Path, field: str
) -> tuple[str, bytes, str]:
    root = Path(os.path.abspath(repository_root))
    absolute = Path(os.path.abspath(path))
    try:
        relative = absolute.relative_to(root).as_posix()
    except ValueError as error:
        raise GateInputError(f"{field} must stay inside the repository") from error
    if not relative.startswith(".llm-handoff-artifacts/"):
        raise GateInputError(f"{field} must stay under .llm-handoff-artifacts")
    raw = _read_contract_file(
        absolute,
        repository_root=root,
        field=field,
        size_limit=_MAX_JSON_INPUT_BYTES,
    )
    return relative, raw, hashlib.sha256(raw).hexdigest()


def _capture_evidence_by_selector(
    arguments: argparse.Namespace,
    *,
    selectors: set[str],
    repository_root: Path,
) -> dict[str, tuple[str, str, str, str]]:
    selector_evidence = getattr(arguments, "selector_evidence", None)
    legacy_junit = getattr(arguments, "junit", None)
    legacy_ledger = getattr(arguments, "ledger", None)
    legacy_present = legacy_junit is not None or legacy_ledger is not None
    if selector_evidence and legacy_present:
        raise GateInputError(
            "--selector-evidence is mutually exclusive with --junit/--ledger"
        )
    if legacy_present:
        if legacy_junit is None or legacy_ledger is None:
            raise GateInputError("--junit and --ledger must be provided together")
        if len(selectors) != 1:
            raise GateInputError(
                "legacy --junit/--ledger requires exactly one distinct selector"
            )
        requested: Sequence[Sequence[object]] = (
            (next(iter(selectors)), legacy_junit, legacy_ledger),
        )
    else:
        if not selector_evidence:
            raise GateInputError(
                "one --selector-evidence tuple is required for every distinct selector"
            )
        requested = selector_evidence

    paths_by_selector: dict[str, tuple[Path, Path]] = {}
    for item in requested:
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            raise GateInputError("selector evidence tuple is malformed")
        selector, junit_value, ledger_value = item
        if not isinstance(selector, str) or not selector:
            raise GateInputError("selector evidence selector is malformed")
        if selector in paths_by_selector:
            raise GateInputError(f"duplicate selector evidence: {selector}")
        try:
            junit = Path(junit_value)
            ledger = Path(ledger_value)
        except TypeError as error:
            raise GateInputError("selector evidence paths are malformed") from error
        paths_by_selector[selector] = (junit, ledger)

    missing = sorted(selectors - paths_by_selector.keys())
    if missing:
        raise GateInputError(
            "selector evidence is missing required selectors: " + ", ".join(missing)
        )
    extra = sorted(paths_by_selector.keys() - selectors)
    if extra:
        raise GateInputError(
            "selector evidence contains unregistered selectors: " + ", ".join(extra)
        )

    evidence: dict[str, tuple[str, str, str, str]] = {}
    for selector in sorted(selectors):
        junit, ledger = paths_by_selector[selector]
        junit_path, junit_raw, junit_sha256 = _artifact_reference(
            junit,
            repository_root=repository_root,
            field=f"compensating JUnit artifact for {selector}",
        )
        ledger_path, ledger_raw, ledger_sha256 = _artifact_reference(
            ledger,
            repository_root=repository_root,
            field=f"compensating ledger artifact for {selector}",
        )
        _junit_counts(junit_raw, selector=selector)
        _validate_zero_live_ledger(ledger_raw)
        evidence[selector] = (
            junit_path,
            junit_sha256,
            ledger_path,
            ledger_sha256,
        )
    return evidence


def _capture_exclusion_receipts(arguments: argparse.Namespace) -> int:
    repository_root = Path(os.path.abspath(arguments.repository_root))
    changes, changes_artifact_sha256 = _read_bound_json(
        arguments.changes,
        repository_root=repository_root,
        field="resolved change inventory",
    )
    exclusions, exclusions_artifact_sha256 = _read_bound_json(
        arguments.exclusions,
        repository_root=repository_root,
        field="coverage exclusions",
    )
    try:
        as_of = date.fromisoformat(arguments.as_of)
    except (TypeError, ValueError) as error:
        raise GateInputError("capture receipt date must be an ISO date") from error
    head = changes.get("headCommit")
    if not isinstance(head, str) or re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise GateInputError("capture receipt change inventory is malformed")
    validated = _validate_exclusions(
        exclusions,
        as_of=as_of,
        expected_head=head,
        repository_root=repository_root,
    )
    evidence_by_selector = _capture_evidence_by_selector(
        arguments,
        selectors={
            exclusion["compensatingIntegrationTest"]["selector"]
            for exclusion in validated
        },
        repository_root=repository_root,
    )
    change_inventory_sha256 = _canonical_json_sha256(
        changes, field="change inventory"
    )
    changed_files = changes.get("files")
    if not isinstance(changed_files, list):
        raise GateInputError("capture receipt change inventory is malformed")
    eligible = {
        entry.get("path"): entry
        for entry in changed_files
        if isinstance(entry, Mapping)
        and entry.get("correctnessCritical") is True
        and entry.get("status") != "deleted"
        and isinstance(entry.get("path"), str)
    }
    receipt_references: list[dict[str, str]] = []
    receipt_paths: set[str] = set()
    for exclusion in validated:
        matches = [
            path
            for path in eligible
            if _matching_exclusion(path, (exclusion,)) is not None
        ]
        if len(matches) != 1:
            raise GateInputError(
                "coverage exclusion must match exactly one changed correctness file: "
                f"{exclusion['id']}"
            )
        source_path = matches[0]
        source_sha256 = eligible[source_path].get("contentSha256")
        if not isinstance(source_sha256, str) or not _SHA256.fullmatch(source_sha256):
            raise GateInputError("excluded source identity is malformed")
        _read_trusted_repository_file(
            repository_root,
            source_path,
            expected_sha256=source_sha256,
            field="excluded source",
            evidence_only=False,
        )
        integration = exclusion["compensatingIntegrationTest"]
        selector = integration["selector"]
        (
            junit_path,
            junit_sha256,
            ledger_path,
            ledger_sha256,
        ) = evidence_by_selector[selector]
        execution_policy = integration["executionPolicy"]
        runner_path, runner_sha256 = _evidence_reference(
            execution_policy.get("runner"), "approved compensating runner"
        )
        _read_trusted_repository_file(
            repository_root,
            runner_path,
            expected_sha256=runner_sha256,
            field="approved compensating runner",
            evidence_only=False,
        )
        runtime_path, runtime_sha256 = _read_approved_runtime(
            repository_root, execution_policy.get("runtime")
        )
        argv_template = execution_policy.get("argvTemplate")
        if (
            not isinstance(argv_template, list)
            or len(argv_template) < 3
            or any(not isinstance(argument, str) or not argument for argument in argv_template)
            or argv_template.count("{selector}") != 1
            or argv_template.count("{runtime}") != 1
            or argv_template[-1] != "{selector}"
            or argv_template[0] != runner_path
            or argv_template[1] != "{runtime}"
        ):
            raise GateInputError("approved compensating argv template is malformed")
        argv = [
            selector
            if argument == "{selector}"
            else runtime_path
            if argument == "{runtime}"
            else argument
            for argument in argv_template
        ]
        receipt_artifact = integration["receipt"]["artifact"]
        if receipt_artifact in receipt_paths:
            raise GateInputError("compensating receipt artifact must be unique")
        receipt_paths.add(receipt_artifact)
        manifest = {
            "schemaVersion": 1,
            "selector": selector,
            "headCommit": head,
            "changeInventorySha256": change_inventory_sha256,
            "source": {"path": source_path, "sha256": source_sha256},
            "runner": {"artifact": runner_path, "sha256": runner_sha256},
            "runtime": {"realPath": runtime_path, "sha256": runtime_sha256},
            "argv": argv,
            "junit": {"artifact": junit_path, "sha256": junit_sha256},
            "ledger": {"artifact": ledger_path, "sha256": ledger_sha256},
        }
        receipt_output = repository_root / receipt_artifact
        _write_json(receipt_output, manifest)
        raw = _read_contract_file(
            receipt_output,
            repository_root=repository_root,
            field="captured compensating receipt",
            size_limit=_MAX_JSON_INPUT_BYTES,
        )
        receipt_references.append(
            {"artifact": receipt_artifact, "sha256": hashlib.sha256(raw).hexdigest()}
        )
    current_changes, current_changes_sha256 = _read_bound_json(
        arguments.changes,
        repository_root=repository_root,
        field="resolved change inventory",
    )
    current_exclusions, current_exclusions_sha256 = _read_bound_json(
        arguments.exclusions,
        repository_root=repository_root,
        field="coverage exclusions",
    )
    if (
        current_changes != changes
        or current_changes_sha256 != changes_artifact_sha256
        or current_exclusions != exclusions
        or current_exclusions_sha256 != exclusions_artifact_sha256
    ):
        raise GateInputError("receipt capture inputs changed during qualification")
    _write_json(
        arguments.output,
        {
            "schemaVersion": 1,
            "changeInventorySha256": change_inventory_sha256,
            "receipts": sorted(receipt_references, key=lambda item: item["artifact"]),
        },
    )
    return 0


def _dispatch(arguments: argparse.Namespace) -> int:
    if arguments.command == "evaluate":
        return _evaluate(arguments)
    if arguments.command == "capture-baseline":
        source_inventory = _resolved_source_inventory(
            arguments.source_inventory_policy,
            repository_root=arguments.repository_root,
        )
        baseline = capture_coverage_baseline(
            [
                _read_json(path, repository_root=arguments.repository_root)
                for path in arguments.report
            ],
            comparison_base=arguments.comparison_base,
            source_snapshot_sha256=arguments.source_snapshot_sha256,
            required_domains=_domain_policy(
                arguments.domain_policy,
                repository_root=arguments.repository_root,
            ),
            source_inventory=source_inventory,
        )
        _write_json(arguments.output, baseline)
        return 0
    if arguments.command == "aggregate":
        aggregate = aggregate_normalized_reports(
            [_read_json(path) for path in arguments.report], language=arguments.language
        )
        _write_json(arguments.output, aggregate)
        return 0
    if arguments.command == "normalize-jacoco":
        report = normalize_jacoco_xml(
            arguments.input,
            module=arguments.module,
            source_root=arguments.source_root,
            repository_root=arguments.repository_root,
            tool_version=arguments.tool_version,
            source_inventory_sha256=arguments.source_inventory_sha256,
        )
        _write_json(arguments.output, report)
        return 0
    if arguments.command == "normalize-jacoco-aggregate":
        aggregate, modules = normalize_jacoco_aggregate_xml(
            arguments.input,
            module_groups=_java_module_policy(
                arguments.module_policy, repository_root=arguments.repository_root
            ),
            repository_root=arguments.repository_root,
            tool_version=arguments.tool_version,
            source_inventory_sha256=arguments.source_inventory_sha256,
        )
        _write_json(arguments.aggregate_output, aggregate)
        for report in modules:
            _write_json(
                arguments.module_output_root / report["module"] / "coverage.json",
                report,
            )
        return 0
    if arguments.command == "normalize-coveragepy":
        report = normalize_coveragepy_json(
            arguments.input,
            module=arguments.module,
            source_prefix=arguments.source_prefix,
            repository_root=arguments.repository_root,
            source_inventory_sha256=arguments.source_inventory_sha256,
        )
        _write_json(arguments.output, report)
        return 0
    if arguments.command == "resolve-changes":
        if arguments.baseline_manifest is not None:
            raise GateInputError("resolve-changes requires the dirty-state attestation")
        if not arguments.base_attestation_sha256:
            raise GateInputError("base attestation digest is required")
        if not arguments.include_worktree:
            raise GateInputError("resolve-changes requires exact worktree resolution")
        if arguments.correctness_policy is None:
            raise GateInputError("resolve-changes requires a correctness policy")
        base_contract = load_comparison_base_attestation(
            arguments.base_attestation,
            expected_attestation_sha256=arguments.base_attestation_sha256,
            expected_manifest_sha256=arguments.baseline_manifest_sha256,
            with_dirty_state=True,
            repository_root=arguments.repository_root,
        )
        if not isinstance(base_contract, tuple):
            raise GateInputError("comparison-base dirty state is missing")
        base, baseline_dirty_entries = base_contract
        correctness_policy, correctness_path, correctness_sha256 = (
            load_correctness_policy(
                arguments.correctness_policy,
                repository_root=arguments.repository_root,
            )
        )
        changes = resolve_git_changes(
            arguments.repository_root,
            base_commit=base,
            include_worktree=arguments.include_worktree,
            correctness_policy=correctness_policy,
            correctness_policy_path=correctness_path,
            correctness_policy_sha256=correctness_sha256,
            baseline_dirty_entries=baseline_dirty_entries,
        )
        _write_json(arguments.output, changes)
        return 0
    if arguments.command == "capture-source-snapshot":
        source_inventory = _resolved_source_inventory(
            arguments.source_inventory_policy,
            repository_root=arguments.repository_root,
        )
        snapshot = capture_source_snapshot(
            [
                _read_json(path, repository_root=arguments.repository_root)
                for path in arguments.report
            ],
            repository_root=arguments.repository_root,
            source_inventory=source_inventory,
        )
        _write_json(arguments.output, snapshot)
        return 0
    if arguments.command == "verify-source-snapshot":
        try:
            raw_snapshot = _read_json_bytes(
                arguments.snapshot,
                repository_root=arguments.repository_root,
                field="source snapshot",
            )
        except GateInputError as error:
            raise GateInputError("source snapshot is unreadable") from error
        digest = hashlib.sha256(raw_snapshot).hexdigest()
        if digest != arguments.snapshot_sha256:
            raise GateInputError("source snapshot digest mismatch")
        source_inventory = _resolved_source_inventory(
            arguments.source_inventory_policy,
            repository_root=arguments.repository_root,
        )
        verify_source_snapshot(
            _decode_json(raw_snapshot, path=arguments.snapshot),
            repository_root=arguments.repository_root,
            source_inventory=source_inventory,
        )
        return 0
    if arguments.command == "run-mutations":
        try:
            runner_raw = _read_contract_file(
                arguments.offline_runner,
                repository_root=arguments.repository_root,
                field="offline isolation runner",
                size_limit=_MAX_JSON_INPUT_BYTES,
            )
        except GateInputError as error:
            raise GateInputError("offline isolation runner is unreadable") from error
        runner_digest = hashlib.sha256(runner_raw).hexdigest()
        if runner_digest != arguments.offline_runner_sha256:
            raise GateInputError("offline isolation runner digest mismatch")
        result = run_mutation_profile(
            repository_root=arguments.repository_root,
            profile=_read_json(
                arguments.profile,
                repository_root=arguments.repository_root,
            ),
            artifact_root=arguments.artifact_root,
            python_runtime=arguments.python_runtime,
            offline_runner=arguments.offline_runner,
        )
        _write_json(arguments.output, result)
        return 0 if result["passed"] else 1
    if arguments.command == "resolve-source-inventory":
        inventory = _resolved_source_inventory(
            arguments.policy, repository_root=arguments.repository_root
        )
        _write_json(arguments.output, inventory)
        return 0
    if arguments.command == "verify-trust-bundle":
        verify_trust_bundle(
            arguments.bundle,
            expected_sha256=arguments.bundle_sha256,
            repository_root=arguments.repository_root,
        )
        return 0
    if arguments.command == "capture-trust-bundle":
        bundle = create_trust_bundle(repository_root=arguments.repository_root)
        _write_json(arguments.output, bundle)
        return 0
    if arguments.command == "capture-exclusion-receipts":
        return _capture_exclusion_receipts(arguments)
    raise GateInputError(f"unsupported command: {arguments.command}")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        return _dispatch(arguments)
    except GateInputError as error:
        print(f"quality gate input error: {error}", file=sys.stderr)
        return 2
