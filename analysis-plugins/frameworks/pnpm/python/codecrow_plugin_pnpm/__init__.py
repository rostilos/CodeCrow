"""Declared catalog versions, without claiming dependency consumption or installation."""
from dataclasses import dataclass
from codecrow_plugins import FileArtifact, GraphFact, PluginDescriptor, PluginDiagnostic, PluginOutcome


def pnpm_catalog_facts(artifact: FileArtifact) -> tuple[GraphFact, ...]:
    import yaml

    root = yaml.compose(artifact.content, Loader=yaml.SafeLoader)
    if root is None:
        return ()
    if not isinstance(root, yaml.MappingNode):
        raise ValueError("pnpm-workspace.yaml must contain a mapping")
    string_tag = "tag:yaml.org,2002:str"
    catalogs = []
    for key, value in root.value:
        if not isinstance(key, yaml.ScalarNode) or key.tag != string_tag:
            continue
        if key.value == "catalog":
            catalogs.append(("default", value, "/catalog"))
        elif key.value == "catalogs" and isinstance(value, yaml.MappingNode):
            for name, entries in value.value:
                if isinstance(name, yaml.ScalarNode) and name.tag == string_tag:
                    pointer = "/catalogs/" + name.value.replace("~", "~0").replace("/", "~1")
                    catalogs.append((name.value, entries, pointer))
    facts = []
    for catalog, entries, pointer in catalogs:
        if not isinstance(entries, yaml.MappingNode):
            continue
        # shortcut: YAML merge keys are not expanded; add merge semantics when source-backed cases need them.
        for name, specifier in entries.value:
            if (not isinstance(name, yaml.ScalarNode) or not isinstance(specifier, yaml.ScalarNode)
                    or name.tag != string_tag or specifier.tag != string_tag or not name.value.strip()):
                continue
            entry_pointer = pointer + "/" + name.value.replace("~", "~0").replace("/", "~1")
            facts.append(GraphFact(
                "pnpm-catalog-entry", artifact.path, "configures-module", name.value,
                artifact.path, name.start_mark.line + 1,
                attributes=(("catalog", catalog), ("specifier", specifier.value), ("yamlPointer", entry_pointer)),
            ))
    return tuple(sorted(facts))


@dataclass(frozen=True)
class PnpmPlugin:
    descriptor: PluginDescriptor

    def index_file(self, artifact: FileArtifact):
        if artifact.deleted or artifact.path != "pnpm-workspace.yaml":
            return PluginOutcome.abstained()
        try:
            return PluginOutcome.handled(pnpm_catalog_facts(artifact))
        except Exception as exception:
            return PluginOutcome.failed(PluginDiagnostic(
                "pnpm-workspace-parse-failed", f"{type(exception).__name__}: {exception}",
                self.descriptor.id, path=artifact.path, recoverable=True))


def create_plugin(descriptor: PluginDescriptor) -> PnpmPlugin:
    return PnpmPlugin(descriptor)
