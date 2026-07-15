from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


QUALITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QUALITY_ROOT))

from quality_gates import GateInputError  # noqa: E402
from quality_gates import git_changes as changes  # noqa: E402


def _json_file(path: Path, value: object) -> tuple[Path, str]:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "quality@example.invalid")
    _git(repo, "config", "user.name", "Quality Gate")
    source = repo / "python-ecosystem/demo/src/rules.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("{broken", "manifest is malformed"),
        ("[]", "no repository inventory"),
        (json.dumps({"repositories": {}}), "no repository inventory"),
        (
            json.dumps(
                {
                    "repositories": [
                        {"id": "codecrow-public", "headCommit": "bad"}
                    ]
                }
            ),
            "commit is malformed",
        ),
        (
            json.dumps(
                {
                    "repositories": [
                        {"id": "codecrow-public", "headCommit": "a" * 40},
                        {"id": "codecrow-public", "headCommit": "b" * 40},
                    ]
                }
            ),
            "repository missing",
        ),
    ],
)
def test_manifest_negative_matrix(tmp_path: Path, raw: str, message: str) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(raw, encoding="utf-8")
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    with pytest.raises(GateInputError, match=message):
        changes.load_comparison_base(manifest, expected_sha256=digest)


def _attestation() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "source": {
            "taskId": "P0-01",
            "artifact": ".llm-handoff-artifacts/p0-01/baseline-manifest.json",
            "manifestSha256": "b" * 64,
        },
        "repository": {"id": "codecrow-public", "headCommit": "a" * 40},
    }


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda value: value.update(schemaVersion=2), "schema is malformed"),
        (lambda value: value.update(extra=True), "schema is malformed"),
        (lambda value: value.update(source=[]), "source is malformed"),
        (
            lambda value: value["source"].update(taskId="P0-02"),
            "source is malformed",
        ),
        (
            lambda value: value["source"].update(artifact="wrong"),
            "source is malformed",
        ),
        (
            lambda value: value["source"].update(extra=True),
            "source is malformed",
        ),
        (
            lambda value: value["source"].update(manifestSha256="bad"),
            "source is malformed",
        ),
        (lambda value: value.update(repository=[]), "repository missing"),
        (
            lambda value: value["repository"].update(id="codecrow-static"),
            "repository missing",
        ),
        (
            lambda value: value["repository"].update(headCommit=None),
            "commit is malformed",
        ),
        (
            lambda value: value["repository"].update(headCommit="A" * 40),
            "commit is malformed",
        ),
        (
            lambda value: value["repository"].update(extra=True),
            "repository missing",
        ),
    ],
)
def test_attestation_negative_matrix(
    tmp_path: Path, mutator: object, message: str
) -> None:
    value = _attestation()
    mutator(value)  # type: ignore[operator]
    path, digest = _json_file(tmp_path / "attestation.json", value)
    with pytest.raises(GateInputError, match=message):
        changes.load_comparison_base_attestation(
            path,
            expected_attestation_sha256=digest,
            expected_manifest_sha256="b" * 64,
        )


