"""Resolve an independent, reviewable inventory of coverage source files."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .changed_coverage import GateInputError, validate_normalized_report


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LANGUAGES = {"java", "python"}
_MAX_POLICY_BYTES = 1024 * 1024
_MAX_SOURCE_BYTES = 4 * 1024 * 1024


def _inventory_projection(inventory: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": inventory.get("schemaVersion"),
        "policyPath": inventory.get("policyPath"),
        "policySha256": inventory.get("policySha256"),
        "sources": inventory.get("sources"),
    }


def _canonical_inventory_digest(inventory: Mapping[str, Any]) -> str:
    try:
        raw = json.dumps(
            _inventory_projection(inventory),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise GateInputError("resolved source inventory cannot be canonically hashed") from error
    return hashlib.sha256(raw).hexdigest()


def _repository_path(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise GateInputError(f"{field} must be a repository-relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or value == "."
        or path.as_posix() != value
    ):
        raise GateInputError(f"{field} must be a repository-relative path")
    return value


def _open_repository_root(repository_root: Path) -> int:
    root = Path(os.path.abspath(repository_root))
    try:
        return os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
    except OSError as error:
        raise GateInputError("source inventory repository root is not trusted") from error


def _descriptor_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_mode


def _assert_repository_root_stable(repository_root: Path, descriptor: int) -> None:
    reopened = _open_repository_root(repository_root)
    try:
        if _descriptor_identity(os.fstat(descriptor)) != _descriptor_identity(
            os.fstat(reopened)
        ):
            raise GateInputError("source inventory repository root changed during resolution")
    finally:
        os.close(reopened)


def _open_directory_at(root_descriptor: int, path: str, field: str) -> int:
    current = os.dup(root_descriptor)
    try:
        for component in PurePosixPath(path).parts:
            next_descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = next_descriptor
        return current
    except OSError as error:
        try:
            os.close(current)
        except OSError:
            pass
        raise GateInputError(f"{field} is not a trusted directory: {path}") from error


def _read_open_file(descriptor: int, *, field: str, size_limit: int) -> bytes:
    initial = os.fstat(descriptor)
    if not stat.S_ISREG(initial.st_mode):
        raise GateInputError(f"{field} must be a regular file")
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            final = os.fstat(descriptor)
            identity = lambda value: (
                value.st_dev,
                value.st_ino,
                value.st_mode,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )
            if identity(initial) != identity(final):
                raise GateInputError(f"{field} changed while it was read")
            return b"".join(chunks)
        size += len(chunk)
        if size > size_limit:
            raise GateInputError(f"{field} exceeds the size limit")
        chunks.append(chunk)


def _read_file_at(
    root_descriptor: int,
    path: str,
    *,
    field: str,
    size_limit: int,
) -> bytes:
    parts = PurePosixPath(path).parts
    directory = os.dup(root_descriptor)
    descriptors = [directory]
    try:
        for component in parts[:-1]:
            directory = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=directory,
            )
            descriptors.append(directory)
        descriptor = os.open(
            parts[-1],
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        descriptors.append(descriptor)
        return _read_open_file(descriptor, field=field, size_limit=size_limit)
    except GateInputError:
        raise
    except OSError as error:
        raise GateInputError(f"{field} is not a trusted regular file: {path}") from error
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_explicit_source_stable(root_descriptor: int, path: str) -> bytes:
    """Read one policy-listed source while proving its path stayed bound."""

    parts = PurePosixPath(path).parts
    parent_path = "/".join(parts[:-1])
    parent = (
        _open_directory_at(root_descriptor, parent_path, "source inventory file parent")
        if parent_path
        else os.dup(root_descriptor)
    )
    try:
        initial_parent = os.fstat(parent)
        initial_names = sorted(os.listdir(parent))
        try:
            initial_file = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            descriptor = os.open(
                parts[-1],
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
        except OSError as error:
            raise GateInputError(
                f"source inventory file is not a trusted regular file: {path}"
            ) from error
        try:
            opened_file = os.fstat(descriptor)
            if (
                _descriptor_identity(initial_file)
                != _descriptor_identity(opened_file)
                or not stat.S_ISREG(opened_file.st_mode)
            ):
                raise GateInputError(
                    f"source inventory file changed during scan: {path}"
                )
            raw = _read_open_file(
                descriptor,
                field=f"source inventory file {path}",
                size_limit=_MAX_SOURCE_BYTES,
            )
        finally:
            os.close(descriptor)

        try:
            final_names = sorted(os.listdir(parent))
            final_parent = os.fstat(parent)
            final_file = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            reopened_file = os.open(
                parts[-1],
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
        except OSError as error:
            raise GateInputError(
                f"source inventory file changed during scan: {path}"
            ) from error
        try:
            reopened_metadata = os.fstat(reopened_file)
            file_identity = lambda value: (
                value.st_dev,
                value.st_ino,
                value.st_mode,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )
            if (
                initial_names != final_names
                or _descriptor_identity(initial_parent)
                != _descriptor_identity(final_parent)
                or initial_parent.st_mtime_ns != final_parent.st_mtime_ns
                or initial_parent.st_ctime_ns != final_parent.st_ctime_ns
                or file_identity(initial_file) != file_identity(final_file)
                or file_identity(initial_file) != file_identity(reopened_metadata)
            ):
                raise GateInputError(
                    f"source inventory file changed during scan: {path}"
                )
        finally:
            os.close(reopened_file)
    finally:
        os.close(parent)

    reopened_parent = (
        _open_directory_at(root_descriptor, parent_path, "source inventory file parent")
        if parent_path
        else os.dup(root_descriptor)
    )
    try:
        if _descriptor_identity(initial_parent) != _descriptor_identity(
            os.fstat(reopened_parent)
        ):
            raise GateInputError(
                f"source inventory file parent changed during scan: {path}"
            )
    finally:
        os.close(reopened_parent)
    return raw


def _scan_root(
    root_descriptor: int,
    relative_root: str,
    suffix: str,
    excluded_trees: set[str],
) -> dict[str, str]:
    source_root = _open_directory_at(
        root_descriptor, relative_root, "source inventory root"
    )
    result: dict[str, str] = {}

    def walk(directory: int, prefix: str) -> None:
        initial_directory = os.fstat(directory)
        try:
            names = sorted(os.listdir(directory))
        except OSError as error:
            raise GateInputError(f"source inventory directory is unreadable: {prefix}") from error
        for name in names:
            if "/" in name or name in {".", ".."}:
                raise GateInputError("source inventory directory contains an unsafe name")
            path = f"{prefix}/{name}"
            try:
                metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except OSError as error:
                raise GateInputError(f"source inventory entry changed during scan: {path}") from error
            if stat.S_ISLNK(metadata.st_mode):
                raise GateInputError(f"source inventory cannot traverse a symlink: {path}")
            if stat.S_ISDIR(metadata.st_mode):
                if path in excluded_trees:
                    continue
                try:
                    child = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                        dir_fd=directory,
                    )
                except OSError as error:
                    raise GateInputError(
                        f"source inventory directory changed during scan: {path}"
                    ) from error
                try:
                    if _descriptor_identity(metadata) != _descriptor_identity(
                        os.fstat(child)
                    ):
                        raise GateInputError(
                            f"source inventory directory changed during scan: {path}"
                        )
                    walk(child, path)
                finally:
                    os.close(child)
                continue
            if not name.endswith(suffix):
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise GateInputError(f"source inventory file must be regular: {path}")
            try:
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=directory,
                )
            except OSError as error:
                raise GateInputError(
                    f"source inventory file changed during scan: {path}"
                ) from error
            try:
                opened_metadata = os.fstat(descriptor)
                if (
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_mode,
                ) != (
                    opened_metadata.st_dev,
                    opened_metadata.st_ino,
                    opened_metadata.st_mode,
                ):
                    raise GateInputError(
                        f"source inventory file changed during scan: {path}"
                    )
                raw = _read_open_file(
                    descriptor,
                    field=f"source inventory file {path}",
                    size_limit=_MAX_SOURCE_BYTES,
                )
            finally:
                os.close(descriptor)
            result[path] = hashlib.sha256(raw).hexdigest()
        try:
            final_names = sorted(os.listdir(directory))
            final_directory = os.fstat(directory)
        except OSError as error:
            raise GateInputError(
                f"source inventory directory changed during scan: {prefix}"
            ) from error
        if (
            final_names != names
            or _descriptor_identity(initial_directory)
            != _descriptor_identity(final_directory)
            or initial_directory.st_mtime_ns != final_directory.st_mtime_ns
            or initial_directory.st_ctime_ns != final_directory.st_ctime_ns
        ):
            raise GateInputError(f"source inventory directory changed during scan: {prefix}")

    initial_source_identity = _descriptor_identity(os.fstat(source_root))
    try:
        walk(source_root, relative_root)
    finally:
        os.close(source_root)
    reopened_source = _open_directory_at(
        root_descriptor, relative_root, "source inventory root"
    )
    try:
        if initial_source_identity != _descriptor_identity(os.fstat(reopened_source)):
            raise GateInputError(
                f"source inventory root changed during scan: {relative_root}"
            )
    finally:
        os.close(reopened_source)
    return result


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GateInputError(f"duplicate source inventory policy key: {key}")
        result[key] = value
    return result


def _parse_policy(raw: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                GateInputError(f"invalid source inventory JSON constant: {constant}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateInputError("source inventory policy is malformed JSON") from error
    if not isinstance(value, Mapping):
        raise GateInputError("source inventory policy must be an object")
    return value


def _resolve_source_inventory(
    policy: Mapping[str, Any],
    *,
    policy_sha256: str,
    policy_path: str,
    root_descriptor: int,
) -> dict[str, Any]:
    """Resolve every source from policy roots without consulting coverage reports."""

    if not _SHA256.fullmatch(policy_sha256):
        raise GateInputError("source inventory policy digest is malformed")
    policy_path = _repository_path(policy_path, "source inventory policy path")
    if set(policy) != {
        "schemaVersion",
        "roots",
        "files",
        "excludedSourceTrees",
        "nonExecutableSources",
    }:
        raise GateInputError("source inventory policy contract is malformed")
    roots = policy.get("roots")
    files = policy.get("files")
    excluded_source_trees = policy.get("excludedSourceTrees")
    non_executable = policy.get("nonExecutableSources")
    if (
        policy.get("schemaVersion") != 1
        or not isinstance(roots, list)
        or not roots
        or not isinstance(files, list)
        or not isinstance(excluded_source_trees, list)
        or not isinstance(non_executable, list)
    ):
        raise GateInputError("source inventory policy contract is malformed")

    excluded_trees: set[str] = set()
    for entry in excluded_source_trees:
        if not isinstance(entry, Mapping) or set(entry) != {
            "path",
            "reason",
            "owner",
            "reviewer",
        }:
            raise GateInputError("excluded source tree approval is malformed")
        path = _repository_path(entry.get("path"), "excluded source tree")
        reason = entry.get("reason")
        owner = entry.get("owner")
        reviewer = entry.get("reviewer")
        try:
            tree_descriptor = _open_directory_at(
                root_descriptor, path, "excluded source tree"
            )
        except GateInputError as error:
            raise GateInputError("excluded source tree approval is malformed") from error
        else:
            os.close(tree_descriptor)
        if (
            path in excluded_trees
            or any(
                path.startswith(existing + "/") or existing.startswith(path + "/")
                for existing in excluded_trees
            )
            or not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(owner, str)
            or not owner.strip()
            or not isinstance(reviewer, str)
            or not reviewer.strip()
            or owner == reviewer
        ):
            raise GateInputError("excluded source tree approval is malformed")
        excluded_trees.add(path)

    discovered: dict[str, tuple[str, str, str]] = {}
    root_identities: set[tuple[str, str, str, str]] = set()
    root_paths: set[str] = set()
    root_specs: list[tuple[str, str, str, set[str]]] = []
    used_excluded_trees: set[str] = set()
    for entry in roots:
        if not isinstance(entry, Mapping) or set(entry) != {
            "language",
            "module",
            "root",
            "suffix",
        }:
            raise GateInputError("source inventory root entry is malformed")
        language = entry.get("language")
        module = entry.get("module")
        relative_root = _repository_path(entry.get("root"), "source inventory root")
        suffix = entry.get("suffix")
        identity = (str(language), str(module), relative_root, str(suffix))
        if (
            language not in _LANGUAGES
            or not isinstance(module, str)
            or not module
            or suffix not in {".java", ".py"}
            or suffix != {"java": ".java", "python": ".py"}.get(language)
            or identity in root_identities
            or any(
                relative_root.startswith(existing + "/")
                or existing.startswith(relative_root + "/")
                or relative_root == existing
                for existing in root_paths
            )
        ):
            raise GateInputError("source inventory root entry is malformed")
        root_identities.add(identity)
        root_paths.add(relative_root)
        applicable_exclusions = {
            path
            for path in excluded_trees
            if path.startswith(relative_root + "/")
        }
        used_excluded_trees.update(applicable_exclusions)
        root_specs.append((language, relative_root, suffix, applicable_exclusions))
        scanned = _scan_root(
            root_descriptor, relative_root, suffix, applicable_exclusions
        )
        for path, digest in scanned.items():
            if path in discovered:
                raise GateInputError(f"source inventory path has multiple owners: {path}")
            discovered[path] = (language, module, digest)

    if used_excluded_trees != excluded_trees:
        raise GateInputError("excluded source tree is not owned by exactly one source root")

    explicit_specs: list[tuple[str, str, str]] = []
    for entry in files:
        if not isinstance(entry, Mapping) or set(entry) != {"language", "module", "path"}:
            raise GateInputError("source inventory file entry is malformed")
        language = entry.get("language")
        module = entry.get("module")
        path = _repository_path(entry.get("path"), "source inventory file")
        if language not in _LANGUAGES or not isinstance(module, str) or not module:
            raise GateInputError("source inventory file entry is malformed")
        if not path.endswith({"java": ".java", "python": ".py"}[language]):
            raise GateInputError("source inventory file entry is malformed")
        raw = _read_explicit_source_stable(root_descriptor, path)
        if path in discovered:
            raise GateInputError(f"source inventory path has multiple owners: {path}")
        discovered[path] = (language, module, hashlib.sha256(raw).hexdigest())
        explicit_specs.append((language, module, path))

    rechecked: dict[str, tuple[str, str, str]] = {}
    module_by_root = {
        (language, relative_root): next(
            entry["module"]
            for entry in roots
            if entry["language"] == language and entry["root"] == relative_root
        )
        for language, relative_root, _, _ in root_specs
    }
    for language, relative_root, suffix, applicable_exclusions in root_specs:
        module = module_by_root[(language, relative_root)]
        for path, digest in _scan_root(
            root_descriptor, relative_root, suffix, applicable_exclusions
        ).items():
            if path in rechecked:
                raise GateInputError(f"source inventory path has multiple owners: {path}")
            rechecked[path] = (language, module, digest)
    for language, module, path in explicit_specs:
        raw = _read_explicit_source_stable(root_descriptor, path)
        if path in rechecked:
            raise GateInputError(f"source inventory path has multiple owners: {path}")
        rechecked[path] = (language, module, hashlib.sha256(raw).hexdigest())
    if rechecked != discovered:
        raise GateInputError("source inventory changed between complete scans")

    non_executable_paths: set[str] = set()
    for entry in non_executable:
        if not isinstance(entry, Mapping) or set(entry) != {
            "path",
            "reason",
            "owner",
            "reviewer",
        }:
            raise GateInputError("non-executable source approval is malformed")
        path = _repository_path(entry.get("path"), "non-executable source")
        reason = entry.get("reason")
        owner = entry.get("owner")
        reviewer = entry.get("reviewer")
        if (
            path in non_executable_paths
            or path not in discovered
            or not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(owner, str)
            or not owner.strip()
            or not isinstance(reviewer, str)
            or not reviewer.strip()
            or owner == reviewer
        ):
            raise GateInputError("non-executable source approval is malformed")
        non_executable_paths.add(path)

    if not discovered:
        raise GateInputError("source inventory resolved no source files")
    sources = []
    for path, (language, module, digest) in sorted(discovered.items()):
        sources.append(
            {
                "path": path,
                "language": language,
                "module": module,
                "coverageDisposition": (
                    "nonExecutable" if path in non_executable_paths else "required"
                ),
                "sha256": digest,
            }
        )
    result = {
        "schemaVersion": 1,
        "policyPath": policy_path,
        "policySha256": policy_sha256,
        "sources": sources,
    }
    result["inventorySha256"] = _canonical_inventory_digest(result)
    return result


def resolve_source_inventory(
    policy: Mapping[str, Any],
    *,
    policy_sha256: str,
    repository_root: Path,
    policy_path: str = "policy/source-inventory-policy-v1.json",
) -> dict[str, Any]:
    """Resolve an already parsed policy through one stable repository root FD."""

    root_descriptor = _open_repository_root(repository_root)
    try:
        result = _resolve_source_inventory(
            policy,
            policy_sha256=policy_sha256,
            policy_path=policy_path,
            root_descriptor=root_descriptor,
        )
        _assert_repository_root_stable(repository_root, root_descriptor)
        return result
    finally:
        os.close(root_descriptor)


def load_and_resolve_source_inventory(
    policy_path: Path, *, repository_root: Path
) -> dict[str, Any]:
    """Read exact policy/source bytes with no-follow FDs and resolve their inventory."""

    repository = Path(os.path.abspath(repository_root))
    candidate_input = policy_path if policy_path.is_absolute() else repository / policy_path
    candidate = Path(os.path.abspath(candidate_input))
    try:
        relative_path = candidate.relative_to(repository).as_posix()
    except ValueError as error:
        raise GateInputError("source inventory policy must stay inside the repository") from error
    relative_path = _repository_path(relative_path, "source inventory policy path")
    root_descriptor = _open_repository_root(repository)
    try:
        raw = _read_file_at(
            root_descriptor,
            relative_path,
            field="source inventory policy",
            size_limit=_MAX_POLICY_BYTES,
        )
        result = _resolve_source_inventory(
            _parse_policy(raw),
            policy_sha256=hashlib.sha256(raw).hexdigest(),
            policy_path=relative_path,
            root_descriptor=root_descriptor,
        )
        _assert_repository_root_stable(repository, root_descriptor)
        return result
    finally:
        os.close(root_descriptor)


def validate_source_inventory(inventory: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Validate a resolved inventory and return its exact path mapping."""

    if set(inventory) != {
        "schemaVersion",
        "policyPath",
        "policySha256",
        "inventorySha256",
        "sources",
    }:
        raise GateInputError("resolved source inventory contract is malformed")
    sources = inventory.get("sources")
    if (
        inventory.get("schemaVersion") != 1
        or not isinstance(inventory.get("policyPath"), str)
        or _repository_path(inventory.get("policyPath"), "source inventory policy path")
        != inventory["policyPath"]
        or not isinstance(inventory.get("policySha256"), str)
        or not _SHA256.fullmatch(inventory["policySha256"])
        or not isinstance(inventory.get("inventorySha256"), str)
        or not _SHA256.fullmatch(inventory["inventorySha256"])
        or not isinstance(sources, list)
        or not sources
    ):
        raise GateInputError("resolved source inventory contract is malformed")
    result: dict[str, Mapping[str, Any]] = {}
    previous = ""
    for entry in sources:
        if not isinstance(entry, Mapping) or set(entry) != {
            "path",
            "language",
            "module",
            "coverageDisposition",
            "sha256",
        }:
            raise GateInputError("resolved source inventory entry is malformed")
        path = _repository_path(entry.get("path"), "resolved source inventory path")
        if (
            path <= previous
            or entry.get("language") not in _LANGUAGES
            or not isinstance(entry.get("module"), str)
            or not entry["module"]
            or entry.get("coverageDisposition") not in {"required", "nonExecutable"}
            or not isinstance(entry.get("sha256"), str)
            or not _SHA256.fullmatch(entry["sha256"])
        ):
            raise GateInputError("resolved source inventory entry is malformed or unsorted")
        previous = path
        result[path] = entry
    if inventory["inventorySha256"] != _canonical_inventory_digest(inventory):
        raise GateInputError("resolved source inventory digest mismatch")
    return result


