"""
Unit tests for utils.diff_processor — DiffProcessor, DiffChangeType,
DiffFile, ProcessedDiff, process_raw_diff, format_diff_for_prompt.
"""
import pytest
from utils.diff_processor import (
    DiffProcessor,
    DiffChangeType,
    HunkDisposition,
    DiffFile,
    ProcessedDiff,
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
        assert f.is_gitlink is False
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

    def test_git_submodule_pointer_is_excluded_from_source_review(self):
        raw_diff = """\
diff --git a/frontend b/frontend
index 0f3b28a..ea8d4ca 160000
--- a/frontend
+++ b/frontend
@@ -1 +1 @@
-Subproject commit 0f3b28ae5ab45d8563797a69ed1c64d491387b9a
+Subproject commit ea8d4ca7d4d024b8bdba327d30ae5fc382061d30
"""
        result = DiffProcessor().process(raw_diff)

        assert result.get_included_files() == []
        assert len(result.get_skipped_files()) == 1
        gitlink = result.get_skipped_files()[0]
        assert gitlink.path == "frontend"
        assert gitlink.is_gitlink is True
        assert gitlink.skip_reason == "Git submodule pointer"
        assert [hunk.disposition for hunk in gitlink.hunks] == [
            HunkDisposition.GITLINK
        ]

    def test_gitlink_to_regular_file_uses_new_mode(self):
        raw_diff = """\
diff --git a/component b/component
old mode 160000
new mode 100644
index 0f3b28a..c1f5880
--- a/component
+++ b/component
@@ -1 +1 @@
-Subproject commit 0f3b28ae5ab45d8563797a69ed1c64d491387b9a
+regular source
"""
        result = DiffProcessor().process(raw_diff)

        assert [item.path for item in result.get_included_files()] == [
            "component"
        ]
        assert result.files[0].is_gitlink is False
        assert result.files[0].hunks[0].disposition is HunkDisposition.REVIEWABLE

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

    def test_oversized_text_diff_is_preserved(self):
        raw_diff = """\
diff --git a/src/big.py b/src/big.py
--- a/src/big.py
+++ b/src/big.py
@@ -1 +1,4 @@
+aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
+bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
+cccccccccccccccccccccccccccccccccccccccccccccc
+dddddddddddddddddddddddddddddddddddddddddddddd
"""
        result = DiffProcessor().process(raw_diff)
        included = result.get_included_files()

        assert [f.path for f in included] == ["src/big.py"]
        assert result.get_skipped_files() == []
        assert result.total_files == 1
        assert result.skipped_files == 0
        assert included[0].skip_reason is None
        assert included[0].content == raw_diff.rstrip("\n")
        assert "+dddddddddddddddddddddddddddddddddddddddddddddd" in included[0].content

    def test_many_lines_text_diff_is_preserved(self):
        raw_diff = """\
diff --git a/src/noisy.py b/src/noisy.py
--- a/src/noisy.py
+++ b/src/noisy.py
@@ -1 +1,4 @@
+line_one()
+line_two()
+line_three()
"""
        result = DiffProcessor().process(raw_diff)
        included = result.get_included_files()

        assert [f.path for f in included] == ["src/noisy.py"]
        assert result.get_skipped_files() == []
        assert included[0].skip_reason is None
        assert "+line_one()" in included[0].content
        assert "+line_three()" in included[0].content


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

    def test_builds_stable_lossless_hunk_manifest(self):
        first = process_raw_diff(SIMPLE_RAW_DIFF).hunk_manifest()
        second = process_raw_diff(SIMPLE_RAW_DIFF).hunk_manifest()

        assert len(first) == 1
        assert first == second
        assert first[0].id.startswith("sha256:")
        assert first[0].path == "src/service/OrderService.java"
        assert first[0].new_start == 10
        assert first[0].disposition is HunkDisposition.REVIEWABLE
        assert "+    public Order createOrder" in first[0].content

    def test_keeps_all_hunks_for_large_file(self):
        raw = (
            "diff --git a/src/Large.php b/src/Large.php\n"
            "--- a/src/Large.php\n+++ b/src/Large.php\n"
            "@@ -1 +1 @@\n-old\n+new\n"
            "@@ -100 +100 @@\n-old2\n+" + "x" * 200 + "\n"
        )
        result = DiffProcessor().process(raw)

        assert len(result.hunk_manifest()) == 2
        assert all(hunk.disposition is HunkDisposition.REVIEWABLE for hunk in result.hunk_manifest())

    def test_metadata_only_mode_change_is_deterministically_skipped(self):
        raw = (
            "diff --git a/src/tool.sh b/src/tool.sh\n"
            "old mode 100644\n"
            "new mode 100755\n"
        )

        result = DiffProcessor().process(raw)

        assert len(result.files) == 1
        assert result.files[0].is_skipped is True
        assert result.files[0].skip_reason == "Metadata-only change"
        assert result.hunk_manifest() == []


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

    def test_complete_diff_is_preserved(self):
        result = process_raw_diff(SIMPLE_RAW_DIFF)
        output = format_diff_for_prompt(result, include_stats=False)
        assert SIMPLE_RAW_DIFF.rstrip("\n") in output

    def test_empty_diff(self):
        result = process_raw_diff("")
        output = format_diff_for_prompt(result)
        assert "Files changed: 0" in output
