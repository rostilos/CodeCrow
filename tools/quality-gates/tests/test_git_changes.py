from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


QUALITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QUALITY_ROOT))

from quality_gates import GateInputError  # noqa: E402
from quality_gates.git_changes import (  # noqa: E402
    load_comparison_base,
    load_comparison_base_attestation,
    resolve_git_changes,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "quality@example.invalid")
    _git(repo, "config", "user.name", "Quality Gate")
    files = {
        "python-ecosystem/demo/src/rules.py": "VALUE = 1\n",
        "python-ecosystem/demo/src/rename.py": "RENAMED = 1\n",
        "python-ecosystem/demo/src/delete.py": "DELETED = 1\n",
        "python-ecosystem/demo/src/copy.py": "COPIED = 1\n",
    }
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_resolve_git_changes_handles_modify_rename_copy_delete_and_untracked(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    rules = repo / "python-ecosystem/demo/src/rules.py"
    rules.write_text("VALUE = 1\nENABLED = True\n", encoding="utf-8")
    _git(
        repo,
        "mv",
        "python-ecosystem/demo/src/rename.py",
        "python-ecosystem/demo/src/renamed.py",
    )
    _git(repo, "rm", "-q", "python-ecosystem/demo/src/delete.py")
    copied = repo / "python-ecosystem/demo/src/copied.py"
    copied.write_text(
        (repo / "python-ecosystem/demo/src/copy.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "changes")
    untracked = repo / "java-ecosystem/libs/demo/src/main/java/example/NewGuard.java"
    untracked.parent.mkdir(parents=True)
    untracked.write_text("final class NewGuard {}\n", encoding="utf-8")

    result = resolve_git_changes(repo, base_commit=base, include_worktree=True)
    by_path = {entry["path"]: entry for entry in result["files"]}

    assert by_path["python-ecosystem/demo/src/rules.py"]["changedLines"] == [2]
    assert by_path["python-ecosystem/demo/src/rules.py"]["status"] == "modified"
    assert by_path["python-ecosystem/demo/src/renamed.py"] == {
        "path": "python-ecosystem/demo/src/renamed.py",
        "oldPath": "python-ecosystem/demo/src/rename.py",
        "status": "renamed",
        "correctnessCritical": True,
        "language": "python",
        "changedLines": [],
        "contentSha256": hashlib.sha256(b"RENAMED = 1\n").hexdigest(),
        "previousContentSha256": hashlib.sha256(b"RENAMED = 1\n").hexdigest(),
    }
    assert by_path["python-ecosystem/demo/src/delete.py"]["status"] == "deleted"
    assert by_path["python-ecosystem/demo/src/copied.py"]["status"] == "copied"
    assert by_path["python-ecosystem/demo/src/copied.py"]["changedLines"] == [1]
    assert by_path[str(untracked.relative_to(repo))]["status"] == "added"
    assert result["baseCommit"] == base
    assert result["dirty"] is True


def test_load_comparison_base_requires_exact_manifest_digest_and_repository(tmp_path: Path) -> None:
    manifest = tmp_path / "baseline.json"
    manifest.write_text(
        json.dumps(
            {
                "repositories": [
                    {"id": "codecrow-public", "headCommit": "a" * 40}
                ]
            }
        ),
        encoding="utf-8",
    )
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()

    assert load_comparison_base(manifest, expected_sha256=digest) == "a" * 40
    with pytest.raises(GateInputError, match="baseline manifest digest mismatch"):
        load_comparison_base(manifest, expected_sha256="0" * 64)
    with pytest.raises(GateInputError, match="repository missing from baseline manifest"):
        load_comparison_base(
            manifest, expected_sha256=digest, repository_id="codecrow-static"
        )


def test_resolve_git_changes_rejects_missing_base_and_shallow_repository(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    with pytest.raises(GateInputError, match="comparison base commit is unavailable"):
        resolve_git_changes(repo, base_commit="f" * 40)

    (repo / ".git/shallow").write_text(base + "\n", encoding="ascii")
    with pytest.raises(GateInputError, match="shallow repository"):
        resolve_git_changes(repo, base_commit=base)


def test_tracked_attestation_is_tied_to_the_exact_p0_01_manifest(tmp_path: Path) -> None:
    attestation = tmp_path / "comparison-base.json"
    value = {
        "schemaVersion": 1,
        "source": {
            "taskId": "P0-01",
            "artifact": ".llm-handoff-artifacts/p0-01/baseline-manifest.json",
            "manifestSha256": "b" * 64,
        },
        "repository": {"id": "codecrow-public", "headCommit": "a" * 40},
    }
    attestation.write_text(json.dumps(value), encoding="utf-8")
    digest = hashlib.sha256(attestation.read_bytes()).hexdigest()

    assert (
        load_comparison_base_attestation(
            attestation,
            expected_attestation_sha256=digest,
            expected_manifest_sha256="b" * 64,
        )
        == "a" * 40
    )
    with pytest.raises(GateInputError, match="attestation digest mismatch"):
        load_comparison_base_attestation(
            attestation,
            expected_attestation_sha256="0" * 64,
            expected_manifest_sha256="b" * 64,
        )
    with pytest.raises(GateInputError, match="P0-01 manifest digest mismatch"):
        load_comparison_base_attestation(
            attestation,
            expected_attestation_sha256=digest,
            expected_manifest_sha256="c" * 64,
        )


def test_committed_and_worktree_changed_lines_are_unioned(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    rules = repo / "python-ecosystem/demo/src/rules.py"
    rules.write_text("VALUE = 1\nCOMMITTED = 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "committed")
    rules.write_text("VALUE = 1\nCOMMITTED = 2\nWORKTREE = 3\n", encoding="utf-8")

    result = resolve_git_changes(repo, base_commit=base, include_worktree=True)
    entry = next(item for item in result["files"] if item["path"].endswith("rules.py"))
    assert entry["changedLines"] == [2, 3]


def test_rag_launcher_is_a_python_correctness_path(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    launcher = repo / "python-ecosystem/rag-pipeline/main.py"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("def run():\n    return True\n", encoding="utf-8")

    result = resolve_git_changes(repo, base_commit=base, include_worktree=True)
    entry = next(item for item in result["files"] if item["path"].endswith("main.py"))
    assert entry["correctnessCritical"] is True
    assert entry["language"] == "python"
    assert entry["changedLines"] == [1, 2]


def test_exact_p0_dirty_state_is_subtracted_but_one_byte_drift_fails(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "quality@example.invalid")
    _git(repo, "config", "user.name", "Quality Gate")
    protected = repo / "deployment/build/production-build.sh"
    protected.parent.mkdir(parents=True)
    protected.write_text("BASE\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    protected.write_text("ATTESTED\n", encoding="utf-8")
    prior_tool = repo / "tools/baseline-manifest/tool.mjs"
    prior_tool.parent.mkdir(parents=True)
    prior_tool.write_text("export const value = 1;\n", encoding="utf-8")
    current = repo / "python-ecosystem/demo/src/current.py"
    current.parent.mkdir(parents=True)
    current.write_text("VALUE = 1\n", encoding="utf-8")
    dirty_entries = [
        {
            "path": "deployment/build/production-build.sh",
            "status": " M",
            "contentSha256": hashlib.sha256(protected.read_bytes()).hexdigest(),
        },
        {
            "path": "tools/baseline-manifest/tool.mjs",
            "status": "??",
            "contentSha256": hashlib.sha256(prior_tool.read_bytes()).hexdigest(),
        },
    ]

    result = resolve_git_changes(
        repo,
        base_commit=base,
        include_worktree=True,
        baseline_dirty_entries=dirty_entries,
    )
    assert [entry["path"] for entry in result["files"]] == [
        "python-ecosystem/demo/src/current.py"
    ]
    assert result["comparisonBaseDirtyStateSha256"] == hashlib.sha256(
        json.dumps(dirty_entries, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    protected.write_text("ATTESTED!\n", encoding="utf-8")
    with pytest.raises(GateInputError, match="dirty entry drifted"):
        resolve_git_changes(
            repo,
            base_commit=base,
            include_worktree=True,
            baseline_dirty_entries=dirty_entries,
        )

    protected.write_text("ATTESTED\n", encoding="utf-8")
    prior_tool.unlink()
    with pytest.raises(GateInputError, match="dirty entry drifted"):
        resolve_git_changes(
            repo,
            base_commit=base,
            include_worktree=True,
            baseline_dirty_entries=dirty_entries,
        )

    prior_tool.write_text("export const value = 1;\n", encoding="utf-8")
    _git(repo, "add", "deployment/build/production-build.sh")
    with pytest.raises(GateInputError, match="dirty entry drifted"):
        resolve_git_changes(
            repo,
            base_commit=base,
            include_worktree=True,
            baseline_dirty_entries=dirty_entries,
        )

    _git(repo, "reset", "-q")
    with pytest.raises(GateInputError, match="must be path-sorted"):
        resolve_git_changes(
            repo,
            base_commit=base,
            include_worktree=True,
            baseline_dirty_entries=list(reversed(dirty_entries)),
        )


def test_dirty_attestation_requires_string_canonical_sorted_paths(tmp_path: Path) -> None:
    def attestation(entries: list[dict[str, object]]) -> tuple[Path, str]:
        path = tmp_path / "comparison-base.json"
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "source": {
                        "taskId": "P0-01",
                        "artifact": ".llm-handoff-artifacts/p0-01/baseline-manifest.json",
                        "manifestSha256": "b" * 64,
                    },
                    "repository": {
                        "id": "codecrow-public",
                        "headCommit": "a" * 40,
                        "dirtyState": {"captured": True, "entries": entries},
                    },
                }
            ),
            encoding="utf-8",
        )
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    valid = [
        {"path": "a", "status": "??", "contentSha256": "1" * 64},
        {"path": "b", "status": " M", "contentSha256": "2" * 64},
    ]
    path, digest = attestation(list(reversed(valid)))
    with pytest.raises(GateInputError, match="must be path-sorted"):
        load_comparison_base_attestation(
            path,
            expected_attestation_sha256=digest,
            expected_manifest_sha256="b" * 64,
            with_dirty_state=True,
        )

    path, digest = attestation(
        [{"path": 7, "status": "??", "contentSha256": "1" * 64}]
    )
    with pytest.raises(GateInputError, match="dirty entry is malformed"):
        load_comparison_base_attestation(
            path,
            expected_attestation_sha256=digest,
            expected_manifest_sha256="b" * 64,
            with_dirty_state=True,
        )