def source_inventory_digest(inventory: Mapping[str, Any]) -> str:
    """Return the self-verified semantic identity of one resolved inventory."""

    validate_source_inventory(inventory)
    return inventory["inventorySha256"]


def reconcile_reports_with_inventory(
    reports: Sequence[Mapping[str, Any]],
    inventory: Mapping[str, Any],
    *,
    require_aggregates: bool = False,
) -> dict[str, Mapping[str, Any]]:
    """Require every executable source exactly once in its declared module report."""

    inventory_by_path = validate_source_inventory(inventory)
    required = {
        path: entry
        for path, entry in inventory_by_path.items()
        if entry["coverageDisposition"] == "required"
    }
    reported: dict[str, Mapping[str, Any]] = {}
    seen_modules: set[tuple[str, str]] = set()
    aggregates: dict[str, Mapping[str, Any]] = {}
    for report in reports:
        validate_normalized_report(report)
        language = report.get("language")
        module = report.get("module")
        if module == "@repository":
            if language in aggregates:
                raise GateInputError(f"duplicate repository aggregate: {language}")
            aggregates[language] = report
            continue
        identity = (language, module)
        if identity in seen_modules:
            raise GateInputError(f"duplicate inventory report module: {language}:{module}")
        seen_modules.add(identity)
        for path, file_report in report["files"].items():
            source = required.get(path)
            if source is None:
                raise GateInputError(f"coverage report contains an unowned source: {path}")
            if source["language"] != language or source["module"] != module:
                raise GateInputError(f"coverage source is reported by the wrong module: {path}")
            if path in reported:
                raise GateInputError(f"coverage source is reported more than once: {path}")
            reported[path] = file_report
    missing = sorted(set(required) - set(reported))
    if missing:
        raise GateInputError(f"coverage report omits required source: {missing[0]}")
    expected_modules = {
        (entry["language"], entry["module"])
        for entry in required.values()
    }
    if seen_modules != expected_modules:
        missing_modules = sorted(expected_modules - seen_modules)
        extra_modules = sorted(seen_modules - expected_modules)
        detail = missing_modules[0] if missing_modules else extra_modules[0]
        raise GateInputError(
            f"coverage report module inventory is not exact: {detail[0]}:{detail[1]}"
        )
    if require_aggregates:
        expected_languages = {entry["language"] for entry in required.values()}
        if set(aggregates) != expected_languages:
            raise GateInputError("repository aggregate language inventory is not exact")
        for language, aggregate in aggregates.items():
            module_union = {
                path: report
                for path, report in reported.items()
                if required[path]["language"] == language
            }
            if aggregate["files"] != dict(sorted(module_union.items())):
                raise GateInputError(
                    f"{language}:@repository does not exactly match module report files"
                )
    return reported
