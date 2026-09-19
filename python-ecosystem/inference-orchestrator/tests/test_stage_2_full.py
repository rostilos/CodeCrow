"""Tests for stage_2_cross_file helpers: architecture context, migration detection, slim issues."""
import hashlib
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from service.review.orchestrator.stage_2_cross_file import (
    _build_architecture_context,
    _build_stage_2_prompts,
    _build_task_history_context,
    _detect_migration_paths,
    _estimated_prompt_tokens,
    _format_complete_project_rules,
    _merge_stage_2_results,
    _merge_stage_2_results_with_provenance,
    _stage_2_input_token_budget,
    _Stage2Prompt,
    _slim_issues_for_stage_2,
    Stage2GenerationError,
    execute_stage_2_cross_file,
    stage_2_coverage_ledger,
)
from model.multi_stage import CrossFileAnalysisResult, CrossFileIssue
from service.review.pr_evidence import PrEvidenceLedger, PrLedgerEvidence


# ── _build_architecture_context ───────────────────────────────


class TestBuildArchitectureContext:
    def test_no_enrichment(self):
        result = _build_architecture_context(enrichment=None, changed_files=[])
        assert "No architecture context" in result

    def test_relationships_section(self):
        enrichment = MagicMock()
        rel = MagicMock()
        rel.sourceFile = "a.py"
        rel.targetFile = "b.py"
        rel.relationshipType = "imports"
        rel.matchedOn = "SomeClass"
        enrichment.relationships = [rel]
        enrichment.fileMetadata = []
        result = _build_architecture_context(enrichment, ["a.py", "b.py"])
        assert "a.py" in result
        assert "imports" in result
        assert "SomeClass" in result

    def test_class_hierarchy(self):
        enrichment = MagicMock()
        enrichment.relationships = []
        meta = MagicMock()
        meta.path = "Foo.java"
        meta.extendsClasses = ["BaseClass"]
        meta.implementsInterfaces = ["IFoo"]
        meta.imports = []
        enrichment.fileMetadata = [meta]
        result = _build_architecture_context(enrichment, [])
        assert "BaseClass" in result
        assert "IFoo" in result

    def test_cross_file_imports(self):
        enrichment = MagicMock()
        enrichment.relationships = []
        meta = MagicMock()
        meta.path = "a.py"
        meta.extendsClasses = []
        meta.implementsInterfaces = []
        meta.imports = ["b.py", "external_lib"]
        enrichment.fileMetadata = [meta]
        result = _build_architecture_context(enrichment, ["a.py", "b.py"])
        assert "imports" in result
        assert "external_lib" in result

    def test_no_ext_or_impl(self):
        enrichment = MagicMock()
        enrichment.relationships = []
        meta = MagicMock()
        meta.path = "a.py"
        meta.extendsClasses = []
        meta.implementsInterfaces = []
        meta.imports = []
        enrichment.fileMetadata = [meta]
        result = _build_architecture_context(enrichment, [])
        assert "Structured enrichment context" in result
        assert "a.py" in result

    def test_no_matched_on(self):
        enrichment = MagicMock()
        rel = MagicMock()
        rel.sourceFile = "a.py"
        rel.targetFile = "b.py"
        rel.relationshipType = "imports"
        rel.matchedOn = None
        enrichment.relationships = [rel]
        enrichment.fileMetadata = []
        result = _build_architecture_context(enrichment, [])
        assert "imports" in result
        assert "matched on" not in result


# ── _detect_migration_paths ───────────────────────────────────


class TestDetectMigrationPaths:
    def test_no_processed_diff(self):
        result = _detect_migration_paths(None)
        assert "not pre-classified" in result

    def test_no_migrations(self):
        diff = MagicMock()
        f = MagicMock()
        f.path = "src/main.py"
        diff.files = [f]
        result = _detect_migration_paths(diff)
        assert "not pre-classified" in result

    def test_sql_file_detected(self):
        diff = MagicMock()
        f = MagicMock()
        f.path = "db/schema.sql"
        diff.files = [f]
        result = _detect_migration_paths(diff)
        assert "not pre-classified" in result

    def test_migration_path_detected(self):
        diff = MagicMock()
        f1 = MagicMock()
        f1.path = "src/db/migrate/001_add_table.rb"
        f2 = MagicMock()
        f2.path = "src/alembic/versions/abc.py"  # needs /alembic/ with leading slash
        diff.files = [f1, f2]
        result = _detect_migration_paths(diff)
        assert "not pre-classified" in result

    def test_flyway_detected(self):
        diff = MagicMock()
        f = MagicMock()
        f.path = "src/main/resources/flyway/V1__init.sql"
        diff.files = [f]
        result = _detect_migration_paths(diff)
        assert "not pre-classified" in result


