"""
Unit tests for utils.file_classifier — FileClassifier, FilePriority, ClassifiedFile.
"""
import pytest
from utils.file_classifier import FileClassifier, FilePriority, ClassifiedFile


class TestFilePriority:

    def test_values(self):
        assert FilePriority.HIGH == "HIGH"
        assert FilePriority.MEDIUM == "MEDIUM"
        assert FilePriority.LOW == "LOW"
        assert FilePriority.SKIP == "SKIP"


class TestClassifySingleFile:

    # HIGH priority
    @pytest.mark.parametrize("path", [
        "src/service/OrderService.java",
        "app/controller/UserController.py",
        "src/handler/EventHandler.ts",
        "src/api/routes.py",
        "src/core/engine.rs",
        "src/domain/model.go",
        "app/Auth/LoginController.php",
        "src/SecurityManager.java",
        "src/PermissionCheck.py",
        "src/repository/UserRepository.java",
        "src/dao/OrderDao.java",
        "db/migration/V001.sql",
    ])
    def test_high_priority(self, path):
        result = FileClassifier._classify_single_file(path)
        assert result.priority == FilePriority.HIGH, f"{path} should be HIGH"

    # HIGH by keyword
    @pytest.mark.parametrize("path", [
        "src/auth_service.py",
        "lib/payment_processor.ts",
        "src/user_manager.go",
        "src/token_validator.java",
    ])
    def test_high_by_keyword(self, path):
        result = FileClassifier._classify_single_file(path)
        assert result.priority == FilePriority.HIGH, f"{path} should be HIGH (keyword)"

    # MEDIUM priority
    @pytest.mark.parametrize("path", [
        "src/model/Order.java",
        "src/entity/Item.py",
        "src/dto/Response.ts",
        "src/utils/helpers.py",
        "src/common/constants.java",
        "src/components/Button.tsx",
        "src/hooks/useForm.ts",
        "src/client/HttpClient.py",
    ])
    def test_medium_priority(self, path):
        result = FileClassifier._classify_single_file(path)
        assert result.priority == FilePriority.MEDIUM, f"{path} should be MEDIUM"

    # LOW priority
    @pytest.mark.parametrize("path", [
        "tests/test_order.py",
        "src/OrderServiceTest.java",
        "config/settings.yaml",
        "docs/README.md",
        "app.config.json",
    ])
    def test_low_priority(self, path):
        result = FileClassifier._classify_single_file(path)
        assert result.priority == FilePriority.LOW, f"{path} should be LOW"

    # SKIP
    @pytest.mark.parametrize("path", [
        "project/dist/bundle.js",
        "project/build/output.o",
        "project/node_modules/lodash/index.js",
        "project/__pycache__/cache.pyc",
        "src/main.min.js",
        "src/app.js.map",
        "src/types.d.ts",
        "project/.idea/workspace.xml",
        "project/.vscode/settings.json",
        "assets/logo.png",
        "fonts/arial.woff",
    ])
    def test_skip_priority(self, path):
        result = FileClassifier._classify_single_file(path)
        assert result.priority == FilePriority.SKIP, f"{path} should be SKIP"


class TestClassifyFiles:

    def test_classify_mixed(self):
        paths = [
            "src/service/Order.java",
            "tests/test_order.py",
            "project/dist/bundle.js",
            "src/model/Order.java",
        ]
        classified = FileClassifier.classify_files(paths)
        assert len(classified[FilePriority.HIGH]) >= 1
        assert len(classified[FilePriority.SKIP]) >= 1

    def test_empty_list(self):
        classified = FileClassifier.classify_files([])
        assert all(len(v) == 0 for v in classified.values())

    def test_sorted_by_importance(self):
        paths = [
            "src/service/B.java",
            "src/service/auth_service.java",  # has security keyword → higher importance
        ]
        classified = FileClassifier.classify_files(paths)
        high = classified[FilePriority.HIGH]
        if len(high) >= 2:
            assert high[0].estimated_importance >= high[1].estimated_importance


class TestCalculateImportance:

    def test_new_file_boost(self):
        base = FileClassifier._calculate_importance("src/main.py")
        added = FileClassifier._calculate_importance("src/main.py", change_type="added")
        assert added > base

    def test_security_keyword_boost(self):
        base = FileClassifier._calculate_importance("src/utils.py")
        sec = FileClassifier._calculate_importance("src/auth_service.py")
        assert sec > base

    def test_capped_at_one(self):
        score = FileClassifier._calculate_importance("src/auth_service.py", change_type="added")
        assert score <= 1.0


class TestGetCategory:

    @pytest.mark.parametrize("path,expected", [
        ("src/service/Foo.java", "service"),
        ("src/controller/Bar.py", "controller"),
        ("src/repository/Baz.java", "repository"),
        ("src/model/User.java", "model"),
        ("src/util/helpers.py", "utility"),
        ("config/app.yml", "config"),
        ("tests/test_foo.py", "test"),
        ("src/components/Button.tsx", "component"),
        ("src/api/routes.py", "api"),
        ("docs/README.md", "documentation"),
        ("random.py", "other"),
    ])
    def test_category(self, path, expected):
        assert FileClassifier._get_category(path) == expected


class TestGetPriorityStats:

    def test_stats(self):
        classified = FileClassifier.classify_files([
            "src/service/A.java",
            "dist/b.js",
            "tests/test_c.py",
        ])
        stats = FileClassifier.get_priority_stats(classified)
        assert stats["total"] == 3
        assert "high" in stats


class TestDetectCoreFiles:

    def test_returns_high_first(self):
        paths = [
            "src/service/OrderService.java",
            "tests/test_order.py",
            "src/model/User.java",
        ]
        core = FileClassifier.detect_core_files(paths, max_core=2)
        assert len(core) <= 2
        # Should prefer service over test
        assert "src/service/OrderService.java" in core

    def test_max_core(self):
        paths = [f"src/service/S{i}.java" for i in range(10)]
        core = FileClassifier.detect_core_files(paths, max_core=3)
        assert len(core) <= 3
