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
        assert "estimated_issues" not in result

    def test_with_task_context(self):
        result = PromptBuilder.build_stage_0_planning_prompt(
            repo_slug="repo", pr_id="42", pr_title="Add feature",
            author="dev", branch_name="feat/PROJ-1", target_branch="main",
            commit_hash="abc", changed_files_json="[]",
            task_context="### Task: PROJ-1 — Add export",
        )
        assert "PROJ-1" in result
        assert "untrusted business input" in result


class TestBuildStage1:

    def test_basic(self):
        files = [{"path": "src/main.py", "diff": "+print('hi')",
                  "type": "MODIFIED", "current_code": "print('hi')", "focus_areas": ["logic"]}]
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=files, priority="HIGH",
        )
        assert "src/main.py" in result
        assert "HIGH" in result
        assert "Current File Content (post-change" in result
        assert "print('hi')" in result

    def test_stage_1_requires_atomic_root_findings(self):
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=[{"path": "src/main.py", "diff": "+run_cleanup()"}],
            priority="HIGH",
        )

        assert "1 root/issue" in result
        assert '"relatedLocations": ["path/to/other-manifestation:84"]' in result
        assert "Repeats: relatedLocations" in result
        assert 'return `"relatedLocations": []`' in result

    def test_incremental_mode(self):
        files = [{"path": "a.py", "diff": "+x", "type": "MODIFIED"}]
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=files, priority="MEDIUM", is_incremental=True,
            previous_issues="Previous issue list",
        )
        assert "INCREMENTAL" in result
        assert "Delta Diff" in result

    def test_with_structural_context(self):
        files = [{"path": "a.py", "diff": "+x"}]
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=files,
            priority="LOW",
            structural_context="compact relation data",
        )
        assert "compact relation data" in result

    def test_agent_prompt_requires_minimal_proposed_tree_tool_without_preload(self):
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=[{"path": "a.py", "diff": "+x"}],
            priority="HIGH",
            structural_context="compact structural relation map",
            use_mcp_tools=True,
            target_branch="main",
            vcs_workspace="tenant",
            vcs_repo_slug="repository",
        )

        assert "compact structural relation map" not in result
        assert "PRELOADED STRUCTURAL RELATION MAP" not in result
        assert "No structural context is preloaded in agentic mode" in result
        assert "Graph use is optional" not in result
        assert "structural-context section above states" in result
        assert "begin with the required minimal-context call" in result
        assert '["a.py"]' in result
        assert "Findings based only on source reads leave" in result
        assert "never invent or transform an Evidence ID" in result
        assert "searchRepositoryCode" not in result
        assert "getStructuralRelations" not in result
        assert "getMinimalReviewContext(question, focusSymbols?, maxRelations?" in result
        assert "getImpactRadius(targets?, maxDepth?, maxResults?" in result
        assert "queryCodeGraph(pattern, target, maxResults?" in result
        assert "traverseCodeGraph(start, direction?, strategy?" in result
        assert "getStructuralUnit(unitId, offset?, maxCharacters?)" in result
        assert "`tokenBudget` accepts 512–16,000" in result
        assert "If `status=ambiguous`" in result
        assert "When `sourceEvidence=true`" in result
        assert (
            "exploreReviewContext(question, focusSymbols?, maxRelations?"
            in result
        )
        assert "host supplies the exact batch paths" in result
        assert "sealed, selection-matched proposed-tree generation" in result
        assert "modified/added units use proposed bytes" in result
        assert "deleted units are absent" in result
        assert "unchanged related units use pinned target bytes" in result
        assert "Finish the complete review" in result
        assert "exactly one review object" in result
        assert "Required first call" in result
        assert "Current File Content are the post-change authority" in result
        assert (
            "getBranchFileContent(workspace, repoSlug, branch, filePath, startLine?,"
            in result
        )
        assert "TARGET BRANCH/REVISION REF: main" in result
        assert "VCS WORKSPACE: tenant" in result
        assert "VCS REPOSITORY (repoSlug/projectKey): repository" in result

    def test_agent_prompt_without_structural_tools_is_vcs_only(self):
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=[{"path": "a.py", "diff": "+x"}],
            priority="HIGH",
            structural_context="structural context must not leak",
            use_mcp_tools=True,
            structural_tools_available=False,
            target_branch="main",
            vcs_workspace="tenant",
            vcs_repo_slug="repository",
        )

        assert "## Repository File Tool" in result
        assert "getBranchFileContent" in result
        assert "PRELOADED STRUCTURAL RELATION MAP" not in result
        assert "structural context must not leak" not in result
        assert "getStructuralRelations" not in result
        assert "queryCodeGraph" not in result
        assert "getStructuralUnit" not in result
        assert "exploreReviewContext" not in result
        assert "No structural relation metadata or graph tools" in result
        assert "getReviewFileContent" not in result
        assert "target-head-only source" not in result

    def test_agent_prompt_uses_review_tree_for_changed_paths(self):
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=[{"path": "src/current.py", "diff": "+x"}],
            priority="HIGH",
            all_pr_files=["src/current.py", "src/other_batch.py"],
            deleted_files=["src/deleted.py"],
            use_mcp_tools=True,
            review_file_tool_available=True,
            target_branch="target-head-sha",
            vcs_workspace="tenant",
            vcs_repo_slug="repository",
        )

        assert (
            "getReviewFileContent(workspace, repoSlug, filePath, startLine?, "
            "endLine?)" in result
        )
        assert "including a path assigned to another Stage 1 batch" in result
        assert "Use getReviewFileContent for code unrepresented by the graph" in result
        assert "smallest useful\nline range by default" in result
        assert "needed whole-file bytes" not in result
        assert "getBranchFileContent(" not in result
        assert "reports deleted paths as absent" in result
        assert "modified/added units use proposed bytes" in result
        assert "deleted units are absent" in result
        assert "exact source and tests" in result
        assert "win whenever they disagree" in result
        assert "exact proposed-tree source are post-change authority" in result
        assert "unchanged paths from the pinned target-head snapshot" in result

    def test_with_all_pr_files(self):
        files = [{"path": "a.py", "diff": "+x"}]
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=files, priority="HIGH",
            all_pr_files=["a.py", "b.py", "c.py"],
        )
        assert "b.py" in result or "OTHER FILES" in result

    def test_other_pr_file_scaffold_is_bounded(self):
        files = [{"path": "current.py", "diff": "+x"}]
        other_files = [f"peer-{index:03d}.py" for index in range(50)]

        result = PromptBuilder.build_stage_1_batch_prompt(
            files=files,
            priority="HIGH",
            all_pr_files=["current.py", *other_files],
        )

        assert "peer-019.py" in result
        assert "peer-020.py" not in result
        assert "... and 30 more files" in result

    def test_with_deleted_files(self):
        files = [{"path": "a.py", "diff": "+x"}]
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=files, priority="HIGH",
            deleted_files=["old.py"],
        )
        assert "DELETED" in result
        assert "old.py" in result

    def test_deleted_file_scaffold_is_bounded(self):
        deleted = [f"deleted-{index:03d}.py" for index in range(60)]

        result = PromptBuilder.build_stage_1_batch_prompt(
            files=[{"path": "a.py", "diff": "+x"}],
            priority="HIGH",
            deleted_files=deleted,
        )

        assert "deleted-029.py" in result
        assert "deleted-030.py" not in result
        assert "... and 30 more" in result

    def test_project_rules(self):
        files = [{"path": "a.py", "diff": "+x"}]
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=files, priority="HIGH", project_rules="No magic numbers",
        )
        assert "No magic numbers" in result

    def test_with_task_context_guardrails(self):
        files = [{"path": "a.py", "diff": "+x"}]
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=files,
            priority="HIGH",
            task_context="### Task: PROJ-1 — Add export",
        )
        assert "PROJ-1" in result
        assert "Stage 2/Stage 3" in result
        assert "missing requirement" in result

    def test_stage_1_suppresses_pre_change_bug_already_fixed_by_diff(self):
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=[{
                "path": "Shipping/MethodList.php",
                "current_code": "$resultMethod = '';",
                "diff": "-$resultMethod = null;\n+$resultMethod = '';",
            }],
            priority="HIGH",
            task_context="MID-55: checkout crashes when no shipping methods exist",
        )

        assert "must still exist in the post-change source" in result
        assert "pre-change defect" in result
        assert "do not report that" in result
        assert "fix as an issue" in result
        assert "must not say that the current diff already fixes" in result
        assert "removed code alone does not qualify" in result
        assert "only to an issue explicitly supplied" in result
        assert "CURRENT-DEFECT CONTRACT FOR NEW FINDINGS" in result
        assert "sole exception" in result
        assert "historical codeSnippet" in result
        assert "exempt from current-source snippet matching" in result
        assert '"resolutionReason": null' in result
        assert "INFO: do not create an issue" in result
        assert "never create a new" in result
        assert "informational issue" in result
        assert '"claimKind"' in result
        assert "exact plugin evidence class" in result
        assert "leave `claimKind` empty even when" in result
        assert "Anchor every new finding" in result
        assert "reviewable changed hunk" in result
        assert "not a separate PR finding" in result

    def test_stage_1_requires_state_transition_reasoning(self):
        result = PromptBuilder.build_stage_1_batch_prompt(
            files=[{
                "path": "cache.py",
                "current_code": "cache = load()",
                "diff": "+with lock:\n+    cache = load()",
            }],
            priority="HIGH",
        )

        assert "STATEFUL / CONCURRENT CHANGE CHECK" in result
        assert "caller succeeds" in result
        assert "caller that was already waiting later fails" in result
        assert "lost update" in result
        assert "lock coverage alone" in result

