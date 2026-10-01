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
    def test_unicode_one_line_is_bounded_with_truthful_partial_coverage(self):
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
        diagnostic = next(
            item
            for packet in packets
            for item in packet
            if item.key == "codecrow:qa-coverage-diagnostic"
        )

        assert len(packets) == 4
        admitted = "".join(item.text for item in fragments)
        assert text.startswith(admitted)
        assert len(admitted) < len(text)
        assert '"coverage":"PARTIAL"' in diagnostic.text
        assert '"omittedCharacterCount":' in diagnostic.text
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

    def test_dependency_batches_are_capped_with_file_coverage_diagnostic(self):
        orchestrator = QaDocOrchestrator(llm=MagicMock())
        paths = [f"src/file_{index:03d}.py" for index in range(75)]

        batches = orchestrator.build_dependency_batches(
            paths,
            enrichment_data=None,
        )

        assert len(batches) == 4
        diagnostic = orchestrator.dependency_batch_coverage_diagnostic()
        assert diagnostic == {
            "coverage": "PARTIAL",
            "reason": "QA dependency-batch invocation ceiling",
            "maxDependencyBatches": 4,
            "sourceBatchCount": 5,
            "omittedBatchCount": 1,
            "omittedFileCount": 15,
            "omittedFileSample": [
                f"src/file_{index:03d}.py" for index in range(60, 75)
            ],
        }


class TestQaPackedStages:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_stage_1_prefers_diff_with_one_call_and_reports_omissions(self):
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
        assert llm.ainvoke.await_count == 1
        assert results
        assert diff_markers[0] in prompts
        assert sum(marker in prompts for marker in diff_markers) < len(diff_markers)
        assert sum(marker in prompts for marker in source_markers) < len(source_markers)
        assert "QA_COVERAGE_DIAGNOSTIC" in prompts
        assert all(
            orchestrator.estimate_rendered_input_tokens(call.args[0]) <= 7_000
            for call in llm.ainvoke.await_args_list
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stage_2_hierarchy_is_four_call_bounded_and_partial(self):
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
        assert 1 < llm.ainvoke.await_count <= 4
        assert result["cross_file_scenarios"]
        assert markers[0] in prompts
        assert sum(marker in prompts for marker in markers) < len(markers)
        assert "QA_COVERAGE_DIAGNOSTIC" in prompts
        assert all(
            orchestrator.estimate_rendered_input_tokens(call.args[0]) <= 7_000
            for call in llm.ainvoke.await_args_list
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stage_3_delta_prioritizes_diff_with_four_call_ceiling(self):
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
        assert 1 < llm.ainvoke.await_count <= 4
        assert diff_markers[0] in prompts
        assert sum(marker in prompts for marker in diff_markers) < len(diff_markers)
        assert "QA_COVERAGE_DIAGNOSTIC" in prompts
        assert all(
            orchestrator.estimate_rendered_input_tokens(call.args[0]) <= 7_000
            for call in llm.ainvoke.await_args_list
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_single_pass_and_repair_are_four_call_bounded(self):
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
        assert llm.ainvoke.await_count <= 4
        assert diff_marker in single_pass_prompts
        assert "QA_COVERAGE_DIAGNOSTIC" in single_pass_prompts

        llm.ainvoke.reset_mock()
        repair_marker = "REPAIR_GUIDE_UNIQUE"
        repaired = await orchestrator._ensure_shareable_sections(
            (repair_marker + "😀 ") * 8_000,
            _placeholders(),
        )
        repair_prompts = _all_prompt_text(llm)

        assert orchestrator._has_complete_shareable_sections(repaired)
        assert llm.ainvoke.await_count <= 4
        assert "QA_COVERAGE_DIAGNOSTIC" in repair_prompts
        assert all(
            orchestrator.estimate_rendered_input_tokens(call.args[0]) <= 7_000
            for call in llm.ainvoke.await_args_list
        )