def test_attestation_rejects_unreadable_and_invalid_json(tmp_path: Path) -> None:
    with pytest.raises(GateInputError, match="unreadable|trusted regular file"):
        changes.load_comparison_base_attestation(
            tmp_path / "missing",
            expected_attestation_sha256="0" * 64,
            expected_manifest_sha256="b" * 64,
        )
    path = tmp_path / "attestation.json"
    path.write_text("{broken", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(GateInputError, match="attestation is malformed"):
        changes.load_comparison_base_attestation(
            path,
            expected_attestation_sha256=digest,
            expected_manifest_sha256="b" * 64,
        )


def test_contract_loaders_reject_symlink_fifo_and_oversize_without_blocking(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.json"
    target.write_text(json.dumps(_attestation()), encoding="utf-8")
    linked = tmp_path / "linked.json"
    linked.symlink_to(target)
    with pytest.raises(GateInputError, match="trusted regular file"):
        changes.load_comparison_base_attestation(
            linked,
            expected_attestation_sha256="0" * 64,
            expected_manifest_sha256="b" * 64,
            repository_root=tmp_path,
        )

    fifo = tmp_path / "attestation.fifo"
    os.mkfifo(fifo)
    with pytest.raises(GateInputError, match="regular file"):
        changes.load_comparison_base_attestation(
            fifo,
            expected_attestation_sha256="0" * 64,
            expected_manifest_sha256="b" * 64,
            repository_root=tmp_path,
        )

    oversized = tmp_path / "attestation.json"
    oversized.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(GateInputError, match="exceeds the size limit"):
        changes.load_comparison_base_attestation(
            oversized,
            expected_attestation_sha256="0" * 64,
            expected_manifest_sha256="b" * 64,
            repository_root=tmp_path,
        )

    root_link = tmp_path / "root-link"
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "attestation.json").write_text("{}", encoding="utf-8")
    root_link.symlink_to(repository, target_is_directory=True)
    with pytest.raises(GateInputError, match="repository root is not trusted"):
        changes.load_comparison_base_attestation(
            Path("attestation.json"),
            expected_attestation_sha256="0" * 64,
            expected_manifest_sha256="b" * 64,
            repository_root=root_link,
        )


def test_git_command_failure_and_path_decoding_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        changes.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout=b"", stderr=b"fatal detail"
        ),
    )
    with pytest.raises(GateInputError, match="fatal detail"):
        changes._git(tmp_path, "status")
    assert changes._git(tmp_path, "status", check=False) == b""

    with pytest.raises(GateInputError, match="not valid UTF-8"):
        changes._decode_path(b"\xff")
    for value in (
        b"",
        b"/absolute",
        b"../escape",
        b"a\\b",
        b"./a",
        b"a//b",
        b"a\nb",
        b"a\x7fb",
    ):
        with pytest.raises(GateInputError, match="escapes the repository"):
            changes._decode_path(value)
    assert changes._decode_path(b"safe/path.py") == "safe/path.py"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"R100\0only-old\0", "rename/copy inventory"),
        (b"M\0", "change inventory"),
        (b"X\0path\0", "unsupported Git change status"),
    ],
)
def test_name_status_rejects_malformed_inventory(raw: bytes, message: str) -> None:
    with pytest.raises(GateInputError, match=message):
        changes._name_status(raw)


def test_name_status_accepts_every_supported_shape() -> None:
    assert changes._name_status(b"") == []
    assert changes._name_status(b"T\0type.py\0") == [("T", None, "type.py")]
    assert changes._name_status(b"C75\0old.py\0new.py\0") == [
        ("C75", "old.py", "new.py")
    ]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("java-ecosystem/a/src/main/java/x/A.java", (True, "java")),
        ("java-ecosystem/a/src/test/java/x/A.java", (False, None)),
        ("java-ecosystem/a/src/main/java/x/A.kt", (False, None)),
        ("python-ecosystem/a/src/module.py", (True, "python")),
        ("python-ecosystem/a/src/tests/test_module.py", (False, None)),
        ("python-ecosystem/a/src/module.txt", (False, None)),
        ("python-ecosystem/rag-pipeline/main.py", (True, "python")),
        ("tools/quality-gates/quality_gates/rules.py", (True, "python")),
        ("tools/quality-gates/quality_gates.py", (False, None)),
        ("README.md", (False, None)),
    ],
)
def test_correctness_path_classification(
    path: str, expected: tuple[bool, str | None]
) -> None:
    assert changes._classification(path) == expected


