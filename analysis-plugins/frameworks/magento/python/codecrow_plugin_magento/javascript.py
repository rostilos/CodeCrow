from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class RequireJsRelation:
    kind: str
    source: str
    relation: str
    target: str
    line: int
    scope: str = ""
    position: int = 0


@dataclass(frozen=True, order=True)
class TemplateGlobalReference:
    """One exact access to a named browser global inside a PHTML script."""

    name: str
    relation: str
    line: int


@dataclass(frozen=True, order=True)
class TemplateEventReference:
    """One exact browser event dispatch or listener inside a PHTML script."""

    owner: str
    name: str
    relation: str
    line: int


_SCRIPT = re.compile(
    r"<script(?:\s[^>]*)?>(?P<body>.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
_PHP = re.compile(r"<\?(?:php|=)?.*?\?>", re.IGNORECASE | re.DOTALL)
_EVENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _key(node, source: bytes) -> str:
    value = _text(node, source).strip()
    if len(value) >= 2 and value[0] in {'"', "'"} and value[-1] == value[0]:
        return value[1:-1]
    return value


def _string(node, source: bytes) -> str:
    if node.type != "string":
        return ""
    return _key(node, source)


def _window_property(node, source: bytes) -> str:
    if node is None:
        return ""
    if node.type == "member_expression":
        owner = node.child_by_field_name("object")
        property_node = node.child_by_field_name("property")
        if (
            owner is not None
            and property_node is not None
            and _text(owner, source).strip() == "window"
        ):
            value = _text(property_node, source).strip()
            return value if value.isidentifier() else ""
    if node.type == "subscript_expression":
        owner = node.child_by_field_name("object")
        index = node.child_by_field_name("index")
        if owner is not None and _text(owner, source).strip() == "window":
            value = _string(index, source) if index is not None else ""
            return value if value.isidentifier() else ""
    return ""


def _browser_member(node, source: bytes) -> tuple[str, str]:
    if node is None:
        return "", ""
    if node.type == "member_expression":
        owner_node = node.child_by_field_name("object")
        property_node = node.child_by_field_name("property")
        if owner_node is None or property_node is None:
            return "", ""
        owner = _text(owner_node, source).strip()
        member = _text(property_node, source).strip()
    elif node.type == "subscript_expression":
        owner_node = node.child_by_field_name("object")
        index_node = node.child_by_field_name("index")
        if owner_node is None or index_node is None:
            return "", ""
        owner = _text(owner_node, source).strip()
        member = _string(index_node, source)
    else:
        return "", ""
    if owner not in {"window", "document"} or not member.isidentifier():
        return "", ""
    return owner, member


def _mask_php(content: str) -> str:
    """Preserve script offsets while removing server-side expressions."""

    return _PHP.sub(
        lambda match: "".join(
            "\n" if character == "\n" else " "
            for character in match.group(0)
        ),
        content,
    )


def extract_template_global_references(
    content: str,
) -> tuple[TemplateGlobalReference, ...]:
    """Parse direct ``window.name`` definitions and calls from PHTML scripts.

    The extractor intentionally does not treat reads, ``typeof`` guards, dynamic
    property names, or unqualified identifiers as runtime relationships.
    """

    try:
        from tree_sitter import Language, Parser
        import tree_sitter_javascript
    except ImportError as exception:
        raise RuntimeError(
            "Magento template-global analysis requires tree-sitter-javascript"
        ) from exception

    references: set[TemplateGlobalReference] = set()
    for script in _SCRIPT.finditer(content):
        body = _mask_php(script.group("body"))
        source = body.encode("utf-8")
        tree = Parser(Language(tree_sitter_javascript.language())).parse(source)
        line_offset = content.count("\n", 0, script.start("body"))
        pending = [tree.root_node]
        while pending:
            node = pending.pop()
            if node.type == "assignment_expression":
                name = _window_property(
                    node.child_by_field_name("left"),
                    source,
                )
                if name:
                    references.add(TemplateGlobalReference(
                        name,
                        "defines",
                        line_offset + node.start_point[0] + 1,
                    ))
            elif node.type == "call_expression":
                name = _window_property(
                    node.child_by_field_name("function"),
                    source,
                )
                if name:
                    references.add(TemplateGlobalReference(
                        name,
                        "calls",
                        line_offset + node.start_point[0] + 1,
                    ))
            pending.extend(node.named_children)
    return tuple(sorted(references))


def extract_template_event_references(
    content: str,
) -> tuple[TemplateEventReference, ...]:
    """Parse exact ``window``/``document`` event wiring from PHTML scripts.

    Only literal event names in direct ``addEventListener`` calls or direct
    ``dispatchEvent(new CustomEvent|Event(...))`` calls are retained. Dynamic
    names, aliases, jQuery events, element-local events, and PHP-computed
    strings remain unresolved.
    """

    try:
        from tree_sitter import Language, Parser
        import tree_sitter_javascript
    except ImportError as exception:
        raise RuntimeError(
            "Magento template-event analysis requires tree-sitter-javascript"
        ) from exception

    references: set[TemplateEventReference] = set()
    for script in _SCRIPT.finditer(content):
        body = _mask_php(script.group("body"))
        source = body.encode("utf-8")
        tree = Parser(Language(tree_sitter_javascript.language())).parse(source)
        line_offset = content.count("\n", 0, script.start("body"))
        pending = [tree.root_node]
        while pending:
            node = pending.pop()
            if node.type == "call_expression":
                owner, member = _browser_member(
                    node.child_by_field_name("function"),
                    source,
                )
                arguments = node.child_by_field_name("arguments")
                argument_nodes = (
                    arguments.named_children if arguments is not None else ()
                )
                event_name = ""
                relation = ""
                if (
                    owner
                    and member == "addEventListener"
                    and argument_nodes
                ):
                    event_name = _string(argument_nodes[0], source)
                    relation = "listens"
                elif (
                    owner
                    and member == "dispatchEvent"
                    and argument_nodes
                    and argument_nodes[0].type == "new_expression"
                ):
                    event = argument_nodes[0]
                    constructor = event.child_by_field_name("constructor")
                    event_arguments = event.child_by_field_name("arguments")
                    event_argument_nodes = (
                        event_arguments.named_children
                        if event_arguments is not None
                        else ()
                    )
                    if (
                        constructor is not None
                        and _text(constructor, source).strip()
                        in {"CustomEvent", "Event"}
                        and event_argument_nodes
                    ):
                        event_name = _string(event_argument_nodes[0], source)
                        relation = "dispatches"
                if (
                    relation
                    and _EVENT_NAME.fullmatch(event_name or "") is not None
                ):
                    references.add(TemplateEventReference(
                        owner,
                        event_name,
                        relation,
                        line_offset + node.start_point[0] + 1,
                    ))
            pending.extend(node.named_children)
    return tuple(sorted(references))


def extract_requirejs_relations(content: str) -> tuple[RequireJsRelation, ...]:
    """Parse Magento RequireJS config object relations with tree-sitter."""
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_javascript
    except ImportError as exception:
        raise RuntimeError(
            "Magento RequireJS analysis requires tree-sitter-javascript"
        ) from exception

    source = content.encode("utf-8")
    tree = Parser(Language(tree_sitter_javascript.language())).parse(source)
    relations: set[RequireJsRelation] = set()

    def visit_object(node, context: tuple[str, ...]) -> None:
        for pair in (child for child in node.named_children if child.type == "pair"):
            key_node = pair.child_by_field_name("key")
            value_node = pair.child_by_field_name("value")
            if key_node is None or value_node is None:
                continue
            key = _key(key_node, source)
            line = pair.start_point[0] + 1
            if value_node.type == "object":
                visit_object(value_node, (*context, key))
                continue
            value = _string(value_node, source)
            if context == ("paths",):
                path_values = (
                    tuple(
                        (
                            _string(candidate, source),
                            candidate.start_point[0] + 1,
                        )
                        for candidate in value_node.named_children
                    )
                    if value_node.type == "array"
                    else ((value, line),)
                )
                for position, (path_value, path_line) in enumerate(path_values):
                    if path_value:
                        relations.add(RequireJsRelation(
                            "path",
                            key,
                            "resolves-to",
                            path_value,
                            path_line,
                            position=position,
                        ))
            elif len(context) >= 2 and context[0] == "map" and value:
                relations.add(RequireJsRelation(
                    "map",
                    key,
                    "maps-to",
                    value,
                    line,
                    scope=context[1],
                ))
            elif len(context) >= 3 and context[:2] == ("config", "mixins"):
                enabled = _text(value_node, source).strip().casefold() == "true"
                relations.add(RequireJsRelation(
                    "mixin",
                    context[2],
                    "mixed-by" if enabled else "disables-mixin",
                    key,
                    line,
                ))
            elif (
                context == ()
                and key == "deps"
                and value_node.type == "array"
            ):
                for position, dependency in enumerate(value_node.named_children):
                    dependency_name = _string(dependency, source)
                    if dependency_name:
                        relations.add(RequireJsRelation(
                            "dependency",
                            "requirejs-config",
                            "loads",
                            dependency_name,
                            dependency.start_point[0] + 1,
                            position=position,
                        ))
            elif (
                context == ("shim",)
                and value_node.type == "array"
            ):
                for position, dependency in enumerate(value_node.named_children):
                    dependency_name = _string(dependency, source)
                    if dependency_name:
                        relations.add(RequireJsRelation(
                            "shim",
                            key,
                            "depends-on",
                            dependency_name,
                            dependency.start_point[0] + 1,
                            position=position,
                        ))
            elif (
                len(context) >= 2
                and context[0] == "shim"
                and key == "deps"
                and value_node.type == "array"
            ):
                for position, dependency in enumerate(value_node.named_children):
                    dependency_name = _string(dependency, source)
                    if dependency_name:
                        relations.add(RequireJsRelation(
                            "shim",
                            context[1],
                            "depends-on",
                            dependency_name,
                            dependency.start_point[0] + 1,
                            position=position,
                        ))

    for node in tree.root_node.named_children:
        candidates = [node]
        while candidates:
            candidate = candidates.pop()
            if candidate.type == "variable_declarator":
                name = candidate.child_by_field_name("name")
                value = candidate.child_by_field_name("value")
                if name is not None and _text(name, source) == "config" and value is not None:
                    if value.type == "object":
                        visit_object(value, ())
            candidates.extend(candidate.named_children)

    return tuple(sorted(relations))
