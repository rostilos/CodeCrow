"""
Unit tests for utils.dependency_graph — FileNode, FileRelationship,
DependencyGraphBuilder, create_smart_batches, build_dependency_aware_batches.
"""
import pytest
from types import SimpleNamespace
from collections import defaultdict
from utils.dependency_graph import (
    FileNode,
    FileRelationship,
    DependencyGraphBuilder,
    create_smart_batches,
    create_smart_batches_async,
    build_dependency_aware_batches,
)


# ── FileNode ─────────────────────────────────────────────────

class TestFileNode:
    def test_defaults(self):
        n = FileNode(path="a.py", priority="MEDIUM")
        assert n.path == "a.py"
        assert n.priority == "MEDIUM"
        assert n.relationship_strength == 0.0
        assert isinstance(n.related_files, set)

    def test_custom_fields(self):
        n = FileNode(path="b.java", priority="HIGH", focus_areas=["security"])
        assert n.priority == "HIGH"
        assert n.focus_areas == ["security"]


class TestFileRelationship:
    def test_creation(self):
        r = FileRelationship(
            source_file="a.py", target_file="b.py",
            relationship_type="import", matched_on="module_b", strength=0.9
        )
        assert r.source_file == "a.py"
        assert r.matched_on == "module_b"


# ── DependencyGraphBuilder basics ───────────────────────────

class TestDependencyGraphBuilderBasic:
    def test_init(self):
        b = DependencyGraphBuilder()
        assert b.rag_client is None
        assert len(b.nodes) == 0

    def test_calculate_strength_capped(self):
        b = DependencyGraphBuilder()
        b.relationships = [
            FileRelationship(source_file="a.py", target_file="b.py", relationship_type="import", matched_on="x", strength=3.0),
            FileRelationship(source_file="a.py", target_file="c.py", relationship_type="import", matched_on="y", strength=3.0),
        ]
        strength = b._calculate_strength("a.py", {"b.py", "c.py"})
        assert strength == 5.0  # capped at 5

    def test_get_relationship_summary_empty(self):
        b = DependencyGraphBuilder()
        s = b.get_relationship_summary()
        assert s["total_files"] == 0
        assert s["total_relationships"] == 0


# ── build_graph_from_enrichment ──────────────────────────────

def _make_file(path, focus_areas=None):
    return SimpleNamespace(path=path, focus_areas=focus_areas or [])


def _make_group(priority, files):
    return SimpleNamespace(priority=priority, files=files)


def _make_enrichment(relationships=None, file_metadata=None):
    rels = []
    if relationships:
        for r in relationships:
            rels.append(SimpleNamespace(
                sourceFile=r[0], targetFile=r[1],
                relationshipType=SimpleNamespace(value=r[2]),
                matchedOn=r[3] if len(r) > 3 else None,
                strength=r[4] if len(r) > 4 else 0.8
            ))
    metas = []
    if file_metadata:
        for m in file_metadata:
            metas.append(SimpleNamespace(
                path=m["path"],
                imports=m.get("imports", []),
                extendsClasses=m.get("extends", []),
                implementsInterfaces=m.get("implements", []),
            ))
    return SimpleNamespace(
        relationships=rels,
        fileMetadata=metas,
        has_data=lambda: bool(rels or metas),
        fileContents=None,
    )


class TestBuildGraphFromEnrichment:
    def test_basic(self):
        groups = [
            _make_group("HIGH", [_make_file("src/a.py")]),
            _make_group("MEDIUM", [_make_file("src/b.py")]),
        ]
        enrichment = _make_enrichment(
            relationships=[("src/a.py", "src/b.py", "IMPORTS", "module_b")]
        )
        b = DependencyGraphBuilder()
        b.build_graph_from_enrichment(groups, enrichment)
        assert "src/a.py" in b.nodes
        assert "src/b.py" in b.nodes
        assert len(b.relationships) > 0

    def test_no_relationships(self):
        groups = [_make_group("LOW", [_make_file("x.py")])]
        enrichment = _make_enrichment()
        b = DependencyGraphBuilder()
        b.build_graph_from_enrichment(groups, enrichment)
        assert "x.py" in b.nodes
        assert len(b.relationships) == 0


