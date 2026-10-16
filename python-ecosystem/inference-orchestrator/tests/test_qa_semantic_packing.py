import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from service.qa_documentation.base_orchestrator import (
    BaseOrchestrator,
    QaSemanticRecord,
)
from service.qa_documentation.qa_doc_orchestrator import QaDocOrchestrator


def _placeholders(**overrides):
    values = {
        "project_name": "Storefront",
        "pr_number": "42",
        "pr_title": "Improve checkout",
        "pr_description": "Checkout behavior changes",
        "task_key": "SHOP-42",
        "task_summary": "Improve checkout",
        "source_branch": "feature/checkout",
        "target_branch": "main",
        "task_context": "No additional task context.",
        "analysis_summary": "Complete analysis summary.",
        "diff": "No diff available.",
        "issues_found": "0",
        "files_analyzed": "1",
        "output_language": "English",
    }
    values.update(overrides)
    return values


def _valid_document(label="Complete"):
    return f"""# QA Testing Guide — {label}

<!-- codecrow-test-cases:start -->
## Test Scenarios
<!-- codecrow-test-cases:content -->
**Checkout succeeds** (HIGH)
- **Steps:**
  1. Complete checkout.
- **Expected Result:** The order is created.
<!-- codecrow-test-cases:end -->

<!-- codecrow-environment:start -->
## Environment and Setup Notes
<!-- codecrow-environment:content -->
No special setup is required.
<!-- codecrow-environment:end -->"""


def _all_prompt_text(llm):
    parts = []
    for call in llm.ainvoke.await_args_list:
        request = call.args[0]
        if isinstance(request, str):
            parts.append(request)
        else:
            parts.extend(str(message.get("content", "")) for message in request)
    return "\n".join(parts)


class TestQaSemanticPackingPrimitives:
    @pytest.mark.parametrize("capacity, expected", [(200_000, 180_000), (50_000, 30_000), (12_000, 6_000), (None, 180_000)])
    def test_persisted_context_reserves_output_within_model_capacity(self, capacity, expected):
        assert BaseOrchestrator.request_input_token_target(capacity, persisted_context=True) == expected

    def test_complete_aggregate_can_exceed_batch_target_but_not_model_capacity(self):
        orchestrator = BaseOrchestrator(llm=MagicMock())
        messages = [{"role": "user", "content": "x" * 240_000}]
        assert not orchestrator.prompt_fits(messages)
        orchestrator.set_request_input_token_target(200_000, persisted_context=True)
        assert orchestrator.prompt_fits(messages)
        orchestrator.set_request_input_token_target(50_000, persisted_context=True)
        assert not orchestrator.prompt_fits(messages)

    def test_unicode_one_line_is_bounded_and_lossless(self):
        text = "😀Привіт漢字" * 2_000
        record = QaSemanticRecord(
            key="unicode",
            section="free_text",
            text=text,
            character_end=len(text),
            source_character_count=len(text),
        )

        def render(records):
            return [{
                "role": "user",
                "content": "\n".join(item.envelope() for item in records),
            }]

        packets = BaseOrchestrator.pack_semantic_records(
            [record],
            render,
            token_target=1_500,
        )
        fragments = [
            item
            for packet in packets
            for item in packet
            if item.key != "codecrow:qa-coverage-diagnostic"
        ]
        assert len(packets) > 4
        assert "".join(item.text for item in fragments) == text
        assert all(item.key != "codecrow:qa-coverage-diagnostic" for packet in packets for item in packet)
        assert all(
            BaseOrchestrator.estimate_rendered_input_tokens(render(packet))
            <= 1_500
            for packet in packets
        )
        assert BaseOrchestrator.request_input_token_target(50_000) == 30_000
        assert BaseOrchestrator.request_input_token_target(12_000) == 6_000

    @pytest.mark.asyncio(loop_scope="function")
    async def test_oversized_relevance_gate_skips_provider(self):
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(content="NO"))
        orchestrator = QaDocOrchestrator(llm=llm)
        orchestrator.qa_input_token_target = 2_000

        needed = await orchestrator._is_documentation_needed(
            _placeholders(diff="RELEVANCE_UNIQUE " * 20_000)
        )

        assert needed is True
        llm.ainvoke.assert_not_awaited()

    @pytest.mark.parametrize("count", [100, 200, 300])
    def test_dependency_batches_retain_all_files(self, count):
        orchestrator = QaDocOrchestrator(llm=MagicMock())
        paths = [f"src/file_{index:03d}.py" for index in range(count)]
        batches = orchestrator.build_dependency_batches(paths, enrichment_data=None)
        assert [item["file_info"].path for batch in batches for item in batch] == paths


