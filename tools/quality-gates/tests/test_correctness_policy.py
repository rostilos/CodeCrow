from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


QUALITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QUALITY_ROOT))

from quality_gates import GateInputError  # noqa: E402
from quality_gates.changed_coverage import _evaluate_unbound_gate  # noqa: E402
from quality_gates.correctness_policy import (  # noqa: E402
    classify_path,
    load_correctness_policy,
    validate_correctness_policy,
)
from quality_gates import correctness_policy as correctness_module  # noqa: E402
from quality_gates.git_changes import resolve_git_changes  # noqa: E402


def _policy() -> dict:
    return {
        "schemaVersion": 1,
        "scopeRoots": [".github", "deployment", "java-ecosystem", "python-ecosystem", "tools"],
        "languageSuffixes": {".java": "java", ".py": "python"},
        "nonCriticalPaths": [
            {
                "glob": "python-ecosystem/*/tests/**/*.py",
                "reason": "Test-only source.",
                "owner": "test-owner",
                "reviewer": "quality-reviewer",
            }
        ],
    }


def test_policy_parser_glob_and_identity_defensive_paths(tmp_path: Path) -> None:
    assert correctness_module._glob_matches("a", "?")
    with pytest.raises(GateInputError, match="duplicate correctness policy key"):
        correctness_module._parse(b'{"a": 1, "a": 2}')
    with pytest.raises(GateInputError, match="malformed JSON"):
        correctness_module._parse(b"\xff")
    with pytest.raises(GateInputError, match="must be an object"):
        correctness_module._parse(b"[]")
    with pytest.raises(GateInputError, match="stay inside"):
        load_correctness_policy(tmp_path.parent / "outside.json", repository_root=tmp_path)
    with pytest.raises(GateInputError, match="digest is malformed"):
        correctness_module.correctness_policy_identity(
            _policy(), path="policy.json", sha256="bad"
        )


def test_policy_contract_rejects_top_level_root_and_approval_shapes() -> None:
    malformed = _policy()
    malformed["extra"] = True
    with pytest.raises(GateInputError, match="contract is malformed"):
        validate_correctness_policy(malformed)

    malformed = _policy()
    malformed["scopeRoots"] = []
    with pytest.raises(GateInputError, match="contract is malformed"):
        validate_correctness_policy(malformed)

    malformed = _policy()
    malformed["nonCriticalPaths"] = ["not-an-object"]
    with pytest.raises(GateInputError, match="approval is malformed"):
        validate_correctness_policy(malformed)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("java-ecosystem/pom.xml", (True, None)),
        (".github/workflows/offline-tests.yml", (True, None)),
        ("deployment/build/production-build.sh", (True, None)),
        ("python-ecosystem/demo/pyproject.toml", (True, None)),
        ("java-ecosystem/new-layout/Guard.java", (True, "java")),
        ("python-ecosystem/new-layout/guard.py", (True, "python")),
        ("python-ecosystem/demo/tests/test_guard.py", (False, None)),
        ("README.md", (False, None)),
    ],
)
def test_versioned_policy_is_default_critical_across_layouts(
    path: str, expected: tuple[bool, str | None]
) -> None:
    assert classify_path(path, _policy()) == expected


@pytest.mark.parametrize(
    "path",
    [
        "java-ecosystem/libs/demo/src/test/resources/application.yml",
        "python-ecosystem/demo/tests/fixture.json",
        "python-ecosystem/demo/integration/cassette.yaml",
        "tools/quality-gates/tests/fixtures/report.json",
        "tools/offline-harness/fixtures/ledger.json",
    ],
)
def test_checked_in_policy_marks_all_reviewed_test_material_noncritical(
    path: str,
) -> None:
    repository = QUALITY_ROOT.parents[1]
    policy, _, _ = load_correctness_policy(
        QUALITY_ROOT / "policy/correctness-policy-v1.json",
        repository_root=repository,
    )
    assert classify_path(path, policy) == (False, None)


