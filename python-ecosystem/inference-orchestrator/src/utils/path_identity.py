"""Neutral repository-path identity helpers used by inference host services."""

from typing import Any


def normalize_repository_path(path: Any) -> str:
    """Normalize separators and harmless relative/absolute prefixes."""
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def repository_paths_match(left: Any, right: Any) -> bool:
    """Match exact or repository-relative paths without basename identity.

    A repository path may be returned with an absolute checkout prefix, so a
    multi-segment relative suffix is accepted. A bare basename is never accepted
    as a suffix identity because framework repositories routinely contain many
    meaningful files named ``di.xml``, ``routes.xml``, or ``config.php``.
    """
    left_path = normalize_repository_path(left)
    right_path = normalize_repository_path(right)
    if not left_path or not right_path:
        return False
    if left_path == right_path:
        return True

    shorter, longer = sorted(
        (left_path, right_path),
        key=lambda value: (len(value), value),
    )
    return "/" in shorter and longer.endswith("/" + shorter)
