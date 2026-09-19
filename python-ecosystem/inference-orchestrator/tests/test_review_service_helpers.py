"""
Tests for ReviewService helper methods.

Covers: _build_jvm_props, _build_pr_metadata, _emit_event, _create_llm,
        _create_mcp_client
"""
import asyncio
import json
import logging
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from model.dtos import ReviewRequestDto
from service.review.review_service import ReviewService
from utils.mcp_config import MCPConfigBuilder


@pytest.fixture
def service():
    with patch.dict("os.environ", {
        "MCP_SERVER_JAR": "/tmp/test.jar",
        "REVIEW_TIMEOUT_SECONDS": "60",
        "MAX_CONCURRENT_REVIEWS": "2",
    }):
        with patch("service.review.review_service.RagClient"):
            svc = ReviewService()
    return svc


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
            vcsBaseUrl=None,
            localRepoPath="/tmp/review-snapshot",
            localRepoTargetBranch="main",
            localRepoRevision="abc123",
            localReviewOverlayPath="/tmp/review-overlay",
        )
        result = service._build_jvm_props(request)
        assert isinstance(result, dict)
        assert result["max.allowed.tokens"] == "100000"
        assert result["local.repo.path"] == "/tmp/review-snapshot"
        assert result["local.repo.targetBranch"] == "main"
        assert result["local.repo.revision"] == "abc123"
        assert result["local.review.overlay.path"] == "/tmp/review-overlay"

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
            vcsBaseUrl=None,
            localRepoPath=None,
            localRepoTargetBranch=None,
            localRepoRevision=None,
            localReviewOverlayPath=None,
        )
        result = service._build_jvm_props(request)
        assert isinstance(result, dict)

    def test_review_credentials_do_not_enter_mcp_process_args(self, service):
        request = MagicMock(
            projectId=1,
            pullRequestId=42,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            oAuthClient=None,
            oAuthSecret=None,
            accessToken="review-flow-token-sentinel",
            maxAllowedTokens=None,
            vcsProvider="github",
            vcsBaseUrl=None,
            localRepoPath=None,
            localRepoTargetBranch=None,
            localRepoRevision=None,
            localReviewOverlayPath=None,
        )

        config = MCPConfigBuilder.build_config(
            "/server.jar",
            service._build_jvm_props(request),
        )["mcpServers"]["codecrow-vcs-mcp"]

        assert "review-flow-token-sentinel" not in " ".join(config["args"])
        assert config["env"]["CODECROW_MCP_ACCESS_TOKEN"] == (
            "review-flow-token-sentinel"
        )

    def test_local_only_request_sets_java_fail_closed_property(self, service):
        request = MagicMock(
            projectId=1,
            pullRequestId=42,
            projectVcsWorkspace="ws",
            projectVcsRepoSlug="repo",
            oAuthClient=None,
            oAuthSecret=None,
            accessToken=None,
            maxAllowedTokens=None,
            vcsProvider="github",
            vcsBaseUrl=None,
            localRepoPath="/tmp/target",
            localRepoTargetBranch="main",
            localRepoRevision="target-head",
            localReviewOverlayPath="/tmp/overlay",
            mcpLocalOnly=True,
        )

        assert service._build_jvm_props(request)["local.mcp.only"] == "true"


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
            client.add_middleware.assert_called_once()

    def test_failure(self, service):
        with patch("service.review.review_service.MCPClient") as mock_cls:
            mock_cls.from_dict.side_effect = Exception("fail")
            with pytest.raises(Exception, match="Failed to construct"):
                service._create_mcp_client({})


