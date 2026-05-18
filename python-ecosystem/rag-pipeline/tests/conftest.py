"""
Shared test fixtures for RAG pipeline unit tests.
"""
import pytest
from rag_pipeline.models.config import RAGConfig
from rag_pipeline.models.scoring_config import reset_scoring_config


@pytest.fixture(autouse=True)
def _reset_scoring_singleton():
    """Reset scoring config singleton between tests."""
    reset_scoring_config()
    yield
    reset_scoring_config()


@pytest.fixture
def rag_config():
    """Default RAGConfig for testing."""
    return RAGConfig()
