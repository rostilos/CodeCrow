"""Regression coverage for request-bound structural base revisions."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from model.dtos import ReviewRequestDto
from service.review.orchestrator.stage_1_rag_retrieval import (
    fetch_stage1_relation_briefing,
    has_exact_base_binding,
    has_exact_proposed_tree_binding,
)
from service.review.review_service import ReviewService
from service.review.snapshot_identity import (
    resolve_exact_structural_base_revision,
    validate_review_snapshot_identity,
)


def _request(**updates) -> ReviewRequestDto:
    values = {
        "projectId": 1,
        "projectVcsWorkspace": "provider-tenant",
        "projectVcsRepoSlug": "provider-repository",
        "projectWorkspace": "tenant",
        "projectNamespace": "repository",
        "aiProvider": "OPENAI",
        "aiModel": "gpt-4",
        "aiApiKey": "key",
        "targetBranchName": "main",
        "sourceBranchName": "feature/revision-binding",
        "pullRequestId": 7,
        "commitHash": "source-head",
        "currentCommitHash": "source-head",
        "targetHeadCommitHash": "target-b",
        "baseCommitHash": "merge-base",
        "changedFiles": ["src/a.py"],
        "localRepoPath": "/tmp/target-snapshot",
        "localRepoTargetBranch": "main",
        "localRepoRevision": "target-b",
        "localRagRepoPath": "/tmp/structural-snapshot",
        "localReviewOverlayPath": "/tmp/review-overlay",
        "ragCollectionTarget": "sealed-target-generation",
        "ragBaseGenerationManifestSha256": "a" * 64,
        "ragReviewGenerationStatus": "ready",
        "ragReviewCollectionTarget": "sealed-review-generation",
        "ragReviewGenerationManifestSha256": "b" * 64,
    }
    values.update(updates)
    return ReviewRequestDto(**values)


def test_pr_structural_binding_cross_binds_provider_and_local_target_revision():
    request = _request(
        targetHeadCommitHash="target-b",
        localRepoRevision="target-a",
    )

    identity = validate_review_snapshot_identity(request)

    assert identity.target_head_revision == "target-b"
    assert resolve_exact_structural_base_revision(request) is None
    assert has_exact_proposed_tree_binding(request) is False
    assert has_exact_base_binding(request) is False


def test_pr_structural_binding_does_not_treat_merge_base_as_target_head():
    request = _request(
        targetHeadCommitHash=None,
        baseCommitHash="merge-base",
        localRepoRevision="merge-base",
    )

    assert resolve_exact_structural_base_revision(request) is None
    assert has_exact_proposed_tree_binding(request) is False


def test_nonzero_negative_pr_id_still_requires_target_head_cross_binding():
    request = _request(
        pullRequestId=-1,
        targetHeadCommitHash=None,
        baseCommitHash="merge-base",
        localRepoRevision="merge-base",
    )

    assert request.pullRequestId
    assert resolve_exact_structural_base_revision(request) is None
    assert has_exact_proposed_tree_binding(request) is False
    assert has_exact_base_binding(request) is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("projectWorkspace", ""),
        ("projectNamespace", ""),
        ("localRepoTargetBranch", "release"),
    ],
)
def test_pr_structural_binding_requires_exact_tenant_repo_and_target(
    field,
    value,
):
    request = _request(**{field: value})

    assert has_exact_proposed_tree_binding(request) is False


def test_manual_review_keeps_local_revision_compatibility_without_pr_target_head():
    request = _request(
        pullRequestId=None,
        targetHeadCommitHash=None,
        baseCommitHash=None,
        localRepoTargetBranch=None,
        localRepoRevision="manual-snapshot",
    )

    assert resolve_exact_structural_base_revision(request) == "manual-snapshot"
    assert has_exact_proposed_tree_binding(request) is True
    assert has_exact_base_binding(request) is True


@pytest.mark.asyncio
async def test_relation_briefing_skips_mismatched_pr_binding_without_rag_call(
    caplog,
):
    request = _request(
        targetHeadCommitHash="target-b",
        localRepoRevision="target-a",
    )
    rag_client = MagicMock()
    rag_client.explore_review_context = AsyncMock()

    with caplog.at_level(logging.INFO):
        result = await fetch_stage1_relation_briefing(
            rag_client,
            request,
            ["src/a.py"],
        )

    assert result is None
    rag_client.explore_review_context.assert_not_awaited()
    assert "no exact tenant, repository, target snapshot" in caplog.text


def test_review_service_skips_only_graph_context_for_mismatched_pr_binding(
    caplog,
):
    request = _request(
        targetHeadCommitHash="target-b",
        localRepoRevision="target-a",
    )
    service = ReviewService.__new__(ReviewService)

    with caplog.at_level(logging.INFO):
        context = service._build_rag_mcp_context(request, MagicMock())

    assert context is None
    assert "repository tools remain available" in caplog.text


@pytest.mark.asyncio
async def test_relation_briefing_sends_cross_bound_target_revision():
    request = _request()
    rag_client = MagicMock()
    rag_client.explore_review_context = AsyncMock(return_value={"status": "ready"})

    result = await fetch_stage1_relation_briefing(
        rag_client,
        request,
        ["src/a.py"],
    )

    assert result == {"status": "ready"}
    assert (
        rag_client.explore_review_context.await_args.kwargs["base_revision"]
        == "target-b"
    )
