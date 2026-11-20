from __future__ import annotations

import hashlib
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from .judge import _validate_local_snapshot
from .util import (
    hermetic_git_environment,
    read_json,
    require_full_sha,
    require_text,
    run,
    sha256_json,
    sha256_text,
    write_json,
)


REPOSITORY_EVIDENCE_KIND = (
    "codecrow-magento2-source-repository-evidence"
)
REPOSITORY_EVIDENCE_FIELDS = {
    "kind",
    "generatedAt",
    "corpusId",
    "corpusDigest",
    "repository",
    "objectFormat",
    "repositoryPath",
    "cases",
    "refs",
    "objectCount",
    "objectIdentityDigest",
    "repositoryContentDigest",
    "evidenceDigest",
}
CASE_FIELDS = {
    "caseId",
    "base",
    "reviewedHead",
    "finalHead",
    "fixCommits",
    "reviewedChangedPaths",
    "reviewedDiffSha256",
    "finalChangedPaths",
    "finalDiffSha256",
}
COMMIT_FIELDS = {"commitSha", "treeSha"}
REF_FIELDS = {"name", "commitSha"}
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
SAFE_REF = re.compile(
    r"^refs/codecrow/evidence/[0-9a-f]{40}$"
)


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _exact_fields(
    value: Any,
    expected: set[str],
    field: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"{field} fields are invalid")
    return value


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or SHA256_HEX.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _timestamp(value: Any, field: str) -> str:
    text = require_text(value, field)
    if not text.endswith("Z"):
        raise ValueError(f"{field} must be a UTC ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(
            f"{field} must be a UTC ISO-8601 timestamp"
        ) from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field} must be a UTC ISO-8601 timestamp")
    return text


def _git(repository: Path, *arguments: str, input_text: str | None = None) -> str:
    return run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(repository),
            *arguments,
        ],
        env=hermetic_git_environment(offline=True),
        input_text=input_text,
    )


def _git_completed(
    repository: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(repository),
            *arguments,
        ],
        env=hermetic_git_environment(offline=True),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _reject_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise ValueError("repository evidence root must not be a symlink")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(
                f"repository evidence must not contain symlinks: {path}"
            )


def _bare_repository(repository: Path) -> None:
    if not repository.is_dir() or repository.is_symlink():
        raise ValueError(
            "repository evidence must contain a real bare Git repository"
        )
    if _git(repository, "rev-parse", "--is-bare-repository").strip() != "true":
        raise ValueError("repository evidence Git repository must be bare")
    if _git(repository, "rev-parse", "--show-object-format").strip() != "sha1":
        raise ValueError(
            "repository evidence must use the corpus SHA-1 object format"
        )
    if (repository / "commondir").exists() or (
        repository / "gitdir"
    ).exists():
        raise ValueError(
            "repository evidence must not use worktree linkage"
        )
    for forbidden in (
        repository / "objects" / "info" / "alternates",
        repository / "info" / "grafts",
        repository / "shallow",
    ):
        if forbidden.exists():
            raise ValueError(
                "repository evidence must not contain alternates, grafts, "
                "or shallow history"
            )
    hooks = repository / "hooks"
    if hooks.exists() and any(hooks.iterdir()):
        raise ValueError("repository evidence must not contain hooks")
    if any(repository.rglob("*.promisor")):
        raise ValueError(
            "repository evidence must not contain promisor objects"
        )
    config = _git_completed(repository, "config", "--local", "--list")
    if config.returncode:
        detail = config.stderr.strip() or config.stdout.strip()
        raise ValueError(
            "cannot inspect repository evidence configuration"
            + (f": {detail}" if detail else "")
        )
    forbidden_config = (
        "remote.",
        "url.",
        "http.",
        "credential.",
        "core.worktree",
        "extensions.partialclone",
    )
    for line in config.stdout.splitlines():
        key = line.split("=", 1)[0].casefold()
        if key.startswith(forbidden_config):
            raise ValueError(
                "repository evidence contains a remote, credential, "
                "partial-clone, or worktree configuration"
            )
    replace_refs = _git(
        repository,
        "for-each-ref",
        "--format=%(refname)",
        "refs/replace",
    )
    if replace_refs.strip():
        raise ValueError("repository evidence must not contain replace refs")


