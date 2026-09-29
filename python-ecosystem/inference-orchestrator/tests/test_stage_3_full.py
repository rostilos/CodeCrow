"""Tests for stage_3_aggregation: summarizers, dismissed issues, MCP stage 3."""
import json
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch
from model.output_schemas import CodeReviewIssue
from model.multi_stage import FileGroup, FileToSkip, ReviewFile, ReviewPlan
from service.review.orchestrator import stage_3_aggregation as stage_3_module
from service.review.orchestrator.stage_3_aggregation import (
    execute_stage_3_aggregation,
    _summarize_issues_for_stage_3,
    _summarize_plan_for_stage_3,
    _extract_dismissed_issues,
    _stage_3_with_mcp,
)


# ── _summarize_issues_for_stage_3 ─────────────────────────────


class TestSummarizeIssuesStage3:
    def test_empty_issues(self):
        result = _summarize_issues_for_stage_3([])
        assert "No issues found" in result

    def test_severity_counts(self):
        issues = []
        for sev in ["HIGH", "HIGH", "MEDIUM", "LOW"]:
            issue = MagicMock()
            issue.severity = sev
            issue.category = "BUG_RISK"
            issue.id = f"id-{sev}"
            issue.title = f"Title {sev}"
            issue.file = "a.py"
            issue.reason = "Some reason text here"
            issues.append(issue)
        result = _summarize_issues_for_stage_3(issues)
        assert "Total issues: 4" in result
        assert "HIGH: 2" in result
        assert "MEDIUM: 1" in result

    def test_complete_records_keep_stable_verification_identity(self):
        critical = MagicMock()
        critical.severity = "CRITICAL"
        critical.category = "SECURITY"
        critical.id = "c1"
        critical.title = "Critical Bug"
        critical.file = "main.py"
        critical.reason = "SQL injection"

        low = MagicMock()
        low.severity = "LOW"
        low.category = "STYLE"
        low.id = "l1"
        low.title = "Style issue"
        low.file = "utils.py"
        low.reason = "Naming convention"

        result = _summarize_issues_for_stage_3([low, critical])
        records = json.loads(result.split("Complete verification records (JSON):\n", 1)[1])
        assert records[0]["verification_id"] == "issue_0"
        assert records[0]["original_id"] == "l1"
        assert records[1]["verification_id"] == "issue_1"
        assert records[1]["original_id"] == "c1"

    def test_complete_records_preserve_every_issue_field(self):
        issue = CodeReviewIssue(
            id="db-complete",
            severity="HIGH",
            category="ARCHITECTURE",
            file="src/service.py",
            line=37,
            scope="FUNCTION",
            title="Dependency contract is broken",
            reason="Concrete relationship evidence and impact.",
            suggestedFixDescription="Restore the dependency contract.",
            suggestedFixDiff=(
                "--- a/src/service.py\n+++ b/src/service.py\n"
                "@@ -37 +37 @@\n-old\n+new"
            ),
            isResolved=False,
            resolutionReason="legacy-compatible value",
            resolutionExplanation="legacy-compatible explanation",
            resolvedInCommit="legacy-commit",
            visibility="workspace",
            codeSnippet="dependency.call()",
            evidenceRefs=["RAG-dependency", "RAG-contract"],
            claimKind="python-dependency-contract",
            relatedLocations=["src/consumer.py:81"],
        )

        result = _summarize_issues_for_stage_3([issue])
        [record] = json.loads(
            result.split("Complete verification records (JSON):\n", 1)[1]
        )

        assert set(CodeReviewIssue.model_fields).issubset(record)
        assert record["scope"] == "FUNCTION"
        assert record["suggestedFixDescription"] == (
            "Restore the dependency contract."
        )
        assert record["suggestedFixDiff"].endswith("-old\n+new")
        assert record["evidenceRefs"] == [
            "RAG-dependency",
            "RAG-contract",
        ]
        assert record["claimKind"] == "python-dependency-contract"
        assert record["codeSnippet"] == "dependency.call()"
        assert record["relatedLocations"] == ["src/consumer.py:81"]

    def test_all_issue_ids_listed(self):
        issues = []
        for i in range(3):
            issue = MagicMock()
            issue.severity = "MEDIUM"
            issue.category = "BUG_RISK"
            issue.id = f"issue-{i}"
            issue.title = ""
            issue.file = "a.py"
            issue.reason = "Reason"
            issues.append(issue)
        result = _summarize_issues_for_stage_3(issues)
        assert "issue-0" in result
        assert "issue-1" in result
        assert "issue-2" in result

    def test_issue_without_title(self):
        issue = MagicMock()
        issue.severity = "HIGH"
        issue.category = "BUG_RISK"
        issue.id = "no-title"
        issue.title = ""
        issue.file = "a.py"
        issue.reason = "Missing import causes failure"
        result = _summarize_issues_for_stage_3([issue])
        assert "no-title" in result

    def test_issue_without_id(self):
        issue = MagicMock()
        issue.severity = "HIGH"
        issue.category = "BUG_RISK"
        issue.id = ""
        issue.title = "Some title"
        issue.file = "a.py"
        issue.reason = "Reason here"
        result = _summarize_issues_for_stage_3([issue])
        assert "Total issues: 1" in result


