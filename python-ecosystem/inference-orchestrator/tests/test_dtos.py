"""
Unit tests for model.dtos — all DTO models.
"""
import pytest
from model.dtos import (
    ReviewRequestDto,
    ReviewResponseDto,
    SummarizeRequestDto,
    SummarizeResponseDto,
    AskRequestDto,
    AskResponseDto,
)


# ── Shared fixtures ──────────────────────────────────────────────

def _minimal_review_request(**overrides):
    defaults = dict(
        projectId=1,
        projectVcsWorkspace="ws",
        projectVcsRepoSlug="repo",
        projectWorkspace="ws",
        projectNamespace="ns",
        aiProvider="OPENAI",
        aiModel="gpt-4",
        aiApiKey="sk-test",
    )
    defaults.update(overrides)
    return ReviewRequestDto(**defaults)


# ── ReviewRequestDto ─────────────────────────────────────────────

class TestReviewRequestDto:
    def test_pinned_review_inputs(self):
        request = _minimal_review_request(
            targetBranchName="main",
            currentCommitHash="source-head",
            targetHeadCommitHash="target-head",
            localRepoRevision="target-head",
            localReviewOverlayPath="/tmp/review-overlay",
        )
        assert request.targetBranchName == "main"
        assert request.currentCommitHash == "source-head"
        assert request.get_target_head_commit_hash() == "target-head"
        assert request.localReviewOverlayPath == "/tmp/review-overlay"

    def test_target_head_can_use_legacy_base_metadata(self):
        request = _minimal_review_request(baseCommitHash="base-head")
        assert request.get_target_head_commit_hash() == "base-head"


# ── ReviewResponseDto ────────────────────────────────────────────

class TestReviewResponseDto:

    def test_defaults(self):
        resp = ReviewResponseDto()
        assert resp.result is None
        assert resp.error is None
        assert resp.exception is None

    def test_with_error(self):
        resp = ReviewResponseDto(error="boom")
        assert resp.error == "boom"


# ── SummarizeRequestDto ──────────────────────────────────────────

class TestSummarizeRequestDto:

    def test_minimal(self):
        req = SummarizeRequestDto(
            projectId=1,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            projectWorkspace="ws",
            projectNamespace="ns",
            aiProvider="ANTHROPIC",
            aiModel="claude-3",
            aiApiKey="sk-test",
            pullRequestId=10,
        )
        assert req.supportsMermaid is True

# ── SummarizeResponseDto ─────────────────────────────────────────

class TestSummarizeResponseDto:

    def test_defaults(self):
        resp = SummarizeResponseDto()
        assert resp.summary is None
        assert resp.diagramType == "MERMAID"


# ── AskRequestDto / AskResponseDto ──────────────────────────────

class TestAskRequestDto:

    def test_minimal(self):
        req = AskRequestDto(
            projectId=1,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            projectWorkspace="ws",
            projectNamespace="ns",
            aiProvider="OPENAI",
            aiModel="gpt-4",
            aiApiKey="sk-test",
            question="Why?",
        )
        assert req.question == "Why?"
        assert req.issueReferences == []

    def test_structural_search_binding(self):
        req = AskRequestDto(
            projectId=1,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            projectWorkspace="ws",
            projectNamespace="ns",
            aiProvider="OPENAI",
            aiModel="gpt-4",
            aiApiKey="sk-test",
            question="Where is auth handled?",
            branch="main",
            repositoryRevision="a" * 40,
            repositoryGenerationManifestSha256="b" * 64,
            ragCollectionTarget="cc_ws_repo_main_generation",
        )

        assert req.branch == "main"
        assert req.repositoryRevision == "a" * 40
        assert req.ragGenerationManifestSha256 == "b" * 64

    def test_response_defaults(self):
        resp = AskResponseDto()
        assert resp.answer is None
        assert resp.error is None
