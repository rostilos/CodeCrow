"""SQLite-backed immutable structural repository generations.

The node/edge storage and bounded directional-query model are adapted from
``code-review-graph`` 2.3.8 (MIT, Copyright (c) 2026 Tirth Kanani). CodeCrow
keeps its own schema because its neutral plugin facts, immutable target-head
generation receipts, and tenant binding are not represented by the upstream
local-development database.

This module intentionally contains no embeddings and no vector-store adapter.
Source units are produced by CodeCrow's existing Tree-sitter/plugin pipeline;
SQLite stores their exact structure, logical relations, and source bodies.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable
from uuid import uuid4

from .documents import TextNode
from .exact_index import ExactIndexPreconditionError
from .generation_manifest import (
    GENERATION_SCHEMA,
    canonical_index_selection_policy,
    compute_index_selection_policy_sha256,
)
from ..utils.path_identity import normalize_repository_path


logger = logging.getLogger(__name__)

STRUCTURAL_STORE_SCHEMA = "codecrow.structural-graph"
STRUCTURAL_STORE_SCHEMA_REVISION = 7
_ZERO_FINGERPRINT = "sha256:" + "0" * 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_:$\\.-]*")
_MAX_PRELOADED_ANCHOR_UNITS = 160
_BULK_RELATION_RESOLUTION_THRESHOLD = 256
_PENDING_OWNERSHIP_FILE = ".build.lock"
_ENDPOINT_RELATION_KINDS = frozenset({
    "DJANGO_VIEW_ACTION",
    "EXPRESS_ROUTE",
    "FASTAPI_ROUTE",
    "NEXTJS_ROUTE_HANDLER",
    "QUARKUS_JAXRS_ROUTE",
    "RAILS_ROUTE",
    "SPRING_ROUTE",
})


@contextmanager
def _interrupt_sql_on_cancel(
    connection: sqlite3.Connection,
    cancellation_check: Callable[[], None] | None,
):
    """Interrupt a long SQLite statement when its owning build is cancelled."""

    if cancellation_check is None:
        yield
        return

    cancellation_check()

    def interrupted() -> int:
        try:
            cancellation_check()
        except BaseException:
            return 1
        return 0

    connection.set_progress_handler(interrupted, 10_000)
    try:
        yield
    except sqlite3.OperationalError:
        # SQLite converts a non-zero progress callback into ``interrupted``.
        # Re-run the owner check so callers receive their domain cancellation
        # exception rather than a misleading database failure.
        cancellation_check()
        raise
    finally:
        connection.set_progress_handler(None, 0)
    cancellation_check()


_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS generation (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    receipt_json TEXT NOT NULL,
    sealed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS units (
    unit_id TEXT PRIMARY KEY,
    record_type TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT,
    kind TEXT,
    name TEXT,
    qualified_name TEXT,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_units_path ON units(path, start_line, end_line);
CREATE INDEX IF NOT EXISTS idx_units_name ON units(name);
CREATE INDEX IF NOT EXISTS idx_units_qualified ON units(qualified_name);
CREATE INDEX IF NOT EXISTS idx_units_record_type ON units(record_type);

CREATE TABLE IF NOT EXISTS unit_names (
    normalized_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    unit_id TEXT NOT NULL REFERENCES units(unit_id) ON DELETE CASCADE,
    PRIMARY KEY (normalized_name, unit_id)
);

CREATE INDEX IF NOT EXISTS idx_unit_names_unit ON unit_names(unit_id);

CREATE TABLE IF NOT EXISTS relations (
    relation_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    relation TEXT NOT NULL,
    target TEXT NOT NULL,
    source_unit_id TEXT REFERENCES units(unit_id) ON DELETE SET NULL,
    target_unit_id TEXT REFERENCES units(unit_id) ON DELETE SET NULL,
    path TEXT NOT NULL,
    line INTEGER NOT NULL,
    origin TEXT NOT NULL,
    plugin_id TEXT,
    attributes_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source);
CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target);
CREATE INDEX IF NOT EXISTS idx_relations_source_unit ON relations(source_unit_id);
CREATE INDEX IF NOT EXISTS idx_relations_target_unit ON relations(target_unit_id);
CREATE INDEX IF NOT EXISTS idx_relations_path ON relations(path, line);
CREATE INDEX IF NOT EXISTS idx_relations_kind ON relations(kind);

-- Derived endpoint-name index used by cloned-generation invalidation. Keeping
-- normalized names beside the relation avoids invoking a Python normalization
-- callback over the complete graph for every changed symbol name.
CREATE TABLE IF NOT EXISTS relation_names (
    relation_id TEXT NOT NULL REFERENCES relations(relation_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('source', 'target')),
    normalized_name TEXT NOT NULL,
    PRIMARY KEY (relation_id, role)
);

CREATE INDEX IF NOT EXISTS idx_relation_names_lookup
ON relation_names(normalized_name, role, relation_id);

CREATE TABLE IF NOT EXISTS relation_manifest_cache (
    relation_id TEXT PRIMARY KEY
        REFERENCES relations(relation_id) ON DELETE CASCADE,
    member_json TEXT NOT NULL
);

DROP TRIGGER IF EXISTS invalidate_relation_manifest_after_update;
CREATE TRIGGER invalidate_relation_manifest_after_update
AFTER UPDATE OF kind, source, relation, target, path, line, origin, attributes_json
ON relations
WHEN OLD.kind IS NOT NEW.kind
  OR OLD.source IS NOT NEW.source
  OR OLD.relation IS NOT NEW.relation
  OR OLD.target IS NOT NEW.target
  OR OLD.path IS NOT NEW.path
  OR OLD.line IS NOT NEW.line
  OR OLD.origin IS NOT NEW.origin
  OR OLD.attributes_json IS NOT NEW.attributes_json
BEGIN
    DELETE FROM relation_manifest_cache
    WHERE relation_id = NEW.relation_id;
END;

CREATE TABLE IF NOT EXISTS relation_plugins (
    relation_id TEXT NOT NULL REFERENCES relations(relation_id) ON DELETE CASCADE,
    plugin_id TEXT NOT NULL,
    PRIMARY KEY (relation_id, plugin_id)
);

CREATE INDEX IF NOT EXISTS idx_relation_plugins_plugin
ON relation_plugins(plugin_id, relation_id);

-- A logical relation can be emitted by both per-file parsing and repository
-- finalization. Keep stage ownership separate from the canonical contributor
-- union so repository reconciliation cannot remove an unchanged file owner.
CREATE TABLE IF NOT EXISTS relation_scopes (
    relation_id TEXT NOT NULL REFERENCES relations(relation_id) ON DELETE CASCADE,
    scope TEXT NOT NULL CHECK (scope IN ('file', 'repository')),
    PRIMARY KEY (relation_id, scope)
);

CREATE INDEX IF NOT EXISTS idx_relation_scopes_scope
ON relation_scopes(scope, relation_id);

CREATE TABLE IF NOT EXISTS relation_plugin_scopes (
    relation_id TEXT NOT NULL REFERENCES relations(relation_id) ON DELETE CASCADE,
    plugin_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('file', 'repository')),
    PRIMARY KEY (relation_id, plugin_id, scope)
);

CREATE INDEX IF NOT EXISTS idx_relation_plugin_scopes_scope
ON relation_plugin_scopes(scope, relation_id, plugin_id);

CREATE TRIGGER IF NOT EXISTS invalidate_relation_manifest_after_plugin_insert
AFTER INSERT ON relation_plugins
BEGIN
    DELETE FROM relation_manifest_cache
    WHERE relation_id = NEW.relation_id;
END;

CREATE TRIGGER IF NOT EXISTS invalidate_relation_manifest_after_plugin_delete
AFTER DELETE ON relation_plugins
BEGIN
    DELETE FROM relation_manifest_cache
    WHERE relation_id = OLD.relation_id;
END;

CREATE TABLE IF NOT EXISTS relation_paths (
    relation_id TEXT NOT NULL REFERENCES relations(relation_id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    PRIMARY KEY (relation_id, path)
);

CREATE INDEX IF NOT EXISTS idx_relation_paths_path ON relation_paths(path);

CREATE TABLE IF NOT EXISTS repository_snapshots (
    plugin_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    PRIMARY KEY (plugin_id, kind)
);
"""

