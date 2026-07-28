from __future__ import annotations

import base64
import gzip
import json
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from codecrow_plugins import (
    ArchitecturePacket,
    FileArtifact,
    GraphFact,
    PluginOutcome,
    RepositoryAnalysis,
    RepositorySnapshot,
    TreeSitterDocument,
)


JAVASCRIPT_EXTENSIONS = (".cjs", ".js", ".jsx", ".mjs")
_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_CUSTOM_JSX_NAME = re.compile(r"^[A-Z][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*$")
_IMPORT_FROM = re.compile(
    r"^\s*import\s+(?P<clause>.*?)\s+from\s+(?P<source>['\"][^'\"]+['\"])",
    re.DOTALL,
)
_PROP_TYPES = re.compile(
    r"^(?P<component>[A-Za-z_$][A-Za-z0-9_$]*)\.propTypes$"
)
_DEFAULT_PROPS = re.compile(
    r"^(?P<component>[A-Za-z_$][A-Za-z0-9_$]*)\.defaultProps$"
)


@dataclass(frozen=True, order=True)
class ImportBinding:
    local: str
    imported: str
    module: str
    line: int


@dataclass(frozen=True, order=True)
class ComponentRecord:
    name: str
    line: int
    accepted_props: tuple[str, ...] = ()
    required_props: tuple[str, ...] = ()
    open_props: bool = False
    default_export: bool = False
    export_names: tuple[str, ...] = ()


@dataclass(frozen=True, order=True)
class JsxUsage:
    owner: str
    component: str
    line: int
    props: tuple[str, ...] = ()
    has_spread: bool = False


@dataclass(frozen=True, order=True)
class JavascriptFileRecord:
    path: str
    imports: tuple[ImportBinding, ...] = ()
    components: tuple[ComponentRecord, ...] = ()
    usages: tuple[JsxUsage, ...] = ()


def _named(node, field: str):
    return node.child_by_field_name(field)


def _object_pattern_props(document: TreeSitterDocument, node) -> tuple[set[str], bool]:
    props: set[str] = set()
    has_rest = False
    for child in node.named_children:
        if child.type in {
            "shorthand_property_identifier_pattern",
            "property_identifier",
        }:
            value = document.text(child)
            if value:
                props.add(value)
        elif child.type == "pair_pattern":
            key = next(
                (
                    candidate
                    for candidate in child.named_children
                    if candidate.type in {
                        "property_identifier",
                        "string",
                        "number",
                    }
                ),
                None,
            )
            value = document.text(key).strip("'\"") if key is not None else ""
            if value:
                props.add(value)
        elif child.type == "object_assignment_pattern":
            key = next(
                (
                    candidate
                    for candidate in child.named_children
                    if candidate.type in {
                        "shorthand_property_identifier_pattern",
                        "property_identifier",
                        "identifier",
                    }
                ),
                None,
            )
            value = document.text(key) if key is not None else ""
            if value:
                props.add(value)
        elif child.type == "rest_pattern":
            has_rest = True
    return props, has_rest


def _parameter_contract(
    document: TreeSitterDocument,
    callable_node,
) -> tuple[set[str], bool, str]:
    parameters = _named(callable_node, "parameters")
    if parameters is None:
        return set(), False, ""
    first = next(iter(parameters.named_children), None)
    if first is None:
        return set(), False, ""
    if first.type == "object_pattern":
        props, has_rest = _object_pattern_props(document, first)
        return props, has_rest, ""
    if first.type in {"identifier", "required_parameter", "optional_parameter"}:
        identifier = document.text(first).strip()
        if first.type != "identifier":
            candidate = next(
                (
                    child for child in first.named_children
                    if child.type == "identifier"
                ),
                None,
            )
            identifier = document.text(candidate).strip()
        return set(), True, identifier
    return set(), True, ""


