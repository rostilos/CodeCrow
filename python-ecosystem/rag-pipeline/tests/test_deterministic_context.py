"""
Tests for rag_pipeline.services.deterministic_context — DeterministicContextMixin.

Covers:
- get_deterministic_context full workflow (Steps 1-4)
- _apply_branch_priority
- _query_changed_file
- _query_definitions
- _query_transitive_parents
- _query_class_context
- _query_namespace_context
- Edge cases: collection not found, errors in queries, deduplication
"""
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from types import SimpleNamespace
from qdrant_client.http.models import FieldCondition, MatchValue


# ── Helper factories ──

def _mock_config(**overrides):
    cfg = MagicMock()
    cfg.qdrant_url = "http://localhost:6333"
    cfg.qdrant_api_key = None
    cfg.qdrant_collection_prefix = "rag"
    cfg.embedding_provider = "ollama"
    cfg.embedding_dim = 768
    cfg.embedding_supports_instructions = False
    cfg.fallback_branches = ["main", "master"]
    cfg.ollama_model = "nomic-embed-text"
    cfg.ollama_base_url = "http://localhost:11434"
    cfg.openrouter_api_key = "sk-test"
    cfg.openrouter_model = "openai/text-embedding-3-small"
    cfg.openrouter_base_url = "https://openrouter.ai/api/v1"
    cfg.max_identifiers_per_query = 100
    cfg.max_parent_classes_per_query = 20
    cfg.max_namespaces_per_query = 10
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _make_point(payload, point_id="p1"):
    """Create a mock Qdrant point with the given payload."""
    p = SimpleNamespace()
    p.id = point_id
    p.payload = payload
    p.vector = [0.0] * 10
    return p


def _branch_filter(branch="main"):
    """Create a real FieldCondition usable as branch_filter."""
    return FieldCondition(key="branch", match=MatchValue(value=branch))


def _build_service():
    """Build a DeterministicContextMixin-bearing service with all deps mocked."""
    with patch("rag_pipeline.services.base.create_embedding_model") as mock_create, \
         patch("rag_pipeline.services.base.get_embedding_model_info") as mock_info, \
         patch("rag_pipeline.services.base.QdrantClient") as MockQdrant:

        mock_info.return_value = {"provider": "ollama", "type": "local"}
        mock_create.return_value = MagicMock()

        from rag_pipeline.services.base import RAGQueryBase
        from rag_pipeline.services.deterministic_context import DeterministicContextMixin

        class TestService(DeterministicContextMixin, RAGQueryBase):
            pass

        config = _mock_config()
        service = TestService(config)
        return service


# ─────────────────────────────────────────────────────────────
# _apply_branch_priority
# ─────────────────────────────────────────────────────────────
class TestApplyBranchPriority:

    def test_empty_points(self):
        svc = _build_service()
        result = svc._apply_branch_priority([], "main", ["main"], set())
        assert result == []

    def test_pr_points_take_priority(self):
        svc = _build_service()
        pr_pt = _make_point({"path": "a.java", "pr": True, "branch": "feat"}, "pr1")
        branch_pt = _make_point({"path": "a.java", "pr": False, "branch": "main"}, "br1")
        result = svc._apply_branch_priority([pr_pt, branch_pt], "main", ["main", "feat"], set())
        assert len(result) == 1
        assert result[0].payload["pr"] is True

    def test_target_branch_takes_priority_over_base(self):
        svc = _build_service()
        target_pt = _make_point({"path": "b.java", "branch": "feat"}, "t1")
        base_pt = _make_point({"path": "b.java", "branch": "main"}, "b1")
        result = svc._apply_branch_priority(
            [target_pt, base_pt], "feat", ["feat", "main"], set()
        )
        assert len(result) == 1
        assert result[0].payload["branch"] == "feat"

    def test_base_branch_included_when_path_not_in_target(self):
        svc = _build_service()
        base_pt = _make_point({"path": "c.java", "branch": "main"}, "b1")
        result = svc._apply_branch_priority(
            [base_pt], "feat", ["feat", "main"], set()
        )
        assert len(result) == 1

    def test_base_branch_excluded_when_path_in_target_branch_paths(self):
        svc = _build_service()
        base_pt = _make_point({"path": "c.java", "branch": "main"}, "b1")
        result = svc._apply_branch_priority(
            [base_pt], "feat", ["feat", "main"], {"c.java"}
        )
        assert len(result) == 0

    def test_single_branch_returns_all_non_pr(self):
        svc = _build_service()
        pt1 = _make_point({"path": "d.java", "branch": "main"}, "d1")
        pt2 = _make_point({"path": "d.java", "branch": "main"}, "d2")
        result = svc._apply_branch_priority([pt1, pt2], "main", ["main"], set())
        assert len(result) == 2


