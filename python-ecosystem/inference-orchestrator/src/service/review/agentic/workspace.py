"""Secure lifecycle for an exact-head repository archive.

The VCS-owning Java worker streams an archive into an execution directory on an
ephemeral shared volume.  This module verifies the bound archive coordinates,
extracts it without invoking repository code or a shell, and removes every byte
when the review finishes or fails.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import stat
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Optional

from model.dtos import AgenticRepositoryArchiveV1


_WORKSPACE_KEY = re.compile(r"^[0-9a-f]{64}$")
logger = logging.getLogger(__name__)


class AgenticWorkspace:
    """Async context manager for one verified, read-only repository snapshot."""

    def __init__(
        self,
        storage_root: Path | str,
        descriptor: AgenticRepositoryArchiveV1,
        *,
        expected_head_sha: str,
        max_archive_bytes: int = 512 * 1024 * 1024,
        max_expanded_bytes: int = 2 * 1024 * 1024 * 1024,
        max_file_bytes: int = 25 * 1024 * 1024,
        max_files: int = 200_000,
        max_extract_seconds: float = 120.0,
    ) -> None:
        # Keep the lexical final component so a configured root symlink can be
        # rejected instead of being silently followed by Path.resolve().
        self.storage_root = Path(storage_root).absolute()
        self.descriptor = descriptor
        self.expected_head_sha = expected_head_sha
        self.max_archive_bytes = max_archive_bytes
        self.max_expanded_bytes = max_expanded_bytes
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files
        if (
            not isinstance(max_extract_seconds, (int, float))
            or isinstance(max_extract_seconds, bool)
            or max_extract_seconds <= 0
            or max_extract_seconds > 600
        ):
            raise ValueError(
                "max_extract_seconds must be greater than zero and at most 600"
            )
        self.max_extract_seconds = float(max_extract_seconds)
        self.execution_dir = self.storage_root / descriptor.workspaceKey
        self.archive_path = self.execution_dir / "repository.zip"
        self.source_path = self.execution_dir / "source"
        self._entered = False
        self.skipped_entries: list[dict[str, object]] = []

    async def __aenter__(self) -> Path:
        try:
            # Extraction is intentionally completed before the LLM/tool loop
            # starts. Keeping it in the owning request task also guarantees
            # cancellation cannot strand a background extraction thread.
            source = self._prepare()
            self._entered = True
            return source
        except BaseException:
            self._cleanup()
            raise

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self._cleanup()

    def _prepare(self) -> Path:
        if not _WORKSPACE_KEY.fullmatch(self.descriptor.workspaceKey):
            raise ValueError("agentic workspace key is invalid")
        if self.descriptor.snapshotSha != self.expected_head_sha:
            raise ValueError("agentic repository snapshot does not match review head")
        self.storage_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.storage_root.is_symlink() or not self.storage_root.is_dir():
            raise ValueError("agentic storage root is not a secure directory")
        self.storage_root.chmod(0o700)
        root = self.storage_root.resolve(strict=True)
        if self.execution_dir.is_symlink() or not self.execution_dir.is_dir():
            raise ValueError("agentic workspace is not a secure directory")
        execution = self.execution_dir.resolve(strict=True)
        if execution.parent != root:
            raise ValueError("agentic workspace escapes configured storage root")
        self.execution_dir.chmod(0o700)
        archive = self.archive_path
        try:
            archive_mode = archive.stat(follow_symlinks=False).st_mode
        except FileNotFoundError as error:
            raise ValueError("agentic repository archive is not a regular file") from error
        if archive.is_symlink() or not stat.S_ISREG(archive_mode):
            raise ValueError("agentic repository archive is not a regular file")
        observed_size = self._secure_archive_permissions(archive)
        if observed_size != self.descriptor.byteLength:
            raise ValueError("agentic repository archive byte length mismatch")
        if observed_size > self.max_archive_bytes:
            raise ValueError("agentic repository archive exceeds size limit")
        deadline = time.monotonic() + self.max_extract_seconds
        observed_digest = self._sha256(archive, deadline)
        if observed_digest != self.descriptor.contentDigest:
            raise ValueError("agentic repository archive digest mismatch")

        self.source_path.mkdir(mode=0o700)
        self._extract(archive, self.source_path, deadline)
        archive.unlink()
        return self.source_path

    @staticmethod
    def _secure_archive_permissions(archive: Path) -> int:
        """Set owner-only permissions without relying on symlink chmod support."""

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(archive, flags | no_follow)
        except (FileNotFoundError, OSError) as error:
            raise ValueError(
                "agentic repository archive is not a regular file"
            ) from error
        try:
            archive_stat = os.fstat(descriptor)
            if not stat.S_ISREG(archive_stat.st_mode):
                raise ValueError(
                    "agentic repository archive is not a regular file"
                )
            os.fchmod(descriptor, 0o600)
            return archive_stat.st_size
        finally:
            os.close(descriptor)

    @classmethod
    def _sha256(cls, path: Path, deadline: float) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                cls._check_deadline(deadline)
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _check_deadline(deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise ValueError("repository archive preparation exceeded time limit")

    def _extract(self, archive_path: Path, target: Path, deadline: float) -> None:
        self._check_deadline(deadline)
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            self._check_deadline(deadline)
            if len(entries) > self.max_files:
                raise ValueError("repository archive exceeds file-count limit")
            normalized = []
            for info in entries:
                self._check_deadline(deadline)
                normalized.append(self._validated_parts(info))
            root_component = self._common_archive_root(normalized, entries)
            destinations: set[tuple[str, ...]] = set()
            adjusted_entries: list[tuple[str, ...]] = []
            for parts in normalized:
                self._check_deadline(deadline)
                if root_component is not None and parts and parts[0] == root_component:
                    parts = parts[1:]
                if parts and parts in destinations:
                    raise ValueError("repository archive contains duplicate entries")
                if parts:
                    destinations.add(parts)
                adjusted_entries.append(parts)
            expanded = 0
            extracted_files = 0
            target_root = target.resolve()

            for info, parts in zip(entries, adjusted_entries):
                self._check_deadline(deadline)
                if not parts:
                    continue
                output = target.joinpath(*parts)
                resolved_output = output.resolve(strict=False)
                if resolved_output != target_root and target_root not in resolved_output.parents:
                    raise ValueError("repository archive entry escapes workspace")
                if info.is_dir():
                    output.mkdir(parents=True, exist_ok=True, mode=0o700)
                    output.chmod(0o700)
                    continue

                if info.file_size > self.max_file_bytes:
                    # The repository snapshot may legitimately contain large
                    # videos, generated assets, or data files that code-reading
                    # tools cannot consume. Keep the extraction boundary intact
                    # by never opening or writing the entry, without making the
                    # entire source snapshot unusable.
                    self.skipped_entries.append(
                        {
                            "path": "/".join(parts),
                            "byteLength": info.file_size,
                            "reason": "file_size_limit",
                        }
                    )
                    continue

                expanded += info.file_size
                extracted_files += 1
                if expanded > self.max_expanded_bytes:
                    raise ValueError("repository archive exceeds expanded size limit")
                if extracted_files > self.max_files:
                    raise ValueError("repository archive exceeds file-count limit")

                output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                output.parent.chmod(0o700)
                written = 0
                no_follow = getattr(os, "O_NOFOLLOW", 0)

                def secure_opener(path: str, flags: int) -> int:
                    return os.open(path, flags | no_follow, 0o600)

                with archive.open(info, "r") as source, open(
                    output, "xb", opener=secure_opener
                ) as destination:
                    while True:
                        self._check_deadline(deadline)
                        block = source.read(64 * 1024)
                        if not block:
                            break
                        written += len(block)
                        if written > info.file_size or written > self.max_file_bytes:
                            raise ValueError("repository archive entry expanded beyond declared size")
                        destination.write(block)
                if written != info.file_size:
                    raise ValueError("repository archive entry size does not match metadata")
                output.chmod(0o600)

            if self.skipped_entries:
                logger.info(
                    "Skipped %d oversized repository archive entries (%d bytes); "
                    "per-file extraction limit is %d bytes",
                    len(self.skipped_entries),
                    sum(int(item["byteLength"]) for item in self.skipped_entries),
                    self.max_file_bytes,
                )

    @staticmethod
    def _validated_parts(info: zipfile.ZipInfo) -> tuple[str, ...]:
        name = info.filename.replace("\\", "/")
        if not name or "\x00" in name or name.startswith("/"):
            raise ValueError("repository archive entry path is invalid")
        path = PurePosixPath(name)
        parts = tuple(part for part in path.parts if part not in ("", "."))
        if not parts or any(part == ".." for part in parts):
            raise ValueError("repository archive entry path is invalid")
        if ":" in parts[0]:
            raise ValueError("repository archive entry path is invalid")

        unix_mode = info.external_attr >> 16
        if unix_mode:
            entry_type = stat.S_IFMT(unix_mode)
            allowed = {0, stat.S_IFREG, stat.S_IFDIR}
            if entry_type not in allowed:
                raise ValueError("repository archive special or symlink entry is forbidden")
        return parts

    @staticmethod
    def _common_archive_root(
        entries: list[tuple[str, ...]],
        archive_entries: list[zipfile.ZipInfo],
    ) -> Optional[str]:
        populated = [
            (parts, info)
            for parts, info in zip(entries, archive_entries)
            if parts
        ]
        if not populated:
            return None
        # Provider archives normally wrap repository contents in one directory.
        # A real file at the archive root is repository content, not a wrapper;
        # stripping it would silently produce an empty snapshot.
        if any(len(parts) == 1 and not info.is_dir() for parts, info in populated):
            return None
        first = populated[0][0][0]
        if all(parts[0] == first for parts, _info in populated):
            return first
        return None

    def _cleanup(self) -> None:
        self.cleanup_workspace(
            self.storage_root,
            self.descriptor.workspaceKey,
        )

    @classmethod
    def cleanup_workspace(
        cls,
        storage_root: Path | str,
        workspace_key: str,
    ) -> bool:
        """Delete one canonical staged workspace without following links."""

        # Cleanup is an authorization boundary too: never derive a deletion
        # target from a malformed descriptor or a symlinked storage root.
        if not cls.is_valid_workspace_key(workspace_key):
            return False
        root_path = Path(storage_root).absolute()
        try:
            if root_path.is_symlink() or not root_path.is_dir():
                return False
            root = root_path.resolve(strict=True)
            candidate = root_path / workspace_key
            if candidate.parent != root_path:
                return False
            if candidate.is_symlink():
                candidate.unlink(missing_ok=True)
                return True
            if candidate.resolve(strict=False).parent != root:
                return False
            try:
                mode = candidate.stat(follow_symlinks=False).st_mode
            except FileNotFoundError:
                return False
            if stat.S_ISDIR(mode):
                shutil.rmtree(candidate, ignore_errors=False)
            else:
                candidate.unlink(missing_ok=True)
            return True
        except FileNotFoundError:
            return False

    @staticmethod
    def is_valid_workspace_key(workspace_key: object) -> bool:
        return isinstance(workspace_key, str) and bool(
            _WORKSPACE_KEY.fullmatch(workspace_key)
        )

    @classmethod
    def cleanup_stale(cls, storage_root: Path | str, *, ttl_seconds: int) -> int:
        root = Path(storage_root).absolute()
        if not root.exists():
            return 0
        if root.is_symlink() or not root.is_dir():
            raise ValueError("agentic storage root is not a secure directory")
        cutoff = time.time() - max(0, ttl_seconds)
        removed = 0
        for candidate in root.iterdir():
            if not _WORKSPACE_KEY.fullmatch(candidate.name):
                continue
            try:
                modified = candidate.lstat().st_mtime
                if modified >= cutoff:
                    continue
                if candidate.is_symlink():
                    candidate.unlink()
                elif candidate.is_dir():
                    shutil.rmtree(candidate)
                else:
                    candidate.unlink()
                removed += 1
            except FileNotFoundError:
                continue
        return removed

    @classmethod
    async def run_cleanup_loop(
        cls,
        storage_root: Path | str,
        *,
        ttl_seconds: int,
        interval_seconds: float = 15 * 60,
    ) -> None:
        """Periodically remove crash remnants until the owning task is cancelled."""

        if interval_seconds <= 0:
            raise ValueError("agentic cleanup interval must be positive")
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                cls.cleanup_stale(storage_root, ttl_seconds=ttl_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "Agentic periodic workspace cleanup failed: %s",
                    type(error).__name__,
                )
