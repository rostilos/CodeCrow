"""
Extended tests for service.review.orchestrator.json_utils —
clean_json_text, parse_llm_response, repair_json_with_llm.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from pydantic import BaseModel
from service.review.orchestrator.json_utils import clean_json_text


class SimpleModel(BaseModel):
    name: str
    value: int = 0


# ── clean_json_text ──────────────────────────────────────────

class TestCleanJsonText:
    def test_plain_json(self):
        assert clean_json_text('{"a": 1}') == '{"a": 1}'

    def test_markdown_code_block(self):
        text = '```json\n{"a": 1}\n```'
        result = clean_json_text(text)
        assert '"a"' in result

    def test_generic_code_block(self):
        text = '```\n{"a": 1}\n```'
        result = clean_json_text(text)
        assert '"a"' in result

    def test_text_before_json(self):
        text = 'Here is the result:\n{"a": 1}'
        result = clean_json_text(text)
        assert result == '{"a": 1}'

    def test_text_after_json(self):
        text = '{"a": 1}\nSome explanation'
        result = clean_json_text(text)
        assert result == '{"a": 1}'

    def test_nested_json(self):
        text = '{"a": {"b": 2}}'
        result = clean_json_text(text)
        assert json.loads(result) == {"a": {"b": 2}}

    def test_code_block_opening(self):
        text = '```json\n{"name": "test"}\n```'
        result = clean_json_text(text)
        parsed = json.loads(result)
        assert parsed["name"] == "test"

    def test_leading_whitespace(self):
        text = '   \n  {"x": 1}  '
        result = clean_json_text(text)
        assert json.loads(result) == {"x": 1}

    def test_multiple_json_objects(self):
        """When text has explanatory JSON and actual JSON, takes first object."""
        text = '{"name": "actual"} is what you need'
        result = clean_json_text(text)
        assert '"name"' in result

    def test_array_start(self):
        text = '[{"a": 1}]'
        result = clean_json_text(text)
        # Should still work (array or object detection)
        assert result.strip() != ""