# ─────────────────────────────────────────────────────────────
# _query_changed_file
# ─────────────────────────────────────────────────────────────
class TestQueryChangedFile:

    def test_exact_path_match(self):
        svc = _build_service()

        payload = {
            "text": "public class Foo {}",
            "path": "src/Foo.java",
            "branch": "main",
            "parent_class": "BaseClass",
            "namespace": "com.example",
            "imports": ["com.util.Helper"],
            "extends": ["BaseClass"],
            "semantic_names": ["Foo"],
            "primary_name": "Foo",
        }
        pt = _make_point(payload, "p1")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        branch_filter = _branch_filter()
        all_chunks = []
        identifiers = set()
        parent_classes = set()
        namespaces = set()
        imports_raw = set()
        extends_raw = set()
        seen_texts = set()
        target_branch_paths = set()
        changed_file_paths = set()

        result = svc._query_changed_file(
            "coll", branch_filter, "src/Foo.java", 10,
            ["main"], "main", seen_texts, target_branch_paths,
            changed_file_paths, identifiers, parent_classes,
            namespaces, imports_raw, extends_raw, all_chunks
        )

        assert len(result) == 1
        assert result[0]["_match_type"] == "changed_file"
        assert "BaseClass" in parent_classes
        assert "com.example" in namespaces
        assert "Helper" in imports_raw
        assert "BaseClass" in extends_raw
        assert "src/Foo.java" in changed_file_paths
        assert "main" == "main" and "src/Foo.java" in target_branch_paths

    def test_fallback_to_filename_match(self):
        svc = _build_service()

        payload = {
            "text": "class Bar {}",
            "path": "src/Bar.java",
            "branch": "main",
        }
        pt = _make_point(payload, "p2")
        # First scroll returns empty (exact path miss), second returns the point
        svc.qdrant_client.scroll.side_effect = [
            ([], None),
            ([pt], None),
        ]

        all_chunks = []
        result = svc._query_changed_file(
            "coll", _branch_filter(), "src/Bar.java", 10,
            ["main"], "main", set(), set(),
            set(), set(), set(), set(), set(), set(), all_chunks
        )
        assert len(result) == 1
        assert svc.qdrant_client.scroll.call_count == 2

    def test_dedup_by_seen_texts(self):
        svc = _build_service()

        payload = {"text": "duplicate text", "path": "x.java", "branch": "main"}
        pt = _make_point(payload, "p3")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        seen = {"duplicate text"}
        all_chunks = []
        result = svc._query_changed_file(
            "coll", _branch_filter(), "x.java", 10,
            ["main"], "main", seen, set(),
            set(), set(), set(), set(), set(), set(), all_chunks
        )
        assert len(result) == 0

    def test_branch_priority_filtering(self):
        svc = _build_service()

        target_pt = _make_point({"text": "target", "path": "a.java", "branch": "feat"}, "t1")
        base_pt = _make_point({"text": "base", "path": "a.java", "branch": "main"}, "b1")
        svc.qdrant_client.scroll.return_value = ([target_pt, base_pt], None)

        all_chunks = []
        result = svc._query_changed_file(
            "coll", _branch_filter("feat"), "a.java", 10,
            ["feat", "main"], "feat", set(), set(),
            set(), set(), set(), set(), set(), set(), all_chunks
        )
        assert len(result) == 1
        assert result[0]["text"] == "target"

    def test_imports_parsing_multi_segment(self):
        svc = _build_service()

        payload = {
            "text": "code",
            "path": "x.java",
            "branch": "main",
            "imports": ["com.example.util.Helper;", "org.foo.Bar"],
        }
        pt = _make_point(payload, "p4")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        imports_raw = set()
        svc._query_changed_file(
            "coll", _branch_filter(), "x.java", 10,
            ["main"], "main", set(), set(),
            set(), set(), set(), set(), imports_raw, set(), []
        )
        assert "Helper" in imports_raw
        assert "Bar" in imports_raw


