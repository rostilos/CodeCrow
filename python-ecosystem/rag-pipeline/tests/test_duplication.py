"""
Unit tests for rag_pipeline.services.duplication module.
"""
import pytest
from rag_pipeline.services.duplication import (
    generate_duplication_queries,
    _is_config_file,
)


class TestIsConfigFile:

    @pytest.mark.parametrize("path,expected", [
        # Config-format extensions
        ("config/database.yml", True),
        ("config/app.yaml", True),
        ("settings.json", True),
        ("pyproject.toml", True),
        ("config.ini", True),
        ("app.properties", True),
        ("server.conf", True),
        (".env", False),  # splitext('.env') gives ext='', not matched by extension
        ("settings.env", True),  # ext = '.env' — matches _CONFIG_EXTENSIONS
        ("di.xml", True),
        # Root manifests
        ("Dockerfile", True),
        ("Makefile", True),
        ("CMakeLists.txt", True),
        # Source code — not config
        ("src/main.py", False),
        ("src/UserService.java", False),
        ("lib/utils.ts", False),
    ])
    def test_detection(self, path, expected):
        assert _is_config_file(path) is expected


class TestGenerateDuplicationQueries:

    def test_empty_inputs(self):
        queries = generate_duplication_queries([], [])
        assert isinstance(queries, list)

    def test_function_declarations_extracted(self):
        snippets = [
            "def calculate_discount(price, rate):\n    return price * rate"
        ]
        queries = generate_duplication_queries(snippets, ["pricing.py"])
        query_texts = [q[0] for q in queries]
        assert any("calculate_discount" in t for t in query_texts)

    def test_skip_lifecycle_functions(self):
        snippets = ["def __init__(self, x):\n    self.x = x"]
        queries = generate_duplication_queries(snippets, ["model.py"])
        query_texts = [q[0] for q in queries]
        assert not any("__init__" in t for t in query_texts)

    def test_class_inheritance_extracted(self):
        snippets = ["class OrderProcessor extends BaseProcessor {"]
        queries = generate_duplication_queries(snippets, ["processor.java"])
        query_texts = [q[0] for q in queries]
        assert any("BaseProcessor" in t for t in query_texts)

    def test_event_patterns_extracted(self):
        snippets = ["emit('order_completed', data)"]
        queries = generate_duplication_queries(snippets, ["order.py"])
        query_texts = [q[0] for q in queries]
        assert any("order_completed" in t for t in query_texts)

    def test_sql_patterns_extracted(self):
        snippets = ["SELECT * FROM users WHERE active = 1"]
        queries = generate_duplication_queries(snippets, ["query.py"])
        query_texts = [q[0] for q in queries]
        assert any("users" in t for t in query_texts)

    def test_decorator_patterns_extracted(self):
        snippets = ['@RequestMapping("/api/orders")\npublic class OrderController {']
        queries = generate_duplication_queries(snippets, ["OrderController.java"])
        query_texts = [q[0] for q in queries]
        # Should find route/mapping decorators
        assert any("RequestMapping" in t or "route" in t.lower() for t in query_texts)

    def test_deduplicates_queries(self):
        snippets = [
            "def calculate_total(items):\n    pass",
            "def calculate_total(products):\n    pass",
        ]
        queries = generate_duplication_queries(snippets, ["a.py", "b.py"])
        query_texts = [q[0] for q in queries]
        # Should only have one query for calculate_total
        total_queries = [t for t in query_texts if "calculate_total" in t]
        assert len(total_queries) == 1

    def test_query_tuples_have_correct_structure(self):
        snippets = ["def process_order(order):\n    pass"]
        queries = generate_duplication_queries(snippets, ["order.py"])
        for q in queries:
            assert len(q) == 4  # (text, weight, top_k, instruction_type)
            text, weight, top_k, inst = q
            assert isinstance(text, str)
            assert isinstance(weight, float)
            assert isinstance(top_k, int)

    def test_short_functions_skipped(self):
        """Functions with <=4 char names should be skipped."""
        snippets = ["def foo(x):\n    pass"]
        queries = generate_duplication_queries(snippets, ["a.py"])
        query_texts = [q[0] for q in queries]
        assert not any("foo" in t for t in query_texts)
