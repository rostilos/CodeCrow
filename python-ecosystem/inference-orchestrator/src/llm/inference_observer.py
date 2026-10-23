"""Optional host-supplied model-call observer. Public deployments install none."""
from functools import lru_cache
import importlib
import logging
import os

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _factory():
    target = os.environ.get("INFERENCE_OBSERVER_FACTORY", "")
    if not target:
        return None
    module, name = target.rsplit(":", 1)
    return getattr(importlib.import_module(module), name)


def observe_model(llm, *, project_id, operation, model, provider, api_key):
    try:
        factory = _factory()
        if factory is None:
            return llm
        observer = factory(project_id=project_id, operation=operation, model=model,
                           provider=provider, api_key=api_key)
        if observer is not None:
            callbacks = getattr(llm, "callbacks", None)
            if callbacks is None or isinstance(callbacks, list):
                llm.callbacks = [*(callbacks or []), observer]
            else:
                callbacks.add_handler(observer)
    except Exception:
        logger.warning("Optional inference observer unavailable; model execution continues")
    return llm


def initialize_observer():
    try:
        factory = _factory()
        if factory is not None and hasattr(factory, "start"):
            factory.start()
    except Exception:
        logger.warning("Optional inference observer startup deferred; model execution continues")


def filter_host_error(message):
    """Allow a deployment to conceal private provider details in outward errors."""
    try:
        factory = _factory()
        return factory.filter_error(message) if factory is not None and hasattr(factory, "filter_error") else message
    except Exception:
        return "The AI service could not complete this request. Please retry or contact support."
