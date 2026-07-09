"""
Unit tests for utils.signature_patterns compatibility helpers.
"""

from utils.signature_patterns import (
    CONFIG_EXTENSIONS,
    DIFF_SIGNATURE_PATTERNS,
    EVENT_KEYWORDS,
    SKIP_DECORATORS,
    SKIP_FUNC_NAMES,
    extract_class_names,
    extract_decorators,
    extract_event_names,
    extract_function_names,
    extract_identifier_tokens,
    extract_import_modules,
    extract_sql_tables,
)


def test_no_language_specific_pattern_tables_remain():
    assert DIFF_SIGNATURE_PATTERNS == []
    assert SKIP_FUNC_NAMES == set()
    assert SKIP_DECORATORS == set()
    assert EVENT_KEYWORDS == ()
    assert CONFIG_EXTENSIONS == ()


def test_identifier_tokens_are_neutral_and_deduplicated():
    text = "def run():\n    run(order.total)\nclass OrderService:\n    pass"
    tokens = extract_identifier_tokens(text)

    assert "run" in tokens
    assert "order.total" in tokens
    assert "OrderService" in tokens
    assert len(tokens) == len(set(tokens))


def test_compatibility_wrappers_return_raw_tokens_without_classification():
    text = "@override\nSELECT * FROM user_accounts\nclass Foo:\n    def get(self): pass"

    assert "override" in extract_decorators(text)
    assert "user_accounts" in extract_sql_tables(text)
    assert "Foo" in extract_class_names(text)
    assert "get" in extract_function_names(text, min_length=1)
    assert "SELECT" in extract_import_modules(text)
    assert "user_accounts" in extract_event_names(text)


def test_empty_inputs_return_empty_lists():
    assert extract_identifier_tokens("") == []
    assert extract_function_names("") == []
    assert extract_class_names("") == []
    assert extract_import_modules("") == []
    assert extract_decorators("") == []
    assert extract_event_names("") == []
    assert extract_sql_tables("") == []
