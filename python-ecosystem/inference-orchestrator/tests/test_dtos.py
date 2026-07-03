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

    def test_get_rag_branch_with_pr(self):
        req = _minimal_review_request(
            pullRequestId=42,
            sourceBranchName="feat/x",
            targetBranchName="main",
        )
        assert req.get_rag_branch() == "feat/x"

    def test_get_rag_branch_without_pr(self):
        req = _minimal_review_request(targetBranchName="develop")
        assert req.get_rag_branch() == "develop"

    def test_get_rag_branch_pr_no_source(self):
        req = _minimal_review_request(pullRequestId=1, targetBranchName="main")
        assert req.get_rag_branch() == "main"

    def test_get_rag_base_branch_with_pr(self):
        req = _minimal_review_request(pullRequestId=1, targetBranchName="main")
        assert req.get_rag_base_branch() == "main"

    def test_get_rag_base_branch_without_pr(self):
        req = _minimal_review_request(targetBranchName="main")
        assert req.get_rag_base_branch() is None

    def test_defaults(self):
        req = _minimal_review_request()
        assert req.changedFiles == []
        assert req.deletedFiles == []
        assert req.diffSnippets == []
        assert req.previousCodeAnalysisIssues == []
        assert req.analysisMode == "FULL"
        assert req.useMcpTools is False

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

    def test_get_rag_branch_with_pr(self):
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
            sourceBranch="feat/y",
            targetBranch="main",
        )
        assert req.get_rag_branch() == "feat/y"
        assert req.get_rag_base_branch() == "main"


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

    def test_response_defaults(self):
        resp = AskResponseDto()
        assert resp.answer is None
        assert resp.error is None
