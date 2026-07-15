"""Create exact, reviewable coverage aggregates and frozen baselines."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .changed_coverage import GateInputError, validate_normalized_report
from .source_inventory import (
    reconcile_reports_with_inventory,
    source_inventory_digest,
    validate_source_inventory,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _totals(report: Mapping[str, Any], domain: str) -> Mapping[str, Any]:
    totals = report.get("totals")
    if not isinstance(totals, Mapping):
        raise GateInputError(f"{domain} has malformed totals")
    for kind in ("lines", "branches"):
        counter = totals.get(kind)
        if (
            not isinstance(counter, Mapping)
            or not isinstance(counter.get("covered"), int)
            or isinstance(counter.get("covered"), bool)
            or not isinstance(counter.get("total"), int)
            or isinstance(counter.get("total"), bool)
            or counter["covered"] < 0
            or counter["total"] < counter["covered"]
        ):
            raise GateInputError(f"{domain} has malformed {kind} totals")
    return totals


def aggregate_normalized_reports(
    reports: Sequence[Mapping[str, Any]], *, language: str
) -> dict[str, Any]:
    """Sum disjoint module reports into one exact repository report."""

    if language not in {"java", "python"} or not reports:
        raise GateInputError("coverage aggregate language or report set is invalid")
    files: dict[str, Any] = {}
    line_covered = line_total = branch_covered = branch_total = 0
    tool_versions: set[str] = set()
    adapters: set[str] = set()
    modules: set[str] = set()
    source_inventory_digests: set[str] = set()
    for report in reports:
        validate_normalized_report(report)
        if report.get("schemaVersion") != 1 or report.get("language") != language:
            raise GateInputError("cannot mix coverage languages or schemas")
        module = report.get("module")
        report_files = report.get("files")
        if not isinstance(module, str) or not module or module == "@repository":
            raise GateInputError("coverage aggregate requires module reports")
        if module in modules:
            raise GateInputError(f"duplicate coverage module: {module}")
        modules.add(module)
        if report.get("branchInstrumentation") is not True or not isinstance(report_files, Mapping):
            raise GateInputError(f"{language}:{module} is not branch-instrumented")
        version = report.get("toolVersion")
        adapter = report.get("adapter")
        if not isinstance(version, str) or not isinstance(adapter, str):
            raise GateInputError(f"{language}:{module} lacks adapter identity")
        tool_versions.add(version)
        adapters.add(adapter)
        source_inventory_digests.add(report["sourceInventorySha256"])
        totals = _totals(report, f"{language}:{module}")
        line_covered += totals["lines"]["covered"]
        line_total += totals["lines"]["total"]
        branch_covered += totals["branches"]["covered"]
        branch_total += totals["branches"]["total"]
        for path, file_report in report_files.items():
            if path in files:
                raise GateInputError(f"duplicate normalized source path: {path}")
            files[path] = file_report
    if len(tool_versions) != 1 or len(adapters) != 1:
        raise GateInputError("coverage aggregate adapters must have one exact version")
    if len(source_inventory_digests) != 1:
        raise GateInputError("coverage aggregate reports span source inventory epochs")
    aggregate = {
        "schemaVersion": 1,
        "adapter": next(iter(adapters)),
        "language": language,
        "module": "@repository",
        "toolVersion": next(iter(tool_versions)),
        "sourceInventorySha256": next(iter(source_inventory_digests)),
        "branchInstrumentation": True,
        "files": dict(sorted(files.items())),
        "totals": {
            "lines": {"covered": line_covered, "total": line_total},
            "branches": {"covered": branch_covered, "total": branch_total},
        },
    }
    validate_normalized_report(aggregate)
    return aggregate


def _capture_unbound_coverage_baseline(
    reports: Sequence[Mapping[str, Any]],
    *,
    comparison_base: str,
    source_snapshot_sha256: str,
    required_domains: set[str] | None = None,
    source_inventory: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Legacy test helper for exercising counter logic without release evidence.

    Release baseline capture must go through :func:`capture_coverage_baseline`,
    which requires a complete source inventory.  Keeping this implementation
    private makes the unbound contract visibly unsuitable for acceptance
    evidence while preserving narrow unit tests for malformed counter inputs.
    """

    if not _COMMIT.fullmatch(comparison_base) or not _SHA256.fullmatch(source_snapshot_sha256):
        raise GateInputError("coverage baseline provenance is malformed")
    if not reports:
        raise GateInputError("coverage baseline report set is empty")
    expected_source_inventory_sha256 = (
        source_inventory_digest(source_inventory)
        if source_inventory is not None
        else None
    )
    domains: dict[str, Any] = {}
    for report in reports:
        validate_normalized_report(report)
        if (
            expected_source_inventory_sha256 is not None
            and report["sourceInventorySha256"]
            != expected_source_inventory_sha256
        ):
            raise GateInputError("coverage report source inventory is stale")
        language = report.get("language")
        module = report.get("module")
        if language not in {"java", "python"} or not isinstance(module, str) or not module:
            raise GateInputError("coverage baseline report identity is malformed")
        domain = f"{language}:{module}"
        if domain in domains:
            raise GateInputError(f"duplicate coverage baseline domain: {domain}")
        totals = _totals(report, domain)
        domains[domain] = {
            "lines": dict(totals["lines"]),
            "branches": dict(totals["branches"]),
        }
    languages = {domain.split(":", 1)[0] for domain in domains}
    for language in languages:
        aggregate_domain = f"{language}:@repository"
        if aggregate_domain not in domains:
            raise GateInputError(
                f"{language} baseline lacks authoritative repository aggregate"
            )
        module_domains = [
            value
            for domain, value in domains.items()
            if domain.startswith(f"{language}:") and domain != aggregate_domain
        ]
        if not module_domains:
            raise GateInputError(f"{language} baseline has no module reports")
        summed = {
            kind: {
                "covered": sum(value[kind]["covered"] for value in module_domains),
                "total": sum(value[kind]["total"] for value in module_domains),
            }
            for kind in ("lines", "branches")
        }
        if summed != domains[aggregate_domain]:
            raise GateInputError(
                f"{language} repository aggregate does not match module reports"
            )
    if required_domains is not None and set(domains) != required_domains:
        raise GateInputError("coverage baseline domains do not match policy")
    result: dict[str, Any] = {
        "schemaVersion": 1,
        "comparisonBase": comparison_base,
        "sourceSnapshotSha256": source_snapshot_sha256,
        "domains": dict(sorted(domains.items())),
    }
    if source_inventory is not None:
        inventory_by_path = validate_source_inventory(source_inventory)
        reported = reconcile_reports_with_inventory(
            reports, source_inventory, require_aggregates=True
        )
        files: dict[str, Any] = {}
        for path, file_report in sorted(reported.items()):
            source = inventory_by_path[path]
            files[path] = {
                "domain": f"{source['language']}:{source['module']}",
                "sourceSha256": source["sha256"],
                "executableLines": list(file_report["executableLines"]),
                "branchShape": {
                    origin: branch["covered"] + branch["missed"]
                    for origin, branch in sorted(
                        file_report["branches"].items(), key=lambda item: int(item[0])
                    )
                },
            }
        result["sourceInventoryPolicyPath"] = source_inventory["policyPath"]
        result["sourceInventoryPolicySha256"] = source_inventory["policySha256"]
        result["files"] = files
    return result


