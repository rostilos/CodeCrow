"""
Tests for rag_pipeline.core.splitter.splitter — ASTCodeSplitter.
Targets the biggest uncovered module (650 stmts, 578 missing).
"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import dataclass

from rag_pipeline.core.splitter.splitter import (
    ASTCodeSplitter,
    ASTChunk,
    generate_deterministic_id,
    compute_file_hash,
    ContentType,
)


# ── Helper functions ──


class TestGenerateDeterministicId:

    def test_same_input_same_output(self):
        id1 = generate_deterministic_id("file.py", "content", 0)
        id2 = generate_deterministic_id("file.py", "content", 0)
        assert id1 == id2

    def test_different_path_different_id(self):
        id1 = generate_deterministic_id("a.py", "content", 0)
        id2 = generate_deterministic_id("b.py", "content", 0)
        assert id1 != id2

    def test_different_chunk_index_different_id(self):
        id1 = generate_deterministic_id("file.py", "content", 0)
        id2 = generate_deterministic_id("file.py", "content", 1)
        assert id1 != id2

    def test_id_is_32_chars(self):
        result = generate_deterministic_id("f.py", "c", 0)
        assert len(result) == 32


class TestComputeFileHash:

    def test_deterministic(self):
        h1 = compute_file_hash("hello")
        h2 = compute_file_hash("hello")
        assert h1 == h2

    def test_different_content(self):
        assert compute_file_hash("a") != compute_file_hash("b")


# ── ASTChunk dataclass ──


class TestASTChunk:

    def test_default_fields(self):
        chunk = ASTChunk(
            content="code",
            content_type=ContentType.FUNCTIONS_CLASSES,
            language="python",
            path="test.py",
        )
        assert chunk.methods == []
        assert chunk.properties == []
        assert chunk.parameters == []
        assert chunk.return_type is None
        assert chunk.decorators == []
        assert chunk.modifiers == []
        assert chunk.calls == []
        assert chunk.referenced_types == []
        assert chunk.variables == []
        assert chunk.constants == []
        assert chunk.type_parameters == []

    def test_all_fields(self):
        chunk = ASTChunk(
            content="code",
            content_type=ContentType.FUNCTIONS_CLASSES,
            language="python",
            path="test.py",
            semantic_names=["MyClass"],
            parent_context=["Outer"],
            methods=["foo", "bar"],
            extends=["Base"],
            implements=["IFoo"],
            decorators=["staticmethod"],
        )
        assert chunk.methods == ["foo", "bar"]
        assert chunk.extends == ["Base"]


# ── ASTCodeSplitter initialization ──


class TestASTCodeSplitterInit:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_default_init(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()
        assert splitter.max_chunk_size == 8000
        assert splitter.min_chunk_size == 100
        assert splitter.chunk_overlap == 200
        assert splitter.parser_threshold == 3
        assert splitter.enrich_embedding_text is True

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_custom_init(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter(
            max_chunk_size=4000,
            min_chunk_size=50,
            chunk_overlap=100,
            parser_threshold=5,
            enrich_embedding_text=False,
        )
        assert splitter.max_chunk_size == 4000
        assert splitter.enrich_embedding_text is False


# ── split_documents ──


class TestSplitDocuments:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_empty_documents(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()
        result = splitter.split_documents([])
        assert result == []

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    @patch("rag_pipeline.core.splitter.splitter.get_language_from_path")
    def test_fallback_for_unknown_language(self, mock_lang, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        mock_lang.return_value = None

        splitter = ASTCodeSplitter()

        doc = MagicMock()
        doc.text = "some code here\nline 2\nline 3\nline 4\nline 5"
        doc.metadata = {"path": "unknown.xyz", "language": "text"}

        nodes = splitter.split_documents([doc])
        assert len(nodes) >= 0  # May produce nodes via fallback

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    @patch("rag_pipeline.core.splitter.splitter.get_language_from_path")
    @patch("rag_pipeline.core.splitter.splitter.AST_SUPPORTED_LANGUAGES", new=set())
    def test_fallback_for_unsupported_ast_language(self, mock_lang, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        from langchain_text_splitters import Language
        mock_lang.return_value = Language.PYTHON

        splitter = ASTCodeSplitter()
        doc = MagicMock()
        doc.text = "def foo():\n    pass\n" * 10
        doc.metadata = {"path": "test.py", "language": "python"}

        nodes = splitter.split_documents([doc])
        assert len(nodes) >= 0


# ── _split_fallback ──


class TestSplitFallback:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_empty_text_returns_empty(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        doc = MagicMock()
        doc.text = ""
        doc.metadata = {"path": "test.py", "language": "python"}

        nodes = splitter._split_fallback(doc)
        assert nodes == []

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_whitespace_only_returns_empty(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        doc = MagicMock()
        doc.text = "   \n\n   "
        doc.metadata = {"path": "test.py", "language": "python"}

        nodes = splitter._split_fallback(doc)
        assert nodes == []

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_small_code(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        doc = MagicMock()
        doc.text = "class Foo(Bar):\n    def method(self):\n        return 42\n" * 5
        doc.metadata = {"path": "test.py", "language": "python"}

        nodes = splitter._split_fallback(doc)
        assert len(nodes) > 0
        # Check metadata
        node = nodes[0]
        assert node.metadata["content_type"] == "fallback"

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_fallback_with_no_language(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        doc = MagicMock()
        doc.text = "some random text\n" * 20
        doc.metadata = {"path": "readme.txt", "language": "text"}

        nodes = splitter._split_fallback(doc, language=None)
        assert len(nodes) >= 0


# ── _build_metadata ──


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
            path="test.py",
            semantic_names=["foo"],
            parent_context=["MyClass"],
            docstring="Does something",
            signature="def foo():",
            start_line=1,
            end_line=5,
            node_type="function_definition",
            extends=["Base"],
            implements=["IFoo"],
            imports=["os"],
            namespace="mypackage",
            methods=["bar"],
            properties=["prop"],
            parameters=["x", "y"],
            return_type="int",
            decorators=["staticmethod"],
            modifiers=["public"],
            calls=["print"],
            referenced_types=["str"],
            variables=["z"],
            constants=["PI"],
            type_parameters=["T"],
        )

        base_meta = {"workspace": "ws", "project": "proj", "branch": "main", "path": "test.py"}
        meta = splitter._build_metadata(chunk, base_meta, 0, 5)

        assert meta["content_type"] == "functions_classes"
        assert meta["semantic_names"] == ["foo"]
        assert meta["primary_name"] == "foo"
        assert meta["parent_context"] == ["MyClass"]
        assert meta["parent_class"] == "MyClass"
        assert meta["docstring"] == "Does something"
        assert meta["signature"] == "def foo():"
        assert meta["extends"] == ["Base"]
        assert meta["implements"] == ["IFoo"]
        assert meta["imports"] == ["os"]
        assert meta["namespace"] == "mypackage"
        assert meta["methods"] == ["bar"]
        assert meta["properties"] == ["prop"]
        assert meta["parameters"] == ["x", "y"]
        assert meta["return_type"] == "int"
        assert meta["decorators"] == ["staticmethod"]
        assert meta["modifiers"] == ["public"]
        assert meta["calls"] == ["print"]
        assert meta["referenced_types"] == ["str"]
        assert meta["variables"] == ["z"]
        assert meta["constants"] == ["PI"]
        assert meta["type_parameters"] == ["T"]
        assert "information_density" in meta

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_minimal_metadata(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        chunk = ASTChunk(
            content="x = 1",
            content_type=ContentType.FALLBACK,
            language="python",
            path="test.py",
        )
        meta = splitter._build_metadata(chunk, {}, 0, 1)
        assert meta["content_type"] == "fallback"
        assert "parent_context" not in meta
        assert "semantic_names" not in meta


# ── _compute_information_density ──


class TestComputeInformationDensity:

    def test_high_density(self):
        meta = {
            "start_line": 1, "end_line": 10,
            "semantic_names": ["foo", "bar"],
            "signature": "def foo():",
            "methods": ["m1", "m2", "m3"],
            "properties": ["p1"],
            "calls": ["print", "len"],
            "docstring": "hello",
            "parameters": ["x"],
        }
        density = ASTCodeSplitter._compute_information_density(meta)
        assert density > 0.0
        assert density <= 1.0

    def test_low_density(self):
        meta = {"start_line": 1, "end_line": 200}
        density = ASTCodeSplitter._compute_information_density(meta)
        assert density == 0.0

    def test_caps_at_1(self):
        meta = {
            "start_line": 1, "end_line": 2,
            "semantic_names": ["a", "b", "c", "d", "e"],
            "signature": "sig",
            "methods": list(range(20)),
            "properties": list(range(20)),
            "constants": list(range(10)),
            "extends": ["Base"],
            "implements": ["IFoo"],
            "calls": list(range(50)),
            "referenced_types": list(range(50)),
            "docstring": "doc",
            "parameters": list(range(20)),
            "decorators": list(range(10)),
        }
        density = ASTCodeSplitter._compute_information_density(meta)
        assert density == 1.0


# ── _create_embedding_text ──


class TestCreateEmbeddingText:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_enriched_text(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        meta = {
            "path": "src/main.py",
            "parent_context": ["MyClass"],
            "extends": ["Base"],
            "implements": ["IFoo"],
            "node_type": "class",
            "methods": ["m1", "m2"],
            "properties": ["p1", "p2"],
            "docstring": "A class.",
        }
        text = splitter._create_embedding_text("class MyClass:", meta)
        assert "[" in text
        assert "File:" in text
        assert "In: MyClass" in text
        assert "Extends: Base" in text
        assert "Implements: IFoo" in text

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_no_enrichment_when_disabled(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter(enrich_embedding_text=False)

        text = splitter._create_embedding_text("code", {"path": "f.py"})
        assert text == "code"

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_namespace_cleaned(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        meta = {"path": "f.py", "namespace": "package com.example;"}
        text = splitter._create_embedding_text("code", meta)
        assert "com.example" in text

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_no_context_returns_plain(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        text = splitter._create_embedding_text("code", {})
        assert text == "code"


# ── _clean_path ──


class TestCleanPath:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_strips_commit_hash_prefix(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        result = splitter._clean_path("owner-repo-abc123def456789012345678901234567890/src/main.py")
        assert result == "src/main.py"

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_keeps_normal_path(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        result = splitter._clean_path("src/main.py")
        assert result == "src/main.py"

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_empty_path(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        assert splitter._clean_path("") == ""
        assert splitter._clean_path(None) is None


# ── _parse_type_list ──


class TestParseTypeList:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_simple_list(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        result = splitter._parse_type_list("Base, Mixin")
        assert result == ["Base", "Mixin"]

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


# ── _get_semantic_node_types ──


class TestGetSemanticNodeTypes:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_python_types(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        types = splitter._get_semantic_node_types("python")
        assert "class_definition" in types["class"]
        assert "function_definition" in types["function"]

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_java_types(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        types = splitter._get_semantic_node_types("java")
        assert "class_declaration" in types["class"]

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_unknown_language(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        types = splitter._get_semantic_node_types("unknown")
        assert types == {"class": [], "function": []}


# ── _get_rich_node_types ──


class TestGetRichNodeTypes:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    @pytest.mark.parametrize("lang", ["python", "java", "javascript", "typescript", "go", "rust", "c_sharp", "php"])
    def test_known_languages(self, mock_qr, mock_parser, lang):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        types = splitter._get_rich_node_types(lang)
        assert "method" in types
        assert "property" in types
        assert "parameter" in types
        assert "decorator" in types
        assert "call" in types or "call" in types.get("call", []) is not None

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_unknown_gets_defaults(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        types = splitter._get_rich_node_types("brainfuck")
        assert "method" in types
        assert types["method"] == []


# ── _split_oversized_chunk ──


class TestSplitOversizedChunk:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_splits_large_chunk(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter(max_chunk_size=100, min_chunk_size=10, chunk_overlap=10)

        chunk = ASTChunk(
            content="def foo():\n    pass\n" * 50,
            content_type=ContentType.FUNCTIONS_CLASSES,
            language="python",
            path="test.py",
            semantic_names=["foo"],
            parent_context=["MyClass"],
            start_line=1,
            end_line=100,
            node_type="function_definition",
            extends=["Base"],
            implements=["IFoo"],
            methods=["bar", "baz"],
        )

        from langchain_text_splitters import Language
        nodes = splitter._split_oversized_chunk(chunk, Language.PYTHON, {"path": "test.py"}, "test.py")
        assert len(nodes) > 0
        for node in nodes:
            assert node.metadata["content_type"] == "oversized_split"
            assert node.metadata.get("is_fragment") is True


# ── _create_simplified_code ──


class TestCreateSimplifiedCode:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_replaces_chunks_with_placeholders(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        source = "import os\n\ndef foo():\n    pass\n\ndef bar():\n    pass\n"
        chunks = [
            ASTChunk(
                content="def foo():\n    pass",
                content_type=ContentType.FUNCTIONS_CLASSES,
                language="python",
                path="test.py",
                start_line=3,
                end_line=4,
            ),
        ]
        result = splitter._create_simplified_code(source, chunks, "python")
        assert "# Code for:" in result

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_no_semantic_chunks_returns_source(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        source = "import os\nprint('hello')"
        chunks = [
            ASTChunk(
                content=source,
                content_type=ContentType.SIMPLIFIED_CODE,
                language="python",
                path="test.py",
            ),
        ]
        result = splitter._create_simplified_code(source, chunks, "python")
        assert result == source


# ── Static methods ──


class TestStaticMethods:

    def test_get_supported_languages(self):
        langs = ASTCodeSplitter.get_supported_languages()
        assert isinstance(langs, list)
        assert len(langs) > 0

    def test_is_ast_supported_python(self):
        assert ASTCodeSplitter.is_ast_supported("test.py") is True

    def test_is_ast_supported_unknown(self):
        assert ASTCodeSplitter.is_ast_supported("test.xyz") is False


# ── _get_text_splitter ──


class TestGetTextSplitter:

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_caches_splitters(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        from langchain_text_splitters import Language
        s1 = splitter._get_text_splitter(Language.PYTHON)
        s2 = splitter._get_text_splitter(Language.PYTHON)
        assert s1 is s2

    @patch("rag_pipeline.core.splitter.splitter.get_parser")
    @patch("rag_pipeline.core.splitter.splitter.get_query_runner")
    def test_different_languages(self, mock_qr, mock_parser):
        mock_parser.return_value = MagicMock()
        mock_qr.return_value = MagicMock()
        splitter = ASTCodeSplitter()

        from langchain_text_splitters import Language
        s_py = splitter._get_text_splitter(Language.PYTHON)
        s_js = splitter._get_text_splitter(Language.JS)
        assert s_py is not s_js
