from __future__ import annotations

import json
import re
import threading

from codecrow_plugins import FileArtifact, GraphFact, SymbolDefinition

from .declarations import PhpDeclarationExtractor
from .receivers import PhpReceiverResolver
from .references import PhpReferenceExtractor
from .syntax import (
    DECLARATIONS,
    TEMPLATE_ASSIGNMENTS,
    TEMPLATE_CALLABLE_SCOPES,
    _TEMPLATE_INSTANCE_CALL_REFERENCE,
    _modifiers,
    _nearest,
    _text,
    _walk,
)


_THREAD_LOCAL = threading.local()


def _thread_parser() -> "PhpAstParser":
    parser = getattr(_THREAD_LOCAL, "php_parser", None)
    if parser is None:
        parser = PhpAstParser()
        _THREAD_LOCAL.php_parser = parser
    return parser

def _parse_artifact(artifact: FileArtifact) -> tuple[SymbolDefinition, ...]:
    return _thread_parser().parse(artifact)

def _parse_template_artifact(
    artifact: FileArtifact,
) -> tuple[SymbolDefinition, ...]:
    template = _thread_parser().parse_template(artifact)
    return (template,) if template is not None else ()

def php_file_use_facts(artifact: FileArtifact) -> tuple[GraphFact, ...]:
    """Extract namespace imports and declaration-owned trait composition."""
    return _thread_parser().file_use_facts(artifact)


