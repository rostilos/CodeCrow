from __future__ import annotations

import posixpath
import re
from pathlib import PurePosixPath
from typing import Mapping

from codecrow_plugins import (
    FileArtifact,
    GraphFact,
    ImportBinding,
    ImportFileRecord,
    ImportedCall,
    TreeSitterDocument,
)


TYPESCRIPT_EXTENSIONS = (".cts", ".mts", ".ts")
_IMPORT_FROM = re.compile(
    r"^\s*import\s+(?:type\s+)?(?P<clause>.*?)\s+from\s+"
    r"(?P<quote>['\"])(?P<module>[^'\"]+)(?P=quote)",
    re.DOTALL,
)
_EXPORT_DECLARATION = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?"
    r"(?:class|interface|type|enum|function|const|let|var)\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)"
)
_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def module_for_path(path: str) -> str:
    pure = PurePosixPath(path)
    suffix = pure.suffix.casefold()
    without_suffix = str(pure)[: -len(suffix)] if suffix else str(pure)
    if without_suffix.endswith("/index"):
        without_suffix = without_suffix[: -len("/index")]
    return without_suffix


def _named(node, field: str):
    return node.child_by_field_name(field)


def _import_bindings(
    statement: str,
    *,
    line: int,
) -> tuple[ImportBinding, ...]:
    match = _IMPORT_FROM.match(statement)
    if match is None:
        return ()
    clause = match.group("clause").strip()
    module = match.group("module")
    bindings: set[ImportBinding] = set()

    default_clause = clause
    remainder = ""
    if clause.startswith(("{", "*")):
        default_clause, remainder = "", clause
    elif "," in clause:
        default_clause, remainder = clause.split(",", 1)
    default_clause = default_clause.strip()
    if _IDENTIFIER.fullmatch(default_clause):
        bindings.add(ImportBinding(module, "default", default_clause, line))

    remainder = remainder.strip()
    if remainder.startswith("*"):
        namespace = re.search(
            r"\*\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*)",
            remainder,
        )
        if namespace:
            bindings.add(ImportBinding(
                module,
                "*",
                namespace.group(1),
                line,
            ))
    elif remainder.startswith("{") and "}" in remainder:
        for raw_item in remainder[1:remainder.rfind("}")].split(","):
            item = raw_item.strip()
            if not item:
                continue
            item = re.sub(r"^type\s+", "", item)
            parts = re.split(r"\s+as\s+", item)
            imported = parts[0].strip()
            local = parts[-1].strip()
            if _IDENTIFIER.fullmatch(imported) and _IDENTIFIER.fullmatch(local):
                bindings.add(ImportBinding(
                    module,
                    imported,
                    local,
                    line,
                ))
    return tuple(sorted(bindings))


