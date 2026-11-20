"""
Unit tests for model.dtos — all DTO models.
"""
import pytest
from model.dtos import (
    IssueDTO,
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


# ── IssueDTO ─────────────────────────────────────────────────────

class TestIssueDTO:

    def test_all_optional(self):
        dto = IssueDTO()
        assert dto.id is None
        assert dto.type is None
        assert dto.file is None
        assert dto.line is None

    def test_from_dict(self):
        dto = IssueDTO(
            id="42",
            type="security",
            category="SECURITY",
            severity="HIGH",
            reason="SQL injection",
            file="src/dao.py",
            line=10,
            status="open",
        )
        assert dto.id == "42"
        assert dto.severity == "HIGH"
        assert dto.line == 10

    def test_resolution_fields(self):
        dto = IssueDTO(
            prVersion=2,
            resolvedDescription="Fixed",
            resolvedByCommit="abc123",
            resolvedInPrVersion=3,
        )
        assert dto.prVersion == 2
        assert dto.resolvedByCommit == "abc123"


# ── ReviewRequestDto ─────────────────────────────────────────────

class TestReviewRequestDto:

    def test_minimal(self):
        req = _minimal_review_request()
        assert req.projectId == 1
        assert req.aiProvider == "OPENAI"

    def test_branch_alias(self):
        """branch is an alias for targetBranchName."""
        req = _minimal_review_request(branch="main")
        assert req.targetBranchName == "main"

    def test_get_rag_branch_with_pr_uses_target_as_repository_truth(self):
        req = _minimal_review_request(
            pullRequestId=42,
            sourceBranchName="feat/x",
            targetBranchName="main",
        )
        assert req.get_rag_branch() == "main"

    def test_get_rag_branch_without_pr(self):
        req = _minimal_review_request(targetBranchName="develop")
        assert req.get_rag_branch() == "develop"

    def test_get_rag_branch_pr_no_source(self):
        req = _minimal_review_request(pullRequestId=1, targetBranchName="main")
        assert req.get_rag_branch() == "main"

    def test_get_rag_branch_pr_without_target_does_not_fall_back_to_source(self):
        req = _minimal_review_request(
            pullRequestId=1,
            sourceBranchName="rejected-source",
        )
        assert req.get_rag_branch() is None

    def test_get_rag_base_branch_with_pr(self):
        req = _minimal_review_request(pullRequestId=1, targetBranchName="main")
        assert req.get_rag_base_branch() == "main"

    def test_get_rag_base_branch_without_pr(self):
        req = _minimal_review_request(targetBranchName="main")
        assert req.get_rag_base_branch() is None

    def test_target_head_commit_is_distinct_from_merge_base(self):
        req = _minimal_review_request(
            targetHeadCommitHash="target-head",
            baseCommitHash="merge-base",
        )
        assert req.get_target_head_commit_hash() == "target-head"
        assert req.baseCommitHash == "merge-base"

    def test_target_head_commit_falls_back_to_legacy_base_field(self):
        req = _minimal_review_request(baseCommitHash="legacy-target-head")
        assert req.get_target_head_commit_hash() == "legacy-target-head"

    def test_defaults(self):
        req = _minimal_review_request()
        assert req.changedFiles == []
        assert req.deletedFiles == []
        assert req.previousCodeAnalysisIssues == []
        assert req.analysisMode == "FULL"
        assert req.useMcpTools is True
        assert req.mcpLocalOnly is False
        assert req.ragEnabled is True

    def test_project_can_disable_rag_for_one_review(self):
        req = _minimal_review_request(ragEnabled=False)
        assert req.ragEnabled is False

    def test_project_can_disable_mcp_tools_for_one_review(self):
        req = _minimal_review_request(useMcpTools=False)
        assert req.useMcpTools is False

    def test_request_can_require_provider_isolated_mcp(self):
        req = _minimal_review_request(mcpLocalOnly=True)
        assert req.mcpLocalOnly is True

    def test_local_repository_snapshot_metadata(self):
        req = _minimal_review_request(
            localRepoPath="/tmp/review-snapshot",
            localRepoTargetBranch="main",
            localRepoRevision="abc123",
            localRagRepoPath="/tmp/structural-snapshot",
            localReviewOverlayPath="/tmp/review-overlay",
        )
        assert req.localRepoPath == "/tmp/review-snapshot"
        assert req.localRepoTargetBranch == "main"
        assert req.localRepoRevision == "abc123"
        assert req.localRagRepoPath == "/tmp/structural-snapshot"
        assert req.localReviewOverlayPath == "/tmp/review-overlay"

    def test_enrichment_data_none(self):
        req = _minimal_review_request()
        assert req.enrichmentData is None

    def test_task_context_aliases(self):
        req = _minimal_review_request(
            task_context={"task_key": "PROJ-123", "task_summary": "Ship flow"}
        )
        assert req.taskContext["task_key"] == "PROJ-123"

        req2 = _minimal_review_request(
            taskContext={"taskKey": "PROJ-124", "taskSummary": "Fix flow"}
        )
        assert req2.taskContext["taskKey"] == "PROJ-124"

    def test_task_history_context_aliases(self):
        req = _minimal_review_request(task_history_context="PR #12 covered AC1")
        assert req.taskHistoryContext == "PR #12 covered AC1"

        req2 = _minimal_review_request(taskHistoryContext="PR #13 covered AC2")
        assert req2.taskHistoryContext == "PR #13 covered AC2"


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
