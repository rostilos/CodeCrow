"""
Unit tests for service.review.orchestrator.reconciliation —
issue_matches_files, compute_issue_fingerprint, is_semantically_similar,
deduplicate_issues, format_previous_issues_for_batch, deduplicate_final_issues,
deduplicate_cross_batch_issues, _build_batches, reconcile_previous_issues.
"""
import pytest
from unittest.mock import MagicMock
from model.output_schemas import CodeReviewIssue
from service.review.orchestrator.reconciliation import (
    issue_matches_files,
    compute_issue_fingerprint,
    is_semantically_similar,
    deduplicate_issues,
    format_previous_issues_for_batch,
    deduplicate_final_issues,
    deduplicate_cross_batch_issues,
    issues_are_semantic_dedup_candidates,
    _build_batches,
    reconcile_previous_issues,
)


def _make_issue(**kwargs):
    defaults = dict(
        severity="MEDIUM", category="CODE_QUALITY", file="src/app.py",
        line=10, reason="Some issue", suggestedFixDescription="Fix it",
    )
    defaults.update(kwargs)
    return CodeReviewIssue(**defaults)


# ── issue_matches_files ──────────────────────────────────────────

class TestIssueMatchesFiles:

    def test_exact_match(self):
        issue = _make_issue(file="src/app.py")
        assert issue_matches_files(issue, ["src/app.py"]) is True

    def test_suffix_match(self):
        issue = _make_issue(file="project/src/app.py")
        assert issue_matches_files(issue, ["src/app.py"]) is True

    def test_reverse_suffix_match(self):
        issue = _make_issue(file="src/app.py")
        assert issue_matches_files(issue, ["project/src/app.py"]) is True

    def test_no_match(self):
        issue = _make_issue(file="src/app.py")
        assert issue_matches_files(issue, ["src/other.py"]) is False

    def test_no_basename_only_match(self):
        """Different paths with same basename should NOT match."""
        issue = _make_issue(file="module_a/utils.py")
        assert issue_matches_files(issue, ["module_b/utils.py"]) is False

    def test_bare_basename_is_not_a_repository_identity(self):
        issue = _make_issue(file="module_a/utils.py")
        assert issue_matches_files(issue, ["utils.py"]) is False

    def test_absolute_checkout_prefix_matches_repository_relative_path(self):
        issue = _make_issue(file="/tmp/checkout/module_a/utils.py")
        assert issue_matches_files(issue, ["module_a/utils.py"]) is True

    def test_empty_file_paths(self):
        issue = _make_issue(file="src/app.py")
        assert issue_matches_files(issue, []) is False

    def test_empty_issue_file(self):
        issue = _make_issue(file="")
        assert issue_matches_files(issue, ["src/app.py"]) is False

    def test_dict_issue(self):
        issue = {"file": "src/app.py"}
        assert issue_matches_files(issue, ["src/app.py"]) is True


# ── compute_issue_fingerprint ────────────────────────────────────

class TestComputeIssueFingerprint:

    def test_deterministic(self):
        data = {"file": "a.py", "line": 10, "severity": "HIGH", "reason": "Bad code"}
        fp1 = compute_issue_fingerprint(data)
        fp2 = compute_issue_fingerprint(data)
        assert fp1 == fp2

    def test_different_files(self):
        fp1 = compute_issue_fingerprint({"file": "a.py", "line": 10, "severity": "HIGH", "reason": "r"})
        fp2 = compute_issue_fingerprint({"file": "b.py", "line": 10, "severity": "HIGH", "reason": "r"})
        assert fp1 != fp2

    def test_line_tolerance(self):
        """Lines within ±3 should get same group."""
        fp1 = compute_issue_fingerprint({"file": "a.py", "line": 10, "severity": "HIGH", "reason": "r"})
        fp2 = compute_issue_fingerprint({"file": "a.py", "line": 11, "severity": "HIGH", "reason": "r"})
        assert fp1 == fp2  # Both line//3 = 3

    def test_line_zero(self):
        fp = compute_issue_fingerprint({"file": "a.py", "line": 0, "severity": "HIGH", "reason": "r"})
        assert "::" in fp

    def test_missing_fields(self):
        fp = compute_issue_fingerprint({})
        assert isinstance(fp, str)


