"""Exact proposed-tree construction and bounded composite review evidence."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rag_pipeline.api.models import ReviewContextResponse
from rag_pipeline.core.exact_index import RepositoryDeltaRebuildRequired
from rag_pipeline.core.index_manager.manager import RAGIndexManager
from rag_pipeline.core.review_context import (
    LayeredReviewGraphReader,
    ProposedTreeGeneration,
    ProposedTreeReviewContextService,
    ProposedTreeUnavailableError,
    _compact_graph_evidence,
    _review_identity,
    load_review_overlay,
    materialize_proposed_tree,
)
from rag_pipeline.core.source_tree import attest_repository_source_tree
from rag_pipeline.models.config import RAGConfig


def _write_overlay(root, *, changed, deleted=(), bodies=None):
    files = root / "files"
    files.mkdir(parents=True)
    for path, content in (bodies or {}).items():
        destination = files / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({
        "changedFiles": list(changed),
        "deletedFiles": list(deleted),
    }), encoding="utf-8")


def test_review_generation_preparation_is_single_flight(tmp_path):
    overlay = tmp_path / "overlay"
    _write_overlay(
        overlay,
        changed=("src/service.py",),
        bodies={"src/service.py": "def service():\n    return 1\n"},
    )
    representation = {
        "representation_identity": "sha256:" + "1" * 64,
        "index_representation_fingerprint": "sha256:" + "2" * 64,
    }
    manager = SimpleNamespace(
        current_representation_identity=lambda: representation,
    )
    service = ProposedTreeReviewContextService(manager)
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def prepare_generation(**_arguments):
        nonlocal calls
        with calls_lock:
            calls += 1
        entered.set()
        assert release.wait(5)
        return ProposedTreeGeneration(
            collection_target="sealed-review",
            receipt={"generation_manifest_sha256": "a" * 64},
            target_source_tree_sha256="b" * 64,
            overlay_sha256=load_review_overlay(overlay).fingerprint,
            proposed_source_tree_sha256="c" * 64,
            representation_identity=representation["representation_identity"],
            changed_paths=("src/service.py",),
            deleted_paths=(),
            cache_hit=False,
        )

    service.prepare_generation = prepare_generation
    arguments = {
        "target_repo_path": str(tmp_path / "target"),
        "review_overlay_path": str(overlay),
        "workspace": "workspace",
        "project": "project",
        "target_branch": "main",
        "base_revision": "base",
        "source_revision": "source",
        "base_collection_target": "sealed-base",
        "base_generation_manifest_sha256": "d" * 64,
    }
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(service.prepare_generation_singleflight, **arguments)
            for _ in range(8)
        ]
        assert entered.wait(5)
        time.sleep(0.05)
        release.set()
        results = [future.result(timeout=5) for future in futures]

    assert calls == 1
    assert {result.collection_target for result in results} == {"sealed-review"}
    assert sum(result.cache_hit is False for result in results) == 1


def test_prepared_read_session_never_enters_generation_mutation(tmp_path):
    overlay = tmp_path / "overlay"
    _write_overlay(
        overlay,
        changed=("src/service.py",),
        bodies={"src/service.py": "def service():\n    return 1\n"},
    )
    loaded_overlay = load_review_overlay(overlay)
    representation = {
        "representation_identity": "sha256:" + "1" * 64,
        "index_representation_fingerprint": "sha256:" + "2" * 64,
    }
    receipt = {
        "generation_manifest_sha256": "a" * 64,
        "source_tree_sha256": "b" * 64,
        "index_representation_fingerprint": (
            representation["index_representation_fingerprint"]
        ),
        "snapshot_metadata": {
            "kind": "proposed_tree",
            "storage_mode": "exact_policy_selected_proposed_tree",
            "base_revision": "base",
            "base_collection_target": "sealed-base",
            "base_generation_manifest_sha256": "c" * 64,
            "source_revision": "source",
            "target_source_tree_sha256": "d" * 64,
            "overlay_sha256": loaded_overlay.fingerprint,
            "representation_identity": representation[
                "representation_identity"
            ],
            "changed_paths": ["src/service.py"],
            "deleted_paths": [],
        },
    }

    @contextmanager
    def open_reader(**_arguments):
        yield "reader"

    manager = SimpleNamespace(
        current_representation_identity=lambda: representation,
        get_revision_preflight=lambda *_args, **_kwargs: receipt,
        open_reader=open_reader,
    )
    service = ProposedTreeReviewContextService(manager)
    service.prepare_generation = MagicMock(
        side_effect=AssertionError("query attempted graph mutation")
    )

    with service.open_read_session(
        target_repo_path=str(tmp_path / "target"),
        review_overlay_path=str(overlay),
        workspace="workspace",
        project="project",
        target_branch="main",
        base_revision="base",
        source_revision="source",
        base_collection_target="sealed-base",
        base_generation_manifest_sha256="c" * 64,
        review_collection_target="sealed-review",
        review_generation_manifest_sha256="a" * 64,
    ) as session:
        assert session.reader == "reader"

    service.prepare_generation.assert_not_called()


class _Lease:
    @staticmethod
    def assert_owned():
        return None


class _Coordinator:
    @contextmanager
    def acquire(self, *args, **kwargs):
        yield _Lease()

    @staticmethod
    def close():
        return None


def _javascript_relation_projection(reader, path):
    return sorted(
        (
            relation.get("kind"),
            relation.get("source"),
            relation.get("relation"),
            relation.get("target"),
            tuple(relation.get("relatedPaths") or ()),
        )
        for relation in reader.relations_for_paths(
            [path],
            max_relations=200,
        )["relations"]
        if str(relation.get("kind") or "").startswith("javascript-")
    )


def test_materialized_tree_uses_overlay_deletions_and_unchanged_target(tmp_path):
    target = tmp_path / "target"
    (target / "src").mkdir(parents=True)
    (target / "src/changed.py").write_text("old = True\n", encoding="utf-8")
    (target / "src/deleted.py").write_text("gone = False\n", encoding="utf-8")
    (target / "src/unchanged.py").write_text("stable = True\n", encoding="utf-8")
    overlay_root = tmp_path / "overlay"
    _write_overlay(
        overlay_root,
        changed=("src/changed.py", "src/added.py", "src/deleted.py"),
        deleted=("src/deleted.py",),
        bodies={
            "src/changed.py": "old = False\n",
            "src/added.py": "added = True\n",
        },
    )

    destination = tmp_path / "proposed"
    materialize_proposed_tree(
        target,
        load_review_overlay(overlay_root),
        destination,
    )

    assert (destination / "src/changed.py").read_text() == "old = False\n"
    assert (destination / "src/added.py").read_text() == "added = True\n"
    assert not (destination / "src/deleted.py").exists()
    assert (destination / "src/unchanged.py").read_text() == "stable = True\n"
    assert (target / "src/changed.py").read_text() == "old = True\n"


def test_missing_changed_body_never_falls_back_to_target(tmp_path):
    overlay_root = tmp_path / "overlay"
    _write_overlay(
        overlay_root,
        changed=("src/unavailable.py",),
    )

    with pytest.raises(ProposedTreeUnavailableError, match="unavailable.py"):
        load_review_overlay(overlay_root)


def test_review_cache_identity_binds_tenant_revisions_content_and_representation():
    base = {
        "workspace": "workspace",
        "project": "project",
        "target_branch": "main",
        "base_revision": "base",
        "base_collection_target": "cc_base_generation",
        "base_generation_manifest_sha256": "1" * 64,
        "source_revision": "source",
        "target_source_tree_sha256": "a" * 64,
        "overlay_sha256": "b" * 64,
        "representation": {"representation_identity": "sha256:" + "c" * 64},
        "include_patterns": None,
        "exclude_patterns": None,
        "project_type": None,
        "source_root": None,
    }
    original, _ = _review_identity(**base)

    for field, replacement in (
        ("workspace", "other-workspace"),
        ("project", "other-project"),
        ("base_revision", "other-base"),
        ("base_collection_target", "other-base-target"),
        ("base_generation_manifest_sha256", "2" * 64),
        ("source_revision", "other-source"),
        ("target_source_tree_sha256", "d" * 64),
        ("overlay_sha256", "e" * 64),
        ("representation", {"representation_identity": "sha256:" + "f" * 64}),
    ):
        changed = dict(base)
        changed[field] = replacement
        assert _review_identity(**changed)[0] != original


def test_compact_graph_evidence_deduplicates_endpoint_manifests_and_links_source():
    source = {
        "unitId": "unit:source",
        "path": "src/source.py",
        "kind": "function",
        "name": "source",
        "qualifiedName": "src.source.source",
        "startLine": 1,
        "endLine": 20,
        "language": "python",
    }
    target = {
        "unitId": "unit:target",
        "path": "src/target.py",
        "kind": "function",
        "name": "target",
        "qualifiedName": "src.target.target",
        "startLine": 4,
        "endLine": 12,
        "language": "python",
    }
    relations = [
        {
            "evidenceId": "relation:" + f"{index:064x}",
            "kind": "CALLS",
            "source": "src.source.source",
            "relation": "calls",
            "target": "src.target.target",
            "origin": {"path": "src/source.py", "line": index + 1},
            "sourceUnit": source,
            "targetUnit": target,
            "relatedPaths": ["src/source.py", "src/target.py"],
            "attributes": {"dispatch": "direct"},
        }
        for index in range(32)
    ]
    hops = {
        relation["evidenceId"]: index % 3
        for index, relation in enumerate(relations)
    }
    windows = [{
        "evidenceId": "source:unit:target",
        "unitId": "unit:target",
    }]

    nodes, edges = _compact_graph_evidence(relations, hops, windows)

    assert len(nodes) == 2
    assert len(edges) == 32
    assert all("sourceUnit" not in edge and "targetUnit" not in edge for edge in edges)
    target_node = next(node for node in nodes if node["unitId"] == "unit:target")
    assert target_node["sourceEvidenceId"] == "source:unit:target"
    assert len(json.dumps({"nodes": nodes, "relations": edges})) < (
        len(json.dumps(relations)) * 3 // 5
    )


def test_layered_reader_preserves_unit_query_shapes_and_hides_stale_changed_units():
    overlay_reader = MagicMock()
    base_reader = MagicMock()
    overlay_reader.snapshot.return_value = {"kind": "proposed_tree"}
    overlay_reader.query_graph.return_value = {
        "pattern": "symbol_search",
        "results": [{
            "unitId": "overlay-unit",
            "path": "src/changed.py",
            "name": "current_symbol",
        }],
    }
    base_reader.query_graph.return_value = {
        "pattern": "symbol_search",
        "results": [
            {
                "unitId": "stale-unit",
                "path": "src/changed.py",
                "name": "old_symbol",
            },
            {
                "unitId": "stable-unit",
                "path": "src/stable.py",
                "name": "stable_symbol",
            },
        ],
    }
    reader = LayeredReviewGraphReader(
        base_reader,
        overlay_reader,
        ["src/changed.py"],
    )

    response = reader.query_graph(
        "symbol_search",
        "symbol",
        max_results=10,
    )

    assert [unit["unitId"] for unit in response["results"]] == [
        "overlay-unit",
        "stable-unit",
    ]
    assert all("evidenceId" not in unit for unit in response["results"])


def test_review_context_reserves_second_hop_and_returns_exact_source_with_frontier():
    units = {
        name: {
            "unitId": f"unit:{name.lower()}",
            "path": f"src/{name.lower()}.py",
            "kind": "function",
            "name": name,
            "qualifiedName": name,
            "startLine": 1,
            "endLine": 3,
            "language": "python",
        }
        for name in ("A", "B", "C")
    }

    def relation(index, source, target):
        return {
            "evidenceId": "relation:" + f"{index:064x}",
            "kind": "CALLS",
            "source": source,
            "relation": "calls",
            "target": target,
            "origin": {
                "path": units[source]["path"],
                "line": 2,
                "extractor": "ast",
            },
            "sourceUnit": units[source],
            "targetUnit": units[target],
            "relatedPaths": [
                units[source]["path"],
                units[target]["path"],
            ],
        }

    direct = relation(1, "A", "B")
    second_hop = relation(2, "B", "C")

    class Reader:
        @staticmethod
        def snapshot():
            return {"kind": "proposed_tree", "revision": "source"}

        @staticmethod
        def relations_for_paths(_paths, *, max_relations):
            return {
                "anchors": [{
                    "path": "src/a.py",
                    "symbols": [units["A"]],
                    "omittedSymbols": 0,
                }],
                "relations": [direct][:max_relations],
                "coverage": {
                    "state": "complete",
                    "totalRelations": 1,
                    "omittedRelations": 0,
                    "omittedSymbols": 0,
                },
            }

        @staticmethod
        def query_graph(pattern, target, *, max_results):
            results = []
            if target == "A" and pattern in {"callees_of", "relations_of"}:
                results = [direct]
            elif target == "B" and pattern == "callees_of":
                results = [second_hop]
            return {
                "pattern": pattern,
                "target": target,
                "results": results[:max_results],
                "truncated": False,
            }

        @staticmethod
        def search_units(query, *, max_results):
            return [units[query]][:max_results] if query in units else []

        @staticmethod
        def source_detail_for_manifest(unit):
            return {
                "unit": {
                    **unit,
                    "content": (
                        f"def {unit['name']}():\n"
                        f"    return '{unit['name']}-exact-source'\n"
                    ),
                    "contentSha256": unit["name"].lower() * 64,
                },
                "sourceEvidence": True,
            }

    generation = ProposedTreeGeneration(
        collection_target="cc_review",
        receipt={
            "snapshot_metadata": {
                "base_revision": "base",
                "source_revision": "source",
            },
            "generation_manifest_sha256": "a" * 64,
            "index_representation_fingerprint": "sha256:" + "b" * 64,
            "skipped_file_count": 0,
        },
        target_source_tree_sha256="c" * 64,
        overlay_sha256="d" * 64,
        proposed_source_tree_sha256="e" * 64,
        representation_identity="sha256:" + "f" * 64,
        changed_paths=("src/a.py",),
        deleted_paths=(),
        cache_hit=False,
    )

    response = ProposedTreeReviewContextService._read_context(
        Reader(),
        generation,
        ["src/a.py"],
        "Trace A through its callees",
        ["A"],
        max_relations=12,
        max_source_windows=4,
        max_source_characters=4000,
    )

    relations = response["evidence"]["relations"]
    assert any(edge["evidenceId"] == second_hop["evidenceId"] for edge in relations)
    assert next(
        edge for edge in relations
        if edge["evidenceId"] == second_hop["evidenceId"]
    )["hop"] == 2
    assert {item["symbol"] for item in response["evidence"]["frontier"]} >= {
        "B",
        "C",
    }
    assert any(
        "exact-source" in window["content"]
        for window in response["sourceWindows"]
    )
    assert len(json.dumps(response)) < 20_000


def test_review_context_selects_direct_relations_fairly_across_changed_roots():
    paths = ("src/a.py", "src/b.py", "src/c.py")
    units = {
        path: {
            "unitId": f"unit:{path[4]}",
            "path": path,
            "kind": "function",
            "name": path[4].upper(),
            "qualifiedName": path[4].upper(),
            "startLine": 1,
            "endLine": 3,
            "language": "python",
        }
        for path in paths
    }
    target_to_path = {
        unit["qualifiedName"]: path for path, unit in units.items()
    }

    def anchor_relation(path):
        unit = units[path]
        return {
            "evidenceId": "relation:" + f"{900 + paths.index(path):064x}",
            "kind": "CONTAINS",
            "source": path,
            "relation": "contains",
            "target": unit["qualifiedName"],
            "origin": {"path": path, "line": 1, "extractor": "ast"},
            "sourceUnit": unit,
            "targetUnit": unit,
            "relatedPaths": [path],
        }

    def relation(path, target, pattern, variant):
        root_name = target.casefold()
        related = {
            "unitId": f"unit:related:{root_name}:{pattern}:{variant}",
            "path": f"related/{root_name}/{pattern}_{variant}.py",
            "kind": "function",
            "name": f"related_{variant}",
            "qualifiedName": f"related.{root_name}.{pattern}.{variant}",
            "startLine": 1,
            "endLine": 2,
            "language": "python",
        }
        ordinal = (
            paths.index(path) * 100
            + (
                "callers_of",
                "callees_of",
                "references_to",
                "importers_of",
                "inheritors_of",
                "tests_for",
                "framework_relations",
            ).index(pattern) * 10
            + variant
            + 1
        )
        return {
            "evidenceId": "relation:" + f"{ordinal:064x}",
            "kind": "REFERENCES",
            "source": target,
            "relation": "references",
            "target": related["qualifiedName"],
            "origin": {"path": path, "line": variant + 1, "extractor": "ast"},
            "sourceUnit": units[path],
            "targetUnit": related,
            "relatedPaths": [path, related["path"]],
            "attributes": {"queryPattern": pattern, "rootPath": path},
        }

    class Reader:
        def __init__(self):
            self.query_calls = []

        @staticmethod
        def snapshot():
            return {"kind": "proposed_tree", "revision": "source"}

        @staticmethod
        def relations_for_paths(requested_paths, *, max_relations):
            return {
                "anchors": [
                    {
                        "path": path,
                        "symbols": [units[path]],
                        "omittedSymbols": 0,
                    }
                    for path in requested_paths
                ],
                "relations": [anchor_relation(path) for path in sorted(paths)][
                    :max_relations
                ],
                "coverage": {
                    "state": "complete",
                    "totalRelations": 3,
                    "omittedRelations": 0,
                    "omittedSymbols": 0,
                },
            }

        def query_graph(self, pattern, target, *, max_results):
            self.query_calls.append((pattern, target))
            path = target_to_path.get(target)
            results = (
                [relation(path, target, pattern, variant) for variant in range(3)]
                if path
                else []
            )
            return {
                "pattern": pattern,
                "target": target,
                "results": results[:max_results],
                "truncated": len(results) > max_results,
            }

        @staticmethod
        def search_units(_query, *, max_results):
            return []

        @staticmethod
        def source_detail_for_manifest(unit):
            return {
                "unit": {
                    **unit,
                    "content": f"def {unit['name']}():\n    return 'exact-source'\n",
                    "contentSha256": "a" * 64,
                },
                "sourceEvidence": True,
            }

    generation = ProposedTreeGeneration(
        collection_target="cc_review",
        receipt={
            "snapshot_metadata": {
                "base_revision": "base",
                "source_revision": "source",
            },
            "generation_manifest_sha256": "b" * 64,
            "index_representation_fingerprint": "sha256:" + "c" * 64,
            "skipped_file_count": 0,
        },
        target_source_tree_sha256="d" * 64,
        overlay_sha256="e" * 64,
        proposed_source_tree_sha256="f" * 64,
        representation_identity="sha256:" + "1" * 64,
        changed_paths=paths,
        deleted_paths=(),
        cache_hit=False,
    )

    responses = []
    query_orders = []
    for focus_paths in (paths, tuple(reversed(paths))):
        reader = Reader()
        responses.append(ProposedTreeReviewContextService._read_context(
            reader,
            generation,
            focus_paths,
            "Trace changed roots across dependency categories",
            (),
            max_relations=9,
            max_source_windows=6,
            max_source_characters=4000,
        ))
        query_orders.append(reader.query_calls)

    expected_query_order = [
        ("callers_of", "A"),
        ("callees_of", "B"),
        ("references_to", "C"),
    ]
    assert query_orders == [expected_query_order, expected_query_order]
    relation_sets = [response["evidence"]["relations"] for response in responses]
    assert [edge["evidenceId"] for edge in relation_sets[0]] == [
        edge["evidenceId"] for edge in relation_sets[1]
    ]
    anchor_edges = [edge for edge in relation_sets[0] if edge["hop"] == 0]
    direct_edges = [edge for edge in relation_sets[0] if edge["hop"] == 1]
    assert len(anchor_edges) == 3
    assert len(direct_edges) == 3
    assert {edge["attributes"]["rootPath"] for edge in direct_edges} == set(paths)
    assert {edge["attributes"]["queryPattern"] for edge in direct_edges} == {
        "callers_of",
        "callees_of",
        "references_to",
    }
    assert all(
        "exact-source" in window["content"]
        for window in responses[0]["sourceWindows"]
    )


def test_review_context_indexes_and_caches_exact_proposed_topology(tmp_path):
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_python")
    target = tmp_path / "target"
    (target / "src").mkdir(parents=True)
    (target / "src/service.py").write_text(
        "def run():\n"
        "    # The body is intentionally large enough for semantic AST admission.\n"
        "    value = old_dependency()\n"
        "    if value is None:\n"
        "        return 'missing'\n"
        "    if not value:\n"
        "        return 'empty'\n"
        "    # Preserve a concrete result for the caller.\n"
        "    return value\n"
        "\n",
        encoding="utf-8",
    )
    (target / "src/old.py").write_text(
        "def old_dependency():\n"
        "    # Target-only implementation that must disappear from the graph.\n"
        "    return 'old dependency result'\n",
        encoding="utf-8",
    )
    (target / "src/stable.py").write_text(
        "def stable():\n    return True\n",
        encoding="utf-8",
    )
    overlay_root = tmp_path / "overlay"
    _write_overlay(
        overlay_root,
        changed=("src/service.py", "src/new.py", "src/old.py"),
        deleted=("src/old.py",),
        bodies={
            "src/service.py": (
                "def run():\n"
                "    # Proposed implementation used to prove target topology is stale.\n"
                "    value = proposed_dependency()\n"
                "    if value is None:\n"
                "        return 'missing'\n"
                "    if not value:\n"
                "        return 'empty'\n"
                "    # Preserve a concrete result for the caller.\n"
                "    return value\n"
                "\n"
            ),
            "src/new.py": (
                "def proposed_dependency():\n"
                "    # Exact related proposed source returned to the reviewer.\n"
                "    return 'new dependency result'\n"
            ),
        },
    )
    manager = RAGIndexManager(RAGConfig(
        structural_index_root=str(tmp_path / "structural"),
    ))
    original_coordinator = manager._mutation_coordinator
    original_coordinator.close()
    manager._mutation_coordinator = _Coordinator()
    manager.plugin_catalog = None
    manager.plugin_runtime = None
    manager.plugin_selector = None
    service = ProposedTreeReviewContextService(manager)
    base_collection_target = "cc_test_base_generation"
    manager.index_repository(
        repo_path=str(target),
        workspace="workspace",
        project="project",
        branch="main",
        commit="base-revision",
        collection_target=base_collection_target,
    )
    base_receipt = manager.get_revision_preflight(
        "workspace",
        "project",
        "main",
        "base-revision",
        collection_target=base_collection_target,
    )
    assert base_receipt is not None
    arguments = {
        "target_repo_path": str(target),
        "review_overlay_path": str(overlay_root),
        "workspace": "workspace",
        "project": "project",
        "target_branch": "main",
        "base_revision": "base-revision",
        "base_collection_target": base_collection_target,
        "base_generation_manifest_sha256": base_receipt[
            "generation_manifest_sha256"
        ],
        "source_revision": "source-revision",
        "focus_paths": ["src/service.py"],
        "question": "What does run call and what is its blast radius?",
        "focus_symbols": ["run"],
    }
    preparation_arguments = {
        key: value
        for key, value in arguments.items()
        if key not in {"focus_paths", "question", "focus_symbols"}
    }
    try:
        generation = service.prepare_generation_singleflight(
            **preparation_arguments
        )
        arguments["review_collection_target"] = generation.collection_target
        arguments["review_generation_manifest_sha256"] = generation.receipt[
            "generation_manifest_sha256"
        ]
        first = service.review_context(**arguments)
        second = service.review_context(**arguments)
    finally:
        manager.close()

    assert first["snapshot"]["kind"] == "proposed_tree"
    assert ReviewContextResponse.model_validate(first).status == "ready"
    assert first["coverage"]["treeState"] == "exact"
    assert first["provenance"]["cacheHit"] is True
    assert second["provenance"]["cacheHit"] is True
    assert first["provenance"]["collectionTarget"] == second["provenance"][
        "collectionTarget"
    ]
    assert first["evidence"]["format"] == "normalized_nodes_edges"
    assert all(
        "sourceUnit" not in relation and "targetUnit" not in relation
        for relation in first["evidence"]["relations"]
    )
    relation_text = json.dumps(first["evidence"]["relations"])
    assert "proposed_dependency" in relation_text
    assert "old_dependency" not in relation_text
    assert all(
        window["path"] != "src/service.py"
        for window in first["sourceWindows"]
    )
    assert any(
        window["path"] == "src/new.py"
        and "return 'new" in window["content"]
        for window in first["sourceWindows"]
    ), first["sourceWindows"]
    assert "src/old.py" in first["changed"]["deletedPaths"]


def test_proposed_tree_builds_exact_delta_from_sealed_base(
    tmp_path,
):
    target = tmp_path / "target"
    target.mkdir()
    (target / "service.php").write_text("<?php\n", encoding="utf-8")
    overlay_root = tmp_path / "overlay"
    _write_overlay(
        overlay_root,
        changed=("schema/new.graphqls",),
        bodies={"schema/new.graphqls": "type Query { value: String }\n"},
    )

    base_source = attest_repository_source_tree(target, "base-revision")
    base_receipt = {
        "generation_manifest_sha256": "a" * 64,
        "source_tree_sha256": base_source.tree_sha256,
        "index_representation_fingerprint": "sha256:representation",
        "index_include_patterns": ["service.php", "schema/new.graphqls"],
        "index_exclude_patterns": ["vendor/**"],
        "index_selection_policy_sha256": "b" * 64,
    }
    proposed_receipt = None
    manager = MagicMock()
    manager.current_representation_identity.return_value = {
        "index_representation_fingerprint": "sha256:representation",
        "representation_identity": "sha256:" + "c" * 64,
    }
    base_reader = manager.open_reader.return_value.__enter__.return_value
    base_reader.repository_facts.return_value = {
        "revision": "base-revision",
        "paths": ["service.php"],
        "projectType": "magento",
        "sourceRoot": "src",
    }
    manager.loader.iter_repository_files.return_value = [
        Path("schema/new.graphqls"),
    ]

    def preflight(_workspace, _project, _branch, revision, **_kwargs):
        if revision == "base-revision":
            return base_receipt
        return proposed_receipt

    def delta_build(**kwargs):
        nonlocal proposed_receipt
        proposed_receipt = {
            "generation_manifest_sha256": "d" * 64,
            "source_tree_sha256": "e" * 64,
            "snapshot_metadata": kwargs["snapshot_metadata"],
        }

    manager.get_revision_preflight.side_effect = preflight
    manager.index_proposed_tree_delta.side_effect = delta_build

    service = ProposedTreeReviewContextService(manager)
    generation = service.prepare_generation(
        target_repo_path=str(target),
        review_overlay_path=str(overlay_root),
        workspace="workspace",
        project="project",
        target_branch="main",
        base_revision="base-revision",
        source_revision="source-revision",
        base_collection_target="cc_base",
        base_generation_manifest_sha256="a" * 64,
    )

    manager.index_repository.assert_not_called()
    delta = manager.index_proposed_tree_delta.call_args.kwargs
    assert delta["base_revision"] == "base-revision"
    assert delta["base_collection_target"] == "cc_base"
    assert delta["base_generation_manifest_sha256"] == "a" * 64
    assert delta["changed_paths"] == ("schema/new.graphqls",)
    assert delta["deleted_paths"] == ()
    assert delta["source_tree_sha256"] is None
    assert delta["project_type"] == "magento"
    assert delta["source_root"] == "src"
    assert delta["source_tree_exclusively_owned"] is True
    assert delta["snapshot_metadata"]["kind"] == "proposed_tree"
    assert delta["snapshot_metadata"]["storage_mode"] == (
        "exact_policy_selected_proposed_tree"
    )
    assert generation.receipt["generation_manifest_sha256"] == "d" * 64
    assert generation.proposed_source_tree_sha256 == "e" * 64

    target.rename(tmp_path / "target-after-first-build")
    cached = service.prepare_generation(
        target_repo_path=str(target),
        review_overlay_path=str(overlay_root),
        workspace="workspace",
        project="project",
        target_branch="main",
        base_revision="base-revision",
        source_revision="source-revision",
        base_collection_target="cc_base",
        base_generation_manifest_sha256="a" * 64,
    )
    assert cached.cache_hit is True
    assert manager.index_proposed_tree_delta.call_count == 1


@pytest.mark.parametrize(
    "reason",
    (
        "proposed changes alter repository-aware structural plugin selection",
        "sealed base generation lacks resumable architecture state for: custom",
    ),
)
def test_proposed_tree_falls_back_to_full_sealed_policy_build(
    tmp_path,
    reason,
):
    target = tmp_path / "target"
    (target / "src").mkdir(parents=True)
    (target / "src/stable.js").write_text(
        "export const stable = true;\n",
        encoding="utf-8",
    )
    overlay_root = tmp_path / "overlay"
    _write_overlay(
        overlay_root,
        changed=("src/changed.js",),
        bodies={"src/changed.js": "export const changed = true;\n"},
    )
    base_source = attest_repository_source_tree(target, "base-revision")
    base_receipt = {
        "generation_manifest_sha256": "a" * 64,
        "source_tree_sha256": base_source.tree_sha256,
        "index_representation_fingerprint": "sha256:representation",
        "index_include_patterns": ["src/**"],
        "index_exclude_patterns": ["src/generated/**"],
        "index_selection_policy_sha256": "b" * 64,
    }
    proposed_receipt = None
    manager = MagicMock()
    manager.current_representation_identity.return_value = {
        "index_representation_fingerprint": "sha256:representation",
        "representation_identity": "sha256:" + "c" * 64,
    }
    base_reader = manager.open_reader.return_value.__enter__.return_value
    base_reader.repository_facts.return_value = {
        "revision": "base-revision",
        "paths": ["src/stable.js"],
        "projectType": "custom",
        "sourceRoot": "src",
    }
    manager.loader.iter_repository_files.return_value = [
        Path("src/changed.js"),
    ]

    def preflight(_workspace, _project, _branch, revision, **_kwargs):
        return base_receipt if revision == "base-revision" else proposed_receipt

    def full_build(**kwargs):
        nonlocal proposed_receipt
        proposed_receipt = {
            "generation_manifest_sha256": "d" * 64,
            "source_tree_sha256": "e" * 64,
            "snapshot_metadata": kwargs["snapshot_metadata"],
        }

    manager.get_revision_preflight.side_effect = preflight
    manager.index_proposed_tree_delta.side_effect = (
        RepositoryDeltaRebuildRequired(reason)
    )
    manager.index_repository.side_effect = full_build

    generation = ProposedTreeReviewContextService(manager).prepare_generation(
        target_repo_path=str(target),
        review_overlay_path=str(overlay_root),
        workspace="workspace",
        project="project",
        target_branch="main",
        base_revision="base-revision",
        source_revision="source-revision",
        base_collection_target="cc_base",
        base_generation_manifest_sha256="a" * 64,
    )

    fallback = manager.index_repository.call_args.kwargs
    assert fallback["include_patterns"] == ["src/**"]
    assert fallback["exclude_patterns"] == ["src/generated/**"]
    assert fallback["project_type"] == "custom"
    assert fallback["source_root"] == "src"
    assert fallback["source_tree_exclusively_owned"] is True
    assert "repository_fact_paths" not in fallback
    assert generation.proposed_source_tree_sha256 == "e" * 64


def test_proposed_delta_preserves_javascript_relations_to_unchanged_file(
    tmp_path,
):
    target = tmp_path / "target"
    (target / "src").mkdir(parents=True)
    (target / "src/Button.jsx").write_text(
        "export default function Button({label}) {\n"
        "  return <button>{label}</button>;\n"
        "}\n",
        encoding="utf-8",
    )
    (target / "src/App.jsx").write_text(
        "export default function App() { return <main />; }\n",
        encoding="utf-8",
    )
    overlay_root = tmp_path / "overlay"
    _write_overlay(
        overlay_root,
        changed=("src/App.jsx",),
        bodies={
            "src/App.jsx": (
                "import Button from './Button';\n"
                "export default function App() {\n"
                "  return <Button label='ok' />;\n"
                "}\n"
            ),
        },
    )
    full_proposed = tmp_path / "full-proposed"
    materialize_proposed_tree(
        target,
        load_review_overlay(overlay_root),
        full_proposed,
    )

    manager = RAGIndexManager(RAGConfig(
        structural_index_root=str(tmp_path / "structural"),
    ))
    manager._mutation_coordinator.close()
    manager._mutation_coordinator = _Coordinator()
    try:
        manager.index_repository(
            repo_path=str(target),
            workspace="workspace",
            project="project",
            branch="main",
            commit="base-revision",
            collection_target="cc_js_base",
        )
        base_receipt = manager.get_revision_preflight(
            "workspace",
            "project",
            "main",
            "base-revision",
            collection_target="cc_js_base",
        )
        assert base_receipt is not None

        service = ProposedTreeReviewContextService(manager)
        generation = service.prepare_generation(
            target_repo_path=str(target),
            review_overlay_path=str(overlay_root),
            workspace="workspace",
            project="project",
            target_branch="main",
            base_revision="base-revision",
            source_revision="source-revision",
            base_collection_target="cc_js_base",
            base_generation_manifest_sha256=base_receipt[
                "generation_manifest_sha256"
            ],
        )
        with service.open_read_session(
            target_repo_path=str(target),
            review_overlay_path=str(overlay_root),
            workspace="workspace",
            project="project",
            target_branch="main",
            base_revision="base-revision",
            source_revision="source-revision",
            base_collection_target="cc_js_base",
            base_generation_manifest_sha256=base_receipt[
                "generation_manifest_sha256"
            ],
            review_collection_target=generation.collection_target,
            review_generation_manifest_sha256=generation.receipt[
                "generation_manifest_sha256"
            ],
        ) as session:
            assert not isinstance(session.reader, LayeredReviewGraphReader)
            session_relations = _javascript_relation_projection(
                session.reader,
                "src/App.jsx",
            )
        manager.index_repository(
            repo_path=str(full_proposed),
            workspace="workspace",
            project="project",
            branch="main",
            commit="source-revision",
            collection_target="cc_js_full",
        )
        full_receipt = manager.get_revision_preflight(
            "workspace",
            "project",
            "main",
            "source-revision",
            collection_target="cc_js_full",
        )
        assert full_receipt is not None

        with (
            manager.open_reader(
                workspace="workspace",
                project="project",
                branch="main",
                revision="source-revision",
                generation_manifest_sha256=generation.receipt[
                    "generation_manifest_sha256"
                ],
                collection_target=generation.collection_target,
            ) as delta_reader,
            manager.open_reader(
                workspace="workspace",
                project="project",
                branch="main",
                revision="source-revision",
                generation_manifest_sha256=full_receipt[
                    "generation_manifest_sha256"
                ],
                collection_target="cc_js_full",
            ) as full_reader,
        ):
            delta_relations = _javascript_relation_projection(
                delta_reader,
                "src/App.jsx",
            )
            full_relations = _javascript_relation_projection(
                full_reader,
                "src/App.jsx",
            )
    finally:
        manager.close()

    assert delta_relations == full_relations
    assert session_relations == full_relations
    assert {
        relation[0] for relation in delta_relations
    } >= {
        "javascript-component-resolution",
        "javascript-jsx-prop-contract",
    }


def test_repository_plugin_selection_change_uses_full_proposed_fallback(
    tmp_path,
):
    target = tmp_path / "target"
    target.mkdir()
    overlay_root = tmp_path / "overlay"
    _write_overlay(
        overlay_root,
        changed=("src/App.jsx",),
        bodies={
            "src/App.jsx": (
                "export default function App() { return <main />; }\n"
            ),
        },
    )
    manager = RAGIndexManager(RAGConfig(
        structural_index_root=str(tmp_path / "structural"),
    ))
    manager._mutation_coordinator.close()
    manager._mutation_coordinator = _Coordinator()
    try:
        manager.index_repository(
            repo_path=str(target),
            workspace="workspace",
            project="project",
            branch="main",
            commit="base-revision",
            collection_target="cc_selection_base",
        )
        base_receipt = manager.get_revision_preflight(
            "workspace",
            "project",
            "main",
            "base-revision",
            collection_target="cc_selection_base",
        )
        assert base_receipt is not None
        assert base_receipt["plugin_ids"] == []

        original_delta = manager.index_proposed_tree_delta
        original_full = manager.index_repository
        manager.index_proposed_tree_delta = MagicMock(wraps=original_delta)
        manager.index_repository = MagicMock(wraps=original_full)
        generation = ProposedTreeReviewContextService(
            manager
        ).prepare_generation(
            target_repo_path=str(target),
            review_overlay_path=str(overlay_root),
            workspace="workspace",
            project="project",
            target_branch="main",
            base_revision="base-revision",
            source_revision="source-revision",
            base_collection_target="cc_selection_base",
            base_generation_manifest_sha256=base_receipt[
                "generation_manifest_sha256"
            ],
        )
    finally:
        manager.close()

    manager.index_proposed_tree_delta.assert_called_once()
    manager.index_repository.assert_called_once()
    assert generation.receipt["plugin_ids"] == ["javascript"]


def test_unrestorable_repository_plugin_uses_full_proposed_fallback(tmp_path):
    target = tmp_path / "target"
    (target / "src").mkdir(parents=True)
    (target / "src/App.jsx").write_text(
        "export default function App() { return <main />; }\n",
        encoding="utf-8",
    )
    overlay_root = tmp_path / "overlay"
    _write_overlay(
        overlay_root,
        changed=("src/App.jsx",),
        bodies={
            "src/App.jsx": (
                "export default function App() { return <section />; }\n"
            ),
        },
    )
    manager = RAGIndexManager(RAGConfig(
        structural_index_root=str(tmp_path / "structural"),
    ))
    manager._mutation_coordinator.close()
    manager._mutation_coordinator = _Coordinator()
    try:
        manager.index_repository(
            repo_path=str(target),
            workspace="workspace",
            project="project",
            branch="main",
            commit="base-revision",
            collection_target="cc_unrestorable_base",
        )
        base_receipt = manager.get_revision_preflight(
            "workspace",
            "project",
            "main",
            "base-revision",
            collection_target="cc_unrestorable_base",
        )
        assert base_receipt is not None
        actual_catalog = manager.plugin_catalog
        assert actual_catalog is not None
        manager.plugin_catalog = SimpleNamespace(
            registry=actual_catalog.registry,
            implementation_fingerprint=(
                actual_catalog.implementation_fingerprint
            ),
            implementation=lambda _plugin_id: SimpleNamespace(),
        )
        original_delta = manager.index_proposed_tree_delta
        original_full = manager.index_repository
        manager.index_proposed_tree_delta = MagicMock(wraps=original_delta)
        manager.index_repository = MagicMock(wraps=original_full)

        generation = ProposedTreeReviewContextService(
            manager
        ).prepare_generation(
            target_repo_path=str(target),
            review_overlay_path=str(overlay_root),
            workspace="workspace",
            project="project",
            target_branch="main",
            base_revision="base-revision",
            source_revision="source-revision",
            base_collection_target="cc_unrestorable_base",
            base_generation_manifest_sha256=base_receipt[
                "generation_manifest_sha256"
            ],
        )
    finally:
        manager.close()

    manager.index_proposed_tree_delta.assert_called_once()
    manager.index_repository.assert_called_once()
    assert generation.receipt["plugin_ids"] == ["javascript"]


def test_proposed_overlay_uses_sealed_profile_and_selection_policy(tmp_path):
    target = tmp_path / "target"
    (target / "src").mkdir(parents=True)
    (target / "vendor").mkdir()
    (target / "src/kept.py").write_text(
        "def kept():\n    return 'base'\n",
        encoding="utf-8",
    )
    (target / "vendor/excluded.py").write_text(
        "def excluded():\n    return 'base'\n",
        encoding="utf-8",
    )
    overlay_root = tmp_path / "overlay"
    _write_overlay(
        overlay_root,
        changed=("src/kept.py", "vendor/excluded.py"),
        bodies={
            "src/kept.py": "def kept():\n    return 'proposed'\n",
            "vendor/excluded.py": (
                "def excluded():\n    return 'proposed'\n"
            ),
        },
    )

    manager = RAGIndexManager(RAGConfig(
        structural_index_root=str(tmp_path / "structural"),
    ))
    manager._mutation_coordinator.close()
    manager._mutation_coordinator = _Coordinator()
    manager.plugin_catalog = None
    manager.plugin_runtime = None
    manager.plugin_selector = None
    try:
        manager.index_repository(
            repo_path=str(target),
            workspace="workspace",
            project="project",
            branch="main",
            commit="base-revision",
            collection_target="cc_profile_base",
            include_patterns=["src/**"],
            project_type="sealed-framework",
            source_root="src",
        )
        base_receipt = manager.get_revision_preflight(
            "workspace",
            "project",
            "main",
            "base-revision",
            collection_target="cc_profile_base",
        )
        assert base_receipt is not None

        generation = ProposedTreeReviewContextService(
            manager
        ).prepare_generation(
            target_repo_path=str(target),
            review_overlay_path=str(overlay_root),
            workspace="workspace",
            project="project",
            target_branch="main",
            base_revision="base-revision",
            source_revision="source-revision",
            base_collection_target="cc_profile_base",
            base_generation_manifest_sha256=base_receipt[
                "generation_manifest_sha256"
            ],
            project_type="drifted-live-profile",
            source_root="elsewhere",
        )
        with manager.open_reader(
            workspace="workspace",
            project="project",
            branch="main",
            revision="source-revision",
            generation_manifest_sha256=generation.receipt[
                "generation_manifest_sha256"
            ],
            collection_target=generation.collection_target,
        ) as overlay_reader:
            facts = overlay_reader.repository_facts()
            kept = overlay_reader.query_graph(
                "file_summary",
                "src/kept.py",
            )
            excluded = overlay_reader.query_graph(
                "file_summary",
                "vendor/excluded.py",
            )
    finally:
        manager.close()

    assert generation.receipt["snapshot_metadata"]["project_type"] == (
        "sealed-framework"
    )
    assert generation.receipt["snapshot_metadata"]["source_root"] == "src"
    assert generation.receipt["snapshot_metadata"][
        "selected_overlay_paths"
    ] == ["src/kept.py"]
    assert facts["projectType"] == "sealed-framework"
    assert facts["sourceRoot"] == "src"
    assert facts["paths"] == ["src/kept.py"]
    assert kept["results"]
    assert excluded["results"] == []


def test_proposed_tree_delta_matches_changed_topology_from_full_oracle(
    tmp_path,
):
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_python")
    target = tmp_path / "target"
    (target / "src").mkdir(parents=True)
    (target / "src/service.py").write_text(
        "from src.old import old_dependency\n"
        "\n"
        "def run(value):\n"
        "    # Keep enough syntax and source text for semantic AST admission.\n"
        "    result = old_dependency(value)\n"
        "    if result is None:\n"
        "        return 'missing'\n"
        "    if not result:\n"
        "        return 'empty'\n"
        "    return result\n",
        encoding="utf-8",
    )
    (target / "src/old.py").write_text(
        "def old_dependency(value):\n"
        "    # This implementation is deleted from the proposed topology.\n"
        "    return f'old:{value}'\n",
        encoding="utf-8",
    )
    (target / "src/stable.py").write_text(
        "def stable(value):\n"
        "    # This file proves unchanged base units survive the delta exactly.\n"
        "    normalized = str(value).strip()\n"
        "    if not normalized:\n"
        "        return 'empty'\n"
        "    return normalized\n",
        encoding="utf-8",
    )
    overlay_root = tmp_path / "overlay"
    _write_overlay(
        overlay_root,
        changed=("src/service.py", "src/new.py", "src/old.py"),
        deleted=("src/old.py",),
        bodies={
            "src/service.py": (
                "from src.new import proposed_dependency\n"
                "\n"
                "def run(value):\n"
                "    # The proposed call must resolve to the newly added file.\n"
                "    result = proposed_dependency(value)\n"
                "    if result is None:\n"
                "        return 'missing'\n"
                "    if not result:\n"
                "        return 'empty'\n"
                "    return result\n"
            ),
            "src/new.py": (
                "def proposed_dependency(value):\n"
                "    # Added implementation used by the proposed service.\n"
                "    return f'new:{value}'\n"
            ),
        },
    )
    independently_materialized = tmp_path / "proposed-full"
    materialize_proposed_tree(
        target,
        load_review_overlay(overlay_root),
        independently_materialized,
    )

    manager = RAGIndexManager(RAGConfig(
        structural_index_root=str(tmp_path / "structural"),
    ))
    original_coordinator = manager._mutation_coordinator
    original_coordinator.close()
    manager._mutation_coordinator = _Coordinator()
    manager.plugin_catalog = None
    manager.plugin_runtime = None
    manager.plugin_selector = None
    base_collection_target = "cc_oracle_base_generation"
    full_collection_target = "cc_oracle_full_proposed_generation"
    try:
        manager.index_repository(
            repo_path=str(target),
            workspace="workspace",
            project="project",
            branch="main",
            commit="base-revision",
            collection_target=base_collection_target,
        )
        base_receipt = manager.get_revision_preflight(
            "workspace",
            "project",
            "main",
            "base-revision",
            collection_target=base_collection_target,
        )
        assert base_receipt is not None

        service = ProposedTreeReviewContextService(manager)
        generation = service.prepare_generation_singleflight(
            target_repo_path=str(target),
            review_overlay_path=str(overlay_root),
            workspace="workspace",
            project="project",
            target_branch="main",
            base_revision="base-revision",
            source_revision="source-revision",
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=base_receipt[
                "generation_manifest_sha256"
            ],
        )
        proposed_context = service.review_context(
            target_repo_path=str(target),
            review_overlay_path=str(overlay_root),
            workspace="workspace",
            project="project",
            target_branch="main",
            base_revision="base-revision",
            source_revision="source-revision",
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=base_receipt[
                "generation_manifest_sha256"
            ],
            review_collection_target=generation.collection_target,
            review_generation_manifest_sha256=generation.receipt[
                "generation_manifest_sha256"
            ],
            focus_paths=["src/service.py"],
            focus_symbols=["run", "stable"],
            question="What does run call and how is stable related?",
        )
        overlay_receipt = manager.get_revision_preflight(
            "workspace",
            "project",
            "main",
            "source-revision",
            collection_target=proposed_context["provenance"]["collectionTarget"],
        )
        assert overlay_receipt is not None

        manager.index_repository(
            repo_path=str(independently_materialized),
            workspace="workspace",
            project="project",
            branch="main",
            commit="source-revision",
            collection_target=full_collection_target,
        )
        full_receipt = manager.get_revision_preflight(
            "workspace",
            "project",
            "main",
            "source-revision",
            collection_target=full_collection_target,
        )
        assert full_receipt is not None

    finally:
        manager.close()

    assert overlay_receipt["unit_count"] == full_receipt["unit_count"]
    assert overlay_receipt["relation_count"] == full_receipt["relation_count"]
    assert overlay_receipt["snapshot_metadata"]["storage_mode"] == (
        "exact_policy_selected_proposed_tree"
    )
    relation_text = json.dumps(proposed_context["evidence"]["relations"])
    assert "proposed_dependency" in relation_text
    assert "old_dependency" not in relation_text
    focus_text = json.dumps(proposed_context["changed"]["focusMatches"])
    assert "stable" in focus_text