def test_moving_runtime_code_to_an_unknown_layout_does_not_bypass_classification() -> None:
    policy = _policy()
    assert classify_path("python-ecosystem/demo/src/guard.py", policy) == (True, "python")
    assert classify_path("python-ecosystem/demo/moved/guard.py", policy) == (True, "python")
    assert classify_path("tools/new-runner", policy) == (True, None)


def test_noncritical_approvals_are_exact_independent_and_nonoverlapping() -> None:
    policy = _policy()
    duplicate = copy.deepcopy(policy["nonCriticalPaths"][0])
    duplicate["glob"] = "python-ecosystem/demo/tests/**/*.py"
    policy["nonCriticalPaths"].append(duplicate)
    with pytest.raises(GateInputError, match="multiple non-critical path approvals"):
        classify_path("python-ecosystem/demo/tests/test_guard.py", policy)

    policy = _policy()
    policy["nonCriticalPaths"][0]["reviewer"] = "test-owner"
    with pytest.raises(GateInputError, match="non-critical path approval"):
        validate_correctness_policy(policy)


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def test_resolver_binds_policy_and_rename_target_is_reclassified(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "quality@example.invalid")
    _git(repo, "config", "user.name", "Quality Gate")
    source = repo / "python-ecosystem/demo/tests/test_guard.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    target = repo / "python-ecosystem/demo/runtime/test_guard.py"
    target.parent.mkdir(parents=True)
    _git(
        repo,
        "mv",
        "python-ecosystem/demo/tests/test_guard.py",
        "python-ecosystem/demo/runtime/test_guard.py",
    )
    identity_path = "tools/quality-gates/policy/correctness-policy-v1.json"
    result = resolve_git_changes(
        repo,
        base_commit=base,
        include_worktree=True,
        correctness_policy=_policy(),
        correctness_policy_path=identity_path,
        correctness_policy_sha256="a" * 64,
    )
    entry = result["files"][0]
    assert entry["oldPath"].endswith("tests/test_guard.py")
    assert entry["path"].endswith("runtime/test_guard.py")
    assert entry["correctnessCritical"] is True
    assert entry["language"] == "python"
    assert result["correctnessPolicy"] == {
        "path": identity_path,
        "sha256": "a" * 64,
    }


