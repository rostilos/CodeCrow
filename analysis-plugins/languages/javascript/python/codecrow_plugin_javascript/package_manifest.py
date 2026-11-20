"""Manifest declarations are module references, not installed-package evidence."""
import json

from codecrow_plugins import FileArtifact, GraphFact


def package_dependency_facts(artifact: FileArtifact) -> tuple[GraphFact, ...]:
    manifest = json.loads(artifact.content)
    if not isinstance(manifest, dict):
        raise ValueError("package.json must contain an object")
    facts = []
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        dependencies = manifest.get(section, {})
        if not isinstance(dependencies, dict):
            raise ValueError(f"package.json {section} must contain an object")
        for name, specifier in dependencies.items():
            if not isinstance(specifier, str):
                raise ValueError(f"package.json {section}/{name} must contain a string")
            pointer = f"/{section}/{name.replace('~', '~0').replace('/', '~1')}"
            # JSON pointers identify declarations without guessing a token's source line.
            facts.append(GraphFact(
                "javascript-package-dependency", artifact.path, "declares-dependency",
                name, artifact.path, attributes=(("jsonPointer", pointer),
                    ("section", section), ("specifier", specifier)),
            ))
    return tuple(sorted(facts))

