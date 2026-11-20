from __future__ import annotations

import re
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Callable

from codecrow_plugins import GraphFact, PluginDiagnostic

from .architecture import ModuleRecord, PacketGraph, attrs, line, tag, view_area
from .dependency_injection import DependencyInjectionTopology
from .layout import merge_layout, parse_layout_document
from .resolution_index import RepositorySourceIndex
from .resolution_models import DiState, LayoutSources, ThemeRecord


class LayoutTopology:
    """Resolve effective layout composition and emit its exact source evidence."""

    def __init__(
        self,
        index: RepositorySourceIndex,
        graph: PacketGraph,
        di: DependencyInjectionTopology,
        layout_sources: LayoutSources,
    ) -> None:
        self.index = index
        self.graph = graph
        self.di = di
        self.layout_sources = layout_sources

    def effective_layouts(
        self,
        modules: tuple[ModuleRecord, ...],
        themes: tuple[ThemeRecord, ...],
        layout_by_handle: dict[tuple[str, str], list[str]],
        effective_layout_paths: Callable[
            [str, str, ThemeRecord | None], tuple[str, ...]
        ],
        layout_identity: Callable[
            [str, str],
            tuple[str, str, str, ThemeRecord | None] | None,
        ],
        di_states: dict[str, DiState] | None,
    ) -> None:
        """Emit the effective Magento layout for every provable theme selection.

        Module/theme file collection remains repository-aware here. Instruction
        parsing and mutation semantics live in ``layout.py`` so the large
        repository resolver only supplies ordered, runtime-compatible inputs.
        """

        module_order = {
            module.name: module.order
            for module in modules
            if module.enabled
        }
        parsed_documents = {}

        def parse_document(path: str, area: str, handle: str):
            identity = (path, area, handle)
            if identity in parsed_documents:
                return parsed_documents[identity]
            root = self.index.xml(path)
            if root is None:
                parsed_documents[identity] = None
                return None
            document = parse_layout_document(
                path=path,
                area=area,
                handle=handle,
                content=self.index.artifacts[path],
                root=root,
            )
            parsed_documents[identity] = document
            return document

        def selection_load_key(
            path: str,
            area: str,
            selected_theme: ThemeRecord | None,
        ) -> tuple[int, int, int, str]:
            theme = self.index.theme_for_path(path, themes)
            if theme is None:
                module = self.index.module_for_path(path, modules)
                source_area = view_area(path, "layout") or view_area(
                    path,
                    "page_layout",
                )
                return (
                    0 if source_area == "base" else 1,
                    module.order if module is not None else -1,
                    0,
                    path,
                )
            chain = (
                tuple(reversed(self.index.theme_chain(selected_theme, themes)))
                if selected_theme is not None
                else ()
            )
            theme_position = next(
                (
                    index
                    for index, candidate in enumerate(chain)
                    if candidate == theme
                ),
                len(chain),
            )
            theme_module = self.index.theme_module(path, theme)
            return (
                2,
                theme_position,
                module_order.get(theme_module, -1),
                path,
            )

        def selection_layout_paths(
            area: str,
            handle: str,
            selected_theme: ThemeRecord | None,
        ) -> tuple[str, ...]:
            candidates = effective_layout_paths(
                area,
                handle,
                selected_theme,
            )
            if selected_theme is None:
                candidates = tuple(
                    path
                    for path in candidates
                    if self.index.theme_for_path(path, themes) is None
                )
            return tuple(sorted(
                candidates,
                key=lambda path: selection_load_key(
                    path,
                    area,
                    selected_theme,
                ),
            ))

        page_layout_by_id: dict[tuple[str, str], list[str]] = {}
        page_layout_identities: dict[
            str,
            tuple[str, str, str, ThemeRecord | None],
        ] = {}
        layout_declarations: dict[tuple[str, str], set[str]] = {}
        for path in sorted(self.index.artifacts):
            theme = self.index.theme_for_path(path, themes)
            if not self.index.is_deployed_view_source(path, modules, themes):
                continue
            normalized = f"/{path}"
            if "/page_layout/" in normalized and path.endswith(".xml"):
                area = view_area(path, "page_layout") or (
                    theme.area if theme else None
                )
                if area is not None:
                    page_layout_by_id.setdefault(
                        (area, PurePosixPath(path).stem),
                        [],
                    ).append(path)
                    identity = layout_identity(path, "page_layout")
                    if identity is not None:
                        page_layout_identities[path] = identity
                continue
            if (
                PurePosixPath(path).name != "layouts.xml"
                or "/page_layout/" in normalized
            ):
                continue
            area_match = re.search(
                r"/view/(base|frontend|adminhtml)/layouts\.xml$",
                normalized,
            )
            area = (
                area_match.group(1)
                if area_match is not None
                else (theme.area if theme else None)
            )
            if area is None:
                continue
            root = self.index.xml(path)
            if root is None:
                continue
            content = self.index.artifacts[path]
            for element in root.iter():
                if tag(element) != "layout" or not element.get("id"):
                    continue
                layout_id = element.get("id", "").strip()
                if not layout_id:
                    continue
                layout_declarations.setdefault((area, layout_id), set()).add(
                    path
                )
                packet = self.graph.packet(
                    "magento-page-layout",
                    f"{area}:{layout_id}",
                    area=area,
                    pageLayout=layout_id,
                )
                packet.add(GraphFact(
                    "magento-page-layout-declaration",
                    layout_id,
                    "declared-in",
                    path,
                    path,
                    line(content, layout_id),
                    attrs(
                        area=area,
                        label=next(
                            (
                                (child.text or "").strip()
                                for child in element
                                if tag(child) == "label"
                            ),
                            element.get("label", ""),
                        ),
                        theme=theme.name if theme else "",
                    ),
                ), theme.theme_xml if theme else "")

        def selection_page_layout_paths(
            area: str,
            layout_id: str,
            selected_theme: ThemeRecord | None,
        ) -> tuple[str, ...]:
            allowed_theme_roots = (
                {
                    candidate.root
                    for candidate in self.index.theme_chain(
                        selected_theme,
                        themes,
                    )
                }
                if selected_theme is not None
                else set()
            )
            paths = {
                path
                for candidate_area in (
                    ("base", area) if area != "base" else ("base",)
                )
                for path in page_layout_by_id.get(
                    (candidate_area, layout_id),
                    (),
                )
                if (
                    (candidate_theme := self.index.theme_for_path(path, themes))
                    is None
                    or (
                        selected_theme is not None
                        and candidate_theme.root in allowed_theme_roots
                    )
                )
            }
            if selected_theme is not None:
                effective_by_identity = {
                    identity[0]: path
                    for path in sorted(paths)
                    if (
                        (identity := page_layout_identities.get(path))
                        is not None
                        and identity[3] is None
                    )
                }
                for current_theme in reversed(
                    self.index.theme_chain(selected_theme, themes)
                ):
                    theme_paths = tuple(sorted(
                        path
                        for path in paths
                        if (
                            (identity := page_layout_identities.get(path))
                            is not None
                            and identity[3] == current_theme
                        )
                    ))
                    for path in theme_paths:
                        identity = page_layout_identities[path]
                        if not identity[1].startswith("override-"):
                            effective_by_identity[identity[0]] = path
                    for path in theme_paths:
                        identity = page_layout_identities[path]
                        if (
                            identity[1].startswith("override-")
                            and identity[0] in effective_by_identity
                        ):
                            effective_by_identity[identity[0]] = path
                paths = set(effective_by_identity.values())
            return tuple(sorted(
                paths,
                key=lambda path: selection_load_key(
                    path,
                    area,
                    selected_theme,
                ),
            ))

        runtime_areas = {
            area
            for area, _ in layout_by_handle
            if area != "base"
        }
        runtime_areas.update(theme.area for theme in themes)
        if not runtime_areas and any(
            area == "base" for area, _ in layout_by_handle
        ):
            runtime_areas.add("base")

        emitted_diagnostics: set[tuple[str, str, int]] = set()
        for area in sorted(runtime_areas):
            global_di_state = (di_states or {}).get("global", DiState())
            area_di_state = (di_states or {}).get(area, global_di_state)
            area_themes = tuple(
                theme for theme in themes if theme.area == area
            )
            selections: tuple[ThemeRecord | None, ...] = (
                (None, *area_themes)
                if modules
                else area_themes
            )
            for selected_theme in selections:
                selection_name = (
                    selected_theme.name
                    if selected_theme is not None
                    else "module-only"
                )
                selection_theme_roots = (
                    {
                        candidate.root
                        for candidate in self.index.theme_chain(
                            selected_theme,
                            themes,
                        )
                    }
                    if selected_theme is not None
                    else set()
                )

                def selection_compatible_path(path: str) -> bool:
                    candidate_theme = self.index.theme_for_path(path, themes)
                    return (
                        candidate_theme is None
                        or (
                            selected_theme is not None
                            and candidate_theme.root in selection_theme_roots
                        )
                    )

                handles = sorted({
                    handle
                    for candidate_area in (
                        ("base", area) if area != "base" else ("base",)
                    )
                    for handle in (
                        candidate_handle
                        for source_area, candidate_handle in layout_by_handle
                        if source_area == candidate_area
                    )
                }, key=lambda candidate: (
                    candidate != "default",
                    candidate,
                ))
                documents_by_handle = {}
                default_nodes_by_name = {}
                default_missing_parents: set[str] = set()
                default_assets_by_src = {}
                default_unresolved_operations = set()
                for handle in handles:
                    documents = tuple(filter(None, (
                        parse_document(path, area, handle)
                        for path in selection_layout_paths(
                            area,
                            handle,
                            selected_theme,
                        )
                    )))
                    if documents:
                        documents_by_handle[handle] = documents

                page_documents_by_handle = {}
                page_layout_ids = sorted({
                    layout_id
                    for candidate_area in (
                        ("base", area) if area != "base" else ("base",)
                    )
                    for source_area, layout_id in page_layout_by_id
                    if source_area == candidate_area
                })
                for layout_id in page_layout_ids:
                    page_handle = f"page_layout:{layout_id}"
                    page_documents = []
                    for path in selection_page_layout_paths(
                        area,
                        layout_id,
                        selected_theme,
                    ):
                        document = parse_document(
                            path,
                            area,
                            page_handle,
                        )
                        if document is None:
                            continue
                        # Page-layout <update> instructions inherit another
                        # page-layout wireframe, not an ordinary page
                        # configuration handle with the same basename.
                        page_documents.append(replace(
                            document,
                            operations=tuple(
                                replace(
                                    operation,
                                    name=f"page_layout:{operation.name}",
                                )
                                if operation.kind == "update"
                                else operation
                                for operation in document.operations
                            ),
                        ))
                    if page_documents:
                        page_documents_by_handle[page_handle] = tuple(
                            page_documents
                        )

                for handle in handles:
                    if handle not in documents_by_handle:
                        continue
                    generic_layout = all(
                        document.root_kind == "layout"
                        for document in documents_by_handle[handle]
                    )
                    requested_handles = (
                        (handle,)
                        if handle == "default" or generic_layout
                        else ("default", handle)
                    )
                    effective = merge_layout(
                        documents_by_handle,
                        requested_handles,
                    )
                    page_layout_id = effective.root_layout
                    page_layout_paths: tuple[str, ...] = ()
                    if page_layout_id:
                        page_handle = f"page_layout:{page_layout_id}"
                        page_documents = page_documents_by_handle.get(
                            page_handle,
                            (),
                        )
                        if page_documents:
                            effective = merge_layout(
                                {
                                    **documents_by_handle,
                                    **page_documents_by_handle,
                                },
                                (page_handle, *requested_handles),
                            )
                            page_layout_paths = tuple(sorted({
                                path
                                for path in effective.document_paths
                                if path in page_layout_identities
                            }))

                    if not effective.document_paths:
                        continue
                    current_nodes_by_name = {
                        node.name: node for node in effective.nodes
                    }
                    suppressed_inherited_names = (
                        set()
                        if generic_layout
                        else (
                            set(default_nodes_by_name)
                            - set(current_nodes_by_name)
                        )
                    )
                    inherited_node_changed = any(
                        current_nodes_by_name.get(name) != baseline
                        for name, baseline in default_nodes_by_name.items()
                    )
                    newly_resolved_parent = bool(
                        (
                            set(current_nodes_by_name)
                            - set(default_nodes_by_name)
                        ).intersection(default_missing_parents)
                    )
                    emit_full_projection = (
                        handle == "default"
                        or generic_layout
                        or not default_nodes_by_name
                        or inherited_node_changed
                        or newly_resolved_parent
                    )
                    packet = self.graph.packet(
                        "magento-effective-layout",
                        f"{area}:{selection_name}:{handle}",
                        area=area,
                        handle=handle,
                        themeSelection=selection_name,
                    )
                    primary_path = effective.document_paths[-1]
                    related_documents = tuple(sorted(
                        set(effective.document_paths) - {primary_path}
                    ))
                    packet.add(GraphFact(
                        "magento-effective-layout",
                        handle,
                        "composes-handles",
                        ",".join(effective.expanded_handles),
                        primary_path,
                        1,
                        attrs(
                            area=area,
                            documentKind=(
                                "generic-layout"
                                if generic_layout
                                else "page-configuration"
                            ),
                            rootLayout=effective.root_layout,
                            emissionMode=(
                                "full"
                                if emit_full_projection
                                else "delta-from-default"
                            ),
                            semanticRole="topology",
                            themeSelection=selection_name,
                        ),
                    ), *related_documents)

                    if page_layout_id:
                        page_source = next(
                            (
                                document
                                for source_handle in reversed(
                                    requested_handles
                                )
                                for document in reversed(
                                    documents_by_handle.get(
                                        source_handle,
                                        (),
                                    )
                                )
                                if document.root_layout == page_layout_id
                            ),
                            None,
                        )
                        page_source_path = (
                            page_source.path if page_source else primary_path
                        )
                        declaration_paths = tuple(sorted({
                            path
                            for candidate_area in {"base", area}
                            for path in layout_declarations.get(
                                (candidate_area, page_layout_id),
                                (),
                            )
                            if selection_compatible_path(path)
                        }))
                        packet.add(GraphFact(
                            "magento-page-layout-selection",
                            handle,
                            "selects-page-layout",
                            page_layout_id,
                            page_source_path,
                            line(
                                self.index.artifacts[page_source_path],
                                page_layout_id,
                            ),
                            attrs(
                                area=area,
                                resolved=str(bool(page_layout_paths)).lower(),
                                themeSelection=selection_name,
                            ),
                        ), *page_layout_paths, *declaration_paths)

                    nodes_by_name = current_nodes_by_name
                    rendering_state_by_name = {}
                    for candidate_name in nodes_by_name:
                        current_name = candidate_name
                        visited: set[str] = set()
                        rendering_state = "rendered"
                        has_ifconfig = False
                        has_acl = False
                        while current_name:
                            if current_name in visited:
                                rendering_state = "unresolved-parent-cycle"
                                break
                            visited.add(current_name)
                            candidate = nodes_by_name.get(current_name)
                            if candidate is None:
                                rendering_state = "unresolved-parent"
                                break
                            if candidate.removed:
                                rendering_state = (
                                    "removed"
                                    if current_name == candidate_name
                                    else "ancestor-removed"
                                )
                                break
                            if candidate.display is False:
                                rendering_state = (
                                    "display-disabled"
                                    if current_name == candidate_name
                                    else "ancestor-display-disabled"
                                )
                                break
                            candidate_attributes = dict(
                                candidate.attributes
                            )
                            has_ifconfig = has_ifconfig or bool(
                                candidate_attributes.get("ifconfig", "")
                            )
                            has_acl = has_acl or bool(
                                candidate_attributes.get("aclResource", "")
                            )
                            current_name = candidate.parent
                        if rendering_state == "rendered":
                            if has_ifconfig and has_acl:
                                rendering_state = "conditional-ifconfig-acl"
                            elif has_ifconfig:
                                rendering_state = "conditional-ifconfig"
                            elif has_acl:
                                rendering_state = "conditional-acl"
                        rendering_state_by_name[candidate_name] = (
                            rendering_state
                        )

                    for suppressed_name in sorted(
                        suppressed_inherited_names
                    ):
                        baseline = default_nodes_by_name[suppressed_name]
                        suppression_path = primary_path
                        suppression_line = 1
                        ancestor_name = baseline.parent
                        visited_ancestors: set[str] = set()
                        while (
                            ancestor_name
                            and ancestor_name not in visited_ancestors
                        ):
                            visited_ancestors.add(ancestor_name)
                            current_ancestor = current_nodes_by_name.get(
                                ancestor_name
                            )
                            baseline_ancestor = default_nodes_by_name.get(
                                ancestor_name
                            )
                            if current_ancestor != baseline_ancestor:
                                if current_ancestor is not None:
                                    suppression_path = (
                                        current_ancestor.source.path
                                    )
                                    suppression_line = (
                                        current_ancestor.source.line
                                    )
                                break
                            ancestor_name = (
                                baseline_ancestor.parent
                                if baseline_ancestor is not None
                                else ""
                            )
                        baseline_paths = tuple(sorted({
                            source.path for source in baseline.provenance
                        }))
                        packet.add(GraphFact(
                            "magento-layout-effective-node",
                            handle,
                            "suppresses-inherited-node",
                            suppressed_name,
                            suppression_path,
                            suppression_line,
                            attrs(
                                area=area,
                                baselineHandle="default",
                                blockClass=baseline.block_class,
                                handle=handle,
                                nodeKind=baseline.node_kind,
                                parent=baseline.parent,
                                renderingState=(
                                    "suppressed-by-redeclaration"
                                ),
                                semanticRole="topology",
                                template=baseline.template,
                                themeSelection=selection_name,
                            ),
                        ), *baseline_paths)

                        configured_class = baseline.block_class
                        suppressed_class = (
                            self.di.resolve_type(
                                configured_class,
                                area_di_state,
                            )
                            if configured_class
                            else ""
                        )
                        if suppressed_class:
                            packet.add(GraphFact(
                                "magento-layout-effective-block-class",
                                suppressed_name,
                                "suppresses-inherited-block-class",
                                suppressed_class,
                                suppression_path,
                                suppression_line,
                                attrs(
                                    area=area,
                                    baselineHandle="default",
                                    configuredBlockClass=configured_class,
                                    handle=handle,
                                    renderingState=(
                                        "suppressed-by-redeclaration"
                                    ),
                                    themeSelection=selection_name,
                                ),
                            ), self.index.symbol_path(suppressed_class),
                                *baseline_paths)

                        baseline_property_sources = {
                            item.property: item.source
                            for item in baseline.property_sources
                        }
                        conditional_templates = []
                        for baseline_action in baseline.actions:
                            if (
                                baseline_action.method.casefold()
                                != "settemplate"
                                or not baseline_action.arguments
                            ):
                                continue
                            template_argument = (
                                baseline_action.arguments[0]
                            )
                            if (
                                not template_argument.value
                                or template_argument.value_type
                                not in {"", "string"}
                            ):
                                continue
                            if baseline_action.ifconfig:
                                conditional_templates.append((
                                    template_argument.value,
                                    baseline_action.ifconfig,
                                    template_argument.source.path,
                                ))
                            else:
                                conditional_templates.clear()
                        template_variants = []
                        if baseline.template:
                            template_source = baseline_property_sources.get(
                                "template",
                                baseline.source,
                            )
                            template_variants.append((
                                baseline.template,
                                "",
                                template_source.path,
                            ))
                        template_variants.extend(conditional_templates)
                        selection_themes = (
                            themes if selected_theme is not None else ()
                        )
                        for (
                            suppressed_template,
                            template_ifconfig,
                            template_declaration_path,
                        ) in dict.fromkeys(template_variants):
                            template_paths = self.index.template_paths(
                                suppressed_template,
                                area,
                                modules,
                                selection_themes,
                                selected_theme,
                            )
                            suppressed_template_path = (
                                self.index.selected_template_path(
                                    suppressed_template,
                                    area,
                                    modules,
                                    selection_themes,
                                    selected_theme,
                                )
                            )
                            packet.add(GraphFact(
                                "magento-layout-effective-template",
                                suppressed_name,
                                "suppresses-inherited-template",
                                (
                                    suppressed_template_path
                                    or suppressed_template
                                ),
                                suppression_path,
                                suppression_line,
                                attrs(
                                    area=area,
                                    baselineHandle="default",
                                    handle=handle,
                                    ifconfig=template_ifconfig,
                                    renderingState=(
                                        "suppressed-by-redeclaration"
                                    ),
                                    template=suppressed_template,
                                    themeSelection=selection_name,
                                ),
                            ), *template_paths, *baseline_paths,
                                template_declaration_path)
                            if suppressed_template_path and suppressed_class:
                                packet.add(GraphFact(
                                    "magento-template-effective-block-binding",
                                    suppressed_template_path,
                                    "suppressed-in-effective-layout",
                                    suppressed_class,
                                    suppressed_template_path,
                                    1,
                                    attrs(
                                        activationCertainty="inactive",
                                        area=area,
                                        baselineHandle="default",
                                        blockName=suppressed_name,
                                        handle=handle,
                                        ifconfig=template_ifconfig,
                                        renderingState=(
                                            "suppressed-by-redeclaration"
                                        ),
                                        themeSelection=selection_name,
                                    ),
                                    related_paths=tuple(sorted(set(filter(None, (
                                        suppression_path,
                                        template_declaration_path,
                                        *baseline_paths,
                                        self.index.symbol_path(suppressed_class),
                                    ))))),
                                ))

                        for argument in baseline.arguments:
                            if (
                                argument.value_type != "object"
                                or not argument.value
                            ):
                                continue
                            object_class = argument.value.lstrip("\\")
                            packet.add(GraphFact(
                                "magento-layout-effective-object-argument",
                                suppressed_name,
                                "suppresses-inherited-layout-object",
                                object_class,
                                suppression_path,
                                suppression_line,
                                attrs(
                                    area=area,
                                    argument=argument.name,
                                    baselineHandle="default",
                                    handle=handle,
                                    renderingState=(
                                        "suppressed-by-redeclaration"
                                    ),
                                    themeSelection=selection_name,
                                ),
                            ), self.index.symbol_path(object_class),
                                argument.source.path,
                                *baseline_paths)

                        for action in baseline.actions:
                            action_target = (
                                f"{suppressed_class}::{action.method}"
                                if suppressed_class
                                else action.method
                            )
                            packet.add(GraphFact(
                                "magento-layout-effective-action",
                                suppressed_name,
                                "suppresses-inherited-layout-action",
                                action_target,
                                suppression_path,
                                suppression_line,
                                attrs(
                                    area=area,
                                    baselineHandle="default",
                                    handle=handle,
                                    method=action.method,
                                    renderingState=(
                                        "suppressed-by-redeclaration"
                                    ),
                                    themeSelection=selection_name,
                                ),
                            ), action.source.path, *baseline_paths)

                        if baseline.node_kind == "uiComponent":
                            ui_paths = tuple(sorted(
                                path
                                for path in self.index.ui_component_paths_by_name.get(
                                    suppressed_name,
                                    (),
                                )
                                if self.index.is_deployed_view_source(
                                    path,
                                    modules,
                                    themes,
                                )
                                and (
                                    (
                                        self.index.theme_for_path(path, themes)
                                        is not None
                                        and selection_compatible_path(path)
                                    )
                                    or (
                                        self.index.theme_for_path(path, themes)
                                        is None
                                        and view_area(
                                            path,
                                            "ui_component",
                                        ) in {"base", area}
                                    )
                                )
                            ))
                            packet.add(GraphFact(
                                "magento-layout-ui-component-activation",
                                handle,
                                "suppresses-inherited-ui-component",
                                suppressed_name,
                                suppression_path,
                                suppression_line,
                                attrs(
                                    area=area,
                                    baselineHandle="default",
                                    renderingState=(
                                        "suppressed-by-redeclaration"
                                    ),
                                    resolved=str(bool(ui_paths)).lower(),
                                    themeSelection=selection_name,
                                ),
                            ), *ui_paths, *baseline_paths)

                    for node in effective.nodes:
                        property_sources = {
                            item.property: item.source
                            for item in node.property_sources
                        }
                        provenance_paths = tuple(sorted({
                            source.path for source in node.provenance
                        }))
                        if (
                            not emit_full_projection
                            and node.name in default_nodes_by_name
                        ):
                            continue
                        rendering_state = rendering_state_by_name[node.name]
                        renders = rendering_state == "rendered"
                        potentially_renders = renders or (
                            rendering_state.startswith("conditional-")
                        ) or (
                            rendering_state == "unresolved-parent"
                        )
                        node_configuration = dict(node.attributes)
                        configured_block_class = node.block_class
                        effective_block_class = (
                            self.di.resolve_type(
                                configured_block_class,
                                area_di_state,
                            )
                            if configured_block_class
                            else ""
                        )
                        block_resolution_paths = (
                            self.di.resolution_paths(
                                configured_block_class,
                                area_di_state,
                            )
                            if configured_block_class
                            else ()
                        )
                        block_class_defaulted = (
                            node_configuration.get(
                                "blockClassDefaulted",
                                "",
                            ).casefold()
                            == "true"
                        )
                        node_attributes = attrs(
                            after=node.after,
                            alias=node.alias,
                            area=area,
                            before=node.before,
                            blockClass=effective_block_class,
                            blockClassDefaulted=str(
                                block_class_defaulted
                            ).lower(),
                            configuredBlockClass=configured_block_class,
                            display=(
                                str(node.display).lower()
                                if node.display is not None
                                else ""
                            ),
                            handle=handle,
                            ifconfig=node_configuration.get("ifconfig", ""),
                            nodeKind=node.node_kind,
                            order=node.order,
                            parent=node.parent,
                            removed=str(node.removed).lower(),
                            renderingState=rendering_state,
                            aclResource=node_configuration.get(
                                "aclResource",
                                "",
                            ),
                            semanticRole=(
                                "uncertainty"
                                if rendering_state == "unresolved-parent"
                                else "topology"
                            ),
                            template=node.template,
                            themeSelection=selection_name,
                        )
                        packet.add(GraphFact(
                            "magento-layout-effective-node",
                            handle,
                            (
                                "removes-node"
                                if node.removed
                                else "contains-node"
                            ),
                            node.name,
                            node.source.path,
                            node.source.line,
                            node_attributes,
                        ), *(
                            path for path in provenance_paths
                            if path != node.source.path
                        ))

                        if node.parent:
                            parent = nodes_by_name.get(node.parent)
                            parent_paths = (
                                tuple(
                                    source.path
                                    for source in parent.provenance
                                )
                                if parent is not None
                                else ()
                            )
                            parent_source = property_sources.get(
                                "parent",
                                node.source,
                            )
                            packet.add(GraphFact(
                                "magento-layout-effective-parent",
                                node.name,
                                "placed-in",
                                node.parent,
                                parent_source.path,
                                parent_source.line,
                                attrs(
                                    alias=node.alias,
                                    area=area,
                                    handle=handle,
                                    order=node.order,
                                    resolved=str(parent is not None).lower(),
                                    semanticRole=(
                                        "topology"
                                        if parent is not None
                                        else "uncertainty"
                                    ),
                                    themeSelection=selection_name,
                                ),
                            ), *provenance_paths, *parent_paths)

                        if effective_block_class:
                            class_source = property_sources.get(
                                "blockClass",
                                node.source,
                            )
                            packet.add(GraphFact(
                                "magento-layout-effective-block-class",
                                node.name,
                                (
                                    "uses-block-class"
                                    if rendering_state in {
                                        "rendered",
                                        "display-disabled",
                                        "ancestor-display-disabled",
                                    }
                                    else (
                                        "conditionally-uses-block-class"
                                        if potentially_renders
                                        else "declares-inactive-block-class"
                                    )
                                ),
                                effective_block_class,
                                class_source.path,
                                class_source.line,
                                attrs(
                                    area=area,
                                    blockClassDefaulted=str(
                                        block_class_defaulted
                                    ).lower(),
                                    configuredBlockClass=(
                                        configured_block_class
                                    ),
                                    handle=handle,
                                    renderingState=rendering_state,
                                    themeSelection=selection_name,
                                ),
                            ), self.index.symbol_path(effective_block_class),
                                *block_resolution_paths,
                                *provenance_paths)

                        selected_template_path = ""
                        template_paths: tuple[str, ...] = ()
                        template_binding_candidates: list[
                            tuple[str, bool]
                        ] = []
                        selection_themes = (
                            themes if selected_theme is not None else ()
                        )
                        conditional_template_actions = []
                        for candidate_action in node.actions:
                            if (
                                candidate_action.method.casefold()
                                != "settemplate"
                                or not candidate_action.arguments
                            ):
                                continue
                            candidate_argument = (
                                candidate_action.arguments[0]
                            )
                            if (
                                not candidate_argument.value
                                or candidate_argument.value_type
                                not in {"", "string"}
                            ):
                                continue
                            if candidate_action.ifconfig:
                                conditional_template_actions.append((
                                    candidate_action,
                                    candidate_argument,
                                ))
                            else:
                                # A later unconditional setTemplate call wins
                                # over every earlier conditional candidate.
                                conditional_template_actions.clear()
                        has_conditional_template = bool(
                            conditional_template_actions
                        )
                        if node.template:
                            template_paths = self.index.template_paths(
                                node.template,
                                area,
                                modules,
                                selection_themes,
                                selected_theme,
                            )
                            selected_template_path = self.index.selected_template_path(
                                node.template,
                                area,
                                modules,
                                selection_themes,
                                selected_theme,
                            )
                            if selected_template_path and potentially_renders:
                                template_binding_candidates.append((
                                    selected_template_path,
                                    renders and not has_conditional_template,
                                ))
                            template_source = property_sources.get(
                                "template",
                                node.source,
                            )
                            packet.add(GraphFact(
                                "magento-layout-effective-template",
                                node.name,
                                (
                                    "renders-template"
                                    if renders and not has_conditional_template
                                    else (
                                        "conditionally-renders-template"
                                        if potentially_renders
                                        else "declares-inactive-template"
                                    )
                                ),
                                selected_template_path or node.template,
                                template_source.path,
                                template_source.line,
                                attrs(
                                    area=area,
                                    handle=handle,
                                    template=node.template,
                                    themeSelection=selection_name,
                                    renderingState=rendering_state,
                                    conditionalTemplateCandidate=(
                                        node_configuration.get(
                                            "conditionalTemplateCandidate",
                                            "",
                                        )
                                    ),
                                    conditionalTemplateIfconfig=(
                                        node_configuration.get(
                                            "conditionalTemplateIfconfig",
                                            "",
                                        )
                                    ),
                                ),
                            ), *template_paths, *provenance_paths)
                            if (
                                selected_template_path
                                and renders
                                and not has_conditional_template
                            ):
                                self.layout_sources.templates.setdefault(
                                    selected_template_path,
                                    set(),
                                ).add((template_source.path, area, handle))
                            elif selected_template_path and potentially_renders:
                                self.layout_sources.conditional_templates.setdefault(
                                    selected_template_path,
                                    set(),
                                ).add((template_source.path, area, handle))

                        for (
                            candidate_action,
                            candidate_argument,
                        ) in conditional_template_actions:
                            conditional_template = candidate_argument.value
                            candidate_paths = self.index.template_paths(
                                conditional_template,
                                area,
                                modules,
                                selection_themes,
                                selected_theme,
                            )
                            candidate_path = self.index.selected_template_path(
                                conditional_template,
                                area,
                                modules,
                                selection_themes,
                                selected_theme,
                            )
                            if candidate_path and potentially_renders:
                                template_binding_candidates.append((
                                    candidate_path,
                                    False,
                                ))
                                self.layout_sources.conditional_templates.setdefault(
                                    candidate_path,
                                    set(),
                                ).add((
                                    candidate_argument.source.path,
                                    area,
                                    handle,
                                ))
                            packet.add(GraphFact(
                                "magento-layout-effective-template",
                                node.name,
                                (
                                    "conditionally-renders-template"
                                    if potentially_renders
                                    else "declares-inactive-template"
                                ),
                                candidate_path or conditional_template,
                                candidate_argument.source.path,
                                candidate_argument.source.line,
                                attrs(
                                    area=area,
                                    handle=handle,
                                    ifconfig=candidate_action.ifconfig,
                                    renderingState=rendering_state,
                                    template=conditional_template,
                                    themeSelection=selection_name,
                                ),
                            ), *candidate_paths, *provenance_paths)

                        for (
                            binding_template_path,
                            binding_is_exact,
                        ) in dict.fromkeys(template_binding_candidates):
                            if not effective_block_class:
                                continue
                            block_symbol = self.index.unique_symbol_casefold(
                                effective_block_class
                            )
                            packet.add(GraphFact(
                                "magento-template-effective-block-binding",
                                binding_template_path,
                                (
                                    "rendered-by-effective-block"
                                    if binding_is_exact
                                    else (
                                        "conditionally-rendered-by-"
                                        "effective-block"
                                    )
                                ),
                                effective_block_class,
                                binding_template_path,
                                1,
                                attrs(
                                    activationCertainty=(
                                        "exact"
                                        if binding_is_exact
                                        else "conditional"
                                    ),
                                    area=area,
                                    configuredBlockClass=(
                                        configured_block_class
                                    ),
                                    blockName=node.name,
                                    handle=handle,
                                    themeSelection=selection_name,
                                ),
                                related_paths=tuple(sorted(filter(None, (
                                    *provenance_paths,
                                    *block_resolution_paths,
                                    block_symbol.path if block_symbol else "",
                                )))),
                            ))
                            for call in self.index.template_php_calls(
                                binding_template_path,
                                "block",
                            ):
                                argument_name = self.index.layout_argument_for_call(
                                    call,
                                    node.arguments,
                                )
                                if argument_name:
                                    argument = next(
                                        candidate
                                        for candidate in node.arguments
                                        if candidate.name == argument_name
                                    )
                                    packet.add(GraphFact(
                                        "magento-template-effective-layout-argument-read",
                                        binding_template_path,
                                        (
                                            "reads-effective-layout-argument"
                                            if binding_is_exact
                                            else (
                                                "conditionally-reads-effective-"
                                                "layout-argument"
                                            )
                                        ),
                                        argument.value or argument.name,
                                        binding_template_path,
                                        call.line,
                                        attrs(
                                            activationCertainty=(
                                                "exact"
                                                if binding_is_exact
                                                else "conditional"
                                            ),
                                            argument=argument.name,
                                            area=area,
                                            blockName=node.name,
                                            handle=handle,
                                            method=call.method,
                                            valueType=argument.value_type,
                                            themeSelection=selection_name,
                                        ),
                                        related_paths=tuple(sorted({
                                            *provenance_paths,
                                            argument.source.path,
                                        })),
                                    ))
                                declaration = (
                                    self.index.method_symbol(
                                        block_symbol,
                                        call.method,
                                    )
                                    if block_symbol is not None
                                    else None
                                )
                                if declaration is not None:
                                    declaring_symbol, declared_method = declaration
                                    packet.add(GraphFact(
                                        "magento-template-effective-block-method-call",
                                        binding_template_path,
                                        (
                                            "calls-effective-block-method"
                                            if binding_is_exact
                                            else (
                                                "conditionally-calls-effective-"
                                                "block-method"
                                            )
                                        ),
                                        (
                                            f"{declaring_symbol.qualified_name}::"
                                            f"{declared_method}"
                                        ),
                                        binding_template_path,
                                        call.line,
                                        attrs(
                                            activationCertainty=(
                                                "exact"
                                                if binding_is_exact
                                                else "conditional"
                                            ),
                                            area=area,
                                            blockClass=effective_block_class,
                                            blockName=node.name,
                                            handle=handle,
                                            themeSelection=selection_name,
                                        ),
                                        related_paths=tuple(sorted({
                                            *provenance_paths,
                                            *block_resolution_paths,
                                            declaring_symbol.path,
                                        })),
                                    ))
                                    continue
                                packet.add(GraphFact(
                                    "magento-template-effective-block-method-call-unresolved",
                                    binding_template_path,
                                    (
                                        "calls-unresolved-effective-block-method"
                                        if binding_is_exact
                                        else (
                                            "conditionally-calls-unresolved-"
                                            "effective-block-method"
                                        )
                                    ),
                                    f"{effective_block_class}::{call.method}",
                                    binding_template_path,
                                    call.line,
                                    attrs(
                                        activationCertainty=(
                                            "exact"
                                            if binding_is_exact
                                            else "conditional"
                                        ),
                                        area=area,
                                        blockClass=effective_block_class,
                                        blockName=node.name,
                                        handle=handle,
                                        semanticRole="uncertainty",
                                        themeSelection=selection_name,
                                    ),
                                    related_paths=tuple(sorted({
                                        *provenance_paths,
                                        *block_resolution_paths,
                                    })),
                                ))

                        for argument in node.arguments:
                            if argument.value_type != "object" or not argument.value:
                                continue
                            object_class = argument.value.lstrip("\\")
                            object_symbol = self.index.unique_symbol_casefold(
                                object_class
                            )
                            object_inactive = rendering_state in {
                                "removed",
                                "ancestor-removed",
                                "unresolved-parent-cycle",
                            }
                            packet.add(GraphFact(
                                "magento-layout-effective-object-argument",
                                node.name,
                                (
                                    "declares-inactive-layout-object"
                                    if object_inactive
                                    else (
                                        "conditionally-receives-layout-object"
                                        if potentially_renders and not renders
                                        else "receives-layout-object"
                                    )
                                ),
                                object_class,
                                argument.source.path,
                                argument.source.line,
                                attrs(
                                    area=area,
                                    argument=argument.name,
                                    handle=handle,
                                    renderingState=rendering_state,
                                    themeSelection=selection_name,
                                ),
                            ), *provenance_paths, (
                                object_symbol.path if object_symbol else ""
                            ))
                            if selected_template_path and potentially_renders:
                                template_relation = (
                                    "receives-layout-object"
                                    if renders
                                    else "conditionally-receives-layout-object"
                                )
                                packet.add(GraphFact(
                                    "magento-template-effective-object-binding",
                                    selected_template_path,
                                    template_relation,
                                    object_class,
                                    selected_template_path,
                                    1,
                                    attrs(
                                        argument=argument.name,
                                        area=area,
                                        blockName=node.name,
                                        handle=handle,
                                        renderingState=rendering_state,
                                        themeSelection=selection_name,
                                    ),
                                    related_paths=tuple(sorted(filter(None, (
                                        *provenance_paths,
                                        object_symbol.path if object_symbol else "",
                                    )))),
                                ))
                            if (
                                selected_template_path
                                and potentially_renders
                                and self.index.is_view_model_argument(
                                    argument.name,
                                    object_symbol,
                                )
                            ):
                                packet.add(GraphFact(
                                    "magento-template-effective-view-model-binding",
                                    selected_template_path,
                                    (
                                        "receives-view-model"
                                        if renders
                                        else "conditionally-receives-view-model"
                                    ),
                                    object_class,
                                    selected_template_path,
                                    1,
                                    attrs(
                                        argument=argument.name,
                                        area=area,
                                        blockName=node.name,
                                        handle=handle,
                                        renderingState=rendering_state,
                                        themeSelection=selection_name,
                                    ),
                                    related_paths=tuple(sorted(filter(None, (
                                        *provenance_paths,
                                        object_symbol.path if object_symbol else "",
                                    )))),
                                ))

                        for action in node.actions:
                            target = (
                                f"{effective_block_class}::{action.method}"
                                if effective_block_class
                                else action.method
                            )
                            method_paths: tuple[str, ...] = ()
                            if effective_block_class:
                                block_symbol = self.index.unique_symbol_casefold(
                                    effective_block_class
                                )
                                declaration = (
                                    self.index.method_symbol(
                                        block_symbol,
                                        action.method,
                                    )
                                    if block_symbol is not None
                                    else None
                                )
                                if declaration is not None:
                                    target = (
                                        f"{declaration[0].qualified_name}::"
                                        f"{declaration[1]}"
                                    )
                                    method_paths = (declaration[0].path,)
                            action_inactive = rendering_state in {
                                "removed",
                                "ancestor-removed",
                                "unresolved-parent-cycle",
                            }
                            action_conditional = bool(
                                action.ifconfig
                            ) or (potentially_renders and not renders)
                            packet.add(GraphFact(
                                "magento-layout-effective-action",
                                node.name,
                                (
                                    "declares-inactive-layout-action"
                                    if action_inactive
                                    else (
                                        "conditionally-calls-layout-action"
                                        if action_conditional
                                        else "calls-layout-action"
                                    )
                                ),
                                target,
                                action.source.path,
                                action.source.line,
                                attrs(
                                    area=area,
                                    handle=handle,
                                    ifconfig=action.ifconfig,
                                    method=action.method,
                                    renderingState=rendering_state,
                                    themeSelection=selection_name,
                                ),
                            ), *provenance_paths, *block_resolution_paths,
                                *method_paths)

                        if (
                            node.node_kind == "uiComponent"
                            and potentially_renders
                        ):
                            ui_paths = tuple(sorted(
                                path
                                for path in self.index.ui_component_paths_by_name.get(
                                    node.name,
                                    (),
                                )
                                if self.index.is_deployed_view_source(
                                    path,
                                    modules,
                                    themes,
                                )
                                and (
                                    (
                                        self.index.theme_for_path(path, themes)
                                        is not None
                                        and selection_compatible_path(path)
                                    )
                                    or (
                                        self.index.theme_for_path(path, themes)
                                        is None
                                        and view_area(
                                            path,
                                            "ui_component",
                                        ) in {"base", area}
                                    )
                                )
                            ))
                            packet.add(GraphFact(
                                "magento-layout-ui-component-activation",
                                handle,
                                (
                                    "activates-ui-component"
                                    if renders
                                    else "conditionally-activates-ui-component"
                                ),
                                node.name,
                                node.source.path,
                                node.source.line,
                                attrs(
                                    area=area,
                                    renderingState=rendering_state,
                                    resolved=str(bool(ui_paths)).lower(),
                                    themeSelection=selection_name,
                                ),
                            ), *ui_paths, *provenance_paths)

                    for asset in effective.assets:
                        if (
                            not emit_full_projection
                            and default_assets_by_src.get(asset.src) == asset
                        ):
                            continue
                        packet.add(GraphFact(
                            "magento-layout-effective-asset",
                            handle,
                            (
                                "removes-asset"
                                if asset.removed
                                else "loads-asset"
                            ),
                            asset.src,
                            asset.source.path,
                            asset.source.line,
                            attrs(
                                area=area,
                                assetKind=asset.asset_kind,
                                themeSelection=selection_name,
                            ),
                        ), *(
                            source.path for source in asset.provenance
                            if source.path != asset.source.path
                        ))

                    for operation in effective.unresolved_operations:
                        if (
                            not emit_full_projection
                            and operation in default_unresolved_operations
                        ):
                            continue
                        packet.add(GraphFact(
                            "magento-layout-unresolved-instruction",
                            operation.name or handle,
                            "has-unresolved-layout-instruction",
                            operation.kind,
                            operation.source.path,
                            operation.source.line,
                            attrs(
                                area=area,
                                semanticRole="uncertainty",
                                themeSelection=selection_name,
                            ),
                        ))

                    for diagnostic in effective.diagnostics:
                        identity = (
                            diagnostic.code,
                            diagnostic.source.path,
                            diagnostic.source.line,
                        )
                        if identity in emitted_diagnostics:
                            continue
                        emitted_diagnostics.add(identity)
                        self.index.diagnostics.append(PluginDiagnostic(
                            diagnostic.code,
                            diagnostic.message,
                            self.index.plugin_id,
                            diagnostic.source.path,
                            recoverable=True,
                        ))
                    if handle == "default":
                        default_nodes_by_name = current_nodes_by_name
                        default_missing_parents = {
                            node.parent
                            for node in effective.nodes
                            if node.parent
                            and node.parent not in current_nodes_by_name
                        }
                        default_assets_by_src = {
                            asset.src: asset for asset in effective.assets
                        }
                        default_unresolved_operations = set(
                            effective.unresolved_operations
                        )
