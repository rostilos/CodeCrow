import asyncio
import hashlib
import io
import os
import stat
import time
import zipfile
from pathlib import Path

import pytest

from model.dtos import AgenticRepositoryArchive
from service.review.agentic.workspace import AgenticWorkspace


HEAD_SHA = "b" * 40


def _archive_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _descriptor(workspace_key: str, content: bytes) -> AgenticRepositoryArchive:
    return AgenticRepositoryArchive(
        workspaceKey=workspace_key,
        snapshotSha=HEAD_SHA,
        contentDigest=hashlib.sha256(content).hexdigest(),
        byteLength=len(content),
    )


def _write_archive(root: Path, descriptor: AgenticRepositoryArchive, content: bytes) -> Path:
    directory = root / descriptor.workspaceKey
    directory.mkdir(parents=True, mode=0o700)
    archive_path = directory / "repository.zip"
    archive_path.write_bytes(content)
    archive_path.chmod(0o600)
    return archive_path


@pytest.mark.asyncio
async def test_workspace_extracts_exact_archive_and_always_deletes_it(tmp_path):
    content = _archive_bytes({
        "repo-root/src/app.py": b"def run():\n    return 1\n",
        "repo-root/config/app.xml": b"<config/>\n",
    })
    descriptor = _descriptor("a" * 64, content)
    _write_archive(tmp_path, descriptor, content)

    async with AgenticWorkspace(tmp_path, descriptor, expected_head_sha=HEAD_SHA) as source:
        assert (source / "src/app.py").read_text() == "def run():\n    return 1\n"
        assert (source / "config/app.xml").read_text() == "<config/>\n"
        assert not (tmp_path / descriptor.workspaceKey / "repository.zip").exists()
        assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
        assert stat.S_IMODE(source.stat().st_mode) == 0o700
        assert stat.S_IMODE((source / "src").stat().st_mode) == 0o700
        assert stat.S_IMODE((source / "src/app.py").stat().st_mode) == 0o600

    assert not (tmp_path / descriptor.workspaceKey).exists()


@pytest.mark.asyncio
async def test_workspace_does_not_treat_a_single_root_file_as_archive_wrapper(tmp_path):
    content = _archive_bytes({"README.md": b"# repository\n"})
    descriptor = _descriptor("9" * 64, content)
    _write_archive(tmp_path, descriptor, content)

    async with AgenticWorkspace(tmp_path, descriptor, expected_head_sha=HEAD_SHA) as source:
        assert (source / "README.md").read_text() == "# repository\n"

    assert not (tmp_path / descriptor.workspaceKey).exists()


@pytest.mark.asyncio
async def test_workspace_does_not_require_follow_symlink_chmod_support(
    tmp_path, monkeypatch
):
    content = _archive_bytes({"repo-root/src/app.py": b"value = 1\n"})
    descriptor = _descriptor("0" * 64, content)
    _write_archive(tmp_path, descriptor, content)
    original_chmod = Path.chmod

    def platform_chmod(self, mode, *, follow_symlinks=True):
        if not follow_symlinks:
            raise NotImplementedError(
                "chmod: follow_symlinks unavailable on this platform"
            )
        return original_chmod(self, mode)

    monkeypatch.setattr(Path, "chmod", platform_chmod)

    async with AgenticWorkspace(
        tmp_path, descriptor, expected_head_sha=HEAD_SHA
    ) as source:
        assert (source / "src/app.py").read_text() == "value = 1\n"

    assert not (tmp_path / descriptor.workspaceKey).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_kind", ["exception", "timeout", "cancellation"])
async def test_workspace_cleanup_covers_every_request_exit(tmp_path, exit_kind):
    content = _archive_bytes({"repo-root/src/app.py": b"value = 1\n"})
    descriptor = _descriptor(
        hashlib.sha256(exit_kind.encode("utf-8")).hexdigest(), content
    )
    _write_archive(tmp_path, descriptor, content)

    async def use_workspace():
        async with AgenticWorkspace(
            tmp_path, descriptor, expected_head_sha=HEAD_SHA
        ) as source:
            assert (source / "src/app.py").exists()
            if exit_kind == "exception":
                raise RuntimeError("model failed")
            await asyncio.sleep(60)

    task = asyncio.create_task(use_workspace())
    await asyncio.sleep(0)
    if exit_kind == "exception":
        with pytest.raises(RuntimeError, match="model failed"):
            await task
    elif exit_kind == "timeout":
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.01):
                await task
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert not (tmp_path / descriptor.workspaceKey).exists()


