"""Executable evidence contracts; synthetic fixtures never contact a model."""
import asyncio
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tarfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.review.runtime_verification import (
    RuntimeVerification, VerificationBinding, VerificationCheck,
    VerificationConfig, VerificationUnavailable, proposed_tree_archive,
    build_verification_context,
)


@pytest.fixture
def binding(tmp_path):
    base, overlay = tmp_path / "base", tmp_path / "overlay"
    base.mkdir()
    (overlay / "files").mkdir(parents=True)
    (base / "app.txt").write_text("old\n")
    (base / "removed.txt").write_text("deleted\n")
    (base / "kept.txt").write_text("retained\n")
    (overlay / "files" / "app.txt").write_text("proposed\n")
    (overlay / "manifest.json").write_text(json.dumps({
        "changedFiles": ["app.txt", "removed.txt"], "deletedFiles": ["removed.txt"],
    }))
    return VerificationBinding("tenant", "project", "base-revision", "head-revision", base, overlay)


def test_snapshot_composes_changed_deleted_and_unchanged_with_stable_identity(binding):
    data, digest = proposed_tree_archive(binding, VerificationConfig())
    assert proposed_tree_archive(binding, VerificationConfig()) == (data, digest)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        assert archive.getnames() == ["app.txt", "kept.txt"]
        assert archive.extractfile("app.txt").read() == b"proposed\n"
        assert archive.extractfile("kept.txt").read() == b"retained\n"
    assert (binding.base_path / "app.txt").read_text() == "old\n"
    assert (binding.base_path / "removed.txt").exists()


def test_missing_proposed_body_never_executes_old_version(binding):
    (binding.overlay_path / "files" / "app.txt").unlink()
    with pytest.raises(VerificationUnavailable, match="Changed source is missing"):
        proposed_tree_archive(binding, VerificationConfig())


@pytest.mark.parametrize("path", ["../../secret", "/etc/passwd", ".git/config", "a/../secret"])
def test_overlay_paths_cannot_leave_repository(binding, path):
    (binding.overlay_path / "manifest.json").write_text(json.dumps({
        "changedFiles": [path], "deletedFiles": [],
    }))
    with pytest.raises(VerificationUnavailable):
        proposed_tree_archive(binding, VerificationConfig())


def test_external_link_is_unavailable_and_never_reads_host_target(binding, tmp_path):
    secret = tmp_path / "other-tenant"
    secret.write_text("not for this review")
    (binding.base_path / "link").symlink_to(secret)
    with pytest.raises(VerificationUnavailable, match="link leaves"):
        proposed_tree_archive(binding, VerificationConfig())


def test_changed_file_symlink_cannot_cross_tenant_boundary(binding, tmp_path):
    (binding.overlay_path / "files" / "app.txt").unlink()
    (binding.overlay_path / "files" / "app.txt").symlink_to(tmp_path / "another-project")
    with pytest.raises(VerificationUnavailable):
        proposed_tree_archive(binding, VerificationConfig())


def test_internal_links_preserved_and_git_metadata_excluded(binding):
    (binding.base_path / "link").symlink_to("kept.txt")
    (binding.base_path / ".git").mkdir()
    (binding.base_path / ".git" / "config").write_text("credential")
    data, _ = proposed_tree_archive(binding, VerificationConfig())
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        assert archive.getmember("link").issym()
        assert archive.getmember("link").linkname == "kept.txt"
        assert not any(p.startswith(".git") for p in archive.getnames())


def test_archive_budget_includes_headers_and_source(binding):
    with pytest.raises(VerificationUnavailable, match="budget"):
        proposed_tree_archive(binding, VerificationConfig(max_source_bytes=20))


def test_large_manifest_is_bounded_before_changed_source_lookups(binding):
    (binding.overlay_path / "manifest.json").write_text(json.dumps({
        "changedFiles": [f"unavailable-{index}.py" for index in range(20)], "deletedFiles": [],
    }))
    with pytest.raises(VerificationUnavailable, match="manifest path budget"):
        proposed_tree_archive(binding, VerificationConfig(max_files=10))


