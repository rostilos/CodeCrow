from __future__ import annotations

import json
import math
import os
import re
import stat
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .config import secret_from_env
from .execution_corpus import (
    EXECUTION_CORPUS_KIND,
    assert_label_free_execution_value,
    validate_execution_corpus,
)
from .replay import (
    validate_replay_attestation,
    validate_replay_attestation_freshness,
    validate_replay_lock,
)
from .util import (
    canonical_json,
    configured_secret_values,
    deterministic_git_diff_command,
    hermetic_git_environment,
    public_config,
    read_json,
    redact_secret_text,
    require_no_secret_values,
    run,
    sha256_json,
    sha256_text,
    write_json,
)


RUN_KIND = "codecrow-magento2-analysis-run"
ATTEMPT_START_KIND = "codecrow-magento2-analysis-attempt-start"
ATTEMPT_RESULT_KIND = "codecrow-magento2-analysis-attempt-result"
QUALITY_CAPTURE_ARTIFACT_KIND = "review-quality-candidate-capture"
QUALITY_CAPTURE_RECEIPT_KIND = "review-quality-capture-receipt"
QUALITY_CAPTURE_EVENT_STATE = "review_quality_capture_completed"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
QUALITY_CAPTURE_FILENAME = re.compile(
    r"^project-([1-9][0-9]*)-review-([1-9][0-9]*|branch)-"
    r"[0-9a-f]{32}\.json$"
)
SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
QUALITY_CAPTURE_SECRET_KEYS = {
    "access_token",
    "accesstoken",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "oauth_client",
    "oauth_secret",
    "oauthclient",
    "oauthsecret",
    "password",
    "private_key",
    "privatekey",
    "secret",
    "token",
    "x-api-key",
}
ANALYSIS_MODEL_CONTROL_SENTINEL = "<varied-analysis-model>"
MAX_PAPER_ATTESTATION_AGE_SECONDS = 3_600
MAX_CASE_ATTEMPTS_LIMIT = 100
INDEX_SELECTION_POLICY_SCHEMA = "codecrow.repository-index-selection"


@dataclass(frozen=True)
class AnalysisExecutionContext:
    """Validated alternate-snapshot inputs for a non-primary analysis lane.

    The normal runner always analyzes the released H snapshot. A caller may
    supply this context only after independently validating another immutable
    snapshot/replay contract. The resulting manifest uses a distinct kind and
    carries the exact caller-provided bindings, so it cannot be consumed as a
    primary H analysis run.
    """

    run_kind: str
    cases: tuple[Mapping[str, Any], ...]
    replay_lock: Mapping[str, Any]
    replay_by_case: Mapping[str, Mapping[str, Any]]
    replay_attestation: Mapping[str, Any] | None
    replay_attestation_digest: str | None
    replay_lock_artifact: str
    replay_attestation_artifact: str | None
    manifest_bindings: Mapping[str, Any]
    run_id_prefix: str
    required_analysis_config_digest: str | None = None
    required_index_receipts: Mapping[str, Any] | None = None
    required_runtime_images: Mapping[str, Any] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def runtime_image_projection(value: Any) -> dict[str, Any]:
    """Return the stable runtime-image controls, excluding container instances."""

    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, Any] = {"required": value.get("required")}
    for service in ("analysis", "rag", "finalizer"):
        identity = value.get(service)
        if not isinstance(identity, Mapping):
            projected[service] = None
            continue
        projected[service] = {
            "imageId": identity.get("imageId"),
            "imageReference": identity.get("imageReference"),
        }
    return projected


def _json_request(
    url: str,
    *,
    method: str = "GET",
    payload: Any = None,
    secret: str = "",
    secret_header: str = "x-service-secret",
    timeout: int = 60,
) -> Any:
    body = None if payload is None else canonical_json(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if secret:
        headers[secret_header] = secret
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:4000]
        raise RuntimeError(
            f"{method} {url} returned HTTP {exc.code}: {detail}"
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"{method} {url} failed: {exc}") from exc
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{method} {url} returned invalid JSON") from exc


def _git_diff(repository: Path, base_sha: str, head_sha: str) -> str:
    return run(
        deterministic_git_diff_command(
            repository,
            "--full-index",
            base_sha,
            head_sha,
        ),
        env=hermetic_git_environment(offline=True),
    )


def _git_paths(
    repository: Path,
    base_sha: str,
    head_sha: str,
    *,
    diff_filter: str | None = None,
) -> list[str]:
    command = deterministic_git_diff_command(
        repository,
        "--name-only",
        "-z",
    )
    if diff_filter:
        command.append(f"--diff-filter={diff_filter}")
    command.extend([base_sha, head_sha])
    return sorted({
        value
        for value in run(
            command,
            env=hermetic_git_environment(offline=True),
        ).split("\0")
        if value
    })


def _git_blob(repository: Path, revision: str, path: str) -> bytes | None:
    completed = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(repository),
            "show",
            f"{revision}:{path}",
        ],
        env=hermetic_git_environment(offline=True),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return completed.stdout if completed.returncode == 0 else None


def _enrichment(
    repository: Path,
    *,
    head_sha: str,
    changed_paths: list[str],
    deleted_paths: set[str],
    max_file_bytes: int,
    max_total_bytes: int,
) -> dict[str, Any]:
    entries = []
    total = 0
    enriched = 0
    reasons: dict[str, int] = {}
    for path in changed_paths:
        content: str | None = None
        reason: str | None = None
        if path in deleted_paths:
            reason = "deleted_file"
        else:
            blob = _git_blob(repository, head_sha, path)
            if blob is None:
                reason = "source_unavailable"
            elif len(blob) > max_file_bytes:
                reason = "file_too_large"
            elif b"\0" in blob:
                reason = "binary_file"
            elif total + len(blob) > max_total_bytes:
                reason = "total_content_limit"
            else:
                try:
                    content = blob.decode("utf-8")
                except UnicodeDecodeError:
                    reason = "non_utf8_source"
        if reason is None and content is not None:
            size = len(content.encode("utf-8"))
            total += size
            enriched += 1
        else:
            size = 0
            reasons[reason or "unknown"] = reasons.get(reason or "unknown", 0) + 1
        entries.append(
            {
                "path": path,
                "content": content,
                "sizeBytes": size,
                "skipped": reason is not None,
                "skipReason": reason,
            }
        )
    return {
        "fileContents": entries,
        "fileMetadata": [],
        "relationships": [],
        "stats": {
            "totalFilesRequested": len(entries),
            "filesEnriched": enriched,
            "filesSkipped": len(entries) - enriched,
            "relationshipsFound": 0,
            "totalContentSizeBytes": total,
            "processingTimeMs": 0,
            "skipReasons": dict(sorted(reasons.items())),
        },
    }


def _redacted_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    safe = public_config(payload)
    safe["aiApiKey"] = "<redacted>"
    return safe


def _safe_request_digest(payload: Mapping[str, Any]) -> str:
    return sha256_json(_redacted_request(payload))


def _request_control_digest(payload: Mapping[str, Any]) -> str:
    normalized = _redacted_request(payload)
    normalized["aiModel"] = ANALYSIS_MODEL_CONTROL_SENTINEL
    return sha256_json(normalized)


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest.pop("runDigest", None)
    manifest["runDigest"] = sha256_json(manifest)
    write_json(path, manifest)


def _validate_output_intent(output_dir: Path, *, resume: bool) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError(f"analysis output path is not a directory: {output_dir}")
    entries = list(output_dir.iterdir()) if output_dir.is_dir() else []
    if resume:
        if not entries or not (output_dir / "run.json").is_file():
            raise ValueError(
                "--resume requires a non-empty analysis output directory "
                "containing run.json"
            )
        return
    if entries:
        raise ValueError(
            "analysis output directory is non-empty; use a new directory or "
            "--resume the existing run"
        )


def _validated_run_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or SAFE_RUN_ID.fullmatch(value) is None:
        raise ValueError(
            "analysis run ID must be a safe 1-256 character identifier"
        )
    return value


def _validate_project_fork_coordinates(
    config: Mapping[str, Any],
    replay_lock: Mapping[str, Any],
) -> None:
    fork_repository = replay_lock.get("forkRepository")
    if (
        not isinstance(fork_repository, str)
        or fork_repository.count("/") != 1
    ):
        raise ValueError("replay lock forkRepository is invalid")
    fork_owner, fork_name = fork_repository.split("/", 1)
    configured_owner = str(config.get("project_vcs_workspace") or "")
    configured_name = str(config.get("project_vcs_repo_slug") or "")
    if (
        not fork_owner
        or not fork_name
        or configured_owner != fork_owner
        or configured_name != fork_name
    ):
        raise ValueError(
            "analysis project VCS coordinates must exactly match replay lock "
            f"forkRepository {fork_repository!r}; configured "
            f"{configured_owner!r}/{configured_name!r}"
        )


def _attempt_policy(max_case_attempts: int) -> dict[str, Any]:
    return {
        "maxAttemptsPerCase": max_case_attempts,
        "attemptsPerInvocation": 1,
        "retryTrigger": "explicit-resume",
        "exhaustedCaseStatus": "failed",
    }


def _attempt_artifact_name(attempt_id: str, phase: str) -> str:
    if phase not in {"start", "result"}:
        raise ValueError(f"unknown analysis attempt artifact phase: {phase}")
    return f"raw/attempts/{attempt_id}-{phase}.json"


def _quality_capture_artifact_name(attempt_id: str) -> str:
    return f"raw/attempts/{attempt_id}-quality-capture.json"


def _quality_capture_container_source(
    config: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    pull_request_id: int | None = None,
) -> PurePosixPath:
    root_text = str(config.get("quality_capture_container_dir") or "")
    source_text = receipt.get("artifactContainerPath")
    if not isinstance(source_text, str) or not source_text:
        raise ValueError("quality capture artifact container path is missing")
    root = PurePosixPath(root_text)
    source = PurePosixPath(source_text)
    if (
        not root.is_absolute()
        or root == PurePosixPath("/")
        or ".." in root.parts
        or not source.is_absolute()
        or ".." in source.parts
        or root.as_posix() != root_text.rstrip("/")
        or source.as_posix() != source_text
        or source.parent != root
    ):
        raise ValueError(
            "quality capture artifact path is outside the configured "
            "container directory"
        )
    match = QUALITY_CAPTURE_FILENAME.fullmatch(source.name)
    if match is None:
        raise ValueError("quality capture artifact filename is invalid")
    project_id = config.get("project_id")
    if (
        isinstance(project_id, bool)
        or not isinstance(project_id, int)
        or int(match.group(1)) != project_id
    ):
        raise ValueError("quality capture artifact project identity mismatch")
    expected_review = (
        str(pull_request_id)
        if pull_request_id is not None
        else match.group(2)
    )
    if match.group(2) != expected_review:
        raise ValueError("quality capture artifact PR identity mismatch")
    return source


def _quality_capture_receipt_from_events(
    events: list[Mapping[str, Any]],
    *,
    provider: str,
    requested_model: str,
    expected_response_model: str,
    required: bool,
) -> dict[str, Any] | None:
    matches = [
        event
        for event in events
        if event.get("type") == "status"
        and event.get("state") == QUALITY_CAPTURE_EVENT_STATE
    ]
    if not matches:
        if required:
            raise RuntimeError(
                "analysis emitted no terminal model-call quality-capture receipt"
            )
        return None
    if len(matches) != 1:
        raise RuntimeError(
            "analysis emitted duplicate model-call quality-capture receipts"
        )
    receipt = matches[0].get("qualityCapture")
    return _validate_quality_capture_receipt(
        receipt,
        provider=provider,
        requested_model=requested_model,
        expected_response_model=expected_response_model,
    )


