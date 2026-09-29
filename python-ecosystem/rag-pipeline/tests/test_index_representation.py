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
