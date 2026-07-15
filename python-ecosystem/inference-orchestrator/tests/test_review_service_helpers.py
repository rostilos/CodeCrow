"""
Tests for ReviewService helper methods.

Covers: _build_jvm_props, _build_pr_metadata, _emit_event, _create_llm,
        _create_mcp_client
"""
import pytest
from time import monotonic_ns
from unittest.mock import MagicMock, patch

from model.dtos import ReviewRequestDto
from service.review.review_service import ReviewService
from service.review.telemetry import (
    CoverageCounts,
    StageOutcome,
    bind_telemetry,
    reset_telemetry,
)


@pytest.fixture
def service():
    with patch.dict("os.environ", {
        "MCP_SERVER_JAR": "/tmp/test.jar",
        "REVIEW_TIMEOUT_SECONDS": "60",
        "MAX_CONCURRENT_REVIEWS": "2",
    }):
        with patch("service.review.review_service.RagClient"):
            with patch("service.review.review_service.get_rag_cache"):
                svc = ReviewService()
    return svc


def _telemetry_request(**overrides):
    values = {
        "projectId": 1,
        "projectVcsWorkspace": "vcs-workspace",
        "projectVcsRepoSlug": "repo",
        "projectWorkspace": "workspace",
        "projectNamespace": "namespace",
        "aiProvider": "scripted",
        "aiModel": "fixture-v1",
        "aiApiKey": "secret-credential",
        "executionId": "execution-review-1",
        "baseRevision": "a" * 40,
        "headRevision": "b" * 40,
        "indexVersion": "rag-commit-" + "c" * 40,
        "rawDiff": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n",
    }
    values.update(overrides)
    return ReviewRequestDto(**values)


# ── _emit_event ──────────────────────────────────────────────────

class TestReviewServiceEmitEvent:
    def test_calls_callback(self, service):
        cb = MagicMock()
        ReviewService._emit_event(cb, {"type": "test"})
        cb.assert_called_once_with({"type": "test"})

    def test_none_callback(self, service):
        ReviewService._emit_event(None, {"type": "test"})

    def test_exception_swallowed(self, service):
        cb = MagicMock(side_effect=RuntimeError("boom"))
        ReviewService._emit_event(cb, {"type": "test"})


class TestReviewTelemetry:
    def test_requires_both_exact_revisions(self, service):
        recorder, _ = service._create_telemetry_recorder(
            _telemetry_request(baseRevision=None)
        )
        assert recorder is None

    def test_failed_named_boundary_emits_failed_terminal_not_zero_success(self, service):
        request = _telemetry_request()
        recorder, sink = service._create_telemetry_recorder(request)
        assert recorder is not None
        recorder.record_stage(
            name="generation",
            producer="stage_1",
            outcome=StageOutcome.FAILED,
            duration_ms=5,
            coverage=CoverageCounts(inventory=1, represented=0, unrepresented=1),
            reason="provider_timeout",
        )
        events = []

        result = service._attach_terminal_telemetry(
            request=request,
            result={"result": {"status": "error", "issues": []}},
            recorder=recorder,
            sink=sink,
            started_ns=monotonic_ns(),
            event_callback=events.append,
        )

        telemetry = result["result"]["telemetry"]
        assert telemetry["trace"]["outcome"] == "failed"
        assert telemetry["trace"]["reason"] == "analysis_failed"
        assert telemetry["trace"]["stages"][-1]["name"] == "generation"
        assert telemetry["finalizationState"] == "pending_java"
        assert telemetry["metric"] is None
        assert "secret-credential" not in repr(telemetry)
        assert events[-1] == {
            "type": "telemetry",
            "state": "provisional",
            "outcome": "failed",
            "reason": "analysis_failed",
        }

    @pytest.mark.asyncio(loop_scope="function")
    async def test_rag_fault_is_a_named_failed_retrieval_without_payload(self, service):
        request = _telemetry_request(
            targetBranchName="main",
            changedFiles=["private/customer.py"],
        )
        recorder, _ = service._create_telemetry_recorder(request)
        assert recorder is not None
        service.rag_cache.get.side_effect = RuntimeError("credential=secret-rag")
        token = bind_telemetry(recorder)
        try:
            result = await service._fetch_rag_context(request, None)
        finally:
            reset_telemetry(token)

        assert result is None
        stage = recorder.stages[-1]
        assert stage.name == "retrieval"
        assert stage.producer == "global_rag"
        assert stage.outcome is StageOutcome.FAILED
        assert stage.reason == "rag_retrieval_failed"
        assert "secret-rag" not in repr(stage)
        assert "private/customer.py" not in repr(stage)


