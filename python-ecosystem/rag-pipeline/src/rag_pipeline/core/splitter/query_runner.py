"""
Tree-sitter query runner using custom query files with built-in fallback.

Prefers custom .scm query files for rich metadata extraction (extends, implements, imports),
falling back to built-in TAGS_QUERY only when custom query is unavailable.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Any, Optional

from .tree_parser import get_parser
from .languages import TREESITTER_MODULES

logger = logging.getLogger(__name__)

# Directory containing custom .scm query files
QUERIES_DIR = Path(__file__).parent / "queries"

# Languages that have built-in TAGS_QUERY (used as fallback only)
LANGUAGES_WITH_BUILTIN_TAGS = {'python', 'java', 'javascript', 'go', 'rust', 'php'}

# Languages with custom .scm files for rich metadata (extends, implements, imports)
LANGUAGES_WITH_CUSTOM_QUERY = {
    'python', 'java', 'javascript', 'typescript', 'c_sharp', 'go', 'rust', 'php'
}


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
    
    def __init__(self):
        self._query_cache: Dict[str, Any] = {}  # lang -> compiled query
        self._scm_cache: Dict[str, str] = {}    # lang -> raw scm string
        self._parser = get_parser()
    
    def _get_builtin_tags_query(self, lang_name: str) -> Optional[str]:
        """Get built-in TAGS_QUERY from language package if available."""
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
    
    def _load_custom_query_file(self, lang_name: str) -> Optional[str]:
        """Load custom .scm query file for languages without built-in queries."""
        if lang_name in self._scm_cache:
            return self._scm_cache[lang_name]
        
        query_file = QUERIES_DIR / f"{lang_name}.scm"
        
        if not query_file.exists():
            logger.debug(f"No custom query file for {lang_name}")
            return None
        
        try:
            scm_content = query_file.read_text(encoding='utf-8')
            self._scm_cache[lang_name] = scm_content
            logger.debug(f"Loaded custom query file for {lang_name}")
            return scm_content
        except Exception as e:
            logger.warning(f"Failed to load query file {query_file}: {e}")
            return None
    
    def _get_query_string(self, lang_name: str) -> Optional[str]:
        """Get query string - custom first, then built-in fallback."""
        # Prefer custom .scm for rich metadata (extends, implements, imports)
        custom = self._load_custom_query_file(lang_name)
        if custom:
            return custom
        
        # Fall back to built-in TAGS_QUERY (limited metadata)
        return self._get_builtin_tags_query(lang_name)
    
    def _try_compile_query(self, lang_name: str, scm_content: str, language: Any) -> Optional[Any]:
        """Try to compile a query string, returning None on failure."""
        try:
            from tree_sitter import Query
            return Query(language, scm_content)
        except Exception as e:
            logger.debug(f"Query compilation failed for {lang_name}: {e}")
            return None
    
    def _get_compiled_query(self, lang_name: str) -> Optional[Any]:
        """Get or compile the query for a language with fallback."""
        if lang_name in self._query_cache:
            return self._query_cache[lang_name]
        
        language = self._parser.get_language(lang_name)
        if not language:
            return None
        
        # Try custom .scm first
        custom_scm = self._load_custom_query_file(lang_name)
        if custom_scm:
            query = self._try_compile_query(lang_name, custom_scm, language)
            if query:
                logger.debug(f"Using custom query for {lang_name}")
                self._query_cache[lang_name] = query
                return query
            else:
                logger.debug(f"Custom query failed for {lang_name}, trying built-in")
        
        # Fallback to built-in TAGS_QUERY
        builtin_scm = self._get_builtin_tags_query(lang_name)
        if builtin_scm:
            query = self._try_compile_query(lang_name, builtin_scm, language)
            if query:
                logger.debug(f"Using built-in TAGS_QUERY for {lang_name}")
                self._query_cache[lang_name] = query
                return query
        
        logger.debug(f"No working query available for {lang_name}")
        return None
    
    def run_query(
        self,
        source_code: str,
        lang_name: str,
        tree: Optional[Any] = None
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
        query = self._get_compiled_query(lang_name)
        if not query:
            return []
        
        if tree is None:
            tree = self._parser.parse(source_code, lang_name)
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
            match.captures[pattern_name] = CapturedNode(
                name=pattern_name,
                text=source_bytes[main_node.start_byte:main_node.end_byte].decode('utf-8', errors='replace'),
                start_byte=main_node.start_byte,
                end_byte=main_node.end_byte,
                start_point=(main_node.start_point.row, main_node.start_point.column),
                end_point=(main_node.end_point.row, main_node.end_point.column),
                node_type=main_node.type
            )
            
            # Add name capture if present
            if name_node:
                match.captures[f'{pattern_name}.name'] = CapturedNode(
                    name=f'{pattern_name}.name',
                    text=source_bytes[name_node.start_byte:name_node.end_byte].decode('utf-8', errors='replace'),
                    start_byte=name_node.start_byte,
                    end_byte=name_node.end_byte,
                    start_point=(name_node.start_point.row, name_node.start_point.column),
                    end_point=(name_node.end_point.row, name_node.end_point.column),
                    node_type=name_node.type
                )
            
            # Add doc capture if present
            if doc_node:
                match.captures[f'{pattern_name}.doc'] = CapturedNode(
                    name=f'{pattern_name}.doc',
                    text=source_bytes[doc_node.start_byte:doc_node.end_byte].decode('utf-8', errors='replace'),
                    start_byte=doc_node.start_byte,
                    end_byte=doc_node.end_byte,
                    start_point=(doc_node.start_point.row, doc_node.start_point.column),
                    end_point=(doc_node.end_point.row, doc_node.end_point.column),
                    node_type=doc_node.type
                )
            
            # Process any additional sub-captures from custom queries
            for capture_name, nodes in captures_dict.items():
                if '.' in capture_name and not capture_name.startswith(('definition.', 'reference.')):
                    node = nodes[0]
                    match.captures[capture_name] = CapturedNode(
                        name=capture_name,
                        text=source_bytes[node.start_byte:node.end_byte].decode('utf-8', errors='replace'),
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        start_point=(node.start_point.row, node.start_point.column),
                        end_point=(node.end_point.row, node.end_point.column),
                        node_type=node.type
                    )
            
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
    
    def has_query(self, lang_name: str) -> bool:
        """Check if a query is available for this language (custom or built-in)."""
        # Check custom file first
        query_file = QUERIES_DIR / f"{lang_name}.scm"
        if query_file.exists():
            return True
        # Check built-in fallback
        return lang_name in LANGUAGES_WITH_BUILTIN_TAGS
    
    def uses_custom_query(self, lang_name: str) -> bool:
        """Check if this language uses custom .scm query (rich metadata)."""
        query_file = QUERIES_DIR / f"{lang_name}.scm"
        return query_file.exists()
    
    def uses_builtin_query(self, lang_name: str) -> bool:
        """Check if this language uses built-in TAGS_QUERY (limited metadata)."""
        return lang_name in LANGUAGES_WITH_BUILTIN_TAGS and not self.uses_custom_query(lang_name)
    
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
