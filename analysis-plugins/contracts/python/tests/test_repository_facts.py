from pathlib import Path

import pytest

from codecrow_plugins import (
    ProjectSelector,
    build_repository_facts,
)
from codecrow_plugins.bootstrap import discover_builtin_plugins


def _write(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_nested_framework_pattern_marker_is_acquired_relative_to_its_root(
    tmp_path,
):
    catalog = discover_builtin_plugins()
    selector = ProjectSelector(catalog.registry)
    root = "services/blog"
    paths = (
        f"{root}/blog.gemspec",
        f"{root}/config/routes.rb",
        f"{root}/lib/blog/engine.rb",
    )
    _write(tmp_path, f"{root}/blog.gemspec", "Gem::Specification.new\n")
    _write(tmp_path, f"{root}/config/routes.rb", "Blog::Engine.routes.draw do\nend\n")
    _write(
        tmp_path,
        f"{root}/lib/blog/engine.rb",
        "module Blog\n  class Engine < Rails::Engine\n  end\nend\n",
    )

    facts = build_repository_facts(
        tmp_path,
        "base",
        paths,
        catalog.registry,
    )

    assert facts.marker_contents == {
        f"{root}/lib/blog/engine.rb": (
            "module Blog\n  class Engine < Rails::Engine\n  end\nend\n"
        ),
    }
    selected = selector.select(facts)
    assert "rails" in selected.repository_plugins
    assert f"root:{root}" in selected.detection_evidence["rails"]


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


def test_pattern_marker_scan_budgets_non_matching_files_before_reading(
    tmp_path,
    caplog,
    monkeypatch,
):
    catalog = discover_builtin_plugins()
    paths = tuple(f"src/Type{index}.java" for index in range(3))
    for path in paths:
        _write(tmp_path, path, "final class Type {}\n")

    original_read_text = Path.read_text
    reads = []

    def counted_read_text(path, *args, **kwargs):
        reads.append(path)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read_text)
    facts = build_repository_facts(
        tmp_path,
        "base",
        paths,
        catalog.registry,
        max_marker_bytes=1_024,
        max_marker_files=1,
    )

    assert facts.marker_contents == {}
    assert len(reads) == 1
    assert "file inspection budget" in caplog.text


@pytest.mark.parametrize("unsafe_kind", ("outside-symlink", "invalid-utf8"))
def test_optional_marker_read_failure_degrades_without_failing_index(
    tmp_path,
    caplog,
    unsafe_kind,
):
    catalog = discover_builtin_plugins()
    selector = ProjectSelector(catalog.registry)
    marker = tmp_path / "package.json"
    if unsafe_kind == "outside-symlink":
        outside = tmp_path.parent / f"{tmp_path.name}-outside-package.json"
        outside.write_text('{"dependencies":{"express":"*"}}', encoding="utf-8")
        marker.symlink_to(outside)
    else:
        marker.write_bytes(b"\xff\xfe")
    _write(tmp_path, "src/app.js", "export const app = true;\n")

    facts = build_repository_facts(
        tmp_path,
        "base",
        ("package.json", "src/app.js"),
        catalog.registry,
    )

    assert facts.marker_contents == {}
    assert "express" not in selector.select(facts).repository_plugins
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
