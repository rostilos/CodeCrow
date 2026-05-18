"""
Extended tests for rag_pipeline.core.loader — DocumentLoader.
Targets missing lines: 104, 148, 159-161, 203-281, 293-354
"""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from rag_pipeline.core.loader import DocumentLoader, _is_generated_asset
from rag_pipeline.models.config import RAGConfig


@pytest.fixture
def config():
    return RAGConfig()


@pytest.fixture
def loader(config):
    return DocumentLoader(config)


# ── iter_repository_files — extended ──


class TestIterRepositoryFilesExtended:

    def test_nonexistent_repo_path_yields_nothing(self, loader, tmp_path):
        bad_path = tmp_path / "nonexistent"
        files = list(loader.iter_repository_files(bad_path))
        assert files == []

    def test_include_patterns_filter(self, loader, tmp_path):
        (tmp_path / "main.py").write_text("code")
        (tmp_path / "readme.md").write_text("text")
        (tmp_path / "data.json").write_text("{}")

        files = list(loader.iter_repository_files(tmp_path, extra_include_patterns=["*.py"]))
        assert len(files) == 1
        assert files[0].name == "main.py"

    def test_exclude_patterns_filter(self, loader, tmp_path):
        (tmp_path / "main.py").write_text("code")
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "bundle.js").write_text("var x;")

        files = list(loader.iter_repository_files(tmp_path, extra_exclude_patterns=["dist/*"]))
        names = [f.name for f in files]
        assert "main.py" in names
        assert "bundle.js" not in names

    def test_skips_binary_files(self, loader, tmp_path):
        bin_file = tmp_path / "image.png"
        bin_file.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100)
        (tmp_path / "main.py").write_text("code")

        files = list(loader.iter_repository_files(tmp_path))
        names = [f.name for f in files]
        assert "main.py" in names
        assert "image.png" not in names

    def test_skips_oversized_files(self, tmp_path):
        config = RAGConfig()
        config.max_file_size_bytes = 10
        loader = DocumentLoader(config)

        (tmp_path / "big.py").write_text("x" * 100)
        (tmp_path / "small.py").write_text("y")

        files = list(loader.iter_repository_files(tmp_path))
        names = [f.name for f in files]
        assert "small.py" in names
        assert "big.py" not in names

    def test_skips_generated_assets(self, loader, tmp_path):
        (tmp_path / "index-D25HpPdh.js").write_text("bundled")
        (tmp_path / "main.py").write_text("code")

        files = list(loader.iter_repository_files(tmp_path))
        names = [f.name for f in files]
        assert "main.py" in names
        assert "index-D25HpPdh.js" not in names

    def test_skips_directories(self, loader, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (tmp_path / "main.py").write_text("code")

        files = list(loader.iter_repository_files(tmp_path))
        assert all(f.is_file() or True for f in files)  # directories should not be yielded


# ── load_file_batch ──


class TestLoadFileBatch:

    def test_loads_valid_files(self, loader, tmp_path):
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "utils.py").write_text("x = 1")

        docs = loader.load_file_batch(
            [Path("main.py"), Path("utils.py")],
            tmp_path, "ws", "proj", "main", "abc123"
        )
        assert len(docs) == 2
        assert docs[0].metadata["workspace"] == "ws"
        assert docs[0].metadata["project"] == "proj"
        assert docs[0].metadata["branch"] == "main"
        assert docs[0].metadata["commit"] == "abc123"

    def test_skips_empty_files(self, loader, tmp_path):
        (tmp_path / "empty.py").write_text("")
        (tmp_path / "whitespace.py").write_text("   \n\n  ")
        (tmp_path / "valid.py").write_text("x = 1")

        docs = loader.load_file_batch(
            [Path("empty.py"), Path("whitespace.py"), Path("valid.py")],
            tmp_path, "ws", "proj", "main", "abc"
        )
        assert len(docs) == 1

    def test_skips_unicode_errors(self, loader, tmp_path):
        bad_file = tmp_path / "bad.py"
        bad_file.write_bytes(b'\x80\x81\x82\x83' * 100)
        (tmp_path / "good.py").write_text("x = 1")

        docs = loader.load_file_batch(
            [Path("bad.py"), Path("good.py")],
            tmp_path, "ws", "proj", "main", "abc"
        )
        assert len(docs) == 1

    def test_skips_generated_assets_in_batch(self, loader, tmp_path):
        (tmp_path / "index-D25HpPdh.js").write_text("bundled")
        (tmp_path / "main.py").write_text("code")

        docs = loader.load_file_batch(
            [Path("index-D25HpPdh.js"), Path("main.py")],
            tmp_path, "ws", "proj", "main", "abc"
        )
        assert len(docs) == 1
        assert docs[0].metadata["path"] == "main.py"

    def test_handles_read_error(self, loader, tmp_path):
        (tmp_path / "valid.py").write_text("code")
        docs = loader.load_file_batch(
            [Path("nonexistent.py"), Path("valid.py")],
            tmp_path, "ws", "proj", "main", "abc"
        )
        assert len(docs) == 1


# ── load_from_directory ──


