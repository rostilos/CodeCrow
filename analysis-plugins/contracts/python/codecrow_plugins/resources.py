from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .manifest import load_descriptor


@dataclass(frozen=True)
class PluginResources:
    """Read-only locator for resources owned by discovered plugins."""

    roots: Mapping[str, Path]

    @classmethod
    def discover(cls, plugins_root: str | Path) -> "PluginResources":
        root = Path(plugins_root).resolve(strict=True)
        roots: dict[str, Path] = {}
        for kind in ("languages", "frameworks", "domains"):
            kind_root = root / kind
            if not kind_root.is_dir():
                continue
            for plugin_root in sorted(path for path in kind_root.iterdir() if path.is_dir()):
                descriptor_path = plugin_root / "plugin.json"
                if not descriptor_path.is_file():
                    continue
                descriptor = load_descriptor(descriptor_path)
                if descriptor.id in roots:
                    raise ValueError(f"duplicate plugin resource root: {descriptor.id}")
                roots[descriptor.id] = plugin_root
        return cls(MappingProxyType(roots))

    def path(self, plugin_id: str, relative_path: str) -> Path | None:
        relative = Path(relative_path)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError("plugin resource path must be safe and relative")
        root = self.roots.get(plugin_id)
        if root is None:
            return None
        candidate = root / relative
        return candidate if candidate.is_file() else None