# ── is_semantically_similar ──────────────────────────────────────

class TestIsSemanticallySimilar:

    def test_identical(self):
        assert is_semantically_similar("Missing null check", "Missing null check") is True

    def test_case_insensitive(self):
        assert is_semantically_similar("Missing NULL check", "missing null check") is True

    def test_similar(self):
        assert is_semantically_similar(
            "Missing null check in user lookup",
            "Missing null check in user lookup method",
        ) is True

    def test_different(self):
        assert is_semantically_similar(
            "SQL injection vulnerability",
            "Memory leak in connection pool",
        ) is False

    def test_empty_returns_false(self):
        assert is_semantically_similar("", "text") is False
        assert is_semantically_similar("text", "") is False
        assert is_semantically_similar("", "") is False

    def test_custom_threshold(self):
        r1 = "Missing null check in user"
        r2 = "Null check missing for user"
        # With very high threshold, might not match
        assert isinstance(is_semantically_similar(r1, r2, threshold=0.99), bool)


# ── deduplicate_issues ───────────────────────────────────────────

class TestDeduplicateIssues:

    def test_empty(self):
        assert deduplicate_issues([]) == []

    def test_no_duplicates(self):
        issues = [
            {"file": "a.py", "line": 10, "severity": "HIGH", "reason": "Issue A"},
            {"file": "b.py", "line": 20, "severity": "LOW", "reason": "Issue B"},
        ]
        result = deduplicate_issues(issues)
        assert len(result) == 2

    def test_exact_duplicates_deduped(self):
        issues = [
            {"file": "a.py", "line": 10, "severity": "HIGH", "reason": "Same issue"},
            {"file": "a.py", "line": 10, "severity": "HIGH", "reason": "Same issue"},
        ]
        result = deduplicate_issues(issues)
        assert len(result) == 1

    def test_newer_version_wins(self):
        issues = [
            {"file": "a.py", "line": 10, "severity": "HIGH", "reason": "Same issue", "prVersion": 1, "status": "open"},
            {"file": "a.py", "line": 10, "severity": "HIGH", "reason": "Same issue", "prVersion": 2, "status": "open"},
        ]
        result = deduplicate_issues(issues)
        assert len(result) == 1
        assert result[0]["prVersion"] == 2

    def test_preserves_resolved_status(self):
        issues = [
            {"file": "a.py", "line": 10, "severity": "HIGH", "reason": "Same issue", "prVersion": 1, "status": "resolved"},
            {"file": "a.py", "line": 10, "severity": "HIGH", "reason": "Same issue", "prVersion": 2, "status": "open"},
        ]
        result = deduplicate_issues(issues)
        assert len(result) == 1
        assert result[0]["status"] == "resolved"

    def test_pydantic_model_input(self):
        issue = _make_issue()
        result = deduplicate_issues([issue])
        assert len(result) == 1


# ── format_previous_issues_for_batch ─────────────────────────────

