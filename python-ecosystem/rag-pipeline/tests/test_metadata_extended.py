"""
Extended tests for rag_pipeline.core.splitter.metadata module.
Targets missing lines: 90-92, 120, 138-140, 158-163, 166, 191-192, 267-295, 299-335,
339, 360, 365, 369, 379-387, 391-406, 416-424, 433-460, 472-501, 511-519, 527-563
"""
import pytest
from unittest.mock import MagicMock, PropertyMock

from rag_pipeline.core.splitter.metadata import (
    ContentType,
    ChunkMetadata,
    MetadataExtractor,
)


@pytest.fixture
def extractor():
    return MetadataExtractor()


# ── Docstring extraction (regex) ──


class TestExtractDocstringRegex:
    """Cover _extract_docstring_regex edge cases."""

    def test_python_triple_single_quotes(self, extractor):
        code = "def f():\n    '''Single-quoted doc.'''\n    pass"
        result = extractor._extract_docstring_regex(code, "python")
        assert result == "Single-quoted doc."

    def test_python_no_docstring(self, extractor):
        code = "def f():\n    pass"
        assert extractor._extract_docstring_regex(code, "python") is None

    @pytest.mark.parametrize("lang", [
        "javascript", "typescript", "java", "kotlin", "c_sharp", "php", "go", "scala", "c", "cpp"
    ])
    def test_jsdoc_style_docstring(self, extractor, lang):
        code = "/**\n * This is a doc.\n * @param x number\n */\nfunction foo() {}"
        result = extractor._extract_docstring_regex(code, lang)
        assert result is not None
        assert "This is a doc" in result

    def test_rust_doc_comments_triple_slash(self, extractor):
        code = "/// First line\n/// Second line\nfn main() {}"
        result = extractor._extract_docstring_regex(code, "rust")
        assert "First line" in result
        assert "Second line" in result

    def test_rust_doc_comments_bang(self, extractor):
        code = "//! Module doc line\nfn main() {}"
        result = extractor._extract_docstring_regex(code, "rust")
        assert "Module doc line" in result

    def test_rust_stops_at_non_doc(self, extractor):
        code = "/// Doc line\nfn main() {}"
        result = extractor._extract_docstring_regex(code, "rust")
        assert result == "Doc line"

    def test_unknown_language_returns_none(self, extractor):
        assert extractor._extract_docstring_regex("hello", "cobol") is None


# ── Signature extraction (regex) — missing lines 138-166 ──


