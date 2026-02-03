"""
FastAPI Application Factory.

Creates and configures the FastAPI application with all routers.
"""
import os
from fastapi import FastAPI

from api.routers import health, review, commands


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(title="codecrow-mcp-client")
    
    # Register routers
    app.include_router(health.router)
    app.include_router(review.router)
    app.include_router(commands.router)
    
    return app


def run_http_server(host: str = "0.0.0.0", port: int = 8000):
    """Run the FastAPI application."""
    app = create_app()
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info", timeout_keep_alive=300)


if __name__ == "__main__":
    host = os.environ.get("AI_CLIENT_HOST", "0.0.0.0")
    port = int(os.environ.get("AI_CLIENT_PORT", "8000"))
    run_http_server(host=host, port=port)