def test_changed_and_all_lines_edge_cases(tmp_path: Path) -> None:
    assert changes._changed_lines(b"@@ -4 +7,0 @@\n@@ -9 +11,2 @@\n") == [11, 12]
    assert changes._changed_lines(b"no hunks") == []

    missing = "python-ecosystem/a/src/missing.py"
    descriptor = changes._open_repository_root(tmp_path)
    try:
        with pytest.raises(GateInputError, match="trusted regular file"):
            changes._all_lines(descriptor, missing)
        target = tmp_path / "target"
        target.write_text("x\n", encoding="utf-8")
        link = tmp_path / "python-ecosystem/a/src/link.py"
        link.parent.mkdir(parents=True)
        link.symlink_to(target)
        with pytest.raises(GateInputError, match="trusted regular file"):
            changes._all_lines(descriptor, link.relative_to(tmp_path).as_posix())
        binary = tmp_path / "python-ecosystem/a/src/binary.py"
        binary.write_bytes(b"\xff")
        with pytest.raises(GateInputError, match="not UTF-8"):
            changes._all_lines(descriptor, binary.relative_to(tmp_path).as_posix())
    finally:
        os.close(descriptor)


def test_entry_and_merge_cover_type_change_and_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(changes, "_diff_for_path", lambda *args, **kwargs: b"@@ -1 +3 @@\n")
    monkeypatch.setattr(changes, "_git_blob_sha256", lambda *args, **kwargs: "a" * 64)
    (tmp_path / "README.md").write_text("readme\n", encoding="utf-8")
    (tmp_path / "new.md").write_text("new\n", encoding="utf-8")
    descriptor = changes._open_repository_root(tmp_path)
    try:
        entry = changes._entry(
            tmp_path,
            status_token="T",
            old_path=None,
            path="README.md",
            left="a" * 40,
            right="b" * 40,
            root_descriptor=descriptor,
        )
        renamed = changes._entry(
            tmp_path,
            status_token="R90",
            old_path="old.md",
            path="new.md",
            left="a" * 40,
            right=None,
            root_descriptor=descriptor,
        )
    finally:
        os.close(descriptor)
    assert entry["status"] == "type_changed"
    assert entry["changedLines"] == [3]
    assert renamed["oldPath"] == "old.md"
    assert renamed["changedLines"] == [3]

    def item(status: str, lines: list[int]) -> dict[str, object]:
        return {"path": "same", "status": status, "changedLines": lines}

    assert changes._merge_entries([item("modified", [1]), item("deleted", [])]) == [
        item("deleted", [])
    ]
    assert changes._merge_entries([item("deleted", []), item("modified", [2])]) == [
        item("modified", [2])
    ]
    assert changes._merge_entries([item("added", [1]), item("modified", [2])]) == [
        item("added", [1, 2])
    ]
    assert changes._merge_entries([item("modified", [1]), item("modified", [2])]) == [
        item("modified", [1, 2])
    ]


def test_resolver_rejects_malformed_and_nonancestor_and_supports_clean_mode(
    tmp_path: Path,
) -> None:
    with pytest.raises(GateInputError, match="repository or comparison base"):
        changes.resolve_git_changes(tmp_path, base_commit="bad")

    repo, base = _repository(tmp_path)
    assert changes.resolve_git_changes(repo, base_commit=base)["dirty"] is False

    source = repo / "python-ecosystem/demo/src/rules.py"
    source.write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "future")
    future = _git(repo, "rev-parse", "HEAD")
    _git(repo, "reset", "-q", "--hard", base)
    with pytest.raises(GateInputError, match="not an ancestor"):
        changes.resolve_git_changes(repo, base_commit=future)


