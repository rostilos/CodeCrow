from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .architecture import line, tag


_NODE_TAGS = frozenset({"block", "container", "uiComponent"})
_REFERENCE_TAGS = frozenset({"referenceBlock", "referenceContainer"})
_ASSET_TAGS = frozenset({"css", "font", "link", "script"})
_DEFAULT_BLOCK_CLASS = r"Magento\Framework\View\Element\Template"


def _xsi_type(element: ET.Element) -> str:
    return next(
        (
            value
            for key, value in element.attrib.items()
            if key == "type" or key.endswith("}type")
        ),
        "",
    )


def _attributes(element: ET.Element) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(
        (key.rsplit("}", 1)[-1], str(value).strip())
        for key, value in element.attrib.items()
        if value is not None
    ))


@dataclass(frozen=True, order=True)
class LayoutSource:
    path: str
    line: int
    handle: str


@dataclass(frozen=True, order=True)
class LayoutArgument:
    name: str
    value_type: str
    value: str
    source: LayoutSource


@dataclass(frozen=True, order=True)
class LayoutAction:
    owner: str
    method: str
    arguments: tuple[LayoutArgument, ...]
    source: LayoutSource
    ifconfig: str = ""


@dataclass(frozen=True, order=True)
class LayoutPropertySource:
    property: str
    source: LayoutSource


@dataclass(frozen=True, order=True)
class LayoutOperation:
    kind: str
    source: LayoutSource
    name: str = ""
    node_kind: str = ""
    parent: str = ""
    attributes: tuple[tuple[str, str], ...] = ()
    arguments: tuple[LayoutArgument, ...] = ()
    actions: tuple[LayoutAction, ...] = ()


@dataclass(frozen=True, order=True)
class LayoutDocument:
    path: str
    area: str
    handle: str
    operations: tuple[LayoutOperation, ...]
    root_layout: str = ""
    root_kind: str = ""


@dataclass(frozen=True, order=True)
class LayoutMergeDiagnostic:
    code: str
    message: str
    source: LayoutSource


@dataclass(frozen=True, order=True)
class EffectiveLayoutNode:
    name: str
    node_kind: str
    parent: str
    alias: str
    block_class: str
    template: str
    before: str
    after: str
    removed: bool
    display: bool | None
    order: int
    attributes: tuple[tuple[str, str], ...]
    arguments: tuple[LayoutArgument, ...]
    actions: tuple[LayoutAction, ...]
    source: LayoutSource
    property_sources: tuple[LayoutPropertySource, ...]
    provenance: tuple[LayoutSource, ...]


@dataclass(frozen=True, order=True)
class EffectiveLayoutAsset:
    src: str
    asset_kind: str
    removed: bool
    attributes: tuple[tuple[str, str], ...]
    source: LayoutSource
    provenance: tuple[LayoutSource, ...]


@dataclass(frozen=True)
class EffectiveLayout:
    requested_handles: tuple[str, ...]
    expanded_handles: tuple[str, ...]
    document_paths: tuple[str, ...]
    root_layout: str
    nodes: tuple[EffectiveLayoutNode, ...]
    assets: tuple[EffectiveLayoutAsset, ...]
    unresolved_operations: tuple[LayoutOperation, ...]
    diagnostics: tuple[LayoutMergeDiagnostic, ...]


def _argument_value(element: ET.Element) -> str:
    text = (element.text or "").strip()
    if text:
        return text
    return ""


def _arguments(
    element: ET.Element,
    content: str,
    path: str,
    handle: str,
    prefix: str = "",
) -> tuple[LayoutArgument, ...]:
    result: list[LayoutArgument] = []
    for child in element:
        child_tag = tag(child)
        if child_tag not in {"argument", "item"}:
            continue
        raw_name = child.get("name", "").strip()
        name = ".".join(filter(None, (prefix, raw_name)))
        value_type = _xsi_type(child)
        nested = tuple(
            candidate
            for candidate in child
            if tag(candidate) in {"argument", "item"}
        )
        if nested:
            result.extend(_arguments(
                child,
                content,
                path,
                handle,
                name,
            ))
            continue
        value = _argument_value(child)
        needle = value or raw_name or child_tag
        result.append(LayoutArgument(
            name=name or raw_name or child_tag,
            value_type=value_type,
            value=value,
            source=LayoutSource(path, line(content, needle), handle),
        ))
    return tuple(result)


