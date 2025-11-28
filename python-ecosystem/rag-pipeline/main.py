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
load_dotenv()

# Validate critical environment variables before starting
def validate_environment():
    """Validate that required environment variables are set"""
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")

    logger.info("=" * 60)
    logger.info("RAG Pipeline Starting - Environment Check")
    logger.info("=" * 60)
    logger.info(f"QDRANT_URL: {os.getenv('QDRANT_URL', 'NOT SET')}")
    logger.info(f"QDRANT_COLLECTION_PREFIX: {os.getenv('QDRANT_COLLECTION_PREFIX', 'codecrow')}")
    logger.info(f"OPENROUTER_MODEL: {os.getenv('OPENROUTER_MODEL', 'openai/text-embedding-3-small')}")

    if not openrouter_key or openrouter_key.strip() == "":
        logger.error("=" * 60)
        logger.error("CRITICAL ERROR: OPENROUTER_API_KEY not set!")
        logger.error("=" * 60)
        logger.error("The OPENROUTER_API_KEY environment variable is required")
        logger.error("but was not found or is empty.")
        logger.error("")
        logger.error("To fix this:")
        logger.error("1. Set the environment variable:")
        logger.error("   export OPENROUTER_API_KEY='sk-or-v1-...'")
        logger.error("2. Or add it to docker-compose.yml")
        logger.error("3. Or create a .env file with: OPENROUTER_API_KEY=sk-or-v1-...")
        logger.error("=" * 60)
        sys.exit(1)

    logger.info(f"OPENROUTER_API_KEY: {openrouter_key[:15]}...{openrouter_key[-4:]} ✓")
    logger.info("=" * 60)
    logger.info("Environment validation passed ✓")
    logger.info("Using Qdrant for vector storage")
    logger.info("=" * 60)

# Validate before importing app
validate_environment()

import uvicorn
from rag_pipeline.api.api import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)

