"""
Extended tests for rag_pipeline.core.splitter.splitter — ASTCodeSplitter.

Focus on MISSING coverage ranges:
- _split_oversized_chunk (lines 746-825)
- _split_fallback (lines 559-625 — fallback splitting for non-AST files)
- _process_chunks (lines 406-492)
- _build_metadata (lines 275-300 coverage gaps)
- _create_simplified_code
- _compute_information_density
- _create_embedding_text
- _clean_path
- _parse_type_list
- _get_semantic_node_types
"""
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from llama_index.core.schema import Document as LlamaDocument, TextNode

from rag_pipeline.core.splitter.splitter import (
    ASTCodeSplitter, ASTChunk, generate_deterministic_id, compute_file_hash,
)
from rag_pipeline.core.splitter.metadata import ContentType


# ─────────────────────────────────────────────────────────────
# generate_deterministic_id / compute_file_hash
# ─────────────────────────────────────────────────────────────
class TestHelperFunctions:

    def test_deterministic_id_is_deterministic(self):
        id1 = generate_deterministic_id("a.py", "content", 0)
        id2 = generate_deterministic_id("a.py", "content", 0)
        assert id1 == id2

    def test_deterministic_id_varies_with_path(self):
        id1 = generate_deterministic_id("a.py", "content", 0)
        id2 = generate_deterministic_id("b.py", "content", 0)
        assert id1 != id2

    def test_deterministic_id_length(self):
        cid = generate_deterministic_id("p", "c", 1)
        assert len(cid) == 32

    def test_file_hash_is_consistent(self):
        h1 = compute_file_hash("hello world")
        h2 = compute_file_hash("hello world")
        assert h1 == h2

    def test_file_hash_differs(self):
        h1 = compute_file_hash("a")
        h2 = compute_file_hash("b")
        assert h1 != h2