def parse_layout_document(
    *,
    path: str,
    area: str,
    handle: str,
    content: str,
    root: ET.Element,
) -> LayoutDocument:
    """Parse one Magento layout document into instruction-level operations.

    The parser intentionally does not decide module, theme, or handle precedence.
    Those repository-level choices are supplied to :func:`merge_layout` as an
    ordered document sequence.
    """

    operations: list[LayoutOperation] = []
    anonymous_position = 0

    def source(element: ET.Element, needle: str = "") -> LayoutSource:
        identity = (
            needle
            or element.get("name", "")
            or element.get("element", "")
            or element.get("src", "")
            or tag(element)
        )
        return LayoutSource(path, line(content, identity), handle)

    def node_arguments(element: ET.Element) -> tuple[LayoutArgument, ...]:
        return tuple(
            argument
            for child in element
            if tag(child) == "arguments"
            for argument in _arguments(child, content, path, handle)
        )

    def node_actions(element: ET.Element, owner: str) -> tuple[LayoutAction, ...]:
        actions: list[LayoutAction] = []
        for child in element:
            if tag(child) != "action":
                continue
            method = child.get("method", "").strip()
            if not method:
                continue
            action_arguments = (
                _arguments(child, content, path, handle)
                + tuple(
                    argument
                    for arguments_node in child
                    if tag(arguments_node) == "arguments"
                    for argument in _arguments(
                        arguments_node,
                        content,
                        path,
                        handle,
                    )
                )
            )
            actions.append(LayoutAction(
                owner=owner,
                method=method,
                arguments=action_arguments,
                source=source(child, method),
                ifconfig=child.get("ifconfig", "").strip(),
            ))
        return tuple(actions)

    def walk(element: ET.Element, structural_parent: str = "") -> None:
        nonlocal anonymous_position
        element_tag = tag(element)
        child_parent = structural_parent

        if element_tag == "update":
            target_handle = element.get("handle", "").strip()
            if target_handle:
                operations.append(LayoutOperation(
                    kind="update",
                    name=target_handle,
                    source=source(element, target_handle),
                    attributes=_attributes(element),
                ))
        elif element_tag in _NODE_TAGS:
            name = element.get("name", "").strip()
            generated_name = False
            if not name and element_tag == "block":
                anonymous_position += 1
                declaration_source = source(element, element_tag)
                name = (
                    f"@anonymous:block:{path}:"
                    f"{declaration_source.line}:{anonymous_position}"
                )
                generated_name = True
            if name:
                operation_attributes = dict(_attributes(element))
                if generated_name:
                    operation_attributes["generatedName"] = "true"
                node_source = (
                    declaration_source
                    if generated_name
                    else source(element, name)
                )
                operations.append(LayoutOperation(
                    kind="declare",
                    name=name,
                    node_kind=element_tag,
                    parent=structural_parent,
                    source=node_source,
                    attributes=tuple(sorted(operation_attributes.items())),
                    arguments=node_arguments(element),
                    actions=node_actions(element, name),
                ))
                child_parent = name
        elif element_tag in _REFERENCE_TAGS:
            name = element.get("name", "").strip()
            if name:
                operations.append(LayoutOperation(
                    kind="reference",
                    name=name,
                    node_kind=(
                        "block"
                        if element_tag == "referenceBlock"
                        else "container"
                    ),
                    source=source(element, name),
                    attributes=_attributes(element),
                    arguments=node_arguments(element),
                    actions=node_actions(element, name),
                ))
                child_parent = name
        elif element_tag == "move":
            name = element.get("element", "").strip()
            destination = element.get("destination", "").strip()
            if name and destination:
                operations.append(LayoutOperation(
                    kind="move",
                    name=name,
                    parent=destination,
                    source=source(element, name),
                    attributes=_attributes(element),
                ))
        elif element_tag in _ASSET_TAGS:
            src = element.get("src", "").strip()
            if src:
                operations.append(LayoutOperation(
                    kind="asset",
                    name=src,
                    node_kind=element_tag,
                    source=source(element, src),
                    attributes=_attributes(element),
                ))
        elif element_tag == "remove":
            src = element.get("src", "").strip()
            if src:
                operations.append(LayoutOperation(
                    kind="remove-asset",
                    name=src,
                    source=source(element, src),
                    attributes=_attributes(element),
                ))

        for child in element:
            if tag(child) in {"arguments", "action"}:
                continue
            walk(child, child_parent)

    walk(root)
    return LayoutDocument(
        path=path,
        area=area,
        handle=handle,
        operations=tuple(operations),
        root_layout=root.get("layout", "").strip(),
        root_kind=tag(root),
    )