# ── _summarize_plan_for_stage_3 ───────────────────────────────


class TestSummarizePlanStage3:
    def test_basic_plan(self):
        plan = MagicMock()
        group = MagicMock()
        f1 = MagicMock()
        f1.path = "a.py"
        group.files = [f1]
        group.priority = "HIGH"
        plan.file_groups = [group]
        plan.cross_file_concerns = []
        result = _summarize_plan_for_stage_3(plan)
        assert "Total files planned" in result
        assert "HIGH: 1" in result

    def test_cross_file_concerns(self):
        plan = MagicMock()
        group = MagicMock()
        group.files = []
        group.priority = "MEDIUM"
        plan.file_groups = [group]
        plan.cross_file_concerns = ["Concern A", "Concern B"]
        result = _summarize_plan_for_stage_3(plan)
        assert "Concern A" in result
        assert "Concern B" in result

    def test_many_files_are_all_preserved(self):
        plan = MagicMock()
        group = MagicMock()
        files = [MagicMock(path=f"file_{i}.py") for i in range(25)]
        group.files = files
        group.priority = "LOW"
        plan.file_groups = [group]
        plan.cross_file_concerns = []
        result = _summarize_plan_for_stage_3(plan)
        assert "file_0.py" in result
        assert "file_24.py" in result
        assert "... and" not in result

    def test_complete_and_semantic_plan_records_preserve_every_field(self):
        plan = ReviewPlan(
            analysis_summary="PLAN_ANALYSIS_MARKER",
            file_groups=[FileGroup(
                group_id="PLAN_GROUP_MARKER",
                priority="CRITICAL",
                rationale="PLAN_RATIONALE_MARKER",
                files=[ReviewFile(
                    path="src/PLAN_PATH_MARKER.py",
                    focus_areas=["PLAN_FOCUS_MARKER", "SECURITY"],
                    risk_level="HIGH",
                )],
            )],
            files_to_skip=[FileToSkip(
                path="generated/PLAN_SKIP_PATH_MARKER.py",
                reason="PLAN_SKIP_REASON_MARKER",
            )],
            cross_file_concerns=["PLAN_CONCERN_COMPLETE_MARKER"],
        )

        complete = _summarize_plan_for_stage_3(plan)
        complete_payload = json.loads(
            complete.split("Complete ReviewPlan record (JSON):\n", 1)[1]
        )
        assert complete_payload == plan.model_dump(mode="json")

        records = stage_3_module._plan_semantic_records(plan)
        serialized = json.dumps(
            [record.value for record in records],
            ensure_ascii=False,
        )
        for marker in (
            "PLAN_ANALYSIS_MARKER",
            "PLAN_GROUP_MARKER",
            "PLAN_RATIONALE_MARKER",
            "PLAN_PATH_MARKER",
            "PLAN_FOCUS_MARKER",
            "PLAN_SKIP_PATH_MARKER",
            "PLAN_SKIP_REASON_MARKER",
            "PLAN_CONCERN_COMPLETE_MARKER",
        ):
            assert serialized.count(marker) == 1
        assert {record.key for record in records} == {
            "plan:analysis_summary",
            "plan:group:000000",
            "plan:group:000000:file:000000",
            "plan:skip:000000",
            "plan:concern:000000",
        }


# ── _extract_dismissed_issues ─────────────────────────────────


class TestExtractDismissedIssues:
    def test_no_marker(self):
        content = "Just a report"
        report, dismissed = _extract_dismissed_issues(content)
        assert report == content
        assert dismissed == []

    def test_extracts_ids(self):
        content = 'Report text\n<!-- DISMISSED_ISSUES: ["id1", "id2"] -->\nMore'
        report, dismissed = _extract_dismissed_issues(content)
        assert "id1" in dismissed
        assert "id2" in dismissed
        assert "DISMISSED_ISSUES" not in report

    def test_malformed_json(self):
        content = '<!-- DISMISSED_ISSUES: not_json -->'
        report, dismissed = _extract_dismissed_issues(content)
        assert dismissed == []

    def test_non_list_value(self):
        content = '<!-- DISMISSED_ISSUES: {"key": "val"} -->'
        report, dismissed = _extract_dismissed_issues(content)
        assert dismissed == []

    def test_empty_list(self):
        content = 'Hello\n<!-- DISMISSED_ISSUES: [] -->\nEnd'
        report, dismissed = _extract_dismissed_issues(content)
        assert dismissed == []


