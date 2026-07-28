from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .api import CodeCrowPlugin, PluginDescriptor
from .manifest import load_descriptor
from .registry import PluginRegistry


def _descriptor_paths(root: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    for kind in ("languages", "frameworks", "domains"):
        kind_root = root / kind
        if not kind_root.exists():
            continue
        for plugin_dir in sorted(path for path in kind_root.iterdir() if path.is_dir()):
            descriptor_path = plugin_dir / "plugin.json"
            if descriptor_path.is_file():
                paths.append(descriptor_path)
    return tuple(paths)


def _runtime_files(root: Path, descriptor_path: Path) -> tuple[Path, ...]:
    candidates = [descriptor_path]
    python_root = descriptor_path.parent / "python"
    if python_root.is_dir():
        candidates.extend(
            path
            for path in python_root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        )
    return tuple(sorted(set(candidates), key=lambda path: path.relative_to(root).as_posix()))


def _contract_runtime_files(root: Path) -> tuple[Path, ...]:
    package_root = root / "contracts" / "python" / "codecrow_plugins"
    if not package_root.is_dir():
        return ()
    return tuple(sorted(
        (
            path
            for path in package_root.rglob("*.py")
            if path.is_file() and "__pycache__" not in path.parts
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    ))


def _file_projection(root: Path, paths: tuple[Path, ...]) -> list[dict[str, str]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in paths
    ]


def _load_entrypoint(descriptor_path: Path, descriptor: PluginDescriptor) -> CodeCrowPlugin | None:
    entrypoint = descriptor.entrypoints.get("python")
    if entrypoint is None:
        return None
    if entrypoint.count(":") != 1:
        raise ValueError(f"invalid Python entrypoint for {descriptor.id}: {entrypoint}")
    module_name, attribute_name = entrypoint.split(":", 1)
    if not module_name or not attribute_name or any(part in {"", ".", ".."} for part in module_name.split(".")):
        raise ValueError(f"invalid Python entrypoint for {descriptor.id}: {entrypoint}")

    python_root = descriptor_path.parent / "python"
    module_path = python_root.joinpath(*module_name.split("."))
    package_path = module_path / "__init__.py"
    file_path = module_path.with_suffix(".py")
    if package_path.is_file():
        source_path = package_path
        search_locations = [str(module_path)]
    elif file_path.is_file():
        source_path = file_path
        search_locations = None
    else:
        raise ValueError(f"Python entrypoint module for {descriptor.id} is not packaged")

    loaded_name = f"_codecrow_builtin_{descriptor.id}_{module_name.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(
        loaded_name,
        source_path,
        submodule_search_locations=search_locations,
    )
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load Python entrypoint for {descriptor.id}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[loaded_name] = module
    try:
        spec.loader.exec_module(module)
        entrypoint_value = getattr(module, attribute_name)
        implementation = entrypoint_value(descriptor) if callable(entrypoint_value) else entrypoint_value
    except Exception:
        sys.modules.pop(loaded_name, None)
        raise
    if getattr(implementation, "descriptor", None) != descriptor:
        raise ValueError(f"descriptor/implementation mismatch for plugin {descriptor.id}")
    return implementation


@dataclass(frozen=True)
class PluginCatalog:
    registry: PluginRegistry
    implementations: Mapping[str, CodeCrowPlugin]
    root: Path
    runtime_files: Mapping[str, tuple[Path, ...]]
    contract_runtime_files: tuple[Path, ...]

    @classmethod
    def discover(cls, plugins_root: str | Path) -> "PluginCatalog":
        root = Path(plugins_root).resolve(strict=True)
        descriptors: list[PluginDescriptor] = []
        implementations: dict[str, CodeCrowPlugin] = {}
        runtime_files: dict[str, tuple[Path, ...]] = {}
        for descriptor_path in _descriptor_paths(root):
            descriptor = load_descriptor(descriptor_path)
            descriptors.append(descriptor)
            runtime_files[descriptor.id] = _runtime_files(root, descriptor_path)
            implementation = _load_entrypoint(descriptor_path, descriptor)
            if implementation is not None:
                if descriptor.id in implementations:
                    raise ValueError(f"duplicate plugin implementation: {descriptor.id}")
                implementations[descriptor.id] = implementation
        registry = PluginRegistry(descriptors)
        ordered_implementations = {
            plugin_id: implementations[plugin_id]
            for plugin_id in registry.ordered_ids
            if plugin_id in implementations
        }
        return cls(
            registry=registry,
            implementations=MappingProxyType(ordered_implementations),
            root=root,
            runtime_files=MappingProxyType({
                plugin_id: runtime_files[plugin_id]
                for plugin_id in registry.ordered_ids
            }),
            contract_runtime_files=_contract_runtime_files(root),
        )

    def implementation(self, plugin_id: str) -> CodeCrowPlugin | None:
        return self.implementations.get(plugin_id)

    def implementation_fingerprint(self, plugin_ids) -> str:
        """Hash the selected Python plugin runtime and its neutral contract.

        This is build-content identity, not a release or compatibility version.
        Unselected plugin changes deliberately do not invalidate an index.
        """
        selected = tuple(descriptor.id for descriptor in self.registry.resolve(plugin_ids))
        projection = {
            "contracts": (
                _file_projection(self.root, self.contract_runtime_files)
                if selected else []
            ),
            "plugins": [
                {
                    "id": plugin_id,
                    "files": _file_projection(
                        self.root,
                        self.runtime_files[plugin_id],
                    ),
                }
                for plugin_id in selected
            ],
        }
        encoded = json.dumps(
            projection,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()
