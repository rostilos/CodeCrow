"""Provider output recovery without extra paid requests."""
import json
from types import SimpleNamespace

import pytest

from service.review.model_calls import parse_object


@pytest.mark.parametrize("prefix,suffix", [
    ("", ""),
    ("The related changes form one contract.\n\n", "\nBoth changed paths are assigned."),
])
@pytest.mark.parametrize("blocks", [False, True])
def test_fenced_final_object_survives_explanatory_prose(prefix, suffix, blocks):
    expected = {"groups": [{"paths": ["producer.py", "consumer.py"], "focus": "Shared contract"}]}
    text = prefix + "```json\n" + json.dumps(expected) + "\n```" + suffix
    response = SimpleNamespace(content=[{"type": "text", "text": text}] if blocks else text)
    assert parse_object(response) == expected


def test_plain_json_and_final_corrected_fence():
    assert parse_object('{"findings": []}') == {"findings": []}
    assert parse_object('```json\n{"groups": []}\n```\nCorrection:\n```JSON\n{"groups": [{"paths": ["a.py"]}]}\n```') == {
        "groups": [{"paths": ["a.py"]}],
    }


@pytest.mark.parametrize("text", [
    "The model did not produce an object.",
    '```json\n{"groups": [{"paths": ["a.py"]}]\n```',
    '```python\n{"groups": []}\n```',
    '```json\n[]\n```',
])
def test_invalid_output_is_not_reconstructed_from_inner_objects(text):
    with pytest.raises(ValueError):
        parse_object(text)
