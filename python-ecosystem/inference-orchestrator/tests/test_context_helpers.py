"""
Unit tests for service.review.orchestrator.context_helpers —
extract_symbols_from_diff, extract_diff_snippets, get_diff_snippets_for_batch, format_rag_context.
"""
import pytest
from service.review.orchestrator.context_helpers import (
    extract_symbols_from_diff,
    extract_diff_snippets,
    get_diff_snippets_for_batch,
    format_rag_context,
)


SAMPLE_DIFF = """\
diff --git a/src/OrderService.java b/src/OrderService.java
--- a/src/OrderService.java
+++ b/src/OrderService.java
@@ -10,6 +10,10 @@
+    public Order createOrder(CreateOrderRequest request) {
+        OrderValidator validator = new OrderValidator();
+        validator.validate(request);
+        return orderRepository.save(request.toOrder());
+    }
-    public void oldMethod() {
"""


# ── extract_symbols_from_diff ────────────────────────────────────

class TestExtractSymbolsFromDiff:

    def test_extracts_camel_case(self):
        symbols = extract_symbols_from_diff(SAMPLE_DIFF)
        assert any("OrderService" in s or "OrderValidator" in s or "CreateOrderRequest" in s for s in symbols)

    def test_extracts_snake_case(self):
        diff = "+    user_name = get_user_name(request)"
        symbols = extract_symbols_from_diff(diff)
        assert any("user_name" in s or "get_user_name" in s for s in symbols)

    def test_preserves_keywords_as_neutral_tokens(self):
        symbols = extract_symbols_from_diff(SAMPLE_DIFF)
        assert "public" in symbols
        assert "return" in symbols

    def test_empty(self):
        assert extract_symbols_from_diff("") == []
        assert extract_symbols_from_diff(None) == []

    def test_limit_20(self):
        # Generate diff with many symbols
        big_diff = "\n".join(f"+    {chr(65+i)}Symbol{i*100}Name = 1" for i in range(26))
        symbols = extract_symbols_from_diff(big_diff)
        assert len(symbols) <= 20


# ── extract_diff_snippets ────────────────────────────────────────

class TestExtractDiffSnippets:

    def test_extracts_added_lines(self):
        snippets = extract_diff_snippets(SAMPLE_DIFF)
        assert len(snippets) > 0
        # Should contain meaningful code from added lines
        combined = " ".join(snippets)
        assert "createOrder" in combined or "OrderValidator" in combined or "orderRepository" in combined

    def test_preserves_comments_and_trivial_added_lines(self):
        diff = "+// comment\n+#\n+{\n+}\n+\n+   real_code = True"
        snippets = extract_diff_snippets(diff)
        combined = "\n".join(snippets)
        assert "// comment" in combined
        assert "#" in combined
        assert "{" in combined
        assert "}" in combined

    def test_empty(self):
        assert extract_diff_snippets("") == []
        assert extract_diff_snippets(None) == []

    def test_limit_10(self):
        big = "\n".join(f"+    statement_{i} = very_long_code_expression_{i}()" for i in range(50))
        snippets = extract_diff_snippets(big)
        assert len(snippets) <= 10


# ── get_diff_snippets_for_batch ──────────────────────────────────

class TestGetDiffSnippetsForBatch:

    def test_returns_all_snippets(self):
        """Since Java snippets are clean code without file paths, all are returned."""
        all_snippets = ["def foo():", "class Bar:", "import os"]
        batch_files = ["src/app.py"]
        result = get_diff_snippets_for_batch(all_snippets, batch_files)
        assert result == all_snippets

    def test_empty(self):
        assert get_diff_snippets_for_batch([], ["a.py"]) == []


# ── format_rag_context ───────────────────────────────────────────

class TestFormatRagContext:

    def test_empty_input(self):
        assert format_rag_context(None) == ""
        assert format_rag_context({}) == ""
        assert format_rag_context({"relevant_code": []}) == ""

    def test_basic_chunk(self):
        rag = {
            "relevant_code": [
                {
                    "text": "def process(): pass",
                    "score": 0.90,
                    "metadata": {"path": "src/proc.py", "content_type": "functions_classes"},
                    "_source": "semantic",
                }
            ]
        }
        result = format_rag_context(rag)
        assert "src/proc.py" in result
        assert "def process(): pass" in result

    def test_filters_deleted_files(self):
        rag = {
            "relevant_code": [
                {
                    "text": "old code",
                    "score": 0.90,
                    "metadata": {"path": "deleted.py"},
                    "_source": "semantic",
                },
                {
                    "text": "kept code",
                    "score": 0.90,
                    "metadata": {"path": "kept.py"},
                    "_source": "semantic",
                },
            ]
        }
        result = format_rag_context(rag, deleted_files=["deleted.py"])
        assert "deleted.py" not in result
        assert "kept.py" in result

    def test_tiered_budgeting(self):
        """Tier 1 (definition) chunks should appear in output."""
        chunks = [
            {
                "text": f"class Base{i}: pass",
                "score": 0.95,
                "metadata": {"path": f"src/base{i}.py", "content_type": "functions_classes"},
                "_match_type": "definition",
                "_source": "deterministic",
            }
            for i in range(12)
        ]
        rag = {"relevant_code": chunks}
        result = format_rag_context(rag)
        # Tier 1 budget is 8, so at least 8 should appear
        count = sum(1 for i in range(12) if f"src/base{i}.py" in result)
        assert count >= 8

    def test_low_score_documentation_chunk_preserved(self):
        rag = {
            "relevant_code": [
                {
                    "text": "readme content",
                    "score": 0.50,
                    "metadata": {"path": "README.md", "content_type": "documentation"},
                    "_source": "semantic",
                },
            ]
        }
        result = format_rag_context(rag)
        assert "README.md" in result
        assert "readme content" in result

    def test_deduplication(self):
        """Chunks with same basename+content should be deduplicated."""
        rag = {
            "relevant_code": [
                {
                    "text": "same content here",
                    "score": 0.90,
                    "metadata": {"path": "src/a/util.py"},
                    "_source": "semantic",
                },
                {
                    "text": "same content here",
                    "score": 0.88,
                    "metadata": {"path": "src/b/util.py"},
                    "_source": "semantic",
                },
            ]
        }
        result = format_rag_context(rag)
        # Should only include one
        assert result.count("same content here") == 1

    def test_stale_chunk_from_modified_file_low_score(self):
        rag = {
            "relevant_code": [
                {
                    "text": "stale code",
                    "score": 0.50,
                    "metadata": {"path": "modified.py"},
                    "_source": "semantic",
                },
            ]
        }
        result = format_rag_context(rag, pr_changed_files=["modified.py"])
        assert result == ""

    def test_pr_indexed_not_filtered(self):
        """PR-indexed chunks from modified files should NOT be filtered."""
        rag = {
            "relevant_code": [
                {
                    "text": "fresh indexed code",
                    "score": 0.80,
                    "metadata": {"path": "modified.py"},
                    "_source": "pr_indexed",
                },
            ]
        }
        result = format_rag_context(rag, pr_changed_files=["modified.py"])
        assert "fresh indexed code" in result
