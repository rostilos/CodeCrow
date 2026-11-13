"""Extended dependency-graph and smart-batching tests."""
import pytest
from unittest.mock import MagicMock, patch
from collections import defaultdict
from utils.dependency_graph import (
    DependencyGraphBuilder,
    FileNode,
    FileRelationship,
)
# Alias for readability in tests
DependencyGraph = DependencyGraphBuilder


def _make_file_group(files_with_priority):
    """Helper: create mock FileGroup objects."""
    groups = []
    for priority, file_paths in files_with_priority:
        group = MagicMock()
        group.priority = priority
        group.files = []
        for fp in file_paths:
            f = MagicMock()
            f.path = fp
            f.focus_areas = []
            group.files.append(f)
        groups.append(group)
    return groups


class TestFileNode:
    def test_default_values(self):
        node = FileNode(path="a.py", priority="MEDIUM")
        assert node.path == "a.py"
        assert node.relationship_degree == 0
        assert len(node.related_files) == 0
        assert node.priority == "MEDIUM"


class TestFileRelationship:
    def test_creation(self):
        rel = FileRelationship(
            source_file="a.py",
            target_file="b.py",
            relationship_type="imports",
            matched_on="Foo",
        )
        assert rel.source_file == "a.py"
        assert rel.matched_on == "Foo"


class TestRelationshipDegree:
    def test_counts_edges(self):
        graph = DependencyGraph()
        graph.nodes["a.py"] = FileNode(path="a.py", priority="HIGH")
        # Add many structural relationships.
        for i in range(20):
            graph.relationships.append(
                FileRelationship(
                    source_file="a.py",
                    target_file=f"b{i}.py",
                    relationship_type="imports",
                    matched_on="",
                )
            )
        result = graph._relationship_degree("a.py")
        assert result == 20

    def test_zero_when_no_relationships(self):
        graph = DependencyGraph()
        graph.nodes["a.py"] = FileNode(path="a.py", priority="HIGH")
        result = graph._relationship_degree("a.py")
        assert result == 0


class TestBuildBasicGraph:
    def test_same_dir_files_are_not_assumed_related(self):
        groups = _make_file_group([
            ("HIGH", ["src/a.py", "src/b.py"]),
        ])
        graph = DependencyGraph()
        nodes = graph._build_basic_graph(groups)
        assert "src/a.py" in nodes
        assert "src/b.py" not in nodes["src/a.py"].related_files

    def test_different_dirs_not_related(self):
        groups = _make_file_group([
            ("HIGH", ["src/a.py", "lib/b.py"]),
        ])
        graph = DependencyGraph()
        nodes = graph._build_basic_graph(groups)
        assert "lib/b.py" not in nodes["src/a.py"].related_files


class TestConnectedComponents:
    def test_single_component(self):
        graph = DependencyGraph()
        graph.nodes["a.py"] = FileNode(path="a.py", priority="HIGH")
        graph.nodes["b.py"] = FileNode(path="b.py", priority="HIGH")
        graph.nodes["a.py"].related_files.add("b.py")
        graph.nodes["b.py"].related_files.add("a.py")
        components = graph.get_connected_components()
        assert len(components) == 1
        assert {"a.py", "b.py"} == components[0]

    def test_two_components(self):
        graph = DependencyGraph()
        graph.nodes["a.py"] = FileNode(path="a.py", priority="HIGH")
        graph.nodes["b.py"] = FileNode(path="b.py", priority="HIGH")
        graph.nodes["c.py"] = FileNode(path="c.py", priority="LOW")
        graph.nodes["a.py"].related_files.add("b.py")
        graph.nodes["b.py"].related_files.add("a.py")
        # c.py is isolated
        components = graph.get_connected_components()
        assert len(components) == 2

    def test_empty_graph(self):
        graph = DependencyGraph()
        assert graph.get_connected_components() == []


class TestBuildGraphFromEnrichment:
    def test_basic_enrichment(self):
        groups = _make_file_group([
            ("HIGH", ["a.py", "b.py"]),
        ])
        enrichment = MagicMock()
        rel = MagicMock()
        rel.sourceFile = "a.py"
        rel.targetFile = "b.py"
        rel.relationshipType = MagicMock(value="imports")
        rel.matchedOn = "Foo"
        enrichment.relationships = [rel]

        meta_a = MagicMock()
        meta_a.path = "a.py"
        meta_a.imports = ["Foo"]
        meta_a.symbolNames = ["bar"]
        meta_a.extendsClasses = []
        meta_a.parentClass = None
        meta_a.namespace = None

        meta_b = MagicMock()
        meta_b.path = "b.py"
        meta_b.imports = []
        meta_b.symbolNames = ["Foo"]
        meta_b.extendsClasses = ["Base"]
        meta_b.parentClass = "Base"
        meta_b.namespace = "com.example"

        enrichment.fileMetadata = [meta_a, meta_b]

        graph = DependencyGraph()
        nodes = graph.build_graph_from_enrichment(groups, enrichment)
        assert "a.py" in nodes
        assert "b.py" in nodes["a.py"].related_files
        assert len(graph.relationships) > 0