_FTS_SCHEMA_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS units_fts USING fts5(
    unit_id UNINDEXED,
    path,
    name,
    qualified_name,
    symbols,
    content,
    tokenize = 'unicode61 remove_diacritics 2 tokenchars ''_:$\\.-'''
);
"""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_path(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    raw_candidate = PurePosixPath(raw)
    if (
        not raw
        or raw_candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in raw_candidate.parts)
    ):
        raise ValueError(f"invalid repository-relative path: {value!r}")
    normalized = normalize_repository_path(raw)
    if not normalized:
        raise ValueError("repository path must be non-empty")
    candidate = PurePosixPath(normalized)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError(f"invalid repository-relative path: {value!r}")
    return candidate.as_posix()


def _optional_path(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _normalize_path(value)
    except ValueError:
        return None


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, (list, tuple, set)):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _first_string(metadata: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _unit_id(
    record_type: str,
    path: str,
    name: str,
    start_line: int,
    end_line: int,
    content_sha256: str,
) -> str:
    identity = "\0".join((
        record_type,
        path,
        name,
        str(start_line),
        str(end_line),
        content_sha256,
    ))
    return "unit:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _relation_id(projection: Mapping[str, Any]) -> str:
    return "relation:" + _sha256_text(_canonical_json(dict(projection)))


def _normalized_name(value: str) -> str:
    return value.strip().casefold()


def _short_name(value: str) -> str:
    parts = re.split(r"[\\/:.]+", value.strip())
    return next((part for part in reversed(parts) if part), value.strip())


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _fts_query(value: str) -> str:
    tokens = [token for token in _WORD_RE.findall(value) if token]
    return " OR ".join(
        f'"{token.replace(chr(34), chr(34) * 2)}"'
        for token in tokens[:12]
    )


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

    def initialize(self, paths: GenerationPaths) -> sqlite3.Connection:
        paths.directory.mkdir(parents=True, exist_ok=False)
        connection = self.connect(paths.database)
        connection.executescript(_SCHEMA_SQL)
        connection.execute(
            f"PRAGMA user_version = {STRUCTURAL_STORE_SCHEMA_REVISION}"
        )
        try:
            connection.executescript(_FTS_SCHEMA_SQL)
        except sqlite3.OperationalError as exception:
            logger.warning(
                "SQLite FTS5 is unavailable; structural symbol search will use "
                "indexed path/name fallback: %s",
                exception,
            )
        connection.commit()
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


class StructuralGraphWriter:
    """Write source units and neutral relations into one pending database."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        track_mutations: bool = False,
        defer_file_relation_ownership: bool = False,
    ):
        self.connection = connection
        self.unit_count = 0
        self.relation_count = 0
        self._file_unit_ids: dict[str, str] = {}
        self._track_mutations = track_mutations
        self._defer_file_relation_ownership = defer_file_relation_ownership
        self._touched_relation_ids: set[str] = set()
        self._touched_names: set[str] = set()
        self._explicit_source_relation_ids: set[str] = set()
        self._explicit_target_relation_ids: set[str] = set()
        self._capturing_repository_outputs = False
        self._repository_output_unit_ids: set[str] = set()
        self._repository_output_relation_plugins: dict[str, set[str]] = {}
        self._repository_output_snapshots: set[tuple[str, str]] = set()
        self.fts_available = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'units_fts'"
        ).fetchone() is not None

    def refresh_counts(self) -> None:
        """Refresh counters and file ownership after cloning a sealed graph."""

        self.unit_count = int(
            self.connection.execute("SELECT count(*) FROM units").fetchone()[0]
        )
        self.relation_count = int(
            self.connection.execute("SELECT count(*) FROM relations").fetchone()[0]
        )
        self._file_unit_ids = {
            str(row["path"]): str(row["unit_id"])
            for row in self.connection.execute(
                "SELECT path, unit_id FROM units WHERE record_type = 'structural_file'"
            )
        }

    def _remember_names_for_units(self, unit_table: str) -> None:
        if not self._track_mutations:
            return
        rows = self.connection.execute(
            "SELECT DISTINCT unit_names.normalized_name FROM unit_names "
            f"JOIN {unit_table} ON {unit_table}.unit_id = unit_names.unit_id"  # nosec B608
        ).fetchall()
        self._touched_names.update(str(row["normalized_name"]) for row in rows)

    def remove_paths(
        self,
        paths: Sequence[str],
        *,
        cancellation_check: Callable[[], None] | None = None,
    ) -> tuple[tuple[str, ...], frozenset[str]]:
        """Remove file-owned graph rows and return the exact reparse closure.

        Cross-file non-packet facts name every related path in ``relation_paths``.
        Their origin files join the closure so a changed dependency never leaves
        an unchanged but stale per-file fact in the cloned generation.
        """

        with _interrupt_sql_on_cancel(self.connection, cancellation_check):
            return self._remove_paths(paths)

    def _remove_paths(
        self,
        paths: Sequence[str],
    ) -> tuple[tuple[str, ...], frozenset[str]]:
        """Apply path removal while the public method owns cancellation setup."""

        normalized = tuple(sorted({_normalize_path(path) for path in paths if path}))
        if not normalized:
            return (), frozenset()
        self.connection.executescript(
            "CREATE TEMP TABLE IF NOT EXISTS delta_paths("
            "path TEXT PRIMARY KEY);"
            "DELETE FROM delta_paths;"
            "CREATE TEMP TABLE IF NOT EXISTS delta_units("
            "unit_id TEXT PRIMARY KEY, unit_rowid INTEGER UNIQUE);"
            "DELETE FROM delta_units;"
        )
        self.connection.executemany(
            "INSERT OR IGNORE INTO delta_paths(path) VALUES (?)",
            ((path,) for path in normalized),
        )
        # A provider may report a removed directory. Expand it to concrete
        # stored descendants before computing relation dependencies.
        self.connection.execute(
            "INSERT OR IGNORE INTO delta_paths(path) "
            "SELECT DISTINCT units.path FROM units JOIN delta_paths requested "
            "ON units.path = requested.path "
            "OR units.path LIKE requested.path || '/%'"
        )
        while True:
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO delta_paths(path) "
                "SELECT DISTINCT relations.path FROM relations "
                "JOIN relation_paths ON relation_paths.relation_id = relations.relation_id "
                "JOIN delta_paths changed ON changed.path = relation_paths.path "
                "WHERE json_type(relations.attributes_json, '$.packetKind') IS NULL"
            )
            if cursor.rowcount <= 0:
                break

        affected_paths = tuple(
            str(row["path"])
            for row in self.connection.execute(
                "SELECT path FROM delta_paths ORDER BY path"
            )
        )
        old_document_paths = frozenset(
            str(row["path"])
            for row in self.connection.execute(
                "SELECT DISTINCT units.path FROM units JOIN delta_paths "
                "ON delta_paths.path = units.path "
                "WHERE units.record_type = 'source_unit' OR ("
                "units.record_type = 'plugin_context' AND "
                "json_extract(units.metadata_json, '$.content_type') = "
                "'architecture-source')"
            )
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO delta_units(unit_id, unit_rowid) "
            "SELECT units.unit_id, units.rowid FROM units JOIN delta_paths "
            "ON delta_paths.path = units.path"
        )
        self._remember_names_for_units("delta_units")
        self._touched_relation_ids.update(
            str(row["relation_id"])
            for row in self.connection.execute(
                "SELECT relation_id FROM relations WHERE "
                "source_unit_id IN (SELECT unit_id FROM delta_units) OR "
                "target_unit_id IN (SELECT unit_id FROM delta_units)"
            )
        )
        self.connection.execute(
            "DELETE FROM relations WHERE path IN (SELECT path FROM delta_paths)"
        )
        if self.fts_available:
            self.connection.execute(
                "DELETE FROM units_fts WHERE rowid IN "
                "(SELECT unit_rowid FROM delta_units)"
            )
        self.connection.execute(
            "DELETE FROM units WHERE unit_id IN (SELECT unit_id FROM delta_units)"
        )
        for path in affected_paths:
            self._file_unit_ids.pop(path, None)
        return affected_paths, old_document_paths

    def remove_repository_analysis_outputs(self) -> None:
        """Remove repository-global plugin output while retaining file facts."""

        self.connection.executescript(
            "CREATE TEMP TABLE IF NOT EXISTS delta_global_units("
            "unit_id TEXT PRIMARY KEY, unit_rowid INTEGER UNIQUE);"
            "DELETE FROM delta_global_units;"
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO delta_global_units(unit_id, unit_rowid) "
            "SELECT unit_id, rowid FROM units WHERE "
            "record_type IN ('plugin_symbol', 'repository_state') OR ("
            "record_type = 'plugin_context' AND "
            "json_type(metadata_json, '$.architecture_plugin') IS NOT NULL)"
        )
        self._remember_names_for_units("delta_global_units")
        self._touched_relation_ids.update(
            str(row["relation_id"])
            for row in self.connection.execute(
                "SELECT relation_id FROM relations WHERE "
                "source_unit_id IN (SELECT unit_id FROM delta_global_units) OR "
                "target_unit_id IN (SELECT unit_id FROM delta_global_units)"
            )
        )
        self.connection.execute(
            "DELETE FROM relations WHERE "
            "json_type(attributes_json, '$.packetKind') IS NOT NULL OR ("
            "origin = 'plugin' AND ("
            "source_unit_id IN (SELECT unit_id FROM delta_global_units) OR "
            "target_unit_id IN (SELECT unit_id FROM delta_global_units)))"
        )
        if self.fts_available:
            self.connection.execute(
                "DELETE FROM units_fts WHERE rowid IN "
                "(SELECT unit_rowid FROM delta_global_units)"
            )
        self.connection.execute(
            "DELETE FROM units WHERE unit_id IN "
            "(SELECT unit_id FROM delta_global_units)"
        )
        self.connection.execute("DELETE FROM repository_snapshots")

    def remove_repository_state_output(self) -> None:
        """Remove the replaceable repository-state unit and its FTS document."""

        self.connection.executescript(
            "CREATE TEMP TABLE IF NOT EXISTS delta_repository_state("
            "unit_id TEXT PRIMARY KEY, unit_rowid INTEGER UNIQUE);"
            "DELETE FROM delta_repository_state;"
        )
        self.connection.execute(
            "INSERT INTO delta_repository_state(unit_id, unit_rowid) "
            "SELECT unit_id, rowid FROM units WHERE record_type = 'repository_state'"
        )
        self._remember_names_for_units("delta_repository_state")
        if self.fts_available:
            self.connection.execute(
                "DELETE FROM units_fts WHERE rowid IN "
                "(SELECT unit_rowid FROM delta_repository_state)"
            )
        self.connection.execute(
            "DELETE FROM units WHERE unit_id IN "
            "(SELECT unit_id FROM delta_repository_state)"
        )

    def begin_repository_analysis_reconciliation(self) -> None:
        """Capture a complete replacement set without deleting equal output."""

        if self._capturing_repository_outputs:
            raise RuntimeError("repository output reconciliation is already active")
        # Prepare reusable temporary tables with individual statements:
        # sqlite3.executescript() would commit the surrounding delta transaction.
        self.connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS keep_repository_units("
            "unit_id TEXT PRIMARY KEY)"
        )
        self.connection.execute("DELETE FROM keep_repository_units")
        self.connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS keep_repository_relations("
            "relation_id TEXT PRIMARY KEY)"
        )
        self.connection.execute("DELETE FROM keep_repository_relations")
        self.connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS keep_repository_relation_plugins("
            "relation_id TEXT NOT NULL, plugin_id TEXT NOT NULL, "
            "PRIMARY KEY(relation_id, plugin_id))"
        )
        self.connection.execute("DELETE FROM keep_repository_relation_plugins")
        self.connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS affected_repository_relations("
            "relation_id TEXT PRIMARY KEY)"
        )
        self.connection.execute("DELETE FROM affected_repository_relations")
        self.connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS keep_repository_snapshots("
            "plugin_id TEXT NOT NULL, kind TEXT NOT NULL, "
            "PRIMARY KEY(plugin_id, kind))"
        )
        self.connection.execute("DELETE FROM keep_repository_snapshots")
        self.connection.execute(
            "CREATE TEMP TABLE IF NOT EXISTS old_repository_units("
            "unit_id TEXT PRIMARY KEY, unit_rowid INTEGER UNIQUE)"
        )
        self.connection.execute("DELETE FROM old_repository_units")
        self.connection.execute("SAVEPOINT repository_analysis_reconcile")
        self._capturing_repository_outputs = True
        self._repository_output_unit_ids.clear()
        self._repository_output_relation_plugins.clear()
        self._repository_output_snapshots.clear()

    def abort_repository_analysis_reconciliation(self) -> None:
        if self._capturing_repository_outputs:
            self.connection.execute("ROLLBACK TO repository_analysis_reconcile")
            self.connection.execute("RELEASE repository_analysis_reconcile")
        self._capturing_repository_outputs = False
        self._repository_output_unit_ids.clear()
        self._repository_output_relation_plugins.clear()
        self._repository_output_snapshots.clear()

    def reconcile_repository_analysis_outputs(self) -> None:
        """Delete only repository-global output absent from the new analysis."""

        if not self._capturing_repository_outputs:
            raise RuntimeError("repository output reconciliation is not active")
        self.connection.executemany(
            "INSERT INTO keep_repository_units(unit_id) VALUES (?)",
            ((unit_id,) for unit_id in sorted(self._repository_output_unit_ids)),
        )
        self.connection.executemany(
            "INSERT INTO keep_repository_relations(relation_id) VALUES (?)",
            (
                (relation_id,)
                for relation_id in sorted(
                    self._repository_output_relation_plugins
                )
            ),
        )
        self.connection.executemany(
            "INSERT INTO keep_repository_relation_plugins("
            "relation_id, plugin_id) VALUES (?, ?)",
            (
                (relation_id, plugin_id)
                for relation_id, plugin_ids in sorted(
                    self._repository_output_relation_plugins.items()
                )
                for plugin_id in sorted(plugin_ids)
            ),
        )
        self.connection.executemany(
            "INSERT INTO keep_repository_snapshots(plugin_id, kind) VALUES (?, ?)",
            sorted(self._repository_output_snapshots),
        )
        self.connection.execute(
            "INSERT INTO old_repository_units(unit_id, unit_rowid) "
            "SELECT unit_id, rowid FROM units WHERE "
            "record_type = 'plugin_symbol' OR ("
            "record_type = 'plugin_context' AND "
            "json_type(metadata_json, '$.architecture_plugin') IS NOT NULL)"
        )

        self.connection.execute(
            "INSERT OR IGNORE INTO affected_repository_relations(relation_id) "
            "SELECT relation_id FROM relation_scopes WHERE scope = 'repository'"
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO affected_repository_relations(relation_id) "
            "SELECT relation_id FROM keep_repository_relations"
        )
        self.connection.execute(
            "DELETE FROM relation_plugin_scopes WHERE scope = 'repository' "
            "AND NOT EXISTS (SELECT 1 FROM keep_repository_relation_plugins keep "
            "WHERE keep.relation_id = relation_plugin_scopes.relation_id "
            "AND keep.plugin_id = relation_plugin_scopes.plugin_id)"
        )
        self.connection.execute(
            "DELETE FROM relation_scopes WHERE scope = 'repository' "
            "AND relation_id NOT IN (SELECT relation_id FROM keep_repository_relations)"
        )
        self.connection.execute(
            "DELETE FROM relations WHERE relation_id IN "
            "(SELECT relation_id FROM affected_repository_relations) "
            "AND NOT EXISTS (SELECT 1 FROM relation_scopes ownership "
            "WHERE ownership.relation_id = relations.relation_id)"
        )

        # Synchronize the canonical contributor union only when ownership
        # changes. Equal rows retain their manifest-cache entry.
        current_plugins: dict[str, set[str]] = {}
        for row in self.connection.execute(
            "SELECT relation_plugins.relation_id, relation_plugins.plugin_id "
            "FROM relation_plugins JOIN affected_repository_relations "
            "USING (relation_id)"
        ):
            current_plugins.setdefault(str(row["relation_id"]), set()).add(
                str(row["plugin_id"])
            )
        desired_plugins: dict[str, set[str]] = {}
        for row in self.connection.execute(
            "SELECT relation_plugin_scopes.relation_id, "
            "relation_plugin_scopes.plugin_id FROM relation_plugin_scopes "
            "JOIN affected_repository_relations USING (relation_id)"
        ):
            desired_plugins.setdefault(str(row["relation_id"]), set()).add(
                str(row["plugin_id"])
            )
        affected_relation_ids = {
            str(row["relation_id"])
            for row in self.connection.execute(
                "SELECT relation_id FROM affected_repository_relations"
            )
        }
        for relation_id in sorted(affected_relation_ids):
            desired = desired_plugins.get(relation_id, set())
            if current_plugins.get(relation_id, set()) == desired:
                continue
            self.connection.execute(
                "DELETE FROM relation_plugins WHERE relation_id = ?",
                (relation_id,),
            )
            self.connection.executemany(
                "INSERT INTO relation_plugins(relation_id, plugin_id) VALUES (?, ?)",
                ((relation_id, plugin_id) for plugin_id in sorted(desired)),
            )
            self.connection.execute(
                "UPDATE relations SET plugin_id = ? WHERE relation_id = ?",
                (
                    next(iter(desired)) if len(desired) == 1 else None,
                    relation_id,
                ),
            )

        if self.fts_available:
            self.connection.execute(
                "DELETE FROM units_fts WHERE rowid IN ("
                "SELECT unit_rowid FROM old_repository_units WHERE unit_id NOT IN "
                "(SELECT unit_id FROM keep_repository_units))"
            )
        self.connection.execute(
            "DELETE FROM units WHERE unit_id IN (SELECT unit_id FROM old_repository_units) "
            "AND unit_id NOT IN (SELECT unit_id FROM keep_repository_units)"
        )
        self.connection.execute(
            "DELETE FROM repository_snapshots WHERE NOT EXISTS ("
            "SELECT 1 FROM keep_repository_snapshots keep WHERE "
            "keep.plugin_id = repository_snapshots.plugin_id AND "
            "keep.kind = repository_snapshots.kind)"
        )
        self.refresh_counts()
        self.connection.execute("RELEASE repository_analysis_reconcile")
        self._capturing_repository_outputs = False
        self._repository_output_unit_ids.clear()
        self._repository_output_relation_plugins.clear()
        self._repository_output_snapshots.clear()

    def add_unit(
        self,
        node: TextNode,
        *,
        record_type: str = "source_unit",
        metadata_override: Mapping[str, Any] | None = None,
    ) -> str:
        metadata = dict(node.metadata)
        if metadata_override:
            metadata.update(metadata_override)
        path = _normalize_path(str(metadata.get("path") or ""))
        content = str(node.text or "")
        start_line = max(1, int(metadata.get("start_line") or 1))
        end_line = max(start_line, int(metadata.get("end_line") or start_line))
        name = _first_string(
            metadata,
            "primary_name",
            "symbol_qualified_name",
            "full_path",
            "parent_class",
        ) or Path(path).name
        qualified_name = _first_string(
            metadata,
            "symbol_qualified_name",
            "full_path",
        ) or f"{path}:{start_line}-{end_line}:{name}"
        kind = _first_string(
            metadata,
            "symbol_kind",
            "node_type",
            "content_type",
        ) or record_type
        language = str(metadata.get("language") or "")
        content_sha256 = _sha256_text(content)
        unit_id = _unit_id(
            record_type,
            path,
            qualified_name,
            start_line,
            end_line,
            content_sha256,
        )
        encoded_metadata = _canonical_json(metadata)
        capture_repository_unit = self._capturing_repository_outputs and (
            record_type == "plugin_symbol"
            or (
                record_type == "plugin_context"
                and metadata.get("architecture_plugin") is not None
            )
        )
        if capture_repository_unit:
            self._repository_output_unit_ids.add(unit_id)
        stored_values = (
            record_type,
            path,
            language,
            kind,
            name,
            qualified_name,
            start_line,
            end_line,
            content,
            content_sha256,
            encoded_metadata,
        )
        unit_row = self.connection.execute(
            """INSERT INTO units(
                   unit_id, record_type, path, language, kind, name,
                   qualified_name, start_line, end_line, content,
                   content_sha256, metadata_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(unit_id) DO NOTHING
               RETURNING rowid""",
            (unit_id, *stored_values),
        ).fetchone()
        if unit_row is not None:
            inserted = True
            unit_rowid = int(unit_row["rowid"])
        else:
            inserted = False
            existing_unit = self.connection.execute(
                "SELECT rowid, record_type, path, language, kind, name, "
                "qualified_name, start_line, end_line, content, content_sha256, "
                "metadata_json FROM units WHERE unit_id = ?",
                (unit_id,),
            ).fetchone()
            if existing_unit is None:
                raise RuntimeError(
                    "structural unit insert conflicted but its row is unavailable"
                )
            if tuple(
                existing_unit[key]
                for key in (
                    "record_type",
                    "path",
                    "language",
                    "kind",
                    "name",
                    "qualified_name",
                    "start_line",
                    "end_line",
                    "content",
                    "content_sha256",
                    "metadata_json",
                )
            ) == stored_values:
                # Repository finalizers emit a complete deterministic snapshot.
                # A cloned generation already contains most of those rows; avoid
                # rewriting their names and FTS documents into the WAL.
                return unit_id
            unit_row = self.connection.execute(
                """UPDATE units SET
                       record_type = ?, path = ?, language = ?, kind = ?, name = ?,
                       qualified_name = ?, start_line = ?, end_line = ?, content = ?,
                       content_sha256 = ?, metadata_json = ?
                   WHERE unit_id = ?
                   RETURNING rowid""",
                (*stored_values, unit_id),
            ).fetchone()
            if unit_row is None:
                raise RuntimeError("structural unit update did not return its rowid")
            unit_rowid = int(unit_row["rowid"])
            self.connection.execute(
                "DELETE FROM unit_names WHERE unit_id = ?",
                (unit_id,),
            )
        names = {
            name,
            qualified_name,
            _short_name(name),
            _short_name(qualified_name),
            *_string_list(metadata.get("symbol_names")),
            *_string_list(metadata.get("architecture_identifiers")),
        }
        namespace = str(metadata.get("namespace") or "").strip(" \\")
        parent_class = str(metadata.get("parent_class") or "").strip()
        if namespace and parent_class and name:
            qualified_owner = f"{namespace}\\{parent_class}"
            names.update({
                qualified_owner,
                f"{qualified_owner}::{name}",
                f"{qualified_owner}.{name}",
            })
        elif namespace and name:
            names.add(f"{namespace}\\{name}")
        if self._track_mutations:
            self._touched_names.update(
                _normalized_name(value)
                for value in names
                if value and value.strip()
            )
        name_rows = tuple(
            (_normalized_name(value), value, unit_id)
            for value in sorted(
                item.strip() for item in names if item and item.strip()
            )
        )
        self.connection.executemany(
            "INSERT OR IGNORE INTO unit_names(normalized_name, display_name, unit_id) "
            "VALUES (?, ?, ?)",
            name_rows,
        )
        if self.fts_available:
            # unit_id is intentionally UNINDEXED in FTS5. Sharing the stable
            # units rowid makes both first writes and duplicate replacement
            # constant-time without a full virtual-table delete scan.
            self.connection.execute(
                "INSERT OR REPLACE INTO units_fts("
                "rowid, unit_id, path, name, qualified_name, symbols, content) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    unit_rowid,
                    unit_id,
                    path,
                    name,
                    qualified_name,
                    " ".join(sorted(names)),
                    content,
                ),
            )
        if inserted:
            self.unit_count += 1
        return unit_id

    def add_file(self, node: TextNode) -> str:
        """Add the repository file that owns source/plugin structural units."""
        metadata = dict(node.metadata)
        path = _normalize_path(str(metadata.get("path") or ""))
        file_unit_id = self.add_unit(
            TextNode(
                text=f"Repository file {path}",
                metadata={
                    **metadata,
                    "path": path,
                    "start_line": 1,
                    "end_line": max(1, str(node.text or "").count("\n") + 1),
                    "primary_name": Path(path).name,
                    "symbol_qualified_name": path,
                    "symbol_kind": "file",
                },
            ),
            record_type="structural_file",
        )
        self._file_unit_ids[path] = file_unit_id
        return file_unit_id

    def add_file_containment(
        self,
        file_unit_id: str,
        child_unit_id: str,
        node: TextNode,
    ) -> str:
        """Connect a repository file to one of its concrete structural units."""
        metadata = dict(node.metadata)
        path = _normalize_path(str(metadata.get("path") or ""))
        child_name = _first_string(
            metadata,
            "symbol_qualified_name",
            "full_path",
            "primary_name",
        ) or f"{path}:{max(1, int(metadata.get('start_line') or 1))}"
        return self.add_relation(
            kind="CONTAINS",
            source=path,
            relation="contains",
            target=child_name,
            path=path,
            line=max(1, int(metadata.get("start_line") or 1)),
            origin="structural-index",
            source_unit_id=file_unit_id,
            target_unit_id=child_unit_id,
            identity_discriminator=child_unit_id,
        )

    def add_ast_relations(self, unit_id: str, node: TextNode) -> None:
        metadata = dict(node.metadata)
        path = _normalize_path(str(metadata.get("path") or ""))
        source = _first_string(
            metadata,
            "symbol_qualified_name",
            "full_path",
            "primary_name",
        ) or Path(path).name
        line = max(1, int(metadata.get("start_line") or 1))
        mappings = (
            ("IMPORTS", "imports"),
            ("EXTENDS", "extends"),
            ("IMPLEMENTS", "implements"),
            ("CALLS", "calls"),
            ("REFERENCES", "referenced_types"),
        )
        for kind, field in mappings:
            for target in sorted(set(_string_list(metadata.get(field)))):
                self.add_relation(
                    kind=kind,
                    source=source,
                    relation=kind.lower(),
                    target=target,
                    path=path,
                    line=line,
                    origin="tree-sitter",
                    source_unit_id=unit_id,
                )

        parent_context = _string_list(metadata.get("parent_context"))
        for index, _ in enumerate(parent_context):
            parent = ".".join(parent_context[:index + 1])
            child = (
                ".".join(parent_context[:index + 2])
                if index + 1 < len(parent_context)
                else source
            )
            if not parent or not child or parent == child:
                continue
            self.add_relation(
                kind="CONTAINS",
                source=parent,
                relation="contains",
                target=child,
                path=path,
                line=line,
                origin="tree-sitter",
                target_unit_id=(
                    unit_id if index + 1 == len(parent_context) else None
                ),
            )

    def add_graph_fact(
        self,
        fact: Any,
        *,
        plugin_id: str | None,
        packet_kind: str | None = None,
        packet_key: str | None = None,
    ) -> str:
        attributes = dict(getattr(fact, "attributes", ()) or ())
        source = str(fact.source)
        target = str(fact.target)
        fact_kind = str(fact.kind)
        if fact_kind in {
            "php-intra-class-call-relation",
            "php-instance-call-relation",
            "php-static-call-relation",
        }:
            caller_method = str(attributes.get("callerMethod") or "").strip()
            target_method = str(attributes.get("targetMethod") or "").strip()
            target_declared = (
                str(attributes.get("targetMethodDeclared") or "").casefold()
                == "true"
            )
            target_owner = str(
                attributes.get("targetMethodDeclaredOn") or ""
            ).strip()
            if caller_method:
                attributes["declaringSource"] = source
                source = f"{source}::{caller_method}"
            if target_method:
                attributes["declaringTarget"] = target
                target_resolution_proven = target_declared and bool(target_owner)
                attributes["targetResolutionProven"] = target_resolution_proven
                target = (
                    f"{target_owner if target_resolution_proven else target}"
                    f"::{target_method}"
                )
        if packet_kind:
            attributes["packetKind"] = packet_kind
        if packet_key:
            attributes["packetKey"] = packet_key
        return self.add_relation(
            kind=fact_kind,
            source=source,
            relation=str(fact.relation),
            target=target,
            path=_normalize_path(str(fact.path)),
            line=max(1, int(fact.line)),
            origin="plugin",
            plugin_id=plugin_id,
            plugin_ids=tuple(
                str(value)
                for value in getattr(fact, "contributing_plugin_ids", ())
            ),
            attributes=attributes,
            related_paths=tuple(str(path) for path in fact.related_paths),
        )

    def add_relation(
        self,
        *,
        kind: str,
        source: str,
        relation: str,
        target: str,
        path: str,
        line: int,
        origin: str,
        plugin_id: str | None = None,
        plugin_ids: Sequence[str] = (),
        attributes: Mapping[str, Any] | None = None,
        related_paths: Sequence[str] = (),
        source_unit_id: str | None = None,
        target_unit_id: str | None = None,
        identity_discriminator: str | None = None,
    ) -> str:
        normalized_path = _normalize_path(path)
        normalized_related = tuple(sorted({
            _normalize_path(value) for value in related_paths if value
        }))
        normalized_plugin_ids = tuple(sorted({
            value.strip()
            for value in (plugin_id, *plugin_ids)
            if isinstance(value, str) and value.strip()
        }))
        projection = {
            "kind": str(kind),
            "source": str(source),
            "relation": str(relation),
            "target": str(target),
            "path": normalized_path,
            "line": max(1, int(line)),
            "origin": str(origin),
            "attributes": dict(attributes or {}),
            "relatedPaths": normalized_related,
        }
        identity_projection = projection
        if identity_discriminator is not None:
            # Concrete containment edges can share every human-readable field
            # while still pointing at distinct source units. Keep that storage
            # identity out of the displayed relation payload, but include it in
            # the edge key so each concrete child remains reachable.
            identity_projection = {
                **projection,
                "_identityDiscriminator": str(identity_discriminator),
            }
        relation_id = _relation_id(identity_projection)
        if self._capturing_repository_outputs:
            self._repository_output_relation_plugins.setdefault(
                relation_id,
                set(),
            ).update(normalized_plugin_ids)
            existing_relation = self.connection.execute(
                "SELECT source_unit_id, target_unit_id FROM relations "
                "WHERE relation_id = ?",
                (relation_id,),
            ).fetchone()
            if (
                existing_relation is not None
                and (
                    source_unit_id is None
                    or existing_relation["source_unit_id"] is not None
                )
                and (
                    target_unit_id is None
                    or existing_relation["target_unit_id"] is not None
                )
            ):
                self._record_relation_ownership(
                    relation_id,
                    normalized_plugin_ids,
                )
                # The relation identity covers its complete logical payload and
                # related-path set. Contributor reconciliation below handles a
                # changed plugin set in bulk; a cloned equal row needs no SQL
                # writes or per-edge contributor scans here.
                return relation_id
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO relations(
                   relation_id, kind, source, relation, target,
                   source_unit_id, target_unit_id, path, line, origin,
                   plugin_id, attributes_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                relation_id,
                projection["kind"],
                projection["source"],
                projection["relation"],
                projection["target"],
                source_unit_id,
                target_unit_id,
                normalized_path,
                projection["line"],
                projection["origin"],
                normalized_plugin_ids[0]
                if len(normalized_plugin_ids) == 1
                else None,
                _canonical_json(projection["attributes"]),
            ),
        )
        inserted = cursor.rowcount > 0
        endpoint_changed = False
        if not inserted and (source_unit_id or target_unit_id):
            endpoint_cursor = self.connection.execute(
                "UPDATE relations SET "
                "source_unit_id = coalesce(source_unit_id, ?), "
                "target_unit_id = coalesce(target_unit_id, ?) "
                "WHERE relation_id = ? AND ("
                "(source_unit_id IS NULL AND ? IS NOT NULL) OR "
                "(target_unit_id IS NULL AND ? IS NOT NULL))",
                (
                    source_unit_id,
                    target_unit_id,
                    relation_id,
                    source_unit_id,
                    target_unit_id,
                ),
            )
            endpoint_changed = endpoint_cursor.rowcount > 0
        if self._track_mutations and (inserted or endpoint_changed):
            self._touched_relation_ids.add(relation_id)
            if source_unit_id:
                self._explicit_source_relation_ids.add(relation_id)
            if target_unit_id:
                self._explicit_target_relation_ids.add(relation_id)
        self._record_relation_ownership(
            relation_id,
            normalized_plugin_ids,
        )
        for contributor in normalized_plugin_ids:
            self.connection.execute(
                "INSERT OR IGNORE INTO relation_plugins(relation_id, plugin_id) "
                "VALUES (?, ?)",
                (relation_id, contributor),
            )
        if not inserted:
            # A fresh row already carries the correct denormalized value from
            # normalized_plugin_ids. Only a duplicate can add contributors.
            contributors = [
                row["plugin_id"]
                for row in self.connection.execute(
                    "SELECT plugin_id FROM relation_plugins WHERE relation_id = ? "
                    "ORDER BY plugin_id",
                    (relation_id,),
                ).fetchall()
            ]
            self.connection.execute(
                "UPDATE relations SET plugin_id = ? "
                "WHERE relation_id = ? AND plugin_id IS NOT ?",
                (
                    contributors[0] if len(contributors) == 1 else None,
                    relation_id,
                    contributors[0] if len(contributors) == 1 else None,
                ),
            )
        for related_path in (normalized_path, *normalized_related):
            self.connection.execute(
                "INSERT OR IGNORE INTO relation_paths(relation_id, path) VALUES (?, ?)",
                (relation_id, related_path),
            )
        self.connection.executemany(
            "INSERT OR IGNORE INTO relation_names("
            "relation_id, role, normalized_name) VALUES (?, ?, ?)",
            (
                (relation_id, "source", _normalized_name(projection["source"])),
                (relation_id, "target", _normalized_name(projection["target"])),
            ),
        )
        if inserted:
            self.relation_count += 1
        return relation_id

    def _record_relation_ownership(
        self,
        relation_id: str,
        plugin_ids: Sequence[str],
    ) -> None:
        scope = "repository" if self._capturing_repository_outputs else "file"
        if scope == "file" and self._defer_file_relation_ownership:
            return
        self.connection.execute(
            "INSERT OR IGNORE INTO relation_scopes(relation_id, scope) "
            "VALUES (?, ?)",
            (relation_id, scope),
        )
        self.connection.executemany(
            "INSERT OR IGNORE INTO relation_plugin_scopes("
            "relation_id, plugin_id, scope) VALUES (?, ?, ?)",
            ((relation_id, plugin_id, scope) for plugin_id in plugin_ids),
        )

    def flush_deferred_file_relation_ownership(self) -> None:
        """Materialize fresh-build file ownership from canonical edge tables."""

        if not self._defer_file_relation_ownership:
            return
        # A fresh full build contains only file-produced relations until the
        # repository-analysis capture starts. Deriving ownership once avoids two
        # indexed ownership writes for every edge while retaining deterministic
        # insertion order and the exact contributor union.
        self.connection.execute(
            "INSERT OR IGNORE INTO relation_scopes(relation_id, scope) "
            "SELECT relation_id, 'file' FROM relations ORDER BY relation_id"
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO relation_plugin_scopes("
            "relation_id, plugin_id, scope) "
            "SELECT relation_id, plugin_id, 'file' FROM relation_plugins "
            "ORDER BY relation_id, plugin_id"
        )
        self._defer_file_relation_ownership = False

    def add_symbol(
        self,
        symbol: Any,
        *,
        plugin_id: str | None = None,
        plugin_ids: Sequence[str] = (),
    ) -> str:
        normalized_plugin_ids = tuple(sorted({
            value.strip()
            for value in (plugin_id, *plugin_ids)
            if isinstance(value, str) and value.strip()
        }))
        metadata = {
            "path": str(symbol.path),
            "language": "structural-symbol",
            "start_line": int(symbol.line),
            "end_line": int(symbol.line),
            "primary_name": _short_name(str(symbol.qualified_name)),
            "symbol_qualified_name": str(symbol.qualified_name),
            "symbol_kind": str(symbol.kind),
            "symbol_names": [
                _short_name(str(symbol.qualified_name)),
                str(symbol.qualified_name),
            ],
            "symbol_parents": list(symbol.parents),
            "symbol_methods": list(symbol.methods),
            "symbol_constructor_types": list(symbol.constructor_types),
            "symbol_attributes": dict(symbol.attributes),
            "plugin_id": (
                normalized_plugin_ids[0]
                if len(normalized_plugin_ids) == 1
                else None
            ),
            "plugin_ids": list(normalized_plugin_ids),
        }
        unit_id = self.add_unit(
            TextNode(
                text=f"{symbol.kind} {symbol.qualified_name}",
                metadata=metadata,
            ),
            record_type="plugin_symbol",
        )
        for parent in symbol.parents:
            self.add_relation(
                kind="INHERITS",
                source=str(symbol.qualified_name),
                relation="inherits",
                target=str(parent),
                path=str(symbol.path),
                line=int(symbol.line),
                origin="plugin",
                plugin_ids=normalized_plugin_ids,
                source_unit_id=unit_id,
            )
        for dependency in symbol.constructor_types:
            self.add_relation(
                kind="CONSTRUCTOR_DEPENDENCY",
                source=str(symbol.qualified_name),
                relation="constructor-depends-on",
                target=str(dependency),
                path=str(symbol.path),
                line=int(symbol.line),
                origin="plugin",
                plugin_ids=normalized_plugin_ids,
                source_unit_id=unit_id,
            )
        normalized_path = _normalize_path(str(symbol.path))
        file_unit_id = self._file_unit_ids.get(normalized_path)
        if file_unit_id:
            self.add_relation(
                kind="CONTAINS",
                source=normalized_path,
                relation="contains",
                target=str(symbol.qualified_name),
                path=normalized_path,
                line=int(symbol.line),
                origin="plugin",
                plugin_ids=normalized_plugin_ids,
                source_unit_id=file_unit_id,
                target_unit_id=unit_id,
                identity_discriminator=unit_id,
            )
        return unit_id

    def add_context(self, context: Any) -> str:
        line_count = str(context.content).count("\n") + 1
        unit_id = self.add_unit(
            TextNode(
                text=str(context.content),
                metadata={
                    "path": str(context.path),
                    "language": "architecture-source",
                    "start_line": 1,
                    "end_line": line_count,
                    "primary_name": f"{context.plugin_id}:{context.kind}",
                    "architecture_plugin": str(context.plugin_id),
                    "architecture_source_kind": str(context.kind),
                    "architecture_attributes": dict(context.attributes),
                },
            ),
            record_type="plugin_context",
        )
        normalized_path = _normalize_path(str(context.path))
        file_unit_id = self._file_unit_ids.get(normalized_path)
        if file_unit_id:
            self.add_relation(
                kind="CONTAINS",
                source=normalized_path,
                relation="contains",
                target=f"{context.plugin_id}:{context.kind}",
                path=normalized_path,
                line=1,
                origin="plugin",
                plugin_id=str(context.plugin_id),
                source_unit_id=file_unit_id,
                target_unit_id=unit_id,
                identity_discriminator=unit_id,
            )
        return unit_id

    def add_snapshot(self, snapshot: Any) -> None:
        content = str(snapshot.content)
        self.connection.execute(
            "INSERT INTO repository_snapshots("
            "plugin_id, kind, content, content_sha256) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(plugin_id, kind) DO UPDATE SET "
            "content = excluded.content, "
            "content_sha256 = excluded.content_sha256 "
            "WHERE repository_snapshots.content IS NOT excluded.content OR "
            "repository_snapshots.content_sha256 IS NOT excluded.content_sha256",
            (
                str(snapshot.plugin_id),
                str(snapshot.kind),
                content,
                _sha256_text(content),
            ),
        )
        if self._capturing_repository_outputs:
            self._repository_output_snapshots.add((
                str(snapshot.plugin_id),
                str(snapshot.kind),
            ))

    def invalidate_touched_name_resolutions(self) -> None:
        """Invalidate endpoints whose candidate set changed in a cloned graph."""

        if not self._track_mutations or not self._touched_names:
            return
        self.connection.executescript(
            "CREATE TEMP TABLE IF NOT EXISTS delta_names("
            "normalized_name TEXT PRIMARY KEY);"
            "DELETE FROM delta_names;"
            "CREATE TEMP TABLE IF NOT EXISTS delta_explicit_sources("
            "relation_id TEXT PRIMARY KEY);"
            "DELETE FROM delta_explicit_sources;"
            "CREATE TEMP TABLE IF NOT EXISTS delta_explicit_targets("
            "relation_id TEXT PRIMARY KEY);"
            "DELETE FROM delta_explicit_targets;"
        )
        self.connection.executemany(
            "INSERT OR IGNORE INTO delta_names(normalized_name) VALUES (?)",
            ((name,) for name in sorted(self._touched_names)),
        )
        self.connection.executemany(
            "INSERT OR IGNORE INTO delta_explicit_sources(relation_id) VALUES (?)",
            (
                (relation_id,)
                for relation_id in sorted(self._explicit_source_relation_ids)
            ),
        )
        self.connection.executemany(
            "INSERT OR IGNORE INTO delta_explicit_targets(relation_id) VALUES (?)",
            (
                (relation_id,)
                for relation_id in sorted(self._explicit_target_relation_ids)
            ),
        )
        rows = self.connection.execute(
            "SELECT DISTINCT relation_names.relation_id FROM relation_names "
            "JOIN delta_names USING (normalized_name)"
        ).fetchall()
        self._touched_relation_ids.update(str(row["relation_id"]) for row in rows)
        self.connection.execute(
            "UPDATE relations SET source_unit_id = NULL WHERE "
            "relation_id IN (SELECT relation_names.relation_id FROM "
            "relation_names JOIN delta_names USING (normalized_name) "
            "WHERE relation_names.role = 'source') AND relation_id NOT IN "
            "(SELECT relation_id FROM delta_explicit_sources) AND NOT ("
            "origin = 'structural-index' OR "
            "(origin = 'tree-sitter' AND kind != 'CONTAINS') OR "
            "(origin = 'plugin' AND kind IN ("
            "'INHERITS', 'CONSTRUCTOR_DEPENDENCY', 'CONTAINS')))"
        )
        self.connection.execute(
            "UPDATE relations SET target_unit_id = NULL WHERE "
            "relation_id IN (SELECT relation_names.relation_id FROM "
            "relation_names JOIN delta_names USING (normalized_name) "
            "WHERE relation_names.role = 'target') AND relation_id NOT IN "
            "(SELECT relation_id FROM delta_explicit_targets) AND NOT ("
            "origin = 'structural-index' OR kind = 'CONTAINS')"
        )

    def resolve_relations(
        self,
        relation_ids: Iterable[str] | None = None,
    ) -> None:
        """Resolve logical endpoints without guessing across repository files."""

        relation_join = ""
        selected: tuple[str, ...] | None = None
        if relation_ids is not None:
            selected = tuple(sorted(set(relation_ids)))
            if not selected:
                return
            self.connection.executescript(
                "CREATE TEMP TABLE IF NOT EXISTS delta_relations("
                "relation_id TEXT PRIMARY KEY);"
                "DELETE FROM delta_relations;"
            )
            self.connection.executemany(
                "INSERT OR IGNORE INTO delta_relations(relation_id) VALUES (?)",
                ((relation_id,) for relation_id in selected),
            )
            relation_join = (
                " JOIN delta_relations ON "
                "delta_relations.relation_id = relations.relation_id"
            )
        # Full generations can contain hundreds of thousands of logical edges.
        # Resolve their names from one in-memory projection of the indexed name
        # table instead of issuing one or two SQLite SELECTs per endpoint.
        # Selective delta resolution intentionally keeps the smaller indexed
        # lookup path below.
        resolve_name = self._unique_named_unit
        if (
            relation_ids is None
            or len(selected or ()) >= _BULK_RELATION_RESOLUTION_THRESHOLD
        ):
            candidates_by_name: dict[
                str,
                list[tuple[str, str, str, str]],
            ] = {}
            for candidate in self.connection.execute(
                "SELECT unit_names.normalized_name, units.unit_id, units.path, "
                "units.qualified_name, units.record_type FROM unit_names "
                "JOIN units ON units.unit_id = unit_names.unit_id "
                "ORDER BY unit_names.normalized_name, units.path, "
                "units.start_line, units.end_line, units.unit_id"
            ):
                candidates_by_name.setdefault(
                    str(candidate["normalized_name"]),
                    [],
                ).append((
                    str(candidate["unit_id"]),
                    str(candidate["path"]),
                    str(candidate["qualified_name"] or ""),
                    str(candidate["record_type"]),
                ))

            def resolve_preloaded(
                value: str,
                *,
                preferred_paths: Sequence[str] = (),
                allow_short_name: bool = True,
            ) -> str | None:
                def candidates(
                    normalized_name: str,
                    paths: Sequence[str],
                ) -> list[tuple[str, str, str, str]]:
                    rows = candidates_by_name.get(normalized_name, ())
                    if not paths:
                        return list(rows)
                    path_set = set(paths)
                    return [row for row in rows if row[1] in path_set]

                def unambiguous(
                    rows: Sequence[tuple[str, str, str, str]],
                    requested: str,
                ) -> str | None:
                    if len(rows) == 1:
                        return rows[0][0]
                    if not rows:
                        return None
                    normalized_requested = _normalized_name(requested)
                    qualified = [
                        row
                        for row in rows
                        if _normalized_name(row[2]) == normalized_requested
                    ]
                    if len(qualified) == 1:
                        return qualified[0][0]
                    source_qualified = [
                        row for row in qualified if row[3] == "source_unit"
                    ]
                    if len(source_qualified) == 1:
                        return source_qualified[0][0]
                    return None

                exact_name = _normalized_name(value)
                if preferred_paths:
                    preferred = unambiguous(
                        candidates(exact_name, preferred_paths),
                        value,
                    )
                    if preferred:
                        return preferred
                exact_rows = candidates(exact_name, ())
                exact = unambiguous(exact_rows, value)
                if exact:
                    return exact
                if exact_rows or not allow_short_name:
                    return None
                short_name = _normalized_name(_short_name(value))
                if short_name == exact_name:
                    return None
                return unambiguous(
                    candidates(short_name, ()),
                    _short_name(value),
                )

            resolve_name = resolve_preloaded

        rows = self.connection.execute(
            "SELECT relations.relation_id AS relation_id, "
            "relations.kind AS kind, relations.source AS source, "
            "relations.target AS target, relations.relation AS relation, "
            "relations.source_unit_id AS source_unit_id, "
            "relations.target_unit_id AS target_unit_id, "
            "relations.path AS path, relations.line AS line, "
            "relations.attributes_json AS attributes_json, "
            "group_concat(relation_paths.path, char(31)) AS indexed_paths "
            "FROM relations" + relation_join + " LEFT JOIN relation_paths ON "
            "relation_paths.relation_id = relations.relation_id "
            "GROUP BY relations.relation_id"
        )
        callable_by_path: dict[str, list[tuple[int, int, str]]] = {}

        def callable_at(path: str, line: int) -> str | None:
            if path not in callable_by_path:
                callable_by_path[path] = [
                    (int(unit["start_line"]), int(unit["end_line"]), str(unit["unit_id"]))
                    for unit in self.connection.execute(
                        "SELECT unit_id, start_line, end_line FROM units "
                        "WHERE path = ? AND kind IN "
                        "('method', 'function', 'constructor', 'arrow_function')",
                        (path,),
                    )
                ]
            enclosing = [
                unit for unit in callable_by_path[path]
                if unit[0] <= line <= unit[1]
            ]
            if not enclosing:
                return None
            return min(enclosing, key=lambda unit: (unit[1] - unit[0], -unit[0]))[2]

        self.connection.executescript(
            "CREATE TEMP TABLE IF NOT EXISTS resolved_relation_endpoints("
            "relation_id TEXT PRIMARY KEY, source_unit_id TEXT, "
            "target_unit_id TEXT);"
            "DELETE FROM resolved_relation_endpoints;"
        )
        endpoint_updates: list[tuple[str, str | None, str | None]] = []

        def flush_updates() -> None:
            if endpoint_updates:
                self.connection.executemany(
                    "INSERT INTO resolved_relation_endpoints("
                    "relation_id, source_unit_id, target_unit_id) "
                    "VALUES (?, ?, ?) ON CONFLICT(relation_id) DO UPDATE SET "
                    "source_unit_id = coalesce(excluded.source_unit_id, "
                    "resolved_relation_endpoints.source_unit_id), "
                    "target_unit_id = coalesce(excluded.target_unit_id, "
                    "resolved_relation_endpoints.target_unit_id)",
                    endpoint_updates,
                )
                endpoint_updates.clear()

        for row in rows:
            updates: dict[str, str] = {}
            indexed_paths = tuple(dict.fromkeys(
                path
                for path in str(row["indexed_paths"] or row["path"]).split(chr(31))
                if path
            ))
            source_paths = (row["path"],)
            target_paths = tuple(
                path for path in indexed_paths if path != row["path"]
            ) + source_paths
            is_call = str(row["relation"] or "").casefold().startswith("call")
            located_source = (
                callable_at(str(row["path"]), int(row["line"]))
                if is_call else None
            )
            if located_source and located_source != row["source_unit_id"]:
                updates["source_unit_id"] = located_source
            elif row["source_unit_id"] is None:
                resolved = resolve_name(
                    row["source"],
                    preferred_paths=source_paths,
                    allow_short_name="::" not in str(row["source"] or ""),
                )
                if resolved:
                    updates["source_unit_id"] = resolved
            if row["target_unit_id"] is None:
                target = str(row["target"] or "")
                try:
                    relation_attributes = json.loads(
                        row["attributes_json"] or "{}"
                    )
                except (TypeError, ValueError):
                    relation_attributes = {}
                if relation_attributes.get("targetResolutionProven") is False:
                    target = ""
                target_is_scoped = (
                    str(row["kind"] or "").upper() == "CONTAINS"
                    or any(delimiter in target for delimiter in (".", "/", "\\", ":"))
                )
                resolved = (
                    resolve_name(
                        target,
                        preferred_paths=(target_paths if target_is_scoped else ()),
                        allow_short_name="::" not in target,
                    )
                    if target
                    else None
                )
                if resolved:
                    updates["target_unit_id"] = resolved
            if not updates:
                continue
            source_unit_id = updates.get("source_unit_id")
            target_unit_id = updates.get("target_unit_id")
            relation_id = str(row["relation_id"])
            endpoint_updates.append((
                relation_id,
                source_unit_id,
                target_unit_id,
            ))
            if len(endpoint_updates) >= 2000:
                flush_updates()
        flush_updates()
        self.connection.execute(
            "UPDATE relations SET source_unit_id = ("
            "SELECT source_unit_id FROM resolved_relation_endpoints "
            "WHERE resolved_relation_endpoints.relation_id = relations.relation_id"
            ") WHERE relation_id IN (SELECT relation_id FROM "
            "resolved_relation_endpoints WHERE source_unit_id IS NOT NULL)"
        )
        self.connection.execute(
            "UPDATE relations SET target_unit_id = ("
            "SELECT target_unit_id FROM resolved_relation_endpoints "
            "WHERE resolved_relation_endpoints.relation_id = relations.relation_id"
            ") WHERE relation_id IN (SELECT relation_id FROM "
            "resolved_relation_endpoints WHERE target_unit_id IS NOT NULL)"
        )

    def resolve_touched_relations(self) -> None:
        """Re-resolve only endpoints affected by one cloned-generation delta."""

        self.invalidate_touched_name_resolutions()
        self.resolve_relations(self._touched_relation_ids)
        self._touched_relation_ids.clear()
        self._touched_names.clear()
        self._explicit_source_relation_ids.clear()
        self._explicit_target_relation_ids.clear()

    def _unique_named_unit(
        self,
        value: str,
        *,
        preferred_paths: Sequence[str] = (),
        allow_short_name: bool = True,
    ) -> str | None:
        def candidates(normalized_name: str, paths: Sequence[str]) -> list[sqlite3.Row]:
            parameters: list[Any] = [normalized_name]
            where = "unit_names.normalized_name = ?"
            if paths:
                placeholders = ",".join("?" for _ in paths)
                where += f" AND units.path IN ({placeholders})"
                parameters.extend(paths)
            return self.connection.execute(  # nosec B608 -- placeholders only
                "SELECT units.* FROM unit_names JOIN units "
                "ON units.unit_id = unit_names.unit_id WHERE " + where
                + " ORDER BY units.path, units.start_line, units.end_line, units.unit_id",
                parameters,
            ).fetchall()

        def unambiguous(rows: Sequence[sqlite3.Row], requested: str) -> str | None:
            if len(rows) == 1:
                return rows[0]["unit_id"]
            if not rows:
                return None
            normalized_requested = _normalized_name(requested)
            qualified = [
                row for row in rows
                if _normalized_name(str(row["qualified_name"] or ""))
                == normalized_requested
            ]
            if len(qualified) == 1:
                return qualified[0]["unit_id"]
            source_qualified = [
                row for row in qualified if row["record_type"] == "source_unit"
            ]
            if len(source_qualified) == 1:
                return source_qualified[0]["unit_id"]
            return None

        exact_name = _normalized_name(value)
        if preferred_paths:
            preferred = unambiguous(
                candidates(exact_name, preferred_paths),
                value,
            )
            if preferred:
                return preferred

        exact_rows = candidates(exact_name, ())
        exact = unambiguous(exact_rows, value)
        if exact:
            return exact
        if exact_rows:
            return None
        if not allow_short_name:
            return None

        short_name = _normalized_name(_short_name(value))
        if short_name == exact_name:
            return None
        short_rows = candidates(short_name, ())
        return unambiguous(short_rows, _short_name(value))

    def seal(self, receipt: Mapping[str, Any]) -> None:
        encoded = _canonical_json(dict(receipt))
        self.connection.execute(
            "INSERT INTO generation(singleton, receipt_json, sealed_at) VALUES (1, ?, ?)",
            (encoded, time.time()),
        )
        self.connection.commit()
        self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.connection.commit()


def unit_to_manifest(row: sqlite3.Row, *, include_content: bool = False) -> dict[str, Any]:
    result = {
        "unitId": row["unit_id"],
        "path": row["path"],
        "kind": row["kind"],
        "name": row["name"],
        "qualifiedName": row["qualified_name"],
        "startLine": row["start_line"],
        "endLine": row["end_line"],
        "language": row["language"],
        "recordType": row["record_type"],
    }
    if include_content:
        result["content"] = row["content"]
        result["contentSha256"] = row["content_sha256"]
    return result


def _unit_by_id(connection: sqlite3.Connection, unit_id: str | None) -> sqlite3.Row | None:
    if not unit_id:
        return None
    return connection.execute(
        "SELECT * FROM units WHERE unit_id = ?",
        (unit_id,),
    ).fetchone()


def relation_to_manifest(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    source_unit = _unit_by_id(connection, row["source_unit_id"])
    target_unit = _unit_by_id(connection, row["target_unit_id"])
    related_paths = [
        item["path"]
        for item in connection.execute(
            "SELECT path FROM relation_paths WHERE relation_id = ? ORDER BY path",
            (row["relation_id"],),
        ).fetchall()
    ]
    plugin_ids = [
        item["plugin_id"]
        for item in connection.execute(
            "SELECT plugin_id FROM relation_plugins WHERE relation_id = ? "
            "ORDER BY plugin_id",
            (row["relation_id"],),
        ).fetchall()
    ]
    return {
        "evidenceId": row["relation_id"],
        "kind": row["kind"],
        "source": row["source"],
        "relation": row["relation"],
        "target": row["target"],
        "origin": {
            "path": row["path"],
            "line": row["line"],
            "extractor": row["origin"],
            "plugin": plugin_ids[0] if len(plugin_ids) == 1 else None,
            "plugins": plugin_ids,
        },
        "sourceUnit": unit_to_manifest(source_unit) if source_unit else None,
        "targetUnit": unit_to_manifest(target_unit) if target_unit else None,
        "relatedPaths": related_paths,
        "attributes": json.loads(row["attributes_json"] or "{}"),
    }


class StructuralGraphReader:
    """Deterministic, bounded reads over one already-bound generation."""

    def __init__(self, connection: sqlite3.Connection, receipt: Mapping[str, Any]):
        self.connection = connection
        self.receipt = dict(receipt)

    def snapshot(self) -> dict[str, Any]:
        metadata = self.receipt.get("snapshot_metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        snapshot = {
            "kind": str(metadata.get("kind") or "target_head"),
            "branch": self.receipt["branch"],
            "revision": self.receipt["repository_revision"],
            "generationManifestSha256": self.receipt[
                "generation_manifest_sha256"
            ],
        }
        for source_key, target_key in (
            ("base_revision", "baseRevision"),
            ("base_collection_target", "baseCollectionTarget"),
            (
                "base_generation_manifest_sha256",
                "baseGenerationManifestSha256",
            ),
            ("source_revision", "sourceRevision"),
            ("target_source_tree_sha256", "targetSourceTreeSha256"),
            ("overlay_sha256", "overlaySha256"),
        ):
            value = metadata.get(source_key)
            if isinstance(value, str) and value:
                snapshot[target_key] = value
        return snapshot

    def repository_facts(self) -> dict[str, Any]:
        """Return the exact repository profile sealed with this generation."""

        row = self.connection.execute(
            "SELECT content, content_sha256 FROM units "
            "WHERE path = ? AND record_type = 'repository_state' "
            "ORDER BY unit_id LIMIT 1",
            ("__analysis_state__/repository-facts.state",),
        ).fetchone()
        if row is None:
            raise ExactIndexPreconditionError(
                "sealed structural generation lacks repository facts"
            )
        content = str(row["content"] or "")
        expected_sha256 = str(
            self.receipt.get("repository_facts_sha256") or ""
        )
        if (
            not expected_sha256
            or str(row["content_sha256"] or "") != expected_sha256
            or _sha256_text(content) != expected_sha256
        ):
            raise ExactIndexPreconditionError(
                "sealed structural repository facts do not match the receipt"
            )
        try:
            facts = json.loads(content)
        except (TypeError, ValueError) as exception:
            raise ExactIndexPreconditionError(
                "sealed structural repository facts are malformed"
            ) from exception
        if not isinstance(facts, dict):
            raise ExactIndexPreconditionError(
                "sealed structural repository facts are malformed"
            )
        paths = facts.get("paths")
        if not isinstance(paths, list) or not all(
            isinstance(path, str) for path in paths
        ):
            raise ExactIndexPreconditionError(
                "sealed structural repository paths are malformed"
            )
        for field in ("projectType", "sourceRoot"):
            if facts.get(field) is not None and not isinstance(
                facts.get(field), str
            ):
                raise ExactIndexPreconditionError(
                    "sealed structural repository profile is malformed"
                )
        return facts

    def relations_for_paths(
        self,
        paths: Sequence[str],
        *,
        max_relations: int = 80,
    ) -> dict[str, Any]:
        normalized_paths = tuple(sorted({_normalize_path(path) for path in paths}))
        max_relations = max(1, min(int(max_relations), 500))
        anchors: list[dict[str, Any]] = []
        anchor_unit_ids: set[str] = set()
        per_path_limit = max(
            1,
            _MAX_PRELOADED_ANCHOR_UNITS // max(1, len(normalized_paths)),
        )
        total_anchor_units = 0
        omitted_anchor_units = 0
        for path in normalized_paths:
            units = self.connection.execute(
                "SELECT * FROM units WHERE path = ? "
                "ORDER BY start_line, end_line, unit_id LIMIT ?",
                (path, per_path_limit + 1),
            ).fetchall()
            selected_units = units[:per_path_limit]
            anchor_unit_ids.update(row["unit_id"] for row in selected_units)
            total_anchor_units += len(selected_units)
            omitted_for_path = max(0, len(units) - len(selected_units))
            if omitted_for_path:
                total_for_path = self.connection.execute(
                    "SELECT COUNT(*) AS count FROM units WHERE path = ?",
                    (path,),
                ).fetchone()["count"]
                omitted_for_path = max(0, total_for_path - len(selected_units))
            omitted_anchor_units += omitted_for_path
            anchors.append({
                "path": path,
                "symbols": [unit_to_manifest(row) for row in selected_units],
                "omittedSymbols": omitted_for_path,
            })

        relation_ids: list[str] = []
        candidate_queries: list[tuple[str, tuple[Any, ...]]] = []
        if normalized_paths:
            placeholders = ",".join("?" for _ in normalized_paths)
            path_candidates_sql = (
                f"SELECT DISTINCT relations.relation_id "
                f"FROM relations JOIN relation_paths "
                f"ON relation_paths.relation_id = relations.relation_id "
                f"WHERE relation_paths.path IN ({placeholders})"
            )
            candidate_queries.append((path_candidates_sql, normalized_paths))
            relation_ids.extend(
                row["relation_id"]
                for row in self.connection.execute(  # nosec B608
                    path_candidates_sql
                    + f" ORDER BY CASE WHEN relations.path IN ({placeholders}) "
                    f"THEN 0 ELSE 1 END, "
                    f"CASE WHEN relations.origin = 'plugin' THEN 0 ELSE 1 END, "
                    f"CASE relations.kind WHEN 'CONTAINS' THEN 2 "
                    f"WHEN 'CALLS' THEN 1 ELSE 0 END, "
                    f"relations.path, relations.line, relations.relation_id LIMIT ?",
                    (*normalized_paths, *normalized_paths, max_relations + 1),
                ).fetchall()
            )
        if anchor_unit_ids:
            unit_ids = tuple(sorted(anchor_unit_ids))
            placeholders = ",".join("?" for _ in unit_ids)
            unit_candidates_sql = (
                f"SELECT relation_id FROM relations WHERE "
                f"source_unit_id IN ({placeholders}) OR "
                f"target_unit_id IN ({placeholders})"
            )
            candidate_queries.append((unit_candidates_sql, (*unit_ids, *unit_ids)))
            if len(dict.fromkeys(relation_ids)) <= max_relations:
                relation_ids.extend(
                    row["relation_id"]
                    for row in self.connection.execute(  # nosec B608
                        unit_candidates_sql + " ORDER BY relation_id LIMIT ?",
                        (*unit_ids, *unit_ids, max_relations + 1),
                    ).fetchall()
                )
        ordered_ids = tuple(dict.fromkeys(relation_ids))
        selected_ids = ordered_ids[:max_relations]
        if candidate_queries:
            union_sql = " UNION ".join(
                query for query, _ in candidate_queries
            )
            union_parameters = tuple(
                parameter
                for _, parameters in candidate_queries
                for parameter in parameters
            )
            total_relations = self.connection.execute(  # nosec B608
                f"SELECT COUNT(*) AS count FROM ({union_sql})",
                union_parameters,
            ).fetchone()["count"]
        else:
            total_relations = 0
        relations: list[dict[str, Any]] = []
        for relation_id in selected_ids:
            row = self.connection.execute(
                "SELECT * FROM relations WHERE relation_id = ?",
                (relation_id,),
            ).fetchone()
            if row is not None:
                relations.append(relation_to_manifest(self.connection, row))
        return {
            "snapshot": self.snapshot(),
            "anchors": anchors,
            "relations": relations,
            "coverage": {
                "state": (
                    "complete"
                    if total_relations <= max_relations and omitted_anchor_units == 0
                    else "bounded"
                ),
                "totalRelations": total_relations,
                "omittedRelations": max(0, total_relations - len(relations)),
                "preloadedSymbols": total_anchor_units,
                "omittedSymbols": omitted_anchor_units,
            },
        }

    def get_unit(self, unit_id: str) -> dict[str, Any] | None:
        row = _unit_by_id(self.connection, unit_id)
        if row is None:
            return None
        source_evidence = row["record_type"] in {"source_unit", "plugin_context"}
        return {
            "snapshot": self.snapshot(),
            "unit": unit_to_manifest(row, include_content=source_evidence),
            "sourceEvidence": source_evidence,
        }

    def relations_among(
        self,
        unit_ids: Sequence[str],
        *,
        max_results: int = 10000,
    ) -> dict[str, Any]:
        """Return a deterministic bounded subgraph induced by unit IDs."""

        selected_ids = tuple(dict.fromkeys(
            str(unit_id).strip()
            for unit_id in unit_ids
            if str(unit_id).strip()
        ))
        max_results = max(1, min(int(max_results), 10000))
        if not selected_ids:
            return {
                "snapshot": self.snapshot(),
                "results": [],
                "truncated": False,
                "resultCount": 0,
            }
        values = ",".join("(?)" for _unit_id_value in selected_ids)
        rows = self.connection.execute(
            "WITH selected(unit_id) AS (VALUES " + values + ") "
            "SELECT relations.* FROM relations "
            "JOIN selected AS sources "
            "ON sources.unit_id = relations.source_unit_id "
            "JOIN selected AS targets "
            "ON targets.unit_id = relations.target_unit_id "
            "ORDER BY relations.relation_id LIMIT ?",
            (*selected_ids, max_results + 1),
        ).fetchall()
        selected = rows[:max_results]
        return {
            "snapshot": self.snapshot(),
            "results": [
                relation_to_manifest(self.connection, row)
                for row in selected
            ],
            "truncated": len(rows) > max_results,
            "resultCount": len(selected),
        }

    def query_graph(
        self,
        pattern: str,
        target: str,
        *,
        max_results: int = 25,
        cursor: int = 0,
    ) -> dict[str, Any]:
        pattern = str(pattern or "").strip().casefold()
        target = str(target or "").strip()
        max_results = max(1, min(int(max_results), 100))
        cursor = max(0, int(cursor))
        if not target:
            raise ValueError("graph query target must be non-empty")

        if pattern in {"symbol_search", "symbols"}:
            rows = self.search_units(
                target,
                max_results=max_results + 1,
                offset=cursor,
            )
            selected = rows[:max_results]
            truncated = len(rows) > max_results
            return {
                "snapshot": self.snapshot(),
                "pattern": "symbol_search",
                "target": target,
                "results": selected,
                "cursor": cursor,
                "nextCursor": cursor + len(selected) if truncated else None,
                "truncated": truncated,
                "resultCount": len(selected),
            }
        if pattern == "file_summary":
            path = _normalize_path(target)
            rows = self.connection.execute(
                "SELECT * FROM units WHERE path = ? "
                "ORDER BY start_line, end_line, unit_id LIMIT ? OFFSET ?",
                (path, max_results + 1, cursor),
            ).fetchall()
            return self._unit_query_response(
                pattern,
                target,
                rows,
                max_results,
                cursor=cursor,
            )

        kind_map = {
            "callers_of": ("incoming", {
                "CALLS",
                "CALLS_INSTANCE",
                "CALLS_RESOLVED_TARGET",
                "CALLS_STATIC",
                "CALLS_UNIQUE_CO_DECLARED_DEFINITION",
            }),
            "callees_of": ("outgoing", {
                "CALLS",
                "CALLS_INSTANCE",
                "CALLS_RESOLVED_TARGET",
                "CALLS_STATIC",
                "CALLS_UNIQUE_CO_DECLARED_DEFINITION",
            }),
            "references_to": ("incoming", {
                "DEPENDS_ON",
                "DEPENDS_ON_CONFIG_FIELD",
                "DEPENDS_ON_INDEXER",
                "REFERENCES",
                "REFERENCES_DECLARED_FIELD",
                "REFERENCES_JSON_SCHEMA_TARGET",
                "USES",
            }),
            "imports_of": ("outgoing", {
                "IMPORTS",
                "IMPORTS_FROM",
                "RESOLVES_IMPORT",
            }),
            "importers_of": ("incoming", {
                "IMPORTS",
                "IMPORTS_FROM",
                "RESOLVES_IMPORT",
            }),
            "children_of": ("outgoing", {"CONTAINS"}),
            "tests_for": ("both", {"TESTED_BY", "TESTS"}),
            "inheritors_of": ("incoming", {"EXTENDS", "INHERITS", "IMPLEMENTS"}),
            # Neutral plugin facts preserve an extractor-specific ``kind`` and
            # carry the portable verb in ``relation``. These patterns therefore
            # describe vocabulary emitted by CodeCrow's current plugins rather
            # than pretending upstream-only TRIGGERS/PUBLISHES edges exist.
            "triggers_of": ("incoming", {"RUNS_ON"}),
            "triggered_by": ("outgoing", {"RUNS_ON"}),
            "publishers_of": ("incoming", {
                "DISPATCHES_EVENT",
                "DISPATCHES_TO_UNIQUE_LAYOUT_LISTENER",
                "PRODUCES",
                "PUBLISHES_THROUGH",
            }),
            "listeners_of": ("outgoing", {
                "LISTENS_TO_LAYOUT_DISPATCHERS",
                "OBSERVED_BY",
            }),
            "handlers_of": ("incoming", {"HANDLES"}),
            "endpoints_for": ("outgoing", {"HANDLES"}),
            "consumers_of": ("incoming", {"CONSUMES", "CONSUMES_QUEUE"}),
            "relations_of": ("both", set()),
            "framework_relations": ("both", {"__plugin__"}),
        }
        if pattern not in kind_map:
            return {
                "status": "error",
                "error": "Unknown graph pattern",
                "availablePatterns": sorted((*kind_map, "symbol_search", "file_summary")),
                "results": [],
            }
        direction, kinds = kind_map[pattern]
        normalized_target_path = _optional_path(target)
        candidate_page_size = 20
        candidate_units, candidate_count = self._resolve_units_with_count(
            target,
            limit=candidate_page_size,
        )
        target_is_exact_path = bool(
            candidate_units
            and normalized_target_path is not None
            and all(
                str(row["path"] or "") == normalized_target_path
                for row in candidate_units
            )
        )
        if candidate_count > 1 and not target_is_exact_path:
            candidate_page_size = min(20, max_results)
            if cursor or len(candidate_units) > candidate_page_size:
                candidate_units, _ = self._resolve_units_with_count(
                    target,
                    limit=candidate_page_size,
                    offset=cursor,
                )
            next_cursor = cursor + len(candidate_units)
            has_more_candidates = next_cursor < candidate_count
            return {
                "status": "ambiguous",
                "error": "Graph target resolves to multiple structural units",
                "snapshot": self.snapshot(),
                "pattern": pattern,
                "target": target,
                "candidates": [
                    unit_to_manifest(row) for row in candidate_units
                ],
                "candidateCount": candidate_count,
                "candidateResultCount": len(candidate_units),
                "candidatesTruncated": candidate_count > len(candidate_units),
                "hint": (
                    "Retry with a candidate unitId; request the same pattern and "
                    "target with nextCursor to inspect additional candidates."
                    if has_more_candidates
                    else "Retry with a candidate unitId."
                ),
                "results": [],
                "cursor": cursor,
                "nextCursor": next_cursor if has_more_candidates else None,
                "truncated": has_more_candidates,
                "resultCount": 0,
            }
        unit_ids = tuple(row["unit_id"] for row in candidate_units)
        short_target = _short_name(target).casefold()
        candidate_queries: list[tuple[str, tuple[Any, ...]]] = []

        def add_endpoint_candidates(column: str) -> None:
            # Resolve the exact text candidate through the normalized endpoint
            # index, then retain the original SQLite NOCASE comparison as the
            # final predicate. The latter preserves the previous matching
            # semantics for whitespace and non-ASCII text while avoiding a
            # complete relations-table scan for every graph pattern.
            candidate_queries.append((
                "SELECT relation_names.relation_id FROM relation_names "
                "JOIN relations AS exact_relation ON "
                "exact_relation.relation_id = relation_names.relation_id "
                "WHERE relation_names.normalized_name = ? "
                "AND relation_names.role = ? "
                f"AND exact_relation.{column} = ? COLLATE NOCASE",
                (_normalized_name(target), column, target),
            ))
            if unit_ids:
                placeholders = ",".join("?" for _ in unit_ids)
                candidate_queries.append((
                    "SELECT relation_id FROM relations WHERE "
                    f"{column}_unit_id IN ({placeholders})",
                    unit_ids,
                ))
                return
            # Only broaden an unresolved, already-unqualified target. A
            # qualified target is precise and must not pull in every unit that
            # happens to share its final component.
            if short_target != target.casefold():
                return
            fallback_clauses: list[str] = []
            fallback_parameters: list[str] = []
            for delimiter in (".", "/", "\\", ":"):
                fallback_clauses.append(
                    f"lower({column}) LIKE ? ESCAPE '\\'"
                )
                fallback_parameters.append(
                    "%" + _escape_like(delimiter + short_target)
                )
            candidate_queries.append((
                "SELECT relation_id FROM relations WHERE "
                + " OR ".join(fallback_clauses),
                tuple(fallback_parameters),
            ))

        if direction in {"outgoing", "both"}:
            add_endpoint_candidates("source")
        if direction in {"incoming", "both"}:
            add_endpoint_candidates("target")
        path_target = _optional_path(target)
        if direction == "both" and path_target is not None:
            candidate_queries.append((
                "SELECT relation_id FROM relation_paths WHERE path = ?",
                (path_target,),
            ))
        candidate_sql = " UNION ".join(
            query for query, _ in candidate_queries
        )
        params = [
            parameter
            for _, parameters in candidate_queries
            for parameter in parameters
        ]
        where_clauses: list[str] = []
        if kinds == {"__plugin__"}:
            where_clauses.append("relations.origin = 'plugin'")
        elif kinds:
            placeholders = ",".join("?" for _ in kinds)
            normalized_kinds = sorted(kinds)
            where_clauses.append(
                "(upper(replace(relations.kind, '-', '_')) IN ("
                f"{placeholders}) OR "
                "upper(replace(relations.relation, '-', '_')) IN ("
                f"{placeholders}))"
            )
            params.extend(normalized_kinds)
            params.extend(normalized_kinds)
        if pattern == "endpoints_for":
            endpoint_placeholders = ",".join(
                "?" for _ in _ENDPOINT_RELATION_KINDS
            )
            where_clauses.append(
                "(upper(replace(relations.kind, '-', '_')) IN ("
                f"{endpoint_placeholders}) OR EXISTS ("
                "SELECT 1 FROM units AS endpoint_units "
                "WHERE endpoint_units.unit_id = relations.target_unit_id "
                "AND upper(endpoint_units.kind) = 'ENDPOINT'))"
            )
            params.extend(sorted(_ENDPOINT_RELATION_KINDS))
        where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        rows = self.connection.execute(  # nosec B608
            "WITH candidate_relations(relation_id) AS ("
            f"{candidate_sql}) "
            "SELECT relations.* FROM candidate_relations "
            "JOIN relations ON relations.relation_id = "
            "candidate_relations.relation_id "
            f"{where} "
            "ORDER BY relations.path, relations.line, "
            "relations.relation_id LIMIT ? OFFSET ?",
            (*params, max_results + 1, cursor),
        ).fetchall()
        selected = rows[:max_results]
        truncated = len(rows) > max_results
        return {
            "snapshot": self.snapshot(),
            "pattern": pattern,
            "target": target,
            "resolvedUnits": [
                unit_to_manifest(row) for row in candidate_units
            ],
            "results": [relation_to_manifest(self.connection, row) for row in selected],
            "cursor": cursor,
            "nextCursor": cursor + len(selected) if truncated else None,
            "truncated": truncated,
            "resultCount": len(selected),
        }

    def search_units(
        self,
        query: str,
        *,
        max_results: int = 25,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        max_results = max(1, int(max_results))
        offset = max(0, int(offset))
        expression = _fts_query(query)
        rows: list[sqlite3.Row] = []
        fts_available = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'units_fts'"
        ).fetchone() is not None
        if expression and fts_available:
            try:
                rows = self.connection.execute(
                    "SELECT units.* FROM units_fts JOIN units "
                    "ON units.unit_id = units_fts.unit_id "
                    "WHERE units_fts MATCH ? "
                    "ORDER BY bm25(units_fts), units.unit_id LIMIT ? OFFSET ?",
                    (expression, max_results, offset),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            token = f"%{_escape_like(query.casefold())}%"
            rows = self.connection.execute(
                "SELECT * FROM units WHERE lower(path) LIKE ? ESCAPE '\\' "
                "OR lower(name) LIKE ? ESCAPE '\\' "
                "OR lower(qualified_name) LIKE ? ESCAPE '\\' "
                "ORDER BY path, start_line, unit_id LIMIT ? OFFSET ?",
                (token, token, token, max_results, offset),
            ).fetchall()
        return [unit_to_manifest(row) for row in rows]

    def _resolve_units(self, target: str) -> list[sqlite3.Row]:
        rows, _ = self._resolve_units_with_count(target)
        return rows

    def _resolve_units_with_count(
        self,
        target: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[sqlite3.Row], int]:
        """Resolve one stable candidate page and the exact candidate count."""

        limit = max(1, int(limit))
        offset = max(0, int(offset))
        direct = _unit_by_id(self.connection, target)
        if direct is not None:
            return [direct], 1
        path = _optional_path(target)
        if path:
            count = int(self.connection.execute(
                "SELECT COUNT(*) FROM units WHERE path = ?",
                (path,),
            ).fetchone()[0])
            rows = self.connection.execute(
                "SELECT * FROM units WHERE path = ? "
                "ORDER BY start_line, end_line, unit_id LIMIT ? OFFSET ?",
                (path, limit, offset),
            ).fetchall()
            if count:
                return rows, count

        def named_rows(normalized_name: str) -> tuple[list[sqlite3.Row], int]:
            count = int(self.connection.execute(
                "SELECT COUNT(*) FROM unit_names WHERE normalized_name = ?",
                (normalized_name,),
            ).fetchone()[0])
            if not count:
                return [], 0
            rows = self.connection.execute(
                "SELECT units.* FROM unit_names JOIN units "
                "ON units.unit_id = unit_names.unit_id "
                "WHERE unit_names.normalized_name = ? "
                "ORDER BY units.path, units.start_line, units.unit_id "
                "LIMIT ? OFFSET ?",
                (normalized_name, limit, offset),
            ).fetchall()
            return rows, count

        exact_name = _normalized_name(target)
        rows, count = named_rows(exact_name)
        if count:
            return rows, count
        short_name = _normalized_name(_short_name(target))
        if short_name != exact_name:
            return named_rows(short_name)
        return [], 0

    def _unit_query_response(
        self,
        pattern: str,
        target: str,
        rows: Sequence[sqlite3.Row],
        max_results: int,
        *,
        cursor: int = 0,
    ) -> dict[str, Any]:
        selected = rows[:max_results]
        truncated = len(rows) > max_results
        return {
            "snapshot": self.snapshot(),
            "pattern": pattern,
            "target": target,
            "results": [unit_to_manifest(row) for row in selected],
            "cursor": cursor,
            "nextCursor": cursor + len(selected) if truncated else None,
            "truncated": truncated,
            "resultCount": len(selected),
        }


def build_receipt(
    connection: sqlite3.Connection,
    *,
    workspace: str,
    project: str,
    branch: str,
    revision: str,
    source_tree_sha256: str,
    collection_target: str,
    repository_facts_json: str,
    plugin_ids: Sequence[str],
    plugin_fingerprint: str,
    plugin_descriptor_fingerprint: str,
    plugin_implementation_fingerprint: str,
    index_representation_fingerprint: str,
    include_patterns: Sequence[str] | None,
    exclude_patterns: Sequence[str] | None,
    document_count: int,
    skipped_file_count: int,
    snapshot_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # Hash the canonical member array as a stream. Large framework indexes can
    # contain hundreds of thousands of edges; retaining three complete Python
    # member lists (plus a contributor map) multiplied memory by every parallel
    # indexing job without changing the generation digest.
    members_hasher = hashlib.sha256()
    members_hasher.update(b"[")
    member_count = 0
    unit_count = 0
    relation_count = 0
    snapshot_count = 0

    def add_encoded_member(encoded_member: str) -> None:
        nonlocal member_count
        if member_count:
            members_hasher.update(b",")
        members_hasher.update(encoded_member.encode("utf-8"))
        member_count += 1

    def add_member(member: tuple[str, str, str, str]) -> None:
        add_encoded_member(_canonical_json(member))

    for row in connection.execute(
        "SELECT unit_id, content_sha256, metadata_json "
        "FROM units ORDER BY unit_id"
    ):
        add_member((
            "unit",
            str(row["unit_id"]),
            str(row["content_sha256"]),
            str(row["metadata_json"]),
        ))
        unit_count += 1

    # A cloned delta changes only a bounded subset of a structural graph. The
    # canonical digest of an unchanged relation row is therefore safe to carry
    # with the clone. SQLite invalidates the cache on every relation UPDATE and
    # cascades DELETEs; new rows have no cache entry. Recompute only those
    # missing rows before streaming the exact same canonical member sequence.
    missing_relation_members: list[tuple[str, str]] = []
    plugin_rows = iter(connection.execute(
        "SELECT relation_id, plugin_id FROM relation_plugins "
        "ORDER BY relation_id, plugin_id"
    ))
    plugin_row = next(plugin_rows, None)
    for row in connection.execute(
        "SELECT relations.* FROM relations "
        "LEFT JOIN relation_manifest_cache USING (relation_id) "
        "WHERE relation_manifest_cache.relation_id IS NULL "
        "ORDER BY relations.relation_id"
    ):
        payload = dict(row)
        # Endpoint IDs and the single-plugin shortcut are derived indexes. The
        # logical source/target and exact contributor array below are the
        # authoritative relation content. Excluding derived columns keeps
        # endpoint re-resolution from invalidating tens of thousands of
        # otherwise unchanged semantic relation digests on every delta.
        payload.pop("source_unit_id", None)
        payload.pop("target_unit_id", None)
        payload.pop("plugin_id", None)
        relation_id = str(row["relation_id"])
        contributors: list[str] = []
        while (
            plugin_row is not None
            and str(plugin_row["relation_id"]) < relation_id
        ):
            plugin_row = next(plugin_rows, None)
        while (
            plugin_row is not None
            and str(plugin_row["relation_id"]) == relation_id
        ):
            contributors.append(str(plugin_row["plugin_id"]))
            plugin_row = next(plugin_rows, None)
        missing_relation_members.append((
            relation_id,
            _canonical_json((
                "relation",
                relation_id,
                _sha256_text(_canonical_json(payload)),
                _canonical_json(contributors),
            )),
        ))
    if missing_relation_members:
        connection.executemany(
            "INSERT INTO relation_manifest_cache(relation_id, member_json) "
            "VALUES (?, ?)",
            missing_relation_members,
        )

    for row in connection.execute(
        "SELECT member_json FROM relation_manifest_cache ORDER BY relation_id"
    ):
        add_encoded_member(str(row["member_json"]))
        relation_count += 1

    for row in connection.execute(
        "SELECT plugin_id, kind, content_sha256 "
        "FROM repository_snapshots ORDER BY plugin_id, kind"
    ):
        add_member((
            "snapshot",
            f"{row['plugin_id']}:{row['kind']}",
            str(row["content_sha256"]),
            "",
        ))
        snapshot_count += 1
    members_hasher.update(b"]")
    members_sha256 = members_hasher.hexdigest()
    selection = canonical_index_selection_policy(include_patterns, exclude_patterns)
    selection_sha256 = compute_index_selection_policy_sha256(
        selection["includePatterns"],
        selection["excludePatterns"],
    )
    repository_facts_sha256 = _sha256_text(repository_facts_json)
    normalized_snapshot_metadata = dict(snapshot_metadata or {})
    manifest_content = {
        "schema": GENERATION_SCHEMA,
        "storeSchema": STRUCTURAL_STORE_SCHEMA,
        "storeSchemaRevision": STRUCTURAL_STORE_SCHEMA_REVISION,
        "workspace": workspace,
        "project": project,
        "branch": branch,
        "revision": revision,
        "collectionTargetSha256": _sha256_text(collection_target),
        "sourceTreeSha256": source_tree_sha256,
        "memberCount": member_count,
        "membersSha256": members_sha256,
        "documentCount": int(document_count),
        "skippedFileCount": int(skipped_file_count),
        "repositoryFactsSha256": repository_facts_sha256,
        "indexSelectionPolicySha256": selection_sha256,
        "pluginIds": list(plugin_ids),
        "pluginFingerprint": plugin_fingerprint,
        "pluginDescriptorFingerprint": plugin_descriptor_fingerprint,
        "pluginImplementationFingerprint": plugin_implementation_fingerprint,
        "indexRepresentationFingerprint": index_representation_fingerprint,
        "snapshotMetadata": normalized_snapshot_metadata,
    }
    manifest_sha256 = _sha256_text(_canonical_json(manifest_content))
    return {
        "workspace": workspace,
        "project": project,
        "branch": branch,
        # Kept alongside the explicit repository_revision field because the
        # stable preflight response contract exposes both names.
        "commit": revision,
        "repository_revision": revision,
        "repository_facts_sha256": repository_facts_sha256,
        "plugin_ids": list(plugin_ids),
        "plugin_fingerprint": plugin_fingerprint or _ZERO_FINGERPRINT,
        "plugin_descriptor_fingerprint": (
            plugin_descriptor_fingerprint or _ZERO_FINGERPRINT
        ),
        "plugin_implementation_fingerprint": (
            plugin_implementation_fingerprint or _ZERO_FINGERPRINT
        ),
        "index_representation_fingerprint": index_representation_fingerprint,
        "current_index_representation_fingerprint": index_representation_fingerprint,
        "generation_schema": GENERATION_SCHEMA,
        "store_schema": STRUCTURAL_STORE_SCHEMA,
        "store_schema_revision": STRUCTURAL_STORE_SCHEMA_REVISION,
        "generation_member_count": max(1, member_count),
        "generation_members_sha256": members_sha256,
        "generation_manifest_sha256": manifest_sha256,
        "source_tree_sha256": source_tree_sha256,
        "index_include_patterns": selection["includePatterns"],
        "index_exclude_patterns": selection["excludePatterns"],
        "index_selection_policy_sha256": selection_sha256,
        "point_count": max(1, member_count),
        "collection_target": collection_target,
        "unit_count": unit_count,
        "relation_count": relation_count,
        "snapshot_count": snapshot_count,
        "document_count": int(document_count),
        "skipped_file_count": int(skipped_file_count),
        "snapshot_metadata": normalized_snapshot_metadata,
    }


def write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    if not _SHA256_RE.fullmatch(str(receipt.get("generation_manifest_sha256", ""))):
        raise ValueError("structural generation receipt has an invalid manifest digest")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(_canonical_json(dict(receipt)), encoding="utf-8")
    os.replace(temporary, path)
