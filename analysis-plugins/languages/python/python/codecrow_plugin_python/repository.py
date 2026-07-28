from __future__ import annotations

import ast
from pathlib import PurePosixPath
from typing import Mapping

from codecrow_plugins import (
    FileArtifact,
    ImportBinding,
    ImportFileRecord,
    ImportedCall,
)


PYTHON_EXTENSIONS = (".py", ".pyi", ".pyw")


def module_for_path(path: str) -> str:
    pure = PurePosixPath(path)
    suffix = pure.suffix.casefold()
    without_suffix = str(pure)[: -len(suffix)] if suffix else str(pure)
    if without_suffix.endswith("/__init__"):
        without_suffix = without_suffix[: -len("/__init__")]
    return without_suffix.replace("/", ".")


def parse_import_record(artifact: FileArtifact) -> ImportFileRecord | None:
    if (
        artifact.deleted
        or not artifact.path.casefold().endswith(PYTHON_EXTENSIONS)
    ):
        return None
    tree = ast.parse(
        artifact.content,
        filename=artifact.path,
        type_comments=True,
    )
    module = module_for_path(artifact.path)
    exports: set[str] = set()
    imports: set[ImportBinding] = set()
    calls: set[ImportedCall] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            exports.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            for target in targets:
                if isinstance(target, ast.Name):
                    exports.add(target.id)
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                imports.add(ImportBinding(
                    module=alias.name,
                    imported="*",
                    local=local,
                    line=node.lineno,
                ))
        elif isinstance(node, ast.ImportFrom):
            specifier = "." * node.level + (node.module or "")
            for alias in node.names:
                imports.add(ImportBinding(
                    module=specifier,
                    imported=alias.name,
                    local=alias.asname or alias.name,
                    line=node.lineno,
                ))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name):
            calls.add(ImportedCall(function.id, "", node.lineno))
        elif (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
        ):
            calls.add(ImportedCall(
                function.value.id,
                function.attr,
                node.lineno,
            ))
    return ImportFileRecord(
        path=artifact.path,
        module=module,
        exports=tuple(sorted(exports)),
        imports=tuple(sorted(imports)),
        calls=tuple(sorted(calls)),
    )


def resolve_import(
    source: ImportFileRecord,
    binding: ImportBinding,
    records: Mapping[str, ImportFileRecord],
) -> str:
    by_module = {
        record.module: record.path
        for record in records.values()
    }
    specifier = binding.module
    if not specifier.startswith("."):
        return by_module.get(specifier, "")
    level = len(specifier) - len(specifier.lstrip("."))
    remainder = specifier[level:]
    package = source.module.split(".")[:-1]
    if level > len(package):
        return ""
    base = package[: len(package) - level + 1]
    resolved = ".".join(
        [*base, *(part for part in remainder.split(".") if part)]
    )
    return by_module.get(resolved, "")
