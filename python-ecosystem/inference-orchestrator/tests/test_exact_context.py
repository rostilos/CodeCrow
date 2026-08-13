import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from model.multi_stage import (
    FileReviewBatchOutput,
    FileReviewOutput,
    ReviewContextRequest,
    ReviewFile,
)
from model.output_schemas import CodeReviewIssue
from service.review.candidate_ledger import CandidateEvidenceLedger
from service.review.orchestrator.exact_context import (
    ExactContextResolver,
    ReviewFollowupBudget,
    _exact_reader_payload,
)
from service.review.orchestrator.stage_1_file_review import (
    Stage1ReviewUnitState,
    _finalize_stage_1_batch_output,
)
from utils.prompts.prompt_builder import PromptBuilder


def _request(**updates):
    values = {
        "currentCommitHash": "a" * 40,
        "commitHash": "a" * 40,
        "projectWorkspace": "workspace",
        "projectNamespace": "repo",
        "changedFiles": ["src/app.py"],
        "pullRequestId": 12,
        "baseCommitHash": "b" * 40,
        "ragBaseGenerationManifestSha256": None,
        "ragPrGenerationFingerprint": None,
        "ragPrOverlayGenerationManifestSha256": None,
        "ragCollectionTarget": None,
        "get_rag_branch": lambda: "main",
        "get_rag_base_branch": lambda: "main",
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _issue(title: str, *, causal: str) -> CodeReviewIssue:
    return CodeReviewIssue(
        severity="MEDIUM",
        category="BUG_RISK",
        file="src/app.py",
        line=4,
        title=title,
        reason="The changed call reaches a disabled dependency.",
        suggestedFixDescription="Handle the disabled dependency.",
        codeSnippet="run_dependency()",
        triggerCondition=causal,
        causalPath="changed call -> disabled dependency",
        observableImpact="the request fails",
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_followup_budget_is_shared_and_atomic():
    budget = ReviewFollowupBudget(max_calls=2)

    acquired = await asyncio.gather(*(
        budget.try_acquire("stage_1", f"batch-{index}")
        for index in range(4)
    ))

    assert acquired.count(True) == 2
    assert acquired.count(False) == 2
    assert budget.summary()["used"] == 2
    assert budget.remaining == 0


def test_executor_budget_message_is_never_treated_as_exact_source():
    assert _exact_reader_payload(
        "Tool budget exhausted (4/4)",
        1,
        20,
    ) == ("", 0, 0)


@pytest.mark.asyncio(loop_scope="function")
async def test_enrichment_resolution_is_revision_bound_windowed_and_cached():
    resolver = ExactContextResolver(
        _request(),
        file_contents={
            "src/helper.py": "first\nneedle()\nthird\n",
        },
    )
    context_request = ReviewContextRequest(
        requestId="ctx-helper",
        question="Does the helper still invoke needle?",
        targetPath="src/helper.py",
        targetSymbol="needle",
        requiredEvidence="The exact current helper implementation.",
        relatedIssueIndexes=[0],
    )

    first = await resolver.resolve([context_request])
    second = await resolver.resolve([context_request])

    assert first.unresolved == ()
    assert first.resolved[0].revision == "a" * 40
    assert first.resolved[0].content == "first\nneedle()\nthird"
    assert first.resolved[0].source == "enrichment"
    assert second.resolved[0].evidence_id == first.resolved[0].evidence_id
    assert second.resolved[0].source == "enrichment"


@pytest.mark.asyncio(loop_scope="function")
async def test_enriched_line_range_must_contain_requested_symbol():
    resolver = ExactContextResolver(
        _request(),
        file_contents={
            "src/helper.py": "unrelated()\nother()\nneedle()\n",
        },
    )
    context_request = ReviewContextRequest(
        requestId="ctx-stale-range",
        question="Does this range contain needle?",
        targetPath="src/helper.py",
        targetSymbol="needle",
        startLine=1,
        endLine=2,
        requiredEvidence="The exact requested symbol range.",
    )

    result = await resolver.resolve([context_request])

    assert result.resolved == ()
    assert "symbol is absent" in result.unresolved[0].reason


@pytest.mark.asyncio(loop_scope="function")
async def test_exact_resolution_rejects_missing_revision_and_parent_paths():
    missing_revision = ExactContextResolver(
        _request(currentCommitHash=None, commitHash=None),
        file_contents={"src/helper.py": "needle()\n"},
    )
    normal_request = ReviewContextRequest(
        requestId="ctx-revision",
        question="Does the helper invoke needle at the reviewed head?",
        targetPath="src/helper.py",
        targetSymbol="needle",
        requiredEvidence="The exact current helper implementation.",
    )
    traversal_request = ReviewContextRequest(
        requestId="ctx-traversal",
        question="What is stored outside the repository root?",
        targetPath="../secret.txt",
        startLine=1,
        endLine=2,
        requiredEvidence="The requested exact file content.",
    )

    no_revision = await missing_revision.resolve([normal_request])
    traversal = await ExactContextResolver(_request()).resolve(
        [traversal_request]
    )

    assert "revision is unavailable" in no_revision.unresolved[0].reason
    assert traversal.resolved == ()
    assert "No unique current-head path" in traversal.unresolved[0].reason


@pytest.mark.asyncio(loop_scope="function")
async def test_rag_is_navigation_only_before_an_exact_current_head_read():
    rag = MagicMock()
    rag.get_deterministic_context = AsyncMock(return_value={
        "context": {
            "chunks": [{
                "path": "src/target.py",
                "content": "STALE_RAG_TEXT",
                "semantic_names": ["TargetSymbol"],
                "start_line": 20,
                "end_line": 30,
            }]
        }
    })
    exact_reader = AsyncMock(return_value={
        "content": "def TargetSymbol():\n    return current\n",
        "startLine": 20,
        "endLine": 21,
    })
    resolver = ExactContextResolver(
        _request(),
        rag_client=rag,
        exact_reader=exact_reader,
    )
    context_request = ReviewContextRequest(
        requestId="ctx-symbol",
        question="What does TargetSymbol return at the reviewed head?",
        targetSymbol="TargetSymbol",
        requiredEvidence="The exact current definition body.",
    )

    result = await resolver.resolve(
        [context_request],
        originating_paths=["src/app.py"],
    )

    assert result.unresolved == ()
    assert result.resolved[0].content == "def TargetSymbol():\n    return current\n"
    assert "STALE_RAG_TEXT" not in result.resolved[0].content
    exact_reader.assert_awaited_once_with(
        path="src/target.py",
        start_line=20,
        end_line=30,
        revision="a" * 40,
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_reverse_navigation_failure_is_incomplete_not_review_failure():
    rag = MagicMock()
    rag.get_deterministic_context = AsyncMock(
        side_effect=RuntimeError("RAG temporarily unavailable")
    )
    resolver = ExactContextResolver(_request(), rag_client=rag)

    result = await resolver.resolve_reverse_references(
        "LegacyApi",
        originating_paths=["src/legacy.py"],
    )

    assert result.resolved == ()
    assert len(result.unresolved) == 1
    assert "RuntimeError" in result.unresolved[0].reason


@pytest.mark.asyncio(loop_scope="function")
async def test_reverse_navigation_without_complete_overlay_binding_is_base_only():
    rag = MagicMock()
    rag.get_deterministic_context = AsyncMock(return_value={
        "context": {"reference_navigation": []},
    })
    resolver = ExactContextResolver(
        _request(
            baseCommitHash="b" * 40,
            ragBaseGenerationManifestSha256="base-generation",
            ragPrGenerationFingerprint=None,
            ragPrOverlayGenerationManifestSha256=None,
        ),
        rag_client=rag,
    )

    await resolver.resolve_reverse_references(
        "LegacyApi",
        originating_paths=["src/legacy.py"],
    )

    kwargs = rag.get_deterministic_context.await_args.kwargs
    assert kwargs["pr_number"] is None
    assert kwargs["pr_changed_files"] is None
    assert kwargs["source_revision"] is None
    assert kwargs["base_revision"] == "b" * 40
    assert kwargs["pr_generation_fingerprint"] is None
    assert kwargs["pr_overlay_generation_manifest_sha256"] is None


@pytest.mark.asyncio(loop_scope="function")
async def test_identical_concurrent_requests_share_one_exact_reader_call():
    reader_started = asyncio.Event()
    release_reader = asyncio.Event()
    reader_calls = 0

    async def exact_reader(**_kwargs):
        nonlocal reader_calls
        reader_calls += 1
        reader_started.set()
        await release_reader.wait()
        return {
            "content": "def shared_target():\n    return current\n",
            "startLine": 20,
            "endLine": 21,
        }

    resolver = ExactContextResolver(
        _request(),
        exact_reader=exact_reader,
        max_parallel_reads=4,
    )
    context_requests = [
        ReviewContextRequest(
            requestId=f"ctx-shared-{index}",
            question="What does shared_target return at the reviewed head?",
            targetPath="src/shared.py",
            targetSymbol="shared_target",
            startLine=20,
            endLine=21,
            requiredEvidence="The exact current definition body.",
        )
        for index in range(4)
    ]

    resolution_task = asyncio.create_task(resolver.resolve(context_requests))
    await asyncio.wait_for(reader_started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert reader_calls == 1

    release_reader.set()
    result = await resolution_task

    assert result.unresolved == ()
    assert reader_calls == 1
    assert [evidence.request_id for evidence in result.resolved] == [
        request.requestId for request in context_requests
    ]
    assert len({evidence.evidence_id for evidence in result.resolved}) == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_review_scoped_mcp_reader_handles_more_than_four_distinct_reads():
    mcp_client = MagicMock()

    async def call_tool(_tool_name, arguments):
        path = arguments["filePath"]
        return SimpleNamespace(content=[SimpleNamespace(
            text=(
                '{"fileContent":"current source for '
                + path
                + '","startLine":1,"endLine":1,"completeFile":false}'
            ),
        )])

    mcp_client.session.call_tool = AsyncMock(side_effect=call_tool)
    resolver = ExactContextResolver(
        _request(
            projectVcsWorkspace="workspace",
            projectVcsRepoSlug="repo",
        ),
        mcp_client=mcp_client,
    )
    requests = [
        ReviewContextRequest(
            requestId=f"ctx-{index}",
            question="Read the exact current source.",
            targetPath=f"src/file_{index}.py",
            startLine=1,
            endLine=1,
            requiredEvidence="The current source line.",
        )
        for index in range(5)
    ]

    result = await resolver.resolve(requests)

    assert result.unresolved == ()
    assert len(result.resolved) == 5
    assert mcp_client.session.call_tool.await_count == 5


@pytest.mark.asyncio(loop_scope="function")
async def test_incomplete_stage1_request_ids_are_scoped_by_batch():
    issue = _issue("Needs exact source", causal="dependency is disabled")
    initial = FileReviewBatchOutput(
        reviews=[FileReviewOutput(
            file="src/app.py",
            analysis_summary="provisional",
            issues=[issue],
            confidence="MEDIUM",
        )],
        contextRequests=[ReviewContextRequest(
            requestId="ctx-1",
            kind="LOCAL_EXACT",
            question="Is the dependency enabled?",
            targetPath="src/helper.py",
            startLine=1,
            endLine=2,
            requiredEvidence="The exact current configuration.",
            relatedIssueIndexes=[0],
        )],
    )
    state = Stage1ReviewUnitState()
    batch = [{
        "file": ReviewFile(path="src/app.py"),
        "_review_unit_id": "sha256:unit",
        "_hunk_ids": ("sha256:hunk",),
    }]

    for batch_index in (1, 2):
        result = await _finalize_stage_1_batch_output(
            llm=MagicMock(),
            initial_output=initial,
            generation_prompt="prompt",
            batch_file_paths=["src/app.py"],
            batch_items=batch,
            candidate_ledger=CandidateEvidenceLedger(),
            visible_evidence_by_id={},
            exact_context_resolver=None,
            followup_budget=ReviewFollowupBudget(max_calls=4),
            batch_index=batch_index,
            review_unit_state=state,
        )
        assert result == []

    assert state.incomplete_followups == {"batch-1:ctx-1", "batch-2:ctx-1"}


@pytest.mark.asyncio(loop_scope="function")
async def test_identical_symbol_requests_share_navigation_and_exact_reads():
    navigation_started = asyncio.Event()
    release_navigation = asyncio.Event()

    async def navigate(**_kwargs):
        navigation_started.set()
        await release_navigation.wait()
        return {
            "context": {
                "chunks": [{
                    "path": "src/shared.py",
                    "content": "STALE_RAG_TEXT",
                    "semantic_names": ["shared_target"],
                    "start_line": 20,
                    "end_line": 21,
                }]
            }
        }

    rag = MagicMock()
    rag.get_deterministic_context = AsyncMock(side_effect=navigate)
    exact_reader = AsyncMock(return_value={
        "content": "def shared_target():\n    return current\n",
        "startLine": 20,
        "endLine": 21,
    })
    resolver = ExactContextResolver(
        _request(
            ragBaseGenerationManifestSha256="base-generation",
            ragPrGenerationFingerprint="pr-generation",
            ragPrOverlayGenerationManifestSha256="overlay-generation",
        ),
        rag_client=rag,
        exact_reader=exact_reader,
        max_parallel_reads=4,
    )
    context_requests = [
        ReviewContextRequest(
            requestId=f"ctx-symbol-{index}",
            question="Where is shared_target defined at the reviewed head?",
            targetSymbol="shared_target",
            requiredEvidence="The exact current definition body.",
        )
        for index in range(4)
    ]

    resolution_task = asyncio.create_task(resolver.resolve(
        context_requests,
        originating_paths=["src/app.py"],
    ))
    await asyncio.wait_for(navigation_started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert rag.get_deterministic_context.await_count == 1

    release_navigation.set()
    result = await resolution_task

    assert result.unresolved == ()
    assert rag.get_deterministic_context.await_count == 1
    assert exact_reader.await_count == 1
    assert [evidence.request_id for evidence in result.resolved] == [
        request.requestId for request in context_requests
    ]


@pytest.mark.asyncio(loop_scope="function")
async def test_stage_1_registers_only_post_continuation_candidates():
    provisional_issue = _issue("Provisional", causal="provisional trigger")
    final_issue = _issue("Final", causal="verified trigger")
    initial = FileReviewBatchOutput(
        reviews=[FileReviewOutput(
            file="src/app.py",
            analysis_summary="provisional",
            issues=[provisional_issue],
            confidence="MEDIUM",
        )],
        contextRequests=[ReviewContextRequest(
            requestId="ctx-helper",
            question="Is the dependency disabled at the current head?",
            targetPath="src/helper.py",
            startLine=1,
            endLine=2,
            requiredEvidence="The exact enabled flag assignment.",
            relatedIssueIndexes=[0],
        )],
    )
    final = FileReviewBatchOutput(
        reviews=[FileReviewOutput(
            file="src/app.py",
            analysis_summary="final",
            issues=[final_issue],
            confidence="HIGH",
        )],
    )
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=final)
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    ledger = CandidateEvidenceLedger()
    budget = ReviewFollowupBudget(max_calls=4)
    state = Stage1ReviewUnitState()
    resolver = ExactContextResolver(
        _request(),
        file_contents={"src/helper.py": "enabled = False\nrun()\n"},
    )
    batch = [{
        "file": ReviewFile(path="src/app.py"),
        "_review_unit_id": "sha256:unit",
        "_hunk_ids": ("sha256:hunk",),
    }]
    prompt = PromptBuilder.build_stage_1_batch_prompt(
        files=[{"path": "src/app.py", "diff": "+run_dependency()"}],
        priority="MEDIUM",
    )

    result = await _finalize_stage_1_batch_output(
        llm=llm,
        initial_output=initial,
        generation_prompt=prompt,
        batch_file_paths=["src/app.py"],
        batch_items=batch,
        candidate_ledger=ledger,
        visible_evidence_by_id={},
        exact_context_resolver=resolver,
        followup_budget=budget,
        review_unit_state=state,
        rag_state=None,
    )

    assert result == [final_issue]
    assert ledger.record_for(provisional_issue) is None
    assert ledger.record_for(final_issue) is not None
    assert ledger.summary()["generated"] == 1
    assert ledger.summary()["records"][0]["causalEvidence"] == {
        "triggerCondition": "verified trigger",
        "causalPath": "changed call -> disabled dependency",
        "observableImpact": "the request fails",
    }
    assert len(final_issue.evidenceRefs) == 1
    assert final_issue.evidenceRefs[0].startswith("CTX-")
    record = ledger.record_for(final_issue)
    assert record.visible_evidence_by_id[final_issue.evidenceRefs[0]][0][
        "content"
    ] == "enabled = False\nrun()"
    assert budget.used == 1
    assert state.continuation_calls_used == 1
    messages = structured.ainvoke.await_args.args[0]
    assert [message[0] for message in messages] == [
        "system",
        "human",
        "assistant",
        "human",
    ]


def test_causal_evidence_is_internal_but_present_in_provider_schema():
    issue = _issue("Internal receipt", causal="input is empty")

    assert "triggerCondition" not in issue.model_dump()
    assert "causalPath" not in issue.model_dump()
    assert "observableImpact" not in issue.model_dump()
    schema = CodeReviewIssue.model_json_schema()["properties"]
    assert {"triggerCondition", "causalPath", "observableImpact"} <= set(schema)
