"""VS-33 contracts for retiring lossy manifest-bound correctness routes."""

from __future__ import annotations

import json
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import service.review.orchestrator.orchestrator as orchestrator_module
import service.review.orchestrator.stage_1_file_review as stage1_module
import service.review.orchestrator.verification_agent as verification_module
from model.dtos import ReviewRequestDto
from model.multi_stage import FileGroup, ReviewFile, ReviewPlan
from model.output_schemas import CodeReviewIssue
from service.review.orchestrator.orchestrator import MultiStageReviewOrchestrator
from service.review.orchestrator.stage_1_file_review import (
    Stage1BatchFailure,
    execute_stage_1_file_reviews,
)
from service.review.orchestrator.verification_agent import run_verification_agent
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
        "executionId": "execution-vs33-retirement",
        "projectId": 7,
        "repositoryId": "github:codecrow/review-fixture",
        "pullRequestId": 42,
        "baseSha": "a" * 40,
        "headSha": "b" * 40,
        "mergeBaseSha": "c" * 40,
        "diffArtifactId": "diff-artifact-vs33-retirement",
        "diffDigest": sha256(RAW_DIFF.encode("utf-8")).hexdigest(),
        "diffByteLength": len(RAW_DIFF.encode("utf-8")),
        "diffArtifactKind": "raw-diff",
        "diffArtifactProducer": "java-vcs-acquisition",
        "diffArtifactProducerVersion": "p1-01-v1",
        "artifactSchemaVersion": "review-artifact-v1",
        "policyVersion": "candidate-review-v2",
        "creationFence": "creation:vs33-retirement",
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


def _request(*, manifest_bound: bool) -> ReviewRequestDto:
    values: dict[str, object] = {
        "projectId": 7,
        "projectVcsWorkspace": "codecrow",
        "projectVcsRepoSlug": "review-fixture",
        "projectWorkspace": "Codecrow",
        "projectNamespace": "codecrow-garden",
        "aiProvider": "scripted",
        "aiModel": "fixture-v1",
        "aiApiKey": "not-a-live-key",
        "pullRequestId": 42,
        "analysisType": "PR_REVIEW",
        "vcsProvider": "github",
        "changedFiles": [] if manifest_bound else ["src/a.py"],
        "rawDiff": RAW_DIFF,
        "useMcpTools": False,
    }
    if manifest_bound:
        values["executionManifest"] = _manifest()
        values["indexVersion"] = "rag-disabled"
    else:
        values.update(
            {
                "sourceBranchName": "feature/legacy",
                "targetBranchName": "main",
            }
        )
    return ReviewRequestDto(**values)


def _issue(issue_id: str) -> CodeReviewIssue:
    return CodeReviewIssue(
        id=issue_id,
        severity="HIGH",
        category="BUG_RISK",
        file="src/a.py",
        line=1,
        title="Same structural location",
        reason=f"Distinct causal finding {issue_id}",
        suggestedFixDescription="Fix the causal defect",
        codeSnippet="unsafe()",
    )


def _plan() -> ReviewPlan:
    return ReviewPlan(
        analysis_summary="bounded test plan",
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
        cross_file_concerns=[],
    )


async def _run_review(
    request: ReviewRequestDto,
    issues: list[CodeReviewIssue],
):
    telemetry = MagicMock()
    cross_batch = MagicMock(side_effect=lambda candidates: candidates[:1])
    final_deterministic = MagicMock(side_effect=lambda candidates: candidates[:1])
    final_llm = AsyncMock(side_effect=lambda candidates: candidates[:1])
    orchestrator = MultiStageReviewOrchestrator(
        llm=MagicMock(name="offline_llm"),
        mcp_client=None,
        rag_client=None,
        telemetry=telemetry,
    )
    profile = SimpleNamespace(
        fast_check_enabled=True,
        describe=lambda: "offline retirement contract",
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
        return_value=issues,
    ), patch.object(
        orchestrator_module,
        "deduplicate_cross_batch_issues",
        cross_batch,
    ), patch.object(
        orchestrator_module,
        "run_deterministic_evidence_gate",
        side_effect=lambda candidates, *_args: candidates,
    ), patch.object(
        orchestrator_module,
        "should_run_stage_2",
        return_value=(False, "bounded contract"),
    ), patch.object(
        orchestrator_module,
        "should_use_fast_dedup",
        return_value=True,
    ), patch.object(
        orchestrator_module,
        "deduplicate_final_issues",
        final_deterministic,
    ), patch.object(
        orchestrator_module,
        "deduplicate_final_issues_llm",
        final_llm,
    ), patch.object(
        orchestrator_module,
        "execute_stage_3_aggregation",
        new_callable=AsyncMock,
        return_value={"report": "offline report", "dismissed_issue_ids": []},
    ), patch.object(orchestrator_module, "VERIFICATION_ENABLED", False):
        result = await orchestrator.orchestrate_review(request)

    return result, telemetry, cross_batch, final_deterministic, final_llm


