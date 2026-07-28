"""Repository-path identity helpers for retrieval and index compatibility."""

from typing import Any, List


def normalize_repository_path(path: Any) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def repository_paths_match(left: Any, right: Any) -> bool:
    """Match exact or multi-segment relative paths, never bare basenames."""
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


def repository_path_suffix_candidates(path: Any) -> List[str]:
    """Return exact query candidates without degrading to a bare basename."""
    normalized = normalize_repository_path(path)
    if not normalized:
        return []

    candidates = [normalized]
    remainder = normalized
    while "/" in remainder:
        remainder = remainder.split("/", 1)[1]
        if "/" not in remainder:
            break
        candidates.append(remainder)
    return candidates
