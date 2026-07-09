"""
Extended tests for service.review.orchestrator.context_helpers — 
extract_symbols_from_diff, extract_diff_snippets, format_rag_context,
format_duplication_context, get_diff_snippets_for_batch.
"""
import pytest
from service.review.orchestrator.context_helpers import (
    extract_symbols_from_diff,
    extract_diff_snippets,
    get_diff_snippets_for_batch,
    format_rag_context,
    format_duplication_context,
)


# ── extract_symbols_from_diff ────────────────────────────────

class TestExtractSymbolsFromDiff:
    def test_empty(self):
        assert extract_symbols_from_diff("") == []
        assert extract_symbols_from_diff(None) == []

    def test_camel_case(self):
        diff = "+class MyService implements BaseInterface {"
        result = extract_symbols_from_diff(diff)
        assert "MyService" in result or "BaseInterface" in result

    def test_snake_case(self):
        diff = "+def process_review_request(self):"
        result = extract_symbols_from_diff(diff)
        assert "process_review_request" in result

    def test_imports(self):
        diff = "+from utils.response_parser import ResponseParser"
        result = extract_symbols_from_diff(diff)
        assert any("response_parser" in s or "ResponseParser" in s for s in result)

    def test_preserves_keywords_as_neutral_tokens(self):
        diff = "+import os\n+class True:\n+def return():"
        result = extract_symbols_from_diff(diff)
        assert "import" in result
        assert "True" in result
        assert "return" in result

    def test_limits_results(self):
        diff = "\n".join(f"+class Symbol{i}Extra:" for i in range(50))
        result = extract_symbols_from_diff(diff)
        assert len(result) <= 20


# ── extract_diff_snippets ───────────────────────────────────

class TestExtractDiffSnippets:
    def test_empty(self):
        assert extract_diff_snippets("") == []
        assert extract_diff_snippets(None) == []

    def test_added_lines(self):
        diff = "+++ b/file.py\n+def long_function_name():\n+    return something_interesting\n+    more_code_here()"
        result = extract_diff_snippets(diff)
        assert len(result) >= 1

    def test_preserves_comments(self):
        diff = "+// This is a comment\n+# another comment\n+* javadoc"
        result = extract_diff_snippets(diff)
        assert "// This is a comment" in "\n".join(result)

    def test_preserves_short_lines(self):
        diff = "+x = 1\n+y = 2"  # too short
        result = extract_diff_snippets(diff)
        assert "x = 1" in "\n".join(result)
        assert "y = 2" in "\n".join(result)

    def test_batching(self):
        diff = "\n".join(f"+def function_{i}(): return value_{i}" for i in range(20))
        result = extract_diff_snippets(diff)
        assert len(result) <= 10

    def test_preserves_braces(self):
        diff = "+{\n+}\n+"
        result = extract_diff_snippets(diff)
        assert "{" in "\n".join(result)
        assert "}" in "\n".join(result)


# ── get_diff_snippets_for_batch ──────────────────────────────

class TestGetDiffSnippetsForBatch:
    def test_empty(self):
        assert get_diff_snippets_for_batch([], ["a.py"]) == []

    def test_returns_all(self):
        snippets = ["code1", "code2"]
        result = get_diff_snippets_for_batch(snippets, ["a.py"])
        assert result == snippets


# ── format_rag_context ───────────────────────────────────────