def test_resolver_rejects_repository_root_symlink_and_mid_resolution_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = _repository(tmp_path)
    root_link = tmp_path / "repo-link"
    root_link.symlink_to(repo, target_is_directory=True)
    with pytest.raises(GateInputError, match="repository root is not trusted"):
        changes.resolve_git_changes(root_link, base_commit=base)

    untracked = repo / "new.txt"
    untracked.write_text("new\n", encoding="utf-8")
    backup = tmp_path / "repo-backup"
    original_current_file_bytes = changes._current_file_bytes
    swapped = False

    def swapping_read(root_descriptor: int, path: str) -> bytes:
        nonlocal swapped
        raw = original_current_file_bytes(root_descriptor, path)
        if path == "new.txt" and not swapped:
            swapped = True
            repo.rename(backup)
            repo.mkdir()
        return raw

    monkeypatch.setattr(changes, "_current_file_bytes", swapping_read)
    with pytest.raises(GateInputError, match="repository root changed"):
        changes.resolve_git_changes(repo, base_commit=base, include_worktree=True)


def test_contract_attestation_and_porcelain_residual_negative_paths(
    tmp_path: Path,
) -> None:
    with pytest.raises(GateInputError, match="duplicate comparison-base key"):
        changes._parse_contract_json(b'{"a": 1, "a": 2}', "contract")

    value = _attestation()
    path, digest = _json_file(tmp_path / "attestation.json", value)
    with pytest.raises(GateInputError, match="dirty state is missing"):
        changes.load_comparison_base_attestation(
            path, expected_attestation_sha256=digest,
            expected_manifest_sha256="b" * 64, with_dirty_state=True,
        )

    value = _attestation()
    value["repository"]["dirtyState"] = {"captured": False, "entries": []}
    path, digest = _json_file(tmp_path / "attestation.json", value)
    with pytest.raises(GateInputError, match="dirty state is malformed"):
        changes.load_comparison_base_attestation(
            path, expected_attestation_sha256=digest,
            expected_manifest_sha256="b" * 64,
        )

    value = _attestation()
    value["repository"]["dirtyState"] = {"captured": True, "entries": ["bad"]}
    path, digest = _json_file(tmp_path / "attestation.json", value)
    with pytest.raises(GateInputError, match="dirty entry is malformed"):
        changes.load_comparison_base_attestation(
            path, expected_attestation_sha256=digest,
            expected_manifest_sha256="b" * 64,
        )

    value = _attestation()
    value["repository"]["dirtyState"] = {
        "captured": True,
        "entries": [{"path": "a", "status": "bad", "contentSha256": "c" * 64}],
    }
    path, digest = _json_file(tmp_path / "attestation.json", value)
    with pytest.raises(GateInputError, match="dirty entry is malformed"):
        changes.load_comparison_base_attestation(
            path, expected_attestation_sha256=digest,
            expected_manifest_sha256="b" * 64,
        )

    value = _attestation()
    value["repository"]["dirtyState"] = {"captured": True, "entries": []}
    path, digest = _json_file(tmp_path / "attestation.json", value)
    with pytest.raises(GateInputError, match="dirty state is empty"):
        changes.load_comparison_base_attestation(
            path, expected_attestation_sha256=digest,
            expected_manifest_sha256="b" * 64,
        )

    value = _attestation()
    value["repository"]["dirtyState"] = {
        "captured": True,
        "entries": [{"path": "a", "status": "??", "contentSha256": "c" * 64}],
    }
    path, digest = _json_file(tmp_path / "attestation.json", value)
    assert changes.load_comparison_base_attestation(
        path, expected_attestation_sha256=digest,
        expected_manifest_sha256="b" * 64, with_dirty_state=True,
    ) == ("a" * 40, ({"path": "a", "status": "??", "contentSha256": "c" * 64},))

    for raw, message in (
        (b"bad\0", "status is malformed"),
        (b"\xff\xff path\0", "status is malformed"),
        (b"?? a\0?? a\0", "duplicate Git porcelain"),
        (b"R  new\0", "rename is malformed"),
    ):
        with pytest.raises(GateInputError, match=message):
            changes._porcelain_status(raw)
    assert changes._porcelain_status(b"R  new\0old\0") == {"new": "R "}