class TestExtractSignatureRegex:

    def test_python_simple_def(self, extractor):
        code = "def hello(x, y):\n    return x + y"
        sig = extractor._extract_signature_regex(code, "python")
        assert sig is not None
        assert "def hello" in sig

    def test_python_async_def(self, extractor):
        code = "async def run(ctx):\n    await ctx.do()"
        sig = extractor._extract_signature_regex(code, "python")
        assert "async def run" in sig

    def test_python_class_signature(self, extractor):
        code = "class Foo(Base):\n    pass"
        sig = extractor._extract_signature_regex(code, "python")
        assert sig is not None
        assert "class Foo" in sig

    def test_python_multiline_def(self, extractor):
        code = "def foo(\n    x,\n    y\n):\n    pass"
        sig = extractor._extract_signature_regex(code, "python")
        assert sig is not None
        assert "def foo" in sig

    @pytest.mark.parametrize("lang", ["java", "kotlin", "c_sharp"])
    def test_java_style_signature(self, extractor, lang):
        code = "public void doSomething(int x) {\n    return;\n}"
        sig = extractor._extract_signature_regex(code, lang)
        assert sig is not None
        assert "doSomething" in sig

    def test_kotlin_fun(self, extractor):
        code = "fun compute(n: Int): Int {\n    return n * 2\n}"
        sig = extractor._extract_signature_regex(code, "kotlin")
        assert sig is not None
        assert "fun compute" in sig

    def test_javascript_function(self, extractor):
        code = "function processData(items) {\n    return items;\n}"
        sig = extractor._extract_signature_regex(code, "javascript")
        assert "function processData" in sig

    def test_javascript_async_function(self, extractor):
        code = "async function fetchData() {\n    return null;\n}"
        sig = extractor._extract_signature_regex(code, "javascript")
        assert "async function fetchData" in sig

    def test_javascript_class(self, extractor):
        code = "class Widget {\n    constructor() {}\n}"
        sig = extractor._extract_signature_regex(code, "javascript")
        assert "class Widget" in sig

    def test_javascript_arrow(self, extractor):
        code = "const fn = (x) => {\n    return x;\n}"
        sig = extractor._extract_signature_regex(code, "javascript")
        assert sig is not None
        assert "=>" in sig

    def test_typescript_function(self, extractor):
        code = "function greet(name: string): string {\n    return name;\n}"
        sig = extractor._extract_signature_regex(code, "typescript")
        assert "function greet" in sig

    def test_go_func(self, extractor):
        code = "func main() {\n    fmt.Println()\n}"
        sig = extractor._extract_signature_regex(code, "go")
        assert "func main()" in sig

    def test_go_type(self, extractor):
        code = "type Server struct {\n    Port int\n}"
        sig = extractor._extract_signature_regex(code, "go")
        assert "type Server struct" in sig

    def test_rust_fn(self, extractor):
        code = "pub fn calculate(x: i32) -> i32 {\n    x * 2\n}"
        sig = extractor._extract_signature_regex(code, "rust")
        assert "pub fn calculate" in sig

    def test_rust_struct(self, extractor):
        code = "struct Config {\n    name: String,\n}"
        sig = extractor._extract_signature_regex(code, "rust")
        assert "struct Config" in sig

    def test_rust_impl(self, extractor):
        code = "impl Server {\n    fn new() -> Self {}\n}"
        sig = extractor._extract_signature_regex(code, "rust")
        assert "impl Server" in sig

    def test_rust_trait(self, extractor):
        code = "trait Handler {\n    fn handle();\n}"
        sig = extractor._extract_signature_regex(code, "rust")
        assert "trait Handler" in sig

    def test_rust_enum(self, extractor):
        code = "enum Color {\n    Red,\n    Blue,\n}"
        sig = extractor._extract_signature_regex(code, "rust")
        assert "enum Color" in sig

    def test_rust_async_fn(self, extractor):
        code = "pub async fn serve() {\n    loop {}\n}"
        sig = extractor._extract_signature_regex(code, "rust")
        assert "pub async fn serve" in sig

    def test_php_function(self, extractor):
        code = "function handle($request) {\n    return null;\n}"
        sig = extractor._extract_signature_regex(code, "php")
        assert "function handle" in sig

    def test_php_class(self, extractor):
        code = "class UserController {\n    public function index() {}\n}"
        sig = extractor._extract_signature_regex(code, "php")
        assert "class UserController" in sig

    def test_php_interface(self, extractor):
        code = "interface Cacheable {\n    public function cache();\n}"
        sig = extractor._extract_signature_regex(code, "php")
        assert "interface Cacheable" in sig

    def test_returns_none_for_no_match(self, extractor):
        code = "# just a comment\nsome_var = 42"
        assert extractor._extract_signature_regex(code, "python") is None

    def test_unknown_language(self, extractor):
        code = "module Main where"
        assert extractor._extract_signature_regex(code, "haskell") is None


# ── Name extraction — covers extract_names_from_content / _get_name_patterns ──


