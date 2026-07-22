"""
Tests for MultiStageReviewOrchestrator helper methods and _convert_cross_file_issues.

Covers: _filter_diff_for_files, _split_issues_into_batches,
        _deduplicate_previous_issues, _ensure_all_files_planned,
        _count_files, _convert_cross_file_issues
"""
import pytest
from unittest.mock import MagicMock

from service.review.orchestrator.orchestrator import (
    MultiStageReviewOrchestrator,
    _apply_stage_3_dismissals,
    _convert_cross_file_issues,
    _deduplicate_cross_batch_issues_preserving_lifecycle,
    _partition_issue_lifecycle,
    _partition_protected_active_issues,
    _retain_published_cross_file_issues,
    _serialize_issue_for_client,
    _suppress_duplicates_of_protected_history,
)
from model.multi_stage import (
    CrossFileAnalysisResult,
    CrossFileIssue,
    ReviewPlan,
    ReviewFile,
    FileGroup,
    FileToSkip,
)
from model.output_schemas import CodeReviewIssue


@pytest.fixture
def orchestrator():
    return MultiStageReviewOrchestrator(
        llm=MagicMock(),
        mcp_client=MagicMock(),
    )


# ── _filter_diff_for_files ──────────────────────────────────────

class TestFilterDiffForFiles:
    def test_filters_relevant_sections(self):
        raw_diff = (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1,3 +1,4 @@\n"
            "+import os\n"
            "diff --git a/src/utils.py b/src/utils.py\n"
            "--- a/src/utils.py\n"
            "+++ b/src/utils.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        result = MultiStageReviewOrchestrator._filter_diff_for_files(
            raw_diff, {"src/main.py"}
        )
        assert "src/main.py" in result
        assert "src/utils.py" not in result

    def test_none_diff(self):
        assert MultiStageReviewOrchestrator._filter_diff_for_files(None, {"a.py"}) is None

    def test_empty_files(self):
        assert MultiStageReviewOrchestrator._filter_diff_for_files("diff...", set()) is None

    def test_no_matching_files(self):
        raw_diff = "diff --git a/other.py b/other.py\n---\n+++\n@@\n"
        result = MultiStageReviewOrchestrator._filter_diff_for_files(
            raw_diff, {"missing.py"}
        )
        assert result is None

    def test_both_a_b_paths_checked(self):
        raw_diff = "diff --git a/old_name.py b/new_name.py\n---\n+++\n@@\n"
        result = MultiStageReviewOrchestrator._filter_diff_for_files(
            raw_diff, {"new_name.py"}
        )
        assert result is not None


# ── _split_issues_into_batches ───────────────────────────────────

class TestSplitIssuesIntoBatches:
    def test_single_batch(self, orchestrator):
        issues = [{"file": "a.py", "title": "Issue 1"}]
        batches = orchestrator._split_issues_into_batches(issues)
        assert len(batches) == 1
        assert len(batches[0]) == 1

    def test_groups_by_file(self, orchestrator):
        issues = [
            {"file": "a.py", "title": "1"},
            {"file": "a.py", "title": "2"},
            {"file": "b.py", "title": "3"},
        ]
        batches = orchestrator._split_issues_into_batches(issues)
        assert len(batches) >= 1
        total = sum(len(b) for b in batches)
        assert total == 3

    def test_respects_max_issues_cap(self, orchestrator):
        # Create more issues than _BRANCH_BATCH_MAX_ISSUES
        issues = [{"file": f"file{i}.py", "title": f"Issue {i}"} for i in range(50)]
        batches = orchestrator._split_issues_into_batches(issues)
        for batch in batches:
            assert len(batch) <= orchestrator._BRANCH_BATCH_MAX_ISSUES

    def test_empty_issues(self, orchestrator):
        batches = orchestrator._split_issues_into_batches([])
        assert batches == []

    def test_unknown_file(self, orchestrator):
        issues = [{"title": "No file key"}]
        batches = orchestrator._split_issues_into_batches(issues)
        assert len(batches) == 1


# ── _deduplicate_previous_issues ─────────────────────────────────

