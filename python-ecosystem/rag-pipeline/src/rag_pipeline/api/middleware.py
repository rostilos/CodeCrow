"""
Shared-secret authentication middleware.

Validates that internal service-to-service requests carry
the correct X-Service-Secret header matching the SERVICE_SECRET env var.
"""
import hmac
import os
import logging

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Paths that skip auth (health checks, readiness probes)
_PUBLIC_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})


class ServiceSecretMiddleware:
    """Authenticate without wrapping the receive channel used for cancellation."""

    def __init__(self, app: ASGIApp, secret: str | None = None):
        self.app = app
        self.secret = (
            secret
            if secret is not None
            else os.environ.get("SERVICE_SECRET", "")
        )
        if self.secret:
            logger.info("ServiceSecretMiddleware: secret configured (length=%d)", len(self.secret))
        else:
            logger.warning("ServiceSecretMiddleware: no secret configured — auth disabled")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in _PUBLIC_PATHS or not self.secret:
            await self.app(scope, receive, send)
            return

        provided = Headers(scope=scope).get("x-service-secret", "")
        if not hmac.compare_digest(provided.encode("utf-8"), self.secret.encode("utf-8")):
            client = scope.get("client")
            logger.warning(
                "Unauthorized request to %s from %s",
                scope["path"], client[0] if client else "unknown",
            )
            response = JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid service secret"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
