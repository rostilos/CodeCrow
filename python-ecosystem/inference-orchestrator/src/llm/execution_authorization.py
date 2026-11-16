"""Explicit host authorization, independent of optional model telemetry."""
from functools import lru_cache
import importlib
import os


@lru_cache(maxsize=1)
def _factory():
    target = os.environ.get("INFERENCE_AUTHORIZER_FACTORY", "")
    if not target:
        return None
    module, name = target.rsplit(":", 1)
    return getattr(importlib.import_module(module), name)


def authorize_model(llm, **context):
    factory = _factory()
    if factory is None:
        return llm
    authorizer = factory(**context)
    if authorizer is None or not authorizer.raise_error:
        raise RuntimeError("Configured execution authorization is unavailable")
    callbacks = getattr(llm, "callbacks", None)
    if callbacks is None or isinstance(callbacks, list):
        llm.callbacks = [*(callbacks or []), authorizer]
    else:
        callbacks.add_handler(authorizer)
    return llm
