"""
Unit tests for utils.file_classifier compatibility behavior.
"""
from utils.file_classifier import FileClassifier, FilePriority, ClassifiedFile


class TestFilePriority:
    def test_values(self):
        assert FilePriority.HIGH == "HIGH"
        assert FilePriority.MEDIUM == "MEDIUM"
        assert FilePriority.LOW == "LOW"
        assert FilePriority.SKIP == "SKIP"


class TestClassifiedFile:
    def test_creation(self):
        item = ClassifiedFile(path="a.py", priority=FilePriority.MEDIUM, category="reviewable")
        assert item.path == "a.py"
        assert item.estimated_importance == 1.0


class TestNeutralClassification:
    def test_all_paths_are_neutral_reviewable(self):
        paths = [
            "src/service/OrderService.java",
            "tests/test_order.py",
            "config/settings.yaml",
            "docs/README.md",
            "project/dist/bundle.js",
            "assets/logo.png",
        ]

        for path in paths:
            result = FileClassifier._classify_single_file(path)
            assert result.priority == FilePriority.MEDIUM
            assert result.category == "reviewable"
            assert result.estimated_importance == 1.0

    def test_deleted_change_only_affects_neutral_importance(self):
        result = FileClassifier._classify_single_file("anything.ext", change_type="deleted")
        assert result.priority == FilePriority.MEDIUM
        assert result.estimated_importance == 0.0

    def test_classify_files_preserves_all_inputs_as_medium(self):
        paths = ["a.py", "README.md", "bundle.min.js"]
        classified = FileClassifier.classify_files(paths)
        assert [item.path for item in classified[FilePriority.MEDIUM]] == paths
        assert classified[FilePriority.HIGH] == []
        assert classified[FilePriority.LOW] == []
        assert classified[FilePriority.SKIP] == []


class TestStatsAndCoreFiles:
    def test_stats(self):
        classified = FileClassifier.classify_files(["a.py", "b.py"])
        stats = FileClassifier.get_priority_stats(classified)
        assert stats == {
            "high": 0,
            "medium": 2,
            "low": 0,
            "skipped": 0,
            "total": 2,
        }

    def test_detect_core_files_preserves_input_order(self):
        paths = ["first.py", "second.py", "third.py"]
        assert FileClassifier.detect_core_files(paths, max_core=2) == ["first.py", "second.py"]
