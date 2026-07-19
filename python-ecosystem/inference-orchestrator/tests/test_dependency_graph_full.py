"""Extended tests for dependency_graph: RAG-based graph building, smart batches, connected components."""
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
        assert node.relationship_strength == 0.0
        assert len(node.related_files) == 0
        assert node.priority == "MEDIUM"


class TestFileRelationship:
    def test_creation(self):
        rel = FileRelationship(
            source_file="a.py",
            target_file="b.py",
            relationship_type="imports",
            matched_on="Foo",
            strength=1.0,
        )
        assert rel.source_file == "a.py"
        assert rel.strength == 1.0


class TestCalculateStrength:
    def test_capped_at_5(self):
        graph = DependencyGraph()
        graph.nodes["a.py"] = FileNode(path="a.py", priority="HIGH")
        # Add many high-weight relationships
        for i in range(20):
            graph.relationships.append(
                FileRelationship(
                    source_file="a.py",
                    target_file=f"b{i}.py",
                    relationship_type="imports",
                    matched_on="",
                    strength=1.0,
                )
            )
        result = graph._calculate_strength("a.py", {"b0.py"})
        assert result == 5.0

    def test_zero_when_no_relationships(self):
        graph = DependencyGraph()
        graph.nodes["a.py"] = FileNode(path="a.py", priority="HIGH")
        result = graph._calculate_strength("a.py", set())
        assert result == 0.0


class TestBuildBasicGraph:
    def test_same_dir_files_related(self):
        groups = _make_file_group([
            ("HIGH", ["src/a.py", "src/b.py"]),
        ])
        graph = DependencyGraph()
        nodes = graph._build_basic_graph(groups)
        assert "src/a.py" in nodes
        assert "src/b.py" in nodes["src/a.py"].related_files

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
        meta_a.semanticNames = ["bar"]
        meta_a.extendsClasses = []
        meta_a.parentClass = None
        meta_a.namespace = None

        meta_b = MagicMock()
        meta_b.path = "b.py"
        meta_b.imports = []
        meta_b.semanticNames = ["Foo"]
        meta_b.extendsClasses = ["Base"]
        meta_b.parentClass = "Base"
        meta_b.namespace = "com.example"

        enrichment.fileMetadata = [meta_a, meta_b]

        graph = DependencyGraph()
        nodes = graph.build_graph_from_enrichment(groups, enrichment)
        assert "a.py" in nodes
        assert "b.py" in nodes["a.py"].related_files
        assert len(graph.relationships) > 0


class TestBuildGraphFromRag:
    def test_no_rag_client_fallback(self):
        groups = _make_file_group([("HIGH", ["a.py"])])
        graph = DependencyGraph(rag_client=None)
        nodes = graph.build_graph_from_rag(groups, "ws", "proj", ["main"])
        assert "a.py" in nodes

    def test_rag_exception_fallback(self):
        mock_rag = MagicMock()
        mock_rag.get_deterministic_context.side_effect = Exception("fail")
        groups = _make_file_group([("HIGH", ["a.py"])])
        graph = DependencyGraph(rag_client=mock_rag)
        nodes = graph.build_graph_from_rag(groups, "ws", "proj", ["main"])
        assert "a.py" in nodes


