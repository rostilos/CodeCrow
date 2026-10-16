"""
FastAPI Application Factory.

Creates and configures the FastAPI application with all routers.
Uses lifespan context manager for proper startup/shutdown of shared resources.
"""
import asyncio
import os
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv(interpolate=False)

from api.routers import health, review, commands, qa_documentation
from api.middleware import ServiceSecretMiddleware
from service.review.review_service import ReviewService
from service.command.command_service import CommandService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: create services on startup, clean up on shutdown."""
    services = []
    consumers = []
    try:
        logger.info("Initializing application services...")
        review_service = ReviewService()
        services.append(review_service)
        command_service = CommandService()
        services.append(command_service)

        from server.queue_consumer import RedisQueueConsumer
        from server.command_queue_consumer import CommandQueueConsumer

        queue_consumer = RedisQueueConsumer(review_service)
        consumers.append(queue_consumer)
        app.state.queue_consumer = queue_consumer
        await queue_consumer.start()

        command_queue_consumer = CommandQueueConsumer(command_service)
        consumers.append(command_queue_consumer)
        app.state.command_queue_consumer = command_queue_consumer
        await command_queue_consumer.start()

        app.state.review_service = review_service
        app.state.command_service = command_service
        logger.info("Application services ready")
        yield
    finally:
        # This also runs when only part of startup succeeds or the application
        # exits its lifespan with an error. Stop both intakes before draining.
        logger.info("Shutting down application services...")
        stopped = await asyncio.gather(
            *(consumer.stop() for consumer in consumers),
            return_exceptions=True,
        )
        for result in stopped:
            if isinstance(result, BaseException):
                logger.warning("Error stopping queue consumer: %s", result)
        for service in services:
            try:
                await service.rag_client.close()
            except Exception as error:
                logger.warning("Error closing %s RagClient: %s", type(service).__name__, error)
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
            logger.warning(f"New Relic ASGI wrapper failed: {e}")

    import uvicorn
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        timeout_keep_alive=300,
        # New Relic exports a callable wrapper whose signature can otherwise
        # be mistaken for an ASGI2 application by Uvicorn.
        interface="asgi3",
    )


if __name__ == "__main__":
    host = os.environ.get("AI_CLIENT_HOST", "0.0.0.0")
    port = int(os.environ.get("AI_CLIENT_PORT", "8000"))
    run_http_server(host=host, port=port)
