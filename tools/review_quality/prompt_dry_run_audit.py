#!/usr/bin/env python3
"""Audit one source-bearing full-pipeline prompt dry-run artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from .prompt_gate_profile import stable_prompt_digest


_FULL_OBJECT_ID = re.compile(r"^[0-9a-fA-F]{40,64}$")
_REQUIRED_EVENT_STATES = {
    "pr_context_preflight_started",
    "pr_context_preflight_completed",
    "stage_0_started",
    "stage_1_started",
    "verification_started",
    "stage_2_started",
    "stage_3_started",
    "review_evidence_completed",
}

APPROVED_MAGENTO_REGRESSION_IDENTITY = {
    "projectId": 1802,
    "pullRequestId": 196,
    "targetBranch": "develop",
    "sourceBranch": "codecrow-test",
    "headRevision": "f2ddf3a2e42ebdeb65a6ef25f2d64b2365fbc818",
    "baseRevision": "1912eab1f5104a1c9244baa3f1bef68cf8addade",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, (list, tuple)) else ()


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def audit_prompt_dry_run(
    artifact: Mapping[str, Any],
    *,
    max_stage1_estimated_input_tokens: int = 60_000,
    expected_review_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return deterministic pass/fail checks for a queued dry-run artifact."""
    if max_stage1_estimated_input_tokens <= 0:
        raise ValueError("max Stage 1 token ceiling must be positive")

    prompts = [
        prompt
        for prompt in _sequence(artifact.get("prompts"))
        if isinstance(prompt, Mapping)
    ]
    actual_stage_counts = Counter(
        str(prompt.get("stage") or "") for prompt in prompts
    )
    declared_stage_counts = {
        str(key): value
        for key, value in _mapping(
            artifact.get("promptCountsByStage")
        ).items()
    }
    simulation = _mapping(artifact.get("simulation"))
    guard = _mapping(artifact.get("providerConstructionGuard"))
    quality = _mapping(_mapping(artifact.get("qualitySignals")).get("stage1"))
    pipeline = _mapping(artifact.get("pipeline"))
    plugin_diagnostics = _mapping(artifact.get("pluginDiagnostics"))
    stage1_assembly = [
        item
        for item in _sequence(
            _mapping(artifact.get("promptAssemblyDiagnostics")).get("stage1")
        )
        if isinstance(item, Mapping)
    ]
    evidence = _mapping(pipeline.get("evidence"))
    hunk_coverage = _mapping(evidence.get("hunkCoverage"))
    review_units = _mapping(evidence.get("reviewUnits"))
    candidates = _mapping(evidence.get("candidates"))
    retrieval = _mapping(evidence.get("retrieval"))
    identity = _mapping(artifact.get("reviewIdentity"))
    event_states = {
        str(state)
        for state in _sequence(pipeline.get("eventStates"))
        if str(state)
    }
    retrieval_states = [
        str(state)
        for state in _sequence(retrieval.get("deterministicStates"))
    ]

    simulated_findings = simulation.get("simulatedFindingsProduced")
    requires_verification = (
        isinstance(simulated_findings, int)
        and not isinstance(simulated_findings, bool)
        and simulated_findings > 0
    )
    is_pull_request = identity.get("pullRequestId") is not None
    immutable_snapshot = bool(identity.get("targetBranch")) and bool(
        _FULL_OBJECT_ID.fullmatch(str(identity.get("headRevision") or ""))
    )
    if is_pull_request:
        immutable_snapshot = (
            immutable_snapshot
            and bool(identity.get("sourceBranch"))
            and bool(
                _FULL_OBJECT_ID.fullmatch(
                    str(identity.get("baseRevision") or "")
                )
            )
        )

    transient_hunk_count = sum(
        int(hunk_coverage.get(state) or 0)
        for state in ("ingested", "planned", "reviewed", "validated")
    )
    registered_units = review_units.get("registered")
    completed_units = review_units.get("completed")
    generated_candidates = candidates.get("generated")
    published_candidates = candidates.get("published")
    rejected_candidates = candidates.get("rejected")
    candidate_records = [
        record
        for record in _sequence(candidates.get("records"))
        if isinstance(record, Mapping)
    ]
    candidate_ids = [
        str(record.get("candidateId") or "")
        for record in candidate_records
    ]
    candidate_by_id = {
        candidate_id: record
        for candidate_id, record in zip(candidate_ids, candidate_records)
    }
    computed_rejection_counts = Counter(
        (
            f"{_mapping(record.get('rejection')).get('gate')}:"
            f"{_mapping(record.get('rejection')).get('code')}"
        )
        for record in candidate_records
        if record.get("terminalState") == "rejected"
    )
    prompt_digests_by_stage: dict[str, set[str]] = {}
    for prompt in prompts:
        stage = str(prompt.get("stage") or "")
        rendered = prompt.get("renderedPrompt")
        if stage and isinstance(rendered, str):
            prompt_digests_by_stage.setdefault(stage, set()).add(
                "sha256:"
                + hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            )
    candidate_records_valid = all(
        re.fullmatch(r"sha256:[0-9a-f]{64}", candidate_id)
        and record.get("stage") in {"stage_1", "stage_2"}
        and bool(
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(record.get("generationPromptDigest") or ""),
            )
        )
        and record.get("generationPromptDigest")
        in prompt_digests_by_stage.get(str(record.get("stage") or ""), set())
        and record.get("terminalState") in {"published", "rejected"}
        and all(
            isinstance(values := record.get(field), list)
            and values == sorted(set(values))
            and all(isinstance(value, str) and value for value in values)
            for field in (
                "reviewUnitIds",
                "promptHunkIds",
                "anchorHunkIds",
                "evidenceRefs",
                "visibleEvidenceIds",
            )
        )
        and isinstance(
            record.get("visibleEvidenceFactDigests"),
            Mapping,
        )
        and set(record.get("visibleEvidenceFactDigests", {}))
        == set(record.get("visibleEvidenceIds", ()))
        and all(
            isinstance(digests, list)
            and digests == sorted(set(digests))
            and all(
                isinstance(digest, str)
                and re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                for digest in digests
            )
            for digests in record.get(
                "visibleEvidenceFactDigests",
                {},
            ).values()
        )
        and (
            (
                record.get("terminalState") == "published"
                and record.get("rejection") is None
                and bool(record.get("reviewUnitIds"))
                and bool(record.get("promptHunkIds"))
                and bool(record.get("anchorHunkIds"))
                and set(record.get("anchorHunkIds", ())).issubset(
                    record.get("promptHunkIds", ())
                )
                and set(record.get("evidenceRefs", ())).issubset(
                    record.get("visibleEvidenceIds", ())
                )
            )
            or (
                record.get("terminalState") == "rejected"
                and isinstance(record.get("rejection"), Mapping)
                and bool(_mapping(record.get("rejection")).get("gate"))
                and bool(_mapping(record.get("rejection")).get("code"))
            )
        )
        for candidate_id, record in zip(candidate_ids, candidate_records)
    )
    hunk_receipts = [
        receipt
        for receipt in _sequence(evidence.get("hunkReceipts"))
        if isinstance(receipt, Mapping)
    ]
    hunk_receipt_ids = [
        str(receipt.get("hunkId") or "")
        for receipt in hunk_receipts
    ]
    hunk_receipts_valid = True
    for receipt in hunk_receipts:
        hunk_id = str(receipt.get("hunkId") or "")
        expected_prompt = sorted(
            candidate_id
            for candidate_id, record in candidate_by_id.items()
            if hunk_id in record.get("promptHunkIds", ())
        )
        expected_anchored = sorted(
            candidate_id
            for candidate_id, record in candidate_by_id.items()
            if hunk_id in record.get("anchorHunkIds", ())
        )
        expected_published = sorted(
            candidate_id
            for candidate_id in expected_anchored
            if candidate_by_id[candidate_id].get("terminalState")
            == "published"
        )
        expected_rejected = sorted(
            candidate_id
            for candidate_id in expected_anchored
            if candidate_by_id[candidate_id].get("terminalState")
            == "rejected"
        )
        expected_outcome = (
            "published"
            if expected_published
            else "rejected"
            if expected_rejected
            else "no_anchored_candidate"
        )
        hunk_receipts_valid = hunk_receipts_valid and (
            bool(hunk_id)
            and bool(receipt.get("path"))
            and receipt.get("promptCandidateIds") == expected_prompt
            and receipt.get("anchoredCandidateIds") == expected_anchored
            and receipt.get("publishedCandidateIds") == expected_published
            and receipt.get("rejectedCandidateIds") == expected_rejected
            and receipt.get("outcome") == expected_outcome
        )
    stage1_max_tokens = quality.get("maxEstimatedInputTokens")
    prompt_stage1_character_counts = Counter(
        int(prompt.get("renderedPromptCharacterCount") or 0)
        for prompt in prompts
        if prompt.get("stage") == "stage_1"
    )
    assembly_stage1_character_counts = Counter(
        int(item.get("totalPromptChars") or 0)
        for item in stage1_assembly
    )
    composition_fields = (
        "currentSourceChars",
        "diffChars",
        "metadataChars",
        "ragChars",
        "pluginChars",
        "projectRulesChars",
        "taskContextChars",
        "previousIssuesChars",
    )

    checks = {
        "dryRun": artifact.get("dryRun") is True,
        "zeroReviewProviderCalls": artifact.get("providerCalls") == 0,
        "reviewProviderScopeDeclared": (
            artifact.get("providerCallsScope") == "review-llm-only"
        ),
        "providerConstructionGuard": (
            guard.get("enabled") is True
            and guard.get("boundary") == "LLMFactory.create_llm"
        ),
        "fullPipelineContext": simulation.get("fullPipelineContext") is True,
        "prOverlayIndexingExercised": (
            simulation.get("prIndexMutationEnabled") is True
        ),
        "promptCountIntegrity": (
            artifact.get("promptCount") == len(prompts)
            and declared_stage_counts == dict(sorted(actual_stage_counts.items()))
        ),
        "renderedPromptLengthIntegrity": all(
            prompt.get("renderedPromptCharacterCount")
            == len(str(prompt.get("renderedPrompt") or ""))
            for prompt in prompts
        ),
        "stage0Captured": actual_stage_counts["stage_0"] == 1,
        "stage1Captured": actual_stage_counts["stage_1"] >= 1,
        "stage2Captured": actual_stage_counts["stage_2"] == 1,
        "stage3Captured": actual_stage_counts["stage_3"] == 1,
        "verificationCaptured": (
            not requires_verification
            or actual_stage_counts["verification"] == 1
        ),
        "pipelineCompleted": pipeline.get("completed") is True,
        "requiredPipelineEvents": _REQUIRED_EVENT_STATES.issubset(event_states),
        "immutableSnapshotIdentity": immutable_snapshot,
        "changedManifestPresent": bool(_sequence(identity.get("changedFiles"))),
        "diffFingerprintPresent": bool(
            re.fullmatch(
                r"[0-9a-f]{64}",
                str(identity.get("rawDiffSha256") or ""),
            )
        ),
        "hunkCoverageCompleted": (
            transient_hunk_count == 0
            and _positive_int(hunk_coverage.get("completed"))
        ),
        "reviewUnitCoverageCompleted": (
            _positive_int(registered_units)
            and registered_units == completed_units
        ),
        "candidateLedgerComplete": (
            isinstance(generated_candidates, int)
            and not isinstance(generated_candidates, bool)
            and generated_candidates > 0
            and isinstance(published_candidates, int)
            and not isinstance(published_candidates, bool)
            and published_candidates >= 0
            and isinstance(rejected_candidates, int)
            and not isinstance(rejected_candidates, bool)
            and rejected_candidates >= 0
            and published_candidates + rejected_candidates
            == generated_candidates
            and len(candidate_records) == generated_candidates
            and len(candidate_ids) == len(set(candidate_ids))
            and candidate_ids == sorted(candidate_ids)
            and candidate_records_valid
            and _mapping(candidates.get("rejectionCounts"))
            == dict(sorted(computed_rejection_counts.items()))
        ),
        "hunkReceiptsComplete": (
            isinstance(hunk_coverage.get("completed"), int)
            and not isinstance(hunk_coverage.get("completed"), bool)
            and len(hunk_receipts) == hunk_coverage.get("completed")
            and len(hunk_receipt_ids) == len(set(hunk_receipt_ids))
            and hunk_receipt_ids == sorted(hunk_receipt_ids)
            and hunk_receipts_valid
        ),
        "deterministicRetrievalComplete": (
            bool(retrieval_states)
            and set(retrieval_states) == {"complete"}
        ),
        "semanticRetrievalHealthy": (
            retrieval.get("semanticFailures") == 0
            and retrieval.get("semanticDisabled") is False
        ),
        "noPluginDiagnostics": (
            plugin_diagnostics.get("count") == 0
            and plugin_diagnostics.get("exceptionCount") == 0
        ),
        "stage1AssemblyDiagnosticsComplete": (
            len(stage1_assembly) == actual_stage_counts["stage_1"]
            and assembly_stage1_character_counts
            == prompt_stage1_character_counts
        ),
        "stage1CompositionValuesValid": all(
            _positive_int(item.get("fileCount"))
            and _positive_int(item.get("totalPromptChars"))
            and all(
                isinstance(item.get(field), int)
                and not isinstance(item.get(field), bool)
                and item[field] >= 0
                for field in composition_fields
            )
            for item in stage1_assembly
        ),
        "pluginContextBounded": (
            bool(stage1_assembly)
            and all(
                int(item.get("pluginChars") or 0) <= 6_000
                for item in stage1_assembly
            )
        ),
        "ragEvidenceDelivered": _positive_int(quality.get("ragEvidenceEntries")),
        "stage1PromptBounded": (
            isinstance(stage1_max_tokens, int)
            and not isinstance(stage1_max_tokens, bool)
            and 0 < stage1_max_tokens <= max_stage1_estimated_input_tokens
        ),
        "noUnclassifiedProviderInputs": actual_stage_counts["unclassified"] == 0,
    }
    if expected_review_identity is not None:
        checks["expectedReviewIdentity"] = all(
            identity.get(field) == expected
            for field, expected in expected_review_identity.items()
        )

    return {
        "status": "passed" if all(checks.values()) else "failed",
        "scope": (
            "framework-neutral queued prompt/context delivery; "
            "not candidate precision or recall"
        ),
        "checks": checks,
        "diagnostics": {
            "failedChecks": sorted(
                name for name, passed in checks.items() if not passed
            ),
            "promptDigest": (
                stable_prompt_digest(prompts) if prompts else None
            ),
            "promptCountsByStage": dict(sorted(actual_stage_counts.items())),
            "eventStates": sorted(event_states),
            "hunkCoverage": dict(hunk_coverage),
            "reviewUnits": dict(review_units),
            "candidates": dict(candidates),
            "hunkReceipts": list(hunk_receipts),
            "retrieval": dict(retrieval),
            "pluginDiagnostics": dict(plugin_diagnostics),
            "promptAssemblyDiagnostics": stage1_assembly,
            "stage1": dict(quality),
            "maxStage1EstimatedInputTokens": (
                max_stage1_estimated_input_tokens
            ),
            "embeddingProviderCallsMeasured": artifact.get(
                "embeddingProviderCallsMeasured"
            ),
            "expectedReviewIdentity": (
                dict(expected_review_identity)
                if expected_review_identity is not None
                else None
            ),
            "actualExpectedReviewIdentityFields": (
                {
                    field: identity.get(field)
                    for field in expected_review_identity
                }
                if expected_review_identity is not None
                else None
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit a full-pipeline provider-free prompt artifact without "
            "printing its source-bearing prompts."
        )
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument(
        "--max-stage1-estimated-input-tokens",
        type=int,
        default=60_000,
    )
    parser.add_argument(
        "--approved-magento-regression",
        action="store_true",
        help=(
            "fail unless the artifact is the pinned hofmanflowers project "
            "1802 / PR 196 Magento regression capture"
        ),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.artifact.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("prompt artifact must be a JSON object")
    report = audit_prompt_dry_run(
        payload,
        max_stage1_estimated_input_tokens=(
            args.max_stage1_estimated_input_tokens
        ),
        expected_review_identity=(
            APPROVED_MAGENTO_REGRESSION_IDENTITY
            if args.approved_magento_regression
            else None
        ),
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
