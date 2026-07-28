"""Prepare and compare disconnected fallback/candidate queue requests.

This command exercises the production Java acquisition/request builder twice
against one temporary local Git snapshot: once with an empty plugin bundle and
once with the assembled bundle. It performs no Redis, RAG, embedding, or review
provider call. Its output is a prerequisite for a later explicitly authorized
paid paired capture, not quality evidence.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .isolated_deployed_replay import (
    REPOSITORY_ROOT,
    _assert_no_connected_identity,
    build_java_review_request,
    build_synthetic_repository,
)
from model.dtos import ReviewRequestDto


_SOURCE_FIELDS = (
    "projectId",
    "projectVcsWorkspace",
    "projectVcsRepoSlug",
    "projectWorkspace",
    "projectNamespace",
    "analysisType",
    "branch",
    "sourceBranchName",
    "pullRequestId",
    "currentCommitHash",
    "baseCommitHash",
    "changedFiles",
    "deletedFiles",
    "rawDiff",
    "enrichmentData",
)


def audit_paired_requests(
    baseline: ReviewRequestDto,
    candidate: ReviewRequestDto,
) -> dict[str, Any]:
    baseline_payload = baseline.model_dump(mode="json", by_alias=True)
    candidate_payload = candidate.model_dump(mode="json", by_alias=True)
    _assert_no_connected_identity(baseline_payload)
    _assert_no_connected_identity(candidate_payload)

    drift = [
        field
        for field in _SOURCE_FIELDS
        if baseline_payload.get(field) != candidate_payload.get(field)
    ]
    if drift:
        raise RuntimeError(
            "fallback/candidate source identity drift: " + ", ".join(drift)
        )
    if baseline.promptDryRun is not True or candidate.promptDryRun is not True:
        raise RuntimeError("paired preflight must remain provider-free")
    for payload in (baseline_payload, candidate_payload):
        if payload.get("aiApiKey") != "dry-run-provider-disabled":
            raise RuntimeError("paired preflight contains a review credential")
        for field in ("accessToken", "oAuthClient", "oAuthSecret"):
            if payload.get(field) is not None:
                raise RuntimeError(
                    f"paired preflight contains VCS credential field {field}"
                )

    baseline_capabilities = baseline.projectCapabilities
    candidate_capabilities = candidate.projectCapabilities
    if baseline_capabilities is None or candidate_capabilities is None:
        raise RuntimeError("paired preflight requires explicit capability identity")
    if baseline_capabilities.repositoryPlugins:
        raise RuntimeError("fallback preflight selected repository plugins")
    if not candidate_capabilities.repositoryPlugins:
        raise RuntimeError("candidate preflight selected no repository plugins")
    if (
        baseline_capabilities.fingerprint
        == candidate_capabilities.fingerprint
    ):
        raise RuntimeError("fallback/candidate selection fingerprints are identical")
    if (
        baseline_capabilities.descriptorFingerprint
        == candidate_capabilities.descriptorFingerprint
    ):
        raise RuntimeError("fallback/candidate descriptor fingerprints are identical")

    return {
        "status": "passed",
        "kind": "review-quality-isolated-paired-preflight",
        "scope": (
            "disconnected Java acquisition/request identity only; no Redis, RAG, "
            "embedding, or review-provider call and no precision/recall evidence"
        ),
        "sourceIdentity": {
            field: baseline_payload.get(field)
            for field in _SOURCE_FIELDS
            if field not in {"rawDiff", "enrichmentData"}
        },
        "modes": {
            "fallback": {
                "repositoryPlugins": [],
                "selectionFingerprint": baseline_capabilities.fingerprint,
                "descriptorFingerprint": (
                    baseline_capabilities.descriptorFingerprint
                ),
            },
            "plugin-context": {
                "repositoryPlugins": list(
                    candidate_capabilities.repositoryPlugins
                ),
                "selectionFingerprint": candidate_capabilities.fingerprint,
                "descriptorFingerprint": (
                    candidate_capabilities.descriptorFingerprint
                ),
            },
        },
        "reviewProviderCalls": 0,
        "embeddingProviderCalls": 0,
        "connectedProjectCreated": False,
    }


def run_preflight(args: argparse.Namespace) -> Mapping[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="codecrow-paired-preflight-",
    ) as temporary:
        root = Path(temporary)
        empty_plugins = root / "empty-plugins"
        empty_plugins.mkdir()
        repository = build_synthetic_repository(root)
        namespace = "neutral-paired-preflight"
        baseline, baseline_producer = build_java_review_request(
            repository,
            project_namespace=namespace,
            temporary_root=root,
            java_ecosystem=args.java_ecosystem,
            plugin_directory=empty_plugins,
            expected_repository_plugins=(),
        )
        candidate, candidate_producer = build_java_review_request(
            repository,
            project_namespace=namespace,
            temporary_root=root,
            java_ecosystem=args.java_ecosystem,
            plugin_directory=args.java_plugin_directory,
        )
        result = audit_paired_requests(baseline, candidate)
        result["javaProducers"] = {
            "fallback": baseline_producer,
            "plugin-context": candidate_producer,
        }
        return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify disconnected production Java fallback/candidate request "
            "identity without invoking Redis, RAG, embeddings, or a review model."
        )
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
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not args.java_ecosystem.is_dir():
        raise FileNotFoundError(args.java_ecosystem)
    if not args.java_plugin_directory.is_dir():
        raise FileNotFoundError(args.java_plugin_directory)
    report = run_preflight(args)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
