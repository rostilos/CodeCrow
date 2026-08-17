from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from codecrow_plugins import (
    CandidateClaim,
    Capability,
    DetectionRules,
    OutcomeStatus,
    PluginDescriptor,
    PluginDiagnostic,
    PluginKind,
    PluginOutcome,
    PluginCatalog,
    PluginRegistry,
    ProjectCapabilities,
    RepositoryFacts,
    load_descriptors,
)
from codecrow_plugins.selection import ProjectSelector
from codecrow_plugins.manifest import descriptor_from_mapping


CONTRACTS_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = CONTRACTS_ROOT / "fixtures" / "plugins-valid.json"
PYTHON_ROOT = Path(__file__).resolve().parents[1]


def descriptor(plugin_id: str, kind: PluginKind, requires=()) -> PluginDescriptor:
    return PluginDescriptor(
        id=plugin_id,
        kind=kind,
        requires=tuple(requires),
        capabilities=(Capability.SYNTAX,),
        detection=DetectionRules(),
    )


def test_shared_fixture_resolves_dependencies_before_framework():
    registry = PluginRegistry(load_descriptors(FIXTURE))

    assert registry.ordered_ids == ("php", "magento")
    assert [item.id for item in registry.resolve(["magento"])] == ["php", "magento"]
    assert registry.fingerprint.startswith("sha256:")
    assert len(registry.fingerprint) == 71
    assert registry.fingerprint_for(["php"]) == (
        "sha256:def383c4fbe4b426bdd55e0bdac4a067ee0789603ba60bab530f8bfa61b27f51"
    )
    assert registry.fingerprint_for(["magento"]) == registry.fingerprint


def test_registration_permutations_have_identical_order_and_fingerprint():
    descriptors = load_descriptors(FIXTURE)
    results = {
        (PluginRegistry(items).ordered_ids, PluginRegistry(items).fingerprint)
        for items in itertools.permutations(descriptors)
    }
    assert len(results) == 1


def test_implementation_fingerprint_tracks_selected_runtime_only(tmp_path):
    contract = tmp_path / "contracts/python/codecrow_plugins/runtime.py"
    contract.parent.mkdir(parents=True)
    contract.write_text("CONTRACT = 1\n", encoding="utf-8")
    for plugin_id in ("alpha", "beta"):
        descriptor = tmp_path / f"languages/{plugin_id}/plugin.json"
        descriptor.parent.mkdir(parents=True)
        descriptor.write_text(json.dumps({
            "id": plugin_id,
            "kind": "language",
            "requires": [],
            "capabilities": ["syntax"],
            "detection": {
                "extensions": [f".{plugin_id}"],
                "filesAll": [],
                "filesAny": [],
                "contentMarkers": [],
                "alternatives": [],
            },
            "entrypoints": {},
        }), encoding="utf-8")
        resource = descriptor.parent / "python/resources/rag-chunks.scm"
        resource.parent.mkdir(parents=True)
        resource.write_text(f"({plugin_id})\n", encoding="utf-8")

    catalog = PluginCatalog.discover(tmp_path)
    alpha_before = catalog.implementation_fingerprint(["alpha"])
    beta_resource = tmp_path / "languages/beta/python/resources/rag-chunks.scm"
    beta_resource.write_text("(beta changed)\n", encoding="utf-8")
    catalog = PluginCatalog.discover(tmp_path)
    assert catalog.implementation_fingerprint(["alpha"]) == alpha_before

    alpha_resource = tmp_path / "languages/alpha/python/resources/rag-chunks.scm"
    alpha_resource.write_text("(alpha changed)\n", encoding="utf-8")
    catalog = PluginCatalog.discover(tmp_path)
    alpha_after_plugin_change = catalog.implementation_fingerprint(["alpha"])
    assert alpha_after_plugin_change != alpha_before

    contract.write_text("CONTRACT = 2\n", encoding="utf-8")
    catalog = PluginCatalog.discover(tmp_path)
    assert (
        catalog.implementation_fingerprint(["alpha"])
        != alpha_after_plugin_change
    )


