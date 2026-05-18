"""
Unit tests for rag_pipeline.core.splitter.metadata module.
Covers: MetadataExtractor docstring/signature/name extraction, ContentType enum.
"""
import pytest

from rag_pipeline.core.splitter.metadata import (
    ContentType,
    ChunkMetadata,
    MetadataExtractor,
)


class TestContentType:

    def test_enum_values(self):
        assert ContentType.FUNCTIONS_CLASSES.value == "functions_classes"
        assert ContentType.SIMPLIFIED_CODE.value == "simplified_code"
        assert ContentType.FALLBACK.value == "fallback"
        assert ContentType.OVERSIZED_SPLIT.value == "oversized_split"


class TestChunkMetadata:

    def test_defaults(self):
        m = ChunkMetadata(
            content_type=ContentType.FUNCTIONS_CLASSES,
            language="python",
            path="test.py",
        )
        assert m.semantic_names == []
        assert m.docstring is None
        assert m.signature is None
        assert m.extends == []
        assert m.implements == []
        assert m.imports == []
        assert m.namespace is None


class TestExtractDocstring:

    def setup_method(self):
        self.extractor = MetadataExtractor()

    def test_python_triple_double_quotes(self):
        code = '''def foo():
    """This is a docstring."""
    pass'''
        result = self.extractor.extract_docstring(code, "python")
        assert result == "This is a docstring."

    def test_python_triple_single_quotes(self):
        code = """def foo():
    '''Another docstring.'''
    pass"""
        result = self.extractor.extract_docstring(code, "python")
        assert result == "Another docstring."

    def test_java_javadoc(self):
        code = """/**
 * Finds a user by ID.
 * @param id the user ID
 */
public User findById(Long id) {"""
        result = self.extractor.extract_docstring(code, "java")
        assert "Finds a user by ID" in result

    def test_javascript_jsdoc(self):
        code = """/**
 * Calculate the sum.
 */
function sum(a, b) {"""
        result = self.extractor.extract_docstring(code, "javascript")
        assert "Calculate the sum" in result

    def test_rust_doc_comments(self):
        code = """/// Adds two numbers.
/// Returns the sum.
fn add(a: i32, b: i32) -> i32 {"""
        result = self.extractor.extract_docstring(code, "rust")
        assert "Adds two numbers" in result
        assert "Returns the sum" in result

    def test_no_docstring_returns_none(self):
        code = "x = 1 + 2"
        assert self.extractor.extract_docstring(code, "python") is None
        assert self.extractor.extract_docstring(code, "java") is None


class TestExtractSignature:

    def setup_method(self):
        self.extractor = MetadataExtractor()

    def test_python_function(self):
        code = "def calculate_total(items, tax_rate):\n    pass"
        sig = self.extractor.extract_signature(code, "python")
        assert sig is not None
        assert "calculate_total" in sig

    def test_python_async_function(self):
        code = "async def fetch_data(url):\n    pass"
        sig = self.extractor.extract_signature(code, "python")
        assert sig is not None
        assert "fetch_data" in sig

    def test_python_class(self):
        code = "class UserService:\n    pass"
        sig = self.extractor.extract_signature(code, "python")
        assert sig is not None
        assert "UserService" in sig

    def test_java_method(self):
        code = "public User findById(Long id) {\n    return null;\n}"
        sig = self.extractor.extract_signature(code, "java")
        assert sig is not None
        assert "findById" in sig

    def test_javascript_function(self):
        code = "function calculateTotal(items) {\n    return 0;\n}"
        sig = self.extractor.extract_signature(code, "javascript")
        assert sig is not None
        assert "calculateTotal" in sig

    def test_javascript_arrow_function(self):
        code = "const fetchData = (url) => {\n    return null;\n}"
        sig = self.extractor.extract_signature(code, "javascript")
        # Arrow functions should be extracted
        assert sig is not None

    def test_go_function(self):
        code = "func HandleRequest(w http.ResponseWriter, r *http.Request) {\n}"
        sig = self.extractor.extract_signature(code, "go")
        assert sig is not None
        assert "HandleRequest" in sig

    def test_rust_function(self):
        code = "pub fn process_data(input: &str) -> Result<String, Error> {\n}"
        sig = self.extractor.extract_signature(code, "rust")
        assert sig is not None
        assert "process_data" in sig

    def test_php_function(self):
        code = "public function getUser($id) {\n    return null;\n}"
        sig = self.extractor.extract_signature(code, "php")
        assert sig is not None
        assert "getUser" in sig

    def test_no_signature_returns_none(self):
        code = "x = 1 + 2\ny = 3"
        assert self.extractor.extract_signature(code, "python") is None


class TestExtractNamesFromContent:

    def setup_method(self):
        self.extractor = MetadataExtractor()

    def test_python_names(self):
        code = """
class UserService:
    def get_user(self, user_id):
        pass
    def create_user(self, data):
        pass
"""
        names = self.extractor.extract_names_from_content(code, "python")
        assert "UserService" in names
        assert "get_user" in names

    def test_java_names(self):
        code = """
public class OrderService {
    public Order createOrder(OrderRequest req) {}
    private void validateOrder(Order order) {}
}
"""
        names = self.extractor.extract_names_from_content(code, "java")
        assert "OrderService" in names
