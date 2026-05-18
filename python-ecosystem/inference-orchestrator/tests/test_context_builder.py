"""
Unit tests for utils.context_builder — RAGMetrics, SmartChunker, RAGCache.
"""
import time
import pytest
from utils.context_builder import RAGMetrics, SmartChunker, RAGCache, get_rag_cache


# ── RAGMetrics ───────────────────────────────────────────────────

class TestRAGMetrics:

    def test_defaults(self):
        m = RAGMetrics()
        assert m.query_count == 0
        assert m.avg_relevance_score == 0.0

    def test_to_dict(self):
        m = RAGMetrics(query_count=2, total_results=10, high_priority_hits=3)
        d = m.to_dict()
        assert d["query_count"] == 2
        assert d["total_results"] == 10
        assert d["priority_distribution"]["high"] == 3

    def test_from_results_empty(self):
        m = RAGMetrics.from_results([], processing_time_ms=100.0)
        assert m.total_results == 0
        assert m.processing_time_ms == 100.0

    def test_from_results_with_data(self):
        results = [
            {"score": 0.9, "_priority": "HIGH"},
            {"score": 0.7, "_priority": "MEDIUM"},
            {"score": 0.5, "_priority": "LOW"},
        ]
        m = RAGMetrics.from_results(results, reranking_applied=True)
        assert m.total_results == 3
        assert m.high_priority_hits == 1
        assert m.medium_priority_hits == 1
        assert m.low_priority_hits == 1
        assert m.max_relevance_score == 0.9
        assert m.min_relevance_score == 0.5
        assert m.reranking_applied is True

    def test_from_results_missing_priority(self):
        results = [{"score": 0.8}]  # no _priority → defaults to MEDIUM
        m = RAGMetrics.from_results(results)
        assert m.medium_priority_hits == 1


# ── SmartChunker._detect_language ────────────────────────────────

class TestSmartChunkerDetectLanguage:

    @pytest.mark.parametrize("path,expected", [
        ("main.py", "python"),
        ("app.tsx", "typescript"),
        ("index.js", "javascript"),
        ("Main.java", "java"),
        ("app.kt", "kotlin"),
        ("main.go", "go"),
        ("index.php", "php"),
        ("Program.cs", "csharp"),
        ("main.cpp", "cpp"),
        ("app.swift", "swift"),
        ("unknown.xyz", "javascript"),
        ("", "javascript"),
    ])
    def test_detect(self, path, expected):
        assert SmartChunker._detect_language(path) == expected


class TestSmartChunkerGetPatterns:

    def test_known_language(self):
        patterns = SmartChunker._get_patterns_for_language("python")
        assert len(patterns) > 0

    def test_unknown_defaults_to_js(self):
        patterns = SmartChunker._get_patterns_for_language("brainfuck")
        assert patterns == SmartChunker.LANGUAGE_PATTERNS["javascript"]


class TestSmartChunkerGetImportPattern:

    def test_python(self):
        pattern = SmartChunker._get_import_pattern("python")
        assert "import" in pattern

    def test_unknown(self):
        pattern = SmartChunker._get_import_pattern("unknown")
        assert "import" in pattern


# ── SmartChunker.smart_chunk ─────────────────────────────────────

class TestSmartChunk:

    def test_small_file_single_chunk(self):
        content = "def foo():\n    pass\n"
        result = SmartChunker.smart_chunk(content, max_tokens=1000)
        assert len(result) == 1
        assert result[0] == content

    def test_large_file_splits(self):
        # Create content that exceeds max_tokens
        content = "\n".join([f"def func_{i}():\n    pass\n" for i in range(100)])
        result = SmartChunker.smart_chunk(content, max_tokens=200, tokens_per_char=0.25)
        assert len(result) > 1

    def test_preserves_imports(self):
        content = "import os\nimport sys\n\ndef main():\n    pass\n"
        # Make it need splitting
        content += "\n".join([f"def f{i}():\n    return {i}" for i in range(200)])
        result = SmartChunker.smart_chunk(content, max_tokens=300, preserve_imports=True,
                                          file_path="main.py")
        if len(result) > 1:
            assert "import os" in result[1]

    def test_with_file_path(self):
        content = "class Foo:\n    pass\n" * 100
        result = SmartChunker.smart_chunk(content, max_tokens=200, file_path="test.java")
        assert len(result) >= 1


# ── SmartChunker._find_boundaries ────────────────────────────────

