"""
Tests for rag_pipeline.core.splitter.query_runner — QueryRunner, CapturedNode, QueryMatch.
"""
import pytest
from unittest.mock import patch, MagicMock


# ─────────────────────────────────────────────────────────────
# CapturedNode
# ─────────────────────────────────────────────────────────────
class TestCapturedNode:

    def test_creation(self):
        from rag_pipeline.core.splitter.query_runner import CapturedNode

        node = CapturedNode(
            name="function",
            text="def foo(): pass",
            start_byte=0,
            end_byte=15,
            start_point=(0, 0),
            end_point=(0, 15),
            node_type="function_definition",
        )
        assert node.text == "def foo(): pass"
        assert node.node_type == "function_definition"
        assert node.name == "function"
        assert node.start_byte == 0
        assert node.end_byte == 15

    def test_start_line_property(self):
        from rag_pipeline.core.splitter.query_runner import CapturedNode

        node = CapturedNode("fn", "code", 0, 10, (5, 0), (5, 10), "type")
        assert node.start_line == 6  # 1-based

    def test_end_line_property(self):
        from rag_pipeline.core.splitter.query_runner import CapturedNode

        node = CapturedNode("fn", "code", 0, 10, (0, 0), (9, 10), "type")
        assert node.end_line == 10  # 1-based

    def test_equality_by_fields(self):
        from rag_pipeline.core.splitter.query_runner import CapturedNode

        n1 = CapturedNode("name", "code", 0, 10, (0, 0), (0, 10), "type")
        n2 = CapturedNode("name", "code", 0, 10, (0, 0), (0, 10), "type")
        assert n1 == n2


# ─────────────────────────────────────────────────────────────
# QueryMatch
# ─────────────────────────────────────────────────────────────
class TestQueryMatch:

    def test_creation(self):
        from rag_pipeline.core.splitter.query_runner import QueryMatch, CapturedNode

        node = CapturedNode("name", "code", 0, 4, (0, 0), (0, 4), "identifier")
        match = QueryMatch(pattern_name="function", captures={"name": node})
        assert match.pattern_name == "function"
        assert match.captures["name"] is node

    def test_get_capture(self):
        from rag_pipeline.core.splitter.query_runner import QueryMatch, CapturedNode

        node = CapturedNode("fn", "code", 0, 4, (0, 0), (0, 4), "identifier")
        match = QueryMatch(pattern_name="function", captures={"function": node})
        assert match.get("function") is node
        assert match.get("missing") is None

    def test_full_text(self):
        from rag_pipeline.core.splitter.query_runner import QueryMatch, CapturedNode

        node = CapturedNode("function", "def foo(): pass", 0, 15, (0, 0), (0, 15), "function_definition")
        match = QueryMatch(pattern_name="function", captures={"function": node})
        assert match.full_text == "def foo(): pass"


# ─────────────────────────────────────────────────────────────
# QueryRunner
# ─────────────────────────────────────────────────────────────
class TestQueryRunner:

    def _make_runner(self):
        from rag_pipeline.core.splitter.query_runner import QueryRunner

        with patch("rag_pipeline.core.splitter.query_runner.get_parser") as mock_get_parser:
            mock_parser = MagicMock()
            mock_get_parser.return_value = mock_parser
            runner = QueryRunner()
        return runner, mock_parser

    def test_init(self):
        runner, _ = self._make_runner()
        assert runner is not None
        assert runner._query_cache == {}

    def test_get_compiled_query_python(self):
        runner, mock_parser = self._make_runner()

        mock_language = MagicMock()
        mock_parser.get_language.return_value = mock_language

        # Mock _load_custom_query_file to return a query string
        with patch.object(runner, '_load_custom_query_file', return_value="(function_definition) @function"):
            with patch.object(runner, '_try_compile_query', return_value=MagicMock()):
                query = runner._get_compiled_query("python")
                assert query is not None

    def test_get_compiled_query_unknown_language(self):
        runner, mock_parser = self._make_runner()

        # Unknown language: parser returns None for language
        mock_parser.get_language.return_value = None

        query = runner._get_compiled_query("brainfuck")
        assert query is None

    def test_get_compiled_query_caches(self):
        runner, mock_parser = self._make_runner()

        mock_language = MagicMock()
        mock_parser.get_language.return_value = mock_language

        with patch.object(runner, '_load_custom_query_file', return_value="(function_definition) @function"):
            with patch.object(runner, '_try_compile_query', return_value=MagicMock()) as mock_compile:
                q1 = runner._get_compiled_query("python")
                q2 = runner._get_compiled_query("python")
                assert q1 is q2
                # Should only compile once (second call uses cache)
                mock_compile.assert_called_once()

    def test_run_query_no_compiled_query_returns_empty(self):
        runner, mock_parser = self._make_runner()

        # No language available -> no query
        mock_parser.get_language.return_value = None

        results = runner.run_query("def foo(): pass", "brainfuck")
        assert results == []

    def test_run_query_with_matches(self):
        runner, mock_parser = self._make_runner()

        mock_language = MagicMock()
        mock_parser.get_language.return_value = mock_language

        # Create a mock compiled query
        mock_query = MagicMock()
        runner._query_cache["python"] = mock_query

        # Mock tree parse
        mock_tree = MagicMock()
        mock_parser.parse.return_value = mock_tree

        mock_node = MagicMock()
        mock_node.start_byte = 0
        mock_node.end_byte = 15
        mock_node.start_point = MagicMock(row=0, column=0)
        mock_node.end_point = MagicMock(row=0, column=15)
        mock_node.type = "function_definition"

        # QueryCursor is imported locally inside run_query, so patch tree_sitter.QueryCursor
        with patch("tree_sitter.QueryCursor") as mock_cursor_cls:
            mock_cursor = MagicMock()
            mock_cursor.matches.return_value = [
                (0, {"definition.function": [mock_node], "name": [mock_node]}),
            ]
            mock_cursor_cls.return_value = mock_cursor

            results = runner.run_query("def foo(): pass", "python")
            assert isinstance(results, list)

    def test_run_query_parser_error_returns_empty(self):
        runner, mock_parser = self._make_runner()

        # Put a query in cache so it tries to parse
        runner._query_cache["python"] = MagicMock()

        # Parser returns None tree
        mock_parser.parse.return_value = None

        results = runner.run_query("bad code", "python")
        assert results == []
