"""Explicit Wrangler assets directories, without executing builds or selecting environments."""
from dataclasses import dataclass
import posixpath
import re
import tomllib

from codecrow_plugins import FileArtifact, GraphFact, PluginDescriptor, PluginDiagnostic, PluginOutcome
from codecrow_plugins.jsonc import load_jsonc


def assets_directory_facts(artifact: FileArtifact):
    config = (tomllib.loads(artifact.content) if artifact.path.endswith('.toml')
              else load_jsonc(artifact.content))
    if not isinstance(config, dict):
        raise ValueError('Wrangler configuration must contain an object')
    facts = []

    def add(config, pointer):
        if not isinstance(config, dict):
            raise ValueError('Wrangler environment must contain an object')
        assets = config.get('assets', {})
        if not isinstance(assets, dict):
            raise ValueError('Wrangler assets must contain an object')
        directory = assets.get('directory')
        if directory is None:
            return
        if not isinstance(directory, str) or not directory:
            raise ValueError('Wrangler assets.directory must contain a nonempty string')
        target = directory.replace('\\', '/')
        if not posixpath.isabs(target) and not re.match(r'^[A-Za-z]:', target):
            target = posixpath.normpath(posixpath.join(posixpath.dirname(artifact.path), target))
        # File-level origin plus a configuration pointer; no field line is inferred from repeated values.
        facts.append(GraphFact('wrangler-assets-directory', artifact.path, 'declares-directory',
            target, artifact.path, attributes=(('configurationPointer', pointer + '/assets/directory'),
                ('originScope', 'configuration-file'), ('targetResolutionProven', 'false'),
                ('targetSpecifier', directory))))

    add(config, '')
    environments = config.get('env', {})
    if not isinstance(environments, dict):
        raise ValueError('Wrangler env must contain an object')
    for name, environment in environments.items():
        add(environment, '/env/' + name.replace('~', '~0').replace('/', '~1'))
    return tuple(sorted(facts))


@dataclass(frozen=True)
class WranglerPlugin:
    descriptor: PluginDescriptor

    def index_file(self, artifact: FileArtifact):
        if artifact.deleted or artifact.path.rsplit('/', 1)[-1] not in {'wrangler.json', 'wrangler.jsonc', 'wrangler.toml'}:
            return PluginOutcome.abstained()
        try:
            # shortcut: explicit declarations only; CLI overrides and generated-config redirects need invocation evidence.
            return PluginOutcome.handled(assets_directory_facts(artifact))
        except Exception as exception:
            return PluginOutcome.failed(PluginDiagnostic('wrangler-config-parse-failed',
                f'{type(exception).__name__}: {exception}', self.descriptor.id,
                path=artifact.path, recoverable=True))


def create_plugin(descriptor: PluginDescriptor) -> WranglerPlugin:
    return WranglerPlugin(descriptor)