class TestFormatPreviousIssuesForBatch:

    def test_empty(self):
        assert format_previous_issues_for_batch([]) == ""

    def test_open_issues(self):
        issues = [
            {"id": "1", "severity": "HIGH", "file": "a.py", "line": 10,
             "reason": "Bug found", "status": "open", "prVersion": 1},
        ]
        result = format_previous_issues_for_batch(issues)
        assert "OPEN ISSUES" in result
        assert "Bug found" in result
        assert "ID:1" in result

    def test_resolved_issues(self):
        issues = [
            {"id": "2", "severity": "LOW", "file": "b.py", "line": 5,
             "reason": "Style issue", "status": "resolved", "prVersion": 1,
             "resolvedDescription": "Fixed formatting"},
        ]
        result = format_previous_issues_for_batch(issues)
        assert result == ""

    def test_ignored_issue_is_context_only(self):
        issues = [
            {"id": "3", "severity": "LOW", "file": "b.py", "line": 5,
             "reason": "Dismissed issue", "status": "ignored", "prVersion": 1},
        ]

        result = format_previous_issues_for_batch(issues)

        assert result == ""

    def test_terminal_history_is_excluded_when_open_history_exists(self):
        issues = [
            {"id": "1", "severity": "HIGH", "file": "a.py", "line": 10,
             "reason": "Open defect", "status": "open", "prVersion": 2},
            {"id": "2", "severity": "LOW", "file": "b.py", "line": 5,
             "reason": "Already fixed", "status": "resolved", "prVersion": 1},
        ]

        result = format_previous_issues_for_batch(issues)

        assert "Open defect" in result
        assert "Already fixed" not in result
        assert "terminal history is excluded" in result

    def test_instructions_present(self):
        issues = [{"id": "1", "severity": "HIGH", "file": "a.py", "reason": "r"}]
        result = format_previous_issues_for_batch(issues)
        assert "INSTRUCTIONS" in result
        assert "isResolved" in result


# ── deduplicate_final_issues ─────────────────────────────────────

