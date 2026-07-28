"""Capture a real BYOK fallback/plugin pair without a connected CodeCrow project.

The command accepts one remote-free local Git repository and two immutable
commits. It runs the production Java request builder, isolated Redis/RAG
namespaces, and the normal inference queue twice: once with an empty plugin
catalog and once with the assembled catalog. Review and embedding provider calls
are impossible unless the caller supplies the explicit spend acknowledgement.

This is an operator tool for creating paired evidence. It does not label
findings, infer provider cost, or make a precision/recall claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .capture_pair_evaluation import (
    _validate_capture,
    create_template,
)
from .isolated_deployed_replay import (
    ISOLATED_PR_NUMBER,
    ISOLATED_PROJECT_ID,
    ISOLATED_REDIS_DB,
    ISOLATED_WORKSPACE,
    JOB_QUEUE_KEY,
    REPOSITORY_ROOT,
    SyntheticRepository,
    _assert_no_connected_identity,
    _exclusive_isolated_state_lock,
    _env_values,
    _qdrant_collections_for_project,
    _queue_review,
    _redis,
    _run,
    _wait_for_consumer,
    build_java_review_request,
)
from .isolated_paired_capture_preflight import audit_paired_requests


SPEND_ACKNOWLEDGEMENT = (
    "I_UNDERSTAND_THIS_CALLS_OPENROUTER_EMBEDDINGS_AND_THE_BYOK_REVIEW_PROVIDER"
)
_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_ALLOWED_REVIEW_CONFIG_FIELDS = frozenset({
    "provider",
    "model",
    "apiKey",
    "baseUrl",
    "customParameters",
    "maxAllowedTokens",
    "useMcpTools",
})
_ALLOWED_CASE_FIELDS = frozenset({
    "caseId",
    "repositoryPath",
    "baseCommit",
    "headCommit",
    "languages",
    "frameworks",
    "candidatePlugins",
    "requestPlugins",
})


@dataclass(frozen=True)
class LocalCaptureCase:
    case_id: str
    languages: tuple[str, ...]
    frameworks: tuple[str, ...]
    candidate_plugins: tuple[str, ...]
    request_plugins: tuple[str, ...]
    repository: SyntheticRepository
    changed_lines: int
    repository_files: int
    repository_bytes: int


@dataclass(frozen=True)
class ReviewProviderConfig:
    provider: str
    model: str
    api_key: str
    base_url: str | None
    custom_parameters: Mapping[str, Any] | None
    max_allowed_tokens: int | None


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty list")
    normalized = tuple(
        _non_empty_string(item, f"{field} entry")
        for item in value
    )
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} must not contain duplicates")
    return normalized


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exception:
        raise ValueError(f"cannot read JSON object from {path}") from exception
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def load_review_provider_config(path: Path) -> ReviewProviderConfig:
    """Load the only secret input and require owner-only file permissions."""
    try:
        metadata = path.stat()
    except OSError as exception:
        raise ValueError(f"cannot stat review provider config {path}") from exception
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("review provider config must be a regular file")
    if metadata.st_uid != os.getuid():
        raise ValueError("review provider config must be owned by the current user")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("review provider config must use owner-only permissions")

    payload = _load_object(path)
    unknown = sorted(set(payload) - _ALLOWED_REVIEW_CONFIG_FIELDS)
    if unknown:
        raise ValueError(
            "review provider config contains unknown fields: "
            + ", ".join(unknown)
        )
    if payload.get("useMcpTools") not in (None, False):
        raise ValueError("isolated paired capture forbids MCP/agent tools")
    custom_parameters = payload.get("customParameters")
    if custom_parameters is not None and not isinstance(custom_parameters, dict):
        raise ValueError("customParameters must be an object or null")
    max_tokens = payload.get("maxAllowedTokens")
    if (
        not isinstance(max_tokens, int)
        or isinstance(max_tokens, bool)
        or max_tokens < 1
    ):
        raise ValueError(
            "maxAllowedTokens is required and must be a positive integer"
        )
    base_url = payload.get("baseUrl")
    if base_url is not None:
        base_url = _non_empty_string(base_url, "baseUrl")
    return ReviewProviderConfig(
        provider=_non_empty_string(payload.get("provider"), "provider"),
        model=_non_empty_string(payload.get("model"), "model"),
        api_key=_non_empty_string(payload.get("apiKey"), "apiKey"),
        base_url=base_url,
        custom_parameters=custom_parameters,
        max_allowed_tokens=max_tokens,
    )


def require_spend_authorization(
    *,
    preflight_only: bool,
    acknowledgement: str | None,
) -> None:
    if preflight_only:
        return
    if acknowledgement != SPEND_ACKNOWLEDGEMENT:
        raise ValueError(
            "provider-backed capture requires --authorize-provider-spend "
            f"{SPEND_ACKNOWLEDGEMENT}"
        )


def _canonical_commit(repository: Path, value: Any, field: str) -> str:
    declared = _non_empty_string(value, field).casefold()
    if _COMMIT.fullmatch(declared) is None:
        raise ValueError(f"{field} must be a full lowercase commit ID")
    resolved = _run(
        ("git", "rev-parse", "--verify", f"{declared}^{{commit}}"),
        cwd=repository,
    ).stdout.strip().casefold()
    if resolved != declared:
        raise ValueError(f"{field} does not match the canonical commit ID")
    return resolved


def _changed_line_count(raw_diff: str) -> int:
    return sum(
        1
        for line in raw_diff.splitlines()
        if (
            (line.startswith("+") and not line.startswith("+++"))
            or (line.startswith("-") and not line.startswith("---"))
        )
    )


def _repository_blob_size(
    repository: Path,
    revision: str,
) -> tuple[int, int]:
    """Return conservative tracked-blob count and bytes at one revision."""
    output = _run(
        (
            "git",
            "ls-tree",
            "-r",
            "-l",
            "-z",
            "--full-tree",
            revision,
        ),
        cwd=repository,
    ).stdout
    file_count = 0
    byte_count = 0
    for record in output.split("\0"):
        if not record:
            continue
        metadata, separator, _path = record.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 4:
            raise RuntimeError("cannot measure immutable repository tree")
        _mode, object_type, _object_id, raw_size = fields
        if object_type != "blob":
            continue
        try:
            size = int(raw_size)
        except ValueError as exception:
            raise RuntimeError(
                "cannot measure immutable repository blob"
            ) from exception
        file_count += 1
        byte_count += size
    return file_count, byte_count


def load_local_capture_case(
    path: Path,
    *,
    temporary_root: Path,
    maximum_files: int,
    maximum_changed_lines: int,
    maximum_repository_files: int,
    maximum_repository_bytes: int,
) -> LocalCaptureCase:
    temporary_root.mkdir(parents=True, exist_ok=True)
    payload = _load_object(path)
    unknown = sorted(set(payload) - _ALLOWED_CASE_FIELDS)
    if unknown:
        raise ValueError(
            "case manifest contains unknown fields: "
            + ", ".join(unknown)
        )
    case_id = _non_empty_string(payload.get("caseId"), "caseId")
    if _CASE_ID.fullmatch(case_id) is None:
        raise ValueError(
            "caseId must use lowercase letters, digits, dots, dashes, or underscores"
        )
    repository_path = Path(
        _non_empty_string(payload.get("repositoryPath"), "repositoryPath")
    ).expanduser().resolve()
    if not repository_path.is_dir():
        raise ValueError("repositoryPath must be a local Git working tree")
    if not (repository_path / ".git").exists():
        raise ValueError("repositoryPath has no .git identity")
    remotes = [
        item
        for item in _run(
            ("git", "remote"),
            cwd=repository_path,
        ).stdout.splitlines()
        if item.strip()
    ]
    if remotes:
        raise ValueError(
            "isolated paired capture requires a remote-free Git copy"
        )

    base_revision = _canonical_commit(
        repository_path,
        payload.get("baseCommit"),
        "baseCommit",
    )
    head_revision = _canonical_commit(
        repository_path,
        payload.get("headCommit"),
        "headCommit",
    )
    ancestor = _run(
        ("git", "merge-base", "--is-ancestor", base_revision, head_revision),
        cwd=repository_path,
        check=False,
    )
    if ancestor.returncode != 0:
        raise ValueError("baseCommit must be an ancestor of headCommit")
    repository_files, repository_bytes = _repository_blob_size(
        repository_path,
        base_revision,
    )
    if repository_files > maximum_repository_files:
        raise ValueError(
            f"base repository contains {repository_files} tracked files; "
            f"maximum is {maximum_repository_files}"
        )
    if repository_bytes > maximum_repository_bytes:
        raise ValueError(
            f"base repository contains {repository_bytes} tracked bytes; "
            f"maximum is {maximum_repository_bytes}"
        )

    status_lines = [
        line
        for line in _run(
            (
                "git",
                "diff",
                "--name-status",
                "--no-renames",
                base_revision,
                head_revision,
            ),
            cwd=repository_path,
        ).stdout.splitlines()
        if line
    ]
    changed_files: list[str] = []
    for line in status_lines:
        fields = line.split("\t")
        if len(fields) != 2 or fields[0] not in {"A", "M", "T"}:
            raise ValueError(
                "isolated paired capture currently requires non-deleted, "
                "non-renamed text changes"
            )
        changed_files.append(fields[1])
    changed_files = sorted(changed_files)
    if not changed_files:
        raise ValueError("case diff contains no changed files")
    if len(changed_files) > maximum_files:
        raise ValueError(
            f"case changes {len(changed_files)} files; maximum is {maximum_files}"
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
        cwd=repository_path,
    ).stdout
    if not raw_diff.strip():
        raise ValueError("case diff is empty")
    changed_lines = _changed_line_count(raw_diff)
    if changed_lines > maximum_changed_lines:
        raise ValueError(
            f"case changes {changed_lines} lines; maximum is "
            f"{maximum_changed_lines}"
        )

    base_tree = temporary_root / f"{case_id}-base"
    _run(
        (
            "git",
            "clone",
            "--quiet",
            "--local",
            "--no-checkout",
            str(repository_path),
            str(base_tree),
        )
    )
    _run(
        ("git", "checkout", "--quiet", "--detach", base_revision),
        cwd=base_tree,
    )
    head_files: dict[str, str] = {}
    for changed_path in changed_files:
        try:
            head_files[changed_path] = _run(
                ("git", "show", f"{head_revision}:{changed_path}"),
                cwd=repository_path,
            ).stdout
        except UnicodeDecodeError as exception:
            raise ValueError(
                f"case changed file is not UTF-8 text: {changed_path}"
            ) from exception

    languages = _string_list(payload.get("languages"), "languages")
    frameworks_value = payload.get("frameworks", [])
    if not isinstance(frameworks_value, list):
        raise ValueError("frameworks must be a list")
    frameworks = tuple(
        _non_empty_string(item, "frameworks entry")
        for item in frameworks_value
    )
    if len(frameworks) != len(set(frameworks)):
        raise ValueError("frameworks must not contain duplicates")
    candidate_plugins = _string_list(
        payload.get("candidatePlugins"),
        "candidatePlugins",
    )
    request_plugins = _string_list(
        payload.get("requestPlugins", payload.get("candidatePlugins")),
        "requestPlugins",
    )
    unexpected_request_plugins = sorted(
        set(request_plugins) - set(candidate_plugins)
    )
    if unexpected_request_plugins:
        raise ValueError(
            "requestPlugins must be a subset of candidatePlugins: "
            + ", ".join(unexpected_request_plugins)
        )
    _assert_no_connected_identity({
        "caseId": case_id,
        "repositoryPath": str(repository_path),
        "rawDiff": raw_diff,
    })
    return LocalCaptureCase(
        case_id=case_id,
        languages=languages,
        frameworks=frameworks,
        candidate_plugins=candidate_plugins,
        request_plugins=request_plugins,
        repository=SyntheticRepository(
            root=repository_path,
            base_tree=base_tree,
            base_revision=base_revision,
            head_revision=head_revision,
            raw_diff=raw_diff,
            changed_files=tuple(changed_files),
            head_files=head_files,
        ),
        changed_lines=changed_lines,
        repository_files=repository_files,
        repository_bytes=repository_bytes,
    )


def _apply_review_provider(
    request: Any,
    config: ReviewProviderConfig,
    *,
    case_id: str,
) -> Any:
    return request.model_copy(update={
        "aiProvider": config.provider,
        "aiModel": config.model,
        "aiApiKey": config.api_key,
        "aiBaseUrl": config.base_url,
        "aiCustomParameters": (
            dict(config.custom_parameters)
            if config.custom_parameters is not None
            else None
        ),
        "maxAllowedTokens": config.max_allowed_tokens,
        "promptDryRun": False,
        "promptDryRunId": None,
        "useMcpTools": False,
        "accessToken": None,
        "oAuthClient": None,
        "oAuthSecret": None,
        "prTitle": f"Isolated paired quality capture: {case_id}",
    })


def _wait_for_rag(container_name: str, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        probe = _run(
            (
                "docker",
                "exec",
                container_name,
                "curl",
                "-sf",
                "http://127.0.0.1:8001/health",
            ),
            check=False,
        )
        if probe.returncode == 0:
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
            logs = _run(("docker", "logs", container_name), check=False)
            raise RuntimeError(
                "isolated RAG container stopped before health:\n"
                + (logs.stdout + logs.stderr)[-4_000:]
            )
        time.sleep(0.5)
    raise TimeoutError("isolated RAG container did not become healthy")


def _start_rag_container(
    *,
    container_name: str,
    image: str,
    network: str,
    rag_env_file: Path,
    service_secret: str,
    empty_plugins: Path | None,
) -> None:
    environment = dict(os.environ)
    environment["SERVICE_SECRET"] = service_secret
    environment["CODECROW_INTERNAL_SECRET"] = service_secret
    command = [
        "docker",
        "run",
        "--detach",
        "--rm",
        "--name",
        container_name,
        "--network",
        network,
        "--label",
        "codecrow.quality-scope=isolated-paid-pair",
        "--volume",
        f"{rag_env_file.resolve()}:/app/.env:ro",
        "--env",
        "SERVICE_SECRET",
        "--env",
        "CODECROW_INTERNAL_SECRET",
        "--env",
        "CODECROW_WEB_SERVER_URL=",
        "--env",
        f"REDIS_URL=redis://redis:6379/{ISOLATED_REDIS_DB}",
        "--env",
        "UVICORN_WORKERS=1",
    ]
    if empty_plugins is not None:
        command.extend([
            "--volume",
            f"{empty_plugins.resolve()}:/app/empty-plugins:ro",
            "--env",
            "CODECROW_PLUGINS_ROOT=/app/empty-plugins",
        ])
    command.append(image)
    _run(tuple(command), env=environment)
    _wait_for_rag(container_name)


def _container_json_request(
    container_name: str,
    *,
    method: str,
    path: str,
    payload: Mapping[str, Any] | None = None,
    timeout: float,
) -> dict[str, Any]:
    request = {
        "method": method,
        "path": path,
        "payload": payload,
        "timeout": timeout,
    }
    script = (
        "import json,os,sys,httpx;"
        "r=json.load(sys.stdin);"
        "h={'x-service-secret':os.environ['SERVICE_SECRET']};"
        "x=httpx.request(r['method'],'http://127.0.0.1:8001'+r['path'],"
        "json=r['payload'],headers=h,timeout=r['timeout']);"
        "x.raise_for_status();"
        "print(json.dumps(x.json() if x.content else {}))"
    )
    result = _run(
        ("docker", "exec", "-i", container_name, "python3", "-c", script),
        input_text=json.dumps(request),
    ).stdout.strip()
    parsed = json.loads(result or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError("isolated RAG request returned a non-object")
    return parsed


def _start_inference_container(
    *,
    container_name: str,
    image: str,
    network: str,
    inference_env_file: Path,
    rag_container_name: str,
    service_secret: str,
    empty_plugins: Path | None,
) -> None:
    environment = dict(os.environ)
    environment["SERVICE_SECRET"] = service_secret
    environment["INTERNAL_API_SECRET"] = service_secret
    environment["CODECROW_RAG_API_SECRET"] = service_secret
    command = [
        "docker",
        "run",
        "--detach",
        "--rm",
        "--name",
        container_name,
        "--network",
        network,
        "--label",
        "codecrow.quality-scope=isolated-paid-pair",
        "--volume",
        f"{inference_env_file.resolve()}:/app/.env:ro",
        "--env",
        "SERVICE_SECRET",
        "--env",
        "INTERNAL_API_SECRET",
        "--env",
        "CODECROW_RAG_API_SECRET",
        "--env",
        f"REDIS_URL=redis://redis:6379/{ISOLATED_REDIS_DB}",
        "--env",
        f"RAG_API_URL=http://{rag_container_name}:8001",
        "--env",
        "ANALYSIS_PROMPT_DRY_RUN_ENABLED=false",
        "--env",
        "REVIEW_QUALITY_CAPTURE_ENABLED=true",
        "--env",
        f"REVIEW_QUALITY_CAPTURE_PROJECT_IDS={ISOLATED_PROJECT_ID}",
        "--env",
        "REVIEW_QUALITY_CAPTURE_OUTPUT_DIR=/app/logs/review-quality-captures",
        "--env",
        "REVIEW_QUALITY_CAPTURE_MAX_FILES=2",
        "--env",
        "PROMPT_LOG_ENABLED=false",
    ]
    if empty_plugins is not None:
        command.extend([
            "--volume",
            f"{empty_plugins.resolve()}:/app/empty-plugins:ro",
            "--env",
            "CODECROW_PLUGINS_ROOT=/app/empty-plugins",
        ])
    command.append(image)
    _run(tuple(command), env=environment)
    _wait_for_consumer(container_name)


def _copy_quality_capture(
    *,
    container_name: str,
    destination: Path,
) -> Path:
    script = (
        "import json,pathlib;"
        "p=pathlib.Path('/app/logs/review-quality-captures');"
        "print(json.dumps(sorted(x.name for x in p.glob('*.json'))))"
    )
    output = _run(
        ("docker", "exec", container_name, "python3", "-c", script),
    ).stdout.strip()
    filenames = json.loads(output or "[]")
    if (
        not isinstance(filenames, list)
        or len(filenames) != 1
        or not isinstance(filenames[0], str)
        or Path(filenames[0]).name != filenames[0]
    ):
        raise RuntimeError(
            "isolated inference container did not produce exactly one safe capture"
        )
    _run((
        "docker",
        "cp",
        (
            f"{container_name}:/app/logs/review-quality-captures/"
            f"{filenames[0]}"
        ),
        str(destination),
    ))
    if not destination.is_file():
        raise RuntimeError("quality capture copy did not produce a file")
    os.chmod(destination, 0o600)
    return destination


def _run_capture_mode(
    *,
    mode: str,
    request: Any,
    case: LocalCaptureCase,
    output_path: Path,
    args: argparse.Namespace,
    service_secret: str,
    empty_plugins: Path,
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:12]
    rag_container = f"codecrow-quality-rag-{suffix}"
    inference_container = f"codecrow-quality-inference-{suffix}"
    repository_path = f"/tmp/codecrow-quality-capture-{suffix}"
    job_id = f"{case.case_id}-{mode}-{suffix}"
    event_key = f"codecrow:analysis:events:{job_id}"
    project_path = (
        f"/index/{ISOLATED_WORKSPACE}/{request.projectNamespace}/*"
    )
    pr_path = (
        f"/index/pr-files/{ISOLATED_WORKSPACE}/"
        f"{request.projectNamespace}/{ISOLATED_PR_NUMBER}"
    )
    baseline = mode == "fallback"
    rag_started = False
    inference_started = False
    capture_copied = False
    try:
        _redis(args.redis_container, "DEL", JOB_QUEUE_KEY, event_key)
        _start_rag_container(
            container_name=rag_container,
            image=args.rag_image,
            network=args.network,
            rag_env_file=args.rag_env_file,
            service_secret=service_secret,
            empty_plugins=empty_plugins if baseline else None,
        )
        rag_started = True
        _run(("docker", "exec", rag_container, "mkdir", "-p", repository_path))
        _run((
            "docker",
            "cp",
            f"{case.repository.base_tree}/.",
            f"{rag_container}:{repository_path}",
        ))
        index_result = _container_json_request(
            rag_container,
            method="POST",
            path="/index/repository",
            payload={
                "repo_path": repository_path,
                "workspace": ISOLATED_WORKSPACE,
                "project": request.projectNamespace,
                "branch": "main",
                "commit": case.repository.base_revision,
                "preserve_other_branches": False,
                "cleanup_repo_path": False,
            },
            timeout=args.timeout,
        )
        if int(index_result.get("document_count") or 0) < 1:
            raise RuntimeError("isolated base index contains no documents")

        _start_inference_container(
            container_name=inference_container,
            image=args.inference_image,
            network=args.network,
            inference_env_file=args.inference_env_file,
            rag_container_name=rag_container,
            service_secret=service_secret,
            empty_plugins=empty_plugins if baseline else None,
        )
        inference_started = True
        try:
            events, final_event = _queue_review(
                args.redis_container,
                request,
                job_id,
                args.timeout,
            )
        except Exception:
            try:
                _copy_quality_capture(
                    container_name=inference_container,
                    destination=output_path,
                )
                capture_copied = True
            except Exception:
                pass
            raise

        _copy_quality_capture(
            container_name=inference_container,
            destination=output_path,
        )
        capture_copied = True
        capture = _load_object(output_path)
        _assert_no_connected_identity(capture)
        _validate_capture(capture, output_path)
        expected_plugins = (
            [] if baseline else list(case.candidate_plugins)
        )
        actual_plugins = capture["pluginIdentity"]["repositoryPlugins"]
        if actual_plugins != expected_plugins:
            raise RuntimeError(
                f"{mode} capture plugin identity mismatch: {actual_plugins}"
            )
        return {
            "mode": mode,
            "capture": str(output_path),
            "captureDigest": capture["captureDigest"],
            "providerCalls": capture["providerCalls"],
            "repositoryPlugins": actual_plugins,
            "eventTypes": [str(event.get("type") or "") for event in events],
            "finalEventType": str(final_event.get("type") or ""),
            "indexDocumentCount": int(index_result.get("document_count") or 0),
        }
    finally:
        if inference_started:
            _run(("docker", "stop", inference_container), check=False)
        _redis(args.redis_container, "DEL", JOB_QUEUE_KEY, event_key)
        if rag_started:
            for cleanup_path in (pr_path, project_path):
                try:
                    _container_json_request(
                        rag_container,
                        method="DELETE",
                        path=cleanup_path,
                        timeout=120,
                    )
                except Exception:
                    pass
            remaining: list[str] = []
            cleanup_error: Exception | None = None
            try:
                remaining = _qdrant_collections_for_project(
                    rag_container,
                    workspace=ISOLATED_WORKSPACE,
                    project=request.projectNamespace,
                )
            except Exception as exception:
                cleanup_error = exception
            finally:
                _run(("docker", "stop", rag_container), check=False)
            if cleanup_error is not None:
                raise RuntimeError(
                    "could not verify isolated Qdrant cleanup"
                ) from cleanup_error
            if remaining:
                raise RuntimeError(
                    "isolated capture cleanup left Qdrant collections: "
                    + ", ".join(remaining)
                )
        if not capture_copied and output_path.exists():
            output_path.unlink()


def run_capture(args: argparse.Namespace) -> dict[str, Any]:
    require_spend_authorization(
        preflight_only=args.preflight_only,
        acknowledgement=args.authorize_provider_spend,
    )
    rag_environment = _env_values(args.rag_env_file)
    if rag_environment.get("EMBEDDING_PROVIDER", "").strip().casefold() != "openrouter":
        raise RuntimeError(
            "isolated paired capture requires EMBEDDING_PROVIDER=openrouter"
        )
    if not rag_environment.get("OPENROUTER_API_KEY", "").strip():
        raise RuntimeError("OpenRouter embedding API key is missing")
    if not rag_environment.get("OPENROUTER_MODEL", "").strip():
        raise RuntimeError("OpenRouter embedding model is missing")
    deployment_environment = _env_values(args.deployment_env_file)
    service_secret = deployment_environment.get("INTERNAL_API_SECRET", "").strip()
    if not service_secret:
        raise RuntimeError("INTERNAL_API_SECRET is missing")

    output_directory = args.output_directory.resolve()
    if output_directory.exists() and any(output_directory.iterdir()):
        raise ValueError("output directory must be absent or empty")
    output_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(output_directory, 0o700)

    with tempfile.TemporaryDirectory(
        prefix="codecrow-isolated-paid-pair-",
    ) as temporary:
        root = Path(temporary)
        empty_plugins = root / "empty-plugins"
        empty_plugins.mkdir()
        case = load_local_capture_case(
            args.case_manifest,
            temporary_root=root,
            maximum_files=args.maximum_files,
            maximum_changed_lines=args.maximum_changed_lines,
            maximum_repository_files=args.maximum_repository_files,
            maximum_repository_bytes=args.maximum_repository_bytes,
        )
        provider = load_review_provider_config(args.review_config)
        namespace = (
            f"neutral-paired-{case.case_id}-"
            f"{hashlib.sha256(case.repository.head_revision.encode()).hexdigest()[:10]}"
        )
        fallback_request, fallback_producer = build_java_review_request(
            case.repository,
            project_namespace=namespace,
            temporary_root=root,
            java_ecosystem=args.java_ecosystem,
            plugin_directory=empty_plugins,
            expected_repository_plugins=(),
        )
        candidate_request, candidate_producer = build_java_review_request(
            case.repository,
            project_namespace=namespace,
            temporary_root=root,
            java_ecosystem=args.java_ecosystem,
            plugin_directory=args.java_plugin_directory,
            expected_repository_plugins=case.request_plugins,
        )
        preflight = audit_paired_requests(
            fallback_request,
            candidate_request,
        )
        report: dict[str, Any] = {
            "kind": "review-quality-isolated-paired-capture",
            "status": (
                "preflight-passed"
                if args.preflight_only
                else "captures-completed"
            ),
            "case": {
                "caseId": case.case_id,
                "languages": list(case.languages),
                "frameworks": list(case.frameworks),
                "baseCommit": case.repository.base_revision,
                "headCommit": case.repository.head_revision,
                "changedFiles": list(case.repository.changed_files),
                "changedLines": case.changed_lines,
                "baseRepositoryFiles": case.repository_files,
                "baseRepositoryBytes": case.repository_bytes,
                "gitRemoteCount": 0,
                "requestPlugins": list(case.request_plugins),
                "effectiveCandidatePlugins": list(
                    case.candidate_plugins
                ),
            },
            "embedding": {
                "provider": "openrouter",
                "model": rag_environment["OPENROUTER_MODEL"],
                "callsExecuted": not args.preflight_only,
            },
            "review": {
                "provider": provider.provider,
                "model": provider.model,
                "callsExecuted": not args.preflight_only,
                "mcpTools": False,
            },
            "connectedProjectCreated": False,
            "preflight": preflight,
            "javaProducers": {
                "fallback": fallback_producer,
                "plugin-context": candidate_producer,
            },
            "captures": [],
        }
        if args.preflight_only:
            _assert_no_connected_identity(report)
            return report

        with _exclusive_isolated_state_lock():
            requests = {
                "fallback": _apply_review_provider(
                    fallback_request,
                    provider,
                    case_id=case.case_id,
                ),
                "plugin-context": _apply_review_provider(
                    candidate_request,
                    provider,
                    case_id=case.case_id,
                ),
            }
            capture_paths = {
                mode: output_directory / f"{case.case_id}-{mode}.json"
                for mode in requests
            }
            for mode in ("fallback", "plugin-context"):
                report["captures"].append(_run_capture_mode(
                    mode=mode,
                    request=requests[mode],
                    case=case,
                    output_path=capture_paths[mode],
                    args=args,
                    service_secret=service_secret,
                    empty_plugins=empty_plugins,
                ))

            template = create_template(
                case_id=case.case_id,
                languages=case.languages,
                frameworks=case.frameworks,
                captures=[
                    ("fallback", capture_paths["fallback"]),
                    ("plugin-context", capture_paths["plugin-context"]),
                ],
                baseline="fallback",
            )
            template_path = output_directory / f"{case.case_id}-labels.json"
            template_path.write_text(
                json.dumps(template, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.chmod(template_path, 0o600)
            report["labelTemplate"] = str(template_path)
        _assert_no_connected_identity(report)
        return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a disconnected full-pipeline fallback/plugin BYOK capture "
            "pair from a remote-free local Git repository."
        )
    )
    parser.add_argument("case_manifest", type=Path)
    parser.add_argument("--review-config", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--authorize-provider-spend")
    parser.add_argument("--maximum-files", type=int, default=20)
    parser.add_argument("--maximum-changed-lines", type=int, default=2_000)
    parser.add_argument("--maximum-repository-files", type=int, default=2_000)
    parser.add_argument(
        "--maximum-repository-bytes",
        type=int,
        default=20_000_000,
    )
    parser.add_argument("--timeout", type=float, default=1_800)
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
    parser.add_argument("--redis-container", default="codecrow-redis")
    parser.add_argument("--network", default="deployment_codecrow-network")
    parser.add_argument(
        "--rag-image",
        default="deployment-rag-pipeline:latest",
    )
    parser.add_argument(
        "--inference-image",
        default="deployment-inference-orchestrator:latest",
    )
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    for path in (
        args.case_manifest,
        args.review_config,
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
    if (
        args.maximum_files < 1
        or args.maximum_changed_lines < 1
        or args.maximum_repository_files < 1
        or args.maximum_repository_bytes < 1
    ):
        raise ValueError("case size limits must be positive")
    if args.timeout <= 0:
        raise ValueError("timeout must be positive")
    report = run_capture(args)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        os.chmod(args.output, 0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
