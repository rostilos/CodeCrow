from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest


QUALITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QUALITY_ROOT))

from quality_gates import GateInputError  # noqa: E402
from quality_gates import source_inventory as source_inventory_module  # noqa: E402
from quality_gates.baseline import (  # noqa: E402
    capture_coverage_baseline,
    capture_source_snapshot,
    verify_source_snapshot,
)
from quality_gates.changed_coverage import (  # noqa: E402
    _evaluate_unbound_gate,
    evaluate_gate,
)
from quality_gates.source_inventory import (  # noqa: E402
    reconcile_reports_with_inventory,
    load_and_resolve_source_inventory,
    resolve_source_inventory,
    source_inventory_digest,
)


BASE = "8" * 40


def _policy() -> dict:
    return {
        "schemaVersion": 1,
        "roots": [
            {
                "language": "python",
                "module": "app",
                "root": "app/src",
                "suffix": ".py",
            }
        ],
        "files": [],
        "excludedSourceTrees": [],
        "nonExecutableSources": [],
    }


def _inventory(repository: Path) -> dict:
    return resolve_source_inventory(
        _policy(), policy_sha256="a" * 64, repository_root=repository
    )


def _report(files: dict[str, tuple[list[int], list[int], dict[str, dict[str, int]]]]) -> dict:
    normalized = {
        path: {
            "executableLines": executable,
            "coveredLines": covered,
            "branches": branches,
        }
        for path, (executable, covered, branches) in files.items()
    }
    return {
        "schemaVersion": 1,
        "adapter": "coveragepy-json",
        "language": "python",
        "module": "app",
        "toolVersion": "7.15.1",
        "sourceInventorySha256": "f" * 64,
        "branchInstrumentation": True,
        "files": normalized,
        "totals": {
            "lines": {
                "covered": sum(len(value["coveredLines"]) for value in normalized.values()),
                "total": sum(len(value["executableLines"]) for value in normalized.values()),
            },
            "branches": {
                "covered": sum(
                    branch["covered"]
                    for value in normalized.values()
                    for branch in value["branches"].values()
                ),
                "total": sum(
                    branch["covered"] + branch["missed"]
                    for value in normalized.values()
                    for branch in value["branches"].values()
                ),
            },
        },
    }


def _aggregate(report: dict) -> dict:
    result = dict(report)
    result["module"] = "@repository"
    return result


