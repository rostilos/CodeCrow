"""Tests for json_utils: parse_llm_response, repair_json_with_llm, clean_json_text."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pydantic import BaseModel, Field
from service.review.orchestrator.json_utils import (
    parse_llm_response,
    repair_json_with_llm,
    clean_json_text,
)


class DummyModel(BaseModel):
    name: str = ""
    value: int = 0


# ── clean_json_text ───────────────────────────────────────────


class TestCleanJsonText:
    def test_plain_json(self):
        assert json.loads(clean_json_text('{"a": 1}')) == {"a": 1}

    def test_strips_markdown_json_block(self):
        text = '```json\n{"a": 1}\n```'
        assert json.loads(clean_json_text(text)) == {"a": 1}

    def test_strips_generic_code_block(self):
        text = '```\n{"x": 2}\n```'
        assert json.loads(clean_json_text(text)) == {"x": 2}

    def test_json_embedded_in_text(self):
        text = 'Here is the result: {"name": "test", "value": 42} done'
        result = json.loads(clean_json_text(text))
        assert result["name"] == "test"

    def test_array_only_input(self):
        text = '[1, 2, 3]'
        result = json.loads(clean_json_text(text))
        assert result == [1, 2, 3]

    def test_mid_text_json_block(self):
        text = 'prefix ```json\n{"ok": true}\n``` suffix'
        result = json.loads(clean_json_text(text))
        assert result == {"ok": True}

    def test_no_json_boundaries(self):
        # No braces/brackets → returns the stripped text as-is
        result = clean_json_text("hello world")
        assert result == "hello world"

    def test_array_before_object(self):
        text = '[1] {"a": 2}'
        result = clean_json_text(text)
        # Object is preferred when it appears
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_multiline_code_block(self):
        text = "```\n{\n  \"key\": \"val\"\n}\n```"
        parsed = json.loads(clean_json_text(text))
        assert parsed["key"] == "val"


# ── repair_json_with_llm ─────────────────────────────────────


class TestRepairJsonWithLlm:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_llm_text(self):
        llm = MagicMock()
        resp = MagicMock()
        resp.content = '{"name": "fixed", "value": 1}'
        llm.ainvoke = AsyncMock(return_value=resp)
        result = await repair_json_with_llm(llm, '{"broken', "err", {})
        assert "fixed" in result or "name" in result

    @pytest.mark.asyncio(loop_scope="function")
    async def test_truncates_long_input(self):
        llm = MagicMock()
        resp = MagicMock()
        resp.content = '{"ok": true}'
        llm.ainvoke = AsyncMock(return_value=resp)
        long_json = "x" * 5000
        await repair_json_with_llm(llm, long_json, "error", {})
        call_args = llm.ainvoke.call_args[0][0]
        # The broken json in the prompt should be truncated
        assert len(call_args) < len(long_json) + 2000


# ── parse_llm_response ───────────────────────────────────────


class TestParseLlmResponse:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_initial_clean_parse_succeeds(self):
        content = '{"name": "hello", "value": 99}'
        result = await parse_llm_response(content, DummyModel, MagicMock())
        assert result.name == "hello"
        assert result.value == 99

    @pytest.mark.asyncio(loop_scope="function")
    async def test_markdown_wrapped_json(self):
        content = '```json\n{"name": "md", "value": 7}\n```'
        result = await parse_llm_response(content, DummyModel, MagicMock())
        assert result.name == "md"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_structured_output_fallback(self):
        """When initial parse fails, structured output retry is attempted."""
        content = "NOT_JSON"
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(return_value=DummyModel(name="structured", value=1))
        llm.with_structured_output.return_value = structured
        result = await parse_llm_response(content, DummyModel, llm)
        assert result.name == "structured"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_repair_loop_fallback(self):
        """When structured output also fails, LLM repair loop is tried."""
        content = "NOT_JSON"
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(side_effect=Exception("structured fail"))
        llm.with_structured_output.return_value = structured

        resp = MagicMock()
        resp.content = '{"name": "repaired", "value": 5}'
        llm.ainvoke = AsyncMock(return_value=resp)

        result = await parse_llm_response(content, DummyModel, llm, retries=1)
        assert result.name == "repaired"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_raises_after_all_retries(self):
        content = "NOT_JSON"
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(side_effect=Exception("fail"))
        llm.with_structured_output.return_value = structured

        resp = MagicMock()
        resp.content = "STILL_NOT_JSON"
        llm.ainvoke = AsyncMock(return_value=resp)

        with pytest.raises(ValueError, match="Failed to parse"):
            await parse_llm_response(content, DummyModel, llm, retries=1)