def analyze_typescript_artifact(
    artifact: FileArtifact,
) -> tuple[tuple[GraphFact, ...], ImportFileRecord] | None:
    if (
        artifact.deleted
        or not artifact.path.casefold().endswith(TYPESCRIPT_EXTENSIONS)
    ):
        return None
    document = TreeSitterDocument.parse(
        artifact.content,
        "tree_sitter_typescript",
        "language_typescript",
    )
    module = module_for_path(artifact.path)
    facts: set[GraphFact] = {
        GraphFact(
            "typescript-module",
            artifact.path,
            "declares",
            module,
            artifact.path,
        )
    }
    exports: set[str] = set()
    imports: set[ImportBinding] = set()
    calls: set[ImportedCall] = set()
    for node in document.walk():
        line = document.line(node)
        if node.type == "import_statement":
            statement = document.text(node)
            parsed = _import_bindings(statement, line=line)
            imports.update(parsed)
            for binding in parsed:
                facts.add(GraphFact(
                    "typescript-import",
                    module,
                    "imports",
                    f"{binding.module}::{binding.imported}",
                    artifact.path,
                    line,
                    attributes=(("local", binding.local),),
                ))
        elif node.type == "export_statement":
            statement = document.text(node)
            declaration = _EXPORT_DECLARATION.match(statement)
            if declaration:
                exported = declaration.group(1)
                exports.add(exported)
                facts.add(GraphFact(
                    "typescript-export",
                    module,
                    "exports",
                    exported,
                    artifact.path,
                    line,
                ))
            elif statement.lstrip().startswith("export {"):
                body = statement[
                    statement.find("{") + 1:statement.rfind("}")
                ]
                for raw_item in body.split(","):
                    item = re.sub(r"^type\s+", "", raw_item.strip())
                    if not item:
                        continue
                    parts = re.split(r"\s+as\s+", item)
                    exported = parts[-1].strip()
                    if _IDENTIFIER.fullmatch(exported):
                        exports.add(exported)
                        facts.add(GraphFact(
                            "typescript-export",
                            module,
                            "exports",
                            exported,
                            artifact.path,
                            line,
                        ))
        elif node.type in {
            "class_declaration",
            "interface_declaration",
            "type_alias_declaration",
            "enum_declaration",
        }:
            name = document.text(_named(node, "name"))
            if name:
                relation = {
                    "class_declaration": "declares-class",
                    "interface_declaration": "declares-interface",
                    "type_alias_declaration": "declares-type",
                    "enum_declaration": "declares-enum",
                }[node.type]
                facts.add(GraphFact(
                    "typescript-type",
                    module,
                    relation,
                    name,
                    artifact.path,
                    line,
                ))
        elif node.type == "function_declaration":
            name = document.text(_named(node, "name"))
            if name:
                facts.add(GraphFact(
                    "typescript-callable",
                    module,
                    "declares-function",
                    name,
                    artifact.path,
                    line,
                ))
        elif node.type == "variable_declarator":
            name = document.text(_named(node, "name"))
            value = _named(node, "value")
            if name and value is not None and value.type == "arrow_function":
                facts.add(GraphFact(
                    "typescript-callable",
                    module,
                    "declares-function",
                    name,
                    artifact.path,
                    line,
                ))
        elif node.type == "call_expression":
            function = _named(node, "function")
            if function is None:
                continue
            if function.type == "identifier":
                local = document.text(function)
                member = ""
            elif function.type == "member_expression":
                object_node = _named(function, "object")
                property_node = _named(function, "property")
                local = document.text(object_node).split(".", 1)[0]
                member = document.text(property_node)
            else:
                continue
            if local:
                calls.add(ImportedCall(local, member, line))
                facts.add(GraphFact(
                    "typescript-call",
                    module,
                    "calls",
                    f"{local}.{member}".rstrip("."),
                    artifact.path,
                    line,
                ))
        elif node.type == "new_expression":
            constructor = _named(node, "constructor")
            target = document.text(constructor)
            if target:
                local = target.split(".", 1)[0]
                calls.add(ImportedCall(local, "<init>", line))
                facts.add(GraphFact(
                    "typescript-construction",
                    module,
                    "constructs",
                    target,
                    artifact.path,
                    line,
                ))
    return (
        tuple(sorted(facts)),
        ImportFileRecord(
            path=artifact.path,
            module=module,
            exports=tuple(sorted(exports)),
            imports=tuple(sorted(imports)),
            calls=tuple(sorted(calls)),
        ),
    )


def parse_import_record(artifact: FileArtifact) -> ImportFileRecord | None:
    analyzed = analyze_typescript_artifact(artifact)
    return analyzed[1] if analyzed is not None else None


def resolve_import(
    source: ImportFileRecord,
    binding: ImportBinding,
    records: Mapping[str, ImportFileRecord],
) -> str:
    if not binding.module.startswith("."):
        return ""
    joined = posixpath.normpath(
        posixpath.join(
            str(PurePosixPath(source.path).parent),
            binding.module,
        )
    )
    if joined == ".." or joined.startswith("../") or joined.startswith("/"):
        return ""
    candidates = [joined]
    suffix = PurePosixPath(joined).suffix.casefold()
    if not suffix:
        candidates.extend(
            f"{joined}{extension}"
            for extension in TYPESCRIPT_EXTENSIONS
        )
        candidates.extend(
            f"{joined}/index{extension}"
            for extension in TYPESCRIPT_EXTENSIONS
        )
    elif suffix in {".js", ".mjs", ".cjs"}:
        replacement = {
            ".js": ".ts",
            ".mjs": ".mts",
            ".cjs": ".cts",
        }[suffix]
        candidates.append(joined[: -len(suffix)] + replacement)
    matches = [
        candidate
        for candidate in candidates
        if candidate in records
    ]
    return matches[0] if len(matches) == 1 else ""
