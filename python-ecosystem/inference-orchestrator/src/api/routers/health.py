"""
Health check endpoints.
"""
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}
