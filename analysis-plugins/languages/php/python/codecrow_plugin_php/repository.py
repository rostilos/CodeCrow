from __future__ import annotations

import re
import base64
import gzip
import json
import logging
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from codecrow_plugins import (
    ArchitecturePacket,
    FileArtifact,
    GraphFact,
    PluginOutcome,
    RepositoryAnalysis,
    RepositorySnapshot,
    SymbolDefinition,
)


_BUILTIN_TYPES = {
    "array", "bool", "callable", "false", "float", "int", "iterable",
    "implements", "extends", "mixed", "never", "null", "object", "parent",
    "resource", "self", "static", "string", "true", "void",
}
_TYPE_TOKEN = re.compile(r"\\?[A-Za-z_][A-Za-z0-9_\\]*")
_DECLARATION_HINT = re.compile(
    r"\b(?:class|interface|trait|enum)\s+[A-Za-z_][A-Za-z0-9_]*"
)
_CONSTRUCTION_REFERENCE = "php-construction-reference:"
_STATIC_CALL_REFERENCE = "php-static-call-reference:"
_INSTANCE_CALL_REFERENCE = "php-instance-call-reference:"
_CHAINED_INSTANCE_CALL_REFERENCE = "php-chained-instance-call-reference:"
_LITERAL_INSTANCE_CALL_REFERENCE = "php-literal-instance-call-reference:"
_CLASS_CONSTANT_DECLARATION = "php-class-constant:"
_CLASS_CONSTANT_REFERENCE = "php-class-constant-reference:"
logger = logging.getLogger(__name__)
_THREAD_LOCAL = threading.local()


def _thread_parser() -> "PhpAstParser":
    parser = getattr(_THREAD_LOCAL, "php_parser", None)
    if parser is None:
        parser = PhpAstParser()
        _THREAD_LOCAL.php_parser = parser
    return parser


def _parse_artifact(artifact: FileArtifact) -> tuple[SymbolDefinition, ...]:
    return _thread_parser().parse(artifact)


def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _nearest(node, node_types: set[str]):
    parent = node.parent
    while parent is not None:
        if parent.type in node_types:
            return parent
        parent = parent.parent
    return None


def _nested_scope_between(node, stop, node_types: set[str]) -> bool:
    parent = node.parent
    while parent is not None and parent != stop:
        if parent.type in node_types:
            return True
        parent = parent.parent
    return False


def _type_tokens(value: str) -> tuple[str, ...]:
    return tuple(sorted({
        token
        for token in _TYPE_TOKEN.findall(value)
        if token.casefold() not in _BUILTIN_TYPES
    }))


def _ordered_type_tokens(value: str) -> tuple[str, ...]:
    """Preserve declaration order where PHP runtime relation order is semantic."""
    seen: set[str] = set()
    result: list[str] = []
    for token in _TYPE_TOKEN.findall(value):
        if token.casefold() in _BUILTIN_TYPES or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return tuple(result)


def _resolved_declared_type(
    value: str,
    namespace: str,
    imports: dict[str, str],
    resolve_type,
) -> str:
    """Resolve named members of one exact PHP declaration type.

    Union/intersection/nullable syntax and built-in types are retained. Named
    types are resolved through the declaration's namespace/import table. This
    never infers a runtime type from a returned expression or docblock.
    """
    compact = re.sub(r"\s+", "", value or "")
    if not compact:
        return ""

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        normalized = token.lstrip("\\")
        if normalized.casefold() in _BUILTIN_TYPES:
            return normalized.casefold()
        return resolve_type(token, namespace, imports)

    return _TYPE_TOKEN.sub(replace, compact)


def _decode_reference(value: str) -> tuple[int, str, str, str, str]:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exception:
        raise ValueError("PHP code reference snapshot contains invalid JSON") from exception
    if not isinstance(payload, dict):
        raise ValueError("PHP code reference snapshot must be an object")
    line_number = payload.get("line")
    target = payload.get("target")
    method = payload.get("method", "")
    caller = payload.get("caller", "")
    receiver_resolution = payload.get("receiverResolution", "")
    if not isinstance(line_number, int) or line_number < 1:
        raise ValueError("PHP code reference snapshot has an invalid line")
    if not isinstance(target, str) or not target:
        raise ValueError("PHP code reference snapshot has an invalid target")
    if (
        not isinstance(method, str)
        or not isinstance(caller, str)
        or not isinstance(receiver_resolution, str)
    ):
        raise ValueError("PHP code reference snapshot has invalid callable names")
    return line_number, target, method, caller, receiver_resolution


def _decode_chained_reference(
    value: str,
) -> tuple[int, str, tuple[str, ...], str, str, str]:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exception:
        raise ValueError(
            "PHP chained call reference snapshot contains invalid JSON"
        ) from exception
    if not isinstance(payload, dict):
        raise ValueError("PHP chained call reference snapshot must be an object")
    line_number = payload.get("line")
    target = payload.get("target")
    via_methods = payload.get("viaMethods")
    method = payload.get("method")
    caller = payload.get("caller", "")
    receiver_resolution = payload.get("receiverResolution", "")
    if not isinstance(line_number, int) or line_number < 1:
        raise ValueError("PHP chained call reference snapshot has an invalid line")
    if not isinstance(target, str) or not target:
        raise ValueError("PHP chained call reference snapshot has an invalid target")
    if (
        not isinstance(via_methods, list)
        or not via_methods
        or any(not isinstance(item, str) or not item for item in via_methods)
    ):
        raise ValueError(
            "PHP chained call reference snapshot has invalid intermediate methods"
        )
    if (
        not isinstance(method, str)
        or not method
        or not isinstance(caller, str)
        or not isinstance(receiver_resolution, str)
    ):
        raise ValueError("PHP chained call reference snapshot has invalid call names")
    return (
        line_number,
        target,
        tuple(via_methods),
        method,
        caller,
        receiver_resolution,
    )