class TestFindBoundaries:

    def test_python_boundaries(self):
        lines = ["import os", "", "class MyClass:", "    def method(self):", "        pass"]
        boundaries = SmartChunker._find_boundaries(lines, "main.py")
        types = [b[1] for b in boundaries]
        assert "class" in types

    def test_java_boundaries(self):
        lines = ["public class Main {", "    public void run() {", "    }", "}"]
        boundaries = SmartChunker._find_boundaries(lines, "Main.java")
        assert len(boundaries) > 0

    def test_no_boundaries(self):
        lines = ["x = 1", "y = 2"]
        boundaries = SmartChunker._find_boundaries(lines, "data.txt")
        # txt isn't recognized → uses JS patterns → no match
        assert isinstance(boundaries, list)


# ── SmartChunker._chunk_by_lines ─────────────────────────────────

class TestChunkByLines:

    def test_basic(self):
        content = "line1\nline2\nline3\nline4\n"
        result = SmartChunker._chunk_by_lines(content, max_chars=15)
        assert len(result) >= 1

    def test_with_imports(self):
        content = "a\nb\nc\nd\ne\n"
        result = SmartChunker._chunk_by_lines(content, max_chars=10, imports_section="import x\n")
        assert all("import x" in c for c in result)


# ── SmartChunker.chunk_diff ──────────────────────────────────────

class TestChunkDiff:

    def test_small_diff_single_chunk(self):
        diff = "@@ -1,3 +1,3 @@\n-old\n+new\n"
        result = SmartChunker.chunk_diff(diff, max_tokens=1000)
        assert len(result) == 1

    def test_large_diff_splits(self):
        hunks = "\n".join([
            f"@@ -{i*10},5 +{i*10},5 @@\n-removed_{i}\n+added_{i}\n context\n"
            for i in range(50)
        ])
        diff = "--- a/file.py\n+++ b/file.py\n" + hunks
        result = SmartChunker.chunk_diff(diff, max_tokens=200, tokens_per_char=0.25)
        assert len(result) > 1


# ── RAGCache ─────────────────────────────────────────────────────

class TestRAGCache:

    def test_get_miss(self):
        cache = RAGCache(ttl_seconds=60, max_size=10)
        result = cache.get("ws", "proj", "main", ["a.py"])
        assert result is None

    def test_set_and_get(self):
        cache = RAGCache(ttl_seconds=60, max_size=10)
        cache.set("ws", "proj", "main", ["a.py"], {"data": "test"})
        result = cache.get("ws", "proj", "main", ["a.py"])
        assert result is not None
        assert result["data"] == "test"

    def test_ttl_expiry(self):
        cache = RAGCache(ttl_seconds=0, max_size=10)  # 0 TTL = immediate expiry
        cache.set("ws", "proj", "main", ["a.py"], {"data": "test"})
        time.sleep(0.01)
        result = cache.get("ws", "proj", "main", ["a.py"])
        assert result is None

    def test_max_size_eviction(self):
        cache = RAGCache(ttl_seconds=300, max_size=2)
        cache.set("ws", "p", "b1", ["a.py"], {"d": 1})
        cache.set("ws", "p", "b2", ["b.py"], {"d": 2})
        cache.set("ws", "p", "b3", ["c.py"], {"d": 3})
        # After eviction, oldest should be gone
        assert len(cache._cache) <= 2

    def test_clear(self):
        cache = RAGCache(ttl_seconds=300, max_size=10)
        cache.set("ws", "p", "b", ["a.py"], {"d": 1})
        cache.clear()
        assert len(cache._cache) == 0

    def test_stats(self):
        cache = RAGCache(ttl_seconds=300, max_size=10)
        cache.set("ws", "p", "b", ["a.py"], {"d": 1})
        stats = cache.stats()
        assert stats["total_entries"] == 1
        assert stats["max_size"] == 10

    def test_invalidate(self):
        cache = RAGCache(ttl_seconds=1, max_size=10)
        cache.set("ws", "p", "b", ["a.py"], {"d": 1})
        time.sleep(0.6)
        count = cache.invalidate("ws", "p")
        assert isinstance(count, int)

    def test_cached_at_excluded_from_get(self):
        cache = RAGCache(ttl_seconds=60, max_size=10)
        cache.set("ws", "p", "b", ["a.py"], {"key": "val"})
        result = cache.get("ws", "p", "b", ["a.py"])
        assert "_cached_at" not in result


class TestGetRagCache:

    def test_returns_instance(self):
        cache = get_rag_cache()
        assert isinstance(cache, RAGCache)

    def test_singleton(self):
        assert get_rag_cache() is get_rag_cache()
