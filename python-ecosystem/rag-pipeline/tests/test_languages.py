"""
Unit tests for rag_pipeline.core.splitter.languages module.
"""
import pytest
from langchain_text_splitters import Language

from rag_pipeline.core.splitter.languages import (
    EXTENSION_TO_LANGUAGE,
    AST_SUPPORTED_LANGUAGES,
    LANGUAGE_TO_TREESITTER,
    TREESITTER_MODULES,
    get_language_from_path,
    get_treesitter_name,
    is_ast_supported,
    get_supported_languages,
)


class TestGetLanguageFromPath:

    @pytest.mark.parametrize("path,expected", [
        ("main.py", Language.PYTHON),
        ("main.pyi", Language.PYTHON),
        ("App.java", Language.JAVA),
        ("app.js", Language.JS),
        ("app.jsx", Language.JS),
        ("app.mjs", Language.JS),
        ("app.ts", Language.TS),
        ("app.tsx", Language.TS),
        ("main.go", Language.GO),
        ("lib.rs", Language.RUST),
        ("main.c", Language.C),
        ("main.h", Language.C),
        ("main.cpp", Language.CPP),
        ("main.cc", Language.CPP),
        ("main.hpp", Language.CPP),
        ("Program.cs", Language.CSHARP),
        ("index.php", Language.PHP),
        ("app.rb", Language.RUBY),
        ("main.kt", Language.KOTLIN),
        ("App.scala", Language.SCALA),
        ("script.lua", Language.LUA),
        ("script.pl", Language.PERL),
        ("app.swift", Language.SWIFT),
        ("README.md", Language.MARKDOWN),
        ("index.html", Language.HTML),
        ("doc.rst", Language.RST),
        ("doc.proto", Language.PROTO),
    ])
    def test_known_extensions(self, path, expected):
        assert get_language_from_path(path) == expected

    def test_unknown_extension_returns_none(self):
        assert get_language_from_path("file.xyz") is None
        assert get_language_from_path("Makefile") is None

    def test_case_insensitive(self):
        # Path suffix is lowered
        assert get_language_from_path("FILE.PY") == Language.PYTHON

    def test_nested_path(self):
        assert get_language_from_path("src/main/java/App.java") == Language.JAVA


class TestIsASTSupported:

    @pytest.mark.parametrize("path", [
        "main.py", "App.java", "app.js", "app.ts", "main.go",
        "lib.rs", "main.c", "main.cpp", "Program.cs", "index.php", "app.rb",
    ])
    def test_supported_languages(self, path):
        assert is_ast_supported(path) is True

    @pytest.mark.parametrize("path", [
        "README.md", "config.json", "style.css", "index.html", "unknown.xyz",
    ])
    def test_unsupported_languages(self, path):
        assert is_ast_supported(path) is False


class TestGetTreesitterName:

    def test_python(self):
        assert get_treesitter_name(Language.PYTHON) == "python"

    def test_java(self):
        assert get_treesitter_name(Language.JAVA) == "java"

    def test_typescript(self):
        assert get_treesitter_name(Language.TS) == "typescript"

    def test_csharp(self):
        assert get_treesitter_name(Language.CSHARP) == "c_sharp"

    def test_unsupported_language_returns_none(self):
        assert get_treesitter_name(Language.MARKDOWN) is None


class TestGetSupportedLanguages:

    def test_returns_list(self):
        langs = get_supported_languages()
        assert isinstance(langs, list)
        assert len(langs) > 0
        assert "python" in langs
        assert "java" in langs

    def test_all_treesitter_languages_covered(self):
        langs = get_supported_languages()
        for ts_name in LANGUAGE_TO_TREESITTER.values():
            assert ts_name in langs


class TestTreesitterModules:

    def test_all_have_module_and_function(self):
        for ts_name, (module, func) in TREESITTER_MODULES.items():
            assert isinstance(module, str)
            assert isinstance(func, str)
            assert module.startswith("tree_sitter_")

    def test_key_languages_present(self):
        assert "python" in TREESITTER_MODULES
        assert "java" in TREESITTER_MODULES
        assert "javascript" in TREESITTER_MODULES
        assert "typescript" in TREESITTER_MODULES
        assert "go" in TREESITTER_MODULES
        assert "rust" in TREESITTER_MODULES
        assert "php" in TREESITTER_MODULES
        assert "c_sharp" in TREESITTER_MODULES


class TestConsistency:
    """Cross-check that all maps are consistent."""

    def test_ast_supported_has_treesitter_mapping(self):
        for lang in AST_SUPPORTED_LANGUAGES:
            if lang in LANGUAGE_TO_TREESITTER:
                ts_name = LANGUAGE_TO_TREESITTER[lang]
                # Not all languages have modules (e.g., kotlin, scala, haskell)
                # but core ones should
                if ts_name in TREESITTER_MODULES:
                    module, _ = TREESITTER_MODULES[ts_name]
                    assert module is not None