# ── _build_basic_graph ───────────────────────────────────────

class TestBuildBasicGraph:
    def test_directory_grouping(self):
        groups = [
            _make_group("HIGH", [_make_file("src/a.py"), _make_file("src/b.py")]),
        ]
        b = DependencyGraphBuilder()
        nodes = b._build_basic_graph(groups)
        assert "src/a.py" in nodes
        assert "src/b.py" in nodes["src/a.py"].related_files


# ── get_connected_components ─────────────────────────────────

class TestGetConnectedComponents:
    def test_single_component(self):
        b = DependencyGraphBuilder()
        b.nodes["a"] = FileNode(path="a", priority="HIGH", related_files={"b"})
        b.nodes["b"] = FileNode(path="b", priority="HIGH", related_files={"a"})
        b.nodes["c"] = FileNode(path="c", priority="LOW")
        comps = b.get_connected_components()
        assert len(comps) == 2  # {a,b} and {c}

    def test_all_isolated(self):
        b = DependencyGraphBuilder()
        b.nodes["x"] = FileNode(path="x", priority="MEDIUM")
        b.nodes["y"] = FileNode(path="y", priority="MEDIUM")
        comps = b.get_connected_components()
        assert len(comps) == 2


# ── get_smart_batches ────────────────────────────────────────

class TestGetSmartBatches:
    def test_with_enrichment(self):
        groups = [
            _make_group("HIGH", [_make_file("a.py"), _make_file("b.py")]),
            _make_group("LOW", [_make_file("c.py")]),
        ]
        enrichment = _make_enrichment(
            relationships=[("a.py", "b.py", "IMPORTS", "b")]
        )
        b = DependencyGraphBuilder()
        batches = b.get_smart_batches(
            groups, "ws", "proj", ["main"],
            enrichment_data=enrichment,
        )
        assert len(batches) >= 1
        all_paths = [item["file"].path for batch in batches for item in batch]
        assert "a.py" in all_paths
        assert "b.py" in all_paths
        assert "c.py" in all_paths

    def test_max_batch_size(self):
        files = [_make_file(f"f{i}.py") for i in range(20)]
        groups = [_make_group("MEDIUM", files)]
        enrichment = _make_enrichment()
        b = DependencyGraphBuilder()
        batches = b.get_smart_batches(
            groups, "ws", "proj", ["main"],
            max_batch_size=5,
            enrichment_data=enrichment,
        )
        for batch in batches:
            assert len(batch) <= 5


# ── _merge_small_batches ────────────────────────────────────

class TestMergeSmallBatches:
    def test_merges(self):
        b = DependencyGraphBuilder()
        batch1 = [{"file": _make_file("a.py"), "priority": "HIGH"}]
        batch2 = [{"file": _make_file("b.py"), "priority": "HIGH"}]
        result = b._merge_small_batches([batch1, batch2], min_size=3, max_size=10)
        # Should merge them since same priority and total < max
        assert len(result) == 1
        assert len(result[0]) == 2

    def test_empty(self):
        b = DependencyGraphBuilder()
        assert b._merge_small_batches([], 3, 10) == []


# ── create_smart_batches convenience function ────────────────

