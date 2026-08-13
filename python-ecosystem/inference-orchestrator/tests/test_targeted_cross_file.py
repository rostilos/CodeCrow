import json
from types import SimpleNamespace

import pytest

from model.dtos import ReviewRequestDto
from model.enrichment import FileContentDto, FileRelationshipDto, PrEnrichmentDataDto
from model.enums import RelationshipType
from model.multi_stage import ReviewContextRequest, ReviewPlan
from service.review.orchestrator.targeted_cross_file import (
    build_investigation_tickets,
    run_targeted_cross_file,
)
from service.review.orchestrator.exact_context import (
    ExactContextResolver,
    ReviewFollowupBudget,
)
from utils.diff_processor import DiffProcessor


def _request() -> ReviewRequestDto:
    return ReviewRequestDto(
        projectId=1,
        projectVcsRepoSlug="org/repo",
        projectVcsWorkspace="org",
        projectWorkspace="org",
        projectNamespace="org",
        targetBranchName="main",
        sourceBranchName="feature",
        currentCommitHash="abc123",
        commitHash="abc123",
        analysisType="PULL_REQUEST",
        analysisMode="FULL",
        aiProvider="test",
        aiModel="test",
        aiApiKey="test",
        changedFiles=["src/api.py"],
        deletedFiles=[],
        enrichmentData=PrEnrichmentDataDto(
            fileContents=[
                FileContentDto(
                    path="src/api.py",
                    content="def public_api(value):\n    return value\n",
                ),
                FileContentDto(
                    path="src/client.py",
                    content="from .api import public_api\nresult = public_api(None)\n",
                ),
            ],
            relationships=[
                FileRelationshipDto(
                    sourceFile="src/client.py",
                    targetFile="src/api.py",
                    relationshipType=RelationshipType.IMPORTS,
                    matchedOn="public_api",
                    strength=10,
                )
            ],
        ),
    )


def _diff():
    return DiffProcessor().process(
        """diff --git a/src/api.py b/src/api.py
--- a/src/api.py
+++ b/src/api.py
@@ -1,2 +1,2 @@
-def api(value):
+def public_api(value):
     return value
"""
    )


def _plan(*concerns: str) -> ReviewPlan:
    return ReviewPlan(
        analysis_summary="",
        file_groups=[],
        cross_file_concerns=list(concerns),
    )


def test_edge_alone_does_not_admit_ticket():
    tickets, incomplete = build_investigation_tickets(
        _request(), _diff(), _plan(), []
    )
    # The visible public contract change supplies the second admission fact.
    assert len(tickets) == 1
    assert incomplete == []


def test_generic_non_contract_edge_is_not_admitted():
    request = _request()
    diff = DiffProcessor().process(
        """diff --git a/src/api.py b/src/api.py
--- a/src/api.py
+++ b/src/api.py
@@ -1 +1 @@
-value = 1
+value = 2
"""
    )
    tickets, _ = build_investigation_tickets(request, diff, _plan(), [])
    assert tickets == []


def test_exact_edge_overflow_is_reported_incomplete():
    request = _request()
    request.enrichmentData.fileContents.append(FileContentDto(
        path="src/client_two.py",
        content="from .api import public_api\npublic_api('two')\n",
    ))
    request.enrichmentData.relationships.append(FileRelationshipDto(
        sourceFile="src/client_two.py",
        targetFile="src/api.py",
        relationshipType=RelationshipType.IMPORTS,
        matchedOn="public_api",
        strength=10,
    ))

    tickets, incomplete = build_investigation_tickets(
        request,
        _diff(),
        _plan(),
        [],
        max_tickets=1,
    )

    assert len(tickets) == 1
    assert len(incomplete) == 1
    assert incomplete[0].endswith(":ticket_limit_exceeded")


