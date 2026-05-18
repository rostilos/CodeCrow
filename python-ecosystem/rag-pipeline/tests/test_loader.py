"""
Unit tests for rag_pipeline.core.loader — DocumentLoader, _is_generated_asset.
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from rag_pipeline.core.loader import DocumentLoader, _is_generated_asset
from rag_pipeline.models.config import RAGConfig


class TestIsGeneratedAsset:

    @pytest.mark.parametrize("filename,expected", [
        ("index-D25HpPdh.js", True),
        ("main.a1b2c3d4.css", True),
        ("vendor~lib.9fca3e7.mjs", True),  # 7-char mixed hash
        ("chunk-AbC12dEf.js", True),
        # Not generated: no mixed alpha+digit hash
        ("index.js", False),
        ("main.css", False),
        ("utils.py", False),
        # Not generated: hash is all digits or all letters
        ("index-12345678.js", False),
        ("index-abcdefgh.js", False),
        # Not generated: wrong extension
        ("data-a1b2c3d4.json", False),
        # Short hash (< 7 chars)
        ("index-a1b2.js", False),
    ])
    def test_detection(self, filename, expected):
        assert _is_generated_asset(filename) is expected


class TestDocumentLoaderIterFiles:

    def test_yields_python_files(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "util.py").write_text("x = 1")
        config = RAGConfig()
        loader = DocumentLoader(config)

        files = list(loader.iter_repository_files(tmp_path))
        assert len(files) == 2

    def test_excludes_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module.exports = {}")
        (tmp_path / "main.py").write_text("x = 1")
        config = RAGConfig()
        loader = DocumentLoader(config)

        files = list(loader.iter_repository_files(tmp_path))
        paths = [str(f) for f in files]
        assert not any("node_modules" in p for p in paths)
        assert len(files) == 1

    def test_excludes_binary_files(self, tmp_path):
        (tmp_path / "image.bin").write_bytes(b"\x00\x01\x02")
        (tmp_path / "main.py").write_text("x = 1")
        config = RAGConfig()
        loader = DocumentLoader(config)

        files = list(loader.iter_repository_files(tmp_path))
        assert len(files) == 1

    def test_excludes_oversized_files(self, tmp_path):
        config = RAGConfig(max_file_size_bytes=100)
        (tmp_path / "big.py").write_text("x" * 200)
        (tmp_path / "small.py").write_text("x = 1")
        loader = DocumentLoader(config)

        files = list(loader.iter_repository_files(tmp_path))
        assert len(files) == 1

    def test_include_patterns_filter(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x = 1")
        (tmp_path / "lib").mkdir()
        (tmp_path / "lib" / "util.py").write_text("y = 2")
        config = RAGConfig()
        loader = DocumentLoader(config)

        files = list(loader.iter_repository_files(tmp_path, extra_include_patterns=["src/**"]))
        paths = [str(f) for f in files]
        assert any("main.py" in p for p in paths)
        # lib/util.py should be excluded by the include filter
        assert not any("util.py" in p for p in paths)

    def test_extra_exclude_patterns(self, tmp_path):
        (tmp_path / "vendor").mkdir()
        (tmp_path / "vendor" / "lib.php").write_text("<?php")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.php").write_text("<?php")
        config = RAGConfig()
        loader = DocumentLoader(config)

        files = list(loader.iter_repository_files(tmp_path, extra_exclude_patterns=["vendor/**"]))
        paths = [str(f) for f in files]
        assert not any("vendor" in p for p in paths)

    def test_skips_generated_assets(self, tmp_path):
        (tmp_path / "index-D25HpPdh.js").write_text("bundled code")
        (tmp_path / "app.js").write_text("real code")
        config = RAGConfig()
        loader = DocumentLoader(config)

        files = list(loader.iter_repository_files(tmp_path))
        names = [f.name for f in files]
        assert "index-D25HpPdh.js" not in names
        assert "app.js" in names

    def test_nonexistent_path(self, tmp_path):
        config = RAGConfig()
        loader = DocumentLoader(config)
        files = list(loader.iter_repository_files(tmp_path / "nonexistent"))
        assert files == []


class TestDocumentLoaderLoadBatch:

    def test_loads_documents_with_metadata(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hello')")
        config = RAGConfig()
        loader = DocumentLoader(config)

        docs = loader.load_file_batch(
            [Path("main.py")],
            repo_base=tmp_path,
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc123",
        )
        assert len(docs) == 1
        assert docs[0].metadata["workspace"] == "ws"
        assert docs[0].metadata["project"] == "proj"
        assert docs[0].metadata["branch"] == "main"
        assert docs[0].metadata["language"] == "python"
        assert "print" in docs[0].text

    def test_skips_empty_files(self, tmp_path):
        (tmp_path / "empty.py").write_text("")
        config = RAGConfig()
        loader = DocumentLoader(config)

        docs = loader.load_file_batch(
            [Path("empty.py")],
            repo_base=tmp_path,
            workspace="ws", project="proj", branch="main", commit="abc",
        )
        assert len(docs) == 0

    def test_skips_binary_decode_errors(self, tmp_path):
        (tmp_path / "bad.py").write_bytes(b"\x80\x81\x82\x83")
        config = RAGConfig()
        loader = DocumentLoader(config)

        docs = loader.load_file_batch(
            [Path("bad.py")],
            repo_base=tmp_path,
            workspace="ws", project="proj", branch="main", commit="abc",
        )
        assert len(docs) == 0

    def test_cleans_archive_path(self, tmp_path):
        archive_dir = tmp_path / "owner-repo-commitabc123"
        src_dir = archive_dir / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "main.py").write_text("x = 1")

        config = RAGConfig()
        loader = DocumentLoader(config)

        docs = loader.load_file_batch(
            [Path("owner-repo-commitabc123/src/main.py")],
            repo_base=tmp_path,
            workspace="ws", project="proj", branch="main", commit="abc",
        )
        assert len(docs) == 1
        assert docs[0].metadata["path"] == "src/main.py"
