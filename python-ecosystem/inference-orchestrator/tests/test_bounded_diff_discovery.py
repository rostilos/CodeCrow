"""Provider-free regressions for bounded prompt ownership and removal discovery."""
from types import SimpleNamespace
import pytest

from model.output_schemas import CodeReviewIssue
from service.review.candidate_ledger import CandidateEvidenceLedger, generation_prompt_digest
from service.review.pr_evidence import build_pr_evidence_ledger
from service.review.orchestrator.orchestrator import _register_stage_2_candidates
from service.review.orchestrator.stage_0_planning import _mechanical_skip_reason
from service.review.orchestrator.stage_1_local_packing import (
    Stage1PreparedContext, Stage1ReviewUnitState, _ensure_stage1_review_unit,
)
from service.review.orchestrator.verification_agent import (
    apply_candidate_provenance_gate, run_deterministic_evidence_gate,
)
from utils.diff_processor import DiffProcessor, DiffChangeType
from utils.hunk_coverage import HunkCoverageLedger


def issue(path, snippet, line=1):
    return CodeReviewIssue(file=path, line=line, severity="HIGH", category="BUG_RISK",
        title="Module fails at import", reason="The changed module fails at import.",
        suggestedFixDescription="Repair the failing expression.", codeSnippet=snippet)


def request():
    return SimpleNamespace(previousCodeAnalysisIssues=[], enrichmentData=None)


def owned(processed):
    file = processed.files[0]
    prepared = Stage1PreparedContext(diff_source=processed, diff_by_path={file.path: file})
    unit = _ensure_stage1_review_unit({"file": SimpleNamespace(path=file.path)}, prepared)
    state = Stage1ReviewUnitState()
    state.register_batches([[unit]])
    state.mark_completed((unit["_review_unit_id"],))
    state.assert_complete()
    return unit, state


@pytest.mark.parametrize("change_type", ["added", "modified", "deleted"])
def test_compaction_retains_visible_candidates_and_excludes_hidden_evidence(change_type):
    path = "src/large.py"
    lines = ["result = 1 / 0"] + [f"padding_{i} = '{'x' * 100}'" for i in range(320)]
    if change_type == "deleted":
        metadata = "deleted file mode 100644\n--- a/src/large.py\n+++ /dev/null"
        hunk = f"@@ -1,{len(lines)} +0,0 @@"
        body = "\n".join("-" + line for line in lines)
    elif change_type == "added":
        metadata = "new file mode 100644\n--- /dev/null\n+++ b/src/large.py"
        hunk = f"@@ -0,0 +1,{len(lines)} @@"
        body = "\n".join("+" + line for line in lines)
    else:
        metadata = "--- a/src/large.py\n+++ b/src/large.py"
        hunk = f"@@ -1,1 +1,{len(lines)} @@"
        body = "-old_value = 1\n" + "\n".join("+" + line for line in lines)
    diff = f"diff --git a/{path} b/{path}\n{metadata}\n{hunk}\n{body}\n"
    processed = DiffProcessor().process(diff)
    file = processed.files[0]
    assert file.change_type.value == change_type
    assert lines[0] in file.content and lines[-1] not in file.content
    unit, state = owned(processed)
    assert unit["_hunk_ids"] and state.omitted_hunk_ids
    coverage = HunkCoverageLedger.from_processed_diff(processed)
    coverage.mark_planned([path])
    coverage.mark_budget_omitted_hunks(state.omitted_hunk_ids, reason="bounded diff")
    coverage.mark_reviewed_hunks(state.reviewed_hunk_ids, allow_excluded=True)
    coverage.mark_validated()
    coverage.complete()
    coverage.assert_complete()
    assert coverage.summary()["excluded"] == 1
    assert coverage.summary()["completed"] == 1
    for snippet, expected in [(lines[0], 1), (lines[-1], 0)]:
        finding = issue(path, snippet)
        ledger = CandidateEvidenceLedger()
        ledger.register(finding, stage="stage_1", source_key=snippet,
            review_unit_ids=(unit["_review_unit_id"],), prompt_hunk_ids=unit["_hunk_ids"],
            generation_prompt=file.content)
        kept = apply_candidate_provenance_gate([finding], request(), processed, ledger, state.units_by_hunk)
        assert len(kept) == expected
        if kept:
            assert run_deterministic_evidence_gate(kept, request(), processed, ledger) == kept
    evidence = build_pr_evidence_ledger(processed, processed, incremental=False)
    assert lines[-1] not in evidence.full_pr_context
    assert not evidence.full_evidence_complete
    finding = issue(path, lines[0])
    ledger = CandidateEvidenceLedger()
    _register_stage_2_candidates([finding], request(), processed, state, ledger,
        set(evidence.delta_hunk_ids), {},
        {"generationPromptDigest": generation_prompt_digest(evidence.full_pr_context)})
    assert apply_candidate_provenance_gate([finding], request(), processed, ledger, state.units_by_hunk) == [finding]


def test_whole_file_deletion_owns_base_evidence_and_starts_discovery():
    path = "src/api.py"
    diff = f"diff --git a/{path} b/{path}\ndeleted file mode 100644\n--- a/{path}\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-def api():\n-    return 1\n"
    processed = DiffProcessor().process(diff)
    file = processed.files[0]
    assert file.change_type is DiffChangeType.DELETED
    assert not file.is_skipped
    assert _mechanical_skip_reason(file) is None
    assert HunkCoverageLedger.from_processed_diff(processed).reviewable_hunk_ids
    unit, state = owned(processed)
    finding = issue(path, "def api():")
    ledger = CandidateEvidenceLedger()
    ledger.register(finding, stage="stage_1", source_key="deletion",
        review_unit_ids=(unit["_review_unit_id"],), prompt_hunk_ids=unit["_hunk_ids"],
        generation_prompt=file.content)
    assert apply_candidate_provenance_gate([finding], request(), processed, ledger, state.units_by_hunk) == [finding]
    assert run_deterministic_evidence_gate([finding], request(), processed, ledger) == [finding]
    assert finding.anchorSide == "BASE" and finding.line == 1
    evidence = build_pr_evidence_ledger(processed, processed, incremental=False)
    assert list(evidence.evidence_by_ref.values())[0].line_start == 1