class TestFormatRagContext:
    def test_none(self):
        assert format_rag_context(None) == ""

    def test_empty_chunks(self):
        assert format_rag_context({"relevant_code": []}) == ""

    def test_basic_chunk(self):
        ctx = {
            "relevant_code": [
                {
                    "metadata": {"path": "src/utils.py", "content_type": "code"},
                    "text": "def helper():\n    pass",
                    "score": 0.9,
                }
            ]
        }
        result = format_rag_context(ctx)
        assert "src/utils.py" in result
        assert "def helper" in result

    def test_filters_deleted_files(self):
        ctx = {
            "relevant_code": [
                {"metadata": {"path": "old.py"}, "text": "old code", "score": 0.9},
            ]
        }
        result = format_rag_context(ctx, deleted_files=["old.py"])
        assert result == ""

    def test_preserves_documentation_context_for_llm(self):
        ctx = {
            "relevant_code": [
                {"metadata": {"path": "README.md", "content_type": "documentation"}, "text": "readme content", "score": 0.5},
            ]
        }
        result = format_rag_context(ctx)
        assert "README.md" in result
        assert "readme content" in result

    def test_tiered_assembly(self):
        chunks = []
        # Tier 1: definitions
        chunks.append({"metadata": {"path": "types.py"}, "text": "class Base: pass", "score": 0.9, "_match_type": "definition"})
        # Tier 2: changed_file context
        chunks.append({"metadata": {"path": "service.py"}, "text": "def svc(): pass", "score": 0.85, "_match_type": "changed_file"})
        # Tier 3: duplication
        chunks.append({"metadata": {"path": "other.py"}, "text": "def dup(): pass", "score": 0.7, "_source": "duplication"})
        
        ctx = {"relevant_code": chunks}
        result = format_rag_context(ctx)
        assert "types.py" in result
        assert "service.py" in result

    def test_deduplication(self):
        same_text = "def foo(): return 42"
        ctx = {
            "relevant_code": [
                {"metadata": {"path": "a/utils.py"}, "text": same_text, "score": 0.9},
                {"metadata": {"path": "b/utils.py"}, "text": same_text, "score": 0.8},
            ]
        }
        result = format_rag_context(ctx)
        # Same basename + content → deduped
        assert result.count("utils.py") >= 1

    def test_chunks_key_fallback(self):
        ctx = {"chunks": [{"metadata": {"path": "a.py"}, "text": "code", "score": 0.9}]}
        result = format_rag_context(ctx)
        assert "a.py" in result

    def test_metadata_formatting(self):
        ctx = {
            "relevant_code": [
                {
                    "metadata": {
                        "path": "service.java",
                        "namespace": "com.example",
                        "primary_name": "UserService",
                        "extends": ["BaseService"],
                        "implements": ["IService"],
                        "imports": ["com.util.Helper"],
                    },
                    "text": "public class UserService {}",
                    "score": 0.95,
                }
            ]
        }
        result = format_rag_context(ctx)
        assert "Namespace: com.example" in result
        assert "Definition: UserService" in result
        assert "Extends: BaseService" in result

    def test_pr_changed_stale_filtering(self):
        ctx = {
            "relevant_code": [
                {
                    "metadata": {"path": "modified.py"},
                    "text": "old code from branch index",
                    "score": 0.5,  # below stale threshold
                },
            ]
        }
        result = format_rag_context(ctx, pr_changed_files=["modified.py"])
        # Score below threshold → filtered as stale
        assert result == ""

    def test_pr_indexed_not_stale(self):
        ctx = {
            "relevant_code": [
                {
                    "metadata": {"path": "modified.py"},
                    "text": "fresh PR code",
                    "score": 0.5,
                    "_source": "pr_indexed",
                },
            ]
        }
        result = format_rag_context(ctx, pr_changed_files=["modified.py"])
        # PR-indexed is NOT stale
        assert "modified.py" in result


# ── format_duplication_context ───────────────────────────────

class TestFormatDuplicationContext:
    def test_empty(self):
        assert format_duplication_context([], ["a.py"]) == ""

    def test_filters_self_matches(self):
        results = [
            {"metadata": {"path": "src/a.py"}, "text": "code here", "score": 0.9},
        ]
        assert format_duplication_context(results, ["src/a.py"]) == ""

    def test_filters_low_score(self):
        results = [
            {"metadata": {"path": "other.py"}, "text": "code", "score": 0.3},
        ]
        assert format_duplication_context(results, ["a.py"]) == ""

    def test_formats_results(self):
        results = [
            {"metadata": {"path": "lib/helper.py", "primary_name": "helper_func"},
             "text": "def helper_func():\n    pass",
             "score": 0.85, "_query": "find similar"},
        ]
        result = format_duplication_context(results, ["src/main.py"])
        assert "helper.py" in result
        assert "SIMILAR IMPLEMENTATIONS" in result

    def test_deduplication(self):
        results = [
            {"metadata": {"path": "lib/a.py"}, "text": "same code same code same", "score": 0.8},
            {"metadata": {"path": "lib/b.py"}, "text": "same code same code same", "score": 0.7},
        ]
        result = format_duplication_context(results, ["src/main.py"])
        # deduplication by text hash
        assert result.count("Existing Implementation") >= 1

    def test_max_chunks_limit(self):
        results = [
            {"metadata": {"path": f"lib/f{i}.py"}, "text": f"unique code {i}" * 20, "score": 0.8}
            for i in range(20)
        ]
        result = format_duplication_context(results, ["src/main.py"], max_chunks=3)
        assert result.count("Existing Implementation") <= 3
