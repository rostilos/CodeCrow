"""
Main entry point for the RAG Pipeline API server
"""
import os
import sys
import logging
from dotenv import load_dotenv

# Configure logging early
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
load_dotenv(interpolate=False)

# In community/self-hosted mode, fetch embedding config from the Java web-server
# before reading env vars. This is a no-op if CODECROW_WEB_SERVER_URL is not set.
from rag_pipeline.config_poller import fetch_and_apply_settings
fetch_and_apply_settings()

# Validate critical environment variables before starting
def validate_environment():
    """Validate that required environment variables are set"""
    embedding_provider = os.environ.get("EMBEDDING_PROVIDER", "ollama").lower()
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")

    logger.info("=" * 60)
    logger.info("RAG Pipeline Starting - Environment Check")
    logger.info("=" * 60)
    logger.info(f"QDRANT_URL: {os.getenv('QDRANT_URL', 'http://qdrant:6333')}")
    logger.info(f"QDRANT_COLLECTION_PREFIX: {os.getenv('QDRANT_COLLECTION_PREFIX', 'codecrow')}")
    logger.info(f"EMBEDDING_PROVIDER: {embedding_provider}")

    if embedding_provider == "ollama":
        ollama_url = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
        ollama_model = os.getenv('OLLAMA_EMBEDDING_MODEL', 'qwen3-embedding:0.6b')
        logger.info(f"OLLAMA_BASE_URL: {ollama_url}")
        logger.info(f"OLLAMA_EMBEDDING_MODEL: {ollama_model}")
        logger.info("=" * 60)
        logger.info("Using Ollama for local embeddings ✓")
        logger.info("Make sure Ollama is running: ollama serve")
        logger.info(f"And model is pulled: ollama pull {ollama_model}")
        logger.info("=" * 60)
    elif embedding_provider == "openrouter":
        logger.info(f"OPENROUTER_MODEL: {os.getenv('OPENROUTER_MODEL', 'qwen/qwen3-embedding-8b')}")

        if not openrouter_key or openrouter_key.strip() == "":
            logger.error("=" * 60)
            logger.error("CRITICAL ERROR: OPENROUTER_API_KEY not set!")
            logger.error("=" * 60)
            logger.error("The OPENROUTER_API_KEY environment variable is required")
            logger.error("when EMBEDDING_PROVIDER=openrouter but was not found or is empty.")
            logger.error("")
            logger.error("To fix this:")
            logger.error("1. Set the environment variable:")
            logger.error("   export OPENROUTER_API_KEY='sk-or-v1-...'")
            logger.error("2. Or add it to docker-compose.yml")
            logger.error("3. Or create a .env file with: OPENROUTER_API_KEY=sk-or-v1-...")
            logger.error("4. Or switch to local embeddings: EMBEDDING_PROVIDER=ollama")
            logger.error("=" * 60)
            sys.exit(1)

        logger.info(f"OPENROUTER_API_KEY: {openrouter_key[:15]}...{openrouter_key[-4:]} ✓")
        logger.info("=" * 60)
        logger.info("Using OpenRouter for cloud embeddings ✓")
        logger.info("=" * 60)
    else:
        logger.warning(f"Unknown EMBEDDING_PROVIDER '{embedding_provider}', defaulting to 'ollama'")

    logger.info("Environment validation passed ✓")
    logger.info("Using Qdrant for vector storage")
    logger.info("=" * 60)

# Validate before importing app
validate_environment()

import uvicorn
from rag_pipeline.api.api import app

if __name__ == "__main__":
    # Use multiple workers to allow concurrent indexing requests
    # Each worker can handle one long-running indexing task
    workers = int(os.environ.get("UVICORN_WORKERS", "4"))
    logger.info(f"Starting Uvicorn with {workers} workers for concurrent request handling")
    uvicorn.run(
        "rag_pipeline.api.api:app",
        host="0.0.0.0",
        port=8001,
        workers=workers
    )

