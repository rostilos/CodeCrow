from __future__ import annotations

from collections import deque

from codecrow_plugins import GraphFact, RepositoryAnalysis

from .template_runtime import TemplateRuntime, _CALL_FACT_KINDS, _normalized_route


class HyvaDependencyIndex:
    """Interpret exact Magento/PHP dependency evidence for Hyva topology."""

    def __init__(self, templates: dict[str, TemplateRuntime]) -> None:
        self.templates = templates

    @staticmethod
    def layout_topology(
        dependencies: RepositoryAnalysis,
    ) -> tuple[
        dict[str, dict[str, dict[str, str]]],
        dict[str, set[str]],
    ]:
        blocks_by_source: dict[str, dict[str, dict[str, str]]] = {}
        sources_by_template: dict[str, set[str]] = {}
        ambiguous_blocks: set[tuple[str, str]] = set()
        for packet in dependencies.packets:
            if packet.plugin_id != "magento" or packet.kind != "magento-layout":
                continue
            for fact in packet.facts:
                if fact.kind != "magento-layout-block":
                    continue
                attributes = dict(fact.attributes)
                selected = attributes.get("selectedTemplatePath", "")
                name = attributes.get("name", "")
                if not name or (fact.path, name) in ambiguous_blocks:
                    continue
                block = {
                    "alias": attributes.get("alias", ""),
                    "name": name,
                    "parent": attributes.get("parentName", ""),
                    "template": selected,
                }
                prior = blocks_by_source.setdefault(
                    fact.path,
                    {},
                ).get(name)
                if prior is None:
                    blocks_by_source[fact.path][name] = block
                elif prior != block:
                    # Multiple contradictory declarations in one physical XML
                    # source are not a stable render edge.
                    blocks_by_source[fact.path].pop(name, None)
                    ambiguous_blocks.add((fact.path, name))
                if selected:
                    sources_by_template.setdefault(
                        selected,
                        set(),
                    ).add(fact.path)
        return blocks_by_source, sources_by_template

    @staticmethod
    def hyva_theme_templates(
        dependencies: RepositoryAnalysis,
    ) -> dict[str, tuple[str, tuple[str, ...]]]:
        """Map exact theme roots to a Hyva identity and inheritance proof."""
        roots: dict[tuple[str, str], set[tuple[str, str]]] = {}
        parents: dict[tuple[str, str], set[str]] = {}
        theme_paths: dict[tuple[str, str], set[str]] = {}
        for packet in dependencies.packets:
            if packet.plugin_id != "magento" or packet.kind != "magento-theme":
                continue
            for fact in packet.facts:
                if fact.kind != "magento-theme":
                    continue
                attributes = dict(fact.attributes)
                area = attributes.get("area", "")
                if area != "frontend" or not fact.path.endswith("/theme.xml"):
                    continue
                identity = (area, fact.source)
                root = fact.path.removesuffix("/theme.xml")
                roots.setdefault(identity, set()).add((root, fact.path))
                theme_paths.setdefault(identity, set()).add(fact.path)
                if fact.relation == "inherits":
                    parents.setdefault(identity, set()).add(fact.target)

        def proof(
            identity: tuple[str, str],
        ) -> tuple[str, ...] | None:
            visited: set[tuple[str, str]] = set()
            paths: set[str] = set()
            current = identity
            while current not in visited:
                visited.add(current)
                if current in roots and len(roots[current]) != 1:
                    return None
                paths.update(theme_paths.get(current, ()))
                if current[1].casefold().startswith("hyva/"):
                    return tuple(sorted(paths))
                candidates = parents.get(current, ())
                if len(candidates) != 1:
                    return None
                current = (current[0], next(iter(candidates)))
            return None

        candidates_by_root: dict[
            str,
            set[tuple[str, tuple[str, ...]]],
        ] = {}
        for identity, values in sorted(roots.items()):
            if len(values) != 1:
                continue
            inheritance_paths = proof(identity)
            if inheritance_paths is None:
                continue
            root, theme_xml = next(iter(values))
            candidates_by_root.setdefault(root, set()).add((
                identity[1],
                tuple(sorted({theme_xml, *inheritance_paths})),
            ))
        return {
            root: next(iter(candidates))
            for root, candidates in sorted(candidates_by_root.items())
            if len(candidates) == 1
        }

    @staticmethod
    def template_hyva_theme(
        template_path: str,
        themes: dict[str, tuple[str, tuple[str, ...]]],
    ) -> tuple[str, tuple[str, ...]] | None:
        matches = tuple(
            value
            for root, value in themes.items()
            if template_path.startswith(f"{root}/")
        )
        return matches[0] if len(matches) == 1 else None

    def runtime_scope_templates(
        self,
        source_template: str,
        state_identifiers: tuple[str, ...],
        layout_source: str,
        blocks_by_source: dict[str, dict[str, dict[str, str]]],
    ) -> set[str]:
        """Select only sibling subtrees that read state written by the route."""
        if not layout_source or not state_identifiers:
            return set()
        blocks = blocks_by_source.get(layout_source, {})
        source_blocks = tuple(
            block for block in blocks.values()
            if block["template"] == source_template
        )
        if len(source_blocks) != 1:
            return set()
        source_parent = source_blocks[0]["parent"]
        if not source_parent:
            return set()

        state = set(state_identifiers)
        roots = {
            block["name"]
            for block in blocks.values()
            if (
                block["parent"] == source_parent
                and block["template"] != source_template
                and state.intersection(
                    self.templates.get(
                        block["template"],
                        TemplateRuntime(),
                    ).alpine_identifiers
                )
            )
        }
        if not roots:
            return set()

        selected_templates: set[str] = set()
        pending = deque(sorted(roots))
        visited: set[str] = set()
        while pending:
            name = pending.popleft()
            if name in visited:
                continue
            visited.add(name)
            block = blocks.get(name)
            if block is None:
                continue
            if block["template"]:
                selected_templates.add(block["template"])
            pending.extend(sorted(
                child["name"]
                for child in blocks.values()
                if child["parent"] == name
            ))
        return selected_templates

    @staticmethod
    def webapi_routes(
        dependencies: RepositoryAnalysis,
    ) -> dict[tuple[str, str], tuple[GraphFact, ...]]:
        routes: dict[tuple[str, str], list[GraphFact]] = {}
        for packet in dependencies.packets:
            if packet.plugin_id != "magento" or packet.kind != "magento-webapi":
                continue
            for fact in packet.facts:
                if fact.kind != "magento-webapi-route":
                    continue
                method, separator, route = fact.source.partition(" ")
                if not separator:
                    continue
                key = (
                    method.strip().upper(),
                    _normalized_route(route).casefold(),
                )
                routes.setdefault(key, []).append(fact)
        return {
            key: tuple(sorted(facts))
            for key, facts in routes.items()
        }

    @staticmethod
    def call_edges(
        dependencies: RepositoryAnalysis,
    ) -> dict[tuple[str, str], tuple[GraphFact, ...]]:
        edges: dict[tuple[str, str], list[GraphFact]] = {}
        for packet in dependencies.packets:
            if packet.plugin_id != "php":
                continue
            for fact in packet.facts:
                if fact.kind not in _CALL_FACT_KINDS:
                    continue
                attributes = dict(fact.attributes)
                caller = attributes.get("callerMethod", "")
                target_method = attributes.get("targetMethod", "")
                if (
                    not caller
                    or not target_method
                    or attributes.get("targetMethodDeclared") != "true"
                ):
                    continue
                edges.setdefault(
                    (fact.source.casefold(), caller.casefold()),
                    [],
                ).append(fact)
        return {
            key: tuple(sorted(facts))
            for key, facts in edges.items()
        }

    @staticmethod
    def symbols(
        dependencies: RepositoryAnalysis,
    ) -> dict[str, tuple]:
        by_name: dict[str, list] = {}
        for symbol in dependencies.symbols:
            by_name.setdefault(
                symbol.qualified_name.casefold(),
                [],
            ).append(symbol)
        return {
            name: tuple(sorted(values))
            for name, values in by_name.items()
        }