class TestReviewServiceRequestRag:
    def test_project_disabled_rag_does_not_expose_client(self, service):
        service.rag_client = MagicMock(enabled=True)
        request = MagicMock(ragEnabled=False, projectId=1, pullRequestId=42)

        assert service._rag_client_for_request(request) is None

    def test_globally_disabled_rag_does_not_expose_client(self, service):
        service.rag_client = MagicMock(enabled=False)
        request = MagicMock(ragEnabled=True, projectId=1, pullRequestId=42)

        assert service._rag_client_for_request(request) is None

    def test_enabled_project_uses_enabled_shared_client(self, service):
        service.rag_client = MagicMock(enabled=True)
        request = MagicMock(ragEnabled=True, projectId=1, pullRequestId=42)

        assert service._rag_client_for_request(request) is service.rag_client

    def test_agent_tools_ignore_project_persistent_index_setting(self, service):
        service.rag_client = MagicMock(enabled=True)
        request = MagicMock(ragEnabled=False, projectId=1, pullRequestId=42)

        assert service._rag_client_for_request(request) is None
        assert service._rag_client_for_agent_tools() is service.rag_client

    def test_globally_disabled_rag_disables_agent_structural_service(self, service):
        service.rag_client = MagicMock(enabled=False)

        assert service._rag_client_for_agent_tools() is None

    def test_exact_proposed_tree_binding_enables_rag_mcp(self, service):
        request = MagicMock(
            projectWorkspace="tenant",
            projectNamespace="project",
            targetBranchName="main",
            currentCommitHash="source-head",
            commitHash=None,
            localRepoPath="/tmp/target-snapshot",
            localRagRepoPath="/tmp/structural-snapshot",
            localRepoRevision="target-head",
            localReviewOverlayPath="/tmp/review-overlay",
            ragBaseGenerationManifestSha256="manifest",
            ragCollectionTarget="collection",
            ragReviewCollectionTarget="review-collection",
            ragReviewGenerationManifestSha256="review-manifest",
            changedFiles=[],
            deletedFiles=[],
        )
        request.get_target_head_commit_hash.return_value = "target-head"

        assert service._build_rag_mcp_context(request, MagicMock()) == {
            "workspace": "tenant",
            "project": "project",
            "branch": "main",
            "revision": "target-head",
            "source_revision": "source-head",
            "target_repo_path": "/tmp/structural-snapshot",
            "review_overlay_path": "/tmp/review-overlay",
            "manifest": "manifest",
            "collection_target": "collection",
            "review_collection_target": "review-collection",
            "review_generation_manifest_sha256": "review-manifest",
        }

    def test_incomplete_proposed_tree_binding_skips_only_rag_mcp(
        self,
        service,
        caplog,
    ):
        request = MagicMock(
            projectWorkspace="tenant",
            projectNamespace="project",
            targetBranchName="main",
            currentCommitHash="source-head",
            commitHash=None,
            localRepoPath="/tmp/target-snapshot",
            localRepoRevision="target-head",
            localReviewOverlayPath="/tmp/review-overlay",
            ragBaseGenerationManifestSha256=None,
            ragCollectionTarget=None,
        )
        request.get_target_head_commit_hash.return_value = "target-head"

        with caplog.at_level(logging.INFO):
            assert service._build_rag_mcp_context(request, MagicMock()) is None
        assert (
            "repository tools remain available without indexed relationships"
            in caplog.text
        )
        assert "preassembled RAG" not in caplog.text

    def test_proposed_tree_binding_falls_back_to_vcs_snapshot(self, service):
        request = MagicMock(
            projectWorkspace="tenant",
            projectNamespace="project",
            targetBranchName="main",
            currentCommitHash="source-head",
            commitHash=None,
            localRepoPath="/tmp/target-snapshot",
            localRagRepoPath=None,
            localRepoRevision="target-head",
            localReviewOverlayPath="/tmp/review-overlay",
            ragBaseGenerationManifestSha256="manifest",
            ragCollectionTarget="collection",
            ragReviewCollectionTarget="review-collection",
            ragReviewGenerationManifestSha256="review-manifest",
            changedFiles=[],
            deletedFiles=[],
        )
        request.get_target_head_commit_hash.return_value = "target-head"

        context = service._build_rag_mcp_context(request, MagicMock())

        assert context["target_repo_path"] == "/tmp/target-snapshot"

    def test_pr_graph_binding_does_not_ship_changed_paths_for_source_redaction(
        self,
        service,
    ):
        request = MagicMock(
            projectWorkspace="tenant",
            projectNamespace="project",
            targetBranchName="main",
            currentCommitHash="source-head",
            commitHash=None,
            localRepoPath="/tmp/target-snapshot",
            localRepoRevision="target-head",
            ragBaseGenerationManifestSha256="manifest",
            ragCollectionTarget="collection",
            ragReviewCollectionTarget="review-collection",
            ragReviewGenerationManifestSha256="review-manifest",
            localReviewOverlayPath="/tmp/review-overlay",
            changedFiles=[f"src/file-{index}.py" for index in range(25)],
            deletedFiles=["src/deleted.py"],
        )
        request.get_target_head_commit_hash.return_value = "target-head"

        context = service._build_rag_mcp_context(request, MagicMock())

        assert context["source_revision"] == "source-head"
        assert context["target_repo_path"] == "/tmp/target-snapshot"
        assert context["review_overlay_path"] == "/tmp/review-overlay"
        assert "proposed_tree_paths_json" not in context