class PhpAstParser:
    """Parse PHP source and coordinate syntax-owned semantic extractors."""

    def __init__(self) -> None:
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_php
        except ImportError as exception:
            raise RuntimeError("PHP repository analysis requires tree-sitter-php") from exception
        self._parser = Parser(Language(tree_sitter_php.language_php()))
        self.declarations = PhpDeclarationExtractor()
        self.references = PhpReferenceExtractor(PhpReceiverResolver())

    def parse(self, artifact: FileArtifact) -> tuple[SymbolDefinition, ...]:
        source = artifact.content.encode("utf-8")
        tree = self._parser.parse(source)
        root = tree.root_node
        symbols: list[SymbolDefinition] = []
        for node in _walk(root):
            kind = DECLARATIONS.get(node.type)
            if kind is None:
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            namespace = self.namespace_for(node, source)
            imports = self.imports_for(node, source)
            name = _text(name_node, source).strip()
            qualified_name = f"{namespace}\\{name}" if namespace else name
            parents, parent_attributes = self.declarations.parents(
                node,
                source,
                namespace,
                imports,
                kind,
            )
            methods, constructor_types, method_attributes = self.declarations.methods(
                node,
                source,
                namespace,
                imports,
            )
            trait_attributes = self.declarations.traits(
                node,
                source,
                namespace,
                imports,
            )
            reference_attributes = self.references.code_references(
                node,
                source,
                namespace,
                imports,
                qualified_name,
                next(
                    (
                        value
                        for key, value in parent_attributes
                        if key == "php-parent-class"
                    ),
                    "",
                ),
            )
            constant_attributes = self.declarations.class_constants(
                node,
                source,
                namespace,
                imports,
                qualified_name,
                next(
                    (
                        value
                        for key, value in parent_attributes
                        if key == "php-parent-class"
                    ),
                    "",
                ),
            )
            type_attributes = {
                (f"type:{modifier}", "true")
                for modifier in _modifiers(node, source)
            }
            type_attributes.update(parent_attributes)
            type_attributes.update(trait_attributes)
            type_attributes.update(reference_attributes)
            type_attributes.update(constant_attributes)
            symbols.append(SymbolDefinition(
                qualified_name=qualified_name.lstrip("\\"),
                kind=kind,
                path=artifact.path,
                line=node.start_point[0] + 1,
                parents=parents,
                methods=methods,
                constructor_types=constructor_types,
                attributes=tuple(sorted(type_attributes | method_attributes)),
            ))
        return tuple(sorted(symbols))

    def file_use_facts(self, artifact: FileArtifact) -> tuple[GraphFact, ...]:
        """Return syntax-proven facts for PHP's two distinct ``use`` forms."""
        source = artifact.content.encode("utf-8")
        root = self._parser.parse(source).root_node
        facts: set[GraphFact] = set()

        for node in _walk(root):
            if node.type == "namespace_use_declaration":
                namespace = self.namespace_for(node, source)
                owner = namespace or artifact.path
                for line_number, target in self.namespace_imports(node, source):
                    facts.add(GraphFact(
                        "php-import",
                        owner,
                        "imports",
                        target,
                        artifact.path,
                        line_number,
                    ))
                continue

            if node.type != "use_declaration":
                continue
            declaration = _nearest(node, set(DECLARATIONS))
            if declaration is None:
                continue
            name_node = declaration.child_by_field_name("name")
            if name_node is None:
                continue
            namespace = self.namespace_for(declaration, source)
            name = _text(name_node, source).strip()
            owner = f"{namespace}\\{name}" if namespace else name
            for child in node.named_children:
                if child.type not in {"name", "qualified_name"}:
                    continue
                target = _text(child, source).strip()
                if target:
                    facts.add(GraphFact(
                        "php-trait",
                        owner,
                        "uses-trait",
                        target,
                        artifact.path,
                        child.start_point[0] + 1,
                    ))

        return tuple(sorted(facts))

    @staticmethod
    def namespace_imports(
        declaration,
        source: bytes,
    ) -> tuple[tuple[int, str], ...]:
        group = next(
            (
                child
                for child in declaration.named_children
                if child.type == "namespace_use_group"
            ),
            None,
        )
        prefix_node = next(
            (
                child
                for child in declaration.named_children
                if child.type == "namespace_name"
            ),
            None,
        )
        prefix = (
            _text(prefix_node, source).strip().rstrip("\\")
            if group is not None and prefix_node is not None
            else ""
        )
        scope = group if group is not None else declaration
        imports: list[tuple[int, str]] = []
        for clause in _walk(scope):
            if clause.type != "namespace_use_clause":
                continue
            value = _text(clause, source).strip()
            value = re.sub(
                r"^(?:function|const)\s+",
                "",
                value,
                count=1,
                flags=re.IGNORECASE,
            )
            target = re.split(
                r"\s+as\s+",
                value,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip()
            if prefix and target:
                target = prefix + "\\" + target.lstrip("\\")
            if target:
                imports.append((clause.start_point[0] + 1, target))
        return tuple(imports)

    def parse_template(
        self,
        artifact: FileArtifact,
    ) -> SymbolDefinition | None:
        """Retain exact direct variable calls from one PHP template.

        Magento owns the meaning of conventional receivers such as ``$block``;
        the PHP plugin only publishes syntax-proven receiver, method, literal
        argument, path, and line metadata. Calls in comments/HTML/strings and
        dynamic receiver or method expressions are deliberately absent.
        """

        source = artifact.content.encode("utf-8")
        tree = self._parser.parse(source)
        root = tree.root_node
        if root.has_error:
            return None

        declaration_types = set(DECLARATIONS)
        excluded_scopes = declaration_types | TEMPLATE_CALLABLE_SCOPES
        reassigned_at: dict[str, int] = {}
        uncertain_reassignment_at: int | None = None
        for node in _walk(root):
            if node.type not in TEMPLATE_ASSIGNMENTS:
                continue
            if _nearest(node, excluded_scopes) is not None:
                continue
            left = node.child_by_field_name("left")
            if left is None:
                uncertain_reassignment_at = min(
                    uncertain_reassignment_at or node.end_byte,
                    node.end_byte,
                )
                continue
            if left.type == "variable_name":
                receiver_name = _text(left, source).strip().lstrip("$")
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", receiver_name):
                    reassigned_at[receiver_name] = min(
                        reassigned_at.get(receiver_name, node.end_byte),
                        node.end_byte,
                    )
                else:
                    uncertain_reassignment_at = min(
                        uncertain_reassignment_at or node.end_byte,
                        node.end_byte,
                    )
                continue
            # Mutating an element or property does not replace the receiver.
            # Other assignment targets (for example dynamic variables or
            # destructuring) cannot be attributed safely, so later calls
            # abstain rather than guessing which template binding survived.
            if left.type not in {
                "member_access_expression",
                "nullsafe_member_access_expression",
                "scoped_property_access_expression",
                "subscript_expression",
            }:
                uncertain_reassignment_at = min(
                    uncertain_reassignment_at or node.end_byte,
                    node.end_byte,
                )

        references: set[tuple[int, str, str, tuple[tuple[int, str], ...]]] = set()
        literal_pattern = re.compile(
            r"(?P<quote>['\"])(?P<value>[A-Za-z0-9_.:/-]{1,256})(?P=quote)"
        )
        for node in _walk(root):
            if node.type not in {
                "member_call_expression",
                "nullsafe_member_call_expression",
            }:
                continue
            # Declarations have their normal symbols. Named functions,
            # closures, and arrow functions have their own runtime scope, so
            # none of their receiver calls belong to top-level PHTML context.
            if _nearest(node, excluded_scopes) is not None:
                continue
            receiver = node.child_by_field_name("object")
            method_node = node.child_by_field_name("name")
            if (
                receiver is None
                or receiver.type != "variable_name"
                or method_node is None
                or method_node.type not in {"name", "identifier"}
            ):
                continue
            receiver_name = _text(receiver, source).strip().lstrip("$")
            method = _text(method_node, source).strip()
            if (
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", receiver_name)
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", method)
            ):
                continue
            if (
                node.start_byte >= reassigned_at.get(receiver_name, node.end_byte + 1)
                or (
                    uncertain_reassignment_at is not None
                    and node.start_byte >= uncertain_reassignment_at
                )
            ):
                continue
            arguments = node.child_by_field_name("arguments") or next(
                (
                    child
                    for child in node.named_children
                    if child.type == "arguments"
                ),
                None,
            )
            literal_arguments: list[tuple[int, str]] = []
            if arguments is not None:
                for position, argument in enumerate(arguments.named_children):
                    value_node = argument
                    if (
                        argument.type == "argument"
                        and len(argument.named_children) == 1
                    ):
                        value_node = argument.named_children[0]
                    match = literal_pattern.fullmatch(
                        _text(value_node, source).strip()
                    )
                    if match is not None:
                        literal_arguments.append((position, match.group("value")))
            references.add((
                node.start_point[0] + 1,
                receiver_name,
                method,
                tuple(literal_arguments),
            ))
        if not references:
            return None
        attributes = tuple(
            (
                f"{_TEMPLATE_INSTANCE_CALL_REFERENCE}{index:04d}",
                json.dumps(
                    {
                        "line": line_number,
                        "literalStringArguments": {
                            str(position): value
                            for position, value in literal_arguments
                        },
                        "method": method,
                        "receiver": receiver,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            for index, (
                line_number,
                receiver,
                method,
                literal_arguments,
            ) in enumerate(sorted(references))
        )
        return SymbolDefinition(
            qualified_name=f"template:{artifact.path}",
            kind="template",
            path=artifact.path,
            line=min(reference[0] for reference in references),
            attributes=attributes,
        )

    def namespace_for(self, declaration, source: bytes) -> str:
        namespace_node = _nearest(declaration, {"namespace_definition"})
        candidates = namespace_node.children if namespace_node is not None else declaration.parent.children
        for node in candidates:
            if node.type != "namespace_definition":
                continue
            if namespace_node is not None and node != namespace_node:
                continue
            name = next((child for child in node.children if child.type == "namespace_name"), None)
            return _text(name, source).strip() if name is not None else ""
        # Unbracketed namespaces are siblings of declarations.
        root = declaration
        while root.parent is not None:
            root = root.parent
        namespace = ""
        for node in root.children:
            if node.start_byte >= declaration.start_byte:
                break
            if node.type == "namespace_definition":
                name = next((child for child in node.children if child.type == "namespace_name"), None)
                namespace = _text(name, source).strip() if name is not None else ""
        return namespace

    def imports_for(self, declaration, source: bytes) -> dict[str, str]:
        scope = _nearest(declaration, {"namespace_definition"})
        root = scope if scope is not None else declaration
        while scope is None and root.parent is not None:
            root = root.parent
        imports: dict[str, str] = {}
        for node in _walk(root):
            if node.type != "namespace_use_clause" or node.start_byte >= declaration.start_byte:
                continue
            clause = _text(node, source).strip()
            if "{" in clause and "}" in clause:
                prefix, members = clause.split("{", 1)
                prefix = prefix.rstrip("\\")
                for member in members.rsplit("}", 1)[0].split(","):
                    self.record_import(f"{prefix}\\{member.strip()}", imports)
            else:
                self.record_import(clause, imports)
        return imports

    @staticmethod
    def record_import(clause: str, imports: dict[str, str]) -> None:
        parts = re.split(r"\s+as\s+", clause.strip(), flags=re.IGNORECASE)
        target = parts[0].strip().lstrip("\\")
        if not target or target.casefold().startswith(("function ", "const ")):
            return
        alias = parts[1].strip() if len(parts) > 1 else target.rsplit("\\", 1)[-1]
        imports[alias.casefold()] = target
