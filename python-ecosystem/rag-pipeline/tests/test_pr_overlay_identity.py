from types import SimpleNamespace

from rag_pipeline.core.pr_overlay_identity import (
    ZERO_FINGERPRINT,
    is_complete_reusable_generation,
    pr_overlay_generation_fingerprint,
)


def _fingerprint(files, *, source="head", base="base", snapshots=()):
    return pr_overlay_generation_fingerprint(
        workspace="workspace",
        project="project",
        pr_number=42,
        branch="feature",
        base_branch="main",
        source_revision=source,
        base_revision=base,
        files=files,
        requested_plugin_ids=("php", "magento"),
        repository_plugin_ids=("php", "magento"),
        request_plugin_fingerprint="sha256:request",
        target_plugin_fingerprint="sha256:target",
        capability_fingerprint="sha256:capabilities",
        descriptor_fingerprint="sha256:descriptors",
        implementation_fingerprint="sha256:implementation",
        index_representation_fingerprint="sha256:representation",
        pr_overlay_representation_fingerprint="sha256:overlay",
        snapshots=snapshots,
    )


def test_generation_identity_is_order_independent():
    first = SimpleNamespace(path="a.php", change_type="MODIFIED", content="<?php")
    second = SimpleNamespace(path="etc/di.xml", change_type="ADDED", content="<config/>")

    assert _fingerprint((first, second)) == _fingerprint((second, first))


def test_generation_identity_changes_with_source_base_content_and_snapshot():
    file_info = SimpleNamespace(
        path="etc/di.xml",
        change_type="MODIFIED",
        content="<config/>",
    )
    changed_file = SimpleNamespace(
        path="etc/di.xml",
        change_type="MODIFIED",
        content="<config><type/></config>",
    )
    snapshot = SimpleNamespace(
        plugin_id="magento",
        kind="repository",
        content="snapshot-a",
    )
    changed_snapshot = SimpleNamespace(
        plugin_id="magento",
        kind="repository",
        content="snapshot-b",
    )
    baseline = _fingerprint((file_info,), snapshots=(snapshot,))

    assert _fingerprint((file_info,), source="other") != baseline
    assert _fingerprint((file_info,), base="other") != baseline
    assert _fingerprint((changed_file,), snapshots=(snapshot,)) != baseline
    assert _fingerprint((file_info,), snapshots=(changed_snapshot,)) != baseline


def test_generation_identity_changes_with_content_completeness():
    complete = SimpleNamespace(
        path="src/service.py",
        change_type="MODIFIED",
        content="same bytes",
        content_state="complete",
    )
    partial = SimpleNamespace(
        path="src/service.py",
        change_type="MODIFIED",
        content="same bytes",
        content_state="partial_diff",
    )

    assert _fingerprint((complete,)) != _fingerprint((partial,))


def test_generation_identity_changes_with_index_representation():
    file_info = SimpleNamespace(
        path="etc/di.xml",
        change_type="MODIFIED",
        content="<config/>",
    )
    baseline = _fingerprint((file_info,))
    changed = pr_overlay_generation_fingerprint(
        workspace="workspace",
        project="project",
        pr_number=42,
        branch="feature",
        base_branch="main",
        source_revision="head",
        base_revision="base",
        files=(file_info,),
        requested_plugin_ids=("php", "magento"),
        repository_plugin_ids=("php", "magento"),
        request_plugin_fingerprint="sha256:request",
        target_plugin_fingerprint="sha256:target",
        capability_fingerprint="sha256:capabilities",
        descriptor_fingerprint="sha256:descriptors",
        implementation_fingerprint="sha256:implementation",
        index_representation_fingerprint="sha256:other-representation",
        pr_overlay_representation_fingerprint="sha256:overlay",
        snapshots=(),
    )
    assert changed != baseline


def test_generation_identity_changes_with_overlay_representation():
    file_info = SimpleNamespace(
        path="src/service.py",
        change_type="MODIFIED",
        content="value = 1",
    )
    baseline = _fingerprint((file_info,))
    changed = pr_overlay_generation_fingerprint(
        workspace="workspace",
        project="project",
        pr_number=42,
        branch="feature",
        base_branch="main",
        source_revision="head",
        base_revision="base",
        files=(file_info,),
        requested_plugin_ids=("php", "magento"),
        repository_plugin_ids=("php", "magento"),
        request_plugin_fingerprint="sha256:request",
        target_plugin_fingerprint="sha256:target",
        capability_fingerprint="sha256:capabilities",
        descriptor_fingerprint="sha256:descriptors",
        implementation_fingerprint="sha256:implementation",
        index_representation_fingerprint="sha256:representation",
        pr_overlay_representation_fingerprint="sha256:other-overlay",
        snapshots=(),
    )
    assert changed != baseline


def test_reuse_requires_every_point_to_have_the_expected_identity():
    expected = "sha256:" + "1" * 64
    matching = SimpleNamespace(payload={"pr_generation_fingerprint": expected})
    stale = SimpleNamespace(payload={
        "pr_generation_fingerprint": ZERO_FINGERPRINT,
    })

    assert is_complete_reusable_generation((matching,), expected)
    assert not is_complete_reusable_generation((), expected)
    assert not is_complete_reusable_generation((matching, stale), expected)
