"""Resolve exact changed paths from the P0-01 comparison commit."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from .changed_coverage import GateInputError
from .correctness_policy import classify_path, correctness_policy_identity
from .source_inventory import (
    _assert_repository_root_stable,
    _open_repository_root,
    _read_file_at,
    _repository_path,
)


_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_HUNK = re.compile(rb"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
_MAX_CHANGED_FILE_BYTES = 16 * 1024 * 1024
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_ATTESTATION_BYTES = 1024 * 1024


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GateInputError(f"duplicate comparison-base key: {key}")
        result[key] = value
    return result


def _parse_contract_json(raw: bytes, field: str) -> Any:
    try:
        return json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                GateInputError(f"invalid JSON constant in {field}: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateInputError(f"{field} is malformed") from error


def _read_contract_file(
    path: Path,
    *,
    repository_root: Path | None,
    field: str,
    size_limit: int,
) -> bytes:
    if repository_root is None:
        absolute_path = Path(os.path.abspath(path))
        repository_root = absolute_path.parent
        path = Path(absolute_path.name)

    root = Path(os.path.abspath(repository_root))
    absolute_path = Path(os.path.abspath(path if path.is_absolute() else root / path))
    try:
        relative_path = absolute_path.relative_to(root).as_posix()
    except ValueError as error:
        raise GateInputError(f"{field} must be inside the repository") from error
    relative_path = _repository_path(relative_path, field)
    root_descriptor = _open_repository_root(root)
    try:
        raw = _read_file_at(
            root_descriptor,
            relative_path,
            field=field,
            size_limit=size_limit,
        )
        _assert_repository_root_stable(root, root_descriptor)
        return raw
    finally:
        os.close(root_descriptor)


def load_comparison_base(
    manifest_path: Path,
    *,
    expected_sha256: str,
    repository_id: str = "codecrow-public",
    repository_root: Path | None = None,
) -> str:
    """Return only the revision from the byte-exact verified baseline manifest."""

    raw = _read_contract_file(
        manifest_path,
        repository_root=repository_root,
        field="baseline manifest",
        size_limit=_MAX_MANIFEST_BYTES,
    )
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise GateInputError("baseline manifest digest mismatch")
    try:
        manifest = _parse_contract_json(raw, "baseline manifest")
    except GateInputError:
        raise
    repositories = manifest.get("repositories") if isinstance(manifest, dict) else None
    if not isinstance(repositories, list):
        raise GateInputError("baseline manifest has no repository inventory")
    matches = [
        repository
        for repository in repositories
        if isinstance(repository, dict) and repository.get("id") == repository_id
    ]
    if len(matches) != 1:
        raise GateInputError("repository missing from baseline manifest")
    commit = matches[0].get("headCommit")
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise GateInputError("baseline repository commit is malformed")
    return commit


def load_comparison_base_attestation(
    attestation_path: Path,
    *,
    expected_attestation_sha256: str,
    expected_manifest_sha256: str,
    repository_id: str = "codecrow-public",
    with_dirty_state: bool = False,
    repository_root: Path | None = None,
) -> str | tuple[str, tuple[Mapping[str, str], ...]]:
    """Load a tracked extraction that remains tied to the exact P0-01 artifact."""

    raw = _read_contract_file(
        attestation_path,
        repository_root=repository_root,
        field="comparison-base attestation",
        size_limit=_MAX_ATTESTATION_BYTES,
    )
    if hashlib.sha256(raw).hexdigest() != expected_attestation_sha256:
        raise GateInputError("comparison-base attestation digest mismatch")
    try:
        attestation = _parse_contract_json(raw, "comparison-base attestation")
    except GateInputError:
        raise
    if (
        not isinstance(attestation, dict)
        or set(attestation) != {"schemaVersion", "source", "repository"}
        or attestation.get("schemaVersion") != 1
    ):
        raise GateInputError("comparison-base attestation schema is malformed")
    source = attestation.get("source")
    repository = attestation.get("repository")
    if (
        not isinstance(source, dict)
        or set(source) != {
            "taskId",
            "artifact",
            "manifestSha256",
        }
        or source.get("taskId") != "P0-01"
        or source.get("artifact")
        != ".llm-handoff-artifacts/p0-01/baseline-manifest.json"
        or not isinstance(source.get("manifestSha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", source["manifestSha256"])
    ):
        raise GateInputError("comparison-base attestation source is malformed")
    if source.get("manifestSha256") != expected_manifest_sha256:
        raise GateInputError("P0-01 manifest digest mismatch")
    if (
        not isinstance(repository, dict)
        or set(repository) not in (
            {"id", "headCommit"},
            {"id", "headCommit", "dirtyState"},
        )
        or repository.get("id") != repository_id
    ):
        raise GateInputError("repository missing from comparison-base attestation")
    commit = repository.get("headCommit")
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise GateInputError("attested repository commit is malformed")
    dirty_state = repository.get("dirtyState")
    if dirty_state is None:
        if with_dirty_state:
            raise GateInputError("comparison-base dirty state is missing")
        return commit
    if (
        not isinstance(dirty_state, Mapping)
        or set(dirty_state) != {"captured", "entries"}
        or dirty_state.get("captured") is not True
        or not isinstance(dirty_state.get("entries"), list)
    ):
        raise GateInputError("comparison-base dirty state is malformed")
    entries: list[Mapping[str, str]] = []
    seen_paths: set[str] = set()
    for entry in dirty_state["entries"]:
        if not isinstance(entry, Mapping) or set(entry) != {
            "path",
            "status",
            "contentSha256",
        }:
            raise GateInputError("comparison-base dirty entry is malformed")
        raw_path = entry.get("path")
        if not isinstance(raw_path, str):
            raise GateInputError("comparison-base dirty entry is malformed")
        path = _repository_path(raw_path, "comparison-base dirty path")
        status = entry.get("status")
        digest = entry.get("contentSha256")
        if (
            path in seen_paths
            or status not in {" M", "??"}
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise GateInputError("comparison-base dirty entry is malformed")
        seen_paths.add(path)
        entries.append(
            {"path": path, "status": status, "contentSha256": digest}
        )
    if not entries:
        raise GateInputError("comparison-base dirty state is empty")
    if [entry["path"] for entry in entries] != sorted(seen_paths):
        raise GateInputError("comparison-base dirty entries must be path-sorted")
    if with_dirty_state:
        return commit, tuple(entries)
    return commit


def _git(repo: Path, *args: str, check: bool = True) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GateInputError(f"git command failed: {detail or 'unknown error'}")
    return result.stdout


def _decode_path(value: bytes) -> str:
    try:
        path = value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GateInputError("changed path is not valid UTF-8") from error
    try:
        return _repository_path(path, "changed path")
    except GateInputError as error:
        raise GateInputError("changed path escapes the repository") from error


def _name_status(raw: bytes) -> list[tuple[str, str | None, str]]:
    tokens = raw.rstrip(b"\0").split(b"\0") if raw else []
    result: list[tuple[str, str | None, str]] = []
    index = 0
    while index < len(tokens):
        status_token = tokens[index].decode("ascii", errors="strict")
        index += 1
        code = status_token[:1]
        if code in {"R", "C"}:
            if index + 1 >= len(tokens):
                raise GateInputError("malformed Git rename/copy inventory")
            old_path = _decode_path(tokens[index])
            path = _decode_path(tokens[index + 1])
            index += 2
        else:
            if index >= len(tokens):
                raise GateInputError("malformed Git change inventory")
            old_path = None
            path = _decode_path(tokens[index])
            index += 1
        if code not in {"A", "M", "D", "R", "C", "T"}:
            raise GateInputError(f"unsupported Git change status: {status_token}")
        result.append((status_token, old_path, path))
    return result


def _porcelain_status(raw: bytes) -> dict[str, str]:
    tokens = raw.rstrip(b"\0").split(b"\0") if raw else []
    result: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if len(token) < 4 or token[2:3] != b" ":
            raise GateInputError("Git porcelain status is malformed")
        try:
            status = token[:2].decode("ascii")
        except UnicodeDecodeError as error:
            raise GateInputError("Git porcelain status is malformed") from error
        path = _decode_path(token[3:])
        if path in result:
            raise GateInputError(f"duplicate Git porcelain path: {path}")
        result[path] = status
        if status[:1] in {"R", "C"} or status[1:] in {"R", "C"}:
            if index >= len(tokens):
                raise GateInputError("Git porcelain rename is malformed")
            _decode_path(tokens[index])
            index += 1
    return result


def _baseline_dirty_digest(entries: Sequence[Mapping[str, str]]) -> str:
    return hashlib.sha256(
        json.dumps(
            list(entries), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()


def _validate_and_subtract_baseline_dirty(
    repo: Path,
    root_descriptor: int,
    entries: list[dict[str, Any]],
    baseline_dirty_entries: Sequence[Mapping[str, str]],
) -> tuple[list[dict[str, Any]], str]:
    status_by_path = _porcelain_status(
        _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    )
    baseline_paths: set[str] = set()
    normalized_entries: list[Mapping[str, str]] = []
    for entry in baseline_dirty_entries:
        if not isinstance(entry, Mapping) or set(entry) != {
            "path",
            "status",
            "contentSha256",
        }:
            raise GateInputError("comparison-base dirty entry is malformed")
        raw_path = entry.get("path")
        if not isinstance(raw_path, str):
            raise GateInputError("comparison-base dirty entry is malformed")
        path = _repository_path(raw_path, "comparison-base dirty path")
        status = entry.get("status")
        digest = entry.get("contentSha256")
        if (
            path in baseline_paths
            or status_by_path.get(path) != status
            or status not in {" M", "??"}
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or hashlib.sha256(_current_file_bytes(root_descriptor, path)).hexdigest()
            != digest
        ):
            raise GateInputError(f"comparison-base dirty entry drifted: {path}")
        baseline_paths.add(path)
        normalized_entries.append(
            {"path": path, "status": status, "contentSha256": digest}
        )
    if not baseline_paths:
        raise GateInputError("comparison-base dirty state is empty")
    if [entry["path"] for entry in normalized_entries] != sorted(baseline_paths):
        raise GateInputError("comparison-base dirty entries must be path-sorted")
    entry_paths = {entry["path"] for entry in entries}
    if not baseline_paths <= entry_paths:
        missing = sorted(baseline_paths - entry_paths)[0]
        raise GateInputError(f"comparison-base dirty entry is not replayed: {missing}")
    return (
        [entry for entry in entries if entry["path"] not in baseline_paths],
        _baseline_dirty_digest(normalized_entries),
    )


def _classification(
    path: str, policy: Mapping[str, Any] | None = None
) -> tuple[bool, str | None]:
    if policy is not None:
        return classify_path(path, policy)
    pure = PurePosixPath(path)
    parts = pure.parts
    if (
        path.startswith("java-ecosystem/")
        and "/src/main/java/" in path
        and path.endswith(".java")
    ):
        return True, "java"
    if (
        path.startswith("python-ecosystem/")
        and "/src/" in path
        and "/src/tests/" not in path
        and path.endswith(".py")
    ):
        return True, "python"
    if path == "python-ecosystem/rag-pipeline/main.py":
        return True, "python"
    if (
        len(parts) >= 4
        and parts[:3] == ("tools", "quality-gates", "quality_gates")
        and path.endswith(".py")
    ):
        return True, "python"
    return False, None


def _changed_lines(diff: bytes) -> list[int]:
    lines: set[int] = set()
    for match in _HUNK.finditer(diff):
        start = int(match.group(1))
        count = int(match.group(2) or b"1")
        lines.update(range(start, start + count))
    return sorted(lines)


def _current_file_bytes(root_descriptor: int, path: str) -> bytes:
    return _read_file_at(
        root_descriptor,
        path,
        field="changed repository file",
        size_limit=_MAX_CHANGED_FILE_BYTES,
    )


def _all_lines(root_descriptor: int, path: str) -> list[int]:
    try:
        count = len(_current_file_bytes(root_descriptor, path).decode("utf-8").splitlines())
    except UnicodeDecodeError as error:
        raise GateInputError(f"changed correctness source is not UTF-8: {path}") from error
    return list(range(1, count + 1))


def _git_blob_sha256(repo: Path, revision: str, path: str) -> str:
    object_name = f"{revision}:{path}"
    size_raw = _git(repo, "cat-file", "-s", object_name)
    try:
        size = int(size_raw)
    except ValueError as error:
        raise GateInputError(f"changed previous source is unavailable: {path}") from error
    if size < 0 or size > _MAX_CHANGED_FILE_BYTES:
        raise GateInputError(f"changed previous source exceeds the size limit: {path}")
    raw = _git(repo, "cat-file", "blob", object_name)
    if len(raw) != size:
        raise GateInputError(f"changed previous source size drifted: {path}")
    return hashlib.sha256(raw).hexdigest()


def _diff_for_path(repo: Path, left: str, right: str | None, path: str) -> bytes:
    args = [
        "diff",
        "--unified=0",
        "--no-color",
        "--no-ext-diff",
        "--find-renames",
        "--find-copies-harder",
        left,
    ]
    if right is not None:
        args.append(right)
    args.extend(["--", path])
    return _git(repo, *args)


def _entry(
    repo: Path,
    *,
    status_token: str,
    old_path: str | None,
    path: str,
    left: str,
    right: str | None,
    correctness_policy: Mapping[str, Any] | None = None,
    root_descriptor: int,
) -> dict[str, Any]:
    code = status_token[:1]
    status = {
        "A": "added",
        "M": "modified",
        "D": "deleted",
        "R": "renamed",
        "C": "copied",
        "T": "type_changed",
    }[code]
    critical, language = _classification(path, correctness_policy)
    if old_path is not None:
        old_critical, old_language = _classification(old_path, correctness_policy)
        if old_critical:
            critical = True
            languages = {
                value for value in (language, old_language) if value is not None
            }
            language = next(iter(languages)) if len(languages) == 1 else None
    if status in {"added", "copied"} and critical and language in {"java", "python"}:
        changed_lines = _all_lines(root_descriptor, path)
    elif status == "renamed" and status_token == "R100":
        changed_lines = []
    elif status == "deleted":
        changed_lines = []
    else:
        changed_lines = _changed_lines(_diff_for_path(repo, left, right, path))
    result: dict[str, Any] = {"path": path}
    if old_path is not None:
        result["oldPath"] = old_path
    result.update(
        {
            "status": status,
            "correctnessCritical": critical,
            "language": language,
            "changedLines": changed_lines,
        }
    )
    if status != "deleted":
        result["contentSha256"] = hashlib.sha256(
            _current_file_bytes(root_descriptor, path)
        ).hexdigest()
    if status != "added":
        result["previousContentSha256"] = _git_blob_sha256(
            repo, left, old_path if old_path is not None else path
        )
    return result


def _merge_entries(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for entry in entries:
        existing = by_path.get(entry["path"])
        if existing is None:
            by_path[entry["path"]] = entry
            continue
        if entry["status"] == "deleted":
            by_path[entry["path"]] = entry
            continue
        if existing["status"] != "deleted":
            existing["changedLines"] = sorted(
                set(existing["changedLines"]) | set(entry["changedLines"])
            )
            if existing["status"] != "added":
                existing["status"] = entry["status"]
            continue
        by_path[entry["path"]] = entry
    return [by_path[path] for path in sorted(by_path)]


def resolve_git_changes(
    repository_root: Path,
    *,
    base_commit: str,
    include_worktree: bool = False,
    correctness_policy: Mapping[str, Any] | None = None,
    correctness_policy_path: str | None = None,
    correctness_policy_sha256: str | None = None,
    baseline_dirty_entries: Sequence[Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """Resolve a deterministic base-to-HEAD inventory, optionally overlaying worktree state."""

    repo = Path(os.path.abspath(repository_root))
    root_descriptor = _open_repository_root(repo)
    try:
        if not (repo / ".git").exists() or not _COMMIT.fullmatch(base_commit):
            raise GateInputError("repository or comparison base is malformed")
        shallow = _git(repo, "rev-parse", "--is-shallow-repository").decode().strip()
        if shallow != "false":
            raise GateInputError("changed coverage refuses a shallow repository")
        if subprocess.run(
            ["git", "cat-file", "-e", f"{base_commit}^{{commit}}"],
            cwd=repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode:
            raise GateInputError("comparison base commit is unavailable")
        head = _git(repo, "rev-parse", "HEAD").decode("ascii").strip()
        merge_base = _git(repo, "merge-base", base_commit, head).decode("ascii").strip()
        if merge_base != base_commit:
            raise GateInputError("comparison base is not an ancestor of HEAD")

        if baseline_dirty_entries is not None and not include_worktree:
            raise GateInputError("comparison-base dirty replay requires worktree resolution")
        baseline_dirty_digest: str | None = None
        if include_worktree:
            tracked = _name_status(
                _git(
                    repo,
                    "diff",
                    "--name-status",
                    "-z",
                    "--find-renames",
                    "--find-copies-harder",
                    base_commit,
                )
            )
            right: str | None = None
        else:
            tracked = _name_status(
                _git(
                    repo,
                    "diff",
                    "--name-status",
                    "-z",
                    "--find-renames",
                    "--find-copies-harder",
                    base_commit,
                    head,
                )
            )
            right = head
        entries = [
            _entry(
                repo,
                status_token=status_token,
                old_path=old_path,
                path=path,
                left=base_commit,
                right=right,
                correctness_policy=correctness_policy,
                root_descriptor=root_descriptor,
            )
            for status_token, old_path, path in tracked
        ]

        dirty = False
        if include_worktree:
            worktree = _name_status(
                _git(
                    repo,
                    "diff",
                    "--name-status",
                    "-z",
                    "--find-renames",
                    "--find-copies-harder",
                    head,
                )
            )
            dirty = bool(worktree)
            untracked_raw = _git(
                repo, "ls-files", "--others", "--exclude-standard", "-z"
            )
            untracked = [
                _decode_path(token)
                for token in untracked_raw.rstrip(b"\0").split(b"\0")
                if token
            ]
            dirty = dirty or bool(untracked)
            for path in untracked:
                critical, language = _classification(path, correctness_policy)
                raw = _current_file_bytes(root_descriptor, path)
                entries.append(
                    {
                        "path": path,
                        "status": "added",
                        "correctnessCritical": critical,
                        "language": language,
                        "changedLines": (
                            _all_lines(root_descriptor, path)
                            if critical and language in {"java", "python"}
                            else []
                        ),
                        "contentSha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
        if baseline_dirty_entries is not None:
            entries, baseline_dirty_digest = _validate_and_subtract_baseline_dirty(
                repo,
                root_descriptor,
                entries,
                baseline_dirty_entries,
            )

        result: dict[str, Any] = {
            "schemaVersion": 1,
            "baseCommit": base_commit,
            "headCommit": head,
            "mergeBase": merge_base,
            "dirty": dirty,
            "files": _merge_entries(entries),
        }
        if correctness_policy is not None:
            if correctness_policy_path is None or correctness_policy_sha256 is None:
                raise GateInputError("correctness policy identity is required")
            result["correctnessPolicy"] = correctness_policy_identity(
                correctness_policy,
                path=correctness_policy_path,
                sha256=correctness_policy_sha256,
            )
        if baseline_dirty_digest is not None:
            result["comparisonBaseDirtyStateSha256"] = baseline_dirty_digest
        _assert_repository_root_stable(repo, root_descriptor)
        return result
    finally:
        os.close(root_descriptor)