def _local_only_request(tmp_path, **overrides):
    target = tmp_path / "target"
    structural = tmp_path / "structural"
    overlay = tmp_path / "overlay"
    for path in (target, structural, overlay):
        path.mkdir(exist_ok=True)
    values = {
        "projectId": 1,
        "projectVcsWorkspace": "ws",
        "projectVcsRepoSlug": "repo",
        "projectWorkspace": "tenant",
        "projectNamespace": "project",
        "aiProvider": "OPENAI",
        "aiModel": "gpt-4",
        "aiApiKey": "key",
        "targetBranchName": "main",
        "sourceBranchName": "feature",
        "pullRequestId": 7,
        "commitHash": "source-head",
        "currentCommitHash": "source-head",
        "targetHeadCommitHash": "target-head",
        "baseCommitHash": "target-head",
        "changedFiles": ["src/a.py"],
        "useMcpTools": True,
        "mcpLocalOnly": True,
        "ragEnabled": True,
        "ragCollectionTarget": "sealed-collection",
        "ragBaseGenerationManifestSha256": "a" * 64,
        "localRepoPath": str(target),
        "localRepoTargetBranch": "main",
        "localRepoRevision": "target-head",
        "localRagRepoPath": str(structural),
        "localReviewOverlayPath": str(overlay),
    }
    values.update(overrides)
    return ReviewRequestDto(**values)


@pytest.mark.asyncio(loop_scope="function")
async def test_review_generation_is_prepared_before_mcp_context(service, tmp_path):
    request = _local_only_request(tmp_path)
    client = MagicMock()
    client.prepare_review_generation = AsyncMock(return_value={
        "status": "ready",
        "collection_target": "sealed-review",
        "generation_manifest_sha256": "b" * 64,
        "source_revision": "source-head",
        "cache_hit": False,
    })
    events = []

    await service._prepare_rag_review_generation(request, client, events.append)

    assert request.ragReviewGenerationStatus == "ready"
    assert request.ragReviewCollectionTarget == "sealed-review"
    assert request.ragReviewGenerationManifestSha256 == "b" * 64
    assert events[-1]["state"] == "rag_review_generation_ready"
    assert service._build_rag_mcp_context(request, client)[
        "review_collection_target"
    ] == "sealed-review"
    client.prepare_review_generation.assert_awaited_once()


@pytest.mark.asyncio(loop_scope="function")
async def test_review_generation_failure_is_observable_and_fails_open(
    service,
    tmp_path,
):
    request = _local_only_request(tmp_path)
    client = MagicMock()
    client.prepare_review_generation = AsyncMock(return_value={
        "status": "error",
        "error": "capacity unavailable",
    })
    events = []

    await service._prepare_rag_review_generation(request, client, events.append)

    assert request.ragReviewGenerationStatus == "unavailable"
    assert request.ragReviewCollectionTarget is None
    assert request.ragReviewGenerationError == "capacity unavailable"
    assert service._build_rag_mcp_context(request, client) is None
    assert events[-1]["state"] == "rag_review_generation_degraded"


@pytest.mark.asyncio(loop_scope="function")
async def test_required_review_generation_failure_is_observable_and_fail_closed(
    service,
    tmp_path,
):
    request = _local_only_request(
        tmp_path,
        mcpLocalOnly=False,
        requireStructuralMcp=True,
    )
    client = MagicMock()
    client.prepare_review_generation = AsyncMock(return_value={
        "status": "error",
        "error": "capacity unavailable",
    })
    events = []

    await service._prepare_rag_review_generation(request, client, events.append)

    assert request.ragReviewGenerationStatus == "unavailable"
    assert request.ragReviewGenerationError == "capacity unavailable"
    assert events[-1]["state"] == "rag_review_generation_failed"
    assert "stop before model use" in events[-1]["message"]


