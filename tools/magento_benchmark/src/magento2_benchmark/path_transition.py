from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .util import (
    deterministic_git_diff_command,
    require_full_sha,
    require_text,
    run,
    sha256_text,
)


PATH_TRANSITION_FIELDS = {
    "status",
    "sourcePath",
    "finalPath",
    "renameSimilarity",
    "checkpointBlobOid",
    "finalBlobOid",
    "diffSha256",
}
PATH_TRANSITION_STATUSES = {"modified", "renamed", "deleted"}


def _blob_oid(
    repository: Path,
    revision: str,
    path: str,
    *,
    git_env: Mapping[str, str] | None,
) -> str:
    oid = run(
        [
            "git",
            "-C",
            str(repository),
            "rev-parse",
            "--verify",
            f"{revision}:{path}",
        ],
        env=git_env,
    ).strip()
    require_full_sha(oid, f"{revision}:{path} blob")
    object_type = run(
        ["git", "-C", str(repository), "cat-file", "-t", oid],
        env=git_env,
    ).strip()
    if object_type != "blob":
        raise ValueError(f"{revision}:{path} is not a Git blob")
    return oid


def _name_status_tokens(
    repository: Path,
    *,
    checkpoint_sha: str,
    final_sha: str,
    source_path: str | None,
    git_env: Mapping[str, str] | None,
) -> list[str]:
    path_arguments = (
        ["--", f":(literal){source_path}"]
        if source_path is not None
        else []
    )
    raw = run(
        deterministic_git_diff_command(
            repository,
            "--name-status",
            "-z",
            checkpoint_sha,
            final_sha,
            *path_arguments,
        ),
        env=git_env,
    )
    tokens = raw.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    return tokens


def _name_status_records(tokens: list[str]) -> list[tuple[str, list[str]]]:
    records: list[tuple[str, list[str]]] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        path_count = 2 if status.startswith(("R", "C")) else 1
        end = index + 1 + path_count
        if not status or end > len(tokens):
            raise ValueError(f"malformed Git name-status output: {tokens!r}")
        records.append((status, tokens[index + 1 : end]))
        index = end
    return records


def resolve_path_transition(
    repository: Path,
    *,
    checkpoint_sha: str,
    final_sha: str,
    source_path: str,
    git_env: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], str]:
    """Resolve one reviewed path from H to F under the frozen diff policy."""

    require_full_sha(checkpoint_sha, "checkpoint_sha")
    require_full_sha(final_sha, "final_sha")
    source_path = require_text(source_path, "source_path")
    checkpoint_blob = _blob_oid(
        repository,
        checkpoint_sha,
        source_path,
        git_env=git_env,
    )
    path_records = _name_status_records(
        _name_status_tokens(
            repository,
            checkpoint_sha=checkpoint_sha,
            final_sha=final_sha,
            source_path=source_path,
            git_env=git_env,
        )
    )
    if len(path_records) != 1:
        raise ValueError(
            f"{source_path} has ambiguous checkpoint-to-final transitions: "
            f"{path_records!r}"
        )
    raw_status, paths = path_records[0]
    if raw_status == "D":
        rename_records = [
            record
            for record in _name_status_records(
                _name_status_tokens(
                    repository,
                    checkpoint_sha=checkpoint_sha,
                    final_sha=final_sha,
                    source_path=None,
                    git_env=git_env,
                )
            )
            if record[0].startswith("R")
            and record[1]
            and record[1][0] == source_path
        ]
        if len(rename_records) > 1:
            raise ValueError(
                f"{source_path} has multiple rename destinations"
            )
        if rename_records:
            raw_status, paths = rename_records[0]

    final_path: str | None
    final_blob: str | None
    rename_similarity: int | None = None
    if raw_status == "M" and paths == [source_path]:
        status = "modified"
        final_path = source_path
        final_blob = _blob_oid(
            repository,
            final_sha,
            final_path,
            git_env=git_env,
        )
    elif raw_status == "D" and paths == [source_path]:
        status = "deleted"
        final_path = None
        final_blob = None
    elif (
        raw_status.startswith("R")
        and len(paths) == 2
        and paths[0] == source_path
        and raw_status[1:].isdigit()
    ):
        status = "renamed"
        rename_similarity = int(raw_status[1:])
        if not 50 <= rename_similarity <= 100:
            raise ValueError(
                f"{source_path} has unsupported rename score {rename_similarity}"
            )
        final_path = require_text(paths[1], "renamed final path")
        if final_path == source_path:
            raise ValueError(f"{source_path} rename did not change the path")
        final_blob = _blob_oid(
            repository,
            final_sha,
            final_path,
            git_env=git_env,
        )
    else:
        raise ValueError(
            f"{source_path} has ambiguous or unsupported Git transition: "
            f"{(raw_status, paths)!r}"
        )

    diff_paths = [f":(literal){source_path}"]
    if final_path is not None and final_path != source_path:
        diff_paths.append(f":(literal){final_path}")
    diff = run(
        deterministic_git_diff_command(
            repository,
            "--full-index",
            "--unified=80",
            checkpoint_sha,
            final_sha,
            "--",
            *diff_paths,
        ),
        env=git_env,
    )
    if not diff:
        raise ValueError(
            f"{source_path} has no checkpoint-to-final path diff"
        )
    evidence = {
        "status": status,
        "sourcePath": source_path,
        "finalPath": final_path,
        "renameSimilarity": rename_similarity,
        "checkpointBlobOid": checkpoint_blob,
        "finalBlobOid": final_blob,
        "diffSha256": sha256_text(diff),
    }
    validate_path_transition_evidence(
        evidence,
        source_path=source_path,
        diff_sha256=evidence["diffSha256"],
    )
    return evidence, diff


def validate_path_transition_evidence(
    value: Any,
    *,
    source_path: str,
    diff_sha256: str,
) -> dict[str, Any]:
    """Validate the exact JSON shape and status-dependent transition fields."""

    if not isinstance(value, Mapping) or set(value) != PATH_TRANSITION_FIELDS:
        raise ValueError("path transition fields are invalid")
    if value.get("sourcePath") != source_path:
        raise ValueError("path transition source path drift")
    status = value.get("status")
    if status not in PATH_TRANSITION_STATUSES:
        raise ValueError("path transition status is invalid")
    if (
        not isinstance(diff_sha256, str)
        or len(diff_sha256) != 64
        or any(character not in "0123456789abcdef" for character in diff_sha256)
        or value.get("diffSha256") != diff_sha256
    ):
        raise ValueError("path transition diff digest mismatch")
    require_full_sha(
        value.get("checkpointBlobOid"),
        "path transition checkpointBlobOid",
    )

    final_path = value.get("finalPath")
    final_blob = value.get("finalBlobOid")
    similarity = value.get("renameSimilarity")
    if status == "modified":
        if (
            final_path != source_path
            or similarity is not None
            or not isinstance(final_blob, str)
        ):
            raise ValueError("modified path transition is inconsistent")
        require_full_sha(final_blob, "path transition finalBlobOid")
    elif status == "renamed":
        if (
            not isinstance(final_path, str)
            or not final_path
            or final_path == source_path
            or isinstance(similarity, bool)
            or not isinstance(similarity, int)
            or not 50 <= similarity <= 100
            or not isinstance(final_blob, str)
        ):
            raise ValueError("renamed path transition is inconsistent")
        require_full_sha(final_blob, "path transition finalBlobOid")
    else:
        if final_path is not None or final_blob is not None or similarity is not None:
            raise ValueError("deleted path transition is inconsistent")
    return dict(value)
