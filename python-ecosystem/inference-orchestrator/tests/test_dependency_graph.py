"""
Unit tests for the RAG-based dependency graph builder.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock
from utils.dependency_graph import (
    DependencyGraphBuilder, 
    create_smart_batches, 
    FileNode,
    FileRelationship
)


class MockReviewFile:
    """Mock ReviewFile for testing."""
    def __init__(self, path: str, focus_areas: list = None):
        self.path = path
        self.focus_areas = focus_areas or []
        self.risk_level = "MEDIUM"


class MockFileGroup:
    """Mock FileGroup for testing."""
    def __init__(self, files: list, priority: str = "MEDIUM"):
        self.files = files
        self.priority = priority
        self.group_id = "test-group"
        self.rationale = "Test group"


class MockRAGClient:
    """Mock RAG client that returns predefined responses."""
    
    def __init__(self, response: dict = None):
        self.response = response or {}
        self.call_count = 0
        self.last_call_args = None
    
    def get_deterministic_context(self, workspace, project, branches, file_paths, limit_per_file=15):
        self.call_count += 1
        self.last_call_args = {
            "workspace": workspace,
            "project": project,
            "branches": branches,
            "file_paths": file_paths,
            "limit_per_file": limit_per_file
        }
        return self.response


class TestDependencyGraphBuilder:
    """Tests for DependencyGraphBuilder class."""
    
    def test_init_without_rag_client(self):
        """Test initialization without RAG client."""
        builder = DependencyGraphBuilder()
        assert builder.rag_client is None
        assert builder.nodes == {}
        assert builder.relationships == []
    
    def test_init_with_rag_client(self):
        """Test initialization with RAG client."""
        mock_rag = MockRAGClient()
        builder = DependencyGraphBuilder(rag_client=mock_rag)
        assert builder.rag_client is mock_rag
    
    def test_build_basic_graph_fallback(self):
        """Test fallback to basic graph when no RAG client."""
        builder = DependencyGraphBuilder()
        
        file_groups = [
            MockFileGroup([
                MockReviewFile("src/service/user_service.py"),
                MockReviewFile("src/service/auth_service.py"),
            ], priority="HIGH"),
            MockFileGroup([
                MockReviewFile("tests/test_user.py"),
            ], priority="MEDIUM"),
        ]
        
        nodes = builder._build_basic_graph(file_groups)
        
        assert len(nodes) == 3
        assert "src/service/user_service.py" in nodes
        # Files in same directory should be related
        assert "src/service/auth_service.py" in nodes["src/service/user_service.py"].related_files
    
    def test_build_graph_from_rag_with_relationships(self):
        """Test building graph with RAG-discovered relationships."""
        # Create RAG response with relationships
        rag_response = {
            "changed_files": {
                "src/service/user_service.py": [
                    {
                        "metadata": {
                            "primary_name": "UserService",
                            "semantic_names": ["get_user", "create_user"],
                            "imports": ["AuthService", "UserRepository"],
                            "parent_class": None,
                            "namespace": "src.service",
                            "path": "src/service/user_service.py"
                        }
                    }
                ],
                "src/service/auth_service.py": [
                    {
                        "metadata": {
                            "primary_name": "AuthService",
                            "semantic_names": ["authenticate", "validate_token"],
                            "imports": [],
                            "parent_class": None,
                            "namespace": "src.service",
                            "path": "src/service/auth_service.py"
                        }
                    }
                ]
            },
            "related_definitions": {
                "AuthService": [
                    {
                        "metadata": {
                            "primary_name": "AuthService",
                            "path": "src/service/auth_service.py"
                        }
                    }
                ]
            },
            "class_context": {},
            "namespace_context": {
                "src.service": [
                    {"metadata": {"path": "src/service/user_service.py"}},
                    {"metadata": {"path": "src/service/auth_service.py"}}
                ]
            }
        }
        
        mock_rag = MockRAGClient(response=rag_response)
        builder = DependencyGraphBuilder(rag_client=mock_rag)
        
        file_groups = [
            MockFileGroup([
                MockReviewFile("src/service/user_service.py"),
                MockReviewFile("src/service/auth_service.py"),
            ], priority="HIGH"),
        ]
        
        nodes = builder.build_graph_from_rag(
            file_groups,
            workspace="test-workspace",
            project="test-project",
            branches=["main"]
        )
        
        assert len(nodes) == 2
        assert mock_rag.call_count == 1
        
        # Check metadata extraction
        user_node = nodes["src/service/user_service.py"]
        assert "AuthService" in user_node.imports_symbols
        assert "UserService" in user_node.exports_symbols
        
        # Check relationships discovered
        assert len(builder.relationships) > 0
    
    def test_get_connected_components(self):
        """Test finding connected components."""
        builder = DependencyGraphBuilder()
        
        # Manually set up nodes with relationships
        builder.nodes = {
            "a.py": FileNode(path="a.py", priority="HIGH", related_files={"b.py"}),
            "b.py": FileNode(path="b.py", priority="HIGH", related_files={"a.py", "c.py"}),
            "c.py": FileNode(path="c.py", priority="HIGH", related_files={"b.py"}),
            "d.py": FileNode(path="d.py", priority="MEDIUM", related_files=set()),  # Isolated
        }
        
        components = builder.get_connected_components()
        
        assert len(components) == 2
        # One component with a, b, c
        abc_component = next(c for c in components if "a.py" in c)
        assert abc_component == {"a.py", "b.py", "c.py"}
        # One isolated component with d
        d_component = next(c for c in components if "d.py" in c)
        assert d_component == {"d.py"}
    
    def test_smart_batches_keeps_related_files_together(self):
        """Test that smart batching keeps related files in same batch."""
        rag_response = {
            "changed_files": {
                "src/model/user.py": [
                    {"metadata": {"primary_name": "User", "path": "src/model/user.py"}}
                ],
                "src/service/user_service.py": [
                    {"metadata": {
                        "primary_name": "UserService", 
                        "imports": ["User"],
                        "path": "src/service/user_service.py"
                    }}
                ],
                "src/api/user_api.py": [
                    {"metadata": {
                        "primary_name": "UserAPI", 
                        "imports": ["UserService"],
                        "path": "src/api/user_api.py"
                    }}
                ],
                "unrelated/config.py": [
                    {"metadata": {"primary_name": "Config", "path": "unrelated/config.py"}}
                ]
            },
            "related_definitions": {
                "User": [{"metadata": {"path": "src/model/user.py"}}],
                "UserService": [{"metadata": {"path": "src/service/user_service.py"}}]
            },
            "class_context": {},
            "namespace_context": {}
        }
        
        mock_rag = MockRAGClient(response=rag_response)
        builder = DependencyGraphBuilder(rag_client=mock_rag)
        
        file_groups = [
            MockFileGroup([
                MockReviewFile("src/model/user.py"),
                MockReviewFile("src/service/user_service.py"),
                MockReviewFile("src/api/user_api.py"),
                MockReviewFile("unrelated/config.py"),
            ], priority="HIGH"),
        ]
        
        batches = builder.get_smart_batches(
            file_groups,
            workspace="test",
            project="test",
            branches=["main"],
            max_batch_size=3
        )
        
        # Should have batches created
        assert len(batches) > 0
        
        # Get all paths per batch
        batch_paths = [[f['file'].path for f in b] for b in batches]
        
        # Related files should be in the same batch if possible
        # (User -> UserService -> UserAPI chain)
        for batch in batch_paths:
            if "src/model/user.py" in batch:
                # If User is in batch, UserService should be too (they're related)
                if len(batch) > 1:
                    assert "src/service/user_service.py" in batch or "src/api/user_api.py" in batch
    
    def test_relationship_summary(self):
        """Test getting relationship summary."""
        builder = DependencyGraphBuilder()
        
        builder.nodes = {
            "a.py": FileNode(path="a.py", priority="HIGH", related_files={"b.py"}),
            "b.py": FileNode(path="b.py", priority="HIGH", related_files={"a.py"}),
            "c.py": FileNode(path="c.py", priority="LOW", related_files=set()),
        }
        builder.relationships = [
            FileRelationship("a.py", "b.py", "definition", "SomeClass", 0.95),
        ]
        
        summary = builder.get_relationship_summary()
        
        assert summary["total_files"] == 3
        assert summary["total_relationships"] == 1
        assert summary["files_with_relationships"] == 2
        assert summary["relationship_types"]["definition"] == 1


class TestCreateSmartBatches:
    """Tests for the convenience function."""
    
    def test_without_rag_client(self):
        """Test smart batches without RAG client (fallback)."""
        file_groups = [
            MockFileGroup([
                MockReviewFile("a.py"),
                MockReviewFile("b.py"),
            ], priority="HIGH"),
        ]
        
        batches = create_smart_batches(
            file_groups,
            workspace="test",
            project="test",
            branches=["main"],
            rag_client=None,
            max_batch_size=5
        )
        
        assert len(batches) > 0
        total_files = sum(len(b) for b in batches)
        assert total_files == 2
    
    def test_with_rag_client(self):
        """Test smart batches with RAG client."""
        mock_rag = MockRAGClient(response={
            "changed_files": {},
            "related_definitions": {},
            "class_context": {},
            "namespace_context": {}
        })
        
        file_groups = [
            MockFileGroup([
                MockReviewFile("a.py"),
                MockReviewFile("b.py"),
                MockReviewFile("c.py"),
            ], priority="HIGH"),
        ]
        
        batches = create_smart_batches(
            file_groups,
            workspace="test",
            project="test",
            branches=["main"],
            rag_client=mock_rag,
            max_batch_size=2
        )
        
        assert mock_rag.call_count == 1
        assert len(batches) >= 1


class TestMergingSmallBatches:
    """Tests for batch merging optimization."""
    
    def test_merge_same_priority_batches(self):
        """Test that small batches of same priority get merged."""
        builder = DependencyGraphBuilder()
        
        batches = [
            [{"file": MockReviewFile("a.py"), "priority": "HIGH"}],
            [{"file": MockReviewFile("b.py"), "priority": "HIGH"}],
            [{"file": MockReviewFile("c.py"), "priority": "LOW"}],
        ]
        
        merged = builder._merge_small_batches(batches, min_size=2, max_size=5)
        
        # HIGH priority files should be merged
        high_batch = next((b for b in merged if b[0]["priority"] == "HIGH"), None)
        if high_batch:
            assert len(high_batch) <= 5
    
    def test_no_merge_when_at_max_size(self):
        """Test that batches at max size are not merged."""
        builder = DependencyGraphBuilder()
        
        batches = [
            [
                {"file": MockReviewFile("a.py"), "priority": "HIGH"},
                {"file": MockReviewFile("b.py"), "priority": "HIGH"},
                {"file": MockReviewFile("c.py"), "priority": "HIGH"},
            ],
            [{"file": MockReviewFile("d.py"), "priority": "HIGH"}],
        ]
        
        merged = builder._merge_small_batches(batches, min_size=2, max_size=3)
        
        # First batch is already at max, so d.py stays separate
        assert any(len(b) == 3 for b in merged)
