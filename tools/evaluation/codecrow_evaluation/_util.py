from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_sha256(value: object, field: str, error_type: type[Exception]) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise error_type(f"{field} must be a lowercase SHA-256 digest")
    return value


def require_string(value: object, field: str, error_type: type[Exception]) -> str:
    if not isinstance(value, str) or not value.strip():
        raise error_type(f"{field} must be a non-empty string")
    return value


def require_mapping(value: object, field: str, error_type: type[Exception]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise error_type(f"{field} must be an object")
    return value


def parse_utc(value: object, field: str, error_type: type[Exception]) -> datetime:
    text = require_string(value, field, error_type)
    if not text.endswith("Z"):
        raise error_type(f"{field} must be an RFC3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise error_type(f"{field} must be a valid RFC3339 timestamp") from exc
    return parsed
