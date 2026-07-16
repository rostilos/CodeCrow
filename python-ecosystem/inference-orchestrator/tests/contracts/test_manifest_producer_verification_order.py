"""CR-01 contracts for manifest-bound producer/verification ordering."""

from __future__ import annotations

import json
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import service.review.orchestrator.orchestrator as orchestrator_module
from model.dtos import ReviewRequestDto
from model.multi_stage import (
    CrossFileAnalysisResult,
    CrossFileIssue,
    FileGroup,
    ReviewFile,
    ReviewPlan,
)
from model.output_schemas import CodeReviewIssue
from service.review.orchestrator.orchestrator import MultiStageReviewOrchestrator
from service.review.telemetry import StageOutcome


RAW_DIFF = "diff --git a/src/a.py b/src/a.py\n+unsafe()\n"


def _canonical_digest(document: dict[str, object]) -> str:
    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _manifest() -> dict[str, object]:
    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "executionId": "execution-cr01-shared-verification",
        "projectId": 7,
        "repositoryId": "github:codecrow/review-fixture",
        "pullRequestId": 42,
        "baseSha": "a" * 40,
        "headSha": "b" * 40,
        "mergeBaseSha": "c" * 40,
        "diffArtifactId": "diff-artifact-cr01-shared-verification",
        "diffDigest": sha256(RAW_DIFF.encode("utf-8")).hexdigest(),
        "diffByteLength": len(RAW_DIFF.encode("utf-8")),
        "diffArtifactKind": "raw-diff",
        "diffArtifactProducer": "java-vcs-acquisition",
        "diffArtifactProducerVersion": "p1-01-v1",
        "artifactSchemaVersion": "review-artifact-v1",
        "policyVersion": "candidate-review-v2",
        "creationFence": "creation:cr01-shared-verification",
        "createdAt": "2026-07-16T12:00:00Z",
    }
    manifest["inputArtifacts"] = [
        {
            "executionId": manifest["executionId"],
            "artifactId": manifest["diffArtifactId"],
            "contentKey": "pull-request.diff",
            "snapshotSha": manifest["headSha"],
            "contentDigest": manifest["diffDigest"],
            "byteLength": manifest["diffByteLength"],
            "kind": "raw-diff",
            "artifactSchemaVersion": manifest["artifactSchemaVersion"],
            "producer": manifest["diffArtifactProducer"],
            "producerVersion": manifest["diffArtifactProducerVersion"],
        }
    ]
    manifest["artifactManifestDigest"] = _canonical_digest(manifest)
    return manifest


def _request() -> ReviewRequestDto:
    return ReviewRequestDto(
        projectId=7,
        projectVcsWorkspace="codecrow",
        projectVcsRepoSlug="review-fixture",
        projectWorkspace="Codecrow",
        projectNamespace="codecrow-garden",
        aiProvider="scripted",
        aiModel="fixture-v1",
        aiApiKey="not-a-live-key",
        pullRequestId=42,
        analysisType="PR_REVIEW",
        vcsProvider="github",
        changedFiles=[],
        rawDiff=RAW_DIFF,
        executionManifest=_manifest(),
        indexVersion="rag-disabled",
        useMcpTools=False,
    )


def _stage_one_issue() -> CodeReviewIssue:
    return CodeReviewIssue(
        id="stage-one",
        severity="HIGH",
        category="BUG_RISK",
        file="src/a.py",
        line=1,
        title="Stage 1 candidate",
        reason="Local producer hypothesis",
        suggestedFixDescription="Fix the local defect",
        codeSnippet="unsafe()",
    )


def _stage_two_result() -> CrossFileAnalysisResult:
    return CrossFileAnalysisResult(
        pr_risk_level="HIGH",
        cross_file_issues=[
            CrossFileIssue(
                id="stage-two",
                severity="HIGH",
                category="BUG_RISK",
                title="Stage 2 candidate",
                primary_file="src/a.py",
                affected_files=["src/a.py"],
                description="Cross-file producer hypothesis",
                evidence="The changed call crosses a boundary.",
                business_impact="Unsafe state can escape.",
                suggestion="Validate before crossing the boundary.",
                line=1,
                codeSnippet="unsafe()",
            )
        ],
        data_flow_concerns=[],
        pr_recommendation="Review both producer candidates.",
        confidence="HIGH",
    )


def _plan() -> ReviewPlan:
    return ReviewPlan(
        analysis_summary="bounded CR-01 plan",
        file_groups=[
            FileGroup(
                group_id="group-1",
                priority="HIGH",
                rationale="changed code",
                files=[
                    ReviewFile(
                        path="src/a.py",
                        focus_areas=["correctness"],
                        risk_level="HIGH",
                    )
                ],
            )
        ],
        cross_file_concerns=["boundary safety"],
    )


