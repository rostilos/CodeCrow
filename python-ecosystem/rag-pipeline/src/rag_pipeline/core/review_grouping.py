"""Neutral projection of repository graph facts into changed-file review groups."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def _normalized_path(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().replace("\\", "/").lstrip("/")


def review_groups_from_architecture_payloads(
    payloads: Iterable[Mapping[str, Any]],
    changed_paths: Sequence[str],
) -> list[list[str]]:
    """Return stable connected components proved by plugin graph facts.

    Only changed files are returned. Repository-only related paths remain RAG
    context and do not become Stage 1 review inputs. The host interprets no
    concrete plugin relation: every edge comes from the neutral
    ``GraphFact.path`` and ``GraphFact.related_paths`` contract.
    """
    canonical_by_normalized: dict[str, str] = {}
    for path in changed_paths:
        normalized = _normalized_path(path)
        if normalized and normalized not in canonical_by_normalized:
            canonical_by_normalized[normalized] = path

    parent = {path: path for path in canonical_by_normalized}

    def find(path: str) -> str:
        root = path
        while parent[root] != root:
            root = parent[root]
        while parent[path] != path:
            next_path = parent[path]
            parent[path] = root
            path = next_path
        return root

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root

    for payload in payloads:
        facts = payload.get("plugin_graph_facts") or ()
        if not isinstance(facts, (list, tuple)):
            continue
        for fact in facts:
            if not isinstance(fact, Mapping):
                continue
            candidates = [fact.get("path")]
            related_paths = fact.get("related_paths") or ()
            if isinstance(related_paths, (list, tuple)):
                candidates.extend(related_paths)
            members = sorted({
                normalized
                for candidate in candidates
                if (
                    (normalized := _normalized_path(candidate))
                    and normalized in canonical_by_normalized
                )
            })
            if len(members) < 2:
                continue
            for member in members[1:]:
                union(members[0], member)

    components: dict[str, list[str]] = {}
    for normalized, canonical in canonical_by_normalized.items():
        components.setdefault(find(normalized), []).append(canonical)

    return sorted(
        (
            sorted(paths, key=lambda path: _normalized_path(path))
            for paths in components.values()
            if len(paths) > 1
        ),
        key=lambda paths: tuple(_normalized_path(path) for path in paths),
    )