# ─────────────────────────────────────────────────────────────
# _query_definitions (Step 2)
# ─────────────────────────────────────────────────────────────
class TestQueryDefinitions:

    def test_finds_definitions_by_primary_name(self):
        svc = _build_service()

        payload = {
            "text": "class Helper {}",
            "path": "src/Helper.java",
            "branch": "main",
            "primary_name": "Helper",
        }
        pt = _make_point(payload, "d1")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        all_chunks = []
        related_defs = {}

        svc._query_definitions(
            "coll", _branch_filter(), {"Helper"},
            ["main"], "main", set(),
            set(), set(), all_chunks, related_defs
        )

        assert "Helper" in related_defs
        assert len(related_defs["Helper"]) == 1
        assert related_defs["Helper"][0]["_match_type"] == "definition"

    def test_skips_changed_files(self):
        svc = _build_service()

        payload = {
            "text": "class Foo {}",
            "path": "src/Foo.java",
            "branch": "main",
            "primary_name": "Foo",
        }
        pt = _make_point(payload, "d2")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        all_chunks = []
        related_defs = {}

        svc._query_definitions(
            "coll", _branch_filter(), {"Foo"},
            ["main"], "main", set(),
            {"src/Foo.java"},  # already a changed file
            set(), all_chunks, related_defs
        )

        assert len(related_defs) == 0

    def test_handles_exception(self):
        svc = _build_service()
        svc.qdrant_client.scroll.side_effect = Exception("network error")

        all_chunks = []
        related_defs = {}

        # Should not raise
        svc._query_definitions(
            "coll", _branch_filter(), {"Foo"},
            ["main"], "main", set(),
            set(), set(), all_chunks, related_defs
        )
        assert len(related_defs) == 0


# ─────────────────────────────────────────────────────────────
# _query_transitive_parents (Step 2b)
# ─────────────────────────────────────────────────────────────
class TestQueryTransitiveParents:

    def test_finds_transitive_parents(self):
        svc = _build_service()

        payload = {
            "text": "class GrandParent {}",
            "path": "src/GrandParent.java",
            "branch": "main",
            "primary_name": "GrandParent",
        }
        pt = _make_point(payload, "tp1")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        all_chunks = []
        related_defs = {}

        svc._query_transitive_parents(
            "coll", _branch_filter(), {"GrandParent"},
            ["main"], "main", set(),
            set(), set(), all_chunks, related_defs
        )

        assert "GrandParent" in related_defs
        assert related_defs["GrandParent"][0]["_match_type"] == "transitive_parent"

    def test_skips_changed_file_paths(self):
        svc = _build_service()

        payload = {
            "text": "class P {}",
            "path": "src/P.java",
            "branch": "main",
            "primary_name": "P",
        }
        pt = _make_point(payload, "tp2")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        all_chunks = []
        related_defs = {}

        svc._query_transitive_parents(
            "coll", _branch_filter(), {"P"},
            ["main"], "main", set(),
            {"src/P.java"}, set(), all_chunks, related_defs
        )
        assert len(related_defs) == 0

    def test_handles_exception(self):
        svc = _build_service()
        svc.qdrant_client.scroll.side_effect = Exception("timeout")

        svc._query_transitive_parents(
            "coll", _branch_filter(), {"X"},
            ["main"], "main", set(),
            set(), set(), [], {}
        )
        # Should not raise


