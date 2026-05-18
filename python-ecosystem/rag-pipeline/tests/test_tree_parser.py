"""
Tests for rag_pipeline.core.splitter.tree_parser — TreeSitterParser, get_parser.
"""
import pytest
from unittest.mock import patch, MagicMock


# ─────────────────────────────────────────────────────────────
# TreeSitterParser
# ─────────────────────────────────────────────────────────────
class TestTreeSitterParser:

    def test_init(self):
        from rag_pipeline.core.splitter.tree_parser import TreeSitterParser

        parser = TreeSitterParser()
        assert parser is not None
        assert parser._language_cache == {}
        assert parser._available is None

    def test_is_available(self):
        from rag_pipeline.core.splitter.tree_parser import TreeSitterParser

        parser = TreeSitterParser()
        result = parser.is_available()
        assert isinstance(result, bool)

    def test_get_language_known(self):
        from rag_pipeline.core.splitter.tree_parser import TreeSitterParser

        parser = TreeSitterParser()
        if not parser.is_available():
            pytest.skip("tree-sitter not available")

        lang = parser.get_language("python")
        assert lang is not None

    def test_get_language_unknown(self):
        from rag_pipeline.core.splitter.tree_parser import TreeSitterParser

        parser = TreeSitterParser()
        lang = parser.get_language("brainfuck")
        assert lang is None

    def test_parse(self):
        from rag_pipeline.core.splitter.tree_parser import TreeSitterParser

        parser = TreeSitterParser()
        if not parser.is_available():
            pytest.skip("tree-sitter not available")

        tree = parser.parse("def foo(): pass", "python")
        assert tree is not None

    def test_parse_with_unknown_language(self):
        from rag_pipeline.core.splitter.tree_parser import TreeSitterParser

        parser = TreeSitterParser()
        tree = parser.parse("def foo(): pass", "brainfuck")
        assert tree is None

    def test_clear_cache(self):
        from rag_pipeline.core.splitter.tree_parser import TreeSitterParser

        parser = TreeSitterParser()
        parser._language_cache["test"] = "dummy"
        parser.clear_cache()
        assert parser._language_cache == {}


# ─────────────────────────────────────────────────────────────
# get_parser singleton
# ─────────────────────────────────────────────────────────────
class TestGetParser:

    def test_get_parser_returns_instance(self):
        from rag_pipeline.core.splitter.tree_parser import get_parser

        p = get_parser()
        assert p is not None

    def test_get_parser_is_singleton(self):
        from rag_pipeline.core.splitter.tree_parser import get_parser

        p1 = get_parser()
        p2 = get_parser()
        assert p1 is p2


# ─────────────────────────────────────────────────────────────
# Language mapping (TREESITTER_MODULES from languages.py)
# ─────────────────────────────────────────────────────────────
class TestLanguageMapping:

    def test_treesitter_modules_has_python(self):
        from rag_pipeline.core.splitter.languages import TREESITTER_MODULES

        assert "python" in TREESITTER_MODULES

    def test_treesitter_modules_has_common_langs(self):
        from rag_pipeline.core.splitter.languages import TREESITTER_MODULES

        for lang in ("python", "javascript", "typescript", "java"):
            assert lang in TREESITTER_MODULES, f"{lang} should be in TREESITTER_MODULES"