class TestExtractNames:

    def test_python_names(self, extractor):
        code = "class Foo:\n    def bar(self):\n        pass\n    async def baz():\n        pass"
        names = extractor.extract_names_from_content(code, "python")
        assert "Foo" in names
        assert "bar" in names
        assert "baz" in names

    def test_java_names(self, extractor):
        code = "public class UserService {\n    public void createUser() {}\n    public interface Repo {}\n}"
        names = extractor.extract_names_from_content(code, "java")
        assert "UserService" in names

    def test_javascript_names(self, extractor):
        code = "class Widget {}\nfunction render() {}\nconst fn = (x) => x;"
        names = extractor.extract_names_from_content(code, "javascript")
        assert "Widget" in names
        assert "render" in names

    def test_typescript_names(self, extractor):
        code = "export interface Config {}\nexport type Props = {}\nexport class App {}"
        names = extractor.extract_names_from_content(code, "typescript")
        assert "Config" in names
        assert "Props" in names
        assert "App" in names

    def test_go_names(self, extractor):
        code = "func (s *Server) Handle() {}\ntype Config struct {}"
        names = extractor.extract_names_from_content(code, "go")
        assert "Handle" in names
        assert "Config" in names

    def test_rust_names(self, extractor):
        code = "pub fn run() {}\npub struct App {}\npub trait Handler {}\npub enum Color {}"
        names = extractor.extract_names_from_content(code, "rust")
        assert "run" in names
        assert "App" in names

    def test_php_names(self, extractor):
        code = "class Controller {}\nfunction handle() {}\ninterface Repo {}"
        names = extractor.extract_names_from_content(code, "php")
        assert "Controller" in names
        assert "handle" in names

    def test_csharp_names(self, extractor):
        code = "public class UserService {}\npublic interface IRepo {}\npublic int GetCount() {}"
        names = extractor.extract_names_from_content(code, "c_sharp")
        assert "UserService" in names

    def test_unknown_language_returns_empty(self, extractor):
        names = extractor.extract_names_from_content("hello world", "brainfuck")
        assert names == []

    def test_deduplication(self, extractor):
        code = "def foo():\n    pass\ndef foo():\n    pass"
        names = extractor.extract_names_from_content(code, "python")
        assert names.count("foo") == 1

    def test_limits_to_30(self, extractor):
        code = "\n".join(f"def func_{i}():\n    pass" for i in range(50))
        names = extractor.extract_names_from_content(code, "python")
        assert len(names) <= 30


# ── Inheritance extraction — covers extract_inheritance / _get_inheritance_patterns ──


class TestExtractInheritance:

    def test_python_extends(self, extractor):
        code = "class Foo(Base, Mixin):\n    pass"
        result = extractor.extract_inheritance(code, "python")
        assert "Base" in result["extends"]
        assert "Mixin" in result["extends"]

    def test_python_imports(self, extractor):
        code = "from os.path import join\nimport sys"
        result = extractor.extract_inheritance(code, "python")
        assert len(result["imports"]) > 0

    def test_java_extends_and_implements(self, extractor):
        code = "class Foo extends Bar implements Baz, Qux {}"
        result = extractor.extract_inheritance(code, "java")
        assert "Bar" in result["extends"]
        assert "Baz" in result["implements"]

    def test_java_imports(self, extractor):
        code = "import java.util.List;\nimport java.io.*;"
        result = extractor.extract_inheritance(code, "java")
        assert len(result["imports"]) > 0

    def test_typescript_extends_and_implements(self, extractor):
        code = "class Foo extends Bar implements IFoo {}"
        result = extractor.extract_inheritance(code, "typescript")
        assert "Bar" in result["extends"]
        assert "IFoo" in result["implements"]

    def test_typescript_import(self, extractor):
        code = "import { Foo } from './foo';"
        result = extractor.extract_inheritance(code, "typescript")
        assert "./foo" in result["imports"]

    def test_javascript_extends(self, extractor):
        code = "class Child extends Parent {}"
        result = extractor.extract_inheritance(code, "javascript")
        assert "Parent" in result["extends"]

    def test_javascript_require(self, extractor):
        code = "const x = require('express');"
        result = extractor.extract_inheritance(code, "javascript")
        assert "express" in result["imports"]

    def test_php_extends_implements(self, extractor):
        code = "class Foo extends Bar implements Baz {}"
        result = extractor.extract_inheritance(code, "php")
        assert "Bar" in result["extends"]

    def test_php_use(self, extractor):
        code = "use App\\Models\\User;"
        result = extractor.extract_inheritance(code, "php")
        assert len(result["imports"]) > 0

    def test_csharp_extends(self, extractor):
        code = "class Foo : Bar {}"
        result = extractor.extract_inheritance(code, "c_sharp")
        assert "Bar" in result["extends"]

    def test_csharp_using(self, extractor):
        code = "using System.Collections.Generic;"
        result = extractor.extract_inheritance(code, "c_sharp")
        assert len(result["imports"]) > 0

    def test_go_import(self, extractor):
        code = 'import "fmt"'
        result = extractor.extract_inheritance(code, "go")
        assert "fmt" in result["imports"]

    def test_rust_use(self, extractor):
        code = "use std::collections::HashMap;"
        result = extractor.extract_inheritance(code, "rust")
        assert len(result["imports"]) > 0

    def test_unknown_language(self, extractor):
        result = extractor.extract_inheritance("code", "cobol")
        assert result == {"extends": [], "implements": [], "imports": []}

    def test_imports_capped_at_50(self, extractor):
        code = "\n".join(f"import module_{i};" for i in range(60))
        result = extractor.extract_inheritance(code, "java")
        assert len(result["imports"]) <= 50