class TestBuildStage2:

    def test_basic(self):
        result = PromptBuilder.build_stage_2_cross_file_prompt(
            repo_slug="repo", pr_title="Title", commit_hash="abc",
            stage_1_findings_json="[]", architecture_context="",
            migrations="", cross_file_concerns=["Concern A"],
        )
        assert "repo" in result
        assert "Concern A" in result
        assert '"claimKind"' in result
        assert "plugin-governed relationship claim using an exact evidence class" in result
        assert "exact bracketed fact kind" in result
        assert "reviewable PR diff hunk" in result
        assert "cannot be the annotation anchor" in result

    def test_with_project_rules(self):
        result = PromptBuilder.build_stage_2_cross_file_prompt(
            repo_slug="repo", pr_title="T", commit_hash="a",
            stage_1_findings_json="[]", architecture_context="",
            migrations="", cross_file_concerns=[],
            project_rules="Rule digest",
        )
        assert "Rule digest" in result

    def test_with_task_context_and_pr_change_summary(self):
        result = PromptBuilder.build_stage_2_cross_file_prompt(
            repo_slug="repo", pr_title="T", commit_hash="a",
            stage_1_findings_json="[]", architecture_context="",
            migrations="", cross_file_concerns=[],
            task_context="### Task: PROJ-2 — Add billing flow",
            pr_change_summary="- src/Billing.py (+10/-2)",
        )
        assert "PROJ-2" in result
        assert "src/Billing.py" in result
        assert "TASK-COVERAGE" in result

    def test_with_task_history_context(self):
        result = PromptBuilder.build_stage_2_cross_file_prompt(
            repo_slug="repo", pr_title="T", commit_hash="a",
            stage_1_findings_json="[]", architecture_context="",
            migrations="", cross_file_concerns=[],
            task_context="### Task: PROJ-3 - Reopened checkout",
            task_history_context="PR #41 (MERGED) covered checkout discounts",
        )
        assert "Prior Task History Context" in result
        assert "PR #41 (MERGED)" in result
        assert "already covered by a merged prior PR" in result

    def test_stage_2_does_not_report_valid_fix_strategies_as_inconsistency(self):
        result = PromptBuilder.build_stage_2_cross_file_prompt(
            repo_slug="repo",
            pr_title="Fix checkout null handling",
            commit_hash="abc",
            stage_1_findings_json="[]",
            architecture_context="",
            migrations="",
            cross_file_concerns=["Compare shipping and ApplePay null handling"],
            task_context="MID-55: checkout crashes after cart manipulation",
            pr_change_summary=(
                "- Shipping/MethodList.php\n"
                "  -$resultMethod = null;\n"
                "  +$resultMethod = '';\n"
                "- fix-applepay-country-id-null.patch\n"
                "  +$countryId = (string) $address->getCountryId();"
            ),
        )

        assert "must describe a concrete defect that remains" in result
        assert "baseline bug described by the task/PR" in result
        assert "Different valid implementation techniques are not" in result
        assert "hypotheses, not findings" in result
        assert "speculate that similar code" in result
        assert "already-applied fixes belong" in result
        assert "Different styles or" in result
        assert "DATA_INTEGRITY" not in result
        assert "BUSINESS_LOGIC" not in result
        assert "LOW, MEDIUM, or HIGH cross_file_issue" in result
        assert "CRITICAL cross_file_issue exists" in result
        assert "no cross_file_issues" in result


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

    def test_with_task_context(self):
        result = PromptBuilder.build_stage_3_aggregation_prompt(
            repo_slug="r", pr_id="1", author="d", pr_title="T",
            total_files=1, additions=1, deletions=0,
            stage_0_plan="p", stage_1_issues_json="[]",
            stage_2_findings_json="[]", recommendation="APPROVE",
            task_context="### Task: PROJ-3 — Improve checkout",
        )
        assert "PROJ-3" in result
        assert "task-coverage" in result

    def test_mcp_verification_uses_reviewed_revision_and_verification_ids(self):
        result = PromptBuilder.build_stage_3_aggregation_prompt(
            repo_slug="r", pr_id="7", author="d", pr_title="T",
            total_files=1, additions=1, deletions=0,
            stage_0_plan="p", stage_1_issues_json="[]",
            stage_2_findings_json="[]", recommendation="APPROVE",
            use_mcp_tools=True, review_revision="commit-abc",
        )

        assert "REVIEWED REVISION: commit-abc" in result
        assert "Verification ID" in result
        assert 'DISMISSED_ISSUES: ["issue_0", "issue_3"]' in result

    def test_local_only_mcp_verification_never_advertises_provider_tools(self):
        result = PromptBuilder.build_stage_3_aggregation_prompt(
            repo_slug="r", pr_id="7", author="d", pr_title="T",
            total_files=1, additions=1, deletions=0,
            stage_0_plan="p", stage_1_issues_json="[]",
            stage_2_findings_json="[]", recommendation="APPROVE",
            use_mcp_tools=True,
            review_revision="commit-abc",
            mcp_local_only=True,
        )

        assert "getReviewFileContent" in result
        assert "getPullRequestComments" not in result
        assert "getBranchFileContent" not in result