class TestBuildGraphFromStructuralRelations:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_structural_client_fallback(self):
        groups = _make_file_group([("HIGH", ["a.py"])])
        graph = DependencyGraph(rag_client=None)
        nodes = await graph.build_graph_from_structural_relations(
            groups,
            "ws",
            "proj",
            ["main"],
        )
        assert "a.py" in nodes

    @pytest.mark.asyncio(loop_scope="function")
    async def test_structural_exception_fallback(self):
        client = MagicMock()
        client.get_structural_relations.side_effect = Exception("fail")
        groups = _make_file_group([("HIGH", ["a.py"])])
        graph = DependencyGraph(rag_client=client)
        nodes = await graph.build_graph_from_structural_relations(
            groups,
            "ws",
            "proj",
            ["main"],
            structural_binding={"repository_revision": "abc123"},
        )
        assert "a.py" in nodes

    @pytest.mark.asyncio(loop_scope="function")
    async def test_structured_error_does_not_invent_directory_edges(self):
        client = MagicMock()
        client.get_structural_relations.return_value = {
            "status": "error",
            "status_code": 503,
            "error": "unavailable",
        }
        groups = _make_file_group([
            ("HIGH", ["src/a.py", "src/b.py", "lib/c.py"]),
        ])

        graph = DependencyGraph(rag_client=client)
        nodes = await graph.build_graph_from_structural_relations(
            groups,
            "ws",
            "proj",
            ["main"],
            structural_binding={"repository_revision": "abc123"},
        )

        assert "src/b.py" not in nodes["src/a.py"].related_files
        assert "lib/c.py" not in nodes["src/a.py"].related_files


class TestExtractRelationshipsFromStructuralMap:
    def test_processes_anchor_symbols(self):
        graph = DependencyGraph()
        graph.nodes["a.py"] = FileNode(path="a.py", priority="HIGH")
        graph.nodes["b.py"] = FileNode(path="b.py", priority="HIGH")

        relation_map = {
            "anchors": [{
                "path": "a.py",
                "symbols": [{
                    "name": "Foo",
                    "qualifiedName": "example.Foo",
                }],
            }],
            "relations": [],
        }
        graph._extract_relationships_from_structural_map(
            relation_map,
            ["a.py", "b.py"],
        )
        node_a = graph.nodes["a.py"]
        assert "Foo" in node_a.exports_symbols
        assert "example.Foo" in node_a.exports_symbols

    def test_processes_relation_paths(self):
        graph = DependencyGraph()
        graph.nodes["a.py"] = FileNode(path="a.py", priority="HIGH")
        graph.nodes["b.py"] = FileNode(path="b.py", priority="HIGH")

        relation_map = {
            "anchors": [],
            "relations": [{
                "kind": "IMPORTS",
                "source": "a.py",
                "relation": "imports",
                "target": "b.py",
                "origin": {"path": "a.py", "line": 1},
                "relatedPaths": ["a.py", "b.py"],
            }],
        }
        graph._extract_relationships_from_structural_map(
            relation_map,
            ["a.py", "b.py"],
        )
        assert graph.nodes["a.py"].related_files == {"b.py"}
        assert len(graph.relationships) == 1

class TestSmartBatches:
    def test_enrichment_path(self):
        groups = _make_file_group([("HIGH", ["a.py", "b.py"])])
        enrichment = MagicMock()
        enrichment.has_data.return_value = True
        enrichment.relationships = []
        enrichment.fileMetadata = []

        graph = DependencyGraph()
        batches = graph.get_smart_batches(
            groups, "ws", "proj", ["main"],
            enrichment_data=enrichment,
        )
        assert len(batches) >= 1

    def test_basic_path_without_enrichment(self):
        groups = _make_file_group([("HIGH", ["a.py"])])
        graph = DependencyGraph(rag_client=None)
        batches = graph.get_smart_batches(
            groups, "ws", "proj", ["main"],
        )
        assert len(batches) >= 1

    def test_respects_max_batch_size(self):
        paths = [f"file_{i}.py" for i in range(20)]
        groups = _make_file_group([("MEDIUM", paths)])
        graph = DependencyGraph(rag_client=None)
        batches = graph.get_smart_batches(
            groups, "ws", "proj", ["main"],
            max_batch_size=5,
        )
        for batch in batches:
            assert len(batch) <= 5

    def test_orphan_files_included(self):
        # Two separate groups — one will be orphaned
        groups = _make_file_group([
            ("HIGH", ["a.py"]),
            ("LOW", ["orphan.py"]),
        ])
        enrichment = MagicMock()
        enrichment.has_data.return_value = True
        enrichment.relationships = []
        enrichment.fileMetadata = []

        graph = DependencyGraph()
        batches = graph.get_smart_batches(
            groups, "ws", "proj", ["main"],
            enrichment_data=enrichment,
        )
        all_paths = [b["file"].path for batch in batches for b in batch]
        assert "a.py" in all_paths
        assert "orphan.py" in all_paths

    def test_token_budget_splitting(self):
        groups = _make_file_group([("HIGH", ["a.py", "b.py"])])
        # Mock a processed_diff to give high token cost
        processed_diff = MagicMock()
        f1 = MagicMock()
        f1.path = "a.py"
        f1.content = "x" * 400000  # ~100K tokens
        f2 = MagicMock()
        f2.path = "b.py"
        f2.content = "y" * 400000
        processed_diff.files = [f1, f2]

        graph = DependencyGraph(rag_client=None)
        batches = graph.get_smart_batches(
            groups, "ws", "proj", ["main"],
            max_allowed_tokens=150000,
            processed_diff=processed_diff,
        )
        # Should be split into 2 batches due to token budget
        assert len(batches) == 2