@pytest.mark.asyncio
async def test_workspace_rejects_digest_mismatch_and_cleans(tmp_path):
    content = _archive_bytes({"repo/a.py": b"print('safe')\n"})
    descriptor = _descriptor("c" * 64, content)
    tampered = bytes([content[0] ^ 0x01]) + content[1:]
    _write_archive(tmp_path, descriptor, tampered)

    with pytest.raises(ValueError, match="digest"):
        async with AgenticWorkspace(tmp_path, descriptor, expected_head_sha=HEAD_SHA):
            pass

    assert not (tmp_path / descriptor.workspaceKey).exists()


@pytest.mark.asyncio
async def test_workspace_rejects_revision_mismatch_without_reading_archive(tmp_path):
    content = _archive_bytes({"repo/a.py": b"pass\n"})
    descriptor = _descriptor("d" * 64, content)
    _write_archive(tmp_path, descriptor, content)

    with pytest.raises(ValueError, match="snapshot"):
        async with AgenticWorkspace(tmp_path, descriptor, expected_head_sha="e" * 40):
            pass

    assert not (tmp_path / descriptor.workspaceKey).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry_name",
    ["../escape.py", "/absolute.py", "repo/../../escape.py", "C:/escape.py"],
)
async def test_workspace_rejects_path_escape_and_cleans(tmp_path, entry_name):
    content = _archive_bytes({entry_name: b"escape"})
    descriptor = _descriptor(hashlib.sha256(entry_name.encode()).hexdigest(), content)
    _write_archive(tmp_path, descriptor, content)

    with pytest.raises(ValueError, match="archive entry"):
        async with AgenticWorkspace(tmp_path, descriptor, expected_head_sha=HEAD_SHA):
            pass

    assert not (tmp_path / descriptor.workspaceKey).exists()
    assert not (tmp_path.parent / "escape.py").exists()


@pytest.mark.asyncio
async def test_workspace_skips_symlink_and_extracts_regular_source(tmp_path):
    external = tmp_path / "external.txt"
    external.write_text("untouched")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        entry = zipfile.ZipInfo("repo/external-link")
        entry.create_system = 3
        entry.external_attr = 0o120777 << 16
        archive.writestr(entry, "../../external.txt")
        archive.writestr("repo/src/app.py", "value = 1\n")
    content = buffer.getvalue()
    descriptor = _descriptor("f" * 64, content)
    _write_archive(tmp_path, descriptor, content)

    workspace = AgenticWorkspace(
        tmp_path, descriptor, expected_head_sha=HEAD_SHA
    )
    async with workspace as source:
        assert not (source / "external-link").exists()
        assert (source / "src/app.py").read_text() == "value = 1\n"
        assert workspace.skipped_entries == [
            {
                "path": "external-link",
                "byteLength": len("../../external.txt"),
                "reason": "symlink",
            }
        ]

    assert not (tmp_path / descriptor.workspaceKey).exists()
    assert external.read_text() == "untouched"


@pytest.mark.asyncio
async def test_workspace_rejects_special_file_entries(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        entry = zipfile.ZipInfo("repo/pipe")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFIFO | 0o600) << 16
        archive.writestr(entry, b"")
    content = buffer.getvalue()
    descriptor = _descriptor("8" * 64, content)
    _write_archive(tmp_path, descriptor, content)

    with pytest.raises(ValueError, match="special|symlink"):
        async with AgenticWorkspace(tmp_path, descriptor, expected_head_sha=HEAD_SHA):
            pass

    assert not (tmp_path / descriptor.workspaceKey).exists()


