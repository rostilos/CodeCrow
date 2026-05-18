"""
Unit tests for utils.signature_patterns — all extraction functions.
"""
import pytest
from utils.signature_patterns import (
    extract_function_names,
    extract_class_names,
    extract_import_modules,
    extract_decorators,
    extract_event_names,
    extract_sql_tables,
    SKIP_FUNC_NAMES,
    SKIP_DECORATORS,
)


class TestExtractFunctionNames:

    def test_python_def(self):
        text = "def process_order(order_id):\n    pass"
        names = extract_function_names(text)
        assert "process_order" in names

    def test_async_def(self):
        text = "async def fetch_data(url):\n    pass"
        names = extract_function_names(text)
        assert "fetch_data" in names

    def test_java_method(self):
        text = "public void processOrder(Long id) {"
        names = extract_function_names(text)
        # _RE_FUNC_NAME requires def/function/func/fn keyword;
        # Java return-type syntax is not matched by this extractor
        assert "processOrder" not in names

    def test_go_func(self):
        text = "func handleRequest(w http.ResponseWriter, r *http.Request) {"
        names = extract_function_names(text)
        assert "handleRequest" in names

    def test_js_function(self):
        text = "function calculateTotal(items) {"
        names = extract_function_names(text)
        assert "calculateTotal" in names

    def test_skips_generic_names(self):
        text = "def get():\n    pass\ndef run():\n    pass\ndef set():\n    pass"
        names = extract_function_names(text)
        for n in names:
            assert n.lower() not in SKIP_FUNC_NAMES

    def test_min_length_filter(self):
        text = "def go():\n    pass\ndef ab():\n    pass"
        names = extract_function_names(text, min_length=4)
        assert all(len(n) > 4 for n in names)

    def test_deduplication(self):
        text = "def process(x):\n    pass\ndef process(y):\n    pass"
        names = extract_function_names(text)
        assert len(names) == len(set(names))

    def test_empty(self):
        assert extract_function_names("") == []
        assert extract_function_names("no functions here") == []


class TestExtractClassNames:

    def test_class(self):
        text = "class OrderService:\n    pass"
        names = extract_class_names(text)
        assert "OrderService" in names

    def test_interface(self):
        text = "interface PaymentGateway {"
        names = extract_class_names(text)
        assert "PaymentGateway" in names

    def test_struct(self):
        text = "struct UserData {"
        names = extract_class_names(text)
        assert "UserData" in names

    def test_enum(self):
        text = "enum OrderStatus {"
        names = extract_class_names(text)
        assert "OrderStatus" in names

    def test_trait(self):
        text = "trait Serializable {"
        names = extract_class_names(text)
        assert "Serializable" in names

    def test_min_length(self):
        text = "class AB:\n    pass"
        names = extract_class_names(text, min_length=3)
        assert "AB" not in names

    def test_empty(self):
        assert extract_class_names("") == []


class TestExtractImportModules:

    def test_python_import(self):
        text = "import os\nfrom collections import OrderedDict"
        names = extract_import_modules(text)
        assert "collections" in names

    def test_js_require(self):
        text = "import express from 'express'"
        names = extract_import_modules(text)
        assert "express" in names

    def test_java_import(self):
        text = "import java.util.List"
        names = extract_import_modules(text)
        assert "List" in names

    def test_nested_path(self):
        text = "from app.services.order import OrderService"
        names = extract_import_modules(text)
        assert "order" in names

    def test_deduplication(self):
        text = "import os\nimport os"
        names = extract_import_modules(text)
        assert names.count("os") <= 1  # may be filtered by min_length

    def test_empty(self):
        assert extract_import_modules("") == []


class TestExtractDecorators:

    def test_domain_decorators(self):
        text = "@Route('/api/orders')\n@Transactional\ndef process():\n    pass"
        names = extract_decorators(text)
        assert "Route" in names
        assert "Transactional" in names

    def test_skips_standard(self):
        text = "@override\n@staticmethod\n@property\ndef foo():\n    pass"
        names = extract_decorators(text)
        for n in names:
            assert n.lower() not in SKIP_DECORATORS

    def test_empty(self):
        assert extract_decorators("") == []


class TestExtractEventNames:

    def test_event_strings(self):
        text = """
        dispatcher.dispatch('order_create_submit')
        bus.emit('user_account_delete')
        """
        names = extract_event_names(text)
        assert "order_create_submit" in names
        assert "user_account_delete" in names

    def test_requires_event_keyword(self):
        text = "'some_random_long_name'"
        names = extract_event_names(text)
        assert "some_random_long_name" not in names

    def test_needs_three_segments(self):
        text = "'order_submit'"
        names = extract_event_names(text)
        assert len(names) == 0

    def test_empty(self):
        assert extract_event_names("") == []


class TestExtractSqlTables:

    def test_select(self):
        text = "SELECT * FROM orders WHERE id = 1"
        tables = extract_sql_tables(text)
        assert "orders" in tables

    def test_update(self):
        text = "UPDATE users SET name = 'Bob'"
        tables = extract_sql_tables(text)
        assert "users" in tables

    def test_delete(self):
        text = "DELETE FROM inventory WHERE qty = 0"
        tables = extract_sql_tables(text)
        assert "inventory" in tables

    def test_quoted_table(self):
        text = 'SELECT * FROM `order_items`'
        tables = extract_sql_tables(text)
        assert "order_items" in tables

    def test_min_length(self):
        text = "SELECT * FROM t1"
        tables = extract_sql_tables(text, min_length=3)
        assert "t1" not in tables

    def test_empty(self):
        assert extract_sql_tables("") == []
