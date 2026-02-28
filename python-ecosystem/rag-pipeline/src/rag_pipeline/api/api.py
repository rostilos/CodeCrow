"""
CodeCrow RAG Pipeline API — application entry point.

Creates the FastAPI application, manages singleton lifecycle (startup/shutdown),
and includes all routers. This is the thin orchestration layer.
"""
import logging
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
    query_service = RAGQueryService(config)
    logger.info("RAG Pipeline API started successfully")
    yield
    logger.info("Shutting down RAG Pipeline API...")
    if hasattr(index_manager, 'embed_model') and hasattr(index_manager.embed_model, 'close'):
        index_manager.embed_model.close()
    if hasattr(query_service, 'embed_model') and hasattr(query_service.embed_model, 'close'):
        query_service.embed_model.close()
    logger.info("RAG Pipeline API shutdown complete")


app = FastAPI(title="CodeCrow RAG API", version="2.0.0", lifespan=lifespan)

# Service-to-service auth
from .middleware import ServiceSecretMiddleware
app.add_middleware(ServiceSecretMiddleware)

# Include routers
from .routers.system import router as system_router
from .routers.parse import router as parse_router
from .routers.index import router as index_router
from .routers.query import router as query_router
from .routers.pr import router as pr_router

app.include_router(system_router)
app.include_router(parse_router)
app.include_router(index_router)
app.include_router(query_router)
app.include_router(pr_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
