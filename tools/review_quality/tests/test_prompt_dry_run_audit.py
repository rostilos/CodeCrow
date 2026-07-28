from __future__ import annotations

from copy import deepcopy
import hashlib

import pytest

from tools.review_quality.prompt_dry_run_audit import (
    APPROVED_MAGENTO_REGRESSION_IDENTITY,
    audit_prompt_dry_run,
)


def _artifact() -> dict:
    stages = [
        "stage_0",
        "stage_1",
        "verification",
        "stage_2",
        "stage_3",
    ]
    prompts = [
        {
            "sequence": index,
            "stage": stage,
            "renderedPrompt": "x" * 100,
            "renderedPromptCharacterCount": 100,
            "characterCount": 100,
            "estimatedInputTokens": 25,
        }
        for index, stage in enumerate(stages, start=1)
    ]
    return {
        "dryRun": True,
        "providerCalls": 0,
        "providerCallsScope": "review-llm-only",
        "embeddingProviderCallsMeasured": False,
        "providerConstructionGuard": {
            "enabled": True,
            "boundary": "LLMFactory.create_llm",
        },
        "simulation": {
            "simulatedFindingsProduced": 1,
            "fullPipelineContext": True,
            "prIndexMutationEnabled": True,
        },
        "promptCount": len(prompts),
        "promptCountsByStage": {stage: 1 for stage in stages},
        "prompts": prompts,
        "qualitySignals": {
            "stage1": {
                "maxEstimatedInputTokens": 25,
                "ragEvidenceEntries": 2,
            },
        },
        "reviewIdentity": {
            "projectId": 900001,
            "pullRequestId": 42,
            "targetBranch": "synthetic-target",
            "sourceBranch": "feature/replay",
            "headRevision": "1" * 40,
            "baseRevision": "2" * 40,
            "changedFiles": ["src/a.py"],
            "deletedFiles": [],
            "rawDiffSha256": "a" * 64,
        },
        "pipeline": {
            "completed": True,
            "eventStates": [
                "pr_context_preflight_started",
                "pr_context_preflight_completed",
                "stage_0_started",
                "stage_1_started",
                "verification_started",
                "stage_2_started",
                "stage_3_started",
                "review_evidence_completed",
            ],
            "evidence": {
                "hunkCoverage": {
                    "ingested": 0,
                    "planned": 0,
                    "reviewed": 0,
                    "validated": 0,
                    "completed": 1,
                    "excluded": 0,
                },
                "reviewUnits": {"registered": 1, "completed": 1},
                "candidates": {
                    "generated": 1,
                    "published": 1,
                    "rejected": 0,
                    "rejectionCounts": {},
                    "records": [{
                        "candidateId": "sha256:" + "c" * 64,
                        "stage": "stage_1",
                        "generationPromptDigest": (
                            "sha256:"
                            + hashlib.sha256(
                                ("x" * 100).encode("utf-8")
                            ).hexdigest()
                        ),
                        "reviewUnitIds": ["sha256:unit"],
                        "promptHunkIds": ["sha256:hunk"],
                        "anchorHunkIds": ["sha256:hunk"],
                        "evidenceRefs": [],
                        "visibleEvidenceIds": ["RAG-visible"],
                        "visibleEvidenceFactDigests": {
                            "RAG-visible": [],
                        },
                        "terminalState": "published",
                        "rejection": None,
                    }],
                },
                "hunkReceipts": [{
                    "hunkId": "sha256:hunk",
                    "path": "src/app.py",
                    "promptCandidateIds": ["sha256:" + "c" * 64],
                    "anchoredCandidateIds": ["sha256:" + "c" * 64],
                    "publishedCandidateIds": ["sha256:" + "c" * 64],
                    "rejectedCandidateIds": [],
                    "outcome": "published",
                }],
                "retrieval": {
                    "deterministicStates": ["complete"],
                    "semanticFailures": 0,
                    "semanticDisabled": False,
                    "exactEvidenceIds": 1,
                },
            },
        },
        "pluginDiagnostics": {
            "count": 0,
            "exceptionCount": 0,
            "items": [],
        },
        "promptAssemblyDiagnostics": {
            "stage1": [{
                "stage": "stage_1",
                "batchPaths": ["src/a.py"],
                "fileCount": 1,
                "totalPromptChars": 100,
                "currentSourceChars": 10,
                "diffChars": 20,
                "metadataChars": 5,
                "ragChars": 10,
                "pluginChars": 5,
                "projectRulesChars": 0,
                "taskContextChars": 10,
                "previousIssuesChars": 0,
                "currentSourcePerFileBudget": 12_000,
            }],
        },
    }


