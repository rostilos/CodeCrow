#!/usr/bin/env python3
"""Audit operator-owned Python environment settings against deployment samples."""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DECLARATION_RE = re.compile(
    r"^\s*(?P<commented>#\s*)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<value>[^\r\n]*)$",
    re.MULTILINE,
)
ENV_HELPERS = {
    "_env_bool",
    "_env_float",
    "_env_int",
    "_parse_csv_env",
    "_parse_float_env",
    "env_bool",
    "env_float",
    "env_int",
    "secret_from_env",
}
IGNORED_INTERNAL_ENV = {"_CODECROW_SETTINGS_FETCHED"}


@dataclass(frozen=True)
class ServiceConfig:
    name: str
    code_roots: tuple[Path, ...]
    sample: Path
    required_active: set[str] = field(default_factory=set)
    request_owned_env: set[str] = field(default_factory=set)


INFERENCE_REQUEST_OWNED_ENV = {
    # AI connection fields are supplied by the Java pipeline agent per request.
    "AI_PROVIDER",
    "AI_MODEL",
    "AI_API_KEY",
    "OPENROUTER_API_KEY",
    "QA_DOC_AI_PROVIDER",
    "QA_DOC_AI_MODEL",
    "QA_DOC_AI_API_KEY",
    # Vertex project/location belongs to the request's AI connection metadata.
    "GOOGLE_VERTEX_PROJECT",
    "GOOGLE_CLOUD_PROJECT",
    "GCLOUD_PROJECT",
    "GOOGLE_VERTEX_LOCATION",
    "GOOGLE_CLOUD_LOCATION",
    # OpenAI-compatible parameters are sent as aiCustomParameters by Java.
    "OPENAI_COMPATIBLE_CUSTOM_PARAMS",
    "OPENAI_COMPATIBLE_CUSTOM_PARAMS_JSON",
    "OPENAI_COMPATIBLE_MODEL_KWARGS",
    "OPENAI_COMPATIBLE_MODEL_KWARGS_JSON",
    "OPENAI_COMPATIBLE_EXTRA_BODY",
    "OPENAI_COMPATIBLE_EXTRA_BODY_JSON",
    "OPENAI_COMPATIBLE_DEFAULT_HEADERS",
    "OPENAI_COMPATIBLE_DEFAULT_HEADERS_JSON",
    "OPENAI_COMPATIBLE_CONSTRUCTOR_KWARGS",
    "OPENAI_COMPATIBLE_CONSTRUCTOR_KWARGS_JSON",
}


SERVICES = (
    ServiceConfig(
        name="inference-orchestrator",
        code_roots=(REPO_ROOT / "python-ecosystem/inference-orchestrator/src",),
        sample=REPO_ROOT / "deployment/config/inference-orchestrator/.env.sample",
        required_active={"SERVICE_SECRET"},
        request_owned_env=INFERENCE_REQUEST_OWNED_ENV,
    ),
    ServiceConfig(
        name="rag-pipeline",
        code_roots=(
            REPO_ROOT / "python-ecosystem/rag-pipeline/main.py",
            REPO_ROOT / "python-ecosystem/rag-pipeline/src",
        ),
        sample=REPO_ROOT / "deployment/config/rag-pipeline/.env.sample",
        required_active={"SERVICE_SECRET"},
    ),
)


def _python_files(roots: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    excluded_parts = {".venv", "__pycache__", "integration", "tests"}
    for root in roots:
        candidates = root.rglob("*.py") if root.is_dir() else (root,)
        files.extend(
            path for path in candidates
            if not excluded_parts.intersection(path.parts)
        )
    return sorted(set(files))


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_env_arguments(node: ast.Call) -> list[ast.AST]:
    function = node.func
    if (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id in {"os", "_os"}
        and function.attr == "getenv"
    ):
        return list(node.args[:1])
    if (
        isinstance(function, ast.Attribute)
        and function.attr == "get"
        and isinstance(function.value, ast.Attribute)
        and isinstance(function.value.value, ast.Name)
        and function.value.value.id in {"os", "_os"}
        and function.value.attr == "environ"
    ):
        return list(node.args[:1])
    if isinstance(function, ast.Name) and function.id in ENV_HELPERS:
        return list(node.args[:1])
    if isinstance(function, ast.Name) and function.id == "_parse_env_json_object":
        return list(node.args)
    return []


def _code_references(config: ServiceConfig) -> set[str]:
    references: set[str] = set()
    for path in _python_files(config.code_roots):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for argument in _call_env_arguments(node):
                    name = _literal_string(argument)
                    if name:
                        references.add(name)
            elif (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id in {"os", "_os"}
                and node.value.attr == "environ"
            ):
                name = _literal_string(node.slice)
                if name:
                    references.add(name)

    if config.name == "inference-orchestrator":
        # inference_policy.py constructs these canonical names dynamically.
        for stage in ("STAGE_0", "STAGE_1", "VERIFICATION", "STAGE_2", "DEDUP", "STAGE_3"):
            references.add(f"REVIEW_{stage}_MAX_OUTPUT_TOKENS")
            for size in ("SMALL", "MEDIUM", "LARGE"):
                references.add(f"REVIEW_{stage}_{size}_MAX_OUTPUT_TOKENS")

    return references - IGNORED_INTERNAL_ENV


def _literal_default(node: ast.AST) -> str | None:
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError):
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return ",".join(value)
    return None


