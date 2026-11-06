from __future__ import annotations

import re
from pathlib import PurePosixPath

from codecrow_plugins import GraphFact, PluginDiagnostic

from .architecture import ModuleRecord, PacketGraph, attrs, line, tag
from .resolution_index import RepositorySourceIndex
from .resolution_models import (
    ThemeRecord,
    _MODULES_SECTION,
    _MODULE_ENABLED,
    _REGISTRATION,
    _THEME_REGISTRATION,
    _module_root,
    _path_under,
)


class ComponentDiscovery:
    """Build Magento discovery relationships from repository evidence."""

    def __init__(self, index: RepositorySourceIndex, graph: PacketGraph) -> None:
        self.index = index
        self.graph = graph

    def modules(self) -> tuple[ModuleRecord, ...]:
        enabled_order: dict[str, tuple[bool, int]] = {}
        config_content = self.index.artifacts.get("app/etc/config.php", "")
        modules_section = _MODULES_SECTION.search(config_content)
        if modules_section is not None:
            for index, match in enumerate(
                _MODULE_ENABLED.finditer(modules_section.group("body"))
            ):
                enabled_order[match.group("name")] = (
                    match.group("enabled") == "1",
                    index,
                )
            self.index.configured_modules = {
                name: enabled
                for name, (enabled, _) in enabled_order.items()
            }
        elif config_content:
            self.index.diagnostics.append(PluginDiagnostic(
                "magento-config-modules-unreadable",
                "app/etc/config.php does not contain a statically readable modules array",
                self.index.plugin_id,
            ))

        discovered: dict[str, tuple[str, str, tuple[str, ...]]] = {}
        for path in sorted(self.index.artifacts):
            if not (path == "etc/module.xml" or path.endswith("/etc/module.xml")):
                continue
            root = self.index.xml(path)
            if root is None:
                continue
            module_node = next(
                (element for element in root.iter() if tag(element) == "module" and element.get("name")),
                None,
            )
            if module_node is None:
                continue
            name = module_node.get("name")
            sequence = tuple(sorted({
                element.get("name")
                for element in module_node.iter()
                if element is not module_node and tag(element) == "module" and element.get("name")
            }))
            discovered[name] = (_module_root(path), path, sequence)

        ordered_names = self.sort_modules(discovered, enabled_order)
        records: list[ModuleRecord] = []
        for order, name in enumerate(ordered_names):
            root, module_xml, sequence = discovered[name]
            # Once a readable deployment module list exists it is authoritative:
            # discovered code absent from the list is installed but not enabled.
            enabled = (
                enabled_order.get(name, (False, order))[0]
                if modules_section is not None
                else True
            )
            records.append(ModuleRecord(name, root, module_xml, sequence, enabled, order))
        return tuple(records)

    def sort_modules(
        self,
        discovered: dict[str, tuple[str, str, tuple[str, ...]]],
        enabled_order: dict[str, tuple[bool, int]],
    ) -> tuple[str, ...]:
        names = set(discovered)
        if enabled_order:
            # Magento materializes the effective component order into
            # app/etc/config.php. Runtime XML merging follows this order, even
            # when module.xml was edited without regenerating the component
            # list. Keep unlisted installed modules for disabled-module facts,
            # but never let them perturb enabled merge order.
            configured = [
                name
                for name, _ in sorted(
                    enabled_order.items(),
                    key=lambda item: item[1][1],
                )
                if name in names
            ]
            return tuple((*configured, *sorted(names - set(configured))))

        base = sorted(
            names,
            key=lambda name: (
                0 if "Magento_" in name else 1,
                name,
            ),
        )

        sequence_cache: dict[str, tuple[str, ...]] = {}

        def expand(name: str, stack: tuple[str, ...] = ()) -> tuple[str, ...]:
            if name in stack:
                cycle_start = stack.index(name)
                cycle = (*stack[cycle_start:], name)
                raise ValueError(" -> ".join(cycle))
            if name in sequence_cache:
                return sequence_cache[name]
            direct = discovered.get(name, ("", "", ()))[2]
            expanded: list[str] = []
            for dependency in direct:
                expanded.extend(expand(dependency, (*stack, name)))
            expanded.extend(direct)
            sequence_cache[name] = tuple(dict.fromkeys(expanded))
            return sequence_cache[name]

        try:
            expanded = [
                [name, set(expand(name))]
                for name in base
            ]
        except ValueError as exception:
            self.index.diagnostics.append(PluginDiagnostic(
                "magento-module-sequence-cycle",
                f"Module sequence cycle detected: {exception}",
                self.index.plugin_id,
            ))
            return tuple(base)

        # Mirror Magento's pairwise sequence ordering. A normal topological sort
        # changes the order of otherwise-unrelated modules and therefore changes
        # last-wins XML merge results.
        total = len(expanded)
        for left in range(total - 1):
            for right in range(left, total):
                if expanded[right][0] in expanded[left][1]:
                    expanded[left], expanded[right] = expanded[right], expanded[left]
        return tuple(item[0] for item in expanded)

    def module_packets(self, modules: tuple[ModuleRecord, ...]) -> None:
        enabled_modules = tuple(module for module in modules if module.enabled)
        enabled_positions = {
            module.name: index
            for index, module in enumerate(enabled_modules)
        }
        deployment_order_path = (
            "app/etc/config.php"
            if _MODULES_SECTION.search(self.index.artifacts.get("app/etc/config.php", ""))
            else ""
        )
        for module in modules:
            packet = self.graph.packet(
                "magento-module",
                module.name,
                enabled=str(module.enabled).lower(),
                order=str(module.order),
                root=module.root or ".",
            )
            packet.add(GraphFact(
                "magento-module",
                module.name,
                "enabled" if module.enabled else "disabled",
                module.root or ".",
                module.module_xml,
                line(self.index.artifacts[module.module_xml], module.name),
                attrs(order=module.order),
            ))
            registration = _path_under(module.root, "registration.php")
            if registration in self.index.artifacts:
                match = _REGISTRATION.search(self.index.artifacts[registration])
                if match:
                    packet.add(GraphFact(
                        "magento-module-registration",
                        registration,
                        "registers-module",
                        match.group("name"),
                        registration,
                        line(self.index.artifacts[registration], match.group(0)),
                    ))
            for dependency in module.sequence:
                dependency_module = next((item for item in modules if item.name == dependency), None)
                packet.add(GraphFact(
                    "magento-module-sequence",
                    module.name,
                    "loads-after",
                    dependency,
                    module.module_xml,
                    line(self.index.artifacts[module.module_xml], dependency),
                    attrs(
                        effectiveOrder=module.order,
                        dependencyOrder=(
                            dependency_module.order
                            if dependency_module is not None
                            else ""
                        ),
                    ),
                ), dependency_module.module_xml if dependency_module else "")
                if (
                    module.enabled
                    and dependency_module is not None
                    and dependency_module.enabled
                    and enabled_positions[dependency] > enabled_positions[module.name]
                ):
                    packet.add(GraphFact(
                        "magento-module-sequence-mismatch",
                        module.name,
                        "configured-before-required-module",
                        dependency,
                        module.module_xml,
                        line(self.index.artifacts[module.module_xml], dependency),
                        attrs(
                            effectiveOrder=module.order,
                            dependencyOrder=dependency_module.order,
                        ),
                    ), dependency_module.module_xml, "app/etc/config.php")

            position = enabled_positions.get(module.name)
            if deployment_order_path and position is not None and position:
                previous = enabled_modules[position - 1]
                packet.add(GraphFact(
                    "magento-module-effective-order",
                    module.name,
                    "configured-after",
                    previous.name,
                    deployment_order_path,
                    line(self.index.artifacts[deployment_order_path], module.name),
                    attrs(
                        effectiveOrder=module.order,
                        previousOrder=previous.order,
                    ),
                ), module.module_xml, previous.module_xml)

    def themes(self, modules: tuple[ModuleRecord, ...]) -> tuple[ThemeRecord, ...]:
        themes: list[ThemeRecord] = []
        for path in sorted(self.index.artifacts):
            if PurePosixPath(path).name != "theme.xml":
                continue
            root = self.index.xml(path)
            if root is None:
                continue
            theme_root = path.removesuffix("/theme.xml") if path != "theme.xml" else ""
            inferred = re.match(
                r"app/design/(?P<area>frontend|adminhtml)/(?P<vendor>[^/]+)/(?P<theme>[^/]+)/theme\.xml$",
                path,
            )
            registration_path = _path_under(theme_root, "registration.php")
            registered = ""
            if registration_path in self.index.artifacts:
                match = _THEME_REGISTRATION.search(self.index.artifacts[registration_path])
                registered = match.group("name") if match else ""
            if registered and registered.count("/") >= 2:
                area, vendor, theme = registered.split("/", 2)
                name = f"{vendor}/{theme}"
            elif inferred:
                area = inferred.group("area")
                name = f"{inferred.group('vendor')}/{inferred.group('theme')}"
            else:
                # Composer-installed themes must be registered to establish area/name.
                continue
            parent_node = next(
                (node for node in root.iter() if tag(node) == "parent" and node.text),
                None,
            )
            parent = parent_node.text.strip() if parent_node is not None else ""
            record = ThemeRecord(name, area, theme_root, path, parent)
            themes.append(record)
            packet = self.graph.packet(
                "magento-theme",
                f"{area}:{name}",
                area=area,
                theme=name,
            )
            packet.add(GraphFact(
                "magento-theme",
                name,
                "inherits" if parent else "declared-in",
                parent or path,
                path,
                line(self.index.artifacts[path], parent or "<theme"),
                attrs(area=area),
            ), registration_path if registration_path in self.index.artifacts else "")
            if parent:
                parent_theme = next(
                    (
                        item for item in themes
                        if item.area == area and item.name == parent
                    ),
                    None,
                )
                if parent_theme:
                    packet.add(GraphFact(
                        "magento-theme-parent",
                        name,
                        "inherits-files-from",
                        parent,
                        path,
                        line(self.index.artifacts[path], parent),
                        attrs(area=area),
                    ), parent_theme.theme_xml)

        by_identity = {(theme.area, theme.name): theme for theme in themes}
        for theme in themes:
            if not theme.parent:
                continue
            parent_theme = by_identity.get((theme.area, theme.parent))
            if parent_theme is None:
                continue
            packet = self.graph.packet("magento-theme", f"{theme.area}:{theme.name}")
            packet.add(GraphFact(
                "magento-theme-parent",
                theme.name,
                "inherits-files-from",
                theme.parent,
                theme.theme_xml,
                line(self.index.artifacts[theme.theme_xml], theme.parent),
                attrs(area=theme.area),
            ), parent_theme.theme_xml)
        return tuple(sorted(themes, key=lambda item: (item.area, item.name, item.root)))