def test_host_bound_cross_file_request_admits_exact_edge_after_provisional_drop():
    diff = DiffProcessor().process(
        """diff --git a/src/api.py b/src/api.py
--- a/src/api.py
+++ b/src/api.py
@@ -1 +1 @@
-value = 1
+value = 2
"""
    )
    context_request = ReviewContextRequest(
        requestId="ctx-1",
        kind="CROSS_FILE",
        question="Does src/client.py rely on the changed src/api.py value?",
        targetPath="src/client.py",
        relationship="IMPORTS public_api",
        requiredEvidence="The exact current client use of public_api.",
        relatedIssueIndexes=[0],
        originatingPaths=["src/api.py"],
    )

    tickets, incomplete = build_investigation_tickets(
        _request(),
        diff,
        _plan(),
        [],
        context_requests=[context_request],
    )

    assert len(tickets) == 1
    assert incomplete == []


@pytest.mark.asyncio
async def test_unbound_cross_file_request_is_reported_incomplete():
    request = ReviewContextRequest(
        requestId="ctx-unbound",
        kind="CROSS_FILE",
        question="Does an unchanged caller still use this contract?",
        targetPath="src/client.py",
        relationship="IMPORTS public_api",
        requiredEvidence="The exact current caller.",
        relatedIssueIndexes=[0],
        originatingPaths=[],
    )

    class Llm:
        async def ainvoke(self, _messages):
            return SimpleNamespace(content='{"issues":[]}')

    result = await run_targeted_cross_file(
        Llm(),
        _request(),
        _diff(),
        _plan(),
        [],
        context_requests=[request],
        exact_context_resolver=ExactContextResolver(_request()),
        available_calls=1,
    )

    assert any(
        item.startswith("XREQ-") and item.endswith(":origin_unavailable")
        for item in result.incomplete_tickets
    )


@pytest.mark.asyncio
async def test_cross_file_request_fetches_unchanged_target_at_reviewed_head():
    request = _request()
    request.enrichmentData.fileContents = [
        FileContentDto(
            path="src/api.py",
            content="def public_api(value):\n    return value\n",
        )
    ]

    async def exact_reader(**kwargs):
        assert kwargs["path"] == "src/client.py"
        assert kwargs["revision"] == "abc123"
        return {
            "content": "from .api import public_api\npublic_api(None)\n",
            "startLine": 1,
            "endLine": 2,
        }

    resolver = ExactContextResolver(request, exact_reader=exact_reader)
    context_request = ReviewContextRequest(
        requestId="ctx-caller",
        kind="CROSS_FILE",
        question="Does the unchanged client satisfy the public_api contract?",
        targetPath="src/client.py",
        startLine=1,
        endLine=20,
        relationship="IMPORTS public_api",
        requiredEvidence="The exact current client call arguments.",
        relatedIssueIndexes=[0],
        originatingPaths=["src/api.py"],
    )

    class Llm:
        async def ainvoke(self, _messages):
            return SimpleNamespace(content='{"issues":[]}')

    result = await run_targeted_cross_file(
        Llm(),
        request,
        _diff(),
        _plan(),
        [],
        context_requests=[context_request],
        exact_context_resolver=resolver,
        available_calls=1,
    )

    assert result.admitted_tickets == 1
    assert result.calls_used == 1
    assert result.incomplete_tickets == ()


@pytest.mark.asyncio
async def test_one_remaining_call_batches_multiple_exact_context_tickets():
    request = _request()
    request.enrichmentData.fileContents.append(FileContentDto(
        path="src/client_two.py",
        content="from .api import public_api\npublic_api('two')\n",
    ))
    requests = [
        ReviewContextRequest(
            requestId=f"ctx-{index}",
            kind="CROSS_FILE",
            question=f"Does {path} still satisfy the changed API contract?",
            targetPath=path,
            startLine=1,
            endLine=20,
            relationship="IMPORTS public_api",
            requiredEvidence="The exact current caller arguments.",
            relatedIssueIndexes=[0],
            originatingPaths=["src/api.py"],
        )
        for index, path in enumerate(
            ("src/client.py", "src/client_two.py"),
            start=1,
        )
    ]

    class Llm:
        calls = 0

        async def ainvoke(self, _messages):
            self.calls += 1
            return SimpleNamespace(content='{"issues":[]}')

    llm = Llm()
    budget = ReviewFollowupBudget(max_calls=1)
    result = await run_targeted_cross_file(
        llm,
        request,
        _diff(),
        _plan(),
        [],
        context_requests=requests,
        exact_context_resolver=ExactContextResolver(request),
        followup_budget=budget,
        available_calls=1,
    )

    assert result.admitted_tickets >= 2
    assert result.calls_used == 1
    assert llm.calls == 1
    assert not any(
        "followup_budget_unavailable" in item
        for item in result.incomplete_tickets
    )


