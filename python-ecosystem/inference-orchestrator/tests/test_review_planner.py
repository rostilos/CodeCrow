"""Behavioral checks for structural ownership and cross-batch coverage."""

from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from service.review.planner import ReviewPlanner


@dataclass(frozen=True)
class Part:
    id: str
    path: str
    anchors: dict[int, str] = field(default_factory=lambda: {5: "changed"})
    side: str = "proposed"
    diff: str = "+changed"


def unit(unit_id, path, start=1, end=10):
    return {
        "unitId": unit_id, "path": path, "startLine": start, "endLine": end,
        "qualifiedName": unit_id, "kind": "function",
    }


def edge(source, target, kind="CALLS", **extra):
    return {"kind": kind, "sourceUnit": source, "targetUnit": target, **extra}


def reader(units, relations=()):
    async def read(*, pattern, target, focus_path):
        if pattern == "file_summary":
            return [value for value in units if value["path"] == target]
        assert pattern == "relations_of"
        return [
            value for value in relations
            if any(value.get(endpoint, {}).get("unitId") == target for endpoint in ("sourceUnit", "targetUnit"))
        ]
    return read


def owned_parts(plan):
    return {frozenset(part.id for part in batch.parts) for batch in plan.batches}


@pytest.mark.asyncio
async def test_complete_file_scope_keeps_related_and_separate_method_hunks_together():
    first = unit("first", "service.py", 1, 20)
    second = unit("second", "service.py", 25, 50)
    parts = [Part("one", "service.py"), Part("two", "service.py", {15: "second change"}), Part("three", "service.py", {30: "other method"})]

    plan = await ReviewPlanner(reader([first, second])).plan(parts)

    assert owned_parts(plan) == {frozenset({"one", "two", "three"})}
    assert not plan.cross_batch_scopes
    assert plan.graph_context["one"]["units"] == [first]
    assert plan.graph_context["three"]["units"] == [second]


@pytest.mark.asyncio
async def test_hunk_spanning_multiple_units_preserves_all_owners_and_hunks():
    outer = unit("class", "service.py", 1, 100)
    first = unit("first", "service.py", 5, 20)
    second = unit("second", "service.py", 25, 50)
    parts = [Part("spans", "service.py", {10: "a", 30: "b"}), Part("same-first", "service.py", {15: "c"}), Part("same-second", "service.py", {40: "d"})]

    plan = await ReviewPlanner(reader([outer, first, second])).plan(parts)

    assert owned_parts(plan) == {frozenset(part.id for part in parts)}
    assert {item["unitId"] for item in plan.graph_context["spans"]["units"]} == {"first", "second"}
    assert len([part for batch in plan.batches for part in batch.parts]) == len(parts)


@pytest.mark.asyncio
async def test_call_chain_groups_direct_contracts_and_preserves_boundary_source():
    units = [unit(name, f"{name}.py") for name in ("api", "service", "storage")]
    parts = [Part(value["unitId"], value["path"]) for value in units]
    relations = [edge(units[0], units[1]), edge(units[1], units[2])]

    plan = await ReviewPlanner(reader(units, relations)).plan(parts)

    assert owned_parts(plan) == {frozenset({"api", "service"}), frozenset({"storage"})}
    assert len(plan.cross_batch_scopes) == 1
    by_owned = {frozenset(part.id for part in batch.parts): batch for batch in plan.batches}
    assert [part.id for part in by_owned[frozenset({"api", "service"})].companion_parts] == ["storage"]
    assert [part.id for part in by_owned[frozenset({"storage"})].companion_parts] == ["service"]
    assert all(len(scope.batch_ids) == 2 for scope in plan.cross_batch_scopes)


@pytest.mark.asyncio
async def test_shared_unchanged_dependency_connects_changed_callers():
    first, second = unit("first", "a.py"), unit("second", "b.py")
    contract = unit("shared", "contracts.py")
    relations = [edge(first, contract, "PLUGIN", relation="consumes"), edge(second, contract, "PLUGIN", relation="produces")]

    plan = await ReviewPlanner(reader([first, second], relations)).plan([Part("a", "a.py"), Part("b", "b.py")])

    assert len(plan.cross_batch_scopes) == 1
    scope = plan.cross_batch_scopes[0]
    assert scope.reason == "Shared unchanged structural dependency: shared"
    assert len(scope.relations) == 2
    assert {relation["relation"] for relation in scope.relations} == {"consumes", "produces"}


