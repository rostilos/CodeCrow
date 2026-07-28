from __future__ import annotations

import os
from pathlib import Path

from .catalog import PluginCatalog


def builtin_plugins_root() -> Path:
    configured = os.environ.get("CODECROW_PLUGINS_ROOT")
    if configured:
        return Path(configured).resolve(strict=True)
    return Path(__file__).resolve().parents[3]


def discover_builtin_plugins() -> PluginCatalog:
    return PluginCatalog.discover(builtin_plugins_root())
