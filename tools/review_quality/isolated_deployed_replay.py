#!/usr/bin/env python3
"""Run a provider-free deployed review against a local synthetic repository.

This gate deliberately does not create a CodeCrow project or contact a VCS
provider.  It creates immutable local Git commits with no remote, publishes the
base snapshot into a unique RAG namespace, and sends the exact head diff through
an isolated Redis database to a one-off inference-orchestrator container.

The review LLM is replaced by the production prompt-capture adapter.  Embedding
requests remain enabled and this command refuses to run unless the configured RAG
provider is OpenRouter.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
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
from tools.source_tree_identity import (  # noqa: E402
    compute_repository_source_tree_sha256,
)

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


def build_repository_index_payload(
    *,
    repo_path: str,
    source_tree: Path,
    workspace: str,
    project: str,
    branch: str,
    commit: str,
) -> dict[str, Any]:
    """Build the canonical attested full-index request shared by replay tools."""
    return {
        "repo_path": repo_path,
        "workspace": workspace,
        "project": project,
        "branch": branch,
        "commit": commit,
        "source_tree_sha256": compute_repository_source_tree_sha256(
            source_tree
        ),
        "preserve_other_branches": False,
        "cleanup_repo_path": False,
    }


@contextmanager
def _exclusive_isolated_state_lock(
    path: Path = ISOLATED_STATE_LOCK,
):
    """Prevent concurrent tools from sharing Redis DB 15/Qdrant state."""
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
    """Create two deterministic local commits and an immutable base worktree."""
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

    _git(
        repository,
        "worktree",
        "add",
        "--quiet",
        "--detach",
        str(base_tree),
        base_revision,
    )
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
        useMcpTools=False,
        prTitle="Isolated neutral mixed-language context replay",
    )


def build_java_review_request(
    repository: SyntheticRepository,
    *,
    project_namespace: str,
    temporary_root: Path,
    java_ecosystem: Path,
    plugin_directory: Path,
    expected_repository_plugins: Sequence[str] = (
        "java",
        "python",
        "typescript",
    ),
) -> tuple[ReviewRequestDto, dict[str, Any]]:
    """Capture the request emitted by the production Java producer."""
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
    if request_payload.get("currentCommitHash") != repository.head_revision:
        raise RuntimeError("Java producer emitted the wrong head revision")
    if request_payload.get("changedFiles") != list(repository.changed_files):
        raise RuntimeError("Java producer changed-file manifest is not lossless")
    if request_payload.get("promptDryRun") is not True:
        raise RuntimeError("Java producer did not enable prompt dry-run")
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
    return ReviewRequestDto.model_validate(request_payload), {
        "kind": "production-java-queue-envelope",
        "configuredTargetBranch": ISOLATED_BRANCH,
        "sourceBranch": ISOLATED_SOURCE_BRANCH,
        "baseRevision": repository.base_revision,
        "headRevision": repository.head_revision,
        "changedFiles": list(repository.changed_files),
        "repositoryPlugins": list(capabilities["repositoryPlugins"]),
        "requestDigest": hashlib.sha256(
            canonical_request.encode("utf-8")
        ).hexdigest(),
        "reviewCredentialsPresent": False,
    }


def _env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _json_request(
    url: str,
    *,
    method: str = "GET",
    secret: str = "",
    payload: Mapping[str, Any] | None = None,
    timeout: float = 1_800,
) -> dict[str, Any]:
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
    parsed = json.loads(raw) if raw else {}
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{method} {url} returned a non-object response")
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
        if event.get("type") == "error":
            raise RuntimeError(
                f"isolated review job {job_id} failed: {event.get('message')}"
            )
        if event.get("type") == "final":
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


def audit_expected_context(
    artifact: Mapping[str, Any],
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
    if len(stage1_prompts) != len(assembly):
        return {
            "status": "failed",
            "failedPaths": sorted(EXPECTED_RELATED_PATHS),
            "paths": {},
        }

    per_path: dict[str, dict[str, Any]] = {}
    for changed_path, related_path in sorted(EXPECTED_RELATED_PATHS.items()):
        matching_indexes = [
            index
            for index, diagnostics in enumerate(assembly)
            if changed_path in diagnostics.get("batchPaths", ())
        ]
        related_visible = any(
            related_path in str(stage1_prompts[index].get("renderedPrompt") or "")
            for index in matching_indexes
        )
        rag_chars = sum(
            int(assembly[index].get("ragChars") or 0)
            for index in matching_indexes
        )
        per_path[changed_path] = {
            "stage1Owners": len(matching_indexes),
            "expectedRelatedPathVisible": related_visible,
            "ragCharacters": rag_chars,
        }

    failed_paths = [
        path
        for path, evidence in per_path.items()
        if (
            evidence["stage1Owners"] != 1
            or not evidence["expectedRelatedPathVisible"]
            or evidence["ragCharacters"] <= 0
        )
    ]
    return {
        "status": "passed" if not failed_paths else "failed",
        "failedPaths": failed_paths,
        "paths": per_path,
    }


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


def _qdrant_collections_for_project(
    rag_container: str,
    *,
    workspace: str,
    project: str,
) -> list[str]:
    """Return exact Qdrant collection names from inside the RAG network."""
    marker = f"codecrow_{workspace}__{project}"
    script = (
        "import json,os,httpx;"
        "r=httpx.get('http://qdrant:6333/collections',"
        "headers={'api-key':os.environ['QDRANT_API_KEY']});"
        "r.raise_for_status();"
        f"marker={marker!r};"
        "print(json.dumps(sorted("
        "item['name'] for item in r.json()['result']['collections'] "
        "if marker in item['name'])))"
    )
    output = _run((
        "docker",
        "exec",
        rag_container,
        "python3",
        "-c",
        script,
    )).stdout.strip()
    parsed = json.loads(output or "[]")
    if not isinstance(parsed, list) or not all(
        isinstance(item, str) for item in parsed
    ):
        raise RuntimeError("Qdrant collection inspection returned invalid data")
    return parsed


def _queue_review(
    redis_container: str,
    request: ReviewRequestDto,
    job_id: str,
    timeout: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    event_key = f"codecrow:analysis:events:{job_id}"
    _redis(redis_container, "DEL", event_key)
    payload = json.dumps(
        {
            "job_id": job_id,
            "request": request.model_dump(mode="json", by_alias=True),
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


def _run_isolated_replay_locked(args: argparse.Namespace) -> dict[str, Any]:
    rag_environment = _env_values(args.rag_env_file)
    embedding_provider = rag_environment.get(
        "EMBEDDING_PROVIDER",
        "ollama",
    ).strip().casefold()
    if embedding_provider != "openrouter":
        raise RuntimeError(
            "isolated deployed replay requires EMBEDDING_PROVIDER=openrouter"
        )
    embedding_model = rag_environment.get("OPENROUTER_MODEL", "").strip()
    if not embedding_model:
        raise RuntimeError("OPENROUTER_MODEL is required")

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
        _assert_no_connected_identity({
            "project": project_namespace,
            "diff": repository.raw_diff,
            "files": repository.head_files,
        })
        java_request, java_producer = build_java_review_request(
            repository,
            project_namespace=project_namespace,
            temporary_root=temporary_root,
            java_ecosystem=args.java_ecosystem,
            plugin_directory=args.java_plugin_directory,
        )

        project_index_url = (
            f"{args.rag_url}/index/"
            f"{ISOLATED_WORKSPACE}/{project_namespace}/*"
        )
        pr_url = (
            f"{args.rag_url}/index/pr-files/"
            f"{ISOLATED_WORKSPACE}/{project_namespace}/{ISOLATED_PR_NUMBER}"
        )
        container_started = False
        copied_repository = False
        cleanup_complete = False
        index_stats: dict[str, Any] = {}
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
            ))
            copied_repository = True
            _run((
                "docker",
                "cp",
                f"{repository.base_tree}/.",
                f"{args.rag_container}:{rag_repo_path}",
            ))
            index_stats = _json_request(
                f"{args.rag_url}/index/repository",
                method="POST",
                secret=service_secret,
                payload=build_repository_index_payload(
                    repo_path=rag_repo_path,
                    source_tree=repository.base_tree,
                    workspace=ISOLATED_WORKSPACE,
                    project=project_namespace,
                    branch=ISOLATED_BRANCH,
                    commit=repository.base_revision,
                ),
                timeout=args.timeout,
            )
            if int(index_stats.get("document_count") or 0) < len(BASE_FILES):
                raise RuntimeError(
                    "synthetic base index did not include every fixture file"
                )

            _start_inference_container(
                container_name=container_name,
                image=args.inference_image,
                network=args.network,
                inference_env_file=args.inference_env_file,
                artifact_directory=artifacts,
                service_secret=service_secret,
            )
            container_started = True

            runs: list[dict[str, Any]] = []
            prompt_digests: list[str] = []
            for job_id in job_ids:
                request = java_request.model_copy(
                    update={"promptDryRunId": job_id},
                )
                events, final_event = _queue_review(
                    args.redis_container,
                    request,
                    job_id,
                    args.timeout,
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
                )
                expected_context = audit_expected_context(artifact)
                if audit["status"] != "passed":
                    raise RuntimeError(
                        "prompt dry-run audit failed: "
                        + ", ".join(
                            audit["diagnostics"]["failedChecks"]
                        )
                    )
                if expected_context["status"] != "passed":
                    raise RuntimeError(
                        "expected context audit failed for: "
                        + ", ".join(expected_context["failedPaths"])
                    )
                prompt_digest = audit["diagnostics"]["promptDigest"]
                prompt_digests.append(prompt_digest)
                runs.append({
                    "jobId": job_id,
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
            container_started = False
            _redis(
                args.redis_container,
                "DEL",
                JOB_QUEUE_KEY,
                *event_keys,
            )
            _json_request(
                pr_url,
                method="DELETE",
                secret=service_secret,
                timeout=120,
            )
            _json_request(
                project_index_url,
                method="DELETE",
                secret=service_secret,
                timeout=120,
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
            ))
            copied_repository = False
            remaining_collections = _qdrant_collections_for_project(
                args.rag_container,
                workspace=ISOLATED_WORKSPACE,
                project=project_namespace,
            )
            if remaining_collections:
                raise RuntimeError(
                    "isolated Qdrant cleanup left collections: "
                    + ", ".join(remaining_collections)
                )
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
                    "embeddingProvider": embedding_provider,
                    "embeddingModel": embedding_model,
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
                    "byteStablePromptInputs": deterministic,
                },
                "cleanup": {
                    "inferenceContainerRemoved": True,
                    "redisQueueRemoved": True,
                    "ragProjectCollectionsRemaining": 0,
                    "copiedRepositoryRemoved": True,
                },
            }
            _assert_no_connected_identity(report)
            return report
        finally:
            if container_started:
                _run(
                    ("docker", "stop", container_name),
                    check=False,
                )
            _redis(
                args.redis_container,
                "DEL",
                JOB_QUEUE_KEY,
                *event_keys,
            )
            if not cleanup_complete:
                for cleanup_url in (pr_url, project_index_url):
                    try:
                        _json_request(
                            cleanup_url,
                            method="DELETE",
                            secret=service_secret,
                            timeout=120,
                        )
                    except Exception:
                        pass
            if copied_repository:
                _run(
                    (
                        "docker",
                        "exec",
                        "--user",
                        "0",
                        args.rag_container,
                        "rm",
                        "-rf",
                        rag_repo_path,
                    ),
                    check=False,
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
        "--rag-env-file",
        type=Path,
        default=(
            REPOSITORY_ROOT
            / "deployment"
            / "config"
            / "rag-pipeline"
            / ".env"
        ),
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
        args.rag_env_file,
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
