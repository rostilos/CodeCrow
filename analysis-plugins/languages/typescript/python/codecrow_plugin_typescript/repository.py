from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
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


TYPESCRIPT_EXTENSIONS = (".cts", ".mts", ".ts", ".tsx")
_IMPORT_FROM = re.compile(
    r"^\s*import\s+(?:type\s+)?(?P<clause>.*?)\s+from\s+"
    r"(?P<quote>['\"])(?P<module>[^'\"]+)(?P=quote)",
    re.DOTALL,
)
_EXPORT_DECLARATION = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
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


def _enclosing_class_name(document: TreeSitterDocument, node) -> str:
    parent = node.parent
    while parent is not None:
        if parent.type == "class_declaration":
            return document.text(_named(parent, "name"))
        parent = parent.parent
    return ""


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
        "language_tsx" if artifact.path.casefold().endswith(".tsx") else "language_typescript",
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
    for node in document.root.named_children:
        if node.type != "comment":
            break
        text = document.text(node)
        # shortcut: entity-escaped directive names remain unknown; support them with compiler-compatible decoding.
        if not text.startswith("///") or not text.rstrip().endswith("/>") or "\n" in text or "&" in text:
            continue
        try:
            directive = ET.fromstring(text[3:].strip())
        except ET.ParseError:
            continue
        name = directive.get("types")
        if directive.tag == "reference" and isinstance(name, str) and name.strip():
            facts.add(GraphFact("typescript-type-reference", module, "declares-dependency", name,
                artifact.path, document.line(node), attributes=(("reference", "types"),)))
    for node in document.walk():
        line = document.line(node)
        if node.type == "import_statement":
            statement = document.text(node)
            parsed = _import_bindings(statement, line=line)
            source = _named(node, "source")
            # Module-only imports still establish a dependency without a local symbol.
            # shortcut: escaped specifiers stay unknown until JS literal decoding is shared.
            if (not parsed and not node.has_error and source is not None and source.type == "string"
                    and all(child.type == "string_fragment" for child in source.named_children)):
                specifier = "".join(document.text(child) for child in source.named_children)
                if specifier:
                    parsed = (ImportBinding(specifier, "", "", line),)
            imports.update(parsed)
            for binding in parsed:
                facts.add(GraphFact(
                    "typescript-import",
                    module,
                    "imports",
                    f"{binding.module}::{binding.imported}" if binding.imported else binding.module,
                    artifact.path,
                    line,
                    attributes=(("local", binding.local),),
                ))
        elif node.type == "export_statement":
            statement = document.text(node)
            source = _named(node, "source")
            # Re-exports depend on a module without introducing a local binding.
            # shortcut: escaped specifiers stay unknown until JS literal decoding is shared.
            if (source is not None and source.type == "string"
                    and all(child.type == "string_fragment" for child in source.named_children)):
                specifier = "".join(document.text(child) for child in source.named_children)
                if specifier:
                    imports.add(ImportBinding(specifier, "", "", line))
                    facts.add(GraphFact("typescript-import", module, "imports", specifier,
                        artifact.path, line, attributes=(("reexport", "true"),)))
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
            if function.type == "import":
                arguments = _named(node, "arguments")
                argument = next((child for child in arguments.named_children
                    if child.type != "comment"), None) if arguments else None
                # shortcut: escaped specifiers stay unresolved; add JS decoding when they need module evidence.
                if (argument is not None and argument.type in {"string", "template_string"}
                        and all(child.type == "string_fragment" for child in argument.named_children)):
                    imported_module = "".join(document.text(child) for child in argument.named_children)
                    if imported_module:
                        imports.add(ImportBinding(imported_module, "", "", line))
                        facts.add(GraphFact("typescript-import", module, "imports", imported_module,
                            artifact.path, line, attributes=(("dynamic", "true"),)))
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
                target = f"{local}.{member}".rstrip(".")
                if local == "this" and member:
                    class_name = _enclosing_class_name(document, node)
                    if class_name:
                        target = f"{class_name}.{member}"
                facts.add(GraphFact(
                    "typescript-call",
                    module,
                    "calls",
                    target,
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
        # A directory index is a fallback after file lookup, not a competing file.
        file_matches = [candidate for candidate in candidates if candidate in records]
        if file_matches:
            return file_matches[0] if len(file_matches) == 1 else ""
        candidates.extend(
            f"{joined}/index{extension}"
            for extension in TYPESCRIPT_EXTENSIONS
        )
    elif suffix in {".js", ".mjs", ".cjs"}:
        replacements = {
            ".js": (".ts", ".tsx"),
            ".mjs": (".mts",),
            ".cjs": (".cts",),
        }[suffix]
        candidates.extend(joined[: -len(suffix)] + replacement for replacement in replacements)
    matches = [
        candidate
        for candidate in candidates
        if candidate in records
    ]
    return matches[0] if len(matches) == 1 else ""
