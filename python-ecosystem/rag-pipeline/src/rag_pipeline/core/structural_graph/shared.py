"""Neutral structural graph identity and serialization helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from ...utils.path_identity import normalize_repository_path
from ...utils.utils import LANGUAGE_MAP

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


_SOURCE_SUFFIXES = frozenset(LANGUAGE_MAP) | {'.kts'}

@contextmanager
def _timed_graph_phase(connection: sqlite3.Connection, phase: str, *, target: str = ""):
    """Correlate build work using its private database; stat never scans pages."""
    database = str(connection.execute("PRAGMA database_list").fetchone()[2])
    started = time.monotonic()
    outcome = "completed"
    try:
        yield
    except BaseException:
        outcome = "failed"
        raise
    finally:
        sizes = []
        for suffix in ("", "-wal"):
            try:
                sizes.append(Path(database + suffix).stat().st_size if database else 0)
            except OSError:
                sizes.append(0)
        logging.getLogger(__name__).info(
            "Structural graph phase: target=%s generation=%s phase=%s "
            "outcome=%s elapsed_ms=%d database_bytes=%d wal_bytes=%d",
            target, Path(database).parent.name if database else "memory", phase,
            outcome, round((time.monotonic() - started) * 1000), *sizes,
        )


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


def _looks_like_path(target: str) -> bool:
    final = target.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    suffix = "." + final.rsplit(".", 1)[-1].casefold() if "." in final else ""
    return "/" in target or "\\" in target or suffix in _SOURCE_SUFFIXES



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


def _unit_names(
    name: str,
    qualified_name: str,
    metadata: Mapping[str, Any],
) -> frozenset[str]:
    """Keep original alias spellings separate from casefolded search keys."""
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
    return frozenset(names)


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
