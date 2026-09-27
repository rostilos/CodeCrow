from __future__ import annotations

import json
import re


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

_TEMPLATE_INSTANCE_CALL_REFERENCE = "php-template-instance-call-reference:"

_CLASS_CONSTANT_DECLARATION = "php-class-constant:"

_CLASS_CONSTANT_REFERENCE = "php-class-constant-reference:"

def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

def _walk(node):
    """Preorder traversal without Python recursion limits on nested expressions."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))

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

def _modifiers(node, source: bytes) -> set[str]:
    return {
        _text(child, source).strip().casefold()
        for child in node.children
        if child.type.endswith("_modifier") and _text(child, source).strip()
    }

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

DECLARATIONS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "trait_declaration": "trait",
    "enum_declaration": "enum",
}

TEMPLATE_CALLABLE_SCOPES = {
    "anonymous_function",
    "arrow_function",
    "function_definition",
}

TEMPLATE_ASSIGNMENTS = {
    "assignment_expression",
    "augmented_assignment_expression",
}
