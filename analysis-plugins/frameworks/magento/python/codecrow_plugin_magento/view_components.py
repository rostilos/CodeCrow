from __future__ import annotations

import json
from pathlib import PurePosixPath

from codecrow_plugins import GraphFact

from .architecture import ModuleRecord, PacketGraph, attrs, line, tag, view_area
from .resolution_index import RepositorySourceIndex
from .resolution_models import ConfigurationSources, ThemeRecord, _path_under


class ViewComponentTopology:
    """Build Magento components relationships from repository evidence."""

    def __init__(
        self,
        index: RepositorySourceIndex,
        graph: PacketGraph,
        configuration_sources: ConfigurationSources,
    ) -> None:
        self.index = index
        self.graph = graph
        self.configuration_sources = configuration_sources

    def ui_components(
        self,
        modules: tuple[ModuleRecord, ...],
        themes: tuple[ThemeRecord, ...],
    ) -> None:
        ui_by_component: dict[tuple[str, str], set[str]] = {}
        for path in sorted(self.index.artifacts):
            if "/ui_component/" not in f"/{path}" or not path.endswith(".xml"):
                continue
            theme = self.index.theme_for_path(path, themes)
            if not self.index.is_deployed_view_source(
                path,
                modules,
                themes,
            ):
                continue
            area = view_area(path, "ui_component") or (
                theme.area if theme else None
            )
            if area is None:
                continue
            ui_by_component.setdefault(
                (area, PurePosixPath(path).stem),
                set(),
            ).add(path)

        def effective_ui_paths(
            area: str,
            component: str,
            source_theme: ThemeRecord | None = None,
        ) -> tuple[str, ...]:
            areas = ("base", area) if area != "base" else ("base",)
            allowed_theme_roots = (
                {
                    candidate.root
                    for candidate in self.index.theme_chain(source_theme, themes)
                }
                if source_theme is not None
                else None
            )
            return tuple(sorted({
                candidate
                for candidate_area in areas
                for candidate in ui_by_component.get(
                    (candidate_area, component),
                    (),
                )
                if (
                    (candidate_theme := self.index.theme_for_path(candidate, themes))
                    is None
                    or allowed_theme_roots is None
                    or candidate_theme.root in allowed_theme_roots
                )
            }))

        for path in sorted(self.index.artifacts):
            if "/ui_component/" not in f"/{path}" or not path.endswith(".xml"):
                continue
            theme = self.index.theme_for_path(path, themes)
            if not self.index.is_deployed_view_source(
                path,
                modules,
                themes,
            ):
                continue
            area = view_area(path, "ui_component") or (theme.area if theme else None)
            if area is None:
                continue
            root = self.index.xml(path)
            if root is None:
                continue
            component_name = PurePosixPath(path).stem
            packet = self.graph.packet(
                "magento-ui-component",
                f"{area}:{component_name}",
                area=area,
                component=component_name,
            )
            packet.add(GraphFact(
                "magento-ui-component",
                component_name,
                "declared-in",
                path,
                path,
                1,
                attrs(area=area, theme=theme.name if theme else ""),
            ), theme.theme_xml if theme else "", *(
                candidate
                for candidate in effective_ui_paths(
                    area,
                    component_name,
                    theme,
                )
                if candidate != path
            ))
            content = self.index.artifacts[path]
            for element in root.iter():
                class_name = element.get("class", "").lstrip("\\")
                if "\\" in class_name:
                    packet.add(GraphFact(
                        "magento-ui-php-class",
                        component_name,
                        "uses-class",
                        class_name,
                        path,
                        line(content, class_name),
                        attrs(area=area, element=tag(element), name=element.get("name", "")),
                    ), self.index.symbol_path(class_name))

                values: list[tuple[str, str]] = []
                if element.get("component"):
                    values.append(("component", element.get("component")))
                if element.get("template"):
                    values.append(("template", element.get("template")))
                if tag(element) in {"item", "param"} and element.get("name") in {
                    "component", "template", "provider", "deps",
                } and element.text:
                    values.append((element.get("name"), element.text.strip()))
                if tag(element) in {"provider", "dep", "aclResource"} and element.text:
                    values.append((tag(element), element.text.strip()))

                for value_kind, value in values:
                    if not value:
                        continue
                    relation = {
                        "component": "uses-js-component",
                        "template": "uses-ui-template",
                        "provider": "depends-on-provider",
                        "deps": "depends-on-component",
                        "dep": "depends-on-component",
                        "aclResource": "requires-resource",
                    }[value_kind]
                    asset_paths = (
                        tuple(sorted(self.configuration_sources.acl.get(value, ())))
                        if value_kind == "aclResource"
                        else self.index.ui_asset_paths(
                            value,
                            area,
                            value_kind == "template",
                            modules,
                            themes,
                            theme,
                        )
                    )
                    packet.add(GraphFact(
                        "magento-ui-relationship",
                        component_name,
                        relation,
                        value,
                        path,
                        line(content, value),
                        attrs(area=area),
                    ), *asset_paths)

    def email_templates(
        self,
        modules: tuple[ModuleRecord, ...],
        themes: tuple[ThemeRecord, ...],
    ) -> None:
        """Join merged email declarations to files, overrides, and exact consumers."""
        enabled_modules = {
            module.name: module
            for module in modules
            if module.enabled
        }
        declarations: dict[str, dict[str, object]] = {}
        for path, owner, order in self.index.ordered_configs(
            "email_templates.xml",
            modules,
            "global",
        ):
            root = self.index.xml(path)
            if root is None:
                continue
            content = self.index.artifacts[path]
            for element in root.iter():
                if tag(element) != "template":
                    continue
                identifier = element.get("id", "").strip()
                filename = element.get("file", "").strip()
                module_name = (
                    element.get("module", "").strip()
                    or (owner.name if owner else "")
                )
                area = element.get("area", "").strip()
                relative = PurePosixPath(filename)
                if (
                    not identifier
                    or not filename
                    or not module_name
                    or not area
                    or relative.is_absolute()
                    or ".." in relative.parts
                    or "\\" in filename
                ):
                    continue
                declarations[identifier] = {
                    "area": area,
                    "file": filename,
                    "label": element.get("label", "").strip(),
                    "module": module_name,
                    "order": order,
                    "path": path,
                    "line": line(content, identifier),
                    "type": element.get("type", "").strip(),
                }

        if not declarations:
            return

        resolved_paths: dict[str, tuple[str, ...]] = {}
        for identifier, declaration in sorted(declarations.items()):
            area = str(declaration["area"])
            filename = str(declaration["file"])
            module_name = str(declaration["module"])
            declaration_path = str(declaration["path"])
            module = enabled_modules.get(module_name)
            module_path = (
                _path_under(
                    module.root,
                    f"view/{area}/email/{filename}",
                )
                if module is not None
                else ""
            )
            if module_path not in self.index.artifacts:
                module_path = ""

            packet = self.graph.packet(
                "magento-email-template",
                identifier,
                area=area,
                module=module_name,
            )
            packet.add(GraphFact(
                "magento-email-template",
                identifier,
                "renders-email-file",
                f"{module_name}::{filename}",
                declaration_path,
                int(declaration["line"]),
                attrs(
                    area=area,
                    file=filename,
                    label=declaration["label"],
                    module=module_name,
                    order=declaration["order"],
                    type=declaration["type"],
                ),
            ), module_path)

            paths = [path for path in (declaration_path, module_path) if path]
            for theme in themes:
                if theme.area != area:
                    continue
                override_path = _path_under(
                    theme.root,
                    f"{module_name}/email/{filename}",
                )
                if override_path not in self.index.artifacts:
                    continue
                paths.append(override_path)
                inherited_override_paths = tuple(
                    candidate
                    for ancestor in self.index.theme_chain(theme, themes)[1:]
                    if (
                        candidate := _path_under(
                            ancestor.root,
                            f"{module_name}/email/{filename}",
                        )
                    ) in self.index.artifacts
                )
                override_packet = self.graph.packet(
                    "magento-email-template-override",
                    f"{area}:{theme.name}:{identifier}",
                    area=area,
                    module=module_name,
                    theme=theme.name,
                )
                override_packet.add(GraphFact(
                    "magento-email-template-override",
                    f"{theme.name}:{identifier}",
                    "overrides-email-template",
                    identifier,
                    override_path,
                    1,
                    attrs(
                        area=area,
                        file=filename,
                        module=module_name,
                        theme=theme.name,
                    ),
                ), declaration_path, module_path, theme.theme_xml,
                    *inherited_override_paths)
            resolved_paths[identifier] = tuple(sorted(set(paths)))

        for path, _, _ in self.index.ordered_configs(
            "config.xml",
            modules,
            "global",
        ):
            root = self.index.xml(path)
            if root is None:
                continue
            content = self.index.artifacts[path]

            def visit(element, ancestors: tuple[str, ...]) -> None:
                current = (*ancestors, tag(element))
                value = (element.text or "").strip()
                if value in declarations:
                    packet = self.graph.packet(
                        "magento-email-template-selection",
                        f"{path}:{'/'.join(current)}",
                    )
                    packet.add(GraphFact(
                        "magento-email-config-default",
                        "/".join(current),
                        "selects-email-template",
                        value,
                        path,
                        line(content, value),
                    ), *resolved_paths[value])
                for child in element:
                    visit(child, current)

            visit(root, ())

        transport_builder = (
            r"Magento\Framework\Mail\Template\TransportBuilder"
        ).casefold()
        for consumer in self.index.symbols:
            for key, encoded in consumer.attributes:
                if not key.startswith(
                    "php-literal-instance-call-reference:"
                ):
                    continue
                try:
                    reference = json.loads(encoded)
                except (TypeError, ValueError):
                    continue
                if (
                    str(reference.get("method", "")).casefold()
                    != "settemplateidentifier"
                    or str(reference.get("target", "")).lstrip(
                        "\\"
                    ).casefold() != transport_builder
                ):
                    continue
                literal_arguments = reference.get(
                    "literalStringArguments",
                    {},
                )
                if not isinstance(literal_arguments, dict):
                    continue
                identifier = literal_arguments.get("0")
                if identifier not in declarations:
                    continue
                try:
                    reference_line = max(
                        1,
                        int(reference.get("line", consumer.line)),
                    )
                except (TypeError, ValueError):
                    reference_line = consumer.line
                packet = self.graph.packet(
                    "magento-email-template-consumer",
                    f"{consumer.qualified_name}:{identifier}",
                )
                packet.add(GraphFact(
                    "magento-email-template-consumer",
                    consumer.qualified_name,
                    "selects-email-template",
                    identifier,
                    consumer.path,
                    reference_line,
                    attrs(
                        caller=reference.get("caller", ""),
                        receiver=reference.get("target", ""),
                    ),
                ), *resolved_paths[identifier])
