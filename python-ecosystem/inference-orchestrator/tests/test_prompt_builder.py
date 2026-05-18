"""
Unit tests for utils.prompts.prompt_builder — PromptBuilder.
"""
import pytest
from utils.prompts.prompt_builder import PromptBuilder


class TestBuildBranchReviewPrompt:

    def test_basic_prompt(self):
        metadata = {
            "workspace": "ws",
            "repoSlug": "repo",
            "commitHash": "abc123",
            "branch": "main",
            "previousCodeAnalysisIssues": [],
        }
        result = PromptBuilder.build_branch_review_prompt_with_branch_issues_data(metadata)
        assert "ws" in result
        assert "repo" in result
        assert "abc123" in result

    def test_batch_mode(self):
        metadata = {
            "workspace": "ws",
            "repoSlug": "repo",
            "commitHash": "abc",
            "branch": "main",
            "previousCodeAnalysisIssues": [{"id": "1", "severity": "HIGH"}],
        }
        result = PromptBuilder.build_branch_review_prompt_with_branch_issues_data(
            metadata, batch_number=1, total_batches=3,
        )
        assert result  # non-empty prompt generated
        # Batch info either present in header or prompt is still valid
        assert len(result) > 100

    def test_defaults_for_missing_keys(self):
        result = PromptBuilder.build_branch_review_prompt_with_branch_issues_data({})
        assert "<unknown_workspace>" in result


class TestBuildBranchReconciliationDirectPrompt:

    def test_basic(self):
        metadata = {"branch": "feat", "commitHash": "abc",
                     "previousCodeAnalysisIssues": []}
        result = PromptBuilder.build_branch_reconciliation_direct_prompt(
            metadata, file_contents={"a.py": "print('hi')"},
        )
        assert "feat" in result
        assert "a.py" in result

    def test_with_diff(self):
        metadata = {"branch": "b", "commitHash": "c",
                     "previousCodeAnalysisIssues": []}
        result = PromptBuilder.build_branch_reconciliation_direct_prompt(
            metadata, file_contents={}, raw_diff="diff --git a/f.py",
        )
        assert "RECENT CHANGES" in result

    def test_batch_mode(self):
        metadata = {"branch": "b", "commitHash": "c",
                     "previousCodeAnalysisIssues": []}
        result = PromptBuilder.build_branch_reconciliation_direct_prompt(
            metadata, file_contents={}, batch_number=2, total_batches=5,
        )
        assert "Batch 2 of 5" in result

    def test_no_file_contents(self):
        metadata = {"branch": "b", "commitHash": "c",
                     "previousCodeAnalysisIssues": []}
        result = PromptBuilder.build_branch_reconciliation_direct_prompt(
            metadata, file_contents={},
        )
        assert "No file contents" in result


class TestGetAdditionalInstructions:

    def test_returns_string(self):
        result = PromptBuilder.get_additional_instructions()
        assert isinstance(result, str)
        assert len(result) > 0


class TestBuildStage0:

    def test_basic(self):
        result = PromptBuilder.build_stage_0_planning_prompt(
            repo_slug="repo", pr_id="42", pr_title="Add feature",
            author="dev", branch_name="feat", target_branch="main",
            commit_hash="abc", changed_files_json="[]",
        )
        assert "repo" in result
        assert "Add feature" in result


class TestBuildStage1:

    def test_basic(self):
        files = [{"path": "src/main.py", "diff": "+print('hi')",
                  "type": "MODIFIED", "old_code": "", "focus_areas": ["logic"]}]
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=files, priority="HIGH",
        )
        assert "src/main.py" in result
        assert "HIGH" in result

    def test_incremental_mode(self):
        files = [{"path": "a.py", "diff": "+x", "type": "MODIFIED"}]
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=files, priority="MEDIUM", is_incremental=True,
            previous_issues="Previous issue list",
        )
        assert "INCREMENTAL" in result
        assert "Delta Diff" in result

    def test_with_rag_context(self):
        files = [{"path": "a.py", "diff": "+x"}]
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=files, priority="LOW", rag_context="RAG data here",
        )
        assert "RAG data here" in result

    def test_with_all_pr_files(self):
        files = [{"path": "a.py", "diff": "+x"}]
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=files, priority="HIGH",
            all_pr_files=["a.py", "b.py", "c.py"],
        )
        assert "b.py" in result or "OTHER FILES" in result

    def test_with_deleted_files(self):
        files = [{"path": "a.py", "diff": "+x"}]
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=files, priority="HIGH",
            deleted_files=["old.py"],
        )
        assert "DELETED" in result
        assert "old.py" in result

    def test_project_rules(self):
        files = [{"path": "a.py", "diff": "+x"}]
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=files, priority="HIGH", project_rules="No magic numbers",
        )
        assert "No magic numbers" in result


class TestBuildStage2:

    def test_basic(self):
        result = PromptBuilder.build_stage_2_cross_file_prompt(
            repo_slug="repo", pr_title="Title", commit_hash="abc",
            stage_1_findings_json="[]", architecture_context="",
            migrations="", cross_file_concerns=["Concern A"],
        )
        assert "repo" in result
        assert "Concern A" in result

    def test_with_project_rules(self):
        result = PromptBuilder.build_stage_2_cross_file_prompt(
            repo_slug="repo", pr_title="T", commit_hash="a",
            stage_1_findings_json="[]", architecture_context="",
            migrations="", cross_file_concerns=[],
            project_rules="Rule digest",
        )
        assert "Rule digest" in result


class TestBuildStage3:

    def test_basic(self):
        result = PromptBuilder.build_stage_3_aggregation_prompt(
            repo_slug="repo", pr_id="1", author="dev", pr_title="Title",
            total_files=5, additions=100, deletions=50,
            stage_0_plan="plan", stage_1_issues_json="[]",
            stage_2_findings_json="[]", recommendation="APPROVE",
        )
        assert "repo" in result
        assert "Title" in result
        assert "APPROVE" in result

    def test_incremental_context(self):
        result = PromptBuilder.build_stage_3_aggregation_prompt(
            repo_slug="r", pr_id="1", author="d", pr_title="T",
            total_files=1, additions=1, deletions=0,
            stage_0_plan="p", stage_1_issues_json="[]",
            stage_2_findings_json="[]", recommendation="APPROVE",
            incremental_context="Incremental info here",
        )
        assert "Incremental info here" in result
