from __future__ import annotations

import re
from pathlib import PurePosixPath

from codecrow_plugins import GraphFact, SymbolDefinition

from .architecture import ModuleRecord, PacketGraph, attrs, line, tag, view_area
from .layout_topology import LayoutTopology
from .resolution_index import RepositorySourceIndex
from .resolution_models import (
    ConfigValue,
    ConfigurationSources,
    DiState,
    ThemeRecord,
    _path_under,
)


class RouteTopology:
    """Build Magento routes relationships from repository evidence."""

    def __init__(
        self,
        index: RepositorySourceIndex,
        graph: PacketGraph,
        configuration_sources: ConfigurationSources,
        layouts: LayoutTopology,
    ) -> None:
        self.index = index
        self.graph = graph
        self.configuration_sources = configuration_sources
        self.layouts = layouts

    def routes_and_layouts(
        self,
        modules: tuple[ModuleRecord, ...],
        themes: tuple[ThemeRecord, ...],
        di_states: dict[str, DiState] | None = None,
    ) -> None:
        route_modules: dict[tuple[str, str], dict[str, ConfigValue]] = {}
        route_front_names: dict[tuple[str, str], str] = {}
        route_position = 0
        for area in ("adminhtml", "frontend"):
            for path, module, order in self.index.ordered_configs("routes.xml", modules, area):
                root = self.index.xml(path)
                if root is None:
                    continue
                content = self.index.artifacts[path]
                module_name = module.name if module else "application"
                for router in (node for node in root.iter() if tag(node) == "router" and node.get("id")):
                    for route in (node for node in router if tag(node) == "route" and node.get("id")):
                        key = (area, route.get("id"))
                        if route.get("frontName"):
                            route_front_names[key] = route.get("frontName")
                        for route_module in (node for node in route if tag(node) == "module" and node.get("name")):
                            name = route_module.get("name")
                            prior = route_modules.setdefault(key, {}).get(name)
                            merged_attributes = (
                                dict(prior.attributes) if prior else {}
                            )
                            merged_attributes.update(dict(attrs(
                                router=router.get("id"),
                                before=route_module.get("before", ""),
                                after=route_module.get("after", ""),
                            )))
                            value = ConfigValue(
                                name, path, line(content, name), module_name,
                                order, tuple(sorted(merged_attributes.items())),
                                route_position,
                            )
                            route_modules[key][name] = value
                            route_position += 1

        layout_by_handle: dict[tuple[str, str], list[str]] = {}
        for path in sorted(self.index.artifacts):
            theme = self.index.theme_for_path(path, themes)
            if not self.index.is_deployed_view_source(
                path,
                modules,
                themes,
            ):
                continue
            area = view_area(path, "layout") or (theme.area if theme else None)
            if area is None or not path.endswith(".xml"):
                continue
            handle = PurePosixPath(path).stem
            layout_by_handle.setdefault((area, handle), []).append(path)

        def layout_identity(
            path: str,
            directory: str = "layout",
        ) -> tuple[str, str, str, ThemeRecord | None] | None:
            """Mirror Magento's View File identifier for either layout kind."""
            filename = PurePosixPath(path).name
            theme = self.index.theme_for_path(path, themes)
            if theme is not None:
                relative = path[len(theme.root):].lstrip("/")
                parts = relative.split("/")
                if len(parts) < 3 or parts[1] != directory:
                    return None
                module_name = parts[0]
                if (
                    len(parts) >= 5
                    and parts[2:4] == ["override", "base"]
                ):
                    return (
                        f"module:{module_name}:{filename}",
                        "override-base",
                        "",
                        theme,
                    )
                if (
                    len(parts) >= 7
                    and parts[2:4] == ["override", "theme"]
                ):
                    ancestor = f"{parts[4]}/{parts[5]}"
                    return (
                        f"theme:{ancestor}:{module_name}:{filename}",
                        "override-theme",
                        ancestor,
                        theme,
                    )
                return (
                    f"theme:{theme.name}:{module_name}:{filename}",
                    "theme",
                    theme.name,
                    theme,
                )

            module = self.index.module_for_path(path, modules)
            if module is None:
                return None
            relative = (
                path[len(module.root):].lstrip("/")
                if module.root
                else path
            )
            match = re.match(
                rf"view/(?P<area>[^/]+)/{re.escape(directory)}/.+\.xml$",
                relative,
            )
            if match is None:
                return None
            collected_area = match.group("area")
            identity_prefix = (
                "base" if collected_area == "base" else "module"
            )
            return (
                f"{identity_prefix}:{module.name}:{filename}",
                f"module-{collected_area}",
                module.name,
                None,
            )

        layout_identities = {
            path: identity
            for paths in layout_by_handle.values()
            for path in paths
            if (identity := layout_identity(path)) is not None
        }

        def effective_layout_paths(
            area: str,
            handle: str,
            source_theme: ThemeRecord | None = None,
        ) -> tuple[str, ...]:
            # Magento's base collector loads module `view/base/layout` files
            # before the current design area's files. For a theme-owned source,
            # Magento\Framework\View\Design\Fallback\Rule\Theme walks only that
            # theme and its parent chain; sibling themes are not runtime
            # fallback candidates. For module-owned sources, keep every
            # installed theme variant explicit because the active store theme
            # is repository-external database state.
            areas = ("base", area) if area != "base" else ("base",)
            allowed_theme_roots = (
                {
                    candidate.root
                    for candidate in self.index.theme_chain(source_theme, themes)
                }
                if source_theme is not None
                else None
            )
            candidates = {
                candidate
                for candidate_area in areas
                for candidate in layout_by_handle.get(
                    (candidate_area, handle),
                    (),
                )
                if (
                    (candidate_theme := self.index.theme_for_path(candidate, themes))
                    is None
                    or allowed_theme_roots is None
                    or candidate_theme.root in allowed_theme_roots
                )
            }
            if source_theme is None:
                return tuple(sorted(candidates))

            # Magento's Aggregated collector starts with module base/area
            # files, then walks inherited themes root-to-child. Regular theme
            # files are added; override/base and override/theme files replace
            # the existing View\File identity. Preserve exactly the resulting
            # paths instead of forwarding both a replacement and suppressed
            # source as executable context.
            effective_by_identity = {
                identity[0]: candidate
                for candidate in sorted(candidates)
                if (
                    (identity := layout_identities.get(candidate))
                    is not None
                    and identity[3] is None
                )
            }
            for current_theme in reversed(
                self.index.theme_chain(source_theme, themes)
            ):
                theme_candidates = sorted(
                    candidate
                    for candidate in candidates
                    if (
                        (identity := layout_identities.get(candidate))
                        is not None
                        and identity[3] == current_theme
                    )
                )
                for candidate in theme_candidates:
                    identity = layout_identities[candidate]
                    if not identity[1].startswith("override-"):
                        effective_by_identity[identity[0]] = candidate
                for candidate in theme_candidates:
                    identity = layout_identities[candidate]
                    if identity[1].startswith("override-"):
                        if identity[0] in effective_by_identity:
                            effective_by_identity[identity[0]] = candidate
            return tuple(sorted(effective_by_identity.values()))

        for path in sorted(self.index.artifacts):
            theme = self.index.theme_for_path(path, themes)
            if not self.index.is_deployed_view_source(
                path,
                modules,
                themes,
            ):
                continue
            area = view_area(path, "layout") or (theme.area if theme else None)
            if area is None or not path.endswith(".xml"):
                continue
            handle = PurePosixPath(path).stem
            root = self.index.xml(path)
            if root is None:
                continue
            theme_module = self.index.theme_module(path, theme)
            identity = layout_identities.get(path)
            override_kind = (
                identity[1]
                if identity is not None
                and identity[1].startswith("override-")
                else ""
            )
            packet = self.graph.packet(
                "magento-layout",
                f"{area}:{handle}",
                area=area,
                handle=handle,
            )
            packet.add(GraphFact(
                "magento-layout-handle",
                handle,
                "declared-in",
                path,
                path,
                1,
                attrs(
                    area=area,
                    theme=theme.name if theme else "",
                    themeModule=theme_module,
                    themeSelection=(
                        theme.name if theme else "runtime-selected"
                    ),
                    overrideKind=override_kind,
                ),
            ), theme.theme_xml if theme else "", *(
                candidate
                for candidate in effective_layout_paths(area, handle, theme)
                if candidate != path
            ))
            if identity is not None and override_kind and theme is not None:
                allowed_roots = {
                    candidate.root
                    for candidate in self.index.theme_chain(theme, themes)
                }
                replacement_candidates = tuple(sorted(
                    candidate
                    for candidate, candidate_identity in layout_identities.items()
                    if candidate != path
                    and candidate_identity[0] == identity[0]
                    and (
                        candidate_identity[3] is None
                        or candidate_identity[3].root in allowed_roots
                    )
                ))
                packet.add(GraphFact(
                    (
                        "magento-layout-override"
                        if replacement_candidates
                        else "magento-layout-override-unresolved"
                    ),
                    path,
                    (
                        "replaces-layout-identity"
                        if replacement_candidates
                        else "has-no-replaceable-layout-identity"
                    ),
                    identity[0],
                    path,
                    1,
                    attrs(
                        area=area,
                        theme=theme.name,
                        overrideKind=override_kind,
                        ancestorTheme=identity[2],
                    ),
                ), *replacement_candidates)
            content = self.index.artifacts[path]
            parents = {
                id(child): parent
                for parent in root.iter()
                for child in parent
            }
            for element in root.iter():
                element_tag = tag(element)
                config_condition = element.get("ifconfig", "").strip()
                if config_condition:
                    element_identity = (
                        element.get("name")
                        or element.get("class")
                        or element_tag
                    )
                    packet.add(GraphFact(
                        "magento-layout-config-condition",
                        element_identity,
                        "visible-when-config-enabled",
                        config_condition,
                        path,
                        line(content, config_condition),
                        attrs(
                            area=area,
                            element=element_tag,
                            handle=handle,
                        ),
                    ), *sorted(
                        self.configuration_sources.system.get(
                            config_condition,
                            (),
                        )
                    ))
                acl_condition = element.get("aclResource", "").strip()
                if acl_condition:
                    element_identity = (
                        element.get("name")
                        or element.get("class")
                        or element_tag
                    )
                    packet.add(GraphFact(
                        "magento-layout-acl-condition",
                        element_identity,
                        "visible-to-resource",
                        acl_condition,
                        path,
                        line(content, acl_condition),
                        attrs(
                            area=area,
                            element=element_tag,
                            handle=handle,
                        ),
                    ), *sorted(
                        self.configuration_sources.acl.get(acl_condition, ())
                    ))
                if element_tag == "update" and element.get("handle"):
                    related = effective_layout_paths(
                        area,
                        element.get("handle"),
                        theme,
                    )
                    packet.add(GraphFact(
                        "magento-layout-update",
                        handle,
                        "includes-handle",
                        element.get("handle"),
                        path,
                        line(content, element.get("handle")),
                        attrs(area=area),
                    ), *related)
                elif element_tag in {"block", "referenceBlock"}:
                    target = element.get("class") or element.get("name")
                    if target:
                        parent = parents.get(id(element))
                        while (
                            parent is not None
                            and tag(parent) not in {"block", "referenceBlock"}
                        ):
                            parent = parents.get(id(parent))
                        template_paths = self.index.template_paths(
                            element.get("template", ""),
                            area,
                            modules,
                            themes,
                            theme,
                        )
                        selected_template_path = (
                            self.index.selected_template_path(
                                element.get("template", ""),
                                area,
                                modules,
                                themes,
                                theme,
                            )
                        )
                        packet.add(GraphFact(
                            "magento-layout-block",
                            handle,
                            "declares-block",
                            target,
                            path,
                            line(content, target),
                            attrs(
                                area=area,
                                alias=element.get("as", ""),
                                name=element.get("name", ""),
                                parentName=(
                                    parent.get("name", "")
                                    if parent is not None
                                    else ""
                                ),
                                template=element.get("template", ""),
                                selectedTemplatePath=(
                                    selected_template_path or ""
                                ),
                                theme=theme.name if theme else "",
                            ),
                        ),
                            self.index.symbol_path(element.get("class", "")),
                            *template_paths,
                        )
                        block_class = element.get("class", "").strip()
                        block_symbol = self.index.unique_symbol_casefold(block_class)
                        if selected_template_path and block_class:
                            related = tuple(sorted(filter(None, (
                                path,
                                block_symbol.path if block_symbol else "",
                            ))))
                            packet.add(GraphFact(
                                "magento-template-block-binding",
                                selected_template_path,
                                "declared-with-block-class",
                                block_class,
                                selected_template_path,
                                1,
                                attrs(
                                    area=area,
                                    handle=handle,
                                    layoutPath=path,
                                ),
                                related_paths=related,
                            ))
                            for call in self.index.template_php_calls(
                                selected_template_path,
                                "block",
                            ):
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
                                        "magento-template-block-method-call",
                                        selected_template_path,
                                        "declares-block-method-call",
                                        f"{declaring_symbol.qualified_name}::{declared_method}",
                                        selected_template_path,
                                        call.line,
                                        attrs(
                                            area=area,
                                            blockClass=block_class,
                                            handle=handle,
                                            layoutPath=path,
                                        ),
                                        related_paths=tuple(sorted({
                                            path,
                                            declaring_symbol.path,
                                        })),
                                    ))
                            for arguments_node in (
                                child for child in element
                                if tag(child) == "arguments"
                            ):
                                for argument in (
                                    child for child in arguments_node
                                    if tag(child) == "argument"
                                ):
                                    argument_type = next((
                                        value
                                        for key, value in argument.attrib.items()
                                        if key == "type" or key.endswith("}type")
                                    ), "")
                                    object_class = (argument.text or "").strip()
                                    if argument_type != "object" or not object_class:
                                        continue
                                    object_symbol = self.index.unique_symbol_casefold(
                                        object_class
                                    )
                                    packet.add(GraphFact(
                                        "magento-template-view-model-binding",
                                        selected_template_path,
                                        "declares-layout-object",
                                        object_class,
                                        selected_template_path,
                                        1,
                                        attrs(
                                            argument=argument.get("name", ""),
                                            area=area,
                                            handle=handle,
                                            layoutPath=path,
                                        ),
                                        related_paths=tuple(sorted(filter(None, (
                                            path,
                                            object_symbol.path if object_symbol else "",
                                        )))),
                                    ))

        self.layouts.effective_layouts(
            modules,
            themes,
            layout_by_handle,
            effective_layout_paths,
            layout_identity,
            di_states,
        )

        for (area, route_id), entries_by_module in sorted(route_modules.items()):
            entries, route_order_complete = self.ordered_route_modules(
                area,
                route_id,
                tuple(entries_by_module.values()),
            )
            front_name = route_front_names.get((area, route_id), route_id)
            packet = self.graph.packet(
                "magento-route",
                f"{area}:{route_id}",
                area=area,
                route=route_id,
            )
            for position, entry in enumerate(entries):
                route_module = next(
                    (module for module in modules if module.name == entry.value),
                    None,
                )
                packet.add(GraphFact(
                    "magento-effective-route",
                    route_id,
                    "handled-by-module",
                    entry.value,
                    entry.path,
                    entry.line,
                    attrs(**{
                        "area": area,
                        "module": entry.module,
                        **dict(entry.attributes),
                        "frontName": front_name,
                        "priorityPosition": (
                            position if route_order_complete else ""
                        ),
                    }),
                ), route_module.module_xml if route_module else "")
                if route_order_complete and position:
                    previous = entries[position - 1]
                    packet.add(GraphFact(
                        "magento-route-priority",
                        previous.value,
                        "searched-before",
                        entry.value,
                        previous.path,
                        previous.line,
                        attrs(
                            area=area,
                            routeId=route_id,
                            frontName=front_name,
                            position=position - 1,
                            nextPosition=position,
                        ),
                    ), entry.path)

            if not route_order_complete:
                continue

            controllers: dict[
                str,
                list[tuple[int, ConfigValue, SymbolDefinition, str]],
            ] = {}
            for position, entry in enumerate(entries):
                route_module = next(
                    (module for module in modules if module.name == entry.value),
                    None,
                )
                if route_module is None:
                    continue
                controller_prefix = _path_under(
                    route_module.root,
                    (
                        "Controller/Adminhtml/"
                        if area == "adminhtml"
                        else "Controller/"
                    ),
                )
                for symbol in self.index.symbols:
                    if not symbol.path.startswith(controller_prefix):
                        continue
                    relative = symbol.path[
                        len(controller_prefix):
                    ].removesuffix(".php")
                    controllers.setdefault(
                        relative.casefold(),
                        [],
                    ).append((position, entry, symbol, relative))

            for _, candidates in sorted(controllers.items()):
                candidates.sort(key=lambda item: item[0])
                _, winner_entry, winner, relative = candidates[0]
                controller_action = relative.replace("/", "_").casefold()
                handle = f"{route_id}_{controller_action}"
                layout_paths = effective_layout_paths(area, handle)
                request_path = (
                    f"{front_name}/"
                    f"{relative.removesuffix('/Index').casefold()}"
                )
                normalized_request_path = request_path.strip("/").casefold()
                if area == "adminhtml":
                    self.configuration_sources.controllers.setdefault(
                        normalized_request_path,
                        set(),
                    ).update((winner.path, winner_entry.path))
                    if relative.casefold().endswith("/index"):
                        self.configuration_sources.controllers.setdefault(
                            (
                                f"{front_name}/{relative}"
                                .strip("/")
                                .casefold()
                            ),
                            set(),
                        ).update((winner.path, winner_entry.path))
                packet.add(GraphFact(
                    "magento-route-controller",
                    request_path,
                    "dispatches-to",
                    winner.qualified_name,
                    winner.path,
                    winner.line,
                    attrs(
                        area=area,
                        layoutHandle=handle,
                        routeId=route_id,
                        routeModule=winner_entry.value,
                    ),
                ), winner.path, *layout_paths)
                for _, shadowed_entry, shadowed, _ in candidates[1:]:
                    packet.add(GraphFact(
                        "magento-route-controller-shadowed",
                        shadowed.qualified_name,
                        "shadowed-by",
                        winner.qualified_name,
                        shadowed.path,
                        shadowed.line,
                        attrs(
                            area=area,
                            requestPath=request_path,
                            routeId=route_id,
                            routeModule=shadowed_entry.value,
                        ),
                    ), winner.path)

    def ordered_route_modules(
        self,
        area: str,
        route_id: str,
        entries: tuple[ConfigValue, ...],
    ) -> tuple[tuple[ConfigValue, ...], bool]:
        del area, route_id
        base = tuple(sorted(
            entries,
            key=lambda entry: (
                entry.order,
                entry.position,
                entry.value,
            ),
        ))
        # Match Magento\Framework\App\Route\Config\Converter::_sortModulesList.
        # This is an insertion algorithm, not a topological sort: an unresolved
        # `before` target inserts at the front, an unresolved `after` target
        # appends, and a self-reference is resolved before the current module
        # has been inserted. Core Magento route declarations rely on these
        # semantics (for example Magento_Reports before Magento_Reports).
        ordered: list[ConfigValue] = []
        for entry in base:
            attributes = dict(entry.attributes)
            if "before" in attributes:
                target = attributes["before"]
                position = next(
                    (
                        index
                        for index, candidate in enumerate(ordered)
                        if candidate.value == target
                    ),
                    0,
                )
                ordered.insert(position, entry)
            elif "after" in attributes:
                target = attributes["after"]
                position = next(
                    (
                        index
                        for index, candidate in enumerate(ordered)
                        if candidate.value == target
                    ),
                    len(base),
                )
                ordered.insert(position + 1, entry)
            else:
                ordered.append(entry)
        return tuple(ordered), True