class PhpAstParser:
    """Tree-sitter backed PHP semantic extractor used by repository plugins."""

    DECLARATIONS = {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "trait_declaration": "trait",
        "enum_declaration": "enum",
    }

    def __init__(self) -> None:
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_php
        except ImportError as exception:
            raise RuntimeError("PHP repository analysis requires tree-sitter-php") from exception
        self._parser = Parser(Language(tree_sitter_php.language_php()))

    def parse(self, artifact: FileArtifact) -> tuple[SymbolDefinition, ...]:
        source = artifact.content.encode("utf-8")
        tree = self._parser.parse(source)
        root = tree.root_node
        symbols: list[SymbolDefinition] = []
        for node in _walk(root):
            kind = self.DECLARATIONS.get(node.type)
            if kind is None:
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            namespace = self._namespace_for(node, source)
            imports = self._imports_for(node, source)
            name = _text(name_node, source).strip()
            qualified_name = f"{namespace}\\{name}" if namespace else name
            parents, parent_attributes = self._parents(
                node,
                source,
                namespace,
                imports,
                kind,
            )
            methods, constructor_types, method_attributes = self._methods(
                node,
                source,
                namespace,
                imports,
            )
            trait_attributes = self._traits(
                node,
                source,
                namespace,
                imports,
            )
            reference_attributes = self._code_references(
                node,
                source,
                namespace,
                imports,
                qualified_name,
                next(
                    (
                        value
                        for key, value in parent_attributes
                        if key == "php-parent-class"
                    ),
                    "",
                ),
            )
            constant_attributes = self._class_constants(
                node,
                source,
                namespace,
                imports,
                qualified_name,
                next(
                    (
                        value
                        for key, value in parent_attributes
                        if key == "php-parent-class"
                    ),
                    "",
                ),
            )
            type_attributes = {
                (f"type:{modifier}", "true")
                for modifier in self._modifiers(node, source)
            }
            type_attributes.update(parent_attributes)
            type_attributes.update(trait_attributes)
            type_attributes.update(reference_attributes)
            type_attributes.update(constant_attributes)
            symbols.append(SymbolDefinition(
                qualified_name=qualified_name.lstrip("\\"),
                kind=kind,
                path=artifact.path,
                line=node.start_point[0] + 1,
                parents=parents,
                methods=methods,
                constructor_types=constructor_types,
                attributes=tuple(sorted(type_attributes | method_attributes)),
            ))
        return tuple(sorted(symbols))

    def _namespace_for(self, declaration, source: bytes) -> str:
        namespace_node = _nearest(declaration, {"namespace_definition"})
        candidates = namespace_node.children if namespace_node is not None else declaration.parent.children
        for node in candidates:
            if node.type != "namespace_definition":
                continue
            if namespace_node is not None and node != namespace_node:
                continue
            name = next((child for child in node.children if child.type == "namespace_name"), None)
            return _text(name, source).strip() if name is not None else ""
        # Unbracketed namespaces are siblings of declarations.
        root = declaration
        while root.parent is not None:
            root = root.parent
        namespace = ""
        for node in root.children:
            if node.start_byte >= declaration.start_byte:
                break
            if node.type == "namespace_definition":
                name = next((child for child in node.children if child.type == "namespace_name"), None)
                namespace = _text(name, source).strip() if name is not None else ""
        return namespace

    def _imports_for(self, declaration, source: bytes) -> dict[str, str]:
        scope = _nearest(declaration, {"namespace_definition"})
        root = scope if scope is not None else declaration
        while scope is None and root.parent is not None:
            root = root.parent
        imports: dict[str, str] = {}
        for node in _walk(root):
            if node.type != "namespace_use_clause" or node.start_byte >= declaration.start_byte:
                continue
            clause = _text(node, source).strip()
            if "{" in clause and "}" in clause:
                prefix, members = clause.split("{", 1)
                prefix = prefix.rstrip("\\")
                for member in members.rsplit("}", 1)[0].split(","):
                    self._record_import(f"{prefix}\\{member.strip()}", imports)
            else:
                self._record_import(clause, imports)
        return imports

    @staticmethod
    def _record_import(clause: str, imports: dict[str, str]) -> None:
        parts = re.split(r"\s+as\s+", clause.strip(), flags=re.IGNORECASE)
        target = parts[0].strip().lstrip("\\")
        if not target or target.casefold().startswith(("function ", "const ")):
            return
        alias = parts[1].strip() if len(parts) > 1 else target.rsplit("\\", 1)[-1]
        imports[alias.casefold()] = target

    def _parents(
        self,
        declaration,
        source: bytes,
        namespace: str,
        imports: dict[str, str],
        declaration_kind: str,
    ) -> tuple[tuple[str, ...], set[tuple[str, str]]]:
        parents: set[str] = set()
        attributes: set[tuple[str, str]] = set()
        for child in declaration.children:
            if child.type not in {"base_clause", "class_interface_clause"}:
                continue
            resolved = tuple(
                self._resolve_type(token, namespace, imports)
                for token in _ordered_type_tokens(_text(child, source))
            )
            parents.update(resolved)
            if child.type == "base_clause" and declaration_kind == "class":
                if resolved:
                    attributes.add(("php-parent-class", resolved[0]))
                continue
            relation = (
                "php-parent-interface"
                if declaration_kind == "interface"
                else "php-interface"
            )
            for index, target in enumerate(resolved):
                attributes.add((f"{relation}:{index:04d}", target))
        return tuple(sorted(parents)), attributes

    def _methods(
        self,
        declaration,
        source: bytes,
        namespace: str,
        imports: dict[str, str],
    ) -> tuple[
        tuple[str, ...],
        tuple[str, ...],
        set[tuple[str, str]],
    ]:
        methods: set[str] = set()
        constructor_types: set[str] = set()
        attributes: set[tuple[str, str]] = set()
        for node in _walk(declaration):
            if node.type != "method_declaration":
                continue
            if _nearest(node, set(self.DECLARATIONS)) != declaration:
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            method_name = _text(name_node, source).strip()
            methods.add(method_name)
            modifiers = self._modifiers(node, source)
            visibility = next(
                (
                    modifier
                    for modifier in modifiers
                    if modifier in {"private", "protected", "public"}
                ),
                "public",
            )
            attributes.add((f"method:{method_name}:visibility", visibility))
            for modifier in modifiers - {"private", "protected", "public"}:
                attributes.add((f"method:{method_name}:{modifier}", "true"))
            return_type = node.child_by_field_name("return_type")
            if return_type is not None:
                resolved_return_type = _resolved_declared_type(
                    _text(return_type, source),
                    namespace,
                    imports,
                    self._resolve_type,
                )
                if resolved_return_type:
                    attributes.add((
                        f"method:{method_name}:returnType",
                        resolved_return_type,
                    ))
            if method_name.casefold() != "__construct":
                continue
            parameters = node.child_by_field_name("parameters")
            if parameters is None:
                continue
            for parameter in _walk(parameters):
                if parameter.type not in {"simple_parameter", "variadic_parameter", "property_promotion_parameter"}:
                    continue
                type_node = parameter.child_by_field_name("type")
                if type_node is None:
                    type_node = next(
                        (
                            child for child in parameter.named_children
                            if child.type not in {"variable_name", "property_modifier", "visibility_modifier"}
                        ),
                        None,
                    )
                if type_node is None:
                    continue
                for token in _type_tokens(_text(type_node, source)):
                    constructor_types.add(self._resolve_type(token, namespace, imports))
        return (
            tuple(sorted(methods)),
            tuple(sorted(constructor_types)),
            attributes,
        )

    def _traits(
        self,
        declaration,
        source: bytes,
        namespace: str,
        imports: dict[str, str],
    ) -> set[tuple[str, str]]:
        traits: set[str] = set()
        for node in _walk(declaration):
            if node.type != "use_declaration":
                continue
            if _nearest(node, set(self.DECLARATIONS)) != declaration:
                continue
            for child in node.named_children:
                if child.type not in {"name", "qualified_name"}:
                    continue
                value = _text(child, source).strip()
                if value:
                    traits.add(self._resolve_type(value, namespace, imports))
        return {
            (f"php-trait:{index:04d}", target)
            for index, target in enumerate(sorted(traits))
        }

    def _code_references(
        self,
        declaration,
        source: bytes,
        namespace: str,
        imports: dict[str, str],
        qualified_name: str,
        parent_class: str,
    ) -> set[tuple[str, str]]:
        """Retain only statically proven construction and call targets."""
        references: dict[
            tuple[str, str, str, str],
            tuple[int, str],
        ] = {}
        chained_references: dict[
            tuple[str, tuple[str, ...], str, str],
            tuple[int, str],
        ] = {}
        literal_instance_references: dict[
            tuple[
                str,
                str,
                str,
                tuple[tuple[int, str], ...],
            ],
            tuple[int, str, tuple[tuple[int, str], ...]],
        ] = {}
        property_types = self._property_types(
            declaration,
            source,
            namespace,
            imports,
        )
        method_receiver_types: dict[int, dict[int, tuple[str, str]]] = {}
        method_literal_values: dict[
            int,
            dict[int, dict[str, str]],
        ] = {}
        for node in _walk(declaration):
            if node.type not in {
                "object_creation_expression",
                "scoped_call_expression",
                "member_call_expression",
                "nullsafe_member_call_expression",
            }:
                continue
            if _nearest(node, set(self.DECLARATIONS)) != declaration:
                continue

            caller_method = ""
            containing_method = _nearest(node, {"method_declaration"})
            if (
                containing_method is not None
                and _nearest(
                    containing_method,
                    set(self.DECLARATIONS),
                ) == declaration
            ):
                caller_name = containing_method.child_by_field_name("name")
                if caller_name is not None:
                    caller_method = _text(caller_name, source).strip()

            if node.type == "object_creation_expression":
                scope = next(
                    (
                        child
                        for child in node.named_children
                        if child.type in {
                            "name",
                            "qualified_name",
                            "relative_scope",
                        }
                    ),
                    None,
                )
                method = ""
                reference_kind = "construction"
            elif node.type == "scoped_call_expression":
                scope = node.child_by_field_name("scope")
                call_name = node.child_by_field_name("name")
                method = (
                    _text(call_name, source).strip()
                    if call_name is not None
                    else ""
                )
                reference_kind = "static-call"
            else:
                scope = None
                call_name = node.child_by_field_name("name")
                method = (
                    _text(call_name, source).strip()
                    if call_name is not None
                    else ""
                )
                receiver = node.child_by_field_name("object")
                chain = self._member_call_chain(receiver, source)
                if chain is not None:
                    base_receiver, base_call, via_methods = chain
                    target, receiver_resolution = self._direct_receiver_target(
                        base_receiver,
                        base_call,
                        containing_method,
                        source,
                        namespace,
                        imports,
                        qualified_name,
                        property_types,
                        method_receiver_types,
                    )
                    if target and method:
                        identity = (
                            target,
                            via_methods,
                            method,
                            caller_method,
                        )
                        line_number = node.start_point[0] + 1
                        existing = chained_references.get(identity)
                        if existing is None or line_number < existing[0]:
                            chained_references[identity] = (
                                line_number,
                                receiver_resolution,
                            )
                    continue

                target, receiver_resolution = self._direct_receiver_target(
                    receiver,
                    node,
                    containing_method,
                    source,
                    namespace,
                    imports,
                    qualified_name,
                    property_types,
                    method_receiver_types,
                )
                if not target or not method:
                    continue
                arguments = next(
                    (
                        child
                        for child in node.named_children
                        if child.type == "arguments"
                    ),
                    None,
                )
                literal_arguments: list[tuple[int, str]] = []
                literal_argument_resolution: list[tuple[int, str]] = []
                if arguments is not None:
                    literal_pattern = re.compile(
                        r"(?P<quote>['\"])(?P<value>"
                        r"[A-Za-z0-9_.:/-]{1,256})(?P=quote)"
                    )
                    local_literals: dict[str, str] = {}
                    if containing_method is not None:
                        method_key = containing_method.start_byte
                        if method_key not in method_literal_values:
                            method_literal_values[method_key] = (
                                self._method_variable_literal_values(
                                    containing_method,
                                    source,
                                )
                            )
                        local_literals = method_literal_values[
                            method_key
                        ].get(node.start_byte, {})
                    for argument_index, argument in enumerate(
                        arguments.named_children
                    ):
                        match = literal_pattern.fullmatch(
                            _text(argument, source).strip()
                        )
                        if match is not None:
                            literal_arguments.append((
                                argument_index,
                                match.group("value"),
                            ))
                            literal_argument_resolution.append((
                                argument_index,
                                "direct-literal",
                            ))
                            continue
                        argument_value = argument
                        if (
                            argument.type == "argument"
                            and len(argument.named_children) == 1
                        ):
                            argument_value = argument.named_children[0]
                        if argument_value.type != "variable_name":
                            continue
                        variable = _text(argument_value, source).strip()
                        local_value = local_literals.get(variable)
                        if local_value is not None:
                            literal_arguments.append((
                                argument_index,
                                local_value,
                            ))
                            literal_argument_resolution.append((
                                argument_index,
                                "local-exact-assignment",
                            ))
                if literal_arguments:
                    literal_identity = (
                        target,
                        method,
                        caller_method,
                        tuple(literal_arguments),
                    )
                    literal_line = node.start_point[0] + 1
                    prior_literal = literal_instance_references.get(
                        literal_identity
                    )
                    if (
                        prior_literal is None
                        or literal_line < prior_literal[0]
                    ):
                        literal_instance_references[literal_identity] = (
                            literal_line,
                            receiver_resolution,
                            tuple(literal_argument_resolution),
                        )
                reference_kind = "instance-call"

            if (
                reference_kind != "instance-call"
                and (
                    scope is None
                    or (reference_kind == "static-call" and not method)
                )
            ):
                continue
            if reference_kind != "instance-call":
                raw_scope = _text(scope, source).strip()
                folded_scope = raw_scope.casefold()
                if folded_scope == "static":
                    # Late-static binding can target an unseen subclass.
                    continue
                if folded_scope == "self":
                    target = qualified_name
                elif folded_scope == "parent":
                    target = parent_class
                else:
                    target = self._resolve_type(raw_scope, namespace, imports)
            if not target:
                continue
            identity = (
                reference_kind,
                target,
                method,
                caller_method,
            )
            line_number = node.start_point[0] + 1
            existing = references.get(identity)
            if existing is None or line_number < existing[0]:
                references[identity] = (
                    line_number,
                    receiver_resolution
                    if reference_kind == "instance-call"
                    else "",
                )

        attributes: set[tuple[str, str]] = set()
        by_kind = {
            "construction": _CONSTRUCTION_REFERENCE,
            "static-call": _STATIC_CALL_REFERENCE,
            "instance-call": _INSTANCE_CALL_REFERENCE,
        }
        for reference_kind, prefix in by_kind.items():
            selected = sorted(
                (
                    kind,
                    line_number,
                    target,
                    method,
                    caller,
                    receiver_resolution,
                )
                for (
                    kind,
                    target,
                    method,
                    caller,
                ), (
                    line_number,
                    receiver_resolution,
                )
                in references.items()
                if kind == reference_kind
            )
            for index, (
                _,
                line_number,
                target,
                method,
                caller,
                receiver_resolution,
            ) in enumerate(
                selected
            ):
                payload = {
                    "caller": caller,
                    "line": line_number,
                    "method": method,
                    "target": target,
                }
                if receiver_resolution:
                    payload["receiverResolution"] = receiver_resolution
                attributes.add((
                    f"{prefix}{index:04d}",
                    json.dumps(
                        payload,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ))
        for index, (
            (
                target,
                via_methods,
                method,
                caller,
            ),
            (
                line_number,
                receiver_resolution,
            ),
        ) in enumerate(sorted(chained_references.items())):
            payload = {
                "caller": caller,
                "line": line_number,
                "method": method,
                "receiverResolution": receiver_resolution,
                "target": target,
                "viaMethods": list(via_methods),
            }
            attributes.add((
                f"{_CHAINED_INSTANCE_CALL_REFERENCE}{index:04d}",
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ))
        for index, (
            (
                target,
                method,
                caller,
                literal_arguments,
            ),
            (
                line_number,
                receiver_resolution,
                literal_argument_resolution,
            ),
        ) in enumerate(sorted(literal_instance_references.items())):
            payload = {
                "caller": caller,
                "line": line_number,
                "literalStringArguments": {
                    str(position): value
                    for position, value in literal_arguments
                },
                "method": method,
                "receiverResolution": receiver_resolution,
                "target": target,
            }
            if any(
                resolution != "direct-literal"
                for _, resolution in literal_argument_resolution
            ):
                payload["literalArgumentResolution"] = {
                    str(position): resolution
                    for position, resolution
                    in literal_argument_resolution
                }
            attributes.add((
                f"{_LITERAL_INSTANCE_CALL_REFERENCE}{index:04d}",
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ))
        return attributes

    def _method_variable_literal_values(
        self,
        method,
        source: bytes,
    ) -> dict[int, dict[str, str]]:
        """Resolve only unconditional, uniquely assigned local string literals."""
        safe_literal = re.compile(
            r"(?P<quote>['\"])(?P<value>"
            r"[A-Za-z0-9_.:/-]{1,256})(?P=quote)"
        )
        candidates: dict[str, set[str | None]] = {}
        parameters = method.child_by_field_name("parameters")
        if parameters is not None:
            for parameter in parameters.named_children:
                name = parameter.child_by_field_name("name")
                if name is not None:
                    candidates.setdefault(
                        _text(name, source).strip(),
                        set(),
                    ).add(None)

        events: list[tuple[int, int, object]] = []
        for node in _walk(method):
            if node == method or _nearest(node, {"method_declaration"}) != method:
                continue
            if _nested_scope_between(
                node,
                method,
                {"anonymous_function", "arrow_function"},
            ):
                continue
            if node.type in {
                "member_call_expression",
                "nullsafe_member_call_expression",
            }:
                events.append((node.start_byte, 0, node))
            elif node.type == "assignment_expression":
                events.append((node.end_byte, 1, node))

        resolved_calls: dict[int, dict[str, str]] = {}
        control_scopes = {
            "catch_clause",
            "do_statement",
            "else_clause",
            "finally_clause",
            "for_statement",
            "foreach_statement",
            "if_statement",
            "switch_block",
            "switch_statement",
            "while_statement",
        }
        for _, event_kind, node in sorted(
            events,
            key=lambda item: (item[0], item[1], item[2].end_byte),
        ):
            if event_kind == 0:
                resolved_calls[node.start_byte] = {
                    variable: next(iter(values))
                    for variable, values in sorted(candidates.items())
                    if None not in values and len(values) == 1
                }
                continue

            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if left is None or left.type != "variable_name":
                continue
            variable = _text(left, source).strip()
            value: str | None = None
            if not _nested_scope_between(node, method, control_scopes):
                match = (
                    safe_literal.fullmatch(_text(right, source).strip())
                    if right is not None
                    else None
                )
                if match is not None:
                    value = match.group("value")
                elif right is not None and right.type == "variable_name":
                    source_values = candidates.get(
                        _text(right, source).strip(),
                        set(),
                    )
                    if (
                        None not in source_values
                        and len(source_values) == 1
                    ):
                        value = next(iter(source_values))
            candidates.setdefault(variable, set()).add(value)
        return resolved_calls

    def _class_constants(
        self,
        declaration,
        source: bytes,
        namespace: str,
        imports: dict[str, str],
        qualified_name: str,
        parent_class: str,
    ) -> set[tuple[str, str]]:
        """Record literal declarations and statically resolved constant reads.

        Literal string values are deliberately limited to registry-safe tokens.
        Dynamic expressions and interpolated strings remain unknown.
        """
        declarations: list[dict[str, object]] = []
        references: list[dict[str, object]] = []
        safe_literal = re.compile(r"[A-Za-z0-9_.:/-]+")

        for node in _walk(declaration):
            if _nearest(node, set(self.DECLARATIONS)) != declaration:
                continue
            if node.type == "const_element":
                named = node.named_children
                if len(named) < 2:
                    continue
                name = _text(named[0], source).strip()
                raw_value = _text(named[-1], source).strip()
                if (
                    not name
                    or len(raw_value) < 2
                    or raw_value[0] not in {"'", '"'}
                    or raw_value[-1] != raw_value[0]
                ):
                    continue
                value = raw_value[1:-1]
                if not safe_literal.fullmatch(value):
                    continue
                declarations.append({
                    "line": node.start_point[0] + 1,
                    "name": name,
                    "value": value,
                })
                continue
            if node.type != "class_constant_access_expression":
                continue
            named = node.named_children
            if len(named) < 2:
                continue
            raw_scope = _text(named[0], source).strip()
            constant = _text(named[-1], source).strip()
            if not raw_scope or not constant or constant.casefold() == "class":
                continue
            folded_scope = raw_scope.casefold()
            if folded_scope == "self":
                target = qualified_name
            elif folded_scope == "parent":
                target = parent_class
            elif folded_scope == "static":
                continue
            else:
                target = self._resolve_type(raw_scope, namespace, imports)
            if not target:
                continue
            payload: dict[str, object] = {
                "constant": constant,
                "line": node.start_point[0] + 1,
                "target": target,
            }
            argument = (
                node.parent
                if node.parent is not None
                and node.parent.type == "argument"
                else node
            )
            arguments = (
                argument.parent
                if argument.parent is not None
                and argument.parent.type == "arguments"
                else None
            )
            call = arguments.parent if arguments is not None else None
            if call is not None and call.type in {
                "function_call_expression",
                "member_call_expression",
                "nullsafe_member_call_expression",
                "scoped_call_expression",
            }:
                call_name = call.child_by_field_name("name")
                argument_of = (
                    _text(call_name, source).strip()
                    if call_name is not None
                    else ""
                )
                if argument_of:
                    payload["argumentOf"] = argument_of
            references.append(payload)

        attributes: set[tuple[str, str]] = set()
        for index, payload in enumerate(sorted(
            declarations,
            key=lambda item: (
                str(item["name"]),
                int(item["line"]),
                str(item["value"]),
            ),
        )):
            attributes.add((
                f"{_CLASS_CONSTANT_DECLARATION}{index:04d}",
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ))
        for index, payload in enumerate(sorted(
            references,
            key=lambda item: (
                str(item["target"]),
                str(item["constant"]),
                int(item["line"]),
                str(item.get("argumentOf", "")),
            ),
        )):
            attributes.add((
                f"{_CLASS_CONSTANT_REFERENCE}{index:04d}",
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ))
        return attributes

    def _direct_receiver_target(
        self,
        receiver,
        call_node,
        containing_method,
        source: bytes,
        namespace: str,
        imports: dict[str, str],
        qualified_name: str,
        property_types: dict[str, str],
        method_receiver_types: dict[int, dict[int, tuple[str, str]]],
    ) -> tuple[str, str]:
        if (
            receiver is not None
            and receiver.type == "variable_name"
            and _text(receiver, source).strip() == "$this"
        ):
            return qualified_name, "self-instance"

        property_name = self._this_property_name(receiver, source)
        if property_name:
            target = property_types.get(property_name, "")
            return (
                (target, "declared-property")
                if target
                else ("", "")
            )

        target, receiver_resolution = self._expression_receiver_type(
            receiver,
            source,
            namespace,
            imports,
        )
        if (
            target
            or receiver is None
            or receiver.type != "variable_name"
            or containing_method is None
        ):
            return target, receiver_resolution

        method_key = containing_method.start_byte
        receiver_types = method_receiver_types.get(method_key)
        if receiver_types is None:
            receiver_types = self._method_variable_call_targets(
                containing_method,
                source,
                namespace,
                imports,
                property_types,
            )
            method_receiver_types[method_key] = receiver_types
        return receiver_types.get(call_node.start_byte, ("", ""))

    @staticmethod
    def _member_call_chain(
        receiver,
        source: bytes,
    ) -> tuple[object, object, tuple[str, ...]] | None:
        """Return the direct base receiver and ordered intermediate calls."""
        while (
            receiver is not None
            and receiver.type == "parenthesized_expression"
            and len(receiver.named_children) == 1
        ):
            receiver = receiver.named_children[0]
        if receiver is None or receiver.type not in {
            "member_call_expression",
            "nullsafe_member_call_expression",
        }:
            return None

        calls: list[object] = []
        current = receiver
        while current.type in {
            "member_call_expression",
            "nullsafe_member_call_expression",
        }:
            calls.append(current)
            current = current.child_by_field_name("object")
            while (
                current is not None
                and current.type == "parenthesized_expression"
                and len(current.named_children) == 1
            ):
                current = current.named_children[0]
            if current is None:
                return None
        calls.reverse()
        methods: list[str] = []
        for call in calls:
            name = call.child_by_field_name("name")
            method = _text(name, source).strip() if name is not None else ""
            if not method:
                return None
            methods.append(method)
        return current, calls[0], tuple(methods)

    def _method_variable_call_targets(
        self,
        method,
        source: bytes,
        namespace: str,
        imports: dict[str, str],
        property_types: dict[str, str],
    ) -> dict[int, tuple[str, str]]:
        """Resolve method-local receivers without pretending to do full flow analysis.

        A variable remains usable only while every declaration/assignment seen
        before a call points to the same exact named type. Unknown or conflicting
        assignments permanently make that variable unresolved for later calls.
        This conservative union of possible assignments is stable across branch
        layouts and never guesses a dynamic factory/call return type.
        """
        candidates: dict[str, set[str | None]] = {}
        assigned_locally: set[str] = set()
        parameters = method.child_by_field_name("parameters")
        if parameters is not None:
            for parameter in parameters.named_children:
                if parameter.type not in {
                    "simple_parameter",
                    "variadic_parameter",
                    "property_promotion_parameter",
                }:
                    continue
                name_node = parameter.child_by_field_name("name")
                target = self._single_resolved_type(
                    parameter.child_by_field_name("type"),
                    source,
                    namespace,
                    imports,
                )
                if name_node is None or not target:
                    continue
                variable = _text(name_node, source).strip()
                candidates.setdefault(variable, set()).add(target)

        events: list[tuple[int, int, object]] = []
        for node in _walk(method):
            if node == method or _nearest(node, {"method_declaration"}) != method:
                continue
            if _nested_scope_between(
                node,
                method,
                {"anonymous_function", "arrow_function"},
            ):
                continue
            if node.type in {
                "member_call_expression",
                "nullsafe_member_call_expression",
            }:
                events.append((node.start_byte, 0, node))
            elif node.type == "assignment_expression":
                # The right-hand side is evaluated before the assignment takes
                # effect, so place the state update at the expression end.
                events.append((node.end_byte, 1, node))

        resolved_calls: dict[int, tuple[str, str]] = {}
        for _, event_kind, node in sorted(
            events,
            key=lambda item: (item[0], item[1], item[2].end_byte),
        ):
            if event_kind == 0:
                receiver = node.child_by_field_name("object")
                if receiver is None or receiver.type != "variable_name":
                    continue
                variable = _text(receiver, source).strip()
                targets = candidates.get(variable, set())
                if None in targets or len(targets) != 1:
                    continue
                resolved_calls[node.start_byte] = (
                    next(iter(targets)),
                    (
                        "local-exact-assignment"
                        if variable in assigned_locally
                        else "declared-parameter"
                    ),
                )
                continue

            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if left is None or left.type != "variable_name":
                continue
            variable = _text(left, source).strip()
            target, _ = self._expression_receiver_type(
                right,
                source,
                namespace,
                imports,
            )
            if not target and right is not None:
                if right.type == "variable_name":
                    source_targets = candidates.get(
                        _text(right, source).strip(),
                        set(),
                    )
                    if None not in source_targets and len(source_targets) == 1:
                        target = next(iter(source_targets))
                else:
                    property_name = self._this_property_name(right, source)
                    if property_name:
                        target = property_types.get(property_name, "")
            candidates.setdefault(variable, set()).add(target or None)
            assigned_locally.add(variable)
        return resolved_calls

    def _expression_receiver_type(
        self,
        expression,
        source: bytes,
        namespace: str,
        imports: dict[str, str],
    ) -> tuple[str, str]:
        """Resolve one direct ``new Type`` receiver, including parentheses."""
        while (
            expression is not None
            and expression.type == "parenthesized_expression"
            and len(expression.named_children) == 1
        ):
            expression = expression.named_children[0]
        if expression is None or expression.type != "object_creation_expression":
            return "", ""
        scope = next(
            (
                child
                for child in expression.named_children
                if child.type in {
                    "name",
                    "qualified_name",
                    "relative_scope",
                }
            ),
            None,
        )
        if scope is None:
            return "", ""
        raw_scope = _text(scope, source).strip()
        if raw_scope.casefold() in {"self", "parent", "static"}:
            return "", ""
        return (
            self._resolve_type(raw_scope, namespace, imports),
            "direct-construction",
        )

    def _property_types(
        self,
        declaration,
        source: bytes,
        namespace: str,
        imports: dict[str, str],
    ) -> dict[str, str]:
        """
        Resolve only property types that have one exact declared source.

        Magento services commonly retain constructor dependencies on ``$this``.
        A declared/promoted property type or an exact ``$this->x = $x``
        constructor assignment from one typed parameter is sufficient to resolve
        the receiver. Union/intersection types and conflicting assignments remain
        unresolved instead of guessing a runtime target.
        """
        candidates: dict[str, set[str]] = {}

        def record(property_name: str, target: str) -> None:
            if property_name and target:
                candidates.setdefault(property_name, set()).add(target)

        for node in _walk(declaration):
            if (
                node.type != "property_declaration"
                or _nearest(node, set(self.DECLARATIONS)) != declaration
            ):
                continue
            target = self._single_resolved_type(
                node.child_by_field_name("type"),
                source,
                namespace,
                imports,
            )
            if not target:
                continue
            for child in node.named_children:
                if child.type != "property_element":
                    continue
                name_node = child.child_by_field_name("name")
                if name_node is not None:
                    record(_text(name_node, source).strip().lstrip("$"), target)

        constructor = next(
            (
                node
                for node in _walk(declaration)
                if node.type == "method_declaration"
                and _nearest(node, set(self.DECLARATIONS)) == declaration
                and (
                    (name_node := node.child_by_field_name("name")) is not None
                    and _text(name_node, source).strip().casefold() == "__construct"
                )
            ),
            None,
        )
        if constructor is None:
            return {
                name: next(iter(targets))
                for name, targets in sorted(candidates.items())
                if len(targets) == 1
            }

        parameter_types: dict[str, str] = {}
        parameters = constructor.child_by_field_name("parameters")
        if parameters is not None:
            for parameter in parameters.named_children:
                if parameter.type not in {
                    "simple_parameter",
                    "variadic_parameter",
                    "property_promotion_parameter",
                }:
                    continue
                target = self._single_resolved_type(
                    parameter.child_by_field_name("type"),
                    source,
                    namespace,
                    imports,
                )
                name_node = parameter.child_by_field_name("name")
                if not target or name_node is None:
                    continue
                parameter_name = _text(name_node, source).strip().lstrip("$")
                parameter_types[parameter_name] = target
                if parameter.type == "property_promotion_parameter":
                    record(parameter_name, target)

        for node in _walk(constructor):
            if node.type != "assignment_expression":
                continue
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            property_name = self._this_property_name(left, source)
            if (
                not property_name
                or right is None
                or right.type != "variable_name"
            ):
                continue
            parameter_name = _text(right, source).strip().lstrip("$")
            target = parameter_types.get(parameter_name)
            if target:
                record(property_name, target)

        return {
            name: next(iter(targets))
            for name, targets in sorted(candidates.items())
            if len(targets) == 1
        }

    def _single_resolved_type(
        self,
        type_node,
        source: bytes,
        namespace: str,
        imports: dict[str, str],
    ) -> str:
        if type_node is None:
            return ""
        tokens = _type_tokens(_text(type_node, source))
        if len(tokens) != 1:
            return ""
        return self._resolve_type(tokens[0], namespace, imports)

    @staticmethod
    def _this_property_name(node, source: bytes) -> str:
        if node is None or node.type != "member_access_expression":
            return ""
        receiver = node.child_by_field_name("object")
        name_node = node.child_by_field_name("name")
        if (
            receiver is None
            or receiver.type != "variable_name"
            or _text(receiver, source).strip().casefold() != "$this"
            or name_node is None
        ):
            return ""
        return _text(name_node, source).strip().lstrip("$")

    @staticmethod
    def _modifiers(node, source: bytes) -> set[str]:
        return {
            _text(child, source).strip().casefold()
            for child in node.children
            if child.type.endswith("_modifier") and _text(child, source).strip()
        }

    @staticmethod
    def _resolve_type(value: str, namespace: str, imports: dict[str, str]) -> str:
        if value.startswith("\\"):
            return value.lstrip("\\")
        head, separator, tail = value.partition("\\")
        if head.casefold() == "namespace":
            return f"{namespace}\\{tail}" if namespace and separator else tail
        imported = imports.get(head.casefold())
        if imported:
            return imported + (f"\\{tail}" if separator else "")
        return f"{namespace}\\{value}" if namespace else value


@dataclass
class PhpRepositorySession:
    plugin_id: str
    revision: str
    _symbols: set[SymbolDefinition] = field(default_factory=set)
    _executor: ThreadPoolExecutor | None = field(default=None, init=False, repr=False)

    def _parse_workers(self) -> int:
        configured = os.getenv("CODECROW_PHP_PARSE_WORKERS")
        if configured is not None:
            try:
                return max(1, min(8, int(configured)))
            except ValueError as exception:
                raise ValueError("CODECROW_PHP_PARSE_WORKERS must be an integer") from exception
        return max(1, min(4, os.cpu_count() or 1))

    @classmethod
    def restore(cls, plugin_id: str, revision: str, snapshots) -> "PhpRepositorySession":
        snapshot = next((item for item in snapshots if item.kind == "php-symbols"), None)
        if snapshot is None:
            raise ValueError("PHP repository snapshot is missing php-symbols")
        raw = gzip.decompress(base64.b64decode(snapshot.content.encode("ascii")))
        records = json.loads(raw.decode("utf-8"))
        symbols = {
            SymbolDefinition(
                qualified_name=record["name"],
                kind=record["kind"],
                path=record["path"],
                line=record["line"],
                parents=tuple(record.get("parents", ())),
                methods=tuple(record.get("methods", ())),
                constructor_types=tuple(record.get("constructorTypes", ())),
                attributes=tuple(tuple(item) for item in record.get("attributes", ())),
            )
            for record in records
        }
        return cls(plugin_id, revision, _symbols=symbols)

    def _snapshot(self) -> RepositorySnapshot:
        records = [
            {
                "name": symbol.qualified_name,
                "kind": symbol.kind,
                "path": symbol.path,
                "line": symbol.line,
                "parents": list(symbol.parents),
                "methods": list(symbol.methods),
                "constructorTypes": list(symbol.constructor_types),
                "attributes": [list(item) for item in symbol.attributes],
            }
            for symbol in sorted(self._symbols)
        ]
        raw = json.dumps(
            records, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        content = base64.b64encode(gzip.compress(raw, compresslevel=6, mtime=0)).decode("ascii")
        return RepositorySnapshot(self.plugin_id, "php-symbols", content)

    def _relation_packets(self) -> tuple[ArchitecturePacket, ...]:
        """Resolve exact in-repository PHP code relations without path guessing."""
        symbols_by_name: dict[str, list[SymbolDefinition]] = {}
        for symbol in sorted(self._symbols):
            symbols_by_name.setdefault(
                symbol.qualified_name.casefold(),
                [],
            ).append(symbol)

        facts_by_path: dict[str, set[GraphFact]] = {}
        related_by_path: dict[str, set[str]] = {}
        for source in sorted(self._symbols):
            attributes = dict(source.attributes)
            relations: set[
                tuple[
                    str,
                    str,
                    str,
                    int,
                    tuple[tuple[str, str], ...],
                ]
            ] = set()
            chained_relations: set[
                tuple[
                    int,
                    str,
                    tuple[str, ...],
                    str,
                    str,
                    str,
                ]
            ] = set()

            parent_class = attributes.get("php-parent-class")
            if parent_class:
                relations.add((
                    "php-inheritance",
                    "extends",
                    parent_class,
                    source.line,
                    (),
                ))
            interface_relation = (
                "extends"
                if source.kind == "interface"
                else "implements"
            )
            relations.update(
                (
                    "php-inheritance",
                    interface_relation,
                    target,
                    source.line,
                    (),
                )
                for key, target in source.attributes
                if key.startswith(("php-parent-interface:", "php-interface:"))
            )
            relations.update(
                (
                    "php-trait-use",
                    "uses-trait",
                    target,
                    source.line,
                    (),
                )
                for key, target in source.attributes
                if key.startswith("php-trait:")
            )
            relations.update(
                (
                    "php-constructor-dependency",
                    "constructor-requires",
                    target,
                    source.line,
                    (),
                )
                for target in source.constructor_types
            )
            for key, value in source.attributes:
                if key.startswith(_CONSTRUCTION_REFERENCE):
                    (
                        line_number,
                        target,
                        _,
                        caller,
                        _,
                    ) = _decode_reference(value)
                    call_attributes = (
                        (("callerMethod", caller),)
                        if caller
                        else ()
                    )
                    relations.add((
                        "php-construction-relation",
                        "constructs",
                        target,
                        line_number,
                        call_attributes,
                    ))
                elif key.startswith(_STATIC_CALL_REFERENCE):
                    (
                        line_number,
                        target,
                        method,
                        caller,
                        _,
                    ) = _decode_reference(value)
                    call_attributes = tuple(sorted((
                        *((("callerMethod", caller),) if caller else ()),
                        ("retrievalIdentifier:targetMethod", method),
                        ("targetMethod", method),
                    )))
                    relations.add((
                        "php-static-call-relation",
                        "calls-static",
                        target,
                        line_number,
                        call_attributes,
                    ))
                elif key.startswith(_INSTANCE_CALL_REFERENCE):
                    (
                        line_number,
                        target,
                        method,
                        caller,
                        receiver_resolution,
                    ) = _decode_reference(value)
                    call_attributes = tuple(sorted((
                        *((("callerMethod", caller),) if caller else ()),
                        *(
                            (("receiverResolution", receiver_resolution),)
                            if receiver_resolution
                            else ()
                        ),
                        ("retrievalIdentifier:targetMethod", method),
                        ("targetMethod", method),
                    )))
                    relations.add((
                        "php-instance-call-relation",
                        "calls-instance",
                        target,
                        line_number,
                        call_attributes,
                    ))
                elif key.startswith(_CHAINED_INSTANCE_CALL_REFERENCE):
                    chained_relations.add(_decode_chained_reference(value))

            resolved_relations: dict[
                tuple[str, str, str, tuple[tuple[str, str], ...]],
                tuple[SymbolDefinition, int],
            ] = {}
            for (
                kind,
                relation,
                target_name,
                line_number,
                relation_attributes,
            ) in sorted(relations):
                candidates = symbols_by_name.get(target_name.casefold(), ())
                if len(candidates) != 1:
                    continue
                target = candidates[0]
                if kind == "php-trait-use":
                    source_methods = {
                        method.casefold() for method in source.methods
                    }
                    target_methods = {
                        method.casefold() for method in target.methods
                    }
                    constructor_attributes: list[tuple[str, str]] = []
                    if "__construct" in source_methods:
                        constructor_attributes.append((
                            "retrievalIdentifier:consumerConstructor",
                            "__construct",
                        ))
                    if "__construct" in target_methods:
                        constructor_attributes.append((
                            "retrievalIdentifier:traitConstructor",
                            "__construct",
                        ))
                    if (
                        "__construct" in source_methods
                        and "__construct" in target_methods
                    ):
                        constructor_attributes.extend((
                            ("constructorResolution", "class-method-precedence"),
                            ("resolvedMethod", "__construct"),
                        ))
                    relation_attributes = tuple(sorted((
                        *relation_attributes,
                        *constructor_attributes,
                    )))
                if target.path == source.path:
                    if kind not in {
                        "php-instance-call-relation",
                        "php-static-call-relation",
                    }:
                        continue
                    target_method = dict(relation_attributes).get(
                        "targetMethod",
                        "",
                    )
                    (
                        method_contract,
                        _,
                    ) = self._declared_method_contract(
                        target,
                        target_method,
                        symbols_by_name,
                    )
                    if (
                        not target_method
                        or dict(method_contract).get(
                            "targetMethodDeclared"
                        ) != "true"
                    ):
                        continue
                    facts_by_path.setdefault(source.path, set()).add(GraphFact(
                        kind="php-intra-class-call-relation",
                        source=source.qualified_name,
                        relation=relation,
                        target=target.qualified_name,
                        path=source.path,
                        line=line_number,
                        attributes=tuple(sorted((
                            ("sourceKind", source.kind),
                            ("targetKind", target.kind),
                            *relation_attributes,
                            *method_contract,
                        ))),
                    ))
                    continue
                identity = (
                    kind,
                    relation,
                    target.qualified_name,
                    relation_attributes,
                )
                existing = resolved_relations.get(identity)
                if existing is None or line_number < existing[1]:
                    resolved_relations[identity] = (target, line_number)

            for (
                kind,
                relation,
                _,
                relation_attributes,
            ), (target, line_number) in sorted(resolved_relations.items()):
                target_method = dict(relation_attributes).get(
                    "targetMethod",
                    "",
                )
                (
                    method_contract,
                    method_declaration_path,
                ) = self._declared_method_contract(
                    target,
                    target_method,
                    symbols_by_name,
                )
                fact_related_paths = tuple(sorted({
                    target.path,
                    *(
                        (method_declaration_path,)
                        if method_declaration_path
                        else ()
                    ),
                }))
                facts_by_path.setdefault(source.path, set()).add(GraphFact(
                    kind=kind,
                    source=source.qualified_name,
                    relation=relation,
                    target=target.qualified_name,
                    path=source.path,
                    line=line_number,
                    attributes=tuple(sorted((
                        ("sourceKind", source.kind),
                        ("targetKind", target.kind),
                        *relation_attributes,
                        *method_contract,
                    ))),
                    related_paths=fact_related_paths,
                ))
                related_by_path.setdefault(source.path, set()).update(
                    fact_related_paths
                )

            for (
                line_number,
                base_target_name,
                via_methods,
                target_method,
                caller,
                base_resolution,
            ) in sorted(chained_relations):
                resolved = self._resolve_chained_instance_call(
                    source,
                    base_target_name,
                    via_methods,
                    target_method,
                    caller,
                    base_resolution,
                    line_number,
                    symbols_by_name,
                )
                if resolved is None:
                    continue
                fact, fact_related_paths = resolved
                facts_by_path.setdefault(source.path, set()).add(fact)
                related_by_path.setdefault(source.path, set()).update(
                    fact_related_paths
                )

        return tuple(
            ArchitecturePacket(
                plugin_id=self.plugin_id,
                kind="php-code-relation",
                key=source_path,
                paths=tuple(sorted({
                    source_path,
                    *related_by_path.get(source_path, ()),
                })),
                facts=tuple(sorted(facts)),
                attributes=(("resolution", "unique-repository-symbol"),),
            )
            for source_path, facts in sorted(facts_by_path.items())
            if facts
        )

    def _resolve_chained_instance_call(
        self,
        source: SymbolDefinition,
        base_target_name: str,
        via_methods: tuple[str, ...],
        target_method: str,
        caller: str,
        base_resolution: str,
        line_number: int,
        symbols_by_name: dict[str, list[SymbolDefinition]],
    ) -> tuple[GraphFact, tuple[str, ...]] | None:
        """Follow only exact, non-null, in-repository declared return types."""
        base_candidates = symbols_by_name.get(base_target_name.casefold(), ())
        if len(base_candidates) != 1:
            return None
        current = base_candidates[0]
        related_paths: set[str] = {current.path}
        chain_attributes: list[tuple[str, str]] = []

        for index, method_name in enumerate(via_methods):
            contract, declaration_path = self._declared_method_contract(
                current,
                method_name,
                symbols_by_name,
            )
            contract_values = dict(contract)
            declared_return_type = contract_values.get(
                "targetDeclaredReturnType",
                "",
            )
            return_target = self._exact_declared_return_symbol(
                declared_return_type,
                contract_values,
                symbols_by_name,
            )
            if return_target is None:
                return None
            if declaration_path:
                related_paths.add(declaration_path)
            related_paths.add(return_target.path)
            prefix = f"receiverCall:{index:04d}:"
            chain_attributes.extend((
                (f"{prefix}sourceType", current.qualified_name),
                (f"{prefix}method", method_name),
                (f"{prefix}declaredReturnType", declared_return_type),
                (
                    f"{prefix}methodDeclaredOn",
                    contract_values["targetMethodDeclaredOn"],
                ),
            ))
            current = return_target

        if current.path == source.path:
            return None
        target_contract, target_declaration_path = self._declared_method_contract(
            current,
            target_method,
            symbols_by_name,
        )
        if target_declaration_path:
            related_paths.add(target_declaration_path)
        relation_attributes = (
            *((("callerMethod", caller),) if caller else ()),
            ("receiverBaseResolution", base_resolution),
            ("receiverResolution", "exact-call-return"),
            ("retrievalIdentifier:targetMethod", target_method),
            ("targetMethod", target_method),
            *chain_attributes,
            *target_contract,
        )
        fact_related_paths = tuple(sorted(related_paths))
        return (
            GraphFact(
                kind="php-instance-call-relation",
                source=source.qualified_name,
                relation="calls-instance",
                target=current.qualified_name,
                path=source.path,
                line=line_number,
                attributes=tuple(sorted(relation_attributes)),
                related_paths=fact_related_paths,
            ),
            fact_related_paths,
        )

    @staticmethod
    def _exact_declared_return_symbol(
        declared_return_type: str,
        method_contract: dict[str, str],
        symbols_by_name: dict[str, list[SymbolDefinition]],
    ) -> SymbolDefinition | None:
        """Accept one concrete named type, never nullable/union/intersection/builtin."""
        if declared_return_type.casefold() == "self":
            declaring_name = method_contract.get("targetMethodDeclaredOn", "")
            candidates = symbols_by_name.get(declaring_name.casefold(), ())
            return candidates[0] if len(candidates) == 1 else None
        if (
            not declared_return_type
            or declared_return_type.casefold() in _BUILTIN_TYPES
            or re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*(?:\\[A-Za-z_][A-Za-z0-9_]*)*",
                declared_return_type,
            )
            is None
        ):
            return None
        candidates = symbols_by_name.get(declared_return_type.casefold(), ())
        return candidates[0] if len(candidates) == 1 else None

    def _declared_method_contract(
        self,
        target: SymbolDefinition,
        method_name: str,
        symbols_by_name: dict[str, list[SymbolDefinition]],
    ) -> tuple[tuple[tuple[str, str], ...], str]:
        """Project an unambiguous direct or parent-declared target method.

        Parent traversal requires one exact in-repository class at every step.
        Trait, interface-contract, magic, external, ambiguous, and private-parent
        cases remain unknown rather than being turned into absence assertions.
        """
        if not method_name:
            return (), ""

        declaring_target = target
        declaration_origin = "direct"
        seen: set[str] = set()
        while True:
            identity = declaring_target.qualified_name.casefold()
            if identity in seen:
                return (), ""
            seen.add(identity)
            declared = tuple(
                candidate
                for candidate in declaring_target.methods
                if candidate.casefold() == method_name.casefold()
            )
            if len(declared) == 1:
                break
            if declared:
                return (), ""
            parent_name = dict(declaring_target.attributes).get(
                "php-parent-class",
                "",
            )
            if not parent_name:
                return (), ""
            parent_candidates = symbols_by_name.get(
                parent_name.casefold(),
                (),
            )
            if len(parent_candidates) != 1:
                return (), ""
            declaring_target = parent_candidates[0]
            declaration_origin = "inherited-parent"

        exact_name = declared[0]
        prefix = f"method:{exact_name}:"
        target_attributes = {
            key[len(prefix):]: value
            for key, value in declaring_target.attributes
            if key.startswith(prefix)
        }
        if (
            declaration_origin == "inherited-parent"
            and target_attributes.get("visibility") == "private"
        ):
            return (), ""
        contract = {
            "targetMethodDeclared": "true",
            "targetMethodDeclarationOrigin": declaration_origin,
            "targetMethodDeclaredOn": declaring_target.qualified_name,
        }
        projected = {
            "final": "targetMethodFinal",
            "returnType": "targetDeclaredReturnType",
            "static": "targetMethodStatic",
            "visibility": "targetMethodVisibility",
        }
        for source_key, fact_key in projected.items():
            value = target_attributes.get(source_key)
            if value:
                contract[fact_key] = value
        return (
            tuple(sorted(contract.items())),
            declaring_target.path,
        )

    def ingest(self, artifacts: tuple[FileArtifact, ...]) -> None:
        php_artifacts = tuple(
            artifact
            for artifact in artifacts
            if artifact.path.casefold().endswith((".php", ".inc"))
        )
        if not php_artifacts:
            return
        changed_paths = {artifact.path for artifact in php_artifacts}
        self._symbols = {
            symbol for symbol in self._symbols if symbol.path not in changed_paths
        }
        parseable = tuple(
            artifact for artifact in php_artifacts
            if not artifact.deleted and _DECLARATION_HINT.search(artifact.content)
        )
        if not parseable:
            return
        workers = self._parse_workers()
        if workers == 1 or len(parseable) == 1:
            parsed = tuple(_parse_artifact(artifact) for artifact in parseable)
        else:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=workers,
                    thread_name_prefix="codecrow-php-ast",
                )
            parsed = tuple(self._executor.map(_parse_artifact, parseable))
        for symbols in parsed:
            self._symbols.update(symbols)

    def finish(self, dependencies: RepositoryAnalysis):
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None
        started = time.monotonic()
        snapshot = self._snapshot()
        logger.info(
            "PHP repository snapshot: symbols=%s encoded_bytes=%s elapsed=%.3fs",
            len(self._symbols),
            len(snapshot.content),
            time.monotonic() - started,
        )
        return PluginOutcome.handled(RepositoryAnalysis(
            symbols=tuple(sorted(self._symbols)),
            packets=self._relation_packets(),
            snapshots=(snapshot,),
        ))
