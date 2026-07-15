"""P0-02 characterization of Stage 1 batching loss boundaries."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from model.multi_stage import ReviewFile, ReviewPlan
from model.output_schemas import CodeReviewIssue
from service.review.orchestrator.stage_1_file_review import (
    _build_stage_1_prepared_context,
    execute_stage_1_file_reviews,
    review_file_batch,
)


pytestmark = pytest.mark.legacy_defect


def _issue(issue_id, file_path):
    return CodeReviewIssue(
        id=issue_id,
        severity="HIGH",
        category="BUG_RISK",
        file=file_path,
        line=10,
        reason=f"Observed issue {issue_id}",
        suggestedFixDescription="Fix it",
        codeSnippet="unsafe_call()",
    )


def _request(paths):
    request = MagicMock(
        deltaDiff=None,
        rawDiff="",
        taskContext=None,
        enrichmentData=None,
        projectRules=[],
        previousCodeAnalysisIssues=[],
        changedFiles=paths,
        deletedFiles=[],
    )
    return request


def _batches(paths):
    return [
        [{"file": ReviewFile(path=path, focus_areas=[], risk_level="MEDIUM"), "priority": "MEDIUM"}]
        for path in paths
    ]


@pytest.mark.asyncio(loop_scope="function")
async def test_legacy_defect_partial_batch_failure_disappears_from_the_result():
    paths = ["src/ordinary.py", "src/timed_out.py"]

    async def run_batch(batch_idx, *_args, **_kwargs):
        if batch_idx == 2:
            raise asyncio.TimeoutError("legacy injected timeout")
        return [_issue("survivor", paths[0])]

    with patch(
        "service.review.orchestrator.stage_1_file_review.create_smart_batches_wrapper",
        new_callable=AsyncMock,
        return_value=_batches(paths),
    ), patch(
        "service.review.orchestrator.stage_1_file_review._review_batch_with_timing",
        side_effect=run_batch,
    ):
        result = await execute_stage_1_file_reviews(
            MagicMock(),
            _request(paths),
            ReviewPlan(analysis_summary="legacy", file_groups=[], cross_file_concerns=[]),
            rag_client=None,
        )

    assert [issue.id for issue in result] == ["survivor"]
    assert all(issue.file != paths[1] for issue in result)


@pytest.mark.asyncio(loop_scope="function")
async def test_legacy_defect_total_batch_failure_is_indistinguishable_from_zero_findings():
    paths = ["src/timed_out.py"]

    with patch(
        "service.review.orchestrator.stage_1_file_review.create_smart_batches_wrapper",
        new_callable=AsyncMock,
        return_value=_batches(paths),
    ), patch(
        "service.review.orchestrator.stage_1_file_review._review_batch_with_timing",
        new_callable=AsyncMock,
        side_effect=asyncio.TimeoutError("legacy injected timeout"),
    ):
        result = await execute_stage_1_file_reviews(
            MagicMock(),
            _request(paths),
            ReviewPlan(analysis_summary="legacy", file_groups=[], cross_file_concerns=[]),
            rag_client=None,
        )

    assert result == []


@pytest.mark.asyncio(loop_scope="function")
async def test_ordinary_batches_merge_in_plan_order_despite_completion_order():
    paths = ["src/first.py", "src/second.py"]

    async def run_batch(batch_idx, *_args, **_kwargs):
        if batch_idx == 1:
            await asyncio.sleep(0.01)
        return [_issue(f"issue-{batch_idx}", paths[batch_idx - 1])]

    with patch(
        "service.review.orchestrator.stage_1_file_review.create_smart_batches_wrapper",
        new_callable=AsyncMock,
        return_value=_batches(paths),
    ), patch(
        "service.review.orchestrator.stage_1_file_review._review_batch_with_timing",
        side_effect=run_batch,
    ):
        result = await execute_stage_1_file_reviews(
            MagicMock(),
            _request(paths),
            ReviewPlan(analysis_summary="legacy", file_groups=[], cross_file_concerns=[]),
            rag_client=None,
            max_parallel=2,
        )

    assert [issue.id for issue in result] == ["issue-1", "issue-2"]


@pytest.mark.asyncio(loop_scope="function")
async def test_legacy_defect_malformed_capped_and_uncapped_attempts_collapse_to_empty():
    path = "src/malformed.py"
    request = _request([path])
    prepared = _build_stage_1_prepared_context(request, None, is_incremental=False)
    batch = _batches([path])[0]

    with patch(
        "service.review.orchestrator.stage_1_file_review._invoke_stage_1_batch_llm",
        new_callable=AsyncMock,
        side_effect=[None, None],
    ) as invoke:
        result = await review_file_batch(
            MagicMock(name="capped_llm"),
            request,
            batch,
            rag_client=None,
            prepared_context=prepared,
            fallback_llm=MagicMock(name="uncapped_llm"),
        )

    assert result == []
    assert [call.kwargs["label"] for call in invoke.await_args_list] == ["capped", "uncapped retry"]


@pytest.mark.asyncio(loop_scope="function")
async def test_legacy_defect_restart_replays_successful_work_without_a_checkpoint():
    paths = ["src/succeeds.py", "src/fails.py"]
    calls = []

    async def run_batch(batch_idx, *_args, **_kwargs):
        calls.append(batch_idx)
        if batch_idx == 2:
            raise RuntimeError("legacy injected worker crash")
        return [_issue("survivor", paths[0])]

    async def one_process_lifetime():
        with patch(
            "service.review.orchestrator.stage_1_file_review.create_smart_batches_wrapper",
            new_callable=AsyncMock,
            return_value=_batches(paths),
        ), patch(
            "service.review.orchestrator.stage_1_file_review._review_batch_with_timing",
            side_effect=run_batch,
        ):
            return await execute_stage_1_file_reviews(
                MagicMock(),
                _request(paths),
                ReviewPlan(analysis_summary="legacy", file_groups=[], cross_file_concerns=[]),
                rag_client=None,
            )

    first = await one_process_lifetime()
    after_restart = await one_process_lifetime()

    assert [issue.id for issue in first] == ["survivor"]
    assert [issue.id for issue in after_restart] == ["survivor"]
    assert calls == [1, 2, 1, 2]
