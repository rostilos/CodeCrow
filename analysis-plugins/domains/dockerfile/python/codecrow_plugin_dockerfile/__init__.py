"""Literal Dockerfile base images, without executing builds or resolving registries."""
from dataclasses import dataclass
import re
import shlex

from codecrow_plugins import FileArtifact, GraphFact, PluginDescriptor, PluginDiagnostic, PluginOutcome


# Consume quoted/escaped text before looking for an unquoted heredoc operator.
_HEREDOC = re.compile(r'''(?:'[^']*'|"(?:\\.|[^"\\])*"|\\.)|(?<!<)<<(?!<)(-?)[ \t]*((?:[^\s'"`\\;&|<>()]|\\.|'[^']*'|"(?:\\.|[^"\\])*")+)''')


def _instructions(content):
    lines = iter(enumerate(content.lstrip('\ufeff').splitlines(), 1))
    escape, directives = '\\', True
    for number, raw in lines:
        line = raw.strip()
        if directives:
            directive = re.fullmatch(r'#\s*(syntax|escape|check)\s*=\s*(.*)', line, re.I)
            directives = directive is not None
            if directive and directive[1].lower() == 'escape':
                if directive[2] not in {'\\', '`'}:
                    raise ValueError(f'Invalid escape directive at line {number}')
                escape = directive[2]
        if not line or line.startswith('#'):
            continue
        while (len(line) - len(line.rstrip(escape))) % 2:
            line = line[:-1]
            for _, continuation in lines:
                if continuation.strip() and not continuation.lstrip().startswith('#'):
                    line += continuation.rstrip()
                    break
            else:
                raise ValueError(f'Unterminated continuation at line {number}')
        yield number, line
        # Tabs separate instructions too; shell/inline-file bodies are never Docker instructions.
        parts = line.split(None, 1)
        instruction, arguments = parts[0].upper(), parts[1] if len(parts) == 2 else ''
        if instruction == 'ONBUILD':
            parts = arguments.split(None, 1)
            instruction, arguments = parts[0].upper(), parts[1] if len(parts) == 2 else ''
        if instruction in {'RUN', 'COPY', 'ADD'} and not arguments.lstrip().startswith('['):
            for match in _HEREDOC.finditer(arguments):
                if match[2] is None:
                    continue
                delimiter, = shlex.split(match[2])
                for _, body in lines:
                    if (body.lstrip('\t') if match[1] else body) == delimiter:
                        break
                else:
                    raise ValueError(f'Unterminated heredoc at line {number}')


def base_image_facts(artifact: FileArtifact):
    facts, stages = [], set()
    for line, instruction in _instructions(artifact.content):
        tokens = instruction.split()
        if tokens[0].upper() != 'FROM':
            continue
        tokens = tokens[1:]
        if tokens and tokens[0].startswith('--platform='):
            tokens = tokens[1:]
        if not (len(tokens) == 1 or len(tokens) == 3 and tokens[1].upper() == 'AS'):
            raise ValueError(f'Invalid FROM declaration at line {line}')
        image = tokens[0]
        # shortcut: literal FROM only; dynamic ARG values and named build contexts need invocation evidence.
        if '$' not in image and image.lower() not in stages and image != 'scratch':
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@+\[\]-]*', image):
                raise ValueError(f'Unsupported FROM image syntax at line {line}')
            facts.append(GraphFact('docker-base-image', artifact.path, 'configures-module',
                image, artifact.path, line=line, attributes=(('referenceKind', 'dockerfile-from'),
                    ('targetResolutionProven', 'false'), ('targetSpecifier', image))))
        if len(tokens) == 3:
            stages.add(tokens[2].lower())
    return tuple(sorted(facts))


@dataclass(frozen=True)
class DockerfilePlugin:
    descriptor: PluginDescriptor

    def index_file(self, artifact: FileArtifact):
        name = artifact.path.rsplit('/', 1)[-1]
        if artifact.deleted or name.endswith('.dockerignore') or not (name == 'Dockerfile' or name.startswith('Dockerfile.') or name.endswith('.Dockerfile')):
            return PluginOutcome.abstained()
        try:
            return PluginOutcome.handled(base_image_facts(artifact))
        except Exception as exception:
            return PluginOutcome.failed(PluginDiagnostic('dockerfile-parse-failed',
                f'{type(exception).__name__}: {exception}', self.descriptor.id,
                path=artifact.path, recoverable=True))


def create_plugin(descriptor: PluginDescriptor) -> DockerfilePlugin:
    return DockerfilePlugin(descriptor)
