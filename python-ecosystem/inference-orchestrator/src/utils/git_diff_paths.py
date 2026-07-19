"""Parse paths rendered by Git in unified-diff metadata.

Git uses either ordinary ``a/path b/path`` tokens or C-style quoted tokens.
Quoted non-ASCII bytes are commonly emitted as three-digit octal escapes and
must be decoded as UTF-8 only after the complete token has been read.  This
module intentionally mirrors the exact-inventory parser at the Java boundary
so downstream review components cannot disagree about a valid path.
"""

from __future__ import annotations


class GitDiffPathError(ValueError):
    """A Git diff path token is malformed or cannot be decoded safely."""


_SIMPLE_ESCAPES = {
    "a": 0x07,
    "b": 0x08,
    "t": 0x09,
    "n": 0x0A,
    "v": 0x0B,
    "f": 0x0C,
    "r": 0x0D,
    '"': 0x22,
    "\\": 0x5C,
}


def _parse_quoted_token(value: str, opening_quote: int = 0) -> tuple[str, int]:
    if opening_quote >= len(value) or value[opening_quote] != '"':
        raise GitDiffPathError("quoted Git path must start with a double quote")

    encoded = bytearray()
    offset = opening_quote + 1
    while offset < len(value):
        current = value[offset]
        if current == '"':
            try:
                return bytes(encoded).decode("utf-8", errors="strict"), offset + 1
            except UnicodeDecodeError as error:
                raise GitDiffPathError(
                    "quoted Git path is not valid UTF-8"
                ) from error
        if current != "\\":
            encoded.extend(current.encode("utf-8"))
            offset += 1
            continue

        offset += 1
        if offset >= len(value):
            raise GitDiffPathError("unterminated escape in quoted Git path")
        escaped = value[offset]
        if "0" <= escaped <= "7":
            octal = 0
            digits = 0
            while (
                offset < len(value)
                and digits < 3
                and "0" <= value[offset] <= "7"
            ):
                octal = octal * 8 + ord(value[offset]) - ord("0")
                offset += 1
                digits += 1
            encoded.append(octal & 0xFF)
            continue

        decoded = _SIMPLE_ESCAPES.get(escaped)
        if decoded is None:
            raise GitDiffPathError(
                f"unsupported escape in quoted Git path: \\{escaped}"
            )
        encoded.append(decoded)
        offset += 1

    raise GitDiffPathError("unterminated quoted Git path")


def _parse_header_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    offset = 0
    while offset < len(value):
        while offset < len(value) and value[offset].isspace():
            offset += 1
        if offset == len(value):
            break
        if value[offset] == '"':
            token, offset = _parse_quoted_token(value, offset)
            tokens.append(token)
            continue
        end = offset
        while end < len(value) and not value[end].isspace():
            end += 1
        tokens.append(value[offset:end])
        offset = end
    return tokens


def _normalize_path(path: str, side_prefix: str) -> str | None:
    if path == "/dev/null":
        return None
    return path[len(side_prefix) :] if path.startswith(side_prefix) else path


def parse_git_diff_header(line: str) -> tuple[str | None, str | None]:
    """Return decoded old/new paths from one ``diff --git`` header."""

    marker = "diff --git "
    if not isinstance(line, str) or not line.startswith(marker):
        raise GitDiffPathError("missing diff --git marker")
    rendered = line[len(marker) :]
    if not rendered.startswith('"') and rendered.startswith("a/"):
        boundary = rendered.rfind(" b/")
        tokens = (
            [rendered[:boundary], rendered[boundary + 1 :]]
            if boundary >= 0
            else _parse_header_tokens(rendered)
        )
    else:
        tokens = _parse_header_tokens(rendered)
    if len(tokens) != 2:
        raise GitDiffPathError("diff --git header must contain exactly two paths")
    old_path = _normalize_path(tokens[0], "a/")
    new_path = _normalize_path(tokens[1], "b/")
    if old_path is None and new_path is None:
        raise GitDiffPathError("both diff paths resolve to /dev/null")
    return old_path, new_path


def parse_git_marker_path(line: str, marker: str) -> str | None:
    """Return a decoded path from a ``---`` or ``+++`` marker line."""

    if marker not in {"---", "+++"}:
        raise ValueError("marker must be --- or +++")
    prefix = marker + " "
    if not isinstance(line, str) or not line.startswith(prefix):
        raise GitDiffPathError(f"missing {marker} marker")
    candidate = line[len(prefix) :].lstrip()
    if candidate.startswith('"'):
        path, _end = _parse_quoted_token(candidate)
    else:
        path = candidate.split("\t", 1)[0]
    return _normalize_path(path, "a/" if marker == "---" else "b/")