@dataclass
class _NodeState:
    name: str
    node_kind: str
    declaration_position: int
    parent: str = ""
    alias: str = ""
    block_class: str = ""
    template: str = ""
    before: str = ""
    after: str = ""
    removed: bool = False
    display: bool | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    arguments: dict[str, LayoutArgument] = field(default_factory=dict)
    actions: list[LayoutAction] = field(default_factory=list)
    source: LayoutSource | None = None
    property_sources: dict[str, LayoutSource] = field(default_factory=dict)
    provenance: set[LayoutSource] = field(default_factory=set)


@dataclass
class _AssetState:
    src: str
    asset_kind: str
    removed: bool = False
    attributes: dict[str, str] = field(default_factory=dict)
    source: LayoutSource | None = None
    provenance: set[LayoutSource] = field(default_factory=set)


def _boolean(value: str, default: bool) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _apply_node_operation(state: _NodeState, operation: LayoutOperation) -> None:
    attributes = dict(operation.attributes)
    state.source = operation.source
    state.provenance.add(operation.source)
    if operation.kind == "declare" and operation.parent:
        state.parent = operation.parent
        state.property_sources["parent"] = operation.source
    if operation.node_kind:
        state.node_kind = operation.node_kind
        state.property_sources["nodeKind"] = operation.source
    if (
        operation.kind == "declare"
        and operation.node_kind == "block"
        and not attributes.get("class")
    ):
        state.block_class = _DEFAULT_BLOCK_CLASS
        state.attributes["blockClassDefaulted"] = "true"
        state.property_sources["blockClass"] = operation.source
    if "as" in attributes:
        state.alias = attributes["as"]
        state.property_sources["alias"] = operation.source
    if "class" in attributes and attributes["class"].strip():
        state.block_class = attributes["class"].lstrip("\\")
        state.attributes.pop("blockClassDefaulted", None)
        state.property_sources["blockClass"] = operation.source
    if "template" in attributes:
        state.template = attributes["template"]
        state.property_sources["template"] = operation.source
    if "before" in attributes:
        state.before = attributes["before"]
        state.property_sources["before"] = operation.source
        if "after" not in attributes:
            state.after = ""
            state.property_sources["after"] = operation.source
    if "after" in attributes:
        state.after = attributes["after"]
        state.property_sources["after"] = operation.source
        if "before" not in attributes:
            state.before = ""
            state.property_sources["before"] = operation.source
    if "remove" in attributes:
        state.removed = _boolean(attributes["remove"], state.removed)
        state.property_sources["removed"] = operation.source
    if "display" in attributes:
        state.display = _boolean(attributes["display"], True)
        state.property_sources["display"] = operation.source
    state.attributes.update(attributes)
    for key in attributes:
        state.property_sources[f"attribute:{key}"] = operation.source
    for argument in operation.arguments:
        for existing_name in tuple(state.arguments):
            if (
                existing_name == argument.name
                or existing_name.startswith(argument.name + ".")
                or argument.name.startswith(existing_name + ".")
            ):
                state.arguments.pop(existing_name, None)
        state.arguments[argument.name] = argument
        state.property_sources[f"argument:{argument.name}"] = argument.source
    for action in operation.actions:
        if action not in state.actions:
            state.actions.append(action)
        state.property_sources[f"action:{action.method}"] = action.source
        if action.method.casefold() != "settemplate" or not action.arguments:
            continue
        template_argument = action.arguments[0]
        if (
            not template_argument.value
            or template_argument.value_type not in {"", "string"}
        ):
            continue
        if action.ifconfig:
            state.attributes["conditionalTemplateCandidate"] = (
                template_argument.value
            )
            state.attributes["conditionalTemplateIfconfig"] = action.ifconfig
            state.property_sources["conditionalTemplate"] = (
                template_argument.source
            )
            continue
        state.attributes.pop("conditionalTemplateCandidate", None)
        state.attributes.pop("conditionalTemplateIfconfig", None)
        state.property_sources.pop("conditionalTemplate", None)
        state.template = template_argument.value
        state.property_sources["template"] = template_argument.source


