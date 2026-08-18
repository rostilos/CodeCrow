from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping


_TOKEN = re.compile(
    r"(?P<space>\s+)"
    r"|(?P<comment>\#[^\r\n]*)"
    r"|(?P<block>\"\"\"(?:.|\n)*?\"\"\")"
    r"|(?P<template>`(?:\\.|[^`\\])*`)"
    r"|(?P<single>'(?:\\.|[^'\\])*')"
    r"|(?P<string>\"(?:\\.|[^\"\\])*\")"
    r"|(?P<number>-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<spread>\.\.\.)"
    r"|(?P<punct>[!$():=@\[\]{|}&,])",
    re.DOTALL,
)
_GRAPHQL_SCRIPT = re.compile(
    r"<script\b[^>]*\btype\s*=\s*['\"]application/(?:graphql|gql)['\"][^>]*>"
    r"(?P<body>.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
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


@dataclass(frozen=True)
class _RawSelection:
    segments: tuple[str, ...]
    line: int


@dataclass(frozen=True)
class _FragmentSpread:
    name: str
    prefix: tuple[str, ...]
    line: int


def _lexemes(content: str, line_offset: int = 0) -> tuple[_Lexeme, ...]:
    result: list[_Lexeme] = []
    line = 1 + line_offset
    cursor = 0
    for match in _TOKEN.finditer(content):
        line += content.count("\n", cursor, match.start())
        result.append(_Lexeme(
            match.group(0),
            line,
            match.lastgroup or "",
        ))
        line += match.group(0).count("\n")
        cursor = match.end()
    return tuple(result)


def _tokens(content: str, line_offset: int = 0) -> tuple[_Lexeme, ...]:
    return tuple(
        token
        for token in _lexemes(content, line_offset)
        if token.kind not in {
            "space", "comment", "block", "template", "single",
        }
    )


def _skip_balanced(
    tokens: tuple[_Lexeme, ...],
    index: int,
    opening: str,
    closing: str,
) -> int:
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


def _directive_value(
    tokens: tuple[_Lexeme, ...],
    index: int,
) -> tuple[str, int]:
    token = tokens[index]
    if token.value in {"[", "{"}:
        closing = "]" if token.value == "[" else "}"
        end = _skip_balanced(tokens, index, token.value, closing)
        return "".join(item.value for item in tokens[index:end]), end
    raw = token.value
    if token.kind == "string":
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = raw[1:-1]
    return str(raw), index + 1


def _directives(
    tokens: tuple[_Lexeme, ...],
    index: int,
) -> tuple[tuple[GraphqlDirective, ...], int]:
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
                raw, index = _directive_value(tokens, index)
                arguments.append((key, raw))
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


def parse_schema_root_types(content: str) -> tuple[tuple[str, str], ...]:
    tokens = _tokens(content)
    roots: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        if tokens[index].value == "extend":
            index += 1
        if index >= len(tokens) or tokens[index].value != "schema":
            index += 1
            continue
        index += 1
        _, index = _directives(tokens, index)
        if index >= len(tokens) or tokens[index].value != "{":
            continue
        index += 1
        while index < len(tokens) and tokens[index].value != "}":
            operation = tokens[index].value
            index += 1
            if operation not in {"query", "mutation", "subscription"}:
                continue
            if index >= len(tokens) or tokens[index].value != ":":
                continue
            index += 1
            if index >= len(tokens) or tokens[index].kind != "name":
                continue
            roots[operation] = tokens[index].value
            index += 1
        if index < len(tokens) and tokens[index].value == "}":
            index += 1
    return tuple(sorted(roots.items()))


def _selection_set(
    tokens: tuple[_Lexeme, ...],
    index: int,
    prefix: tuple[str, ...] = (),
) -> tuple[list[_RawSelection], list[_FragmentSpread], int]:
    selections: list[_RawSelection] = []
    spreads: list[_FragmentSpread] = []
    if index >= len(tokens) or tokens[index].value != "{":
        return selections, spreads, index
    index += 1
    while index < len(tokens) and tokens[index].value != "}":
        if tokens[index].value == "...":
            spread_line = tokens[index].line
            index += 1
            if index < len(tokens) and tokens[index].value == "on":
                index += 1
                if index < len(tokens) and tokens[index].kind == "name":
                    index += 1
                _, index = _directives(tokens, index)
                nested, nested_spreads, index = _selection_set(
                    tokens, index, prefix,
                )
                selections.extend(nested)
                spreads.extend(nested_spreads)
            elif index < len(tokens) and tokens[index].kind == "name":
                name = tokens[index].value
                index += 1
                _, index = _directives(tokens, index)
                spreads.append(_FragmentSpread(name, prefix, spread_line))
            else:
                index += 1
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
        selections.append(_RawSelection(segments, field.line))
        if index < len(tokens) and tokens[index].value == "{":
            nested, nested_spreads, index = _selection_set(
                tokens, index, segments,
            )
            selections.extend(nested)
            spreads.extend(nested_spreads)
    if index < len(tokens) and tokens[index].value == "}":
        index += 1
    return selections, spreads, index


def _fragment_definitions(
    tokens: tuple[_Lexeme, ...],
) -> dict[str, tuple[list[_RawSelection], list[_FragmentSpread]]]:
    result: dict[str, tuple[list[_RawSelection], list[_FragmentSpread]]] = {}
    index = 0
    while index < len(tokens):
        if tokens[index].value != "fragment":
            index += 1
            continue
        index += 1
        if index >= len(tokens) or tokens[index].kind != "name":
            continue
        name = tokens[index].value
        index += 1
        if index < len(tokens) and tokens[index].value == "on":
            index += 1
            if index < len(tokens) and tokens[index].kind == "name":
                index += 1
        _, index = _directives(tokens, index)
        selections, spreads, index = _selection_set(tokens, index)
        result[name] = (selections, spreads)
    return result


def _expanded_selections(
    root: str,
    selections: list[_RawSelection],
    spreads: list[_FragmentSpread],
    fragments: Mapping[
        str,
        tuple[list[_RawSelection], list[_FragmentSpread]],
    ],
    *,
    base: tuple[str, ...] = (),
    seen: frozenset[str] = frozenset(),
) -> set[GraphqlSelection]:
    result = {
        GraphqlSelection(root, (*base, *selection.segments), selection.line)
        for selection in selections
    }
    for spread in spreads:
        if spread.name in seen or spread.name not in fragments:
            continue
        fragment_selections, fragment_spreads = fragments[spread.name]
        result.update(_expanded_selections(
            root,
            fragment_selections,
            fragment_spreads,
            fragments,
            base=(*base, *spread.prefix),
            seen=seen | {spread.name},
        ))
    return result


def _parse_operation_source(
    content: str,
    line_offset: int = 0,
    root_types: Mapping[str, str] | None = None,
) -> set[GraphqlSelection]:
    tokens = _tokens(content, line_offset)
    if not tokens:
        return set()
    roots = {
        "query": "Query",
        "mutation": "Mutation",
        "subscription": "Subscription",
        **dict(root_types or {}),
    }
    fragments = _fragment_definitions(tokens)
    result: set[GraphqlSelection] = set()

    if tokens[0].value == "{":
        selections, spreads, _ = _selection_set(tokens, 0)
        result.update(_expanded_selections(
            roots["query"], selections, spreads, fragments,
        ))
        return result

    index = 0
    while index < len(tokens):
        operation = tokens[index].value
        if operation not in roots:
            index += 1
            continue
        if index and tokens[index - 1].kind == "name":
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
        selections, spreads, index = _selection_set(tokens, index)
        result.update(_expanded_selections(
            roots[operation], selections, spreads, fragments,
        ))
    return result


def _embedded_source(token: _Lexeme) -> str | None:
    raw = token.value
    if token.kind == "block":
        return raw[3:-3]
    if token.kind in {"template", "single"}:
        return raw[1:-1]
    if token.kind == "string":
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, str) else None
    return None


def parse_operations(
    content: str,
    *,
    embedded_only: bool = False,
    root_types: Mapping[str, str] | None = None,
) -> tuple[GraphqlSelection, ...]:
    result = (
        set()
        if embedded_only
        else _parse_operation_source(content, root_types=root_types)
    )
    for token in _lexemes(content):
        if token.kind not in {"block", "template", "single", "string"}:
            continue
        embedded = _embedded_source(token)
        if not embedded or not (
            embedded.lstrip().startswith("{")
            or re.search(r"\b(?:query|mutation|subscription)\b", embedded)
        ):
            continue
        result.update(_parse_operation_source(
            embedded,
            token.line - 1,
            root_types,
        ))
    for match in _GRAPHQL_SCRIPT.finditer(content):
        result.update(_parse_operation_source(
            match.group("body"),
            content.count("\n", 0, match.start("body")),
            root_types,
        ))
    return tuple(sorted(
        result,
        key=lambda item: (item.root, item.segments, item.line),
    ))
