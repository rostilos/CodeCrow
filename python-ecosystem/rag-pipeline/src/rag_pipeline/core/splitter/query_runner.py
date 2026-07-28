"""
Tree-sitter query runner using custom query files with built-in fallback.

Prefers custom .scm query files for rich metadata extraction (extends, implements, imports),
falling back to built-in TAGS_QUERY only when custom query is unavailable.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional

from codecrow_plugins import PluginResources
from codecrow_plugins.bootstrap import builtin_plugins_root

from .tree_parser import get_parser
from .languages import TREESITTER_MODULES

logger = logging.getLogger(__name__)

# Languages that have built-in TAGS_QUERY (used as fallback only)
LANGUAGES_WITH_BUILTIN_TAGS = {'python', 'java', 'javascript', 'go', 'rust', 'php'}


@dataclass
class CapturedNode:
    """Represents a captured AST node from a query."""
    name: str  # Capture name (e.g., 'function.name', 'class.body')
    text: str  # Node text content
    start_byte: int
    end_byte: int
    start_point: tuple  # (row, column)
    end_point: tuple
    node_type: str  # Tree-sitter node type
    
    @property
    def start_line(self) -> int:
        return self.start_point[0] + 1  # Convert to 1-based
    
    @property
    def end_line(self) -> int:
        return self.end_point[0] + 1


@dataclass 
class QueryMatch:
    """A complete match from a query pattern."""
    pattern_name: str  # e.g., 'function', 'class', 'import'
    captures: Dict[str, CapturedNode] = field(default_factory=dict)
    
    def get(self, capture_name: str) -> Optional[CapturedNode]:
        """Get a captured node by name."""
        return self.captures.get(capture_name)
    
    @property
    def full_text(self) -> Optional[str]:
        """Get the full text of the main capture (pattern_name without suffix)."""
        main_capture = self.captures.get(self.pattern_name)
        return main_capture.text if main_capture else None


class QueryRunner:
    """
    Executes tree-sitter queries using custom .scm files with built-in fallback.
    
    Strategy:
    1. Prefer custom .scm files for rich metadata (extends, implements, imports, decorators)
    2. Fall back to built-in TAGS_QUERY only when no custom query exists
    
    Custom queries capture: @class.extends, @class.implements, @import, @decorator,
    @method.visibility, @function.return_type, etc.
    
    Built-in TAGS_QUERY only captures: @definition.function, @definition.class, @name, @doc
    """
    
    def __init__(self, plugin_resources: PluginResources | None = None):
        self._query_cache: Dict[str, Any] = {}  # lang -> compiled query
        self._scm_cache: Dict[str, str] = {}    # lang -> raw scm string
        self._parser = get_parser()
        self._plugin_resources = plugin_resources or PluginResources.discover(builtin_plugins_root())

    @staticmethod
    def _cache_key(lang_name: str, syntax: Any = None) -> str:
        if syntax is None:
            return lang_name
        return (
            f"plugin:{syntax.plugin_id}:{syntax.language_id}:"
            f"{syntax.query_resource}"
        )

    def _custom_query_path(self, lang_name: str, syntax: Any = None):
        if syntax is not None:
            if not syntax.query_resource:
                return None
            return self._plugin_resources.path(
                syntax.plugin_id,
                syntax.query_resource,
            )
        plugin_id = lang_name.replace("_", "-")
        return self._plugin_resources.path(plugin_id, "python/resources/rag-chunks.scm")
    
    def _get_builtin_tags_query(
        self,
        lang_name: str,
        syntax: Any = None,
    ) -> Optional[str]:
        """Get built-in TAGS_QUERY from language package if available."""
        if syntax is not None:
            if not syntax.builtin_tags:
                return None
            module_name = syntax.grammar_module
        else:
            if lang_name not in LANGUAGES_WITH_BUILTIN_TAGS:
                return None
            lang_info = TREESITTER_MODULES.get(lang_name)
            if not lang_info:
                return None
            module_name = lang_info[0]
        try:
            import importlib
            lang_module = importlib.import_module(module_name)
            tags_query = getattr(lang_module, 'TAGS_QUERY', None)
            if tags_query:
                logger.debug(f"Using built-in TAGS_QUERY for {lang_name}")
                return tags_query
        except (ImportError, AttributeError) as e:
            logger.debug(f"Could not load built-in query for {lang_name}: {e}")
        
        return None
    
    def _load_custom_query_file(
        self,
        lang_name: str,
        syntax: Any = None,
    ) -> Optional[str]:
        """Load custom .scm query file for languages without built-in queries."""
        cache_key = self._cache_key(lang_name, syntax)
        if cache_key in self._scm_cache:
            return self._scm_cache[cache_key]
        
        query_file = self._custom_query_path(lang_name, syntax)
        
        if query_file is None:
            logger.debug(f"No custom query file for {lang_name}")
            return None
        
        try:
            scm_content = query_file.read_text(encoding='utf-8')
            self._scm_cache[cache_key] = scm_content
            logger.debug(f"Loaded custom query file for {lang_name}")
            return scm_content
        except Exception as e:
            logger.warning(f"Failed to load query file {query_file}: {e}")
            return None
    
    def _try_compile_query(self, lang_name: str, scm_content: str, language: Any) -> Optional[Any]:
        """Try to compile a query string, returning None on failure."""
        try:
            from tree_sitter import Query
            return Query(language, scm_content)
        except Exception as e:
            logger.debug(f"Query compilation failed for {lang_name}: {e}")
            return None
    
    def _get_compiled_query(
        self,
        lang_name: str,
        syntax: Any = None,
    ) -> Optional[Any]:
        """Get or compile the query for a language with fallback."""
        cache_key = self._cache_key(lang_name, syntax)
        if cache_key in self._query_cache:
            return self._query_cache[cache_key]
        
        language = (
            self._parser.get_plugin_language(syntax)
            if syntax is not None
            else self._parser.get_language(lang_name)
        )
        if not language:
            return None
        
        # Try custom .scm first
        custom_scm = self._load_custom_query_file(lang_name, syntax)
        if custom_scm:
            query = self._try_compile_query(lang_name, custom_scm, language)
            if query:
                logger.debug(f"Using custom query for {lang_name}")
                self._query_cache[cache_key] = query
                return query
            else:
                logger.debug(f"Custom query failed for {lang_name}, trying built-in")
        
        # Fallback to built-in TAGS_QUERY
        builtin_scm = self._get_builtin_tags_query(lang_name, syntax)
        if builtin_scm:
            query = self._try_compile_query(lang_name, builtin_scm, language)
            if query:
                logger.debug(f"Using built-in TAGS_QUERY for {lang_name}")
                self._query_cache[cache_key] = query
                return query
        
        logger.debug(f"No working query available for {lang_name}")
        return None
    
    def run_query(
        self,
        source_code: str,
        lang_name: str,
        tree: Optional[Any] = None,
        syntax: Any = None,
    ) -> List[QueryMatch]:
        """
        Run the query for a language and return all matches.
        
        Args:
            source_code: Source code string
            lang_name: Tree-sitter language name
            tree: Optional pre-parsed tree (will parse if not provided)
            
        Returns:
            List of QueryMatch objects with captured nodes
        """
        query = self._get_compiled_query(lang_name, syntax)
        if not query:
            return []
        
        if tree is None:
            tree = (
                self._parser.parse_plugin(source_code, syntax)
                if syntax is not None
                else self._parser.parse(source_code, lang_name)
            )
            if tree is None:
                return []
        
        source_bytes = source_code.encode('utf-8')
        
        try:
            # Use QueryCursor.matches() for pattern-grouped results
            # Each match is (pattern_id, {capture_name: [nodes]})
            from tree_sitter import QueryCursor
            cursor = QueryCursor(query)
            raw_matches = list(cursor.matches(tree.root_node))
        except Exception as e:
            logger.warning(f"Query execution failed for {lang_name}: {e}")
            return []
        
        results: List[QueryMatch] = []

        def point_for_byte(byte_offset: int) -> tuple[int, int]:
            """Return a zero-based (line, column) point derived from source bytes."""
            line = source_bytes.count(b'\n', 0, byte_offset)
            previous_newline = source_bytes.rfind(b'\n', 0, byte_offset)
            if previous_newline < 0:
                column = byte_offset
            else:
                column = byte_offset - previous_newline - 1
            return line, column

        def captured_node(capture_name: str, node: Any) -> CapturedNode:
            """Build a captured node using byte-derived points for binding safety."""
            return CapturedNode(
                name=capture_name,
                text=source_bytes[node.start_byte:node.end_byte].decode('utf-8', errors='replace'),
                start_byte=node.start_byte,
                end_byte=node.end_byte,
                start_point=point_for_byte(node.start_byte),
                end_point=point_for_byte(node.end_byte),
                node_type=node.type
            )
        
        for pattern_id, captures_dict in raw_matches:
            # Determine pattern type from captures
            # Built-in: @definition.function, @definition.class, @name
            # Custom: @function, @class, @function.name
            
            pattern_name = None
            main_node = None
            name_node = None
            doc_node = None
            
            for capture_name, nodes in captures_dict.items():
                if not nodes:
                    continue
                node = nodes[0]  # Take first node for each capture
                
                # Built-in definition captures
                if capture_name.startswith('definition.'):
                    pattern_name = capture_name[len('definition.'):]
                    main_node = node
                # Built-in @name capture (associated with this pattern)
                elif capture_name == 'name':
                    name_node = node
                # Built-in @doc capture
                elif capture_name == 'doc':
                    doc_node = node
                # Skip reference captures
                elif capture_name.startswith('reference.'):
                    continue
                # Custom query captures: @function, @class
                elif '.' not in capture_name:
                    pattern_name = capture_name
                    main_node = node
            
            # Skip if no definition pattern found
            if not pattern_name or not main_node:
                continue
            
            # Build the QueryMatch
            match = QueryMatch(pattern_name=pattern_name)
            
            # Add main capture
            match.captures[pattern_name] = captured_node(pattern_name, main_node)
            
            # Add name capture if present
            if name_node:
                capture_name = f'{pattern_name}.name'
                match.captures[capture_name] = captured_node(capture_name, name_node)
            
            # Add doc capture if present
            if doc_node:
                capture_name = f'{pattern_name}.doc'
                match.captures[capture_name] = captured_node(capture_name, doc_node)
            
            # Process any additional sub-captures from custom queries
            for capture_name, nodes in captures_dict.items():
                if '.' in capture_name and not capture_name.startswith(('definition.', 'reference.')):
                    node = nodes[0]
                    match.captures[capture_name] = captured_node(capture_name, node)
            
            results.append(match)
        
        return results
    
    def get_functions(self, source_code: str, lang_name: str) -> List[QueryMatch]:
        """Convenience method to get function/method matches."""
        matches = self.run_query(source_code, lang_name)
        return [m for m in matches if m.pattern_name in ('function', 'method')]
    
    def get_classes(self, source_code: str, lang_name: str) -> List[QueryMatch]:
        """Convenience method to get class/struct/interface matches."""
        matches = self.run_query(source_code, lang_name)
        return [m for m in matches if m.pattern_name in ('class', 'struct', 'interface', 'trait')]
    
    def get_imports(self, source_code: str, lang_name: str) -> List[QueryMatch]:
        """Convenience method to get import statement matches."""
        matches = self.run_query(source_code, lang_name)
        return [m for m in matches if m.pattern_name == 'import']
    
    def has_query(self, lang_name: str, syntax: Any = None) -> bool:
        """Check if a query is available for this language (custom or built-in)."""
        # Check custom file first
        if self._custom_query_path(lang_name, syntax) is not None:
            return True
        # Check built-in fallback
        if syntax is not None:
            return syntax.builtin_tags
        return lang_name in LANGUAGES_WITH_BUILTIN_TAGS
    
    def uses_custom_query(self, lang_name: str, syntax: Any = None) -> bool:
        """Check if this language uses custom .scm query (rich metadata)."""
        return self._custom_query_path(lang_name, syntax) is not None
    
    def uses_builtin_query(self, lang_name: str, syntax: Any = None) -> bool:
        """Check if this language uses built-in TAGS_QUERY (limited metadata)."""
        builtin_available = (
            syntax.builtin_tags
            if syntax is not None
            else lang_name in LANGUAGES_WITH_BUILTIN_TAGS
        )
        return builtin_available and not self.uses_custom_query(lang_name, syntax)
    
    def clear_cache(self):
        """Clear compiled query cache."""
        self._query_cache.clear()
        self._scm_cache.clear()


# Global singleton
_runner_instance: Optional[QueryRunner] = None


def get_query_runner() -> QueryRunner:
    """Get the global QueryRunner instance."""
    global _runner_instance
    if _runner_instance is None:
        _runner_instance = QueryRunner()
    return _runner_instance
