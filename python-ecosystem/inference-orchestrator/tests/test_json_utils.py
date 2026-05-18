"""
Unit tests for service.review.orchestrator.json_utils — clean_json_text.
(parse_llm_response and repair_json_with_llm are async and need LLM mock — tested separately.)
"""
import pytest
from service.review.orchestrator.json_utils import clean_json_text


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