def test_rename_or_copy_from_runtime_into_test_layout_stays_critical(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "quality@example.invalid")
    _git(repo, "config", "user.name", "Quality Gate")
    for name in ("rename_guard.py", "copy_guard.py"):
        source = repo / f"python-ecosystem/demo/src/{name}"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    tests = repo / "python-ecosystem/demo/tests"
    tests.mkdir(parents=True)
    _git(
        repo,
        "mv",
        "python-ecosystem/demo/src/rename_guard.py",
        "python-ecosystem/demo/tests/rename_guard.py",
    )
    (tests / "copy_guard.py").write_text(
        (repo / "python-ecosystem/demo/src/copy_guard.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "move and copy")
    result = resolve_git_changes(
        repo,
        base_commit=base,
        include_worktree=True,
        correctness_policy=_policy(),
        correctness_policy_path="policy.json",
        correctness_policy_sha256="a" * 64,
    )
    by_path = {entry["path"]: entry for entry in result["files"]}
    for path in (
        "python-ecosystem/demo/tests/rename_guard.py",
        "python-ecosystem/demo/tests/copy_guard.py",
    ):
        assert by_path[path]["correctnessCritical"] is True
        assert by_path[path]["language"] == "python"


def test_evaluator_recomputes_and_rejects_a_false_critical_bit(tmp_path: Path) -> None:
    policy = _policy()
    source = tmp_path / "deployment/build/release.sh"
    source.parent.mkdir(parents=True)
    source.write_text("exit 0\n", encoding="utf-8")
    changes = {
        "schemaVersion": 1,
        "baseCommit": "8" * 40,
        "headCommit": "9" * 40,
        "mergeBase": "8" * 40,
        "dirty": True,
        "correctnessPolicy": {"path": "policy.json", "sha256": "a" * 64},
        "files": [
            {
                "path": "deployment/build/release.sh",
                "status": "added",
                "correctnessCritical": False,
                "language": None,
                "changedLines": [1],
                "contentSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ],
    }
    with pytest.raises(GateInputError, match="classification does not match policy"):
        _evaluate_unbound_gate(
            changes=changes,
            reports=[],
            baseline={"schemaVersion": 1, "comparisonBase": "8" * 40, "domains": {}},
            exclusions={"schemaVersion": 1, "entries": []},
            as_of="2026-07-14",
            repository_root=tmp_path,
            correctness_policy=policy,
            correctness_policy_path="policy.json",
            correctness_policy_sha256="a" * 64,
        )


def test_repository_policy_load_binds_exact_bytes() -> None:
    repository = QUALITY_ROOT.parents[1]
    path = repository / "tools/quality-gates/policy/correctness-policy-v1.json"
    policy, relative, digest = load_correctness_policy(path, repository_root=repository)
    assert relative == "tools/quality-gates/policy/correctness-policy-v1.json"
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert classify_path("java-ecosystem/pom.xml", policy) == (True, None)
    for verification_path in (
        ".github/CODEOWNERS",
        "python-ecosystem/inference-orchestrator/pytest.ini",
        "python-ecosystem/test-support/codecrow_test_harness/network.py",
        "tools/offline-harness/bin/run-offline.sh",
    ):
        assert classify_path(verification_path, policy) == (False, None)


def test_change_inventory_digest_binds_noncritical_worktree_bytes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "quality@example.invalid")
    _git(repo, "config", "user.name", "Quality Gate")
    runtime = repo / "python-ecosystem/demo/src/runtime.py"
    test_file = repo / "python-ecosystem/demo/tests/test_runtime.py"
    runtime.parent.mkdir(parents=True)
    test_file.parent.mkdir(parents=True)
    runtime.write_text("VALUE = 1\n", encoding="utf-8")
    test_file.write_text("EXPECTED = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    runtime.write_text("VALUE = 2\n", encoding="utf-8")
    test_file.write_text("EXPECTED = 2\n", encoding="utf-8")
    first = resolve_git_changes(
        repo,
        base_commit=base,
        include_worktree=True,
        correctness_policy=_policy(),
        correctness_policy_path="policy.json",
        correctness_policy_sha256="a" * 64,
    )
    first_digest = hashlib.sha256(
        json.dumps(first, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    first_test = next(
        entry for entry in first["files"] if entry["path"].endswith("test_runtime.py")
    )
    assert first_test["correctnessCritical"] is False

    test_file.write_text("EXPECTED = 3\n", encoding="utf-8")
    second = resolve_git_changes(
        repo,
        base_commit=base,
        include_worktree=True,
        correctness_policy=_policy(),
        correctness_policy_path="policy.json",
        correctness_policy_sha256="a" * 64,
    )
    second_digest = hashlib.sha256(
        json.dumps(second, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    second_test = next(
        entry for entry in second["files"] if entry["path"].endswith("test_runtime.py")
    )
    assert first_test["contentSha256"] != second_test["contentSha256"]
    assert first_digest != second_digest

    with pytest.raises(GateInputError, match="changed repository file digest mismatch"):
        _evaluate_unbound_gate(
            changes=first,
            reports=[],
            baseline={"schemaVersion": 1, "comparisonBase": base, "domains": {}},
            exclusions={"schemaVersion": 1, "entries": []},
            as_of="2026-07-14",
            repository_root=repo,
            correctness_policy=_policy(),
            correctness_policy_path="policy.json",
            correctness_policy_sha256="a" * 64,
        )