def _code_literal_defaults(config: ServiceConfig) -> dict[str, set[str]]:
    """Collect defaults that can be compared without evaluating application code."""
    defaults: dict[str, set[str]] = {}
    for path in _python_files(config.code_roots):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            arguments = _call_env_arguments(node)
            if len(arguments) != 1:
                continue
            name = _literal_string(arguments[0])
            default = _literal_default(node.args[1])
            if name and default is not None:
                defaults.setdefault(name, set()).add(default)
    return defaults


def _sample_declarations(sample: Path) -> tuple[set[str], set[str]]:
    declared: set[str] = set()
    active: set[str] = set()
    for match in DECLARATION_RE.finditer(sample.read_text(encoding="utf-8")):
        name = match.group("name")
        declared.add(name)
        if not match.group("commented"):
            active.add(name)
    return declared, active


def _sample_values(sample: Path) -> dict[str, str]:
    return {
        match.group("name"): match.group("value").strip()
        for match in DECLARATION_RE.finditer(sample.read_text(encoding="utf-8"))
    }


def audit(config: ServiceConfig) -> list[str]:
    references = _code_references(config)
    declared, active = _sample_declarations(config.sample)
    missing = references - declared - config.request_owned_env
    unused = declared - references
    missing_required = config.required_active - active
    unexpectedly_active = active - config.required_active
    request_owned_declarations = declared & config.request_owned_env
    stale_request_owned_classification = config.request_owned_env - references
    literal_defaults = _code_literal_defaults(config)
    sample_values = _sample_values(config.sample)

    errors: list[str] = []
    for label, values in (
        ("missing declarations", missing),
        ("unused declarations", unused),
        ("required variables not active", missing_required),
        ("optional variables must be commented", unexpectedly_active),
        ("request-owned variables declared in sample", request_owned_declarations),
        ("stale request-owned classification", stale_request_owned_classification),
    ):
        if values:
            errors.append(f"{config.name}: {label}: {', '.join(sorted(values))}")

    for name, defaults in sorted(literal_defaults.items()):
        if name in config.required_active or name not in sample_values:
            continue
        if sample_values[name] not in defaults:
            errors.append(
                f"{config.name}: {name} sample value {sample_values[name]!r} "
                f"does not match code default(s): {', '.join(sorted(repr(value) for value in defaults))}"
            )

    print(
        f"{config.name}: {len(references)} code variables, "
        f"{len(declared)} sample declarations, {len(active)} active"
    )
    return errors


def audit_compose_sample() -> list[str]:
    """Keep deployment/.env.sample aligned with both Compose definitions."""
    compose_text = "\n".join(
        (REPO_ROOT / path).read_text(encoding="utf-8")
        for path in ("deployment/docker-compose.yml", "deployment/docker-compose.prod.yml")
    )
    references = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", compose_text))
    required = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*):\?", compose_text))
    sample = REPO_ROOT / "deployment/.env.sample"
    declared, active = _sample_declarations(sample)

    errors: list[str] = []
    for label, values in (
        ("missing declarations", references - declared),
        ("unused declarations", declared - references),
        ("required variables not active", required - active),
        ("optional variables must be commented", active - required),
    ):
        if values:
            errors.append(f"deployment compose: {label}: {', '.join(sorted(values))}")

    print(
        f"deployment compose: {len(references)} referenced variables, "
        f"{len(declared)} sample declarations, {len(active)} active"
    )
    return errors


def main() -> int:
    errors = [error for config in SERVICES for error in audit(config)]
    errors.extend(audit_compose_sample())
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Environment samples are synchronized with Python production code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
