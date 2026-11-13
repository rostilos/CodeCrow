from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .config import load_config
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
from .runner import exact_index_receipt
from .util import (
    canonical_json,
    configured_secret_values,
    deterministic_git_diff_command,
    hermetic_git_environment,
    public_config,
    read_json,
    redact_secret_text,
    require_no_secret_values,
    sha256_json,
    write_json,
)


PREFLIGHT_KIND = "codecrow-magento2-operator-preflight"
SOURCE_TREE_SCHEMA = "codecrow.repository-source-tree"
INDEX_SELECTION_POLICY_SCHEMA = "codecrow.repository-index-selection"
MAX_PAPER_ATTESTATION_AGE_SECONDS = 3_600
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
SHA256_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

ReceiptReader = Callable[..., Mapping[str, Any]]
SnapshotProbe = Callable[[Path, Mapping[str, Any]], Mapping[str, Any]]
RuntimeProbe = Callable[[str], Mapping[str, Any] | None]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _git_environment() -> dict[str, str]:
    environment = hermetic_git_environment(offline=True)
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    return environment


def _run_git(
    repository: Path,
    arguments: Sequence[str],
    *,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    try:
        return subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(repository),
                *arguments,
            ],
            env=_git_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            errors="replace" if text else None,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"cannot execute read-only Git probe: {exc}") from exc


def _require_git_success(
    completed: subprocess.CompletedProcess[Any],
    description: str,
) -> Any:
    if completed.returncode != 0:
        stderr = completed.stderr
        if isinstance(stderr, bytes):
            detail = stderr.decode("utf-8", errors="replace").strip()
        else:
            detail = str(stderr or "").strip()
        raise RuntimeError(
            description + (f": {detail[:1000]}" if detail else "")
        )
    return completed.stdout


def _feed_framed(hasher: Any, value: bytes) -> None:
    hasher.update(len(value).to_bytes(8, "big"))
    hasher.update(value)


