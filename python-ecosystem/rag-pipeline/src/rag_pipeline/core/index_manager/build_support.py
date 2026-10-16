"""Shared indexing configuration and cancellation contracts."""
from __future__ import annotations
from typing import Sequence

_ZERO_FINGERPRINT = "sha256:" + "0" * 64
_BATCH_SIZE = 50

class RepositoryIndexCancelled(InterruptedError):
    """Raised when an admitted repository build is cooperatively cancelled."""


def _config_int(config, name: str, default: int) -> int:
    try:
        return max(1, int(getattr(config, name, default)))
    except (TypeError, ValueError):
        return default


def _config_float(config, name: str, default: float) -> float:
    try:
        return max(0.0, float(getattr(config, name, default)))
    except (TypeError, ValueError):
        return default


def _unsafe_delta_plugin_selection_changes(
    registry,
    base_plugin_ids: Sequence[str],
    plugin_ids: Sequence[str],
) -> tuple[str, ...]:
    """Return selection changes that need a complete repository rebuild.

    A syntax-only language plugin is selected from the presence of its own file
    extension. Adding or deleting the last such file is fully represented by
    the exact changed/deleted path set, and the plugin owns no repository-wide
    state. Framework, domain, graph, and policy changes are not locally bounded.
    """

    unsafe = []
    for plugin_id in sorted(set(base_plugin_ids).symmetric_difference(plugin_ids)):
        descriptor = registry.descriptor(plugin_id)
        kind = str(getattr(descriptor.kind, "value", descriptor.kind))
        capabilities = {
            str(getattr(capability, "value", capability))
            for capability in descriptor.capabilities
        }
        if kind != "language" or capabilities != {"syntax"}:
            unsafe.append(plugin_id)
    return tuple(unsafe)