def test_audit_accepts_complete_framework_neutral_artifact():
    report = audit_prompt_dry_run(_artifact())

    assert report["status"] == "passed"
    assert report["diagnostics"]["failedChecks"] == []
    assert len(report["diagnostics"]["promptDigest"]) == 64


@pytest.mark.parametrize(
    ("mutate", "failed_check"),
    [
        (
            lambda payload: payload.update(providerCalls=1),
            "zeroReviewProviderCalls",
        ),
        (
            lambda payload: payload["pipeline"]["evidence"][
                "hunkCoverage"
            ].update(reviewed=1, completed=0),
            "hunkCoverageCompleted",
        ),
        (
            lambda payload: payload["pipeline"]["evidence"][
                "reviewUnits"
            ].update(completed=0),
            "reviewUnitCoverageCompleted",
        ),
        (
            lambda payload: payload["pipeline"]["evidence"][
                "candidates"
            ].update(published=0),
            "candidateLedgerComplete",
        ),
        (
            lambda payload: payload["pipeline"]["evidence"].update(
                hunkReceipts=[]
            ),
            "hunkReceiptsComplete",
        ),
        (
            lambda payload: payload["pipeline"]["evidence"]["candidates"][
                "records"
            ][0].update(evidenceRefs=["RAG-not-visible"]),
            "candidateLedgerComplete",
        ),
        (
            lambda payload: payload["pipeline"]["evidence"]["candidates"][
                "records"
            ][0].update(
                generationPromptDigest="sha256:" + "f" * 64
            ),
            "candidateLedgerComplete",
        ),
        (
            lambda payload: payload["pipeline"]["evidence"][
                "retrieval"
            ].update(deterministicStates=["failed"]),
            "deterministicRetrievalComplete",
        ),
        (
            lambda payload: payload["pluginDiagnostics"].update(
                count=1,
                exceptionCount=1,
                items=[{
                    "scope": "review",
                    "pluginId": "language",
                    "code": "plugin-review-exception",
                    "message": "RuntimeError",
                }],
            ),
            "noPluginDiagnostics",
        ),
        (
            lambda payload: payload["pluginDiagnostics"].update(
                count=1,
                exceptionCount=0,
                items=[{
                    "scope": "validation",
                    "pluginId": "language",
                    "code": "plugin-validation-warning",
                    "message": "incomplete contribution",
                }],
            ),
            "noPluginDiagnostics",
        ),
        (
            lambda payload: payload["qualitySignals"]["stage1"].update(
                maxEstimatedInputTokens=60_001
            ),
            "stage1PromptBounded",
        ),
        (
            lambda payload: payload["promptAssemblyDiagnostics"].update(
                stage1=[]
            ),
            "stage1AssemblyDiagnosticsComplete",
        ),
        (
            lambda payload: payload["promptAssemblyDiagnostics"]["stage1"][
                0
            ].update(pluginChars=6_001),
            "pluginContextBounded",
        ),
        (
            lambda payload: payload["reviewIdentity"].update(
                headRevision="short"
            ),
            "immutableSnapshotIdentity",
        ),
    ],
)
def test_audit_fails_closed_on_missing_general_pipeline_evidence(
    mutate,
    failed_check,
):
    artifact = deepcopy(_artifact())
    mutate(artifact)

    report = audit_prompt_dry_run(artifact)

    assert report["status"] == "failed"
    assert failed_check in report["diagnostics"]["failedChecks"]


def test_audit_rejects_nonpositive_token_ceiling():
    with pytest.raises(ValueError, match="positive"):
        audit_prompt_dry_run(_artifact(), max_stage1_estimated_input_tokens=0)


def test_approved_magento_regression_requires_exact_hofmanflowers_identity():
    artifact = _artifact()
    artifact["reviewIdentity"].update(
        APPROVED_MAGENTO_REGRESSION_IDENTITY
    )

    report = audit_prompt_dry_run(
        artifact,
        expected_review_identity=APPROVED_MAGENTO_REGRESSION_IDENTITY,
    )

    assert report["status"] == "passed"
    assert report["checks"]["expectedReviewIdentity"] is True


def test_approved_magento_regression_rejects_other_live_project():
    artifact = _artifact()
    artifact["reviewIdentity"].update(
        APPROVED_MAGENTO_REGRESSION_IDENTITY
    )
    artifact["reviewIdentity"]["projectId"] = 352

    report = audit_prompt_dry_run(
        artifact,
        expected_review_identity=APPROVED_MAGENTO_REGRESSION_IDENTITY,
    )

    assert report["status"] == "failed"
    assert report["checks"]["expectedReviewIdentity"] is False
    assert "expectedReviewIdentity" in report["diagnostics"]["failedChecks"]