# ── get_comment_prefix ──


class TestGetCommentPrefix:

    @pytest.mark.parametrize("lang,expected", [
        ("python", "#"), ("javascript", "//"), ("java", "//"),
        ("go", "//"), ("rust", "//"), ("ruby", "#"),
        ("lua", "--"), ("unknown_lang", "//"),
    ])
    def test_comment_prefix(self, extractor, lang, expected):
        assert extractor.get_comment_prefix(lang) == expected


# ── Tree-sitter AST extraction helpers ──


class TestIsCommentNode:

    def test_comment_node(self, extractor):
        node = MagicMock()
        node.type = "comment"
        assert extractor._is_comment_node(node) is True

    def test_block_comment(self, extractor):
        node = MagicMock()
        node.type = "block_comment"
        assert extractor._is_comment_node(node) is True

    def test_non_comment(self, extractor):
        node = MagicMock()
        node.type = "function_definition"
        assert extractor._is_comment_node(node) is False


class TestIsStringNode:

    def test_string_node(self, extractor):
        node = MagicMock()
        node.type = "string"
        assert extractor._is_string_node(node) is True

    def test_not_string(self, extractor):
        node = MagicMock()
        node.type = "identifier"
        assert extractor._is_string_node(node) is False


class TestIsBodyNode:

    def test_block(self, extractor):
        node = MagicMock()
        node.type = "block"
        assert extractor._is_body_node(node) is True

    def test_class_body(self, extractor):
        node = MagicMock()
        node.type = "class_body"
        assert extractor._is_body_node(node) is True

    def test_heuristic_body(self, extractor):
        node = MagicMock()
        node.type = "function_body"
        assert extractor._is_body_node(node) is True

    def test_not_body(self, extractor):
        node = MagicMock()
        node.type = "identifier"
        assert extractor._is_body_node(node) is False


# ── AST docstring extraction ──


class TestExtractPythonDocstringAST:

    def test_extracts_triple_quote_docstring(self, extractor):
        body_expr_string = MagicMock()
        body_expr_string.type = "string"
        body_expr_string.text = b'"""Hello world."""'

        body_expr_stmt = MagicMock()
        body_expr_stmt.type = "expression_statement"
        body_expr_stmt.children = [body_expr_string]

        body = MagicMock()
        body.type = "block"
        body.children = [body_expr_stmt]

        node = MagicMock()
        node.children = [body]

        result = extractor._extract_python_docstring_ast(node)
        assert result == "Hello world."

    def test_no_body_returns_none(self, extractor):
        node = MagicMock()
        child = MagicMock()
        child.type = "identifier"
        node.children = [child]
        assert extractor._extract_python_docstring_ast(node) is None