@pytest.mark.parametrize("seed", ["1", "7", "123"])
def test_hash_seed_does_not_change_registry_output(seed):
    code = (
        "from codecrow_plugins import load_descriptors,PluginRegistry;"
        "import os;"
        "r=PluginRegistry(load_descriptors(os.environ['PLUGIN_FIXTURE']));"
        "print(','.join(r.ordered_ids)+'|'+r.fingerprint)"
    )
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
    env["PYTHONPATH"] = str(PYTHON_ROOT)
    env["PLUGIN_FIXTURE"] = str(FIXTURE)
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.stdout == (
        "php,magento|sha256:b3a63c5e893ce3efa3b0fb35a8d2b4862f48c58fb7d366c0253d55262dbab676\n"
    )


def test_duplicate_missing_and_cyclic_dependencies_are_rejected():
    php = descriptor("php", PluginKind.LANGUAGE)
    with pytest.raises(ValueError, match="duplicate plugin id"):
        PluginRegistry([php, php])
    with pytest.raises(ValueError, match="missing plugins"):
        PluginRegistry([descriptor("magento", PluginKind.FRAMEWORK, ("php",))])
    with pytest.raises(ValueError, match="dependency cycle"):
        PluginRegistry([
            descriptor("alpha", PluginKind.LANGUAGE, ("beta",)),
            descriptor("beta", PluginKind.LANGUAGE, ("alpha",)),
        ])


def test_framework_requires_a_language_plugin_directly_or_transitively():
    with pytest.raises(ValueError, match="must depend on a language"):
        PluginRegistry([descriptor("framework", PluginKind.FRAMEWORK)])


def test_domain_plugin_is_language_neutral():
    registry = PluginRegistry([
        descriptor("contracts", PluginKind.DOMAIN),
    ])

    assert registry.ordered_ids == ("contracts",)


def test_descriptor_rejects_unsorted_values_and_application_release_fields():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))[1]
    raw["capabilities"] = ["syntax", "context"]
    with pytest.raises(ValueError, match="unique and sorted"):
        descriptor_from_mapping(raw)

    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))[1]
    raw["version"] = "1"
    with pytest.raises(ValueError, match="unknown=.*version"):
        descriptor_from_mapping(raw)


def test_capability_lookup_uses_dependency_order():
    registry = PluginRegistry(load_descriptors(FIXTURE))
    assert [item.id for item in registry.for_capability(Capability.GRAPH, ["magento"])] == [
        "php",
        "magento",
    ]


def test_plugin_outcome_states_are_unambiguous():
    assert PluginOutcome.handled("value").status is OutcomeStatus.HANDLED
    assert PluginOutcome.abstained().status is OutcomeStatus.ABSTAINED
    failure = PluginOutcome.failed(PluginDiagnostic("parse-failed", "invalid source", "php"))
    assert failure.status is OutcomeStatus.FAILED
    with pytest.raises(ValueError, match="handled outcome"):
        PluginOutcome(status=OutcomeStatus.HANDLED)
    with pytest.raises(ValueError, match="failed outcome"):
        PluginOutcome(status=OutcomeStatus.FAILED)


def test_repository_and_project_facts_are_immutable_and_canonical():
    facts = RepositoryFacts(
        revision="abc1234",
        paths=("app/code/A.php", "composer.json"),
        marker_contents={"composer.json": "{}"},
    )
    assert list(facts.marker_contents) == ["composer.json"]
    with pytest.raises(TypeError):
        facts.marker_contents["other"] = "x"

    selection = ProjectCapabilities(
        repository_plugins=("php", "magento"),
        file_plugins={"app/code/A.php": ("php",)},
        detection_evidence={"magento": ("composer.json",)},
        fingerprint="sha256:" + "0" * 64,
    )
    assert selection.file_plugins["app/code/A.php"] == ("php",)


def test_unknown_requested_plugin_is_rejected():
    registry = PluginRegistry(load_descriptors(FIXTURE))
    with pytest.raises(ValueError, match="unknown requested plugin"):
        registry.resolve(["missing"])