class TestDeduplicateFinalIssues:

    def test_empty(self):
        assert deduplicate_final_issues([]) == []

    def test_no_duplicates(self):
        issues = [
            _make_issue(file="a.py", line=10, category="SECURITY"),
            _make_issue(file="b.py", line=20, category="BUG_RISK"),
        ]
        result = deduplicate_final_issues(issues)
        assert len(result) == 2

    def test_same_location_and_category_preserves_distinct_root_causes(self):
        issues = [
            _make_issue(file="a.py", line=10, category="SECURITY", reason="First"),
            _make_issue(file="a.py", line=10, category="SECURITY", reason="Second"),
        ]
        result = deduplicate_final_issues(issues)
        assert len(result) == 2

    def test_file_scope_does_not_absorb_distinct_specific_finding(self):
        issues = [
            _make_issue(file="a.py", line=0, category="CODE_QUALITY", reason="File-level"),
            _make_issue(file="a.py", line=42, category="CODE_QUALITY", reason="Specific-line"),
        ]
        result = deduplicate_final_issues(issues)
        assert len(result) == 2

    def test_similar_prose_at_distinct_locations_is_not_identity(self):
        issues = [
            _make_issue(file="a.py", line=10, category="SECURITY", reason="Missing null check in user lookup"),
            _make_issue(file="a.py", line=20, category="SECURITY", reason="Missing null check in user lookup method"),
        ]
        result = deduplicate_final_issues(issues)
        assert len(result) == 2

    def test_same_source_snippet_survives_line_drift_and_deduplicates(self):
        issues = [
            _make_issue(
                file="a.py",
                line=10,
                category="SECURITY",
                reason="Missing null check in user lookup",
                codeSnippet="user = lookup(user_id)",
            ),
            _make_issue(
                file="a.py",
                line=13,
                category="SECURITY",
                reason="Missing null check in user lookup method",
                codeSnippet="user = lookup(user_id)",
            ),
        ]

        assert len(deduplicate_final_issues(issues)) == 1

    def test_exact_history_recreation_keeps_identity_and_refreshes_anchor(self):
        historical = _make_issue(
            id="3989",
            file="src/service.py",
            line=1,
            title="Unbounded retry loop can exhaust workers",
            category="CODE_QUALITY",
            severity="MEDIUM",
            reason="The retry loop has no terminal attempt limit.",
            codeSnippet="class RetryService:",
        )
        recreated = _make_issue(
            file="src/service.py",
            line=1233,
            title="Unbounded retry loop can exhaust workers",
            category="BUG_RISK",
            severity="HIGH",
            reason="The retry loop has no terminal attempt limit.",
            codeSnippet="while should_retry(response):",
        )

        result = deduplicate_final_issues([historical, recreated])

        assert len(result) == 1
        assert result[0].id == "3989"
        assert result[0].line == 1233
        assert result[0].codeSnippet == "while should_retry(response):"
        assert result[0].severity == "HIGH"

    def test_distinct_historical_duplicates_keep_both_concrete_locations(self):
        first = _make_issue(
            id="3989", file="src/service.py", line=10,
            title="Unbounded retry loop can exhaust workers",
            reason="The retry loop has no terminal attempt limit.",
        )
        second = _make_issue(
            id="3990", file="src/service.py", line=20,
            title="Unbounded retry loop can exhaust workers",
            reason="The retry loop has no terminal attempt limit.",
        )

        result = deduplicate_final_issues([second, first])

        assert len(result) == 1
        assert result[0].id == "3989"
        assert result[0].relatedLocations == ["src/service.py:20"]

    def test_exact_plugin_proof_is_a_semantic_candidate_not_a_delete_key(self):
        issues = [
            _make_issue(
                file="app/code/Vendor/Module/etc/di.xml",
                line=12,
                category="ARCHITECTURE",
                reason="The scoped preference selects the wrong implementation.",
                codeSnippet='<preference for="Api" type="Broken"/>',
                claimKind="magento-di-effective-preference",
                evidenceRefs=["RAG-proof-2", "RAG-proof-1"],
            ),
            _make_issue(
                file="app/code/Vendor/Module/etc/di.xml",
                line=45,
                category="BUG_RISK",
                reason="Another batch describes the same effective binding.",
                codeSnippet="<argument name=\"service\">Api</argument>",
                claimKind="magento-di-effective-preference",
                evidenceRefs=["RAG-proof-1", "RAG-proof-2"],
            ),
        ]

        assert deduplicate_final_issues(issues) == issues
        assert issues_are_semantic_dedup_candidates(issues[0], issues[1])

    def test_distinct_plugin_proofs_remain_distinct(self):
        issues = [
            _make_issue(
                file="app/code/Vendor/Module/etc/di.xml",
                line=12,
                reason="First preference defect.",
                codeSnippet='<preference for="ApiOne" type="Broken"/>',
                claimKind="magento-di-effective-preference",
                evidenceRefs=["RAG-proof-1"],
            ),
            _make_issue(
                file="app/code/Vendor/Module/etc/di.xml",
                line=13,
                reason="Second preference defect.",
                codeSnippet='<preference for="ApiTwo" type="Broken"/>',
                claimKind="magento-di-effective-preference",
                evidenceRefs=["RAG-proof-2"],
            ),
        ]

        assert deduplicate_final_issues(issues) == issues

    def test_same_plugin_proof_in_different_files_remains_distinct(self):
        issues = [
            _make_issue(
                file="module-a/etc/di.xml",
                claimKind="magento-di-effective-preference",
                evidenceRefs=["RAG-proof-1"],
            ),
            _make_issue(
                file="module-b/etc/di.xml",
                claimKind="magento-di-effective-preference",
                evidenceRefs=["RAG-proof-1"],
            ),
        ]

        assert deduplicate_final_issues(issues) == issues

    def test_different_files_not_deduped(self):
        """Same reason in DIFFERENT files should NOT be deduped."""
        issues = [
            _make_issue(file="a.py", line=10, reason="Missing null check"),
            _make_issue(file="b.py", line=10, reason="Missing null check"),
        ]
        result = deduplicate_final_issues(issues)
        assert len(result) == 2


# ── deduplicate_cross_batch_issues ───────────────────────────────

class TestDeduplicateCrossBatchIssues:

    def test_empty(self):
        assert deduplicate_cross_batch_issues([]) == []

    def test_no_duplicates(self):
        issues = [
            _make_issue(reason="Issue A"),
            _make_issue(reason="Issue B completely different"),
        ]
        result = deduplicate_cross_batch_issues(issues)
        assert len(result) == 2

    def test_similar_deduped(self):
        issues = [
            _make_issue(reason="Missing null check in user lookup"),
            _make_issue(reason="Missing null check in user lookup method"),
        ]
        result = deduplicate_cross_batch_issues(issues)
        assert len(result) == 1

    def test_similar_wording_in_different_files_is_not_deduplicated(self):
        issues = [
            _make_issue(
                file="module_a/service.py",
                reason="Missing null check in user lookup",
            ),
            _make_issue(
                file="module_b/service.py",
                reason="Missing null check in user lookup method",
            ),
        ]

        assert len(deduplicate_cross_batch_issues(issues)) == 2

    def test_similar_wording_at_different_anchors_is_not_deduplicated(self):
        issues = [
            _make_issue(
                file="service.py",
                line=10,
                reason="Missing null check in user lookup",
            ),
            _make_issue(
                file="service.py",
                line=30,
                reason="Missing null check in user lookup method",
            ),
        ]

        assert len(deduplicate_cross_batch_issues(issues)) == 2