def test_manifest_directory_removal_checks_ancestors_without_losing_other_paths(binding):
    (binding.base_path / "deleted-dir").mkdir()
    (binding.base_path / "deleted-dir" / "nested.txt").write_text("removed")
    (binding.base_path / "deleted-directory.txt").write_text("kept")
    manifest = binding.overlay_path / "manifest.json"
    value = json.loads(manifest.read_text())
    value["deletedFiles"].append("deleted-dir")
    manifest.write_text(json.dumps(value))
    data, _ = proposed_tree_archive(binding, VerificationConfig())
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        assert "deleted-dir/nested.txt" not in archive.getnames()
        assert "deleted-directory.txt" in archive.getnames()


def test_snapshot_deadline_and_cancellation_stop_before_source_execution(binding):
    import threading
    with pytest.raises(VerificationUnavailable, match="preparation deadline"):
        proposed_tree_archive(binding, VerificationConfig(snapshot_timeout_seconds=0))
    cancellation = threading.Event()
    cancellation.set()
    with pytest.raises(VerificationUnavailable, match="cancelled"):
        proposed_tree_archive(binding, VerificationConfig(), cancellation)


def test_no_overlay_only_permitted_for_identical_revision(binding):
    with pytest.raises(VerificationUnavailable, match="requires a complete proposed overlay"):
        proposed_tree_archive(replace(binding, overlay_path=None), VerificationConfig())
    proposed_tree_archive(replace(binding, overlay_path=None, source_revision="base-revision"), VerificationConfig())


@pytest.mark.asyncio
async def test_unknown_check_cannot_supply_command_or_consume_budget(binding):
    runner = RuntimeVerification(binding, VerificationConfig(checks=(VerificationCheck("unit", "busybox", ("true",)),)))
    runner._execute = AsyncMock(return_value={"status": "passed"})
    result = await runner.run("sh -c arbitrary")
    assert result["status"] == "unavailable"
    runner._execute.assert_not_called()
    assert runner._attempts == 0


@pytest.mark.asyncio
async def test_same_check_cached_and_attempts_bounded_even_on_failure(binding):
    checks = tuple(VerificationCheck(name, "busybox", ("true",)) for name in ("a", "b", "c"))
    runner = RuntimeVerification(binding, VerificationConfig(checks=checks, max_checks=2))
    runner._execute = AsyncMock(return_value={"status": "failed", "exitCode": 1})
    first = await runner.run("a", "a defect")
    assert first["binding"]["sourceRevision"] == "head-revision"
    assert first["treeSha256"]
    assert (await runner.run("a", "another hypothesis"))["cached"]
    assert (await runner.run("b"))["status"] == "failed"
    assert (await runner.run("c"))["diagnostic"] == "Request execution budget exhausted"
    assert runner._execute.await_count == 2


@pytest.mark.asyncio
async def test_unavailable_runtime_is_optional_evidence(binding):
    runner = RuntimeVerification(binding, VerificationConfig(
        checks=(VerificationCheck("unit", "busybox", ("true",)),),
        executable="codecrow-missing-runtime-test",
    ))
    result = await runner.run("unit")
    assert result["status"] == "unavailable"
    assert "No such file" in result["diagnostic"]


def test_configuration_does_not_accept_shell_strings_or_unbounded_deadline():
    for entry in ({"image": "busybox", "argv": "sh -c true"},
                  {"image": "busybox", "argv": ["true"], "timeoutSeconds": 999}):
        with pytest.raises(VerificationUnavailable):
            VerificationConfig.from_environment({"CODECROW_VERIFICATION_CHECKS": json.dumps({"unit": entry})})
    assert VerificationConfig.from_environment({}).checks == ()