def test_required_structural_mcp_contract_rejects_disabled_dependencies(service):
    request = MagicMock(
        requireStructuralMcp=True,
        useMcpTools=False,
        ragEnabled=True,
    )
    with pytest.raises(ValueError, match="useMcpTools must be true"):
        service._validate_required_structural_mcp_request(request)

    request.useMcpTools = True
    request.ragEnabled = False
    with pytest.raises(ValueError, match="ragEnabled must be true"):
        service._validate_required_structural_mcp_request(request)


@pytest.mark.asyncio(loop_scope="function")
async def test_local_only_preflight_proves_request_bound_proposed_source(
    service,
    tmp_path,
):
    request = _local_only_request(tmp_path)
    vcs = MagicMock()
    vcs.call_tool = AsyncMock(return_value=SimpleNamespace(content=[
        SimpleNamespace(text=json.dumps({
            "filePath": "src/a.py",
            "fileContent": "proposed source",
            "source": "review-overlay",
            "exists": True,
            "changed": True,
            "unavailable": False,
        }))
    ]))
    client = MagicMock()
    client.get_all_active_sessions.return_value = {
        "codecrow-vcs-mcp": vcs,
    }

    receipt = await service._preflight_local_only_mcp(client, request)

    assert receipt == {
        "status": "ready",
        "focusPath": "src/a.py",
        "baseRevision": "target-head",
        "sourceAuthority": "review-overlay",
    }
    assert vcs.call_tool.await_args.args[0] == "getReviewFileContent"


@pytest.mark.asyncio(loop_scope="function")
async def test_local_only_preflight_does_not_call_optional_structural_session(
    service,
    tmp_path,
):
    request = _local_only_request(tmp_path)
    vcs = MagicMock()
    vcs.call_tool = AsyncMock(return_value=SimpleNamespace(content=[
        SimpleNamespace(text=json.dumps({
            "filePath": "src/a.py",
            "source": "review-overlay",
            "exists": True,
            "changed": True,
            "unavailable": False,
        }))
    ]))
    rag = MagicMock()
    rag.call_tool = AsyncMock(return_value=SimpleNamespace(content=[
        SimpleNamespace(text=json.dumps({
            "status": "error",
            "status_code": 409,
            "error": (
                "proposed changes alter repository-aware structural plugin "
                "selection: data-contracts"
            ),
            "snapshot": {},
        }))
    ]))
    client = MagicMock()
    client.get_all_active_sessions.return_value = {
        "codecrow-vcs-mcp": vcs,
        "codecrow-rag-mcp": rag,
    }

    receipt = await service._preflight_local_only_mcp(client, request)

    assert receipt["sourceAuthority"] == "review-overlay"
    rag.call_tool.assert_not_awaited()


@pytest.mark.asyncio(loop_scope="function")
async def test_local_only_preflight_failure_makes_zero_llm_calls(
    service,
    tmp_path,
):
    request = _local_only_request(tmp_path)
    service.rag_client = MagicMock(enabled=True)
    vcs = MagicMock()
    vcs.call_tool = AsyncMock(return_value=SimpleNamespace(content=[
        SimpleNamespace(text=json.dumps({
            "filePath": "src/a.py",
            "source": "review-overlay",
            "exists": True,
            "changed": True,
            "unavailable": True,
            "error": "review overlay unavailable",
        }))
    ]))
    client = MagicMock()
    client.get_all_active_sessions.return_value = {
        "codecrow-vcs-mcp": vcs,
    }
    client.close_all_sessions = AsyncMock()
    llm = MagicMock()
    llm.ainvoke = AsyncMock()

    class InitializedAgentService:
        def __init__(self, **_kwargs):
            pass

        async def initialize(self, **_kwargs):
            return {}

    with (
        patch("service.review.review_service.os.path.exists", return_value=True),
        patch.object(service, "_create_llm", return_value=llm),
        patch.object(service, "_create_mcp_client", return_value=client),
        patch("service.agent.AgentExecutionService", InitializedAgentService),
    ):
        result = await service._process_review(request)

    llm.ainvoke.assert_not_awaited()
    assert result["result"]["error"]
    client.close_all_sessions.assert_awaited_once()