@pytest.mark.asyncio
async def test_containment_hub_does_not_imply_cross_file_dependency():
    first, second = unit("first", "a.py"), unit("second", "b.py")
    package = unit("package", "package")
    relations = [edge(package, first, "CONTAINS"), edge(package, second, "PLUGIN", relation="contains")]

    plan = await ReviewPlanner(reader([first, second], relations)).plan([Part("a", "a.py"), Part("b", "b.py")])

    assert not plan.cross_batch_scopes


@pytest.mark.asyncio
async def test_target_side_deletion_is_not_bound_to_proposed_line_coordinates():
    parts = [Part("removed", "a.py", side="target"), Part("added", "a.py")]

    plan = await ReviewPlanner(reader([unit("unrelated-proposed", "a.py")])).plan(parts)

    assert owned_parts(plan) == {frozenset({"removed", "added"})}
    assert plan.graph_context["removed"]["units"] == []
    assert any("lack proposed-tree structural ownership" in item for item in plan.diagnostics)


@pytest.mark.asyncio
async def test_module_level_change_preserves_whole_file_scope():
    first, second = unit("first", "a.py", 5, 10), unit("second", "a.py", 15, 20)
    parts = [Part("mixed", "a.py", {1: "module constant", 6: "method"}), Part("other", "a.py", {16: "uses constant"})]

    plan = await ReviewPlanner(reader([first, second])).plan(parts)

    assert owned_parts(plan) == {frozenset({"mixed", "other"})}
    assert any("complete file scope" in item for item in plan.diagnostics)
    assert plan.graph_context["mixed"]["units"] == [first]
    assert not plan.graph_context["mixed"]["structuralOwnershipComplete"]


@pytest.mark.asyncio
async def test_missing_graph_falls_back_to_complete_files_without_dropping_content():
    long_diff = "+" + "critical source " * 20000
    parts = [Part("a1", "a.py", diff=long_diff), Part("a2", "a.py", {50: "changed"}), Part("b", "b.py")]

    plan = await ReviewPlanner().plan(parts)

    assert owned_parts(plan) == {frozenset({"a1", "a2"}), frozenset({"b"})}
    assert next(part for batch in plan.batches for part in batch.parts if part.id == "a1").diff == long_diff
    assert plan.diagnostics == ("Graph planning unavailable; using complete file scopes.",)


@pytest.mark.asyncio
async def test_failed_file_query_does_not_discard_other_files_graph_evidence():
    async def read(*, pattern, target, focus_path):
        if focus_path == "broken.py":
            raise OSError("temporarily unavailable")
        return [unit("good", "good.py")] if pattern == "file_summary" else []

    plan = await ReviewPlanner(read).plan([Part("broken", "broken.py"), Part("good", "good.py")])

    assert len(plan.batches) == 2
    assert plan.graph_context["good"]["units"][0]["unitId"] == "good"
    assert any("temporarily unavailable" in item for item in plan.diagnostics)


@pytest.mark.asyncio
async def test_failed_relation_query_retains_structural_ownership():
    async def read(*, pattern, target, focus_path):
        if pattern == "file_summary":
            return [unit("owner", "a.py")]
        raise OSError("relations unavailable")

    plan = await ReviewPlanner(read).plan([Part("a", "a.py")])

    assert plan.graph_context["a"]["units"][0]["unitId"] == "owner"
    assert plan.graph_context["a"]["relations"] == []
    assert any("relations unavailable" in item for item in plan.diagnostics)


@pytest.mark.asyncio
async def test_malformed_unit_spans_degrade_to_file_scope():
    malformed = {**unit("bad", "a.py"), "startLine": "not a line"}

    plan = await ReviewPlanner(reader([malformed])).plan([Part("a", "a.py")])

    assert owned_parts(plan) == {frozenset({"a"})}
    assert not plan.graph_context["a"]["units"]
    assert plan.diagnostics


