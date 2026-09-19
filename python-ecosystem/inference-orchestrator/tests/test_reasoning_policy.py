import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from llm.reasoning_policy import (
    ReasoningEffort,
    bounded_output_token_limit,
    output_token_request_kwargs,
    reasoning_request_kwargs,
)
from model.multi_stage import (
    CrossFileAnalysisResult,
    FileGroup,
    FileReviewBatchOutput,
    FileReviewOutput,
    ReviewFile,
    ReviewPlan,
)
from service.review.orchestrator.json_utils import parse_llm_response
from service.review.orchestrator.stage_0_planning import execute_stage_0_planning
from service.review.orchestrator.stage_1_file_review import _invoke_stage_1_batch_llm
from service.review.orchestrator.stage_2_cross_file import _invoke_stage_2_llm
from service.review.orchestrator.stage_2_cross_file import (
    STAGE2_MAX_OUTPUT_TOKENS,
)
from service.review.orchestrator.stage_3_aggregation import _invoke_stage_3_report
from service.review.orchestrator.verification_agent import _run_verification_tool_loop


class ChatOpenRouter:
    """Small provider-shaped test double that records invocation kwargs."""

    def __init__(self, responses, *, extra_body=None, max_tokens=None):
        self.responses = list(responses)
        self.extra_body = extra_body
        self.max_tokens = max_tokens
        self.calls = []

    def with_structured_output(self, _schema):
        return self

    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, input_data, **kwargs):
        self.calls.append((input_data, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _effort(call):
    return call[1]["extra_body"]["reasoning"]["effort"]


def test_reasoning_kwargs_are_openrouter_only_and_preserve_other_extra_body():
    configured = {
        "provider": {"order": ["DeepInfra"]},
        "reasoning": {"max_tokens": 1234, "exclude": True},
    }
    llm = ChatOpenRouter([], extra_body=configured)

    kwargs = reasoning_request_kwargs(llm, ReasoningEffort.LOW)

    assert kwargs == {
        "extra_body": {
            "provider": {"order": ["DeepInfra"]},
            "reasoning": {"effort": "low"},
        }
    }
    assert configured["reasoning"] == {"max_tokens": 1234, "exclude": True}
    assert reasoning_request_kwargs(MagicMock(), ReasoningEffort.HIGH) == {}

    wrapped = SimpleNamespace(__codecrow_delegate__=llm)
    assert reasoning_request_kwargs(wrapped, ReasoningEffort.NONE) == {
        "extra_body": {
            "provider": {"order": ["DeepInfra"]},
            "reasoning": {"effort": "none"},
        }
    }


@pytest.mark.parametrize(
    ("class_name", "expected"),
    [
        ("ChatOpenRouter", {"max_tokens": 16_384}),
        ("ChatOpenAI", {"max_tokens": 16_384}),
        ("ChatAnthropic", {"max_tokens": 16_384}),
        ("ChatGoogleGenerativeAI", {"max_output_tokens": 16_384}),
    ],
)
def test_output_cap_uses_provider_canonical_field(class_name, expected):
    llm = type(class_name, (), {})()

    assert output_token_request_kwargs(llm, 16_384) == expected


def test_bounded_output_cap_does_not_raise_wrapped_model_configuration():
    delegate = type("ChatOpenRouter", (), {"max_tokens": 4096})()
    wrapped = SimpleNamespace(__codecrow_delegate__=delegate)

    assert bounded_output_token_limit(wrapped, 16_384) == 4096


@pytest.mark.asyncio(loop_scope="function")
async def test_stage_0_and_stage_1_use_low_reasoning_for_structured_analysis():
    plan = ReviewPlan(
        analysis_summary="plan",
        file_groups=[
            FileGroup(
                group_id="group",
                priority="HIGH",
                rationale="changed code",
                files=[ReviewFile(path="src/a.py")],
            )
        ],
    )
    stage_0_llm = ChatOpenRouter([plan])
    request = MagicMock()
    request.changedFiles = ["src/a.py"]
    request.deletedFiles = []
    request.prTitle = "PR"
    request.prAuthor = "author"
    request.sourceBranchName = "feature"
    request.targetBranchName = "main"
    request.currentCommitHash = "a" * 40
    request.commitHash = "a" * 40
    request.taskContext = None
    request.projectCapabilities = None

    assert await execute_stage_0_planning(stage_0_llm, request) == plan
    assert _effort(stage_0_llm.calls[0]) == "low"

    batch = FileReviewBatchOutput(
        reviews=[
            FileReviewOutput(
                file="src/a.py",
                analysis_summary="clean",
                issues=[],
                confidence="HIGH",
            )
        ]
    )
    stage_1_llm = ChatOpenRouter([batch])
    assert await _invoke_stage_1_batch_llm(
        stage_1_llm,
        "review prompt",
        ["src/a.py"],
    ) == []
    assert _effort(stage_1_llm.calls[0]) == "low"


@pytest.mark.asyncio(loop_scope="function")
async def test_cross_file_is_bounded_low_reasoning_and_verification_stays_high():
    cross_file = CrossFileAnalysisResult(
        pr_risk_level="LOW",
        cross_file_issues=[],
        pr_recommendation="approve",
        confidence="HIGH",
    )
    stage_2_llm = ChatOpenRouter([cross_file])

    assert await _invoke_stage_2_llm(stage_2_llm, "cross-file prompt", "test") == cross_file
    assert _effort(stage_2_llm.calls[0]) == "low"
    assert stage_2_llm.calls[0][1]["max_tokens"] == (
        STAGE2_MAX_OUTPUT_TOKENS
    )

    verification_llm = ChatOpenRouter([
        SimpleNamespace(content='{"issue_ids_to_drop": []}', tool_calls=[]),
    ])
    result = await _run_verification_tool_loop(
        verification_llm,
        "verification prompt",
    )
    assert result.issue_ids_to_drop == []
    assert _effort(verification_llm.calls[0]) == "high"


@pytest.mark.asyncio(loop_scope="function")
async def test_cross_file_respects_lower_provider_output_cap():
    result = CrossFileAnalysisResult(
        pr_risk_level="LOW",
        cross_file_issues=[],
        pr_recommendation="PASS",
        confidence="HIGH",
    )
    structured_llm = ChatOpenRouter([result], max_tokens=4096)

    assert await _invoke_stage_2_llm(
        structured_llm,
        "cross-file prompt",
        "provider-cap",
    ) == result
    assert structured_llm.calls[0][1]["max_tokens"] == 4096

    raw_llm = ChatOpenRouter([
        SimpleNamespace(content=result.model_dump_json()),
    ], max_tokens=4096)
    assert await _invoke_stage_2_llm(
        raw_llm,
        "cross-file prompt",
        "provider-cap-recovery",
        force_unstructured=True,
    ) == result
    assert raw_llm.calls[0][1]["max_tokens"] == 4096


@pytest.mark.asyncio(loop_scope="function")
async def test_cross_file_google_recovery_uses_canonical_lower_output_cap():
    result = CrossFileAnalysisResult(
        pr_risk_level="LOW",
        cross_file_issues=[],
        pr_recommendation="PASS",
        confidence="HIGH",
    )

    class ChatGoogleGenerativeAI:
        def __init__(self):
            self.max_output_tokens = 2048
            self.calls = []

        async def ainvoke(self, prompt, **kwargs):
            self.calls.append((prompt, kwargs))
            return SimpleNamespace(content=result.model_dump_json())

    llm = ChatGoogleGenerativeAI()

    assert await _invoke_stage_2_llm(
        llm,
        "cross-file prompt",
        "google-recovery",
        force_unstructured=True,
    ) == result
    assert llm.calls == [(
        "cross-file prompt",
        {"max_output_tokens": 2048},
    )]


@pytest.mark.asyncio(loop_scope="function")
async def test_report_uses_low_but_json_extraction_uses_no_reasoning():
    report_llm = ChatOpenRouter([SimpleNamespace(content="report")])
    assert await _invoke_stage_3_report(report_llm, "report prompt") == {
        "report": "report",
        "dismissed_issue_ids": [],
    }
    assert _effort(report_llm.calls[0]) == "low"

    repaired_plan = ReviewPlan(
        analysis_summary="repaired",
        file_groups=[],
    )
    parser_llm = ChatOpenRouter([repaired_plan])
    assert await parse_llm_response("not json", ReviewPlan, parser_llm) == repaired_plan
    assert _effort(parser_llm.calls[0]) == "none"


@pytest.mark.asyncio(loop_scope="function")
async def test_direct_output_recoveries_disable_reasoning():
    stage_1_llm = ChatOpenRouter([
        SimpleNamespace(content=json.dumps({
            "reviews": [{
                "file": "src/a.py",
                "analysis_summary": "clean",
                "issues": [],
                "confidence": "HIGH",
                "note": "",
            }],
        })),
    ])
    assert await _invoke_stage_1_batch_llm(
        stage_1_llm,
        "review prompt",
        ["src/a.py"],
        label="direct-output recovery",
        force_unstructured=True,
    ) == []
    assert _effort(stage_1_llm.calls[0]) == "none"

    stage_2_llm = ChatOpenRouter([
        SimpleNamespace(content=CrossFileAnalysisResult(
            pr_risk_level="LOW",
            cross_file_issues=[],
            pr_recommendation="PASS",
            confidence="HIGH",
        ).model_dump_json()),
    ])
    stage_2_result = await _invoke_stage_2_llm(
        stage_2_llm,
        "cross-file prompt",
        "direct-output recovery",
        force_unstructured=True,
    )
    assert stage_2_result is not None
    assert _effort(stage_2_llm.calls[0]) == "none"
    assert stage_2_llm.calls[0][1]["max_tokens"] == (
        STAGE2_MAX_OUTPUT_TOKENS
    )

    report_llm = ChatOpenRouter([
        SimpleNamespace(
            content="",
            response_metadata={"finish_reason": "max_tokens"},
        ),
        SimpleNamespace(
            content="recovered report",
            response_metadata={"finish_reason": "stop"},
        ),
    ])
    assert await _invoke_stage_3_report(
        report_llm,
        "report prompt",
        fallback_llm=report_llm,
    ) == {
        "report": "recovered report",
        "dismissed_issue_ids": [],
    }
    assert _effort(report_llm.calls[0]) == "low"
    assert _effort(report_llm.calls[1]) == "none"
