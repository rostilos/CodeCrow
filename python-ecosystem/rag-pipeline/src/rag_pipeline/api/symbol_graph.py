"""Language-neutral repository symbol resolution for parsed source batches."""

from __future__ import annotations

from collections import defaultdict
import re
from typing import Dict, Iterable, List, Sequence, Tuple

from .models import (
    ParsedFileMetadata,
    ParsedRelationship,
    ParsedRepositoryGraphV1,
    ParsedSymbol,
)


_QUALIFIED_IDENTIFIER = re.compile(
    r"[A-Za-z_$][A-Za-z0-9_$]*(?:[.\\/:]+[A-Za-z_$][A-Za-z0-9_$]*)*"
)
_NOISE = {
    "as", "from", "import", "include", "new", "require", "static", "use",
}


def _canonical(value: str) -> str:
    normalized = value.strip().strip("'\"`;")
    normalized = re.sub(r"<[^<>]*>", "", normalized)
    normalized = normalized.replace("::", ".").replace("\\", ".").replace("/", ".")
    normalized = re.sub(r"\.+", ".", normalized).strip(".")
    return normalized


def _lookup_names(value: str) -> List[str]:
    """Produce bounded exact aliases without guessing language semantics."""
    names: List[str] = []
    seen = set()
    for match in _QUALIFIED_IDENTIFIER.findall(value or ""):
        canonical = _canonical(match)
        if not canonical or canonical.lower() in _NOISE:
            continue
        parts = canonical.split(".")
        candidates = [canonical]
        if len(parts) > 1:
            candidates.append(".".join(parts[-2:]))
        candidates.append(parts[-1])
        for candidate in candidates:
            if candidate and candidate not in seen:
                seen.add(candidate)
                names.append(candidate)
    return names[:12]


def _symbol_aliases(symbol: ParsedSymbol) -> Iterable[str]:
    qualified = _canonical(symbol.qualified_name)
    name = _canonical(symbol.name)
    aliases = [qualified, name]
    parts = qualified.split(".")
    if len(parts) > 1:
        aliases.append(".".join(parts[-2:]))
    return (alias for alias in aliases if alias)


def _candidate_score(
    target_names: Sequence[str],
    symbol: ParsedSymbol,
    *,
    source_path: str,
    source_namespace: str | None,
    relationship_type: str,
) -> float:
    qualified = _canonical(symbol.qualified_name)
    simple = _canonical(symbol.name)
    score = 0.0
    for target in target_names:
        canonical_target = _canonical(target)
        if canonical_target == qualified:
            score = max(score, 1.0)
        elif qualified.endswith("." + canonical_target) or canonical_target.endswith("." + qualified):
            score = max(score, 0.96)
        elif canonical_target == simple:
            score = max(score, 0.86)
        elif canonical_target.endswith("." + simple):
            score = max(score, 0.90)

    if symbol.path == source_path:
        if relationship_type == "contained_by":
            score += 0.12
        elif relationship_type in {"calls", "references"}:
            score += 0.03
    if source_namespace and symbol.qualified_name.startswith(source_namespace + "."):
        score += 0.02
    return min(score, 1.0)


def resolve_parsed_repository_graph(
    parsed_files: Sequence[ParsedFileMetadata],
) -> Tuple[List[ParsedFileMetadata], ParsedRepositoryGraphV1]:
    """Resolve edges only when a unique best symbol exists in the exact batch."""
    symbols = [symbol for parsed in parsed_files for symbol in parsed.symbols]
    by_alias: Dict[str, List[ParsedSymbol]] = defaultdict(list)
    for symbol in symbols:
        for alias in _symbol_aliases(symbol):
            by_alias[alias].append(symbol)

    source_path_by_symbol = {
        symbol.symbol_id: symbol.path
        for symbol in symbols
    }
    namespace_by_path = {
        parsed.path: parsed.namespace
        for parsed in parsed_files
    }
    resolved_files: List[ParsedFileMetadata] = []
    all_relationships: List[ParsedRelationship] = []
    gaps: List[str] = []
    counts = defaultdict(int)

    for parsed in parsed_files:
        resolved_relationships = []
        for relationship in parsed.relationships:
            target_names = _lookup_names(relationship.target_name)
            candidates: Dict[str, ParsedSymbol] = {}
            for target_name in target_names:
                for candidate in by_alias.get(target_name, []):
                    candidates[candidate.symbol_id] = candidate

            source_path = source_path_by_symbol.get(
                relationship.source_symbol_id,
                parsed.path,
            )
            scored = sorted(
                (
                    (
                        _candidate_score(
                            target_names,
                            candidate,
                            source_path=source_path,
                            source_namespace=namespace_by_path.get(source_path),
                            relationship_type=relationship.relationship_type,
                        ),
                        candidate,
                    )
                    for candidate in candidates.values()
                ),
                key=lambda item: (-item[0], item[1].path, item[1].qualified_name),
            )
            best_score = scored[0][0] if scored else 0.0
            best = [candidate for score, candidate in scored if abs(score - best_score) < 0.0001]

            if best_score > 0 and len(best) == 1:
                target = best[0]
                updated = relationship.model_copy(update={
                    "target_symbol_id": target.symbol_id,
                    "target_path": target.path,
                    "resolution": "resolved",
                    "confidence": best_score,
                })
                counts["resolved"] += 1
            elif best_score > 0 and len(best) > 1:
                updated = relationship.model_copy(update={
                    "resolution": "ambiguous",
                    "confidence": min(best_score, 0.79),
                })
                counts["ambiguous"] += 1
                if len(gaps) < 100:
                    gaps.append(
                        f"ambiguous:{parsed.path}:{relationship.relationship_type}:"
                        f"{relationship.target_name}:{len(best)}"
                    )
            else:
                updated = relationship
                counts["unresolved"] += 1
                if len(gaps) < 100:
                    gaps.append(
                        f"unresolved:{parsed.path}:{relationship.relationship_type}:"
                        f"{relationship.target_name}"
                    )
            resolved_relationships.append(updated)
            all_relationships.append(updated)
        resolved_files.append(parsed.model_copy(update={
            "relationships": resolved_relationships,
        }))

    for parsed in parsed_files:
        if not parsed.ast_supported and len(gaps) < 100:
            gaps.append(f"degraded_parser:{parsed.path}:{parsed.degraded_reason or 'unknown'}")

    graph = ParsedRepositoryGraphV1(
        files=[parsed.path for parsed in parsed_files],
        symbols=symbols,
        relationships=all_relationships,
        resolved_count=counts["resolved"],
        ambiguous_count=counts["ambiguous"],
        unresolved_count=counts["unresolved"],
        resolution_gaps=gaps,
    )
    return resolved_files, graph
