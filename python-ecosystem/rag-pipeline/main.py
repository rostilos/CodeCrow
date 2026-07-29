"""
Main entry point for the RAG Pipeline API server
"""

import os as _os

# Load deployment configuration before importing modules that read env vars at
# import time. Keep this before New Relic and application imports.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(interpolate=False)
except Exception as _dotenv_err:
    print(f"[ENV-BOOT] ERROR loading .env: {_dotenv_err}", flush=True)

# New Relic APM must be initialized before any application imports.
_nr_config = _os.environ.get("NEW_RELIC_CONFIG_FILE")
print(f"[NR-BOOT] NEW_RELIC_CONFIG_FILE = {_nr_config!r}", flush=True)
if _nr_config:
    _nr_exists = _os.path.exists(_nr_config)
    print(f"[NR-BOOT] Config file exists: {_nr_exists}", flush=True)
    if _nr_exists:
        try:
            import newrelic.agent
            print(
                f"[NR-BOOT] newrelic.agent imported, version={newrelic.version}",
                flush=True,
            )
            newrelic.agent.initialize(_nr_config)
            print(
                "[NR-BOOT] newrelic.agent.initialize() completed successfully",
                flush=True,
            )
        except Exception as _nr_err:
            print(f"[NR-BOOT] ERROR during initialization: {_nr_err}", flush=True)
else:
    print("[NR-BOOT] Skipping — no config file env var set", flush=True)

import os
import sys
import logging

# Configure logging early
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# In community/self-hosted mode, fetch embedding config from the Java web-server
# before reading env vars. This is a no-op if CODECROW_WEB_SERVER_URL is not set.
from rag_pipeline.config_poller import fetch_and_apply_settings


def _init_settings():
    """Fetch settings once at startup, before workers fork.
    
    With uvicorn workers>1, the module is re-imported per worker.
    We only need to fetch once since env vars are inherited by workers.
    A lightweight env-var guard prevents duplicate HTTP calls.
    """
    if not os.environ.get("_CODECROW_SETTINGS_FETCHED"):
        fetch_and_apply_settings()
        os.environ["_CODECROW_SETTINGS_FETCHED"] = "1"


_init_settings()

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

        logger.info("OPENROUTER_API_KEY: configured ✓")
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
