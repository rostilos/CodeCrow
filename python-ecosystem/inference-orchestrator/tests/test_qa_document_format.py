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


@pytest.mark.asyncio
@pytest.mark.parametrize("fence", [False, True])
async def test_truncated_sections_payload_is_repaired_and_rendered_locally(fence):
    # Reproduces a max-token cutoff: the response is cut inside the last
    # complete section object, exactly like a real truncated QA generation.
    prefix = json.dumps({"sections": [
        {"kind": "overview", "heading": "# Короткий опис змін", "content": "PR змінює обробку сповіщень."},
        {"kind": "test_cases", "heading": "# Тестові сценарії",
         "content": "**Login** (HIGH)\nConfirm the code works."},
        {"kind": "environment", "heading": "# Підготовка",
         "content": "Use a QA account and a test phone number."},
        {"kind": "overview", "heading": "# Ризики", "content": "unreachable after the cut"},
    ]}, ensure_ascii=False)
    cut = prefix.index('"Use a QA account and a test phone number.')
    truncated = prefix[:cut + len('"Use a QA account and a test phone nu')]
    if fence:
        truncated = "```json\n" + truncated

    events = []
    orchestrator = QaDocOrchestrator(
        MagicMock(ainvoke=AsyncMock(side_effect=AssertionError("unexpected paid call"))),
        event_callback=events.append,
    )
    result = await orchestrator._ensure_shareable_sections(truncated, {})

    assert '"sections"' not in result
    assert "PR змінює обробку сповіщень." in result
    assert "**Login** (HIGH)\nConfirm the code works." in result
    # The cut landed mid-string in the environment section: its delivered
    # content is salvaged, so the preview stays available.
    assert "Use a QA account and a test phone nu" in result
    assert "unreachable after the cut" not in result
    assert orchestrator._has_complete_shareable_sections(result)
    assert not any(event.get("stage") == "qa_preview_unavailable" for event in events)
    assert result.startswith("# Короткий опис змін")
    orchestrator.llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_truncated_test_cases_content_is_salvaged_when_environment_never_arrived():
    # Real-world shape: the provider cut the response inside the test_cases
    # string, so no environment section was ever delivered.
    truncated = json.dumps({"sections": [
        {"kind": "overview", "heading": "# Guide", "content": "Keep this."},
        {"kind": "test_cases", "heading": "## Tests",
         "content": "**Checkout** (HIGH)\nConfirm the order appears."},
        {"kind": "environment", "heading": "Setup", "content": "Use a QA account."},
    ]}, ensure_ascii=False)
    cut = truncated.index('"Use a QA account.')
    truncated = truncated[:cut + len('"Use a QA ac')]

    orchestrator = QaDocOrchestrator(MagicMock(ainvoke=AsyncMock()))
    result = await orchestrator._ensure_shareable_sections(truncated, {})

    assert '"sections"' not in result
    assert "Keep this." in result
    # The delivered test-case content is salvaged, not dropped.
    assert "**Checkout** (HIGH)\nConfirm the order appears." in result
    assert "codecrow-test-cases:start" in result
    assert "Use a QA ac" in result
    assert orchestrator._has_complete_shareable_sections(result)


@pytest.mark.asyncio
async def test_truncated_payload_without_complete_section_is_preserved_unchanged():
    truncated = '{"sections": [{"kind": "overview", "heading": "# Guide", "conten'
    orchestrator = QaDocOrchestrator(MagicMock(ainvoke=AsyncMock()))
    assert await orchestrator._ensure_shareable_sections(truncated, {}) == truncated


@pytest.mark.asyncio
async def test_prose_with_braces_is_never_treated_as_a_sections_payload():
    response = "# Guide\nSteps { use the QA account } and confirm."
    orchestrator = QaDocOrchestrator(MagicMock(ainvoke=AsyncMock()))
    assert await orchestrator._ensure_shareable_sections(response, {}) == response
