"""Real Cal12 suppression plus binding-ambiguity controls; no model inference."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from model.output_schemas import CodeReviewIssue
from service.review.orchestrator.verification_agent import run_deterministic_evidence_gate, run_verification_agent
from tests.test_verification_full import _FakeToolLLM, _FakeResponse


def request_for(issue, source):
    return SimpleNamespace(rawDiff=None, deltaDiff=None, previousCodeAnalysisIssues=[],
        enrichmentData=SimpleNamespace(fileContents=[SimpleNamespace(path=issue.file, content=source, skipped=False)]))


@pytest.mark.asyncio
async def test_real_cal12_unused_binding_reaches_normal_verification():
    fixture = json.loads((Path(__file__).parent / "fixtures/cal12_unused_import.json").read_text())
    issue = CodeReviewIssue.model_validate(fixture["original_issue"])
    request = request_for(issue, fixture["exact_source"])
    assert run_deterministic_evidence_gate([issue], request) == [issue]
    llm = _FakeToolLLM([_FakeResponse(content='{"assessments":[{"issue_id":"issue_0","verdict":"unknown","reason":"Lint configuration not checked"}]}')])
    assert await run_verification_agent(llm, [issue], request) == [issue]
    assert len(llm.messages) == 1


@pytest.mark.parametrize("body", [
    "trpc.viewer.useQuery();",
    "trpc.viewer. useQuery();",
    "trpc.viewer.\n useQuery();",
    "trpc.viewer?.useQuery();",
    "trpc.viewer?.\n useQuery();",
    "function run(useQuery) { useQuery(); }",
    "const run = (useQuery) => useQuery();",
    "const { useQuery } = other; useQuery();",
    "const [useQuery] = other; useQuery();",
    "function run(useQuery) { useQuery.fetch(); }",
    "// useQuery() is not a reference\nconst message = 'useQuery()';",
    "const message = `useQuery()`;",
])
def test_ambiguous_identifier_cannot_disprove_unused_import(body):
    snippet = 'import { useQuery } from "@tanstack/react-query";'
    issue = CodeReviewIssue(file="hook.ts", line=1, title="Remove unused useQuery import",
        reason="The useQuery import is unused.", severity="LOW", category="CODE_QUALITY",
        suggestedFixDescription="Remove import", codeSnippet=snippet)
    assert run_deterministic_evidence_gate([issue], request_for(issue, snippet + "\n" + body)) == [issue]
