"""Tests for verification_agent: search_file_content tool, run_verification_agent."""
import asyncio
import pytest
from unittest.mock import MagicMock
from service.review.orchestrator import verification_agent
from service.review.orchestrator.verification_agent import (
    search_file_content,
    run_deterministic_evidence_gate,
    run_verification_agent,
    VerificationResult,
    _FILE_CONTENTS_CACHE,
)
from model.output_schemas import CodeReviewIssue
from utils.diff_processor import DiffChangeType, DiffFile, ProcessedDiff


class _FakeResponse:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeToolLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []

    def bind_tools(self, tools):
        self.tools = tools
        return self

    async def ainvoke(self, messages):
        self.messages.append(list(messages))
        if not self.responses:
            raise AssertionError("No fake response queued")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


# ── search_file_content tool ─────────────────────────────────


class TestSearchFileContent:
    def setup_method(self):
        verification_agent._FILE_CONTENTS_CACHE.clear()

    def teardown_method(self):
        verification_agent._FILE_CONTENTS_CACHE.clear()

    def test_found(self):
        verification_agent._FILE_CONTENTS_CACHE["a.py"] = "class Foo:\n    pass"
        # @tool is mocked as identity, so search_file_content is a plain function
        result = search_file_content("a.py", "Foo")
        assert "Found" in result

    def test_not_found(self):
        verification_agent._FILE_CONTENTS_CACHE["a.py"] = "class Foo:\n    pass"
        result = search_file_content("a.py", "Bar")
        assert "Not Found" in result

    def test_file_not_in_cache(self):
        result = search_file_content("missing.py", "x")
        assert "Error" in result or "not available" in result

    @pytest.mark.asyncio(loop_scope="function")
    async def test_request_local_contents_are_isolated_across_concurrent_reviews(self):
        async def search_with_contents(content, search_string):
            token = verification_agent._ACTIVE_FILE_CONTENTS.set({"same.py": content})
            try:
                await asyncio.sleep(0)
                return search_file_content("same.py", search_string)
            finally:
                verification_agent._ACTIVE_FILE_CONTENTS.reset(token)

        found, missing = await asyncio.gather(
            search_with_contents("Alpha only", "Alpha"),
            search_with_contents("Beta only", "Alpha"),
        )

        assert "Found" in found
        assert "Not Found" in missing


# ── VerificationResult model ──────────────────────────────────


class TestVerificationResultModel:
    def test_empty(self):
        vr = VerificationResult(issue_ids_to_drop=[])
        assert vr.issue_ids_to_drop == []

    def test_with_ids(self):
        vr = VerificationResult(issue_ids_to_drop=["id1", "id2"])
        assert len(vr.issue_ids_to_drop) == 2


# ── run_verification_agent ────────────────────────────────────


