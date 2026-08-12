from types import SimpleNamespace
import logging
from unittest.mock import MagicMock, patch

import pytest

from rag_pipeline.core.coordination import (
    MutationCoordinationUnavailable,
    MutationLease,
    MutationLeaseUnavailable,
    ProjectMutationCoordinator,
    RedisPermitPool,
)
from rag_pipeline.core.index_manager.collection_manager import CollectionManager
from rag_pipeline.core.index_manager.manager import RAGIndexManager


def _coordinator(timeout=0):
    coordinator = ProjectMutationCoordinator(
        "redis://unused",
        lease_seconds=60,
        acquire_timeout_seconds=timeout,
    )
    coordinator._client = MagicMock()
    return coordinator


def test_project_mutation_lease_is_acquired_verified_and_released():
    coordinator = _coordinator()
    coordinator._client.set.side_effect = [True, True]
    coordinator._client.get.return_value = None

    with patch.object(MutationLease, "start_renewal"):
        with coordinator.acquire("workspace", "project", "full-index") as lease:
            coordinator._client.get.return_value = lease.token
            lease.assert_owned()

    assert coordinator._client.set.call_count == 2
    coordinator._client.eval.assert_called_once()


def test_project_mutation_lease_rejects_an_overlapping_job():
    coordinator = _coordinator()
    coordinator._client.set.return_value = False

    with pytest.raises(MutationLeaseUnavailable, match="another RAG mutation"):
        with coordinator.acquire("workspace", "project", "full-index"):
            pass


def test_exact_generation_targets_have_independent_mutation_resources():
    coordinator = _coordinator()

    main = coordinator._resource_key("workspace", "project", "main-target")
    develop = coordinator._resource_key("workspace", "project", "develop-target")

    assert main != develop
    assert main == coordinator._resource_key("workspace", "project", "main-target")


def test_branch_publication_scope_serializes_only_the_same_branch_head():
    coordinator = _coordinator()

    main = coordinator._resource_key(
        "workspace", "project", "main-target", "branch-head:main"
    )
    main_next = coordinator._resource_key(
        "workspace", "project", "next-main-target", "branch-head:main"
    )
    develop = coordinator._resource_key(
        "workspace", "project", "develop-target", "branch-head:develop"
    )

    assert main == main_next
    assert main != develop


def test_pr_overlay_scope_serializes_only_the_same_pr():
    coordinator = _coordinator()

    pr_41_index = coordinator._resource_key(
        "workspace", "project", publication_scope="pr-overlay:41"
    )
    pr_41_delete = coordinator._resource_key(
        "workspace", "project", publication_scope="pr-overlay:41"
    )
    pr_42_index = coordinator._resource_key(
        "workspace", "project", publication_scope="pr-overlay:42"
    )

    assert pr_41_index == pr_41_delete
    assert pr_41_index != pr_42_index


def test_index_manager_binds_overlay_mutations_to_pr_scope():
    coordinator = MagicMock()
    manager = SimpleNamespace(_mutation_coordinator=coordinator)
    lease = object()
    coordinator.acquire.return_value = lease

    result = RAGIndexManager.pr_overlay_mutation(
        manager, "workspace", "project", 42, "index-pr-overlay"
    )

    assert result is lease
    coordinator.acquire.assert_called_once_with(
        "workspace",
        "project",
        "index-pr-overlay",
        publication_scope="pr-overlay:42",
    )


def test_project_mutation_coordination_fails_closed_when_redis_is_unavailable():
    coordinator = _coordinator()
    coordinator._client.set.side_effect = RuntimeError("redis unavailable")

    with pytest.raises(MutationCoordinationUnavailable, match="Redis is unavailable"):
        with coordinator.acquire("workspace", "project", "full-index"):
            pass


def test_openrouter_capacity_limiter_falls_back_locally_when_redis_is_unavailable():
    pool = RedisPermitPool(
        "redis://unused",
        2,
        permit_seconds=60,
        acquire_timeout_seconds=0.1,
    )
    pool._client = MagicMock()
    pool._client.eval.side_effect = RuntimeError("redis unavailable")

    with pool.permit():
        pass

    pool._client.eval.assert_called_once()
    assert pool._local.acquire(blocking=False)
    pool._local.release()


def test_openrouter_capacity_outage_logs_only_transitions(caplog):
    pool = RedisPermitPool(
        "redis://unused",
        2,
        permit_seconds=60,
        acquire_timeout_seconds=0.1,
    )
    pool._client = MagicMock()
    pool._client.eval.side_effect = [
        RuntimeError("redis unavailable"),
        RuntimeError("redis unavailable"),
        True,
    ]

    with caplog.at_level(logging.DEBUG):
        pool._disabled_until = 0
        with pool.permit():
            pass
        pool._disabled_until = 0
        with pool.permit():
            pass
        pool._disabled_until = 0
        with pool.permit():
            pass

    warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING
        and "capacity limit unavailable" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert "capacity limit recovered" in caplog.text


def test_pending_janitor_keeps_live_and_aliased_collections_and_deletes_expired():
    client = MagicMock()
    client.get_aliases.return_value.aliases = [
        SimpleNamespace(
            alias_name="active",
            collection_name="base_pending_1000000000_aaaaaaaa_bbbbbbbb",
        )
    ]
    client.get_collections.return_value.collections = [
        SimpleNamespace(name="base_pending_1000000000_aaaaaaaa_bbbbbbbb"),
        SimpleNamespace(name="base_pending_1000000000_cccccccc_dddddddd"),
        SimpleNamespace(name="base_pending_1000000000_eeeeeeee_ffffffff"),
        SimpleNamespace(name="legacy_pending_unknown"),
    ]
    manager = CollectionManager(client, 3)

    with patch(
        "rag_pipeline.core.index_manager.collection_manager.time.time",
        return_value=1000100000,
    ):
        cleaned = manager.cleanup_expired_pending_collections(
            is_operation_active=lambda token: token == "cccccccc",
            min_age_seconds=300,
        )

    assert cleaned == 1
    client.delete_collection.assert_called_once_with(
        "base_pending_1000000000_eeeeeeee_ffffffff"
    )


def test_pending_janitor_propagates_alias_read_failure_to_lifecycle_owner():
    client = MagicMock()
    client.get_aliases.side_effect = RuntimeError("qdrant unavailable")
    manager = CollectionManager(client, 3)

    with pytest.raises(RuntimeError, match="qdrant unavailable"):
        manager.cleanup_expired_pending_collections(
            is_operation_active=lambda _token: False,
            min_age_seconds=300,
        )

    client.get_collections.assert_not_called()
    client.delete_collection.assert_not_called()


def test_pending_janitor_operation_check_propagates_redis_failure():
    coordinator = _coordinator()
    coordinator._client.exists.side_effect = RuntimeError("redis unavailable")

    with pytest.raises(RuntimeError, match="redis unavailable"):
        coordinator.is_operation_active("aaaaaaaa")