def test_host_context_is_independent_of_index_and_uses_current_revision():
    request = SimpleNamespace(projectWorkspace="tenant", projectNamespace="project", localRepoPath="/staged/base",
                              localRepoRevision="base", currentCommitHash="new", commitHash="old",
                              localReviewOverlayPath="/staged/overlay", ragEnabled=False)
    assert build_verification_context(request, {}) is None
    context = build_verification_context(request, {"CODECROW_VERIFICATION_CHECKS": "{}"})
    assert context == {"workspace": "tenant", "project": "project", "base_revision": "base",
                       "source_revision": "new", "base_path": "/staged/base", "overlay_path": "/staged/overlay"}


def test_mcp_configuration_keeps_source_identity_out_of_model_arguments(monkeypatch):
    from utils.mcp_config import MCPConfigBuilder
    monkeypatch.setenv("CODECROW_VERIFICATION_CHECKS", '{"unit":{"image":"busybox","argv":["true"]}}')
    config = MCPConfigBuilder.build_config("vcs.jar", verification_context={"workspace": "tenant", "project": "private"})
    tool = config["mcpServers"]["codecrow-verification-mcp"]
    assert tool["args"] == ["-m", "service.review.runtime_verification_mcp"]
    assert tool["env"]["CODECROW_VERIFICATION_WORKSPACE"] == "tenant"
    assert "private" not in " ".join(tool["args"])


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("CODECROW_TEST_CONTAINER_VERIFICATION") != "1", reason="requires explicitly enabled local cached-image runtime")
async def test_real_container_executes_exact_tree_without_host_mounts(binding):
    image = os.environ.get("CODECROW_TEST_VERIFICATION_IMAGE", "busybox:latest")
    command = ("sh", "-c", "test \"$(cat app.txt)\" = proposed && test ! -e removed.txt && test \"$(cat kept.txt)\" = retained && test \"$(id -u)\" = 65534 && test ! -S /var/run/docker.sock && test -z \"$DOCKER_HOST\" && test ! -e /sys/class/net/eth0 && ! touch /host-write-check && echo VERIFIED")
    runner = RuntimeVerification(binding, VerificationConfig(checks=(VerificationCheck("exact-tree", image, command),)))
    result = await runner.run("exact-tree", "deleted files must be absent")
    assert result["status"] == "passed", result
    assert "VERIFIED" in result["output"]
    assert not result.get("cleanupDiagnostic")


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("CODECROW_TEST_CONTAINER_VERIFICATION") != "1", reason="requires explicitly enabled local cached-image runtime")
async def test_cancellation_removes_only_its_own_container(binding, monkeypatch):
    import uuid
    import service.review.runtime_verification as runtime
    suffix = uuid.uuid4().hex
    monkeypatch.setattr(runtime.uuid, "uuid4", lambda: SimpleNamespace(hex=suffix))
    image = os.environ.get("CODECROW_TEST_VERIFICATION_IMAGE", "busybox:latest")
    runner = RuntimeVerification(binding, VerificationConfig(checks=(VerificationCheck("wait", image, ("sleep", "30")),)))
    task = asyncio.create_task(runner.run("wait"))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    process = await asyncio.create_subprocess_exec("docker", "container", "inspect", "codecrow-verification-"+suffix,
                                                  stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    assert await process.wait() != 0


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("CODECROW_TEST_CONTAINER_VERIFICATION") != "1", reason="requires explicitly enabled local cached-image runtime")
async def test_real_container_timeout_and_output_bound(binding):
    image = os.environ.get("CODECROW_TEST_VERIFICATION_IMAGE", "busybox:latest")
    # Startup can consume a one-second deadline under load. Check output
    # bounding separately so a valid startup timeout is not a false failure.
    checks = (VerificationCheck("output", image, ("seq", "1", "1000"), timeout_seconds=30),
              VerificationCheck("timeout", image, ("sleep", "30"), timeout_seconds=1))
    runner = RuntimeVerification(binding, VerificationConfig(checks=checks, max_output_bytes=128))
    result = await runner.run("output")
    assert result["status"] == "passed", result
    assert result["outputTruncated"]
    assert len(result["output"].encode()) <= 128
    assert not result.get("cleanupDiagnostic")
    timed_out = await runner.run("timeout")
    assert timed_out["status"] == "timeout", timed_out
    assert not timed_out.get("cleanupDiagnostic")
