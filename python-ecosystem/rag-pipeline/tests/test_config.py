"""Unit tests for structural repository-index configuration."""

import os
from unittest.mock import patch

import pytest

from rag_pipeline.models.config import IndexStats, RAGConfig


class TestRAGConfig:
    def test_default_values(self):
        # Other test modules import ``main`` during collection, which loads the
        # deployment .env. Defaults must be asserted independently of that
        # process-wide production configuration.
        with patch.dict(os.environ, {}, clear=True):
            config = RAGConfig()
        assert config.structural_index_root == "/var/lib/codecrow/structural-index"
        assert config.full_index_concurrency == 1
        assert config.architecture_finalization_timeout_seconds == 600
        assert config.review_generation_ttl_seconds == 21600
        assert config.max_file_size_bytes == 512 * 1024
        assert config.max_files_per_index == 50000
        assert config.chunk_size == 8000
        assert config.chunk_overlap == 200
        assert config.max_chunks_per_index == 1_000_000

    def test_structural_index_root_is_configurable(self):
        with patch.dict(
            os.environ,
            {"STRUCTURAL_INDEX_ROOT": "/tmp/codecrow-structural-test"},
        ):
            assert (
                RAGConfig().structural_index_root
                == "/tmp/codecrow-structural-test"
            )

    def test_index_concurrency_is_configurable(self):
        with patch.dict(os.environ, {"RAG_FULL_INDEX_CONCURRENCY": "2"}):
            config = RAGConfig()
            assert config.full_index_concurrency == 2

    def test_review_generation_ttl_is_configurable(self):
        with patch.dict(
            os.environ,
            {"RAG_REVIEW_GENERATION_TTL_SECONDS": "7200"},
        ):
            assert RAGConfig().review_generation_ttl_seconds == 7200

    def test_max_file_size_is_configurable(self):
        with patch.dict(os.environ, {"RAG_MAX_FILE_SIZE_BYTES": "262144"}):
            assert RAGConfig().max_file_size_bytes == 262144

    @pytest.mark.parametrize("configured", [0, -1])
    def test_max_file_size_must_be_positive(self, configured):
        with pytest.raises(ValueError, match="greater than or equal to 1"):
            RAGConfig(max_file_size_bytes=configured)

    def test_excluded_patterns_defaults(self):
        config = RAGConfig()
        assert "node_modules/**" in config.excluded_patterns
        assert ".git/**" in config.excluded_patterns
        assert "*.min.js" in config.excluded_patterns


class TestIndexStats:
    def test_round_trip(self):
        stats = IndexStats(
            namespace="ws__proj__main",
            document_count=100,
            chunk_count=500,
            last_updated="2026-01-01T00:00:00",
            workspace="ws",
            project="proj",
            branch="main",
        )
        restored = IndexStats(**stats.model_dump())
        assert restored.namespace == "ws__proj__main"
        assert restored.chunk_count == 500
