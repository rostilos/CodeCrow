"""Plugin-owned syntax fallback when the host Python parser is older than source."""
from __future__ import annotations

import ast
import logging

from codecrow_plugins import (
    FileArtifact, GraphFact, ImportBinding, ImportFileRecord, ImportedCall,
    TreeSitterDocument,
)

logger = logging.getLogger(__name__)


def parse_source(artifact: FileArtifact) -> ast.Module | TreeSitterDocument:
    try:
        return ast.parse(artifact.content, filename=artifact.path, type_comments=True)
    except SyntaxError as original:
        try:
            document = TreeSitterDocument.parse(artifact.content, "tree_sitter_python", "language")
        except (ImportError, TypeError, ValueError):
            raise original
        if document.root.has_error:
            raise original
        logger.info("Python host syntax unavailable; using Tree-sitter: %s", artifact.path)
        return document


def _imports(document: TreeSitterDocument, node):
    # Import grammar is stable across host versions; retain the original line.
    statement = ast.parse(document.text(node)).body[0]
    for alias in statement.names:
        if isinstance(statement, ast.Import):
            yield ImportBinding(alias.name, "*", alias.asname or alias.name.split(".", 1)[0], document.line(node))
        else:
            yield ImportBinding("." * statement.level + (statement.module or ""),
                                alias.name, alias.asname or alias.name, document.line(node))


def import_record(document: TreeSitterDocument, path: str, module: str) -> ImportFileRecord:
    exports, imports, calls = set(), set(), set()
    for node in document.root.named_children:
        if node.type == "decorated_definition":
            node = node.child_by_field_name("definition")
        if node.type in {"class_definition", "function_definition"}:
            exports.add(document.text(node.child_by_field_name("name")))
        elif node.type == "expression_statement":
            for assignment in node.named_children:
                if assignment.type == "assignment":
                    target = assignment.child_by_field_name("left")
                    if target is not None and target.type == "identifier":
                        exports.add(document.text(target))
        elif node.type in {"import_statement", "import_from_statement"}:
            imports.update(_imports(document, node))
    for node in document.walk():
        if node.type != "call":
            continue
        function = node.child_by_field_name("function")
        if function.type == "identifier":
            calls.add(ImportedCall(document.text(function), "", document.line(node)))
        elif function.type == "attribute":
            receiver = function.child_by_field_name("object")
            if receiver.type == "identifier":
                calls.add(ImportedCall(document.text(receiver),
                                       document.text(function.child_by_field_name("attribute")), document.line(node)))
    return ImportFileRecord(path, module, tuple(sorted(exports)), tuple(sorted(imports)), tuple(sorted(calls)))


def _name(document: TreeSitterDocument, node) -> str:
    if node is None:
        return ""
    if node.type == "subscript":
        return _name(document, node.child_by_field_name("value"))
    if node.type == "call":
        return _name(document, node.child_by_field_name("function"))
    return document.text(node)


def graph_facts(document: TreeSitterDocument, path: str, module: str) -> tuple[GraphFact, ...]:
    facts = {GraphFact("python-module", path, "declares", module, path)}
    for node in document.walk():
        line = document.line(node)
        if node.type in {"import_statement", "import_from_statement"}:
            for binding in _imports(document, node):
                target = binding.module if node.type == "import_statement" else f"{binding.module}.{binding.imported}".strip(".")
                facts.add(GraphFact("python-import", module, "imports", target, path, line))
        elif node.type in {"class_definition", "function_definition"}:
            name = document.text(node.child_by_field_name("name"))
            if node.type == "class_definition":
                owner = module
                qualified = f"{module}.{name}"
                facts.add(GraphFact("python-type", module, "declares", qualified, path, line, (("type", "class"),)))
                bases = node.child_by_field_name("superclasses")
                for base in bases.named_children if bases is not None else ():
                    if base.type != "keyword_argument":
                        facts.add(GraphFact("python-inheritance", qualified, "extends", _name(document, base), path, line))
            else:
                ancestor = node.parent
                while ancestor is not None and ancestor.type != "class_definition":
                    ancestor = ancestor.parent
                owner = f"{module}.{document.text(ancestor.child_by_field_name('name'))}" if ancestor is not None else module
                qualified = f"{owner}.{name}"
                relation = "declares-async-function" if any(child.type == "async" for child in node.children) else "declares-function"
                facts.add(GraphFact("python-callable", owner, relation, name, path, line))
            if node.parent is not None and node.parent.type == "decorated_definition":
                for decorator in node.parent.named_children:
                    if decorator.type == "decorator":
                        facts.add(GraphFact("python-decorator", qualified, "decorated-by",
                                            _name(document, decorator.named_children[0]), path, document.line(decorator)))
        elif node.type == "call":
            facts.add(GraphFact("python-call", module, "calls", _name(document, node.child_by_field_name("function")), path, line))
    return tuple(sorted(facts))
