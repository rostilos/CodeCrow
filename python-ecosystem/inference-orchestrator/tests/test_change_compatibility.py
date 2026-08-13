import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from model.dtos import (
    PullRequestFileManifestDto,
    PullRequestManifestChangeDto,
    ReviewRequestDto,
)
from model.enrichment import (
    FileContentDto,
    FileRelationshipDto,
    PrEnrichmentDataDto,
)
from model.enums import RelationshipType
from service.review.candidate_ledger import CandidateEvidenceLedger
from service.review.orchestrator.change_compatibility import (
    build_compatibility_changes,
    run_change_compatibility_review,
)
from service.review.orchestrator.exact_context import (
    ExactContextResolver,
    ReviewFollowupBudget,
)
from service.review.orchestrator.verification_agent import (
    apply_candidate_provenance_gate,
    run_deterministic_evidence_gate,
)
from service.review.orchestrator.verification_wave import (
    build_verification_records,
)
from utils.diff_processor import DiffProcessor


DELETION_DIFF = """diff --git a/src/legacy.py b/src/legacy.py
deleted file mode 100644
--- a/src/legacy.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def legacy_api(value):
-    return value
"""

INDENTED_DELETION_DIFF = """diff --git a/config/legacy.yml b/config/legacy.yml
deleted file mode 100644
--- a/config/legacy.yml
+++ /dev/null
@@ -1 +0,0 @@
-    legacy: true
"""


def _request(**updates) -> ReviewRequestDto:
    values = dict(
        projectId=1,
        projectVcsRepoSlug="org/repo",
        projectVcsWorkspace="org",
        projectWorkspace="org",
        projectNamespace="repo",
        aiProvider="test",
        aiModel="test",
        aiApiKey="test",
        targetBranchName="main",
        pullRequestId=7,
        commitHash="a" * 40,
        currentCommitHash="a" * 40,
        changedFiles=["src/legacy.py"],
        deletedFiles=["src/legacy.py"],
        enrichmentData=PrEnrichmentDataDto(
            fileContents=[
                FileContentDto(
                    path="src/consumer.py",
                    content="from src.legacy import legacy_api\nlegacy_api(1)\n",
                ),
            ],
            relationships=[
                FileRelationshipDto(
                    sourceFile="src/consumer.py",
                    targetFile="src/legacy.py",
                    relationshipType=RelationshipType.IMPORTS,
                    matchedOn="legacy_api",
                ),
            ],
        ),
    )
    values.update(updates)
    return ReviewRequestDto(**values)


class _FindingLlm:
    async def ainvoke(self, messages):
        prompt = messages[-1]["content"]
        tickets = json.loads(prompt.split("Tickets:\n", 1)[1])
        ticket = tickets[0]
        current = ticket["currentRelatedEvidence"][0]
        return SimpleNamespace(content=json.dumps({"issues": [{
            "id": ticket["ticketId"],
            "severity": "HIGH",
            "category": "BUG_RISK",
            "file": ticket["summaryOnlyFile"],
            "line": 0,
            "scope": "FILE",
            "title": "Deleted API remains imported",
            "reason": "The current consumer imports a module removed by this change.",
            "suggestedFixDescription": "Migrate the consumer before deleting the API.",
            "codeSnippet": ticket["allowedCodeSnippet"],
            "evidenceRefs": [
                ticket["changeEvidenceId"],
                current["evidenceId"],
            ],
            "triggerCondition": "The consumer module is imported",
            "causalPath": "consumer import -> deleted module path",
            "observableImpact": "Module loading fails before the request can run",
        }]}))


@pytest.mark.asyncio(loop_scope="function")
async def test_deleted_contract_candidate_is_summary_only_and_verifiable():
    request = _request()
    processed = DiffProcessor().process(DELETION_DIFF)
    ledger = CandidateEvidenceLedger()
    resolver = ExactContextResolver(request)

    result = await run_change_compatibility_review(
        _FindingLlm(),
        request,
        processed,
        exact_context_resolver=resolver,
        followup_budget=ReviewFollowupBudget(max_calls=1),
        candidate_ledger=ledger,
    )

    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.file == "src/legacy.py"
    assert issue.line == 0
    assert issue.scope == "FILE"
    assert issue.relatedLocations == ["src/consumer.py:1"]
    assert apply_candidate_provenance_gate(
        [issue], request, processed, ledger
    ) == [issue]
    assert run_deterministic_evidence_gate(
        [issue], request, processed, ledger
    ) == [issue]
    records, missing = build_verification_records(
        [issue], request, processed, ledger
    )
    assert missing == []
    assert records[0].payload["evidenceScope"] == (
        "removed_change_and_current_related_source"
    )
    assert "-def legacy_api" in records[0].payload["currentChangedHunk"]


