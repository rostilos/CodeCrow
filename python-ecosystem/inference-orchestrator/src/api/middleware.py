"""
Shared-secret authentication middleware.

Validates that internal service-to-service requests carry
the correct X-Service-Secret header matching the SERVICE_SECRET env var.
"""
import os
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# Paths that skip auth (health checks, readiness probes)
_PUBLIC_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})


class ServiceSecretMiddleware(BaseHTTPMiddleware):
    """Reject requests that don't carry a valid shared service secret."""

    def __init__(self, app, secret: str | None = None):
        super().__init__(app)
        self.secret = secret or os.environ.get("SERVICE_SECRET", "")
        if self.secret:
            logger.info("ServiceSecretMiddleware: secret configured (length=%d)", len(self.secret))
        else:
            logger.warning("ServiceSecretMiddleware: no secret configured — auth disabled")

    async def dispatch(self, request: Request, call_next):
        # Skip auth for health/doc endpoints
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        # If no secret is configured, allow all (dev mode)
        if not self.secret:
            return await call_next(request)

        provided = request.headers.get("x-service-secret", "")
        if provided != self.secret:
            logger.warning(
                "Unauthorized request to %s from %s — "
                "provided_len=%d expected_len=%d match=%s",
                request.url.path,
                request.client.host if request.client else "unknown",
                len(provided),
                len(self.secret),
                provided == self.secret,
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid service secret"},
            )

        return await call_next(request)
