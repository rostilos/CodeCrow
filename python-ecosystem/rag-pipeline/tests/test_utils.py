"""
Comprehensive unit tests for rag_pipeline.utils.utils module.
Covers: detect_language_from_path, make_namespace, make_project_namespace,
        clean_archive_path, should_include_file, should_exclude_file, is_binary_file.
"""
import os
import tempfile
import pytest
from pathlib import Path

from rag_pipeline.utils.utils import (
    detect_language_from_path,
    make_namespace,
    make_project_namespace,
    clean_archive_path,
    should_include_file,
    should_exclude_file,
    is_binary_file,
    LANGUAGE_MAP,
)


# ─── detect_language_from_path ────────────────────────────────────────────────


class TestDetectLanguage:

    @pytest.mark.parametrize("path,expected", [
        ("main.py", "python"),
        ("app.js", "javascript"),
        ("app.jsx", "javascript"),
        ("App.tsx", "typescript"),
        ("App.ts", "typescript"),
        ("Main.java", "java"),
        ("Main.kt", "kotlin"),
        ("index.php", "php"),
        ("page.phtml", "php"),
        ("main.go", "go"),
        ("lib.rs", "rust"),
        ("util.cpp", "cpp"),
        ("util.cc", "cpp"),
        ("util.hpp", "cpp"),
        ("main.c", "c"),
        ("header.h", "c"),
        ("app.rb", "ruby"),
        ("Program.cs", "csharp"),
        ("app.swift", "swift"),
        ("file.m", "objective-c"),
        ("App.scala", "scala"),
        ("script.sh", "bash"),
        ("script.bash", "bash"),
        ("script.zsh", "zsh"),
        ("query.sql", "sql"),
        ("analysis.r", "r"),
        ("analysis.R", "r"),
        ("mod.lua", "lua"),
        ("mod.pl", "perl"),
        ("README.md", "markdown"),
        ("doc.rst", "rst"),
        ("notes.txt", "text"),
        ("data.json", "json"),
        ("config.xml", "xml"),
        ("config.yaml", "yaml"),
        ("config.yml", "yaml"),
        ("config.toml", "toml"),
        ("config.ini", "ini"),
        ("server.conf", "config"),
        ("index.html", "html"),
        ("page.htm", "html"),
        ("styles.css", "css"),
        ("styles.scss", "scss"),
        ("styles.sass", "sass"),
        ("App.vue", "vue"),
        ("App.svelte", "svelte"),
    ])
    def test_known_extensions(self, path, expected):
        assert detect_language_from_path(path) == expected

    def test_unknown_extension_returns_text(self):
        assert detect_language_from_path("file.xyz") == "text"
        assert detect_language_from_path("file.abc") == "text"
        assert detect_language_from_path("Makefile") == "text"

    def test_case_insensitive_extension(self):
        assert detect_language_from_path("FILE.PY") == "python"
        assert detect_language_from_path("APP.JS") == "javascript"
        assert detect_language_from_path("DATA.JSON") == "json"

    def test_nested_path(self):
        assert detect_language_from_path("src/main/java/App.java") == "java"
        assert detect_language_from_path("deep/nested/dir/script.py") == "python"

    def test_all_language_map_entries_covered(self):
        """Every entry in LANGUAGE_MAP should return its value."""
        for ext, lang in LANGUAGE_MAP.items():
            assert detect_language_from_path(f"test{ext}") == lang


# ─── make_namespace / make_project_namespace ──────────────────────────────────


class TestNamespaces:

    def test_basic_namespace(self):
        assert make_namespace("ws", "proj", "main") == "ws__proj__main"

    def test_namespace_replaces_slashes(self):
        assert make_namespace("ws", "proj", "feature/new") == "ws__proj__feature_new"

    def test_namespace_replaces_dots(self):
        assert make_namespace("ws", "proj.x", "v1.0") == "ws__proj_x__v1_0"

    def test_namespace_lowercased(self):
        assert make_namespace("WS", "PROJ", "MAIN") == "ws__proj__main"

    def test_project_namespace_no_branch(self):
        assert make_project_namespace("ws", "proj") == "ws__proj"

    def test_project_namespace_replaces_special(self):
        assert make_project_namespace("my/ws", "proj.x") == "my_ws__proj_x"


# ─── clean_archive_path ──────────────────────────────────────────────────────


