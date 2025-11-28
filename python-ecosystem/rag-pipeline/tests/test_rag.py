"""
Unit tests for RAG pipeline components
"""

import pytest
from pathlib import Path
from rag_pipeline.models.config import RAGConfig, DocumentMetadata
from rag_pipeline.utils.utils import (
    detect_language_from_path,
    make_namespace,
    should_exclude_file,
    is_code_file
)


def test_detect_language():
    """Test language detection"""
    assert detect_language_from_path("main.py") == "python"
    assert detect_language_from_path("app.js") == "javascript"
    assert detect_language_from_path("App.tsx") == "typescript"
    assert detect_language_from_path("Main.java") == "java"
    assert detect_language_from_path("README.md") == "markdown"
    assert detect_language_from_path("unknown.xyz") == "text"


def test_make_namespace():
    """Test namespace creation"""
    ns = make_namespace("codecrow", "demo", "main")
    assert ns == "codecrow__demo__main"

    ns = make_namespace("codecrow", "demo", "feature/new")
    assert ns == "codecrow__demo__feature_new"

    ns = make_namespace("CODECROW", "Demo", "Main")
    assert ns == "codecrow__demo__main"


def test_should_exclude_file():
    """Test file exclusion"""
    config = RAGConfig()

    assert should_exclude_file("node_modules/package/index.js", config.excluded_patterns) == True
    assert should_exclude_file(".venv/lib/python.py", config.excluded_patterns) == True
    assert should_exclude_file("__pycache__/module.pyc", config.excluded_patterns) == True
    assert should_exclude_file("src/main.py", config.excluded_patterns) == False
    assert should_exclude_file("README.md", config.excluded_patterns) == False


def test_is_code_file():
    """Test code file detection"""
    assert is_code_file("python") == True
    assert is_code_file("javascript") == True
    assert is_code_file("java") == True
    assert is_code_file("text") == False
    assert is_code_file("markdown") == False
    assert is_code_file("json") == False


def test_config_defaults():
    """Test default configuration"""
    config = RAGConfig()

    assert config.chunk_size == 800
    assert config.chunk_overlap == 200
    assert config.text_chunk_size == 1000
    assert config.retrieval_top_k == 10
    assert config.similarity_threshold == 0.7


def test_document_metadata():
    """Test document metadata model"""
    metadata = DocumentMetadata(
        workspace="codecrow",
        project="demo",
        branch="main",
        path="src/main.py",
        commit="abc123",
        language="python",
        filetype="py"
    )

    assert metadata.workspace == "codecrow"
    assert metadata.language == "python"
    assert metadata.chunk_index is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

