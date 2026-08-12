"""
CodeCrow RAG Pipeline API — application entry point.

Creates the FastAPI application, manages singleton lifecycle (startup/shutdown),
and includes all routers. This is the thin orchestration layer.
"""
import logging
import os
import asyncio
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI

from ..models.config import RAGConfig
from ..core.index_manager import RAGIndexManager
from ..services.query_service import RAGQueryService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Lifecycle-managed singletons ---
config: Optional[RAGConfig] = None
index_manager: Optional[RAGIndexManager] = None
query_service: Optional[RAGQueryService] = None

_DEFAULT_PENDING_JANITOR_INTERVAL_SECONDS = 3600
_MIN_PENDING_JANITOR_INTERVAL_SECONDS = 300


def _pending_janitor_interval_seconds() -> int:
    raw_interval = os.environ.get(
        "RAG_PENDING_JANITOR_INTERVAL_SECONDS",
        str(_DEFAULT_PENDING_JANITOR_INTERVAL_SECONDS),
    )
    try:
        configured_interval = int(raw_interval)
    except ValueError:
        logger.warning(
            "Invalid RAG_PENDING_JANITOR_INTERVAL_SECONDS=%r; using default %s",
            raw_interval,
            _DEFAULT_PENDING_JANITOR_INTERVAL_SECONDS,
        )
        return _DEFAULT_PENDING_JANITOR_INTERVAL_SECONDS
    return max(_MIN_PENDING_JANITOR_INTERVAL_SECONDS, configured_interval)


async def _pending_collection_janitor(manager: RAGIndexManager) -> None:
    """Periodically remove only expired, unowned pending collections."""
    interval = _pending_janitor_interval_seconds()
    unavailable = False
    while True:
        try:
            cleaned = await asyncio.to_thread(
                manager.cleanup_expired_pending_collections
            )
            if unavailable:
                logger.info("Pending collection janitor recovered")
                unavailable = False
            if cleaned:
                logger.info("Pending collection janitor removed %s collections", cleaned)
        except asyncio.CancelledError:
            raise
        except Exception as exception:
            # Cleanup is auxiliary: retain uncertain collections and keep serving.
            if not unavailable:
                logger.warning(
                    "Pending collection janitor unavailable; retaining "
                    "uncertain collections: %s",
                    exception,
                    exc_info=True,
                )
                unavailable = True
            else:
                logger.debug(
                    "Pending collection janitor still unavailable: %s",
                    exception,
                )
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown lifecycle of the application.

    Creates shared singletons (config, index_manager, query_service) on
    startup and tears them down on shutdown — closing Qdrant + HTTP clients.
    """
    global config, index_manager, query_service
    logger.info("Starting RAG Pipeline API...")
    config = RAGConfig()
    index_manager = RAGIndexManager(config)
    query_service = RAGQueryService(
        config,
        plugin_catalog=index_manager.plugin_catalog,
    )
    from .routers.index import (
        cleanup_orphaned_index_repository_stream_workspaces,
    )
    cleaned_stream_workspaces = (
        cleanup_orphaned_index_repository_stream_workspaces()
    )
    if cleaned_stream_workspaces:
        logger.info(
            "Removed %s orphaned RAG HTTP index workspaces",
            cleaned_stream_workspaces,
        )

    # Initialize and start the Redis Queue Consumer
    from ..server.rag_queue_consumer import RAGQueueConsumer
    rag_queue_consumer = RAGQueueConsumer(index_manager)
    app.state.rag_queue_consumer = rag_queue_consumer
    await rag_queue_consumer.start()
    app.state.pending_collection_janitor = asyncio.create_task(
        _pending_collection_janitor(index_manager)
    )

    logger.info("RAG Pipeline API started successfully")
    yield
    logger.info("Shutting down RAG Pipeline API...")
    if hasattr(app.state, "pending_collection_janitor"):
        app.state.pending_collection_janitor.cancel()
        try:
            await app.state.pending_collection_janitor
        except asyncio.CancelledError:
            pass
    if hasattr(app.state, 'rag_queue_consumer'):
        await app.state.rag_queue_consumer.stop()
    # HTTP streaming requests run synchronous indexing in dedicated workers.
    # A disconnected response task can be gone before that call returns, so
    # drain the independently tracked workers before closing shared embedding
    # and Qdrant clients.
    from .routers.index import drain_index_repository_stream_workers
    await drain_index_repository_stream_workers()
    if hasattr(index_manager, 'embed_model') and hasattr(index_manager.embed_model, 'close'):
        index_manager.embed_model.close()
    if hasattr(query_service, 'embed_model') and hasattr(query_service.embed_model, 'close'):
        query_service.embed_model.close()
    if query_service is not None:
        query_service.close()
    if index_manager is not None:
        index_manager.close()
    logger.info("RAG Pipeline API shutdown complete")


app = FastAPI(
    title="CodeCrow RAG API",
    version="unreleased",
    lifespan=lifespan,
)

# Service-to-service auth
from .middleware import ServiceSecretMiddleware
app.add_middleware(ServiceSecretMiddleware)

# Include routers
from .routers.system import router as system_router
from .routers.parse import router as parse_router
from .routers.index import router as index_router
from .routers.query import router as query_router
from .routers.pr import router as pr_router
from .routers.inspect import router as inspect_router

app.include_router(system_router)
app.include_router(parse_router)
app.include_router(index_router)
app.include_router(query_router)
app.include_router(pr_router)
app.include_router(inspect_router)

# Uvicorn loads this module by import string in every worker. Wrap the exported
# application here so each worker receives top-level ASGI instrumentation.
if os.environ.get("NEW_RELIC_CONFIG_FILE"):
    try:
        import newrelic.agent
        app = newrelic.agent.ASGIApplicationWrapper(app)
        logger.info("New Relic ASGI wrapper applied")
    except Exception as exc:
        logger.warning("New Relic ASGI wrapper failed: %s", exc)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
