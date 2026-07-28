#!/usr/bin/env python3
"""Fail CI when generic hosts couple to a concrete analysis plugin."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "analysis-plugins"
CONTRACT_ROOTS = (
    PLUGIN_ROOT / "contracts" / "java" / "src" / "main",
    PLUGIN_ROOT / "contracts" / "python" / "codecrow_plugins",
)
IMPLEMENTATION_ROOTS = (
    PLUGIN_ROOT / "languages",
    PLUGIN_ROOT / "frameworks",
    PLUGIN_ROOT / "domains",
)
GENERIC_ROOTS = (
    ROOT / "java-ecosystem" / "libs",
    ROOT / "java-ecosystem" / "services",
    ROOT / "python-ecosystem",
)
SOURCE_SUFFIXES = {".java", ".py"}
IGNORED_PARTS = {"target", ".venv", "venv", "__pycache__", ".pytest_cache"}
CONCRETE_ARTIFACT = re.compile(r"<artifactId>(codecrow-plugin-(?!contracts)[^<]+)</artifactId>")
FORBIDDEN_PLUGIN_DEPENDENCIES = (
    "import openai",
    "import anthropic",
    "import langchain",
    "from openai",
    "from anthropic",
    "from langchain",
    "org.springframework",
    "org.rostilos.codecrow.vcsclient",
)
RELEASE_KEYS = {"version", "apiVersion", "schemaVersion", "compatibilityVersion"}


def _source_files(root: Path):
    if not root.exists():
        return
    for path in root.rglob("*"):
        if (
            path.is_file()
            and path.suffix in SOURCE_SUFFIXES
            and not (IGNORED_PARTS & set(path.parts))
        ):
            yield path


def validate() -> list[str]:
    errors: list[str] = []
    descriptors = sorted(
        descriptor
        for kind in ("languages", "frameworks", "domains")
        for descriptor in (PLUGIN_ROOT / kind).glob("*/plugin.json")
    )
    implementation_references: set[str] = set()
    for descriptor in descriptors:
        payload = json.loads(descriptor.read_text(encoding="utf-8"))
        for runtime, entrypoint in payload.get("entrypoints", {}).items():
            implementation_references.add(
                entrypoint.split(":", 1)[0] if runtime == "python" else entrypoint
            )
    for root in GENERIC_ROOTS:
        for path in _source_files(root):
            text = path.read_text(encoding="utf-8")
            for reference in implementation_references:
                if reference in text:
                    errors.append(
                        f"generic host references plugin implementation {reference!r}: {path.relative_to(ROOT)}"
                    )
        for pom in root.rglob("pom.xml"):
            text = pom.read_text(encoding="utf-8")
            for artifact in CONCRETE_ARTIFACT.findall(text):
                errors.append(
                    f"generic host depends on concrete plugin artifact {artifact!r}: "
                    f"{pom.relative_to(ROOT)}"
                )

        for query in root.rglob("*.scm"):
            if not (IGNORED_PARTS & set(query.parts)):
                errors.append(
                    f"generic host owns a language query instead of a plugin: {query.relative_to(ROOT)}"
                )

    if not descriptors:
        errors.append("no plugin descriptors found")
    for descriptor in descriptors:
        payload = json.loads(descriptor.read_text(encoding="utf-8"))
        release_fields = RELEASE_KEYS & payload.keys()
        if release_fields:
            errors.append(
                f"plugin descriptor contains release/compatibility fields {sorted(release_fields)}: "
                f"{descriptor.relative_to(ROOT)}"
            )

    for contract_root in CONTRACT_ROOTS:
        for path in _source_files(contract_root):
            text = path.read_text(encoding="utf-8")
            for dependency in FORBIDDEN_PLUGIN_DEPENDENCIES:
                if dependency in text:
                    errors.append(
                        f"neutral plugin API depends on {dependency!r}: {path.relative_to(ROOT)}"
                    )
    for implementation_root in IMPLEMENTATION_ROOTS:
        for path in _source_files(implementation_root):
            text = path.read_text(encoding="utf-8")
            for dependency in FORBIDDEN_PLUGIN_DEPENDENCIES:
                if dependency in text:
                    errors.append(
                        "plugin implementation depends on host/model API "
                        f"{dependency!r}: {path.relative_to(ROOT)}"
                    )

    if (ROOT / "plugins").exists():
        errors.append(
            "obsolete plugins root still exists; analysis plugins must live "
            "under analysis-plugins/"
        )

    language_descriptors = sorted((PLUGIN_ROOT / "languages").glob("*/plugin.json"))
    for descriptor in language_descriptors:
        payload = json.loads(descriptor.read_text(encoding="utf-8"))
        if "syntax" not in payload["capabilities"]:
            errors.append(f"language plugin has no syntax capability: {descriptor.relative_to(ROOT)}")
        java_entrypoint = payload["entrypoints"].get("java")
        if java_entrypoint and not (descriptor.parent / "java" / "pom.xml").is_file():
            errors.append(f"Java language plugin has no build module: {descriptor.relative_to(ROOT)}")
    for descriptor in descriptors:
        payload = json.loads(descriptor.read_text(encoding="utf-8"))
        if payload["entrypoints"].get("java") and not (descriptor.parent / "java" / "pom.xml").is_file():
            errors.append(f"Java plugin has no build module: {descriptor.relative_to(ROOT)}")

    production_build = (ROOT / "deployment/build/production-build.sh").read_text(encoding="utf-8")
    if "tools/assemble_java_plugins.py" not in production_build:
        errors.append("production build does not assemble independently packaged Java plugins")

    pipeline_dockerfile = (
        ROOT / "java-ecosystem/services/pipeline-agent/Dockerfile"
    ).read_text(encoding="utf-8")
    if "-Dloader.path=/app/plugins" not in pipeline_dockerfile:
        errors.append("pipeline-agent does not load external Java plugins")
    if "analysis-plugins/build/java/" not in pipeline_dockerfile:
        errors.append(
            "pipeline-agent image does not consume the neutral plugin build bundle"
        )

    assembly_script = (
        ROOT / "tools/assemble_java_plugins.py"
    ).read_text(encoding="utf-8")
    if 'PLUGINS_ROOT / "build" / "java"' not in assembly_script:
        errors.append(
            "Java plugin assembly output is not owned by analysis-plugins/"
        )

    for dockerfile in (
        ROOT / "python-ecosystem/inference-orchestrator/src/Dockerfile",
        ROOT / "python-ecosystem/inference-orchestrator/src/Dockerfile.observable",
        ROOT / "python-ecosystem/rag-pipeline/Dockerfile",
    ):
        text = dockerfile.read_text(encoding="utf-8")
        if "COPY analysis-plugins/ ./plugins/" not in text:
            errors.append(
                f"Python image does not package the plugin tree generically: {dockerfile.relative_to(ROOT)}"
            )
        if (
            "analysis-plugins/languages/php" in text
            or "analysis-plugins/frameworks/magento" in text
        ):
            errors.append(f"Python image names a plugin implementation: {dockerfile.relative_to(ROOT)}")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Plugin architecture boundaries are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