class TestDeduplicatePreviousIssues:
    def test_empty(self):
        assert MultiStageReviewOrchestrator._deduplicate_previous_issues([]) == []

    def test_no_duplicates(self):
        issues = [
            {"file": "a.py", "lineHash": "h1", "category": "BUG", "title": "Bug A", "severity": "HIGH"},
            {"file": "b.py", "lineHash": "h2", "category": "BUG", "title": "Bug B", "severity": "HIGH"},
        ]
        result = MultiStageReviewOrchestrator._deduplicate_previous_issues(issues)
        assert len(result) == 2

    def test_location_dedup(self):
        issues = [
            {"file": "a.py", "lineHash": "abc", "category": "BUG", "title": "Bug 1", "severity": "HIGH"},
            {"file": "a.py", "lineHash": "abc", "category": "BUG", "title": "Bug 1 again", "severity": "MEDIUM"},
        ]
        result = MultiStageReviewOrchestrator._deduplicate_previous_issues(issues)
        assert len(result) == 1
        assert result[0]["severity"] == "HIGH"  # Keeps higher severity

    def test_semantic_dedup(self):
        issues = [
            {"file": "a.py", "lineHash": "h1", "category": "BUG", "title": "Null pointer dereference in handler", "severity": "HIGH"},
            {"file": "a.py", "lineHash": "h2", "category": "BUG", "title": "Null pointer dereference in the handler method", "severity": "MEDIUM"},
        ]
        result = MultiStageReviewOrchestrator._deduplicate_previous_issues(issues)
        assert len(result) == 1  # Similar titles deduped

    def test_different_files_not_deduped(self):
        issues = [
            {"file": "a.py", "lineHash": "h1", "category": "BUG", "title": "Same title", "severity": "HIGH"},
            {"file": "b.py", "lineHash": "h1", "category": "BUG", "title": "Same title", "severity": "HIGH"},
        ]
        result = MultiStageReviewOrchestrator._deduplicate_previous_issues(issues)
        assert len(result) == 2  # Different files = different issues

    def test_no_line_hash_not_location_deduped(self):
        issues = [
            {"file": "a.py", "lineHash": "", "category": "BUG", "title": "Issue A", "severity": "HIGH"},
            {"file": "a.py", "lineHash": "", "category": "BUG", "title": "Issue B completely different", "severity": "MEDIUM"},
        ]
        result = MultiStageReviewOrchestrator._deduplicate_previous_issues(issues)
        assert len(result) == 2  # Different titles, no lineHash


# ── _ensure_all_files_planned ────────────────────────────────────

class TestEnsureAllFilesPlanned:
    def test_no_missing(self, orchestrator):
        plan = ReviewPlan(
            analysis_summary="ok",
            file_groups=[
                FileGroup(
                    group_id="g1",
                    priority="HIGH",
                    rationale="test",
                    files=[ReviewFile(path="a.py")],
                )
            ],
        )
        result = orchestrator._ensure_all_files_planned(plan, ["a.py"])
        assert len(result.file_groups) == 1

    def test_adds_missing_files(self, orchestrator):
        plan = ReviewPlan(
            analysis_summary="ok",
            file_groups=[
                FileGroup(
                    group_id="g1",
                    priority="HIGH",
                    rationale="test",
                    files=[ReviewFile(path="a.py")],
                )
            ],
        )
        result = orchestrator._ensure_all_files_planned(plan, ["a.py", "b.py", "c.py"])
        assert len(result.file_groups) == 2
        catchall = result.file_groups[-1]
        assert catchall.group_id == "uncategorized"
        paths = [f.path for f in catchall.files]
        assert "b.py" in paths
        assert "c.py" in paths

    def test_does_not_readd_files_skipped_by_stage_0(self, orchestrator):
        plan = ReviewPlan(
            analysis_summary="ok",
            file_groups=[],
            files_to_skip=[
                FileToSkip(path="package-lock.json", reason="No substantive changed content")
            ],
        )

        result = orchestrator._ensure_all_files_planned(
            plan,
            ["package-lock.json", "src/app.py"],
        )

        assert len(result.file_groups) == 1
        assert [f.path for f in result.file_groups[0].files] == ["src/app.py"]
        assert result.files_to_skip[0].path == "package-lock.json"

    def test_empty_changed_files(self, orchestrator):
        plan = ReviewPlan(
            analysis_summary="ok",
            file_groups=[],
        )
        result = orchestrator._ensure_all_files_planned(plan, [])
        assert len(result.file_groups) == 0


# ── _count_files ─────────────────────────────────────────────────

