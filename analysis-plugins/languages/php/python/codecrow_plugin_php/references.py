from __future__ import annotations

import json
import re

from .receivers import PhpReceiverResolver
from .syntax import (
    DECLARATIONS,
    _CHAINED_INSTANCE_CALL_REFERENCE,
    _CONSTRUCTION_REFERENCE,
    _INSTANCE_CALL_REFERENCE,
    _LITERAL_INSTANCE_CALL_REFERENCE,
    _STATIC_CALL_REFERENCE,
    _nearest,
    _nested_scope_between,
    _resolve_type,
    _text,
    _walk,
)


class PhpReferenceExtractor:
    """Extract statically proven source references and literal call arguments."""

    def __init__(self, receivers: PhpReceiverResolver) -> None:
        self.receivers = receivers

    def code_references(
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
        property_types = self.receivers.property_types(
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
            if _nearest(node, set(DECLARATIONS)) != declaration:
                continue

            caller_method = ""
            containing_method = _nearest(node, {"method_declaration"})
            if (
                containing_method is not None
                and _nearest(
                    containing_method,
                    set(DECLARATIONS),
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
                chain = self.member_call_chain(receiver, source)
                if chain is not None:
                    base_receiver, base_call, via_methods = chain
                    target, receiver_resolution = self.receivers.direct_receiver_target(
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

                target, receiver_resolution = self.receivers.direct_receiver_target(
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
                                self.method_variable_literal_values(
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
                    target = _resolve_type(raw_scope, namespace, imports)
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

    def method_variable_literal_values(
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

    @staticmethod
    def member_call_chain(
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