def test_candidate_claim_kind_is_optional_but_must_be_normalized():
    base = {
        "category": "bug-risk",
        "path": "src/App.java",
        "line": 1,
        "message": "The dependency resolves to the wrong implementation.",
    }

    assert CandidateClaim(**base).claim_kind == ""
    assert CandidateClaim(**base, claim_kind="java-file").claim_kind == "java-file"
    with pytest.raises(ValueError, match="already be normalized"):
        CandidateClaim(**base, claim_kind=" java-file ")
    with pytest.raises(ValueError, match="must be text"):
        CandidateClaim(**base, claim_kind=123)


def test_project_selection_matches_the_shared_cross_runtime_projection():
    registry = PluginRegistry(load_descriptors(FIXTURE))
    facts = RepositoryFacts(
        revision="abc1234",
        paths=(
            "app/code/Vendor/Module/Model/Foo.php",
            "app/etc/config.php",
            "bin/magento",
            "composer.json",
        ),
        marker_contents={"composer.json": '{"require":{"magento/framework":"*"}}'},
    )

    selected = ProjectSelector(registry).select(facts)

    assert selected.repository_plugins == ("php", "magento")
    assert selected.file_plugins["app/code/Vendor/Module/Model/Foo.php"] == ("php",)
    assert selected.detection_evidence["magento"] == (
        "file:app/etc/config.php",
        "file:bin/magento",
        "file:composer.json",
        "root:.",
    )
    assert selected.fingerprint == (
        "sha256:82da50c6916ad2b50e268523e6226aeaee6f9bb8e76fd868aed5419503946eaf"
    )
    assert ProjectSelector(registry).validate(selected, "abc1234") == selected


def test_cross_runtime_capability_validation_rejects_stale_or_invalid_projection():
    registry = PluginRegistry(load_descriptors(FIXTURE))
    selector = ProjectSelector(registry)
    selected = selector.select(RepositoryFacts(
        revision="abc1234",
        paths=(
            "app/code/Vendor/Module/Model/Foo.php",
            "app/etc/config.php",
            "bin/magento",
            "composer.json",
        ),
        marker_contents={
            "composer.json": '{"require":{"magento/framework":"*"}}',
        },
    ))

    with pytest.raises(ValueError, match="descriptor fingerprint"):
        selector.validate(
            replace(
                selected,
                descriptor_fingerprint="sha256:" + "0" * 64,
            ),
            "abc1234",
        )
    with pytest.raises(ValueError, match="immutable revision"):
        selector.validate(selected, "different-revision")
    with pytest.raises(ValueError, match="not language plugins"):
        selector.validate(
            replace(
                selected,
                file_plugins={"composer.json": ("magento",)},
                fingerprint="sha256:" + "0" * 64,
            ),
            "abc1234",
        )
    with pytest.raises(ValueError, match="unselected plugins"):
        selector.validate(
            replace(
                selected,
                detection_evidence={"other": ()},
                fingerprint="sha256:" + "0" * 64,
            ),
            "abc1234",
        )


def test_projected_capabilities_bind_combined_revision_evidence():
    registry = PluginRegistry(load_descriptors(FIXTURE))
    selector = ProjectSelector(registry)

    projected = selector.project(
        revision="head1234",
        repository_plugins=("php", "magento"),
        file_plugins={"app/code/Vendor/Module/Foo.php": ("php",)},
        detection_evidence={
            "php": ("extension:app/code/Vendor/Module/Foo.php",),
            "magento": (
                "indexed-target:main:sha256:target:magento",
            ),
        },
    )

    assert selector.validate(projected, "head1234") == projected
    assert projected.repository_plugins == ("php", "magento")
    with pytest.raises(ValueError, match="missing detection evidence"):
        selector.project(
            revision="head1234",
            repository_plugins=("php", "magento"),
            file_plugins={"app/code/Vendor/Module/Foo.php": ("php",)},
            detection_evidence={
                "php": ("extension:app/code/Vendor/Module/Foo.php",),
            },
        )