def _commit(repository: Path, sha: Any, field: str) -> dict[str, str]:
    commit_sha = require_full_sha(sha, field)
    if _git(repository, "cat-file", "-t", commit_sha).strip() != "commit":
        raise ValueError(f"{field} is not a Git commit")
    tree_sha = _git(repository, "show", "-s", "--format=%T", commit_sha).strip()
    require_full_sha(tree_sha, f"{field} tree")
    return {"commitSha": commit_sha, "treeSha": tree_sha}


def _changed_paths(
    repository: Path,
    base_sha: str,
    head_sha: str,
) -> list[str]:
    from .util import deterministic_git_diff_command

    return sorted(
        value
        for value in run(
            deterministic_git_diff_command(
                repository,
                "--name-only",
                "-z",
                base_sha,
                head_sha,
            ),
            env=hermetic_git_environment(offline=True),
        ).split("\0")
        if value
    )


def _diff(repository: Path, base_sha: str, head_sha: str) -> str:
    from .util import deterministic_git_diff_command

    return run(
        deterministic_git_diff_command(
            repository,
            "--full-index",
            base_sha,
            head_sha,
        ),
        env=hermetic_git_environment(offline=True),
    )


def _fix_commit_shas(case: Mapping[str, Any]) -> list[str]:
    result = []
    for gold in case.get("goldenComments") or []:
        if not isinstance(gold, Mapping):
            continue
        validity = gold.get("validity")
        if not isinstance(validity, Mapping):
            continue
        value = validity.get("fixCommitSha")
        if isinstance(value, str) and value:
            result.append(require_full_sha(value, "fixCommitSha"))
    return sorted(set(result))


def _case_projection(
    repository: Path,
    case: Mapping[str, Any],
) -> dict[str, Any]:
    case_id = require_text(case.get("caseId"), "caseId")
    snapshot = case.get("snapshot")
    source_pr = case.get("sourcePr")
    if not isinstance(snapshot, Mapping) or not isinstance(source_pr, Mapping):
        raise ValueError(f"{case_id} source snapshot is invalid")
    base = _commit(
        repository,
        snapshot.get("baseSha"),
        f"{case_id}.baseSha",
    )
    reviewed = _commit(
        repository,
        snapshot.get("headSha"),
        f"{case_id}.headSha",
    )
    final = _commit(
        repository,
        source_pr.get("finalHeadSha"),
        f"{case_id}.finalHeadSha",
    )
    fix_commits = [
        _commit(repository, sha, f"{case_id}.fixCommitSha")
        for sha in _fix_commit_shas(case)
    ]
    base_sha = base["commitSha"]
    reviewed_sha = reviewed["commitSha"]
    final_sha = final["commitSha"]
    _validate_local_snapshot(repository, case)
    for ancestor, descendant, field in (
        (base_sha, reviewed_sha, "base-to-reviewed"),
        (reviewed_sha, final_sha, "reviewed-to-final"),
    ):
        completed = _git_completed(
            repository,
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        )
        if completed.returncode != 0:
            raise ValueError(f"{case_id} {field} ancestry is invalid")
    for fix in fix_commits:
        fix_sha = fix["commitSha"]
        for ancestor, descendant, field in (
            (reviewed_sha, fix_sha, "reviewed-to-fix"),
            (fix_sha, final_sha, "fix-to-final"),
        ):
            completed = _git_completed(
                repository,
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            )
            if completed.returncode != 0:
                raise ValueError(f"{case_id} {field} ancestry is invalid")
    final_paths = _changed_paths(repository, base_sha, final_sha)
    return {
        "caseId": case_id,
        "base": base,
        "reviewedHead": reviewed,
        "finalHead": final,
        "fixCommits": fix_commits,
        "reviewedChangedPaths": list(snapshot["changedPaths"]),
        "reviewedDiffSha256": str(snapshot["diffSha256"]),
        "finalChangedPaths": final_paths,
        "finalDiffSha256": sha256_text(
            _diff(repository, base_sha, final_sha)
        ),
    }