def _member_prop(document: TreeSitterDocument, node, parameter: str) -> str:
    if node.type != "member_expression":
        return ""
    object_node = _named(node, "object")
    property_node = _named(node, "property")
    if object_node is None or property_node is None:
        return ""
    object_text = document.text(object_node)
    if parameter and object_text == parameter:
        return document.text(property_node)
    if object_text == "this.props":
        return document.text(property_node)
    return ""


def _component_owner(document: TreeSitterDocument, node, path: str) -> str:
    current = node.parent
    while current is not None:
        if current.type in {"function_declaration", "generator_function_declaration"}:
            name = document.text(_named(current, "name"))
            return name or path
        if current.type == "arrow_function":
            parent = current.parent
            if parent is not None and parent.type == "variable_declarator":
                name = document.text(_named(parent, "name"))
                return name or path
        if current.type == "method_definition":
            method = document.text(_named(current, "name"))
            class_node = current.parent
            while class_node is not None and class_node.type != "class_declaration":
                class_node = class_node.parent
            owner = (
                document.text(_named(class_node, "name"))
                if class_node is not None
                else path
            )
            return f"{owner}::{method}" if method else owner
        current = current.parent
    return path


def _import_bindings(
    document: TreeSitterDocument,
    node,
) -> tuple[ImportBinding, ...]:
    statement = document.text(node)
    match = _IMPORT_FROM.match(statement)
    if match is None:
        return ()
    clause = match.group("clause").strip()
    module = match.group("source").strip("'\"")
    line = document.line(node)
    bindings: set[ImportBinding] = set()

    default_clause = clause
    remainder = ""
    if "," in clause:
        default_clause, remainder = clause.split(",", 1)
    elif clause.startswith(("{", "*")):
        default_clause, remainder = "", clause
    if _IDENTIFIER.fullmatch(default_clause.strip()):
        bindings.add(ImportBinding(default_clause.strip(), "default", module, line))

    named_clause = remainder.strip()
    if named_clause.startswith("*"):
        namespace = re.search(
            r"\*\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*)",
            named_clause,
        )
        if namespace:
            bindings.add(
                ImportBinding(namespace.group(1), "*", module, line)
            )
    elif named_clause.startswith("{") and "}" in named_clause:
        for item in named_clause[1:named_clause.rfind("}")].split(","):
            item = item.strip()
            if not item:
                continue
            parts = re.split(r"\s+as\s+", item)
            imported = parts[0].strip()
            local = parts[-1].strip()
            if _IDENTIFIER.fullmatch(imported) and _IDENTIFIER.fullmatch(local):
                bindings.add(ImportBinding(local, imported, module, line))
    return tuple(sorted(bindings))


def _object_keys(document: TreeSitterDocument, node) -> set[str]:
    if node is None or node.type != "object":
        return set()
    keys: set[str] = set()
    for pair in (child for child in node.named_children if child.type == "pair"):
        key = _named(pair, "key")
        value = document.text(key).strip("'\"") if key is not None else ""
        if value:
            keys.add(value)
    return keys


