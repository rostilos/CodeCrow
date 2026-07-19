"""Tests for json_utils: parse_llm_response, repair_json_with_llm, clean_json_text."""
import json
import logging
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pydantic import BaseModel, Field
from service.review.orchestrator.json_utils import (
    parse_llm_response,
    repair_json_with_llm,
    clean_json_text,
)
import service.review.orchestrator.json_utils as json_utils


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

    def test_mid_text_generic_blocks_with_and_without_closer(self):
        assert json.loads(clean_json_text('prefix ```{"closed": true}``` suffix')) == {
            "closed": True
        }
        assert json.loads(clean_json_text('prefix ```{"open": true}')) == {
            "open": True
        }

    def test_object_nested_in_array_uses_object_payload(self):
        assert json.loads(clean_json_text('[{"nested": true}]')) == {"nested": True}

    def test_unclosed_array_prefix_with_complete_object_is_preserved(self):
        assert clean_json_text('[ broken {"nested": true}') == '[ broken {"nested": true}'

    def test_escaped_character_is_preserved_during_newline_repair(self):
        text = '{"value":"escaped\\nvalue"}'
        assert json_utils._escape_newlines_in_strings(text) == text


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
    async def test_model_source_is_never_written_to_logs(self, caplog):
        source = "MODEL-SOURCE-SENTINEL-c974d2"
        caplog.set_level(logging.DEBUG, logger=json_utils.__name__)

        result = await parse_llm_response(
            json.dumps({"name": source, "value": 7}),
            DummyModel,
            MagicMock(),
        )

        assert result.name == source
        assert source not in caplog.text

    @pytest.mark.asyncio(loop_scope="function")
    async def test_parse_failures_expose_only_stable_diagnostics(self, caplog):
        source = "BROKEN-MODEL-SOURCE-SENTINEL-ae8051"
        credential = "MODEL-CREDENTIAL-SENTINEL-2b338f"
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            side_effect=RuntimeError(f"provider rejected {credential}")
        )
        llm.with_structured_output.return_value = structured
        llm.ainvoke = AsyncMock(
            return_value=MagicMock(content=f"not-json-{source}")
        )
        caplog.set_level(logging.DEBUG, logger=json_utils.__name__)

        with pytest.raises(ValueError) as raised:
            await parse_llm_response(source, DummyModel, llm, retries=1)

        observable = caplog.text + str(raised.value)
        assert source not in observable
        assert credential not in observable

    def test_environment_and_provider_structured_output_switches(self, monkeypatch):
        monkeypatch.setenv("STRUCTURED_TEST", " yes ")
        assert json_utils._env_bool("STRUCTURED_TEST", False) is True

        monkeypatch.setattr(json_utils, "STRUCTURED_OUTPUT_ENABLED", False)
        assert json_utils.supports_structured_output(MagicMock()) is False

        monkeypatch.setattr(json_utils, "STRUCTURED_OUTPUT_ENABLED", True)
        monkeypatch.setattr(json_utils, "CLOUDFLARE_STRUCTURED_OUTPUT_ENABLED", True)
        assert json_utils.supports_structured_output(MagicMock()) is True

        monkeypatch.setattr(json_utils, "CLOUDFLARE_STRUCTURED_OUTPUT_ENABLED", False)
        cloudflare_type = type("ChatCloudflareOpenAI", (), {})
        assert json_utils.supports_structured_output(cloudflare_type()) is False
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
    async def test_trailing_comma_repaired_without_llm(self):
        content = '{"name": "local", "value": 7,}'
        llm = MagicMock()

        result = await parse_llm_response(content, DummyModel, llm)

        assert result.name == "local"
        llm.with_structured_output.assert_not_called()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unescaped_newline_repaired_without_llm(self):
        content = '{"name": "hello\nworld", "value": 7}'
        llm = MagicMock()

        result = await parse_llm_response(content, DummyModel, llm)

        assert result.name == "hello\nworld"
        llm.with_structured_output.assert_not_called()

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

    @pytest.mark.asyncio(loop_scope="function")
    async def test_disabled_structured_output_goes_directly_to_repair(self, monkeypatch):
        monkeypatch.setattr(json_utils, "STRUCTURED_OUTPUT_ENABLED", False)
        llm = MagicMock()
        response = MagicMock(content='{"name":"repaired","value":3}')
        llm.ainvoke = AsyncMock(return_value=response)

        result = await parse_llm_response("NOT_JSON", DummyModel, llm, retries=1)

        assert result == DummyModel(name="repaired", value=3)
        llm.with_structured_output.assert_not_called()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_structured_repair_continues_to_llm_repair(self):
        llm = MagicMock()
        llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=None)
        llm.ainvoke = AsyncMock(return_value=MagicMock(
            content='{"name":"repaired","value":9}'
        ))
        result = await parse_llm_response("NOT_JSON", DummyModel, llm, retries=1)
        assert result.value == 9
