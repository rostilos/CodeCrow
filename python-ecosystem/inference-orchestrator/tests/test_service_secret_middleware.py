from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.middleware import ServiceSecretMiddleware


@pytest.mark.parametrize(("provided", "expected"), [("internal-token", 200), ("wrong-token", 401), (None, 401)])
def test_internal_routes_require_configured_service_secret(provided, expected):
    app = FastAPI()
    app.add_middleware(ServiceSecretMiddleware, secret="internal-token")

    @app.get("/internal")
    async def internal():
        return {"ok": True}

    headers = {} if provided is None else {"x-service-secret": provided}
    with TestClient(app) as client:
        assert client.get("/internal", headers=headers).status_code == expected