def _source_tree_sha256_at_commit(repository: Path, commit: str) -> str:
    """Reconstruct the canonical indexing source identity without checkout.

    The probe reads only Git objects. It deliberately rejects submodules and
    non-blob entries because those do not have an unambiguous equivalence to
    the no-follow filesystem attestation used by repository indexing.
    """

    raw = _require_git_success(
        _run_git(
            repository,
            ["ls-tree", "-r", "-z", "--full-tree", commit],
            text=False,
        ),
        f"cannot enumerate source tree for {commit}",
    )
    entries: list[tuple[bytes, str, bytes]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            identity, path = record.split(b"\t", 1)
            mode_raw, object_type_raw, object_id_raw = identity.split(b" ", 2)
            mode = mode_raw.decode("ascii")
            object_type = object_type_raw.decode("ascii")
            object_id = object_id_raw.decode("ascii")
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError("Git returned a malformed tree entry") from exc
        if object_type != "blob" or mode not in {"100644", "100755", "120000"}:
            raise RuntimeError(
                "source tree contains an unsupported non-file Git entry at "
                + path.decode("utf-8", errors="replace")
            )
        entries.append((path, mode, object_id.encode("ascii")))
    entries.sort(key=lambda item: item[0])

    command = [
        "git",
        "--no-replace-objects",
        "-C",
        str(repository),
        "cat-file",
        "--batch",
    ]
    try:
        process = subprocess.Popen(
            command,
            env=_git_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise RuntimeError(f"cannot execute read-only Git object probe: {exc}") from exc
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.kill()
        raise RuntimeError("cannot open Git object probe streams")

    hasher = hashlib.sha256()
    _feed_framed(hasher, SOURCE_TREE_SCHEMA.encode("ascii"))
    try:
        for path, mode, object_id in entries:
            process.stdin.write(object_id + b"\n")
            process.stdin.flush()
            header = process.stdout.readline()
            parts = header.rstrip(b"\n").split(b" ")
            if len(parts) != 3 or parts[1] != b"blob":
                raise RuntimeError(
                    "Git object probe returned a missing or non-blob object"
                )
            try:
                size = int(parts[2])
            except ValueError as exc:
                raise RuntimeError("Git object probe returned an invalid size") from exc
            content = process.stdout.read(size)
            delimiter = process.stdout.read(1)
            if len(content) != size or delimiter != b"\n":
                raise RuntimeError("Git object probe returned truncated content")
            kind = b"symlink" if mode == "120000" else b"file"
            _feed_framed(hasher, kind)
            _feed_framed(hasher, path)
            if kind == b"symlink":
                _feed_framed(hasher, content)
            else:
                hasher.update(size.to_bytes(8, "big"))
                hasher.update(content)
        process.stdin.close()
        return_code = process.wait()
        stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
        if return_code != 0:
            raise RuntimeError(
                "Git object probe failed" + (f": {stderr[:1000]}" if stderr else "")
            )
    except BaseException:
        if process.poll() is None:
            process.kill()
            process.wait()
        raise

    hasher.update(len(entries).to_bytes(8, "big"))
    return hasher.hexdigest()


def _default_snapshot_probe(
    repository: Path,
    case: Mapping[str, Any],
) -> dict[str, Any]:
    snapshot = case["snapshot"]
    base = str(snapshot["baseSha"])
    head = str(snapshot["headSha"])
    for label, commit in (("base", base), ("head", head)):
        _require_git_success(
            _run_git(repository, ["cat-file", "-e", f"{commit}^{{commit}}"]),
            f"{label} commit is unavailable for {case['caseId']}",
        )
    ancestry = _run_git(repository, ["merge-base", "--is-ancestor", base, head])
    if ancestry.returncode != 0:
        if ancestry.returncode == 1:
            raise RuntimeError(
                f"base is not an ancestor of head for {case['caseId']}"
            )
        _require_git_success(
            ancestry,
            f"cannot verify base/head ancestry for {case['caseId']}",
        )

    diff = _require_git_success(
        subprocess.run(
            deterministic_git_diff_command(
                repository,
                "--full-index",
                base,
                head,
            ),
            env=_git_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            check=False,
        ),
        f"cannot reconstruct diff for {case['caseId']}",
    )
    paths_raw = _require_git_success(
        subprocess.run(
            deterministic_git_diff_command(
                repository,
                "--name-only",
                "-z",
                base,
                head,
            ),
            env=_git_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="surrogateescape",
            check=False,
        ),
        f"cannot reconstruct changed paths for {case['caseId']}",
    )
    return {
        "basePresent": True,
        "headPresent": True,
        "baseAncestorHead": True,
        "diffSha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        "changedPaths": sorted({path for path in paths_raw.split("\0") if path}),
        "baseSourceTreeSha256": _source_tree_sha256_at_commit(repository, base),
    }


def _default_runtime_probe(name: str) -> dict[str, Any] | None:
    try:
        completed = subprocess.run(
            ["docker", "inspect", "--format", "{{json .}}", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
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
    container_config = (
        value.get("Config") if isinstance(value.get("Config"), Mapping) else {}
    )
    state = value.get("State") if isinstance(value.get("State"), Mapping) else {}
    health = (
        state.get("Health") if isinstance(state.get("Health"), Mapping) else {}
    )
    return {
        "containerId": value.get("Id"),
        "imageId": value.get("Image"),
        "imageReference": container_config.get("Image"),
        "status": state.get("Status"),
        "health": health.get("Status"),
    }


def _check(
    checks: list[dict[str, Any]],
    check_id: str,
    *,
    passed: bool,
    failures: Sequence[Mapping[str, str]] = (),
    blocked: bool = False,
    required_for_run: bool = True,
    required_for_paper: bool = True,
) -> None:
    checks.append(
        {
            "id": check_id,
            "requiredForRun": required_for_run,
            "requiredForPaper": required_for_paper,
            "status": "passed" if passed else "blocked" if blocked else "failed",
            "failures": [dict(item) for item in failures],
        }
    )


def _failure(
    code: str,
    detail: str,
    *,
    secrets: Sequence[str] = (),
) -> dict[str, str]:
    return {
        "code": code,
        "detail": redact_secret_text(detail[:2000], secrets),
    }


def _environment_requirements(
    config: Mapping[str, Any],
    environment: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    github = config.get("github")
    analysis = config.get("analysis")
    judge = config.get("judge")
    sections = {
        "github": github if isinstance(github, Mapping) else {},
        "analysis": analysis if isinstance(analysis, Mapping) else {},
        "judge": judge if isinstance(judge, Mapping) else {},
    }
    requirements = [
        ("github_read", sections["github"].get("token_env")),
        ("analysis_provider", sections["analysis"].get("api_key_env")),
        (
            "codecrow_service",
            sections["analysis"].get("service_secret_env")
            or "CODECROW_SERVICE_SECRET",
        ),
        ("judge_provider", sections["judge"].get("api_key_env")),
    ]
    observations: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    secret_values: list[str] = []
    for purpose, value in requirements:
        variable = str(value or "")
        valid_name = bool(ENV_NAME.fullmatch(variable))
        present = valid_name and bool(environment.get(variable))
        observations.append(
            {
                "purpose": purpose,
                "variable": variable if valid_name else "<invalid>",
                "present": present,
            }
        )
        if not valid_name:
            failures.append(
                _failure(
                    "invalid_environment_variable_name",
                    f"{purpose} does not configure a valid environment variable name",
                )
            )
        elif not present:
            failures.append(
                _failure(
                    "missing_environment_variable",
                    f"{purpose} environment variable {variable} is unset",
                )
            )
        else:
            secret_values.append(str(environment[variable]))
    return observations, failures, secret_values


def _effective_configuration(
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    analysis_value = config.get("analysis")
    judge_value = config.get("judge")
    analysis = (
        copy.deepcopy(dict(analysis_value))
        if isinstance(analysis_value, Mapping)
        else {}
    )
    judge = (
        copy.deepcopy(dict(judge_value))
        if isinstance(judge_value, Mapping)
        else {}
    )
    analysis_model = str(analysis.get("model") or "").strip()
    analysis["model"] = analysis_model
    analysis["expected_response_model"] = str(
        analysis.get("expected_response_model") or analysis_model
    ).strip()
    analysis["service_secret_env"] = str(
        analysis.get("service_secret_env") or "CODECROW_SERVICE_SECRET"
    )
    analysis["max_case_attempts"] = analysis.get("max_case_attempts", 1)
    analysis["require_model_call_evidence"] = bool(
        analysis.get("require_model_call_evidence", False)
    )
    analysis["quality_capture_container_dir"] = str(
        analysis.get("quality_capture_container_dir")
        or "/app/logs/review-quality-captures"
    )

    judge_model = str(judge.get("model") or "").strip()
    judge["model"] = judge_model
    judge["expected_response_model"] = str(
        judge.get("expected_response_model") or judge_model
    ).strip()
    return analysis, judge


def _validate_receipt(
    receipt: Any,
    *,
    analysis: Mapping[str, Any],
    locked: Mapping[str, Any],
    source_tree_sha256: str | None,
) -> list[dict[str, str]]:
    if not isinstance(receipt, Mapping):
        return [_failure("receipt_missing", "exact base-index receipt is missing")]
    failures: list[dict[str, str]] = []
    workspace = str(
        analysis.get("rag_workspace")
        or analysis.get("project_workspace")
        or ""
    )
    project = str(
        analysis.get("rag_project")
        or analysis.get("project_namespace")
        or ""
    )
    expected_identity = {
        "workspace": workspace,
        "project": project,
        "branch": locked["baseRef"],
        "commit": locked["baseSha"],
        "repository_revision": locked["baseSha"],
    }
    mismatched = [
        field
        for field, expected in expected_identity.items()
        if receipt.get(field) != expected
    ]
    if mismatched:
        failures.append(
            _failure(
                "receipt_identity_mismatch",
                "exact receipt identity drifted: " + ", ".join(mismatched),
            )
        )
    count = receipt.get("point_count")
    members = receipt.get("generation_member_count")
    if (
        receipt.get("generation_schema")
        != "codecrow.repository-index-generation"
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count <= 0
        or isinstance(members, bool)
        or not isinstance(members, int)
        or members <= 0
        or count != members + 1
    ):
        failures.append(
            _failure(
                "receipt_generation_invalid",
                "sealed generation schema/count relationship is invalid",
            )
        )
    for field in (
        "generation_members_sha256",
        "generation_manifest_sha256",
        "repository_facts_sha256",
        "source_tree_sha256",
    ):
        if not isinstance(receipt.get(field), str) or not SHA256_HEX.fullmatch(
            str(receipt.get(field))
        ):
            failures.append(
                _failure(
                    f"{field}_invalid",
                    f"receipt {field} is not a lowercase SHA-256 digest",
                )
            )
    if (
        source_tree_sha256 is None
        or receipt.get("source_tree_sha256") != source_tree_sha256
    ):
        failures.append(
            _failure(
                "source_tree_mismatch",
                "receipt source tree does not match the local immutable B snapshot",
            )
        )

    include_patterns = receipt.get("index_include_patterns")
    exclude_patterns = receipt.get("index_exclude_patterns")
    if (
        not isinstance(include_patterns, list)
        or any(not isinstance(item, str) for item in include_patterns)
        or include_patterns != sorted(set(include_patterns))
        or not isinstance(exclude_patterns, list)
        or any(not isinstance(item, str) for item in exclude_patterns)
        or exclude_patterns != sorted(set(exclude_patterns))
    ):
        failures.append(
            _failure(
                "selection_policy_invalid",
                "index selection patterns are not canonical sorted sets",
            )
        )
    else:
        policy = {
            "schema": INDEX_SELECTION_POLICY_SCHEMA,
            "includePatterns": include_patterns,
            "excludePatterns": exclude_patterns,
        }
        if receipt.get("index_selection_policy_sha256") != sha256_json(policy):
            failures.append(
                _failure(
                    "selection_policy_digest_mismatch",
                    "index selection policy digest does not bind its patterns",
                )
            )
    for field in (
        "plugin_fingerprint",
        "plugin_descriptor_fingerprint",
        "plugin_implementation_fingerprint",
        "index_representation_fingerprint",
    ):
        if not isinstance(receipt.get(field), str) or not SHA256_FINGERPRINT.fullmatch(
            str(receipt.get(field))
        ):
            failures.append(
                _failure(
                    f"{field}_invalid",
                    f"receipt {field} is not a SHA-256 fingerprint",
                )
            )
    plugin_ids = receipt.get("plugin_ids")
    required_plugins = {
        str(item)
        for item in analysis.get("required_repository_plugins") or []
        if isinstance(item, str) and item
    }
    if (
        not isinstance(plugin_ids, list)
        or not plugin_ids
        or any(not isinstance(item, str) or not item for item in plugin_ids)
        or len(plugin_ids) != len(set(plugin_ids))
        or not required_plugins.issubset(set(plugin_ids))
    ):
        failures.append(
            _failure(
                "plugin_identity_invalid",
                "receipt plugin identities are invalid or omit a required plugin",
            )
        )
    return failures


def _receipt_projection(
    case_id: str,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "caseId": case_id,
        "branch": receipt.get("branch"),
        "commit": receipt.get("commit"),
        "receiptDigest": sha256_json(receipt),
        "pointCount": receipt.get("point_count"),
        "generationMemberCount": receipt.get("generation_member_count"),
        "generationMembersSha256": receipt.get("generation_members_sha256"),
        "generationManifestSha256": receipt.get(
            "generation_manifest_sha256"
        ),
        "sourceTreeSha256": receipt.get("source_tree_sha256"),
        "repositoryFactsSha256": receipt.get("repository_facts_sha256"),
    }


def _cross_receipt_failures(
    receipts: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    if len(receipts) != 50:
        return [
            _failure(
                "receipt_set_incomplete",
                f"expected 50 exact receipts, observed {len(receipts)}",
            )
        ]
    failures: list[dict[str, str]] = []
    stable_fields = (
        "workspace",
        "project",
        "generation_schema",
        "plugin_ids",
        "plugin_fingerprint",
        "plugin_descriptor_fingerprint",
        "plugin_implementation_fingerprint",
        "index_representation_fingerprint",
        "index_include_patterns",
        "index_exclude_patterns",
        "index_selection_policy_sha256",
    )
    reference = receipts[0]
    drifted = [
        field
        for field in stable_fields
        if any(receipt.get(field) != reference.get(field) for receipt in receipts[1:])
    ]
    if drifted:
        failures.append(
            _failure(
                "receipt_control_drift",
                "base indexes do not share exact plugin/representation/selection "
                "controls: "
                + ", ".join(drifted),
            )
        )
    manifests: dict[str, tuple[Any, Any]] = {}
    source_trees: dict[str, str] = {}
    for receipt in receipts:
        manifest = str(receipt.get("generation_manifest_sha256") or "")
        identity = (receipt.get("commit"), receipt.get("source_tree_sha256"))
        previous_identity = manifests.setdefault(manifest, identity)
        if previous_identity != identity:
            failures.append(
                _failure(
                    "generation_manifest_reused",
                    "one generation manifest digest is bound to different "
                    "revision/source-tree identities",
                )
            )
            break
        commit = str(receipt.get("commit") or "")
        source_tree = str(receipt.get("source_tree_sha256") or "")
        previous_tree = source_trees.setdefault(commit, source_tree)
        if previous_tree != source_tree:
            failures.append(
                _failure(
                    "source_tree_commit_drift",
                    "one base commit is bound to different source-tree identities",
                )
            )
            break
    return failures


def build_operator_preflight(
    *,
    config: Mapping[str, Any],
    execution_corpus: Any,
    replay_lock: Any,
    repository: Path,
    replay_attestation: Any | None = None,
    environment: Mapping[str, str] | None = None,
    reference_at: str | None = None,
    receipt_reader: ReceiptReader = exact_index_receipt,
    snapshot_probe: SnapshotProbe = _default_snapshot_probe,
    runtime_probe: RuntimeProbe = _default_runtime_probe,
) -> dict[str, Any]:
    """Build a fail-closed, secret-free readiness artifact.

    This function performs only read operations against Git, the exact-index
    endpoint, and Docker/container metadata. It never indexes a repository,
    creates a replay ref/PR, enqueues analysis, calls a model, or finalizes
    product findings.
    """

    observed_at = reference_at or _now()
    environment = environment if environment is not None else os.environ
    checks: list[dict[str, Any]] = []

    env_observations, env_failures, secret_values = _environment_requirements(
        config,
        environment,
    )
    secret_values.extend(sorted(configured_secret_values(config)))
    _check(
        checks,
        "environment.required_variables",
        passed=not env_failures,
        failures=env_failures,
    )

    corpus_summary: dict[str, Any] | None = None
    corpus_failures: list[dict[str, str]] = []
    try:
        if (
            not isinstance(execution_corpus, Mapping)
            or execution_corpus.get("kind") != EXECUTION_CORPUS_KIND
        ):
            raise ValueError(
                "operator preflight requires the label-free analysis "
                "execution corpus"
            )
        corpus_summary = validate_execution_corpus(execution_corpus)
        assert_label_free_execution_value(
            execution_corpus,
            context="operator preflight execution corpus",
        )
        if (
            corpus_summary.get("partitionCounts")
            != {"development": 30, "sealed": 20}
        ):
            raise ValueError("label-free release partition binding is invalid")
    except (TypeError, ValueError, RuntimeError) as exc:
        corpus_failures.append(
            _failure(
                "strict_corpus_invalid",
                str(exc),
                secrets=secret_values,
            )
        )
    _check(
        checks,
        "corpus.label_free_execution_release",
        passed=not corpus_failures,
        failures=corpus_failures,
    )

    lock_by_case: dict[str, dict[str, Any]] = {}
    replay_failures: list[dict[str, str]] = []
    if corpus_summary is None:
        replay_failures.append(
            _failure(
                "strict_corpus_unavailable",
                "replay lock validation is blocked by the strict corpus",
            )
        )
    else:
        try:
            lock_by_case = validate_replay_lock(
                replay_lock,
                execution_corpus,
                corpus_summary=corpus_summary,
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            replay_failures.append(
                _failure(
                    "primary_replay_lock_invalid",
                    str(exc),
                    secrets=secret_values,
                )
            )
    _check(
        checks,
        "replay.primary_lock",
        passed=not replay_failures,
        failures=replay_failures,
        blocked=corpus_summary is None,
    )

    analysis, judge = _effective_configuration(config)
    fork_repository = (
        str(replay_lock.get("forkRepository") or "")
        if isinstance(replay_lock, Mapping)
        else ""
    )
    configured_fork = (
        f"{str(analysis.get('project_vcs_workspace') or '')}/"
        f"{str(analysis.get('project_vcs_repo_slug') or '')}"
    )
    workspace = str(
        analysis.get("rag_workspace")
        or analysis.get("project_workspace")
        or ""
    )
    project = str(
        analysis.get("rag_project")
        or analysis.get("project_namespace")
        or ""
    )
    identity_failures: list[dict[str, str]] = []
    project_id = analysis.get("project_id")
    if (
        isinstance(project_id, bool)
        or not isinstance(project_id, int)
        or project_id <= 0
    ):
        identity_failures.append(
            _failure(
                "project_id_invalid",
                "analysis.project_id must identify a positive benchmark project",
            )
        )
    if not fork_repository or configured_fork != fork_repository:
        identity_failures.append(
            _failure(
                "fork_identity_mismatch",
                "configured project VCS owner/repository does not exactly match "
                "the replay lock fork",
            )
        )
    project_workspace = str(analysis.get("project_workspace") or "")
    project_namespace = str(analysis.get("project_namespace") or "")
    if not project_workspace or not project_namespace or not workspace or not project:
        identity_failures.append(
            _failure(
                "project_coordinates_missing",
                "CodeCrow project and RAG workspace/project coordinates are required",
            )
        )
    if (
        workspace != project_workspace
        or project != project_namespace
    ):
        identity_failures.append(
            _failure(
                "rag_project_identity_mismatch",
                "RAG coordinates must resolve to the configured benchmark "
                "project workspace/namespace",
            )
        )
    _check(
        checks,
        "configuration.project_identity",
        passed=not identity_failures,
        failures=identity_failures,
    )

    model_failures: list[dict[str, str]] = []
    analysis_provider = str(analysis.get("provider") or "").strip()
    analysis_model = str(analysis.get("model") or "").strip()
    analysis_expected = str(
        analysis.get("expected_response_model") or ""
    ).strip()
    judge_model = str(judge.get("model") or "").strip()
    judge_expected = str(judge.get("expected_response_model") or "").strip()
    judge_base_url = str(judge.get("base_url") or "").strip()
    for field, value in (
        ("analysis.provider", analysis_provider),
        ("analysis.model", analysis_model),
        ("analysis.expected_response_model", analysis_expected),
        ("judge.model", judge_model),
        ("judge.expected_response_model", judge_expected),
        ("judge.base_url", judge_base_url),
    ):
        if not value:
            model_failures.append(
                _failure(
                    "model_identity_missing",
                    f"{field} must be configured before a benchmark run",
                )
            )
    _check(
        checks,
        "configuration.effective_models",
        passed=not model_failures,
        failures=model_failures,
    )

    control_expectations = {
        "exactIndexRequired": analysis.get("require_exact_index") is True,
        "retrievalEvidenceRequired": (
            analysis.get("require_retrieval_evidence") is True
        ),
        "modelCallEvidenceRequired": (
            analysis.get("require_model_call_evidence") is True
        ),
        "liveReplayAttestationRequired": (
            analysis.get("require_replay_attestation") is True
        ),
        "runtimeProvenanceRequired": (
            analysis.get("require_runtime_provenance") is True
        ),
        "redisTransport": str(analysis.get("transport") or "") == "redis",
    }
    control_failures = [
        _failure(
            "paper_runtime_control_disabled",
            f"{name} must be true for a paper run",
        )
        for name, enabled in control_expectations.items()
        if not enabled
    ]
    maximum_age = analysis.get(
        "replay_attestation_max_age_seconds",
        MAX_PAPER_ATTESTATION_AGE_SECONDS,
    )
    if (
        isinstance(maximum_age, bool)
        or not isinstance(maximum_age, (int, float))
        or maximum_age <= 0
        or maximum_age > MAX_PAPER_ATTESTATION_AGE_SECONDS
    ):
        control_failures.append(
            _failure(
                "attestation_freshness_policy_invalid",
                "paper replay attestation age must be positive and <= 3600 seconds",
            )
        )
    _check(
        checks,
        "configuration.paper_runtime_controls",
        passed=not control_failures,
        failures=control_failures,
    )

    attestation_digest: str | None = None
    attestation_age_seconds: float | None = None
    attestation_failures: list[dict[str, str]] = []
    if replay_attestation is None:
        attestation_failures.append(
            _failure(
                "live_attestation_missing",
                "a fresh live replay attestation was not supplied",
            )
        )
    elif corpus_summary is None or not lock_by_case:
        attestation_failures.append(
            _failure(
                "replay_binding_unavailable",
                "live attestation validation is blocked by corpus/replay lock",
            )
        )
    else:
        try:
            attestation_digest = validate_replay_attestation(
                replay_attestation,
                replay_lock,
                execution_corpus,
                corpus_summary=corpus_summary,
            )
            attestation_age_seconds = validate_replay_attestation_freshness(
                replay_attestation,
                reference_at=observed_at,
                max_age_seconds=maximum_age,
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            attestation_failures.append(
                _failure(
                    "live_attestation_invalid",
                    str(exc),
                    secrets=secret_values,
                )
            )
    _check(
        checks,
        "replay.fresh_live_attestation",
        passed=not attestation_failures,
        failures=attestation_failures,
        blocked=(
            replay_attestation is not None
            and (corpus_summary is None or not lock_by_case)
        ),
    )

    runtime_observations: dict[str, Any] = {}
    runtime_failures: list[dict[str, str]] = []
    for role, field in (
        ("analysis", "analysis_container"),
        ("rag", "rag_container"),
        ("finalizer", "finalizer_container"),
    ):
        name = str(analysis.get(field) or "")
        if not name:
            runtime_observations[role] = None
            runtime_failures.append(
                _failure(
                    "runtime_container_missing",
                    f"analysis.{field} is not configured",
                )
            )
            continue
        try:
            identity = runtime_probe(name)
        except (OSError, RuntimeError, ValueError) as exc:
            identity = None
            runtime_failures.append(
                _failure(
                    "runtime_container_probe_failed",
                    f"{role} runtime cannot be inspected: {exc}",
                    secrets=secret_values,
                )
            )
        if not isinstance(identity, Mapping):
            runtime_observations[role] = None
            if not any(
                failure["code"] == "runtime_container_probe_failed"
                and role in failure["detail"]
                for failure in runtime_failures
            ):
                runtime_failures.append(
                    _failure(
                        "runtime_container_unavailable",
                        f"{role} runtime container is not inspectable",
                    )
                )
            continue
        observation = {
            "containerId": identity.get("containerId"),
            "imageId": identity.get("imageId"),
            "imageReference": identity.get("imageReference"),
            "status": identity.get("status"),
            "health": identity.get("health"),
        }
        runtime_observations[role] = observation
        if (
            not observation["containerId"]
            or not observation["imageId"]
            or not observation["imageReference"]
            or observation["status"] != "running"
            or observation["health"] not in {None, "healthy"}
        ):
            runtime_failures.append(
                _failure(
                    "runtime_container_not_ready",
                    f"{role} runtime has incomplete immutable identity or is "
                    "not running/healthy",
                )
            )
    _check(
        checks,
        "runtime.container_provenance",
        passed=not runtime_failures,
        failures=runtime_failures,
    )

    git_repository_failures: list[dict[str, str]] = []
    if not repository.is_dir() or not (repository / ".git").exists():
        git_repository_failures.append(
            _failure(
                "git_repository_invalid",
                "repository path is not a local Git clone/worktree",
            )
        )
    else:
        try:
            _require_git_success(
                _run_git(repository, ["rev-parse", "--is-inside-work-tree"]),
                "cannot inspect local Git repository",
            )
        except (OSError, RuntimeError, ValueError) as exc:
            git_repository_failures.append(
                _failure(
                    "git_repository_unreadable",
                    str(exc),
                    secrets=secret_values,
                )
            )
    _check(
        checks,
        "git.repository",
        passed=not git_repository_failures,
        failures=git_repository_failures,
    )

    source_tree_by_case: dict[str, str] = {}
    snapshot_records: list[dict[str, Any]] = []
    snapshot_failures: list[dict[str, str]] = []
    if corpus_summary is None or not lock_by_case or git_repository_failures:
        snapshot_failures.append(
            _failure(
                "snapshot_probe_blocked",
                "B/H snapshot validation requires strict corpus, replay lock, "
                "and local Git repository",
            )
        )
    else:
        for case in execution_corpus["cases"]:
            case_id = str(case["caseId"])
            try:
                observation = snapshot_probe(repository, case)
                expected_snapshot = case["snapshot"]
                if (
                    observation.get("basePresent") is not True
                    or observation.get("headPresent") is not True
                    or observation.get("baseAncestorHead") is not True
                    or observation.get("diffSha256")
                    != expected_snapshot["diffSha256"]
                    or observation.get("changedPaths")
                    != expected_snapshot["changedPaths"]
                    or not isinstance(
                        observation.get("baseSourceTreeSha256"),
                        str,
                    )
                    or not SHA256_HEX.fullmatch(
                        str(observation.get("baseSourceTreeSha256"))
                    )
                ):
                    raise ValueError(
                        "B/H availability, ancestry, diff, paths, or source-tree "
                        "identity drifted"
                    )
                source_tree_by_case[case_id] = str(
                    observation["baseSourceTreeSha256"]
                )
                snapshot_records.append(
                    {
                        "caseId": case_id,
                        "baseSha": expected_snapshot["baseSha"],
                        "headSha": expected_snapshot["headSha"],
                        "diffSha256": expected_snapshot["diffSha256"],
                        "changedPathsSha256": sha256_json(
                            expected_snapshot["changedPaths"]
                        ),
                        "baseSourceTreeSha256": source_tree_by_case[case_id],
                    }
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                snapshot_failures.append(
                    _failure(
                        "snapshot_drift",
                        f"{case_id}: {exc}",
                        secrets=secret_values,
                    )
                )
    _check(
        checks,
        "git.all_base_head_snapshots",
        passed=not snapshot_failures and len(snapshot_records) == 50,
        failures=snapshot_failures,
        blocked=(
            corpus_summary is None or not lock_by_case or bool(git_repository_failures)
        ),
    )

    receipt_records: list[dict[str, Any]] = []
    raw_receipts: list[dict[str, Any]] = []
    receipt_failures: list[dict[str, str]] = []
    service_secret_env = str(
        analysis.get("service_secret_env") or "CODECROW_SERVICE_SECRET"
    )
    service_secret = (
        str(environment.get(service_secret_env) or "")
        if ENV_NAME.fullmatch(service_secret_env)
        else ""
    )
    if corpus_summary is None or not lock_by_case:
        receipt_failures.append(
            _failure(
                "receipt_probe_blocked",
                "exact receipt validation requires strict corpus and replay lock",
            )
        )
    elif not service_secret:
        receipt_failures.append(
            _failure(
                "receipt_probe_secret_missing",
                "CodeCrow service-secret environment variable is unavailable",
            )
        )
    else:
        for case in execution_corpus["cases"]:
            case_id = str(case["caseId"])
            locked = lock_by_case[case_id]
            try:
                receipt = receipt_reader(
                    analysis,
                    branch=locked["baseRef"],
                    commit=locked["baseSha"],
                    service_secret=service_secret,
                )
                require_no_secret_values(
                    receipt,
                    secret_values,
                    context=f"exact index receipt for {case_id}",
                )
                failures = _validate_receipt(
                    receipt,
                    analysis=analysis,
                    locked=locked,
                    source_tree_sha256=source_tree_by_case.get(case_id),
                )
                if failures:
                    receipt_failures.extend(
                        {
                            **failure,
                            "detail": f"{case_id}: {failure['detail']}",
                        }
                        for failure in failures
                    )
                    continue
                raw_receipt = dict(receipt)
                raw_receipts.append(raw_receipt)
                receipt_records.append(
                    _receipt_projection(case_id, raw_receipt)
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                receipt_failures.append(
                    _failure(
                        "exact_receipt_invalid",
                        f"{case_id}: {exc}",
                        secrets=secret_values,
                    )
                )
    _check(
        checks,
        "index.all_exact_base_receipts",
        passed=not receipt_failures and len(receipt_records) == 50,
        failures=receipt_failures,
        blocked=(
            corpus_summary is None
            or not lock_by_case
            or not service_secret
        ),
    )

    consistency_failures = _cross_receipt_failures(raw_receipts)
    _check(
        checks,
        "index.receipt_control_consistency",
        passed=not consistency_failures,
        failures=consistency_failures,
        blocked=len(raw_receipts) != 50,
    )
    _check(
        checks,
        "publication.post_run_protocol_evidence",
        passed=False,
        blocked=True,
        required_for_run=False,
        required_for_paper=True,
        failures=[
            _failure(
                "analysis_and_post_run_protocol_evidence_not_yet_produced",
                "registration/seal completion, H/F runs, judgments, blinded "
                "human audits, metrics, and the reproducibility package can "
                "only be proven after this pre-run gate",
            )
        ],
    )

    public_configuration = public_config(config)
    configuration = {
        "configDigest": sha256_json(public_configuration),
        "analysisConfigDigest": sha256_json(public_config(analysis)),
        "judgeConfigDigest": sha256_json(public_config(judge)),
        "identity": {
            "forkRepository": fork_repository or None,
            "configuredForkRepository": configured_fork,
            "projectId": project_id,
            "workspace": workspace or None,
            "project": project or None,
        },
        "models": {
            "analysis": {
                "provider": analysis_provider or None,
                "requestedModel": analysis_model or None,
                "expectedResponseModel": analysis_expected or None,
            },
            "judge": {
                "providerProtocol": "openai-compatible",
                "requestedModel": judge_model or None,
                "expectedResponseModel": judge_expected or None,
            },
        },
        "environment": env_observations,
        "paperRuntimeControls": control_expectations,
    }
    artifact: dict[str, Any] = {
        "kind": PREFLIGHT_KIND,
        "generatedAt": observed_at,
        "operationMode": "strictly-read-only",
        "sideEffectPolicy": {
            "indexesCreated": False,
            "analysisJobsEnqueued": False,
            "modelCallsMade": False,
            "forkOrGitHubMutations": False,
            "productFindingsFinalized": False,
        },
        "corpus": (
            {
                "corpusId": corpus_summary["corpusId"],
                "corpusDigest": corpus_summary["corpusDigest"],
                "executionCorpusDigest": corpus_summary[
                    "executionCorpusDigest"
                ],
                "caseCount": corpus_summary["cases"],
            }
            if corpus_summary is not None
            else None
        ),
        "replay": {
            "forkRepository": fork_repository or None,
            "lockDigest": (
                replay_lock.get("lockDigest")
                if isinstance(replay_lock, Mapping)
                else None
            ),
            "planDigest": (
                replay_lock.get("planDigest")
                if isinstance(replay_lock, Mapping)
                else None
            ),
            "attestationDigest": attestation_digest,
            "attestationAgeSeconds": attestation_age_seconds,
        },
        "configuration": configuration,
        "runtime": runtime_observations,
        "snapshots": snapshot_records,
        "snapshotSetDigest": sha256_json(snapshot_records),
        "indexReceipts": receipt_records,
        "indexReceiptSetDigest": sha256_json(receipt_records),
        "checks": checks,
    }
    artifact["runReady"] = all(
        check["status"] == "passed"
        for check in checks
        if check["requiredForRun"] is True
    )
    # A pre-run observation cannot prove completed analysis/judgment evidence,
    # the human audit, metrics, or the final reproducibility package.
    artifact["paperReady"] = False
    try:
        require_no_secret_values(
            artifact,
            secret_values,
            context="operator preflight artifact",
        )
    except RuntimeError:
        # Do not risk echoing a credential in either a check or a persisted
        # artifact. Redact every configured secret literal before sealing.
        def redact(value: Any) -> Any:
            if isinstance(value, str):
                return redact_secret_text(value, secret_values)
            if isinstance(value, Mapping):
                return {str(key): redact(child) for key, child in value.items()}
            if isinstance(value, list):
                return [redact(child) for child in value]
            return value

        artifact = redact(artifact)
        artifact["checks"].append(
            {
                "id": "artifact.secret_free",
                "requiredForRun": True,
                "requiredForPaper": True,
                "status": "failed",
                "failures": [
                    {
                        "code": "secret_value_redacted",
                        "detail": (
                            "a configured credential appeared in a candidate "
                            "artifact field and was redacted"
                        ),
                    }
                ],
            }
        )
        artifact["runReady"] = False
        artifact["paperReady"] = False
    artifact["readinessDigest"] = sha256_json(artifact)
    return artifact


def operator_preflight(
    *,
    config_path: Path | None,
    execution_corpus_path: Path,
    replay_lock_path: Path,
    repository: Path,
    output: Path,
    replay_attestation_path: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Load inputs, run the read-only audit, and write only its JSON receipt."""

    artifact = build_operator_preflight(
        config=load_config(config_path),
        execution_corpus=read_json(execution_corpus_path),
        replay_lock=read_json(replay_lock_path),
        replay_attestation=(
            read_json(replay_attestation_path)
            if replay_attestation_path is not None
            else None
        ),
        repository=repository,
        environment=environment,
    )
    write_json(output, artifact)
    return artifact


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Magento benchmark readiness audit; it never indexes, "
            "enqueues analysis, calls a model, or mutates GitHub."
        )
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--execution-corpus", type=Path, required=True)
    parser.add_argument("--replay-lock", type=Path, required=True)
    parser.add_argument("--replay-attestation", type=Path)
    parser.add_argument("--repository-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    artifact = operator_preflight(
        config_path=arguments.config,
        execution_corpus_path=arguments.execution_corpus,
        replay_lock_path=arguments.replay_lock,
        replay_attestation_path=arguments.replay_attestation,
        repository=arguments.repository_path,
        output=arguments.output,
    )
    print(
        canonical_json(
            {
                "kind": artifact["kind"],
                "runReady": artifact["runReady"],
                "paperReady": artifact["paperReady"],
                "runBlockingChecks": [
                    check["id"]
                    for check in artifact["checks"]
                    if check["requiredForRun"] is True
                    and check["status"] != "passed"
                ],
                "publicationBlockers": [
                    check["id"]
                    for check in artifact["checks"]
                    if check["requiredForPaper"] is True
                    and check["status"] != "passed"
                ],
                "readinessDigest": artifact["readinessDigest"],
                "output": str(arguments.output),
            }
        )
    )
    return 0 if artifact["runReady"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
