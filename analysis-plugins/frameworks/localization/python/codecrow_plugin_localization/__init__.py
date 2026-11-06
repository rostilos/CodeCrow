"""Lingui and Lunaria path declarations, without evaluating configuration or globs."""
from collections import Counter
from dataclasses import dataclass
import posixpath

from codecrow_plugins import (FileArtifact, GraphFact, PluginDescriptor,
    PluginDiagnostic, PluginOutcome, TreeSitterDocument)


def _literal(document, node):
    if node is not None and node.type == "string" and all(
        child.type == "string_fragment" for child in node.named_children
    ):
        return document.text(node)[1:-1]
    return None


def _fields(document, node):
    if node is None or node.type != "object":
        return None
    fields = {}
    for child in node.named_children:
        if child.type == "comment":
            continue
        key = child.child_by_field_name("key")
        name = (document.text(key) if key is not None and key.type == "property_identifier"
                else _literal(document, key))
        if child.type != "pair" or name is None or name in fields:
            return None
        fields[name] = child.child_by_field_name("value")
    return fields


def _config_object(document, kind):
    # shortcut: static ESM exports only; add loader-aware evaluation when dynamic/CommonJS evidence is needed.
    nodes = tuple(document.walk())
    names = Counter(document.text(node) for node in nodes if node.type == "identifier")
    bindings, constants, exports = set(), {}, []
    modules = {"@lingui/cli", "@lingui/conf"} if kind == "lingui" else {"@lunariajs/core/config"}
    for node in document.root.named_children:
        if node.type == "import_statement" and not any(child.type == "type" for child in node.children):
            if _literal(document, node.child_by_field_name("source")) not in modules:
                continue
            for item in document.walk(node):
                if item.type != "import_specifier" or any(child.type == "type" for child in item.children):
                    continue
                name = item.child_by_field_name("name")
                alias = item.child_by_field_name("alias") or name
                if document.text(name) == "defineConfig" and names[document.text(alias)] == 2:
                    bindings.add(document.text(alias))
        if node.type == "lexical_declaration" and any(child.type == "const" for child in node.children):
            for declaration in node.named_children:
                name = declaration.child_by_field_name("name")
                if name is not None and name.type == "identifier" and names[document.text(name)] == 2:
                    constants[document.text(name)] = declaration
        if node.type == "export_statement" and any(child.type == "default" for child in node.children):
            exports.append(node.child_by_field_name("value"))
    if len(exports) != 1:
        return None
    def unwrap(node):
        while node is not None and node.type in {"parenthesized_expression", "as_expression", "satisfies_expression"}:
            node = node.named_children[0]
        return node
    node = unwrap(exports[0])
    if node is not None and node.type == "identifier":
        # Only the declaration and export may refer to this object; no mutations or escapes.
        declaration = constants.get(document.text(node))
        node = (unwrap(declaration.child_by_field_name("value"))
                if declaration is not None and declaration.end_byte <= node.start_byte else None)
    if node is not None and node.type == "call_expression":
        function = node.child_by_field_name("function")
        arguments = node.child_by_field_name("arguments")
        args = [child for child in arguments.named_children if child.type != "comment"] if arguments else []
        node = unwrap(args[0]) if document.text(function) in bindings and len(args) == 1 else None
    return _fields(document, node)


def catalog_path_facts(document, artifact, kind):
    config = _config_object(document, kind)
    if config is None:
        return ()
    collection = "catalogs" if kind == "lingui" else "files"
    array = config.get(collection)
    if array is None or array.type != "array":
        return ()
    facts = []
    def add(node, option):
        literal = _literal(document, node)
        if not literal:
            return
        target = literal
        # Explicit rootDir/CWD and locale parameters remain unresolved declarations.
        if kind == "lingui" and "rootDir" not in config and (
            literal == "<rootDir>" or literal.startswith("<rootDir>/")
        ):
            target = posixpath.normpath(posixpath.join(posixpath.dirname(artifact.path), literal[9:].lstrip("/")))
        facts.append(GraphFact("localization-file-pattern", artifact.path, "declares-file-pattern",
            target, artifact.path, document.line(node), attributes=(("option", option),
                ("targetSpecifier", literal), ("tool", kind))))
    for index, entry in enumerate(child for child in array.named_children if child.type != "comment"):
        fields = _fields(document, entry)
        if fields is None:
            continue
        for option in ("include", "exclude"):
            values = fields.get(option)
            if values is not None and values.type == "array":
                for offset, value in enumerate(child for child in values.named_children if child.type != "comment"):
                    add(value, f"{collection}[{index}].{option}[{offset}]")
        option = "path" if kind == "lingui" else "pattern"
        value = fields.get(option)
        patterns = _fields(document, value) if kind == "lunaria" else None
        if patterns is None:
            add(value, f"{collection}[{index}].{option}")
        else:
            for name in ("source", "locales"):
                add(patterns.get(name), f"{collection}[{index}].pattern.{name}")
    return tuple(sorted(facts))


@dataclass(frozen=True)
class LocalizationPlugin:
    descriptor: PluginDescriptor

    def index_file(self, artifact: FileArtifact):
        name = artifact.path.rsplit("/", 1)[-1]
        kind, separator, suffix = name.partition(".config.")
        if artifact.deleted or not separator or kind not in {"lingui", "lunaria"} or suffix not in {"js", "mjs", "cjs", "ts", "mts", "cts"}:
            return PluginOutcome.abstained()
        try:
            document = (TreeSitterDocument.parse(artifact.content, "tree_sitter_typescript", "language_typescript")
                if suffix in {"ts", "mts", "cts"} else
                TreeSitterDocument.parse(artifact.content, "tree_sitter_javascript", "language"))
            if document.root.has_error:
                raise ValueError("Invalid localization configuration syntax")
            return PluginOutcome.handled(catalog_path_facts(document, artifact, kind))
        except Exception as exception:
            return PluginOutcome.failed(PluginDiagnostic("localization-config-parse-failed",
                f"{type(exception).__name__}: {exception}", self.descriptor.id,
                path=artifact.path, recoverable=True))


def create_plugin(descriptor: PluginDescriptor) -> LocalizationPlugin:
    return LocalizationPlugin(descriptor)