class TestCountFiles:
    def test_counts_all(self, orchestrator):
        plan = ReviewPlan(
            analysis_summary="ok",
            file_groups=[
                FileGroup(
                    group_id="g1",
                    priority="HIGH",
                    rationale="test",
                    files=[ReviewFile(path="a.py"), ReviewFile(path="b.py")],
                ),
                FileGroup(
                    group_id="g2",
                    priority="MEDIUM",
                    rationale="test",
                    files=[ReviewFile(path="c.py")],
                ),
            ],
        )
        assert orchestrator._count_files(plan) == 3

    def test_empty_plan(self, orchestrator):
        plan = ReviewPlan(analysis_summary="ok", file_groups=[])
        assert orchestrator._count_files(plan) == 0


# ── _convert_cross_file_issues ───────────────────────────────────

class TestConvertCrossFileIssues:
    def test_basic_conversion(self):
        cfi = CrossFileIssue(
            id="cfi-1",
            severity="HIGH",
            category="ARCHITECTURE",
            title="Circular dependency",
            primary_file="a.py",
            affected_files=["a.py", "b.py"],
            description="Circular dependency between modules",
            evidence="a imports b, b imports a",
            business_impact="Hard to maintain",
            suggestion="Use dependency injection",
            line=42,
            codeSnippet="import b",
        )
        result = _convert_cross_file_issues([cfi])
        assert len(result) == 1
        issue = result[0]
        assert issue.id == "cfi-1"
        assert issue.severity == "HIGH"
        assert issue.file == "a.py"
        assert issue.line == 42
        assert issue.codeSnippet == "import b"
        assert "Also affects: b.py" in issue.reason

    def test_no_primary_file_uses_first_affected(self):
        cfi = CrossFileIssue(
            id="cfi-2",
            severity="MEDIUM",
            category="BUG",
            title="Missing error handling",
            primary_file="",
            affected_files=["x.py", "y.py"],
            description="desc",
            evidence="ev",
            business_impact="impact",
            suggestion="fix",
        )
        result = _convert_cross_file_issues([cfi])
        assert result[0].file == "x.py"

    def test_no_line_defaults_to_1(self):
        cfi = CrossFileIssue(
            id="cfi-3",
            severity="LOW",
            category="STYLE",
            title="Inconsistent naming",
            affected_files=["a.py"],
            description="d",
            evidence="e",
            business_impact="b",
            suggestion="s",
        )
        result = _convert_cross_file_issues([cfi])
        assert result[0].line == 1

    def test_empty_list(self):
        assert _convert_cross_file_issues([]) == []

    def test_multiple_issues(self):
        issues = [
            CrossFileIssue(
                id=f"cfi-{i}",
                severity="MEDIUM",
                category="BUG",
                title=f"Issue {i}",
                affected_files=["a.py"],
                description="d",
                evidence="e",
                business_impact="b",
                suggestion="s",
            )
            for i in range(3)
        ]
        result = _convert_cross_file_issues(issues)
        assert len(result) == 3


class TestRetainPublishedCrossFileIssues:
    def test_removes_rejected_candidates_from_stage_3_context(self):
        kept = CrossFileIssue(
            id="CROSS_001",
            severity="MEDIUM",
            category="ARCHITECTURE",
            title="Concrete contract break",
            primary_file="src/a.py",
            affected_files=["src/a.py", "src/b.py"],
            description="The changed callers pass incompatible values.",
            evidence="a.py and b.py disagree on the required type.",
            business_impact="The request fails at runtime.",
            suggestion="Use the same required type.",
            line=10,
            codeSnippet="call(value)",
        )
        rejected = CrossFileIssue(
            id="CROSS_002",
            severity="INFO",
            category="ARCHITECTURE",
            title="Valid fixes use different styles",
            primary_file="src/c.py",
            affected_files=["src/c.py", "src/d.py"],
            description="Both changes already handle null correctly.",
            evidence="One uses a default and one uses a cast.",
            business_impact="No current behavior is broken.",
            suggestion="Consider standardizing them.",
            line=20,
            codeSnippet="value = value or ''",
        )
        results = CrossFileAnalysisResult(
            pr_risk_level="LOW",
            cross_file_issues=[kept, rejected],
            pr_recommendation="PASS_WITH_WARNINGS",
            confidence="HIGH",
        )
        published = _convert_cross_file_issues([kept])

        removed = _retain_published_cross_file_issues(results, published)

        assert removed == 1
        assert [issue.id for issue in results.cross_file_issues] == ["CROSS_001"]
        assert results.pr_risk_level == "MEDIUM"
        assert results.pr_recommendation == "PASS_WITH_WARNINGS"

    def test_normalizes_warning_metadata_when_all_candidates_are_rejected(self):
        rejected = CrossFileIssue(
            id="CROSS_002",
            severity="MEDIUM",
            category="ARCHITECTURE",
            title="Different valid null handling",
            primary_file="src/c.py",
            affected_files=["src/c.py", "src/d.py"],
            description="Both changes already handle null correctly.",
            evidence="One uses a default and one uses a cast.",
            business_impact="No current behavior is broken.",
            suggestion="Consider standardizing them.",
            line=20,
            codeSnippet="value = value or ''",
        )
        results = CrossFileAnalysisResult(
            pr_risk_level="MEDIUM",
            cross_file_issues=[rejected],
            pr_recommendation="PASS_WITH_WARNINGS",
            confidence="HIGH",
        )

        removed = _retain_published_cross_file_issues(results, [])

        assert removed == 1
        assert results.cross_file_issues == []
        assert results.pr_risk_level == "LOW"
        assert results.pr_recommendation == "PASS"


