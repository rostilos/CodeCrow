"""
Tree-sitter parser wrapper with caching and language loading.

Handles dynamic loading of tree-sitter language modules using the new API (v0.23+).
"""

import logging
from typing import Dict, Any, Optional

from .languages import TREESITTER_MODULES

logger = logging.getLogger(__name__)


class TreeSitterParser:
    """
    Wrapper for tree-sitter parser with language caching.
    
    Uses the new tree-sitter API (v0.23+) with individual language packages.
    """
    
    def __init__(self):
        self._language_cache: Dict[str, Any] = {}
        self._available: Optional[bool] = None
    
    def is_available(self) -> bool:
        """Check if tree-sitter is available and working."""
        if self._available is None:
            try:
                from tree_sitter import Parser, Language
                import tree_sitter_python as tspython
                
                py_language = Language(tspython.language())
                parser = Parser(py_language)
                parser.parse(b"def test(): pass")
                
                self._available = True
                logger.info("tree-sitter is available and working")
            except ImportError as e:
                logger.warning(f"tree-sitter not installed: {e}")
                self._available = False
            except Exception as e:
                logger.warning(f"tree-sitter error: {type(e).__name__}: {e}")
                self._available = False
        return self._available
    
    def get_language(self, lang_name: str) -> Optional[Any]:
        """
        Get tree-sitter Language object for a language name.
        
        Args:
            lang_name: Tree-sitter language name (e.g., 'python', 'java', 'php')
            
        Returns:
            tree_sitter.Language object or None if unavailable
        """
        if lang_name in self._language_cache:
            return self._language_cache[lang_name]
        
        if not self.is_available():
            return None
        
        try:
            from tree_sitter import Language
            
            lang_info = TREESITTER_MODULES.get(lang_name)
            if not lang_info:
                logger.debug(f"No tree-sitter module mapping for '{lang_name}'")
                return None
            
            module_name, func_name = lang_info
            
            import importlib
            lang_module = importlib.import_module(module_name)
            
            lang_func = getattr(lang_module, func_name, None)
            if not lang_func:
                logger.debug(f"Module {module_name} has no {func_name} function")
                return None
            
            language = Language(lang_func())
            self._language_cache[lang_name] = language
            return language
            
        except Exception as e:
            logger.debug(f"Could not load tree-sitter language '{lang_name}': {e}")
            return None
    
    def parse(self, source_code: str, lang_name: str) -> Optional[Any]:
        """
        Parse source code and return the AST tree.
        
        Args:
            source_code: Source code string
            lang_name: Tree-sitter language name
            
        Returns:
            tree_sitter.Tree object or None if parsing failed
        """
        language = self.get_language(lang_name)
        if not language:
            return None
        
        try:
            from tree_sitter import Parser
            
            parser = Parser(language)
            tree = parser.parse(bytes(source_code, "utf8"))
            return tree
            
        except Exception as e:
            logger.warning(f"Failed to parse code with tree-sitter ({lang_name}): {e}")
            return None
    
    def clear_cache(self):
        """Clear the language cache."""
        self._language_cache.clear()


# Global singleton instance
_parser_instance: Optional[TreeSitterParser] = None


def get_parser() -> TreeSitterParser:
    """Get the global TreeSitterParser instance."""
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = TreeSitterParser()
    return _parser_instance
