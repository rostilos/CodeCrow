from __future__ import annotations

import subprocess

import pytest

from magento2_benchmark import repository_prep


def _git(repository, *arguments):
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return completed.stdout.strip()


def _prepared_fixture(tmp_path):
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
    _git(repository, "config", "user.email", "fixture@example.test")
    _git(repository, "config", "user.name", "Fixture")
    (repository / "fixture.txt").write_text("fixture\n", encoding="utf-8")
    _git(repository, "add", "fixture.txt")
    _git(repository, "commit", "--quiet", "-m", "fixture")
    revision = _git(repository, "rev-parse", "HEAD")
    _git(
        repository,
        "remote",
        "add",
        "origin",
        repository_prep.OFFICIAL_REMOTE_URL,
    )
    for ref_name in (
        *repository_prep.REQUIRED_DURABLE_BRANCH_REFS,
        "refs/benchmark/pull/7",
    ):
        _git(repository, "update-ref", ref_name, revision)
    return repository


def test_prepare_repository_fetches_exact_builder_aliases(tmp_path, monkeypatch):
    repository = _prepared_fixture(tmp_path)
    fetches = []
    real_git = repository_prep._git

    def fake_git(path, *arguments):
        if arguments[0] == "fetch":
            fetches.append(arguments)
            return ""
        return real_git(path, *arguments)

    monkeypatch.setattr(repository_prep, "_git", fake_git)

    result = repository_prep.prepare_official_repository(repository)

    assert len(fetches) == 1
    fetch = fetches[0]
    assert "--atomic" in fetch
    assert "--prune" in fetch
    assert "--no-filter" in fetch
    assert repository_prep.BRANCH_REFSPEC in fetch
    assert repository_prep.PULL_HEAD_REFSPEC in fetch
    assert fetch[-3:] == (
        "origin",
        repository_prep.BRANCH_REFSPEC,
        repository_prep.PULL_HEAD_REFSPEC,
    )
    assert result["repository"] == "magento/magento2"
    assert result["branchRefCount"] == 3
    assert result["pullHeadRefCount"] == 1
    assert result["partialCloneRefetchedWithoutFilter"] is False
    assert len(result["refInventorySha256"]) == 64


def test_prepare_repository_refetches_partial_clone_without_filter(
    tmp_path,
    monkeypatch,
):
    repository = _prepared_fixture(tmp_path)
    _git(repository, "config", "extensions.partialClone", "origin")
    fetches = []
    real_git = repository_prep._git

    def fake_git(path, *arguments):
        if arguments[0] == "fetch":
            fetches.append(arguments)
            return ""
        return real_git(path, *arguments)

    monkeypatch.setattr(repository_prep, "_git", fake_git)

    result = repository_prep.prepare_official_repository(repository)

    assert "--refetch" in fetches[0]
    assert result["partialCloneRefetchedWithoutFilter"] is True


def test_prepare_repository_rejects_nonofficial_origin(tmp_path):
    repository = _prepared_fixture(tmp_path)
    _git(repository, "remote", "set-url", "origin", "https://example.test/repo.git")

    with pytest.raises(ValueError, match="canonical HTTPS"):
        repository_prep.prepare_official_repository(repository)


def test_prepare_repository_rejects_origin_url_rewriting(tmp_path):
    repository = _prepared_fixture(tmp_path)
    _git(
        repository,
        "config",
        "url.file:///tmp/untrusted.insteadOf",
        repository_prep.OFFICIAL_REMOTE_URL,
    )

    with pytest.raises(ValueError, match="URL rewriting"):
        repository_prep.prepare_official_repository(repository)


def test_prepare_repository_rejects_custom_upload_pack(tmp_path):
    repository = _prepared_fixture(tmp_path)
    _git(repository, "config", "remote.origin.uploadpack", "/tmp/untrusted")

    with pytest.raises(ValueError, match="uploadpack"):
        repository_prep.prepare_official_repository(repository)


def test_prepare_repository_rejects_ssh_origin(tmp_path):
    repository = _prepared_fixture(tmp_path)
    _git(
        repository,
        "remote",
        "set-url",
        "origin",
        "ssh://git@github.com/magento/magento2.git",
    )

    with pytest.raises(ValueError, match="canonical HTTPS"):
        repository_prep.prepare_official_repository(repository)


def test_prepare_repository_rejects_repository_ssh_command_spoof(tmp_path):
    repository = _prepared_fixture(tmp_path)
    _git(
        repository,
        "config",
        "core.sshCommand",
        "/tmp/operator-controlled-transport",
    )

    with pytest.raises(ValueError, match="core.sshcommand.*SSH command override"):
        repository_prep.prepare_official_repository(repository)


def test_repository_prep_git_commands_disable_reference_transaction_hooks(
    tmp_path,
):
    repository = _prepared_fixture(tmp_path)
    marker = tmp_path / "reference-transaction-ran"
    hook = repository / ".git" / "hooks" / "reference-transaction"
    hook.write_text(
        "#!/bin/sh\n"
        f"touch {marker}\n",
        encoding="utf-8",
    )
    hook.chmod(0o700)
    revision = _git(repository, "rev-parse", "HEAD")

    _git(repository, "update-ref", "refs/audit/control", revision)
    assert marker.exists(), "control must demonstrate the local hook can execute"
    marker.unlink()

    repository_prep._git(
        repository,
        "update-ref",
        "refs/audit/preparation-guarded",
        revision,
    )

    assert not marker.exists()


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("remote.backup.uploadpack", "/tmp/untrusted", "custom uploadpack"),
        ("remote.origin.proxy", "socks5://127.0.0.1:9999", "remote proxy"),
        ("remote.origin.proxyAuthMethod", "basic", "remote proxy"),
        ("http.proxy", "http://127.0.0.1:9999", "HTTP proxy/TLS/header"),
        ("http.sslVerify", "false", "HTTP proxy/TLS/header"),
        ("http.extraHeader", "Host: attacker.invalid", "HTTP proxy/TLS/header"),
        ("include.path", "/tmp/operator-controlled.gitconfig", "external config include"),
    ],
)
def test_prepare_repository_rejects_transport_affecting_local_config(
    tmp_path,
    key,
    value,
    message,
):
    repository = _prepared_fixture(tmp_path)
    _git(repository, "config", key, value)

    with pytest.raises(ValueError, match=message):
        repository_prep.prepare_official_repository(repository)


@pytest.mark.parametrize(
    "remote_url",
    [
        "https://github.com/magento/magento2",
        "https://github.com/magento/magento2.git",
        "https://github.com/magento/magento2/",
        "https://github.com/magento/magento2.git/",
    ],
)
def test_official_remote_url_forms_are_explicit(remote_url):
    assert repository_prep._official_remote(remote_url) is True


@pytest.mark.parametrize(
    "remote_url",
    [
        "git://github.com/magento/magento2.git",
        "https://user@github.com/magento/magento2.git",
        "https://github.com/other/magento2.git",
        "https://github.com/magento/magento2.git?token=secret",
        "git@github.com:magento/magento2.git",
        "ssh://git@github.com/magento/magento2.git",
        "file:///tmp/magento2.git",
    ],
)
def test_nonofficial_or_credential_bearing_urls_are_rejected(remote_url):
    assert repository_prep._official_remote(remote_url) is False
