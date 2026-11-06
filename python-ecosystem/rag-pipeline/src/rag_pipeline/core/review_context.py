"""Compose proposed-tree lifecycle and sealed graph operations.

Generation preparation, snapshot attestation and evidence assembly have separate
owners. This service supplies the existing review endpoint operations.
"""
from __future__ import annotations

from typing import Any, Sequence

from .review_generation import ReviewGenerationService
from .review_graph.budget import bound_graph_response, relation_field_page
from .review_evidence import build_review_context, _compact_graph_evidence
from .review_graph_tools import (
    get_review_structural_unit as read_review_structural_unit,
    minimal_review_context as build_minimal_review_context,
    query_review_graph as run_review_graph_query,
    review_impact_radius as build_review_impact_radius,
    traverse_review_graph as run_review_graph_traversal,
)
from .review_snapshot import (
    ProposedTreeGeneration, ProposedTreeReadSession, ProposedTreeUnavailableError,
    ReviewOverlay, load_review_overlay, materialize_proposed_tree,
    _normalize_path, _review_identity,
)


class ProposedTreeReviewContextService:
    def __init__(self, index_manager):
        self.index_manager = index_manager
        self.generations = ReviewGenerationService(index_manager)

    def prepare_generation_singleflight(self, **arguments: Any) -> ProposedTreeGeneration:
        return self.generations.prepare_generation_singleflight(prepare=self.prepare_generation, **arguments)

    def prepare_generation(self, **arguments: Any) -> ProposedTreeGeneration:
        return self.generations.prepare_generation(**arguments)

    def load_prepared_generation(self, **arguments: Any) -> ProposedTreeGeneration:
        return self.generations.load_prepared_generation(**arguments)

    def open_read_session(self, **binding: Any):
        return self.generations.open_read_session(**binding)

    _read_context = staticmethod(build_review_context)

    def review_context(self, *, focus_paths: Sequence[str], question: str,
                       focus_symbols: Sequence[str] = (), max_relations: int = 32,
                       max_source_windows: int = 6, max_source_characters: int = 12000,
                       token_budget: int | None = None, **binding: Any) -> dict[str, Any]:
        paths = tuple(dict.fromkeys(_normalize_path(path) for path in focus_paths))
        with self.open_read_session(**binding) as session:
            return bound_graph_response(build_review_context(
                session.reader, session.generation, paths, question, focus_symbols,
                max_relations=max_relations, max_source_windows=max_source_windows,
                max_source_characters=max_source_characters,
            ), token_budget)

    def minimal_review_context(self, *, focus_paths: Sequence[str], question: str,
                               focus_symbols: Sequence[str] = (), max_relations: int = 25,
                               detail_level: str = "minimal", include_source: bool = True,
                               max_source_windows: int = 4, max_source_characters: int = 8000,
                               token_budget: int | None = None, **binding: Any) -> dict[str, Any]:
        paths = tuple(dict.fromkeys(_normalize_path(path) for path in focus_paths))
        with self.open_read_session(**binding) as session:
            return bound_graph_response(build_minimal_review_context(
                session.reader, question=question, focus_paths=paths, focus_symbols=focus_symbols,
                changed_paths=session.generation.changed_paths, max_relations=max_relations,
                detail_level=detail_level, include_source=include_source,
                max_source_windows=max_source_windows, max_source_characters=max_source_characters,
            ), token_budget)

    def review_impact_radius(self, *, focus_paths: Sequence[str], targets: Sequence[str] = (),
                             max_depth: int = 2, max_results: int = 100,
                             detail_level: str = "standard", include_source: bool = True,
                             max_source_windows: int = 6, max_source_characters: int = 12000,
                             token_budget: int | None = None, **binding: Any) -> dict[str, Any]:
        paths = tuple(dict.fromkeys(_normalize_path(path) for path in focus_paths))
        targets = tuple(dict.fromkeys(str(target).strip() for target in targets if str(target).strip())) or paths
        with self.open_read_session(**binding) as session:
            return bound_graph_response(build_review_impact_radius(
                session.reader, targets=targets, changed_paths=session.generation.changed_paths,
                max_depth=max_depth, max_results=max_results, detail_level=detail_level,
                include_source=include_source, max_source_windows=max_source_windows,
                max_source_characters=max_source_characters,
            ), token_budget)

    def traverse_review_graph(self, *, focus_paths: Sequence[str], start: str,
                              strategy: str = "bfs", direction: str = "both",
                              relation_kinds: Sequence[str] = (), max_depth: int = 3,
                              max_results: int = 100, token_budget: int | None = None,
                              detail_level: str = "standard", include_source: bool = True,
                              max_source_windows: int = 6, max_source_characters: int = 12000,
                              **binding: Any) -> dict[str, Any]:
        with self.open_read_session(**binding) as session:
            return bound_graph_response(run_review_graph_traversal(
                session.reader, start=start, strategy=strategy, direction=direction,
                relation_kinds=relation_kinds, changed_paths=session.generation.changed_paths,
                max_depth=max_depth, max_results=max_results, token_budget=token_budget,
                detail_level=detail_level, include_source=include_source,
                max_source_windows=max_source_windows, max_source_characters=max_source_characters,
            ), token_budget)

    def query_review_graph(self, *, focus_paths: Sequence[str], pattern: str = "relations_of", target: str = "",
                           max_results: int = 25, cursor: int = 0, detail_level: str = "standard",
                           include_source: bool = True, max_source_windows: int = 6,
                           max_source_characters: int = 12000, token_budget: int | None = None,
                           evidence_id: str | None = None, field: str = "target",
                           offset: int = 0, max_characters: int = 12000, **binding: Any) -> dict[str, Any]:
        with self.open_read_session(**binding) as session:
            if evidence_id:
                return bound_graph_response(relation_field_page(
                    session.reader, evidence_id, field, offset, max_characters,
                ), token_budget)
            return bound_graph_response(run_review_graph_query(
                session.reader, pattern=pattern, target=target,
                changed_paths=session.generation.changed_paths, max_results=max_results,
                cursor=cursor, detail_level=detail_level, include_source=include_source,
                max_source_windows=max_source_windows, max_source_characters=max_source_characters,
            ), token_budget)

    def get_review_structural_unit(self, *, focus_paths: Sequence[str], unit_id: str,
                                   offset: int = 0, max_characters: int = 12000,
                                   token_budget: int | None = None, **binding: Any) -> dict[str, Any] | None:
        with self.open_read_session(**binding) as session:
            return bound_graph_response(read_review_structural_unit(
                session.reader, unit_id=unit_id, offset=offset, max_characters=max_characters,
            ), token_budget)