@pytest.mark.asyncio
async def test_explicit_stage_1_request_precedes_automatic_ticket_cap():
    request = _request()
    request.enrichmentData.fileContents = [
        request.enrichmentData.fileContents[0],
        FileContentDto(
            path="src/requested.py",
            content="from .api import public_api\npublic_api('requested')\n",
        ),
    ]
    request.enrichmentData.relationships = []
    for index in range(8):
        path = f"src/automatic_{index}.py"
        request.enrichmentData.fileContents.append(FileContentDto(
            path=path,
            content="from .api import public_api\npublic_api('automatic')\n",
        ))
        request.enrichmentData.relationships.append(FileRelationshipDto(
            sourceFile=path,
            targetFile="src/api.py",
            relationshipType=RelationshipType.IMPORTS,
            matchedOn="public_api",
            strength=10,
        ))
    explicit = ReviewContextRequest(
        requestId="ctx-requested",
        kind="CROSS_FILE",
        question="Does the requested caller satisfy the changed API contract?",
        targetPath="src/requested.py",
        startLine=1,
        endLine=20,
        relationship="IMPORTS public_api",
        requiredEvidence="The exact requested caller arguments.",
        relatedIssueIndexes=[0],
        originatingPaths=["src/api.py"],
    )

    class Llm:
        tickets = []

        async def ainvoke(self, messages):
            prompt = messages[-1]["content"]
            self.tickets = json.loads(prompt.split("Tickets:\n", 1)[1])
            return SimpleNamespace(content='{"issues":[]}')

    llm = Llm()
    result = await run_targeted_cross_file(
        llm,
        request,
        _diff(),
        _plan(),
        [],
        context_requests=[explicit],
        exact_context_resolver=ExactContextResolver(request),
        available_calls=1,
    )

    assert result.admitted_tickets == 8
    assert llm.tickets[0]["relatedPath"] == "src/requested.py"
    assert sum(
        item.endswith(":ticket_limit_exceeded")
        for item in result.incomplete_tickets
    ) == 1


@pytest.mark.asyncio
async def test_one_call_emits_normal_issue_and_exact_related_location():
    class Llm:
        calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            return SimpleNamespace(content="""{
              "issues": [{
                "id": "PLACEHOLDER",
                "severity": "MEDIUM",
                "category": "BUG_RISK",
                "file": "src/api.py",
                "line": 1,
                "title": "Caller contract is broken",
                "reason": "The renamed callable leaves the exact client import stale.",
                "suggestedFixDescription": "Update the caller import.",
                "codeSnippet": "def public_api(value):",
                "relatedLocations": []
              }]
            }""")

    request = _request()
    tickets, _ = build_investigation_tickets(request, _diff(), _plan(), [])
    llm = Llm()
    # Bind the deterministic ticket identity expected from model output.
    original = llm.ainvoke

    async def invoke(messages):
        response = await original(messages)
        response.content = response.content.replace("PLACEHOLDER", tickets[0].ticket_id)
        return response

    llm.ainvoke = invoke
    result = await run_targeted_cross_file(
        llm,
        request,
        _diff(),
        _plan(),
        [],
        available_calls=1,
    )
    assert result.calls_used == 1
    assert len(result.issues) == 1
    assert "src/client.py:1" in result.issues[0].relatedLocations


@pytest.mark.asyncio
async def test_zero_budget_makes_admitted_ticket_incomplete_without_call():
    class Llm:
        async def ainvoke(self, _):
            raise AssertionError("must not call")

    result = await run_targeted_cross_file(
        Llm(), _request(), _diff(), _plan(), [], available_calls=0
    )
    assert result.calls_used == 0
    assert result.admitted_tickets == 1
    assert result.incomplete_tickets
