import pytest

from model.dtos import ReviewRequestDto
from service.review.review_service import select_review_evidence_diff
from utils.diff_processor import DiffProcessor, HunkDisposition
from utils.hunk_coverage import (
    HunkCoverageLedger,
    HunkCoverageState,
    validate_acquired_diff_manifest,
)


def _diff(path: str = "src/a.php") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )


def test_reviewable_hunk_requires_every_transition():
    ledger = HunkCoverageLedger.from_processed_diff(DiffProcessor().process(_diff()))
    ledger.mark_planned(["src/a.php"])
    ledger.mark_reviewed(["src/a.php"])
    ledger.mark_validated()
    ledger.complete()
    ledger.assert_complete()
    assert ledger.summary()[HunkCoverageState.COMPLETED.value] == 1


def test_omitted_reviewable_path_fails_closed():
    ledger = HunkCoverageLedger.from_processed_diff(DiffProcessor().process(_diff()))
    with pytest.raises(RuntimeError, match="omitted"):
        ledger.mark_planned([])


def test_exact_hunk_completion_rejects_a_missing_derived_hunk():
    processed = DiffProcessor().process(_diff("src/a.php") + _diff("src/b.php"))
    ledger = HunkCoverageLedger.from_processed_diff(processed)
    ledger.mark_planned(["src/a.php", "src/b.php"])

    with pytest.raises(RuntimeError, match="omitted reviewable hunk identities"):
        ledger.mark_reviewed_hunks([processed.files[0].hunks[0].id])


def test_exact_hunk_completion_advances_every_reviewable_identity():
    processed = DiffProcessor().process(_diff("src/a.php") + _diff("src/b.php"))
    ledger = HunkCoverageLedger.from_processed_diff(processed)
    ledger.mark_planned(["src/a.php", "src/b.php"])
    expected = tuple(
        hunk.id
        for file in processed.files
        for hunk in file.hunks
    )

    ledger.mark_reviewed_hunks(reversed(expected))
    ledger.mark_validated()
    ledger.complete()
    ledger.assert_complete()

    assert ledger.reviewable_hunk_ids == tuple(sorted(expected))
    assert tuple(
        hunk_id for hunk_id, _path in ledger.reviewable_hunks
    ) == tuple(sorted(expected))
    assert ledger.summary()[HunkCoverageState.COMPLETED.value] == 2


def test_exact_hunk_completion_rejects_unknown_identity():
    processed = DiffProcessor().process(_diff())
    ledger = HunkCoverageLedger.from_processed_diff(processed)
    ledger.mark_planned(["src/a.php"])

    with pytest.raises(RuntimeError, match="unknown reviewable hunk identities"):
        ledger.mark_reviewed_hunks([
            processed.files[0].hunks[0].id,
            "sha256:unknown",
        ])


def test_non_reviewable_hunk_has_explicit_terminal_reason():
    processed = DiffProcessor().process(_diff("src/deleted.php"))
    processed.files[0].hunks[0] = processed.files[0].hunks[0].__class__(
        **{
            **processed.files[0].hunks[0].__dict__,
            "disposition": HunkDisposition.DELETED,
        }
    )
    ledger = HunkCoverageLedger.from_processed_diff(processed)
    ledger.mark_planned([])
    ledger.mark_reviewed([])
    ledger.mark_validated()
    ledger.complete()
    ledger.assert_complete()
    assert ledger.summary()[HunkCoverageState.EXCLUDED.value] == 1


def test_incremental_manifest_is_validated_against_delta_not_full_pr_diff():
    full_diff = _diff("src/old.php") + _diff("src/current.php")
    delta_diff = _diff("src/current.php")
    request = ReviewRequestDto(
        projectId=42,
        projectVcsWorkspace="workspace",
        projectVcsRepoSlug="repository",
        projectWorkspace="project",
        projectNamespace="namespace",
        aiProvider="OPENAI",
        aiModel="test-model",
        aiApiKey="test-key",
        analysisMode="INCREMENTAL",
        rawDiff=full_diff,
        deltaDiff=delta_diff,
        changedFiles=["src/current.php"],
    )

    processed_diff = DiffProcessor().process(select_review_evidence_diff(request))

    validate_acquired_diff_manifest(
        request.changedFiles,
        request.deletedFiles,
        processed_diff,
    )
    assert [file.path for file in processed_diff.files] == ["src/current.php"]
