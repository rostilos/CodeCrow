from pathlib import Path
from types import SimpleNamespace

import pytest

from rag_pipeline.core.index_representation import (
    INDEX_REPRESENTATION_PAYLOAD_KEY,
    IndexCompatibilityError,
    _REPRESENTATION_DEPENDENCIES,
    _REPRESENTATION_SOURCE_PATHS,
    branch_splitter_kwargs,
    compute_index_representation_fingerprint,
    read_branch_index_representation,
    require_compatible_branch_representation,
)
from rag_pipeline.core.pr_overlay_representation import (
    _PR_OVERLAY_DEPENDENCIES,
    _PR_OVERLAY_SOURCE_PATHS,
    compute_pr_overlay_representation_fingerprint,
)


def _projection_root(tmp_path: Path) -> Path:
    root = tmp_path / "rag_pipeline"
    all_paths = tuple(dict.fromkeys(
        (*_REPRESENTATION_SOURCE_PATHS, *_PR_OVERLAY_SOURCE_PATHS)
    ))
    for index, relative_path in enumerate(all_paths):
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"representation input {index}\n", encoding="utf-8")
    return root


def _dependencies(**overrides):
    values = {name: "test" for name in _REPRESENTATION_DEPENDENCIES}
    values.update(overrides)
    return values


def _overlay_dependencies(**overrides):
    values = {name: "test" for name in _PR_OVERLAY_DEPENDENCIES}
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
        runtime_settings={
            "embedding_model": "other-model",
            "embedding_dimension": 4096,
        },
    )


def test_pr_only_source_changes_do_not_invalidate_branch_representation(
    tmp_path,
):
    root = _projection_root(tmp_path)
    branch_fingerprint = compute_index_representation_fingerprint(
        root,
        dependency_versions=_dependencies(),
    )
    overlay_fingerprint = compute_pr_overlay_representation_fingerprint(
        root,
        branch_representation_fingerprint=branch_fingerprint,
        dependency_versions=_overlay_dependencies(),
    )

    pr_source = root / _PR_OVERLAY_SOURCE_PATHS[0]
    pr_source.write_text("changed PR overlay behavior\n", encoding="utf-8")

    assert branch_fingerprint == compute_index_representation_fingerprint(
        root,
        dependency_versions=_dependencies(),
    )
    assert overlay_fingerprint != compute_pr_overlay_representation_fingerprint(
        root,
        branch_representation_fingerprint=branch_fingerprint,
        dependency_versions=_overlay_dependencies(),
    )


def test_manager_wiring_is_pr_overlay_only_and_source_sets_are_disjoint(tmp_path):
    assert "core/index_manager/manager.py" not in _REPRESENTATION_SOURCE_PATHS
    assert "core/index_manager/manager.py" in _PR_OVERLAY_SOURCE_PATHS
    assert set(_REPRESENTATION_SOURCE_PATHS).isdisjoint(_PR_OVERLAY_SOURCE_PATHS)

    root = _projection_root(tmp_path)
    branch_fingerprint = compute_index_representation_fingerprint(
        root,
        dependency_versions=_dependencies(),
    )
    overlay_fingerprint = compute_pr_overlay_representation_fingerprint(
        root,
        branch_representation_fingerprint=branch_fingerprint,
        dependency_versions=_overlay_dependencies(),
    )
    manager_source = root / "core/index_manager/manager.py"
    manager_source.write_text("changed manager wiring\n", encoding="utf-8")

    assert branch_fingerprint == compute_index_representation_fingerprint(
        root,
        dependency_versions=_dependencies(),
    )
    assert overlay_fingerprint != compute_pr_overlay_representation_fingerprint(
        root,
        branch_representation_fingerprint=branch_fingerprint,
        dependency_versions=_overlay_dependencies(),
    )


def test_branch_splitter_construction_is_part_of_runtime_identity():
    config = SimpleNamespace(chunk_size=8000, chunk_overlap=200)

    assert branch_splitter_kwargs(config) == {
        "max_chunk_size": 8000,
        "min_chunk_size": 200,
        "chunk_overlap": 200,
        "parser_threshold": 10,
        "enrich_embedding_text": True,
    }


def test_branch_change_invalidates_both_branch_and_overlay_identity(tmp_path):
    root = _projection_root(tmp_path)
    branch_fingerprint = compute_index_representation_fingerprint(
        root,
        dependency_versions=_dependencies(),
    )
    overlay_fingerprint = compute_pr_overlay_representation_fingerprint(
        root,
        branch_representation_fingerprint=branch_fingerprint,
        dependency_versions=_overlay_dependencies(),
    )
    branch_source = root / _REPRESENTATION_SOURCE_PATHS[0]
    branch_source.write_text("changed branch representation\n", encoding="utf-8")
    changed_branch = compute_index_representation_fingerprint(
        root,
        dependency_versions=_dependencies(),
    )

    assert changed_branch != branch_fingerprint
    assert overlay_fingerprint != compute_pr_overlay_representation_fingerprint(
        root,
        branch_representation_fingerprint=changed_branch,
        dependency_versions=_overlay_dependencies(),
    )


def test_branch_identity_distinguishes_absent_legacy_and_current_points():
    client = SimpleNamespace()
    client.scroll = lambda **_kwargs: ([], None)
    assert read_branch_index_representation(
        client,
        "collection",
        "main",
    ) == (False, None)

    client.scroll = lambda **_kwargs: (
        [SimpleNamespace(payload={"path": "legacy.php"})],
        None,
    )
    assert read_branch_index_representation(
        client,
        "collection",
        "main",
    ) == (True, None)
    assert require_compatible_branch_representation(
        client,
        "collection",
        "main",
        expected_fingerprint="sha256:current",
    ) is True

    client.scroll = lambda **_kwargs: (
        [SimpleNamespace(payload={
            INDEX_REPRESENTATION_PAYLOAD_KEY: "sha256:older-build",
        })],
        None,
    )
    assert require_compatible_branch_representation(
        client,
        "collection",
        "main",
        expected_fingerprint="sha256:current",
    ) is True

    client.scroll = lambda **_kwargs: (
        [SimpleNamespace(payload={
            INDEX_REPRESENTATION_PAYLOAD_KEY: "sha256:current",
        })],
        None,
    )
    assert require_compatible_branch_representation(
        client,
        "collection",
        "main",
        expected_fingerprint="sha256:current",
    ) is True


def test_branch_identity_pages_past_pr_points_and_accepts_missing_pr_as_legacy():
    calls = []

    def scroll(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return (
                [SimpleNamespace(payload={"pr": True})],
                "next",
            )
        return (
            [SimpleNamespace(payload={"path": "legacy.php"})],
            None,
        )

    client = SimpleNamespace(scroll=scroll)
    assert read_branch_index_representation(
        client,
        "collection",
        "feature",
    ) == (True, None)
    assert len(calls) == 2
    assert calls[1]["offset"] == "next"
