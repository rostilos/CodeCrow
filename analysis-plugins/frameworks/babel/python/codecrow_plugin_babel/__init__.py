"""Source declarations of Babel modules; no package or runtime resolution."""
from collections import Counter
from dataclasses import dataclass

from codecrow_plugins import (FileArtifact, GraphFact, ImportBinding, PluginDescriptor,
    PluginDiagnostic, PluginOutcome, TreeSitterDocument)


_BABEL_OPTIONS = {
    "transform": 1, "transformSync": 1, "transformAsync": 1,
    "transformFile": 1, "transformFileSync": 1, "transformFileAsync": 1,
    "transformFromAst": 2, "transformFromAstSync": 2, "transformFromAstAsync": 2,
}


def _literal(document, node):
    if node is not None and node.type == "string" and all(
        child.type == "string_fragment" for child in node.named_children
    ):
        return document.text(node)[1:-1]
    return ""


def _property(document, node, name):
    if node is None or node.type != "object":
        return None
    values = []
    for child in node.named_children:
        if child.type == "comment":
            continue
        key = child.child_by_field_name("key") or child.child_by_field_name("name")
        if child.type == "spread_element" or key is not None and key.type == "computed_property_name":
            return None
        if document.text(key).strip("'\"") == name or document.text(child) == name:
            values.append(child.child_by_field_name("value") if child.type == "pair" else None)
    return values[0] if len(values) == 1 else None


def _loader_resolver(document, nodes, imports):
    bindings = {item.local: item for item in imports}
    definitions = Counter(item.local for item in imports)
    declarations, mutated = {}, set()
    for node in nodes:
        fields = []
        if node.type in {"variable_declarator", "function_declaration", "function_expression",
                         "class_declaration", "class", "generator_function", "generator_function_declaration"}:
            fields.append(node.child_by_field_name("name"))
        if node.type in {"assignment_expression", "augmented_assignment_expression", "update_expression"}:
            left = node.child_by_field_name("left") or node.child_by_field_name("argument")
            if left is not None:
                mutated.update(document.text(part) for part in document.walk(left)
                               if part.type in {"identifier", "shorthand_property_identifier_pattern"})
        if node.type in {"formal_parameters", "catch_clause", "arrow_function"}:
            fields.append(node if node.type == "formal_parameters" else node.child_by_field_name("parameter"))
        for field in fields:
            if field is not None:
                definitions.update(document.text(part) for part in document.walk(field)
                                   if part.type in {"identifier", "shorthand_property_identifier_pattern"})
        if (node.type != "variable_declarator" or node.parent.type != "lexical_declaration"
                or not any(child.type == "const" for child in node.parent.children)):
            continue
        name = node.child_by_field_name("name")
        if name is not None and name.type == "identifier":
            declarations[document.text(name)] = (node, "*")
        elif name is not None and name.type == "object_pattern":
            for part in name.named_children:
                if part.type == "shorthand_property_identifier_pattern":
                    declarations[document.text(part)] = (node, document.text(part))
                elif part.type == "pair_pattern":
                    key, value = part.child_by_field_name("key"), part.child_by_field_name("value")
                    if key is not None and key.type == "property_identifier" and value is not None and value.type == "identifier":
                        declarations[document.text(value)] = (node, document.text(key))

    def resolve(node, seen=frozenset()):
        if node is None:
            return None
        if node.type in {"parenthesized_expression", "as_expression", "satisfies_expression", "non_null_expression"}:
            return resolve(node.named_children[0], seen)
        if node.type == "await_expression":
            binding = resolve(node.named_children[0], seen)
            return (binding[1], "*") if binding and binding[0] == "module-promise" else None
        if node.type == "identifier":
            name = document.text(node)
            # shortcut: duplicate bindings are excluded file-wide; add lexical disambiguation when needed.
            if definitions[name] != 1 or name in mutated or name in seen:
                return None
            if name in bindings:
                binding = bindings[name]
                return binding.module, binding.imported
            if name not in declarations:
                return None
            declaration, member = declarations[name]
            scope = declaration.parent
            while scope.parent is not None and scope.type not in {"statement_block", "program"}:
                scope = scope.parent
            if not (scope.start_byte <= node.start_byte < scope.end_byte and declaration.end_byte <= node.start_byte):
                return None
            binding = resolve(declaration.child_by_field_name("value"), seen | {name})
            if member != "*":
                return (binding[0], member) if binding and binding[1] == "*" else None
            return binding
        if node.type == "member_expression":
            binding = resolve(node.child_by_field_name("object"), seen)
            return (binding[0], document.text(node.child_by_field_name("property"))) if binding and binding[1] in {"*", "default"} else None
        if node.type != "call_expression":
            return None
        function, arguments = node.child_by_field_name("function"), node.child_by_field_name("arguments")
        args = [child for child in arguments.named_children if child.type != "comment"] if arguments else []
        if len(args) != 1 or args[0].type == "spread_element":
            return None
        if function is not None and function.type == "import":
            module = _literal(document, args[0])
            resolved = resolve(args[0], seen) if not module else None
            if resolved and resolved[0] == "resolved-module":
                module = resolved[1]
            return ("module-promise", module) if module in {"@babel/core", "@vitejs/plugin-react"} else None
        binding = resolve(function, seen)
        if binding in {("node:module", "createRequire"), ("module", "createRequire")}:
            return "node:require", "*"
        if binding == ("node:require", "resolve"):
            module = _literal(document, args[0])
            return ("resolved-module", module) if module else None
        return None

    return resolve


