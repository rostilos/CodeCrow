"""Command entry point, loaded lazily so pure adapters remain independent."""
from typing import Any

__all__ = ["CommandService"]


def __getattr__(name: str) -> Any:
    if name != "CommandService":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from service.command.command_service import CommandService
    globals()[name] = CommandService
    return CommandService
