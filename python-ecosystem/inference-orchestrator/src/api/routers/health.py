"""
Health check endpoints.
"""
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request):
    """Health check endpoint."""
    queue_consumer = getattr(request.app.state, "queue_consumer", None)
    if queue_consumer is None or not await queue_consumer.is_healthy():
        raise HTTPException(
            status_code=503,
            detail="review queue consumer is unavailable",
        )
    return {"status": "ok"}