def test_metadata_rename_uses_manifest_receipt_without_fake_diff_anchor():
    request = _request(
        changedFiles=["src/current.py"],
        deletedFiles=["src/old.py"],
        pullRequestFileManifest=PullRequestFileManifestDto(
            completeness="COMPLETE",
            receipt="provider-page-receipt",
            changes=[PullRequestManifestChangeDto(
                path="src/current.py",
                previousPath="src/old.py",
                kind="RENAMED",
            )],
        ),
        enrichmentData=PrEnrichmentDataDto(fileContents=[
            FileContentDto(path="src/current.py", content="def current():\n    pass\n"),
            FileContentDto(path="src/caller.py", content="import src.old\n"),
        ]),
    )
    processed = DiffProcessor().process(
        "diff --git a/src/old.py b/src/current.py\n"
        "similarity index 100%\n"
        "rename from src/old.py\n"
        "rename to src/current.py\n"
    )

    changes = build_compatibility_changes(request, processed)

    assert len(changes) == 1
    assert changes[0].kind == "RENAMED"
    assert changes[0].path == "src/old.py"
    assert changes[0].anchor == (
        "rename from src/old.py\nrename to src/current.py"
    )
    assert changes[0].prompt_hunk_ids == (changes[0].anchor_evidence_id,)
    assert {
        item.targetPath
        for item in changes[0].related_requests
        if item.targetPath is not None
    } == {"src/caller.py"}
    assert changes[0].navigation_identifiers == ("src.old", "old")
    assert changes[0].excluded_evidence_paths == (
        "src/old.py",
        "src/current.py",
    )


