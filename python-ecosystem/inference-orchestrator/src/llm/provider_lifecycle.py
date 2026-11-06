"""Optional provider resources live as long as their owning tool loop."""
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class _ActiveScope:
    llm: object
    task: object
    active: bool = True


_active_scope = ContextVar("provider_execution_scope", default=None)


@asynccontextmanager
async def provider_execution_scope(llm):
    owner = _active_scope.get()
    if owner is not None and owner.active and owner.llm is llm and owner.task is asyncio.current_task():
        yield
        return
    scope = None
    hook = getattr(llm, "provider_execution_scope", None)
    if callable(hook):
        try:
            scope = hook()
            await scope.__aenter__()
        except Exception:
            scope = None
            logger.warning("Optional provider execution scope unavailable", exc_info=True)
    owner = _ActiveScope(llm, asyncio.current_task()) if scope is not None else None
    token = _active_scope.set(owner) if owner is not None else None
    try:
        yield
    finally:
        if owner is not None:
            owner.active = False
            try:
                _active_scope.reset(token)
            except ValueError:
                # Async generators can be closed from a different context.
                pass
        if scope is not None:
            try:
                async with asyncio.timeout(10):
                    # Cleanup cannot suppress the caller's result or exception.
                    await scope.__aexit__(None, None, None)
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                logger.warning("Optional provider cleanup was cancelled")
            except Exception:
                logger.warning("Optional provider cleanup unavailable", exc_info=True)