def _required_commits(cases: Sequence[Mapping[str, Any]]) -> list[str]:
    values = []
    for case in cases:
        for field in ("base", "reviewedHead", "finalHead"):
            commit = case[field]
            values.append(str(commit["commitSha"]))
        values.extend(
            str(item["commitSha"]) for item in case["fixCommits"]
        )
    return sorted(set(values))


def _refs(commits: Sequence[str]) -> list[dict[str, str]]:
    return [
        {
            "name": f"refs/codecrow/evidence/{sha}",
            "commitSha": sha,
        }
        for sha in commits
    ]


def _observed_refs(repository: Path) -> list[dict[str, str]]:
    output = _git(
        repository,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)",
    )
    result = []
    for line in output.splitlines():
        parts = line.split("\0")
        if len(parts) != 2:
            raise ValueError("repository evidence ref inventory is invalid")
        name, commit_sha = parts
        if SAFE_REF.fullmatch(name) is None:
            raise ValueError(
                f"repository evidence contains unexpected ref: {name}"
            )
        result.append(
            {
                "name": name,
                "commitSha": require_full_sha(
                    commit_sha,
                    f"{name} object",
                ),
            }
        )
    return sorted(result, key=lambda item: item["name"])


def _object_inventory(
    repository: Path,
    commits: Sequence[str],
) -> list[dict[str, Any]]:
    if not commits:
        raise ValueError("repository evidence has no required commits")
    object_ids = sorted(
        set(
            _git(
                repository,
                "rev-list",
                "--objects",
                "--no-object-names",
                "--stdin",
                input_text="".join(f"{sha}\n" for sha in commits),
            ).split()
        )
    )
    if not object_ids:
        raise ValueError("repository evidence object inventory is empty")
    output = _git(
        repository,
        "cat-file",
        "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        input_text="".join(f"{sha}\n" for sha in object_ids),
    )
    result = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 3:
            raise ValueError(
                "repository evidence object inventory is invalid"
            )
        sha, object_type, raw_size = parts
        require_full_sha(sha, "repository object")
        if object_type not in {"blob", "commit", "tag", "tree"}:
            raise ValueError("repository evidence object type is invalid")
        try:
            size = int(raw_size)
        except ValueError as exc:
            raise ValueError(
                "repository evidence object size is invalid"
            ) from exc
        if size < 0:
            raise ValueError(
                "repository evidence object size is invalid"
            )
        result.append({"sha": sha, "type": object_type, "size": size})
    if [item["sha"] for item in result] != object_ids:
        raise ValueError("repository evidence object inventory drift")
    return result


def _repository_content_digest(
    *,
    refs: Sequence[Mapping[str, Any]],
    objects: Sequence[Mapping[str, Any]],
) -> str:
    # This logical digest deliberately excludes mtimes, file modes, loose
    # versus packed representation, and pack filenames. Git object IDs plus
    # verified object type/size and the exact ref map define the immutable
    # source store independently of storage layout.
    return sha256_json({"refs": list(refs), "objects": list(objects)})


def _repository_relative_path(
    evidence_root: Path,
    value: Any,
) -> Path:
    text = require_text(value, "repositoryPath")
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or text != pure.as_posix()
        or text in {"", ".", ".."}
        or ".." in pure.parts
        or "\x00" in text
    ):
        raise ValueError("repositoryPath must be normalized and relative")
    candidate = evidence_root.joinpath(*pure.parts)
    if candidate.is_symlink():
        raise ValueError("repositoryPath must not be a symlink")
    try:
        candidate.resolve(strict=True).relative_to(
            evidence_root.resolve(strict=True)
        )
    except (OSError, ValueError) as exc:
        raise ValueError(
            "repositoryPath escapes or is missing from evidence root"
        ) from exc
    return candidate


