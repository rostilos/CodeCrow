"""Focused boundaries for Stage 3 semantic packing and synthesis modules."""

import pytest

from service.review.orchestrator import stage_3_aggregation
from service.review.orchestrator import stage_3_semantic_packing
from service.review.orchestrator.stage_3_semantic_packing import (
    _Stage3PromptContext,
    _Stage3PromptShard,
)
from service.review.orchestrator.stage_3_synthesis import (
    Stage3SynthesisRuntime,
    synthesize_stage_3_results,
)


def test_facade_reexports_historical_semantic_packing_helpers() -> None:
    assert (
        stage_3_aggregation._Stage3PromptContext
        is stage_3_semantic_packing._Stage3PromptContext
    )
    assert (
        stage_3_aggregation._plan_semantic_records
        is stage_3_semantic_packing._plan_semantic_records
    )
    assert (
        stage_3_aggregation._dependency_aware_stage_3_units
        is stage_3_semantic_packing._dependency_aware_stage_3_units
    )
    assert (
        stage_3_aggregation._build_stage_3_prompt_shards
        is stage_3_semantic_packing._build_stage_3_prompt_shards
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_synthesis_uses_its_typed_report_runtime() -> None:
    captured_prompts = []

    async def invoke_report(
        _llm,
        prompt,
        fallback_llm=None,
        allow_retry=True,
    ):
        assert fallback_llm == "fallback"
        assert allow_retry is True
        captured_prompts.append(prompt)
        return {"report": "integrated report"}

    context = _Stage3PromptContext(
        repo_slug="repo",
        pr_id="42",
        author="dev",
        pr_title="Semantic synthesis",
        total_files=2,
        additions=10,
        deletions=2,
        recommendation="REQUEST_CHANGES",
        incremental_context="",
        use_mcp_tools=False,
        review_revision="commit-sha",
        issue_inventory="Global active issue count: 2",
    )
    shards = [
        _Stage3PromptShard(
            prompt="source-a",
            record_keys=("stage_1:issue_0",),
            verification_ids=("issue_0",),
            use_mcp_tools=False,
        ),
        _Stage3PromptShard(
            prompt="source-b",
            record_keys=("stage_1:issue_1",),
            verification_ids=("issue_1",),
            use_mcp_tools=False,
        ),
    ]
    results = [
        {
            "report": "memo-a",
            "dismissed_issue_keys": ["issue_0"],
        },
        {
            "report": "memo-b",
            "dismissed_issue_keys": ["issue_1", "issue_0"],
        },
    ]

    result = await synthesize_stage_3_results(
        object(),
        context=context,
        input_results=results,
        input_shards=shards,
        token_budget=60_000,
        runtime=Stage3SynthesisRuntime(report_invoker=invoke_report),
        fallback_llm="fallback",
    )

    assert result["report"] == "integrated report"
    assert result["dismissed_issue_keys"] == ["issue_0", "issue_1"]
    assert len(result["_synthesis_provenance"]) == 1
    assert len(captured_prompts) == 1
    assert "memo-a" in captured_prompts[0]
    assert "memo-b" in captured_prompts[0]
