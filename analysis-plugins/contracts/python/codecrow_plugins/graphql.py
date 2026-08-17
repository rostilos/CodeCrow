from __future__ import annotations

import json
import re
from dataclasses import dataclass


_TOKEN = re.compile(
    r"(?P<space>\s+)"
    r"|(?P<comment>\#[^\r\n]*)"
    r"|(?P<block>\"\"\"(?:.|\n)*?\"\"\")"
    r"|(?P<string>\"(?:\\.|[^\"\\])*\")"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<spread>\.\.\.)"
    r"|(?P<punct>[!$():=@\[\]{|}&,])",
    re.DOTALL,
)


@dataclass(frozen=True)
class GraphqlDirective:
    name: str
    arguments: tuple[tuple[str, str], ...] = ()

    def argument(self, name: str) -> str | None:
        return dict(self.arguments).get(name)


@dataclass(frozen=True)
class GraphqlField:
    owner: str
    name: str
    target_type: str
    line: int
    directives: tuple[GraphqlDirective, ...] = ()


@dataclass(frozen=True)
class GraphqlType:
    kind: str
    name: str
    line: int
    directives: tuple[GraphqlDirective, ...] = ()
    fields: tuple[GraphqlField, ...] = ()


@dataclass(frozen=True)
class GraphqlSelection:
    root: str
    segments: tuple[str, ...]
    line: int


@dataclass(frozen=True)
class _Lexeme:
    value: str
    line: int
    kind: str


def _tokens(content: str, line_offset: int = 0) -> tuple[_Lexeme, ...]:
    return tuple(
        _Lexeme(
            match.group(0),
            content.count("\n", 0, match.start()) + 1 + line_offset,
            match.lastgroup or "",
        )
        for match in _TOKEN.finditer(content)
        if match.lastgroup not in {"space", "comment", "block"}
        and match.group(0) != ","
    )


def _skip_balanced(tokens: tuple[_Lexeme, ...], index: int, opening: str, closing: str) -> int:
    if index >= len(tokens) or tokens[index].value != opening:
        return index
    depth = 0
    while index < len(tokens):
        depth += tokens[index].value == opening
        depth -= tokens[index].value == closing
        index += 1
        if depth == 0:
            return index
    return index


def _directives(tokens: tuple[_Lexeme, ...], index: int) -> tuple[tuple[GraphqlDirective, ...], int]:
    result: list[GraphqlDirective] = []
    while index < len(tokens) and tokens[index].value == "@":
        index += 1
        if index >= len(tokens) or tokens[index].kind != "name":
            break
        name = tokens[index].value
        index += 1
        arguments: list[tuple[str, str]] = []
        if index < len(tokens) and tokens[index].value == "(":
            index += 1
            while index < len(tokens) and tokens[index].value != ")":
                if tokens[index].kind != "name":
                    index += 1
                    continue
                key = tokens[index].value
                index += 1
                if index >= len(tokens) or tokens[index].value != ":":
                    continue
                index += 1
                if index >= len(tokens):
                    break
                raw = tokens[index].value
                if tokens[index].kind == "string":
                    try:
                        raw = json.loads(raw)
                    except ValueError:
                        raw = raw[1:-1]
                arguments.append((key, str(raw)))
                index += 1
                if index < len(tokens) and tokens[index].value in {"[", "{"}:
                    opening = tokens[index].value
                    index = _skip_balanced(
                        tokens, index, opening, "]" if opening == "[" else "}"
                    )
            if index < len(tokens) and tokens[index].value == ")":
                index += 1
        result.append(GraphqlDirective(name, tuple(sorted(arguments))))
    return tuple(result), index