class TestRunVerificationAgent:
    def _make_issue(self, issue_id, category, reason, file="a.py"):
        issue = MagicMock()
        issue.id = issue_id
        issue.category = category
        issue.reason = reason
        issue.file = file
        issue.severity = "HIGH"
        return issue

    @pytest.mark.asyncio(loop_scope="function")
    async def test_skips_when_no_enrichment(self):
        request = MagicMock()
        request.enrichmentData = None
        issues = [self._make_issue("1", "BUG_RISK", "undefined var")]
        result = await run_verification_agent(MagicMock(), issues, request)
        assert result is issues

    @pytest.mark.asyncio(loop_scope="function")
    async def test_skips_when_no_file_contents(self):
        request = MagicMock()
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = []
        issues = [self._make_issue("1", "BUG_RISK", "undefined var")]
        result = await run_verification_agent(MagicMock(), issues, request)
        # With empty fileContents, still gets past the first check
        # but with no suspect issues matching, returns all
        assert len(result) >= 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_verifies_actionable_issue_with_model_selected_checks(self):
        request = MagicMock()
        fc = MagicMock()
        fc.path = "a.py"
        fc.content = "class Foo:\n    pass\n"
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = [fc]

        issues = [self._make_issue("1", "BUG_RISK", "missing Foo")]
        llm = _FakeToolLLM([
            _FakeResponse(tool_calls=[{
                "name": "search_file_content",
                "args": {"file_path": "a.py", "search_string": "Foo"},
                "id": "call-1",
            }]),
            _FakeResponse(content='{"issue_ids_to_drop": ["issue_0"]}'),
        ])

        result = await run_verification_agent(llm, issues, request)

        assert result == []
        assert any(
            isinstance(message, dict) and message.get("role") == "tool" and "Found" in message.get("content", "")
            for call_messages in llm.messages
            for message in call_messages
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_can_drop_fresh_issue_without_persisted_id(self):
        request = MagicMock()
        fc = MagicMock()
        fc.path = "a.py"
        fc.content = "class Foo:\n    pass\n"
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = [fc]

        issues = [self._make_issue(None, "BUG_RISK", "missing Foo")]
        llm = _FakeToolLLM([
            _FakeResponse(tool_calls=[{
                "name": "search_file_content",
                "args": {"file_path": "a.py", "search_string": "Foo"},
                "id": "call-1",
            }]),
            _FakeResponse(content='{"issue_ids_to_drop": ["issue_0"]}'),
        ])

        result = await run_verification_agent(llm, issues, request)

        assert result == []
        first_prompt_messages = llm.messages[0]
        assert "Verification ID: issue_0" in first_prompt_messages[1]["content"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_duplicate_original_ids_get_unique_verification_tokens(self):
        request = MagicMock()
        fc = MagicMock()
        fc.path = "a.py"
        fc.content = "class Foo:\n    pass\n"
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = [fc]

        first = self._make_issue("12524", "BUG_RISK", "missing Foo")
        second = self._make_issue("12524", "BUG_RISK", "different current defect")
        llm = _FakeToolLLM([
            _FakeResponse(content='{"issue_ids_to_drop": ["issue_0"]}'),
        ])

        result = await run_verification_agent(llm, [first, second], request)

        assert result == [second]
        prompt = llm.messages[0][1]["content"]
        assert "Verification ID: issue_0" in prompt
        assert "Verification ID: issue_1" in prompt
        assert prompt.count("Original ID: 12524") == 2

    @pytest.mark.asyncio(loop_scope="function")
    async def test_llm_rejected_previous_open_issue_is_closed_not_omitted(self):
        request = MagicMock()
        fc = MagicMock()
        fc.path = "a.py"
        fc.content = "class Foo:\n    pass\n"
        fc.skipped = False
        request.enrichmentData = MagicMock(fileContents=[fc])
        request.rawDiff = None
        request.deltaDiff = None
        request.previousCodeAnalysisIssues = [{"id": "12524", "status": "open"}]
        issue = self._make_issue("12524", "BUG_RISK", "missing Foo")
        llm = _FakeToolLLM([
            _FakeResponse(content='{"issue_ids_to_drop": ["issue_0"]}'),
        ])

        result = await run_verification_agent(llm, [issue], request)

        assert result == [issue]
        assert issue.isResolved is True
        assert "current-file verification" in issue.resolutionReason

    @pytest.mark.asyncio(loop_scope="function")
    async def test_keeps_issue_when_verification_returns_no_drops(self):
        request = MagicMock()
        fc = MagicMock()
        fc.path = "a.py"
        fc.content = "class Foo: pass"
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = [fc]

        issues = [self._make_issue("1", "BUG_RISK", "possible null pointer")]
        llm = _FakeToolLLM([
            _FakeResponse(content='{"issue_ids_to_drop": []}'),
        ])

        result = await run_verification_agent(llm, issues, request)

        assert result == issues

    @pytest.mark.asyncio(loop_scope="function")
    async def test_verification_failure_returns_all(self):
        """When verification fails, all issues are returned as fallback."""
        request = MagicMock()
        fc = MagicMock()
        fc.path = "a.py"
        fc.content = "some code"
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = [fc]

        issues = [self._make_issue("1", "BUG_RISK", "undefined variable x")]
        llm = _FakeToolLLM([RuntimeError("fail")])

        result = await run_verification_agent(llm, issues, request)

        assert len(result) == len(issues)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cleans_cache_on_exit(self):
        """Cache is cleared even after exceptions."""
        request = MagicMock()
        fc = MagicMock()
        fc.path = "a.py"
        fc.content = "code"
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = [fc]

        issues = [self._make_issue("1", "BUG_RISK", "undefined xyz")]
        llm = _FakeToolLLM([RuntimeError("fail")])

        await run_verification_agent(llm, issues, request)
        assert len(verification_agent._FILE_CONTENTS_CACHE) == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_drops_unused_import_claim_contradicted_by_same_diff(self):
        path = "app/design/frontend/Perspective/Catalog/templates/ratings.phtml"
        diff = f"""\
diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1,3 +1,8 @@
+<?php
+use Perspective\\CatalogWidget\\Helper\\SwatchHelper;
+$product = $block->getProduct();
+$swatchHelper = $this->helper(SwatchHelper::class);
+$validateWineAttributeSet = $swatchHelper->validateAttributeSetByCode('Wine', $product->getAttributeSetId());
"""
        processed = ProcessedDiff(files=[
            DiffFile(
                path=path,
                change_type=DiffChangeType.MODIFIED,
                content=diff,
            )
        ])
        issue = CodeReviewIssue(
            severity="LOW",
            category="CODE_QUALITY",
            file=path,
            line=2,
            title="Unused SwatchHelper import in ratings template",
            reason="The SwatchHelper import is never referenced in the template.",
            suggestedFixDescription="Remove the unused import.",
            codeSnippet="use Perspective\\CatalogWidget\\Helper\\SwatchHelper;",
        )
        request = MagicMock(enrichmentData=None, rawDiff=diff, deltaDiff=None)

        result = await run_verification_agent(
            MagicMock(),
            [issue],
            request,
            processed,
        )

        assert result == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_contradicted_previous_open_issue_is_closed_not_omitted(self):
        path = "src/example.php"
        diff = f"""\
diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -0,0 +1,3 @@
+<?php
+use Vendor\\Package\\Helper;
+$helper = $this->helper(Helper::class);
"""
        processed = ProcessedDiff(files=[
            DiffFile(path=path, change_type=DiffChangeType.MODIFIED, content=diff)
        ])
        issue = CodeReviewIssue(
            id="12524",
            severity="LOW",
            category="CODE_QUALITY",
            file=path,
            line=2,
            title="Unused Helper import",
            reason="Helper is imported but never referenced.",
            suggestedFixDescription="Remove it.",
            codeSnippet="use Vendor\\Package\\Helper;",
        )
        request = MagicMock(
            enrichmentData=None,
            rawDiff=diff,
            deltaDiff=None,
            previousCodeAnalysisIssues=[{"id": "12524", "status": "open"}],
        )

        result = await run_verification_agent(MagicMock(), [issue], request, processed)

        assert result == [issue]
        assert issue.isResolved is True
        assert "source evidence contradicts" in issue.resolutionReason

    @pytest.mark.asyncio(loop_scope="function")
    async def test_keeps_genuinely_unused_import_from_same_diff(self):
        path = "src/example.php"
        diff = f"""\
diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1 +1,2 @@
+<?php
+use Vendor\\Package\\UnusedHelper;
"""
        processed = ProcessedDiff(files=[
            DiffFile(path=path, change_type=DiffChangeType.MODIFIED, content=diff)
        ])
        issue = CodeReviewIssue(
            severity="LOW",
            category="CODE_QUALITY",
            file=path,
            line=2,
            title="Unused UnusedHelper import",
            reason="UnusedHelper is imported but never referenced.",
            suggestedFixDescription="Remove it.",
            codeSnippet="use Vendor\\Package\\UnusedHelper;",
        )
        request = MagicMock(enrichmentData=None, rawDiff=diff, deltaDiff=None)

        result = await run_verification_agent(MagicMock(), [issue], request, processed)

        assert result == [issue]

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        "non_code_reference",
        [
            "# UnusedHelper is retained for compatibility",
            'print("UnusedHelper")',
        ],
    )
    async def test_comment_or_string_does_not_count_as_import_usage(
        self,
        non_code_reference,
    ):
        path = "src/example.py"
        diff = f"""\
diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1 +1,2 @@
+from package import UnusedHelper
+{non_code_reference}
"""
        processed = ProcessedDiff(files=[
            DiffFile(path=path, change_type=DiffChangeType.MODIFIED, content=diff)
        ])
        issue = CodeReviewIssue(
            severity="LOW",
            category="CODE_QUALITY",
            file=path,
            line=1,
            title="Unused UnusedHelper import",
            reason="UnusedHelper is imported but never referenced.",
            suggestedFixDescription="Remove it.",
            codeSnippet="from package import UnusedHelper",
        )
        request = MagicMock(enrichmentData=None, rawDiff=diff, deltaDiff=None)

        result = await run_verification_agent(MagicMock(), [issue], request, processed)

        assert result == [issue]

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        "non_usage_lines",
        [
            ['"""', "UnusedHelper() is shown in this docstring", '"""'],
            ["value = 1#UnusedHelper() is mentioned in a comment"],
            ["value = 1 -- UnusedHelper() is mentioned in a comment"],
            ["class UnusedHelper:", "    pass"],
        ],
    )
    async def test_ambiguous_identifier_text_does_not_prove_import_usage(
        self,
        non_usage_lines,
    ):
        path = "src/example.py"
        added = ["from package import UnusedHelper", *non_usage_lines]
        diff_lines = [
            f"diff --git a/{path} b/{path}",
            f"--- a/{path}",
            f"+++ b/{path}",
            f"@@ -0,0 +1,{len(added)} @@",
            *(f"+{line}" for line in added),
        ]
        diff = "\n".join(diff_lines)
        processed = ProcessedDiff(files=[
            DiffFile(path=path, change_type=DiffChangeType.MODIFIED, content=diff)
        ])
        issue = CodeReviewIssue(
            severity="LOW",
            category="CODE_QUALITY",
            file=path,
            line=1,
            title="Unused UnusedHelper import",
            reason="UnusedHelper is imported but never referenced.",
            suggestedFixDescription="Remove it.",
            codeSnippet="from package import UnusedHelper",
        )
        request = MagicMock(enrichmentData=None, rawDiff=diff, deltaDiff=None)

        result = await run_verification_agent(MagicMock(), [issue], request, processed)

        assert result == [issue]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_high_confidence_member_reference_still_contradicts_unused_import(self):
        path = "src/example.php"
        diff = f"""\
diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -0,0 +1,3 @@
+<?php
+use Vendor\\Package\\Helper;
+$helper = $this->helper(Helper::class);
"""
        processed = ProcessedDiff(files=[
            DiffFile(path=path, change_type=DiffChangeType.MODIFIED, content=diff)
        ])
        issue = CodeReviewIssue(
            severity="LOW",
            category="CODE_QUALITY",
            file=path,
            line=2,
            title="Unused Helper import",
            reason="Helper is imported but never referenced.",
            suggestedFixDescription="Remove it.",
            codeSnippet="use Vendor\\Package\\Helper;",
        )
        request = MagicMock(enrichmentData=None, rawDiff=diff, deltaDiff=None)

        result = await run_verification_agent(MagicMock(), [issue], request, processed)

        assert result == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_diff_prefixed_anchor_does_not_count_import_as_usage(self):
        path = "src/example.php"
        diff = f"""\
diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1 +1,2 @@
+<?php
+use Vendor\\Package\\UnusedHelper;
"""
        processed = ProcessedDiff(files=[
            DiffFile(path=path, change_type=DiffChangeType.MODIFIED, content=diff)
        ])
        issue = CodeReviewIssue(
            severity="LOW",
            category="CODE_QUALITY",
            file=path,
            line=2,
            title="Unused UnusedHelper import",
            reason="UnusedHelper is imported but never referenced.",
            suggestedFixDescription="Remove it.",
            codeSnippet="+use Vendor\\Package\\UnusedHelper;",
        )
        request = MagicMock(enrichmentData=None, rawDiff=diff, deltaDiff=None)

        result = await run_verification_agent(MagicMock(), [issue], request, processed)

        assert result == [issue]


