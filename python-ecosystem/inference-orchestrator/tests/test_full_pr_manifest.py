from __future__ import annotations

import pytest

from model.dtos import (
    PullRequestFileManifestDto,
    PullRequestManifestChangeDto,
)
from model.enrichment import FileContentDto, PrEnrichmentDataDto
from service.review.evidence_scopes import process_review_evidence_scopes
from service.review.orchestrator.orchestrator import (
    MultiStageReviewOrchestrator,
    _manifest_text_evidence_gaps,
)

from .prompt_dry_run_neutral_fixture import (
    DeterministicRagSpy,
    neutral_request,
)


def _manifest(*changes, completeness: str = "COMPLETE"):
    return PullRequestFileManifestDto(
        changes=[
            PullRequestManifestChangeDto(
                path=path,
                previousPath=previous,
                kind=kind,
            )
            for path, previous, kind in changes
        ],
        completeness=completeness,
        receipt="provider-page-receipt",
    )


def _single_file_diff(path: str, value: int) -> str:
    return f"""diff --git a/{path} b/{path}
new file mode 100644
--- /dev/null
+++ b/{path}
@@ -0,0 +1 @@
+value = {value}
"""


def test_incremental_review_uses_delta_but_full_snapshot_uses_complete_manifest():
    request = neutral_request().model_copy(update={
        "analysisMode": "INCREMENTAL",
        "deltaDiff": neutral_request().rawDiff,
        "changedFiles": ["src/file_0.py"],
        "fullPrChangedFiles": [
            "src/file_0.py",
            "src/from_run_1.py",
            "src/from_run_2.py",
        ],
        "pullRequestFileManifest": _manifest(
            ("src/from_run_1.py", "", "ADDED"),
            ("src/from_run_2.py", "", "MODIFIED"),
            ("src/file_0.py", "", "MODIFIED"),
        ),
        "fullPrManifestComplete": True,
        "enrichmentData": PrEnrichmentDataDto(fileContents=[
            FileContentDto(path="src/file_0.py", content="value_0 = 1\n"),
            FileContentDto(path="src/from_run_1.py", content="a = 1\n"),
            FileContentDto(path="src/from_run_2.py", content="b = 2\n"),
        ]),
    })

    scopes = process_review_evidence_scopes(request)

    assert [item.path for item in scopes.review.files] == ["src/file_0.py"]
    assert [item.path for item in scopes.full_pr.files] == [
        "src/from_run_1.py",
        "src/from_run_2.py",
        "src/file_0.py",
    ]
    assert all(item.full_content is not None for item in scopes.full_pr.files)
    assert _manifest_text_evidence_gaps(request, scopes.review) == set()


def test_full_review_reports_active_manifest_path_missing_from_patch():
    request = neutral_request().model_copy(update={
        "analysisMode": "FULL",
        "changedFiles": ["src/file_0.py", "src/patchless.py"],
        "pullRequestFileManifest": _manifest(
            ("src/file_0.py", "", "MODIFIED"),
            ("src/patchless.py", "", "MODIFIED"),
        ),
        "fullPrManifestComplete": True,
    })
    scopes = process_review_evidence_scopes(request)

    assert _manifest_text_evidence_gaps(request, scopes.review) == {
        "src/patchless.py"
    }


@pytest.mark.asyncio
async def test_four_runs_review_latest_delta_and_rebuild_complete_overlay():
    rag = DeterministicRagSpy()
    orchestrator = MultiStageReviewOrchestrator(object(), None, rag)

    active: list[tuple[str, str, str]] = []
    for run in range(1, 5):
        current_path = f"src/from_run_{run}.py"
        active.append((current_path, "", "ADDED"))
        delta = _single_file_diff(current_path, run)
        request = neutral_request().model_copy(update={
            "analysisMode": "INCREMENTAL" if run > 1 else "FULL",
            "commitHash": f"head-{run}",
            "currentCommitHash": f"head-{run}",
            "previousCommitHash": f"head-{run - 1}" if run > 1 else None,
            "rawDiff": delta,
            "deltaDiff": delta if run > 1 else None,
            "changedFiles": [current_path],
            "fullPrChangedFiles": [path for path, _, _ in active],
            "pullRequestFileManifest": _manifest(*active),
            "fullPrManifestComplete": True,
            "enrichmentData": PrEnrichmentDataDto(fileContents=[
                FileContentDto(path=path, content=f"value = {index}\n")
                for index, (path, _, _) in enumerate(active, start=1)
            ]),
        })
        scopes = process_review_evidence_scopes(request)

        assert [item.path for item in scopes.review.files] == [current_path]
        await orchestrator._index_pr_files(request, scopes.full_pr)
        assert rag.index_requests[-1]["source_revision"] == f"head-{run}"

    final_payload = {
        item["path"]: item for item in rag.index_requests[-1]["files"]
    }
    assert set(final_payload) == {
        "src/from_run_1.py",
        "src/from_run_2.py",
        "src/from_run_3.py",
        "src/from_run_4.py",
    }


