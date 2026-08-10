import subprocess

import pytest

from rag_pipeline.core.source_tree import (
    RepositorySourceTreeError,
    compute_repository_source_tree_sha256,
    read_repository_file_bytes,
    require_repository_source_tree_unchanged,
    verify_repository_source_tree,
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
    with pytest.raises(RepositorySourceTreeError, match="does not match"):
        verify_repository_source_tree(tmp_path, "f" * 40, digest)

    (tmp_path / "untracked.php").write_text("<?php\n", encoding="utf-8")
    dirty_digest = compute_repository_source_tree_sha256(tmp_path)
    with pytest.raises(RepositorySourceTreeError, match="not clean"):
        verify_repository_source_tree(tmp_path, commit, dirty_digest)


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
