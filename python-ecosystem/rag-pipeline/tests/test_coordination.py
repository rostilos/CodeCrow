from unittest.mock import MagicMock, patch

import pytest

from rag_pipeline.core.coordination import (
    MutationCoordinationUnavailable,
    MutationLease,
    MutationLeaseUnavailable,
    ProjectMutationCoordinator,
)


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
        with coordinator.acquire(
            "workspace", "project", "full-index",
            collection_target="generation",
        ) as lease:
            coordinator._client.get.return_value = lease.token
            lease.assert_owned()

    assert coordinator._client.set.call_count == 2
    coordinator._client.eval.assert_called_once()


def test_project_mutation_lease_rejects_an_overlapping_job():
    coordinator = _coordinator()
    coordinator._client.set.return_value = False

    with pytest.raises(MutationLeaseUnavailable, match="another RAG mutation"):
        with coordinator.acquire(
            "workspace", "project", "full-index",
            collection_target="generation",
        ):
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


def test_project_mutation_coordination_fails_closed_when_redis_is_unavailable():
    coordinator = _coordinator()
    coordinator._client.set.side_effect = RuntimeError("redis unavailable")

    with pytest.raises(MutationCoordinationUnavailable, match="Redis is unavailable"):
        with coordinator.acquire(
            "workspace", "project", "full-index",
            collection_target="generation",
        ):
            pass


def test_pending_janitor_operation_check_propagates_redis_failure():
    coordinator = _coordinator()
    coordinator._client.exists.side_effect = RuntimeError("redis unavailable")

    with pytest.raises(RuntimeError, match="redis unavailable"):
        coordinator.is_operation_active("aaaaaaaa")