def _ordered_nodes(
    states: Mapping[str, _NodeState],
) -> tuple[tuple[str, int], tuple[LayoutMergeDiagnostic, ...]]:
    order_by_name: dict[str, int] = {}
    diagnostics: list[LayoutMergeDiagnostic] = []
    children_by_parent: dict[str, list[_NodeState]] = {}
    for state in states.values():
        children_by_parent.setdefault(state.parent, []).append(state)

    next_position = 0
    for parent, children in sorted(children_by_parent.items()):
        children.sort(key=lambda item: (item.declaration_position, item.name))
        by_name = {child.name: child for child in children}
        edges: dict[str, set[str]] = {child.name: set() for child in children}
        indegree = {child.name: 0 for child in children}

        def edge(before: str, after: str) -> None:
            if before == after or after in edges[before]:
                return
            edges[before].add(after)
            indegree[after] += 1

        first = [
            child
            for child in children
            if child.before == "-" and not child.after
        ]
        last = [child for child in children if child.after == "-"]
        for child in children:
            if child.after and child.after != "-" and child.after in by_name:
                edge(child.after, child.name)
            elif (
                child.before
                and child.before != "-"
                and child.before in by_name
            ):
                edge(child.name, child.before)
        for child in first:
            for candidate in children:
                if candidate.name != child.name and candidate not in first:
                    edge(child.name, candidate.name)
        for child in last:
            for candidate in children:
                if candidate.name != child.name and candidate not in last:
                    edge(candidate.name, child.name)

        ready = sorted(
            (child for child in children if indegree[child.name] == 0),
            key=lambda item: (item.declaration_position, item.name),
        )
        ordered: list[_NodeState] = []
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for target in sorted(edges[current.name]):
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(by_name[target])
                    ready.sort(key=lambda item: (
                        item.declaration_position,
                        item.name,
                    ))
        if len(ordered) != len(children):
            remaining = sorted(
                (child for child in children if child not in ordered),
                key=lambda item: (item.declaration_position, item.name),
            )
            ordered.extend(remaining)
            source = min(
                (
                    provenance
                    for child in remaining
                    for provenance in child.provenance
                ),
                default=LayoutSource(".", 1, parent or "default"),
            )
            diagnostics.append(LayoutMergeDiagnostic(
                "magento-layout-order-cycle",
                "Layout sibling ordering contains a cycle below "
                f"{parent or '<root>'}",
                source,
            ))
        for child in ordered:
            order_by_name[child.name] = next_position
            next_position += 1

    return tuple(sorted(order_by_name.items())), tuple(sorted(diagnostics))


