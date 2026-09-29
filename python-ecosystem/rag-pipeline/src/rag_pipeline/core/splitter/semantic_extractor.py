"""Build semantic chunks from plugin queries or the generic AST fallback."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from ..documents import Document, TextNode
from .metadata import MetadataExtractor, ContentType
from .chunk import ASTChunk, generate_deterministic_id, compute_file_hash, normalize_chunk, normalize_metadata

logger = logging.getLogger(__name__)

from .query_runner import QueryMatch
from .ast_details import ASTDetails
from .chunk import _stable_unique_strings

class SemanticExtractor:
    def __init__(self, parser, query_runner, metadata_extractor):
        self._parser = parser
        self._query_runner = query_runner
        self._metadata_extractor = metadata_extractor
        self.details = ASTDetails()

    def _extract_with_queries(
        self,
        text: str,
        lang_name: str,
        path: str,
        syntax: Any = None,
    ) -> List[ASTChunk]:
        """Extract chunks using tree-sitter query files with rich metadata."""
        if not self._query_runner.has_query(lang_name, syntax):
            return []

        tree = (
            self._parser.parse_plugin(text, syntax)
            if syntax is not None
            else self._parser.parse(text, lang_name)
        )
        if not tree:
            return []

        matches = self._query_runner.run_query(
            text,
            lang_name,
            tree,
            syntax,
        )
        if not matches:
            return []

        source_bytes = text.encode('utf-8')
        chunks = []
        chunk_ranges: List[tuple[int, int, ASTChunk]] = []
        chunk_by_range: Dict[tuple, ASTChunk] = {}
        processed_ranges: Set[tuple] = set()

        # Collect file-level metadata from all matches
        imports = []
        namespace = None
        decorators_map: Dict[int, List[str]] = {}  # line -> decorators

        for match in matches:
            # Handle imports (multiple capture variations)
            if match.pattern_name in ('import', 'use'):
                import_path = (
                    match.get('import.path') or
                    match.get('import') or
                    match.get('use.path') or
                    match.get('use')
                )
                if import_path:
                    imports.append(import_path.text.strip().strip('"\''))
                continue

            # Handle namespace/package/module
            if match.pattern_name in ('namespace', 'package', 'module'):
                ns_cap = match.get(f'{match.pattern_name}.name') or match.get(match.pattern_name)
                if ns_cap:
                    namespace = ns_cap.text.strip()
                continue

            # Handle standalone decorators/attributes
            if match.pattern_name in ('decorator', 'attribute', 'annotation'):
                dec_cap = match.get(f'{match.pattern_name}.name') or match.get(match.pattern_name)
                if dec_cap:
                    line = dec_cap.start_line
                    if line not in decorators_map:
                        decorators_map[line] = []
                    decorators_map[line].append(dec_cap.text.strip())
                continue

            # Handle main constructs: functions, classes, methods, etc.
            semantic_patterns = (
                'function', 'method', 'class', 'interface', 'struct', 'trait',
                'enum', 'impl', 'constructor', 'closure', 'arrow', 'const',
                'var', 'static', 'type', 'record'
            )
            if match.pattern_name in semantic_patterns:
                main_cap = match.get(match.pattern_name)
                if not main_cap:
                    continue

                range_key = (main_cap.start_byte, main_cap.end_byte)
                if range_key in processed_ranges:
                    self._merge_query_definition_metadata(chunk_by_range[range_key], match)
                    continue
                processed_ranges.add(range_key)

                # Get name from various capture patterns
                name_cap = (
                    match.get(f'{match.pattern_name}.name') or
                    match.get('name')
                )
                name = name_cap.text if name_cap else None

                # Get inheritance (extends/implements/embeds/supertrait)
                extends = []
                implements = []

                for ext_capture in ('extends', 'embeds', 'supertrait', 'base_type'):
                    cap = match.get(f'{match.pattern_name}.{ext_capture}')
                    if cap:
                        extends.extend(self._parse_type_list(cap.text))

                for impl_capture in ('implements', 'trait'):
                    cap = match.get(f'{match.pattern_name}.{impl_capture}')
                    if cap:
                        implements.extend(self._parse_type_list(cap.text))

                if not extends and not implements:
                    inheritance = self._metadata_extractor.extract_inheritance(main_cap.text, lang_name)
                    extends = inheritance.get('extends', [])
                    implements = inheritance.get('implements', [])

                # Get additional metadata from captures
                visibility = match.get(f'{match.pattern_name}.visibility')
                return_type = match.get(f'{match.pattern_name}.return_type')
                params = match.get(f'{match.pattern_name}.params')
                modifiers = []

                for mod in ('static', 'abstract', 'final', 'async', 'readonly', 'const', 'unsafe'):
                    if match.get(f'{match.pattern_name}.{mod}'):
                        modifiers.append(mod)

                chunk = ASTChunk(
                    content=main_cap.text,
                    content_type=ContentType.FUNCTIONS_CLASSES,
                    language=lang_name,
                    path=path,
                    symbol_names=[name] if name else [],
                    parent_context=[],
                    start_line=main_cap.start_line,
                    end_line=main_cap.end_line,
                    node_type=match.pattern_name,
                    extends=extends,
                    implements=implements,
                    modifiers=modifiers,
                )

                # Extract docstring and signature — prefer AST traversal over regex
                ts_node = self.details._find_node_at_position(
                    tree.root_node, main_cap.start_byte, main_cap.end_byte
                ) if tree else None
                chunk.docstring = self._metadata_extractor.extract_docstring(main_cap.text, lang_name, ts_node=ts_node)
                chunk.signature = self._metadata_extractor.extract_signature(main_cap.text, lang_name, ts_node=ts_node)

                # Extract rich AST details (methods, properties, params, calls, etc.).
                # Some native tree-sitter bindings can segfault while walking large
                # Java trees, so keep this optional and only run it for known-safe
                # languages. Query captures still provide semantic Java chunks.
                if ts_node is not None and self.details._is_rich_ast_traversal_safe(lang_name, syntax):
                    self.details._extract_rich_details_from_node(chunk, ts_node, source_bytes, lang_name)

                chunks.append(chunk)
                chunk_ranges.append((main_cap.start_byte, main_cap.end_byte, chunk))
                chunk_by_range[range_key] = chunk

        self._attach_query_relationship_metadata(matches, chunk_ranges)
        self._attach_query_parent_context(chunk_ranges)

        # Preserve complete source-ordered structural inventories.
        imports = _stable_unique_strings(imports)
        for chunk in chunks:
            chunk.imports = list(imports)
            chunk.namespace = namespace
            normalize_chunk(chunk)

        # Create simplified code chunk
        if chunks:
            simplified = self._create_simplified_code(text, chunks, lang_name)
            if simplified and len(simplified.strip()) > 50:
                chunks.append(ASTChunk(
                    content=simplified,
                    content_type=ContentType.SIMPLIFIED_CODE,
                    language=lang_name,
                    path=path,
                    start_line=1,
                    end_line=text.count('\n') + 1,
                    node_type='simplified',
                    imports=list(imports),
                    namespace=namespace,
                ))

        return chunks


    def _merge_query_definition_metadata(self, chunk: ASTChunk, match: QueryMatch) -> None:
        """Merge metadata from duplicate query matches for the same definition."""
        def add_unique(values: List[str], value: str) -> None:
            for item in self._parse_type_list(value):
                if item and item not in values:
                    values.append(item)

        for ext_capture in ('extends', 'embeds', 'supertrait', 'base_type'):
            cap = match.get(f'{match.pattern_name}.{ext_capture}')
            if cap:
                add_unique(chunk.extends, cap.text)

        for impl_capture in ('implements', 'trait'):
            cap = match.get(f'{match.pattern_name}.{impl_capture}')
            if cap:
                add_unique(chunk.implements, cap.text)

        return_type = match.get(f'{match.pattern_name}.return_type')
        if return_type:
            chunk.return_type = return_type.text.strip()
        normalize_chunk(chunk)


    def _attach_query_relationship_metadata(
        self,
        matches: List[QueryMatch],
        chunk_ranges: List[tuple[int, int, ASTChunk]],
    ) -> None:
        """Attach non-definition query captures to the smallest containing chunk."""
        if not chunk_ranges:
            return

        inventory_members: dict[int, set[str]] = {}

        def add_unique(values: List[str], value: Optional[str]) -> None:
            if not value:
                return
            value = value.strip().strip('"\'`;')
            if not value:
                return
            identity = id(values)
            seen = inventory_members.get(identity)
            if seen is None:
                seen = set(values)
                inventory_members[identity] = seen
            if value not in seen:
                seen.add(value)
                values.append(value)

        def best_chunk(start: int, end: int) -> Optional[ASTChunk]:
            containing = [
                (range_end - range_start, chunk)
                for range_start, range_end, chunk in chunk_ranges
                if range_start <= start and end <= range_end
            ]
            if not containing:
                return None
            return min(containing, key=lambda item: item[0])[1]

        for match in matches:
            main_cap = match.get(match.pattern_name)
            if not main_cap:
                continue

            chunk = best_chunk(main_cap.start_byte, main_cap.end_byte)
            if not chunk:
                continue

            if match.pattern_name == 'call':
                call_name = match.get('call.name')
                call_object = match.get('call.object')
                add_unique(chunk.calls, call_name.text if call_name else main_cap.text)
                if call_object and call_object.text[:1].isupper():
                    add_unique(chunk.referenced_types, call_object.text)
                continue

            if match.pattern_name == 'type_reference':
                type_name = match.get('type_reference.name') or main_cap
                add_unique(chunk.referenced_types, type_name.text)
                continue

            if match.pattern_name == 'parameter':
                parameter_name = match.get('parameter.name')
                parameter_type = match.get('parameter.type')
                add_unique(chunk.parameters, parameter_name.text if parameter_name else None)
                add_unique(chunk.referenced_types, parameter_type.text if parameter_type else None)
                continue

            if match.pattern_name == 'field':
                field_name = match.get('field.name') or match.get('name')
                field_type = match.get('field.type')
                add_unique(chunk.properties, field_name.text if field_name else None)
                add_unique(chunk.referenced_types, field_type.text if field_type else None)
                continue

            if match.pattern_name == 'variable':
                variable_name = match.get('variable.name')
                variable_type = match.get('variable.type')
                add_unique(chunk.variables, variable_name.text if variable_name else None)
                add_unique(chunk.referenced_types, variable_type.text if variable_type else None)

        for chunk in {id(item[2]): item[2] for item in chunk_ranges}.values():
            normalize_chunk(chunk)


    def _attach_query_parent_context(
        self,
        chunk_ranges: List[tuple[int, int, ASTChunk]],
    ) -> None:
        """Infer parent class context from query capture containment."""
        class_like = {
            'class', 'interface', 'record', 'enum', 'annotation', 'struct', 'trait'
        }
        member_like = {'method', 'constructor', 'function'}
        property_like = {'field', 'var', 'const', 'static'}

        parents = [
            (start, end, chunk)
            for start, end, chunk in chunk_ranges
            if chunk.node_type in class_like and chunk.symbol_names
        ]
        if not parents:
            return

        member_names = {id(parent): set(parent.methods) for _, _, parent in parents}
        property_names = {id(parent): set(parent.properties) for _, _, parent in parents}
        changed_parents: dict[int, ASTChunk] = {}
        for start, end, chunk in chunk_ranges:
            if chunk.node_type in class_like:
                continue
            containing = [
                (parent_end - parent_start, parent)
                for parent_start, parent_end, parent in parents
                if parent_start <= start and end <= parent_end
            ]
            if not containing:
                continue
            parent = min(containing, key=lambda item: item[0])[1]
            parent_name = parent.symbol_names[0]
            chunk.parent_context = [*parent.parent_context, parent_name]

            child_name = chunk.symbol_names[0] if chunk.symbol_names else None
            if not child_name:
                continue
            identity = id(parent)
            if chunk.node_type in member_like and child_name not in member_names[identity]:
                member_names[identity].add(child_name)
                parent.methods.append(child_name)
                changed_parents[identity] = parent
            elif chunk.node_type in property_like and child_name not in property_names[identity]:
                property_names[identity].add(child_name)
                parent.properties.append(child_name)
                changed_parents[identity] = parent
        for parent in changed_parents.values():
            normalize_chunk(parent)


    def _extract_with_traversal(
        self,
        text: str,
        lang_name: str,
        path: str,
        syntax: Any = None,
    ) -> List[ASTChunk]:
        """Fallback: extract chunks using manual AST traversal."""
        tree = (
            self._parser.parse_plugin(text, syntax)
            if syntax is not None
            else self._parser.parse(text, lang_name)
        )
        if not tree:
            return []

        source_bytes = text.encode('utf-8')
        chunks = []
        processed_ranges: Set[tuple] = set()

        # Node types for semantic chunking
        semantic_types = self.details._get_semantic_node_types(lang_name)
        class_types = set(semantic_types.get('class', []))
        function_types = set(semantic_types.get('function', []))
        all_types = class_types | function_types

        def get_node_text(node) -> str:
            return source_bytes[node.start_byte:node.end_byte].decode('utf-8', errors='replace')

        def get_node_name(node) -> Optional[str]:
            for child in node.children:
                if child.type in ('identifier', 'name', 'type_identifier', 'property_identifier'):
                    return get_node_text(child)
            return None

        def traverse(node, parent_context: List[str]):
            node_range = (node.start_byte, node.end_byte)

            if node.type in all_types:
                if node_range in processed_ranges:
                    return

                content = get_node_text(node)
                start_line = source_bytes[:node.start_byte].count(b'\n') + 1
                end_line = start_line + content.count('\n')
                node_name = get_node_name(node)
                is_class = node.type in class_types

                chunk = ASTChunk(
                    content=content,
                    content_type=ContentType.FUNCTIONS_CLASSES,
                    language=lang_name,
                    path=path,
                    symbol_names=[node_name] if node_name else [],
                    parent_context=list(parent_context),
                    start_line=start_line,
                    end_line=end_line,
                    node_type=node.type,
                )

                chunk.docstring = self._metadata_extractor.extract_docstring(content, lang_name, ts_node=node)
                chunk.signature = self._metadata_extractor.extract_signature(content, lang_name, ts_node=node)

                # Extract inheritance via regex
                inheritance = self._metadata_extractor.extract_inheritance(content, lang_name)
                chunk.extends = inheritance.get('extends', [])
                chunk.implements = inheritance.get('implements', [])
                chunk.imports = inheritance.get('imports', [])

                # Extract rich AST details directly from this node
                self.details._extract_rich_details_from_node(chunk, node, source_bytes, lang_name)

                chunks.append(chunk)
                processed_ranges.add(node_range)

                if is_class and node_name:
                    for child in node.children:
                        traverse(child, parent_context + [node_name])
            else:
                for child in node.children:
                    traverse(child, parent_context)

        traverse(tree.root_node, [])

        # Create simplified code
        if chunks:
            simplified = self._create_simplified_code(text, chunks, lang_name)
            if simplified and len(simplified.strip()) > 50:
                chunks.append(ASTChunk(
                    content=simplified,
                    content_type=ContentType.SIMPLIFIED_CODE,
                    language=lang_name,
                    path=path,
                    start_line=1,
                    end_line=text.count('\n') + 1,
                    node_type='simplified',
                ))

        return chunks


    def _create_simplified_code(
        self,
        source_code: str,
        chunks: List[ASTChunk],
        language: str
    ) -> str:
        """Create simplified code with placeholders for extracted chunks.

        Uses AST start_line/end_line offsets for a single-pass replacement
        instead of O(n*m) string find() calls per chunk.
        """
        semantic_chunks = [c for c in chunks if c.content_type == ContentType.FUNCTIONS_CLASSES]
        if not semantic_chunks:
            return source_code

        # Sort by start_line descending so replacements don't shift offsets
        sorted_chunks = sorted(
            semantic_chunks,
            key=lambda x: x.start_line,
            reverse=True
        )

        lines = source_code.split('\n')
        comment_prefix = self._metadata_extractor.get_comment_prefix(language)

        for chunk in sorted_chunks:
            # Validate line range (1-indexed from AST)
            start = chunk.start_line - 1  # Convert to 0-indexed
            end = chunk.end_line  # end_line is inclusive, but slice is exclusive
            if start < 0 or end > len(lines):
                continue

            first_line = chunk.content.split('\n')[0].strip()
            if len(first_line) > 60:
                first_line = first_line[:60] + '...'

            breadcrumb = ""
            if chunk.parent_context:
                breadcrumb = f" (in {'.'.join(chunk.parent_context)})"

            placeholder = f"{comment_prefix} Code for: {first_line}{breadcrumb}"
            lines[start:end] = [placeholder]

        return '\n'.join(lines).strip()


    def _parse_type_list(self, text: str) -> List[str]:
        """Parse a comma-separated list of types."""
        if not text:
            return []

        text = text.strip().strip('()[]')

        # Remove keywords
        for kw in ('extends', 'implements', 'with', ':'):
            text = text.replace(kw, ' ')

        types = []
        for part in text.split(','):
            name = part.strip()
            if '<' in name:
                name = name.split('<')[0].strip()
            if '(' in name:
                name = name.split('(')[0].strip()
            if name:
                types.append(name)

        return types


