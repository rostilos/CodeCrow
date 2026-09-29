"""Offline process architecture checks; these are not review-quality benchmarks."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sqlite3
import time

import pytest

from rag_pipeline.api.build_workers import BuildWorkerPool
from rag_pipeline.api.build_operations import execute_build_operation
from rag_pipeline.core.coordination import ProjectMutationCoordinator
from rag_pipeline.core.index_manager import RAGIndexManager
from rag_pipeline.core.source_tree import attest_repository_source_tree
from rag_pipeline.models.config import RAGConfig

_fixture_wave = None
_fixture_root = None
_fixture_entered = False


def isolated_manager(config):
    """Real extraction/storage with no external Redis or plugin services."""
    manager = RAGIndexManager(config)
    manager._mutation_coordinator.close()
    manager._mutation_coordinator = ProjectMutationCoordinator("", enabled=False)
    manager.plugin_catalog = manager.plugin_runtime = manager.plugin_selector = None
    original_builder = manager._generation_builder

    def observed_builder():
        builder = original_builder()
        original = builder.files.index_document

        def observed_index_document(*args, **kwargs):
            global _fixture_entered
            if _fixture_wave is not None and not _fixture_entered:
                _fixture_entered = True
                wave = Path(_fixture_root) / _fixture_wave
                (wave / f"entered-{os.getpid()}").touch()
                deadline = time.monotonic() + 45
                while not (wave / "release").exists():
                    if time.monotonic() >= deadline:
                        raise RuntimeError("offline worker rendezvous did not release")
                    time.sleep(0.01)
            return original(*args, **kwargs)

        builder.files.index_document = observed_index_document
        return builder

    manager._generation_builder = observed_builder
    return manager


def observed_build(operation, payload, slot, job_id):
    global _fixture_wave, _fixture_root, _fixture_entered
    _fixture_wave = payload["fixture_wave"]
    _fixture_root = payload["fixture_root"]
    _fixture_entered = False
    return execute_build_operation(operation, payload, slot, job_id)


async def entered_processes(wave, tasks):
    deadline = time.monotonic() + 35
    while len(tuple(wave.glob("entered-*"))) < 10:
        for task in tasks:
            if task.done():
                task.result()
                pytest.fail("an index finished before every worker reached source ingestion")
        if time.monotonic() >= deadline:
            pytest.fail("ten independent build processes did not enter source ingestion")
        await asyncio.sleep(0.02)
    return {int(path.name.removeprefix("entered-")) for path in wave.glob("entered-*")}


@pytest.mark.asyncio
async def test_ten_processes_build_full_and_delta_graphs_while_parent_queries_continue(tmp_path):
    config = RAGConfig(structural_index_root=str(tmp_path / "indexes"), full_index_concurrency=10)
    parent = isolated_manager(config)
    query_repo = tmp_path / "query-source"
    query_repo.mkdir()
    (query_repo / "query.py").write_text("def query_ready():\n    return True\n")
    parent.index_repository(repo_path=str(query_repo), workspace="workspace", project="query-project",
                            branch="main", commit="query-revision", collection_target="query-seed")
    pool = BuildWorkerPool(config, manager_factory=isolated_manager, runner=observed_build)
    repositories = []
    receipts = []
    try:
        for number in range(10):
            repo = tmp_path / f"repository-{number}"
            repo.mkdir()
            (repo / "service.py").write_text("def service():\n    return 'base'\n")
            (repo / "stable.py").write_text("def stable():\n    return 'unchanged'\n")
            repositories.append(repo)
        for stage in ("full", "delta"):
            wave = tmp_path / stage
            wave.mkdir()
            tasks = []
            started = time.monotonic()
            for number, repo in enumerate(repositories):
                target = f"{stage}-{number}"
                request = {"repo_path": str(repo), "workspace": "workspace", "project": f"project-{number}",
                           "branch": "main", "commit": stage, "collection_target": target}
                if stage == "delta":
                    (repo / "service.py").write_text("def service():\n    return 'changed'\n")
                    request.update(base_revision="full", base_collection_target=f"full-{number}",
                                   base_generation_manifest_sha256=receipts[number]["generation_manifest_sha256"],
                                   changed_paths=["service.py"])
                tasks.append(asyncio.create_task(pool.run("index", {
                    "request": request, "repo_path": str(repo), "collection_target": target,
                    "fixture_wave": stage, "fixture_root": str(tmp_path),
                })))
            try:
                pids = await entered_processes(wave, tasks)
                assert len(pids) == 10 and os.getpid() not in pids
                assert not any(task.done() for task in tasks)
                query_receipt = parent.get_revision_preflight(
                    "workspace", "query-project", "main", "query-revision", collection_target="query-seed",
                )
                assert query_receipt is not None
                with parent.open_reader(
                    workspace="workspace", project="query-project", branch="main", revision="query-revision",
                    collection_target="query-seed",
                    generation_manifest_sha256=query_receipt["generation_manifest_sha256"],
                ) as reader:
                    matches = reader.search_units("query_ready")
                    sources = [reader.get_unit(match["unitId"]) for match in matches
                               if match["recordType"] == "source_unit"]
                    assert any(source["sourceEvidence"] and "return True" in source["unit"]["content"]
                               for source in sources)
                resident_kib = sum(
                    int(line.split()[1]) for pid in pids
                    for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                    if line.startswith("VmRSS:")
                )
            finally:
                (wave / "release").touch()
            receipts = await asyncio.wait_for(asyncio.gather(*tasks), timeout=35)
            print({"wave": stage, "elapsed_seconds": round(time.monotonic() - started, 3),
                   "observed_aggregate_rss_kib": resident_kib, "worker_processes": len(pids)})
            for number, receipt in enumerate(receipts):
                assert receipt["collection_target"] == f"{stage}-{number}"
                assert receipt["document_count"] == 2
                assert receipt["source_tree_sha256"] == attest_repository_source_tree(
                    repositories[number], stage,
                ).tree_sha256
                paths = parent.store.paths_for_target(receipt["collection_target"])
                with sqlite3.connect(f"file:{paths.database}?mode=ro", uri=True) as connection:
                    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
                    bodies = [row[0] for row in connection.execute("SELECT content FROM units WHERE record_type='source_unit'")]
                    assert any("unchanged" in body for body in bodies)
                    expected = "changed" if stage == "delta" else "base"
                    assert any(f"return '{expected}'" in body for body in bodies)
                    assert connection.execute("SELECT count(*) FROM units_fts WHERE units_fts MATCH 'service'").fetchone()[0] > 0
    finally:
        for stage in ("full", "delta"):
            (tmp_path / stage).mkdir(exist_ok=True)
            (tmp_path / stage / "release").touch()
        await pool.close()
        parent.close()


@pytest.mark.asyncio
async def test_review_preparation_worker_preserves_seed_policy_and_exact_proposed_source(tmp_path):
    import json

    config = RAGConfig(structural_index_root=str(tmp_path / "indexes"), full_index_concurrency=1)
    parent = isolated_manager(config)
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "service.py").write_text("def service():\n    return 'target'\n")
    (repository / "stable.py").write_text("def stable():\n    return 'unchanged'\n")
    (repository / "ignored.txt").write_text("This is excluded from graph selection, but stays in source identity.\n")
    parent.index_repository(
        repo_path=str(repository), workspace="workspace", project="project", branch="main",
        commit="target-base", collection_target="review-seed", include_patterns=["*.py"],
    )
    seed = parent.get_revision_preflight("workspace", "project", "main", "target-base",
                                          collection_target="review-seed")
    overlay = tmp_path / "overlay"
    (overlay / "files").mkdir(parents=True)
    proposed = "def service():\n    return 'proposed'\n"
    (overlay / "files" / "service.py").write_text(proposed)
    (overlay / "manifest.json").write_text(json.dumps({"changedFiles": ["service.py"], "deletedFiles": []}))
    wave = tmp_path / "prepare"
    wave.mkdir()
    (wave / "release").touch()
    policy = {"include_patterns": ["*.py"], "exclude_patterns": [], "project_type": None, "source_root": None}
    pool = BuildWorkerPool(config, manager_factory=isolated_manager, runner=observed_build)
    try:
        prepared = await pool.run("prepare_review", {
            "request": {
                "workspace": "workspace", "project": "project", "target_branch": "main",
                "base_revision": "target-base", "source_revision": "proposed-source",
                "target_repo_path": str(repository), "review_overlay_path": str(overlay),
                "index_policy": policy,
                "base_generation_candidates": [{
                    "collection_target": "review-seed", "revision": "target-base",
                    "generation_manifest_sha256": seed["generation_manifest_sha256"],
                }],
            },
            "fixture_wave": "prepare", "fixture_root": str(tmp_path),
        })
        assert prepared["status"] == "ready"
        assert prepared["base_collection_target"] == "review-seed"
        assert prepared["base_generation_revision"] == "target-base"
        assert prepared["base_generation_manifest_sha256"] == seed["generation_manifest_sha256"]
        assert prepared["index_policy"] == policy
        assert prepared["source_revision"] == "proposed-source"
        assert prepared["changed_paths"] == ["service.py"]
        assert len(tuple(wave.glob("entered-*"))) == 1
        with parent.open_reader(
            workspace="workspace", project="project", branch="main", revision="proposed-source",
            collection_target=prepared["collection_target"],
            generation_manifest_sha256=prepared["generation_manifest_sha256"],
        ) as reader:
            source_units = [reader.get_unit(match["unitId"])["unit"]["content"]
                            for match in reader.search_units("service") if match["recordType"] == "source_unit"]
            assert proposed in source_units
            assert all("return 'target'" not in source for source in source_units)
            assert reader.search_units("ignored.txt") == []
        # Construct the expected tree independently, retaining excluded source
        # in the source identity while graph scope remains project-selected.
        expected = tmp_path / "expected"
        expected.mkdir()
        for path in repository.iterdir():
            (expected / path.name).write_bytes(path.read_bytes())
        (expected / "service.py").write_text(proposed)
        assert prepared["source_tree_sha256"] == attest_repository_source_tree(expected, "proposed-source").tree_sha256
        assert (repository / "service.py").read_text() == "def service():\n    return 'target'\n"
    finally:
        await pool.close()
        parent.close()