def _parent_cycle_diagnostics(
    states: Mapping[str, _NodeState],
) -> tuple[LayoutMergeDiagnostic, ...]:
    cycles: set[tuple[str, ...]] = set()
    for start in sorted(states):
        positions: dict[str, int] = {}
        chain: list[str] = []
        current = start
        while current in states:
            if current in positions:
                cycle = chain[positions[current]:]
                rotations = tuple(
                    tuple(cycle[index:] + cycle[:index])
                    for index in range(len(cycle))
                )
                cycles.add(min(rotations))
                break
            positions[current] = len(chain)
            chain.append(current)
            current = states[current].parent

    return tuple(sorted(
        LayoutMergeDiagnostic(
            "magento-layout-parent-cycle",
            "Layout parent relationship contains a cycle: "
            + " -> ".join((*cycle, cycle[0])),
            min(
                source
                for name in cycle
                for source in states[name].provenance
            ),
        )
        for cycle in cycles
    ))


def merge_layout(
    documents_by_handle: Mapping[str, Iterable[LayoutDocument]],
    requested_handles: Iterable[str],
) -> EffectiveLayout:
    """Resolve ordered Magento layout documents into one effective projection.

    Callers own module/theme/page-layout selection and pass documents in the
    exact load order for each handle. Handle updates are expanded once and
    cycles degrade to diagnostics while the remaining layout is retained.
    """

    normalized_documents = {
        handle: tuple(documents)
        for handle, documents in documents_by_handle.items()
    }
    requested = tuple(dict.fromkeys(
        handle.strip() for handle in requested_handles if handle.strip()
    ))
    expanded_handles: list[str] = []
    document_paths: list[str] = []
    operations: list[LayoutOperation] = []
    diagnostics: list[LayoutMergeDiagnostic] = []
    seen: set[str] = set()
    active: list[str] = []
    root_layout = ""

    def expand(handle: str, source: LayoutSource | None = None) -> None:
        nonlocal root_layout
        if handle in active:
            cycle = " -> ".join((*active[active.index(handle):], handle))
            diagnostics.append(LayoutMergeDiagnostic(
                "magento-layout-update-cycle",
                f"Layout handle update cycle detected: {cycle}",
                source or LayoutSource(".", 1, handle),
            ))
            return
        if handle in seen:
            return
        seen.add(handle)
        active.append(handle)
        documents = normalized_documents.get(handle, ())
        # Each physical handle document inherits its update handles before
        # that document's own instructions. A later document can therefore
        # include a handle that overrides an earlier document for the same
        # current handle.
        for document in documents:
            for operation in document.operations:
                if operation.kind == "update":
                    expand(operation.name, operation.source)
            if document.path not in document_paths:
                document_paths.append(document.path)
            if document.root_layout:
                root_layout = document.root_layout
            for operation in document.operations:
                if operation.kind != "update":
                    operations.append(operation)
        expanded_handles.append(handle)
        active.pop()

    for handle in requested:
        expand(handle)

    states: dict[str, _NodeState] = {}
    declaration_position = 0

    unresolved: list[LayoutOperation] = []
    pending_references: dict[str, list[LayoutOperation]] = {}
    moves: list[LayoutOperation] = []
    assets: dict[str, _AssetState] = {}
    for operation in operations:
        if operation.kind == "declare":
            previous = states.get(operation.name)
            if previous is not None:
                # Magento's duplicate-element workaround replaces the prior
                # scheduled element data and removes descendants that belonged
                # to that declaration. Later declarations may build a fresh
                # subtree below the replacement.
                descendants: set[str] = set()
                for candidate_name in states:
                    if candidate_name == operation.name:
                        continue
                    current_name = states[candidate_name].parent
                    visited: set[str] = set()
                    while current_name in states and current_name not in visited:
                        if current_name == operation.name:
                            descendants.add(candidate_name)
                            break
                        visited.add(current_name)
                        current_name = states[current_name].parent
                for descendant in descendants:
                    states.pop(descendant, None)
                position = previous.declaration_position
            else:
                position = declaration_position
                declaration_position += 1
            state = _NodeState(
                operation.name,
                operation.node_kind,
                position,
            )
            states[operation.name] = state
            for reference in pending_references.pop(operation.name, ()):
                if (
                    reference.node_kind
                    and reference.node_kind != operation.node_kind
                ):
                    unresolved.append(reference)
                    continue
                _apply_node_operation(state, reference)
            _apply_node_operation(state, operation)
        elif operation.kind == "reference":
            state = states.get(operation.name)
            if state is None:
                pending_references.setdefault(
                    operation.name,
                    [],
                ).append(operation)
                continue
            if (
                operation.node_kind
                and state.node_kind != operation.node_kind
            ):
                unresolved.append(operation)
                continue
            _apply_node_operation(state, operation)
        elif operation.kind == "move":
            moves.append(operation)
        elif operation.kind in {"asset", "remove-asset"}:
            state = assets.setdefault(
                operation.name,
                _AssetState(operation.name, operation.node_kind),
            )
            state.source = operation.source
            state.provenance.add(operation.source)
            state.attributes.update(dict(operation.attributes))
            if operation.node_kind:
                state.asset_kind = operation.node_kind
            state.removed = operation.kind == "remove-asset"

    unresolved.extend(
        reference
        for references in pending_references.values()
        for reference in references
    )

    # Magento schedules moves before removal generation. Resolve them only after
    # all declarations exist, then retain remove/display state on the moved node.
    for operation in moves:
        state = states.get(operation.name)
        destination = states.get(operation.parent)
        if state is None or destination is None:
            unresolved.append(operation)
            continue
        attributes = dict(operation.attributes)
        state.parent = operation.parent
        state.property_sources["parent"] = operation.source
        state.alias = attributes.get("as", state.alias or operation.name)
        if "as" in attributes:
            state.property_sources["alias"] = operation.source
        state.before = attributes.get("before", "")
        state.after = attributes.get("after", "")
        state.property_sources["before"] = operation.source
        state.property_sources["after"] = operation.source
        state.attributes.update(attributes)
        for key in attributes:
            state.property_sources[f"attribute:{key}"] = operation.source
        state.source = operation.source
        state.provenance.add(operation.source)

    diagnostics.extend(_parent_cycle_diagnostics(states))
    order_pairs, order_diagnostics = _ordered_nodes(states)
    diagnostics.extend(order_diagnostics)
    order = dict(order_pairs)
    nodes = tuple(sorted(
        (
            EffectiveLayoutNode(
                name=state.name,
                node_kind=state.node_kind,
                parent=state.parent,
                alias=state.alias,
                block_class=state.block_class,
                template=state.template,
                before=state.before,
                after=state.after,
                removed=state.removed,
                display=state.display,
                order=order.get(state.name, state.declaration_position),
                attributes=tuple(sorted(state.attributes.items())),
                arguments=tuple(sorted(state.arguments.values())),
                actions=tuple(state.actions),
                source=(
                    state.source
                    or min(state.provenance)
                ),
                property_sources=tuple(sorted(
                    LayoutPropertySource(property_name, property_source)
                    for property_name, property_source
                    in state.property_sources.items()
                )),
                provenance=tuple(sorted(state.provenance)),
            )
            for state in states.values()
        ),
        key=lambda item: (item.order, item.name),
    ))
    effective_assets = tuple(sorted(
        EffectiveLayoutAsset(
            src=state.src,
            asset_kind=state.asset_kind,
            removed=state.removed,
            attributes=tuple(sorted(state.attributes.items())),
            source=state.source or min(state.provenance),
            provenance=tuple(sorted(state.provenance)),
        )
        for state in assets.values()
    ))
    return EffectiveLayout(
        requested_handles=requested,
        expanded_handles=tuple(expanded_handles),
        document_paths=tuple(document_paths),
        root_layout=root_layout,
        nodes=nodes,
        assets=effective_assets,
        unresolved_operations=tuple(unresolved),
        diagnostics=tuple(sorted(diagnostics)),
    )