# ── _build_jvm_props ─────────────────────────────────────────────

class TestBuildJvmProps:
    def test_returns_dict(self, service):
        request = MagicMock(
            projectId=1,
            pullRequestId=42,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            oAuthClient="oc",
            oAuthSecret="os",
            accessToken=None,
            maxAllowedTokens=100000,
            vcsProvider="bitbucket",
        )
        result = service._build_jvm_props(request, None)
        assert isinstance(result, dict)

    def test_with_override_tokens(self, service):
        request = MagicMock(
            projectId=1,
            pullRequestId=42,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            oAuthClient="oc",
            oAuthSecret="os",
            accessToken=None,
            maxAllowedTokens=None,
            vcsProvider="github",
        )
        result = service._build_jvm_props(request, 50000)
        assert isinstance(result, dict)


# ── _build_pr_metadata ───────────────────────────────────────────

class TestBuildPrMetadata:
    def test_basic_metadata(self, service):
        request = MagicMock()
        request.get_rag_branch.return_value = "feature/x"
        request.get_rag_base_branch.return_value = "main"
        request.commitHash = "abc123"
        request.pullRequestId = 42
        request.projectVcsRepoSlug = "repo"
        request.projectVcsWorkspace = "ws"
        request.previousCodeAnalysisIssues = None
        result = service._build_pr_metadata(request)
        assert result["branch"] == "feature/x"
        assert result["baseBranch"] == "main"
        assert result["commitHash"] == "abc123"
        assert result["pullRequestId"] == 42
        assert result["previousCodeAnalysisIssues"] == []

    def test_with_previous_issues(self, service):
        issue = MagicMock()
        issue.dict.return_value = {"file": "a.py", "title": "Bug"}
        request = MagicMock()
        request.get_rag_branch.return_value = "main"
        request.get_rag_base_branch.return_value = "develop"
        request.commitHash = "def456"
        request.pullRequestId = 10
        request.projectVcsRepoSlug = "repo"
        request.projectVcsWorkspace = "ws"
        request.previousCodeAnalysisIssues = [issue]
        result = service._build_pr_metadata(request)
        assert len(result["previousCodeAnalysisIssues"]) == 1
        assert result["previousCodeAnalysisIssues"][0]["file"] == "a.py"


# ── _create_mcp_client ───────────────────────────────────────────

class TestReviewServiceCreateMcpClient:
    def test_success(self, service):
        with patch("service.review.review_service.MCPClient") as mock_cls:
            mock_cls.from_dict.return_value = MagicMock()
            client = service._create_mcp_client({"servers": {}})
            mock_cls.from_dict.assert_called_once()

    def test_failure(self, service):
        with patch("service.review.review_service.MCPClient") as mock_cls:
            mock_cls.from_dict.side_effect = Exception("fail")
            with pytest.raises(Exception, match="Failed to construct"):
                service._create_mcp_client({})


# ── _create_llm ──────────────────────────────────────────────────

class TestReviewServiceCreateLlm:
    def test_success(self, service):
        request = MagicMock(
            aiModel="gpt-4",
            aiProvider="openai",
            aiApiKey="key",
            projectId=1,
        )
        with patch("service.review.review_service.LLMFactory") as mock_factory:
            mock_factory.create_llm.return_value = MagicMock()
            llm = service._create_llm(request)
            mock_factory.create_llm.assert_called_once()

    def test_failure(self, service):
        request = MagicMock(
            aiModel="gpt-4",
            aiProvider="openai",
            aiApiKey="key",
            projectId=1,
        )
        with patch("service.review.review_service.LLMFactory") as mock_factory:
            mock_factory.create_llm.side_effect = Exception("bad")
            with pytest.raises(Exception, match="Failed to create LLM"):
                service._create_llm(request)


# ── Constants ────────────────────────────────────────────────────

class TestReviewServiceConstants:
    def test_max_fix_retries(self, service):
        assert ReviewService.MAX_FIX_RETRIES == 2

    def test_max_concurrent_reviews_is_int(self, service):
        assert isinstance(ReviewService.MAX_CONCURRENT_REVIEWS, int)
        assert ReviewService.MAX_CONCURRENT_REVIEWS > 0

    def test_review_timeout_is_int(self, service):
        assert isinstance(ReviewService.REVIEW_TIMEOUT_SECONDS, int)
        assert ReviewService.REVIEW_TIMEOUT_SECONDS > 0
