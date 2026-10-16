from __future__ import annotations

from .syntax import (
    DECLARATIONS,
    _nearest,
    _nested_scope_between,
    _resolve_type,
    _text,
    _type_tokens,
    _walk,
)


class PhpReceiverResolver:
    """Resolve local, declared-property, and direct-expression receiver types."""

    def direct_receiver_target(
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

        property_name = self.this_property_name(receiver, source)
        if property_name:
            target = property_types.get(property_name, "")
            return (
                (target, "declared-property")
                if target
                else ("", "")
            )

        target, receiver_resolution = self.expression_receiver_type(
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
            receiver_types = self.method_variable_call_targets(
                containing_method,
                source,
                namespace,
                imports,
                property_types,
            )
            method_receiver_types[method_key] = receiver_types
        return receiver_types.get(call_node.start_byte, ("", ""))

    def method_variable_call_targets(
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
                target = self.single_resolved_type(
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
            target, _ = self.expression_receiver_type(
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
                    property_name = self.this_property_name(right, source)
                    if property_name:
                        target = property_types.get(property_name, "")
            candidates.setdefault(variable, set()).add(target or None)
            assigned_locally.add(variable)
        return resolved_calls

    def expression_receiver_type(
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
            _resolve_type(raw_scope, namespace, imports),
            "direct-construction",
        )

    def property_types(
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
                or _nearest(node, set(DECLARATIONS)) != declaration
            ):
                continue
            target = self.single_resolved_type(
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
                and _nearest(node, set(DECLARATIONS)) == declaration
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
                target = self.single_resolved_type(
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
            property_name = self.this_property_name(left, source)
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

    def single_resolved_type(
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
        return _resolve_type(tokens[0], namespace, imports)

    @staticmethod
    def this_property_name(node, source: bytes) -> str:
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
