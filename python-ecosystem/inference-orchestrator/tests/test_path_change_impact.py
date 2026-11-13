"""Path-only moves need exact-source impact review without inventing text edits."""
from model.dtos import ReviewRequestDto
from service.review.evidence_scopes import process_review_evidence_scopes
from service.review.pr_evidence import build_pr_evidence_ledger
from utils.hunk_coverage import validate_acquired_diff_manifest


OLD = "services/DonationServiceWrapper.js"
NEW = "services/donation-service-wrapper.js"
SOURCE = "const repository = require('./DonationBookshelfRepository');\nmodule.exports = repository;\n"
RAW = f"diff --git a/{OLD} b/{NEW}\nsimilarity index 100%\nrename from {OLD}\nrename to {NEW}\n"


def request_for_rename(source=SOURCE):
    return ReviewRequestDto(
        projectId=42, projectWorkspace="project", projectNamespace="namespace",
        projectVcsWorkspace="team", projectVcsRepoSlug="repo",
        aiProvider="OPENAI", aiModel="test-model", aiApiKey="test-key",
        rawDiff=RAW, changedFiles=[NEW], currentCommitHash="a" * 40,
        enrichmentData={"fileContents": [{"path": NEW, "content": source}]},
    )


def test_pure_rename_has_source_backed_impact_unit_but_zero_changed_lines():
    request = request_for_rename()
    scopes = process_review_evidence_scopes(request)
    renamed = scopes.review.files[0]
    assert not renamed.is_skipped
    assert scopes.review.total_files == 1
    assert renamed.additions == renamed.deletions == 0
    assert len(renamed.review_hunks) == 1
    unit = renamed.review_hunks[0]
    assert unit.evidence_kind == "path_change_impact"
    assert (unit.new_start, unit.new_count) == (1, 2)
    assert "require('./DonationBookshelfRepository')" in unit.content
    assert all(not line.startswith(("+", "-")) for line in unit.content.splitlines())
    assert "PATH-CHANGE IMPACT" in renamed.content
    assert OLD in renamed.content and NEW in renamed.content
    validate_acquired_diff_manifest([NEW], [], scopes.review)
    ledger = build_pr_evidence_ledger(scopes.full_pr, scopes.review, incremental=False)
    assert f"{OLD} → {NEW}" in ledger.full_pr_context
    assert "unchanged source" in ledger.full_pr_context


def test_missing_rename_source_has_impact_obligation_without_fabricated_lines():
    scopes = process_review_evidence_scopes(request_for_rename(None))
    assert scopes.review.files[0].review_hunks == []
    ledger = build_pr_evidence_ledger(scopes.full_pr, scopes.review, incremental=False)
    assert f"{OLD} → {NEW}" in ledger.full_pr_context
    assert "source unavailable" in ledger.full_pr_context


def test_deletion_keeps_removed_line_anchors_and_explicit_impact_obligation():
    request = request_for_rename()
    request.rawDiff = "diff --git a/services/old.js b/services/old.js\ndeleted file mode 100644\n--- a/services/old.js\n+++ /dev/null\n@@ -1 +0,0 @@\n-module.exports = run;\n"
    request.changedFiles = []
    request.deletedFiles = ["services/old.js"]
    scopes = process_review_evidence_scopes(request)
    removed = scopes.review.files[0].review_hunks[0]
    assert (removed.old_start, removed.old_count, removed.new_count) == (1, 1, 0)
    ledger = build_pr_evidence_ledger(scopes.full_pr, scopes.review, incremental=False)
    assert "PATH-CHANGE IMPACT" in ledger.full_pr_context
    assert "imports, callers, registrations" in ledger.full_pr_context