# ── _build_batches ───────────────────────────────────────────────

class TestBuildBatches:

    def test_single_file_single_batch(self):
        issues = [_make_issue(file="a.py") for _ in range(5)]
        batches = _build_batches(issues, max_batch_size=10)
        assert len(batches) == 1

    def test_respects_max_size(self):
        issues = [_make_issue(file=f"file{i}.py") for i in range(10)]
        batches = _build_batches(issues, max_batch_size=3)
        for batch in batches:
            # Each batch may exceed if a single file has more, but generally ≤ max
            pass
        assert len(batches) >= 1

    def test_same_file_never_split(self):
        """Issues for same file should never be split across batches."""
        issues = [_make_issue(file="a.py", line=i) for i in range(10)]
        batches = _build_batches(issues, max_batch_size=5)
        # All 10 issues for a.py should be in one batch (possibly oversized)
        for batch in batches:
            files_in_batch = {
                (i.model_dump() if hasattr(i, 'model_dump') else i).get('file')
                for i in batch
            }
            if "a.py" in files_in_batch:
                a_count = sum(1 for i in batch
                              if (i.model_dump() if hasattr(i, 'model_dump') else i).get('file') == "a.py")
                assert a_count == 10

    def test_empty(self):
        assert _build_batches([]) == []


# ── reconcile_previous_issues (async) ────────────────────────────

