"""Immutable source-tree verification for repository indexing."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, Mapping


SOURCE_TREE_SCHEMA = "codecrow.repository-source-tree"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RepositorySourceTreeError(RuntimeError):
    """The indexing source is not the attested immutable repository tree."""


@dataclass(frozen=True)
class RepositorySourceTree:
    """Verified source identity retained across the indexing operation."""

    commit: str
    tree_sha256: str
    git_commit_verified: bool
    file_sha256_by_path: Mapping[str, str]


def _feed_framed(hasher, value: bytes) -> None:
    hasher.update(len(value).to_bytes(8, "big"))
    hasher.update(value)


def _relative_parts(relative_path: str | Path) -> tuple[str, ...]:
    path = Path(relative_path)
    parts = path.parts
    if (
        path.is_absolute()
        or not parts
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise RepositorySourceTreeError(
            f"invalid repository-relative source path: {relative_path}"
        )
    return tuple(parts)


@contextmanager
def open_repository_file_no_follow(
    repo_path: str | Path,
    relative_path: str | Path,
) -> BinaryIO:
    """Open one regular repository file through pinned, no-follow descriptors."""
    parts = _relative_parts(relative_path)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    file_flags = os.O_RDONLY
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not no_follow:
        raise RepositorySourceTreeError(
            "repository source verification requires O_NOFOLLOW support"
        )
    directory_flags |= no_follow
    file_flags |= no_follow
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    directory_flags |= close_on_exec
    file_flags |= close_on_exec

    directory_fd = None
    file_fd = None
    try:
        directory_fd = os.open(os.fspath(repo_path), directory_flags)
        for component in parts[:-1]:
            child_fd = os.open(
                component,
                directory_flags,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = child_fd
        file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise RepositorySourceTreeError(
                "repository source entry is not a regular file: "
                + Path(*parts).as_posix()
            )
        with os.fdopen(file_fd, "rb", closefd=True) as source:
            file_fd = None
            yield source
    except OSError as exception:
        raise RepositorySourceTreeError(
            "cannot safely open repository source file: "
            + Path(*parts).as_posix()
        ) from exception
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def read_repository_file_bytes(
    repo_path: str | Path,
    relative_path: str | Path,
    *,
    expected_sha256: str | None = None,
) -> bytes:
    """Read one regular file without symlink traversal and verify its identity."""
    with open_repository_file_no_follow(repo_path, relative_path) as source:
        content = source.read()
    if (
        expected_sha256 is not None
        and hashlib.sha256(content).hexdigest() != expected_sha256
    ):
        raise RepositorySourceTreeError(
            "repository source file changed after attestation: "
            + Path(relative_path).as_posix()
        )
    return content


def _repository_entries(root: Path):
    """Yield non-Git source entries without following repository symlinks."""

    collected_entries = []
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    if not getattr(os, "O_NOFOLLOW", 0):
        raise RepositorySourceTreeError(
            "repository source verification requires O_NOFOLLOW support"
        )

    def visit(directory_fd: int, relative_directory: Path):
        try:
            with os.scandir(directory_fd) as scanner:
                entries = list(scanner)
        except OSError as exception:
            raise RepositorySourceTreeError(
                f"cannot enumerate repository source directory: {relative_directory}"
            ) from exception

        for entry in entries:
            relative_path = relative_directory / entry.name
            if relative_directory == Path() and entry.name == ".git":
                continue
            try:
                entry_stat = os.stat(
                    entry.name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                if stat.S_ISLNK(entry_stat.st_mode):
                    collected_entries.append(
                        (
                            "symlink",
                            relative_path,
                            os.readlink(entry.name, dir_fd=directory_fd),
                        )
                    )
                elif stat.S_ISDIR(entry_stat.st_mode):
                    child_fd = os.open(
                        entry.name,
                        directory_flags,
                        dir_fd=directory_fd,
                    )
                    try:
                        visit(child_fd, relative_path)
                    finally:
                        os.close(child_fd)
                elif stat.S_ISREG(entry_stat.st_mode):
                    collected_entries.append(
                        ("file", relative_path, None)
                    )
                else:
                    raise RepositorySourceTreeError(
                        "repository source contains an unsupported filesystem "
                        f"entry: {relative_path.as_posix()}"
                    )
            except OSError as exception:
                raise RepositorySourceTreeError(
                    "cannot inspect repository source entry: "
                    f"{relative_path.as_posix()}"
                ) from exception

    root_fd = None
    try:
        root_fd = os.open(os.fspath(root), directory_flags)
        visit(root_fd, Path())
    except OSError as exception:
        raise RepositorySourceTreeError(
            f"cannot safely enumerate repository source directory: {root}"
        ) from exception
    finally:
        if root_fd is not None:
            os.close(root_fd)
    collected_entries.sort(
        key=lambda item: item[1]
        .as_posix()
        .encode("utf-8", "surrogateescape")
    )
    yield from collected_entries


def iter_repository_regular_file_paths(
    repo_path: str | Path,
):
    """Yield regular-file paths from a no-follow repository traversal."""
    for kind, relative_path, _ in _repository_entries(Path(repo_path)):
        if kind == "file":
            yield relative_path


def _compute_repository_source_tree(
    repo_path: str | Path,
) -> tuple[str, Mapping[str, str]]:
    """Hash exact repository-relative paths and retain regular-file identities."""
    root = Path(repo_path)
    try:
        root_stat = root.lstat()
    except OSError as exception:
        raise RepositorySourceTreeError(
            f"repository source path is not a directory: {root}"
        ) from exception
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise RepositorySourceTreeError(
            f"repository source path is not a directory: {root}"
        )

    hasher = hashlib.sha256()
    _feed_framed(hasher, SOURCE_TREE_SCHEMA.encode("ascii"))
    entry_count = 0
    file_sha256_by_path: dict[str, str] = {}
    for kind, relative_path, value in _repository_entries(root):
        entry_count += 1
        _feed_framed(hasher, kind.encode("ascii"))
        _feed_framed(
            hasher,
            relative_path.as_posix().encode("utf-8", "surrogateescape"),
        )
        if kind == "symlink":
            _feed_framed(
                hasher,
                value.encode("utf-8", "surrogateescape"),
            )
            continue

        try:
            observed_size = 0
            file_hasher = hashlib.sha256()
            with open_repository_file_no_follow(root, relative_path) as source:
                expected_size = os.fstat(source.fileno()).st_size
                hasher.update(expected_size.to_bytes(8, "big"))
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    observed_size += len(chunk)
                    hasher.update(chunk)
                    file_hasher.update(chunk)
        except (OSError, RepositorySourceTreeError) as exception:
            raise RepositorySourceTreeError(
                "cannot read repository source file: "
                f"{relative_path.as_posix()}"
            ) from exception
        if observed_size != expected_size:
            raise RepositorySourceTreeError(
                "repository source changed while it was being attested: "
                f"{relative_path.as_posix()}"
            )
        file_sha256_by_path[relative_path.as_posix()] = file_hasher.hexdigest()

    hasher.update(entry_count.to_bytes(8, "big"))
    return hasher.hexdigest(), MappingProxyType(file_sha256_by_path)


def compute_repository_source_tree_sha256(repo_path: str | Path) -> str:
    """Hash exact repository-relative paths and bytes deterministically."""
    return _compute_repository_source_tree(repo_path)[0]


def _git_output(root: Path, *arguments: str) -> str:
    environment = dict(os.environ)
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exception:
        raise RepositorySourceTreeError(
            "cannot verify repository Git revision"
        ) from exception
    return result.stdout


def _verify_git_checkout(root: Path, commit: str) -> bool:
    """Require exact HEAD and no tracked or untracked working-tree changes."""
    git_marker = root / ".git"
    if not git_marker.exists():
        return False

    observed_commit = _git_output(root, "rev-parse", "--verify", "HEAD^{commit}").strip()
    if observed_commit != commit:
        raise RepositorySourceTreeError(
            "repository Git HEAD does not match the supplied commit: "
            f"expected={commit}, actual={observed_commit}"
        )
    status = _git_output(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if status:
        raise RepositorySourceTreeError(
            "repository Git working tree is not clean for the supplied commit"
        )
    return True


def verify_repository_source_tree(
    repo_path: str | Path,
    commit: str,
    expected_tree_sha256: str,
) -> RepositorySourceTree:
    """Verify the caller-attested tree and, for Git worktrees, exact HEAD."""
    if not isinstance(commit, str) or not commit:
        raise RepositorySourceTreeError("repository source commit is required")
    if (
        not isinstance(expected_tree_sha256, str)
        or not _SHA256_RE.fullmatch(expected_tree_sha256)
    ):
        raise RepositorySourceTreeError(
            "repository source tree requires a canonical SHA-256 attestation"
        )

    root = Path(repo_path)
    git_commit_verified = _verify_git_checkout(root, commit)
    observed_tree_sha256, file_sha256_by_path = _compute_repository_source_tree(
        root
    )
    if observed_tree_sha256 != expected_tree_sha256:
        raise RepositorySourceTreeError(
            "repository source tree does not match its acquisition attestation: "
            f"expected={expected_tree_sha256}, actual={observed_tree_sha256}"
        )
    return RepositorySourceTree(
        commit=commit,
        tree_sha256=observed_tree_sha256,
        git_commit_verified=git_commit_verified,
        file_sha256_by_path=file_sha256_by_path,
    )


def require_repository_source_tree_unchanged(
    repo_path: str | Path,
    source_tree: RepositorySourceTree,
) -> None:
    """Recheck the exact source just before its generation is sealed."""
    verified = verify_repository_source_tree(
        repo_path,
        source_tree.commit,
        source_tree.tree_sha256,
    )
    if verified.git_commit_verified != source_tree.git_commit_verified:
        raise RepositorySourceTreeError(
            "repository source verification mode changed during indexing"
        )
