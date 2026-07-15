from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from codecrow_test_harness.fakes import ScriptedLlmFake
from codecrow_test_harness.ledger import ExternalCallLedger
from codecrow_test_harness.scenario import ScenarioStep, ScriptedScenario
from model.multi_stage import FileGroup, ReviewFile, ReviewPlan
from service.review.orchestrator.stage_0_planning import execute_stage_0_planning


@pytest.mark.asyncio(loop_scope="function")
async def test_stage_0_consumes_the_same_scripted_llm_port(
    external_call_ledger: ExternalCallLedger,
) -> None:
    expected = ReviewPlan(
        analysis_summary="Deterministic offline plan",
        file_groups=[
            FileGroup(
                group_id="all",
                priority="MEDIUM",
                rationale="Neutral fixture",
                files=[ReviewFile(path="src/example.py")],
            )
        ],
    )
    fake = ScriptedLlmFake(
        ledger=external_call_ledger,
        scenario=ScriptedScenario(
            "stage-0-port-contract-v1",
            (
                ScenarioStep(
                    operation="llm.ainvoke",
                    call=1,
                    kind="structured",
                    payload=expected,
                    usage={"input_tokens": 7, "output_tokens": 3},
                ),
            ),
        ),
    )
    request = MagicMock()
    request.changedFiles = ["src/example.py"]
    request.projectVcsRepoSlug = "neutral/example"
    request.pullRequestId = 7
    request.prTitle = "Neutral change"
    request.prAuthor = "fixture-user"
    request.sourceBranchName = "fixture"
    request.targetBranchName = "main"
    request.commitHash = "1111111111111111111111111111111111111111"
    request.taskContext = None

    result = await execute_stage_0_planning(fake, request)

    assert result == expected
    assert fake.output_schema is ReviewPlan
    assert fake.last_usage == {"input_tokens": 7, "output_tokens": 3}
    assert external_call_ledger.live_call_count == 0
    assert [entry.operation for entry in external_call_ledger.entries] == ["llm.ainvoke"]
