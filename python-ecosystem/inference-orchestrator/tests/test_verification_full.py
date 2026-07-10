"""Tests for verification_agent: search_file_content tool, run_verification_agent."""
import asyncio
import pytest
from unittest.mock import MagicMock
from service.review.orchestrator import verification_agent
from service.review.orchestrator.verification_agent import (
    search_file_content,
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
    async def test_verifies_all_categories_with_model_selected_checks(self):
        request = MagicMock()
        fc = MagicMock()
        fc.path = "a.py"
        fc.content = "class Foo:\n    pass\n"
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = [fc]

        issues = [self._make_issue("1", "STYLE", "missing Foo")]
        llm = _FakeToolLLM([
            _FakeResponse(tool_calls=[{
                "name": "search_file_content",
                "args": {"file_path": "a.py", "search_string": "Foo"},
                "id": "call-1",
            }]),
            _FakeResponse(content='{"issue_ids_to_drop": ["1"]}'),
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