@pytest.mark.asyncio(loop_scope="function")
async def test_local_only_request_accepts_disabled_optional_graph_service(
    service,
    tmp_path,
):
    request = _local_only_request(tmp_path)
    service.rag_client = MagicMock(enabled=False)

    service._validate_local_only_mcp_request(request)
    assert service._rag_client_for_agent_tools() is None

# ── _create_llm ──────────────────────────────────────────────────

class TestReviewServiceCreateLlm:
    def test_success(self, service):
        request = MagicMock(
            aiModel="gpt-4",
            aiProvider="openai",
            aiApiKey="key",
            aiBaseUrl=None,
            aiCustomParameters=None,
            projectId=1,
        )
        with patch("service.review.review_service.LLMFactory") as mock_factory:
            mock_factory.create_llm.return_value = MagicMock()
            llm = service._create_llm(request)
            mock_factory.create_llm.assert_called_once_with(
                "gpt-4",
                "openai",
                "key",
                ai_base_url=None,
                ai_custom_parameters=None,
            )

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

    def test_mcp_session_close_timeout_is_positive(self, service):
        assert isinstance(ReviewService.MCP_SESSION_CLOSE_TIMEOUT_SECONDS, float)
        assert ReviewService.MCP_SESSION_CLOSE_TIMEOUT_SECONDS > 0


@pytest.mark.asyncio(loop_scope="function")
async def test_hanging_mcp_cleanup_does_not_strand_completed_review(
    service,
    caplog,
):
    cleanup_cancelled = asyncio.Event()

    async def hanging_cleanup():
        try:
            await asyncio.Future()
        finally:
            cleanup_cancelled.set()

    client = MagicMock()
    client.close_all_sessions = AsyncMock(side_effect=hanging_cleanup)
    service.MCP_SESSION_CLOSE_TIMEOUT_SECONDS = 0.01

    with caplog.at_level(logging.WARNING):
        await asyncio.wait_for(
            service._close_mcp_sessions(
                client,
                context="review completion",
            ),
            timeout=0.5,
        )

    client.close_all_sessions.assert_awaited_once()
    assert cleanup_cancelled.is_set()
    assert "MCP session cleanup timed out" in caplog.text
    assert "review completion" in caplog.text


@pytest.mark.asyncio(loop_scope="function")
async def test_mcp_cleanup_failure_is_fail_open(service, caplog):
    client = MagicMock()
    client.close_all_sessions = AsyncMock(side_effect=RuntimeError("disconnect failed"))

    with caplog.at_level(logging.WARNING):
        await service._close_mcp_sessions(
            client,
            context="review completion",
        )

    assert "Error closing MCP sessions during review completion" in caplog.text


@pytest.mark.asyncio(loop_scope="function")
async def test_cancellation_during_mcp_startup_closes_owned_client(service):
    request = ReviewRequestDto(
        projectId=1,
        projectVcsWorkspace="ws",
        projectVcsRepoSlug="repo",
        projectWorkspace="tenant",
        projectNamespace="project",
        aiProvider="OPENAI",
        aiModel="gpt-4",
        aiApiKey="key",
        ragEnabled=False,
        useMcpTools=True,
    )
    client = MagicMock()
    client.close_all_sessions = AsyncMock()
    initialization_started = asyncio.Event()

    class HangingAgentExecutionService:
        def __init__(self, **_kwargs):
            pass

        async def initialize(self, **_kwargs):
            initialization_started.set()
            await asyncio.Future()

    with (
        patch("service.review.review_service.os.path.exists", return_value=True),
        patch.object(service, "_create_llm", return_value=object()),
        patch.object(service, "_create_mcp_client", return_value=client),
        patch("service.agent.AgentExecutionService", HangingAgentExecutionService),
    ):
        task = asyncio.create_task(service._process_review(request))
        await asyncio.wait_for(initialization_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    client.close_all_sessions.assert_awaited_once()
