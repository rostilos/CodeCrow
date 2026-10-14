import errno
import os
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rag_pipeline.core.source_tree import (
    RepositoryFileSizeLimitExceeded,
    RepositorySourceTreeError,
    attest_repository_source_tree,
    compute_repository_source_tree_sha256,
    read_repository_file_bytes,
    require_repository_source_tree_unchanged,
    verify_repository_source_tree,
    _git_output,
    _verify_git_checkout,
)


def _git(repo, *arguments):
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_archive_tree_attestation_detects_content_and_path_changes(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Example.php").write_text(
        "<?php final class Example {}\n",
        encoding="utf-8",
    )
    digest = compute_repository_source_tree_sha256(tmp_path)

    source = verify_repository_source_tree(tmp_path, "a" * 40, digest)

    assert source.tree_sha256 == digest
    assert source.git_commit_verified is False
    (tmp_path / "src" / "Example.php").write_text(
        "<?php final class Changed {}\n",
        encoding="utf-8",
    )
    with pytest.raises(
        RepositorySourceTreeError,
        match="acquisition attestation",
    ):
        require_repository_source_tree_unchanged(tmp_path, source)


def test_canonical_digest_matches_cross_language_golden_fixture(tmp_path):
    """Pin global path ordering and byte framing shared with the Java producer."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "Module.php").write_text("<?php\n", encoding="utf-8")
    (tmp_path / "app.json").write_text(
        '{"name":"fixture"}\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "Deep").mkdir(parents=True)
    (tmp_path / "src" / "Deep" / "Value.php").write_text(
        "value\n",
        encoding="utf-8",
    )
    (tmp_path / "café.txt").write_text("naïve\n", encoding="utf-8")
    try:
        (tmp_path / "latest").symlink_to("app/Module.php")
    except OSError as exception:
        pytest.skip(f"symlinks are unavailable: {exception}")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "internal").write_text("ignored\n", encoding="utf-8")

    assert compute_repository_source_tree_sha256(tmp_path) == (
        "927684e12c804a888d33a14a04c92291"
        "f329aff25941cfa13e5864fd4b15c411"
    )


def test_git_tree_requires_exact_clean_head(tmp_path):
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.email", "benchmark@example.test")
    _git(tmp_path, "config", "user.name", "Benchmark")
    (tmp_path / "Example.php").write_text("<?php\n", encoding="utf-8")
    _git(tmp_path, "add", "Example.php")
    _git(tmp_path, "commit", "--quiet", "-m", "snapshot")
    commit = _git(tmp_path, "rev-parse", "HEAD")
    digest = compute_repository_source_tree_sha256(tmp_path)

    source = verify_repository_source_tree(tmp_path, commit, digest)

    assert source.git_commit_verified is True
    assert attest_repository_source_tree(tmp_path, commit).tree_sha256 == digest
    with pytest.raises(RepositorySourceTreeError, match="does not match"):
        verify_repository_source_tree(tmp_path, "f" * 40, digest)

    (tmp_path / "untracked.php").write_text("<?php\n", encoding="utf-8")
    dirty_digest = compute_repository_source_tree_sha256(tmp_path)
    with pytest.raises(RepositorySourceTreeError, match="not clean"):
        verify_repository_source_tree(tmp_path, commit, dirty_digest)


def test_git_attestation_retries_one_transient_timeout(tmp_path, caplog):
    with patch("rag_pipeline.core.source_tree.subprocess.run", side_effect=[
        subprocess.TimeoutExpired("git status", 30),
        SimpleNamespace(stdout=""),
    ]) as run:
        assert _git_output(tmp_path, "status", "--porcelain=v1", "--untracked-files=normal") == ""
    assert run.call_count == 2
    assert run.call_args_list[0] == run.call_args_list[1]
    assert "git status" in caplog.text.lower()
    assert "retrying once" in caplog.text


def test_git_attestation_timeout_is_bounded_and_identifies_command(tmp_path):
    with patch("rag_pipeline.core.source_tree.subprocess.run", side_effect=
               subprocess.TimeoutExpired("git status", 30)) as run:
        with pytest.raises(RepositorySourceTreeError, match=r"git status.*30.*2 attempts"):
            _git_output(tmp_path, "status", "--porcelain=v1", "--untracked-files=normal")
    assert run.call_count == 2


@pytest.mark.parametrize("failure", [
    subprocess.CalledProcessError(128, "git status", stderr="fixture Git failure"),
    OSError("fixture unavailable executable"),
])
def test_git_attestation_non_timeout_failures_do_not_retry(tmp_path, failure):
    with patch("rag_pipeline.core.source_tree.subprocess.run", side_effect=failure) as run:
        with pytest.raises(RepositorySourceTreeError, match="cannot verify repository Git revision"):
            _git_output(tmp_path, "status", "--porcelain=v1", "--untracked-files=normal")
    assert run.call_count == 1


@pytest.mark.parametrize("linked_worktree", [False, True], ids=["checkout", "worktree"])
@pytest.mark.parametrize("change", ["clean", "tracked", "staged", "deleted", "nested_untracked"])
def test_git_clean_predicate_keeps_all_dirty_controls(tmp_path, linked_worktree, change):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "--quiet")
    _git(origin, "config", "user.email", "benchmark@example.test")
    _git(origin, "config", "user.name", "Benchmark")
    (origin / "tracked.py").write_text("original = True\n")
    _git(origin, "add", ".")
    _git(origin, "commit", "--quiet", "-m", "snapshot")
    commit = _git(origin, "rev-parse", "HEAD")
    repo = origin
    if linked_worktree:
        repo = tmp_path / "linked"
        _git(origin, "worktree", "add", "--detach", str(repo), commit)
        assert (repo / ".git").is_file()
    if change in {"tracked", "staged"}:
        (repo / "tracked.py").write_text("changed = True\n")
        if change == "staged":
            _git(repo, "add", "tracked.py")
    elif change == "deleted":
        (repo / "tracked.py").unlink()
    elif change == "nested_untracked":
        nested = repo / "new" / "deep" / "file.py"
        nested.parent.mkdir(parents=True)
        nested.write_text("untracked = True\n")
    with patch("rag_pipeline.core.source_tree.subprocess.run", wraps=subprocess.run) as run:
        if change == "clean":
            assert attest_repository_source_tree(repo, commit).git_commit_verified
        else:
            with pytest.raises(RepositorySourceTreeError, match="not clean"):
                attest_repository_source_tree(repo, commit)
    assert run.call_count == 2  # rev-parse and one status; dirty is never retried.
    assert "--untracked-files=normal" in run.call_args.args[0]


def test_git_wrong_head_does_not_retry(tmp_path):
    (tmp_path / ".git").mkdir()
    with patch("rag_pipeline.core.source_tree.subprocess.run", return_value=
               SimpleNamespace(stdout="b" * 40)) as run:
        with pytest.raises(RepositorySourceTreeError, match="does not match"):
            _verify_git_checkout(tmp_path, "a" * 40)
    assert run.call_count == 1


def test_git_administrative_data_is_not_part_of_tree_digest(tmp_path):
    (tmp_path / "Example.php").write_text("one\n", encoding="utf-8")
    first = compute_repository_source_tree_sha256(tmp_path)
    (tmp_path / ".git" / "objects").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "internal").write_text(
        "ignored\n",
        encoding="utf-8",
    )

    assert compute_repository_source_tree_sha256(tmp_path) == first


def test_attested_regular_file_cannot_be_replaced_by_symlink(tmp_path):
    source_file = tmp_path / "src" / "Example.php"
    source_file.parent.mkdir()
    source_file.write_text("trusted\n", encoding="utf-8")
    external_file = tmp_path.parent / f"{tmp_path.name}-external.php"
    external_file.write_text("trusted\n", encoding="utf-8")
    digest = compute_repository_source_tree_sha256(tmp_path)
    source = verify_repository_source_tree(tmp_path, "a" * 40, digest)

    source_file.unlink()
    try:
        source_file.symlink_to(external_file)
    except OSError as exception:
        pytest.skip(f"symlinks are unavailable: {exception}")

    with pytest.raises(
        RepositorySourceTreeError,
        match="safely open",
    ):
        read_repository_file_bytes(
            tmp_path,
            "src/Example.php",
            expected_sha256=source.file_sha256_by_path["src/Example.php"],
        )


def test_per_file_attestation_detects_mutation_before_final_tree_recheck(
    tmp_path,
):
    source_file = tmp_path / "src" / "Example.php"
    source_file.parent.mkdir()
    source_file.write_text("trusted\n", encoding="utf-8")
    digest = compute_repository_source_tree_sha256(tmp_path)
    source = verify_repository_source_tree(tmp_path, "a" * 40, digest)

    source_file.write_text("malicious\n", encoding="utf-8")
    with pytest.raises(
        RepositorySourceTreeError,
        match="changed after attestation",
    ):
        read_repository_file_bytes(
            tmp_path,
            "src/Example.php",
            expected_sha256=source.file_sha256_by_path["src/Example.php"],
        )

    # Restoring the tree could satisfy a later aggregate recheck, but it cannot
    # make the untrusted bytes eligible for loading.
    source_file.write_text("trusted\n", encoding="utf-8")
    require_repository_source_tree_unchanged(tmp_path, source)


def test_bounded_file_read_accepts_exact_limit_without_truncation(tmp_path):
    content = b"complete" * 16
    (tmp_path / "exact.txt").write_bytes(content)

    observed = read_repository_file_bytes(
        tmp_path,
        "exact.txt",
        max_size_bytes=len(content),
    )

    assert observed == content


def test_bounded_file_read_rejects_limit_plus_one_without_returning_prefix(
    tmp_path,
):
    content = b"x" * 101
    (tmp_path / "large.txt").write_bytes(content)

    with pytest.raises(RepositoryFileSizeLimitExceeded) as raised:
        read_repository_file_bytes(
            tmp_path,
            "large.txt",
            max_size_bytes=100,
        )

    assert raised.value.relative_path == "large.txt"
    assert raised.value.size_bytes == 101
    assert raised.value.max_size_bytes == 100


def test_known_oversized_file_is_rejected_before_any_content_read():
    source = MagicMock()
    source.fileno.return_value = 123

    @contextmanager
    def opened_source(*_args, **_kwargs):
        yield source

    with (
        patch(
            "rag_pipeline.core.source_tree.open_repository_file_no_follow",
            opened_source,
        ),
        patch(
            "rag_pipeline.core.source_tree.os.fstat",
            return_value=SimpleNamespace(st_size=101),
        ),
        pytest.raises(RepositoryFileSizeLimitExceeded),
    ):
        read_repository_file_bytes(
            "/repo",
            "large.txt",
            max_size_bytes=100,
        )

    source.read.assert_not_called()


def test_source_entry_inspection_failure_reports_os_error_without_retry(
    tmp_path,
):
    (tmp_path / "changed.py").write_text("content\n", encoding="utf-8")
    failure = FileNotFoundError(
        errno.ENOENT,
        os.strerror(errno.ENOENT),
    )
    real_stat = os.stat

    def fail_entry_inspection(path, *args, **kwargs):
        if kwargs.get("dir_fd") is not None:
            raise failure
        return real_stat(path, *args, **kwargs)

    with (
        patch(
            "rag_pipeline.core.source_tree.os.stat",
            side_effect=fail_entry_inspection,
        ) as inspect_entry,
        pytest.raises(RepositorySourceTreeError) as raised,
    ):
        compute_repository_source_tree_sha256(tmp_path)

    assert "cannot inspect repository source entry: changed.py" in str(
        raised.value
    )
    assert "FileNotFoundError" in str(raised.value)
    assert f"errno={errno.ENOENT}" in str(raised.value)
    assert f"reason={os.strerror(errno.ENOENT)}" in str(raised.value)
    assert raised.value.__cause__ is failure
    entry_inspections = [
        call
        for call in inspect_entry.call_args_list
        if call.kwargs.get("dir_fd") is not None
    ]
    assert len(entry_inspections) == 1