# ─────────────────────────────────────────────────────────────
# ASTCodeSplitter initialization
# ─────────────────────────────────────────────────────────────
class TestASTCodeSplitterInit:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_defaults(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()
        assert splitter.max_chunk_size == 8000
        assert splitter.min_chunk_size == 100
        assert splitter.chunk_overlap == 200
        assert splitter.enrich_embedding_text is True

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_custom_params(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter(max_chunk_size=500, min_chunk_size=10, chunk_overlap=50)
        assert splitter.max_chunk_size == 500


# ─────────────────────────────────────────────────────────────
# _compute_information_density
# ─────────────────────────────────────────────────────────────
class TestComputeInformationDensity:

    def test_empty_metadata(self):
        density = ASTCodeSplitter._compute_information_density({})
        assert density == 0.0

    def test_rich_metadata(self):
        meta = {
            "start_line": 1,
            "end_line": 20,
            "semantic_names": ["MyClass"],
            "signature": "class MyClass",
            "methods": ["foo", "bar", "baz"],
            "properties": ["x"],
            "extends": ["Base"],
            "calls": ["helper"],
            "docstring": "A test class.",
            "parameters": ["a", "b"],
        }
        density = ASTCodeSplitter._compute_information_density(meta)
        assert 0.0 < density <= 1.0

    def test_single_line_chunk(self):
        meta = {
            "start_line": 5,
            "end_line": 5,
            "semantic_names": ["x"],
        }
        density = ASTCodeSplitter._compute_information_density(meta)
        assert density == 1.0  # 1 signal / 1 line = 1.0

    def test_many_calls_capped(self):
        meta = {
            "start_line": 1,
            "end_line": 10,
            "calls": [f"fn{i}" for i in range(50)],
        }
        density = ASTCodeSplitter._compute_information_density(meta)
        # Calls are capped at 10, so density = 10/10 = 1.0
        assert density == 1.0


# ─────────────────────────────────────────────────────────────
# _create_embedding_text
# ─────────────────────────────────────────────────────────────
class TestCreateEmbeddingText:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_with_enrichment_enabled(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter(enrich_embedding_text=True)

        meta = {"path": "src/main/java/Foo.java", "parent_context": ["Bar"]}
        result = splitter._create_embedding_text("code body", meta)
        assert "File:" in result
        assert "In: Bar" in result
        assert "code body" in result

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_with_enrichment_disabled(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter(enrich_embedding_text=False)

        result = splitter._create_embedding_text("raw code", {"path": "x.py"})
        assert result == "raw code"

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_extends_and_implements(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        meta = {
            "path": "Foo.java",
            "extends": ["Base"],
            "implements": ["IFoo"],
        }
        result = splitter._create_embedding_text("code", meta)
        assert "Extends: Base" in result
        assert "Implements: IFoo" in result

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_class_container_shows_methods(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        meta = {
            "path": "Foo.java",
            "node_type": "class",
            "methods": ["foo", "bar", "baz"],
            "properties": ["x", "y"],
        }
        result = splitter._create_embedding_text("code", meta)
        assert "Methods(3)" in result
        assert "Fields(2)" in result

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_namespace_cleaning(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        meta = {"path": "Foo.java", "namespace": "package com.example;"}
        result = splitter._create_embedding_text("code", meta)
        assert "Namespace: com.example" in result

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_no_context_returns_raw(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        result = splitter._create_embedding_text("code", {})
        assert result == "code"


# ─────────────────────────────────────────────────────────────
# _clean_path
# ─────────────────────────────────────────────────────────────
class TestCleanPath:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_removes_commit_prefix(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        result = splitter._clean_path(
            "owner-repo-abcdef1234567890abcdef1234567890abcdef12/src/main/Foo.java"
        )
        assert result == "src/main/Foo.java"

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_keeps_src_relative(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        result = splitter._clean_path("src/main/java/Foo.java")
        assert result == "src/main/java/Foo.java"

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_empty_path(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        assert splitter._clean_path("") == ""


# ─────────────────────────────────────────────────────────────
# _parse_type_list
# ─────────────────────────────────────────────────────────────
class TestParseTypeList:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_comma_separated(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        result = splitter._parse_type_list("Foo, Bar, Baz")
        assert result == ["Foo", "Bar", "Baz"]

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_with_generics(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        result = splitter._parse_type_list("List<String>, Map<K,V>")
        assert "List" in result
        assert "Map" in result

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_empty(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        assert splitter._parse_type_list("") == []
        assert splitter._parse_type_list(None) == []


# ─────────────────────────────────────────────────────────────
# _get_semantic_node_types
# ─────────────────────────────────────────────────────────────
class TestGetSemanticNodeTypes:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_known_language(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        types = splitter._get_semantic_node_types("java")
        assert "class" in types
        assert "function" in types
        assert "class_declaration" in types["class"]

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_unknown_language(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        types = splitter._get_semantic_node_types("fortran")
        assert types == {"class": [], "function": []}


# ─────────────────────────────────────────────────────────────
# _split_fallback
# ─────────────────────────────────────────────────────────────
class TestSplitFallback:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_empty_text_returns_empty(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        doc = LlamaDocument(text="", metadata={"path": "x.txt", "language": "text"})
        nodes = splitter._split_fallback(doc)
        assert nodes == []

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_small_text_produces_one_chunk(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter(max_chunk_size=5000, min_chunk_size=10)

        code = "def hello():\n    print('hello')\n" * 5
        doc = LlamaDocument(text=code, metadata={"path": "test.py", "language": "python"})
        nodes = splitter._split_fallback(doc)
        assert len(nodes) >= 1
        assert all(isinstance(n, TextNode) for n in nodes)
        # Check metadata
        for n in nodes:
            assert n.metadata.get("content_type") == "fallback"

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_large_text_produces_multiple_chunks(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter(max_chunk_size=200, min_chunk_size=10, chunk_overlap=20)

        code = "\n".join([f"def func_{i}():\n    return {i}" for i in range(100)])
        doc = LlamaDocument(text=code, metadata={"path": "big.py", "language": "python"})
        nodes = splitter._split_fallback(doc)
        assert len(nodes) > 1


# ─────────────────────────────────────────────────────────────
# _split_oversized_chunk
# ─────────────────────────────────────────────────────────────
class TestSplitOversizedChunk:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_oversized_chunk_split(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter(max_chunk_size=200, min_chunk_size=10)

        big_content = "public class BigClass {\n" + "\n".join(
            [f"    void method{i}() {{ }}" for i in range(50)]
        ) + "\n}"

        chunk = ASTChunk(
            content=big_content,
            content_type=ContentType.FUNCTIONS_CLASSES,
            language="java",
            path="BigClass.java",
            semantic_names=["BigClass"],
            node_type="class",
            start_line=1,
            end_line=52,
            extends=["Base"],
            implements=["IFoo"],
            methods=["method0", "method1"],
            parent_context=["com.example"],
        )

        from langchain_text_splitters import Language
        nodes = splitter._split_oversized_chunk(chunk, Language.JAVA, {"path": "BigClass.java"}, "BigClass.java")
        assert len(nodes) >= 2
        for n in nodes:
            assert n.metadata.get("content_type") == "oversized_split"
            assert n.metadata.get("is_fragment") is True
            assert n.metadata.get("fragment_of") == "BigClass"

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_oversized_preserves_parent_context(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter(max_chunk_size=100, min_chunk_size=5, chunk_overlap=20)

        chunk = ASTChunk(
            content="x " * 200,
            content_type=ContentType.FUNCTIONS_CLASSES,
            language="java",
            path="X.java",
            parent_context=["OuterClass"],
            semantic_names=["innerMethod"],
            start_line=10,
            end_line=20,
        )

        nodes = splitter._split_oversized_chunk(chunk, None, {"path": "X.java"}, "X.java")
        for n in nodes:
            assert n.metadata.get("parent_class") == "OuterClass"


# ─────────────────────────────────────────────────────────────
# _build_metadata
# ─────────────────────────────────────────────────────────────
class TestBuildMetadata:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_full_metadata(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        chunk = ASTChunk(
            content="def foo(): pass",
            content_type=ContentType.FUNCTIONS_CLASSES,
            language="python",
            path="foo.py",
            semantic_names=["foo"],
            node_type="function",
            start_line=1,
            end_line=1,
            parent_context=["MyClass"],
            docstring="A function",
            signature="def foo()",
            extends=["Base"],
            implements=["IFoo"],
            imports=["os"],
            namespace="mypackage",
            methods=["m1"],
            properties=["p1"],
            parameters=["a"],
            return_type="int",
            decorators=["staticmethod"],
            modifiers=["static"],
            calls=["print"],
            referenced_types=["str"],
            variables=["x"],
            constants=["C"],
            type_parameters=["T"],
        )

        meta = splitter._build_metadata(chunk, {"path": "foo.py"}, 0, 1)

        assert meta["content_type"] == "functions_classes"
        assert meta["primary_name"] == "foo"
        assert meta["parent_class"] == "MyClass"
        assert meta["extends"] == ["Base"]
        assert meta["implements"] == ["IFoo"]
        assert meta["imports"] == ["os"]
        assert meta["namespace"] == "mypackage"
        assert meta["methods"] == ["m1"]
        assert meta["properties"] == ["p1"]
        assert meta["parameters"] == ["a"]
        assert meta["return_type"] == "int"
        assert meta["decorators"] == ["staticmethod"]
        assert meta["modifiers"] == ["static"]
        assert meta["calls"] == ["print"]
        assert meta["referenced_types"] == ["str"]
        assert meta["variables"] == ["x"]
        assert meta["constants"] == ["C"]
        assert meta["type_parameters"] == ["T"]
        assert "information_density" in meta
        assert meta["full_path"] == "MyClass.foo"

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_minimal_metadata(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        chunk = ASTChunk(
            content="pass",
            content_type=ContentType.SIMPLIFIED_CODE,
            language="python",
            path="x.py",
        )

        meta = splitter._build_metadata(chunk, {"path": "x.py"}, 0, 1)
        assert meta["content_type"] == "simplified_code"
        assert "primary_name" not in meta
        assert "extends" not in meta