@pytest.mark.asyncio
async def test_workspace_rejects_duplicate_normalized_entries(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("repo/src/app.py", b"first")
        archive.writestr("repo/src/./app.py", b"second")
    content = buffer.getvalue()
    descriptor = _descriptor("7" * 64, content)
    _write_archive(tmp_path, descriptor, content)

    with pytest.raises(ValueError, match="duplicate"):
        async with AgenticWorkspace(tmp_path, descriptor, expected_head_sha=HEAD_SHA):
            pass

    assert not (tmp_path / descriptor.workspaceKey).exists()


@pytest.mark.asyncio
async def test_workspace_enforces_expanded_size_limit(tmp_path):
    content = _archive_bytes({"repo/large.txt": b"x" * 1024})
    descriptor = _descriptor("1" * 64, content)
    _write_archive(tmp_path, descriptor, content)

    with pytest.raises(ValueError, match="expanded size"):
        async with AgenticWorkspace(
            tmp_path,
            descriptor,
            expected_head_sha=HEAD_SHA,
            max_expanded_bytes=128,
        ):
            pass

    assert not (tmp_path / descriptor.workspaceKey).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit_name", "workspace_options", "expected_error"),
    [
        ("compressed", {"max_archive_bytes": 16}, "archive exceeds size"),
        ("file_count", {"max_files": 1}, "file-count limit"),
    ],
)
async def test_workspace_enforces_other_archive_bomb_limits(
    tmp_path, limit_name, workspace_options, expected_error
):
    content = _archive_bytes(
        {"repo/one.txt": b"x" * 32, "repo/two.txt": b"y" * 32}
    )
    descriptor = _descriptor(hashlib.sha256(limit_name.encode()).hexdigest(), content)
    _write_archive(tmp_path, descriptor, content)

    with pytest.raises(ValueError, match=expected_error):
        async with AgenticWorkspace(
            tmp_path,
            descriptor,
            expected_head_sha=HEAD_SHA,
            **workspace_options,
        ):
            pass

    assert not (tmp_path / descriptor.workspaceKey).exists()


@pytest.mark.asyncio
async def test_workspace_skips_oversized_regular_file_and_extracts_the_rest(tmp_path):
    content = _archive_bytes(
        {
            "repo/static/demo.mp4": b"x" * 32,
            "repo/src/app.py": b"value=1\n",
        }
    )
    descriptor = _descriptor("4" * 64, content)
    _write_archive(tmp_path, descriptor, content)
    workspace = AgenticWorkspace(
        tmp_path,
        descriptor,
        expected_head_sha=HEAD_SHA,
        max_file_bytes=16,
        max_expanded_bytes=16,
    )

    async with workspace as source:
        assert not (source / "static/demo.mp4").exists()
        assert (source / "src/app.py").read_text() == "value=1\n"
        assert workspace.skipped_entries == [
            {
                "path": "static/demo.mp4",
                "byteLength": 32,
                "reason": "file_size_limit",
            }
        ]

    assert not (tmp_path / descriptor.workspaceKey).exists()


@pytest.mark.asyncio
async def test_workspace_extraction_deadline_fails_closed_and_cleans(tmp_path):
    content = _archive_bytes({"repo/large.txt": b"x" * (512 * 1024)})
    descriptor = _descriptor("5" * 64, content)
    _write_archive(tmp_path, descriptor, content)

    with pytest.raises(ValueError, match="time limit"):
        async with AgenticWorkspace(
            tmp_path,
            descriptor,
            expected_head_sha=HEAD_SHA,
            max_extract_seconds=1e-12,
        ):
            pass

    assert not (tmp_path / descriptor.workspaceKey).exists()


@pytest.mark.asyncio
async def test_cleanup_never_follows_an_invalid_descriptor_outside_root(tmp_path):
    victim = tmp_path.parent / f"victim-{tmp_path.name}"
    victim.mkdir()
    marker = victim / "keep"
    marker.write_text("safe")
    descriptor = AgenticRepositoryArchive.model_construct(
        workspaceKey=f"../{victim.name}",
        snapshotSha=HEAD_SHA,
        contentDigest="0" * 64,
        byteLength=1,
    )

    with pytest.raises(ValueError, match="workspace key"):
        async with AgenticWorkspace(tmp_path, descriptor, expected_head_sha=HEAD_SHA):
            pass

    assert marker.read_text() == "safe"


@pytest.mark.asyncio
async def test_workspace_rejects_a_symlinked_storage_root_without_deleting_target(
    tmp_path,
):
    actual_root = tmp_path / "actual"
    actual_root.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(actual_root, target_is_directory=True)
    content = _archive_bytes({"repo/app.py": b"pass\n"})
    descriptor = _descriptor("6" * 64, content)
    archive = _write_archive(actual_root, descriptor, content)

    with pytest.raises(ValueError, match="storage root"):
        async with AgenticWorkspace(root_link, descriptor, expected_head_sha=HEAD_SHA):
            pass

    assert archive.exists()


def test_cleanup_stale_removes_only_expired_workspace_directories(tmp_path):
    stale = tmp_path / ("2" * 64)
    fresh = tmp_path / ("3" * 64)
    unrelated = tmp_path / "not-a-workspace"
    for directory in (stale, fresh, unrelated):
        directory.mkdir()
        (directory / "marker").write_text("x")
    old = time.time() - 7200
    os.utime(stale, (old, old))

    removed = AgenticWorkspace.cleanup_stale(tmp_path, ttl_seconds=3600)

    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()
    assert unrelated.exists()