def test_manifest_only_maintenance_never_creates_review_hunks():
    request = neutral_request().model_copy(update={
        "analysisMode": "FULL",
        "changedFiles": [],
        "rawDiff": "diff --git a/src/generated.bin b/src/generated.bin\n",
        "prContextMaintenanceRequired": True,
        "pullRequestFileManifest": _manifest(
            ("src/generated.bin", "", "ADDED"),
        ),
        "fullPrManifestComplete": True,
        "enrichmentData": PrEnrichmentDataDto(fileContents=[
            FileContentDto(path="src/generated.bin", content="bytes\n"),
        ]),
    })

    scopes = process_review_evidence_scopes(request)

    assert scopes.review.files == []
    assert scopes.review.hunk_manifest() == []
    assert [item.path for item in scopes.full_pr.files] == ["src/generated.bin"]


def test_complete_manifest_prunes_patch_paths_absent_from_current_base_to_head_state():
    request = neutral_request(file_count=2).model_copy(update={
        "pullRequestFileManifest": _manifest(
            ("src/file_1.py", "src/old_name.py", "RENAMED"),
        ),
        "fullPrManifestComplete": True,
        "enrichmentData": PrEnrichmentDataDto(fileContents=[
            FileContentDto(path="src/file_1.py", content="value_1 = 2\n"),
        ]),
    })

    scopes = process_review_evidence_scopes(request)

    assert [item.path for item in scopes.full_pr.files] == ["src/file_1.py"]
    assert scopes.full_pr.files[0].old_path == "src/old_name.py"


@pytest.mark.asyncio
async def test_overlay_is_not_mutated_for_incomplete_manifest():
    rag = DeterministicRagSpy()
    request = neutral_request().model_copy(update={
        "pullRequestFileManifest": _manifest(
            ("src/file_0.py", "", "MODIFIED"),
            completeness="INCOMPLETE",
        ),
        "fullPrManifestComplete": False,
    })
    scopes = process_review_evidence_scopes(request)
    orchestrator = MultiStageReviewOrchestrator(object(), None, rag)

    await orchestrator._index_pr_files(request, scopes.full_pr)

    assert rag.index_requests == []
    assert orchestrator._pr_indexed is False


@pytest.mark.asyncio
async def test_overlay_is_not_mutated_without_provider_manifest_receipt():
    rag = DeterministicRagSpy()
    request = neutral_request().model_copy(update={
        "pullRequestFileManifest": None,
        "fullPrManifestComplete": None,
    })
    scopes = process_review_evidence_scopes(request)
    orchestrator = MultiStageReviewOrchestrator(object(), None, rag)

    await orchestrator._index_pr_files(request, scopes.full_pr)

    assert rag.index_requests == []
    assert orchestrator._pr_indexed is False


@pytest.mark.asyncio
async def test_overlay_emits_rename_and_earlier_deletion_tombstones():
    rag = DeterministicRagSpy()
    request = neutral_request().model_copy(update={
        "fullPrChangedFiles": ["src/file_0.py", "src/new_name.py"],
        "fullPrDeletedFiles": ["src/old_name.py", "src/from_run_1.py"],
        "pullRequestFileManifest": _manifest(
            ("src/file_0.py", "", "MODIFIED"),
            ("src/new_name.py", "src/old_name.py", "RENAMED"),
        ),
        "fullPrManifestComplete": True,
        "enrichmentData": PrEnrichmentDataDto(fileContents=[
            FileContentDto(path="src/file_0.py", content="value_0 = 1\n"),
            FileContentDto(path="src/new_name.py", content="renamed = True\n"),
        ]),
    })
    scopes = process_review_evidence_scopes(request)
    orchestrator = MultiStageReviewOrchestrator(object(), None, rag)

    await orchestrator._index_pr_files(request, scopes.full_pr)

    payload = {item["path"]: item for item in rag.index_requests[0]["files"]}
    assert payload["src/old_name.py"]["change_type"] == "DELETED"
    assert payload["src/from_run_1.py"]["change_type"] == "DELETED"


@pytest.mark.asyncio
async def test_overlay_is_not_mutated_when_one_active_path_lacks_exact_source():
    rag = DeterministicRagSpy()
    request = neutral_request().model_copy(update={
        "pullRequestFileManifest": _manifest(
            ("src/file_0.py", "", "MODIFIED"),
            ("src/missing.py", "", "ADDED"),
        ),
        "fullPrManifestComplete": True,
        "enrichmentData": PrEnrichmentDataDto(fileContents=[
            FileContentDto(path="src/file_0.py", content="value_0 = 1\n"),
        ]),
    })
    scopes = process_review_evidence_scopes(request)
    orchestrator = MultiStageReviewOrchestrator(object(), None, rag)

    await orchestrator._index_pr_files(request, scopes.full_pr)

    assert rag.index_requests == []
    assert orchestrator._pr_indexed is False