class TestPartitionIssueLifecycle:
    @pytest.mark.parametrize("resolved_first", [False, True])
    def test_resolved_history_is_kept_out_of_active_dedup_input(
        self,
        resolved_first,
    ):
        active = CodeReviewIssue(
            file="a.py", line=10, severity="MEDIUM", category="BUG_RISK",
            reason="A different current defect remains.",
            suggestedFixDescription="Fix the current defect.",
        )
        resolved = CodeReviewIssue(
            id="12524", file="a.py", line=10, severity="MEDIUM",
            category="BUG_RISK", reason="Historical defect.",
            suggestedFixDescription="Already fixed.", isResolved=True,
        )

        input_items = [resolved, active] if resolved_first else [active, resolved]
        active_items, resolved_items = _partition_issue_lifecycle(input_items)

        assert active_items == [active]
        assert resolved_items == [resolved]

    @pytest.mark.parametrize("explicit_first", [False, True])
    def test_duplicate_resolution_id_prefers_explicit_reason(self, explicit_first):
        generic = CodeReviewIssue(
            id="12524", file="a.py", line=10, severity="INFO",
            category="BUG_RISK", reason="Historical defect.",
            suggestedFixDescription="Already fixed.", isResolved=True,
        )
        explicit = generic.model_copy(update={
            "resolutionReason": "Null guard added.",
        })
        input_items = [explicit, generic] if explicit_first else [generic, explicit]

        active_items, resolved_items = _partition_issue_lifecycle(input_items)

        assert active_items == []
        assert len(resolved_items) == 1
        assert resolved_items[0].resolutionReason == "Null guard added."

    @pytest.mark.parametrize("resolved_first", [False, True])
    def test_cross_batch_dedup_never_discards_matching_resolution(
        self,
        resolved_first,
    ):
        active = CodeReviewIssue(
            file="a.py", line=10, severity="MEDIUM", category="BUG_RISK",
            reason="The same historical root cause.",
            suggestedFixDescription="Fix the remaining defect.",
        )
        resolved = CodeReviewIssue(
            id="12524", file="a.py", line=10, severity="MEDIUM",
            category="BUG_RISK", reason="The same historical root cause.",
            suggestedFixDescription="Already fixed.", isResolved=True,
            resolutionReason="The original defect was fixed.",
        )

        input_items = [resolved, active] if resolved_first else [active, resolved]
        result = _deduplicate_cross_batch_issues_preserving_lifecycle(input_items)

        assert result == [active, resolved]

    @pytest.mark.parametrize("historical_first", [False, True])
    def test_cross_batch_dedup_prefers_protected_open_identity(
        self,
        historical_first,
    ):
        historical = CodeReviewIssue(
            id="12524", file="a.py", line=10, severity="MEDIUM",
            category="BUG_RISK", reason="Null guard is missing.",
            suggestedFixDescription="Add the null guard.",
        )
        fresh_duplicate = CodeReviewIssue(
            file="a.py", line=11, severity="MEDIUM", category="BUG_RISK",
            reason="The null guard is missing.",
            suggestedFixDescription="Add a null guard.",
        )
        input_items = (
            [historical, fresh_duplicate]
            if historical_first else [fresh_duplicate, historical]
        )

        result = _deduplicate_cross_batch_issues_preserving_lifecycle(
            input_items,
            {"12524"},
        )

        assert result == [historical]

    @pytest.mark.parametrize("historical_first", [False, True])
    def test_final_dedup_partition_never_sends_protected_history(
        self,
        historical_first,
    ):
        historical = CodeReviewIssue(
            id="12524", file="a.py", line=10, severity="MEDIUM",
            category="BUG_RISK", reason="Null guard is missing.",
            suggestedFixDescription="Add the null guard.",
        )
        fresh_duplicate = CodeReviewIssue(
            file="a.py", line=11, severity="MEDIUM", category="BUG_RISK",
            reason="The null guard is missing.",
            suggestedFixDescription="Add a null guard.",
        )
        input_items = (
            [historical, fresh_duplicate]
            if historical_first else [fresh_duplicate, historical]
        )

        fresh, protected = _partition_protected_active_issues(
            input_items,
            {"12524"},
        )
        retained_fresh = _suppress_duplicates_of_protected_history(
            fresh,
            protected,
        )

        assert protected == [historical]
        assert retained_fresh == []


