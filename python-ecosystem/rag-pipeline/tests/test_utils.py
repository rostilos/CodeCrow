"""
Comprehensive unit tests for rag_pipeline.utils.utils module.
Covers: make_namespace, should_include_file,
        should_exclude_file, is_binary_file.
"""
import os
import tempfile
import pytest
from pathlib import Path

from rag_pipeline.utils.utils import (
    make_namespace,
    should_include_file,
    should_exclude_file,
    is_binary_file,
)


# ─── make_namespace ──────────────────────────────────────────────────────────


class TestNamespaces:

    def test_basic_namespace(self):
        assert make_namespace("ws", "proj", "main") == "ws__proj__main"

    def test_namespace_replaces_slashes(self):
        assert make_namespace("ws", "proj", "feature/new") == "ws__proj__feature_new"

    def test_namespace_replaces_dots(self):
        assert make_namespace("ws", "proj.x", "v1.0") == "ws__proj_x__v1_0"

    def test_namespace_lowercased(self):
        assert make_namespace("WS", "PROJ", "MAIN") == "ws__proj__main"

# ─── should_include_file ─────────────────────────────────────────────────────


class TestShouldIncludeFile:

    def test_empty_patterns_includes_all(self):
        assert should_include_file("any/file.py", []) is True

    def test_globstar_pattern(self):
        assert should_include_file("src/main/App.java", ["src/**"]) is True
        assert should_include_file("src/deep/nested/App.java", ["src/**"]) is True
        assert should_include_file("lib/main.py", ["src/**"]) is False

    def test_single_star_does_not_cross_directory_boundaries(self):
        assert should_include_file("src/App.java", ["src/*"]) is True
        assert should_include_file("src/main/App.java", ["src/*"]) is False

    def test_globstar_keeps_the_required_suffix(self):
        pattern = "packages/app-store/**/lib/*.ts"
        assert should_include_file(
            "packages/app-store/vital/lib/reschedule.ts",
            [pattern],
        ) is True
        assert should_include_file(
            "packages/app-store/vital/static/reschedule.ts",
            [pattern],
        ) is False

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

    def test_single_star_exclude_does_not_cross_directory_boundaries(self):
        assert should_exclude_file("src/main.js", ["src/*"]) is True
        assert should_exclude_file("src/lib/main.js", ["src/*"]) is False

    def test_extension_exclude(self):
        assert should_exclude_file("app.min.js", ["*.min.js"]) is True
        assert should_exclude_file("app.js", ["*.min.js"]) is False

    def test_directory_prefix_exclude(self):
        assert should_exclude_file("vendor/lib/file.php", ["vendor/"]) is True
        assert should_exclude_file("src/vendor_utils.py", ["vendor/"]) is False

    def test_globstar_suffix_pattern(self):
        assert should_exclude_file("some/dir/bundle.min.css", ["**/*.min.css"]) is True
        assert should_exclude_file("bundle.min.css", ["**/*.min.css"]) is True

    def test_nested_globstar_exclusion_requires_its_suffix_directory(self):
        patterns = [
            "packages/app-store/**/static/**",
            "packages/app-store/**/public/**",
        ]
        assert should_exclude_file(
            "packages/app-store/vital/static/icon.png",
            patterns,
        ) is True
        assert should_exclude_file(
            "packages/app-store/static/icon.png",
            patterns,
        ) is True
        assert should_exclude_file(
            "packages/app-store/vital/public/logo.svg",
            patterns,
        ) is True
        assert should_exclude_file(
            "packages/app-store/_utils/getCalendar.ts",
            patterns,
        ) is False
        assert should_exclude_file(
            "packages/app-store/vital/lib/reschedule.ts",
            patterns,
        ) is False
        assert should_exclude_file(
            "packages/app-store/vital/staticish/icon.svg",
            patterns,
        ) is False

    def test_nested_globstar_file_exclusion_requires_the_terminal_name(self):
        pattern = "packages/prisma/migrations/**/steps.json"
        assert should_exclude_file(
            "packages/prisma/migrations/20230101_init/steps.json",
            [pattern],
        ) is True
        assert should_exclude_file(
            "packages/prisma/migrations/steps.json",
            [pattern],
        ) is True
        assert should_exclude_file(
            "packages/prisma/migrations/20230101_init/migration.sql",
            [pattern],
        ) is False
        assert should_exclude_file(
            "packages/prisma/migrations/20230101_init/steps.json.bak",
            [pattern],
        ) is False

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

    def test_non_utf8_binary_without_nul_is_detected(self, tmp_path):
        f = tmp_path / "document.pdf"
        f.write_bytes(b"%PDF-1.7\r\n%\xb5\xb5\xb5\xb5\r\n1 0 obj\r\n")
        assert is_binary_file(f) is True

    def test_non_utf8_data_after_initial_probe_is_detected(self, tmp_path):
        f = tmp_path / "late-binary.dat"
        f.write_bytes(b"a" * 9000 + b"\xff")
        assert is_binary_file(f) is True

    def test_multibyte_utf8_across_read_boundary_is_text(self, tmp_path):
        f = tmp_path / "unicode.txt"
        f.write_bytes(b"a" * 8191 + "\u20ac".encode("utf-8"))
        assert is_binary_file(f) is False

    def test_nonexistent_file_returns_true(self, tmp_path):
        f = tmp_path / "nonexistent.txt"
        assert is_binary_file(f) is True

    def test_empty_file_not_binary(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        assert is_binary_file(f) is False