def _prop_type_contracts(
    document: TreeSitterDocument,
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    declared: dict[str, set[str]] = {}
    required: dict[str, set[str]] = {}
    defaults: dict[str, set[str]] = {}
    for node in document.walk():
        if node.type != "assignment_expression":
            continue
        left = _named(node, "left")
        right = _named(node, "right")
        left_text = document.text(left)
        prop_types = _PROP_TYPES.fullmatch(left_text)
        default_props = _DEFAULT_PROPS.fullmatch(left_text)
        if prop_types is not None:
            component = prop_types.group("component")
            declared.setdefault(component, set()).update(
                _object_keys(document, right)
            )
            if right is not None and right.type == "object":
                for pair in (
                    child for child in right.named_children
                    if child.type == "pair"
                ):
                    key = _named(pair, "key")
                    value = _named(pair, "value")
                    prop = document.text(key).strip("'\"")
                    if (
                        prop
                        and value is not None
                        and document.text(value).rstrip().endswith(".isRequired")
                    ):
                        required.setdefault(component, set()).add(prop)
        elif default_props is not None:
            defaults.setdefault(
                default_props.group("component"),
                set(),
            ).update(_object_keys(document, right))
    return declared, required, defaults


def _known_wrapper_argument(document: TreeSitterDocument, call) -> str:
    function = _named(call, "function")
    function_name = (
        document.text(function).rsplit(".", 1)[-1]
        if function is not None
        else ""
    )
    if function_name not in {"memo", "forwardRef"}:
        return ""
    arguments = next(
        (
            child for child in call.named_children
            if child.type == "arguments"
        ),
        None,
    )
    if arguments is None or len(arguments.named_children) != 1:
        return ""
    argument = arguments.named_children[0]
    return document.text(argument) if argument.type == "identifier" else ""


def _export_statement_bindings(
    document: TreeSitterDocument,
    node,
) -> tuple[tuple[str, str], ...]:
    """Return exact (exported name, local name) pairs.

    The local name is blank when export presence is exact but its local
    component target cannot be resolved without interpreting dynamic code.
    """
    statement = document.text(node).lstrip()
    source = _named(node, "source")
    declaration = next(
        (
            child for child in node.named_children
            if child.type in {
                "class_declaration",
                "function_declaration",
                "generator_function_declaration",
                "lexical_declaration",
                "variable_declaration",
            }
        ),
        None,
    )
    if statement.startswith("export default"):
        if declaration is not None:
            name = document.text(_named(declaration, "name"))
            return (("default", name),) if name else (("default", ""),)
        identifier = next(
            (
                child for child in node.named_children
                if child.type == "identifier"
            ),
            None,
        )
        if identifier is not None:
            return (("default", document.text(identifier)),)
        call = next(
            (
                child for child in node.named_children
                if child.type == "call_expression"
            ),
            None,
        )
        local = _known_wrapper_argument(document, call) if call is not None else ""
        return (("default", local),)

    if declaration is not None:
        if declaration.type in {
            "class_declaration",
            "function_declaration",
            "generator_function_declaration",
        }:
            name = document.text(_named(declaration, "name"))
            return ((name, name),) if name else ()
        bindings: set[tuple[str, str]] = set()
        for declarator in (
            child for child in declaration.named_children
            if child.type == "variable_declarator"
        ):
            name = document.text(_named(declarator, "name"))
            if _IDENTIFIER.fullmatch(name):
                bindings.add((name, name))
        return tuple(sorted(bindings))

    clause = next(
        (
            child for child in node.named_children
            if child.type == "export_clause"
        ),
        None,
    )
    if clause is None:
        return ()
    bindings: set[tuple[str, str]] = set()
    for specifier in (
        child for child in clause.named_children
        if child.type == "export_specifier"
    ):
        name_node = _named(specifier, "name")
        alias_node = _named(specifier, "alias")
        local = document.text(name_node)
        exported = document.text(alias_node) if alias_node is not None else local
        if _IDENTIFIER.fullmatch(exported):
            bindings.add((exported, "" if source is not None else local))
    return tuple(sorted(bindings))


def _component_exports(
    document: TreeSitterDocument,
) -> tuple[str, dict[str, set[str]]]:
    default_export = ""
    named_exports: dict[str, set[str]] = {}
    for node in document.root.named_children:
        if node.type != "export_statement":
            continue
        for exported, local in _export_statement_bindings(document, node):
            if not local:
                continue
            if exported == "default":
                default_export = local
            else:
                named_exports.setdefault(local, set()).add(exported)
    return default_export, named_exports


def _component_callable(document: TreeSitterDocument, value):
    if value is None:
        return None
    if value.type in {
        "arrow_function",
        "function_expression",
        "generator_function",
    }:
        return value
    if value.type != "call_expression":
        return None
    function = _named(value, "function")
    function_name = ""
    if function is not None:
        function_name = document.text(function).rsplit(".", 1)[-1]
    if function_name not in {"memo", "forwardRef"}:
        return None
    arguments = next(
        (
            child for child in value.named_children
            if child.type == "arguments"
        ),
        None,
    )
    if arguments is None:
        return None
    return next(
        (
            child for child in arguments.named_children
            if child.type in {
                "arrow_function",
                "function_expression",
                "generator_function",
            }
        ),
        None,
    )


def analyze_javascript_artifact(
    artifact: FileArtifact,
) -> tuple[tuple[GraphFact, ...], JavascriptFileRecord]:
    document = TreeSitterDocument.parse(
        artifact.content,
        "tree_sitter_javascript",
        "language",
    )
    module = artifact.path
    facts: set[GraphFact] = set()
    imports: set[ImportBinding] = set()
    components: dict[str, tuple[int, set[str], set[str], bool]] = {}
    usages: set[JsxUsage] = set()
    declared_prop_types, required_prop_types, default_props = (
        _prop_type_contracts(document)
    )
    default_export, named_exports = _component_exports(document)

    for node in document.walk():
        if node.type == "import_statement":
            bindings = _import_bindings(document, node)
            imports.update(bindings)
            source = _named(node, "source")
            target = document.text(source).strip("'\"") if source is not None else ""
            if target:
                facts.add(GraphFact(
                    "javascript-import",
                    module,
                    "imports",
                    target,
                    artifact.path,
                    document.line(node),
                ))
            for binding in bindings:
                facts.add(GraphFact(
                    "javascript-import-binding",
                    binding.local,
                    "imports",
                    f"{binding.module}::{binding.imported}",
                    artifact.path,
                    binding.line,
                ))
        elif node.type == "class_declaration":
            name = document.text(_named(node, "name"))
            if name:
                facts.add(GraphFact(
                    "javascript-type",
                    module,
                    "declares",
                    name,
                    artifact.path,
                    document.line(node),
                    (("type", "class"),),
                ))
                heritage = next(
                    (
                        child for child in node.named_children
                        if child.type == "class_heritage"
                    ),
                    None,
                )
                heritage_text = document.text(heritage)
                if heritage is not None:
                    parents = document.descendants(
                        heritage,
                        "identifier",
                        "member_expression",
                    )
                    if parents:
                        facts.add(GraphFact(
                            "javascript-inheritance",
                            name,
                            "extends",
                            document.text(parents[0]),
                            artifact.path,
                            document.line(heritage),
                        ))
                body = _named(node, "body")
                if body is not None:
                    for method in (
                        child for child in body.named_children
                        if child.type == "method_definition"
                    ):
                        method_name = document.text(_named(method, "name"))
                        if method_name:
                            facts.add(GraphFact(
                                "javascript-callable",
                                name,
                                "declares-method",
                                method_name,
                                artifact.path,
                                document.line(method),
                            ))
                if name[:1].isupper() and (
                    "Component" in heritage_text
                    or document.descendants(node, "jsx_element", "jsx_self_closing_element")
                ):
                    props = {
                        _member_prop(document, candidate, "")
                        for candidate in document.descendants(
                            node,
                            "member_expression",
                        )
                    }
                    props.discard("")
                    props.update(declared_prop_types.get(name, ()))
                    required = (
                        required_prop_types.get(name, set())
                        - default_props.get(name, set())
                    )
                    components[name] = (
                        document.line(node),
                        props,
                        set(required),
                        True,
                    )
        elif node.type in {
            "function_declaration",
            "generator_function_declaration",
        }:
            name = document.text(_named(node, "name"))
            if name:
                facts.add(GraphFact(
                    "javascript-callable",
                    module,
                    "declares-function",
                    name,
                    artifact.path,
                    document.line(node),
                ))
                if name[:1].isupper() and document.descendants(
                    node,
                    "jsx_element",
                    "jsx_self_closing_element",
                ):
                    props, open_props, parameter = _parameter_contract(
                        document,
                        node,
                    )
                    if parameter:
                        props.update(
                            filter(
                                None,
                                (
                                    _member_prop(document, candidate, parameter)
                                    for candidate in document.descendants(
                                        node,
                                        "member_expression",
                                    )
                                ),
                            )
                        )
                    props.update(declared_prop_types.get(name, ()))
                    required = (
                        required_prop_types.get(name, set())
                        - default_props.get(name, set())
                    )
                    components[name] = (
                        document.line(node),
                        props,
                        set(required),
                        open_props,
                    )
        elif node.type == "variable_declarator":
            name_node = _named(node, "name")
            value = _named(node, "value")
            callable_value = _component_callable(document, value)
            name = document.text(name_node)
            if (
                callable_value is not None
                and name[:1].isupper()
                and document.descendants(
                    callable_value,
                    "jsx_element",
                    "jsx_self_closing_element",
                )
            ):
                props, open_props, parameter = _parameter_contract(
                    document,
                    callable_value,
                )
                if parameter:
                    props.update(
                        filter(
                            None,
                            (
                                _member_prop(document, candidate, parameter)
                                for candidate in document.descendants(
                                    callable_value,
                                    "member_expression",
                                )
                            ),
                        )
                    )
                props.update(declared_prop_types.get(name, ()))
                required = (
                    required_prop_types.get(name, set())
                    - default_props.get(name, set())
                )
                components[name] = (
                    document.line(node),
                    props,
                    set(required),
                    open_props,
                )
        elif node.type == "call_expression":
            function = _named(node, "function")
            target = document.text(function)
            if target:
                facts.add(GraphFact(
                    "javascript-call",
                    module,
                    "calls",
                    target,
                    artifact.path,
                    document.line(node),
                ))
        elif node.type == "new_expression":
            constructor = _named(node, "constructor")
            target = document.text(constructor)
            if target:
                facts.add(GraphFact(
                    "javascript-construction",
                    module,
                    "constructs",
                    target,
                    artifact.path,
                    document.line(node),
                ))
        elif node.type == "export_statement":
            for exported, _ in _export_statement_bindings(
                document,
                node,
            ):
                facts.add(GraphFact(
                    "javascript-export",
                    module,
                    "exports",
                    exported,
                    artifact.path,
                    document.line(node),
                ))
        elif node.type in {"jsx_opening_element", "jsx_self_closing_element"}:
            component = document.text(_named(node, "name"))
            if not _CUSTOM_JSX_NAME.fullmatch(component):
                continue
            props: set[str] = set()
            has_spread = False
            for child in node.named_children:
                if child.type == "jsx_attribute":
                    name_node = next(iter(child.named_children), None)
                    prop = document.text(name_node)
                    if prop:
                        props.add(prop)
                elif child.type == "jsx_expression" and any(
                    candidate.type == "spread_element"
                    for candidate in child.named_children
                ):
                    has_spread = True
            owner = _component_owner(document, node, artifact.path)
            usages.add(JsxUsage(
                owner,
                component,
                document.line(node),
                tuple(sorted(props)),
                has_spread,
            ))
            facts.add(GraphFact(
                "javascript-jsx-render",
                owner,
                "renders",
                component,
                artifact.path,
                document.line(node),
            ))
            for prop in sorted(props):
                facts.add(GraphFact(
                    "javascript-jsx-prop",
                    component,
                    "receives-prop",
                    prop,
                    artifact.path,
                    document.line(node),
                ))

    component_records = tuple(sorted(
        ComponentRecord(
            name=name,
            line=line,
            accepted_props=tuple(sorted(props)),
            required_props=tuple(sorted(required)),
            open_props=open_props,
            default_export=name == default_export,
            export_names=tuple(sorted(named_exports.get(name, ()))),
        )
        for name, (line, props, required, open_props) in components.items()
    ))
    for component in component_records:
        for prop in component.accepted_props:
            facts.add(GraphFact(
                "javascript-component-prop",
                component.name,
                (
                    "requires-prop"
                    if prop in component.required_props
                    else "accepts-prop"
                ),
                prop,
                artifact.path,
                component.line,
            ))
    return (
        tuple(sorted(facts)),
        JavascriptFileRecord(
            path=artifact.path,
            imports=tuple(sorted(imports)),
            components=component_records,
            usages=tuple(sorted(usages)),
        ),
    )


def _record_to_json(record: JavascriptFileRecord) -> dict[str, object]:
    return {
        "path": record.path,
        "imports": [
            {
                "local": item.local,
                "imported": item.imported,
                "module": item.module,
                "line": item.line,
            }
            for item in record.imports
        ],
        "components": [
            {
                "name": item.name,
                "line": item.line,
                "acceptedProps": list(item.accepted_props),
                "requiredProps": list(item.required_props),
                "openProps": item.open_props,
                "defaultExport": item.default_export,
                "exportNames": list(item.export_names),
            }
            for item in record.components
        ],
        "usages": [
            {
                "owner": item.owner,
                "component": item.component,
                "line": item.line,
                "props": list(item.props),
                "hasSpread": item.has_spread,
            }
            for item in record.usages
        ],
    }


def _record_from_json(raw: dict[str, object]) -> JavascriptFileRecord:
    return JavascriptFileRecord(
        path=str(raw["path"]),
        imports=tuple(sorted(
            ImportBinding(
                str(item["local"]),
                str(item["imported"]),
                str(item["module"]),
                int(item["line"]),
            )
            for item in raw.get("imports", [])
        )),
        components=tuple(sorted(
            ComponentRecord(
                str(item["name"]),
                int(item["line"]),
                tuple(sorted(str(value) for value in item.get("acceptedProps", []))),
                tuple(sorted(str(value) for value in item.get("requiredProps", []))),
                bool(item.get("openProps", False)),
                bool(item.get("defaultExport", False)),
                tuple(sorted(str(value) for value in item.get("exportNames", []))),
            )
            for item in raw.get("components", [])
        )),
        usages=tuple(sorted(
            JsxUsage(
                str(item["owner"]),
                str(item["component"]),
                int(item["line"]),
                tuple(sorted(str(value) for value in item.get("props", []))),
                bool(item.get("hasSpread", False)),
            )
            for item in raw.get("usages", [])
        )),
    )


def _resolve_module(
    source_path: str,
    module: str,
    records: dict[str, JavascriptFileRecord],
) -> str:
    if not module.startswith("."):
        return ""
    joined = posixpath.normpath(
        posixpath.join(str(PurePosixPath(source_path).parent), module)
    )
    if joined == ".." or joined.startswith("../") or joined.startswith("/"):
        return ""
    candidates = [joined]
    if not PurePosixPath(joined).suffix:
        candidates.extend(f"{joined}{suffix}" for suffix in JAVASCRIPT_EXTENSIONS)
        candidates.extend(
            f"{joined}/index{suffix}" for suffix in JAVASCRIPT_EXTENSIONS
        )
    matches = [candidate for candidate in candidates if candidate in records]
    return matches[0] if len(matches) == 1 else ""


@dataclass
class JavascriptRepositorySession:
    plugin_id: str
    revision: str
    _records: dict[str, JavascriptFileRecord] = field(default_factory=dict)

    @classmethod
    def restore(
        cls,
        plugin_id: str,
        revision: str,
        snapshots,
    ) -> "JavascriptRepositorySession":
        snapshot = next(
            (
                item for item in snapshots
                if item.kind == "javascript-components"
            ),
            None,
        )
        if snapshot is None:
            raise ValueError(
                "JavaScript repository snapshot is missing javascript-components"
            )
        raw = gzip.decompress(
            base64.b64decode(snapshot.content.encode("ascii"))
        )
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, list):
            raise ValueError("JavaScript repository snapshot must be a list")
        records = {
            record.path: record
            for item in payload
            if isinstance(item, dict)
            for record in (_record_from_json(item),)
        }
        return cls(plugin_id, revision, records)

    def ingest(self, artifacts: tuple[FileArtifact, ...]) -> None:
        selected = tuple(
            artifact
            for artifact in artifacts
            if artifact.path.casefold().endswith(JAVASCRIPT_EXTENSIONS)
        )
        for artifact in selected:
            self._records.pop(artifact.path, None)
            if artifact.deleted:
                continue
            _, record = analyze_javascript_artifact(artifact)
            self._records[artifact.path] = record

    def _snapshot(self) -> RepositorySnapshot:
        raw = json.dumps(
            [
                _record_to_json(record)
                for record in sorted(self._records.values())
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return RepositorySnapshot(
            self.plugin_id,
            "javascript-components",
            base64.b64encode(
                gzip.compress(raw, compresslevel=6, mtime=0)
            ).decode("ascii"),
        )

    def _packets(self) -> tuple[ArchitecturePacket, ...]:
        packets: list[ArchitecturePacket] = []
        for source_path, record in sorted(self._records.items()):
            imports = {item.local: item for item in record.imports}
            local_components = {
                item.name: item for item in record.components
            }
            facts: set[GraphFact] = set()
            related_paths: set[str] = {source_path}
            for usage in record.usages:
                component_name = usage.component.rsplit(".", 1)[-1]
                target_path = source_path
                target_component = (
                    None
                    if "." in usage.component
                    else local_components.get(component_name)
                )
                binding_name = usage.component.split(".", 1)[0]
                binding = imports.get(binding_name)
                if binding is not None:
                    target_path = _resolve_module(
                        source_path,
                        binding.module,
                        self._records,
                    )
                    target_record = self._records.get(target_path)
                    if target_record is None:
                        continue
                    expected_name = (
                        component_name
                        if binding.imported == "*"
                        else binding.imported
                    )
                    if expected_name == "default":
                        candidates = [
                            item for item in target_record.components
                            if item.default_export
                        ]
                    else:
                        candidates = [
                            item for item in target_record.components
                            if expected_name in item.export_names
                        ]
                    if len(candidates) != 1:
                        continue
                    target_component = candidates[0]
                if target_component is None:
                    continue
                related_paths.add(target_path)
                source_identity = (
                    f"{source_path}::{usage.owner}::{usage.component}"
                )
                target_identity = (
                    f"{target_path}::{target_component.name}"
                )
                fact_related_paths = (
                    (target_path,) if target_path != source_path else ()
                )
                facts.add(GraphFact(
                    "javascript-component-resolution",
                    source_identity,
                    "resolves-to",
                    target_identity,
                    source_path,
                    usage.line,
                    related_paths=fact_related_paths,
                ))
                accepted = set(target_component.accepted_props)
                required = set(target_component.required_props)
                supplied = set(usage.props)
                for prop in sorted(supplied & accepted):
                    facts.add(GraphFact(
                        "javascript-jsx-prop-contract",
                        source_identity,
                        (
                            "passes-required-prop"
                            if prop in required
                            else "passes-declared-prop"
                        ),
                        f"{target_identity}::{prop}",
                        source_path,
                        usage.line,
                        related_paths=fact_related_paths,
                    ))
                if not usage.has_spread:
                    for prop in sorted(required - supplied):
                        facts.add(GraphFact(
                            "javascript-jsx-required-prop-missing",
                            source_identity,
                            "omits-required-prop",
                            f"{target_identity}::{prop}",
                            source_path,
                            usage.line,
                            related_paths=fact_related_paths,
                        ))
            if facts:
                packets.append(ArchitecturePacket(
                    plugin_id=self.plugin_id,
                    kind="javascript-component-relation",
                    key=source_path,
                    paths=tuple(sorted(related_paths)),
                    facts=tuple(sorted(facts)),
                    attributes=(("resolution", "exact-relative-import"),),
                ))
        return tuple(sorted(packets))

    def finish(self, dependencies: RepositoryAnalysis):
        return PluginOutcome.handled(RepositoryAnalysis(
            packets=self._packets(),
            snapshots=(self._snapshot(),),
        ))