class TestSerializeIssueForClient:
    def test_active_issue_does_not_serialize_resolution_metadata(self):
        issue = CodeReviewIssue(
            file="a.py", line=10, severity="MEDIUM", category="BUG_RISK",
            reason="A current defect remains.",
            suggestedFixDescription="Fix the current defect.",
            resolutionReason="Stale lifecycle value.",
            resolutionExplanation="Stale internal value.",
            resolvedInCommit="abc123",
        )

        data = _serialize_issue_for_client(issue)

        assert "resolutionReason" not in data
        assert "resolutionExplanation" not in data
        assert "resolvedInCommit" not in data

    def test_maps_resolution_explanation_to_java_contract(self):
        issue = CodeReviewIssue(
            id="12524", file="a.py", line=10, severity="MEDIUM",
            category="BUG_RISK", reason="Historical defect.",
            suggestedFixDescription="Already fixed.", isResolved=True,
            resolutionExplanation="No actionable post-change defect remains.",
        )

        data = _serialize_issue_for_client(issue)

        assert data["resolutionReason"] == issue.resolutionExplanation
        assert data["resolutionExplanation"] == issue.resolutionExplanation

    def test_prefers_explicit_client_resolution_reason(self):
        issue = CodeReviewIssue(
            id="12524", file="a.py", line=10, severity="INFO",
            category="BUG_RISK", reason="Historical defect.",
            suggestedFixDescription="Already fixed.", isResolved=True,
            resolutionReason="Empty-string default applied.",
            resolutionExplanation="Stale internal explanation.",
        )

        data = _serialize_issue_for_client(issue)

        assert data["resolutionReason"] == "Empty-string default applied."
        assert data["resolutionExplanation"] == "Empty-string default applied."

    def test_blank_client_reason_falls_back_to_internal_explanation(self):
        issue = CodeReviewIssue(
            id="12524", file="a.py", line=10, severity="INFO",
            category="BUG_RISK", reason="Historical defect.",
            suggestedFixDescription="Already fixed.", isResolved=True,
            resolutionReason="   ",
            resolutionExplanation="Legacy explanation retained.",
        )

        data = _serialize_issue_for_client(issue)

        assert data["resolutionReason"] == "Legacy explanation retained."
        assert data["resolutionExplanation"] == "Legacy explanation retained."


class TestApplyStage3Dismissals:
    def test_resolves_historical_open_issue_and_drops_fresh_candidate(self):
        historical = CodeReviewIssue(
            id="12524", file="a.py", line=10, severity="MEDIUM",
            category="BUG_RISK", reason="Historical defect.",
            suggestedFixDescription="Add the missing guard.",
        )
        fresh = CodeReviewIssue(
            id="CROSS_001", file="b.py", line=20, severity="LOW",
            category="BUG_RISK", reason="Fresh false positive.",
            suggestedFixDescription="No longer needed.",
        )
        unaffected = CodeReviewIssue(
            file="c.py", line=30, severity="HIGH", category="BUG_RISK",
            reason="A real current defect remains.",
            suggestedFixDescription="Fix the current defect.",
        )

        retained, resolved_count, dropped_count = _apply_stage_3_dismissals(
            [historical, fresh, unaffected],
            {"12524", "CROSS_001"},
            {"12524"},
        )

        assert retained == [historical, unaffected]
        assert historical.isResolved is True
        assert historical.resolutionReason == (
            "Closed because final verification no longer supports the prior finding."
        )
        assert historical.resolutionExplanation == historical.resolutionReason
        assert resolved_count == 1
        assert dropped_count == 1
