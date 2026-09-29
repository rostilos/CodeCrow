#!/usr/bin/env python3
"""Run a provider-free deployed review against a local synthetic repository.

This gate deliberately does not create a CodeCrow project or contact a VCS
provider.  It creates immutable local Git commits with no remote, publishes the
base snapshot into a unique RAG namespace, and sends the exact head diff through
an isolated Redis database to a one-off inference-orchestrator container.

The review LLM is replaced by the production prompt-capture adapter. Repository
context uses the deployed structural index.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_CONTRACTS = REPOSITORY_ROOT / "analysis-plugins" / "contracts" / "python"
INFERENCE_SOURCE = (
    REPOSITORY_ROOT / "python-ecosystem" / "inference-orchestrator" / "src"
)
for import_root in (str(PLUGIN_CONTRACTS), str(INFERENCE_SOURCE)):
    if import_root not in sys.path:
        sys.path.insert(0, import_root)

from codecrow_plugins import ProjectSelector, RepositoryFacts  # noqa: E402
from codecrow_plugins.bootstrap import discover_builtin_plugins  # noqa: E402
from model.dtos import ReviewRequestDto  # noqa: E402
from model.enrichment import FileContentDto, PrEnrichmentDataDto  # noqa: E402
from model.plugins import ProjectCapabilitiesDto  # noqa: E402

from .prompt_dry_run_audit import audit_prompt_dry_run  # noqa: E402


ISOLATED_WORKSPACE = "codecrow-quality-isolated"
ISOLATED_PROJECT_ID = 900001
ISOLATED_REDIS_DB = 15
ISOLATED_BRANCH = "main"
ISOLATED_SOURCE_BRANCH = "feature/neutral-context"
ISOLATED_PR_NUMBER = 42
JOB_QUEUE_KEY = "codecrow:analysis:jobs"
ISOLATED_STATE_LOCK = Path(
    "/tmp/codecrow-isolated-review-quality-redis-15.lock"
)
DUMMY_REVIEW_KEY = "isolated-dry-run-key-must-never-be-used"
JAVA_DRY_RUN_KEY = "dry-run-provider-disabled"
FORBIDDEN_CONNECTED_IDENTITIES = (
    "al-ways",
    "al.ways",
    "1.8.0-rc",
    "hofmanflowers",
)
FORBIDDEN_CONNECTED_PROJECT_IDS = frozenset({352, 1802})
FORBIDDEN_CONNECTED_REPOSITORY_NAMES = frozenset({
    "al-ways",
    "al.ways",
    "hofmanflowers",
    "ways",
})
_PROJECT_IDENTITY_FIELDS = frozenset({
    "projectid",
    "project_id",
})
_REPOSITORY_IDENTITY_FIELDS = frozenset({
    "project",
    "projectnamespace",
    "project_namespace",
    "projectvcsreposlug",
    "project_vcs_repo_slug",
    "repository",
    "repositoryname",
    "repository_name",
    "repositorypath",
    "repository_path",
})
_GENERATION_MANIFEST = re.compile(r"^[0-9a-f]{64}$")


@contextmanager
def _exclusive_isolated_state_lock(
    path: Path = ISOLATED_STATE_LOCK,
):
    """Prevent concurrent tools from sharing Redis DB 15/index state."""
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError(
                "isolated quality-state lock must be an owner-owned regular file"
            )
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exception:
            raise RuntimeError(
                "another isolated review-quality run owns Redis DB 15"
            ) from exception
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


BASE_FILES = {
    "service/account.py": (
        "from shared.policy import AccountPolicy\n"
        "\n"
        "def enabled(account):\n"
        "    return False\n"
    ),
    "shared/policy.py": (
        "class AccountPolicy:\n"
        "    @staticmethod\n"
        "    def is_enabled(account):\n"
        "        return account.active and not account.suspended\n"
    ),
    "backend/src/main/java/example/Account.java": (
        "package example;\n"
        "\n"
        "public record Account(boolean active, boolean suspended) {}\n"
    ),
    "backend/src/main/java/example/AccountPolicy.java": (
        "package example;\n"
        "\n"
        "public final class AccountPolicy {\n"
        "    private AccountPolicy() {}\n"
        "\n"
        "    public static boolean enabled(Account account) {\n"
        "        return account.active() && !account.suspended();\n"
        "    }\n"
        "}\n"
    ),
    "backend/src/main/java/example/AccountService.java": (
        "package example;\n"
        "\n"
        "public final class AccountService {\n"
        "    public boolean enabled(Account account) {\n"
        "        return false;\n"
        "    }\n"
        "}\n"
    ),
    "web/src/policy.ts": (
        "export interface Account {\n"
        "  active: boolean;\n"
        "  suspended: boolean;\n"
        "}\n"
        "\n"
        "export const isEnabled = (account: Account): boolean =>\n"
        "  account.active && !account.suspended;\n"
    ),
    "web/src/account.ts": (
        "import type { Account } from './policy';\n"
        "\n"
        "export const enabled = (_account: Account): boolean => false;\n"
    ),
}

HEAD_REPLACEMENTS = {
    "service/account.py": (
        "from shared.policy import AccountPolicy\n"
        "\n"
        "def enabled(account):\n"
        "    return AccountPolicy.is_enabled(account)\n"
    ),
    "backend/src/main/java/example/AccountService.java": (
        "package example;\n"
        "\n"
        "public final class AccountService {\n"
        "    public boolean enabled(Account account) {\n"
        "        return AccountPolicy.enabled(account);\n"
        "    }\n"
        "}\n"
    ),
    "web/src/account.ts": (
        "import { isEnabled, type Account } from './policy';\n"
        "\n"
        "export const enabled = (account: Account): boolean => isEnabled(account);\n"
    ),
}

EXPECTED_RELATED_PATHS = {
    "service/account.py": "shared/policy.py",
    "backend/src/main/java/example/AccountService.java": (
        "backend/src/main/java/example/AccountPolicy.java"
    ),
    "web/src/account.ts": "web/src/policy.ts",
}


@dataclass(frozen=True)
class SyntheticRepository:
    root: Path
    base_tree: Path
    base_revision: str
    head_revision: str
    raw_diff: str
    changed_files: tuple[str, ...]
    head_files: Mapping[str, str]


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        cwd=str(cwd) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and completed.returncode != 0:
        rendered = " ".join(command)
        raise RuntimeError(
            f"command failed ({completed.returncode}): {rendered}\n"
            f"{completed.stderr.strip()}"
        )
    return completed


def _git(
    repository: Path,
    *arguments: str,
    env: Mapping[str, str] | None = None,
) -> str:
    return _run(
        ("git", *arguments),
        cwd=repository,
        env=env,
    ).stdout.strip()


def _write_files(root: Path, files: Mapping[str, str]) -> None:
    for relative_path, content in sorted(files.items()):
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")


def build_synthetic_repository(parent: Path) -> SyntheticRepository:
    """Create two deterministic commits and an immutable plain base snapshot."""
    repository = parent / "neutral-mixed-repository"
    base_tree = parent / "neutral-mixed-base"
    repository.mkdir(parents=True)
    _run(("git", "init", "-q", "-b", ISOLATED_BRANCH), cwd=repository)
    _git(repository, "config", "user.name", "CodeCrow Quality Gate")
    _git(repository, "config", "user.email", "quality-gate@invalid.local")

    commit_env = dict(os.environ)
    commit_env.update({
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
    })
    _write_files(repository, BASE_FILES)
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "synthetic base", env=commit_env)
    base_revision = _git(repository, "rev-parse", "HEAD")

    _write_files(repository, HEAD_REPLACEMENTS)
    _git(repository, "add", ".")
    commit_env["GIT_AUTHOR_DATE"] = "2026-01-01T00:01:00+00:00"
    commit_env["GIT_COMMITTER_DATE"] = "2026-01-01T00:01:00+00:00"
    _git(repository, "commit", "-q", "-m", "synthetic head", env=commit_env)
    head_revision = _git(repository, "rev-parse", "HEAD")

    remotes = _git(repository, "remote")
    if remotes:
        raise RuntimeError("isolated replay repository must not have Git remotes")

    changed_files = tuple(sorted(
        line
        for line in _git(
            repository,
            "diff",
            "--name-only",
            base_revision,
            head_revision,
        ).splitlines()
        if line
    ))
    if changed_files != tuple(sorted(HEAD_REPLACEMENTS)):
        raise RuntimeError(
            "synthetic changed-file manifest does not match the fixed fixture"
        )
    raw_diff = _run(
        (
            "git",
            "diff",
            "--no-ext-diff",
            "--full-index",
            "--unified=80",
            base_revision,
            head_revision,
        ),
        cwd=repository,
    ).stdout
    if not raw_diff.strip():
        raise RuntimeError("synthetic review diff is empty")

    # The RAG service image intentionally does not require Git. Copy a plain,
    # immutable snapshot rather than a linked worktree whose `.git` pointer
    # names a host-only temporary path (or a clone that would require `git` in
    # the container merely because the marker exists).
    base_tree.mkdir()
    _write_files(base_tree, BASE_FILES)
    head_files = {
        path: (repository / path).read_text(encoding="utf-8")
        for path in changed_files
    }
    return SyntheticRepository(
        root=repository,
        base_tree=base_tree,
        base_revision=base_revision,
        head_revision=head_revision,
        raw_diff=raw_diff,
        changed_files=changed_files,
        head_files=head_files,
    )


def build_review_overlay(
    parent: Path,
    repository: SyntheticRepository,
) -> Path:
    """Create the host/RAG proposed-tree overlay used by production reviews."""
    overlay_root = parent / "neutral-mixed-review-overlay"
    files_root = overlay_root / "files"
    files_root.mkdir(parents=True)
    _write_files(files_root, repository.head_files)
    (overlay_root / "manifest.json").write_text(
        json.dumps(
            {
                "changedFiles": list(repository.changed_files),
                "deletedFiles": [],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    return overlay_root


def _capabilities(repository: SyntheticRepository) -> ProjectCapabilitiesDto:
    catalog = discover_builtin_plugins()
    selector = ProjectSelector(catalog.registry)
    paths = tuple(sorted(
        path.relative_to(repository.root).as_posix()
        for path in repository.root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    ))
    selected = selector.select(RepositoryFacts(
        revision=repository.head_revision,
        paths=paths,
        marker_contents={},
    ))
    return ProjectCapabilitiesDto(
        repositoryPlugins=list(selected.repository_plugins),
        filePlugins={
            path: list(plugin_ids)
            for path, plugin_ids in selected.file_plugins.items()
        },
        detectionEvidence={
            plugin_id: list(evidence)
            for plugin_id, evidence in selected.detection_evidence.items()
        },
        unavailableCapabilities=list(selected.unavailable_capabilities),
        fingerprint=selected.fingerprint,
        descriptorFingerprint=selected.descriptor_fingerprint,
    )


def build_review_request(
    repository: SyntheticRepository,
    *,
    project_namespace: str,
    dry_run_id: str,
) -> ReviewRequestDto:
    capabilities = _capabilities(repository)
    return ReviewRequestDto(
        projectId=ISOLATED_PROJECT_ID,
        projectVcsWorkspace=ISOLATED_WORKSPACE,
        projectVcsRepoSlug=project_namespace,
        projectWorkspace=ISOLATED_WORKSPACE,
        projectNamespace=project_namespace,
        aiProvider="OPENAI",
        aiModel="provider-model-never-constructed",
        aiApiKey=DUMMY_REVIEW_KEY,
        analysisType="PR_ANALYSIS",
        targetBranchName=ISOLATED_BRANCH,
        sourceBranchName=ISOLATED_SOURCE_BRANCH,
        pullRequestId=ISOLATED_PR_NUMBER,
        currentCommitHash=repository.head_revision,
        baseCommitHash=repository.base_revision,
        changedFiles=list(repository.changed_files),
        rawDiff=repository.raw_diff,
        enrichmentData=PrEnrichmentDataDto(fileContents=[
            FileContentDto(
                path=path,
                content=repository.head_files[path],
                sizeBytes=len(repository.head_files[path].encode("utf-8")),
            )
            for path in repository.changed_files
        ]),
        projectCapabilities=capabilities,
        promptDryRun=True,
        promptDryRunId=dry_run_id,
        useMcpTools=True,
        prTitle="Isolated neutral mixed-language context replay",
    )


def build_java_review_request(
    repository: SyntheticRepository,
    *,
    project_namespace: str,
    temporary_root: Path,
    java_ecosystem: Path,
    plugin_directory: Path,
    local_repo_path: str | None = None,
    review_overlay_path: str | None = None,
    collection_target: str | None = None,
    generation_manifest_sha256: str | None = None,
    include_request_payload: bool = False,
    expected_repository_plugins: Sequence[str] = (
        "java",
        "python",
        "typescript",
    ),
) -> (
    tuple[ReviewRequestDto, dict[str, Any]]
    | tuple[ReviewRequestDto, dict[str, Any], dict[str, Any]]
):
    """Capture the request emitted by the production Java producer."""
    structural_binding = (
        local_repo_path,
        review_overlay_path,
        collection_target,
        generation_manifest_sha256,
    )
    if any(value is not None for value in structural_binding) and not all(
        isinstance(value, str) and bool(value.strip())
        for value in structural_binding
    ):
        raise ValueError(
            "Java producer structural binding must be complete or omitted"
        )
    rag_enabled = all(
        isinstance(value, str) and bool(value.strip())
        for value in structural_binding
    )
    fixture_path = temporary_root / "java-producer-fixture.json"
    envelope_path = temporary_root / "java-queue-envelope.json"
    fixture_path.write_text(
        json.dumps(
            {
                "baseRevision": repository.base_revision,
                "headRevision": repository.head_revision,
                "rawDiff": repository.raw_diff,
                "headFiles": repository.head_files,
                "projectNamespace": project_namespace,
                "ragEnabled": rag_enabled,
                **(
                    {
                        "localRepoPath": local_repo_path,
                        "reviewOverlayPath": review_overlay_path,
                        "collectionTarget": collection_target,
                        "generationManifestSha256": (
                            generation_manifest_sha256
                        ),
                    }
                    if rag_enabled
                    else {}
                ),
                "expectedRepositoryPlugins": list(
                    expected_repository_plugins
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    java_build = _run(
        (
            "mvn",
            "--offline",
            "--no-transfer-progress",
            "-pl",
            "services/pipeline-agent",
            "-am",
            "-Dtest=IsolatedReviewProducerReplayTest",
            "-Dsurefire.failIfNoSpecifiedTests=false",
            f"-DreviewQuality.syntheticFixture={fixture_path.resolve()}",
            f"-DreviewQuality.queueEnvelopeOutput={envelope_path.resolve()}",
            f"-DreviewQuality.pluginDirectory={plugin_directory.resolve()}",
            "test",
        ),
        cwd=java_ecosystem,
        check=False,
    )
    if java_build.returncode != 0:
        output = (java_build.stdout + "\n" + java_build.stderr).strip()
        raise RuntimeError(
            "production Java request builder failed:\n"
            + output[-12_000:]
        )
    if not envelope_path.is_file():
        raise RuntimeError("Java producer did not emit a queue envelope")
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    if not isinstance(envelope, Mapping):
        raise RuntimeError("Java producer queue envelope is not an object")
    request_payload = envelope.get("request")
    if not isinstance(request_payload, Mapping):
        raise RuntimeError("Java producer queue envelope has no request object")
    request_payload = dict(request_payload)
    captured_job_id = envelope.get("job_id")
    if (
        not isinstance(captured_job_id, str)
        or not captured_job_id
        or captured_job_id != request_payload.get("promptDryRunId")
    ):
        raise RuntimeError(
            "Java producer queue job ID does not match promptDryRunId"
        )
    _assert_no_connected_identity(request_payload)
    if request_payload.get("projectId") != ISOLATED_PROJECT_ID:
        raise RuntimeError("Java producer emitted the wrong synthetic project ID")
    if request_payload.get("projectNamespace") != project_namespace:
        raise RuntimeError("Java producer emitted the wrong project namespace")
    if request_payload.get("targetBranchName") != ISOLATED_BRANCH:
        raise RuntimeError("Java producer emitted the wrong target branch")
    if request_payload.get("sourceBranchName") != ISOLATED_SOURCE_BRANCH:
        raise RuntimeError("Java producer emitted the wrong source branch")
    if request_payload.get("baseCommitHash") != repository.base_revision:
        raise RuntimeError("Java producer emitted the wrong base revision")
    if request_payload.get("targetHeadCommitHash") != repository.base_revision:
        raise RuntimeError("Java producer emitted the wrong target-head revision")
    if request_payload.get("currentCommitHash") != repository.head_revision:
        raise RuntimeError("Java producer emitted the wrong head revision")
    if request_payload.get("changedFiles") != list(repository.changed_files):
        raise RuntimeError("Java producer changed-file manifest is not lossless")
    if request_payload.get("promptDryRun") is not True:
        raise RuntimeError("Java producer did not enable prompt dry-run")
    if request_payload.get("useMcpTools") is not True:
        raise RuntimeError(
            "Java producer did not enable structural Stage 1 agent context"
        )
    if request_payload.get("ragEnabled") is not rag_enabled:
        raise RuntimeError("Java producer emitted the wrong RAG enablement")
    if rag_enabled:
        expected_structural_binding = {
            "localRepoPath": local_repo_path,
            "localRepoTargetBranch": ISOLATED_BRANCH,
            "localRepoRevision": repository.base_revision,
            "localReviewOverlayPath": review_overlay_path,
            "ragCollectionTarget": collection_target,
            "ragBaseGenerationManifestSha256": generation_manifest_sha256,
        }
        for field, expected in expected_structural_binding.items():
            if request_payload.get(field) != expected:
                raise RuntimeError(
                    "Java producer emitted the wrong structural binding: "
                    + field
                )
    if request_payload.get("aiApiKey") != JAVA_DRY_RUN_KEY:
        raise RuntimeError("Java producer did not replace the review credential")
    for credential_field in ("oAuthClient", "oAuthSecret", "accessToken"):
        if request_payload.get(credential_field) is not None:
            raise RuntimeError(
                f"Java producer leaked {credential_field} into dry-run payload"
            )
    capabilities = request_payload.get("projectCapabilities")
    if not isinstance(capabilities, Mapping):
        raise RuntimeError("Java producer omitted project capabilities")
    if capabilities.get("repositoryPlugins") != list(
        expected_repository_plugins
    ):
        raise RuntimeError(
            "Java producer did not emit the expected repository plugin projection"
        )

    canonical_request = json.dumps(
        request_payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    canonical_envelope = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
    )
    request = ReviewRequestDto.model_validate(request_payload)
    producer = {
        "kind": "production-java-queue-envelope",
        "configuredTargetBranch": ISOLATED_BRANCH,
        "sourceBranch": ISOLATED_SOURCE_BRANCH,
        "baseRevision": repository.base_revision,
        "headRevision": repository.head_revision,
        "changedFiles": list(repository.changed_files),
        "repositoryPlugins": list(capabilities["repositoryPlugins"]),
        "capturedRequestDigest": hashlib.sha256(
            canonical_request.encode("utf-8")
        ).hexdigest(),
        "capturedEnvelopeDigest": hashlib.sha256(
            canonical_envelope.encode("utf-8")
        ).hexdigest(),
        "capturedJobIdMatchesPromptDryRunId": True,
        "ragEnabled": rag_enabled,
        "exactStructuralBindingPresent": rag_enabled,
        "reviewCredentialsPresent": False,
    }
    if include_request_payload:
        return request, producer, dict(request_payload)
    return request, producer


def _env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _json_value_request(
    url: str,
    *,
    method: str = "GET",
    secret: str = "",
    payload: Mapping[str, Any] | None = None,
    timeout: float = 1_800,
) -> Any:
    body = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if payload is not None
        else None
    )
    headers = {"accept": "application/json"}
    if body is not None:
        headers["content-type"] = "application/json"
    if secret:
        headers["x-service-secret"] = secret
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{method} {url} returned HTTP {error.code}: {detail}"
        ) from error
    return json.loads(raw) if raw else {}


def _json_request(
    url: str,
    *,
    method: str = "GET",
    secret: str = "",
    payload: Mapping[str, Any] | None = None,
    timeout: float = 1_800,
) -> dict[str, Any]:
    parsed = _json_value_request(
        url,
        method=method,
        secret=secret,
        payload=payload,
        timeout=timeout,
    )
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{method} {url} returned a non-object response")
    return parsed


def _json_list_request(
    url: str,
    *,
    secret: str = "",
    timeout: float = 1_800,
) -> list[dict[str, Any]]:
    parsed = _json_value_request(
        url,
        secret=secret,
        timeout=timeout,
    )
    if not isinstance(parsed, list) or not all(
        isinstance(item, dict) for item in parsed
    ):
        raise RuntimeError(f"GET {url} returned a non-object-list response")
    return parsed


def _redis(
    redis_container: str,
    *arguments: str,
    input_text: str | None = None,
) -> str:
    return _run(
        (
            "docker",
            "exec",
            "-i",
            redis_container,
            "redis-cli",
            "--raw",
            "-n",
            str(ISOLATED_REDIS_DB),
            *arguments,
        ),
        input_text=input_text,
    ).stdout.strip()


def _wait_for_consumer(container_name: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        logs = _run(
            ("docker", "logs", container_name),
            check=False,
        )
        combined = logs.stdout + logs.stderr
        if f"Listening for jobs on '{JOB_QUEUE_KEY}'" in combined:
            return
        running = _run(
            (
                "docker",
                "inspect",
                "-f",
                "{{.State.Running}}",
                container_name,
            ),
            check=False,
        )
        if running.stdout.strip() == "false":
            raise RuntimeError(
                "isolated inference container stopped before queue startup:\n"
                + combined[-4_000:]
            )
        time.sleep(0.5)
    raise TimeoutError("isolated inference queue consumer did not start")


def _wait_for_job(
    redis_container: str,
    job_id: str,
    timeout: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    event_key = f"codecrow:analysis:events:{job_id}"
    deadline = time.monotonic() + timeout
    events: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        remaining = max(1, min(5, int(deadline - time.monotonic())))
        output = _redis(
            redis_container,
            "BRPOP",
            event_key,
            str(remaining),
        )
        if not output:
            continue
        lines = output.splitlines()
        if len(lines) < 2:
            raise RuntimeError(f"malformed Redis event response for {job_id}")
        event = json.loads(lines[-1])
        if not isinstance(event, dict):
            raise RuntimeError(f"non-object Redis event for {job_id}")
        events.append(event)
        if event.get("type") in {"error", "failed"}:
            raise RuntimeError(
                f"isolated review job {job_id} failed: {event.get('message')}"
            )
        if event.get("type") in {"final", "result"}:
            return events, event
    raise TimeoutError(f"isolated review job {job_id} did not finish")


def _artifact_path(
    artifact_directory: Path,
    final_event: Mapping[str, Any],
) -> Path:
    result = final_event.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError("final review event has no result object")
    metadata = result.get("promptArtifact")
    if not isinstance(metadata, Mapping):
        raise RuntimeError("final review result has no prompt artifact")
    filename = str(metadata.get("filename") or "")
    if not filename or Path(filename).name != filename:
        raise RuntimeError("prompt artifact filename is missing or unsafe")
    path = artifact_directory / filename
    if not path.is_file():
        raise RuntimeError(f"prompt artifact was not persisted: {filename}")
    return path


def _copy_artifact_for_audit(
    *,
    container_name: str,
    filename: str,
    destination: Path,
) -> Path:
    """Copy a mode-0600 capture without weakening its in-container permissions."""
    if Path(filename).name != filename:
        raise RuntimeError("prompt artifact filename is unsafe")
    _run((
        "docker",
        "cp",
        (
            f"{container_name}:"
            f"/app/logs/prompt-dry-runs/{filename}"
        ),
        str(destination),
    ))
    if not destination.is_file():
        raise RuntimeError("Docker did not copy the prompt artifact")
    return destination


def _relation_briefing_payloads(rendered_prompt: str) -> list[Mapping[str, Any]]:
    """Decode relation capsules embedded among ordinary prompt prose."""
    decoder = json.JSONDecoder()
    payloads: list[Mapping[str, Any]] = []
    cursor = 0
    while True:
        start = rendered_prompt.find("{", cursor)
        if start < 0:
            break
        try:
            candidate, consumed = decoder.raw_decode(rendered_prompt[start:])
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        cursor = start + max(1, consumed)
        if (
            isinstance(candidate, Mapping)
            and candidate.get("kind")
            == "proposed_tree_relation_briefing"
        ):
            payloads.append(candidate)
    return payloads


_ATTESTED_RELATION_FIELDS = (
    "kind",
    "source",
    "relation",
    "target",
    "sourceUnitId",
    "targetUnitId",
    "origin",
    "relatedPaths",
    "attributes",
)


def _canonical_relation_record(relation: Mapping[str, Any]) -> str:
    """Serialize evidence-identity fields, excluding query-local navigation.

    ``hop``/``depth`` describe distance from the current focus set. The harness
    preflight covers all three fixture paths while production briefs each Stage 1
    batch independently, so those values may legitimately differ for the same
    stable relation fact and evidence ID.
    """
    return json.dumps(
        {
            key: relation.get(key)
            for key in _ATTESTED_RELATION_FIELDS
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _relation_paths(
    relation: Mapping[str, Any],
    nodes: Mapping[str, str],
) -> set[str]:
    paths = {
        str(path)
        for path in relation.get("relatedPaths", ())
        if isinstance(path, str) and path
    }
    origin = relation.get("origin")
    if isinstance(origin, Mapping) and isinstance(origin.get("path"), str):
        paths.add(str(origin["path"]))
    for key in ("sourceUnitId", "targetUnitId"):
        unit_path = nodes.get(str(relation.get(key) or ""))
        if unit_path:
            paths.add(unit_path)
    return paths


def _trusted_relation_records(
    response: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Attest relation records to the exact preflight response."""
    evidence = response.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    raw_nodes = evidence.get("nodes") or response.get("nodes") or ()
    nodes = {
        str(node.get("unitId")): str(node.get("path"))
        for node in raw_nodes
        if isinstance(node, Mapping)
        and node.get("unitId")
        and node.get("path")
    }
    # Attest the exact response projection consumed by the Stage 1 capsule.
    # The composite may also expose a lower-level evidence relation table under
    # the same IDs; those records need not contain edge-local hop/navigation
    # fields. Mirroring the production priority prevents comparing two distinct
    # projections from the same preflight response.
    raw_relations = response.get("edges")
    if not isinstance(raw_relations, list):
        raw_relations = (
            evidence.get("relations")
            or response.get("relations")
            or response.get("results")
            or ()
        )
    records: dict[str, dict[str, Any]] = {}
    for relation in raw_relations:
        if not isinstance(relation, Mapping):
            continue
        evidence_id = str(relation.get("evidenceId") or "")
        if not re.fullmatch(r"relation:[0-9a-f]{64}", evidence_id):
            continue
        record = {
            "canonical": _canonical_relation_record(relation),
            "paths": tuple(sorted(_relation_paths(relation, nodes))),
        }
        previous = records.get(evidence_id)
        if previous is not None and previous != record:
            # One evidence ID cannot attest two different facts. Excluding a
            # collision makes the acceptance gate fail closed without making
            # optional production enrichment fail closed.
            records[evidence_id] = {}
            continue
        records[evidence_id] = record
    return records