def _corpus_identity(corpus: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        require_text(corpus.get("corpusId"), "corpusId"),
        _sha256(corpus.get("corpusDigest"), "corpusDigest"),
        require_text(corpus.get("repository"), "repository"),
    )


def build_repository_evidence_manifest(
    *,
    corpus: Mapping[str, Any],
    repository: Path,
    repository_path: str = "repository.git",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the semantic manifest for a self-contained bare source store."""

    _bare_repository(repository)
    corpus_id, corpus_digest, repository_name = _corpus_identity(corpus)
    cases_value = corpus.get("cases")
    if not isinstance(cases_value, list) or not cases_value or any(
        not isinstance(item, Mapping) for item in cases_value
    ):
        raise ValueError("corpus cases are invalid")
    cases = [
        _case_projection(repository, case)
        for case in cases_value
    ]
    commits = _required_commits(cases)
    refs = _refs(commits)
    if _observed_refs(repository) != refs:
        raise ValueError(
            "repository evidence refs do not exactly cover B/H/F/fix commits"
        )
    objects = _object_inventory(repository, commits)
    fsck = _git_completed(
        repository,
        "fsck",
        "--full",
        "--strict",
        "--no-dangling",
        *commits,
    )
    if fsck.returncode:
        detail = fsck.stderr.strip() or fsck.stdout.strip()
        raise ValueError(
            "repository evidence object connectivity failed"
            + (f": {detail}" if detail else "")
        )
    result = {
        "kind": REPOSITORY_EVIDENCE_KIND,
        "generatedAt": generated_at or _now(),
        "corpusId": corpus_id,
        "corpusDigest": corpus_digest,
        "repository": repository_name,
        "objectFormat": "sha1",
        "repositoryPath": repository_path,
        "cases": cases,
        "refs": refs,
        "objectCount": len(objects),
        "objectIdentityDigest": sha256_json(objects),
        "repositoryContentDigest": _repository_content_digest(
            refs=refs,
            objects=objects,
        ),
    }
    result["evidenceDigest"] = sha256_json(result)
    return result


def validate_repository_evidence(
    *,
    manifest_path: Path,
    corpus: Mapping[str, Any],
    evidence_root: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    """Validate and resolve a portable, offline source reconstruction store."""

    manifest_file = manifest_path
    root = evidence_root or manifest_file.parent
    if not root.is_dir() or root.is_symlink():
        raise ValueError(
            "repository evidence root must be a real directory"
        )
    _reject_symlinks(root)
    try:
        manifest_file.resolve(strict=True).relative_to(
            root.resolve(strict=True)
        )
    except (OSError, ValueError) as exc:
        raise ValueError(
            "repository evidence manifest is outside its evidence root"
        ) from exc
    value = _exact_fields(
        read_json(manifest_file),
        REPOSITORY_EVIDENCE_FIELDS,
        "repository evidence",
    )
    if value.get("kind") != REPOSITORY_EVIDENCE_KIND:
        raise ValueError("repository evidence kind is invalid")
    _timestamp(value.get("generatedAt"), "repository evidence generatedAt")
    digest_payload = dict(value)
    digest = digest_payload.pop("evidenceDigest", None)
    _sha256(digest, "repository evidence digest")
    if digest != sha256_json(digest_payload):
        raise ValueError("repository evidence digest mismatch")
    corpus_id, corpus_digest, repository_name = _corpus_identity(corpus)
    if (
        value.get("corpusId") != corpus_id
        or value.get("corpusDigest") != corpus_digest
        or value.get("repository") != repository_name
        or value.get("objectFormat") != "sha1"
    ):
        raise ValueError(
            "repository evidence corpus/repository identity mismatch"
        )
    repository = _repository_relative_path(root, value.get("repositoryPath"))
    _bare_repository(repository)
    regenerated = build_repository_evidence_manifest(
        corpus=corpus,
        repository=repository,
        repository_path=str(value["repositoryPath"]),
        generated_at=str(value["generatedAt"]),
    )
    if dict(value) != regenerated:
        raise ValueError(
            "repository evidence is not exactly derivable from its Git store"
        )
    return (
        {
            "evidenceDigest": str(digest),
            "repository": repository_name,
            "objectFormat": "sha1",
            "repositoryPath": str(value["repositoryPath"]),
            "cases": len(value["cases"]),
            "requiredCommits": len(value["refs"]),
            "objectCount": int(value["objectCount"]),
            "objectIdentityDigest": str(value["objectIdentityDigest"]),
            "repositoryContentDigest": str(
                value["repositoryContentDigest"]
            ),
        },
        repository,
    )


def create_repository_evidence(
    *,
    corpus_path: Path,
    source_repository: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Materialize a sanitized, self-contained bare repository evidence root."""

    corpus = read_json(corpus_path)
    if not isinstance(corpus, Mapping):
        raise ValueError("released corpus must be an object")
    if output_root.exists():
        raise ValueError("repository evidence output root must not exist")
    if (
        not source_repository.is_dir()
        or source_repository.is_symlink()
    ):
        raise ValueError("source repository must be a real Git directory")
    source_check = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(source_repository),
            "rev-parse",
            "--git-dir",
        ],
        env=hermetic_git_environment(offline=True),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if source_check.returncode:
        raise ValueError("source repository is not a local Git repository")
    cases = corpus.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("released corpus cases are invalid")
    commits: set[str] = set()
    for case in cases:
        if not isinstance(case, Mapping):
            raise ValueError("released corpus case is invalid")
        snapshot = case.get("snapshot")
        source_pr = case.get("sourcePr")
        if not isinstance(snapshot, Mapping) or not isinstance(
            source_pr,
            Mapping,
        ):
            raise ValueError("released corpus snapshot is invalid")
        commits.update(
            {
                require_full_sha(snapshot.get("baseSha"), "baseSha"),
                require_full_sha(snapshot.get("headSha"), "headSha"),
                require_full_sha(
                    source_pr.get("finalHeadSha"),
                    "finalHeadSha",
                ),
            }
        )
        commits.update(_fix_commit_shas(case))
    for sha in sorted(commits):
        if _git(source_repository, "cat-file", "-t", sha).strip() != "commit":
            raise ValueError(f"source repository lacks required commit {sha}")

    output_root.mkdir(parents=True, mode=0o700)
    repository = output_root / "repository.git"
    initialized = subprocess.run(
        ["git", "init", "--bare", "--quiet", str(repository)],
        env=hermetic_git_environment(offline=True),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if initialized.returncode:
        detail = initialized.stderr.strip() or initialized.stdout.strip()
        raise RuntimeError(
            "cannot initialize repository evidence"
            + (f": {detail}" if detail else "")
        )
    hooks = repository / "hooks"
    if hooks.is_dir():
        for hook in hooks.iterdir():
            if hook.is_file():
                hook.unlink()
    for generated in (
        repository / "description",
        repository / "info" / "exclude",
    ):
        if generated.is_file():
            generated.unlink()
    refspecs = [
        f"{sha}:refs/codecrow/evidence/{sha}"
        for sha in sorted(commits)
    ]
    fetched = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(repository),
            "fetch",
            "--no-tags",
            "--force",
            "--no-write-fetch-head",
            str(source_repository.resolve(strict=True)),
            *refspecs,
        ],
        env=hermetic_git_environment(offline=True),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if fetched.returncode:
        detail = fetched.stderr.strip() or fetched.stdout.strip()
        raise RuntimeError(
            "cannot materialize repository evidence objects"
            + (f": {detail}" if detail else "")
        )
    # The source path is used only by the one-shot fetch and is never retained
    # in the bare repository configuration or portable evidence manifest.
    manifest = build_repository_evidence_manifest(
        corpus=corpus,
        repository=repository,
    )
    write_json(output_root / "repository-evidence.json", manifest)
    validate_repository_evidence(
        manifest_path=output_root / "repository-evidence.json",
        corpus=corpus,
        evidence_root=output_root,
    )
    return manifest
