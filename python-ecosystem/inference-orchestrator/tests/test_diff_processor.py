"""
Unit tests for utils.diff_processor — DiffProcessor, DiffChangeType,
DiffFile, ProcessedDiff, summarize_oversized_diff, process_raw_diff, format_diff_for_prompt.
"""
import pytest
from utils.diff_processor import (
    DiffProcessor,
    DiffChangeType,
    DiffFile,
    ProcessedDiff,
    summarize_oversized_diff,
    process_raw_diff,
    format_diff_for_prompt,
)

# ── Sample diffs ─────────────────────────────────────────────────

SIMPLE_RAW_DIFF = """\
diff --git a/src/service/OrderService.java b/src/service/OrderService.java
--- a/src/service/OrderService.java
+++ b/src/service/OrderService.java
@@ -10,6 +10,8 @@ public class OrderService {
     private OrderRepository repo;
 
+    public Order createOrder(CreateOrderRequest request) {
+        return repo.save(request.toOrder());
+    }
+
     public Order getOrder(Long id) {
         return repo.findById(id);
"""

MULTI_FILE_DIFF = """\
diff --git a/src/app.py b/src/app.py
new file mode 100644
--- /dev/null
+++ b/src/app.py
@@ -0,0 +1,3 @@
+from flask import Flask
+app = Flask(__name__)
+def create_app(): return app
diff --git a/package-lock.json b/package-lock.json
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,5 +1,5 @@
-"version": "1.0.0"
+"version": "1.0.1"
diff --git a/tests/test_app.py b/tests/test_app.py
--- a/tests/test_app.py
+++ b/tests/test_app.py
@@ -1,3 +1,5 @@
+import pytest
+def test_create(): pass
"""


# ── DiffChangeType ───────────────────────────────────────────────

class TestDiffChangeType:

    def test_all_values(self):
        values = {e.value for e in DiffChangeType}
        assert "added" in values
        assert "modified" in values
        assert "deleted" in values
        assert "renamed" in values
        assert "binary" in values


# ── DiffFile ─────────────────────────────────────────────────────

class TestDiffFile:

    def test_total_changes(self):
        f = DiffFile(path="a.py", change_type=DiffChangeType.MODIFIED, additions=5, deletions=3)
        assert f.total_changes == 8

    def test_size_bytes(self):
        f = DiffFile(path="a.py", change_type=DiffChangeType.MODIFIED, content="hello")
        assert f.size_bytes == 5

    def test_defaults(self):
        f = DiffFile(path="a.py", change_type=DiffChangeType.ADDED)
        assert f.additions == 0
        assert f.deletions == 0
        assert f.is_binary is False
        assert f.is_skipped is False


# ── ProcessedDiff ────────────────────────────────────────────────

class TestProcessedDiff:

    def test_included_files(self):
        f1 = DiffFile(path="a.py", change_type=DiffChangeType.MODIFIED, is_skipped=False)
        f2 = DiffFile(path="b.py", change_type=DiffChangeType.MODIFIED, is_skipped=True)
        pd = ProcessedDiff(files=[f1, f2])
        assert len(pd.get_included_files()) == 1
        assert len(pd.get_skipped_files()) == 1

    def test_to_unified_diff(self):
        f1 = DiffFile(path="a.py", change_type=DiffChangeType.MODIFIED, content="diff a")
        pd = ProcessedDiff(files=[f1])
        assert "diff a" in pd.to_unified_diff()


# ── DiffProcessor ────────────────────────────────────────────────

class TestDiffProcessorProcess:

    def test_simple_diff(self):
        proc = DiffProcessor()
        result = proc.process(SIMPLE_RAW_DIFF)
        assert result.total_files >= 1
        assert result.original_size_bytes > 0

    def test_empty_diff(self):
        result = DiffProcessor().process("")
        assert result.files == []

    def test_none_diff(self):
        result = DiffProcessor().process(None)
        assert result.files == []


