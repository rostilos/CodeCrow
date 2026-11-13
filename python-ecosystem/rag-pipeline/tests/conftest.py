"""
Shared test fixtures for RAG pipeline unit tests.
"""
import pytest
from rag_pipeline.models.config import RAGConfig


@pytest.fixture
def rag_config():
    """Default RAGConfig for testing."""
    return RAGConfig()