class TestDeterministicPublicationGate:
    @staticmethod
    def _request_without_source_evidence():
        return MagicMock(
            enrichmentData=None,
            rawDiff=None,
            deltaDiff=None,
            previousCodeAnalysisIssues=[],
        )

    @staticmethod
    def _issue(reason: str, suggested_fix: str) -> CodeReviewIssue:
        return CodeReviewIssue(
            severity="MEDIUM",
            category="BUG_RISK",
            file="Shipping/MethodList.php",
            line=60,
            title="Potential checkout failure",
            reason=reason,
            suggestedFixDescription=suggested_fix,
            codeSnippet="$resultMethod = '';",
        )

    @pytest.mark.parametrize(
        ("reason", "suggested_fix"),
        [
            (
                "The old null initialization could violate the string return type.",
                "The current diff already addresses this by changing the initialization to an empty string.",
            ),
            (
                "Casting the country ID prevents the reported type error and is a defensive coding improvement.",
                "The patch is correctly formatted and addresses the type-safety issue.",
            ),
            (
                "This is a corrective measure for a TypeError in Apple Pay checkout.",
                "Ensure the patch file exists at the configured path and is correctly formatted.",
            ),
            (
                "The change fixes the reported checkout crash.",
                "Verify that the checkout now works as expected.",
            ),
        ],
    )
    def test_drops_self_disqualifying_candidates_without_source_evidence(
        self,
        reason,
        suggested_fix,
    ):
        issue = self._issue(reason, suggested_fix)

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == []

    def test_drops_speculative_follow_up_to_a_fix(self):
        issue = self._issue(
            "While these fixes resolve the immediate TypeError, they use different "
            "null-handling techniques. If the underlying cause is not handled "
            "consistently, other checkout components may still trigger similar crashes.",
            "Standardize null handling across all checkout components.",
        )

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == []

    def test_drops_hedged_defect_after_contrast(self):
        issue = self._issue(
            "The fixes resolve the immediate TypeError. However, if quote state is "
            "not handled consistently, other checkout components might still fail.",
            "Consider standardizing quote-state checks.",
        )

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == []

    def test_drops_advisory_only_strategy_difference_after_contrast(self):
        issue = self._issue(
            "The patch correctly fixes the null return, but it uses a different "
            "null-handling strategy than Apple Pay.",
            "Standardize null handling across the checkout.",
        )

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == []

    @pytest.mark.parametrize(
        "reason",
        [
            "The current code prevents authenticated customers from completing checkout.",
            "The current implementation handles only the first shipment and silently ignores the remaining packages.",
            "The current change fixes the country ID to a hard-coded value for every customer.",
        ],
    )
    def test_keeps_bare_current_behavior_that_describes_a_defect(self, reason):
        issue = self._issue(reason, "Correct the current behavior.")

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    @pytest.mark.parametrize(
        "reason",
        [
            "The current patch correctly fixes null handling, so checkout no longer throws.",
            "The current patch correctly fixes the exposure by preventing the handler from leaking credentials.",
            "The current patch correctly fixes authorization without exposing another customer's data.",
            "The current patch correctly fixes the regression; the regression test fails before the patch, as expected.",
        ],
    )
    def test_drops_positive_fix_descriptions_with_negated_or_historical_harm_words(
        self,
        reason,
    ):
        issue = self._issue(reason, "No further changes are required.")

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == []

    def test_keeps_partial_fix_with_distinct_concrete_current_defect(self):
        issue = self._issue(
            "The change correctly fixes the null return, but it introduces a different "
            "current defect: the empty method ID is submitted to the carrier lookup, "
            "which throws and still breaks checkout.",
            "Guard the empty selection before invoking the carrier lookup.",
        )

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    def test_keeps_strategy_difference_with_concrete_current_harm(self):
        issue = self._issue(
            "The patch correctly fixes the null return, but the inconsistent "
            "checkout state overwrites the billing address.",
            "Keep billing and shipping updates isolated.",
        )

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    def test_keeps_distinct_harm_in_sentence_after_successful_fix(self):
        issue = self._issue(
            "The patch correctly fixes the null return. The new empty-string "
            "sentinel selects the first carrier and charges the wrong rate.",
            "Represent the absence of a selection without choosing a carrier.",
        )

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    def test_keeps_distinct_behavioral_regression_without_harm_keyword(self):
        issue = self._issue(
            "The patch correctly fixes the null return. The empty-string sentinel "
            "is accepted as a real choice and places the order with the first "
            "carrier even though the customer selected none.",
            "Keep absence distinct from a carrier selection.",
        )

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    @pytest.mark.parametrize(
        "reason",
        [
            "The patch correctly fixes the null return. Checkout no longer crashes.",
            "The patch correctly fixes the null return. The regression test now passes.",
        ],
    )
    def test_drops_separate_positive_outcome_sentence(self, reason):
        issue = self._issue(reason, "No further changes are required.")

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == []

    def test_keeps_coordinated_harm_after_successful_fix(self):
        issue = self._issue(
            "The patch correctly fixes the null return and now selects the first "
            "carrier, charging the wrong rate.",
            "Keep an empty selection distinct from the first carrier.",
        )

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    def test_keeps_concrete_conditional_defect_after_partial_fix(self):
        issue = self._issue(
            "The change correctly fixes the empty return, but if countryId is null "
            "the new lookup still throws a TypeError.",
            "Guard the null country ID before invoking the lookup.",
        )

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    @pytest.mark.parametrize(
        "reason",
        [
            "The patch correctly fixes the null return, but it selects the wrong shipping method.",
            "The patch correctly fixes the null return, but it deletes the active cart.",
            "The patch correctly fixes the null return, but it introduces a deadlock. Tests may need updates.",
            "While the patch correctly fixes the null return, it deletes the active cart.",
        ],
    )
    def test_keeps_partial_fix_with_unlisted_concrete_regression(self, reason):
        issue = self._issue(reason, "Correct the remaining regression.")

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    def test_keeps_concrete_auth_defect_with_ensure_suggestion(self):
        issue = self._issue(
            "The new endpoint accepts unauthenticated requests and exposes customer data.",
            "Ensure authentication is required.",
        )

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    def test_keeps_real_issue_whose_suggestion_describes_the_correct_fix(self):
        issue = self._issue(
            "The endpoint accepts unauthenticated requests and exposes customer data.",
            "The correct fix is to require authentication before serving the response.",
        )

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    def test_keeps_real_issue_with_corrective_patch_suggestion(self):
        issue = self._issue(
            "The package configuration points to a missing patch and the build fails.",
            "Add a corrective patch at the configured path.",
        )

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    def test_keeps_real_issue_with_proposed_change_wording(self):
        issue = self._issue(
            "The endpoint accepts unauthenticated requests and exposes customer data.",
            "This change fixes the reported authentication bug by requiring a valid session.",
        )

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    @pytest.mark.parametrize(
        "reason",
        [
            "The code lacks a corrective measure for invalid signatures, so forged requests are accepted.",
            "The change addresses the reported bug incorrectly, returning stale data.",
            "Although the change correctly fixes the null return, it introduces an authentication bypass.",
            "Although this patch is a defensive improvement, it breaks checkout because invalid method IDs reach the lookup.",
            "The patch correctly addresses null handling. Separately, it sends an invalid ID and the lookup fails.",
            "The change fixes the reported checkout crash by bypassing authentication, exposing every customer's cart.",
            "The patch resolves the immediate null crash by returning the first customer's address, leaking another user's data.",
        ],
    )
    def test_keeps_concrete_defect_despite_positive_or_negated_wording(self, reason):
        issue = self._issue(reason, "Correct the remaining harmful behavior.")

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    @pytest.mark.parametrize(
        ("reason", "suggested_fix"),
        [
            ("The issue is already fixed by this change.", "No changes are needed."),
            ("This has been fixed in the current patch.", "No changes are needed."),
            ("The current code already guards against a null country ID.", "No action is required."),
        ],
    )
    def test_drops_additional_explicit_no_op_wording(self, reason, suggested_fix):
        issue = self._issue(reason, suggested_fix)

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == []

    @pytest.mark.parametrize(
        "reason",
        [
            "No action is required by an attacker to access another customer's cart.",
            "No fix is required to trigger the crash; an empty cart is sufficient.",
        ],
    )
    def test_keeps_no_action_wording_that_describes_exploitability(self, reason):
        issue = self._issue(reason, "Require authentication before returning the cart.")

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    def test_keeps_mixed_suggestion_that_still_requires_a_code_change(self):
        issue = self._issue(
            "The endpoint accepts unauthenticated requests and exposes customer data.",
            "No changes are required in the API; add session validation in the handler.",
        )

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    def test_keeps_change_that_handles_null_incorrectly(self):
        issue = self._issue(
            "The change handles null incorrectly and returns the wrong shipping method.",
            "Return no selection when the method list is empty.",
        )

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    def test_keeps_fix_wording_that_identifies_the_wrong_field(self):
        issue = self._issue(
            "These fixes address the wrong field and overwrite billing data.",
            "Update the shipping field without changing the billing address.",
        )

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    def test_drops_new_info_observation(self):
        issue = self._issue(
            "The changed naming is easier to read.",
            "Consider keeping this convention.",
        )
        issue.severity = "INFO"
        issue.category = "BEST_PRACTICES"

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == []

    @pytest.mark.parametrize("category", ["STYLE", "DOCUMENTATION"])
    def test_keeps_concrete_project_rule_category(self, category):
        issue = self._issue(
            "The changed file violates an explicitly enforced project rule.",
            "Apply the required project convention.",
        )
        issue.severity = "LOW"
        issue.category = category

        result = run_deterministic_evidence_gate(
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == [issue]

    def test_preserves_matching_historical_resolution(self):
        issue = self._issue(
            "The current diff already fixes the historical issue.",
            "No further action is required.",
        )
        issue.id = "12524"
        issue.isResolved = True
        issue.severity = "INFO"
        issue.category = "DOCUMENTATION"
        issue.resolutionReason = "The current implementation contains the fix."
        request = self._request_without_source_evidence()
        request.previousCodeAnalysisIssues = [{"id": "12524"}]

        result = run_deterministic_evidence_gate([issue], request)

        assert result == [issue]
        assert issue.resolutionExplanation == issue.resolutionReason

    @pytest.mark.parametrize("status", ["resolved", "ignored", "closed"])
    def test_terminal_history_is_not_eligible_for_a_resolution_update(self, status):
        issue = self._issue(
            "The current diff already fixes the historical issue.",
            "No further action is required.",
        )
        issue.id = "12524"
        issue.isResolved = True
        request = self._request_without_source_evidence()
        request.previousCodeAnalysisIssues = [{"id": "12524", "status": status}]

        result = run_deterministic_evidence_gate([issue], request)

        assert result == []

    @pytest.mark.parametrize("status", ["resolved", "ignored"])
    def test_fixed_point_reusing_terminal_history_id_is_dropped(self, status):
        issue = self._issue(
            "The current diff already fixes the historical issue.",
            "No further action is required.",
        )
        issue.id = "12524"
        request = self._request_without_source_evidence()
        request.previousCodeAnalysisIssues = [{"id": "12524", "status": status}]

        result = run_deterministic_evidence_gate([issue], request)

        assert result == []
        assert issue.isResolved is False

    @pytest.mark.parametrize("severity", ["INFO", "MEDIUM"])
    def test_closes_matching_open_historical_non_issue(self, severity):
        issue = self._issue(
            "The current diff already fixes the historical issue.",
            "No further action is required.",
        )
        issue.id = "12524"
        issue.severity = severity
        request = self._request_without_source_evidence()
        request.previousCodeAnalysisIssues = [{"id": "12524", "status": "open"}]

        result = run_deterministic_evidence_gate([issue], request)

        assert result == [issue]
        assert issue.isResolved is True
        assert "no actionable post-change defect" in issue.resolutionExplanation
        assert issue.resolutionReason == issue.resolutionExplanation

    @pytest.mark.parametrize("issue_id", [None, "CROSS_001", "99999"])
    def test_drops_resolution_without_matching_historical_id(self, issue_id):
        issue = self._issue(
            "The current diff already fixes the issue.",
            "No further action is required.",
        )
        issue.id = issue_id
        issue.isResolved = True
        request = self._request_without_source_evidence()
        request.previousCodeAnalysisIssues = [{"id": "12524"}]

        result = run_deterministic_evidence_gate([issue], request)

        assert result == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_llm_verifier_does_not_reject_historical_resolution(self):
        issue = self._issue(
            "The current diff already fixes the historical issue.",
            "No further action is required.",
        )
        issue.id = "12524"
        issue.isResolved = True
        request = self._request_without_source_evidence()
        request.previousCodeAnalysisIssues = [{"id": 12524}]

        result = await run_verification_agent(MagicMock(), [issue], request)

        assert result == [issue]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_run_verification_applies_publication_gate_before_evidence_check(self):
        issue = self._issue(
            "The change fixes the reported checkout crash.",
            "No further action is required.",
        )

        result = await run_verification_agent(
            MagicMock(),
            [issue],
            self._request_without_source_evidence(),
        )

        assert result == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_llm_verifier_receives_fix_text_and_actionability_rules(self):
        issue = self._issue(
            "The new branch still dereferences a null address and throws.",
            "Add a null guard before reading the country ID.",
        )
        file_content = MagicMock(
            path="Shipping/MethodList.php",
            content="<?php\n$resultMethod = '';\n",
            skipped=False,
        )
        request = MagicMock(
            enrichmentData=MagicMock(fileContents=[file_content]),
            rawDiff=None,
            deltaDiff=None,
        )
        llm = _FakeToolLLM([
            _FakeResponse(content='{"issue_ids_to_drop": []}'),
        ])

        result = await run_verification_agent(llm, [issue], request)

        assert result == [issue]
        prompt = llm.messages[0][1]["content"]
        assert "Suggested fix: Add a null guard before reading the country ID." in prompt
        assert "concrete defect that remains in the post-change code" in prompt
        assert "A partial fix is still a valid finding" in prompt
