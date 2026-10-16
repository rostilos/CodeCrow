from __future__ import annotations

import subprocess

import pytest

import magento2_benchmark.collect as collect
from magento2_benchmark.util import run as real_run


def _git(repository, *arguments):
    return subprocess.check_output(
        ["git", "-C", str(repository), *arguments],
        text=True,
    ).strip()


def _repository(tmp_path):
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Benchmark Test"],
        check=True,
    )
    (repository / "fixture.php").write_text(
        "<?php\nreturn 1;\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(repository), "add", "fixture.php"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-q", "-m", "fixture"],
        check=True,
    )
    return repository, _git(repository, "rev-parse", "HEAD")


def test_offline_git_preflight_accepts_worktree_without_remote_mutation(
    tmp_path,
    monkeypatch,
):
    repository, revision = _repository(tmp_path)
    original_remote = "https://example.invalid/original.git"
    subprocess.run(
        ["git", "-C", str(repository), "remote", "add", "origin", original_remote],
        check=True,
    )
    worktree = tmp_path / "linked-worktree"
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "worktree",
            "add",
            "-q",
            "-b",
            "offline-fixture",
            str(worktree),
            revision,
        ],
        check=True,
    )
    assert (worktree / ".git").is_file()

    calls = []

    def recording_run(command, **kwargs):
        calls.append((list(command), kwargs.get("env")))
        return real_run(command, **kwargs)

    monkeypatch.setattr(collect, "run", recording_run)
    git_env = collect._offline_git_environment()
    collect._ensure_git_repository(
        worktree,
        "magento/magento2",
        offline=True,
        git_env=git_env,
    )
    collect._fetch_case(
        worktree,
        12_345,
        [revision],
        offline=True,
        git_env=git_env,
    )

    assert _git(worktree, "remote", "get-url", "origin") == original_remote
    assert calls
    assert all(
        not any(action in command for action in ("clone", "fetch", "remote"))
        for command, _ in calls
    )
    assert all(
        environment
        and environment.get("GIT_NO_LAZY_FETCH") == "1"
        and environment.get("GIT_TERMINAL_PROMPT") == "0"
        and environment.get("GIT_NO_REPLACE_OBJECTS") == "1"
        for _, environment in calls
    )


def test_offline_git_preflight_rejects_missing_objects_without_fetch(
    tmp_path,
    monkeypatch,
):
    repository, _ = _repository(tmp_path)
    calls = []

    def recording_run(command, **kwargs):
        calls.append(list(command))
        return real_run(command, **kwargs)

    monkeypatch.setattr(collect, "run", recording_run)
    with pytest.raises(
        ValueError,
        match="required local Git commit objects are missing",
    ):
        collect._fetch_case(
            repository,
            12_345,
            ["f" * 40],
            offline=True,
            git_env=collect._offline_git_environment(),
        )

    assert calls
    assert all("fetch" not in command for command in calls)


def test_offline_git_preflight_never_clones_missing_repository(
    tmp_path,
    monkeypatch,
):
    calls = []

    def recording_run(command, **kwargs):
        calls.append(list(command))
        return real_run(command, **kwargs)

    monkeypatch.setattr(collect, "run", recording_run)
    with pytest.raises(
        ValueError,
        match="existing local Git clone or linked worktree.*cloning is disabled",
    ):
        collect._ensure_git_repository(
            tmp_path / "missing",
            "magento/magento2",
            offline=True,
            git_env=collect._offline_git_environment(),
        )

    assert all("clone" not in command for command in calls)


def test_offline_snapshot_diff_never_executes_configured_textconv(
    tmp_path,
):
    repository = tmp_path / "textconv-repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Benchmark Test"],
        check=True,
    )
    (repository / ".gitattributes").write_text(
        "fixture.php diff=hostile\n",
        encoding="utf-8",
    )
    (repository / "fixture.php").write_text("return 1;\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-q", "-m", "base"],
        check=True,
    )
    base = _git(repository, "rev-parse", "HEAD")
    (repository / "fixture.php").write_text("return 2;\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-qam", "head"],
        check=True,
    )
    head = _git(repository, "rev-parse", "HEAD")

    marker = tmp_path / "textconv-was-executed"
    textconv = tmp_path / "hostile-textconv.sh"
    textconv.write_text(
        "#!/bin/sh\n"
        f"touch '{marker}'\n"
        "cat \"$1\"\n",
        encoding="utf-8",
    )
    textconv.chmod(0o700)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "config",
            "diff.hostile.textconv",
            str(textconv),
        ],
        check=True,
    )

    with pytest.raises(
        ValueError,
        match=r"repository-local diff\.\* configuration",
    ):
        collect._snapshot_diff(
            repository,
            base,
            head,
            git_env=collect._offline_git_environment(),
        )
    assert not marker.exists()
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "config",
            "--unset",
            "diff.hostile.textconv",
        ],
        check=True,
    )
    diff, paths = collect._snapshot_diff(
        repository,
        base,
        head,
        git_env=collect._offline_git_environment(),
    )

    assert paths == ["fixture.php"]
    assert "-return 1;" in diff
    assert "+return 2;" in diff
    assert not marker.exists()


