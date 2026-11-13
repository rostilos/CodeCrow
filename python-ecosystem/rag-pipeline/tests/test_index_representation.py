from pathlib import Path

from rag_pipeline.core.index_representation import (
    _REPRESENTATION_DEPENDENCIES,
    _REPRESENTATION_SOURCE_PATHS,
    _runtime_representation_settings,
    compute_index_representation_fingerprint,
)
from rag_pipeline.models.config import RAGConfig


def _projection_root(tmp_path: Path) -> Path:
    root = tmp_path / "rag_pipeline"
    for index, relative_path in enumerate(_REPRESENTATION_SOURCE_PATHS):
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"representation input {index}\n", encoding="utf-8")
    return root


def _dependencies(**overrides):
    values = {name: "test" for name in _REPRESENTATION_DEPENDENCIES}
    values.update(overrides)
    return values


def test_fingerprint_is_deterministic_and_changes_with_source_or_dependency(
    tmp_path,
):
    root = _projection_root(tmp_path)
    baseline = compute_index_representation_fingerprint(
        root,
        dependency_versions=_dependencies(),
    )

    assert baseline == compute_index_representation_fingerprint(
        root,
        dependency_versions=_dependencies(),
    )

    source_path = root / _REPRESENTATION_SOURCE_PATHS[0]
    source_path.write_text("changed representation\n", encoding="utf-8")
    assert baseline != compute_index_representation_fingerprint(
        root,
        dependency_versions=_dependencies(),
    )

    source_path.write_text("representation input 0\n", encoding="utf-8")
    changed_dependency = _dependencies()
    changed_dependency[_REPRESENTATION_DEPENDENCIES[0]] = "changed"
    assert baseline != compute_index_representation_fingerprint(
        root,
        dependency_versions=changed_dependency,
    )

    assert baseline != compute_index_representation_fingerprint(
        root,
        dependency_versions=_dependencies(),
        runtime_settings={"excluded_patterns": ["vendor/**"]},
    )


def test_runtime_representation_records_structural_storage_and_file_ceiling():
    smaller = _runtime_representation_settings(
        RAGConfig(max_file_size_bytes=256 * 1024)
    )
    larger = _runtime_representation_settings(
        RAGConfig(max_file_size_bytes=512 * 1024)
    )

    assert smaller["storage"] == "sqlite-structural-graph"
    assert smaller["max_file_size_bytes"] == 256 * 1024
    assert larger["max_file_size_bytes"] == 512 * 1024
    assert smaller != larger


def test_fingerprint_tracks_extracted_producer_collaborators(tmp_path):
    root = _projection_root(tmp_path)
    previous = compute_index_representation_fingerprint(root, dependency_versions=_dependencies())
    for path in ("core/splitter/source_spans.py", "core/index_manager/file_helpers.py",
                 "core/structural_graph/relation_buffer.py"):
        module = root / path
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text("implementation = True\n")
        current = compute_index_representation_fingerprint(root, dependency_versions=_dependencies())
        assert current != previous
        previous = current


def test_query_orchestration_and_runtime_capacities_do_not_invalidate_sealed_indexes(tmp_path):
    root = _projection_root(tmp_path)
    baseline = compute_index_representation_fingerprint(root, dependency_versions=_dependencies())
    for module in ("core/review_generation.py", "core/review_context.py", "core/review_graph/walk.py",
                   "core/structural_graph/reader.py", "core/index_manager/manager.py", "models/config.py",
                   "api/heavy_work.py"):
        path = root / module
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("concurrency = 32\n")
        assert baseline == compute_index_representation_fingerprint(root, dependency_versions=_dependencies())
    assert _runtime_representation_settings(RAGConfig(full_index_concurrency=16)) == (
        _runtime_representation_settings(RAGConfig(full_index_concurrency=32)))


def test_persisted_producers_remain_fingerprint_inputs(tmp_path):
    root = _projection_root(tmp_path)
    previous = compute_index_representation_fingerprint(root, dependency_versions=_dependencies())
    for module in ("core/index_manager/generation_builder.py", "core/index_manager/file_indexer.py",
                   "core/index_manager/repository_enrichment.py", "core/structural_graph/schema.py",
                   "core/structural_graph/relations.py", "core/structural_graph/writer.py", "core/loader.py"):
        path = root / module
        path.write_text(path.read_text() + "changed_producer = True\n")
        current = compute_index_representation_fingerprint(root, dependency_versions=_dependencies())
        assert current != previous
        previous = current
