"""Versioned default-critical classification for changed repository paths."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .changed_coverage import GateInputError
from .source_inventory import (
    _MAX_POLICY_BYTES,
    _open_repository_root,
    _read_file_at,
    _repository_path,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _glob_matches(path: str, pattern: str) -> bool:
    """Match a small anchored Git-style glob where ``**/`` may match zero segments."""

    expression = ""
    index = 0
    while index < len(pattern):
        token = pattern[index]
        if token == "*" and pattern[index : index + 2] == "**":
            if pattern[index + 2 : index + 3] == "/":
                expression += "(?:[^/]+/)*"
                index += 3
            else:
                expression += ".*"
                index += 2
        elif token == "*":
            expression += "[^/]*"
            index += 1
        elif token == "?":
            expression += "[^/]"
            index += 1
        else:
            expression += re.escape(token)
            index += 1
    return re.fullmatch(expression, path) is not None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GateInputError(f"duplicate correctness policy key: {key}")
        result[key] = value
    return result


def _parse(raw: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                GateInputError(f"invalid correctness policy JSON constant: {constant}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateInputError("correctness policy is malformed JSON") from error
    if not isinstance(value, Mapping):
        raise GateInputError("correctness policy must be an object")
    return value


def load_correctness_policy(
    policy_path: Path, *, repository_root: Path
) -> tuple[Mapping[str, Any], str, str]:
    """Load exact protected policy bytes through no-follow repository dirfds."""

    repository = Path(os.path.abspath(repository_root))
    candidate_input = policy_path if policy_path.is_absolute() else repository / policy_path
    candidate = Path(os.path.abspath(candidate_input))
    try:
        relative = candidate.relative_to(repository).as_posix()
    except ValueError as error:
        raise GateInputError("correctness policy must stay inside the repository") from error
    relative = _repository_path(relative, "correctness policy path")
    root_descriptor = _open_repository_root(repository)
    try:
        raw = _read_file_at(
            root_descriptor,
            relative,
            field="correctness policy",
            size_limit=_MAX_POLICY_BYTES,
        )
    finally:
        os.close(root_descriptor)
    policy = _parse(raw)
    validate_correctness_policy(policy)
    return policy, relative, hashlib.sha256(raw).hexdigest()


def validate_correctness_policy(policy: Mapping[str, Any]) -> None:
    if set(policy) != {
        "schemaVersion",
        "scopeRoots",
        "languageSuffixes",
        "nonCriticalPaths",
    }:
        raise GateInputError("correctness policy contract is malformed")
    roots = policy.get("scopeRoots")
    suffixes = policy.get("languageSuffixes")
    exceptions = policy.get("nonCriticalPaths")
    if (
        policy.get("schemaVersion") != 1
        or not isinstance(roots, list)
        or not roots
        or roots != sorted(set(roots))
        or any(_repository_path(root, "correctness scope root") != root for root in roots)
        or not isinstance(suffixes, Mapping)
        or set(suffixes) != {".java", ".py"}
        or suffixes.get(".java") != "java"
        or suffixes.get(".py") != "python"
        or not isinstance(exceptions, list)
    ):
        raise GateInputError("correctness policy contract is malformed")
    seen_patterns: set[str] = set()
    for entry in exceptions:
        if not isinstance(entry, Mapping) or set(entry) != {
            "glob",
            "reason",
            "owner",
            "reviewer",
        }:
            raise GateInputError("non-critical path approval is malformed")
        pattern = entry.get("glob")
        reason = entry.get("reason")
        owner = entry.get("owner")
        reviewer = entry.get("reviewer")
        if (
            not isinstance(pattern, str)
            or not pattern
            or pattern in seen_patterns
            or "\\" in pattern
            or PurePosixPath(pattern).is_absolute()
            or ".." in PurePosixPath(pattern).parts
            or len(PurePosixPath(pattern).parts) < 2
            or PurePosixPath(pattern).parts[0] not in roots
            or not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(owner, str)
            or not owner.strip()
            or not isinstance(reviewer, str)
            or not reviewer.strip()
            or owner == reviewer
        ):
            raise GateInputError("non-critical path approval is malformed")
        seen_patterns.add(pattern)


def classify_path(path: str, policy: Mapping[str, Any]) -> tuple[bool, str | None]:
    """Classify paths inside governed roots as critical unless explicitly approved."""

    validate_correctness_policy(policy)
    path = _repository_path(path, "changed path")
    root = path.split("/", 1)[0]
    if root not in policy["scopeRoots"]:
        return False, None
    matches = [
        entry
        for entry in policy["nonCriticalPaths"]
        if _glob_matches(path, entry["glob"])
    ]
    if len(matches) > 1:
        raise GateInputError(f"multiple non-critical path approvals match: {path}")
    if matches:
        return False, None
    language = next(
        (
            language
            for suffix, language in policy["languageSuffixes"].items()
            if path.endswith(suffix)
        ),
        None,
    )
    return True, language


def correctness_policy_identity(
    policy: Mapping[str, Any], *, path: str, sha256: str
) -> dict[str, str]:
    validate_correctness_policy(policy)
    path = _repository_path(path, "correctness policy path")
    if not _SHA256.fullmatch(sha256):
        raise GateInputError("correctness policy digest is malformed")
    return {"path": path, "sha256": sha256}