def _configured_module_facts(document, artifact, imports):
    nodes = tuple(document.walk())
    resolve = _loader_resolver(document, nodes, imports)
    facts = set()
    for node in nodes:
        if node.type != "call_expression":
            continue
        function = node.child_by_field_name("function")
        arguments = node.child_by_field_name("arguments")
        if function is None or arguments is None:
            continue
        args = [child for child in arguments.named_children if child.type != "comment"]
        if any(child.type == "spread_element" for child in args):
            continue
        binding = resolve(function)
        if not binding:
            continue
        module, method = binding
        if module == "@babel/core" and method in _BABEL_OPTIONS:
            position = _BABEL_OPTIONS[method]
            options = args[position] if len(args) > position else None
        elif module == "@vitejs/plugin-react" and method == "default" and args:
            options = _property(document, args[0], "babel")
        else:
            continue
        # shortcut: only direct literal options; dynamic configs and escaped names need loader-aware evaluation.
        for option in ("plugins", "presets"):
            array = _property(document, options, option)
            if array is None or array.type != "array":
                continue
            for entry in array.named_children:
                values = [child for child in entry.named_children if child.type != "comment"]
                if entry.type == "array":
                    if not values or len(values) > 1 and values[1].type == "false":
                        continue
                    entry = values[0]
                target = _literal(document, entry)
                if target:
                    facts.add(GraphFact("babel-configured-module", artifact.path, "configures-module", target,
                        artifact.path, document.line(entry), attributes=(("loader", module), ("option", option))))
    return tuple(sorted(facts))


@dataclass(frozen=True)
class BabelPlugin:
    descriptor: PluginDescriptor

    def index_file(self, artifact: FileArtifact):
        suffix = artifact.path.rsplit(".", 1)[-1].lower()
        if artifact.deleted or suffix not in {"js", "jsx", "mjs", "cjs", "ts", "tsx", "mts", "cts"}:
            return PluginOutcome.abstained()
        if "@babel/core" not in artifact.content and "@vitejs/plugin-react" not in artifact.content:
            return PluginOutcome.abstained()
        try:
            if suffix in {"ts", "tsx", "mts", "cts"}:
                document = TreeSitterDocument.parse(artifact.content, "tree_sitter_typescript",
                    "language_tsx" if suffix == "tsx" else "language_typescript")
            else:
                document = TreeSitterDocument.parse(artifact.content, "tree_sitter_javascript", "language")
            imports = []
            for node in document.root.named_children:
                if node.type != "import_statement" or any(child.type == "type" for child in node.children):
                    continue
                source = _literal(document, node.child_by_field_name("source"))
                if source not in {"@babel/core", "@vitejs/plugin-react", "node:module", "module"}:
                    continue
                clause = next((child for child in node.named_children if child.type == "import_clause"), None)
                if clause is None:
                    continue
                for child in clause.named_children:
                    if child.type == "identifier":
                        imports.append(ImportBinding(source, "default", document.text(child), document.line(child)))
                    elif child.type == "namespace_import":
                        name = next((part for part in child.named_children if part.type == "identifier"), None)
                        imports.append(ImportBinding(source, "*", document.text(name), document.line(child)))
                    elif child.type == "named_imports":
                        for item in child.named_children:
                            if item.type != "import_specifier" or any(child.type == "type" for child in item.children):
                                continue
                            name = item.child_by_field_name("name")
                            alias = item.child_by_field_name("alias") or name
                            imports.append(ImportBinding(source, document.text(name), document.text(alias), document.line(item)))
            return PluginOutcome.handled(_configured_module_facts(document, artifact, imports))
        except Exception as exception:
            return PluginOutcome.failed(PluginDiagnostic(
                "babel-config-parse-failed", f"{type(exception).__name__}: {exception}",
                self.descriptor.id, path=artifact.path, recoverable=True))


def create_plugin(descriptor: PluginDescriptor) -> BabelPlugin:
    return BabelPlugin(descriptor)