def capture_coverage_baseline(
    reports: Sequence[Mapping[str, Any]],
    *,
    comparison_base: str,
    source_snapshot_sha256: str,
    source_inventory: Mapping[str, Any],
    required_domains: set[str] | None = None,
) -> dict[str, Any]:
    """Freeze exact, source-bound counters for release acceptance."""

    if source_inventory is None:
        raise GateInputError("coverage baseline capture requires a source inventory")
    required_sources = [
        source
        for source in source_inventory.get("sources", [])
        if source.get("coverageDisposition") == "required"
    ]
    if not required_sources:
        raise GateInputError("coverage baseline contains no required source files")
    baseline = _capture_unbound_coverage_baseline(
        reports,
        comparison_base=comparison_base,
        source_snapshot_sha256=source_snapshot_sha256,
        required_domains=required_domains,
        source_inventory=source_inventory,
    )
    if not baseline["files"]:
        raise GateInputError("coverage baseline contains no required source files")
    return baseline


def _capture_unbound_source_snapshot(
    reports: Sequence[Mapping[str, Any]],
    *,
    repository_root: Path,
    source_inventory: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Legacy test helper that cannot produce release acceptance evidence."""

    if not reports and source_inventory is None:
        raise GateInputError("source snapshot report set is empty")
    root = repository_root.resolve()
    entries: dict[str, str] = {}
    if source_inventory is not None:
        for path, source in validate_source_inventory(source_inventory).items():
            entries[path] = source["sha256"]
    for report in reports:
        validate_normalized_report(report)
        if report.get("module") == "@repository":
            continue
        for path in report["files"]:
            if source_inventory is not None:
                continue
            if path in entries:
                raise GateInputError(f"duplicate source snapshot path: {path}")
            source = root / PurePosixPath(path)
            try:
                source.resolve().relative_to(root)
            except ValueError as error:
                raise GateInputError(f"source snapshot path escapes repository: {path}") from error
            if not source.is_file() or source.is_symlink():
                raise GateInputError(f"source snapshot path is not a regular file: {path}")
            entries[path] = hashlib.sha256(source.read_bytes()).hexdigest()
    if not entries:
        raise GateInputError("source snapshot contains no source files")
    return {
        "schemaVersion": 1,
        "files": [
            {"path": path, "sha256": digest}
            for path, digest in sorted(entries.items())
        ],
    }


def capture_source_snapshot(
    reports: Sequence[Mapping[str, Any]],
    *,
    repository_root: Path,
    source_inventory: Mapping[str, Any],
) -> dict[str, Any]:
    """Hash a complete report set bound to one resolved source epoch."""

    if source_inventory is None:
        raise GateInputError("source snapshot capture requires a source inventory")
    inventory_sha256 = source_inventory_digest(source_inventory)
    for report in reports:
        validate_normalized_report(report)
        if report["sourceInventorySha256"] != inventory_sha256:
            raise GateInputError("coverage report source inventory is stale")
    reconcile_reports_with_inventory(
        reports,
        source_inventory,
        require_aggregates=True,
    )
    snapshot = _capture_unbound_source_snapshot(
        reports,
        repository_root=repository_root,
        source_inventory=source_inventory,
    )
    snapshot["sourceInventorySha256"] = inventory_sha256
    snapshot["sourceInventoryPolicyPath"] = source_inventory["policyPath"]
    snapshot["sourceInventoryPolicySha256"] = source_inventory["policySha256"]
    return snapshot


def _verify_unbound_source_snapshot(
    snapshot: Mapping[str, Any], *, repository_root: Path
) -> None:
    """Legacy test helper for the file-list mechanics only."""

    raw_entries = snapshot.get("files")
    if snapshot.get("schemaVersion") != 1 or not isinstance(raw_entries, list) or not raw_entries:
        raise GateInputError("source snapshot contract is malformed")
    root = repository_root.resolve()
    previous = ""
    for entry in raw_entries:
        if not isinstance(entry, Mapping):
            raise GateInputError("source snapshot entry is malformed")
        path = entry.get("path")
        digest = entry.get("sha256")
        if (
            not isinstance(path, str)
            or not path
            or path <= previous
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            or not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
        ):
            raise GateInputError("source snapshot entry is malformed or unsorted")
        previous = path
        source = root / PurePosixPath(path)
        if (
            not source.is_file()
            or source.is_symlink()
            or hashlib.sha256(source.read_bytes()).hexdigest() != digest
        ):
            raise GateInputError(f"source snapshot mismatch: {path}")


def verify_source_snapshot(
    snapshot: Mapping[str, Any],
    *,
    repository_root: Path,
    source_inventory: Mapping[str, Any],
) -> None:
    """Verify one source snapshot against its complete current source epoch."""

    if source_inventory is None:
        raise GateInputError("source snapshot verification requires a source inventory")
    inventory_by_path = validate_source_inventory(source_inventory)
    if (
        set(snapshot)
        != {
            "schemaVersion",
            "sourceInventorySha256",
            "sourceInventoryPolicyPath",
            "sourceInventoryPolicySha256",
            "files",
        }
        or snapshot.get("sourceInventorySha256")
        != source_inventory_digest(source_inventory)
        or snapshot.get("sourceInventoryPolicyPath")
        != source_inventory["policyPath"]
        or snapshot.get("sourceInventoryPolicySha256")
        != source_inventory["policySha256"]
    ):
        raise GateInputError("source snapshot inventory contract is malformed or stale")
    expected_files = [
        {"path": path, "sha256": source["sha256"]}
        for path, source in sorted(inventory_by_path.items())
    ]
    if snapshot["files"] != expected_files:
        raise GateInputError("source snapshot does not match the complete source inventory")
    _verify_unbound_source_snapshot(
        {"schemaVersion": 1, "files": snapshot["files"]},
        repository_root=repository_root,
    )