# ── _slim_issues_for_stage_2 ─────────────────────────────────


class TestSlimIssuesForStage2:
    def test_empty(self):
        result = _slim_issues_for_stage_2([])
        assert json.loads(result) == []

    def test_preserves_complete_finding_fields(self):
        issue = MagicMock()
        issue.model_dump.return_value = {
            "id": "1",
            "severity": "HIGH",
            "suggestedFixDiff": "big patch",
            "suggestedFixDescription": "fix it",
            "resolutionExplanation": "resolved",
            "resolvedInCommit": "abc",
            "visibility": "public",
        }
        result = json.loads(_slim_issues_for_stage_2([issue]))
        assert len(result) == 1
        assert result[0]["suggestedFixDiff"] == "big patch"
        assert result[0]["suggestedFixDescription"] == "fix it"
        assert result[0]["resolutionExplanation"] == "resolved"
        assert result[0]["resolvedInCommit"] == "abc"
        assert result[0]["visibility"] == "public"
        assert result[0]["id"] == "1"


# ── execute_stage_2_cross_file ────────────────────────────────


class TestExecuteStage2CrossFile:
    def test_task_history_context_ignores_non_string_mock_attribute(self):
        request = MagicMock()

        result = _build_task_history_context(request)

        assert result == "No prior task history available."

    @pytest.mark.asyncio(loop_scope="function")
    async def test_structured_output_success(self):
        from model.multi_stage import CrossFileAnalysisResult
        llm = MagicMock()
        expected = CrossFileAnalysisResult(
            cross_file_issues=[],
            duplication_findings=[],
            pr_recommendation="APPROVE",
            pr_risk_level="LOW",
            confidence="HIGH",
        )
        structured = MagicMock()
        structured.ainvoke = AsyncMock(return_value=expected)
        llm.with_structured_output.return_value = structured

        request = MagicMock()
        request.projectVcsRepoSlug = "repo"
        request.prTitle = "title"
        request.commitHash = "abc"
        request.enrichmentData = None
        request.changedFiles = []
        request.projectRules = None

        plan = MagicMock()
        plan.cross_file_concerns = []

        result = await execute_stage_2_cross_file(
            llm, request, [], plan
        )
        assert result.pr_recommendation == "APPROVE"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_fallback_parse(self):
        from model.multi_stage import CrossFileAnalysisResult
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(side_effect=Exception("structured fail"))
        llm.with_structured_output.return_value = structured

        resp = MagicMock()
        resp.content = '{"cross_file_issues":[],"duplication_findings":[],"pr_recommendation":"APPROVE"}'
        llm.ainvoke = AsyncMock(return_value=resp)

        request = MagicMock()
        request.projectVcsRepoSlug = "repo"
        request.prTitle = "title"
        request.commitHash = "abc"
        request.enrichmentData = None
        request.changedFiles = []
        request.projectRules = None

        plan = MagicMock()
        plan.cross_file_concerns = []

        with patch("service.review.orchestrator.stage_2_cross_file.parse_llm_response") as mock_parse:
            mock_parse.return_value = CrossFileAnalysisResult(
                cross_file_issues=[], duplication_findings=[], pr_recommendation="APPROVE",
                pr_risk_level="LOW", confidence="HIGH",
            )
            result = await execute_stage_2_cross_file(llm, request, [], plan)
            assert result.pr_recommendation == "APPROVE"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_passes_request_specific_input_budget_to_packer(self):
        request = MagicMock()
        request.projectVcsRepoSlug = "repo"
        request.prTitle = "title"
        request.commitHash = "abc"
        request.currentCommitHash = "abc"
        request.enrichmentData = None
        request.changedFiles = []
        request.projectRules = None
        request.taskContext = None
        request.analysisMode = "FULL"
        request.deltaDiff = None
        request.maxAllowedTokens = 50_000
        plan = MagicMock(cross_file_concerns=[])
        output = CrossFileAnalysisResult(
            pr_risk_level="LOW",
            cross_file_issues=[],
            pr_recommendation="PASS",
            confidence="HIGH",
        )

        with (
            patch(
                "service.review.orchestrator.stage_2_cross_file."
                "_build_stage_2_prompts",
                return_value=[_Stage2Prompt("core")],
            ) as build,
            patch(
                "service.review.orchestrator.stage_2_cross_file."
                "_invoke_stage_2_llm",
                new=AsyncMock(return_value=output),
            ),
        ):
            assert await execute_stage_2_cross_file(
                MagicMock(),
                request,
                [],
                plan,
            ) == output

        assert build.call_args.kwargs["token_budget"] == 30_000


