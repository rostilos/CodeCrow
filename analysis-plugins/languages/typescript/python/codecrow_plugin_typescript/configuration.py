"""Local compiler declarations; no inherited configuration or effective file selection."""
import posixpath
import re

from codecrow_plugins import FileArtifact, GraphFact
from codecrow_plugins.jsonc import load_jsonc


def is_typescript_config(path: str) -> bool:
    return bool(re.fullmatch(r"tsconfig(?:\.[^/]+)?\.json", path.rsplit("/", 1)[-1]))


def configuration_facts(artifact: FileArtifact) -> tuple[GraphFact, ...]:
    config = load_jsonc(artifact.content)
    if not isinstance(config, dict):
        raise ValueError("TypeScript configuration must contain an object")
    options = config.get("compilerOptions", {})
    if not isinstance(options, dict):
        raise ValueError("compilerOptions must contain an object")
    types = options.get("types", [])
    if not isinstance(types, list) or any(not isinstance(name, str) for name in types):
        raise ValueError("compilerOptions.types must contain an array of strings")
    facts = [GraphFact(
        "typescript-configured-type", artifact.path, "configures-module", name, artifact.path,
        attributes=(("jsonPointer", f"/compilerOptions/types/{index}"), ("option", "types")),
    ) for index, name in enumerate(types) if name.strip()]
    for option in ("include", "exclude"):
        patterns = config.get(option, [])
        if not isinstance(patterns, list) or any(not isinstance(value, str) for value in patterns):
            raise ValueError(f"{option} must contain an array of strings")
        for index, pattern in enumerate(patterns):
            if not pattern:
                continue
            target = pattern.replace("\\", "/")
            if not posixpath.isabs(target) and not re.match(r"^[A-Za-z]:", target):
                target = posixpath.normpath(posixpath.join(posixpath.dirname(artifact.path), target))
            facts.append(GraphFact(
                "typescript-file-pattern", artifact.path, "declares-file-pattern", target, artifact.path,
                attributes=(("jsonPointer", f"/{option}/{index}"), ("option", option),
                            ("targetSpecifier", pattern)),
            ))
    return tuple(facts)