class TestCleanArchivePath:

    def test_empty_path(self):
        assert clean_archive_path("") == ""
        assert clean_archive_path(None) is None

    def test_single_component_unchanged(self):
        assert clean_archive_path("file.py") == "file.py"

    def test_source_marker_first_part_unchanged(self):
        for marker in ["src", "lib", "app", "test", "tests", "pkg", "cmd"]:
            path = f"{marker}/some/file.py"
            assert clean_archive_path(path) == path

    def test_strips_bitbucket_archive_prefix(self):
        # owner-repo-commitHash pattern (>20 chars, hyphens)
        assert clean_archive_path("owner-repo-abc123def456/src/main.py") == "src/main.py"

    def test_strips_long_commit_hash_prefix(self):
        # 40+ char prefix
        long_hash = "a" * 45
        assert clean_archive_path(f"{long_hash}/lib/util.py") == "lib/util.py"

    def test_strips_multi_hyphen_with_digits(self):
        assert clean_archive_path("my-repo-v2-abc123/src/file.py") == "src/file.py"

    def test_preserves_normal_paths(self):
        assert clean_archive_path("normal/path/file.py") == "normal/path/file.py"
        assert clean_archive_path("mymodule/subdir/test.js") == "mymodule/subdir/test.js"


# ─── should_include_file ─────────────────────────────────────────────────────


class TestShouldIncludeFile:

    def test_empty_patterns_includes_all(self):
        assert should_include_file("any/file.py", []) is True

    def test_globstar_pattern(self):
        assert should_include_file("src/main/App.java", ["src/**"]) is True
        assert should_include_file("src/deep/nested/App.java", ["src/**"]) is True
        assert should_include_file("lib/main.py", ["src/**"]) is False

    def test_extension_pattern(self):
        assert should_include_file("src/main.py", ["*.py"]) is True
        assert should_include_file("src/main.js", ["*.py"]) is False

    def test_globstar_extension_pattern(self):
        assert should_include_file("src/deep/file.py", ["**/*.py"]) is True
        assert should_include_file("file.py", ["**/*.py"]) is True
        assert should_include_file("file.js", ["**/*.py"]) is False

    def test_directory_prefix_pattern(self):
        assert should_include_file("src/file.py", ["src/"]) is True
        assert should_include_file("lib/file.py", ["src/"]) is False

    def test_multiple_patterns_or_logic(self):
        patterns = ["src/**", "*.py"]
        assert should_include_file("src/main.java", patterns) is True
        assert should_include_file("lib/util.py", patterns) is True
        assert should_include_file("lib/util.java", patterns) is False

    def test_archive_root_handling(self):
        """Paths with archive root prefix should still match after stripping."""
        assert should_include_file("owner-repo-hash/src/file.py", ["src/**"]) is True


# ─── should_exclude_file ─────────────────────────────────────────────────────


class TestShouldExcludeFile:

    def test_globstar_exclude(self):
        assert should_exclude_file("node_modules/pkg/index.js", ["node_modules/**"]) is True
        assert should_exclude_file("src/main.js", ["node_modules/**"]) is False

    def test_extension_exclude(self):
        assert should_exclude_file("app.min.js", ["*.min.js"]) is True
        assert should_exclude_file("app.js", ["*.min.js"]) is False

    def test_directory_prefix_exclude(self):
        assert should_exclude_file("vendor/lib/file.php", ["vendor/"]) is True
        assert should_exclude_file("src/vendor_utils.py", ["vendor/"]) is False

    def test_globstar_suffix_pattern(self):
        assert should_exclude_file("some/dir/bundle.min.css", ["**/*.min.css"]) is True

    def test_archive_root_prefix(self):
        assert should_exclude_file("repo-hash123/node_modules/pkg.js", ["node_modules/**"]) is True

    def test_real_default_patterns(self):
        from rag_pipeline.models.config import RAGConfig
        config = RAGConfig()
        patterns = config.excluded_patterns

        assert should_exclude_file("node_modules/express/index.js", patterns) is True
        assert should_exclude_file(".venv/lib/site.py", patterns) is True
        assert should_exclude_file("__pycache__/mod.pyc", patterns) is True
        assert should_exclude_file("dist/bundle.js", patterns) is True
        assert should_exclude_file("package-lock.json", patterns) is True
        assert should_exclude_file("src/main.py", patterns) is False
        assert should_exclude_file("README.md", patterns) is False


# ─── is_binary_file ──────────────────────────────────────────────────────────


class TestIsBinaryFile:

    def test_text_file_not_binary(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("print('hello')")
        assert is_binary_file(f) is False

    def test_binary_file_detected(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"\x00\x01\x02\x03\xff\xfe")
        assert is_binary_file(f) is True

    def test_nonexistent_file_returns_true(self, tmp_path):
        f = tmp_path / "nonexistent.txt"
        assert is_binary_file(f) is True

    def test_empty_file_not_binary(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        assert is_binary_file(f) is False