def test_baseline_dirty_git_blob_and_diff_residual_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_descriptor = changes._open_repository_root(tmp_path)
    try:
        monkeypatch.setattr(changes, "_git", lambda *args, **kwargs: b"")
        with pytest.raises(GateInputError, match="dirty entry is malformed"):
            changes._validate_and_subtract_baseline_dirty(
                tmp_path, root_descriptor, [], ["bad"]
            )
        with pytest.raises(GateInputError, match="dirty entry is malformed"):
            changes._validate_and_subtract_baseline_dirty(
                tmp_path, root_descriptor, [],
                [{"path": 1, "status": "??", "contentSha256": "a" * 64}],
            )
        with pytest.raises(GateInputError, match="dirty state is empty"):
            changes._validate_and_subtract_baseline_dirty(
                tmp_path, root_descriptor, [], []
            )

        artifact = tmp_path / "prior"
        artifact.write_text("value\n", encoding="utf-8")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        monkeypatch.setattr(changes, "_git", lambda *args, **kwargs: b"?? prior\0")
        with pytest.raises(GateInputError, match="not replayed"):
            changes._validate_and_subtract_baseline_dirty(
                tmp_path, root_descriptor, [],
                [{"path": "prior", "status": "??", "contentSha256": digest}],
            )
    finally:
        os.close(root_descriptor)

    monkeypatch.setattr(changes, "_git", lambda *args, **kwargs: b"not-a-number")
    with pytest.raises(GateInputError, match="previous source is unavailable"):
        changes._git_blob_sha256(tmp_path, "a" * 40, "path")
    monkeypatch.setattr(changes, "_git", lambda *args, **kwargs: b"-1")
    with pytest.raises(GateInputError, match="exceeds the size limit"):
        changes._git_blob_sha256(tmp_path, "a" * 40, "path")
    responses = iter((b"2", b"x"))
    monkeypatch.setattr(changes, "_git", lambda *args, **kwargs: next(responses))
    with pytest.raises(GateInputError, match="size drifted"):
        changes._git_blob_sha256(tmp_path, "a" * 40, "path")

    observed: list[str] = []
    monkeypatch.setattr(
        changes, "_git", lambda repo, *args, **kwargs: observed.extend(args) or b""
    )
    changes._diff_for_path(tmp_path, "a" * 40, "b" * 40, "path")
    assert "b" * 40 in observed


def test_resolver_requires_dirty_replay_and_policy_identity(tmp_path: Path) -> None:
    repo, base = _repository(tmp_path)
    with pytest.raises(GateInputError, match="dirty replay requires worktree"):
        changes.resolve_git_changes(
            repo, base_commit=base,
            baseline_dirty_entries=(
                {"path": "prior", "status": "??", "contentSha256": "a" * 64},
            ),
        )
    with pytest.raises(GateInputError, match="correctness policy identity is required"):
        changes.resolve_git_changes(repo, base_commit=base, correctness_policy={})


def test_dirty_attestation_plain_return_and_added_entry_skip_previous_identity(
    tmp_path: Path,
) -> None:
    value = _attestation()
    value["repository"]["dirtyState"] = {
        "captured": True,
        "entries": [{"path": "a", "status": "??", "contentSha256": "c" * 64}],
    }
    path, digest = _json_file(tmp_path / "attestation.json", value)
    assert changes.load_comparison_base_attestation(
        path,
        expected_attestation_sha256=digest,
        expected_manifest_sha256="b" * 64,
    ) == "a" * 40

    source = tmp_path / "python-ecosystem/demo/src/new.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    descriptor = changes._open_repository_root(tmp_path)
    try:
        entry = changes._entry(
            tmp_path,
            status_token="A",
            old_path=None,
            path="python-ecosystem/demo/src/new.py",
            left="a" * 40,
            right=None,
            root_descriptor=descriptor,
        )
    finally:
        os.close(descriptor)
    assert entry["status"] == "added"
    assert "previousContentSha256" not in entry