class TestExtractPrecedingCommentDocstring:

    def test_extracts_preceding_comment(self, extractor):
        prev = MagicMock()
        prev.type = "comment"
        prev.text = b"// This is a comment"

        node = MagicMock()
        node.prev_sibling = prev

        result = extractor._extract_preceding_comment_docstring(node)
        assert result is not None
        assert "This is a comment" in result

    def test_no_prev_sibling(self, extractor):
        node = MagicMock()
        node.prev_sibling = None
        node.prev_named_sibling = None
        assert extractor._extract_preceding_comment_docstring(node) is None

    def test_prev_not_comment(self, extractor):
        prev = MagicMock()
        prev.type = "identifier"
        node = MagicMock()
        node.prev_sibling = prev
        assert extractor._extract_preceding_comment_docstring(node) is None


class TestCleanCommentText:

    def test_block_comment(self):
        result = MetadataExtractor._clean_comment_text("/* Hello world */")
        assert result is not None
        assert "Hello" in result

    def test_javadoc_comment(self):
        result = MetadataExtractor._clean_comment_text("/** Doc line\n * @param x */")
        assert result is not None

    def test_line_comments(self):
        result = MetadataExtractor._clean_comment_text("// Line one\n// Line two")
        assert "Line one" in result
        assert "Line two" in result

    def test_hash_comments(self):
        result = MetadataExtractor._clean_comment_text("# Hash comment")
        assert "Hash comment" in result

    def test_rust_doc_comment(self):
        result = MetadataExtractor._clean_comment_text("/// Rust doc")
        assert "Rust doc" in result

    def test_empty_returns_none(self):
        assert MetadataExtractor._clean_comment_text("") is None
        assert MetadataExtractor._clean_comment_text("   ") is None


# ── AST signature extraction ──


class TestExtractSignatureFromNode:

    def test_with_body_child(self, extractor):
        body = MagicMock()
        body.type = "block"
        body.start_byte = 20
        body.children = []

        node = MagicMock()
        node.start_byte = 0
        node.end_byte = 50
        node.children = [body]

        content = "def foo(x, y):       { return x + y; }"
        result = extractor._extract_signature_from_node(node, "python", content)
        assert result is not None

    def test_no_body_uses_first_line(self, extractor):
        child = MagicMock()
        child.type = "identifier"
        child.children = []

        node = MagicMock()
        node.children = [child]
        child.children = []

        content = "some_long_declaration_here\nbody"
        result = extractor._extract_signature_from_node(node, "python", content)
        assert result is not None

    def test_exception_returns_none(self, extractor):
        node = MagicMock()
        node.children = MagicMock(side_effect=Exception("boom"))
        result = extractor._extract_signature_from_node(node, "python", "code")
        assert result is None


# ── extract_docstring with ts_node ──


class TestExtractDocstringWithNode:

    def test_prefers_ts_node_when_available(self, extractor):
        # Create a mock node that simulates a Python function with docstring
        body_string = MagicMock()
        body_string.type = "string"
        body_string.text = b'"""AST docstring."""'

        expr_stmt = MagicMock()
        expr_stmt.type = "expression_statement"
        expr_stmt.children = [body_string]

        body = MagicMock()
        body.type = "block"
        body.children = [expr_stmt]

        ts_node = MagicMock()
        ts_node.children = [body]

        result = extractor.extract_docstring("def foo():\n    '''Regex doc'''", "python", ts_node=ts_node)
        assert result == "AST docstring."

    def test_falls_back_to_regex_when_ts_fails(self, extractor):
        node = MagicMock()
        node.children = []

        code = 'def foo():\n    """Regex fallback."""\n    pass'
        result = extractor.extract_docstring(code, "python", ts_node=node)
        assert result == "Regex fallback."


# ── extract_signature with ts_node ──


class TestExtractSignatureWithNode:

    def test_prefers_ts_node(self, extractor):
        body = MagicMock()
        body.type = "block"
        body.start_byte = 15
        body.children = []

        node = MagicMock()
        node.start_byte = 0
        node.end_byte = 30
        node.children = [body]

        content = "def foo(x, y): { body }"
        result = extractor.extract_signature(content, "python", ts_node=node)
        assert result is not None

    def test_falls_back_to_regex(self, extractor):
        node = MagicMock()
        child = MagicMock()
        child.type = "identifier"
        child.children = []
        node.children = [child]

        code = "def hello():\n    pass"
        result = extractor.extract_signature(code, "python", ts_node=node)
        assert result is not None