def parse_schema(content: str) -> tuple[GraphqlType, ...]:
    tokens = _tokens(content)
    definitions: list[GraphqlType] = []
    index = 0
    kinds = {"type", "interface", "input", "enum", "union", "scalar"}
    while index < len(tokens):
        if tokens[index].value == "extend":
            index += 1
        if index >= len(tokens) or tokens[index].value not in kinds:
            index += 1
            continue
        kind_token = tokens[index]
        kind = kind_token.value
        index += 1
        if index >= len(tokens) or tokens[index].kind != "name":
            continue
        name = tokens[index].value
        line = tokens[index].line
        index += 1
        while index < len(tokens) and tokens[index].value not in {"@", "{"}:
            if tokens[index].value in kinds or tokens[index].value == "extend":
                break
            index += 1
        directives, index = _directives(tokens, index)
        fields: list[GraphqlField] = []
        if index < len(tokens) and tokens[index].value == "{":
            index += 1
            while index < len(tokens) and tokens[index].value != "}":
                if tokens[index].kind == "string":
                    index += 1
                    continue
                if tokens[index].kind != "name":
                    index += 1
                    continue
                field_token = tokens[index]
                index += 1
                if index < len(tokens) and tokens[index].value == "(":
                    index = _skip_balanced(tokens, index, "(", ")")
                if index >= len(tokens) or tokens[index].value != ":":
                    continue
                index += 1
                while index < len(tokens) and tokens[index].value in {"[", "]", "!"}:
                    index += 1
                if index >= len(tokens) or tokens[index].kind != "name":
                    continue
                target_type = tokens[index].value
                index += 1
                while index < len(tokens) and tokens[index].value in {"[", "]", "!"}:
                    index += 1
                field_directives, index = _directives(tokens, index)
                fields.append(GraphqlField(
                    name,
                    field_token.value,
                    target_type,
                    field_token.line,
                    field_directives,
                ))
            if index < len(tokens) and tokens[index].value == "}":
                index += 1
        definitions.append(GraphqlType(
            kind,
            name,
            line,
            directives,
            tuple(fields),
        ))
    return tuple(definitions)


def _selection_set(
    tokens: tuple[_Lexeme, ...],
    index: int,
    root: str,
    prefix: tuple[str, ...] = (),
) -> tuple[list[GraphqlSelection], int]:
    selections: list[GraphqlSelection] = []
    if index >= len(tokens) or tokens[index].value != "{":
        return selections, index
    index += 1
    while index < len(tokens) and tokens[index].value != "}":
        if tokens[index].value == "...":
            index += 1
            while index < len(tokens) and tokens[index].value not in {"{", "}"}:
                index += 1
            if index < len(tokens) and tokens[index].value == "{":
                _, index = _selection_set(tokens, index, root, prefix)
            continue
        if tokens[index].kind != "name":
            index += 1
            continue
        field = tokens[index]
        index += 1
        if index < len(tokens) and tokens[index].value == ":":
            index += 1
            if index >= len(tokens) or tokens[index].kind != "name":
                continue
            field = tokens[index]
            index += 1
        if index < len(tokens) and tokens[index].value == "(":
            index = _skip_balanced(tokens, index, "(", ")")
        _, index = _directives(tokens, index)
        segments = (*prefix, field.value)
        selections.append(GraphqlSelection(root, segments, field.line))
        if index < len(tokens) and tokens[index].value == "{":
            nested, index = _selection_set(tokens, index, root, segments)
            selections.extend(nested)
    if index < len(tokens) and tokens[index].value == "}":
        index += 1
    return selections, index


def _parse_operation_source(
    content: str,
    line_offset: int = 0,
) -> set[GraphqlSelection]:
    tokens = _tokens(content, line_offset)
    result: set[GraphqlSelection] = set()
    roots = {"query": "Query", "mutation": "Mutation", "subscription": "Subscription"}
    index = 0
    while index < len(tokens):
        operation = tokens[index].value
        if operation not in roots:
            index += 1
            continue
        index += 1
        if index < len(tokens) and tokens[index].kind == "name":
            index += 1
        if index < len(tokens) and tokens[index].value == "(":
            index = _skip_balanced(tokens, index, "(", ")")
        _, index = _directives(tokens, index)
        if index >= len(tokens) or tokens[index].value != "{":
            continue
        selections, index = _selection_set(tokens, index, roots[operation])
        result.update(selections)
    return result


def parse_operations(content: str) -> tuple[GraphqlSelection, ...]:
    result = _parse_operation_source(content)
    for match in _TOKEN.finditer(content):
        kind = match.lastgroup
        if kind not in {"block", "string"}:
            continue
        raw = match.group(0)
        if kind == "block":
            embedded = raw[3:-3]
        else:
            try:
                embedded = json.loads(raw)
            except ValueError:
                continue
        if not isinstance(embedded, str) or not re.search(
            r"\b(?:query|mutation|subscription)\b",
            embedded,
        ):
            continue
        line_offset = content.count("\n", 0, match.start())
        result.update(_parse_operation_source(embedded, line_offset))
    return tuple(sorted(result, key=lambda item: (item.root, item.segments, item.line)))