def _paired_relation_evidence_ids(
    payload: Mapping[str, Any],
    *,
    changed_path: str,
    related_path: str,
    trusted_relation_records: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    nodes = {
        str(node.get("unitId")): str(node.get("path"))
        for node in payload.get("nodes", ())
        if isinstance(node, Mapping)
        and node.get("unitId")
        and node.get("path")
    }
    matches: list[str] = []
    for relation in payload.get("relations", ()):
        if not isinstance(relation, Mapping):
            continue
        relation_paths = _relation_paths(relation, nodes)
        evidence_id = str(relation.get("evidenceId") or "")
        trusted = trusted_relation_records.get(evidence_id)
        if (
            {changed_path, related_path}.issubset(relation_paths)
            and re.fullmatch(r"relation:[0-9a-f]{64}", evidence_id)
            and isinstance(trusted, Mapping)
            and trusted.get("canonical")
            == _canonical_relation_record(relation)
            and tuple(sorted(relation_paths)) == trusted.get("paths")
        ):
            matches.append(evidence_id)
    return sorted(set(matches))


def _correlate_stage1_prompts(
    prompts: Sequence[Mapping[str, Any]],
    diagnostics: Sequence[Mapping[str, Any]],
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """Correlate parallel records by invariant content, never list order."""
    available = set(range(len(prompts)))
    correlated: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for diagnostic in diagnostics:
        batch_paths = [
            str(path)
            for path in diagnostic.get("batchPaths", ())
            if isinstance(path, str) and path
        ]
        expected_characters = int(diagnostic.get("totalPromptChars") or 0)
        candidates = [
            index
            for index in sorted(available)
            for rendered in (
                str(prompts[index].get("renderedPrompt") or ""),
            )
            if (
                (not expected_characters or len(rendered) == expected_characters)
                and batch_paths
                and all(path in rendered for path in batch_paths)
            )
        ]
        if len(candidates) != 1:
            return []
        index = candidates[0]
        available.remove(index)
        correlated.append((prompts[index], diagnostic))
    return correlated if not available else []


def audit_expected_context(
    artifact: Mapping[str, Any],
    *,
    trusted_relation_records: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Check per-path exact related context without returning source text."""
    stage1_prompts = [
        item
        for item in artifact.get("prompts", ())
        if isinstance(item, Mapping) and item.get("stage") == "stage_1"
    ]
    assembly = [
        item
        for item in (
            artifact.get("promptAssemblyDiagnostics", {}).get("stage1", ())
            if isinstance(artifact.get("promptAssemblyDiagnostics"), Mapping)
            else ()
        )
        if isinstance(item, Mapping)
    ]
    correlated = _correlate_stage1_prompts(stage1_prompts, assembly)
    if len(stage1_prompts) != len(assembly) or not correlated:
        return {
            "status": "degraded",
            "failedPaths": sorted(EXPECTED_RELATED_PATHS),
            "paths": {},
        }

    per_path: dict[str, dict[str, Any]] = {}
    for changed_path, related_path in sorted(EXPECTED_RELATED_PATHS.items()):
        matching = [
            (prompt, diagnostics)
            for prompt, diagnostics in correlated
            if changed_path in diagnostics.get("batchPaths", ())
        ]
        matching_prompts = [
            str(prompt.get("renderedPrompt") or "")
            for prompt, _diagnostics in matching
        ]
        paired_evidence_ids = sorted({
            evidence_id
            for prompt in matching_prompts
            for payload in _relation_briefing_payloads(prompt)
            for evidence_id in _paired_relation_evidence_ids(
                payload,
                changed_path=changed_path,
                related_path=related_path,
                trusted_relation_records=trusted_relation_records,
            )
        })
        related_visible = bool(paired_evidence_ids)
        relation_evidence_visible = bool(paired_evidence_ids)
        structural_chars = sum(
            int(diagnostics.get("structuralContextChars") or 0)
            for _prompt, diagnostics in matching
        )
        per_path[changed_path] = {
            "stage1Owners": len(matching),
            "expectedRelatedPathVisible": related_visible,
            "relationEvidenceVisible": relation_evidence_visible,
            "pairedRelationEvidenceCount": len(paired_evidence_ids),
            "structuralCharacters": structural_chars,
        }

    failed_paths = [
        path
        for path, evidence in per_path.items()
        if (
            evidence["stage1Owners"] != 1
            or not evidence["expectedRelatedPathVisible"]
            or not evidence["relationEvidenceVisible"]
            or evidence["structuralCharacters"] <= 0
        )
    ]
    return {
        "status": "passed" if not failed_paths else "degraded",
        "failedPaths": failed_paths,
        "paths": per_path,
    }


def require_expected_context(report: Mapping[str, Any]) -> None:
    """Fail the isolated fixture when its known relations are prompt-invisible."""
    if report.get("status") == "passed":
        return
    failed_paths = report.get("failedPaths")
    rendered_paths = ", ".join(
        str(path) for path in failed_paths or ()
    )
    path_diagnostics = report.get("paths")
    rendered_diagnostics = (
        json.dumps(path_diagnostics, sort_keys=True, separators=(",", ":"))
        if isinstance(path_diagnostics, Mapping)
        else "{}"
    )
    raise RuntimeError(
        "expected relation context audit failed"
        + (f": {rendered_paths}" if rendered_paths else "")
        + f"; diagnostics={rendered_diagnostics}"
    )


def _assert_no_connected_identity(value: Any) -> None:
    rendered = json.dumps(value, sort_keys=True).casefold()
    forbidden = [
        identity
        for identity in FORBIDDEN_CONNECTED_IDENTITIES
        if identity.casefold() in rendered
    ]
    structured_forbidden: list[str] = []

    def inspect(item: Any) -> None:
        if isinstance(item, Mapping):
            for raw_key, child in item.items():
                key = str(raw_key).casefold()
                if (
                    key in _PROJECT_IDENTITY_FIELDS
                    and child in FORBIDDEN_CONNECTED_PROJECT_IDS
                ):
                    structured_forbidden.append(f"{raw_key}={child}")
                if key in _REPOSITORY_IDENTITY_FIELDS and isinstance(
                    child, str
                ):
                    candidate = child.strip().casefold()
                    if key in {"repositorypath", "repository_path"}:
                        candidate = Path(candidate).name
                    if candidate in FORBIDDEN_CONNECTED_REPOSITORY_NAMES:
                        structured_forbidden.append(f"{raw_key}={child}")
                inspect(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                inspect(child)

    inspect(value)
    forbidden.extend(structured_forbidden)
    if forbidden:
        raise RuntimeError(
            "isolated replay contains connected repository identity: "
            + ", ".join(sorted(set(forbidden)))
        )


def _start_inference_container(
    *,
    container_name: str,
    image: str,
    network: str,
    inference_env_file: Path,
    artifact_directory: Path,
    service_secret: str,
) -> None:
    environment = dict(os.environ)
    environment["SERVICE_SECRET"] = service_secret
    environment["INTERNAL_API_SECRET"] = service_secret
    command = (
        "docker",
        "run",
        "--detach",
        "--rm",
        "--name",
        container_name,
        "--network",
        network,
        "--label",
        "codecrow.quality-scope=isolated-synthetic",
        "--volume",
        f"{inference_env_file.resolve()}:/app/.env:ro",
        "--volume",
        f"{artifact_directory.resolve()}:/app/logs/prompt-dry-runs",
        "--env",
        "SERVICE_SECRET",
        "--env",
        "INTERNAL_API_SECRET",
        "--env",
        f"REDIS_URL=redis://redis:6379/{ISOLATED_REDIS_DB}",
        "--env",
        "ANALYSIS_PROMPT_DRY_RUN_ENABLED=true",
        "--env",
        "ANALYSIS_PROMPT_DRY_RUN_SYNTHETIC_FINDINGS_PER_FILE=1",
        "--env",
        "ANALYSIS_PROMPT_DRY_RUN_SYNTHETIC_FINDINGS_MAX_TOTAL=12",
        "--env",
        "ANALYSIS_PROMPT_DRY_RUN_OUTPUT_DIR=/app/logs/prompt-dry-runs",
        "--env",
        "PROMPT_LOG_ENABLED=false",
        image,
    )
    _run(command, env=environment)
    _wait_for_consumer(container_name)


def _structural_generation_cleanup_paths(
    *,
    workspace: str,
    project: str,
    branch: str,
    revision: str,
    index_result: Mapping[str, Any],
) -> str:
    """Build cleanup paths bound to the exact sealed structural generation."""
    collection_target = str(
        index_result.get("collection_target")
        or index_result.get("namespace")
        or ""
    ).strip()
    generation_manifest = str(
        index_result.get("generation_manifest_sha256") or ""
    ).strip()
    if not collection_target or not generation_manifest:
        raise RuntimeError(
            "structural index response omitted its exact generation receipt"
        )

    branch_query = urllib.parse.urlencode({
        "collection_target": collection_target,
        "generation_revision": revision,
        "generation_manifest_sha256": generation_manifest,
    })
    return (
        "/index/"
        f"{urllib.parse.quote(workspace, safe='')}/"
        f"{urllib.parse.quote(project, safe='')}/branch/"
        f"{urllib.parse.quote(branch, safe='')}"
        f"?{branch_query}"
    )


def _structural_generation_discovery_path(
    *,
    workspace: str,
    project: str,
    branch: str,
) -> str:
    query = urllib.parse.urlencode({"branch": branch})
    return (
        "/index/"
        f"{urllib.parse.quote(workspace, safe='')}/"
        f"{urllib.parse.quote(project, safe='')}/revisions"
        f"?{query}"
    )


def _review_generation_cleanup_receipt(
    response: Mapping[str, Any],
) -> dict[str, str]:
    """Extract exact deletion coordinates without accepting review identity.

    A request-scoped proposed generation can be created even when its returned
    branch/revision/freshness fields fail the acceptance gate. Cleanup must retain
    the opaque target, manifest, and returned coordinates before strict identity
    validation raises; proposed-tree generations are intentionally absent from
    persistent branch discovery.
    """
    snapshot = response.get("snapshot")
    provenance = response.get("provenance")
    if not isinstance(snapshot, Mapping) or not isinstance(provenance, Mapping):
        raise RuntimeError("review context omitted its generation provenance")
    collection_target = str(
        provenance.get("collectionTarget") or ""
    ).strip()
    revision = str(
        snapshot.get("revision") or snapshot.get("sourceRevision") or ""
    ).strip()
    branch = str(snapshot.get("branch") or "").strip()
    manifest = str(
        snapshot.get("generationManifestSha256") or ""
    ).strip()
    if (
        not collection_target
        or not revision
        or not branch
        or not _GENERATION_MANIFEST.fullmatch(manifest)
    ):
        raise RuntimeError(
            "review context returned an incomplete generation cleanup receipt"
        )
    return {
        "collection_target": collection_target,
        "generation_manifest_sha256": manifest,
        "repository_revision": revision,
        "branch": branch,
    }


def _review_generation_receipt(
    response: Mapping[str, Any],
    *,
    expected_branch: str,
    expected_base_revision: str,
    expected_source_revision: str,
    expected_base_collection_target: str,
    expected_base_generation_manifest_sha256: str,
) -> dict[str, str]:
    snapshot = response.get("snapshot")
    freshness = response.get("freshness")
    if not isinstance(snapshot, Mapping) or not isinstance(freshness, Mapping):
        raise RuntimeError("review context omitted its generation provenance")
    cleanup_receipt = _review_generation_cleanup_receipt(response)
    if (
        response.get("status") != "ready"
        or snapshot.get("kind") != "proposed_tree"
    ):
        raise RuntimeError(
            "review context returned an incomplete generation receipt"
        )
    expected_snapshot = {
        "branch": expected_branch,
        "revision": expected_source_revision,
        "baseRevision": expected_base_revision,
        "sourceRevision": expected_source_revision,
        "baseCollectionTarget": expected_base_collection_target,
        "baseGenerationManifestSha256": (
            expected_base_generation_manifest_sha256
        ),
    }
    mismatches = {
        key: {"expected": expected, "actual": snapshot.get(key)}
        for key, expected in expected_snapshot.items()
        if snapshot.get(key) != expected
    }
    if freshness.get("state") != "exact_proposed_tree":
        mismatches["freshness.state"] = {
            "expected": "exact_proposed_tree",
            "actual": freshness.get("state"),
        }
    for key, expected in (
        ("baseRevision", expected_base_revision),
        ("sourceRevision", expected_source_revision),
    ):
        if freshness.get(key) != expected:
            mismatches[f"freshness.{key}"] = {
                "expected": expected,
                "actual": freshness.get(key),
            }
    if mismatches:
        raise RuntimeError(
            "review context returned the wrong exact proposed-tree identity: "
            + json.dumps(mismatches, sort_keys=True, separators=(",", ":"))
        )
    return cleanup_receipt


def _delete_structural_generation(
    *,
    rag_url: str,
    service_secret: str,
    workspace: str,
    project: str,
    receipt: Mapping[str, Any],
    timeout: float,
) -> dict[str, Any]:
    revision = str(receipt.get("repository_revision") or "").strip()
    branch = str(receipt.get("branch") or "").strip()
    if not revision or not branch:
        raise RuntimeError("structural cleanup receipt omitted branch/revision")
    path = _structural_generation_cleanup_paths(
        workspace=workspace,
        project=project,
        branch=branch,
        revision=revision,
        index_result=receipt,
    )
    result = _json_request(
        f"{rag_url}{path}",
        method="DELETE",
        secret=service_secret,
        timeout=timeout,
    )
    if result.get("status") != "success":
        raise RuntimeError(
            "isolated structural generation cleanup was not acknowledged"
        )
    return result


def _queue_review(
    redis_container: str,
    request: ReviewRequestDto,
    job_id: str,
    timeout: float,
    *,
    captured_request_payload: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    event_key = f"codecrow:analysis:events:{job_id}"
    _redis(redis_container, "DEL", event_key)
    request_payload = _queue_request_payload(
        request,
        captured_request_payload=captured_request_payload,
    )
    payload = json.dumps(
        {
            "job_id": job_id,
            "request": request_payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    # ``redis-cli -x`` consumes the final command argument from stdin.  Passing
    # source-bearing JSON on the command line would expose it through process
    # listings and, without ``-x``, Redis receives an LPUSH with no value.
    _redis(
        redis_container,
        "-x",
        "LPUSH",
        JOB_QUEUE_KEY,
        input_text=payload,
    )
    return _wait_for_job(redis_container, job_id, timeout)


def _queue_request_payload(
    request: ReviewRequestDto,
    *,
    captured_request_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the exact Java wire map, changing only the replay run ID."""
    if captured_request_payload is not None:
        request_payload = dict(captured_request_payload)
        request_payload["promptDryRunId"] = request.promptDryRunId
        # Validate the exact map without round-tripping it through Pydantic;
        # aliases, absent/default fields, and future Java-owned keys stay intact.
        validated = ReviewRequestDto.model_validate(request_payload)
        if validated.promptDryRunId != request.promptDryRunId:
            raise RuntimeError("captured Java request run ID was not preserved")
        return request_payload

    request_payload = request.model_dump(mode="json", by_alias=True)
    # Pydantic excludes internal sealed-generation fields from ordinary dumps
    # so they cannot leak into public response models. This replay is rebuilding
    # the production Redis envelope, where the Java host intentionally includes
    # those exact binding fields; preserve them explicitly.
    for field in (
        "ragCollectionTarget",
        "ragBaseGenerationManifestSha256",
        "ragBasePluginFingerprint",
        "ragBasePluginDescriptorFingerprint",
        "ragBasePluginImplementationFingerprint",
        "ragBaseIndexRepresentationFingerprint",
    ):
        value = getattr(request, field, None)
        if value is not None:
            request_payload[field] = value
    return request_payload


def _run_isolated_replay_locked(args: argparse.Namespace) -> dict[str, Any]:
    deployment_environment = _env_values(args.deployment_env_file)
    service_secret = deployment_environment.get("INTERNAL_API_SECRET", "")
    if not service_secret:
        raise RuntimeError(
            "INTERNAL_API_SECRET is missing from the deployment environment"
        )

    _json_request(f"{args.rag_url}/health", timeout=30)
    run_suffix = uuid.uuid4().hex[:12]
    project_namespace = f"neutral-mixed-{run_suffix}"
    container_name = f"codecrow-neutral-replay-{run_suffix}"
    rag_repo_path = f"/tmp/codecrow-quality-isolated-{run_suffix}"
    rag_overlay_path = f"{rag_repo_path}-overlay"
    job_ids = [
        f"neutral-mixed-{run_suffix}-run-{index}"
        for index in (1, 2)
    ]
    event_keys = [
        f"codecrow:analysis:events:{job_id}" for job_id in job_ids
    ]

    with tempfile.TemporaryDirectory(
        prefix="codecrow-neutral-deployed-",
    ) as temporary:
        temporary_root = Path(temporary)
        artifacts = temporary_root / "artifacts"
        artifacts.mkdir()
        # The one-off image runs as ``appuser``.  This directory contains only
        # generated prompt-gate artifacts and is deleted with the temporary
        # parent; explicit mode avoids the host umask turning 0777 into 0755.
        artifacts.chmod(0o777)
        repository = build_synthetic_repository(temporary_root)
        review_overlay = build_review_overlay(temporary_root, repository)
        _assert_no_connected_identity({
            "project": project_namespace,
            "diff": repository.raw_diff,
            "files": repository.head_files,
        })
        copied_repository = False
        copied_overlay = False
        cleanup_complete = False
        index_stats: dict[str, Any] = {}
        base_generation_receipt: dict[str, str] | None = None
        review_generation_receipt: dict[str, str] | None = None
        review_generation_cleanup_receipt: dict[str, str] | None = None
        review_query_payload: dict[str, Any] | None = None
        try:
            _redis(
                args.redis_container,
                "DEL",
                JOB_QUEUE_KEY,
                *event_keys,
            )
            _run((
                "docker",
                "exec",
                args.rag_container,
                "mkdir",
                "-p",
                rag_repo_path,
                rag_overlay_path,
            ))
            copied_repository = True
            copied_overlay = True
            _run((
                "docker",
                "cp",
                f"{repository.base_tree}/.",
                f"{args.rag_container}:{rag_repo_path}",
            ))
            _run((
                "docker",
                "cp",
                f"{review_overlay}/.",
                f"{args.rag_container}:{rag_overlay_path}",
            ))
            index_stats = _json_request(
                f"{args.rag_url}/index/repository",
                method="POST",
                secret=service_secret,
                payload={
                    "repo_path": rag_repo_path,
                    "workspace": ISOLATED_WORKSPACE,
                    "project": project_namespace,
                    "branch": ISOLATED_BRANCH,
                    "commit": repository.base_revision,
                    "preserve_other_branches": False,
                    "cleanup_repo_path": False,
                },
                timeout=args.timeout,
            )
            if int(index_stats.get("document_count") or 0) < len(BASE_FILES):
                raise RuntimeError(
                    "synthetic base index did not include every fixture file"
                )
            # Resolve the exact cleanup path immediately so a malformed index
            # response cannot be reported as a usable sealed generation.
            _structural_generation_cleanup_paths(
                workspace=ISOLATED_WORKSPACE,
                project=project_namespace,
                branch=ISOLATED_BRANCH,
                revision=repository.base_revision,
                index_result=index_stats,
            )
            collection_target = str(index_stats["collection_target"])
            generation_manifest_sha256 = str(
                index_stats["generation_manifest_sha256"]
            )
            base_generation_receipt = {
                "collection_target": collection_target,
                "generation_manifest_sha256": generation_manifest_sha256,
                "repository_revision": repository.base_revision,
                "branch": ISOLATED_BRANCH,
            }
            review_query_payload = {
                "workspace": ISOLATED_WORKSPACE,
                "project": project_namespace,
                "target_branch": ISOLATED_BRANCH,
                "base_revision": repository.base_revision,
                "source_revision": repository.head_revision,
                "target_repo_path": rag_repo_path,
                "review_overlay_path": rag_overlay_path,
                "base_collection_target": collection_target,
                "base_generation_manifest_sha256": (
                    generation_manifest_sha256
                ),
                "focus_paths": list(repository.changed_files),
                "question": "Review changed",
                "focus_symbols": [],
                "max_relations": 24,
                "max_source_windows": 2,
                "max_source_characters": 4_000,
            }
            review_preflight = _json_request(
                f"{args.rag_url}/query/review-context",
                method="POST",
                secret=service_secret,
                payload=review_query_payload,
                timeout=args.timeout,
            )
            if review_preflight.get("status") != "ready":
                raise RuntimeError(
                    "synthetic proposed-tree relation preflight was unavailable"
                )
            review_generation_cleanup_receipt = (
                _review_generation_cleanup_receipt(review_preflight)
            )
            review_generation_receipt = _review_generation_receipt(
                review_preflight,
                expected_branch=ISOLATED_BRANCH,
                expected_base_revision=repository.base_revision,
                expected_source_revision=repository.head_revision,
                expected_base_collection_target=collection_target,
                expected_base_generation_manifest_sha256=(
                    generation_manifest_sha256
                ),
            )
            trusted_relation_records = _trusted_relation_records(
                review_preflight
            )
            if not trusted_relation_records:
                raise RuntimeError(
                    "synthetic proposed-tree preflight returned no canonical "
                    "relations to attest prompt delivery"
                )

            java_request, java_producer, java_request_payload = (
                build_java_review_request(
                    repository,
                    project_namespace=project_namespace,
                    temporary_root=temporary_root,
                    java_ecosystem=args.java_ecosystem,
                    plugin_directory=args.java_plugin_directory,
                    local_repo_path=rag_repo_path,
                    review_overlay_path=rag_overlay_path,
                    collection_target=collection_target,
                    generation_manifest_sha256=generation_manifest_sha256,
                    include_request_payload=True,
                )
            )

            # If startup or readiness fails after `docker run`, the
            # unconditional final stop still removes the known container name.
            _start_inference_container(
                container_name=container_name,
                image=args.inference_image,
                network=args.network,
                inference_env_file=args.inference_env_file,
                artifact_directory=artifacts,
                service_secret=service_secret,
            )
            _run((
                "docker",
                "exec",
                "--user",
                "0",
                container_name,
                "mkdir",
                "-p",
                rag_repo_path,
                rag_overlay_path,
            ))
            _run((
                "docker",
                "cp",
                f"{repository.base_tree}/.",
                f"{container_name}:{rag_repo_path}",
            ))
            _run((
                "docker",
                "cp",
                f"{review_overlay}/.",
                f"{container_name}:{rag_overlay_path}",
            ))

            runs: list[dict[str, Any]] = []
            prompt_digests: list[str] = []
            for job_id in job_ids:
                request = java_request.model_copy(
                    update={"promptDryRunId": job_id},
                )
                queued_request_payload = _queue_request_payload(
                    request,
                    captured_request_payload=java_request_payload,
                )
                queued_request_digest = hashlib.sha256(
                    json.dumps(
                        queued_request_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                events, final_event = _queue_review(
                    args.redis_container,
                    request,
                    job_id,
                    args.timeout,
                    captured_request_payload=java_request_payload,
                )
                artifact_path = _artifact_path(artifacts, final_event)
                readable_artifact = _copy_artifact_for_audit(
                    container_name=container_name,
                    filename=artifact_path.name,
                    destination=(
                        temporary_root / f"audited-{job_id}.json"
                    ),
                )
                artifact = json.loads(
                    readable_artifact.read_text(encoding="utf-8")
                )
                _assert_no_connected_identity(artifact)
                if DUMMY_REVIEW_KEY in json.dumps(artifact):
                    raise RuntimeError(
                        "dummy review credential leaked into prompt artifact"
                    )
                audit = audit_prompt_dry_run(
                    artifact,
                    max_stage1_estimated_input_tokens=(
                        args.max_stage1_estimated_input_tokens
                    ),
                    expected_review_identity={
                        "projectId": ISOLATED_PROJECT_ID,
                        "analysisType": request.analysisType,
                        "pullRequestId": ISOLATED_PR_NUMBER,
                        "targetBranch": ISOLATED_BRANCH,
                        "sourceBranch": ISOLATED_SOURCE_BRANCH,
                        "headRevision": repository.head_revision,
                        "baseRevision": repository.base_revision,
                        "changedFiles": list(repository.changed_files),
                        "deletedFiles": [],
                        "rawDiffSha256": hashlib.sha256(
                            repository.raw_diff.encode("utf-8")
                        ).hexdigest(),
                    },
                )
                expected_context = audit_expected_context(
                    artifact,
                    trusted_relation_records=trusted_relation_records,
                )
                if audit["status"] != "passed":
                    failed_checks = audit["diagnostics"]["failedChecks"]
                    event_detail = (
                        "; observed event states="
                        + ",".join(audit["diagnostics"]["eventStates"])
                        if "requiredPipelineEvents" in failed_checks
                        else ""
                    )
                    raise RuntimeError(
                        "prompt dry-run audit failed: "
                        + ", ".join(failed_checks)
                        + event_detail
                    )
                require_expected_context(expected_context)
                prompt_digest = audit["diagnostics"]["promptDigest"]
                prompt_digests.append(prompt_digest)
                runs.append({
                    "jobId": job_id,
                    "queuedRequestDigest": queued_request_digest,
                    "eventTypes": [
                        str(event.get("type") or "") for event in events
                    ],
                    "eventStates": [
                        str(event.get("state") or "")
                        for event in events
                        if event.get("state")
                    ],
                    "audit": audit,
                    "expectedContext": expected_context,
                })

            deterministic = (
                len(prompt_digests) == 2
                and prompt_digests[0] == prompt_digests[1]
            )
            if not deterministic:
                raise RuntimeError(
                    "deployed replay prompts differ for the same immutable input"
                )

            _run(("docker", "stop", container_name))
            _redis(
                args.redis_container,
                "DEL",
                JOB_QUEUE_KEY,
                *event_keys,
            )
            if (
                review_generation_receipt is None
                or base_generation_receipt is None
            ):
                raise RuntimeError(
                    "isolated replay lost a created generation receipt"
                )
            cleanup_receipts = (
                review_generation_receipt,
                base_generation_receipt,
            )
            for receipt in cleanup_receipts:
                _delete_structural_generation(
                    rag_url=args.rag_url,
                    service_secret=service_secret,
                    workspace=ISOLATED_WORKSPACE,
                    project=project_namespace,
                    receipt=receipt,
                    timeout=120,
                )
            # Repeating each exact deletion is a source-free absence check for
            # repository and proposed-tree generations alike. The discovery API
            # intentionally excludes proposed-tree receipts.
            for receipt in cleanup_receipts:
                cleanup_path = _structural_generation_cleanup_paths(
                    workspace=ISOLATED_WORKSPACE,
                    project=project_namespace,
                    branch=str(receipt["branch"]),
                    revision=str(receipt["repository_revision"]),
                    index_result=receipt,
                )
                absence = _json_request(
                    f"{args.rag_url}{cleanup_path}",
                    method="DELETE",
                    secret=service_secret,
                    timeout=120,
                )
                if absence.get("status") != "not_found":
                    raise RuntimeError(
                        "deleted structural generation remained addressable"
                    )
            discovery_path = _structural_generation_discovery_path(
                workspace=ISOLATED_WORKSPACE,
                project=project_namespace,
                branch=ISOLATED_BRANCH,
            )
            if _json_list_request(
                f"{args.rag_url}{discovery_path}",
                secret=service_secret,
                timeout=120,
            ):
                raise RuntimeError(
                    "isolated repository generation remained discoverable"
                )
            _run((
                "docker",
                "exec",
                "--user",
                "0",
                args.rag_container,
                "rm",
                "-rf",
                rag_repo_path,
                rag_overlay_path,
            ))
            copied_repository = False
            copied_overlay = False
            cleanup_complete = True

            report = {
                "status": "passed",
                "scope": (
                    "isolated synthetic Redis/RAG prompt-context replay; "
                    "not candidate-generation precision or recall"
                ),
                "isolation": {
                    "localGitRemoteCount": 0,
                    "redisDatabase": ISOLATED_REDIS_DB,
                    "workspace": ISOLATED_WORKSPACE,
                    "project": project_namespace,
                    "connectedProjectCreated": False,
                    "reviewProviderCalls": 0,
                },
                "snapshot": {
                    "baseRevision": repository.base_revision,
                    "headRevision": repository.head_revision,
                    "targetBranch": ISOLATED_BRANCH,
                    "sourceBranch": ISOLATED_SOURCE_BRANCH,
                    "changedFiles": list(repository.changed_files),
                },
                "javaProducer": java_producer,
                "index": {
                    key: value
                    for key, value in index_stats.items()
                    if key not in {"errors", "failed_files"}
                },
                "runs": runs,
                "determinism": {
                    "promptDigests": prompt_digests,
                    "contentEquivalentModuloParallelOrder": deterministic,
                },
                "cleanup": {
                    "inferenceContainerRemoved": True,
                    "redisQueueRemoved": True,
                    "structuralGenerationsDeleted": len(cleanup_receipts),
                    "knownStructuralGenerationsRemaining": 0,
                    "exactAbsenceRechecked": True,
                    "copiedRepositoryRemoved": True,
                    "copiedReviewOverlayRemoved": True,
                },
            }
            _assert_no_connected_identity(report)
            return report
        finally:
            cleanup_errors: list[str] = []
            # `docker run` may succeed and readiness may fail before the caller
            # can record startup. Stopping an absent `--rm` name is harmless.
            stop_result = _run(
                ("docker", "stop", container_name),
                check=False,
            )
            stop_detail = (stop_result.stdout + stop_result.stderr).strip()
            if (
                stop_result.returncode != 0
                and "No such container" not in stop_detail
            ):
                cleanup_errors.append(
                    "inference container stop failed: " + stop_detail[-500:]
                )
            try:
                _redis(
                    args.redis_container,
                    "DEL",
                    JOB_QUEUE_KEY,
                    *event_keys,
                )
            except Exception as exception:
                cleanup_errors.append(
                    "isolated Redis cleanup failed: " + str(exception)
                )
            if not cleanup_complete:
                if (
                    review_generation_cleanup_receipt is None
                    and review_query_payload is not None
                ):
                    try:
                        review_response = _json_request(
                            f"{args.rag_url}/query/review-context",
                            method="POST",
                            secret=service_secret,
                            payload=review_query_payload,
                            timeout=120,
                        )
                        review_generation_cleanup_receipt = (
                            _review_generation_cleanup_receipt(review_response)
                        )
                    except Exception as exception:
                        cleanup_errors.append(
                            "proposed-tree receipt recovery failed: "
                            + str(exception)
                        )
                fallback_receipts: list[Mapping[str, Any]] = []
                if review_generation_cleanup_receipt is not None:
                    fallback_receipts.append(
                        review_generation_cleanup_receipt
                    )
                if base_generation_receipt is not None:
                    fallback_receipts.append(base_generation_receipt)
                else:
                    try:
                        discovery_path = _structural_generation_discovery_path(
                            workspace=ISOLATED_WORKSPACE,
                            project=project_namespace,
                            branch=ISOLATED_BRANCH,
                        )
                        fallback_receipts.extend(_json_list_request(
                            f"{args.rag_url}{discovery_path}",
                            secret=service_secret,
                            timeout=120,
                        ))
                    except Exception as exception:
                        cleanup_errors.append(
                            "base-generation discovery failed: "
                            + str(exception)
                        )
                seen_targets: set[str] = set()
                for receipt in fallback_receipts:
                    target = str(
                        receipt.get("collection_target") or ""
                    ).strip()
                    if not target or target in seen_targets:
                        continue
                    seen_targets.add(target)
                    try:
                        _delete_structural_generation(
                            rag_url=args.rag_url,
                            service_secret=service_secret,
                            workspace=ISOLATED_WORKSPACE,
                            project=project_namespace,
                            receipt=receipt,
                            timeout=120,
                        )
                    except Exception as exception:
                        cleanup_errors.append(
                            "structural generation cleanup failed for "
                            + target
                            + ": "
                            + str(exception)
                        )
            # The exact run-suffixed paths are safe to remove even when mkdir
            # or copy failed before the bookkeeping flags were set.
            path_cleanup = _run(
                (
                    "docker",
                    "exec",
                    "--user",
                    "0",
                    args.rag_container,
                    "rm",
                    "-rf",
                    rag_repo_path,
                    rag_overlay_path,
                ),
                check=False,
            )
            path_cleanup_detail = (
                path_cleanup.stdout + path_cleanup.stderr
            ).strip()
            if path_cleanup.returncode != 0:
                cleanup_errors.append(
                    "copied RAG path cleanup failed: "
                    + path_cleanup_detail[-500:]
                )
            if cleanup_errors:
                print(
                    "WARNING: isolated replay cleanup incomplete: "
                    + " | ".join(cleanup_errors),
                    file=sys.stderr,
                )


def run_isolated_replay(args: argparse.Namespace) -> dict[str, Any]:
    with _exclusive_isolated_state_lock():
        return _run_isolated_replay_locked(args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run two isolated provider-free reviews through deployed Redis and "
            "RAG without creating or selecting a live project."
        )
    )
    parser.add_argument(
        "--rag-url",
        default="http://127.0.0.1:8004",
    )
    parser.add_argument(
        "--rag-container",
        default="codecrow-rag-pipeline",
    )
    parser.add_argument(
        "--redis-container",
        default="codecrow-redis",
    )
    parser.add_argument(
        "--network",
        default="deployment_codecrow-network",
    )
    parser.add_argument(
        "--inference-image",
        default="deployment-inference-orchestrator:latest",
    )
    parser.add_argument(
        "--deployment-env-file",
        type=Path,
        default=REPOSITORY_ROOT / "deployment" / ".env",
    )
    parser.add_argument(
        "--inference-env-file",
        type=Path,
        default=(
            REPOSITORY_ROOT
            / "deployment"
            / "config"
            / "inference-orchestrator"
            / ".env"
        ),
    )
    parser.add_argument(
        "--java-ecosystem",
        type=Path,
        default=REPOSITORY_ROOT / "java-ecosystem",
    )
    parser.add_argument(
        "--java-plugin-directory",
        type=Path,
        default=REPOSITORY_ROOT / "analysis-plugins" / "build" / "java",
    )
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument(
        "--max-stage1-estimated-input-tokens",
        type=int,
        default=20_000,
    )
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    for path in (
        args.deployment_env_file,
        args.inference_env_file,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)
    if not args.java_ecosystem.is_dir():
        raise FileNotFoundError(args.java_ecosystem)
    if not args.java_plugin_directory.is_dir():
        raise FileNotFoundError(args.java_plugin_directory)
    if args.timeout <= 0:
        raise ValueError("timeout must be positive")
    if args.max_stage1_estimated_input_tokens <= 0:
        raise ValueError("Stage 1 token ceiling must be positive")

    report = run_isolated_replay(args)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