# ── execute_stage_3_aggregation ───────────────────────────────


class TestExecuteStage3Aggregation:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_basic_no_mcp(self):
        llm = MagicMock()
        resp = MagicMock()
        resp.content = "Final report: all good"
        llm.ainvoke = AsyncMock(return_value=resp)

        request = MagicMock()
        request.projectVcsRepoSlug = "repo"
        request.pullRequestId = 42
        request.prAuthor = "dev"
        request.prTitle = "Fix stuff"
        request.changedFiles = ["a.py"]
        request.targetBranchName = "main"
        request.previousCodeAnalysisIssues = []
        request.currentCommitHash = None
        request.commitHash = None
        request.taskContext = None

        plan = MagicMock()
        plan.file_groups = []
        plan.cross_file_concerns = []

        stage_2 = MagicMock()
        stage_2.model_dump_json.return_value = "{}"
        stage_2.pr_recommendation = "APPROVE"

        result = await execute_stage_3_aggregation(
            llm, request, plan, [], stage_2
        )
        assert "report" in result
        assert result["dismissed_issue_ids"] == []
        llm.ainvoke.assert_awaited_once()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_incremental_review_context(self):
        llm = MagicMock()
        resp = MagicMock()
        resp.content = "Incremental report"
        llm.ainvoke = AsyncMock(return_value=resp)

        request = MagicMock()
        request.projectVcsRepoSlug = "repo"
        request.pullRequestId = 1
        request.prAuthor = "dev"
        request.prTitle = "Update"
        request.changedFiles = []
        request.targetBranchName = ""
        request.previousCodeAnalysisIssues = ["prev1", "prev2"]
        request.currentCommitHash = None
        request.commitHash = None
        request.taskContext = None

        plan = MagicMock()
        plan.file_groups = []
        plan.cross_file_concerns = []

        stage_2 = MagicMock()
        stage_2.model_dump_json.return_value = "{}"
        stage_2.pr_recommendation = "REQUEST_CHANGES"

        # No processed_diff
        result = await execute_stage_3_aggregation(
            llm, request, plan, [], stage_2, is_incremental=True
        )
        assert "report" in result

    @pytest.mark.asyncio(loop_scope="function")
    async def test_mcp_stage_dispatches(self):
        """An immutable reviewed commit enables the MCP verification loop."""
        llm = MagicMock()
        mcp_client = MagicMock()

        request = MagicMock()
        request.projectVcsRepoSlug = "repo"
        request.pullRequestId = 1
        request.prAuthor = "dev"
        request.prTitle = "Fix"
        request.changedFiles = []
        request.targetBranchName = "main"
        request.previousCodeAnalysisIssues = []
        request.currentCommitHash = "abc123"
        request.commitHash = None
        request.taskContext = None

        plan = MagicMock()
        plan.file_groups = []
        plan.cross_file_concerns = []

        stage_2 = MagicMock()
        stage_2.model_dump_json.return_value = "{}"
        stage_2.pr_recommendation = "APPROVE"

        with patch("service.review.orchestrator.stage_3_aggregation._stage_3_with_mcp") as mock_mcp:
            mock_mcp.return_value = {"report": "mcp report", "dismissed_issue_ids": ["x"]}
            result = await execute_stage_3_aggregation(
                llm, request, plan, [], stage_2,
                mcp_client=mcp_client, use_mcp_tools=True,
            )
            mock_mcp.assert_called_once()
            assert result["dismissed_issue_ids"] == ["x"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_small_profile_admits_one_prioritized_semantic_shard(
        self,
        monkeypatch,
    ):
        token_target = 2_500
        monkeypatch.setattr(
            stage_3_module,
            "STAGE3_INPUT_TOKEN_TARGET",
            token_target,
        )
        issues = [
            CodeReviewIssue(
                id=f"db-{index}",
                file=f"src/ISSUE_FILE_{index}.py",
                line=index + 1,
                severity="HIGH" if index == 0 else "MEDIUM",
                category="BUG_RISK",
                title=f"Issue {index}",
                reason=f"ISSUE_MARKER_{index} " + ("reason " * 260),
                suggestedFixDescription="Fix it.",
            )
            for index in range(4)
        ]
        stage_2_payload = {
            "pr_risk_level": "HIGH",
            "cross_file_issues": [
                {
                    "id": f"cross-{index}",
                    "primary_file": f"src/ISSUE_FILE_{index}.py",
                    "affected_files": [f"src/ISSUE_FILE_{index}.py"],
                    "description": (
                        f"STAGE2_MARKER_{index} " + ("evidence " * 220)
                    ),
                }
                for index in range(4)
            ],
            "pr_recommendation": "REQUEST_CHANGES",
            "confidence": "HIGH",
        }
        stage_2 = SimpleNamespace(
            model_dump_json=lambda indent=2: json.dumps(
                stage_2_payload,
                indent=indent,
            ),
            pr_recommendation="REQUEST_CHANGES",
        )
        plan_file = SimpleNamespace(
            path="src/PLAN_FILE_MARKER.py",
            focus_areas=["PLAN_FOCUS_SHARD_MARKER"],
            risk_level="PLAN_RISK_SHARD_MARKER",
        )
        plan_group = SimpleNamespace(
            files=[plan_file],
            priority="HIGH",
            group_id="PLAN_GROUP_SHARD_MARKER",
            rationale="PLAN_RATIONALE_SHARD_MARKER",
        )
        plan = SimpleNamespace(
            analysis_summary="PLAN_ANALYSIS_SHARD_MARKER",
            file_groups=[plan_group],
            files_to_skip=[SimpleNamespace(
                path="generated/PLAN_SKIP_SHARD_MARKER.py",
                reason="PLAN_SKIP_REASON_SHARD_MARKER",
            )],
            cross_file_concerns=["PLAN_CONCERN_MARKER"],
        )
        request = SimpleNamespace(
            projectVcsRepoSlug="repo",
            pullRequestId=42,
            prAuthor="dev",
            prTitle="Large aggregation",
            changedFiles=["src/a.py"],
            previousCodeAnalysisIssues=[],
            currentCommitHash=None,
            commitHash=None,
            taskContext={
                "task_key": "TASK-42",
                "description": "TASK_CONTEXT_MARKER\nImplementation detail.",
            },
        )
        prompts = []

        async def invoke(prompt, **kwargs):
            prompts.append(prompt)
            forbidden = {
                "max_tokens",
                "max_completion_tokens",
                "max_output_tokens",
                "max_new_tokens",
            }
            assert forbidden.isdisjoint(kwargs)
            return SimpleNamespace(content=f"report-{len(prompts)}")

        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=invoke)

        result = await execute_stage_3_aggregation(
            llm,
            request,
            plan,
            issues,
            stage_2,
        )

        assert len(prompts) == 1
        assert all(
            stage_3_module._estimated_prompt_tokens(prompt) <= token_target
            for prompt in prompts
        )
        provenance = result["stage_3_prompt_provenance"]
        assert provenance["synthesisShards"] == []
        assert len(provenance["inputShards"]) == 1
        admitted = provenance["inputShards"][0]
        assert admitted["recordKeys"] == ["stage_1:issue_0"]
        assert admitted["verificationIds"] == ["issue_0"]
        assert admitted["omittedShardCount"] == 7
        assert admitted["omittedRecordCount"] == 20
        assert admitted["omittedVerificationCount"] == 3
        assert "CodeCrow Stage 3 child invocation ceiling reached" in prompts[0]
        assert result["report"] == "report-1"
        assert all(
            shard["promptSha256"].startswith("sha256:")
            and len(shard["promptSha256"]) == 71
            for shard in provenance["inputShards"]
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_sharded_mcp_results_union_validated_dismissals(self):
        first = CodeReviewIssue(
            id="stored-a",
            file="src/a.py",
            line=10,
            severity="HIGH",
            category="BUG_RISK",
            reason="First issue.",
            suggestedFixDescription="Fix A.",
        )
        second = CodeReviewIssue(
            id="stored-b",
            file="src/b.py",
            line=20,
            severity="MEDIUM",
            category="BUG_RISK",
            reason="Second issue.",
            suggestedFixDescription="Fix B.",
        )
        shards = [
            stage_3_module._Stage3PromptShard(
                prompt="prompt-a",
                record_keys=("stage_1:issue_0",),
                verification_ids=("issue_0",),
                use_mcp_tools=True,
            ),
            stage_3_module._Stage3PromptShard(
                prompt="prompt-b",
                record_keys=("stage_1:issue_1",),
                verification_ids=("issue_1",),
                use_mcp_tools=True,
            ),
        ]
        request = SimpleNamespace(
            projectVcsRepoSlug="repo",
            projectVcsWorkspace="workspace",
            pullRequestId=7,
            prAuthor="dev",
            prTitle="MCP shards",
            changedFiles=["src/a.py", "src/b.py"],
            previousCodeAnalysisIssues=[],
            currentCommitHash="commit-abc",
            commitHash=None,
            taskContext=None,
        )
        plan = SimpleNamespace(file_groups=[], cross_file_concerns=[])
        stage_2 = SimpleNamespace(
            model_dump_json=lambda indent=2: "{}",
            pr_recommendation="REQUEST_CHANGES",
        )
        mcp_results = [
            {
                "report": "report-a",
                "dismissed_issue_ids": ["stored-a"],
                "dismissed_issue_keys": ["issue_0"],
                "dismissed_issue_object_ids": [id(first)],
            },
            {
                "report": "report-b",
                "dismissed_issue_ids": ["stored-b", "stored-a"],
                "dismissed_issue_keys": ["issue_1", "issue_0"],
                "dismissed_issue_object_ids": [id(second), id(first)],
            },
        ]
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=SimpleNamespace(
            content="integrated-report"
        ))

        with (
            patch.object(
                stage_3_module,
                "_build_stage_3_prompt_shards",
                return_value=shards,
            ),
            patch.object(
                stage_3_module,
                "_stage_3_with_mcp",
                new=AsyncMock(side_effect=mcp_results),
            ) as mcp_call,
        ):
            result = await execute_stage_3_aggregation(
                llm,
                request,
                plan,
                [first, second],
                stage_2,
                mcp_client=MagicMock(),
                use_mcp_tools=True,
            )

        assert mcp_call.await_count == 2
        assert list(mcp_call.await_args_list[0].args[-1]) == ["issue_0"]
        assert list(mcp_call.await_args_list[1].args[-1]) == ["issue_1"]
        assert result["dismissed_issue_ids"] == ["stored-a", "stored-b"]
        assert result["dismissed_issue_keys"] == ["issue_0", "issue_1"]
        assert result["dismissed_issue_object_ids"] == [id(first), id(second)]
        assert result["report"] == "integrated-report"
        llm.ainvoke.assert_awaited_once()
        synthesis_prompt = llm.ainvoke.await_args.args[0]
        assert "report-a" in synthesis_prompt
        assert "report-b" in synthesis_prompt

    def test_request_context_hint_and_utf8_tool_schema_estimation(self):
        assert stage_3_module._stage_3_input_token_target(
            SimpleNamespace(maxAllowedTokens=50_000)
        ) == 30_000
        assert stage_3_module._stage_3_input_token_target(
            SimpleNamespace(maxAllowedTokens=None)
        ) == stage_3_module.STAGE3_INPUT_TOKEN_TARGET
        assert stage_3_module._stage_3_input_token_target(
            SimpleNamespace(maxAllowedTokens=10_000)
        ) == 5_000

        unicode_prompt = "🙂" * 4
        plain = stage_3_module._estimated_prompt_tokens(unicode_prompt)
        with_tools = stage_3_module._estimated_prompt_tokens(
            unicode_prompt,
            use_mcp_tools=True,
        )
        assert plain == 4 + stage_3_module._STAGE3_ESTIMATOR_SAFETY_TOKENS
        assert with_tools == stage_3_module._estimated_stage_3_messages_tokens(
            [{"role": "user", "content": unicode_prompt}],
            use_mcp_tools=True,
        )

    def test_large_one_line_unicode_task_has_bounded_partial_coverage(self):
        token_target = 2_500
        original = ("🙂 semantic-context βeta " * 2_000) + "終"
        context = stage_3_module._Stage3PromptContext(
            repo_slug="repo",
            pr_id="10",
            author="dev",
            pr_title="Large task",
            total_files=0,
            additions=0,
            deletions=0,
            recommendation="APPROVE",
            incremental_context="",
            use_mcp_tools=False,
            review_revision="",
            issue_inventory="Global active issue count: 0",
        )
        plan = SimpleNamespace(file_groups=[], cross_file_concerns=[])

        shards = stage_3_module._build_stage_3_prompt_shards(
            context=context,
            complete_plan_summary="No planned files.",
            complete_stage_1_json="No issues found in Stage 1.",
            complete_stage_2_json="{}",
            complete_task_context=original,
            plan=plan,
            issue_by_verification_id={},
            token_budget=token_target,
        )

        assert len(shards) == 4
        assert all(
            stage_3_module._estimated_prompt_tokens(shard.prompt)
            <= token_target
            for shard in shards
        )
        needle = (
            "Complete task-context records assigned to this shard (JSON):\n"
        )
        segments = []
        record_keys = []
        decoder = json.JSONDecoder()
        for shard in shards:
            if needle not in shard.prompt:
                continue
            payload, _ = decoder.raw_decode(
                shard.prompt.split(needle, 1)[1]
            )
            for item in payload:
                if not item["record_key"].startswith("task:"):
                    continue
                record_keys.append(item["record_key"])
                value = item["value"]
                segments.append((
                    value["textSequence"]["characterStart"],
                    value["text"],
                    value["textSequence"],
                ))

        assert len(segments) > 1
        assert len(record_keys) == len(set(record_keys))
        segments.sort(key=lambda item: item[0])
        admitted_text = "".join(item[1] for item in segments)
        assert original.startswith(admitted_text)
        assert len(admitted_text) < len(original)
        assert [item[2]["segmentIndex"] for item in segments] == list(
            range(len(segments))
        )
        segment_counts = {item[2]["segmentCount"] for item in segments}
        assert len(segment_counts) == 1
        assert next(iter(segment_counts)) > len(segments)
        assert shards[-1].omitted_shard_count == 16
        assert shards[-1].omitted_record_count == 17
        assert "CodeCrow Stage 3 child invocation ceiling reached" in (
            shards[-1].prompt
        )

    def test_large_malformed_stage_2_text_has_bounded_partial_coverage(self):
        token_target = 2_500
        original = ("RAW_STAGE2🙂 relationship evidence " * 1_500) + "{"
        context = stage_3_module._Stage3PromptContext(
            repo_slug="repo",
            pr_id="11",
            author="dev",
            pr_title="Malformed Stage 2",
            total_files=0,
            additions=0,
            deletions=0,
            recommendation="REQUEST_CHANGES",
            incremental_context="",
            use_mcp_tools=False,
            review_revision="",
            issue_inventory="Global active issue count: 0",
        )
        shards = stage_3_module._build_stage_3_prompt_shards(
            context=context,
            complete_plan_summary="No planned files.",
            complete_stage_1_json="No issues found in Stage 1.",
            complete_stage_2_json=original,
            complete_task_context="No task context available.",
            plan=SimpleNamespace(file_groups=[], cross_file_concerns=[]),
            issue_by_verification_id={},
            token_budget=token_target,
        )

        assert len(shards) == 4
        assert all(
            stage_3_module._estimated_prompt_tokens(shard.prompt)
            <= token_target
            for shard in shards
        )
        needle = "Complete Stage 2 records assigned to this shard (JSON):\n"
        decoder = json.JSONDecoder()
        segments = []
        for shard in shards:
            if needle not in shard.prompt:
                continue
            payload, _ = decoder.raw_decode(
                shard.prompt.split(needle, 1)[1]
            )
            for item in payload:
                value = item["value"]
                if "rawText" in value:
                    segments.append((
                        value["textSequence"]["characterStart"],
                        value["rawText"],
                    ))

        assert len(segments) > 1
        admitted_text = "".join(
            text for _, text in sorted(segments)
        )
        assert original.startswith(admitted_text)
        assert len(admitted_text) < len(original)
        assert shards[-1].omitted_shard_count == 14
        assert shards[-1].omitted_record_count == 17
        assert "CodeCrow Stage 3 child invocation ceiling reached" in (
            shards[-1].prompt
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_optional_task_context_failure_fails_open(self):
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=SimpleNamespace(
            content="report"
        ))
        request = SimpleNamespace(
            projectVcsRepoSlug="repo",
            pullRequestId=8,
            prAuthor="dev",
            prTitle="Optional context",
            changedFiles=[],
            previousCodeAnalysisIssues=[],
            currentCommitHash=None,
            commitHash=None,
            taskContext={"description": "malformed optional source"},
            maxAllowedTokens=None,
        )
        plan = SimpleNamespace(file_groups=[], cross_file_concerns=[])
        stage_2 = SimpleNamespace(
            model_dump_json=lambda indent=2: "{}",
            pr_recommendation="APPROVE",
        )

        with patch.object(
            stage_3_module,
            "build_task_context",
            side_effect=ValueError("bad task payload"),
        ):
            result = await execute_stage_3_aggregation(
                llm,
                request,
                plan,
                [],
                stage_2,
            )

        assert result["report"] == "report"
        assert "optional enrichment could not be rendered" in (
            llm.ainvoke.await_args.args[0]
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_small_profile_bounds_connected_component_with_authority_provenance(
        self,
        monkeypatch,
    ):
        token_target = 2_500
        monkeypatch.setattr(
            stage_3_module,
            "STAGE3_INPUT_TOKEN_TARGET",
            token_target,
        )
        shared_path = "src/shared/component.py"
        issues = [
            CodeReviewIssue(
                id=f"issue-{index}",
                file=shared_path,
                line=index + 1,
                severity="MEDIUM",
                category="BUG_RISK",
                reason=f"CONNECTED_ISSUE_{index} " + ("reason " * 230),
                suggestedFixDescription="Fix it.",
            )
            for index in range(5)
        ]
        stage_2_payload = {
            "pr_risk_level": "MEDIUM",
            "pr_recommendation": "REQUEST_CHANGES",
            "confidence": "HIGH",
            "cross_file_issues": [
                {
                    "id": f"cross-{index}",
                    "primary_file": shared_path,
                    "affected_files": [shared_path],
                    "description": (
                        f"CONNECTED_STAGE2_{index} " + ("evidence " * 210)
                    ),
                }
                for index in range(5)
            ],
        }
        stage_2 = SimpleNamespace(
            model_dump_json=lambda indent=2: json.dumps(
                stage_2_payload,
                indent=indent,
            ),
            pr_recommendation="REQUEST_CHANGES",
        )
        request = SimpleNamespace(
            projectVcsRepoSlug="repo",
            pullRequestId=9,
            prAuthor="dev",
            prTitle="Connected component",
            changedFiles=[shared_path],
            previousCodeAnalysisIssues=[],
            currentCommitHash=None,
            commitHash=None,
            taskContext=None,
            maxAllowedTokens=30_000,
        )
        plan = SimpleNamespace(
            file_groups=[SimpleNamespace(
                priority="HIGH",
                files=[SimpleNamespace(path=shared_path)],
            )],
            cross_file_concerns=[f"Validate {shared_path}"],
        )
        prompts = []

        async def invoke(prompt, **_kwargs):
            prompts.append(prompt)
            return SimpleNamespace(content=f"memo-{len(prompts)}")

        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=invoke)

        result = await execute_stage_3_aggregation(
            llm,
            request,
            plan,
            issues,
            stage_2,
        )

        input_count = len(
            result["stage_3_prompt_provenance"]["inputShards"]
        )
        input_prompts = prompts[:input_count]
        assert input_count == 1
        assert all(
            stage_3_module._estimated_prompt_tokens(prompt) <= token_target
            for prompt in prompts
        )
        assert "CONNECTED_ISSUE_0" in input_prompts[0]
        assert all(
            f"CONNECTED_ISSUE_{index}" not in input_prompts[0]
            for index in range(1, 5)
        )
        assert all(
            f"CONNECTED_STAGE2_{index}" not in input_prompts[0]
            for index in range(5)
        )
        assert all("componentSha256" in prompt for prompt in input_prompts)
        assert all("upperSynthesisCarriesOmittedRecords" in prompt for prompt in input_prompts)
        provenance = result["stage_3_prompt_provenance"]
        synthesis_provenance = provenance["synthesisShards"]
        assert synthesis_provenance == []
        admitted = provenance["inputShards"][0]
        assert admitted["recordKeys"] == ["stage_1:issue_0"]
        assert admitted["omittedShardCount"] == 9
        assert admitted["omittedRecordCount"] == 17
        assert admitted["omittedVerificationCount"] == 4
        assert "CodeCrow Stage 3 child invocation ceiling reached" in (
            input_prompts[0]
        )

        authority_keys = [
            record_key
            for shard in provenance["inputShards"]
            for record_key in shard["boundaryAuthorityRecordKeys"]
        ]
        assert len(authority_keys) == len(set(authority_keys))
        assert {
            f"stage_1:issue_{index}" for index in range(5)
        }.issubset(authority_keys)
        assert {
            f"stage_2:cross_file_issues:{index:06d}"
            for index in range(5)
        }.issubset(authority_keys)

        assert result["report"] == "memo-1"