@pytest.mark.asyncio
async def test_plan_is_stable_when_input_and_graph_order_change():
    units = [unit("a", "a.py"), unit("b", "b.py"), unit("c", "c.py")]
    parts = [Part(value["unitId"], value["path"]) for value in units]
    relations = [edge(units[0], units[1]), edge(units[1], units[2])]

    first = await ReviewPlanner(reader(units, relations)).plan(parts)
    second = await ReviewPlanner(reader(list(reversed(units)), list(reversed(relations)))).plan(list(reversed(parts)))

    assert first == second


@pytest.mark.asyncio
async def test_duplicate_graph_edges_are_not_repeated_in_joint_review():
    first, second = unit("first", "a.py"), unit("second", "b.py")
    relation = edge(first, second)

    plan = await ReviewPlanner(reader([first, second], [relation, relation])).plan([Part("a", "a.py"), Part("b", "b.py")])

    assert owned_parts(plan) == {frozenset({"a", "b"})}
    assert not plan.cross_batch_scopes
    assert len(plan.graph_context["a"]["relations"]) == 1


@pytest.mark.asyncio
async def test_path_endpoint_can_connect_file_scope_when_unit_identity_missing():
    first = unit("first", "a.py")
    relation = edge(first, {"path": "configuration.json"}, "DEPENDS_ON")

    plan = await ReviewPlanner(reader([first], [relation])).plan([Part("a", "a.py"), Part("config", "configuration.json")])

    assert len(plan.cross_batch_scopes) == 1
    assert len(plan.cross_batch_scopes[0].batch_ids) == 2


@pytest.mark.asyncio
async def test_file_summary_does_not_attach_units_from_other_files():
    read = AsyncMock(return_value=[unit("wrong", "elsewhere.py")])

    plan = await ReviewPlanner(read).plan([Part("a", "a.py")])

    assert plan.graph_context["a"]["units"] == []
    assert read.await_count == 1


@pytest.mark.asyncio
async def test_empty_change_needs_no_graph_queries():
    read = AsyncMock()

    plan = await ReviewPlanner(read).plan([])

    assert not plan.batches
    assert not plan.diagnostics
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_membership_does_not_merge_unrelated_changed_contracts():
    first, second = unit("first", "a.py"), unit("second", "b.py")
    plan = await ReviewPlanner(reader([first, second], [edge(first, second, "IMPORTS")])).plan([Part("a", "a.py"), Part("b", "b.py")])
    assert len(plan.batches) == 2
    assert not plan.cross_batch_scopes


@pytest.mark.asyncio
async def test_same_file_unchanged_method_does_not_force_contract_ownership():
    first, second, unchanged = unit("first", "a.py"), unit("second", "b.py"), unit("unchanged", "b.py", 20, 40)
    plan = await ReviewPlanner(reader([first, second, unchanged], [edge(first, unchanged)])).plan([Part("a", "a.py"), Part("b", "b.py")])
    assert len(plan.batches) == 2
    assert all(not batch.companion_parts for batch in plan.batches)


@pytest.mark.asyncio
async def test_mutually_coupled_contracts_have_no_arbitrary_group_size_limit():
    units = [unit(str(index), f"file{index}.py") for index in range(9)]
    relations = [edge(left, right) for index, left in enumerate(units) for right in units[index + 1:]]
    plan = await ReviewPlanner(reader(units, relations)).plan([Part(item["unitId"], item["path"]) for item in units])
    assert len(plan.batches) == 1
    assert len(plan.batches[0].parts) == 9
    assert not plan.cross_batch_scopes


def test_source_question_without_hunk_ids_preserves_explicit_path_without_expanding_to_all_changes():
    from service.review.review_stages import investigations_from

    parts = {"first": Part("first", "first.py"), "second": Part("second", "second.py")}
    questions = investigations_from([{"question": "Does caller use the new argument name?", "paths": ["caller.py"]}],
                                    origin="cross_file", parts=parts, allow_unscoped=False)
    assert questions[0]["paths"] == ["caller.py"]
    assert questions[0]["partIds"] == []
