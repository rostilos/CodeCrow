"""
FastAPI Application Factory.

Creates and configures the FastAPI application with all routers.
Uses lifespan context manager for proper startup/shutdown of shared resources.
"""
import asyncio
import math
import os
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv(interpolate=False)

from api.routers import health, review, commands, qa_documentation
from api.middleware import ServiceSecretMiddleware
from service.review.review_service import ReviewService
from service.review.agentic.workspace import AgenticWorkspace
from service.command.command_service import CommandService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: create services on startup, clean up on shutdown."""
    # --- Startup ---
    logger.info("Initializing application services...")
    agentic_cleanup_interval = float(
        os.environ.get("AGENTIC_WORKSPACE_CLEANUP_INTERVAL_SECONDS", "900")
    )
    if (
        not math.isfinite(agentic_cleanup_interval)
        or agentic_cleanup_interval <= 0
        or agentic_cleanup_interval > 3600
    ):
        raise ValueError(
            "AGENTIC_WORKSPACE_CLEANUP_INTERVAL_SECONDS must be between 0 and 3600"
        )
    review_service = ReviewService()
    command_service = CommandService()
    
    # Initialize and start the Redis Queue Consumers
    from server.queue_consumer import RedisQueueConsumer
    queue_consumer = RedisQueueConsumer(review_service)
    app.state.queue_consumer = queue_consumer
    await queue_consumer.start()

    from server.command_queue_consumer import CommandQueueConsumer
    command_queue_consumer = CommandQueueConsumer(command_service)
    app.state.command_queue_consumer = command_queue_consumer
    await command_queue_consumer.start()

    # Startup cleanup handles old remnants immediately; this bounded sweep is
    # also required because a worker can crash shortly after startup and leave
    # a fresh directory that is not stale until hours later.
    agentic_cleanup_task = asyncio.create_task(
        AgenticWorkspace.run_cleanup_loop(
            review_service.agentic_workspace_root,
            ttl_seconds=review_service.AGENTIC_WORKSPACE_TTL_SECONDS,
            interval_seconds=agentic_cleanup_interval,
        ),
        name="agentic-workspace-cleanup",
    )
    app.state.agentic_cleanup_task = agentic_cleanup_task

    app.state.review_service = review_service
    app.state.command_service = command_service
    logger.info("Application services ready")

    yield

    # --- Shutdown ---
    logger.info("Shutting down application services...")
    if hasattr(app.state, "queue_consumer"):
        await app.state.queue_consumer.stop()
    
    if hasattr(app.state, "command_queue_consumer"):
        await app.state.command_queue_consumer.stop()

    if hasattr(app.state, "agentic_cleanup_task"):
        app.state.agentic_cleanup_task.cancel()
        try:
            await app.state.agentic_cleanup_task
        except asyncio.CancelledError:
            pass
        
    # Close the RagClient HTTP pools owned by each service
    try:
        await review_service.rag_client.close()
    except Exception as e:
        logger.warning(
            "Error closing review RagClient: error_type=%s",
            type(e).__name__,
        )
    try:
        await command_service.rag_client.close()
    except Exception as e:
        logger.warning(
            "Error closing command RagClient: error_type=%s",
            type(e).__name__,
        )
    logger.info("Application services shut down")


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(title="codecrow-inference-orchestrator", lifespan=lifespan)

    # Service-to-service auth
    app.add_middleware(ServiceSecretMiddleware)
    
    # Register routers
    app.include_router(health.router)
    app.include_router(review.router)
    app.include_router(commands.router)
    app.include_router(qa_documentation.router)
    
    return app


def run_http_server(host: str = "0.0.0.0", port: int = 8000):
    """Run the FastAPI application."""
    app = create_app()

    # Wrap with New Relic ASGI instrumentation.
    # initialize() in main.py registers the agent asynchronously — settings/active
    # aren't populated yet at this point, so we gate on the env var instead.
    if os.environ.get('NEW_RELIC_CONFIG_FILE'):
        try:
            import newrelic.agent
            app = newrelic.agent.ASGIApplicationWrapper(app)
            logger.info("New Relic ASGI wrapper applied")
        except Exception as e:
            logger.warning(
                "New Relic ASGI wrapper failed: error_type=%s",
                type(e).__name__,
            )

    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info", timeout_keep_alive=300)


if __name__ == "__main__":
    host = os.environ.get("AI_CLIENT_HOST", "0.0.0.0")
    port = int(os.environ.get("AI_CLIENT_PORT", "8000"))
    run_http_server(host=host, port=port)
