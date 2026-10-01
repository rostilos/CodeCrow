"""
Unit tests for rag_pipeline.core.loader — DocumentLoader, _is_generated_asset.
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from rag_pipeline.core.loader import (
    REPOSITORY_FILE_SIZE_LIMIT_CODE,
    DocumentLoader,
    _is_generated_asset,
)
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

    def test_excludes_non_utf8_binary_without_nul(self, tmp_path):
        (tmp_path / "document.pdf").write_bytes(
            b"%PDF-1.7\r\n%\xb5\xb5\xb5\xb5\r\n1 0 obj\r\n"
        )
        (tmp_path / "main.py").write_text("x = 1")
        config = RAGConfig()
        loader = DocumentLoader(config)

        files = list(loader.iter_repository_files(tmp_path))
        assert files == [Path("main.py")]

    def test_excludes_files_above_configured_limit_without_truncation(
        self, tmp_path, caplog
    ):
        config = RAGConfig(max_file_size_bytes=100)
        (tmp_path / "exact.py").write_bytes(b"x" * 100)
        (tmp_path / "big.py").write_bytes(b"x" * 101)
        (tmp_path / "small.py").write_text("x = 1")
        loader = DocumentLoader(config)
        skips = []

        files = list(loader.iter_repository_files(tmp_path, on_skip=skips.append))
        assert set(files) == {Path("exact.py"), Path("small.py")}
        assert "path=big.py bytes=101 max_bytes=100" in caplog.text
        assert len(skips) == 1
        assert skips[0].code == REPOSITORY_FILE_SIZE_LIMIT_CODE
        assert skips[0].path == "big.py"
        assert skips[0].size_bytes == 101
        assert skips[0].max_file_size_bytes == 100

    def test_file_ceiling_is_measured_in_utf8_bytes(self, tmp_path):
        (tmp_path / "unicode.txt").write_text("é" * 6, encoding="utf-8")
        loader = DocumentLoader(RAGConfig(max_file_size_bytes=10))
        skips = []

        files = list(loader.iter_repository_files(tmp_path, on_skip=skips.append))

        assert files == []
        assert skips[0].size_bytes == 12

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

    def test_rechecks_file_size_at_batch_load(self, tmp_path, caplog):
        (tmp_path / "big.py").write_bytes(b"x" * 101)
        loader = DocumentLoader(RAGConfig(max_file_size_bytes=100))
        skips = []

        docs = loader.load_file_batch(
            [Path("big.py")],
            repo_base=tmp_path,
            workspace="ws", project="proj", branch="main", commit="abc",
            on_skip=skips.append,
        )

        assert docs == []
        assert "path=big.py bytes=101 max_bytes=100" in caplog.text
        assert [skip.path for skip in skips] == ["big.py"]

    def test_preserves_repository_relative_top_level_directories(self, tmp_path):
        paths = (
            Path("payments-platform-component/src/A.py"),
            Path("service-platform-v2/src/A.py"),
        )
        for path in paths:
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / path).write_text(f"component = {str(path)!r}\n")

        config = RAGConfig()
        loader = DocumentLoader(config)

        docs = loader.load_file_batch(
            list(paths),
            repo_base=tmp_path,
            workspace="ws", project="proj", branch="main", commit="abc",
        )
        assert {doc.metadata["path"] for doc in docs} == {
            path.as_posix() for path in paths
        }
