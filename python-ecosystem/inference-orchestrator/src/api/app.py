"""
FastAPI Application Factory.

Creates and configures the FastAPI application with all routers.
Uses lifespan context manager for proper startup/shutdown of shared resources.
"""
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

from api.routers import health, review, commands
from api.middleware import ServiceSecretMiddleware
from service.review.review_service import ReviewService
from service.command.command_service import CommandService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: create services on startup, clean up on shutdown."""
    # --- Startup ---
    logger.info("Initializing application services...")
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
        
    # Close the RagClient HTTP pools owned by each service
    try:
        await review_service.rag_client.close()
    except Exception as e:
        logger.warning(f"Error closing review RagClient: {e}")
    try:
        await command_service.rag_client.close()
    except Exception as e:
        logger.warning(f"Error closing command RagClient: {e}")
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
    
    return app


def run_http_server(host: str = "0.0.0.0", port: int = 8000):
    """Run the FastAPI application."""
    app = create_app()

    # Wrap with New Relic ASGI instrumentation when the agent is active.
    # initialize() sets up import hooks but cannot wrap the top-level ASGI
    # protocol when the app is passed as a Python object to uvicorn.run().
    try:
        import newrelic.agent
        nr_app = newrelic.agent.application()
        # application() returns a non-None sentinel even when uninitialised,
        # but the settings object is only populated after initialize().
        if nr_app and nr_app.settings:
            app = newrelic.agent.ASGIApplicationWrapper(app)
            logger.info("New Relic ASGI wrapper applied")
    except Exception:
        pass  # NR not installed or not initialized — run without it

    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info", timeout_keep_alive=300)


if __name__ == "__main__":
    host = os.environ.get("AI_CLIENT_HOST", "0.0.0.0")
    port = int(os.environ.get("AI_CLIENT_PORT", "8000"))
    run_http_server(host=host, port=port)
