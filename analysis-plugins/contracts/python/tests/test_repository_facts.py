from pathlib import Path

import pytest

from codecrow_plugins import (
    ProjectSelector,
    RepositoryFacts,
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
    root = "magento/src/etc"
    _write(
        tmp_path,
        f"{root}/composer.json",
        '{"require":{"magento/framework":"*"}}',
    )
    _write(tmp_path, f"{root}/etc/module.xml", "<config/>\n")
    _write(tmp_path, f"{root}/registration.php", "<?php\n")

    updated = overlay_repository_facts(
        baseline,
        tmp_path,
        "changed",
        (
            f"{root}/composer.json",
            f"{root}/etc/module.xml",
            f"{root}/registration.php",
        ),
        ("README.md",),
        catalog.registry,
    )

    assert updated.paths == (
        f"{root}/composer.json",
        f"{root}/etc/module.xml",
        f"{root}/registration.php",
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


def test_many_nested_composer_files_are_not_treated_as_content_evidence(tmp_path):
    catalog = discover_builtin_plugins()
    selector = ProjectSelector(catalog.registry)
    paths = ["app/etc/config.php", "bin/magento", "composer.json"]
    _write(tmp_path, "app/etc/config.php", "<?php return [];\n")
    _write(tmp_path, "bin/magento", "#!/usr/bin/env php\n")
    _write(tmp_path, "composer.json", '{"require":{"magento/framework":"*"}}')
    for index in range(32):
        path = f"app/code/Acme/Module{index}/composer.json"
        paths.append(path)
        _write(tmp_path, path, f'{{"name":"acme/module-{index}"}}')

    facts = build_repository_facts(
        tmp_path,
        "base",
        paths,
        catalog.registry,
    )

    assert facts.marker_contents == {}
    assert "magento" in selector.select(facts).repository_plugins


def test_incremental_overlay_prunes_legacy_non_matching_marker_contents(tmp_path):
    catalog = discover_builtin_plugins()
    paths = tuple(sorted(
        f"app/code/Acme/Module{index}/composer.json"
        for index in range(32)
    ))
    baseline = RepositoryFacts(
        revision="base",
        paths=paths,
        marker_contents={
            path: f'{{"name":"acme/module-{index}"}}'
            for index, path in enumerate(paths)
        },
    )

    updated = overlay_repository_facts(
        baseline,
        None,
        "changed",
        (),
        (),
        catalog.registry,
    )

    assert updated.marker_contents == {}


def test_marker_byte_budget_degrades_detection_without_failing_index(tmp_path, caplog):
    catalog = discover_builtin_plugins()
    selector = ProjectSelector(catalog.registry)
    paths = ("app/etc/config.php", "bin/magento", "composer.json")
    _write(tmp_path, "app/etc/config.php", "<?php return [];\n")
    _write(tmp_path, "bin/magento", "#!/usr/bin/env php\n")
    _write(
        tmp_path,
        "composer.json",
        '{"require":{"magento/framework":"*",'
        '"hyva-themes/magento2-theme-module":"*"}}',
    )

    facts = build_repository_facts(
        tmp_path,
        "base",
        paths,
        catalog.registry,
        max_marker_bytes=8,
    )

    assert facts.marker_contents == {}
    assert "magento" in selector.select(facts).repository_plugins
    assert "hyva" not in selector.select(facts).repository_plugins
    assert "reduced automatic plugin-detection evidence" in caplog.text


def test_automatic_marker_reads_stay_within_configured_source_root(tmp_path):
    catalog = discover_builtin_plugins()
    paths = (
        "composer.json",
        "shop/app/etc/config.php",
        "shop/bin/magento",
        "shop/composer.json",
    )
    _write(
        tmp_path,
        "composer.json",
        '{"require":{"hyva-themes/magento2-theme-module":"*"}}',
    )
    _write(tmp_path, "shop/app/etc/config.php", "<?php return [];\n")
    _write(tmp_path, "shop/bin/magento", "#!/usr/bin/env php\n")
    _write(tmp_path, "shop/composer.json", '{"require":{"magento/framework":"*"}}')

    facts = build_repository_facts(
        tmp_path,
        "base",
        paths,
        catalog.registry,
        source_root="shop",
    )

    assert facts.marker_contents == {}
    assert facts.source_root == "shop"


def test_manual_project_profile_skips_markers_and_survives_overlay(
    tmp_path,
    monkeypatch,
):
    catalog = discover_builtin_plugins()
    root = "magento/src/etc"
    paths = (
        f"{root}/app/etc/config.php",
        f"{root}/app/code/Acme/Checkout/etc/module.xml",
        f"{root}/app/code/Acme/Checkout/Model/Cart.php",
    )
    for path in paths:
        _write(tmp_path, path, "marker content must not be read\n")

    baseline = build_repository_facts(
        tmp_path,
        "base",
        paths,
        catalog.registry,
        project_type="magento",
        source_root=root,
    )

    assert baseline.project_type == "magento"
    assert baseline.source_root == root
    assert baseline.marker_contents == {}

    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("manual overlay must not read marker contents")
        ),
    )
    updated = overlay_repository_facts(
        baseline,
        None,
        "changed",
        (f"{root}/app/etc/config.php",),
        (),
        catalog.registry,
    )

    assert updated.project_type == "magento"
    assert updated.source_root == root
    assert updated.marker_contents == {}