def _validate_quality_capture_receipt(
    value: Any,
    *,
    provider: str,
    requested_model: str,
    expected_response_model: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError("model-call quality-capture receipt is malformed")
    receipt = dict(value)
    expected_fields = {
        "kind",
        "status",
        "artifactContainerPath",
        "captureDigest",
        "provider",
        "requestedModel",
        "providerReportedModels",
        "providerModelEvidenceComplete",
        "modelBoundaryInvocations",
        "providerCalls",
        "calls",
        "receiptDigest",
    }
    if set(receipt) != expected_fields:
        raise RuntimeError("model-call quality-capture receipt fields are invalid")
    digest_payload = dict(receipt)
    declared_digest = digest_payload.pop("receiptDigest", None)
    if (
        not isinstance(declared_digest, str)
        or SHA256_HEX.fullmatch(declared_digest) is None
        or declared_digest != sha256_json(digest_payload)
    ):
        raise RuntimeError("model-call quality-capture receipt digest mismatch")
    if (
        receipt.get("kind") != QUALITY_CAPTURE_RECEIPT_KIND
        or receipt.get("status") != "completed"
        or receipt.get("provider") != provider
        or receipt.get("requestedModel") != requested_model
        or receipt.get("providerModelEvidenceComplete") is not True
        or not isinstance(receipt.get("captureDigest"), str)
        or SHA256_HEX.fullmatch(str(receipt["captureDigest"])) is None
    ):
        raise RuntimeError(
            "model-call quality-capture receipt identity or status mismatch"
        )
    reported_models = receipt.get("providerReportedModels")
    if reported_models != [expected_response_model]:
        raise RuntimeError(
            "analysis provider-reported model does not equal the expected "
            f"response model {expected_response_model!r}"
        )
    boundary_count = receipt.get("modelBoundaryInvocations")
    provider_calls = receipt.get("providerCalls")
    calls = receipt.get("calls")
    if (
        isinstance(boundary_count, bool)
        or not isinstance(boundary_count, int)
        or boundary_count < 1
        or isinstance(provider_calls, bool)
        or not isinstance(provider_calls, int)
        or provider_calls < 1
        or not isinstance(calls, list)
        or len(calls) != boundary_count
    ):
        raise RuntimeError("model-call quality-capture call counts are invalid")
    observed_provider_calls = 0
    completed_calls = 0
    for index, call in enumerate(calls, start=1):
        if not isinstance(call, Mapping) or set(call) != {
            "sequence",
            "stage",
            "status",
            "providerCallCount",
            "providerReportedModels",
            "promptDigest",
            "responseDigest",
        }:
            raise RuntimeError(
                "model-call quality-capture call receipt is malformed"
            )
        status = call.get("status")
        call_count = call.get("providerCallCount")
        call_models = call.get("providerReportedModels")
        if (
            call.get("sequence") != index
            or not isinstance(call.get("stage"), str)
            or not str(call["stage"]).strip()
            or status not in {"completed", "failed"}
            or isinstance(call_count, bool)
            or not isinstance(call_count, int)
            or call_count < 1
            or not isinstance(call_models, list)
            or call_models != sorted(set(call_models))
            or any(
                not isinstance(model_name, str) or not model_name
                for model_name in call_models
            )
            or not isinstance(call.get("promptDigest"), str)
            or SHA256_HEX.fullmatch(str(call["promptDigest"])) is None
        ):
            raise RuntimeError(
                "model-call quality-capture call evidence is invalid"
            )
        if status == "completed":
            completed_calls += 1
            if call_models != [expected_response_model]:
                raise RuntimeError(
                    "completed analysis call has incomplete or mismatched "
                    "provider-reported model evidence"
                )
            response_digest = call.get("responseDigest")
            if (
                not isinstance(response_digest, str)
                or SHA256_HEX.fullmatch(response_digest) is None
            ):
                raise RuntimeError(
                    "completed analysis call response digest is invalid"
                )
        elif call.get("responseDigest") is not None:
            response_digest = call.get("responseDigest")
            if (
                not isinstance(response_digest, str)
                or SHA256_HEX.fullmatch(response_digest) is None
            ):
                raise RuntimeError(
                    "failed analysis call response digest is invalid"
                )
        if any(model_name != expected_response_model for model_name in call_models):
            raise RuntimeError(
                "analysis call reports a model outside the expected response "
                "model"
            )
        observed_provider_calls += call_count
    if completed_calls < 1 or observed_provider_calls != provider_calls:
        raise RuntimeError(
            "model-call quality-capture provider-call evidence is incomplete"
        )
    return receipt


def _reconstruct_quality_capture_receipt(
    artifact: Mapping[str, Any],
    *,
    artifact_container_path: str,
) -> dict[str, Any]:
    call_receipts = []
    all_reported_models: set[str] = set()
    model_evidence_complete = True
    calls = artifact.get("calls")
    if not isinstance(calls, list):
        raise RuntimeError("quality capture artifact calls are malformed")
    for call in calls:
        if not isinstance(call, Mapping):
            raise RuntimeError("quality capture artifact call is malformed")
        provider_events = call.get("providerEvents")
        if not isinstance(provider_events, list):
            raise RuntimeError(
                "quality capture artifact provider events are malformed"
            )
        reported = sorted(
            {
                model_name
                for event in provider_events
                if isinstance(event, Mapping)
                for model_name in (event.get("providerReportedModels") or [])
                if isinstance(model_name, str) and model_name
            }
        )
        all_reported_models.update(reported)
        if call.get("status") == "completed" and not reported:
            model_evidence_complete = False
        call_receipts.append(
            {
                "sequence": call.get("sequence"),
                "stage": call.get("stage"),
                "status": call.get("status"),
                "providerCallCount": call.get("providerCallCount"),
                "providerReportedModels": reported,
                "promptDigest": call.get("promptDigest"),
                "responseDigest": call.get("responseDigest"),
            }
        )
    receipt = {
        "kind": QUALITY_CAPTURE_RECEIPT_KIND,
        "status": artifact.get("status"),
        "artifactContainerPath": artifact_container_path,
        "captureDigest": artifact.get("captureDigest"),
        "provider": artifact.get("provider"),
        "requestedModel": artifact.get("model"),
        "providerReportedModels": sorted(all_reported_models),
        "providerModelEvidenceComplete": model_evidence_complete,
        "modelBoundaryInvocations": artifact.get(
            "modelBoundaryInvocations"
        ),
        "providerCalls": artifact.get("providerCalls"),
        "calls": call_receipts,
    }
    receipt["receiptDigest"] = sha256_json(receipt)
    return receipt


def _quality_capture_is_secret_key(key: str) -> bool:
    lowered = key.strip().casefold()
    compact = re.sub(r"[^a-z0-9]", "", lowered)
    return (
        lowered in QUALITY_CAPTURE_SECRET_KEYS
        or compact in QUALITY_CAPTURE_SECRET_KEYS
        or compact.endswith("apikey")
        or compact.endswith("accesstoken")
        or compact.endswith("oauthsecret")
        or compact.endswith("privatekey")
    )


def _quality_capture_sanitize_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return "[REDACTED_INVALID_URL]"
    if not parsed.scheme or not parsed.netloc:
        return value.split("?", 1)[0].split("#", 1)[0]
    hostname = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError:
        return "[REDACTED_INVALID_URL]"
    if port:
        hostname = f"{hostname}:{port}"
    return urllib.parse.urlunsplit(
        (parsed.scheme, hostname, parsed.path, "", "")
    )


def _quality_capture_redact(value: Any, *, key: str = "") -> Any:
    if _quality_capture_is_secret_key(key):
        return "[REDACTED]" if value not in (None, "", {}, []) else value
    if isinstance(value, Mapping):
        return {
            str(child_key): _quality_capture_redact(
                child_value,
                key=str(child_key),
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [
            _quality_capture_redact(item)
            for item in value
        ]
    if (
        isinstance(value, str)
        and key.casefold().endswith(("baseurl", "base_url"))
    ):
        return _quality_capture_sanitize_url(value)
    return value


def _expected_quality_capture_request(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Mirror ReviewRequestDto's exact aliased, redacted capture snapshot."""

    snapshot = {
        "projectId": request.get("projectId"),
        "projectVcsWorkspace": request.get("projectVcsWorkspace"),
        "projectVcsRepoSlug": request.get("projectVcsRepoSlug"),
        "projectWorkspace": request.get("projectWorkspace"),
        "projectNamespace": request.get("projectNamespace"),
        "aiProvider": request.get("aiProvider"),
        "aiModel": request.get("aiModel"),
        "aiApiKey": request.get("aiApiKey"),
        "aiBaseUrl": request.get("aiBaseUrl"),
        "aiCustomParameters": request.get("aiCustomParameters") or {},
        "branch": request.get(
            "targetBranchName",
            request.get("branch"),
        ),
        "pullRequestId": request.get("pullRequestId"),
        "commitHash": request.get("commitHash"),
        "oAuthClient": request.get("oAuthClient"),
        "oAuthSecret": request.get("oAuthSecret"),
        "accessToken": request.get("accessToken"),
        "mcpServerJar": request.get("mcpServerJar"),
        "analysisType": request.get("analysisType"),
        "prTitle": request.get("prTitle"),
        "prDescription": request.get("prDescription"),
        "taskContext": request.get("taskContext"),
        "taskHistoryContext": request.get("taskHistoryContext"),
        "prAuthor": request.get("prAuthor"),
        "sourceBranchName": request.get("sourceBranchName"),
        "changedFiles": request.get("changedFiles") or [],
        "deletedFiles": request.get("deletedFiles") or [],
        "diffSnippets": request.get("diffSnippets") or [],
        "rawDiff": request.get("rawDiff"),
        "maxAllowedTokens": request.get("maxAllowedTokens"),
        "previousCodeAnalysisIssues": (
            request.get("previousCodeAnalysisIssues") or []
        ),
        "vcsProvider": request.get("vcsProvider"),
        "analysisMode": request.get("analysisMode") or "FULL",
        "deltaDiff": request.get("deltaDiff"),
        "previousCommitHash": request.get("previousCommitHash"),
        "currentCommitHash": request.get("currentCommitHash"),
        "baseCommitHash": request.get("baseCommitHash"),
        "enrichmentData": request.get("enrichmentData"),
        "projectCapabilities": request.get("projectCapabilities"),
        "promptDryRun": False,
        "promptDryRunId": request.get("promptDryRunId"),
        "useMcpTools": request.get("useMcpTools", False),
        "ragEnabled": request.get("ragEnabled", True),
        "projectRules": request.get("projectRules"),
        "reconciliationFileContents": request.get(
            "reconciliationFileContents"
        ),
    }
    return _quality_capture_redact(snapshot)


def _validate_quality_capture_artifact(
    value: Any,
    *,
    receipt: Mapping[str, Any],
    provider: str,
    requested_model: str,
    expected_response_model: str,
    expected_request: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError("quality capture artifact is not an object")
    artifact = dict(value)
    capture_digest = artifact.get("captureDigest")
    digest_payload = dict(artifact)
    digest_payload["captureDigest"] = None
    if (
        artifact.get("kind") != QUALITY_CAPTURE_ARTIFACT_KIND
        or artifact.get("status") != "completed"
        or not isinstance(capture_digest, str)
        or SHA256_HEX.fullmatch(capture_digest) is None
        or capture_digest != sha256_json(digest_payload)
    ):
        raise RuntimeError(
            "quality capture artifact kind, status, or capture digest is invalid"
        )
    captured_request = artifact.get("request")
    captured_request_digest = artifact.get("requestDigest")
    if (
        not isinstance(captured_request, Mapping)
        or not isinstance(captured_request_digest, str)
        or SHA256_HEX.fullmatch(captured_request_digest) is None
        or captured_request_digest != sha256_json(captured_request)
    ):
        raise RuntimeError(
            "quality capture artifact request digest is invalid"
        )
    normalized_expected_request = _expected_quality_capture_request(
        expected_request
    )
    if dict(captured_request) != normalized_expected_request:
        raise RuntimeError(
            "quality capture artifact request snapshot does not match the "
            "exact normalized benchmark request"
        )
    reconstructed = _reconstruct_quality_capture_receipt(
        artifact,
        artifact_container_path=str(receipt.get("artifactContainerPath") or ""),
    )
    if reconstructed != dict(receipt):
        raise RuntimeError(
            "quality capture artifact does not reconstruct the exact receipt"
        )
    _validate_quality_capture_receipt(
        reconstructed,
        provider=provider,
        requested_model=requested_model,
        expected_response_model=expected_response_model,
    )
    return artifact


def _artifact_path(output_dir: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("analysis attempt artifact path is missing")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("analysis attempt artifact path escapes the run directory")
    resolved_root = output_dir.resolve()
    resolved = (output_dir / candidate).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError("analysis attempt artifact path escapes the run directory")
    return resolved


def _write_new_json(path: Path, value: Any) -> None:
    if path.exists():
        raise ValueError(f"analysis attempt artifact already exists: {path}")
    write_json(path, value)


def _read_attempt_artifact(
    output_dir: Path,
    relative: Any,
    *,
    expected_digest: Any = None,
) -> tuple[dict[str, Any], str]:
    path = _artifact_path(output_dir, relative)
    value = read_json(path)
    if not isinstance(value, Mapping):
        raise ValueError(f"analysis attempt artifact is not an object: {path}")
    result = dict(value)
    digest = sha256_json(result)
    if expected_digest is not None and digest != expected_digest:
        raise ValueError(f"analysis attempt artifact digest drift: {path}")
    return result, digest


def _docker_copy_container_file(
    config: Mapping[str, Any],
    *,
    source: PurePosixPath,
    configured_root: PurePosixPath,
    destination: Path,
) -> None:
    container = str(config.get("analysis_container") or "")
    if not container:
        raise ValueError("analysis.analysis_container is required")
    inspection_script = (
        "import json, os, sys; "
        "root=os.path.realpath(sys.argv[1]); "
        "source=os.path.realpath(sys.argv[2]); "
        "valid=(os.path.isdir(root) and os.path.isfile(source) and "
        "not os.path.islink(sys.argv[2])); "
        "print(json.dumps({'root': root, 'source': source, 'valid': valid})); "
        "raise SystemExit(0 if valid else 3)"
    )
    inspected = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "python",
            "-c",
            inspection_script,
            configured_root.as_posix(),
            source.as_posix(),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        errors="replace",
        check=False,
    )
    if inspected.returncode != 0:
        detail = inspected.stderr.strip()[:2000]
        raise RuntimeError(
            "quality capture container artifact is unavailable or unsafe"
            + (f": {detail}" if detail else "")
        )
    try:
        identity = json.loads(inspected.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "quality capture container path inspection returned invalid JSON"
        ) from exc
    canonical_root = PurePosixPath(str(identity.get("root") or ""))
    canonical_source = PurePosixPath(str(identity.get("source") or ""))
    if (
        identity.get("valid") is not True
        or not canonical_root.is_absolute()
        or not canonical_source.is_absolute()
        or canonical_source.parent != canonical_root
        or canonical_source.name != source.name
    ):
        raise RuntimeError(
            "quality capture container artifact resolved outside its "
            "configured directory"
        )
    copied = subprocess.run(
        [
            "docker",
            "cp",
            f"{container}:{canonical_source.as_posix()}",
            str(destination),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        errors="replace",
        check=False,
    )
    if copied.returncode != 0:
        detail = copied.stderr.strip()[:2000]
        raise RuntimeError(
            "cannot copy quality capture artifact from the configured "
            f"analysis container{f': {detail}' if detail else ''}"
        )


def _archive_quality_capture_artifact(
    *,
    config: Mapping[str, Any],
    receipt: Mapping[str, Any],
    output_dir: Path,
    attempt_id: str,
    pull_request_id: int,
    secret_values: set[str],
    provider: str,
    requested_model: str,
    expected_response_model: str,
    expected_request: Mapping[str, Any],
) -> dict[str, Any]:
    source = _quality_capture_container_source(
        config,
        receipt,
        pull_request_id=pull_request_id,
    )
    configured_root = PurePosixPath(
        str(config["quality_capture_container_dir"])
    )
    relative = _quality_capture_artifact_name(attempt_id)
    destination = _artifact_path(output_dir, relative)
    if destination.exists():
        raise ValueError(
            f"quality capture attempt artifact already exists: {destination}"
        )
    temporary = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.tmp"
    )
    temporary.parent.mkdir(parents=True, exist_ok=True)
    try:
        _docker_copy_container_file(
            config,
            source=source,
            configured_root=configured_root,
            destination=temporary,
        )
        mode = temporary.lstat().st_mode if temporary.exists() else 0
        if (
            not temporary.exists()
            or temporary.is_symlink()
            or not stat.S_ISREG(mode)
        ):
            raise RuntimeError(
                "copied quality capture artifact is not a regular file"
            )
        captured = read_json(temporary)
        require_no_secret_values(
            captured,
            secret_values,
            context="analysis model-call quality capture",
        )
        artifact = _validate_quality_capture_artifact(
            captured,
            receipt=receipt,
            provider=provider,
            requested_model=requested_model,
            expected_response_model=expected_response_model,
            expected_request=expected_request,
        )
        artifact_digest = sha256_json(artifact)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        if temporary.is_dir() and not temporary.is_symlink():
            temporary.rmdir()
        else:
            temporary.unlink(missing_ok=True)
    return {
        "receipt": dict(receipt),
        "artifact": relative,
        "artifactDigest": artifact_digest,
    }


def _validate_bound_model_call_evidence(
    value: Any,
    *,
    output_dir: Path,
    config: Mapping[str, Any],
    pull_request_id: int | None = None,
    expected_request: Mapping[str, Any],
    require_present: bool | None = None,
) -> dict[str, Any] | None:
    required = (
        bool(config.get("require_model_call_evidence", False))
        if require_present is None
        else require_present
    )
    if value is None:
        if required:
            raise ValueError("required analysis model-call evidence is missing")
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "receipt",
        "artifact",
        "artifactDigest",
    }:
        raise ValueError("analysis model-call evidence binding is malformed")
    evidence = dict(value)
    receipt = _validate_quality_capture_receipt(
        evidence.get("receipt"),
        provider=str(config.get("provider") or ""),
        requested_model=str(config.get("model") or ""),
        expected_response_model=str(
            config.get("expected_response_model") or config.get("model") or ""
        ),
    )
    _quality_capture_container_source(
        config,
        receipt,
        pull_request_id=pull_request_id,
    )
    artifact, artifact_digest = _read_attempt_artifact(
        output_dir,
        evidence.get("artifact"),
        expected_digest=evidence.get("artifactDigest"),
    )
    _validate_quality_capture_artifact(
        artifact,
        receipt=receipt,
        provider=str(config.get("provider") or ""),
        requested_model=str(config.get("model") or ""),
        expected_response_model=str(
            config.get("expected_response_model") or config.get("model") or ""
        ),
        expected_request=expected_request,
    )
    if artifact_digest != evidence.get("artifactDigest"):
        raise ValueError("analysis model-call evidence artifact digest drift")
    return evidence


def _attempt_stopping_reason(
    *, status: str, attempt_number: int, max_case_attempts: int
) -> tuple[str, bool]:
    if status == "completed":
        return "case_completed", False
    if attempt_number >= max_case_attempts:
        return "attempt_limit_reached", False
    return "explicit_resume_required", True


def _validate_attempt_artifact_identity(
    value: Mapping[str, Any],
    attempt: Mapping[str, Any],
    *,
    kind: str,
) -> None:
    fields_by_kind = {
        ATTEMPT_START_KIND: {
            "kind",
            "attemptId",
            "caseId",
            "attemptNumber",
            "jobId",
            "startedAt",
            "maxAttempts",
            "requestDigest",
            "requestControlDigest",
            "redactedRequest",
        },
        ATTEMPT_RESULT_KIND: {
            "kind",
            "attemptId",
            "caseId",
            "attemptNumber",
            "jobId",
            "status",
            "startedAt",
            "completedAt",
            "durationSeconds",
            "requestDigest",
            "requestControlDigest",
            "redactedRequest",
            "events",
            "response",
            "productFinalization",
            "modelCallEvidence",
            "caseOutcome",
            "error",
            "terminationReason",
            "stoppingReason",
            "retryEligible",
        },
    }
    expected_fields = fields_by_kind.get(kind)
    if expected_fields is None or set(value) != expected_fields:
        raise ValueError(
            f"analysis attempt artifact fields are invalid for {kind}"
        )
    expected = {
        "kind": kind,
        "attemptId": attempt.get("attemptId"),
        "caseId": attempt.get("caseId"),
        "attemptNumber": attempt.get("attemptNumber"),
        "jobId": attempt.get("jobId"),
    }
    if any(value.get(field) != expected_value for field, expected_value in expected.items()):
        raise ValueError(
            f"analysis attempt artifact identity drift for "
            f"{attempt.get('attemptId')}"
        )


def _validate_attempt_ledger(
    manifest: Mapping[str, Any],
    *,
    output_dir: Path,
    selected_ids: list[str],
    max_case_attempts: int,
    allow_running: bool,
) -> dict[str, list[dict[str, Any]]]:
    if manifest.get("attemptPolicy") != _attempt_policy(max_case_attempts):
        raise ValueError("existing run attempt policy cannot be resumed")
    ledger = manifest.get("attemptLedger")
    if not isinstance(ledger, list):
        raise ValueError("existing run attempt ledger is missing")
    analysis_config = manifest.get("analysisConfig")
    if not isinstance(analysis_config, Mapping):
        raise ValueError("existing run analysis configuration is missing")
    replay_lock = read_json(
        _artifact_path(output_dir, manifest.get("replayLockArtifact"))
    )
    replay_values = (
        replay_lock.get("cases") if isinstance(replay_lock, Mapping) else None
    )
    replay_pull_requests = {
        str(item.get("caseId")): item.get("forkPrNumber")
        for item in (replay_values or [])
        if isinstance(item, Mapping)
    }
    selected = set(selected_ids)
    by_case: dict[str, list[dict[str, Any]]] = {}
    attempt_ids: set[str] = set()
    artifact_names: set[str] = set()
    for item in ledger:
        if not isinstance(item, Mapping):
            raise ValueError("analysis attempt ledger entry is not an object")
        attempt = dict(item)
        attempt_id = attempt.get("attemptId")
        case_id = attempt.get("caseId")
        attempt_number = attempt.get("attemptNumber")
        status = attempt.get("status")
        if (
            not isinstance(attempt_id, str)
            or not attempt_id.startswith("attempt-")
            or attempt_id in attempt_ids
            or case_id not in selected
            or isinstance(attempt_number, bool)
            or not isinstance(attempt_number, int)
            or attempt_number < 1
            or attempt_number > max_case_attempts
            or status not in {"running", "completed", "failed"}
            or (status == "running" and not allow_running)
        ):
            raise ValueError("analysis attempt ledger entry is invalid")
        attempt_ids.add(attempt_id)
        case_attempts = by_case.setdefault(str(case_id), [])
        if attempt_number != len(case_attempts) + 1:
            raise ValueError(
                f"analysis attempts for {case_id} are not contiguous"
            )
        if case_attempts and case_attempts[-1].get("status") == "completed":
            raise ValueError(
                f"analysis attempt follows completed case {case_id}"
            )
        if status == "running" and any(
            other.get("status") == "running" for other in case_attempts
        ):
            raise ValueError(f"duplicate running attempt for {case_id}")
        for field in ("startArtifact", "resultArtifact"):
            relative = attempt.get(field)
            if not isinstance(relative, str) or relative in artifact_names:
                raise ValueError("analysis attempt artifact path is invalid")
            _artifact_path(output_dir, relative)
            artifact_names.add(relative)

        start_value: dict[str, Any] | None = None
        start_digest = attempt.get("startArtifactDigest")
        start_path = _artifact_path(output_dir, attempt["startArtifact"])
        if start_digest is not None:
            if not isinstance(start_digest, str) or SHA256_HEX.fullmatch(
                start_digest
            ) is None:
                raise ValueError("analysis attempt start digest is invalid")
            start_value, _ = _read_attempt_artifact(
                output_dir,
                attempt["startArtifact"],
                expected_digest=start_digest,
            )
            _validate_attempt_artifact_identity(
                start_value,
                attempt,
                kind=ATTEMPT_START_KIND,
            )
            if (
                start_value.get("requestDigest") != attempt.get("requestDigest")
                or start_value.get("requestControlDigest")
                != attempt.get("requestControlDigest")
                or start_value.get("startedAt") != attempt.get("startedAt")
                or start_value.get("maxAttempts") != max_case_attempts
                or (
                    start_value.get("redactedRequest") is not None
                    and not isinstance(
                        start_value.get("redactedRequest"),
                        Mapping,
                    )
                )
            ):
                raise ValueError("analysis attempt start request binding drift")
        elif status != "running":
            raise ValueError("analysis attempt start artifact is missing")
        elif start_path.exists():
            raise ValueError("analysis attempt start digest is missing")

        result_digest = attempt.get("resultArtifactDigest")
        result_path = _artifact_path(output_dir, attempt["resultArtifact"])
        if result_digest is not None:
            if not isinstance(result_digest, str) or SHA256_HEX.fullmatch(
                result_digest
            ) is None:
                raise ValueError("analysis attempt result digest is invalid")
            result_value, _ = _read_attempt_artifact(
                output_dir,
                attempt["resultArtifact"],
                expected_digest=result_digest,
            )
            _validate_attempt_artifact_identity(
                result_value,
                attempt,
                kind=ATTEMPT_RESULT_KIND,
            )
            declared_attempt = dict(attempt)
            normalized_attempt = dict(attempt)
            _apply_attempt_result(
                attempt=normalized_attempt,
                record={},
                result=result_value,
                result_digest=result_digest,
                max_case_attempts=max_case_attempts,
            )
            if (
                result_value.get("status") != status
                or result_value.get("stoppingReason")
                != attempt.get("stoppingReason")
                or result_value.get("error") != attempt.get("error")
                or result_value.get("startedAt")
                != attempt.get("startedAt")
                or result_value.get("redactedRequest")
                != (
                    start_value.get("redactedRequest")
                    if start_value is not None
                    else None
                )
                or any(
                    normalized_attempt.get(field)
                    != declared_attempt.get(field)
                    for field in (
                        "status",
                        "completedAt",
                        "durationSeconds",
                        "resultArtifactDigest",
                        "error",
                        "stoppingReason",
                        "retryEligible",
                        "modelCallEvidence",
                    )
                )
            ):
                raise ValueError("analysis attempt result binding drift")
            termination_reason = result_value.get("terminationReason")
            if (
                status == "completed"
                and termination_reason != "case_completed"
            ) or (
                status == "failed"
                and termination_reason
                not in {"case_exception", "runner_interrupted"}
            ):
                raise ValueError(
                    "analysis attempt termination reason is invalid"
                )
            if (
                result_value.get("modelCallEvidence")
                != attempt.get("modelCallEvidence")
            ):
                raise ValueError(
                    "analysis attempt model-call evidence binding drift"
                )
            model_evidence = attempt.get("modelCallEvidence")
            if model_evidence is not None:
                capture_relative = (
                    model_evidence.get("artifact")
                    if isinstance(model_evidence, Mapping)
                    else None
                )
                if (
                    not isinstance(capture_relative, str)
                    or capture_relative in artifact_names
                ):
                    raise ValueError(
                        "analysis model-call evidence artifact path is invalid"
                    )
                artifact_names.add(capture_relative)
            expected_request = (
                start_value.get("redactedRequest")
                if isinstance(start_value, Mapping)
                and isinstance(start_value.get("redactedRequest"), Mapping)
                else {}
            )
            _validate_bound_model_call_evidence(
                model_evidence,
                output_dir=output_dir,
                config=analysis_config,
                pull_request_id=replay_pull_requests.get(str(case_id)),
                expected_request=expected_request,
                require_present=(
                    status == "completed"
                    and bool(
                        analysis_config.get(
                            "require_model_call_evidence",
                            False,
                        )
                    )
                ),
            )
        elif status != "running" or (result_path.exists() and not allow_running):
            raise ValueError("analysis attempt result digest is missing")

        if status != "running":
            expected_reason, expected_retry = _attempt_stopping_reason(
                status=status,
                attempt_number=attempt_number,
                max_case_attempts=max_case_attempts,
            )
            if (
                attempt.get("stoppingReason") != expected_reason
                or attempt.get("retryEligible") is not expected_retry
                or not isinstance(attempt.get("completedAt"), str)
                or not isinstance(attempt.get("durationSeconds"), (int, float))
            ):
                raise ValueError("analysis attempt stopping evidence is invalid")
        case_attempts.append(attempt)

    cases = manifest.get("cases")
    if not isinstance(cases, list) or any(
        not isinstance(item, Mapping) for item in cases
    ):
        raise ValueError("existing run case summaries are invalid")
    case_by_id = {
        str(item.get("caseId")): item
        for item in cases
        if isinstance(item.get("caseId"), str)
    }
    if len(case_by_id) != len(cases) or set(case_by_id) != set(by_case):
        raise ValueError("analysis attempt ledger and case summaries disagree")
    for case_id, attempts in by_case.items():
        latest = attempts[-1]
        case = case_by_id[case_id]
        if (
            case.get("attemptId") != latest.get("attemptId")
            or case.get("attemptNumber") != latest.get("attemptNumber")
            or case.get("attemptCount") != len(attempts)
            or case.get("maxAttempts") != max_case_attempts
            or case.get("status") != latest.get("status")
            or case.get("jobId") != latest.get("jobId")
        ):
            raise ValueError(
                f"analysis attempt ledger and {case_id} summary disagree"
            )
        if latest.get("status") != "running" and (
            case.get("rawResponse") != latest.get("resultArtifact")
            or case.get("responseDigest")
            != latest.get("resultArtifactDigest")
            or case.get("stoppingReason") != latest.get("stoppingReason")
            or case.get("retryEligible") is not latest.get("retryEligible")
            or case.get("modelCallEvidence")
            != latest.get("modelCallEvidence")
        ):
            raise ValueError(
                f"analysis attempt result and {case_id} summary disagree"
            )
    return by_case


def _case_outcome(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "analysisState": record.get("analysisState"),
        "retrievalEvidence": record.get("retrievalEvidence"),
        "modelCallEvidence": record.get("modelCallEvidence"),
        "productFinalization": record.get("productFinalization"),
        "findings": record.get("findings"),
    }


def _elapsed_wall_seconds(started_at: Any, completed_at: str) -> float:
    if not isinstance(started_at, str):
        return 0.0
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return round(max(0.0, (end - start).total_seconds()), 3)


def _apply_attempt_result(
    *,
    attempt: dict[str, Any],
    record: dict[str, Any],
    result: Mapping[str, Any],
    result_digest: str,
    max_case_attempts: int,
) -> None:
    _validate_attempt_artifact_identity(
        result,
        attempt,
        kind=ATTEMPT_RESULT_KIND,
    )
    status = result.get("status")
    if status not in {"completed", "failed"}:
        raise ValueError("analysis attempt result status is invalid")
    stopping_reason, retry_eligible = _attempt_stopping_reason(
        status=str(status),
        attempt_number=int(attempt["attemptNumber"]),
        max_case_attempts=max_case_attempts,
    )
    if (
        result.get("stoppingReason") != stopping_reason
        or result.get("retryEligible") is not retry_eligible
        or result.get("requestDigest") != attempt.get("requestDigest")
        or result.get("requestControlDigest")
        != attempt.get("requestControlDigest")
        or not isinstance(result.get("completedAt"), str)
        or isinstance(result.get("durationSeconds"), bool)
        or not isinstance(result.get("durationSeconds"), (int, float))
        or float(result["durationSeconds"]) < 0
    ):
        raise ValueError("analysis attempt result stopping evidence is invalid")
    outcome = result.get("caseOutcome")
    if (
        not isinstance(outcome, Mapping)
        or not isinstance(outcome.get("findings"), list)
        or outcome.get("modelCallEvidence")
        != result.get("modelCallEvidence")
    ):
        raise ValueError("analysis attempt result case outcome is invalid")
    error = result.get("error")
    if status == "completed" and error is not None:
        raise ValueError("completed analysis attempt contains an error")
    if status == "failed" and (
        not isinstance(error, str) or not error.strip()
    ):
        raise ValueError("failed analysis attempt has no error")
    attempt.update(
        {
            "status": status,
            "completedAt": result["completedAt"],
            "durationSeconds": result["durationSeconds"],
            "resultArtifactDigest": result_digest,
            "error": error,
            "stoppingReason": stopping_reason,
            "retryEligible": retry_eligible,
            "modelCallEvidence": result.get("modelCallEvidence"),
        }
    )
    record.update(
        {
            "status": status,
            "completedAt": result["completedAt"],
            "durationSeconds": result["durationSeconds"],
            "requestDigest": attempt.get("requestDigest"),
            "requestControlDigest": attempt.get("requestControlDigest"),
            "responseDigest": result_digest,
            "rawResponse": attempt["resultArtifact"],
            "analysisState": outcome.get("analysisState"),
            "retrievalEvidence": outcome.get("retrievalEvidence"),
            "modelCallEvidence": outcome.get("modelCallEvidence"),
            "productFinalization": outcome.get("productFinalization"),
            "findings": list(outcome["findings"]),
            "error": error,
            "retryEligible": retry_eligible,
            "stoppingReason": stopping_reason,
        }
    )


def _recover_running_attempts(
    manifest: dict[str, Any],
    *,
    output_dir: Path,
    max_case_attempts: int,
) -> bool:
    cases = {
        str(item["caseId"]): item
        for item in manifest["cases"]
        if isinstance(item, dict)
    }
    changed = False
    for attempt in manifest["attemptLedger"]:
        if attempt.get("status") != "running":
            continue
        changed = True
        record = cases[str(attempt["caseId"])]
        start_path = _artifact_path(output_dir, attempt["startArtifact"])
        start_value: dict[str, Any] | None = None
        if start_path.exists():
            start_value, start_digest = _read_attempt_artifact(
                output_dir,
                attempt["startArtifact"],
                expected_digest=attempt.get("startArtifactDigest"),
            )
            _validate_attempt_artifact_identity(
                start_value,
                attempt,
                kind=ATTEMPT_START_KIND,
            )
            if attempt.get("startArtifactDigest") is None:
                attempt["startArtifactDigest"] = start_digest
                attempt["requestDigest"] = start_value.get("requestDigest")
                attempt["requestControlDigest"] = start_value.get(
                    "requestControlDigest"
                )
        elif attempt.get("startArtifactDigest") is not None:
            raise ValueError(
                "interrupted analysis attempt start artifact is missing"
            )
        else:
            start_value = {
                "kind": ATTEMPT_START_KIND,
                "attemptId": attempt["attemptId"],
                "caseId": attempt["caseId"],
                "attemptNumber": attempt["attemptNumber"],
                "jobId": attempt["jobId"],
                "startedAt": attempt["startedAt"],
                "maxAttempts": max_case_attempts,
                "requestDigest": attempt.get("requestDigest"),
                "requestControlDigest": attempt.get(
                    "requestControlDigest"
                ),
                "redactedRequest": None,
            }
            _write_new_json(start_path, start_value)
            attempt["startArtifactDigest"] = sha256_json(start_value)

        result_path = _artifact_path(output_dir, attempt["resultArtifact"])
        if result_path.exists():
            result, result_digest = _read_attempt_artifact(
                output_dir,
                attempt["resultArtifact"],
                expected_digest=attempt.get("resultArtifactDigest"),
            )
        else:
            completed_at = _now()
            duration = _elapsed_wall_seconds(
                attempt.get("startedAt"),
                completed_at,
            )
            stopping_reason, retry_eligible = _attempt_stopping_reason(
                status="failed",
                attempt_number=int(attempt["attemptNumber"]),
                max_case_attempts=max_case_attempts,
            )
            error = (
                "InterruptedError: prior runner invocation ended before "
                "attempt finalization"
            )
            result = {
                "kind": ATTEMPT_RESULT_KIND,
                "attemptId": attempt["attemptId"],
                "caseId": attempt["caseId"],
                "attemptNumber": attempt["attemptNumber"],
                "jobId": attempt["jobId"],
                "status": "failed",
                "startedAt": attempt["startedAt"],
                "completedAt": completed_at,
                "durationSeconds": duration,
                "requestDigest": attempt.get("requestDigest"),
                "requestControlDigest": attempt.get(
                    "requestControlDigest"
                ),
                "redactedRequest": (
                    start_value.get("redactedRequest")
                    if start_value is not None
                    else None
                ),
                "events": [],
                "response": None,
                "productFinalization": None,
                "modelCallEvidence": None,
                "caseOutcome": {
                    "analysisState": None,
                    "retrievalEvidence": None,
                    "modelCallEvidence": None,
                    "productFinalization": None,
                    "findings": [],
                },
                "error": error,
                "terminationReason": "runner_interrupted",
                "stoppingReason": stopping_reason,
                "retryEligible": retry_eligible,
            }
            _write_new_json(result_path, result)
            result_digest = sha256_json(result)
        _apply_attempt_result(
            attempt=attempt,
            record=record,
            result=result,
            result_digest=result_digest,
            max_case_attempts=max_case_attempts,
        )
    return changed


def _container_identity(name: str) -> dict[str, Any] | None:
    try:
        completed = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .}}",
                name,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, Mapping):
        return None
    config = value.get("Config") if isinstance(value.get("Config"), Mapping) else {}
    return {
        "containerId": value.get("Id"),
        "imageId": value.get("Image"),
        "imageReference": config.get("Image"),
    }


def _runtime_provenance(config: Mapping[str, Any]) -> dict[str, Any]:
    analysis = _container_identity(str(config["analysis_container"]))
    rag = _container_identity(str(config["rag_container"]))
    finalizer = _container_identity(str(config["finalizer_container"]))
    if config.get("require_runtime_provenance") and (
        not analysis
        or not analysis.get("containerId")
        or not analysis.get("imageId")
        or not rag
        or not rag.get("containerId")
        or not rag.get("imageId")
        or not finalizer
        or not finalizer.get("containerId")
        or not finalizer.get("imageId")
    ):
        raise RuntimeError(
            "immutable analysis/RAG/product-finalizer container provenance is "
            "unavailable; paper runs require all configured containers to be "
            "inspectable"
        )
    return {
        "analysis": analysis,
        "rag": rag,
        "finalizer": finalizer,
        "required": bool(config.get("require_runtime_provenance")),
    }


def _analysis_result(value: Any) -> Mapping[str, Any]:
    current = value
    for _ in range(4):
        if not isinstance(current, Mapping):
            raise RuntimeError("CodeCrow final result is not an object")
        if (
            current.get("error") not in (None, False, "")
            or str(current.get("status") or "").casefold() in {"error", "failed"}
        ):
            detail = (
                current.get("error_message")
                or current.get("message")
                or current.get("comment")
                or current.get("error")
            )
            raise RuntimeError(f"CodeCrow returned an error result: {detail}")
        if isinstance(current.get("issues"), list):
            return current
        if "result" not in current:
            break
        next_value = current.get("result")
        if next_value is current:
            break
        current = next_value
    raise RuntimeError("CodeCrow final result has no explicit issues array")


def _findings(response: Any) -> list[dict[str, Any]]:
    result = _analysis_result(response)
    findings = []
    for index, issue in enumerate(result["issues"], start=1):
        if not isinstance(issue, Mapping):
            raise RuntimeError(f"CodeCrow issue {index} is not an object")
        title = str(issue.get("title") or "").strip()
        description = str(
            issue.get("reason") or issue.get("description") or ""
        ).strip()
        if not title and not description:
            raise RuntimeError(
                f"CodeCrow issue {index} has neither title nor description"
            )
        path = issue.get("file")
        if path is not None and (not isinstance(path, str) or not path.strip()):
            raise RuntimeError(f"CodeCrow issue {index} has an invalid file")
        line = issue.get("line")
        if line is not None and (
            isinstance(line, bool) or not isinstance(line, int) or line < 1
        ):
            raise RuntimeError(f"CodeCrow issue {index} has an invalid line")
        findings.append(
            {
                "findingId": f"finding-{index:04d}",
                "path": path,
                "line": line,
                "title": title,
                "description": description,
                "category": issue.get("category") or issue.get("type"),
                "severity": issue.get("severity"),
                "suggestedFix": issue.get("suggestedFixDescription"),
                "confidence": issue.get("confidence"),
                "raw": dict(issue),
            }
        )
    return findings


def _retrieval_evidence(
    events: list[Mapping[str, Any]],
    *,
    required: bool,
    expected_revision_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    matches = [
        event
        for event in events
        if event.get("type") == "status"
        and event.get("state") == "review_evidence_completed"
    ]
    if not matches:
        if required:
            raise RuntimeError(
                "analysis emitted no terminal retrieval evidence; HTTP "
                "transport cannot satisfy the paper-run retrieval gate"
            )
        return None
    if len(matches) != 1:
        raise RuntimeError("analysis emitted duplicate terminal retrieval evidence")
    event = matches[0]
    review_units = event.get("reviewUnits")
    retrieval = event.get("retrieval")
    if not isinstance(review_units, Mapping) or not isinstance(
        retrieval, Mapping
    ):
        raise RuntimeError("terminal retrieval evidence is malformed")
    registered = review_units.get("registered")
    completed = review_units.get("completed")
    if (
        isinstance(registered, bool)
        or not isinstance(registered, int)
        or registered < 1
        or completed != registered
    ):
        raise RuntimeError("analysis did not complete every registered review unit")
    states = retrieval.get("deterministicStates")
    if (
        not isinstance(states, list)
        or any(not isinstance(state, str) or not state for state in states)
        or (registered > 0 and not states)
        or any(state != "complete" for state in states)
    ):
        raise RuntimeError(
            "analysis has missing or incomplete deterministic RAG retrieval"
        )
    semantic_failures = retrieval.get("semanticFailures")
    if (
        isinstance(semantic_failures, bool)
        or not isinstance(semantic_failures, int)
        or semantic_failures != 0
    ):
        raise RuntimeError("analysis has semantic RAG retrieval failures")
    if retrieval.get("semanticDisabled") is not False:
        raise RuntimeError("analysis has semantic RAG retrieval disabled")
    exact_evidence_ids = retrieval.get("exactEvidenceIds")
    if (
        isinstance(exact_evidence_ids, bool)
        or not isinstance(exact_evidence_ids, int)
        or exact_evidence_ids < 0
    ):
        raise RuntimeError("analysis retrieval evidence count is invalid")
    revision_binding = event.get("revisionBinding")
    if not isinstance(revision_binding, Mapping):
        raise RuntimeError("terminal retrieval evidence has no revision binding")
    if revision_binding.get("prIndexed") is not True:
        raise RuntimeError(
            "analysis did not index and lease the exact PR overlay"
        )
    if expected_revision_binding is None:
        raise RuntimeError(
            "runner has no expected revision binding for terminal evidence"
        )
    for field in (
        "pullRequestId",
        "targetBranch",
        "sourceRevision",
        "baseRevision",
        "baseGenerationManifestSha256",
        "basePluginFingerprint",
        "basePluginDescriptorFingerprint",
        "basePluginImplementationFingerprint",
        "baseIndexRepresentationFingerprint",
    ):
        if revision_binding.get(field) != expected_revision_binding.get(field):
            raise RuntimeError(
                f"analysis terminal revision binding mismatch for {field}"
            )
    pr_generation_fingerprint = revision_binding.get(
        "prGenerationFingerprint"
    )
    if (
        not isinstance(pr_generation_fingerprint, str)
        or SHA256_FINGERPRINT.fullmatch(pr_generation_fingerprint) is None
    ):
        raise RuntimeError(
            "analysis terminal PR generation fingerprint is invalid"
        )
    overlay_manifest = revision_binding.get(
        "prOverlayGenerationManifestSha256"
    )
    if (
        not isinstance(overlay_manifest, str)
        or SHA256_HEX.fullmatch(overlay_manifest) is None
    ):
        raise RuntimeError(
            "analysis terminal PR overlay generation manifest is invalid"
        )
    normalized_revision_binding = {
        "prIndexed": True,
        "pullRequestId": revision_binding["pullRequestId"],
        "targetBranch": revision_binding["targetBranch"],
        "sourceRevision": revision_binding["sourceRevision"],
        "baseRevision": revision_binding["baseRevision"],
        "baseGenerationManifestSha256": revision_binding[
            "baseGenerationManifestSha256"
        ],
        "prGenerationFingerprint": pr_generation_fingerprint,
        "prOverlayGenerationManifestSha256": overlay_manifest,
        "basePluginFingerprint": revision_binding[
            "basePluginFingerprint"
        ],
        "basePluginDescriptorFingerprint": revision_binding[
            "basePluginDescriptorFingerprint"
        ],
        "basePluginImplementationFingerprint": revision_binding[
            "basePluginImplementationFingerprint"
        ],
        "baseIndexRepresentationFingerprint": revision_binding[
            "baseIndexRepresentationFingerprint"
        ],
    }
    return {
        "state": "review_evidence_completed",
        "reviewUnits": {
            "registered": registered,
            "completed": completed,
        },
        "retrieval": {
            "deterministicStates": list(states),
            "semanticFailures": semantic_failures,
            "semanticDisabled": False,
            "exactEvidenceIds": exact_evidence_ids,
        },
        "revisionBinding": normalized_revision_binding,
        "evidenceSha256": sha256_json(event),
    }


def _redis_command(
    config: Mapping[str, Any],
    *arguments: str,
    input_text: str | None = None,
) -> str:
    return run(
        [
            "docker",
            "exec",
            "-i",
            str(config["redis_container"]),
            "redis-cli",
            "--raw",
            "-n",
            str(config["redis_db"]),
            *arguments,
        ],
        input_text=input_text,
    ).strip()


def _queue_review(
    config: Mapping[str, Any],
    *,
    job_id: str,
    payload: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], Any]:
    event_key = f"codecrow:analysis:events:{job_id}"
    _redis_command(config, "DEL", event_key)
    envelope = canonical_json({"job_id": job_id, "request": payload})
    _redis_command(
        config,
        "-x",
        "LPUSH",
        str(config["redis_queue"]),
        input_text=envelope,
    )
    deadline = time.monotonic() + int(config["timeout_seconds"])
    events: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        remaining = max(1, min(5, int(deadline - time.monotonic())))
        raw = _redis_command(config, "BRPOP", event_key, str(remaining))
        if not raw:
            continue
        lines = raw.splitlines()
        if len(lines) < 2:
            raise RuntimeError(f"malformed Redis event for job {job_id}")
        event = json.loads(lines[-1])
        if not isinstance(event, dict):
            raise RuntimeError(f"non-object Redis event for job {job_id}")
        events.append(event)
        if event.get("type") == "error":
            raise RuntimeError(str(event.get("message") or "analysis job failed"))
        if event.get("type") == "final":
            return events, event.get("result")
    raise TimeoutError(f"CodeCrow job {job_id} did not finish before timeout")


def _http_review(
    config: Mapping[str, Any],
    *,
    payload: Mapping[str, Any],
    service_secret: str,
) -> tuple[list[dict[str, Any]], Any]:
    value = _json_request(
        str(config["endpoint"]),
        method="POST",
        payload=payload,
        secret=service_secret,
        timeout=int(config["timeout_seconds"]),
    )
    return [], value


def _finalizer_file_contents(payload: Mapping[str, Any]) -> dict[str, str]:
    enrichment = payload.get("enrichmentData")
    if not isinstance(enrichment, Mapping):
        raise RuntimeError("analysis request has no enrichment data")
    entries = enrichment.get("fileContents")
    if not isinstance(entries, list):
        raise RuntimeError("analysis request has no enrichment file contents")
    result: dict[str, str] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise RuntimeError(
                f"analysis enrichment entry {index} is not an object"
            )
        path = entry.get("path")
        content = entry.get("content")
        if content is None:
            continue
        if not isinstance(path, str) or not path or not isinstance(content, str):
            raise RuntimeError(
                f"analysis enrichment entry {index} is malformed"
            )
        if path in result:
            raise RuntimeError(f"duplicate analysis enrichment path: {path}")
        result[path] = content
    return result


def _product_finalize(
    config: Mapping[str, Any],
    *,
    response: Any,
    request_payload: Mapping[str, Any],
    service_secret: str,
) -> dict[str, Any]:
    if not service_secret:
        raise RuntimeError(
            "the CodeCrow internal service secret is required for product "
            "finalization"
        )
    analysis_data = dict(_analysis_result(response))
    value = _json_request(
        str(config["finalizer_endpoint"]),
        method="POST",
        payload={
            "analysisData": analysis_data,
            "fileContents": _finalizer_file_contents(request_payload),
        },
        secret=service_secret,
        secret_header="X-Internal-Secret",
        timeout=int(config["timeout_seconds"]),
    )
    if not isinstance(value, Mapping):
        raise RuntimeError("CodeCrow product finalizer returned a non-object")
    if value.get("kind") != "codecrow-isolated-analysis-finalization":
        raise RuntimeError("CodeCrow product finalizer returned an invalid kind")
    for field, expected in (
        ("analysisDataValidated", True),
        ("persisted", False),
        ("published", False),
        ("previousIssueStateUsed", False),
    ):
        if value.get(field) is not expected:
            raise RuntimeError(
                f"CodeCrow product finalizer returned invalid {field}"
            )
    issues = value.get("issues")
    if not isinstance(issues, list) or any(
        not isinstance(issue, Mapping) for issue in issues
    ):
        raise RuntimeError(
            "CodeCrow product finalizer returned an invalid issues array"
        )
    raw_count = value.get("rawIssueCount")
    final_count = value.get("finalIssueCount")
    if (
        isinstance(raw_count, bool)
        or not isinstance(raw_count, int)
        or raw_count != len(analysis_data["issues"])
    ):
        raise RuntimeError(
            "CodeCrow product finalizer returned an invalid raw issue count"
        )
    if (
        isinstance(final_count, bool)
        or not isinstance(final_count, int)
        or final_count != len(issues)
    ):
        raise RuntimeError(
            "CodeCrow product finalizer returned an invalid final issue count"
        )
    return dict(value)


def _receipt_plugins(receipt: Mapping[str, Any]) -> list[str]:
    for key in ("plugin_ids", "repository_plugins", "repositoryPlugins"):
        value = receipt.get(key)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return value
    identity = receipt.get("plugin_identity")
    if isinstance(identity, Mapping):
        return _receipt_plugins(identity)
    return []


def _validate_index_selection_policy(receipt: Mapping[str, Any]) -> None:
    include_patterns = receipt.get("index_include_patterns")
    exclude_patterns = receipt.get("index_exclude_patterns")
    policy_sha256 = receipt.get("index_selection_policy_sha256")
    for label, patterns in (
        ("include", include_patterns),
        ("exclude", exclude_patterns),
    ):
        if (
            not isinstance(patterns, list)
            or any(not isinstance(pattern, str) for pattern in patterns)
            or patterns != sorted(set(patterns))
        ):
            raise RuntimeError(
                f"RAG exact revision has invalid canonical {label} patterns"
            )
    if (
        not isinstance(policy_sha256, str)
        or SHA256_HEX.fullmatch(policy_sha256) is None
    ):
        raise RuntimeError(
            "RAG exact revision has no valid index selection policy digest"
        )
    policy = {
        "schema": INDEX_SELECTION_POLICY_SCHEMA,
        "includePatterns": include_patterns,
        "excludePatterns": exclude_patterns,
    }
    if sha256_text(canonical_json(policy)) != policy_sha256:
        raise RuntimeError(
            "RAG exact revision index selection policy digest does not match"
        )


def exact_index_receipt(
    config: Mapping[str, Any],
    *,
    branch: str,
    commit: str,
    service_secret: str,
) -> dict[str, Any]:
    if not config.get("require_exact_index", True):
        return {"mode": "rag-disabled", "branch": branch, "commit": commit}
    workspace = str(config.get("rag_workspace") or config.get("project_workspace"))
    project = str(config.get("rag_project") or config.get("project_namespace"))
    if not workspace or not project:
        raise ValueError("analysis RAG workspace/project coordinates are required")
    encoded_workspace = urllib.parse.quote(workspace, safe="")
    encoded_project = urllib.parse.quote(project, safe="")
    query = urllib.parse.urlencode({"branch": branch, "commit": commit})
    value = _json_request(
        f"{str(config['rag_endpoint']).rstrip('/')}/index/"
        f"{encoded_workspace}/{encoded_project}/revision?{query}",
        secret=service_secret,
        timeout=60,
    )
    if not isinstance(value, Mapping):
        raise RuntimeError("RAG exact-revision preflight returned a non-object")
    count = value.get("point_count")
    if not isinstance(count, int) or count <= 0:
        raise RuntimeError(
            f"no exact RAG index for {workspace}/{project} {branch}@{commit}"
        )
    generation_schema = value.get("generation_schema")
    member_count = value.get("generation_member_count")
    if generation_schema != "codecrow.repository-index-generation":
        raise RuntimeError(
            "RAG exact revision has no supported sealed generation manifest"
        )
    if (
        isinstance(member_count, bool)
        or not isinstance(member_count, int)
        or member_count <= 0
        or count != member_count + 1
    ):
        raise RuntimeError(
            "RAG exact revision generation member count is inconsistent"
        )
    for field in (
        "generation_members_sha256",
        "generation_manifest_sha256",
        "source_tree_sha256",
    ):
        digest = value.get(field)
        if not isinstance(digest, str) or not SHA256_HEX.fullmatch(digest):
            raise RuntimeError(
                f"RAG exact revision has no valid {field}"
            )
    _validate_index_selection_policy(value)
    expected_identity = {
        "workspace": workspace,
        "project": project,
        "branch": branch,
        "commit": commit,
        "repository_revision": commit,
    }
    mismatched = [
        field
        for field, expected in expected_identity.items()
        if value.get(field) != expected
    ]
    if mismatched:
        raise RuntimeError(
            "RAG preflight returned different exact revision identity: "
            + ", ".join(mismatched)
        )
    facts_digest = value.get("repository_facts_sha256")
    if not isinstance(facts_digest, str) or not SHA256_HEX.fullmatch(
        facts_digest
    ):
        raise RuntimeError(
            "RAG exact revision has no valid repository facts digest"
        )
    for field in (
        "plugin_fingerprint",
        "plugin_descriptor_fingerprint",
        "plugin_implementation_fingerprint",
        "index_representation_fingerprint",
    ):
        fingerprint = value.get(field)
        if not isinstance(fingerprint, str) or not SHA256_FINGERPRINT.fullmatch(
            fingerprint
        ):
            raise RuntimeError(
                f"RAG exact revision has no valid {field}"
            )
    required = set(config.get("required_repository_plugins") or [])
    plugin_ids = _receipt_plugins(value)
    if not plugin_ids or len(plugin_ids) != len(set(plugin_ids)):
        raise RuntimeError(
            "RAG exact revision has invalid repository plugin identities"
        )
    observed = set(plugin_ids)
    if not required.issubset(observed):
        raise RuntimeError(
            "RAG exact revision lacks required repository plugins: "
            + ", ".join(sorted(required - observed))
        )
    return dict(value)


def _request_payload(
    *,
    config: Mapping[str, Any],
    case: Mapping[str, Any],
    replay: Mapping[str, Any],
    repository: Path,
    model: str,
    api_key: str,
) -> dict[str, Any]:
    base_sha = case["snapshot"]["baseSha"]
    head_sha = case["snapshot"]["headSha"]
    if replay["baseSha"] != base_sha or replay["headSha"] != head_sha:
        raise ValueError(f"replay lock SHA drift for {case['caseId']}")
    diff = _git_diff(repository, base_sha, head_sha)
    if sha256_text(diff) != case["snapshot"]["diffSha256"]:
        raise ValueError(f"local diff digest drift for {case['caseId']}")
    all_paths = _git_paths(repository, base_sha, head_sha)
    if all_paths != case["snapshot"]["changedPaths"]:
        raise ValueError(f"local changed-path drift for {case['caseId']}")
    deleted = set(
        _git_paths(repository, base_sha, head_sha, diff_filter="D")
    )
    changed = [path for path in all_paths if path not in deleted]
    enrichment = _enrichment(
        repository,
        head_sha=head_sha,
        changed_paths=all_paths,
        deleted_paths=deleted,
        max_file_bytes=int(config["max_enrichment_file_bytes"]),
        max_total_bytes=int(config["max_enrichment_total_bytes"]),
    )
    workspace = str(config.get("rag_workspace") or config.get("project_workspace"))
    project = str(config.get("rag_project") or config.get("project_namespace"))
    provider = str(config.get("provider") or "")
    if not provider or not model:
        raise ValueError("analysis provider and model must be configured")
    payload: dict[str, Any] = {
        "projectId": int(config["project_id"]),
        "projectVcsWorkspace": str(config["project_vcs_workspace"]),
        "projectVcsRepoSlug": str(config["project_vcs_repo_slug"]),
        "projectWorkspace": workspace,
        "projectNamespace": project,
        "aiProvider": provider,
        "aiModel": model,
        "aiApiKey": api_key,
        "aiBaseUrl": config.get("base_url") or None,
        "aiCustomParameters": dict(config.get("custom_parameters") or {}),
        "analysisType": "PR_REVIEW",
        "targetBranchName": replay["baseRef"],
        "sourceBranchName": replay["headRef"],
        "pullRequestId": int(replay["forkPrNumber"]),
        "commitHash": head_sha,
        "currentCommitHash": head_sha,
        "baseCommitHash": base_sha,
        "prTitle": f"Magento 2 review benchmark fixture {case['caseId']}",
        "prDescription": "",
        "prAuthor": "benchmark-fixture",
        "taskContext": {},
        "taskHistoryContext": "",
        "changedFiles": changed,
        "deletedFiles": sorted(deleted),
        "diffSnippets": [],
        "rawDiff": diff,
        "vcsProvider": "github",
        "analysisMode": "FULL",
        "previousCodeAnalysisIssues": [],
        "enrichmentData": enrichment,
        "useMcpTools": False,
        "ragEnabled": bool(config.get("require_exact_index", True)),
        "projectRules": "[]",
    }
    return payload


def run_analysis(
    *,
    execution_corpus_path: Path,
    replay_lock_path: Path | None,
    replay_attestation_path: Path | None = None,
    repository: Path,
    output_dir: Path,
    config: Mapping[str, Any],
    run_id: str | None = None,
    model: str | None = None,
    expected_response_model: str | None = None,
    selected_case_ids: set[str] | None = None,
    limit: int | None = None,
    resume: bool = False,
    execution_context: AnalysisExecutionContext | None = None,
) -> dict[str, Any]:
    invocation_started_at = _now()
    resolved_run_id = _validated_run_id(run_id)
    _validate_output_intent(output_dir, resume=resume)
    corpus = read_json(execution_corpus_path)
    analysis = dict(config["analysis"])
    replay_attestation: Mapping[str, Any] | None = None
    replay_attestation_digest: str | None = None
    if execution_context is None:
        if (
            not isinstance(corpus, Mapping)
            or corpus.get("kind") != EXECUTION_CORPUS_KIND
        ):
            raise ValueError(
                "primary H analysis requires a label-free analysis execution "
                "corpus; the released labeled corpus is forbidden pre-unseal"
            )
        corpus_summary = validate_execution_corpus(corpus)
        assert_label_free_execution_value(
            corpus,
            context="primary H analysis execution corpus",
        )
        if replay_lock_path is None:
            raise ValueError("a replay lock is required")
        replay_lock = read_json(replay_lock_path)
        replay_by_case = validate_replay_lock(
            replay_lock,
            corpus,
            corpus_summary=corpus_summary,
        )
        case_universe = list(corpus["cases"])
        expected_run_kind = RUN_KIND
        manifest_bindings: dict[str, Any] = {}
        run_id_prefix = "m2b"
        replay_lock_artifact = "replay-lock.json"
        execution_corpus_artifact = "analysis-execution-corpus.json"
        execution_corpus_bindings = {
            "executionCorpusDigest": corpus_summary[
                "executionCorpusDigest"
            ],
            "executionCorpusArtifact": execution_corpus_artifact,
        }
        replay_attestation_artifact = (
            "replay-attestation.json"
            if replay_attestation_path is not None
            else None
        )
        if replay_attestation_path is not None:
            replay_attestation = read_json(replay_attestation_path)
            replay_attestation_digest = validate_replay_attestation(
                replay_attestation,
                replay_lock,
                corpus,
                corpus_summary=corpus_summary,
            )
    else:
        if (
            not isinstance(corpus, Mapping)
            or corpus.get("kind") != EXECUTION_CORPUS_KIND
        ):
            raise ValueError(
                "alternate pre-unseal analysis also requires the label-free "
                "analysis execution corpus"
            )
        corpus_summary = validate_execution_corpus(corpus)
        assert_label_free_execution_value(
            corpus,
            context="alternate analysis execution corpus",
        )
        expected_run_kind = str(execution_context.run_kind or "")
        run_id_prefix = str(execution_context.run_id_prefix or "")
        if (
            expected_run_kind == RUN_KIND
            or not expected_run_kind
            or SAFE_RUN_ID.fullmatch(expected_run_kind) is None
            or not run_id_prefix
            or SAFE_RUN_ID.fullmatch(run_id_prefix) is None
        ):
            raise ValueError(
                "alternate analysis execution kind/prefix is unsafe or "
                "collides with the primary H lane"
            )
        case_universe = [dict(case) for case in execution_context.cases]
        primary_ids = [str(case["caseId"]) for case in corpus["cases"]]
        alternate_ids = [
            str(case.get("caseId") or "")
            for case in case_universe
            if isinstance(case, Mapping)
        ]
        if (
            len(alternate_ids) != len(case_universe)
            or alternate_ids != primary_ids
            or len(alternate_ids) != len(set(alternate_ids))
        ):
            raise ValueError(
                "alternate analysis cases must preserve the exact corpus "
                "case order and identity"
            )
        replay_lock = dict(execution_context.replay_lock)
        replay_by_case = {
            str(case_id): dict(item)
            for case_id, item in execution_context.replay_by_case.items()
        }
        if set(replay_by_case) != set(alternate_ids):
            raise ValueError(
                "alternate replay mapping must cover every corpus case exactly"
            )
        replay_attestation = (
            dict(execution_context.replay_attestation)
            if execution_context.replay_attestation is not None
            else None
        )
        replay_attestation_digest = (
            execution_context.replay_attestation_digest
        )
        replay_lock_artifact = str(
            execution_context.replay_lock_artifact or ""
        )
        replay_attestation_artifact = (
            str(execution_context.replay_attestation_artifact)
            if execution_context.replay_attestation_artifact is not None
            else None
        )
        execution_corpus_artifact = "analysis-execution-corpus.json"
        execution_corpus_bindings = {
            "executionCorpusDigest": corpus_summary[
                "executionCorpusDigest"
            ],
            "executionCorpusArtifact": execution_corpus_artifact,
        }
        for label, filename in (
            ("replay lock", replay_lock_artifact),
            ("replay attestation", replay_attestation_artifact),
        ):
            if filename is None:
                continue
            candidate = Path(filename)
            if (
                not filename
                or candidate.name != filename
                or candidate.is_absolute()
            ):
                raise ValueError(
                    f"alternate {label} artifact must be a safe basename"
                )
        manifest_bindings = dict(execution_context.manifest_bindings)
        reserved_manifest_fields = {
            "kind",
            "runId",
            "startedAt",
            "completedAt",
            "corpusId",
            "corpusDigest",
            "executionCorpusDigest",
            "executionCorpusArtifact",
            "analysisModel",
            "analysisProvider",
            "analysisModelRoles",
            "analysisConfig",
            "analysisConfigDigest",
            "replayLockDigest",
            "replayLockArtifact",
            "replayAttestationDigest",
            "replayAttestationArtifact",
            "runtimeProvenance",
            "selectedCaseIds",
            "transport",
            "findingSemantics",
            "attemptPolicy",
            "attemptLedger",
            "cases",
            "status",
            "indexReceiptsBefore",
            "indexReceiptsAfter",
            "runDigest",
        }
        if not manifest_bindings or reserved_manifest_fields.intersection(
            manifest_bindings
        ):
            raise ValueError(
                "alternate analysis manifest bindings are missing or overlap "
                "runner-controlled fields"
            )
        if replay_attestation is not None:
            observed_attestation_digest = sha256_json(
                {
                    key: value
                    for key, value in replay_attestation.items()
                    if key != "attestationDigest"
                }
            )
            if (
                replay_attestation.get("attestationDigest")
                != observed_attestation_digest
                or replay_attestation_digest != observed_attestation_digest
            ):
                raise ValueError(
                    "alternate replay attestation digest/binding is invalid"
                )
    replay_lock_digest = str(replay_lock.get("lockDigest") or "")
    lock_payload = dict(replay_lock)
    declared_lock_digest = lock_payload.pop("lockDigest", None)
    if (
        not replay_lock_digest
        or declared_lock_digest != sha256_json(lock_payload)
    ):
        raise ValueError("replay lock digest is invalid")
    if execution_context is not None and replay_attestation_path is not None:
        raise ValueError(
            "alternate execution supplies its replay attestation through the "
            "validated execution context"
        )
    if replay_attestation is not None:
        max_age_seconds = analysis.get(
            "replay_attestation_max_age_seconds",
            MAX_PAPER_ATTESTATION_AGE_SECONDS,
        )
        validate_replay_attestation_freshness(
            replay_attestation,
            reference_at=invocation_started_at,
            max_age_seconds=max_age_seconds,
        )
    replay_attestation_required = bool(
        analysis.get("require_replay_attestation")
        or analysis.get("require_runtime_provenance")
    )
    if replay_attestation_required and replay_attestation_digest is None:
        raise ValueError(
            "a live replay attestation is required for frozen paper runs"
        )
    if replay_attestation_required:
        max_age_seconds = analysis.get(
            "replay_attestation_max_age_seconds",
            MAX_PAPER_ATTESTATION_AGE_SECONDS,
        )
        if (
            isinstance(max_age_seconds, bool)
            or not isinstance(max_age_seconds, (int, float))
            or not math.isfinite(float(max_age_seconds))
            or max_age_seconds <= 0
            or max_age_seconds > MAX_PAPER_ATTESTATION_AGE_SECONDS
        ):
            raise ValueError(
                "analysis.replay_attestation_max_age_seconds must be "
                "positive and no greater than 3600 for frozen paper runs"
            )
    resolved_model = model or str(analysis.get("model") or "")
    if not resolved_model:
        raise ValueError("analysis model is required")
    resolved_expected_response_model = (
        expected_response_model
        if expected_response_model is not None
        else resolved_model
        if model is not None
        else str(analysis.get("expected_response_model") or resolved_model)
    )
    if not resolved_expected_response_model:
        raise ValueError("expected analysis response model is required")
    analysis["model"] = resolved_model
    analysis["expected_response_model"] = resolved_expected_response_model
    max_case_attempts = analysis.get("max_case_attempts", 1)
    if (
        isinstance(max_case_attempts, bool)
        or not isinstance(max_case_attempts, int)
        or max_case_attempts < 1
        or max_case_attempts > MAX_CASE_ATTEMPTS_LIMIT
    ):
        raise ValueError(
            "analysis.max_case_attempts must be an integer between 1 and "
            f"{MAX_CASE_ATTEMPTS_LIMIT}"
        )
    analysis["max_case_attempts"] = max_case_attempts
    api_key = secret_from_env(analysis, "api_key_env")
    service_secret_env = str(
        analysis.get("service_secret_env") or "CODECROW_SERVICE_SECRET"
    )
    service_secret = os.getenv(service_secret_env, "")
    if not service_secret:
        raise ValueError(
            "the CodeCrow internal service secret is required for isolated "
            "product finalization"
        )
    if not repository.is_dir() or not (repository / ".git").exists():
        raise ValueError("analysis repository must be a local Git clone")
    if int(analysis.get("project_id") or 0) <= 0:
        raise ValueError("analysis.project_id must identify the benchmark project")
    for field in (
        "project_vcs_workspace",
        "project_vcs_repo_slug",
        "project_workspace",
        "project_namespace",
        "finalizer_endpoint",
        "finalizer_container",
    ):
        if not str(analysis.get(field) or ""):
            raise ValueError(f"analysis.{field} is required")
    _validate_project_fork_coordinates(analysis, replay_lock)
    transport = str(analysis.get("transport") or "redis")
    if transport not in {"redis", "http"}:
        raise ValueError("analysis.transport must be redis or http")
    require_model_call_evidence = bool(
        analysis.get("require_model_call_evidence", False)
    )
    analysis["require_model_call_evidence"] = require_model_call_evidence
    quality_capture_container_dir = str(
        analysis.get("quality_capture_container_dir")
        or "/app/logs/review-quality-captures"
    )
    analysis[
        "quality_capture_container_dir"
    ] = quality_capture_container_dir
    if require_model_call_evidence and transport != "redis":
        raise ValueError(
            "analysis model-call evidence requires Redis transport"
        )

    corpus_case_ids = [str(case["caseId"]) for case in case_universe]
    if selected_case_ids is not None:
        unknown_case_ids = sorted(set(selected_case_ids) - set(corpus_case_ids))
        if unknown_case_ids:
            raise ValueError(
                "unknown corpus case IDs: " + ", ".join(unknown_case_ids)
            )
    cases = [
        case
        for case in case_universe
        if selected_case_ids is None or case["caseId"] in selected_case_ids
    ]
    if limit is not None:
        if isinstance(limit, bool) or limit < 1:
            raise ValueError("--limit must be a positive integer")
        cases = cases[:limit]
    if not cases:
        raise ValueError("no corpus cases selected")
    selected_ids = [case["caseId"] for case in cases]
    analysis_config = public_config(analysis)
    analysis_config_digest = sha256_json(analysis_config)
    if (
        execution_context is not None
        and execution_context.required_analysis_config_digest is not None
        and analysis_config_digest
        != execution_context.required_analysis_config_digest
    ):
        raise ValueError(
            "alternate analysis configuration differs from its paired H run"
        )
    runtime_provenance = _runtime_provenance(analysis)
    if (
        execution_context is not None
        and execution_context.required_runtime_images is not None
        and runtime_image_projection(runtime_provenance)
        != dict(execution_context.required_runtime_images)
    ):
        raise ValueError(
            "alternate analysis runtime images differ from its paired H run"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "attempts").mkdir(parents=True, exist_ok=True)
    if execution_corpus_artifact is not None:
        copied_execution_corpus = output_dir / execution_corpus_artifact
        if (
            copied_execution_corpus.exists()
            and read_json(copied_execution_corpus) != corpus
        ):
            raise ValueError(
                "existing run analysis execution corpus cannot be replaced"
            )
        if not copied_execution_corpus.exists():
            write_json(copied_execution_corpus, corpus)
    copied_lock = output_dir / replay_lock_artifact
    if copied_lock.exists() and read_json(copied_lock) != replay_lock:
        raise ValueError("existing run replay lock artifact cannot be replaced")
    if not copied_lock.exists():
        write_json(copied_lock, replay_lock)
    if replay_attestation is not None:
        if replay_attestation_artifact is None:
            raise ValueError(
                "replay attestation artifact name is required"
            )
        copied_attestation = output_dir / replay_attestation_artifact
        if (
            copied_attestation.exists()
            and read_json(copied_attestation) != replay_attestation
        ):
            raise ValueError(
                "existing run replay attestation artifact cannot be replaced"
            )
        if not copied_attestation.exists():
            write_json(copied_attestation, replay_attestation)
    manifest_path = output_dir / "run.json"
    if resume:
        manifest = read_json(manifest_path)
        digest_payload = dict(manifest) if isinstance(manifest, Mapping) else {}
        declared_digest = digest_payload.pop("runDigest", None)
        if (
            not isinstance(manifest, Mapping)
            or declared_digest != sha256_json(digest_payload)
            or manifest.get("kind") != expected_run_kind
            or manifest.get("corpusDigest") != corpus_summary["corpusDigest"]
            or any(
                manifest.get(key) != value
                for key, value in execution_corpus_bindings.items()
            )
            or (
                resolved_run_id is not None
                and manifest.get("runId") != resolved_run_id
            )
            or manifest.get("analysisModel") != resolved_model
            or manifest.get("analysisProvider") != analysis["provider"]
            or not isinstance(
                manifest.get("analysisModelRoles"),
                Mapping,
            )
            or manifest["analysisModelRoles"].get("reviewPipeline")
            != resolved_model
            or manifest["analysisModelRoles"].get(
                "reviewPipelineRequested"
            )
            != resolved_model
            or manifest["analysisModelRoles"].get(
                "reviewPipelineExpectedResponse"
            )
            != resolved_expected_response_model
            or manifest.get("transport") != transport
            or manifest.get("findingSemantics")
            != "java-finalized-transient-first-iteration"
            or manifest.get("analysisConfigDigest") != analysis_config_digest
            or manifest.get("replayLockDigest") != replay_lock_digest
            or manifest.get("replayLockArtifact") != replay_lock_artifact
            or manifest.get("replayAttestationDigest")
            != replay_attestation_digest
            or manifest.get("replayAttestationArtifact")
            != replay_attestation_artifact
            or manifest.get("runtimeProvenance") != runtime_provenance
            or manifest.get("selectedCaseIds") != selected_ids
            or any(
                manifest.get(key) != value
                for key, value in manifest_bindings.items()
            )
        ):
            raise ValueError("existing run manifest cannot be resumed")
        manifest = dict(manifest)
        manifest.pop("runDigest", None)
        _validate_attempt_ledger(
            manifest,
            output_dir=output_dir,
            selected_ids=selected_ids,
            max_case_attempts=max_case_attempts,
            allow_running=True,
        )
        if _recover_running_attempts(
            manifest,
            output_dir=output_dir,
            max_case_attempts=max_case_attempts,
        ):
            _write_manifest(manifest_path, manifest)
            manifest.pop("runDigest", None)
        _validate_attempt_ledger(
            manifest,
            output_dir=output_dir,
            selected_ids=selected_ids,
            max_case_attempts=max_case_attempts,
            allow_running=False,
        )
    else:
        manifest = {
            "kind": expected_run_kind,
            "runId": resolved_run_id or f"{run_id_prefix}-{uuid.uuid4().hex}",
            "startedAt": invocation_started_at,
            "completedAt": None,
            "corpusId": corpus_summary["corpusId"],
            "corpusDigest": corpus_summary["corpusDigest"],
            **execution_corpus_bindings,
            "analysisModel": resolved_model,
            "analysisProvider": analysis["provider"],
            "analysisModelRoles": {
                "reviewPipeline": resolved_model,
                "reviewPipelineRequested": resolved_model,
                "reviewPipelineExpectedResponse": (
                    resolved_expected_response_model
                ),
                "reviewPipelineProviderReported": [],
                "providerReportedByStage": {},
            },
            "analysisConfig": analysis_config,
            "analysisConfigDigest": analysis_config_digest,
            "replayLockDigest": replay_lock_digest,
            "replayLockArtifact": replay_lock_artifact,
            "replayAttestationDigest": replay_attestation_digest,
            "replayAttestationArtifact": replay_attestation_artifact,
            "runtimeProvenance": runtime_provenance,
            "selectedCaseIds": selected_ids,
            "transport": transport,
            "findingSemantics": (
                "java-finalized-transient-first-iteration"
            ),
            "attemptPolicy": _attempt_policy(max_case_attempts),
            "attemptLedger": [],
            "cases": [],
            **manifest_bindings,
        }
        _write_manifest(manifest_path, manifest)
    completed = {
        item["caseId"]
        for item in manifest["cases"]
        if isinstance(item, Mapping) and item.get("status") == "completed"
    }

    initial_receipts: dict[str, dict[str, Any]] = {}
    for case in cases:
        replay = replay_by_case.get(case["caseId"])
        if replay is None:
            raise ValueError(f"replay lock omits {case['caseId']}")
        initial_receipts[case["caseId"]] = exact_index_receipt(
            analysis,
            branch=replay["baseRef"],
            commit=case["snapshot"]["baseSha"],
            service_secret=service_secret,
        )
    if (
        execution_context is not None
        and execution_context.required_index_receipts is not None
        and initial_receipts
        != {
            str(case_id): dict(receipt)
            for case_id, receipt in (
                execution_context.required_index_receipts.items()
            )
        }
    ):
        raise ValueError(
            "alternate analysis base-index receipts differ from its paired H "
            "run"
        )
    if resume:
        for record in manifest["cases"]:
            if (
                isinstance(record, Mapping)
                and record.get("status") == "completed"
                and record.get("indexReceipt")
                != initial_receipts.get(str(record.get("caseId")))
            ):
                raise ValueError(
                    f"existing run index receipt drift for {record.get('caseId')}"
                )
            if isinstance(record, Mapping) and record.get("status") == "completed":
                raw_name = record.get("rawResponse")
                raw_relative = (
                    Path(raw_name) if isinstance(raw_name, str) else None
                )
                raw_path = (
                    output_dir / raw_relative
                    if raw_relative is not None
                    and not raw_relative.is_absolute()
                    and ".." not in raw_relative.parts
                    else None
                )
                if (
                    raw_path is None
                    or not raw_path.is_file()
                    or sha256_json(read_json(raw_path))
                    != record.get("responseDigest")
                ):
                    raise ValueError(
                        f"existing raw response drift for {record.get('caseId')}"
                    )

    for case in cases:
        case_id = case["caseId"]
        if case_id in completed:
            continue
        previous_attempts = [
            item
            for item in manifest["attemptLedger"]
            if item.get("caseId") == case_id
        ]
        if len(previous_attempts) >= max_case_attempts:
            continue
        replay = replay_by_case[case_id]
        started = time.monotonic()
        attempt_number = len(previous_attempts) + 1
        attempt_id = f"attempt-{uuid.uuid4().hex}"
        job_id = f"{manifest['runId']}:{case_id}:{uuid.uuid4().hex}"
        start_artifact = _attempt_artifact_name(attempt_id, "start")
        result_artifact = _attempt_artifact_name(attempt_id, "result")
        attempt: dict[str, Any] = {
            "attemptId": attempt_id,
            "caseId": case_id,
            "attemptNumber": attempt_number,
            "jobId": job_id,
            "status": "running",
            "startedAt": _now(),
            "completedAt": None,
            "durationSeconds": None,
            "requestDigest": None,
            "requestControlDigest": None,
            "startArtifact": start_artifact,
            "startArtifactDigest": None,
            "resultArtifact": result_artifact,
            "resultArtifactDigest": None,
            "error": None,
            "stoppingReason": None,
            "retryEligible": False,
            "modelCallEvidence": None,
        }
        record: dict[str, Any] = {
            "caseId": case_id,
            "jobId": job_id,
            "attemptId": attempt_id,
            "attemptNumber": attempt_number,
            "attemptCount": attempt_number,
            "maxAttempts": max_case_attempts,
            "sizeBand": case["sizeBand"],
            "partition": case["partition"],
            "status": "running",
            "startedAt": attempt["startedAt"],
            "completedAt": None,
            "durationSeconds": None,
            "requestDigest": None,
            "requestControlDigest": None,
            "responseDigest": None,
            "rawResponse": None,
            "analysisState": None,
            "retrievalEvidence": None,
            "modelCallEvidence": None,
            "productFinalization": None,
            "findings": [],
            "error": None,
            "indexReceipt": initial_receipts[case_id],
            "retryEligible": False,
            "stoppingReason": None,
        }
        manifest["attemptLedger"].append(attempt)
        manifest["cases"] = [
            item for item in manifest["cases"] if item.get("caseId") != case_id
        ] + [record]
        manifest["cases"].sort(key=lambda item: item["caseId"])
        manifest["completedAt"] = None
        manifest["status"] = "running"
        _write_manifest(manifest_path, manifest)
        case_secret_values = {api_key, service_secret}
        payload: dict[str, Any] | None = None
        artifact_events: list[dict[str, Any]] = []
        artifact_response: Any = None
        artifact_finalization: dict[str, Any] | None = None
        artifact_model_call_evidence: dict[str, Any] | None = None
        try:
            payload = _request_payload(
                config=analysis,
                case=case,
                replay=replay,
                repository=repository,
                model=resolved_model,
                api_key=api_key,
            )
            # This guard runs immediately before either transport boundary.
            # The request may contain source code/diff text, but it must never
            # acquire a label-shaped field through a future runner refactor.
            assert_label_free_execution_value(
                payload,
                context=f"analysis request for {case_id}",
            )
            case_secret_values.update(configured_secret_values(payload))
            attempt["requestDigest"] = _safe_request_digest(payload)
            attempt["requestControlDigest"] = _request_control_digest(payload)
            record["requestDigest"] = attempt["requestDigest"]
            record["requestControlDigest"] = attempt["requestControlDigest"]
            start_payload = {
                "kind": ATTEMPT_START_KIND,
                "attemptId": attempt_id,
                "caseId": case_id,
                "attemptNumber": attempt_number,
                "jobId": job_id,
                "startedAt": attempt["startedAt"],
                "maxAttempts": max_case_attempts,
                "requestDigest": attempt["requestDigest"],
                "requestControlDigest": attempt["requestControlDigest"],
                "redactedRequest": _redacted_request(payload),
            }
            _write_new_json(
                _artifact_path(output_dir, start_artifact),
                start_payload,
            )
            attempt["startArtifactDigest"] = sha256_json(start_payload)
            _write_manifest(manifest_path, manifest)
            if transport == "redis":
                events, response = _queue_review(
                    analysis,
                    job_id=job_id,
                    payload=payload,
                )
            else:
                events, response = _http_review(
                    analysis,
                    payload=payload,
                    service_secret=service_secret,
                )
            require_no_secret_values(
                {"events": events, "response": response},
                case_secret_values,
                context="analysis response evidence",
            )
            artifact_events = events
            artifact_response = response
            quality_capture_receipt = _quality_capture_receipt_from_events(
                events,
                provider=str(analysis["provider"]),
                requested_model=resolved_model,
                expected_response_model=resolved_expected_response_model,
                required=require_model_call_evidence,
            )
            if quality_capture_receipt is not None:
                artifact_model_call_evidence = (
                    _archive_quality_capture_artifact(
                        config=analysis,
                        receipt=quality_capture_receipt,
                        output_dir=output_dir,
                        attempt_id=attempt_id,
                        pull_request_id=int(replay["forkPrNumber"]),
                        secret_values=case_secret_values,
                        provider=str(analysis["provider"]),
                        requested_model=resolved_model,
                        expected_response_model=(
                            resolved_expected_response_model
                        ),
                        expected_request=_redacted_request(payload),
                    )
                )
                record["modelCallEvidence"] = (
                    artifact_model_call_evidence
                )
            result = _analysis_result(response)
            product_finalization = _product_finalize(
                analysis,
                response=response,
                request_payload=payload,
                service_secret=service_secret,
            )
            require_no_secret_values(
                product_finalization,
                case_secret_values,
                context="product finalizer response",
            )
            artifact_finalization = product_finalization
            findings = _findings(product_finalization)
            retrieval_required = bool(
                analysis.get("require_retrieval_evidence", True)
            )
            retrieval_evidence = _retrieval_evidence(
                events,
                required=retrieval_required,
                expected_revision_binding=(
                    {
                        "pullRequestId": int(replay["forkPrNumber"]),
                        "targetBranch": replay["baseRef"],
                        "sourceRevision": case["snapshot"]["headSha"],
                        "baseRevision": case["snapshot"]["baseSha"],
                        "baseGenerationManifestSha256": initial_receipts[
                            case_id
                        ]["generation_manifest_sha256"],
                        "basePluginFingerprint": initial_receipts[case_id][
                            "plugin_fingerprint"
                        ],
                        "basePluginDescriptorFingerprint": initial_receipts[
                            case_id
                        ]["plugin_descriptor_fingerprint"],
                        "basePluginImplementationFingerprint": (
                            initial_receipts[case_id][
                                "plugin_implementation_fingerprint"
                            ]
                        ),
                        "baseIndexRepresentationFingerprint": (
                            initial_receipts[case_id][
                                "index_representation_fingerprint"
                            ]
                        ),
                    }
                    if retrieval_required
                    else None
                ),
            )
            record.update(
                {
                    "status": "completed",
                    "completedAt": _now(),
                    "durationSeconds": round(time.monotonic() - started, 3),
                    "analysisState": (
                        result.get("analysisState") or result.get("status")
                    ),
                    "retrievalEvidence": retrieval_evidence,
                    "productFinalization": {
                        "kind": product_finalization["kind"],
                        "rawIssueCount": product_finalization[
                            "rawIssueCount"
                        ],
                        "finalIssueCount": product_finalization[
                            "finalIssueCount"
                        ],
                        "responseDigest": sha256_json(
                            product_finalization
                        ),
                        "analysisDataValidated": True,
                        "persisted": False,
                        "published": False,
                        "previousIssueStateUsed": False,
                    },
                    "findings": findings,
                }
            )
        except Exception as exc:
            safe_error = redact_secret_text(
                f"{type(exc).__name__}: {exc}",
                case_secret_values,
            )
            record.update(
                {
                    "status": "failed",
                    "completedAt": _now(),
                    "durationSeconds": round(time.monotonic() - started, 3),
                    "error": safe_error,
                }
            )
        stopping_reason, retry_eligible = _attempt_stopping_reason(
            status=str(record["status"]),
            attempt_number=attempt_number,
            max_case_attempts=max_case_attempts,
        )
        result_payload = {
            "kind": ATTEMPT_RESULT_KIND,
            "attemptId": attempt_id,
            "caseId": case_id,
            "attemptNumber": attempt_number,
            "jobId": job_id,
            "status": record["status"],
            "startedAt": attempt["startedAt"],
            "completedAt": record["completedAt"],
            "durationSeconds": record["durationSeconds"],
            "requestDigest": attempt.get("requestDigest"),
            "requestControlDigest": attempt.get("requestControlDigest"),
            "redactedRequest": (
                _redacted_request(payload) if payload is not None else None
            ),
            "events": artifact_events,
            "response": artifact_response,
            "productFinalization": artifact_finalization,
            "modelCallEvidence": artifact_model_call_evidence,
            "caseOutcome": _case_outcome(record),
            "error": record["error"],
            "terminationReason": (
                "case_completed"
                if record["status"] == "completed"
                else "case_exception"
            ),
            "stoppingReason": stopping_reason,
            "retryEligible": retry_eligible,
        }
        result_path = _artifact_path(output_dir, result_artifact)
        _write_new_json(result_path, result_payload)
        _apply_attempt_result(
            attempt=attempt,
            record=record,
            result=result_payload,
            result_digest=sha256_json(result_payload),
            max_case_attempts=max_case_attempts,
        )
        manifest["cases"] = [
            item for item in manifest["cases"] if item.get("caseId") != case_id
        ] + [record]
        manifest["cases"].sort(key=lambda item: item["caseId"])
        _write_manifest(manifest_path, manifest)

    final_receipts = {}
    for case in cases:
        replay = replay_by_case[case["caseId"]]
        final_receipts[case["caseId"]] = exact_index_receipt(
            analysis,
            branch=replay["baseRef"],
            commit=case["snapshot"]["baseSha"],
            service_secret=service_secret,
        )
        if final_receipts[case["caseId"]] != initial_receipts[case["caseId"]]:
            raise RuntimeError(
                f"RAG base-index receipt changed during run for {case['caseId']}"
            )
    manifest["completedAt"] = _now()
    manifest["indexReceiptsBefore"] = initial_receipts
    manifest["indexReceiptsAfter"] = final_receipts
    provider_reported_models: set[str] = set()
    models_by_stage: dict[str, set[str]] = {}
    for case_record in manifest["cases"]:
        if case_record.get("status") != "completed":
            continue
        model_evidence = case_record.get("modelCallEvidence")
        receipt = (
            model_evidence.get("receipt")
            if isinstance(model_evidence, Mapping)
            else None
        )
        if not isinstance(receipt, Mapping):
            continue
        provider_reported_models.update(
            model_name
            for model_name in receipt.get("providerReportedModels") or []
            if isinstance(model_name, str)
        )
        for call in receipt.get("calls") or []:
            if not isinstance(call, Mapping):
                continue
            stage = call.get("stage")
            if not isinstance(stage, str) or not stage:
                continue
            models_by_stage.setdefault(stage, set()).update(
                model_name
                for model_name in call.get("providerReportedModels") or []
                if isinstance(model_name, str)
            )
    manifest["analysisModelRoles"] = {
        "reviewPipeline": resolved_model,
        "reviewPipelineRequested": resolved_model,
        "reviewPipelineExpectedResponse": resolved_expected_response_model,
        "reviewPipelineProviderReported": sorted(provider_reported_models),
        "providerReportedByStage": {
            stage: sorted(models)
            for stage, models in sorted(models_by_stage.items())
        },
    }
    manifest["status"] = (
        "completed"
        if len(manifest["cases"]) == len(selected_ids)
        and all(item["status"] == "completed" for item in manifest["cases"])
        else "partial"
    )
    _validate_attempt_ledger(
        manifest,
        output_dir=output_dir,
        selected_ids=selected_ids,
        max_case_attempts=max_case_attempts,
        allow_running=False,
    )
    _write_manifest(manifest_path, manifest)
    return manifest
