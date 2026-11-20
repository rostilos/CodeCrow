from __future__ import annotations

import os
import re
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any, Sequence

from .util import (
    hermetic_git_environment,
    is_local_git_repository,
    run,
    sha256_json,
    validate_git_evidence_repository,
)


OFFICIAL_REMOTE_URL = "https://github.com/magento/magento2.git"
BRANCH_REFSPEC = "+refs/heads/*:refs/remotes/origin/*"
PULL_HEAD_REFSPEC = "+refs/pull/*/head:refs/benchmark/pull/*"
REQUIRED_DURABLE_BRANCH_REFS = (
    "refs/remotes/origin/2.4-develop",
    "refs/remotes/origin/2.3",
    "refs/remotes/origin/2.2",
)
_PULL_ALIAS = re.compile(r"refs/benchmark/pull/[1-9][0-9]*\Z")


def _git(repository: Path, *arguments: str) -> str:
    return run(
        [
            "git",
            "--no-replace-objects",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-C",
            str(repository),
            *arguments,
        ],
        env=hermetic_git_environment(),
    )


def _official_remote(value: str) -> bool:
    candidate = value.strip()
    try:
        parsed = urllib.parse.urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    if (parsed.hostname or "").casefold() != "github.com" or port is not None:
        return False
    if parsed.query or parsed.fragment or parsed.password is not None:
        return False
    if parsed.username is not None:
        return False
    return parsed.path.rstrip("/").removesuffix(".git") == "/magento/magento2"


def _config_values(repository: Path, key: str) -> list[str]:
    completed = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(repository),
            "config",
            "--get-all",
            key,
        ],
        env=hermetic_git_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode == 1:
        return []
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(
            f"cannot inspect repository-local {key}"
            + (f": {detail}" if detail else "")
        )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _config_names(repository: Path) -> list[str]:
    """Return effective repository/worktree keys under the hermetic config scope."""

    completed = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(repository),
            "config",
            "--name-only",
            "--list",
        ],
        env=hermetic_git_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(
            "cannot inspect effective repository Git configuration"
            + (f": {detail}" if detail else "")
        )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _reject_transport_overrides(repository: Path) -> None:
    forbidden: list[tuple[str, str]] = []
    for key in _config_names(repository):
        folded = key.casefold()
        reason: str | None = None
        if folded == "core.sshcommand":
            reason = "SSH command override"
        elif folded.startswith("url."):
            reason = "URL rewriting"
        elif re.fullmatch(r"remote\..+\.uploadpack", folded):
            reason = "custom uploadpack"
        elif re.fullmatch(r"remote\..+\.(proxy|proxyauthmethod)", folded):
            reason = "remote proxy override"
        elif folded.startswith("http."):
            reason = "HTTP proxy/TLS/header override"
        elif folded == "include.path" or (
            folded.startswith("includeif.") and folded.endswith(".path")
        ):
            reason = "external config include"
        if reason is not None:
            forbidden.append((key, reason))
    if forbidden:
        detail = ", ".join(
            f"{key} ({reason})" for key, reason in sorted(forbidden)
        )
        raise ValueError(
            "repository-local transport-affecting Git configuration must be "
            f"absent: {detail}"
        )


def _verify_origin(repository: Path) -> str:
    _reject_transport_overrides(repository)
    raw_urls = _config_values(repository, "remote.origin.url")
    if len(raw_urls) != 1 or not _official_remote(raw_urls[0]):
        raise ValueError(
            "origin must have exactly one canonical HTTPS "
            "github.com/magento/magento2 fetch URL"
        )
    effective_urls = [
        line.strip()
        for line in _git(repository, "remote", "get-url", "--all", "origin").splitlines()
        if line.strip()
    ]
    if len(effective_urls) != 1 or not _official_remote(effective_urls[0]):
        raise ValueError(
            "origin URL rewriting must still resolve to official magento/magento2"
        )
    return effective_urls[0]


def _ref_records(repository: Path) -> list[tuple[str, str, str]]:
    raw = _git(
        repository,
        "for-each-ref",
        "--format=%(refname)%09%(objectname)%09%(objecttype)",
        "refs/remotes/origin",
        "refs/benchmark/pull",
    )
    records: list[tuple[str, str, str]] = []
    for line in raw.splitlines():
        fields = line.split("\t")
        if len(fields) != 3:
            raise ValueError("cannot parse prepared Git reference inventory")
        ref_name, object_id, object_type = fields
        if len(object_id) != 40 or any(
            character not in "0123456789abcdef" for character in object_id
        ):
            raise ValueError(f"prepared reference {ref_name} has a malformed object ID")
        if object_type != "commit":
            raise ValueError(f"prepared reference {ref_name} does not name a commit")
        records.append((ref_name, object_id, object_type))
    return records


