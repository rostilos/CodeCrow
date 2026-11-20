"""Source-bound external workflow/action and container declarations, without fetching them."""
from dataclasses import dataclass
import re

from codecrow_plugins import FileArtifact, GraphFact, PluginDescriptor, PluginDiagnostic, PluginOutcome


def workflow_references(artifact: FileArtifact) -> tuple[GraphFact, ...]:
    import yaml

    def fields(node):
        if not isinstance(node, yaml.MappingNode):
            return {}
        result = {}
        for key, value in node.value:
            if not isinstance(key, yaml.ScalarNode):
                raise ValueError("Workflow mapping keys must be scalar")
            # Read key spelling: PyYAML's YAML 1.1 resolver tags the workflow's `on` as boolean.
            name = key.value
            if name == '<<' or name in result:
                raise ValueError("Ambiguous duplicate or merged workflow mapping")
            result[name] = (key, value)
        return result

    def child(mapping, key):
        return mapping.get(key, (None, None))[1]

    def pointer(value):
        return value.replace('~', '~0').replace('/', '~1')

    facts = []

    def add_image(declaration, location, kind, prefix=''):
        key, value = declaration or (None, None)
        if not isinstance(value, yaml.ScalarNode) or value.tag != 'tag:yaml.org,2002:str':
            return
        specifier = value.value
        target = specifier.removeprefix(prefix)
        if not target or '${{' in specifier or any(character.isspace() for character in specifier):
            return
        facts.append(GraphFact('github-container-image', artifact.path, 'configures-module',
            target, artifact.path, key.start_mark.line + 1,
            attributes=(('referenceKind', kind), ('targetResolutionProven', 'false'),
                        ('targetSpecifier', specifier), ('yamlPointer', location))))

    def add(mapping, location, kind):
        key, value = mapping.get('uses', (None, None))
        if not isinstance(value, yaml.ScalarNode) or value.tag != 'tag:yaml.org,2002:str':
            return
        specifier = value.value
        if specifier.startswith('docker://'):
            if kind == 'action':
                add_image((key, value), location + '/uses', 'container-action', 'docker://')
            return
        # shortcut: local/self references need repository resolution when supported.
        if (specifier.startswith(('./', '.\\', '$/')) or '${{' in specifier
                or any(character.isspace() for character in specifier) or specifier.count('@') != 1):
            return
        name, ref = specifier.split('@')
        parts = re.split(r'[/\\]', name)
        if (len(parts) < 2 or not ref or any(not part or part in {'.', '..'} for part in parts)
                or not all(re.fullmatch(r'[A-Za-z0-9_.-]+', part) for part in parts[:2])):
            return
        target = '/'.join(parts)
        facts.append(GraphFact('github-action-reference', artifact.path, 'configures-module',
            target, artifact.path, key.start_mark.line + 1,
            attributes=(('gitRef', ref), ('referenceKind', kind), ('targetResolutionProven', 'false'), ('targetSpecifier', specifier),
                        ('yamlPointer', location + '/uses'))))

    root = yaml.compose(artifact.content, Loader=yaml.SafeLoader)
    if root is None:
        return ()
    if not isinstance(root, yaml.MappingNode):
        raise ValueError("Workflow must contain a mapping")
    jobs = fields(child(fields(root), 'jobs'))
    for job_id, (_, job_node) in jobs.items():
        job = fields(job_node)
        location = '/jobs/' + pointer(job_id)
        add(job, location, 'reusable-workflow')
        container = child(job, 'container')
        if isinstance(container, yaml.MappingNode):
            add_image(fields(container).get('image'), location + '/container/image', 'job-container')
        else:
            add_image(job.get('container'), location + '/container', 'job-container')
        for service_id, (_, service_node) in fields(child(job, 'services')).items():
            add_image(fields(service_node).get('image'),
                      location + '/services/' + pointer(service_id) + '/image', 'service-container')
        steps = child(job, 'steps')
        if isinstance(steps, yaml.SequenceNode):
            for index, step_node in enumerate(steps.value):
                step = fields(step_node)
                if 'run' not in step:
                    add(step, location + f'/steps/{index}', 'action')
    return tuple(sorted(facts))


@dataclass(frozen=True)
class GitHubActionsPlugin:
    descriptor: PluginDescriptor

    def index_file(self, artifact: FileArtifact):
        if artifact.deleted or not re.fullmatch(r'\.github/workflows/[^/]+\.ya?ml', artifact.path):
            return PluginOutcome.abstained()
        try:
            return PluginOutcome.handled(workflow_references(artifact))
        except Exception as exception:
            return PluginOutcome.failed(PluginDiagnostic('github-workflow-parse-failed',
                f'{type(exception).__name__}: {exception}', self.descriptor.id,
                path=artifact.path, recoverable=True))


def create_plugin(descriptor: PluginDescriptor) -> GitHubActionsPlugin:
    return GitHubActionsPlugin(descriptor)
