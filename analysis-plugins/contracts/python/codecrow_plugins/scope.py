from __future__ import annotations

from .api import GraphFact, PluginDiagnostic, PluginKind, ProjectCapabilities


def plugin_root_for_path(
    kind: PluginKind,
    plugin_id: str,
    path: str,
    capabilities: ProjectCapabilities,
) -> str | None:
    if kind is PluginKind.LANGUAGE:
        return "" if plugin_id in capabilities.file_plugins.get(path, ()) else None
    if kind is not PluginKind.FRAMEWORK:
        return ""
    evidence = capabilities.detection_evidence.get(plugin_id, ())
    roots = tuple(
        item.removeprefix("root:")
        for item in evidence
        if item.startswith("root:")
    )
    if not roots:
        # Legacy hand-built capabilities had no evidence, while older
        # manual projections may only carry their explicit-selection tag.
        # Repository-derived evidence without a root is incomplete and
        # must not widen a framework contribution to the whole repository.
        if not evidence or any(
            item.startswith((
                "manual-project-type:",
                "manual-project-type-dependency:",
            ))
            for item in evidence
        ):
            return ""
        return None
    matching = tuple(
        "" if root == "." else root
        for root in roots
        if root == "." or path == root or path.startswith(root + "/")
    )
    return max(matching, key=lambda root: (root.count("/"), len(root))) if matching else None


def relative_to_root(path: str, root: str) -> str:
    if not root:
        return path
    return path[len(root) + 1:]


def rebase_fact(fact: GraphFact, root: str) -> GraphFact:
    if not root:
        return fact
    return GraphFact(
        fact.kind,
        fact.source,
        fact.relation,
        fact.target,
        f"{root}/{fact.path}",
        fact.line,
        fact.attributes,
        tuple(f"{root}/{path}" for path in fact.related_paths),
        fact.contributing_plugin_ids,
    )


def rebase_diagnostic(
    diagnostic: PluginDiagnostic,
    root: str,
) -> PluginDiagnostic:
    if not root or diagnostic.path is None:
        return diagnostic
    return PluginDiagnostic(
        diagnostic.code,
        diagnostic.message,
        diagnostic.plugin_id,
        f"{root}/{diagnostic.path}",
        diagnostic.recoverable,
    )