def _verify_reachable_objects(
    repository: Path,
    records: Sequence[tuple[str, str, str]],
) -> None:
    revisions = "".join(f"{object_id}\n" for _, object_id, _ in records)
    completed = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(repository),
            "rev-list",
            "--objects",
            "--missing=error",
            "--stdin",
        ],
        env=hermetic_git_environment(offline=True),
        text=True,
        input=revisions,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.strip()
        raise ValueError(
            "prepared official refs do not have a complete local object closure"
            + (f": {detail}" if detail else "")
        )


def prepare_official_repository(repository: Path) -> dict[str, Any]:
    """Fetch the official branch and PR-head namespaces used by the builder."""

    repository = repository.expanduser()
    created = False
    if repository.is_symlink():
        raise ValueError("--repository-path must not be a symlink")
    repository = repository.resolve()
    if not repository.exists() or (
        repository.is_dir() and not any(repository.iterdir())
    ):
        if repository.exists() and not repository.is_dir():
            raise ValueError("--repository-path must be a directory")
        run(
            [
                "git",
                "init",
                "--bare",
                "--initial-branch=2.4-develop",
                str(repository),
            ],
            env=hermetic_git_environment(),
        )
        _git(repository, "remote", "add", "origin", OFFICIAL_REMOTE_URL)
        created = True
    if not is_local_git_repository(repository):
        raise ValueError(
            "--repository-path must be empty or an existing local Git repository"
        )
    validate_git_evidence_repository(repository)
    remote_url = _verify_origin(repository)

    fetch_arguments = [
        "fetch",
        "--atomic",
        "--force",
        "--prune",
        "--no-tags",
        "--no-write-fetch-head",
        "--no-recurse-submodules",
        "--no-filter",
    ]
    partial_clone = bool(
        _config_values(repository, "extensions.partialClone")
        or _config_values(repository, "remote.origin.promisor")
        or _config_values(repository, "remote.origin.partialclonefilter")
    )
    if partial_clone:
        fetch_arguments.append("--refetch")
    fetch_arguments.extend(("origin", BRANCH_REFSPEC, PULL_HEAD_REFSPEC))
    _git(repository, *fetch_arguments)

    validate_git_evidence_repository(repository)
    if _verify_origin(repository) != remote_url:
        raise ValueError("origin changed while official refs were fetched")
    records = _ref_records(repository)
    branches = [
        record
        for record in records
        if record[0].startswith("refs/remotes/origin/")
        and record[0] != "refs/remotes/origin/HEAD"
    ]
    pull_heads = [
        record for record in records if record[0].startswith("refs/benchmark/pull/")
    ]
    malformed_pull_aliases = [
        ref_name for ref_name, _, _ in pull_heads if not _PULL_ALIAS.fullmatch(ref_name)
    ]
    if malformed_pull_aliases:
        raise ValueError(
            "prepared pull-head namespace contains malformed aliases: "
            + ", ".join(sorted(malformed_pull_aliases)[:3])
        )
    present = {ref_name for ref_name, _, _ in branches}
    missing = sorted(set(REQUIRED_DURABLE_BRANCH_REFS) - present)
    if missing:
        raise ValueError(
            "official fetch did not retain required durable branches: "
            + ", ".join(missing)
        )
    if not pull_heads:
        raise ValueError("official fetch returned no pull-request head refs")
    _verify_reachable_objects(repository, [*branches, *pull_heads])

    inventory = [
        {"ref": ref_name, "objectId": object_id, "objectType": object_type}
        for ref_name, object_id, object_type in sorted([*branches, *pull_heads])
    ]
    return {
        "kind": "codecrow-magento2-official-repository-preparation",
        "repository": "magento/magento2",
        "remote": "origin",
        "remoteUrl": remote_url,
        "createdBareRepository": created,
        "partialCloneRefetchedWithoutFilter": partial_clone,
        "branchRefCount": len(branches),
        "pullHeadRefCount": len(pull_heads),
        "requiredDurableBranchRefs": list(REQUIRED_DURABLE_BRANCH_REFS),
        "refInventorySha256": sha256_json(inventory),
    }
