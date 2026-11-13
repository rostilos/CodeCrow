"""Optional, host-configured checks in isolated containers.

The model chooses a named check, never a command, image, source path or tenant.
The host supplies the immutable base snapshot and proposed-file overlay. Source
is streamed as a bounded archive; no host directory or credential is mounted.
Runtime failure is evidence unavailability, never a failure of core review.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import posixpath
import stat
import tarfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


class VerificationUnavailable(ValueError):
    """The optional check cannot run against a trustworthy bounded snapshot."""


def build_verification_context(request: Any, environ: Mapping[str, str] | None = None) -> dict[str, str] | None:
    """Bind execution from trusted review context, independently of RAG enablement."""
    env = os.environ if environ is None else environ
    if not env.get("CODECROW_VERIFICATION_CHECKS", "").strip():
        return None
    return {
        "workspace": str(getattr(request, "projectWorkspace", None) or ""),
        "project": str(getattr(request, "projectNamespace", None) or ""),
        "base_revision": str(getattr(request, "localRepoRevision", None) or ""),
        "source_revision": str(getattr(request, "currentCommitHash", None)
                               or getattr(request, "commitHash", None) or ""),
        "base_path": str(getattr(request, "localRepoPath", None) or ""),
        "overlay_path": str(getattr(request, "localReviewOverlayPath", None) or ""),
    }


@dataclass(frozen=True)
class VerificationBinding:
    workspace: str
    project: str
    base_revision: str
    source_revision: str
    base_path: Path
    overlay_path: Path | None

    def identity(self) -> dict[str, str]:
        return {"workspace": self.workspace, "project": self.project,
                "baseRevision": self.base_revision, "sourceRevision": self.source_revision}


@dataclass(frozen=True)
class VerificationCheck:
    name: str
    image: str
    argv: tuple[str, ...]
    description: str = ""
    timeout_seconds: float = 30


@dataclass(frozen=True)
class VerificationConfig:
    checks: tuple[VerificationCheck, ...] = ()
    executable: str = "docker"
    max_checks: int = 2
    max_source_bytes: int = 64 * 1024 * 1024
    max_files: int = 20_000
    max_output_bytes: int = 32_768
    snapshot_timeout_seconds: float = 15
    memory_mb: int = 512
    cpus: float = 1
    pids: int = 64

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "VerificationConfig":
        env = os.environ if environ is None else environ
        raw = env.get("CODECROW_VERIFICATION_CHECKS", "").strip()
        if not raw:
            return cls()
        try:
            entries = json.loads(raw)
            if not isinstance(entries, dict):
                raise ValueError("checks must be an object")
            checks = []
            for name, entry in entries.items():
                image, argv = entry["image"], entry["argv"]
                if (not isinstance(name, str) or not name.strip()
                        or not isinstance(image, str) or not image or image.startswith("-") or "\0" in image
                        or not isinstance(argv, list) or not argv
                        or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
                        or not argv[0]):
                    raise ValueError("each check needs a name, image and nonempty argv")
                timeout = float(entry.get("timeoutSeconds", 30))
                if not 0 < timeout <= 120:
                    raise ValueError("timeoutSeconds must be in (0, 120]")
                checks.append(VerificationCheck(name, image, tuple(argv),
                                               str(entry.get("description", "")), timeout))
            return cls(checks=tuple(checks),
                       executable=env.get("CODECROW_VERIFICATION_RUNTIME", "docker"))
        except (ValueError, TypeError, KeyError) as exc:
            raise VerificationUnavailable(f"Invalid verification configuration: {exc}") from exc


def _relative_path(raw: Any) -> str:
    if not isinstance(raw, str) or not raw or "\0" in raw:
        raise VerificationUnavailable("Invalid proposed-tree path")
    value = raw.replace("\\", "/")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise VerificationUnavailable("Proposed-tree path leaves its repository")
    if path.parts[0] == ".git":
        raise VerificationUnavailable("Repository metadata is not executable source")
    return path.as_posix()


def _read_file(root: Path, relative: str, limit: int) -> bytes:
    path = root / relative
    # Reject symlink ancestors as well as symlink leaf nodes. Host-created
    # snapshots are immutable; this also avoids following repository links into
    # a different tenant's staging directory while constructing the archive.
    cursor = root
    for part in PurePosixPath(relative).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise VerificationUnavailable("Source file crosses a symbolic link")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise VerificationUnavailable("Source entry is not a regular file")
            data = source.read(limit + 1)
    except OSError as exc:
        raise VerificationUnavailable("Proposed source content is unavailable") from exc
    if len(data) > limit:
        raise VerificationUnavailable("Proposed tree exceeds the execution source budget")
    return data


def proposed_tree_archive(binding: VerificationBinding, config: VerificationConfig,
                          cancelled: threading.Event | None = None) -> tuple[bytes, str]:
    """Build exact target + changed bodies - deletions, excluding VCS metadata.

    Archive modes are normalized and links may only resolve inside the container
    repository. Missing changed content never silently falls back to old source.
    """
    deadline = time.monotonic() + config.snapshot_timeout_seconds

    def check_budget():
        if (cancelled is not None and cancelled.is_set()) or time.monotonic() >= deadline:
            raise VerificationUnavailable("Proposed snapshot preparation deadline exhausted or cancelled")

    check_budget()
    if not all((binding.workspace, binding.project, binding.base_revision, binding.source_revision)):
        raise VerificationUnavailable("Execution requires host-bound tenant and revision identity")
    base = binding.base_path
    if not base.is_dir() or base.is_symlink():
        raise VerificationUnavailable("Base snapshot is unavailable")
    entries: dict[str, tuple[Path, os.stat_result]] = {}
    directories = 0
    for directory, names, files in os.walk(base, followlinks=False):
        check_budget()
        directories += 1
        if directories > config.max_files:
            raise VerificationUnavailable("Proposed tree exceeds the execution directory budget")
        names[:] = sorted(name for name in names if name != ".git")
        for name in sorted([name for name in files if name != ".git"] + [name for name in names if (Path(directory)/name).is_symlink()]):
            check_budget()
            path = Path(directory) / name
            relative = _relative_path(path.relative_to(base).as_posix())
            entries[relative] = (base, path.lstat())
            if len(entries) > config.max_files:
                raise VerificationUnavailable("Proposed tree exceeds the execution file budget")
    if binding.overlay_path is not None:
        overlay = binding.overlay_path
        if not overlay.is_dir() or overlay.is_symlink() or (overlay / "files").is_symlink():
            raise VerificationUnavailable("Proposed overlay is unavailable")
        try:
            manifest = json.loads(_read_file(overlay, "manifest.json", 4 * 1024 * 1024))
            changed, deleted = manifest["changedFiles"], manifest["deletedFiles"]
            if not isinstance(changed, list) or not isinstance(deleted, list):
                raise ValueError("path lists required")
            if len(changed) > config.max_files or len(deleted) > config.max_files:
                raise VerificationUnavailable("Proposed overlay exceeds the execution manifest path budget")
            changed = {_relative_path(p) for p in changed}
            deleted = {_relative_path(p) for p in deleted}
            if len(changed | deleted) > config.max_files:
                raise VerificationUnavailable("Proposed overlay exceeds the execution manifest path budget")
        except VerificationUnavailable:
            raise
        except (ValueError, TypeError, KeyError) as exc:
            raise VerificationUnavailable("Proposed overlay manifest is unavailable or malformed") from exc
        removed = deleted | changed
        # Inspect each existing path once, checking only its ancestors. Scanning
        # every base entry for each changed/deleted path is quadratic even when
        # the source and manifest independently fit their byte budgets.
        for existing in list(entries):
            check_budget()
            if existing in removed or any(parent.as_posix() in removed
                                          for parent in PurePosixPath(existing).parents):
                del entries[existing]
        for relative in changed - deleted:
            check_budget()
            path = overlay / "files" / relative
            try:
                info = path.lstat()
            except OSError as exc:
                raise VerificationUnavailable("Changed source is missing from the proposed overlay") from exc
            entries[relative] = (overlay / "files", info)
    elif binding.base_revision != binding.source_revision:
        raise VerificationUnavailable("A changed revision requires a complete proposed overlay")
    if len(entries) > config.max_files:
        raise VerificationUnavailable("Proposed tree exceeds the execution file budget")
    output = io.BytesIO()
    total = 0
    with tarfile.open(fileobj=output, mode="w") as archive:
        for relative, (root, info) in sorted(entries.items()):
            check_budget()
            item = tarfile.TarInfo(relative)
            if stat.S_ISLNK(info.st_mode):
                link = os.readlink(root / relative)
                target = posixpath.normpath(posixpath.join(posixpath.dirname(relative), link))
                if link.startswith("/") or target == ".." or target.startswith("../"):
                    raise VerificationUnavailable("Repository link leaves the proposed tree")
                item.type, item.linkname, item.mode = tarfile.SYMTYPE, link, 0o777
                archive.addfile(item)
            elif stat.S_ISREG(info.st_mode):
                content = _read_file(root, relative, config.max_source_bytes - total)
                total += len(content)
                item.size = len(content)
                item.mode = 0o755 if info.st_mode & 0o111 else 0o644
                archive.addfile(item, io.BytesIO(content))
            else:
                raise VerificationUnavailable("Unsupported special file in proposed tree")
            # Include headers and long-name metadata in the bound, not just bytes.
            if output.tell() > config.max_source_bytes:
                raise VerificationUnavailable("Proposed archive exceeds the execution source budget")
    data = output.getvalue()
    if len(data) > config.max_source_bytes:
        raise VerificationUnavailable("Proposed archive exceeds the execution source budget")
    return data, hashlib.sha256(data).hexdigest()


class RuntimeVerification:
    """One request's named checks, bounded attempts, cached outcomes and evidence."""

    def __init__(self, binding: VerificationBinding, config: VerificationConfig):
        self.binding, self.config = binding, config
        self._lock = asyncio.Lock()
        self._attempts = 0
        self._results: dict[str, dict[str, Any]] = {}

    def catalog(self) -> list[dict[str, str]]:
        return [{"name": c.name, "description": c.description} for c in self.config.checks]

    async def run(self, check_name: str, hypothesis: str = "") -> dict[str, Any]:
        async with self._lock:
            if check_name in self._results:
                return {**self._results[check_name], "cached": True, "hypothesis": hypothesis[:2000]}
            result: dict[str, Any] = {
                "check": check_name, "hypothesis": hypothesis[:2000], "binding": self.binding.identity(),
                "status": "unavailable", "evidenceKind": "runtime_check", "cached": False,
                "interpretation": "A check outcome only covers the executed check; it does not prove or disprove every finding.",
            }
            check = next((c for c in self.config.checks if c.name == check_name), None)
            if check is None:
                return {**result, "diagnostic": "Check is not configured by the host", "checks": self.catalog()}
            if self._attempts >= self.config.max_checks:
                return {**result, "diagnostic": "Request execution budget exhausted"}
            self._attempts += 1
            start = time.monotonic()
            cancelled = threading.Event()
            try:
                payload, digest = await asyncio.to_thread(proposed_tree_archive, self.binding, self.config, cancelled)
                result["treeSha256"] = digest
                result.update(await self._execute(check, payload))
            except (VerificationUnavailable, OSError) as exc:
                result["diagnostic"] = str(exc)
            finally:
                cancelled.set()
            result["durationSeconds"] = round(time.monotonic() - start, 3)
            self._results[check_name] = result
            return result

    async def _execute(self, check: VerificationCheck, payload: bytes) -> dict[str, Any]:
        name = "codecrow-verification-" + uuid.uuid4().hex
        # Only this fixed bootstrap runs through a shell, inside the isolated
        # container. The configured argv is passed positionally, never interpolated.
        bootstrap = ('umask 077; tar -xf - -C /workspace && cd /workspace '
                     f'&& exec timeout -s KILL {check.timeout_seconds:g} "$@"')
        command = [self.config.executable, "run", "--rm", "--interactive", "--name", name,
                   "--pull=never", "--network=none", "--read-only", "--cap-drop=ALL",
                   "--security-opt=no-new-privileges", "--user=65534:65534",
                   f"--memory={self.config.memory_mb}m", f"--memory-swap={self.config.memory_mb}m",
                   f"--cpus={self.config.cpus}", f"--pids-limit={self.config.pids}",
                   "--log-driver=none", "--no-healthcheck", "--ipc=private",
                   f"--tmpfs=/workspace:rw,nosuid,nodev,size={self.config.max_source_bytes * 4},mode=1777",
                   "--workdir=/workspace", "--env=HOME=/workspace", "--env=TMPDIR=/workspace",
                   "--entrypoint=/bin/sh", check.image, "-c", bootstrap, "codecrow", *check.argv]
        process = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
        captured = bytearray()
        output_bytes = 0

        async def feed():
            try:
                for offset in range(0, len(payload), 65536):
                    process.stdin.write(payload[offset:offset + 65536])
                    await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        async def drain():
            nonlocal output_bytes
            while chunk := await process.stdout.read(8192):
                output_bytes += len(chunk)
                captured.extend(chunk)
                if len(captured) > self.config.max_output_bytes:
                    del captured[:len(captured) - self.config.max_output_bytes]

        async def complete():
            await asyncio.gather(feed(), drain())
            return await process.wait()

        cleanup_diagnostic = None
        try:
            code = await asyncio.wait_for(complete(), check.timeout_seconds)
            result = {"status": "passed" if code == 0 else "failed", "exitCode": code}
            if code == 124:
                result.update(status="timeout", diagnostic="Container execution deadline exhausted")
            if code in (125, 126, 127):
                result["status"] = "unavailable"
                result["diagnostic"] = "Container or configured executable could not start"
        except asyncio.TimeoutError:
            result = {"status": "timeout", "diagnostic": "Execution deadline exhausted"}
        finally:
            if process.returncode is None:
                process.kill()
            # wait_for cancels the ordinary drainer. Resume bounded draining
            # before wait(): a full stdout pipe can otherwise deadlock waiting
            # for subprocess transport shutdown, even after the child is killed.
            await asyncio.gather(drain(), process.wait())
            # Killing the local CLI alone does not stop the container. Remove only
            # this invocation's unguessable name, including on task cancellation.
            cleanup = asyncio.create_task(self._remove_container(name))
            try:
                cleanup_diagnostic = await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
        result.update({"output": captured.decode("utf-8", errors="replace"),
                       "outputTruncated": output_bytes > self.config.max_output_bytes,
                       "outputBytes": output_bytes, "image": check.image})
        if cleanup_diagnostic:
            result["cleanupDiagnostic"] = cleanup_diagnostic
        return result

    async def _remove_container(self, name: str) -> str | None:
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                self.config.executable, "rm", "--force", name,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, error = await asyncio.wait_for(process.communicate(), timeout=10)
            if process.returncode and b"No such container" not in error:
                return "Container cleanup could not be confirmed by the runtime"
        except (OSError, asyncio.TimeoutError):
            return "Container cleanup could not be confirmed by the runtime"
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
        return None