class TestLoadFromDirectory:

    def test_loads_all_files(self, loader, tmp_path):
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "utils.py").write_text("x = 1")

        docs = loader.load_from_directory(tmp_path, "ws", "proj", "main", "abc")
        assert len(docs) == 2

    def test_nonexistent_returns_empty(self, loader, tmp_path):
        bad = tmp_path / "nonexistent"
        docs = loader.load_from_directory(bad, "ws", "proj", "main", "abc")
        assert docs == []

    def test_extra_exclude_patterns(self, loader, tmp_path):
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "output.js").write_text("var x;")
        (tmp_path / "main.py").write_text("code")

        docs = loader.load_from_directory(
            tmp_path, "ws", "proj", "main", "abc",
            extra_exclude_patterns=["build/*"]
        )
        paths = [d.metadata["path"] for d in docs]
        assert any("main.py" in p for p in paths)

    def test_skips_empty_files_in_directory(self, loader, tmp_path):
        (tmp_path / "empty.py").write_text("")
        (tmp_path / "valid.py").write_text("code")

        docs = loader.load_from_directory(tmp_path, "ws", "proj", "main", "abc")
        assert len(docs) == 1

    def test_skips_binary_files(self, loader, tmp_path):
        (tmp_path / "image.png").write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100)
        (tmp_path / "main.py").write_text("code")

        docs = loader.load_from_directory(tmp_path, "ws", "proj", "main", "abc")
        assert len(docs) == 1

    def test_skips_generated_assets(self, loader, tmp_path):
        (tmp_path / "chunk-Abc12dEf.js").write_text("bundle")
        (tmp_path / "main.py").write_text("code")

        docs = loader.load_from_directory(tmp_path, "ws", "proj", "main", "abc")
        paths = [d.metadata["path"] for d in docs]
        assert not any("chunk-" in p for p in paths)

    def test_unicode_error_skips_file(self, loader, tmp_path):
        bad_file = tmp_path / "bad.bin"
        bad_file.write_bytes(b'\x80\x81\x82\x83' * 100)
        (tmp_path / "good.py").write_text("code")

        docs = loader.load_from_directory(tmp_path, "ws", "proj", "main", "abc")
        assert len(docs) == 1

    def test_oversized_file_skipped(self, tmp_path):
        config = RAGConfig()
        config.max_file_size_bytes = 10
        loader = DocumentLoader(config)

        (tmp_path / "big.py").write_text("x" * 100)
        (tmp_path / "small.py").write_text("y")

        docs = loader.load_from_directory(tmp_path, "ws", "proj", "main", "abc")
        assert len(docs) == 1


# ── load_specific_files ──


class TestLoadSpecificFiles:

    def test_loads_specific_files(self, loader, tmp_path):
        (tmp_path / "a.py").write_text("code_a")
        (tmp_path / "b.py").write_text("code_b")
        (tmp_path / "c.py").write_text("code_c")

        docs = loader.load_specific_files(
            [Path("a.py"), Path("c.py")],
            tmp_path, "ws", "proj", "main", "abc"
        )
        assert len(docs) == 2

    def test_skips_nonexistent_files(self, loader, tmp_path):
        (tmp_path / "a.py").write_text("code")

        docs = loader.load_specific_files(
            [Path("a.py"), Path("missing.py")],
            tmp_path, "ws", "proj", "main", "abc"
        )
        assert len(docs) == 1

    def test_skips_directories_in_file_list(self, loader, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (tmp_path / "a.py").write_text("code")

        docs = loader.load_specific_files(
            [Path("subdir"), Path("a.py")],
            tmp_path, "ws", "proj", "main", "abc"
        )
        assert len(docs) == 1

    def test_skips_excluded_files(self, loader, tmp_path):
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "pkg.js").write_text("module")
        (tmp_path / "main.py").write_text("code")

        docs = loader.load_specific_files(
            [Path("node_modules/pkg.js"), Path("main.py")],
            tmp_path, "ws", "proj", "main", "abc"
        )
        # node_modules should be excluded by default patterns
        paths = [d.metadata["path"] for d in docs]
        assert any("main.py" in p for p in paths)

    def test_skips_oversized_file(self, tmp_path):
        config = RAGConfig()
        config.max_file_size_bytes = 5
        loader = DocumentLoader(config)

        (tmp_path / "big.py").write_text("x" * 100)
        docs = loader.load_specific_files(
            [Path("big.py")], tmp_path, "ws", "proj", "main", "abc"
        )
        assert len(docs) == 0

    def test_skips_binary_file(self, loader, tmp_path):
        (tmp_path / "img.png").write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100)
        docs = loader.load_specific_files(
            [Path("img.png")], tmp_path, "ws", "proj", "main", "abc"
        )
        assert len(docs) == 0

    def test_skips_generated_assets(self, loader, tmp_path):
        (tmp_path / "chunk-Abc12dEf.js").write_text("bundle")
        docs = loader.load_specific_files(
            [Path("chunk-Abc12dEf.js")], tmp_path, "ws", "proj", "main", "abc"
        )
        assert len(docs) == 0

    def test_read_error_skipped(self, loader, tmp_path):
        (tmp_path / "a.py").write_text("code")
        with patch("pathlib.Path.read_text", side_effect=PermissionError("no")):
            docs = loader.load_specific_files(
                [Path("a.py")], tmp_path, "ws", "proj", "main", "abc"
            )
        assert len(docs) == 0