class TestStage3McpVerification:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_uses_shared_model_session_constructor(self):
        request = SimpleNamespace(
            projectVcsWorkspace="workspace",
            projectVcsRepoSlug="repo",
        )
        llm = MagicMock()
        mcp_client = MagicMock()
        final_response = SimpleNamespace(
            content="shared-session report",
            tool_calls=[],
            response_metadata={},
        )
        model_session = MagicMock()
        model_session.ainvoke = AsyncMock(return_value=final_response)

        with patch(
            "service.review.orchestrator.stage_3_mcp_verification."
            "AgentExecutionService"
        ) as service_type:
            service_type.return_value.create_model_session.return_value = (
                model_session
            )
            result = await _stage_3_with_mcp(
                llm,
                request,
                "prompt",
                mcp_client,
                "commit-abc",
                {},
            )

        assert result["report"] == "shared-session report"
        service_type.assert_called_once_with(llm=llm, client=mcp_client)
        create_call = service_type.return_value.create_model_session
        create_call.assert_called_once()
        assert create_call.call_args.kwargs["reasoning_effort"].value == "low"
        assert {
            definition["function"]["name"]
            for definition in create_call.call_args.kwargs["tool_definitions"]
        } == {"getBranchFileContent", "getPullRequestComments"}
        model_session.ainvoke.assert_awaited_once()
        llm.bind_tools.assert_not_called()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_oversized_tool_transcript_fails_open_without_clipping(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(
            stage_3_module,
            "STAGE3_INPUT_TOKEN_TARGET",
            2_500,
        )
        issue = CodeReviewIssue(
            file="src/a.py",
            line=10,
            severity="HIGH",
            category="BUG_RISK",
            reason="Claim to verify.",
            suggestedFixDescription="Fix it.",
        )
        request = SimpleNamespace(
            projectVcsWorkspace="workspace",
            projectVcsRepoSlug="repo",
            maxAllowedTokens=30_000,
        )
        tool_response = SimpleNamespace(
            content="",
            tool_calls=[{
                "id": "call-large",
                "name": "getBranchFileContent",
                "args": {
                    "filePath": "src/a.py",
                    "verificationId": "issue_0",
                },
            }],
            response_metadata={},
        )
        bound_llm = MagicMock()
        bound_llm.ainvoke = AsyncMock(return_value=tool_response)
        llm = MagicMock()
        llm.bind_tools.return_value = bound_llm
        llm.ainvoke = AsyncMock(return_value=SimpleNamespace(
            content="plain fallback report"
        ))
        marker_count = 2_000
        raw_tool_result = "TOOL_RESULT_MARKER🙂 " * marker_count
        mcp_client = MagicMock()
        mcp_client.session.call_tool = AsyncMock(
            return_value=SimpleNamespace(
                content=[SimpleNamespace(text=raw_tool_result)]
            )
        )

        continuation_impl = (
            stage_3_module._stage_3_mcp_continuation_messages
        )
        captured_continuations = []

        def capture_continuation(*args, **kwargs):
            continuation = continuation_impl(*args, **kwargs)
            captured_continuations.append(continuation)
            return continuation

        with patch.object(
            stage_3_module,
            "_stage_3_mcp_continuation_messages",
            side_effect=capture_continuation,
        ) as continuation_call:
            result = await _stage_3_with_mcp(
                llm,
                request,
                "short bounded prompt",
                mcp_client,
                "commit-abc",
                {"issue_0": issue},
            )

        assert result["report"] == "plain fallback report"
        bound_llm.ainvoke.assert_awaited_once()
        llm.ainvoke.assert_awaited_once()
        continuation_call.assert_called_once()
        [continuation_messages] = captured_continuations
        assert continuation_messages[0]["content"].count(
            "TOOL_RESULT_MARKER"
        ) == marker_count
        forbidden = {
            "max_tokens",
            "max_completion_tokens",
            "max_output_tokens",
            "max_new_tokens",
        }
        assert forbidden.isdisjoint(llm.ainvoke.await_args.kwargs)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_successful_revision_read_validates_fresh_issue_dismissal(self):
        issue = CodeReviewIssue(
            file="src/a.py", line=10, severity="HIGH", category="BUG_RISK",
            reason="Claim to verify.", suggestedFixDescription="Fix it.",
        )
        request = SimpleNamespace(
            projectVcsWorkspace="workspace",
            projectVcsRepoSlug="repo",
        )
        tool_response = SimpleNamespace(
            content="",
            tool_calls=[{
                "id": "call-1",
                "name": "getBranchFileContent",
                "args": {
                    "filePath": "src/a.py",
                    "verificationId": "issue_0",
                },
            }],
            response_metadata={},
        )
        final_response = SimpleNamespace(
            content=(
                "Verified report\n"
                '<!-- DISMISSED_ISSUES: ["issue_0"] -->'
            ),
            tool_calls=[],
            response_metadata={},
        )
        bound_llm = MagicMock()
        bound_llm.ainvoke = AsyncMock(return_value=tool_response)
        llm = MagicMock()
        llm.bind_tools.return_value = bound_llm
        llm.ainvoke = AsyncMock(return_value=final_response)
        mcp_client = MagicMock()
        mcp_client.session.call_tool = AsyncMock(return_value=SimpleNamespace(
            content=[SimpleNamespace(text="current source")]
        ))

        result = await _stage_3_with_mcp(
            llm,
            request,
            "prompt",
            mcp_client,
            "commit-abc",
            {"issue_0": issue},
        )

        assert result["report"] == "Verified report"
        assert result["dismissed_issue_ids"] == []
        assert result["dismissed_issue_keys"] == ["issue_0"]
        assert result["dismissed_issue_object_ids"] == [id(issue)]
        call_args = mcp_client.session.call_tool.await_args.args[1]
        assert call_args["branch"] == "commit-abc"
        assert call_args["startLine"] == 1
        assert call_args["endLine"] == 90
