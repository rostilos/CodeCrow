"""Real provider formatting failures must not lose or rewrite source evidence."""
import pytest

from utils.llm_json import parse_json_object


@pytest.mark.parametrize("prefix", ["", "Model notes: "])
def test_json_braces_and_escaped_quotes_in_strings_do_not_end_objects(prefix):
    text = prefix + '{"evidence": "an unmatched } and quote \\\" plus {", "answer": "complete"}'
    assert parse_json_object(text) == {
        "evidence": 'an unmatched } and quote " plus {', "answer": "complete",
    }


def test_qa_trailing_comma_repair_preserves_commas_and_braces_in_strings():
    text = 'Notes: {"example": "literal comma, } and, ]", "items": ["kept",],}'
    assert parse_json_object(text, allow_trailing_commas=True) == {
        "example": "literal comma, } and, ]", "items": ["kept"],
    }
    assert parse_json_object(text) is None


def test_invalid_prose_object_does_not_hide_later_complete_result():
    assert parse_json_object('Explain {placeholder}, then {"answer": "ok"}') == {"answer": "ok"}


@pytest.mark.parametrize("text", ["[]", '[{"answer":"nested"}]', "true", "null", "123", '"string"'])
def test_nonobject_responses_do_not_escape_the_object_contract(text):
    assert parse_json_object(text) is None