def test_evidence_diff_forces_text_despite_host_attributes(tmp_path):
    repository, base = _repository(tmp_path)
    (repository / "fixture.php").write_text(
        "<?php\nreturn 2;\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-qam", "head"],
        check=True,
    )
    head = _git(repository, "rev-parse", "HEAD")
    (repository / ".git" / "info" / "attributes").write_text(
        "fixture.php -diff\n",
        encoding="utf-8",
    )
    global_attributes = tmp_path / "host-attributes"
    global_attributes.write_text("fixture.php -diff\n", encoding="utf-8")
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "config",
            "core.attributesFile",
            str(global_attributes),
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "color.diff", "always"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "core.quotePath", "false"],
        check=True,
    )
    plain = subprocess.check_output(
        ["git", "-C", str(repository), "diff", base, head],
        text=True,
    )
    assert "Binary files" in plain
    with pytest.raises(ValueError, match="host-only Git info/attributes"):
        collect._snapshot_diff(
            repository,
            base,
            head,
            git_env=collect._offline_git_environment(),
        )
    (repository / ".git" / "info" / "attributes").write_text(
        "",
        encoding="utf-8",
    )

    diff, paths = collect._snapshot_diff(
        repository,
        base,
        head,
        git_env=collect._offline_git_environment(),
    )

    assert paths == ["fixture.php"]
    assert "Binary files" not in diff
    assert "\x1b[" not in diff
    assert "diff --git a/fixture.php b/fixture.php" in diff
    assert "-return 1;" in diff
    assert "+return 2;" in diff


def test_offline_evidence_ignores_replace_refs_for_ancestry_and_diff(
    tmp_path,
):
    repository, base = _repository(tmp_path)
    (repository / "fixture.php").write_text(
        "<?php\nreturn 2;\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-qam", "head"],
        check=True,
    )
    head = _git(repository, "rev-parse", "HEAD")

    alternate_blob = subprocess.run(
        ["git", "-C", str(repository), "hash-object", "-w", "--stdin"],
        input="<?php\nreturn 9;\n",
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    alternate_tree = subprocess.run(
        ["git", "-C", str(repository), "mktree"],
        input=f"100644 blob {alternate_blob}\tfixture.php\n",
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    alternate_commit = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "commit-tree",
            alternate_tree,
            "-m",
            "unrelated replacement",
        ],
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(repository), "replace", head, alternate_commit],
        check=True,
    )
    (repository / ".git" / "info" / "grafts").write_text(
        f"{head}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="legacy Git grafts"):
        collect.validate_git_evidence_repository(repository)
    (repository / ".git" / "info" / "grafts").write_text(
        "",
        encoding="utf-8",
    )
    assert "return 9" in subprocess.check_output(
        ["git", "-C", str(repository), "show", f"{head}:fixture.php"],
        text=True,
    )

    git_env = collect._offline_git_environment()
    assert collect._parents(
        repository,
        head,
        git_env=git_env,
    ) == [base]
    diff, paths = collect._snapshot_diff(
        repository,
        base,
        head,
        git_env=git_env,
    )
    assert paths == ["fixture.php"]
    assert "+return 2;" in diff
    assert "return 9;" not in diff


def test_evidence_environment_scrubs_git_redirection_and_config_injection(
    tmp_path,
    monkeypatch,
):
    repository, revision = _repository(tmp_path)
    foreign = tmp_path / "foreign"
    subprocess.run(["git", "init", "-q", str(foreign)], check=True)
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
    monkeypatch.setenv(
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        str(foreign / ".git" / "objects"),
    )
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'diff.external=hostile'")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "diff.external")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "hostile")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")

    offline_env = collect._offline_git_environment()
    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
    ):
        assert key not in offline_env
    assert offline_env["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert offline_env["GIT_GRAFT_FILE"] == "/dev/null"
    assert offline_env["GIT_ATTR_NOSYSTEM"] == "1"
    assert offline_env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert offline_env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert offline_env["GIT_NO_LAZY_FETCH"] == "1"
    assert offline_env["HTTPS_PROXY"] == "http://proxy.invalid:8080"
    assert collect._parents(
        repository,
        revision,
        git_env=offline_env,
    ) == []

    live_env = collect.hermetic_git_environment(offline=False)
    assert "GIT_NO_LAZY_FETCH" not in live_env
    assert live_env["HTTPS_PROXY"] == "http://proxy.invalid:8080"


def test_offline_preflight_rejects_shallow_history(tmp_path):
    repository, revision = _repository(tmp_path)
    (repository / ".git" / "shallow").write_text(
        f"{revision}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="shallow Git history"):
        collect._ensure_git_repository(
            repository,
            "magento/magento2",
            offline=True,
            git_env=collect._offline_git_environment(),
        )
