#!/usr/bin/env python3
"""Assemble independently built Java plugins for the pipeline-agent runtime."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = ROOT / "analysis-plugins"
DESTINATION = PLUGINS_ROOT / "build" / "java"


def descriptors() -> tuple[Path, ...]:
    result: list[Path] = []
    for kind in ("languages", "frameworks", "domains"):
        kind_root = PLUGINS_ROOT / kind
        if not kind_root.is_dir():
            continue
        result.extend(
            plugin_dir / "plugin.json"
            for plugin_dir in sorted(kind_root.iterdir())
            if plugin_dir.is_dir() and (plugin_dir / "plugin.json").is_file()
        )
    return tuple(result)


def built_jar(descriptor_path: Path, plugin_id: str) -> Path:
    target = descriptor_path.parent / "java" / "target"
    candidates = sorted(
        path
        for path in target.glob("codecrow-plugin-*.jar")
        if not any(marker in path.name for marker in ("-sources", "-javadoc", "-tests", "original-"))
    )
    if len(candidates) != 1:
        raise RuntimeError(
            f"plugin {plugin_id} must have exactly one packaged Java JAR in {target}; "
            f"found {[path.name for path in candidates]}"
        )
    return candidates[0]


def main() -> int:
    selected: list[tuple[str, Path]] = []
    for descriptor_path in descriptors():
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        if "java" not in descriptor.get("entrypoints", {}):
            continue
        selected.append((descriptor["id"], built_jar(descriptor_path, descriptor["id"])))

    if not selected:
        raise RuntimeError("no packaged Java plugins were found")

    DESTINATION.mkdir(parents=True, exist_ok=True)
    for stale in DESTINATION.glob("*.jar"):
        stale.unlink()

    names: set[str] = set()
    for plugin_id, source in selected:
        if source.name in names:
            raise RuntimeError(f"duplicate plugin artifact name: {source.name}")
        names.add(source.name)
        shutil.copy2(source, DESTINATION / source.name)

    print(
        f"Assembled {len(selected)} Java plugins in deterministic order: "
        + ", ".join(plugin_id for plugin_id, _ in selected)
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exception:
        print(f"Java plugin assembly failed: {exception}", file=sys.stderr)
        raise SystemExit(1)
