from types import SimpleNamespace
import pytest
from langchain_core.callbacks import BaseCallbackHandler
from llm import execution_authorization as authorization


def test_public_default_is_unchanged(monkeypatch):
    monkeypatch.delenv("INFERENCE_AUTHORIZER_FACTORY", raising=False)
    authorization._factory.cache_clear()
    model = SimpleNamespace(callbacks=None)
    assert authorization.authorize_model(model, project_id=1) is model
    assert model.callbacks is None


def test_configured_authorization_failure_never_becomes_optional(monkeypatch):
    def broken():
        raise RuntimeError("unavailable")
    monkeypatch.setattr(authorization, "_factory", broken)
    with pytest.raises(RuntimeError, match="unavailable"):
        authorization.authorize_model(SimpleNamespace(callbacks=None), project_id=1)
    monkeypatch.setattr(authorization, "_factory", lambda: lambda **context: None)
    with pytest.raises(RuntimeError, match="authorization"):
        authorization.authorize_model(SimpleNamespace(callbacks=None), project_id=1)


def test_authorization_preserves_existing_callbacks(monkeypatch):
    class Required(BaseCallbackHandler):
        raise_error = True
    required, existing = Required(), object()
    monkeypatch.setattr(authorization, "_factory", lambda: lambda **context: required)
    model = SimpleNamespace(callbacks=[existing])
    assert authorization.authorize_model(model, project_id=1) is model
    assert model.callbacks == [existing, required]
