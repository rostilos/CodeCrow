"""Immutable generation lifecycle, exact binding and cross-process ownership."""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import stat
import threading
import time
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from ..exact_index import ExactIndexPreconditionError

from .shared import (
    STRUCTURAL_STORE_SCHEMA,
    STRUCTURAL_STORE_SCHEMA_REVISION,
    _SHA256_RE,
    _PENDING_OWNERSHIP_FILE,
    _canonical_json,
)

from .schema import _SCHEMA_SQL, _FTS_SCHEMA_SQL, _BASE_SCHEMA_SQL, _FTS_CONTENT_SQL


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerationPaths:
    target: str
    digest: str
    directory: Path
    database: Path
    receipt: Path


class StructuralGenerationStore:
    """Own immutable per-generation SQLite databases and bounded reads."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.generations_root = self.root / "generations"
        self.pending_root = self.root / "pending"
        self.generations_root.mkdir(parents=True, exist_ok=True)
        self.pending_root.mkdir(parents=True, exist_ok=True)
        self._local_lock = threading.RLock()

    def paths_for_target(self, target: str) -> GenerationPaths:
        if not isinstance(target, str) or not target.strip():
            raise ValueError("structural generation target must be non-empty")
        digest = hashlib.sha256(target.encode("utf-8")).hexdigest()
        directory = self.generations_root / digest
        return GenerationPaths(
            target=target,
            digest=digest,
            directory=directory,
            database=directory / "graph.sqlite3",
            receipt=directory / "receipt.json",
        )

    def pending_paths(self, target: str) -> GenerationPaths:
        digest = hashlib.sha256(target.encode("utf-8")).hexdigest()
        directory = self.pending_root / f"{digest}-{uuid4().hex}"
        return GenerationPaths(
            target=target,
            digest=digest,
            directory=directory,
            database=directory / "graph.sqlite3",
            receipt=directory / "receipt.json",
        )

    @staticmethod
    def connect(database: Path, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            connection = sqlite3.connect(
                f"file:{database}?mode=ro",
                uri=True,
                timeout=30,
                check_same_thread=False,
            )
        else:
            database.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                database,
                timeout=30,
                check_same_thread=False,
            )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def initialize(self, paths: GenerationPaths, *, bulk_load: bool = False) -> sqlite3.Connection:
        paths.directory.mkdir(parents=True, exist_ok=False)
        connection = self.connect(paths.database)
        try:
            connection.executescript(_BASE_SCHEMA_SQL if bulk_load else _SCHEMA_SQL)
            connection.execute(
                f"PRAGMA user_version = {STRUCTURAL_STORE_SCHEMA_REVISION}"
            )
            try:
                connection.executescript(_FTS_CONTENT_SQL if bulk_load else _FTS_SCHEMA_SQL)
            except sqlite3.OperationalError as exception:
                logger.warning(
                    "SQLite FTS5 is unavailable; structural symbol search will use "
                    "indexed path/name fallback: %s",
                    exception,
                )
            connection.commit()
        except BaseException:
            connection.close()
            raise
        return connection

    def clone_bound_to_pending(
        self,
        *,
        source_target: str,
        target: str,
        workspace: str,
        project: str,
        branch: str,
        revision: str,
        manifest_sha256: str,
    ) -> tuple[GenerationPaths, sqlite3.Connection, dict[str, Any], BinaryIO]:
        """Clone one sealed exact generation into a private unsealed build.

        The source is verified through the same bound-reader contract used by
        queries. SQLite's online backup API copies a coherent database without
        linking writable pages back to the immutable source generation.
        """

        pending = self.pending_paths(target)
        backup_destination: sqlite3.Connection | None = None
        connection: sqlite3.Connection | None = None
        ownership: BinaryIO | None = None
        try:
            pending.directory.mkdir(parents=True, exist_ok=False)
            ownership = self.acquire_pending_ownership(pending)
            # Back up into SQLite's default rollback-journal mode. Opening the
            # empty destination through connect() would enable WAL first and
            # stage every copied base page in a source-sized WAL before the
            # final checkpoint. The pending database is private until publish,
            # so enable WAL only after the coherent backup has completed.
            backup_destination = sqlite3.connect(
                pending.database,
                timeout=30,
                check_same_thread=False,
            )
            backup_destination.execute("PRAGMA busy_timeout=5000")
            with self.open_bound(
                target=source_target,
                workspace=workspace,
                project=project,
                branch=branch,
                revision=revision,
                manifest_sha256=manifest_sha256,
            ) as (source, receipt):
                source.backup(backup_destination)
                base_receipt = dict(receipt)
            backup_destination.close()
            backup_destination = None
            connection = self.connect(pending.database)
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("DELETE FROM generation")
            connection.commit()
            return pending, connection, base_receipt, ownership
        except BaseException:
            if backup_destination is not None:
                backup_destination.close()
            if connection is not None:
                connection.close()
            self.release_pending_ownership(ownership)
            self.remove_pending(pending)
            raise

    def acquire_pending_ownership(self, paths: GenerationPaths) -> BinaryIO:
        """Hold a process-scoped lock while one pending generation is active."""
        if paths.directory.parent != self.pending_root:
            raise ValueError("pending ownership requires a pending structural directory")
        descriptor = os.open(
            paths.directory / _PENDING_OWNERSHIP_FILE,
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        ownership = os.fdopen(descriptor, "a+b")
        try:
            fcntl.flock(ownership.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            ownership.close()
            raise
        return ownership

    @staticmethod
    def release_pending_ownership(ownership: BinaryIO | None) -> None:
        if ownership is None:
            return
        try:
            fcntl.flock(ownership.fileno(), fcntl.LOCK_UN)
        finally:
            ownership.close()

    @staticmethod
    def _acquire_sealed_access(
        paths: GenerationPaths,
        *,
        exclusive: bool,
        nonblocking: bool = False,
    ) -> BinaryIO:
        """Lock one sealed generation across readers and lifecycle deletion.

        The immutable receipt is present in every published generation, so its
        inode is a stable cross-process lock target without adding mutable
        state to the sealed directory.  A janitor takes the lock without
        waiting and therefore retains a generation whenever any reader is
        active.
        """

        descriptor = os.open(
            paths.receipt,
            os.O_RDONLY | os.O_NOFOLLOW,
        )
        access = os.fdopen(descriptor, "rb")
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if nonblocking:
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(access.fileno(), operation)
        except BaseException:
            access.close()
            raise
        return access

    def publish(self, pending: GenerationPaths) -> GenerationPaths:
        final = self.paths_for_target(pending.target)
        with self._local_lock:
            if final.directory.exists():
                raise FileExistsError(final.directory)
            os.replace(pending.directory, final.directory)
        return final

    def remove_pending(self, paths: GenerationPaths) -> None:
        if paths.directory.parent != self.pending_root:
            raise ValueError("refusing to remove a non-pending structural directory")
        shutil.rmtree(paths.directory, ignore_errors=True)

    def delete(
        self,
        target: str,
        *,
        workspace: str,
        project: str,
        branch: str,
        revision: str,
        manifest_sha256: str,
    ) -> bool:
        paths = self.paths_for_target(target)
        try:
            access = self._acquire_sealed_access(
                paths,
                exclusive=True,
                nonblocking=True,
            )
        except (BlockingIOError, FileNotFoundError):
            return False
        try:
            receipt = self.read_receipt(target)
            if receipt is None:
                return False
            expected = {
                "workspace": workspace,
                "project": project,
                "branch": branch,
                "repository_revision": revision,
                "generation_manifest_sha256": manifest_sha256,
                "collection_target": target,
            }
            if any(
                receipt.get(key) != value
                for key, value in expected.items()
            ):
                raise ExactIndexPreconditionError(
                    "structural generation does not match its registry receipt"
                )
            with self._local_lock:
                if not paths.directory.exists():
                    return False
                shutil.rmtree(paths.directory)
            return True
        finally:
            self.release_pending_ownership(access)

    def cleanup_pending(self, max_age_seconds: int = 24 * 60 * 60) -> int:
        cutoff = time.time() - max(0, max_age_seconds)
        removed = 0
        for candidate in self.pending_root.iterdir():
            ownership: BinaryIO | None = None
            try:
                if not candidate.is_dir() or candidate.stat().st_mtime > cutoff:
                    continue
                descriptor = os.open(
                    candidate / _PENDING_OWNERSHIP_FILE,
                    os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                    0o600,
                )
                ownership = os.fdopen(descriptor, "a+b")
                try:
                    fcntl.flock(
                        ownership.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                except BlockingIOError:
                    ownership.close()
                    ownership = None
                    continue
                # Recheck age after acquiring ownership so a newly reused path
                # cannot be removed based on a stale pre-lock observation.
                if candidate.stat().st_mtime > cutoff:
                    continue
                shutil.rmtree(candidate)
                removed += 1
            except FileNotFoundError:
                continue
            except OSError:
                logger.warning("Could not remove stale structural build %s", candidate)
            finally:
                self.release_pending_ownership(ownership)
        return removed

    @staticmethod
    def _is_proposed_tree_receipt(receipt: Mapping[str, Any]) -> bool:
        metadata = receipt.get("snapshot_metadata")
        return (
            isinstance(metadata, Mapping)
            and metadata.get("kind") == "proposed_tree"
        )

    def expired_review_generation_receipt(
        self,
        target: str,
        *,
        max_age_seconds: int,
    ) -> dict[str, Any] | None:
        """Return one still-expired request-scoped generation, if any."""
        paths = self.paths_for_target(target)
        cutoff = time.time() - max(0, max_age_seconds)
        try:
            if paths.directory.stat().st_mtime > cutoff:
                return None
        except FileNotFoundError:
            return None
        receipt = self.read_receipt(target)
        if receipt is None or not self._is_proposed_tree_receipt(receipt):
            return None
        # Recheck after the receipt read. Opening a proposed-tree generation
        # touches its directory, so a concurrent reader cancels expiration.
        try:
            if paths.directory.stat().st_mtime > cutoff:
                return None
        except FileNotFoundError:
            return None
        return receipt

    def expired_review_generation_receipts(
        self,
        *,
        max_age_seconds: int,
    ) -> list[dict[str, Any]]:
        """List only sealed proposed-tree generations past their access TTL."""
        candidates: list[dict[str, Any]] = []
        for directory in self.generations_root.iterdir():
            try:
                directory_stat = directory.lstat()
                if not stat.S_ISDIR(directory_stat.st_mode):
                    continue
                raw_receipt = json.loads(
                    (directory / "receipt.json").read_text(encoding="utf-8")
                )
                target = raw_receipt.get("collection_target")
                if (
                    not isinstance(target, str)
                    or self.paths_for_target(target).directory != directory
                ):
                    continue
                receipt = self.expired_review_generation_receipt(
                    target,
                    max_age_seconds=max_age_seconds,
                )
                if receipt is not None:
                    candidates.append(receipt)
            except (FileNotFoundError, OSError, TypeError, ValueError):
                continue
        return sorted(
            candidates,
            key=lambda receipt: str(receipt.get("collection_target") or ""),
        )

    def delete_expired_review_generation(
        self,
        target: str,
        *,
        max_age_seconds: int,
        workspace: str,
        project: str,
        branch: str,
        revision: str,
        manifest_sha256: str,
    ) -> bool:
        """Delete one expired proposed-tree generation if it has no readers."""

        paths = self.paths_for_target(target)
        try:
            access = self._acquire_sealed_access(
                paths,
                exclusive=True,
                nonblocking=True,
            )
        except (BlockingIOError, FileNotFoundError):
            return False
        try:
            # Candidate discovery is advisory.  Recheck kind, age, and exact
            # binding only after owning the deletion lock so a recent/active
            # generation can never be removed from stale scan data.
            receipt = self.expired_review_generation_receipt(
                target,
                max_age_seconds=max_age_seconds,
            )
            if receipt is None:
                return False
            expected = {
                "workspace": workspace,
                "project": project,
                "branch": branch,
                "repository_revision": revision,
                "generation_manifest_sha256": manifest_sha256,
                "collection_target": target,
            }
            if any(
                receipt.get(key) != value
                for key, value in expected.items()
            ):
                raise ExactIndexPreconditionError(
                    "expired proposed-tree generation changed after discovery"
                )
            with self._local_lock:
                if not paths.directory.exists():
                    return False
                shutil.rmtree(paths.directory)
            return True
        finally:
            self.release_pending_ownership(access)

    @staticmethod
    def _touch_review_generation(
        paths: GenerationPaths,
        receipt: Mapping[str, Any],
    ) -> None:
        if not StructuralGenerationStore._is_proposed_tree_receipt(receipt):
            return
        try:
            os.utime(paths.directory)
        except FileNotFoundError:
            return
        except OSError as exception:
            logger.debug(
                "Could not update proposed-tree generation access time %s: %s",
                paths.target,
                exception,
            )

    def read_receipt(self, target: str) -> dict[str, Any] | None:
        paths = self.paths_for_target(target)
        if not paths.receipt.is_file() or not paths.database.is_file():
            return None
        try:
            receipt = json.loads(paths.receipt.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if (
            not isinstance(receipt, dict)
            or receipt.get("collection_target") != target
            or receipt.get("store_schema") != STRUCTURAL_STORE_SCHEMA
            or receipt.get("store_schema_revision")
            != STRUCTURAL_STORE_SCHEMA_REVISION
            or not _SHA256_RE.fullmatch(
                str(receipt.get("generation_manifest_sha256", ""))
            )
        ):
            return None
        return receipt

    def repository_generation_receipts(
        self,
        *,
        workspace: str,
        project: str,
        branch: str,
        revision: str | None = None,
    ) -> list[dict[str, Any]]:
        """Discover sealed base generations within one tenant/repository branch.

        Proposed-tree review overlays are request-scoped derived artifacts and
        must never be adopted as reusable target-branch indexes.
        """

        matches: list[dict[str, Any]] = []
        for directory in self.generations_root.iterdir():
            try:
                directory_stat = directory.lstat()
                if not stat.S_ISDIR(directory_stat.st_mode):
                    continue
                raw_receipt = json.loads(
                    (directory / "receipt.json").read_text(encoding="utf-8")
                )
                target = raw_receipt.get("collection_target")
                if (
                    not isinstance(target, str)
                    or self.paths_for_target(target).directory != directory
                ):
                    continue
                receipt = self.read_receipt(target)
                if receipt is None:
                    continue
                snapshot_metadata = receipt.get("snapshot_metadata")
                if (
                    isinstance(snapshot_metadata, Mapping)
                    and snapshot_metadata.get("kind") == "proposed_tree"
                ):
                    continue
                if any(
                    receipt.get(key) != value
                    for key, value in (
                        ("workspace", workspace),
                        ("project", project),
                        ("branch", branch),
                    )
                ):
                    continue
                if (
                    revision is not None
                    and receipt.get("repository_revision") != revision
                ):
                    continue
                matches.append(receipt)
            except (FileNotFoundError, OSError, TypeError, ValueError):
                continue
        return sorted(
            matches,
            key=lambda receipt: (
                str(receipt.get("repository_revision") or ""),
                str(receipt.get("collection_target") or ""),
            ),
        )

    @contextmanager
    def open_bound(
        self,
        *,
        target: str,
        workspace: str,
        project: str,
        branch: str,
        revision: str,
        manifest_sha256: str,
    ):
        paths = self.paths_for_target(target)
        try:
            access = self._acquire_sealed_access(
                paths,
                exclusive=False,
            )
        except FileNotFoundError as exception:
            raise ExactIndexPreconditionError(
                "requested structural repository generation is unavailable"
            ) from exception
        try:
            receipt = self.read_receipt(target)
            if receipt is None:
                raise ExactIndexPreconditionError(
                    "requested structural repository generation is unavailable"
                )
            expected = {
                "workspace": workspace,
                "project": project,
                "branch": branch,
                "repository_revision": revision,
                "generation_manifest_sha256": manifest_sha256,
                "collection_target": target,
            }
            if any(
                receipt.get(key) != value
                for key, value in expected.items()
            ):
                raise ExactIndexPreconditionError(
                    "requested structural repository generation changed or does not "
                    "match its receipt"
                )
            self._touch_review_generation(paths, receipt)
            try:
                connection = self.connect(paths.database, read_only=True)
            except sqlite3.OperationalError as exception:
                raise ExactIndexPreconditionError(
                    "requested structural repository generation is unavailable"
                ) from exception
            try:
                database_schema_revision = connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                if database_schema_revision != STRUCTURAL_STORE_SCHEMA_REVISION:
                    raise ExactIndexPreconditionError(
                        "requested structural repository generation uses an "
                        "incompatible storage schema"
                    )
                sealed = connection.execute(
                    "SELECT receipt_json FROM generation WHERE singleton = 1"
                ).fetchone()
                if sealed is None:
                    raise ExactIndexPreconditionError(
                        "requested structural repository generation is not sealed"
                    )
                try:
                    sealed_receipt = json.loads(sealed["receipt_json"])
                except (TypeError, ValueError) as exception:
                    raise ExactIndexPreconditionError(
                        "requested structural repository generation has an invalid seal"
                    ) from exception
                if _canonical_json(sealed_receipt) != _canonical_json(receipt):
                    raise ExactIndexPreconditionError(
                        "requested structural repository generation receipt does not "
                        "match its database seal"
                    )
                yield connection, receipt
            finally:
                connection.close()
                self._touch_review_generation(paths, receipt)
        finally:
            self.release_pending_ownership(access)