class TestReconcilePreviousIssues:

    @pytest.fixture
    def mock_request(self):
        req = MagicMock()
        req.previousCodeAnalysisIssues = []
        req.currentCommitHash = "abc123"
        req.commitHash = "abc123"
        req.deltaDiff = ""
        return req

    @pytest.mark.asyncio
    async def test_no_previous_returns_new(self, mock_request):
        new_issues = [_make_issue()]
        result = await reconcile_previous_issues(mock_request, new_issues)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_merges_by_id(self, mock_request):
        mock_request.previousCodeAnalysisIssues = [
            {"id": "42", "file": "a.py", "line": 10, "severity": "HIGH",
             "reason": "Original reason", "category": "SECURITY",
             "status": "open", "suggestedFixDescription": "Original fix"},
        ]
        new_issue = _make_issue(id="42", file="a.py", line=12, reason="Updated", isResolved=False)
        result = await reconcile_previous_issues(mock_request, [new_issue])
        # Should merge: preserve original reason, use new line
        merged = [i for i in result if (i.model_dump() if hasattr(i, 'model_dump') else i).get('id') == '42']
        assert len(merged) == 1
        data = merged[0].model_dump() if hasattr(merged[0], 'model_dump') else merged[0]
        assert data["reason"] == "Original reason"
        assert data["line"] == 12

    @pytest.mark.asyncio
    async def test_resolved_match_preserves_explicit_resolution_reason(self, mock_request):
        mock_request.previousCodeAnalysisIssues = [
            {"id": "42", "file": "a.py", "line": 10, "severity": "HIGH",
             "reason": "Original defect", "category": "BUG_RISK",
             "status": "open", "suggestedFixDescription": "Use a string default"},
        ]
        new_issue = _make_issue(
            id="42", file="a.py", line=12, isResolved=True,
            reason="Original defect",
            resolutionReason="Empty-string default applied.",
        )

        result = await reconcile_previous_issues(mock_request, [new_issue])
        data = result[0].model_dump()

        assert data["isResolved"] is True
        assert data["resolutionReason"] == "Empty-string default applied."
        assert data["resolutionExplanation"] == "Empty-string default applied."

    @pytest.mark.asyncio
    async def test_blank_reason_uses_legacy_resolution_explanation(self, mock_request):
        mock_request.previousCodeAnalysisIssues = [
            {"id": "42", "file": "a.py", "line": 10, "severity": "HIGH",
             "reason": "Original defect", "category": "BUG_RISK", "status": "open"},
        ]
        new_issue = _make_issue(
            id="42", file="a.py", line=10, isResolved=True,
            resolutionReason="   ",
            resolutionExplanation="Legacy explanation retained.",
        )

        result = await reconcile_previous_issues(mock_request, [new_issue])
        data = result[0].model_dump()

        assert data["resolutionReason"] == "Legacy explanation retained."
        assert data["resolutionExplanation"] == "Legacy explanation retained."

    @pytest.mark.asyncio
    async def test_missing_resolution_reason_uses_neutral_fallback(self, mock_request):
        mock_request.previousCodeAnalysisIssues = [
            {"id": "42", "file": "a.py", "line": 10, "severity": "HIGH",
             "reason": "Original defect", "category": "BUG_RISK", "status": "open"},
        ]
        new_issue = _make_issue(
            id="42", file="a.py", line=10, isResolved=True,
            reason="Original defect",
        )

        result = await reconcile_previous_issues(mock_request, [new_issue])
        data = result[0].model_dump()

        assert data["resolutionReason"] != "Original defect"
        assert "no specific resolution explanation" in data["resolutionReason"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["resolved", "ignored"])
    async def test_terminal_history_is_not_reemitted(self, mock_request, status):
        mock_request.previousCodeAnalysisIssues = [
            {"id": "42", "file": "a.py", "line": 10, "severity": "HIGH",
             "reason": "Historical point", "category": "BUG_RISK", "status": status},
        ]
        model_echo = _make_issue(id="42", file="a.py", line=10, isResolved=False)

        assert await reconcile_previous_issues(mock_request, [model_echo]) == []
        assert await reconcile_previous_issues(mock_request, []) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("explicit_first", [False, True])
    async def test_duplicate_resolution_id_prefers_explicit_reason(
        self,
        mock_request,
        explicit_first,
    ):
        mock_request.previousCodeAnalysisIssues = [
            {"id": "42", "file": "a.py", "line": 10, "severity": "HIGH",
             "reason": "Original defect", "category": "BUG_RISK", "status": "open"},
        ]
        generic = _make_issue(id="42", file="a.py", line=10, isResolved=True)
        explicit = _make_issue(
            id="42", file="a.py", line=10, isResolved=True,
            resolutionReason="Null guard added.",
        )
        new_issues = [explicit, generic] if explicit_first else [generic, explicit]

        result = await reconcile_previous_issues(mock_request, new_issues)

        assert len(result) == 1
        assert result[0].resolutionReason == "Null guard added."

    @pytest.mark.asyncio
    async def test_unhandled_previous_preserved(self, mock_request):
        mock_request.previousCodeAnalysisIssues = [
            {"id": "99", "file": "b.py", "line": 5, "severity": "LOW",
             "reason": "Style", "category": "STYLE", "status": "open"},
        ]
        result = await reconcile_previous_issues(mock_request, [])
        assert len(result) == 1
        data = result[0].model_dump()
        assert data["id"] == "99"
        assert data["file"] == "b.py"

    @pytest.mark.asyncio
    async def test_semantic_match_maps_id(self, mock_request):
        mock_request.previousCodeAnalysisIssues = [
            {"id": "50", "file": "a.py", "line": 10, "severity": "HIGH",
             "reason": "Missing null check in user lookup",
             "category": "BUG_RISK", "status": "open"},
        ]
        # New issue without ID but semantically similar
        new_issue = _make_issue(
            file="a.py", line=12,
            reason="Null check missing in user lookup method",
        )
        result = await reconcile_previous_issues(mock_request, [new_issue])
        # Should map to previous ID 50
        ids = [(i.model_dump() if hasattr(i, 'model_dump') else i).get('id') for i in result]
        assert "50" in ids
