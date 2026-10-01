import subprocess
from pathlib import Path

import pytest

from magento2_benchmark import util
from magento2_benchmark.util import (
    configured_secret_values,
    deterministic_git_diff_command,
    public_config,
    redact_secret_text,
    require_no_secret_values,
    validate_git_evidence_repository,
)


def _repository(path):
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "fixture@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Fixture"],
        check=True,
    )
    (path / "fixture.txt").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "fixture.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "--quiet", "-m", "fixture"],
        check=True,
    )
    return path


def test_public_config_recursively_redacts_auth_and_cookie_values():
    config = {
        "custom_parameters": {
            "headers": {
                "Authorization": "Bearer secret",
                "Cookie": "session=secret",
                "X-Api-Key": "secret",
                "X-Trace": "safe",
            }
        }
    }

    value = public_config(config)

    headers = value["custom_parameters"]["headers"]
    assert headers["Authorization"] == "<redacted>"
    assert headers["Cookie"] == "<redacted>"
    assert headers["X-Api-Key"] == "<redacted>"
    assert headers["X-Trace"] == "safe"


def test_public_config_strips_url_userinfo_query_and_fragment():
    config = {
        "base_url": (
            "https://user:plain-secret@example.test:8443/v1"
            "?api_key=query-secret#private-fragment"
        )
    }

    value = public_config(config)

    assert value["base_url"] == "https://example.test:8443/v1"
    assert configured_secret_values(config) == {
        "plain-secret",
        "query-secret",
        "private-fragment",
    }


def test_external_artifacts_cannot_echo_configured_secrets():
    config = {
        "api_key_env": "KEY_ENV",
        "headers": {
            "Authorization": "Bearer exact-secret",
            "X-Trace": "safe",
        },
        "credentials": {"client": "client-secret"},
    }
    secrets = configured_secret_values(config)

    assert secrets == {
        "Bearer exact-secret",
        "client-secret",
    }
    require_no_secret_values({"response": "safe"}, secrets, context="fixture")
    with pytest.raises(RuntimeError, match="refusing to persist"):
        require_no_secret_values(
            {"response": "echo Bearer exact-secret"},
            secrets,
            context="fixture",
        )
    assert redact_secret_text(
        "provider echoed Bearer exact-secret",
        secrets,
    ) == "provider echoed <redacted>"


def test_git_evidence_rejects_symlinked_dot_git(tmp_path):
    repository = _repository(tmp_path / "repository")
    metadata = repository / ".git"
    external = tmp_path / "external-git"
    metadata.rename(external)
    metadata.symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match=r"\.git metadata must not be a symlink"):
        validate_git_evidence_repository(repository)


def test_git_evidence_rejects_nested_metadata_symlink(tmp_path):
    repository = _repository(tmp_path / "repository")
    external = tmp_path / "external"
    external.mkdir()
    (repository / ".git" / "refs" / "outside").symlink_to(
        external,
        target_is_directory=True,
    )

    with pytest.raises(ValueError, match="must not contain symlinks"):
        validate_git_evidence_repository(repository)


def test_git_evidence_rejects_main_object_alternates(tmp_path):
    repository = _repository(tmp_path / "repository")
    alternates = repository / ".git" / "objects" / "info" / "alternates"
    alternates.write_text("/untrusted/object/store\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Git object alternates"):
        validate_git_evidence_repository(repository)


def test_linked_worktree_rejects_symlinked_gitdir_path(tmp_path):
    repository = _repository(tmp_path / "repository")
    worktree = tmp_path / "worktree"
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "worktree",
            "add",
            "--quiet",
            "-b",
            "fixture-worktree",
            str(worktree),
        ],
        check=True,
    )
    marker = worktree / ".git"
    git_dir = marker.read_text(encoding="utf-8").split(":", 1)[1].strip()
    alias = tmp_path / "gitdir-alias"
    alias.symlink_to(git_dir, target_is_directory=True)
    marker.write_text(f"gitdir: {alias}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="gitdir must not traverse symlinked"):
        validate_git_evidence_repository(worktree)


def test_linked_worktree_rejects_symlinked_commondir_marker(tmp_path):
    repository = _repository(tmp_path / "repository")
    worktree = tmp_path / "worktree"
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "worktree",
            "add",
            "--quiet",
            "-b",
            "fixture-worktree",
            str(worktree),
        ],
        check=True,
    )
    git_dir = worktree / ".git"
    git_dir = git_dir.read_text(encoding="utf-8").split(":", 1)[1].strip()
    common_marker = Path(git_dir) / "commondir"
    original = common_marker.with_name("commondir.fixture")
    common_marker.rename(original)
    common_marker.symlink_to(original)

    with pytest.raises(ValueError, match="commondir marker must not be a symlink"):
        validate_git_evidence_repository(worktree)


def test_linked_worktree_checks_alternates_in_git_and_common_dirs(tmp_path):
    repository = _repository(tmp_path / "repository")
    worktree = tmp_path / "worktree"
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "worktree",
            "add",
            "--quiet",
            "-b",
            "fixture-worktree",
            str(worktree),
        ],
        check=True,
    )
    marker = worktree / ".git"
    git_dir = marker.read_text(encoding="utf-8").split(":", 1)[1].strip()
    metadata_directories = [Path(git_dir), repository / ".git"]

    for metadata in metadata_directories:
        alternates = metadata / "objects" / "info" / "alternates"
        alternates.parent.mkdir(parents=True, exist_ok=True)
        alternates.write_text("/untrusted/object/store\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Git object alternates"):
            validate_git_evidence_repository(worktree)
        alternates.unlink()

    validate_git_evidence_repository(worktree)


def test_deterministic_diff_reuses_stable_full_metadata_validation(
    tmp_path,
    monkeypatch,
):
    repository = _repository(tmp_path / "repository")
    scans = 0
    real_scan = util._reject_metadata_symlinks

    def counted_scan(directory):
        nonlocal scans
        scans += 1
        return real_scan(directory)

    monkeypatch.setattr(util, "_reject_metadata_symlinks", counted_scan)

    for _ in range(20):
        deterministic_git_diff_command(repository, "HEAD^", "HEAD")

    assert scans == 1


def test_deterministic_diff_cache_invalidates_for_alternates(tmp_path):
    repository = _repository(tmp_path / "repository")
    deterministic_git_diff_command(repository, "HEAD^", "HEAD")
    alternates = repository / ".git" / "objects" / "info" / "alternates"
    alternates.write_text("/untrusted/object/store\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Git object alternates"):
        deterministic_git_diff_command(repository, "HEAD^", "HEAD")


def test_deterministic_diff_cache_invalidates_for_metadata_symlink(tmp_path):
    repository = _repository(tmp_path / "repository")
    deterministic_git_diff_command(repository, "HEAD^", "HEAD")
    external = tmp_path / "external"
    external.mkdir()
    (repository / ".git" / "refs" / "outside").symlink_to(
        external,
        target_is_directory=True,
    )

    with pytest.raises(ValueError, match="must not contain symlinks"):
        deterministic_git_diff_command(repository, "HEAD^", "HEAD")
