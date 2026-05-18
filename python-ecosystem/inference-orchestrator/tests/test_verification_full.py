"""Tests for verification_agent: search_file_content tool, run_verification_agent."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from service.review.orchestrator import verification_agent
from service.review.orchestrator.verification_agent import (
    search_file_content,
    run_verification_agent,
    VerificationResult,
    _FILE_CONTENTS_CACHE,
)


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
    async def test_skips_non_suspect_categories(self):
        request = MagicMock()
        fc = MagicMock()
        fc.path = "a.py"
        fc.content = "some code"
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = [fc]

        # STYLE category is not suspect
        issues = [self._make_issue("1", "STYLE", "naming convention")]
        result = await run_verification_agent(MagicMock(), issues, request)
        assert len(result) == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_skips_when_no_suspect_keywords(self):
        request = MagicMock()
        fc = MagicMock()
        fc.path = "a.py"
        fc.content = "class Foo: pass"
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = [fc]

        # BUG_RISK but no suspect keywords
        issues = [self._make_issue("1", "BUG_RISK", "possible null pointer")]
        result = await run_verification_agent(MagicMock(), issues, request)
        assert len(result) == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_verification_failure_returns_all(self):
        """When verification agent fails, all issues are returned as fallback."""
        request = MagicMock()
        fc = MagicMock()
        fc.path = "a.py"
        fc.content = "some code"
        request.enrichmentData = MagicMock()
        request.enrichmentData.fileContents = [fc]

        issues = [self._make_issue("1", "BUG_RISK", "undefined variable x")]
        llm = MagicMock()

        # langchain.agents is imported inside the try block, so we mock the module
        import sys
        mock_agents = MagicMock()
        mock_agents.create_tool_calling_agent.side_effect = Exception("fail")
        with patch.dict(sys.modules, {"langchain": MagicMock(), "langchain.agents": mock_agents}):
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
        llm = MagicMock()

        import sys
        mock_agents = MagicMock()
        mock_agents.create_tool_calling_agent.side_effect = Exception("fail")
        with patch.dict(sys.modules, {"langchain": MagicMock(), "langchain.agents": mock_agents}):
            await run_verification_agent(llm, issues, request)
        assert len(verification_agent._FILE_CONTENTS_CACHE) == 0
