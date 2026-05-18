"""
Unit tests for utils.diff_parser — DiffParser, DiffFileInfo.
"""
import pytest
from utils.diff_parser import DiffParser, DiffFileInfo


# ── Sample diffs ─────────────────────────────────────────────────

SIMPLE_DIFF = """\
diff --git a/src/main.py b/src/main.py
--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,4 @@
 import os
+import sys
 
 def main():
-    pass
+    print("hello")
"""

MULTI_FILE_DIFF = """\
diff --git a/src/app.py b/src/app.py
new file mode 100644
--- /dev/null
+++ b/src/app.py
@@ -0,0 +1,5 @@
+from flask import Flask
+
+app = Flask(__name__)
+
+def create_app():
+    return app
diff --git a/src/old.py b/src/old.py
deleted file mode 100644
--- a/src/old.py
+++ /dev/null
@@ -1,3 +0,0 @@
-def old_function():
-    pass
diff --git a/src/utils.py b/src/utils.py
rename from src/helpers.py
rename to src/utils.py
--- a/src/helpers.py
+++ b/src/utils.py
@@ -1,3 +1,3 @@
-def helper():
-    pass
+def util():
+    return 42
"""


class TestParseDiff:

    def test_single_file_modified(self):
        files = DiffParser.parse_diff(SIMPLE_DIFF)
        assert len(files) == 1
        f = files[0]
        assert f.path == "src/main.py"
        assert f.change_type == "modified"
        assert "import sys" in f.added_lines
        assert "pass" in f.removed_lines

    def test_multi_file(self):
        files = DiffParser.parse_diff(MULTI_FILE_DIFF)
        assert len(files) == 3

    def test_change_types(self):
        files = DiffParser.parse_diff(MULTI_FILE_DIFF)
        types = {f.path: f.change_type for f in files}
        assert types["src/app.py"] == "added"
        assert types["src/old.py"] == "deleted"
        assert types["src/utils.py"] == "renamed"

    def test_empty_diff(self):
        assert DiffParser.parse_diff("") == []

    def test_max_snippets_limits(self):
        files = DiffParser.parse_diff(SIMPLE_DIFF, max_snippets_per_file=1)
        # Should have at most 1 snippet
        for f in files:
            assert len(f.code_snippets) <= 1


class TestExtractSnippets:

    def test_function_signature_priority(self):
        lines = [
            "def process_order(order_id):",
            "    validate(order_id)",
            "    return True",
        ]
        snippets = DiffParser._extract_snippets(lines, max_snippets=3)
        assert any("process_order" in s for s in snippets)

    def test_class_signature(self):
        lines = ["class UserService:", "    pass"]
        snippets = DiffParser._extract_snippets(lines, max_snippets=3)
        assert any("UserService" in s for s in snippets)

    def test_fallback_to_non_empty(self):
        lines = ["x = calculate_complex_value()", "y = transform(x)"]
        snippets = DiffParser._extract_snippets(lines, max_snippets=2)
        assert len(snippets) > 0

    def test_empty_lines(self):
        assert DiffParser._extract_snippets([], 3) == []

    def test_comments_skipped(self):
        lines = ["# comment", "// comment", "real_code = True"]
        snippets = DiffParser._extract_snippets(lines, max_snippets=3)
        for s in snippets:
            assert not s.startswith("#")
            assert not s.startswith("//")


class TestGetChangedFilePaths:

    def test_excludes_deleted(self):
        files = DiffParser.parse_diff(MULTI_FILE_DIFF)
        paths = DiffParser.get_changed_file_paths(files)
        assert "src/old.py" not in paths
        assert "src/app.py" in paths

    def test_empty(self):
        assert DiffParser.get_changed_file_paths([]) == []


class TestBuildRagQueryFromDiff:

    def test_includes_title_and_description(self):
        files = DiffParser.parse_diff(SIMPLE_DIFF)
        query = DiffParser.build_rag_query_from_diff(
            files, pr_description="Add system import", pr_title="Add logging"
        )
        assert "Add logging" in query
        assert "Add system import" in query

    def test_includes_file_paths(self):
        files = DiffParser.parse_diff(SIMPLE_DIFF)
        query = DiffParser.build_rag_query_from_diff(files)
        assert "src/main.py" in query

    def test_truncation(self):
        files = DiffParser.parse_diff(SIMPLE_DIFF)
        query = DiffParser.build_rag_query_from_diff(files, max_query_length=30)
        assert len(query) <= 34  # 30 + "..."

    def test_empty_files(self):
        query = DiffParser.build_rag_query_from_diff([])
        assert query == ""

    def test_no_title_no_description(self):
        files = DiffParser.parse_diff(SIMPLE_DIFF)
        query = DiffParser.build_rag_query_from_diff(files)
        assert len(query) > 0  # Should still have file paths