@pytest.mark.asyncio(loop_scope="function")
async def test_manifest_bound_review_skips_lossy_dedup_and_preserves_candidate_order() -> None:
    first = _issue("candidate-first")
    second = _issue("candidate-second")

    result, telemetry, cross_batch, final_deterministic, final_llm = await _run_review(
        _request(manifest_bound=True),
        [first, second],
    )

    assert [issue["id"] for issue in result["issues"]] == [
        "candidate-first",
        "candidate-second",
    ]
    cross_batch.assert_not_called()
    final_deterministic.assert_not_called()
    final_llm.assert_not_awaited()
    stages = {
        call.kwargs["name"]: call.kwargs
        for call in telemetry.record_stage.call_args_list
        if call.kwargs["name"] in {"pre_dedup", "post_dedup"}
    }
    assert stages["pre_dedup"]["outcome"] is StageOutcome.SKIPPED
    assert stages["pre_dedup"]["reason"] == "retired_lossy_dedup"
    assert stages["pre_dedup"]["candidates"].input == 2
    assert stages["pre_dedup"]["candidates"].retained == 2
    assert stages["post_dedup"]["outcome"] is StageOutcome.SKIPPED
    assert stages["post_dedup"]["reason"] == "retired_lossy_dedup"
    assert stages["post_dedup"]["candidates"].input == 2
    assert stages["post_dedup"]["candidates"].retained == 2


@pytest.mark.asyncio(loop_scope="function")
async def test_legacy_review_retains_existing_cross_batch_and_final_dedup_routes() -> None:
    result, telemetry, cross_batch, final_deterministic, final_llm = await _run_review(
        _request(manifest_bound=False),
        [_issue("legacy-first"), _issue("legacy-second")],
    )

    assert [issue["id"] for issue in result["issues"]] == ["legacy-first"]
    cross_batch.assert_called_once()
    final_deterministic.assert_called_once()
    final_llm.assert_not_awaited()
    stages = {
        call.kwargs["name"]: call.kwargs
        for call in telemetry.record_stage.call_args_list
        if call.kwargs["name"] in {"pre_dedup", "post_dedup"}
    }
    assert stages["pre_dedup"]["outcome"] is StageOutcome.COMPLETE
    assert stages["post_dedup"]["outcome"] is StageOutcome.COMPLETE


@pytest.mark.asyncio(loop_scope="function")
async def test_manifest_bound_verifier_exception_fails_closed() -> None:
    request = _request(manifest_bound=True)
    request.enrichmentData = SimpleNamespace(
        fileContents=[
            SimpleNamespace(path="src/a.py", content="unsafe()", skipped=False)
        ]
    )

    with patch.object(
        verification_module,
        "_run_verification_tool_loop",
        new=AsyncMock(side_effect=RuntimeError("verifier unavailable")),
    ):
        with pytest.raises(RuntimeError, match="verifier unavailable"):
            await run_verification_agent(
                MagicMock(name="offline_llm"),
                [_issue("manifest-verifier")],
                request,
            )


@pytest.mark.asyncio(loop_scope="function")
async def test_legacy_verifier_exception_keeps_all_candidates() -> None:
    request = _request(manifest_bound=False)
    request.enrichmentData = SimpleNamespace(
        fileContents=[
            SimpleNamespace(path="src/a.py", content="unsafe()", skipped=False)
        ]
    )
    issues = [_issue("legacy-verifier")]

    with patch.object(
        verification_module,
        "_run_verification_tool_loop",
        new=AsyncMock(side_effect=RuntimeError("verifier unavailable")),
    ):
        assert await run_verification_agent(
            MagicMock(name="offline_llm"),
            issues,
            request,
        ) == issues


@pytest.mark.asyncio(loop_scope="function")
async def test_manifest_bound_invalid_stage_one_output_raises_without_coverage_tracker() -> None:
    request = _request(manifest_bound=True)
    plan = _plan()
    batch = [{"file": plan.file_groups[0].files[0], "priority": "HIGH"}]

    async def invalid_batch(*_args, **kwargs):
        assert kwargs["fail_closed"] is True
        raise Stage1BatchFailure(
            "invalid manifest-bound output",
            reason_code="stage1_response_invalid",
        )

    with patch.object(
        stage1_module,
        "create_smart_batches_wrapper",
        new=AsyncMock(return_value=[batch]),
    ), patch.object(
        stage1_module,
        "_review_batch_with_timing",
        side_effect=invalid_batch,
    ):
        with pytest.raises(Stage1BatchFailure) as raised:
            await execute_stage_1_file_reviews(
                MagicMock(name="offline_llm"),
                request,
                plan,
                rag_client=None,
                coverage_tracker=None,
            )

    assert raised.value.reason_code == "stage1_response_invalid"