async def _run_manifest_review(
    *,
    verification_enabled: bool,
    verifier: AsyncMock,
    deterministic_gate: MagicMock,
    stage_two: AsyncMock,
    aggregation: AsyncMock,
):
    telemetry = MagicMock()
    orchestrator = MultiStageReviewOrchestrator(
        llm=MagicMock(name="offline_llm"),
        mcp_client=None,
        rag_client=None,
        telemetry=telemetry,
    )
    profile = SimpleNamespace(
        fast_check_enabled=False,
        describe=lambda: "offline CR-01 contract",
    )

    with patch.object(
        orchestrator,
        "_index_pr_files",
        new_callable=AsyncMock,
    ), patch.object(
        orchestrator_module,
        "build_review_inference_profile",
        return_value=profile,
    ), patch.object(
        orchestrator_module,
        "with_stage_output_cap",
        side_effect=lambda llm, *_args: llm,
    ), patch.object(
        orchestrator_module,
        "execute_stage_0_planning",
        new_callable=AsyncMock,
        return_value=_plan(),
    ), patch.object(
        orchestrator_module,
        "execute_stage_1_file_reviews",
        new_callable=AsyncMock,
        return_value=[_stage_one_issue()],
    ), patch.object(
        orchestrator_module,
        "run_verification_agent",
        verifier,
    ), patch.object(
        orchestrator_module,
        "should_run_stage_2",
        return_value=(True, "required producer"),
    ), patch.object(
        orchestrator_module,
        "execute_stage_2_cross_file",
        stage_two,
    ), patch.object(
        orchestrator_module,
        "run_deterministic_evidence_gate",
        deterministic_gate,
    ), patch.object(
        orchestrator_module,
        "execute_stage_3_aggregation",
        aggregation,
    ), patch.object(
        orchestrator_module,
        "VERIFICATION_ENABLED",
        verification_enabled,
    ):
        result = await orchestrator.orchestrate_review(_request())

    return result, telemetry


@pytest.mark.asyncio(loop_scope="function")
async def test_manifest_bound_stage_one_and_stage_two_join_before_shared_verification() -> None:
    sequence: list[str] = []
    verifier_inputs: list[CodeReviewIssue] = []

    async def produce_stage_two(*_args, **_kwargs):
        sequence.append("stage-two-producer")
        return _stage_two_result()

    async def verify_union(_llm, issues, *_args):
        sequence.append("shared-verifier")
        verifier_inputs.extend(issues)
        return issues[1:]

    verifier = AsyncMock(side_effect=verify_union)
    deterministic_gate = MagicMock(side_effect=lambda issues, *_args: issues)
    stage_two = AsyncMock(side_effect=produce_stage_two)
    aggregation = AsyncMock(
        return_value={"report": "verified aggregate", "dismissed_issue_ids": []}
    )

    result, telemetry = await _run_manifest_review(
        verification_enabled=True,
        verifier=verifier,
        deterministic_gate=deterministic_gate,
        stage_two=stage_two,
        aggregation=aggregation,
    )

    assert sequence == ["stage-two-producer", "shared-verifier"]
    assert [issue.id for issue in verifier_inputs] == ["stage-one", "stage-two"]
    assert [issue["id"] for issue in result["issues"]] == ["stage-two"]
    verification_stage = next(
        call.kwargs
        for call in telemetry.record_stage.call_args_list
        if call.kwargs["producer"] == "verification_agent"
    )
    assert verification_stage["outcome"] is StageOutcome.COMPLETE
    assert verification_stage["candidates"].input == 2
    assert verification_stage["candidates"].retained == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_manifest_bound_verifier_error_after_producer_union_cannot_reach_aggregation() -> None:
    stage_two = AsyncMock(return_value=_stage_two_result())
    verifier = AsyncMock(side_effect=RuntimeError("shared verifier unavailable"))
    deterministic_gate = MagicMock(side_effect=lambda issues, *_args: issues)
    aggregation = AsyncMock(
        return_value={"report": "must not publish", "dismissed_issue_ids": []}
    )

    with pytest.raises(RuntimeError, match="shared verifier unavailable"):
        await _run_manifest_review(
            verification_enabled=True,
            verifier=verifier,
            deterministic_gate=deterministic_gate,
            stage_two=stage_two,
            aggregation=aggregation,
        )

    stage_two.assert_awaited_once()
    verifier.assert_awaited_once()
    deterministic_gate.assert_not_called()
    aggregation.assert_not_awaited()


@pytest.mark.asyncio(loop_scope="function")
async def test_manifest_bound_optional_verifier_skip_is_recorded_after_producer_union() -> None:
    sequence: list[str] = []
    accounted_ids: list[str] = []

    async def produce_stage_two(*_args, **_kwargs):
        sequence.append("stage-two-producer")
        return _stage_two_result()

    def account_union(issues, *_args):
        sequence.append("deterministic-verifier")
        accounted_ids.extend(issue.id for issue in issues)
        return issues

    verifier = AsyncMock()
    deterministic_gate = MagicMock(side_effect=account_union)
    stage_two = AsyncMock(side_effect=produce_stage_two)
    aggregation = AsyncMock(
        return_value={"report": "deterministic aggregate", "dismissed_issue_ids": []}
    )

    result, telemetry = await _run_manifest_review(
        verification_enabled=False,
        verifier=verifier,
        deterministic_gate=deterministic_gate,
        stage_two=stage_two,
        aggregation=aggregation,
    )

    assert sequence == ["stage-two-producer", "deterministic-verifier"]
    assert accounted_ids == ["stage-one", "stage-two"]
    assert [issue["id"] for issue in result["issues"]] == [
        "stage-one",
        "stage-two",
    ]
    verifier.assert_not_awaited()
    producers = [call.kwargs["producer"] for call in telemetry.record_stage.call_args_list]
    assert producers.index("stage_2") < producers.index("verification_agent")
    assert producers.index("verification_agent") < producers.index(
        "deterministic_evidence_gate"
    )
    skipped = next(
        call.kwargs
        for call in telemetry.record_stage.call_args_list
        if call.kwargs["producer"] == "verification_agent"
    )
    assert skipped["outcome"] is StageOutcome.SKIPPED
    assert skipped["candidates"].input == 2
    assert skipped["candidates"].retained == 2
