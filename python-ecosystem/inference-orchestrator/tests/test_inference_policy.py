"""
Tests for review inference policy decisions.
"""
from model.dtos import ReviewRequestDto
from model.multi_stage import ReviewPlan
from model.output_schemas import CodeReviewIssue
from service.review.orchestrator.inference_policy import (
    DEFAULT_STAGE_OUTPUT_CAPS,
    ReviewInferenceProfile,
    build_review_inference_profile,
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


def test_task_history_context_forces_stage_2_in_fast_check():
    request = _request(taskHistoryContext="Prior PR #41 covered checkout acceptance criteria.")
    plan = ReviewPlan(analysis_summary="plan", file_groups=[], cross_file_concerns=[])

    run, reason = should_run_stage_2(_fast_profile(), request, plan, [])

    assert run is True
    assert "task history" in reason


def test_absent_task_context_does_not_force_stage_2_in_fast_check():
    request = _request()
    plan = ReviewPlan(analysis_summary="plan", file_groups=[], cross_file_concerns=[])

    run, reason = should_run_stage_2(_fast_profile(), request, plan, [])

    assert run is False
    assert "fast check" in reason


def test_high_severity_issue_forces_stage_2_without_category_allowlist():
    request = _request()
    plan = ReviewPlan(analysis_summary="plan", file_groups=[], cross_file_concerns=[])
    issue = CodeReviewIssue(
        id="i-1",
        severity="HIGH",
        category="STYLE",
        file="a.py",
        line=1,
        reason="High-impact issue from Stage 1.",
        suggestedFixDescription="Fix it.",
        codeSnippet="x = 1",
    )

    run, reason = should_run_stage_2(_fast_profile(), request, plan, [issue])

    assert run is True
    assert "high-severity" in reason


def test_default_output_caps_are_not_tiny_for_structured_json():
    assert DEFAULT_STAGE_OUTPUT_CAPS["stage_0"]["large"] >= 12_000
    assert DEFAULT_STAGE_OUTPUT_CAPS["stage_1"]["large"] >= 40_000
    assert DEFAULT_STAGE_OUTPUT_CAPS["stage_2"]["large"] >= 25_000
    assert DEFAULT_STAGE_OUTPUT_CAPS["stage_3"]["large"] >= 18_000


def test_stage_output_cap_env_override(monkeypatch):
    monkeypatch.setenv("REVIEW_STAGE_0_MAX_OUTPUT_TOKENS", "16000")
    request = _request(changedFiles=[f"src/file_{i}.py" for i in range(20)])

    profile = build_review_inference_profile(request, processed_diff=None)

    assert profile.size_class == "large"
    assert profile.output_cap("stage_0") == 16_000
