"""Contracts for the extracted Stage 1 local-evidence packing boundary."""

from types import SimpleNamespace

from model.multi_stage import ReviewFile
from service.review.orchestrator import stage_1_file_review
from service.review.orchestrator import stage_1_local_packing
from service.review.orchestrator.stage_1_local_packing import (
    Stage1LocalPackingInput,
    Stage1LocalPackingRuntime,
    Stage1PreparedContext,
    Stage1PromptMaterial,
    _allocate_stage1_invocation_quotas,
    pack_stage1_local_batches,
)


def _review_item(path: str, *, priority: str = "MEDIUM", related: bool = False):
    return {
        "file": ReviewFile(
            path=path,
            focus_areas=["general"],
            risk_level=priority,
        ),
        "priority": priority,
        "has_relationships": related,
    }


def _neutral_runtime() -> Stage1LocalPackingRuntime:
    def prepare_material(request, batch, prepared_context, is_incremental):
        paths = [item["file"].path for item in batch]
        return Stage1PromptMaterial(
            request=request,
            batch_items=batch,
            batch_files_data=[
                {"path": path, "current_code": "", "diff": ""}
                for path in paths
            ],
            batch_file_paths=paths,
            complete_current_file_paths=set(paths),
            current_source_per_file_budget=1_000,
            batch_metadata=[],
            enrichment_identifiers=None,
            project_rules="",
            previous_issues_for_batch="",
            file_metadata_text="",
            task_context="",
            plugin_context_override="",
            prepared_context=prepared_context,
            is_incremental=is_incremental,
            boundary_context="",
        )

    return Stage1LocalPackingRuntime(
        prepare_material=prepare_material,
        material_prompt_tokens=lambda material: 100 + len(material.batch_items),
        complete_plugin_context=lambda request, path: "",
    )


def test_typed_local_packing_boundary_owns_a_small_batch_once():
    request = SimpleNamespace(enrichmentData=None)
    prepared = Stage1PreparedContext()
    batch = [_review_item("src/example.py")]

    packed = pack_stage1_local_batches(
        [batch],
        Stage1LocalPackingInput(
            request=request,
            prepared_context=prepared,
            is_incremental=False,
            token_budget=1_000,
        ),
        _neutral_runtime(),
    )

    assert len(packed) == 1
    assert len(packed[0]) == 1
    assert packed[0][0]["_review_unit_id"].startswith("sha256:")
    assert packed[0][0]["_hunk_ids"] == ()


def test_invocation_quota_allocation_is_stable_and_bounded():
    batches = [
        [_review_item("src/a.py")],
        [_review_item("src/b.py")],
        [_review_item("src/c.py")],
    ]

    admitted = _allocate_stage1_invocation_quotas(
        batches,
        total_cap=5,
        per_original_unit_cap=2,
    )

    assert admitted == 5
    assert [batch[0]["_stage1_invocation_quota"] for batch in batches] == [
        2,
        2,
        1,
    ]


def test_stage_1_facade_preserves_historical_local_packing_imports():
    assert (
        stage_1_file_review.Stage1PreparedContext
        is stage_1_local_packing.Stage1PreparedContext
    )
    assert (
        stage_1_file_review.Stage1ReviewUnitState
        is stage_1_local_packing.Stage1ReviewUnitState
    )
    assert (
        stage_1_file_review._split_hunk_by_lines
        is stage_1_local_packing._split_hunk_by_lines
    )
    assert callable(stage_1_file_review._expand_oversized_stage1_evidence_batches)