class TestCreateSmartBatches:
    def test_basic(self):
        groups = [_make_group("HIGH", [_make_file("a.py")])]
        enrichment = _make_enrichment()
        batches = create_smart_batches(
            groups, "ws", "proj", ["main"],
            enrichment_data=enrichment,
        )
        assert len(batches) >= 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_async_rag_client_is_awaited(self):
        class AsyncRag:
            def __init__(self):
                self.called = False

            async def get_deterministic_context(self, **kwargs):
                self.called = True
                return {
                    "context": {
                        "changed_files": {
                            "a.py": [{"metadata": {"path": "a.py", "primary_name": "A"}}],
                            "b.py": [{"metadata": {"path": "b.py", "primary_name": "B"}}],
                        },
                        "related_definitions": {
                            "A": [{"metadata": {"path": "b.py"}}],
                        },
                    }
                }

        groups = [_make_group("HIGH", [_make_file("a.py"), _make_file("b.py")])]
        rag = AsyncRag()

        batches = await create_smart_batches_async(
            groups, "ws", "proj", ["main"], rag_client=rag, max_batch_size=5
        )

        assert rag.called is True
        assert len(batches) >= 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_async_graph_forwards_exact_snapshot_and_overlay_identity(self):
        class AsyncRag:
            def __init__(self):
                self.kwargs = None

            async def get_deterministic_context(self, **kwargs):
                self.kwargs = kwargs
                return {"context": {"changed_files": {}, "related_definitions": {}}}

        snapshot = {"base_sha": "a" * 40, "head_sha": "b" * 40}
        groups = [_make_group("HIGH", [_make_file("a.py")])]
        rag = AsyncRag()

        await create_smart_batches_async(
            groups,
            "ws",
            "proj",
            ["feature", "main"],
            rag_client=rag,
            enrichment_data=SimpleNamespace(relationships=[]),
            snapshot=snapshot,
            execution_id="execution-1",
            pr_number=42,
            pr_changed_files=["a.py"],
        )

        assert rag.kwargs["snapshot"] is snapshot
        assert rag.kwargs["execution_id"] == "execution-1"
        assert rag.kwargs["pr_number"] == 42
        assert rag.kwargs["pr_changed_files"] == ["a.py"]


# ── build_dependency_aware_batches ───────────────────────────

class TestBuildDependencyAwareBatches:
    def test_empty(self):
        assert build_dependency_aware_batches([]) == []

    def test_basic_files(self):
        batches = build_dependency_aware_batches(["a.py", "b.py", "c.py"])
        assert len(batches) >= 1
        all_paths = [item["file_info"].path for batch in batches for item in batch]
        assert set(all_paths) == {"a.py", "b.py", "c.py"}

    def test_with_enrichment(self):
        enrichment = SimpleNamespace(
            relationships=[
                SimpleNamespace(sourceFile="a.py", targetFile="b.py"),
            ],
            fileContents=[
                SimpleNamespace(path="a.py", content="x" * 1000, skipped=False),
                SimpleNamespace(path="b.py", content="y" * 1000, skipped=False),
            ],
        )
        batches = build_dependency_aware_batches(
            ["a.py", "b.py"],
            enrichment_data=enrichment,
        )
        assert len(batches) >= 1

    def test_token_budget_split(self):
        enrichment = SimpleNamespace(
            relationships=[],
            fileContents=[
                SimpleNamespace(path=f"f{i}.py", content="x" * 40000, skipped=False)
                for i in range(5)
            ],
        )
        batches = build_dependency_aware_batches(
            [f"f{i}.py" for i in range(5)],
            enrichment_data=enrichment,
            max_batch_token_budget=15000,
        )
        assert len(batches) > 1

    def test_with_diff(self):
        diff_text = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,3 +1,4 @@\n"
            "+new line\n"
            " old line\n"
        )
        batches = build_dependency_aware_batches(
            ["a.py"],
            diff=diff_text,
        )
        assert len(batches) == 1


# ── get_relationship_summary ─────────────────────────────────

class TestGetRelationshipSummary:
    def test_with_data(self):
        b = DependencyGraphBuilder()
        b.nodes["a"] = FileNode(path="a", priority="HIGH", related_files={"b"})
        b.nodes["b"] = FileNode(path="b", priority="LOW")
        b.relationships = [
            FileRelationship(source_file="a", target_file="b", relationship_type="import", matched_on="z", strength=0.8),
        ]
        summary = b.get_relationship_summary()
        assert summary["total_files"] == 2
        assert summary["total_relationships"] == 1
        assert summary["relationship_types"]["import"] == 1
        assert summary["files_with_relationships"] == 1
