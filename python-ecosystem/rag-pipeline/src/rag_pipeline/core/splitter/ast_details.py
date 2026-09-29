"""Optional native AST detail extraction, isolated from chunk sizing."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from ..documents import Document, TextNode
from .metadata import MetadataExtractor, ContentType
from .chunk import ASTChunk, generate_deterministic_id, compute_file_hash, normalize_chunk, normalize_metadata

logger = logging.getLogger(__name__)

class ASTDetails:
    RICH_AST_TRAVERSAL_UNSAFE_LANGUAGES = {"java"}
    RICH_AST_MAX_DEPTH = 10

    @staticmethod
    def _add_partial_reason(chunk: ASTChunk, reason: str) -> None:
        if reason not in chunk.metadata_partial_reasons:
            chunk.metadata_partial_reasons.append(reason)


    def _find_node_at_position(self, root, start_byte: int, end_byte: int) -> Optional[Any]:
        """Find the tree-sitter node at the given byte position."""
        def find(node):
            if node.start_byte == start_byte and node.end_byte == end_byte:
                return node
            for child in node.children:
                if child.start_byte <= start_byte and child.end_byte >= end_byte:
                    result = find(child)
                    if result:
                        return result
            return None
        return find(root)


    def _is_rich_ast_traversal_safe(
        self,
        language: str,
        syntax: Any = None,
    ) -> bool:
        """Return whether recursive native tree-sitter traversal is safe for metadata enrichment."""
        if syntax is not None:
            return syntax.rich_traversal_safe
        return language not in self.RICH_AST_TRAVERSAL_UNSAFE_LANGUAGES


    def _get_rich_node_types(self, language: str) -> Dict[str, List[str]]:
        """Get tree-sitter node types for extracting rich details."""
        # Common patterns across languages
        common = {
            'identifier': ['identifier', 'name', 'type_identifier', 'property_identifier'],
            'call': ['call_expression', 'call', 'function_call', 'method_invocation'],
            'type_ref': ['type_identifier', 'generic_type', 'type_annotation', 'type'],
            'type_param': ['type_parameter', 'type_parameters', 'generic_parameter'],
        }

        types = {
            'python': {
                **common,
                'method': ['function_definition'],
                'property': ['assignment', 'expression_statement'],
                'parameter': ['parameter', 'default_parameter', 'typed_parameter'],
                'decorator': ['decorator'],
                'return_type': ['type'],
                'variable': ['assignment'],
            },
            'java': {
                **common,
                'method': ['method_declaration', 'constructor_declaration'],
                'property': ['field_declaration'],
                'parameter': ['formal_parameter', 'spread_parameter'],
                'decorator': ['annotation', 'marker_annotation'],
                'return_type': ['type_identifier', 'generic_type', 'void_type'],
                'variable': ['local_variable_declaration'],
            },
            'javascript': {
                **common,
                'method': ['method_definition', 'function_declaration'],
                'property': ['field_definition', 'public_field_definition'],
                'parameter': ['formal_parameters', 'required_parameter'],
                'decorator': ['decorator'],
                'return_type': ['type_annotation'],
                'variable': ['variable_declarator'],
            },
            'typescript': {
                **common,
                'method': ['method_definition', 'method_signature', 'function_declaration'],
                'property': ['public_field_definition', 'property_signature'],
                'parameter': ['required_parameter', 'optional_parameter'],
                'decorator': ['decorator'],
                'return_type': ['type_annotation'],
                'variable': ['variable_declarator'],
            },
            'go': {
                **common,
                'method': ['method_declaration', 'function_declaration'],
                'property': ['field_declaration'],
                'parameter': ['parameter_declaration'],
                'decorator': [],  # Go doesn't have decorators
                'return_type': ['type_identifier', 'pointer_type'],
                'variable': ['short_var_declaration', 'var_declaration'],
            },
            'rust': {
                **common,
                'method': ['function_item', 'associated_item'],
                'property': ['field_declaration'],
                'parameter': ['parameter'],
                'decorator': ['attribute_item'],
                'return_type': ['type_identifier', 'generic_type'],
                'variable': ['let_declaration'],
            },
            'c_sharp': {
                **common,
                'method': ['method_declaration', 'constructor_declaration'],
                'property': ['property_declaration', 'field_declaration'],
                'parameter': ['parameter'],
                'decorator': ['attribute_list', 'attribute'],
                'return_type': ['predefined_type', 'generic_name'],
                'variable': ['variable_declaration'],
            },
            'php': {
                **common,
                'method': ['method_declaration', 'function_definition'],
                'property': ['property_declaration'],
                'parameter': ['simple_parameter'],
                'decorator': ['attribute_list'],
                'return_type': ['named_type', 'union_type'],
                'variable': ['property_declaration', 'simple_variable'],
            },
        }

        return types.get(language, {
            **common,
            'method': [],
            'property': [],
            'parameter': [],
            'decorator': [],
            'return_type': [],
            'variable': [],
        })


    def _get_semantic_node_types(self, language: str) -> Dict[str, List[str]]:
        """Get semantic node types for manual traversal fallback."""
        types = {
            'python': {
                'class': ['class_definition'],
                'function': ['function_definition'],
            },
            'java': {
                'class': ['class_declaration', 'interface_declaration', 'enum_declaration'],
                'function': ['method_declaration', 'constructor_declaration'],
            },
            'javascript': {
                'class': ['class_declaration'],
                'function': ['function_declaration', 'method_definition', 'arrow_function'],
            },
            'typescript': {
                'class': ['class_declaration', 'interface_declaration'],
                'function': ['function_declaration', 'method_definition', 'arrow_function'],
            },
            'go': {
                'class': ['type_declaration'],
                'function': ['function_declaration', 'method_declaration'],
            },
            'rust': {
                'class': ['struct_item', 'impl_item', 'trait_item', 'enum_item'],
                'function': ['function_item'],
            },
            'c_sharp': {
                'class': ['class_declaration', 'interface_declaration', 'struct_declaration'],
                'function': ['method_declaration', 'constructor_declaration'],
            },
            'php': {
                'class': ['class_declaration', 'interface_declaration', 'trait_declaration'],
                'function': ['function_definition', 'method_declaration'],
            },
        }
        return types.get(language, {'class': [], 'function': []})


    def _extract_rich_details_from_node(
        self,
        chunk: ASTChunk,
        node: Any,
        source_bytes: bytes,
        lang_name: str,
        source_offset: int = 0,
    ) -> None:
        """
        Extract rich AST details directly from a tree-sitter node.
        Used by traversal-based extraction when we already have the node.
        """
        node_types = self._get_rich_node_types(lang_name)
        inventories = {
            name: set(getattr(chunk, name))
            for name in (
                "methods", "properties", "parameters", "decorators", "calls",
                "referenced_types", "type_parameters", "variables",
            )
        }

        def add_unique(name: str, value: str | None) -> None:
            if value and value not in inventories[name]:
                inventories[name].add(value)
                getattr(chunk, name).append(value)

        def get_text(n) -> str:
            return source_bytes[n.start_byte - source_offset:n.end_byte - source_offset].decode('utf-8', errors='replace')

        def extract_identifier(n) -> Optional[str]:
            for child in n.children:
                if child.type in node_types['identifier']:
                    return get_text(child)
            return None

        def record_details(n):
            node_type = n.type

            if node_type in node_types['method']:
                name = extract_identifier(n)
                add_unique("methods", name)

            if node_type in node_types['property']:
                name = extract_identifier(n)
                add_unique("properties", name)

            if node_type in node_types['parameter']:
                name = extract_identifier(n)
                add_unique("parameters", name)

            if node_type in node_types['decorator']:
                dec_text = get_text(n).strip()
                if dec_text.startswith('@'):
                    dec_text = dec_text[1:]
                if '(' in dec_text:
                    dec_text = dec_text.split('(')[0]
                add_unique("decorators", dec_text)

            if node_type in node_types['call']:
                name = extract_identifier(n)
                add_unique("calls", name)

            if node_type in node_types['type_ref']:
                type_text = get_text(n).strip()
                if '<' in type_text:
                    type_text = type_text.split('<')[0]
                add_unique("referenced_types", type_text)

            if node_type in node_types['return_type'] and not chunk.return_type:
                chunk.return_type = get_text(n).strip()

            if node_type in node_types['type_param']:
                param_text = get_text(n).strip()
                add_unique("type_parameters", param_text)

            if node_type in node_types['variable']:
                name = extract_identifier(n)
                add_unique("variables", name)

        pending = [(node, 0)]
        while pending:
            current, depth = pending.pop()
            record_details(current)
            if depth >= self.RICH_AST_MAX_DEPTH:
                if current.children:
                    self._add_partial_reason(chunk, "ast_depth_limit")
                continue
            pending.extend(
                (child, depth + 1) for child in reversed(current.children)
            )
        normalize_chunk(chunk)


    def _extract_rich_ast_details(self, chunk, tree, captured_node, lang_name):
        node = self._find_node_at_position(tree.root_node, captured_node.start_byte, captured_node.end_byte)
        if node is not None:
            self._extract_rich_details_from_node(
                chunk, node, chunk.content.encode("utf-8"), lang_name,
                source_offset=captured_node.start_byte,
            )