def _stage_2_ledger(evidence):
    evidence_by_ref = {item.ref: item for item in evidence}
    full_blocks = "\n\n".join(
        f"[{item.ref}] {item.path}\n{item.excerpt}"
        for item in evidence
        if item.scope == "full_pr"
    )
    delta_blocks = "\n\n".join(
        f"[{item.ref}] {item.path}\n{item.excerpt}"
        for item in evidence
        if item.scope == "delta"
    )
    return PrEvidenceLedger(
        full_pr_context=(
            "FULL PR STATE LEDGER\nManifest status: COMPLETE\n\n"
            f"EVIDENCE EXCERPTS:\n{full_blocks}"
        ),
        incremental_delta_context=(
            "CURRENT INCREMENTAL DELTA\nManifest status: COMPLETE\n\n"
            f"EVIDENCE EXCERPTS:\n{delta_blocks}"
        ),
        manifest_complete=True,
        full_evidence_complete=True,
        incremental=True,
        evidence_by_ref=evidence_by_ref,
        delta_removal_refs=frozenset(),
        delta_hunk_ids=frozenset(item.hunk_id for item in evidence),
        task_terms=(),
        task_relevant_paths=(),
    )


class TestStage2SemanticPacking:
    def test_semantic_packets_have_a_finite_profile_budget(self):
        relationships = []
        metadata = []
        findings = []
        evidence = []
        for index in range(12):
            source = f"src/component_{index}/Source.py"
            target = f"src/component_{index}/Target.py"
            relation = MagicMock()
            relation.sourceFile = source
            relation.targetFile = target
            relation.relationshipType = "CALLS"
            relation.matchedOn = f"RELATION_MARKER_{index:02d}"
            relationships.append(relation)

            item = MagicMock()
            item.path = source
            item.extendsClasses = []
            item.implementsInterfaces = []
            item.imports = [f"IMPORT_MARKER_{index:02d}"]
            item.language = "python"
            item.parentClass = None
            item.namespace = None
            item.error = None
            metadata.append(item)

            findings.append({
                "id": f"S1-{index}",
                "file": source,
                "line": index + 1,
                "reason": f"FINDING_MARKER_{index:02d} " + ("detail " * 35),
            })
            evidence.append(PrLedgerEvidence(
                ref=f"PRF{index + 1:03d}",
                scope="full_pr",
                path=target,
                hunk_id=f"hunk-{index}",
                line_start=1,
                line_end=20,
                excerpt=(
                    f"@@ EVIDENCE_MARKER_{index:02d} @@\n"
                    + (f"+changed_line_{index}\n" * 70)
                ),
                has_removal=False,
            ))

        enrichment = MagicMock()
        enrichment.relationships = relationships
        enrichment.fileMetadata = metadata
        budget = 6_000
        prompts = _build_stage_2_prompts(
            repo_slug="owner/repo",
            pr_title="Semantic packing",
            commit_hash="abc",
            stage_1_findings_json=json.dumps(findings),
            architecture_context=_build_architecture_context(enrichment, []),
            migrations="No pre-classification.",
            cross_file_concerns=["Verify dependency contracts"],
            project_rules="",
            task_context="Small task context.",
            task_history_context="No prior task history available.",
            evidence_ledger=_stage_2_ledger(evidence),
            token_budget=budget,
        )

        assert len(prompts) == 4
        assert all(_estimated_prompt_tokens(prompt) <= budget for prompt in prompts)
        assert all(prompt.omitted_packet_count == 8 for prompt in prompts)
        assert all(prompt.omitted_unit_count == 8 for prompt in prompts)
        assert all(prompt.omitted_hunk_count == 0 for prompt in prompts)
        joined = "\n".join(prompts)
        admitted = [
            index
            for index in range(12)
            if f"RELATION_MARKER_{index:02d}" in joined
        ]
        assert admitted == list(range(len(admitted)))
        assert 0 < len(admitted) < len(relationships)
        for index in admitted:
            assert joined.count(f"RELATION_MARKER_{index:02d}") >= 1
            assert joined.count(f"IMPORT_MARKER_{index:02d}") == 1
            assert joined.count(f"FINDING_MARKER_{index:02d}") == 1
            assert joined.count(f"EVIDENCE_MARKER_{index:02d}") == 1
        for index in range(len(admitted), len(relationships)):
            assert f"RELATION_MARKER_{index:02d}" not in joined
            assert f"IMPORT_MARKER_{index:02d}" not in joined
            assert f"FINDING_MARKER_{index:02d}" not in joined
            assert f"EVIDENCE_MARKER_{index:02d}" not in joined
        assert "CodeCrow Stage 2 invocation ceiling reached" in joined

    def test_connected_dependency_component_stays_together_when_it_fits(self):
        relation = MagicMock()
        relation.sourceFile = "src/a.py"
        relation.targetFile = "src/b.py"
        relation.relationshipType = "CALLS"
        relation.matchedOn = "DEPENDENCY_EDGE_MARKER"
        enrichment = MagicMock()
        enrichment.relationships = [relation]
        enrichment.fileMetadata = []
        evidence = [PrLedgerEvidence(
            ref="PRF001",
            scope="full_pr",
            path="src/b.py",
            hunk_id="h1",
            line_start=1,
            line_end=2,
            excerpt="@@ TARGET_EVIDENCE_MARKER @@\n+call()",
            has_removal=False,
        )]

        prompts = _build_stage_2_prompts(
            repo_slug="owner/repo",
            pr_title="dependency component",
            commit_hash="abc",
            stage_1_findings_json=json.dumps([{
                "id": "S1-1",
                "file": "src/a.py",
                "reason": "SOURCE_FINDING_MARKER",
            }]),
            architecture_context=_build_architecture_context(enrichment, []),
            migrations="No pre-classification.",
            cross_file_concerns=[],
            project_rules="",
            task_context="task",
            task_history_context=(
                "### History\n"
                + "".join(f"historical record {index}\n" for index in range(1_000))
            ),
            evidence_ledger=_stage_2_ledger(evidence),
            token_budget=6_000,
        )

        component_prompt = next(
            prompt for prompt in prompts if "DEPENDENCY_EDGE_MARKER" in prompt
        )
        assert "SOURCE_FINDING_MARKER" in component_prompt
        assert "TARGET_EVIDENCE_MARKER" in component_prompt

    def test_oversized_component_keeps_authority_anchors_with_bounded_evidence(self):
        relation = MagicMock()
        relation.sourceFile = "src/source.py"
        relation.targetFile = "src/target.py"
        relation.relationshipType = "CALLS"
        relation.matchedOn = "OVERSIZED_DEPENDENCY_EDGE"
        enrichment = MagicMock(relationships=[relation], fileMetadata=[])
        evidence = PrLedgerEvidence(
            ref="PRF001",
            scope="full_pr",
            path="src/target.py",
            hunk_id="oversized-hunk",
            line_start=1,
            line_end=900,
            excerpt=(
                "@@ OVERSIZED_EVIDENCE @@\n"
                + "".join(
                    f"+OVERSIZED_LINE_{index:04d} detail detail\n"
                    for index in range(900)
                )
            ),
            has_removal=False,
        )
        prompts = _build_stage_2_prompts(
            repo_slug="owner/repo",
            pr_title="Oversized dependency",
            commit_hash="abc",
            stage_1_findings_json=json.dumps([{
                "id": "S1-source",
                "file": "src/source.py",
                "line": 7,
                "title": "Source contract",
                "reason": "OVERSIZED_FINDING_BODY",
            }]),
            architecture_context=_build_architecture_context(enrichment, []),
            migrations="No pre-classification.",
            cross_file_concerns=[],
            project_rules="",
            task_context="task",
            task_history_context="history",
            evidence_ledger=_stage_2_ledger([evidence]),
            token_budget=6_000,
        )

        assert len(prompts) == 4
        assert all(_estimated_prompt_tokens(prompt) <= 6_000 for prompt in prompts)
        assert all(prompt.omitted_packet_count == 15 for prompt in prompts)
        assert all(prompt.omitted_unit_count == 29 for prompt in prompts)
        authority_prompts = [
            prompt for prompt in prompts
            if "OVERSIZED_DEPENDENCY_EDGE" in prompt
        ]
        assert len(authority_prompts) == 3
        assert all(
            '"kind":"relationship"' in prompt
            for prompt in authority_prompts
        )
        assert all(
            '"kind":"stage_1_finding"' in prompt
            for prompt in authority_prompts
        )
        assert any(
            '"kind":"pr_evidence"' in prompt
            for prompt in authority_prompts
        )
        joined = "\n".join(prompts)
        admitted_lines = [
            index
            for index in range(900)
            if f"OVERSIZED_LINE_{index:04d}" in joined
        ]
        assert admitted_lines == list(range(len(admitted_lines)))
        assert 0 < len(admitted_lines) < 900
        assert "OVERSIZED_LINE_0899" not in joined
        assert "CodeCrow Stage 2 invocation ceiling reached" in joined

    def test_multibyte_evidence_uses_a_bounded_utf8_aware_prefix(self):
        evidence = PrLedgerEvidence(
            ref="PRF001",
            scope="full_pr",
            path="src/international.py",
            hunk_id="multibyte-hunk",
            line_start=1,
            line_end=500,
            excerpt=(
                "@@ MULTIBYTE_EVIDENCE @@\n"
                + "".join(
                    f"+界面変更_{index:04d} 詳細な説明\n"
                    for index in range(500)
                )
            ),
            has_removal=False,
        )

        prompts = _build_stage_2_prompts(
            repo_slug="owner/repo",
            pr_title="Multibyte evidence",
            commit_hash="abc",
            stage_1_findings_json="[]",
            architecture_context="No architecture context available.",
            migrations="No pre-classification.",
            cross_file_concerns=[],
            project_rules="",
            task_context="task",
            task_history_context="history",
            evidence_ledger=_stage_2_ledger([evidence]),
            token_budget=6_000,
        )

        assert len(prompts) == 4
        assert all(_estimated_prompt_tokens(prompt) <= 6_000 for prompt in prompts)
        assert all(prompt.omitted_packet_count == 5 for prompt in prompts)
        assert all(prompt.omitted_unit_count == 10 for prompt in prompts)
        joined = "\n".join(prompts)
        admitted = [
            index
            for index in range(500)
            if f"界面変更_{index:04d}" in joined
        ]
        assert admitted == list(range(len(admitted)))
        assert 0 < len(admitted) < 500
        assert all(
            joined.count(f"界面変更_{index:04d}") == 1
            for index in admitted
        )
        assert "界面変更_0499" not in joined
        assert "CodeCrow Stage 2 invocation ceiling reached" in joined

    def test_optional_enrichment_packets_do_not_share_core_failure_boundary(self):
        relationships = []
        for index in range(40):
            relation = MagicMock()
            relation.sourceFile = f"src/source_{index}.py"
            relation.targetFile = f"src/target_{index}.py"
            relation.relationshipType = "CALLS"
            relation.matchedOn = f"OPTIONAL_RELATION_{index:02d}"
            relationships.append(relation)
        enrichment = MagicMock(relationships=relationships, fileMetadata=[])

        prompts = _build_stage_2_prompts(
            repo_slug="owner/repo",
            pr_title="Optional isolation",
            commit_hash="abc",
            stage_1_findings_json="[]",
            architecture_context=_build_architecture_context(enrichment, []),
            migrations="No pre-classification.",
            cross_file_concerns=[],
            project_rules="",
            task_context="task",
            task_history_context="history",
            evidence_ledger=_stage_2_ledger([]),
            token_budget=6_000,
        )

        assert any(prompt.optional_enrichment_only for prompt in prompts)
        assert any(not prompt.optional_enrichment_only for prompt in prompts)
        assert all(
            prompt.optional_enrichment_only
            for prompt in prompts
            if "OPTIONAL_RELATION_" in prompt
        )

    def test_project_rule_records_are_admitted_in_source_order(self):
        rules = [
            {
                "title": f"RULE_TITLE_{index:02d}",
                "description": (
                    f"RULE_DESCRIPTION_{index:02d} " + "exact policy detail " * 35
                ),
                "filePatterns": [
                    f"src/RULE_PATTERN_{index:02d}/**",
                    f"*RULE_SUFFIX_{index:02d}.py",
                ],
                "ruleType": "SUPPRESS" if index % 2 else "ENFORCE",
                "customMetadata": {"ordinal": index, "enabled": True},
            }
            for index in range(10)
        ]
        project_rules = _format_complete_project_rules(json.dumps(rules))
        records = project_rules.splitlines()

        assert len(records) == len(rules)
        for index, record in enumerate(records):
            assert json.loads(record.split("] ", 1)[1]) == rules[index]

        prompts = _build_stage_2_prompts(
            repo_slug="owner/repo",
            pr_title="Complete project rules",
            commit_hash="abc",
            stage_1_findings_json="[]",
            architecture_context="No architecture context available.",
            migrations="No pre-classification.",
            cross_file_concerns=[],
            project_rules=project_rules,
            task_context="task",
            task_history_context="history",
            evidence_ledger=_stage_2_ledger([]),
            token_budget=6_000,
        )

        assert len(prompts) == 4
        assert all(_estimated_prompt_tokens(prompt) <= 6_000 for prompt in prompts)
        assert all(prompt.omitted_packet_count == 2 for prompt in prompts)
        assert all(prompt.omitted_unit_count == 4 for prompt in prompts)
        joined = "\n".join(prompts)
        admitted = [record for record in records if record in joined]
        assert admitted == records[:len(admitted)]
        assert 0 < len(admitted) < len(records)
        assert all(joined.count(record) == 1 for record in admitted)
        assert all(record not in joined for record in records[len(admitted):])
        assert "CodeCrow Stage 2 invocation ceiling reached" in joined

    def test_partial_shards_never_advertise_complete_changed_line_evidence(self):
        evidence = PrLedgerEvidence(
            ref="PRF001",
            scope="full_pr",
            path="src/a.py",
            hunk_id="h1",
            line_start=1,
            line_end=500,
            excerpt="@@ x @@\n" + ("+changed detail\n" * 800),
            has_removal=False,
        )
        ledger = _stage_2_ledger([evidence])
        ledger = PrEvidenceLedger(
            **{
                **ledger.__dict__,
                "full_pr_context": (
                    "FULL PR STATE LEDGER\nManifest status: COMPLETE\n"
                    "Changed-line evidence status: COMPLETE\n"
                    f"EVIDENCE EXCERPTS:\n[PRF001] src/a.py\n{evidence.excerpt}"
                ),
            }
        )
        prompts = _build_stage_2_prompts(
            repo_slug="owner/repo",
            pr_title="Partial contract",
            commit_hash="abc",
            stage_1_findings_json="[]",
            architecture_context="No architecture context available.",
            migrations="No pre-classification.",
            cross_file_concerns=[],
            project_rules="",
            task_context="task",
            task_history_context="history",
            evidence_ledger=ledger,
            token_budget=6_000,
        )

        assert len(prompts) > 1
        assert all(
            "changed-line evidence status: complete" not in prompt.lower()
            for prompt in prompts
        )
        assert all("CURRENT SHARD EVIDENCE STATUS: PARTIAL" in prompt for prompt in prompts)

    def test_request_budget_reserves_context_and_estimator_counts_utf8_schema(self):
        request = MagicMock(maxAllowedTokens=50_000)

        assert _stage_2_input_token_budget(request) == 30_000
        assert _stage_2_input_token_budget(
            MagicMock(maxAllowedTokens=200_000)
        ) == 60_000
        assert _stage_2_input_token_budget(
            MagicMock(maxAllowedTokens=3_000)
        ) == 1_500
        assert _estimated_prompt_tokens("界" * 4_000) > _estimated_prompt_tokens(
            "a" * 4_000
        )
        assert _estimated_prompt_tokens("") > 256

    def test_partial_prompt_scope_disables_full_review_coverage_proof(self):
        ledger = _stage_2_ledger([])

        partial = stage_2_coverage_ledger(
            ledger,
            {"completePrEvidenceVisible": "false"},
        )

        assert partial.full_evidence_complete is False
        assert ledger.full_evidence_complete is True
        assert stage_2_coverage_ledger(
            ledger,
            {"completePrEvidenceVisible": "true"},
        ) is ledger


