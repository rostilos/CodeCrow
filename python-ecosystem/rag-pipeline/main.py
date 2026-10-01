"""Main entry point for the structural repository-index API."""

import logging
import os

try:
    from dotenv import load_dotenv
    load_dotenv(interpolate=False)
except Exception as dotenv_error:
    print(f"[ENV-BOOT] ERROR loading .env: {dotenv_error}", flush=True)

# New Relic must be initialized before application imports.
new_relic_config = os.environ.get("NEW_RELIC_CONFIG_FILE")
if new_relic_config and os.path.exists(new_relic_config):
    try:
        import newrelic.agent
        newrelic.agent.initialize(new_relic_config)
    except Exception as new_relic_error:
        print(
            f"[NR-BOOT] ERROR during initialization: {new_relic_error}",
            flush=True,
        )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def validate_environment() -> None:
    """Report the only external store required by the structural index."""
    logger.info("Repository Index starting")
    logger.info(
        "STRUCTURAL_INDEX_ROOT: %s",
        os.getenv(
            "STRUCTURAL_INDEX_ROOT",
            "/var/lib/codecrow/structural-index",
        ),
    )
    logger.info("SQLite structural generation storage configured")


validate_environment()

import uvicorn
from rag_pipeline.models.config import DEFAULT_FULL_INDEX_CONCURRENCY


def effective_uvicorn_workers() -> int:
    """Report process topology without rewriting per-process index capacity."""

    raw = os.environ.get("UVICORN_WORKERS", "1")
    try:
        requested = max(1, int(raw))
    except (TypeError, ValueError):
        logger.warning("Invalid UVICORN_WORKERS=%r; using one worker", raw)
        requested = 1

    raw_capacity = os.environ.get("RAG_FULL_INDEX_CONCURRENCY", str(DEFAULT_FULL_INDEX_CONCURRENCY))
    try:
        # Match the manager's minimum-one normalization without changing the
        # value inherited by application workers.
        per_process_capacity = max(1, int(raw_capacity))
    except (TypeError, ValueError):
        # RAGConfig owns validation when each application worker starts. Do
        # not silently alter the supplied value or advertise a capacity that
        # those workers cannot use.
        logger.warning(
            "Invalid RAG_FULL_INDEX_CONCURRENCY=%r; worker configuration "
            "requires an integer",
            raw_capacity,
        )
    else:
        logger.info(
            "Repository-index capacity: %s slot(s) per process across %s "
            "Uvicorn worker process(es), %s total slot(s) for full/delta builds",
            per_process_capacity,
            requested,
            requested * per_process_capacity,
        )
    return requested


if __name__ == "__main__":
    workers = effective_uvicorn_workers()
    logger.info("Starting Uvicorn with %s worker process(es)", workers)
    uvicorn.run(
        "rag_pipeline.api.api:app",
        host="0.0.0.0",
        port=8001,
        workers=workers,
        interface="asgi3",
    )