class TestDiffProcessorShouldSkip:

    def test_lock_file_not_skipped_by_path(self):
        proc = DiffProcessor()
        result = proc.process(MULTI_FILE_DIFF)
        included = {f.path for f in result.get_included_files()}
        assert "package-lock.json" in included

    def test_source_not_skipped(self):
        proc = DiffProcessor()
        result = proc.process(MULTI_FILE_DIFF)
        included = {f.path for f in result.get_included_files()}
        assert "src/app.py" in included


class TestDiffProcessorOrdering:

    def test_preserves_original_diff_order_after_skips(self):
        raw_diff = """\
diff --git a/tests/test_app.py b/tests/test_app.py
--- a/tests/test_app.py
+++ b/tests/test_app.py
@@ -1 +1,2 @@
+def test_create(): pass
diff --git a/package-lock.json b/package-lock.json
--- a/package-lock.json
+++ b/package-lock.json
@@ -1 +1 @@
-"version": "1.0.0"
+"version": "1.0.1"
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1,2 @@
+def create_app(): pass
"""
        result = DiffProcessor().process(raw_diff)
        included = result.get_included_files()
        assert [f.path for f in included] == [
            "tests/test_app.py",
            "package-lock.json",
            "src/app.py",
        ]


class TestDiffProcessorApplyLimits:

    def test_max_files_limit(self):
        proc = DiffProcessor(max_files=1)
        result = proc.process(MULTI_FILE_DIFF)
        included = result.get_included_files()
        assert len(included) <= 1
        assert result.truncated is True


class TestDiffProcessorRefactoringSignals:

    def test_detects_rename(self):
        rename_diff = """\
diff --git a/old.py b/new.py
rename from old.py
rename to new.py
--- a/old.py
+++ b/new.py
@@ -1 +1 @@
-old = 1
+new = 1
"""
        proc = DiffProcessor()
        result = proc.process(rename_diff)
        signals = result.refactoring_signals
        assert any("rename" in s.lower() or "move" in s.lower() for s in signals)


# ── summarize_oversized_diff ─────────────────────────────────────

class TestSummarizeOversizedDiff:

    def test_includes_stats(self):
        diff_content = """\
@@ -1,10 +1,15 @@ class Foo {
+    public void newMethod() {
+        // line 1
+        // line 2
-    public void oldMethod() {
-        // old
"""
        summary = summarize_oversized_diff(diff_content, "Foo.java")
        assert "lines added" in summary
        assert "lines removed" in summary

    def test_includes_header(self):
        summary = summarize_oversized_diff("+x = 1\n-y = 2", "test.py")
        assert "test.py" in summary
        assert "CodeCrow Summary" in summary

    def test_empty_diff(self):
        summary = summarize_oversized_diff("", "empty.py")
        assert "empty.py" in summary


# ── process_raw_diff (convenience) ───────────────────────────────

class TestProcessRawDiff:

    def test_none_returns_empty(self):
        result = process_raw_diff(None)
        assert result.files == []

    def test_empty_returns_empty(self):
        result = process_raw_diff("")
        assert result.files == []

    def test_valid(self):
        result = process_raw_diff(SIMPLE_RAW_DIFF)
        assert result.total_files >= 1


# ── format_diff_for_prompt ───────────────────────────────────────

class TestFormatDiffForPrompt:

    def test_includes_stats(self):
        result = process_raw_diff(SIMPLE_RAW_DIFF)
        output = format_diff_for_prompt(result, include_stats=True)
        assert "DIFF STATISTICS" in output
        assert "Additions:" in output

    def test_without_stats(self):
        result = process_raw_diff(SIMPLE_RAW_DIFF)
        output = format_diff_for_prompt(result, include_stats=False)
        assert "DIFF STATISTICS" not in output

    def test_max_chars(self):
        result = process_raw_diff(SIMPLE_RAW_DIFF)
        output = format_diff_for_prompt(result, include_stats=False, max_chars=50)
        # Should still contain some content
        assert len(output) > 0

    def test_empty_diff(self):
        result = process_raw_diff("")
        output = format_diff_for_prompt(result)
        assert "Files changed: 0" in output
