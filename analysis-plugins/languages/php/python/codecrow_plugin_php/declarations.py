from __future__ import annotations

import json
import re

from .syntax import (
    DECLARATIONS,
    _CLASS_CONSTANT_DECLARATION,
    _CLASS_CONSTANT_REFERENCE,
    _modifiers,
    _nearest,
    _ordered_type_tokens,
    _resolve_type,
    _resolved_declared_type,
    _text,
    _type_tokens,
    _walk,
)


class PhpDeclarationExtractor:
    """Extract declared types, methods, constants, and composition contracts."""

    def parents(
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
                _resolve_type(token, namespace, imports)
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

    def methods(
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
            if _nearest(node, set(DECLARATIONS)) != declaration:
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            method_name = _text(name_node, source).strip()
            methods.add(method_name)
            modifiers = _modifiers(node, source)
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
                    _resolve_type,
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
                    constructor_types.add(_resolve_type(token, namespace, imports))
        return (
            tuple(sorted(methods)),
            tuple(sorted(constructor_types)),
            attributes,
        )

    def traits(
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
            if _nearest(node, set(DECLARATIONS)) != declaration:
                continue
            for child in node.named_children:
                if child.type not in {"name", "qualified_name"}:
                    continue
                value = _text(child, source).strip()
                if value:
                    traits.add(_resolve_type(value, namespace, imports))
        return {
            (f"php-trait:{index:04d}", target)
            for index, target in enumerate(sorted(traits))
        }

    def class_constants(
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
            if _nearest(node, set(DECLARATIONS)) != declaration:
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
                target = _resolve_type(raw_scope, namespace, imports)
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
