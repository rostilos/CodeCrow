"""Host-owned proposed-tree snapshots, path handling and deterministic identity."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .generation_manifest import canonical_index_selection_policy
from .source_tree import RepositorySourceTreeError, read_repository_file_bytes

_REVIEW_GRAPH_COMPOSITION = "exact_policy_selected_proposed_tree"


class ProposedTreeUnavailableError(RuntimeError):
    """The overlay cannot represent the complete proposed tree exactly."""



@dataclass(frozen=True)
class ReviewOverlay:
    root: Path
    files_root: Path
    changed_paths: tuple[str, ...]
    deleted_paths: tuple[str, ...]
    file_sha256_by_path: Mapping[str, str]
    fingerprint: str



@dataclass(frozen=True)
class ProposedTreeGeneration:
    collection_target: str
    receipt: Mapping[str, Any]
    target_source_tree_sha256: str
    overlay_sha256: str
    proposed_source_tree_sha256: str
    representation_identity: str
    changed_paths: tuple[str, ...]
    deleted_paths: tuple[str, ...]
    cache_hit: bool



@dataclass(frozen=True)
class ProposedTreeReadSession:
    """One complete reader held open for one exact proposed-tree operation."""

    reader: Any
    generation: ProposedTreeGeneration



def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )



def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()



def _normalize_path(value: object) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    candidate = PurePosixPath(raw)
    if (
        not raw
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ProposedTreeUnavailableError(
            f"review overlay contains an invalid repository path: {value!r}"
        )
    return candidate.as_posix()



def _string_paths(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProposedTreeUnavailableError(
            f"review overlay manifest field {field} must be a path list"
        )
    return tuple(sorted({_normalize_path(item) for item in value}))



def load_review_overlay(path: str | Path) -> ReviewOverlay:
    """Load and attest the host-created proposed-file overlay."""
    root = Path(path).resolve()
    files_root = root / "files"
    try:
        if not root.is_dir() or root.is_symlink():
            raise ProposedTreeUnavailableError("review overlay root is unavailable")
        if not files_root.is_dir() or files_root.is_symlink():
            raise ProposedTreeUnavailableError("review overlay files root is unavailable")
        manifest = json.loads(
            read_repository_file_bytes(root, "manifest.json").decode("utf-8")
        )
    except ProposedTreeUnavailableError:
        raise
    except (OSError, ValueError, TypeError, RepositorySourceTreeError) as exception:
        raise ProposedTreeUnavailableError(
            "review overlay manifest is unavailable or malformed"
        ) from exception
    if not isinstance(manifest, dict):
        raise ProposedTreeUnavailableError("review overlay manifest must be an object")

    changed_paths = _string_paths(manifest.get("changedFiles"), "changedFiles")
    deleted_paths = _string_paths(manifest.get("deletedFiles"), "deletedFiles")
    changed = set(changed_paths)
    deleted = set(deleted_paths)
    changed.update(deleted)
    changed_paths = tuple(sorted(changed))

    file_sha256_by_path: dict[str, str] = {}
    unavailable: list[str] = []
    for relative_path in changed_paths:
        if relative_path in deleted:
            continue
        candidate = files_root / relative_path
        try:
            candidate_stat = candidate.lstat()
            if not stat.S_ISREG(candidate_stat.st_mode):
                raise OSError("not a regular file")
            content = read_repository_file_bytes(files_root, relative_path)
        except (OSError, RuntimeError):
            unavailable.append(relative_path)
            continue
        file_sha256_by_path[relative_path] = hashlib.sha256(content).hexdigest()
    if unavailable:
        preview = ", ".join(unavailable[:5])
        suffix = "" if len(unavailable) <= 5 else f" (+{len(unavailable) - 5} more)"
        raise ProposedTreeUnavailableError(
            "exact proposed tree is unavailable because changed file bodies are "
            f"missing: {preview}{suffix}"
        )

    fingerprint = _sha256_json({
        "changedFiles": list(changed_paths),
        "deletedFiles": list(deleted_paths),
        "fileSha256ByPath": file_sha256_by_path,
    })
    return ReviewOverlay(
        root=root,
        files_root=files_root,
        changed_paths=changed_paths,
        deleted_paths=deleted_paths,
        file_sha256_by_path=file_sha256_by_path,
        fingerprint=fingerprint,
    )



def _destination_without_symlink_ancestor(root: Path, relative_path: str) -> Path:
    destination = root.joinpath(*PurePosixPath(relative_path).parts)
    current = root
    for component in PurePosixPath(relative_path).parts[:-1]:
        current = current / component
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            current.mkdir()
            continue
        if stat.S_ISLNK(current_stat.st_mode) or not stat.S_ISDIR(current_stat.st_mode):
            raise ProposedTreeUnavailableError(
                "proposed-tree path crosses a non-directory repository entry: "
                + relative_path
            )
    return destination



def _remove_destination(destination: Path) -> None:
    try:
        destination_stat = destination.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(destination_stat.st_mode) and not stat.S_ISLNK(destination_stat.st_mode):
        shutil.rmtree(destination)
    else:
        destination.unlink()



def _link_or_copy(source: str, destination: str) -> str:
    try:
        os.link(source, destination, follow_symlinks=False)
        return destination
    except OSError:
        return shutil.copy2(source, destination, follow_symlinks=False)



def materialize_proposed_tree(
    target_root: Path,
    overlay: ReviewOverlay,
    destination: Path,
) -> None:
    """Compose target + changed bodies - deletions without mutating the target."""
    shutil.copytree(
        target_root,
        destination,
        symlinks=True,
        copy_function=_link_or_copy,
    )
    for relative_path in overlay.deleted_paths:
        target = _destination_without_symlink_ancestor(destination, relative_path)
        _remove_destination(target)
    for relative_path, expected_sha256 in overlay.file_sha256_by_path.items():
        target = _destination_without_symlink_ancestor(destination, relative_path)
        _remove_destination(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = read_repository_file_bytes(
            overlay.files_root,
            relative_path,
            expected_sha256=expected_sha256,
        )
        target.write_bytes(content)



def _review_identity(
    *,
    workspace: str,
    project: str,
    target_branch: str,
    base_revision: str,
    base_collection_target: str | None,
    base_generation_manifest_sha256: str | None,
    source_revision: str,
    target_source_tree_sha256: str,
    overlay_sha256: str,
    representation: Mapping[str, Any],
    include_patterns: Sequence[str] | None,
    exclude_patterns: Sequence[str] | None,
    project_type: str | None,
    source_root: str | None,
) -> tuple[str, dict[str, Any]]:
    identity = {
        "workspace": workspace,
        "project": project,
        "targetBranch": target_branch,
        "baseRevision": base_revision,
        "baseCollectionTarget": base_collection_target,
        "baseGenerationManifestSha256": base_generation_manifest_sha256,
        "sourceRevision": source_revision,
        "targetSourceTreeSha256": target_source_tree_sha256,
        "overlaySha256": overlay_sha256,
        "representationIdentity": representation["representation_identity"],
        "selection": canonical_index_selection_policy(
            include_patterns,
            exclude_patterns,
        ),
        "projectType": project_type,
        "sourceRoot": source_root,
        "graphComposition": _REVIEW_GRAPH_COMPOSITION,
    }
    digest = _sha256_json(identity)
    return f"cc_review_g_{digest}", identity

