from pathlib import Path

import pytest

from codecrow_plugins import (
    ProjectSelector,
    build_repository_facts,
    overlay_repository_facts,
)
from codecrow_plugins.bootstrap import discover_builtin_plugins


def _write(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_overlay_recomputes_path_and_content_detection_from_complete_inventory(
    tmp_path,
):
    catalog = discover_builtin_plugins()
    selector = ProjectSelector(catalog.registry)
    _write(tmp_path, "app/main.py", "from fastapi import FastAPI\n")
    _write(tmp_path, "requirements.txt", "fastapi\n")
    baseline = build_repository_facts(
        tmp_path,
        "base",
        ("app/main.py", "requirements.txt"),
        catalog.registry,
    )
    assert "fastapi" in selector.select(baseline).repository_plugins

    _write(tmp_path, "app/main.py", "print('plain python')\n")
    _write(tmp_path, "requirements.txt", "pytest\n")
    updated = overlay_repository_facts(
        baseline,
        tmp_path,
        "changed",
        ("app/main.py", "requirements.txt"),
        (),
        catalog.registry,
    )

    assert updated.revision == "changed"
    assert updated.paths == ("app/main.py", "requirements.txt")
    assert "fastapi" not in selector.select(updated).repository_plugins
    assert "python" in selector.select(updated).repository_plugins


def test_overlay_adds_exact_framework_marker_and_removes_deleted_paths(tmp_path):
    catalog = discover_builtin_plugins()
    selector = ProjectSelector(catalog.registry)
    _write(tmp_path, "README.md", "project\n")
    baseline = build_repository_facts(
        tmp_path,
        "base",
        ("README.md",),
        catalog.registry,
    )
    _write(
        tmp_path,
        "composer.json",
        '{"require":{"magento/framework":"*"}}',
    )
    _write(tmp_path, "etc/module.xml", "<config/>\n")
    _write(tmp_path, "registration.php", "<?php\n")

    updated = overlay_repository_facts(
        baseline,
        tmp_path,
        "changed",
        ("composer.json", "etc/module.xml", "registration.php"),
        ("README.md",),
        catalog.registry,
    )

    assert updated.paths == (
        "composer.json",
        "etc/module.xml",
        "registration.php",
    )
    assert "magento" in selector.select(updated).repository_plugins


def test_delete_only_overlay_needs_no_changed_file_checkout(tmp_path):
    catalog = discover_builtin_plugins()
    _write(tmp_path, "src/App.java", "final class App {}\n")
    baseline = build_repository_facts(
        tmp_path,
        "base",
        ("src/App.java",),
        catalog.registry,
    )

    updated = overlay_repository_facts(
        baseline,
        None,
        "deleted",
        (),
        ("src/App.java",),
        catalog.registry,
    )

    assert updated.paths == ()


def test_overlay_rejects_overlapping_updated_and_deleted_paths(tmp_path):
    catalog = discover_builtin_plugins()
    baseline = build_repository_facts(
        tmp_path,
        "base",
        (),
        catalog.registry,
    )

    with pytest.raises(ValueError, match="same path"):
        overlay_repository_facts(
            baseline,
            tmp_path,
            "changed",
            ("same.py",),
            ("same.py",),
            catalog.registry,
        )