class TestExtractRelationshipsFromRag:
    def test_processes_changed_files_metadata(self):
        graph = DependencyGraph()
        graph.nodes["a.py"] = FileNode(path="a.py", priority="HIGH")
        graph.nodes["b.py"] = FileNode(path="b.py", priority="HIGH")

        rag_response = {
            "changed_files": {
                "a.py": [
                    {
                        "metadata": {
                            "primary_name": "Foo",
                            "semantic_names": ["Foo", "FooBar"],
                            "imports": ["Bar"],
                            "parent_class": "Base",
                            "namespace": "com.example",
                            "extends": ["Base"],
                        }
                    }
                ]
            },
            "related_definitions": {},
            "class_context": {},
            "namespace_context": {},
        }
        graph._extract_relationships_from_rag(rag_response, ["a.py", "b.py"])
        node_a = graph.nodes["a.py"]
        assert "Foo" in node_a.exports_symbols
        assert "Base" in node_a.parent_classes

    def test_processes_related_definitions(self):
        graph = DependencyGraph()
        graph.nodes["a.py"] = FileNode(path="a.py", priority="HIGH")
        graph.nodes["b.py"] = FileNode(path="b.py", priority="HIGH")
        graph.nodes["a.py"].imports_symbols.add("MyFunc")

        rag_response = {
            "changed_files": {},
            "related_definitions": {
                "MyFunc": [
                    {"metadata": {"path": "b.py"}}
                ]
            },
            "class_context": {},
            "namespace_context": {},
        }
        graph._extract_relationships_from_rag(rag_response, ["a.py"])
        assert len(graph.relationships) > 0

    def test_connects_changed_files_through_unchanged_definition(self):
        graph = DependencyGraph()
        graph.nodes["api/a.py"] = FileNode(path="api/a.py", priority="HIGH")
        graph.nodes["worker/b.py"] = FileNode(path="worker/b.py", priority="HIGH")
        rag_response = {
            "changed_files": {
                "api/a.py": [{"metadata": {"imports": ["core.shared.Shared"]}}],
                "worker/b.py": [{"metadata": {"calls": ["Shared"]}}],
            },
            "related_definitions": {
                "Shared": [{"metadata": {"path": "core/shared.py"}}],
            },
            "class_context": {},
            "namespace_context": {},
        }

        graph._extract_relationships_from_rag(
            rag_response, ["api/a.py", "worker/b.py"]
        )

        assert "worker/b.py" in graph.nodes["api/a.py"].related_files
        assert any(
            rel.relationship_type in {"shared_definition", "shared_dependency"}
            and rel.matched_on in {"Shared", "core/shared.py"}
            for rel in graph.relationships
        )

    def test_connects_changed_files_through_transitive_unchanged_parent(self):
        graph = DependencyGraph()
        graph.nodes["service.py"] = FileNode(path="service.py", priority="HIGH")
        graph.nodes["base_consumer.py"] = FileNode(
            path="base_consumer.py", priority="HIGH"
        )
        rag_response = {
            "changed_files": {
                "service.py": [{"metadata": {"imports": ["Service"]}}],
                "base_consumer.py": [{"metadata": {"imports": ["BaseService"]}}],
            },
            "related_definitions": {
                "Service": [{
                    "metadata": {
                        "path": "core/service.py",
                        "extends": ["BaseService"],
                    }
                }],
                "BaseService": [{"metadata": {"path": "core/base.py"}}],
            },
            "class_context": {},
            "namespace_context": {},
        }

        graph._extract_relationships_from_rag(
            rag_response, ["service.py", "base_consumer.py"]
        )

        assert "base_consumer.py" in graph.nodes["service.py"].related_files

    def test_processes_class_context(self):
        graph = DependencyGraph()
        graph.nodes["a.py"] = FileNode(path="a.py", priority="HIGH")
        graph.nodes["b.py"] = FileNode(path="b.py", priority="HIGH")

        rag_response = {
            "changed_files": {},
            "related_definitions": {},
            "class_context": {
                "BaseClass": [
                    {"metadata": {"path": "a.py"}},
                    {"metadata": {"path": "b.py"}},
                ]
            },
            "namespace_context": {},
        }
        graph._extract_relationships_from_rag(rag_response, ["a.py", "b.py"])
        assert "b.py" in graph.nodes["a.py"].related_files

    def test_processes_namespace_context(self):
        graph = DependencyGraph()
        graph.nodes["a.py"] = FileNode(path="a.py", priority="HIGH")
        graph.nodes["b.py"] = FileNode(path="b.py", priority="HIGH")

        rag_response = {
            "changed_files": {},
            "related_definitions": {},
            "class_context": {},
            "namespace_context": {
                "com.example": [
                    {"metadata": {"path": "a.py"}},
                    {"metadata": {"path": "b.py"}},
                ]
            },
        }
        graph._extract_relationships_from_rag(rag_response, ["a.py", "b.py"])
        assert "b.py" in graph.nodes["a.py"].related_files


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

    def test_rag_fallback_path(self):
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
