from __future__ import annotations

from typing import Mapping

from codecrow_plugins import (
    FileArtifact,
    ImportBinding,
    ImportFileRecord,
    ImportedCall,
    TreeSitterDocument,
)


_TYPE_NODES = {
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
    "annotation_type_declaration",
}


def _named(node, field: str):
    return node.child_by_field_name(field)


def parse_import_record(artifact: FileArtifact) -> ImportFileRecord | None:
    if artifact.deleted or not artifact.path.casefold().endswith(".java"):
        return None
    document = TreeSitterDocument.parse(
        artifact.content,
        "tree_sitter_java",
        "language",
    )
    package = ""
    exports: set[str] = set()
    imports: set[ImportBinding] = set()
    calls: set[ImportedCall] = set()
    for node in document.root.named_children:
        if node.type == "package_declaration":
            value = document.text(node)
            package = value.removeprefix("package ").rstrip(";").strip()
        elif node.type == "import_declaration":
            value = (
                document.text(node)
                .removeprefix("import ")
                .rstrip(";")
                .strip()
            )
            value = value.removeprefix("static ")
            imported = value.rsplit(".", 1)[-1]
            if imported != "*":
                imports.add(ImportBinding(
                    module=value,
                    imported=imported,
                    local=imported,
                    line=document.line(node),
                ))
    for node in document.walk():
        if node.type in _TYPE_NODES:
            name = document.text(_named(node, "name"))
            if name:
                exports.add(name)
        elif node.type == "method_invocation":
            receiver = _named(node, "object")
            method = _named(node, "name")
            receiver_name = document.text(receiver)
            method_name = document.text(method)
            if receiver_name and method_name:
                local = receiver_name.split(".", 1)[0]
                imports.add(ImportBinding(
                    module="",
                    imported=local,
                    local=local,
                    line=document.line(node),
                ))
                calls.add(ImportedCall(
                    local=local,
                    member=method_name,
                    line=document.line(node),
                ))
        elif node.type == "object_creation_expression":
            target = document.text(_named(node, "type"))
            if target:
                local = target.rsplit(".", 1)[-1]
                imports.add(ImportBinding(
                    module=target if "." in target else "",
                    imported=local,
                    local=local,
                    line=document.line(node),
                ))
                calls.add(ImportedCall(
                    local=local,
                    member="<init>",
                    line=document.line(node),
                ))
    return ImportFileRecord(
        path=artifact.path,
        module=package or artifact.path,
        exports=tuple(sorted(exports)),
        imports=tuple(sorted(imports)),
        calls=tuple(sorted(calls)),
    )


def resolve_import(
    source: ImportFileRecord,
    binding: ImportBinding,
    records: Mapping[str, ImportFileRecord],
) -> str:
    candidates: list[str] = []
    for record in records.values():
        if binding.imported not in record.exports:
            continue
        qualified = (
            f"{record.module}.{binding.imported}"
            if record.module
            else binding.imported
        )
        if binding.module:
            if binding.module == qualified:
                candidates.append(record.path)
        elif record.module == source.module:
            candidates.append(record.path)
    return candidates[0] if len(candidates) == 1 else ""
