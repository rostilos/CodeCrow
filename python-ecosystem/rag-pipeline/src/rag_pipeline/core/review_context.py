"""Compose proposed-tree lifecycle, graph operations and exact source access.

Generation preparation, source attestation and evidence assembly have separate
owners. This service supplies the existing review endpoint operations.
"""
from __future__ import annotations

from typing import Any, Sequence

from .review_generation import ReviewGenerationService
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
from .review_source import ReviewSourceReader


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
                       **binding: Any) -> dict[str, Any]:
        paths = tuple(dict.fromkeys(_normalize_path(path) for path in focus_paths))
        with self.open_read_session(**binding) as session:
            return build_review_context(
                session.reader, session.generation, paths, question, focus_symbols,
                max_relations=max_relations, max_source_windows=max_source_windows,
                max_source_characters=max_source_characters,
            )

    def minimal_review_context(self, *, focus_paths: Sequence[str], question: str,
                               focus_symbols: Sequence[str] = (), max_relations: int = 25,
                               detail_level: str = "minimal", include_source: bool = True,
                               max_source_windows: int = 4, max_source_characters: int = 8000,
                               **binding: Any) -> dict[str, Any]:
        paths = tuple(dict.fromkeys(_normalize_path(path) for path in focus_paths))
        with self.open_read_session(**binding) as session:
            return build_minimal_review_context(
                session.reader, question=question, focus_paths=paths, focus_symbols=focus_symbols,
                changed_paths=session.generation.changed_paths, max_relations=max_relations,
                detail_level=detail_level, include_source=include_source,
                max_source_windows=max_source_windows, max_source_characters=max_source_characters,
            )

    def review_impact_radius(self, *, focus_paths: Sequence[str], targets: Sequence[str] = (),
                             max_depth: int = 2, max_results: int = 100,
                             detail_level: str = "standard", include_source: bool = True,
                             max_source_windows: int = 6, max_source_characters: int = 12000,
                             **binding: Any) -> dict[str, Any]:
        paths = tuple(dict.fromkeys(_normalize_path(path) for path in focus_paths))
        targets = tuple(dict.fromkeys(str(target).strip() for target in targets if str(target).strip())) or paths
        with self.open_read_session(**binding) as session:
            return build_review_impact_radius(
                session.reader, targets=targets, changed_paths=session.generation.changed_paths,
                max_depth=max_depth, max_results=max_results, detail_level=detail_level,
                include_source=include_source, max_source_windows=max_source_windows,
                max_source_characters=max_source_characters,
            )

    def traverse_review_graph(self, *, focus_paths: Sequence[str], start: str,
                              strategy: str = "bfs", direction: str = "both",
                              relation_kinds: Sequence[str] = (), max_depth: int = 3,
                              max_results: int = 100, token_budget: int | None = None,
                              detail_level: str = "standard", include_source: bool = True,
                              max_source_windows: int = 6, max_source_characters: int = 12000,
                              **binding: Any) -> dict[str, Any]:
        with self.open_read_session(**binding) as session:
            return run_review_graph_traversal(
                session.reader, start=start, strategy=strategy, direction=direction,
                relation_kinds=relation_kinds, changed_paths=session.generation.changed_paths,
                max_depth=max_depth, max_results=max_results, token_budget=token_budget,
                detail_level=detail_level, include_source=include_source,
                max_source_windows=max_source_windows, max_source_characters=max_source_characters,
            )

    def query_review_graph(self, *, focus_paths: Sequence[str], pattern: str, target: str,
                           max_results: int = 25, cursor: int = 0, detail_level: str = "standard",
                           include_source: bool = True, max_source_windows: int = 6,
                           max_source_characters: int = 12000, **binding: Any) -> dict[str, Any]:
        with self.open_read_session(**binding) as session:
            return run_review_graph_query(
                session.reader, pattern=pattern, target=target,
                changed_paths=session.generation.changed_paths, max_results=max_results,
                cursor=cursor, detail_level=detail_level, include_source=include_source,
                max_source_windows=max_source_windows, max_source_characters=max_source_characters,
            )

    def get_review_structural_unit(self, *, focus_paths: Sequence[str], unit_id: str,
                                   offset: int = 0, max_characters: int = 12000,
                                   **binding: Any) -> dict[str, Any] | None:
        with self.open_read_session(**binding) as session:
            return read_review_structural_unit(session.reader, unit_id=unit_id,
                                               offset=offset, max_characters=max_characters)

    @staticmethod
    def _source_reader(session: ProposedTreeReadSession, binding: dict[str, Any]) -> ReviewSourceReader:
        return ReviewSourceReader(
            session, target_repo_path=binding["target_repo_path"],
            review_overlay_path=binding["review_overlay_path"], base_revision=binding["base_revision"],
        )

    def get_review_file_content(self, *, focus_paths: Sequence[str], path: str,
                                side: str = "proposed", start_line: int = 1,
                                end_line: int | None = None, **binding: Any) -> dict[str, Any]:
        with self.open_read_session(**binding) as session:
            return self._source_reader(session, binding).read(path, side=side, start_line=start_line, end_line=end_line)

    def search_review_code(self, *, focus_paths: Sequence[str], query: str,
                           cursor: int = 0, max_results: int = 100, **binding: Any) -> dict[str, Any]:
        with self.open_read_session(**binding) as session:
            return self._source_reader(session, binding).search(query, cursor=cursor, max_results=max_results)