# ── build_metadata_dict ──


class TestBuildMetadataDict:

    def test_full_metadata(self, extractor):
        chunk_meta = ChunkMetadata(
            content_type=ContentType.FUNCTIONS_CLASSES,
            language="python",
            path="test.py",
            semantic_names=["MyClass", "method"],
            parent_context=["OuterClass"],
            docstring="Does stuff.",
            signature="def method(self):",
            start_line=10,
            end_line=20,
            node_type="function_definition",
            extends=["Base"],
            implements=["IFoo"],
            imports=["os", "sys"],
            namespace="mypackage",
        )
        base = {"workspace": "ws", "project": "proj", "branch": "main", "path": "test.py"}
        result = extractor.build_metadata_dict(chunk_meta, base)

        assert result["content_type"] == "functions_classes"
        assert result["node_type"] == "function_definition"
        assert result["start_line"] == 10
        assert result["end_line"] == 20
        assert result["parent_context"] == ["OuterClass"]
        assert result["parent_class"] == "OuterClass"
        assert "OuterClass.MyClass" in result["full_path"]
        assert result["semantic_names"] == ["MyClass", "method"]
        assert result["primary_name"] == "MyClass"
        assert result["docstring"] == "Does stuff."
        assert result["signature"] == "def method(self):"
        assert result["extends"] == ["Base"]
        assert result["parent_types"] == ["Base"]
        assert result["implements"] == ["IFoo"]
        assert result["imports"] == ["os", "sys"]
        assert result["namespace"] == "mypackage"

    def test_minimal_metadata(self, extractor):
        chunk_meta = ChunkMetadata(
            content_type=ContentType.FALLBACK,
            language="python",
            path="test.py",
        )
        base = {"workspace": "ws"}
        result = extractor.build_metadata_dict(chunk_meta, base)
        assert result["content_type"] == "fallback"
        assert "parent_context" not in result
        assert "semantic_names" not in result
        assert "docstring" not in result

    def test_docstring_truncated_to_1000(self, extractor):
        chunk_meta = ChunkMetadata(
            content_type=ContentType.FUNCTIONS_CLASSES,
            language="python",
            path="test.py",
            docstring="x" * 2000,
        )
        result = extractor.build_metadata_dict(chunk_meta, {})
        assert len(result["docstring"]) == 1000


# ── _extract_docstring_from_node fallback ──


class TestExtractDocstringFromNode:

    def test_non_python_uses_preceding_comment(self, extractor):
        prev = MagicMock()
        prev.type = "comment"
        prev.text = b"// Java doc"

        node = MagicMock()
        node.prev_sibling = prev
        node.children = []

        result = extractor._extract_docstring_from_node(node, "java")
        assert "Java doc" in result

    def test_exception_returns_none(self, extractor):
        node = MagicMock()
        type(node).prev_sibling = PropertyMock(side_effect=Exception("boom"))
        node.children = MagicMock(side_effect=Exception("boom"))
        result = extractor._extract_docstring_from_node(node, "python")
        assert result is None


# ── _find_node_with_body ──


class TestFindNodeWithBody:

    def test_direct_body_child(self, extractor):
        body = MagicMock()
        body.type = "block"
        body.children = []

        node = MagicMock()
        node.children = [body]

        result = extractor._find_node_with_body(node)
        assert result == (node, body)

    def test_grandchild_body(self, extractor):
        grandchild_body = MagicMock()
        grandchild_body.type = "compound_statement"

        child = MagicMock()
        child.type = "function_definition"
        child.children = [grandchild_body]

        node = MagicMock()
        node.children = [child]

        result = extractor._find_node_with_body(node)
        assert result == (child, grandchild_body)

    def test_no_body_returns_none(self, extractor):
        child = MagicMock()
        child.type = "identifier"
        child.children = []

        node = MagicMock()
        node.children = [child]

        assert extractor._find_node_with_body(node) is None
