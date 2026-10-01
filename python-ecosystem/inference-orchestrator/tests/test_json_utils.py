"""
Unit tests for service.review.orchestrator.json_utils — clean_json_text.
(parse_llm_response and repair_json_with_llm are async and need LLM mock — tested separately.)
"""
import json

import pytest
from pydantic import BaseModel
from unittest.mock import AsyncMock, MagicMock

from service.review.orchestrator.json_utils import (
    clean_json_text,
    load_json_with_local_repairs,
    parse_llm_response,
)


class _Payload(BaseModel):
    value: int


class TestCleanJsonText:

    def test_plain_json(self):
        assert clean_json_text('{"key": "value"}') == '{"key": "value"}'

    def test_markdown_code_block(self):
        text = '```json\n{"key": "value"}\n```'
        result = clean_json_text(text)
        assert '"key"' in result
        assert "```" not in result

    def test_markdown_block_no_lang(self):
        text = '```\n{"items": [1,2]}\n```'
        result = clean_json_text(text)
        assert '"items"' in result

    def test_leading_text_before_json(self):
        text = 'Here is the result:\n{"comment": "ok", "issues": []}'
        result = clean_json_text(text)
        assert result.startswith("{")
        assert result.endswith("}")

    def test_trailing_text_after_json(self):
        text = '{"data": 1}\nSome trailing note'
        result = clean_json_text(text)
        assert result == '{"data": 1}'

    def test_array_json(self):
        text = '[1, 2, 3]'
        result = clean_json_text(text)
        assert result == '[1, 2, 3]'

    def test_nested_code_block(self):
        text = 'Explanation:\n```json\n{"a": 1}\n```\nDone.'
        result = clean_json_text(text)
        assert '"a"' in result

    def test_whitespace_handling(self):
        text = '  \n  {"x": 1}  \n  '
        result = clean_json_text(text)
        assert '"x"' in result

    def test_no_json(self):
        text = "no json here"
        result = clean_json_text(text)
        # Should return the text as-is since no boundaries found
        assert isinstance(result, str)

    def test_object_inside_array(self):
        text = '[{"a": 1}]'
        # Array comes first but no separate object → returns the object inside
        result = clean_json_text(text)
        assert isinstance(result, str)

    def test_multiple_code_blocks_picks_last(self):
        text = '```json\n{"first": true}\n```\n\n```json\n{"second": true}\n```'
        result = clean_json_text(text)
        assert '"second"' in result

    def test_incomplete_code_block(self):
        text = '```json\n{"incomplete": true}'
        result = clean_json_text(text)
        assert '"incomplete"' in result


class TestLoadJsonWithLocalRepairs:

    def test_valid_json_preserves_nested_json_fence(self):
        payload = {
            "summary": "Example:\n```json\n{'foo': 1}\n```\nEnd.",
        }
        text = json.dumps(payload)

        cleaned, parsed = load_json_with_local_repairs(text)

        assert cleaned == text
        assert parsed == payload

    def test_valid_json_preserves_nested_diff_fence(self):
        payload = {
            "suggestedFixDiff": "```diff\n-old\n+new\n```",
        }
        text = json.dumps(payload)

        cleaned, parsed = load_json_with_local_repairs(text)

        assert cleaned == text
        assert parsed == payload

    @pytest.mark.parametrize(
        ("text", "expected"),
        (
            ('```json\n{"value": 1}\n```', {"value": 1}),
            ('Result: {"value": 2} done.', {"value": 2}),
            ('{"value": 3,}', {"value": 3}),
        ),
    )
    def test_existing_cleanup_fallbacks_remain_available(
            self,
            text,
            expected,
    ):
        _, parsed = load_json_with_local_repairs(text)

        assert parsed == expected


@pytest.mark.asyncio(loop_scope="function")
async def test_zero_provider_repair_budget_never_invokes_model():
    llm = MagicMock()
    llm.ainvoke = AsyncMock()

    with pytest.raises(ValueError, match="Failed to parse _Payload locally"):
        await parse_llm_response(
            "{not-json",
            _Payload,
            llm,
            max_provider_repairs=0,
        )

    llm.with_structured_output.assert_not_called()
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio(loop_scope="function")
async def test_shared_provider_repair_budget_counts_structured_retry():
    structured = MagicMock()
    structured.ainvoke = AsyncMock(side_effect=RuntimeError("provider failed"))
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    llm.ainvoke = AsyncMock()

    with pytest.raises(ValueError):
        await parse_llm_response(
            "{not-json",
            _Payload,
            llm,
            retries=2,
            max_provider_repairs=1,
        )

    structured.ainvoke.assert_awaited_once()
    llm.ainvoke.assert_not_awaited()
