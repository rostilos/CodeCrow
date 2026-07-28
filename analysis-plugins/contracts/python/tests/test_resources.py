from pathlib import Path

import pytest

from codecrow_plugins import PluginResources


PLUGINS_ROOT = Path(__file__).resolve().parents[3]


def test_discovers_plugin_owned_rag_query():
    resources = PluginResources.discover(PLUGINS_ROOT)

    query = resources.path("php", "python/resources/rag-chunks.scm")

    assert query is not None
    assert query.read_text(encoding="utf-8").startswith("; PHP tree-sitter queries")


def test_empty_plugin_root_has_no_resources(tmp_path: Path):
    resources = PluginResources.discover(tmp_path)

    assert resources.roots == {}
    assert resources.path("php", "python/resources/rag-chunks.scm") is None


@pytest.mark.parametrize("resource", ("../plugin.json", "/etc/passwd"))
def test_rejects_unsafe_resource_paths(resource: str):
    resources = PluginResources.discover(PLUGINS_ROOT)

    with pytest.raises(ValueError, match="safe and relative"):
        resources.path("php", resource)