# ─────────────────────────────────────────────────────────────
# _query_class_context (Step 3)
# ─────────────────────────────────────────────────────────────
class TestQueryClassContext:

    def test_finds_class_context(self):
        svc = _build_service()

        payload = {
            "text": "public void otherMethod() {}",
            "path": "src/MyClass.java",
            "branch": "main",
            "parent_class": "MyClass",
        }
        pt = _make_point(payload, "cc1")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        all_chunks = []
        class_ctx = {}

        svc._query_class_context(
            "coll", _branch_filter(), {"MyClass"},
            ["main"], "main", set(),
            set(), set(), all_chunks, class_ctx
        )

        assert "MyClass" in class_ctx
        assert class_ctx["MyClass"][0]["_match_type"] == "class_context"

    def test_skips_changed_files(self):
        svc = _build_service()

        payload = {
            "text": "void m() {}",
            "path": "src/Changed.java",
            "branch": "main",
            "parent_class": "Changed",
        }
        pt = _make_point(payload, "cc2")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        class_ctx = {}
        svc._query_class_context(
            "coll", _branch_filter(), {"Changed"},
            ["main"], "main", set(),
            {"src/Changed.java"}, set(), [], class_ctx
        )
        assert len(class_ctx) == 0

    def test_handles_exception(self):
        svc = _build_service()
        svc.qdrant_client.scroll.side_effect = Exception("err")

        svc._query_class_context(
            "coll", _branch_filter(), {"X"},
            ["main"], "main", set(),
            set(), set(), [], {}
        )


# ─────────────────────────────────────────────────────────────
# _query_namespace_context (Step 4)
# ─────────────────────────────────────────────────────────────
class TestQueryNamespaceContext:

    def test_finds_namespace_context(self):
        svc = _build_service()

        payload = {
            "text": "class Related {}",
            "path": "src/Related.java",
            "branch": "main",
            "namespace": "com.example",
        }
        pt = _make_point(payload, "ns1")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        all_chunks = []
        ns_ctx = {}

        svc._query_namespace_context(
            "coll", _branch_filter(), {"com.example"},
            ["main"], "main", set(),
            set(), set(), all_chunks, ns_ctx
        )

        assert "com.example" in ns_ctx
        assert ns_ctx["com.example"][0]["_match_type"] == "namespace_context"

    def test_handles_exception(self):
        svc = _build_service()
        svc.qdrant_client.scroll.side_effect = Exception("err")

        svc._query_namespace_context(
            "coll", _branch_filter(), {"ns"},
            ["main"], "main", set(),
            set(), set(), [], {}
        )


