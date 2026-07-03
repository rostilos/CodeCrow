"""
Tests for review inference policy decisions.
"""
from model.dtos import ReviewRequestDto
from model.multi_stage import ReviewPlan
from service.review.orchestrator.inference_policy import (
    ReviewInferenceProfile,
    should_run_stage_2,
)


def _request(**overrides):
    data = dict(
        projectId=1,
        projectVcsWorkspace="ws",
        projectVcsRepoSlug="repo",
        projectWorkspace="ws",
        projectNamespace="ns",
        aiProvider="OPENAI",
        aiModel="gpt-4",
        aiApiKey="sk-test",
        changedFiles=["a.py"],
    )
    data.update(overrides)
    return ReviewRequestDto(**data)


def _fast_profile():
    return ReviewInferenceProfile(
        file_count=1,
        changed_lines=10,
        diff_bytes=1000,
        size_class="small",
        fast_check_enabled=True,
        fast_check_reason="test",
        caps={},
    )


def test_task_context_forces_stage_2_in_fast_check():
    request = _request(taskContext={"task_key": "PROJ-123"})
    plan = ReviewPlan(analysis_summary="plan", file_groups=[], cross_file_concerns=[])

    run, reason = should_run_stage_2(_fast_profile(), request, plan, [])

    assert run is True
    assert "task context" in reason


def test_absent_task_context_does_not_force_stage_2_in_fast_check():
    request = _request()
    plan = ReviewPlan(analysis_summary="plan", file_groups=[], cross_file_concerns=[])

    run, reason = should_run_stage_2(_fast_profile(), request, plan, [])

    assert run is False
    assert "fast check" in reason