def test_full_manifest_rename_outside_selected_delta_is_not_rediscovered():
    request = _request(
        analysisMode="INCREMENTAL",
        changedFiles=["src/current_change.py"],
        pullRequestFileManifest=PullRequestFileManifestDto(
            completeness="COMPLETE",
            receipt="provider-full-pr",
            changes=[PullRequestManifestChangeDto(
                path="src/current.py",
                previousPath="src/old.py",
                kind="RENAMED",
            )],
        ),
    )
    selected_delta = DiffProcessor().process(
        "diff --git a/src/current_change.py b/src/current_change.py\n"
        "--- a/src/current_change.py\n"
        "+++ b/src/current_change.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )

    assert build_compatibility_changes(request, selected_delta) == []


def test_full_review_uses_provider_manifest_only_rename_receipt():
    request = _request(
        analysisMode="FULL",
        changedFiles=[],
        pullRequestFileManifest=PullRequestFileManifestDto(
            completeness="COMPLETE",
            receipt="provider-full-review",
            changes=[PullRequestManifestChangeDto(
                path="src/current.py",
                previousPath="src/old.py",
                kind="RENAMED",
            )],
        ),
        enrichmentData=PrEnrichmentDataDto(fileContents=[
            FileContentDto(path="src/current.py", content="def current():\n    pass\n"),
            FileContentDto(path="src/caller.py", content="import src.old\n"),
        ]),
    )

    changes = build_compatibility_changes(
        request,
        DiffProcessor().process(""),
    )

    assert [(item.kind, item.path) for item in changes] == [
        ("RENAMED", "src/old.py")
    ]


def test_removed_anchor_is_canonicalized_for_model_schema_normalization():
    processed = DiffProcessor().process(INDENTED_DELETION_DIFF)

    changes = build_compatibility_changes(_request(), processed)

    assert changes[0].anchor == "legacy: true"


def test_plugin_excluded_deletion_does_not_create_compatibility_ticket():
    processed = DiffProcessor().process(DELETION_DIFF)
    processed.files[0].plugin_disposition = "excluded"

    assert build_compatibility_changes(_request(), processed) == []


def test_full_plugin_generated_rename_does_not_fall_back_to_manifest():
    request = _request(
        analysisMode="FULL",
        pullRequestFileManifest=PullRequestFileManifestDto(
            completeness="COMPLETE",
            receipt="provider-full-review",
            changes=[PullRequestManifestChangeDto(
                path="src/current.py",
                previousPath="src/old.py",
                kind="RENAMED",
            )],
        ),
    )
    processed = DiffProcessor().process(
        "diff --git a/src/old.py b/src/current.py\n"
        "similarity index 100%\n"
        "rename from src/old.py\n"
        "rename to src/current.py\n"
    )
    processed.files[0].plugin_disposition = "generated"

    assert build_compatibility_changes(request, processed) == []


@pytest.mark.asyncio(loop_scope="function")
async def test_manifest_only_deletion_reports_missing_removed_evidence():
    class NoCallLlm:
        async def ainvoke(self, _messages):
            raise AssertionError("model must not run without removed evidence")

    request = _request(
        analysisMode="FULL",
        changedFiles=[],
        deletedFiles=["src/legacy.py"],
        pullRequestFileManifest=PullRequestFileManifestDto(
            completeness="COMPLETE",
            receipt="provider-full-review",
            changes=[PullRequestManifestChangeDto(
                path="src/legacy.py",
                kind="DELETED",
            )],
        ),
    )
    result = await run_change_compatibility_review(
        NoCallLlm(),
        request,
        DiffProcessor().process(""),
        exact_context_resolver=ExactContextResolver(request),
        followup_budget=ReviewFollowupBudget(max_calls=1),
        candidate_ledger=CandidateEvidenceLedger(),
    )

    assert result.changes_considered == 1
    assert result.call_used is False
    assert result.incomplete_changes[0].endswith(
        ":removed_evidence_unavailable"
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_model_cannot_turn_compatibility_finding_into_inline_anchor():
    class InlineLlm(_FindingLlm):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            payload = json.loads(response.content)
            payload["issues"][0]["line"] = 1
            return SimpleNamespace(content=json.dumps(payload))

    request = _request()
    result = await run_change_compatibility_review(
        InlineLlm(),
        request,
        DiffProcessor().process(DELETION_DIFF),
        exact_context_resolver=ExactContextResolver(request),
        followup_budget=ReviewFollowupBudget(max_calls=1),
        candidate_ledger=CandidateEvidenceLedger(),
    )

    assert result.issues == ()
    assert result.call_used is True


@pytest.mark.asyncio(loop_scope="function")
async def test_deletion_without_exact_related_source_spends_no_model_call():
    class NoCallLlm:
        async def ainvoke(self, _messages):
            raise AssertionError("model must not run without exact related source")

    request = _request(enrichmentData=PrEnrichmentDataDto())
    result = await run_change_compatibility_review(
        NoCallLlm(),
        request,
        DiffProcessor().process(DELETION_DIFF),
        exact_context_resolver=ExactContextResolver(request),
        followup_budget=ReviewFollowupBudget(max_calls=1),
        candidate_ledger=CandidateEvidenceLedger(),
    )

    assert result.issues == ()
    assert result.call_used is False
    assert result.incomplete_changes[0].endswith(
        ":external_reference_unavailable"
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_exhausted_followup_budget_performs_no_navigation_or_read():
    class NoCallLlm:
        async def ainvoke(self, _messages):
            raise AssertionError("model must not run without a follow-up slot")

    rag = MagicMock()
    rag.get_deterministic_context = AsyncMock(
        side_effect=AssertionError("budget-losing check must not navigate")
    )
    reader = AsyncMock(
        side_effect=AssertionError("budget-losing check must not read source")
    )
    budget = ReviewFollowupBudget(max_calls=1)
    assert await budget.try_acquire("stage_1", "already-used") is True

    result = await run_change_compatibility_review(
        NoCallLlm(),
        _request(enrichmentData=PrEnrichmentDataDto()),
        DiffProcessor().process(DELETION_DIFF),
        exact_context_resolver=ExactContextResolver(
            _request(enrichmentData=PrEnrichmentDataDto()),
            rag_client=rag,
            exact_reader=reader,
        ),
        followup_budget=budget,
        candidate_ledger=CandidateEvidenceLedger(),
    )

    assert result.call_used is False
    assert result.incomplete_changes[0].endswith(":followup_budget")
    rag.get_deterministic_context.assert_not_awaited()
    reader.assert_not_awaited()


@pytest.mark.asyncio(loop_scope="function")
async def test_change_limit_is_reported_instead_of_claiming_all_changes_clean():
    diffs = []
    for index in range(5):
        path = f"src/legacy_{index}.py"
        diffs.append(
            f"diff --git a/{path} b/{path}\n"
            "deleted file mode 100644\n"
            f"--- a/{path}\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            f"-def legacy_{index}(): pass\n"
        )
    request = _request(enrichmentData=PrEnrichmentDataDto())

    result = await run_change_compatibility_review(
        MagicMock(),
        request,
        DiffProcessor().process("".join(diffs)),
        exact_context_resolver=ExactContextResolver(request),
        followup_budget=ReviewFollowupBudget(max_calls=1),
        candidate_ledger=CandidateEvidenceLedger(),
    )

    assert result.changes_considered == 5
    assert len(result.incomplete_changes) == 5
    assert sum(value.endswith(":change_limit") for value in result.incomplete_changes) == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_removed_identifier_navigates_to_pinned_unchanged_caller():
    request = _request(enrichmentData=PrEnrichmentDataDto())
    rag = MagicMock()
    rag.get_deterministic_context = AsyncMock(return_value={
        "context": {
            "reference_navigation": [{
                "path": "src/unchanged_consumer.py",
                "start_line": 10,
                "end_line": 12,
            }],
        },
    })
    exact_reader = AsyncMock(return_value={
        "content": "from src.legacy import legacy_api\nlegacy_api(1)\n",
        "startLine": 10,
        "endLine": 11,
    })
    ledger = CandidateEvidenceLedger()

    result = await run_change_compatibility_review(
        _FindingLlm(),
        request,
        DiffProcessor().process(DELETION_DIFF),
        exact_context_resolver=ExactContextResolver(
            request,
            rag_client=rag,
            exact_reader=exact_reader,
        ),
        followup_budget=ReviewFollowupBudget(max_calls=1),
        candidate_ledger=ledger,
    )

    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.relatedLocations == ["src/unchanged_consumer.py:10"]
    record = ledger.record_for(issue)
    assert record is not None
    assert all(
        "STALE_NAVIGATION_ONLY" not in str(fact)
        for facts in record.visible_evidence_by_id.values()
        for fact in facts
    )
    assert rag.get_deterministic_context.await_count <= 2
    assert exact_reader.await_count <= 2


@pytest.mark.asyncio(loop_scope="function")
async def test_reverse_navigation_fans_out_to_three_external_callers_in_path_order():
    class AllEvidenceLlm(_FindingLlm):
        async def ainvoke(self, messages):
            response = await super().ainvoke(messages)
            payload = json.loads(response.content)
            tickets = json.loads(messages[-1]["content"].split("Tickets:\n", 1)[1])
            ticket = tickets[0]
            payload["issues"][0]["evidenceRefs"] = [
                ticket["changeEvidenceId"],
                *(
                    item["evidenceId"]
                    for item in ticket["currentRelatedEvidence"]
                ),
            ]
            return SimpleNamespace(content=json.dumps(payload))

    request = _request(enrichmentData=PrEnrichmentDataDto())
    rag = MagicMock()
    rag.get_deterministic_context = AsyncMock(return_value={
        "context": {
            "reference_navigation": [
                {"path": "src/z_consumer.py", "start_line": 20, "end_line": 22},
                {"path": "src/b_consumer.py", "start_line": 1, "end_line": 3},
                {"path": "src/a_consumer.py", "start_line": 5, "end_line": 8},
                {"path": "src/m_consumer.py", "start_line": 12, "end_line": 15},
            ],
        },
    })

    async def read_source(*, path, **_kwargs):
        return {
            "content": f"from src.legacy import legacy_api\n# {path}\nlegacy_api(1)\n",
            "startLine": 1,
            "endLine": 3,
        }

    result = await run_change_compatibility_review(
        AllEvidenceLlm(),
        request,
        DiffProcessor().process(DELETION_DIFF),
        exact_context_resolver=ExactContextResolver(
            request,
            rag_client=rag,
            exact_reader=AsyncMock(side_effect=read_source),
        ),
        followup_budget=ReviewFollowupBudget(max_calls=1),
        candidate_ledger=CandidateEvidenceLedger(),
    )

    assert len(result.issues) == 1
    assert result.issues[0].relatedLocations == [
        "src/a_consumer.py:1",
        "src/b_consumer.py:1",
        "src/m_consumer.py:1",
    ]
    reverse_call = next(
        call.kwargs
        for call in rag.get_deterministic_context.await_args_list
        if call.kwargs.get("navigation_mode") == "REVERSE_REFERENCES"
    )
    assert reverse_call["reference_identifiers"] == ["legacy_api"]


@pytest.mark.asyncio(loop_scope="function")
async def test_rename_destination_definition_cannot_supply_dependency_evidence():
    request = _request(
        changedFiles=["src/current.py"],
        deletedFiles=["src/old.py"],
        pullRequestFileManifest=PullRequestFileManifestDto(
            completeness="COMPLETE",
            receipt="provider-page-receipt",
            changes=[PullRequestManifestChangeDto(
                path="src/current.py",
                previousPath="src/old.py",
                kind="RENAMED",
            )],
        ),
        enrichmentData=PrEnrichmentDataDto(fileContents=[
            FileContentDto(
                path="src/current.py",
                content="# old compatibility alias\ndef current():\n    pass\n",
            ),
        ]),
    )
    rag = MagicMock()
    rag.get_deterministic_context = AsyncMock(return_value={
        "context": {
            "reference_navigation": [{
                "path": "src/current.py",
                "start_line": 1,
                "end_line": 3,
            }],
        },
    })

    result = await run_change_compatibility_review(
        MagicMock(),
        request,
        DiffProcessor().process(
            "diff --git a/src/old.py b/src/current.py\n"
            "similarity index 100%\n"
            "rename from src/old.py\n"
            "rename to src/current.py\n"
        ),
        exact_context_resolver=ExactContextResolver(request, rag_client=rag),
        followup_budget=ReviewFollowupBudget(max_calls=1),
        candidate_ledger=CandidateEvidenceLedger(),
    )

    assert result.issues == ()
    assert result.call_used is False
    assert result.incomplete_changes[0].endswith(
        ":external_reference_unavailable"
    )