# ─────────────────────────────────────────────────────────────
# get_deterministic_context (full orchestration)
# ─────────────────────────────────────────────────────────────
class TestGetDeterministicContext:

    def test_collection_not_found(self):
        svc = _build_service()
        svc.qdrant_client.get_collections.return_value.collections = []
        svc.qdrant_client.get_aliases.return_value.aliases = []

        result = svc.get_deterministic_context(
            workspace="ws",
            project="proj",
            branches=["main"],
            file_paths=["src/Foo.java"],
        )
        assert result["_metadata"]["error"] == "collection_not_found"
        assert result["chunks"] == []

    def test_full_flow_single_branch(self):
        svc = _build_service()

        # Collection exists
        mock_coll = MagicMock()
        mock_coll.name = "rag_ws__proj"
        svc.qdrant_client.get_collections.return_value.collections = [mock_coll]
        svc.qdrant_client.get_aliases.return_value.aliases = []

        # Step 1: changed file query returns a point with metadata
        changed_pt = _make_point({
            "text": "class Foo extends Bar { void doStuff() {} }",
            "path": "src/Foo.java",
            "branch": "main",
            "parent_class": "Bar",
            "namespace": "com.app",
            "imports": ["com.util.Helper"],
            "extends": ["Bar"],
            "semantic_names": ["Foo"],
            "primary_name": "Foo",
        }, "c1")

        # Step 2: definition lookup returns Bar
        def_pt = _make_point({
            "text": "abstract class Bar {}",
            "path": "src/Bar.java",
            "branch": "main",
            "primary_name": "Bar",
        }, "d1")

        # Step 3: class context returns sibling
        class_pt = _make_point({
            "text": "void sibling() {}",
            "path": "src/BarImpl.java",
            "branch": "main",
            "parent_class": "Bar",
        }, "cl1")

        # Step 4: namespace context
        ns_pt = _make_point({
            "text": "class Config {}",
            "path": "src/Config.java",
            "branch": "main",
            "namespace": "com.app",
        }, "ns1")

        # Scroll calls: changed file (exact match), definitions, class, namespace
        svc.qdrant_client.scroll.side_effect = [
            ([changed_pt], None),   # Step 1: exact path match
            ([def_pt], None),       # Step 2: definitions
            ([], None),             # Step 2b: transitive parents (empty)
            ([class_pt], None),     # Step 3: class context
            ([ns_pt], None),        # Step 4: namespace context
        ]

        result = svc.get_deterministic_context(
            workspace="ws",
            project="proj",
            branches=["main"],
            file_paths=["src/Foo.java"],
        )

        assert len(result["chunks"]) >= 1
        assert "src/Foo.java" in result["changed_files"]
        assert result["_metadata"]["branches_searched"] == ["main"]

    def test_with_pr_number(self):
        svc = _build_service()

        mock_coll = MagicMock()
        mock_coll.name = "rag_ws__proj"
        svc.qdrant_client.get_collections.return_value.collections = [mock_coll]
        svc.qdrant_client.get_aliases.return_value.aliases = []

        pt = _make_point({
            "text": "pr code",
            "path": "src/Pr.java",
            "branch": "feat",
            "pr": True,
            "pr_number": 42,
        }, "pr1")

        svc.qdrant_client.scroll.return_value = ([pt], None)

        result = svc.get_deterministic_context(
            workspace="ws",
            project="proj",
            branches=["feat", "main"],
            file_paths=["src/Pr.java"],
            pr_number=42,
        )
        assert len(result["chunks"]) >= 1

    def test_additional_identifiers_injection(self):
        svc = _build_service()

        mock_coll = MagicMock()
        mock_coll.name = "rag_ws__proj"
        svc.qdrant_client.get_collections.return_value.collections = [mock_coll]
        svc.qdrant_client.get_aliases.return_value.aliases = []

        svc.qdrant_client.scroll.return_value = ([], None)

        result = svc.get_deterministic_context(
            workspace="ws",
            project="proj",
            branches=["main"],
            file_paths=["src/X.java"],
            additional_identifiers=["ExtraType", "HelperFunc", "x"],
        )
        # "x" is only 1 char, should be filtered out; other 2 should be in metadata
        ids_extracted = result["_metadata"]["identifiers_extracted"]
        assert "ExtraType" in ids_extracted
        assert "HelperFunc" in ids_extracted

    def test_query_error_does_not_break_flow(self):
        svc = _build_service()

        mock_coll = MagicMock()
        mock_coll.name = "rag_ws__proj"
        svc.qdrant_client.get_collections.return_value.collections = [mock_coll]
        svc.qdrant_client.get_aliases.return_value.aliases = []

        svc.qdrant_client.scroll.side_effect = Exception("scroll error")

        result = svc.get_deterministic_context(
            workspace="ws",
            project="proj",
            branches=["main"],
            file_paths=["src/Err.java"],
        )
        # Should complete without raising
        assert result["chunks"] == []
