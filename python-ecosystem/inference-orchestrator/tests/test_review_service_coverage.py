"""Full-file policy coverage for ReviewService lifecycle and cleanup paths."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import service.review.review_service as review_module
from service.review.review_service import ReviewService
from service.review.telemetry import MemoryTelemetrySink, StageOutcome, TerminalOutcome


def _service():
    service = ReviewService.__new__(ReviewService)
    service.default_jar_path = "/tmp/mcp.jar"
    service.rag_client = MagicMock()
    service.rag_cache = MagicMock()
    service._review_semaphore = asyncio.Semaphore(1)
    return service


def _request(**overrides):
    values = {
        "rawDiff": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n",
        "analysisType": "PULL_REQUEST",
        "reconciliationFileContents": [],
        "previousCodeAnalysisIssues": [],
        "changedFiles": ["a.py"],
        "diffSnippets": ["+new"],
        "projectWorkspace": "workspace",
        "projectNamespace": "namespace",
        "projectVcsWorkspace": "vcs-ws",
        "projectVcsRepoSlug": "repo",
        "projectId": 1,
        "pullRequestId": 2,
        "commitHash": "commit",
        "prTitle": "PR",
        "prDescription": "description",
        "oAuthClient": None,
        "oAuthSecret": None,
        "accessToken": "token",
        "maxAllowedTokens": 10_000,
        "vcsProvider": "github",
        "aiModel": "fixture",
        "aiProvider": "scripted",
        "aiApiKey": "secret",
        "aiBaseUrl": None,
        "aiCustomParameters": None,
        "executionId": "execution-1",
        "baseRevision": "a" * 40,
        "headRevision": "b" * 40,
        "promptVersion": "prompt-v1",
        "rulesVersion": "rules-v1",
        "policyVersion": "policy-v1",
        "indexVersion": "rag-commit-" + "c" * 40,
    }
    values.update(overrides)
    request = MagicMock(**values)
    request.get_rag_branch.return_value = overrides.get("rag_branch", "feature")
    request.get_rag_base_branch.return_value = overrides.get("base_branch", "main")
    return request


class TestProcessReviewLifecycle:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_success_and_cancelled_requests_reset_telemetry(self):
        service = _service()
        request = _request()
        sink = MemoryTelemetrySink()
        with patch.object(
            service, "_create_telemetry_recorder", return_value=(None, sink)
        ), patch.object(
            service, "_process_review", new=AsyncMock(return_value={"result": {"issues": []}})
        ), patch.object(
            service, "_attach_terminal_telemetry", side_effect=lambda **kwargs: kwargs["result"]
        ) as attach:
            result = await service.process_review_request(request)
        assert result["result"]["issues"] == []
        attach.assert_called_once()

        with patch.object(
            service, "_create_telemetry_recorder", return_value=(None, sink)
        ), patch.object(
            service, "_process_review", new=AsyncMock(side_effect=asyncio.CancelledError)
        ), patch.object(
            service, "_attach_terminal_telemetry", side_effect=RuntimeError("telemetry")
        ):
            with pytest.raises(asyncio.CancelledError):
                await service.process_review_request(request)


class _Usage:
    provider_usage_missing_calls = 0
    cost_estimate_missing_calls = 0


class _Recorder:
    def __init__(self, *, incomplete=False, usage=None, finish_error=None, latest_coverage=None):
        self.has_incomplete_operations = incomplete
        self.model_usage = usage or _Usage()
        self.sink_errors = []
        self.finish_error = finish_error
        self.calls = []
        self.latest_coverage = latest_coverage

    def provisional_snapshot(self, **kwargs):
        self.calls.append(kwargs)
        if self.finish_error:
            raise self.finish_error
        return SimpleNamespace(outcome=kwargs["outcome"])


class TestTerminalTelemetryCoverage:
    def test_no_recorder_and_terminal_reason_precedence(self):
        service = _service()
        events = []
        result = {"result": {"issues": []}}
        assert service._attach_terminal_telemetry(
            request=_request(), result=result, recorder=None,
            sink=MemoryTelemetrySink(), started_ns=0, event_callback=events.append,
        ) is result
        assert events[-1]["state"] == "not_emitted"

        cases = [
            ({"result": {"status": "error", "issues": []}}, {}, TerminalOutcome.FAILED),
            ({"result": {"issues": []}}, {"rawDiff": ""}, TerminalOutcome.PARTIAL),
            ({"result": {"issues": []}}, {"indexVersion": "legacy-index-unversioned"}, TerminalOutcome.PARTIAL),
        ]
        for payload, overrides, expected in cases:
            recorder = _Recorder()
            with patch.object(review_module, "trace_document", return_value={"trace": 1}), patch.object(
                review_module, "asdict", side_effect=lambda value: value
            ):
                attached = service._attach_terminal_telemetry(
                    request=_request(**overrides), result=payload, recorder=recorder,
                    sink=SimpleNamespace(metrics=[{"metric": 1}]),
                    started_ns=0, event_callback=None,
                )
            assert recorder.calls[-1]["outcome"] is expected
            assert "telemetry" in attached["result"]

    @pytest.mark.parametrize(
        ("incomplete", "provider_missing", "cost_missing", "expected_reason"),
        [
            (True, 0, 0, "stage_or_call_incomplete"),
            (False, 1, 0, "provider_usage_unavailable"),
            (False, 0, 1, "cost_estimate_unavailable"),
        ],
    )
    def test_partial_usage_reasons(self, incomplete, provider_missing, cost_missing, expected_reason):
        service = _service()
        usage = _Usage()
        usage.provider_usage_missing_calls = provider_missing
        usage.cost_estimate_missing_calls = cost_missing
        recorder = _Recorder(incomplete=incomplete, usage=usage)
        with patch.object(review_module, "trace_document", return_value={}), patch.object(
            review_module, "asdict", side_effect=lambda value: value
        ):
            service._attach_terminal_telemetry(
                request=_request(), result={"result": {"issues": []}}, recorder=recorder,
                sink=SimpleNamespace(metrics=[{}]), started_ns=0, event_callback=None,
            )
        assert recorder.calls[-1]["reason"] == expected_reason

    def test_coverage_finish_and_artifact_failures_are_fail_closed(self):
        service = _service()
        original = {"result": {"issues": "not-a-list"}}
        recorder = _Recorder()
        with patch.object(
            review_module.DiffProcessor, "process", side_effect=RuntimeError("diff")
        ), patch.object(review_module, "trace_document", return_value={}):
            service._attach_terminal_telemetry(
                request=_request(), result=original, recorder=recorder,
                sink=SimpleNamespace(metrics=[]), started_ns=0, event_callback=None,
                forced_outcome=TerminalOutcome.CANCELLED, forced_reason="cancelled",
            )
        assert recorder.calls[-1]["outcome"] is TerminalOutcome.CANCELLED

        assert service._attach_terminal_telemetry(
            request=_request(), result=original,
            recorder=_Recorder(finish_error=RuntimeError("finish")),
            sink=SimpleNamespace(metrics=[]), started_ns=0, event_callback=None,
        ) is original

        with patch.object(review_module, "trace_document", side_effect=RuntimeError("artifact")):
            assert service._attach_terminal_telemetry(
                request=_request(), result=original, recorder=_Recorder(),
                sink=SimpleNamespace(metrics=[]), started_ns=0, event_callback=None,
            ) is original

    def test_skipped_diff_hunks_produce_incomplete_coverage(self):
        service = _service()
        recorder = _Recorder()
        processed = SimpleNamespace(files=[
            SimpleNamespace(hunks=["represented"], is_skipped=False),
            SimpleNamespace(hunks=["skipped"], is_skipped=True),
        ])
        with patch.object(
            review_module.DiffProcessor, "process", return_value=processed
        ), patch.object(review_module, "trace_document", return_value={}), patch.object(
            review_module, "asdict", side_effect=lambda value: value
        ):
            service._attach_terminal_telemetry(
                request=_request(), result={"result": {"issues": []}}, recorder=recorder,
                sink=SimpleNamespace(metrics=[{}]), started_ns=0, event_callback=None,
            )
        assert recorder.calls[-1]["reason"] == "coverage_incomplete"

    def test_retrieval_telemetry_rejection_is_contained(self):
        recorder = MagicMock()
        recorder.record_stage.side_effect = RuntimeError("sink")
        with patch.object(review_module, "current_telemetry", return_value=recorder):
            ReviewService._record_retrieval_telemetry(
                outcome=StageOutcome.FAILED,
                started_ns=0,
                input_count=-1,
                output_count=-2,
                reason="provider_failed",
            )
        recorder.record_stage.assert_called_once()

    def test_non_mapping_analysis_result_still_emits_terminal_event(self):
        service = _service()
        events = []
        with patch.object(review_module, "trace_document", return_value={}), patch.object(
            review_module, "asdict", side_effect=lambda value: value
        ):
            result = service._attach_terminal_telemetry(
                request=_request(), result={"result": None}, recorder=_Recorder(),
                sink=SimpleNamespace(metrics=[{}]), started_ns=0,
                event_callback=events.append,
            )
        assert result == {"result": None}
        assert events[-1]["state"] == "provisional"


class _TimeoutNow:
    async def __aenter__(self):
        raise TimeoutError("deadline")

    async def __aexit__(self, *_args):
        return False


class TestProcessReviewCoverage:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_direct_reconciliation_success_timeout_and_error(self):
        service = _service()
        previous = MagicMock()
        previous.dict.return_value = {"id": "old"}
        request = _request(
            analysisType="BRANCH_ANALYSIS",
            reconciliationFileContents=[{"path": "a.py"}],
            previousCodeAnalysisIssues=[previous],
        )
        orchestrator = MagicMock()
        orchestrator.execute_batched_branch_analysis = AsyncMock(
            return_value={"issues": [{"id": "old"}]}
        )
        with patch.object(service, "_create_llm", return_value=MagicMock()), patch.object(
            review_module, "MultiStageReviewOrchestrator", return_value=orchestrator
        ), patch.object(
            review_module, "post_process_analysis_result", side_effect=lambda value: value
        ):
            result = await service._process_review(request)
        assert result["result"]["issues"]

        orchestrator.execute_batched_branch_analysis = AsyncMock(return_value=None)
        with patch.object(service, "_create_llm", return_value=MagicMock()), patch.object(
            review_module, "MultiStageReviewOrchestrator", return_value=orchestrator
        ):
            assert (await service._process_review(request))["result"] is None

        with patch.object(review_module.asyncio, "timeout", return_value=_TimeoutNow()), patch.object(
            review_module.ResponseParser, "create_error_response", return_value={"status": "error"}
        ):
            assert (await service._process_review(request))["result"]["status"] == "error"

        with patch.object(service, "_create_llm", side_effect=RuntimeError("provider")), patch.object(
            review_module.ResponseParser, "create_error_response", return_value={"status": "error"}
        ):
            assert (await service._process_review(request))["result"]["status"] == "error"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_jar_is_reported(self):
        service = _service()
        with patch.object(review_module.os.path, "exists", return_value=False):
            result = await service._process_review(_request())
        assert "error" in result

    @pytest.mark.asyncio(loop_scope="function")
    async def test_standard_multistage_path_processes_diff_and_closes_client(self):
        service = _service()
        request = _request()
        client = MagicMock(close_all_sessions=AsyncMock(side_effect=RuntimeError("close")))
        orchestrator = MagicMock()
        orchestrator.orchestrate_review = AsyncMock(return_value={"issues": [{"id": "new"}]})
        processed = SimpleNamespace(
            total_files=1, total_additions=1, total_deletions=1,
            skipped_files=0, truncated=True, truncation_reason="bounded",
        )

        async def slow_rag(*_args, **_kwargs):
            await asyncio.sleep(60)

        with patch.object(review_module.os.path, "exists", return_value=True), patch.object(
            service, "_build_jvm_props", return_value={}
        ), patch.object(review_module.MCPConfigBuilder, "build_config", return_value={}), patch.object(
            service, "_create_mcp_client", return_value=client
        ), patch.object(service, "_create_llm", return_value=MagicMock()), patch.object(
            service, "_fetch_rag_context", side_effect=slow_rag
        ), patch.object(review_module.DiffProcessor, "process", return_value=processed), patch.object(
            review_module, "MultiStageReviewOrchestrator", return_value=orchestrator
        ), patch.object(
            review_module, "post_process_analysis_result", side_effect=lambda value: value
        ):
            result = await service._process_review(request)
        assert result["result"]["issues"][0]["id"] == "new"
        client.close_all_sessions.assert_awaited_once()

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize("previous", [True, False])
    async def test_standard_branch_modes(self, previous):
        service = _service()
        prev = MagicMock()
        prev.dict.return_value = {"id": "old"}
        request = _request(
            analysisType="BRANCH_ANALYSIS", rawDiff="",
            reconciliationFileContents=[], previousCodeAnalysisIssues=[prev] if previous else [],
        )
        client = MagicMock(close_all_sessions=AsyncMock())
        orchestrator = MagicMock()
        orchestrator.execute_batched_branch_analysis = AsyncMock(return_value={"issues": []})
        orchestrator.orchestrate_review = AsyncMock(return_value=None)
        with patch.object(review_module.os.path, "exists", return_value=True), patch.object(
            service, "_build_jvm_props", return_value={}
        ), patch.object(review_module.MCPConfigBuilder, "build_config", return_value={}), patch.object(
            service, "_create_mcp_client", return_value=client
        ), patch.object(service, "_create_llm", return_value=MagicMock()), patch.object(
            service, "_fetch_rag_context", new=AsyncMock(return_value=None)
        ), patch.object(review_module, "MultiStageReviewOrchestrator", return_value=orchestrator):
            result = await service._process_review(request)
        if previous:
            assert result["result"]["issues"] == []
            orchestrator.execute_batched_branch_analysis.assert_awaited_once()
        else:
            assert result["result"] is None
            orchestrator.orchestrate_review.assert_awaited_once()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_standard_timeout_and_exception_are_sanitized(self):
        service = _service()
        with patch.object(review_module.os.path, "exists", return_value=True), patch.object(
            review_module.asyncio, "timeout", return_value=_TimeoutNow()
        ), patch.object(
            review_module.ResponseParser, "create_error_response", return_value={"status": "timeout"}
        ):
            assert (await service._process_review(_request()))["result"]["status"] == "timeout"

        with patch.object(review_module.os.path, "exists", return_value=True), patch.object(
            service, "_build_jvm_props", side_effect=RuntimeError("credential=secret")
        ), patch.object(
            review_module, "create_user_friendly_error", return_value="safe"
        ), patch.object(
            review_module.ResponseParser, "create_error_response", return_value={"status": "error"}
        ):
            assert (await service._process_review(_request()))["result"]["status"] == "error"

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        ("diff_error", "response_status"),
        [(TimeoutError("diff deadline"), "timeout"), (RuntimeError("diff failed"), "error")],
    )
    async def test_diff_failure_cancels_active_rag_fallback(self, diff_error, response_status):
        service = _service()
        client = MagicMock(close_all_sessions=AsyncMock())

        async def slow_rag(*_args, **_kwargs):
            await asyncio.sleep(60)

        with patch.object(review_module.os.path, "exists", return_value=True), patch.object(
            service, "_build_jvm_props", return_value={}
        ), patch.object(review_module.MCPConfigBuilder, "build_config", return_value={}), patch.object(
            service, "_create_mcp_client", return_value=client
        ), patch.object(service, "_create_llm", return_value=MagicMock()), patch.object(
            service, "_fetch_rag_context", side_effect=slow_rag
        ), patch.object(
            review_module.DiffProcessor, "process", side_effect=diff_error
        ), patch.object(
            review_module, "create_user_friendly_error", return_value="safe"
        ), patch.object(
            review_module.ResponseParser, "create_error_response",
            return_value={"status": response_status},
        ):
            result = await service._process_review(_request())
        assert result["result"]["status"] == response_status

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        ("orchestrator_error", "response_status"),
        [(TimeoutError("pipeline deadline"), "timeout"), (RuntimeError("pipeline"), "error")],
    )
    async def test_pipeline_failure_consumes_completed_rag_task(
        self, orchestrator_error, response_status
    ):
        service = _service()
        client = MagicMock(close_all_sessions=AsyncMock())
        processed = SimpleNamespace(
            total_files=1, total_additions=1, total_deletions=0,
            skipped_files=0, truncated=False, truncation_reason=None,
        )
        orchestrator = MagicMock()

        async def fail_after_rag(*_args, **_kwargs):
            await asyncio.sleep(0)
            raise orchestrator_error

        orchestrator.orchestrate_review = AsyncMock(side_effect=fail_after_rag)
        with patch.object(review_module.os.path, "exists", return_value=True), patch.object(
            service, "_build_jvm_props", return_value={}
        ), patch.object(review_module.MCPConfigBuilder, "build_config", return_value={}), patch.object(
            service, "_create_mcp_client", return_value=client
        ), patch.object(service, "_create_llm", return_value=MagicMock()), patch.object(
            service, "_fetch_rag_context", new=AsyncMock(return_value=None)
        ), patch.object(review_module.DiffProcessor, "process", return_value=processed), patch.object(
            review_module, "MultiStageReviewOrchestrator", return_value=orchestrator
        ), patch.object(review_module, "create_user_friendly_error", return_value="safe"), patch.object(
            review_module.ResponseParser, "create_error_response",
            return_value={"status": response_status},
        ):
            result = await service._process_review(_request())
        assert result["result"]["status"] == response_status


class TestGlobalRagCoverage:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_branch_cache_hit_remote_success_and_empty(self):
        service = _service()
        no_branch = _request(rag_branch=None)
        no_branch.get_rag_branch.return_value = None
        assert await service._fetch_rag_context(no_branch, None) is None

        cached = {"relevant_code": [{"text": "cache"}]}
        service.rag_cache.get.return_value = cached
        assert await service._fetch_rag_context(_request(), None) is cached

        cached_without_code = {"metadata": "cache"}
        service.rag_cache.get.return_value = cached_without_code
        assert await service._fetch_rag_context(_request(), None) is cached_without_code

        service.rag_cache.get.return_value = None
        service.rag_client.get_pr_context = AsyncMock(return_value={
            "context": {"relevant_code": [{"text": "remote"}]}
        })
        remote = await service._fetch_rag_context(_request(), None)
        assert remote["relevant_code"][0]["text"] == "remote"
        service.rag_cache.set.assert_called()

        service.rag_client.get_pr_context = AsyncMock(return_value={})
        assert await service._fetch_rag_context(_request(), None) is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cancellation_is_propagated(self):
        service = _service()
        service.rag_cache.get.return_value = None
        service.rag_client.get_pr_context = AsyncMock(side_effect=asyncio.CancelledError)
        with pytest.raises(asyncio.CancelledError):
            await service._fetch_rag_context(_request(), None)