def _cross_issue(issue_id, title, severity="MEDIUM"):
    return CrossFileIssue(
        id=issue_id,
        severity=severity,
        category="BUG_RISK",
        title=title,
        primary_file=f"src/{title}.py",
        line=10,
        codeSnippet="broken()",
        affected_files=[f"src/{title}.py", "src/shared.py"],
        description=f"{title} description",
        evidence=f"{title} evidence",
        business_impact=f"{title} impact",
        suggestion=f"Fix {title}",
    )


class TestMergeStage2SemanticResults:
    def test_merge_is_deterministic_and_preserves_distinct_reused_ids(self):
        first = CrossFileAnalysisResult(
            pr_risk_level="MEDIUM",
            cross_file_issues=[_cross_issue("CROSS_001", "alpha")],
            pr_recommendation="PASS_WITH_WARNINGS",
            confidence="HIGH",
        )
        second = CrossFileAnalysisResult(
            pr_risk_level="CRITICAL",
            cross_file_issues=[
                _cross_issue("CROSS_001", "beta", "CRITICAL"),
                _cross_issue("DIFFERENT_ID", "alpha"),
            ],
            pr_recommendation="FAIL",
            confidence="LOW",
        )

        forward = _merge_stage_2_results([first, second])
        reverse = _merge_stage_2_results([second, first])

        assert forward.model_dump() == reverse.model_dump()
        assert [issue.id for issue in forward.cross_file_issues] == [
            "CROSS_001",
            "CROSS_002",
        ]
        assert {issue.title for issue in forward.cross_file_issues} == {"alpha", "beta"}
        assert forward.pr_risk_level == "CRITICAL"
        assert forward.pr_recommendation == "FAIL"
        assert forward.confidence == "LOW"

    def test_duplicate_prefers_shard_where_evidence_refs_were_visible(self):
        prompt_texts = ("semantic-owner-candidate", "semantic-non-owner")
        sorted_by_digest = sorted(
            prompt_texts,
            key=lambda value: hashlib.sha256(value.encode()).hexdigest(),
        )
        non_owner = _Stage2Prompt(
            sorted_by_digest[0],
            visible_evidence_ids={"RAG-other"},
        )
        owner = _Stage2Prompt(
            sorted_by_digest[1],
            visible_evidence_ids={"RAG-own"},
        )
        issue = _cross_issue("CROSS_DUP", "same-issue").model_copy(
            update={"evidenceRefs": ["RAG-own"]},
        )

        def result():
            return CrossFileAnalysisResult(
                pr_risk_level="MEDIUM",
                cross_file_issues=[issue],
                pr_recommendation="PASS_WITH_WARNINGS",
                confidence="HIGH",
            )

        forward, forward_provenance = _merge_stage_2_results_with_provenance([
            (result(), non_owner),
            (result(), owner),
        ])
        reverse, reverse_provenance = _merge_stage_2_results_with_provenance([
            (result(), owner),
            (result(), non_owner),
        ])

        assert forward.model_dump() == reverse.model_dump()
        assert len(forward.cross_file_issues) == 1
        assert forward_provenance == reverse_provenance
        assert forward_provenance["CROSS_001"].visible_evidence_ids == {
            "RAG-own"
        }
        assert forward_provenance["CROSS_001"].prompt_digest == (
            "sha256:" + hashlib.sha256(str(owner).encode()).hexdigest()
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_execute_invokes_every_shard_and_merges_all_findings(self):
        request = MagicMock()
        request.projectVcsRepoSlug = "repo"
        request.prTitle = "title"
        request.commitHash = "abc"
        request.currentCommitHash = "abc"
        request.enrichmentData = None
        request.changedFiles = []
        request.projectRules = None
        request.taskContext = None
        request.analysisMode = "FULL"
        request.deltaDiff = None
        request.maxAllowedTokens = 200_000
        plan = MagicMock(cross_file_concerns=[])
        prompt_provenance = {}
        semantic_prompts = [
            _Stage2Prompt(
                "semantic-one",
                visible_hunk_ids={"hunk-one"},
                visible_evidence_ids={"RAG-one"},
            ),
            _Stage2Prompt(
                "semantic-two",
                visible_hunk_ids={"hunk-two"},
                visible_evidence_ids={"RAG-two"},
            ),
        ]
        outputs = [
            CrossFileAnalysisResult(
                pr_risk_level="MEDIUM",
                cross_file_issues=[_cross_issue("CROSS_001", "alpha")],
                pr_recommendation="PASS_WITH_WARNINGS",
                confidence="HIGH",
            ),
            CrossFileAnalysisResult(
                pr_risk_level="HIGH",
                cross_file_issues=[_cross_issue("CROSS_001", "beta", "HIGH")],
                pr_recommendation="PASS_WITH_WARNINGS",
                confidence="MEDIUM",
            ),
        ]

        with (
            patch(
                "service.review.orchestrator.stage_2_cross_file."
                "_build_stage_2_prompts",
                return_value=semantic_prompts,
            ),
            patch(
                "service.review.orchestrator.stage_2_cross_file."
                "_invoke_stage_2_llm",
                new=AsyncMock(side_effect=outputs),
            ) as invoke,
        ):
            result = await execute_stage_2_cross_file(
                MagicMock(),
                request,
                [],
                plan,
                prompt_provenance=prompt_provenance,
            )

        assert invoke.await_count == 2
        assert {issue.title for issue in result.cross_file_issues} == {"alpha", "beta"}
        digests = json.loads(prompt_provenance["issuePromptDigests"])
        hunk_ids = json.loads(prompt_provenance["issuePromptHunkIds"])
        evidence_ids = json.loads(
            prompt_provenance["issuePromptEvidenceIds"]
        )
        assert digests == {
            "CROSS_001": "sha256:" + hashlib.sha256(b"semantic-two").hexdigest(),
            "CROSS_002": "sha256:" + hashlib.sha256(b"semantic-one").hexdigest(),
        }
        assert hunk_ids == {
            "CROSS_001": ["hunk-two"],
            "CROSS_002": ["hunk-one"],
        }
        assert evidence_ids == {
            "CROSS_001": ["RAG-two"],
            "CROSS_002": ["RAG-one"],
        }
        assert "generationPromptDigest" not in prompt_provenance

    @pytest.mark.asyncio(loop_scope="function")
    async def test_optional_enrichment_shard_failure_fails_open(self, caplog):
        request = MagicMock()
        request.projectVcsRepoSlug = "repo"
        request.prTitle = "title"
        request.commitHash = "abc"
        request.currentCommitHash = "abc"
        request.enrichmentData = None
        request.changedFiles = []
        request.projectRules = None
        request.taskContext = None
        request.analysisMode = "FULL"
        request.deltaDiff = None
        request.maxAllowedTokens = 200_000
        plan = MagicMock(cross_file_concerns=[])
        core_result = CrossFileAnalysisResult(
            pr_risk_level="LOW",
            cross_file_issues=[],
            pr_recommendation="PASS",
            confidence="HIGH",
        )
        prompts = [
            _Stage2Prompt(
                "optional architecture",
                optional_enrichment_only=True,
            ),
            _Stage2Prompt("core evidence"),
        ]
        provenance = {}

        with (
            patch(
                "service.review.orchestrator.stage_2_cross_file."
                "_build_stage_2_prompts",
                return_value=prompts,
            ),
            patch(
                "service.review.orchestrator.stage_2_cross_file."
                "_invoke_stage_2_llm",
                new=AsyncMock(side_effect=[None, None, core_result]),
            ) as invoke,
            caplog.at_level("WARNING"),
        ):
            result = await execute_stage_2_cross_file(
                MagicMock(),
                request,
                [],
                plan,
                prompt_provenance=provenance,
            )

        assert result == core_result
        assert invoke.await_count == 3
        assert provenance["optionalEnrichmentShardFailures"] == "1"
        assert "failed open" in caplog.text

    @pytest.mark.asyncio(loop_scope="function")
    async def test_partial_core_shards_are_retained_with_low_confidence(self):
        request = MagicMock(
            projectVcsRepoSlug="repo",
            prTitle="title",
            commitHash="abc",
            currentCommitHash="abc",
            enrichmentData=None,
            changedFiles=[],
            projectRules=None,
            taskContext=None,
            analysisMode="FULL",
            deltaDiff=None,
            maxAllowedTokens=200_000,
        )
        plan = MagicMock(cross_file_concerns=[])
        retained = CrossFileAnalysisResult(
            pr_risk_level="MEDIUM",
            cross_file_issues=[_cross_issue("CROSS_001", "retained")],
            pr_recommendation="PASS_WITH_WARNINGS",
            confidence="HIGH",
        )
        provenance = {}

        with (
            patch(
                "service.review.orchestrator.stage_2_cross_file."
                "_build_stage_2_prompts",
                return_value=[
                    _Stage2Prompt("failed core"),
                    _Stage2Prompt("successful core"),
                ],
            ),
            patch(
                "service.review.orchestrator.stage_2_cross_file."
                "_invoke_stage_2_llm",
                new=AsyncMock(side_effect=[None, None, retained]),
            ),
        ):
            result = await execute_stage_2_cross_file(
                MagicMock(),
                request,
                [],
                plan,
                prompt_provenance=provenance,
            )

        assert [issue.title for issue in result.cross_file_issues] == ["retained"]
        assert result.confidence == "LOW"
        assert provenance["coreSemanticShardFailures"] == "1"
        assert provenance["completePrEvidenceVisible"] == "false"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_all_core_response_failures_raise_typed_generation_error(self):
        request = MagicMock(
            projectVcsRepoSlug="repo",
            prTitle="title",
            commitHash="abc",
            currentCommitHash="abc",
            enrichmentData=None,
            changedFiles=[],
            projectRules=None,
            taskContext=None,
            analysisMode="FULL",
            deltaDiff=None,
            maxAllowedTokens=200_000,
        )
        provenance = {}

        with (
            patch(
                "service.review.orchestrator.stage_2_cross_file."
                "_build_stage_2_prompts",
                return_value=[_Stage2Prompt("core one")],
            ),
            patch(
                "service.review.orchestrator.stage_2_cross_file."
                "_invoke_stage_2_llm",
                new=AsyncMock(side_effect=[None, None]),
            ),
        ):
            with pytest.raises(Stage2GenerationError, match="every core"):
                await execute_stage_2_cross_file(
                    MagicMock(),
                    request,
                    [],
                    MagicMock(cross_file_concerns=[]),
                    prompt_provenance=provenance,
                )

        assert provenance["coreSemanticShardFailures"] == "1"
        assert provenance["completePrEvidenceVisible"] == "false"
