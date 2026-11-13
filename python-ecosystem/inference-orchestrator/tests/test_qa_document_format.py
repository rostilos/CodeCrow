"""Local formatting recovery must preserve content and never invoke a model."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from service.qa_documentation.qa_doc_orchestrator import QaDocOrchestrator


FIXTURES = json.loads((Path(__file__).resolve().parents[3]
    / "java-ecosystem/libs/core/src/test/resources/qa-document-format.json").read_text())


@pytest.mark.asyncio
@pytest.mark.parametrize("case", FIXTURES, ids=lambda case: case["name"])
async def test_same_section_contract_as_java_without_paid_repair(case):
    llm = MagicMock(ainvoke=AsyncMock(side_effect=AssertionError("unexpected paid call")))
    orchestrator = QaDocOrchestrator(llm)
    result = await orchestrator._ensure_shareable_sections(case["markdown"], {})
    assert orchestrator._has_complete_shareable_sections(result) == case["shareable"]
    if case["shareable"]:
        assert "Use the QA account." in result
        assert "Confirm the order appears." in result
        assert "Verify saved baskets." in result
        if case["name"] in {"bold-priority", "numbered-scenario", "heading-scenario"}:
            assert "**Submit the order** (HIGH)" in result
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fenced", [False, True])
async def test_host_renders_section_boundaries_and_preserves_custom_overview(fenced):
    sections = [
        {"kind": "overview", "heading": "# Guide", "content": ""},
        {"kind": "overview", "heading": "", "content": "Custom instructions"},
        {"kind": "test_cases", "heading": "Перевірки", "content": "**Order (HIGH)**\nConfirm order."},
        {"kind": "overview", "heading": "## Risks", "content": "Check saved baskets."},
        {"kind": "environment", "heading": "Підготовка", "content": "Use a QA account."},
    ]
    response = json.dumps({"sections": sections}, ensure_ascii=False)
    if fenced:
        response = "```json\n" + response + "\n```"
    llm = MagicMock(ainvoke=AsyncMock())
    orchestrator = QaDocOrchestrator(llm)
    result = await orchestrator._ensure_shareable_sections(response, {})
    assert orchestrator._has_complete_shareable_sections(result)
    assert result.startswith("# Guide\n\nCustom instructions")
    assert "**Order** (HIGH)\nConfirm order." in result
    assert "## Перевірки" in result and "## Підготовка" in result
    assert result.index("codecrow-test-cases:end") < result.index("## Risks")
    assert result.index("## Risks") < result.index("codecrow-environment:start")
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_structured_output_preserves_content_without_inventing_setup():
    response = json.dumps({"sections": [
        {"kind": "overview", "heading": "Guide", "content": "Keep this explanation."},
        {"kind": "test_cases", "heading": "Tests", "content": "Confirm checkout."},
    ]})
    orchestrator = QaDocOrchestrator(MagicMock(ainvoke=AsyncMock()))
    result = await orchestrator._ensure_shareable_sections(response, {})
    assert "Keep this explanation." in result and "Confirm checkout." in result
    assert "codecrow-environment" not in result
    assert not orchestrator._has_complete_shareable_sections(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("section", [None, {"kind": []}, {"kind": "test_cases", "content": ["Keep this"]}])
async def test_malformed_section_retains_original_response_instead_of_failing(section):
    response = json.dumps({"sections": [section]})
    orchestrator = QaDocOrchestrator(MagicMock(ainvoke=AsyncMock()))
    assert await orchestrator._ensure_shareable_sections(response, {}) == response


@pytest.mark.asyncio
async def test_json_example_inside_prose_does_not_replace_the_actual_guide():
    response = '# Guide\nKeep these instructions.\n{"sections": [{"kind": "overview", "content": "Example"}]}'
    orchestrator = QaDocOrchestrator(MagicMock(ainvoke=AsyncMock()))
    assert await orchestrator._ensure_shareable_sections(response, {}) == response


@pytest.mark.asyncio
async def test_multiple_explicit_test_sections_merge_without_losing_either_area():
    response = json.dumps({"sections": [
        {"kind": "test_cases", "heading": "Checkout", "content": "**Order** (HIGH)\nVerify receipt."},
        {"kind": "test_cases", "heading": "Account", "content": "**Login** (LOW)\nVerify profile."},
        {"kind": "environment", "heading": "Setup", "content": "Use QA."},
    ]})
    orchestrator = QaDocOrchestrator(MagicMock(ainvoke=AsyncMock()))
    result = await orchestrator._ensure_shareable_sections(response, {})
    assert orchestrator._has_complete_shareable_sections(result)
    assert "Verify receipt." in result and "Verify profile." in result
    assert result.count("<!-- codecrow-test-cases:start -->") == 1
