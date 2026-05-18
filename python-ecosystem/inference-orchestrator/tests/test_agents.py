"""
Unit tests for service.review.orchestrator.agents — extract_llm_response_text.
"""
import pytest
from service.review.orchestrator.agents import extract_llm_response_text


class _FakeResponse:
    """Mock LLM response object."""
    def __init__(self, content):
        self.content = content


class _FakeItem:
    """Mock content item with .text attribute."""
    def __init__(self, text):
        self.text = text


class TestExtractLlmResponseText:

    def test_string_content(self):
        resp = _FakeResponse("Hello world")
        assert extract_llm_response_text(resp) == "Hello world"

    def test_list_of_strings(self):
        resp = _FakeResponse(["Part 1", " Part 2"])
        assert extract_llm_response_text(resp) == "Part 1 Part 2"

    def test_list_of_dicts_with_text(self):
        resp = _FakeResponse([{"text": "chunk A"}, {"text": "chunk B"}])
        assert extract_llm_response_text(resp) == "chunk Achunk B"

    def test_list_of_dicts_with_content(self):
        resp = _FakeResponse([{"content": "c1"}, {"content": "c2"}])
        assert extract_llm_response_text(resp) == "c1c2"

    def test_list_of_objects_with_text_attr(self):
        resp = _FakeResponse([_FakeItem("alpha"), _FakeItem("beta")])
        assert extract_llm_response_text(resp) == "alphabeta"

    def test_mixed_list(self):
        resp = _FakeResponse(["str", {"text": "dict"}, _FakeItem("obj")])
        assert extract_llm_response_text(resp) == "strdictobj"

    def test_no_content_attr(self):
        """Falls back to str()."""
        assert extract_llm_response_text(42) == "42"
        assert extract_llm_response_text("plain") == "plain"

    def test_none_content(self):
        resp = _FakeResponse(None)
        result = extract_llm_response_text(resp)
        assert result == "None"

    def test_empty_list(self):
        resp = _FakeResponse([])
        assert extract_llm_response_text(resp) == ""