def test_independent_inventory_rejects_a_self_erasing_coverage_report(tmp_path: Path) -> None:
    source_root = tmp_path / "app/src"
    source_root.mkdir(parents=True)
    (source_root / "covered.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source_root / "poorly_covered.py").write_text("VALUE = 2\n", encoding="utf-8")
    inventory = _inventory(tmp_path)
    forged = _report({"app/src/covered.py": ([1], [1], {})})

    with pytest.raises(GateInputError, match="omits required source: app/src/poorly_covered.py"):
        reconcile_reports_with_inventory([forged], inventory)


def test_baseline_binds_per_file_shape_and_unchanged_shape_cannot_shrink(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "app/src"
    source_root.mkdir(parents=True)
    source = source_root / "rules.py"
    source.write_text("VALUE = 1\nVALUE = 2\n", encoding="utf-8")
    inventory = _inventory(tmp_path)
    module = _report(
        {"app/src/rules.py": ([1, 2], [1], {"2": {"covered": 0, "missed": 2}})}
    )
    module["sourceInventorySha256"] = inventory["inventorySha256"]
    aggregate = _aggregate(module)
    baseline = capture_coverage_baseline(
        [module, aggregate],
        comparison_base=BASE,
        source_snapshot_sha256="b" * 64,
        required_domains={"python:app", "python:@repository"},
        source_inventory=inventory,
    )
    assert baseline["files"]["app/src/rules.py"] == {
        "domain": "python:app",
        "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "executableLines": [1, 2],
        "branchShape": {"2": 2},
    }

    malformed_baselines = (
        {**baseline, "untrusted": True},
        {**baseline, "schemaVersion": 2},
        {**baseline, "comparisonBase": None},
        {**baseline, "comparisonBase": "bad"},
        {**baseline, "sourceSnapshotSha256": None},
        {**baseline, "sourceSnapshotSha256": "bad"},
        {**baseline, "sourceInventoryPolicyPath": None},
        {**baseline, "sourceInventoryPolicyPath": "../policy.json"},
        {**baseline, "sourceInventoryPolicySha256": None},
        {**baseline, "sourceInventoryPolicySha256": "bad"},
        {**baseline, "domains": []},
        {**baseline, "domains": {}},
        {**baseline, "files": []},
        {**baseline, "files": {}},
    )
    for malformed in malformed_baselines:
        with pytest.raises(GateInputError):
            evaluate_gate(
                changes={
                    "schemaVersion": 1,
                    "baseCommit": BASE,
                    "headCommit": "9" * 40,
                    "files": [],
                },
                reports=[module, aggregate],
                baseline=malformed,
                exclusions={"schemaVersion": 1, "entries": []},
                as_of="2026-07-14",
                repository_root=tmp_path,
                source_inventory=inventory,
            )

    unsafe = json.loads(json.dumps(baseline))
    unsafe["files"] = {"../rules.py": next(iter(unsafe["files"].values()))}
    with pytest.raises(GateInputError, match="baseline source path"):
        evaluate_gate(
            changes={
                "schemaVersion": 1,
                "baseCommit": BASE,
                "headCommit": "9" * 40,
                "files": [],
            },
            reports=[module, aggregate],
            baseline=unsafe,
            exclusions={"schemaVersion": 1, "entries": []},
            as_of="2026-07-14",
            repository_root=tmp_path,
            source_inventory=inventory,
        )

    forged = _report({"app/src/rules.py": ([1], [1], {})})
    forged["sourceInventorySha256"] = inventory["inventorySha256"]
    forged_aggregate = _aggregate(forged)
    with pytest.raises(GateInputError, match="unchanged source coverage shape drifted"):
        evaluate_gate(
            changes={
                "schemaVersion": 1,
                "baseCommit": BASE,
                "headCommit": "9" * 40,
                "files": [],
            },
            reports=[forged, forged_aggregate],
            baseline=baseline,
            exclusions={"schemaVersion": 1, "entries": []},
            as_of="2026-07-14",
            repository_root=tmp_path,
            source_inventory=inventory,
        )


def test_fixed_p0_01_diff_cannot_hide_a_post_baseline_source_change(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "app/src"
    source_root.mkdir(parents=True)
    source = source_root / "rules.py"
    source.write_text("VALUE = 1\nOTHER = 2\n", encoding="utf-8")
    baseline_inventory = _inventory(tmp_path)
    baseline_module = _report({"app/src/rules.py": ([1, 2], [1, 2], {})})
    baseline_module["sourceInventorySha256"] = baseline_inventory["inventorySha256"]
    baseline = capture_coverage_baseline(
        [baseline_module, _aggregate(baseline_module)],
        comparison_base=BASE,
        source_snapshot_sha256="b" * 64,
        required_domains={"python:app", "python:@repository"},
        source_inventory=baseline_inventory,
    )

    # Reverting or re-editing relative to the later baseline can produce no
    # useful hunk against the permanently fixed P0-01 comparison commit.
    source.write_text("VALUE = 3\nOTHER = 4\n", encoding="utf-8")
    current_inventory = _inventory(tmp_path)
    partial = _report({"app/src/rules.py": ([1, 2], [1], {})})
    partial["sourceInventorySha256"] = current_inventory["inventorySha256"]
    inputs = {
        "changes": {
            "schemaVersion": 1,
            "baseCommit": BASE,
            "headCommit": "9" * 40,
            "files": [],
        },
        "baseline": baseline,
        "exclusions": {"schemaVersion": 1, "entries": []},
        "as_of": "2026-07-14",
        "repository_root": tmp_path,
        "source_inventory": current_inventory,
    }
    with pytest.raises(
        GateInputError,
        match="changed baseline source is not fully covered",
    ):
        evaluate_gate(
            reports=[partial, _aggregate(partial)],
            **inputs,
        )

    complete = _report({"app/src/rules.py": ([1, 2], [1, 2], {})})
    complete["sourceInventorySha256"] = current_inventory["inventorySha256"]
    assert evaluate_gate(
        reports=[complete, _aggregate(complete)],
        **inputs,
    ).passed


def test_release_evaluator_rejects_a_missing_source_inventory() -> None:
    with pytest.raises(GateInputError, match="requires a source inventory"):
        evaluate_gate(
            changes={
                "schemaVersion": 1,
                "baseCommit": BASE,
                "headCommit": "9" * 40,
                "files": [],
            },
            reports=[],
            baseline={
                "schemaVersion": 1,
                "comparisonBase": BASE,
                "sourceSnapshotSha256": "b" * 64,
                "domains": {},
            },
            exclusions={"schemaVersion": 1, "entries": []},
            as_of="2026-07-14",
            source_inventory=None,  # type: ignore[arg-type]
        )


def test_source_bound_snapshot_rejects_missing_and_mixed_report_epochs(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "app/src"
    source_root.mkdir(parents=True)
    (source_root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    inventory = _inventory(tmp_path)
    module = _report({"app/src/app.py": ([1], [1], {})})
    module["sourceInventorySha256"] = inventory["inventorySha256"]
    aggregate = _aggregate(module)

    missing = json.loads(json.dumps(aggregate))
    del missing["sourceInventorySha256"]
    with pytest.raises(GateInputError, match="identity is malformed"):
        capture_source_snapshot(
            [module, missing],
            repository_root=tmp_path,
            source_inventory=inventory,
        )

    mixed = json.loads(json.dumps(aggregate))
    mixed["sourceInventorySha256"] = "e" * 64
    with pytest.raises(GateInputError, match="source inventory is stale"):
        capture_source_snapshot(
            [module, mixed],
            repository_root=tmp_path,
            source_inventory=inventory,
        )

    snapshot = capture_source_snapshot(
        [module, aggregate],
        repository_root=tmp_path,
        source_inventory=inventory,
    )
    assert snapshot["files"] == [
        {
            "path": "app/src/app.py",
            "sha256": hashlib.sha256((source_root / "app.py").read_bytes()).hexdigest(),
        }
    ]
    assert snapshot["sourceInventorySha256"] == inventory["inventorySha256"]
    assert snapshot["sourceInventoryPolicyPath"] == inventory["policyPath"]
    assert snapshot["sourceInventoryPolicySha256"] == inventory["policySha256"]
    verify_source_snapshot(
        snapshot,
        repository_root=tmp_path,
        source_inventory=inventory,
    )

    mutations = (
        ("sourceInventorySha256", "e" * 64),
        ("sourceInventoryPolicyPath", "other-policy.json"),
        ("sourceInventoryPolicySha256", "e" * 64),
    )
    for field, value in mutations:
        stale = json.loads(json.dumps(snapshot))
        stale[field] = value
        with pytest.raises(
            GateInputError,
            match="inventory contract is malformed or stale",
        ):
            verify_source_snapshot(
                stale,
                repository_root=tmp_path,
                source_inventory=inventory,
            )

    extra = json.loads(json.dumps(snapshot))
    extra["untrusted"] = True
    with pytest.raises(GateInputError, match="inventory contract is malformed or stale"):
        verify_source_snapshot(
            extra,
            repository_root=tmp_path,
            source_inventory=inventory,
        )

    incomplete = json.loads(json.dumps(snapshot))
    incomplete["files"] = []
    with pytest.raises(GateInputError, match="complete source inventory"):
        verify_source_snapshot(
            incomplete,
            repository_root=tmp_path,
            source_inventory=inventory,
        )


def test_evaluator_rejects_missing_and_mixed_report_epochs(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "app/src"
    source_root.mkdir(parents=True)
    (source_root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    inventory = _inventory(tmp_path)
    module = _report({"app/src/app.py": ([1], [1], {})})
    module["sourceInventorySha256"] = inventory["inventorySha256"]
    aggregate = _aggregate(module)
    baseline = capture_coverage_baseline(
        [module, aggregate],
        comparison_base=BASE,
        source_snapshot_sha256="b" * 64,
        required_domains={"python:app", "python:@repository"},
        source_inventory=inventory,
    )
    changes = {
        "schemaVersion": 1,
        "baseCommit": BASE,
        "headCommit": "9" * 40,
        "files": [],
    }

    missing = json.loads(json.dumps(aggregate))
    del missing["sourceInventorySha256"]
    with pytest.raises(GateInputError, match="identity is malformed"):
        evaluate_gate(
            changes=changes,
            reports=[module, missing],
            baseline=baseline,
            exclusions={"schemaVersion": 1, "entries": []},
            as_of="2026-07-14",
            repository_root=tmp_path,
            source_inventory=inventory,
        )

    mixed = json.loads(json.dumps(aggregate))
    mixed["sourceInventorySha256"] = "e" * 64
    with pytest.raises(GateInputError, match="source inventory is stale"):
        evaluate_gate(
            changes=changes,
            reports=[module, mixed],
            baseline=baseline,
            exclusions={"schemaVersion": 1, "entries": []},
            as_of="2026-07-14",
            repository_root=tmp_path,
            source_inventory=inventory,
        )


def test_explicit_non_executable_source_is_inventoried_but_not_reported(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "app/src"
    source_root.mkdir(parents=True)
    descriptor = source_root / "empty.py"
    descriptor.write_text("", encoding="utf-8")
    policy = _policy()
    policy["nonExecutableSources"] = [
        {
            "path": "app/src/empty.py",
            "reason": "Empty package marker.",
            "owner": "application-owner",
            "reviewer": "quality-reviewer",
        }
    ]
    inventory = resolve_source_inventory(
        policy, policy_sha256="a" * 64, repository_root=tmp_path
    )
    assert inventory["sources"] == [
        {
            "path": "app/src/empty.py",
            "language": "python",
            "module": "app",
            "coverageDisposition": "nonExecutable",
            "sha256": hashlib.sha256(descriptor.read_bytes()).hexdigest(),
        }
    ]
    assert reconcile_reports_with_inventory([], inventory) == {}

    empty_module = _report({})
    empty_module["sourceInventorySha256"] = inventory["inventorySha256"]
    with pytest.raises(GateInputError, match="no required source files"):
        capture_coverage_baseline(
            [empty_module, _aggregate(empty_module)],
            comparison_base=BASE,
            source_snapshot_sha256="b" * 64,
            required_domains={"python:app", "python:@repository"},
            source_inventory=inventory,
        )

    result = _evaluate_unbound_gate(
        changes={
            "schemaVersion": 1,
            "baseCommit": BASE,
            "headCommit": "9" * 40,
            "files": [
                {
                    "path": "app/src/empty.py",
                    "status": "modified",
                    "correctnessCritical": True,
                    "language": "python",
                    "changedLines": [],
                }
            ],
        },
        reports=[],
        baseline={
            "schemaVersion": 1,
            "comparisonBase": BASE,
            "sourceInventoryPolicyPath": inventory["policyPath"],
            "sourceInventoryPolicySha256": inventory["policySha256"],
            "files": {},
            "domains": {},
        },
        exclusions={"schemaVersion": 1, "entries": []},
        as_of="2026-07-14",
        source_inventory=inventory,
    )
    assert result.failures == ("app/src/empty.py has no coverage report",)


def test_inventory_exclusion_is_exact_reviewed_and_cannot_hide_an_unlisted_tree(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "app/src"
    excluded = source_root / ".venv"
    excluded.mkdir(parents=True)
    (source_root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (excluded / "third_party.py").write_text("VALUE = 2\n", encoding="utf-8")
    policy = _policy()
    policy["excludedSourceTrees"] = [
        {
            "path": "app/src/.venv",
            "reason": "Third-party virtual environment.",
            "owner": "application-owner",
            "reviewer": "quality-reviewer",
        }
    ]
    inventory = resolve_source_inventory(
        policy, policy_sha256="a" * 64, repository_root=tmp_path
    )
    assert [entry["path"] for entry in inventory["sources"]] == ["app/src/app.py"]

    policy["excludedSourceTrees"][0]["path"] = "vendor"
    (tmp_path / "vendor").mkdir()
    with pytest.raises(GateInputError, match="not owned by exactly one source root"):
        resolve_source_inventory(
            policy, policy_sha256="a" * 64, repository_root=tmp_path
        )


def test_inventory_rejects_source_symlinks(tmp_path: Path) -> None:
    source_root = tmp_path / "app/src"
    source_root.mkdir(parents=True)
    target = tmp_path / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    (source_root / "linked.py").symlink_to(target)
    with pytest.raises(GateInputError, match="cannot traverse a symlink"):
        _inventory(tmp_path)


@pytest.mark.parametrize(
    "path",
    ["./source.py", "app//source.py", "app/./source.py", "app/source.py/", "a\x00b"],
)
def test_inventory_paths_are_canonical(path: str) -> None:
    from quality_gates.source_inventory import _repository_path

    with pytest.raises(GateInputError, match="repository-relative path"):
        _repository_path(path, "source")


def test_secure_policy_loader_rejects_symlink_fifo_oversize_and_root_symlink(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    source = repository / "app/src/app.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    target = repository / "target.json"
    target.write_text(json.dumps(_policy()), encoding="utf-8")
    linked = repository / "linked.json"
    linked.symlink_to(target)
    with pytest.raises(GateInputError, match="not a trusted regular file"):
        load_and_resolve_source_inventory(linked, repository_root=repository)

    fifo = repository / "policy.fifo"
    os.mkfifo(fifo)
    with pytest.raises(GateInputError, match="must be a regular file"):
        load_and_resolve_source_inventory(fifo, repository_root=repository)

    oversized = repository / "oversized.json"
    oversized.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(GateInputError, match="exceeds the size limit"):
        load_and_resolve_source_inventory(oversized, repository_root=repository)

    root_link = tmp_path / "repo-link"
    root_link.symlink_to(repository, target_is_directory=True)
    with pytest.raises(GateInputError, match="repository root is not trusted"):
        load_and_resolve_source_inventory(Path("target.json"), repository_root=root_link)


def test_fifo_policy_rejection_is_proven_nonblocking_under_outer_timeout(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    fifo = repository / "policy.fifo"
    os.mkfifo(fifo)
    script = (
        "from pathlib import Path; "
        "from quality_gates import GateInputError; "
        "from quality_gates.source_inventory import load_and_resolve_source_inventory; "
        "\ntry: load_and_resolve_source_inventory(Path('policy.fifo'), "
        f"repository_root=Path({str(repository)!r}))"
        "\nexcept GateInputError: raise SystemExit(0)"
        "\nraise SystemExit(1)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env={"PYTHONPATH": str(QUALITY_ROOT)},
        timeout=2,
        check=False,
    )
    assert completed.returncode == 0


def test_source_inventory_rejects_fifo_oversize_and_entry_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "app/src"
    source_root.mkdir(parents=True)
    fifo = source_root / "pipe.py"
    os.mkfifo(fifo)
    with pytest.raises(GateInputError, match="must be regular"):
        _inventory(tmp_path)
    fifo.unlink()

    oversized = source_root / "large.py"
    oversized.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    with pytest.raises(GateInputError, match="exceeds the size limit"):
        _inventory(tmp_path)
    oversized.unlink()

    source = source_root / "app.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    replacement = source_root / "replacement"
    replacement.write_text("VALUE = 2\n", encoding="utf-8")
    original_open = os.open
    swapped = False

    def swapping_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if path == "app.py" and not swapped:
            swapped = True
            source.unlink()
            replacement.rename(source)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr("quality_gates.source_inventory.os.open", swapping_open)
    with pytest.raises(GateInputError, match="changed during scan"):
        _inventory(tmp_path)


def test_inventory_rejects_overlapping_roots_nested_exclusions_and_duplicate_files(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "app/src"
    nested = source_root / "generated"
    nested.mkdir(parents=True)
    (source_root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (nested / "generated.py").write_text("VALUE = 2\n", encoding="utf-8")

    policy = _policy()
    policy["roots"].append(
        {"language": "python", "module": "nested", "root": "app/src/generated", "suffix": ".py"}
    )
    with pytest.raises(GateInputError, match="root entry is malformed"):
        resolve_source_inventory(policy, policy_sha256="a" * 64, repository_root=tmp_path)

    policy = _policy()
    approval = {
        "reason": "Generated third-party tree.",
        "owner": "application-owner",
        "reviewer": "quality-reviewer",
    }
    policy["excludedSourceTrees"] = [
        {"path": "app/src/generated", **approval},
        {"path": "app/src/generated/nested", **approval},
    ]
    (nested / "nested").mkdir()
    with pytest.raises(GateInputError, match="excluded source tree approval"):
        resolve_source_inventory(policy, policy_sha256="a" * 64, repository_root=tmp_path)

    policy = _policy()
    policy["files"] = [
        {"language": "python", "module": "app", "path": "app/src/app.py"}
    ]
    with pytest.raises(GateInputError, match="multiple owners"):
        resolve_source_inventory(policy, policy_sha256="a" * 64, repository_root=tmp_path)


def test_report_reconciliation_rejects_extra_wrong_module_and_forged_aggregate(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "app/src"
    source_root.mkdir(parents=True)
    (source_root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    inventory = _inventory(tmp_path)
    module = _report({"app/src/app.py": ([1], [1], {})})
    aggregate = _aggregate(module)

    extra = _report({})
    extra["module"] = "unknown"
    with pytest.raises(GateInputError, match="module inventory is not exact"):
        reconcile_reports_with_inventory([module, extra], inventory)

    wrong = json.loads(json.dumps(module))
    wrong["module"] = "wrong"
    with pytest.raises(GateInputError, match="wrong module"):
        reconcile_reports_with_inventory([wrong], inventory)

    forged = json.loads(json.dumps(aggregate))
    forged["files"]["app/src/app.py"]["executableLines"] = []
    forged["files"]["app/src/app.py"]["coveredLines"] = []
    forged["totals"]["lines"] = {"covered": 0, "total": 0}
    with pytest.raises(GateInputError, match="does not exactly match module report files"):
        reconcile_reports_with_inventory(
            [module, forged], inventory, require_aggregates=True
        )


def test_inventory_detects_post_list_addition_and_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "app/src"
    source_root.mkdir(parents=True)
    source = source_root / "app.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    original_listdir = os.listdir
    injected = False

    def adding_listdir(path: object) -> list[str]:
        nonlocal injected
        names = original_listdir(path)
        if not injected:
            injected = True
            (source_root / "late.py").write_text("LATE = 1\n", encoding="utf-8")
        return names

    monkeypatch.setattr("quality_gates.source_inventory.os.listdir", adding_listdir)
    with pytest.raises(GateInputError, match="directory changed during scan"):
        _inventory(tmp_path)

    monkeypatch.setattr("quality_gates.source_inventory.os.listdir", original_listdir)
    (source_root / "late.py").unlink()
    removed = False

    def removing_listdir(path: object) -> list[str]:
        nonlocal removed
        names = original_listdir(path)
        if not removed:
            removed = True
            source.unlink()
        return names

    monkeypatch.setattr("quality_gates.source_inventory.os.listdir", removing_listdir)
    with pytest.raises(GateInputError, match="entry changed during scan"):
        _inventory(tmp_path)


def test_inventory_detects_child_directory_and_repository_root_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "app/src"
    child = source_root / "child"
    child.mkdir(parents=True)
    (child / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    original_open = os.open
    swapped = False

    def child_swapping_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if path == "child" and not swapped:
            swapped = True
            child.rename(source_root / "old-child")
            child.mkdir()
            (child / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr("quality_gates.source_inventory.os.open", child_swapping_open)
    with pytest.raises(GateInputError, match="directory changed during scan"):
        _inventory(tmp_path)

    monkeypatch.setattr("quality_gates.source_inventory.os.open", original_open)
    repository = tmp_path / "root-case"
    root_source = repository / "app/src/app.py"
    root_source.parent.mkdir(parents=True)
    root_source.write_text("VALUE = 1\n", encoding="utf-8")
    backup = tmp_path / "root-backup"
    root_swapped = False

    def root_swapping_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal root_swapped
        if path == "app.py" and not root_swapped:
            root_swapped = True
            repository.rename(backup)
            repository.mkdir()
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr("quality_gates.source_inventory.os.open", root_swapping_open)
    with pytest.raises(GateInputError, match="repository root changed"):
        _inventory(repository)


def test_inventory_detects_declared_source_root_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "app/src"
    source_root.mkdir(parents=True)
    (source_root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    original_open = os.open
    swapped = False

    def source_root_swapping_open(
        path: object, flags: int, *args: object, **kwargs: object
    ) -> int:
        nonlocal swapped
        if path == "app.py" and not swapped:
            swapped = True
            source_root.rename(tmp_path / "old-src")
            source_root.mkdir()
            (source_root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(
        "quality_gates.source_inventory.os.open", source_root_swapping_open
    )
    with pytest.raises(GateInputError, match="source inventory (?:root|directory) changed"):
        _inventory(tmp_path)


def test_inventory_detects_explicit_file_replacement_after_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "launcher.py"
    explicit.write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "app/src").mkdir(parents=True)
    policy = _policy()
    policy["files"] = [
        {"language": "python", "module": "app", "path": "launcher.py"}
    ]
    replacement = tmp_path / "replacement"
    replacement.write_text("VALUE = 2\n", encoding="utf-8")
    original_read = source_inventory_module._read_open_file
    swapped = False

    def replacing_read(*args: object, **kwargs: object) -> bytes:
        nonlocal swapped
        raw = original_read(*args, **kwargs)
        if not swapped and kwargs.get("field") == "source inventory file launcher.py":
            swapped = True
            explicit.unlink()
            replacement.rename(explicit)
        return raw

    monkeypatch.setattr(
        "quality_gates.source_inventory._read_open_file", replacing_read
    )
    with pytest.raises(GateInputError, match="file changed during scan"):
        resolve_source_inventory(
            policy,
            policy_sha256="a" * 64,
            repository_root=tmp_path,
        )


def test_inventory_language_and_suffix_must_agree(tmp_path: Path) -> None:
    source_root = tmp_path / "app/src"
    source_root.mkdir(parents=True)
    (source_root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    policy = _policy()
    policy["roots"][0]["language"] = "java"
    with pytest.raises(GateInputError, match="root entry is malformed"):
        resolve_source_inventory(policy, policy_sha256="a" * 64, repository_root=tmp_path)

    policy = _policy()
    policy["files"] = [
        {"language": "java", "module": "app", "path": "app/src/app.py"}
    ]
    with pytest.raises(GateInputError, match="file entry is malformed"):
        resolve_source_inventory(policy, policy_sha256="a" * 64, repository_root=tmp_path)


def test_inventory_digest_binds_content_ownership_disposition_and_policy(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "app/src"
    source_root.mkdir(parents=True)
    source = source_root / "app.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    original = _inventory(tmp_path)
    assert source_inventory_digest(original) == original["inventorySha256"]

    source.write_text("VALUE = 2\n", encoding="utf-8")
    content_changed = _inventory(tmp_path)
    assert content_changed["inventorySha256"] != original["inventorySha256"]

    source.write_text("VALUE = 1\n", encoding="utf-8")
    (source_root / "added.py").write_text("ADDED = 1\n", encoding="utf-8")
    added = _inventory(tmp_path)
    assert added["inventorySha256"] != original["inventorySha256"]
    (source_root / "added.py").unlink()

    policy = _policy()
    policy["roots"][0]["module"] = "renamed-app"
    ownership_changed = resolve_source_inventory(
        policy, policy_sha256="a" * 64, repository_root=tmp_path
    )
    assert ownership_changed["inventorySha256"] != original["inventorySha256"]

    policy = _policy()
    policy["nonExecutableSources"] = [
        {
            "path": "app/src/app.py",
            "reason": "Reviewed descriptor-only source.",
            "owner": "application-owner",
            "reviewer": "quality-reviewer",
        }
    ]
    disposition_changed = resolve_source_inventory(
        policy, policy_sha256="a" * 64, repository_root=tmp_path
    )
    assert disposition_changed["inventorySha256"] != original["inventorySha256"]

    policy_changed = resolve_source_inventory(
        _policy(), policy_sha256="b" * 64, repository_root=tmp_path
    )
    assert policy_changed["inventorySha256"] != original["inventorySha256"]


def test_evaluator_rejects_report_from_a_stale_source_inventory_epoch(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "app/src"
    source_root.mkdir(parents=True)
    source = source_root / "app.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    before = _inventory(tmp_path)
    module = _report({"app/src/app.py": ([1], [1], {})})
    module["sourceInventorySha256"] = before["inventorySha256"]
    aggregate = _aggregate(module)
    baseline = capture_coverage_baseline(
        [module, aggregate],
        comparison_base=BASE,
        source_snapshot_sha256="b" * 64,
        required_domains={"python:app", "python:@repository"},
        source_inventory=before,
    )

    source.write_text("VALUE = 2\n", encoding="utf-8")
    after = _inventory(tmp_path)
    with pytest.raises(GateInputError, match="source inventory is stale"):
        evaluate_gate(
            changes={
                "schemaVersion": 1,
                "baseCommit": BASE,
                "headCommit": "9" * 40,
                "files": [],
            },
            reports=[module, aggregate],
            baseline=baseline,
            exclusions={"schemaVersion": 1, "entries": []},
            as_of="2026-07-14",
            repository_root=tmp_path,
            source_inventory=after,
        )


def test_inventory_parser_digest_and_validation_defensive_contracts(
    tmp_path: Path,
) -> None:
    with pytest.raises(GateInputError, match="canonically hashed"):
        source_inventory_module._canonical_inventory_digest({"sources": object()})
    with pytest.raises(GateInputError, match="duplicate source inventory policy key"):
        source_inventory_module._parse_policy(b'{"a": 1, "a": 2}')
    with pytest.raises(GateInputError, match="malformed JSON"):
        source_inventory_module._parse_policy(b"\xff")
    with pytest.raises(GateInputError, match="must be an object"):
        source_inventory_module._parse_policy(b"[]")
    with pytest.raises(GateInputError, match="stay inside"):
        load_and_resolve_source_inventory(
            tmp_path.parent / "outside.json", repository_root=tmp_path
        )

    source_root = tmp_path / "app/src"
    source_root.mkdir(parents=True)
    (source_root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    inventory = _inventory(tmp_path)
    malformed = dict(inventory)
    malformed["extra"] = True
    with pytest.raises(GateInputError, match="contract is malformed"):
        source_inventory_digest(malformed)
    malformed = dict(inventory)
    malformed["schemaVersion"] = 2
    with pytest.raises(GateInputError, match="contract is malformed"):
        source_inventory_digest(malformed)
    malformed = json.loads(json.dumps(inventory))
    malformed["sources"][0] = {"path": "app/src/app.py"}
    with pytest.raises(GateInputError, match="entry is malformed"):
        source_inventory_digest(malformed)
    malformed = json.loads(json.dumps(inventory))
    malformed["sources"][0]["module"] = ""
    with pytest.raises(GateInputError, match="entry is malformed or unsorted"):
        source_inventory_digest(malformed)
    malformed = json.loads(json.dumps(inventory))
    malformed["inventorySha256"] = "0" * 64
    with pytest.raises(GateInputError, match="digest mismatch"):
        source_inventory_digest(malformed)


def test_inventory_resolution_policy_branch_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    base = {
        "schemaVersion": 1,
        "roots": [
            {"language": "python", "module": "a", "root": "a", "suffix": ".py"}
        ],
        "files": [],
        "excludedSourceTrees": [],
        "nonExecutableSources": [],
    }
    try:
        with pytest.raises(GateInputError, match="digest is malformed"):
            source_inventory_module._resolve_source_inventory(
                base, policy_sha256="bad", policy_path="policy.json", root_descriptor=descriptor
            )
        malformed = dict(base)
        malformed["extra"] = True
        with pytest.raises(GateInputError, match="contract is malformed"):
            source_inventory_module._resolve_source_inventory(
                malformed, policy_sha256="a" * 64,
                policy_path="policy.json", root_descriptor=descriptor,
            )
        malformed = dict(base)
        malformed["roots"] = []
        with pytest.raises(GateInputError, match="contract is malformed"):
            source_inventory_module._resolve_source_inventory(
                malformed, policy_sha256="a" * 64,
                policy_path="policy.json", root_descriptor=descriptor,
            )
        malformed = dict(base)
        malformed["excludedSourceTrees"] = ["bad"]
        with pytest.raises(GateInputError, match="excluded source tree approval"):
            source_inventory_module._resolve_source_inventory(
                malformed, policy_sha256="a" * 64,
                policy_path="policy.json", root_descriptor=descriptor,
            )
        malformed = dict(base)
        malformed["excludedSourceTrees"] = [
            {"path": "missing", "reason": "x", "owner": "a", "reviewer": "b"}
        ]
        with pytest.raises(GateInputError, match="excluded source tree approval"):
            source_inventory_module._resolve_source_inventory(
                malformed, policy_sha256="a" * 64,
                policy_path="policy.json", root_descriptor=descriptor,
            )
        malformed = dict(base)
        malformed["roots"] = ["bad"]
        with pytest.raises(GateInputError, match="root entry is malformed"):
            source_inventory_module._resolve_source_inventory(
                malformed, policy_sha256="a" * 64,
                policy_path="policy.json", root_descriptor=descriptor,
            )

        monkeypatch.setattr(source_inventory_module, "_scan_root", lambda *args: {})
        malformed = dict(base)
        malformed["files"] = ["bad"]
        with pytest.raises(GateInputError, match="file entry is malformed"):
            source_inventory_module._resolve_source_inventory(
                malformed, policy_sha256="a" * 64,
                policy_path="policy.json", root_descriptor=descriptor,
            )
        malformed = dict(base)
        malformed["files"] = [
            {"language": "ruby", "module": "a", "path": "a/file.py"}
        ]
        with pytest.raises(GateInputError, match="file entry is malformed"):
            source_inventory_module._resolve_source_inventory(
                malformed, policy_sha256="a" * 64,
                policy_path="policy.json", root_descriptor=descriptor,
            )
        with pytest.raises(GateInputError, match="resolved no source files"):
            source_inventory_module._resolve_source_inventory(
                base, policy_sha256="a" * 64,
                policy_path="policy.json", root_descriptor=descriptor,
            )

        monkeypatch.setattr(
            source_inventory_module, "_scan_root", lambda *args: {"shared.py": "1" * 64}
        )
        duplicate_roots = dict(base)
        duplicate_roots["roots"] = [
            {"language": "python", "module": "a", "root": "a", "suffix": ".py"},
            {"language": "python", "module": "b", "root": "b", "suffix": ".py"},
        ]
        with pytest.raises(GateInputError, match="multiple owners"):
            source_inventory_module._resolve_source_inventory(
                duplicate_roots, policy_sha256="a" * 64,
                policy_path="policy.json", root_descriptor=descriptor,
            )

        calls = 0
        def changing_scan(*args: object) -> dict[str, str]:
            nonlocal calls
            calls += 1
            return {"a/app.py": ("1" if calls == 1 else "2") * 64}
        monkeypatch.setattr(source_inventory_module, "_scan_root", changing_scan)
        with pytest.raises(GateInputError, match="changed between complete scans"):
            source_inventory_module._resolve_source_inventory(
                base, policy_sha256="a" * 64,
                policy_path="policy.json", root_descriptor=descriptor,
            )

        monkeypatch.setattr(
            source_inventory_module, "_scan_root", lambda *args: {"a/app.py": "1" * 64}
        )
        malformed = dict(base)
        malformed["nonExecutableSources"] = ["bad"]
        with pytest.raises(GateInputError, match="non-executable source approval"):
            source_inventory_module._resolve_source_inventory(
                malformed, policy_sha256="a" * 64,
                policy_path="policy.json", root_descriptor=descriptor,
            )
        malformed = dict(base)
        malformed["nonExecutableSources"] = [
            {"path": "a/app.py", "reason": "", "owner": "a", "reviewer": "b"}
        ]
        with pytest.raises(GateInputError, match="non-executable source approval"):
            source_inventory_module._resolve_source_inventory(
                malformed, policy_sha256="a" * 64,
                policy_path="policy.json", root_descriptor=descriptor,
            )
    finally:
        os.close(descriptor)


def test_inventory_low_level_io_failure_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "src"
    child = source_root / "child"
    child.mkdir(parents=True)
    (source_root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source_root / "ignore.txt").write_text("ignore\n", encoding="utf-8")
    root_descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    original_close = os.close
    original_open = os.open
    original_listdir = os.listdir
    original_fstat = os.fstat
    try:
        with pytest.raises(GateInputError, match="trusted directory"):
            source_inventory_module._open_directory_at(
                root_descriptor, "missing", "directory"
            )
        with monkeypatch.context() as scoped:
            def closing_with_error(fd: int) -> None:
                original_close(fd)
                raise OSError("simulated close failure")
            scoped.setattr(source_inventory_module.os, "close", closing_with_error)
            with pytest.raises(GateInputError, match="trusted directory"):
                source_inventory_module._open_directory_at(
                    root_descriptor, "missing", "directory"
                )

        file_descriptor = os.open(source_root / "app.py", os.O_RDONLY)
        try:
            fstat_calls = 0
            with monkeypatch.context() as scoped:
                def changing_fstat(fd: int):
                    nonlocal fstat_calls
                    value = original_fstat(fd)
                    fstat_calls += 1
                    if fstat_calls == 2:
                        fields = {
                            name: getattr(value, name)
                            for name in (
                                "st_dev", "st_ino", "st_mode", "st_size",
                                "st_mtime_ns", "st_ctime_ns",
                            )
                        }
                        fields["st_mtime_ns"] += 1
                        return SimpleNamespace(**fields)
                    return value
                scoped.setattr(source_inventory_module.os, "fstat", changing_fstat)
                with pytest.raises(GateInputError, match="changed while it was read"):
                    source_inventory_module._read_open_file(
                        file_descriptor, field="source", size_limit=1024
                    )
        finally:
            os.close(file_descriptor)

        with pytest.raises(GateInputError, match="not a trusted regular file"):
            source_inventory_module._read_explicit_source_stable(
                root_descriptor, "src/missing.py"
            )
        with monkeypatch.context() as scoped:
            scoped.setattr(
                source_inventory_module.os,
                "listdir",
                lambda directory: (_ for _ in ()).throw(OSError("unreadable")),
            )
            with pytest.raises(GateInputError, match="directory is unreadable"):
                source_inventory_module._scan_root(root_descriptor, "src", ".py", set())
        with monkeypatch.context() as scoped:
            scoped.setattr(source_inventory_module.os, "listdir", lambda directory: [".."])
            with pytest.raises(GateInputError, match="unsafe name"):
                source_inventory_module._scan_root(root_descriptor, "src", ".py", set())
        with monkeypatch.context() as scoped:
            def child_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
                if path == "child":
                    raise OSError("child changed")
                return original_open(path, flags, *args, **kwargs)
            scoped.setattr(source_inventory_module.os, "open", child_open)
            with pytest.raises(GateInputError, match="directory changed"):
                source_inventory_module._scan_root(root_descriptor, "src", ".py", set())
        with monkeypatch.context() as scoped:
            def file_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
                if path == "app.py":
                    raise OSError("file changed")
                return original_open(path, flags, *args, **kwargs)
            scoped.setattr(source_inventory_module.os, "open", file_open)
            with pytest.raises(GateInputError, match="file changed"):
                source_inventory_module._scan_root(
                    root_descriptor, "src", ".py", {"src/child"}
                )
        with monkeypatch.context() as scoped:
            list_calls = 0
            def failing_final_list(directory: int) -> list[str]:
                nonlocal list_calls
                list_calls += 1
                if list_calls == 2:
                    raise OSError("changed")
                return original_listdir(directory)
            scoped.setattr(source_inventory_module.os, "listdir", failing_final_list)
            with pytest.raises(GateInputError, match="directory changed"):
                source_inventory_module._scan_root(
                    root_descriptor, "src", ".py", {"src/child"}
                )
        assert "src/app.py" in source_inventory_module._scan_root(
            root_descriptor, "src", ".py", {"src/child"}
        )
    finally:
        os.close(root_descriptor)


def test_report_reconciliation_duplicate_and_aggregate_language_contracts(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "app/src"
    source_root.mkdir(parents=True)
    (source_root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    inventory = _inventory(tmp_path)
    module = _report({"app/src/app.py": ([1], [1], {})})
    aggregate = _aggregate(module)
    with pytest.raises(GateInputError, match="duplicate repository aggregate"):
        reconcile_reports_with_inventory(
            [module, aggregate, aggregate], inventory, require_aggregates=True
        )
    with pytest.raises(GateInputError, match="duplicate inventory report module"):
        reconcile_reports_with_inventory([module, module], inventory)
    unowned = _report({"other.py": ([1], [1], {})})
    with pytest.raises(GateInputError, match="unowned source"):
        reconcile_reports_with_inventory([unowned], inventory)
    with pytest.raises(GateInputError, match="aggregate language inventory"):
        reconcile_reports_with_inventory([module], inventory, require_aggregates=True)


def test_inventory_residual_descriptor_swap_and_close_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "app"
    parent.mkdir()
    source = parent / "value.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    root_descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    original_close = os.close
    original_open = os.open
    original_fstat = os.fstat
    original_open_directory = source_inventory_module._open_directory_at
    try:
        with monkeypatch.context() as scoped:
            def noisy_close(fd: int) -> None:
                original_close(fd)
                raise OSError("simulated close failure")
            scoped.setattr(source_inventory_module.os, "close", noisy_close)
            assert source_inventory_module._read_file_at(
                root_descriptor, "app/value.py", field="source", size_limit=1024
            ) == b"VALUE = 1\n"

        with monkeypatch.context() as scoped:
            fstat_calls = 0
            def mismatched_fstat(fd: int):
                nonlocal fstat_calls
                value = original_fstat(fd)
                fstat_calls += 1
                if fstat_calls == 2:
                    fields = {
                        name: getattr(value, name)
                        for name in (
                            "st_dev", "st_ino", "st_mode", "st_size",
                            "st_mtime_ns", "st_ctime_ns",
                        )
                    }
                    fields["st_ino"] += 1
                    return SimpleNamespace(**fields)
                return value
            scoped.setattr(source_inventory_module.os, "fstat", mismatched_fstat)
            with pytest.raises(GateInputError, match="file changed during scan"):
                source_inventory_module._read_explicit_source_stable(
                    root_descriptor, "app/value.py"
                )

        with monkeypatch.context() as scoped:
            file_opens = 0
            def failing_reopen(path: object, flags: int, *args: object, **kwargs: object) -> int:
                nonlocal file_opens
                if path == "value.py":
                    file_opens += 1
                    if file_opens == 2:
                        raise OSError("reopen failed")
                return original_open(path, flags, *args, **kwargs)
            scoped.setattr(source_inventory_module.os, "open", failing_reopen)
            with pytest.raises(GateInputError, match="file changed during scan"):
                source_inventory_module._read_explicit_source_stable(
                    root_descriptor, "app/value.py"
                )

        with monkeypatch.context() as scoped:
            directory_opens = 0
            def swapped_parent(root: int, path: str, field: str) -> int:
                nonlocal directory_opens
                directory_opens += 1
                if directory_opens == 2:
                    return original_open(other, os.O_RDONLY | os.O_DIRECTORY)
                return original_open_directory(root, path, field)
            scoped.setattr(source_inventory_module, "_open_directory_at", swapped_parent)
            with pytest.raises(GateInputError, match="parent changed during scan"):
                source_inventory_module._read_explicit_source_stable(
                    root_descriptor, "app/value.py"
                )

        with monkeypatch.context() as scoped:
            directory_opens = 0
            def swapped_root(root: int, path: str, field: str) -> int:
                nonlocal directory_opens
                directory_opens += 1
                if directory_opens == 2:
                    return original_open(other, os.O_RDONLY | os.O_DIRECTORY)
                return original_open_directory(root, path, field)
            scoped.setattr(source_inventory_module, "_open_directory_at", swapped_root)
            with pytest.raises(GateInputError, match="root changed during scan"):
                source_inventory_module._scan_root(
                    root_descriptor, "app", ".py", set()
                )
    finally:
        os.close(root_descriptor)


def test_inventory_residual_duplicate_ownership_rechecks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    base = {
        "schemaVersion": 1,
        "roots": [
            {"language": "python", "module": "a", "root": "a", "suffix": ".py"}
        ],
        "files": [],
        "excludedSourceTrees": [],
        "nonExecutableSources": [],
    }
    try:
        monkeypatch.setattr(
            source_inventory_module, "_scan_root",
            lambda *args: {"explicit.py": "1" * 64},
        )
        monkeypatch.setattr(
            source_inventory_module, "_read_explicit_source_stable", lambda *args: b"x"
        )
        explicit = dict(base)
        explicit["files"] = [
            {"language": "python", "module": "a", "path": "explicit.py"}
        ]
        with pytest.raises(GateInputError, match="multiple owners"):
            source_inventory_module._resolve_source_inventory(
                explicit, policy_sha256="a" * 64,
                policy_path="policy.json", root_descriptor=descriptor,
            )

        calls = {"a": 0, "b": 0}
        def duplicate_recheck(root: int, relative: str, *args: object) -> dict[str, str]:
            calls[relative] += 1
            if calls[relative] == 1:
                return {f"{relative}/value.py": "1" * 64}
            return {"shared.py": "1" * 64}
        monkeypatch.setattr(source_inventory_module, "_scan_root", duplicate_recheck)
        two_roots = dict(base)
        two_roots["roots"] = [
            {"language": "python", "module": "a", "root": "a", "suffix": ".py"},
            {"language": "python", "module": "b", "root": "b", "suffix": ".py"},
        ]
        two_roots["files"] = []
        with pytest.raises(GateInputError, match="multiple owners"):
            source_inventory_module._resolve_source_inventory(
                two_roots, policy_sha256="a" * 64,
                policy_path="policy.json", root_descriptor=descriptor,
            )

        scan_calls = 0
        def explicit_recheck(*args: object) -> dict[str, str]:
            nonlocal scan_calls
            scan_calls += 1
            return (
                {"a/root.py": "1" * 64}
                if scan_calls == 1
                else {"explicit.py": "1" * 64}
            )
        monkeypatch.setattr(source_inventory_module, "_scan_root", explicit_recheck)
        with pytest.raises(GateInputError, match="multiple owners"):
            source_inventory_module._resolve_source_inventory(
                explicit, policy_sha256="a" * 64,
                policy_path="policy.json", root_descriptor=descriptor,
            )
    finally:
        os.close(descriptor)


def test_report_reconciliation_rejects_a_second_owner_after_identity_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ShiftingSource(dict):
        index = -1

        def __getitem__(self, key: str):
            if key == "coverageDisposition":
                return "required"
            if key == "language":
                self.index += 1
                return ("python", "java")[self.index]
            if key == "module":
                return ("one", "two")[self.index]
            return super().__getitem__(key)

    monkeypatch.setattr(
        source_inventory_module,
        "validate_source_inventory",
        lambda inventory: {"same.py": ShiftingSource()},
    )
    first = _report({"same.py": ([1], [1], {})})
    first["module"] = "one"
    second = _report({"same.py": ([1], [1], {})})
    second["language"] = "java"
    second["module"] = "two"
    second["adapter"] = "jacoco-xml"
    with pytest.raises(GateInputError, match="reported more than once"):
        reconcile_reports_with_inventory([first, second], {})


def test_file_level_baseline_residual_contract_and_new_source_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "app/src"
    source_root.mkdir(parents=True)
    source = source_root / "rules.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    inventory = _inventory(tmp_path)
    module = _report(
        {"app/src/rules.py": ([1], [1], {"1": {"covered": 2, "missed": 0}})}
    )
    module["sourceInventorySha256"] = inventory["inventorySha256"]
    aggregate = _aggregate(module)
    baseline = capture_coverage_baseline(
        [module, aggregate], comparison_base=BASE,
        source_snapshot_sha256="b" * 64,
        required_domains={"python:app", "python:@repository"},
        source_inventory=inventory,
    )
    common = {
        "changes": {
            "schemaVersion": 1, "baseCommit": BASE,
            "headCommit": "9" * 40, "files": [],
        },
        "reports": [module, aggregate],
        "baseline": baseline,
        "exclusions": {"schemaVersion": 1, "entries": []},
        "as_of": "2026-07-14",
        "repository_root": tmp_path,
        "source_inventory": inventory,
    }

    malformed = json.loads(json.dumps(baseline))
    malformed["files"]["app/src/rules.py"]["extra"] = True
    with pytest.raises(GateInputError, match="file contract is malformed or unsorted"):
        _evaluate_unbound_gate(**{**common, "baseline": malformed})
    malformed = json.loads(json.dumps(baseline))
    malformed["files"]["app/src/rules.py"]["domain"] = "python:missing"
    with pytest.raises(GateInputError, match="baseline file contract is malformed"):
        _evaluate_unbound_gate(**{**common, "baseline": malformed})
    malformed = json.loads(json.dumps(baseline))
    malformed["files"]["app/src/rules.py"]["branchShape"] = {"1": 0}
    with pytest.raises(GateInputError, match="branch shape is malformed"):
        _evaluate_unbound_gate(**{**common, "baseline": malformed})

    other_inventory = json.loads(json.dumps(inventory))
    other_inventory["policyPath"] = "other-policy.json"
    other_inventory["inventorySha256"] = source_inventory_module._canonical_inventory_digest(
        other_inventory
    )
    other_module = json.loads(json.dumps(module))
    other_module["sourceInventorySha256"] = other_inventory["inventorySha256"]
    other_aggregate = _aggregate(other_module)
    with pytest.raises(GateInputError, match="policy does not match coverage baseline"):
        _evaluate_unbound_gate(
            **{
                **common,
                "reports": [other_module, other_aggregate],
                "source_inventory": other_inventory,
            }
        )

    non_executable = json.loads(json.dumps(inventory))
    non_executable["sources"][0]["coverageDisposition"] = "nonExecutable"
    non_executable["inventorySha256"] = source_inventory_module._canonical_inventory_digest(
        non_executable
    )
    nonexec_module = json.loads(json.dumps(module))
    nonexec_module["sourceInventorySha256"] = non_executable["inventorySha256"]
    nonexec_aggregate = _aggregate(nonexec_module)
    monkeypatch.setattr(
        source_inventory_module,
        "reconcile_reports_with_inventory",
        lambda reports, inventory, require_aggregates: {
            "app/src/rules.py": nonexec_module["files"]["app/src/rules.py"]
        },
    )
    with pytest.raises(GateInputError, match="not a required inventory source"):
        _evaluate_unbound_gate(
            **{
                **common,
                "reports": [nonexec_module, nonexec_aggregate],
                "source_inventory": non_executable,
            }
        )
    monkeypatch.undo()

    no_files_baseline = json.loads(json.dumps(baseline))
    no_files_baseline["files"] = {}
    with pytest.raises(GateInputError, match="lacks a declared Git change"):
        _evaluate_unbound_gate(**{**common, "baseline": no_files_baseline})

    added = {
        "schemaVersion": 1, "baseCommit": BASE, "headCommit": "9" * 40,
        "files": [
            {
                "path": "app/src/rules.py", "status": "added",
                "correctnessCritical": True, "language": "python", "changedLines": [1],
            }
        ],
    }
    empty = _report({"app/src/rules.py": ([], [], {})})
    empty["sourceInventorySha256"] = inventory["inventorySha256"]
    with pytest.raises(GateInputError, match="has no executable lines"):
        _evaluate_unbound_gate(
            **{
                **common, "changes": added, "baseline": no_files_baseline,
                "reports": [empty, _aggregate(empty)],
            }
        )
    partial = _report({"app/src/rules.py": ([1], [], {})})
    partial["sourceInventorySha256"] = inventory["inventorySha256"]
    with pytest.raises(GateInputError, match="new inventory source is not fully covered"):
        _evaluate_unbound_gate(
            **{
                **common, "changes": added, "baseline": no_files_baseline,
                "reports": [partial, _aggregate(partial)],
            }
        )
    full = _report({"app/src/rules.py": ([1], [1], {"1": {"covered": 2, "missed": 0}})})
    full["sourceInventorySha256"] = inventory["inventorySha256"]
    assert _evaluate_unbound_gate(
        **{
            **common, "changes": added, "baseline": no_files_baseline,
            "reports": [full, _aggregate(full)],
        }
    ).passed
    assert _evaluate_unbound_gate(**common).passed

    changed_inventory = json.loads(json.dumps(inventory))
    changed_inventory["sources"][0]["sha256"] = "0" * 64
    changed_inventory["inventorySha256"] = source_inventory_module._canonical_inventory_digest(
        changed_inventory
    )
    changed_empty = _report({"app/src/rules.py": ([], [], {})})
    changed_empty["sourceInventorySha256"] = changed_inventory["inventorySha256"]
    with pytest.raises(GateInputError, match="changed required source has no executable lines"):
        _evaluate_unbound_gate(
            **{
                **common,
                "reports": [changed_empty, _aggregate(changed_empty)],
                "source_inventory": changed_inventory,
            }
        )


def test_inventory_explicit_source_successfully_survives_the_second_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a").mkdir()
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    policy = {
        "schemaVersion": 1,
        "roots": [
            {"language": "python", "module": "a", "root": "a", "suffix": ".py"}
        ],
        "files": [
            {"language": "python", "module": "a", "path": "explicit.py"}
        ],
        "excludedSourceTrees": [],
        "nonExecutableSources": [],
    }
    monkeypatch.setattr(source_inventory_module, "_scan_root", lambda *args: {})
    monkeypatch.setattr(
        source_inventory_module, "_read_explicit_source_stable", lambda *args: b"value"
    )
    try:
        inventory = source_inventory_module._resolve_source_inventory(
            policy, policy_sha256="a" * 64,
            policy_path="policy.json", root_descriptor=descriptor,
        )
    finally:
        os.close(descriptor)
    assert inventory["sources"][0]["path"] == "explicit.py"
