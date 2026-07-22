"""
Health check endpoints.
"""
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request):
    """Report readiness only while both Redis consumers are alive."""
    consumers = (
        getattr(request.app.state, "queue_consumer", None),
        getattr(request.app.state, "command_queue_consumer", None),
    )
    ready = all(
        consumer is not None
        and consumer.is_running
        and consumer._task is not None
        and not consumer._task.done()
        for consumer in consumers
    )
    if not ready:
        raise HTTPException(status_code=503, detail="queue consumers are not ready")
    return {"status": "ok"}