class TestQaPackedStages:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_stage_1_packs_all_diff_and_source_evidence(self):
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps({
            "batch_id": 1,
            "file_analyses": [],
        })))
        orchestrator = QaDocOrchestrator(llm=llm)
        orchestrator.qa_input_token_target = 7_000

        source_markers = [f"SOURCE_UNIQUE_{index:04d}" for index in range(240)]
        diff_markers = [f"DIFF_UNIQUE_{index:04d}" for index in range(240)]
        source = "\n".join(f"{marker} = {'x' * 48}" for marker in source_markers)
        diff = (
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1,240 @@\n"
            + "\n".join(f"+{marker} {'y' * 48}" for marker in diff_markers)
        )
        file_content = MagicMock(
            path="src/a.py",
            content=source,
            skipped=False,
        )
        enrichment = MagicMock(fileContents=[file_content])

        results = await orchestrator._execute_stage_1(
            batches=BaseOrchestrator._simple_batch(["src/a.py"]),
            diff=diff,
            enrichment_data=enrichment,
            placeholders=_placeholders(diff=diff),
        )

        prompts = _all_prompt_text(llm)
        assert llm.ainvoke.await_count > 1
        assert results
        assert diff_markers[0] in prompts
        assert all(marker in prompts for marker in diff_markers)
        assert all(marker in prompts for marker in source_markers)
        assert "QA_COVERAGE_DIAGNOSTIC" not in prompts
        assert all(
            orchestrator.estimate_rendered_input_tokens(call.args[0]) <= 7_000
            for call in llm.ainvoke.await_args_list
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stage_2_hierarchy_keeps_all_prior_stage_evidence(self):
        call_number = 0

        async def invoke(_messages):
            nonlocal call_number
            call_number += 1
            return MagicMock(content=json.dumps({
                "cross_file_scenarios": [{"name": f"memo-{call_number}"}],
                "cascading_risks": [],
                "uncovered_acceptance_criteria": [],
            }))

        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=invoke)
        orchestrator = QaDocOrchestrator(llm=llm)
        orchestrator.qa_input_token_target = 7_000
        markers = [f"STAGE1_UNIQUE_{index:04d}" for index in range(2_000)]
        stage_1_results = [
            {"batch_id": 1, "raw_analysis": "\n".join(markers)}
        ]

        result = await orchestrator._execute_stage_2(
            stage_1_results=stage_1_results,
            enrichment_data=None,
            changed_file_paths=["src/a.py"],
            placeholders=_placeholders(),
        )

        prompts = _all_prompt_text(llm)
        assert llm.ainvoke.await_count > 1
        assert result["cross_file_scenarios"]
        assert markers[0] in prompts
        assert all(marker in prompts for marker in markers)
        assert "QA_COVERAGE_DIAGNOSTIC" not in prompts
        assert all(
            orchestrator.estimate_rendered_input_tokens(call.args[0]) <= 7_000
            for call in llm.ainvoke.await_args_list
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stage_3_delta_keeps_diff_history_and_stage_evidence(self):
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(
            content=_valid_document(),
        ))
        orchestrator = QaDocOrchestrator(llm=llm)
        orchestrator.qa_input_token_target = 7_000
        stage_markers = [f"DELTA_RESULT_UNIQUE_{index:04d}" for index in range(800)]
        diff_markers = [f"DELTA_DIFF_UNIQUE_{index:04d}" for index in range(800)]
        previous_markers = [f"PREVIOUS_UNIQUE_{index:04d}" for index in range(800)]
        delta_diff = (
            "diff --git a/src/a.py b/src/a.py\n"
            "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1,180 @@\n"
            + "\n".join(f"+{marker}" for marker in diff_markers)
        )

        result = await orchestrator._execute_stage_3_delta(
            stage_1_results=[{"raw_analysis": "\n".join(stage_markers)}],
            stage_2_results={"cross_file_scenarios": []},
            delta_diff=delta_diff,
            previous_documentation="\n".join(previous_markers),
            placeholders=_placeholders(),
        )

        prompts = _all_prompt_text(llm)
        assert result.startswith("# QA Testing Guide")
        assert llm.ainvoke.await_count > 1
        assert diff_markers[0] in prompts
        assert all(marker in prompts for marker in diff_markers)
        assert all(marker in prompts for marker in stage_markers + previous_markers)
        assert "QA_COVERAGE_DIAGNOSTIC" not in prompts
        assert all(
            orchestrator.estimate_rendered_input_tokens(call.args[0]) <= 7_000
            for call in llm.ainvoke.await_args_list
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_single_pass_and_repair_pack_all_records(self):
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(
            content=_valid_document(),
        ))
        orchestrator = QaDocOrchestrator(llm=llm)
        orchestrator.qa_input_token_target = 7_000
        custom_marker = "CUSTOM_TEMPLATE_UNIQUE"
        diff_marker = "SINGLE_DIFF_UNIQUE"
        previous_marker = "SINGLE_PREVIOUS_UNIQUE"

        document = await orchestrator._run_single_pass(
            template_mode="CUSTOM",
            custom_template=(custom_marker + " ") * 2_000,
            placeholders=_placeholders(diff=(diff_marker + " ") * 2_000),
            previous_documentation=(previous_marker + " ") * 2_000,
        )
        single_pass_prompts = _all_prompt_text(llm)

        assert document.startswith("# QA Testing Guide")
        assert llm.ainvoke.await_count > 1
        assert diff_marker in single_pass_prompts
        assert "QA_COVERAGE_DIAGNOSTIC" not in single_pass_prompts

        llm.ainvoke.reset_mock()
        repair_marker = "REPAIR_GUIDE_UNIQUE"
        repaired = await orchestrator._ensure_shareable_sections(
            (repair_marker + "😀 ") * 8_000,
            _placeholders(),
        )
        repair_prompts = _all_prompt_text(llm)

        assert orchestrator._has_complete_shareable_sections(repaired)
        assert llm.ainvoke.await_count > 1
        assert "QA_COVERAGE_DIAGNOSTIC" not in repair_prompts
        assert all(
            orchestrator.estimate_rendered_input_tokens(call.args[0]) <= 7_000
            for call in llm.ainvoke.await_args_list
        )
