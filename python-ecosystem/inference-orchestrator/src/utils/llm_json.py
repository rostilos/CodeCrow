"""Parse complete JSON objects from model text without altering quoted content."""
import json
import re
from collections.abc import Iterator
from typing import Any


_FENCES = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)


def _object_spans(text: str) -> Iterator[str]:
    start = None
    depth = 0
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if start is None:
            if character != "{":
                continue
            start = index
            depth = 1
            continue
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                yield text[start:index + 1]
                start = None


def extract_json_object(text: str) -> str | None:
    """Return the first balanced object, ignoring braces within JSON strings."""
    return next(_object_spans(text), None)


def remove_trailing_commas(text: str) -> str:
    # Regex replacement corrupts legitimate strings such as "example, }".
    # Only discard a comma outside a string when its next token closes a value.
    result = []
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character == ",":
            following = index + 1
            while following < len(text) and text[following].isspace():
                following += 1
            if following < len(text) and text[following] in "}]":
                continue
        result.append(character)
    return "".join(result)


def parse_json_object(text: str | None, *, allow_trailing_commas: bool = False) -> dict[str, Any] | None:
    """Accept direct objects, fenced objects, and objects within prose.

    Arrays/scalars are not objects. Return None so callers can preserve complete
    raw text or use their existing incomplete-result recovery.
    """
    if not text:
        return None

    def parse(candidate: str) -> dict[str, Any] | None:
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            if not allow_trailing_commas:
                return None
            try:
                value = json.loads(remove_trailing_commas(candidate))
            except (json.JSONDecodeError, TypeError):
                return None
        return value if isinstance(value, dict) else None

    # A complete scalar/array is an intentional JSON value, not prose around
    # an object. Do not silently recover a nested item from an array.
    try:
        complete = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    else:
        return complete if isinstance(complete, dict) else None

    result = parse(text)
    if result is not None:
        return result
    for match in _FENCES.finditer(text):
        result = parse(match.group(1).strip())
        if result is not None:
            return result
    for candidate in _object_spans(text):
        result = parse(candidate)
        if result is not None:
            return result
    return None
