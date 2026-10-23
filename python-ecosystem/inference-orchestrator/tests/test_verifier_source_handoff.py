"""Request-local reuse of saved complete source; these are not quality claims."""

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from model.enrichment import FileContentDto
from model.multi_stage import FileReviewBatchOutput, FileReviewOutput, ReviewFile
from model.output_schemas import CodeReviewIssue
from service.agent.models import AgentExecutionError, AgentToolEvent
from service.review.orchestrator import orchestrator as orchestration
from service.review.orchestrator import verification_agent as verifier
from service.review.orchestrator.stage_1_file_review import (
    _consume_stage1_agent_tool_events,
    _invoke_stage_1_batch_llm,
    review_file_batch,
)
from service.review.orchestrator.stage_1_rag_retrieval import Stage1RagState
from service.review.prompt_dry_run import PromptCaptureLLM, PromptCaptureSession
from utils.diff_processor import DiffProcessor
from .prompt_dry_run_neutral_fixture import neutral_request


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/verifier_source_handoff.json").read_text()
)
HELPER = "lib/file_helper.rb"
COMPLETE = FIXTURE["complete"]
SOURCE = json.loads(COMPLETE["observation"])["fileContent"]


def event(receipt=COMPLETE, *, arguments=None, response=None):
    return AgentToolEvent(
        action=SimpleNamespace(
            tool="getReviewFileContent",
            tool_input=arguments if arguments is not None else receipt["action"]["tool_input"],
        ),
        observation=receipt["observation"] if response is None else response,
    )


def consume(events, request=None, state=None):
    state = state if state is not None else Stage1RagState()
    _consume_stage1_agent_tool_events(
        events, visible_evidence_by_id=None, context_holder=None,
        rag_state=state,
        request=request if request is not None else SimpleNamespace(**COMPLETE["request"]),
    )
    return state


def review_request():
    request = neutral_request()
    request.projectVcsWorkspace = COMPLETE["request"]["projectVcsWorkspace"]
    request.projectVcsRepoSlug = COMPLETE["request"]["projectVcsRepoSlug"]
    request.localRepoRevision = request.baseCommitHash
    return request


def candidate(path="src/file_0.py", snippet="value_0 = 1"):
    return CodeReviewIssue(
        file=path, line=1, codeSnippet=snippet, severity="HIGH", category="BUG_RISK",
        title="Hypothesis requiring a helper check",
        reason="The changed call may violate its helper's contract.",
        suggestedFixDescription="Confirm the contract before changing the caller.",
    )


def test_saved_complete_body_supports_existing_bounded_search_and_exact_citation():
    state = consume([event()])
    assert state.complete_file_contents == {HELPER: SOURCE}
    assert state.exact_evidence_by_id == {}
    assert hashlib.sha256(SOURCE.encode()).hexdigest() == COMPLETE["source_body_sha256"]
    # MCP includes the trailing empty line; no incompatible splitlines equality.
    assert len(SOURCE.splitlines()) == 49
    assert json.loads(COMPLETE["observation"])["totalLines"] == 50
    token = verifier._ACTIVE_FILE_CONTENTS.set(dict(state.complete_file_contents))
    try:
        result = verifier.search_file_content(HELPER, "def self.download")
        assert "lines 1-19" in result
        assert "while f.size <= max_file_size" in result
        assert "        f.write(data)" in result
        assert "    tmp\n" not in result  # The normal window is still bounded.
        assessment = verifier.VerificationAssessment(
            issue_id="issue_0", verdict="disproven",
            contradicting_evidence=[verifier.VerificationCitation(
                file_path=HELPER, snippet="while f.size <= max_file_size",
            )],
        )
        assert verifier._validated_assessment(assessment, state.complete_file_contents)["verdict"] == "disproven"
        assessment.contradicting_evidence[0].snippet = "raise DownloadSizeLimit"
        assert verifier._validated_assessment(assessment, state.complete_file_contents)["verdict"] == "unknown"
    finally:
        verifier._ACTIVE_FILE_CONTENTS.reset(token)


def test_real_partial_and_unread_sources_remain_unavailable():
    partial = FIXTURE["partial"]
    state = consume([event(partial)], SimpleNamespace(**partial["request"]))
    assert state.complete_file_contents == {}
    assert json.loads(partial["observation"])["endLine"] == 240
    assert json.loads(partial["observation"])["totalLines"] == 262
    token = verifier._ACTIVE_FILE_CONTENTS.set(state.complete_file_contents)
    try:
        assert "not available" in verifier.search_file_content(
            partial["action"]["tool_input"]["filePath"], "type updateWrapper",
        )
        assert "not available" in verifier.search_file_content(**FIXTURE["unread_search"])
    finally:
        verifier._ACTIVE_FILE_CONTENTS.reset(token)


@pytest.mark.parametrize("patch", [
    {"completeFile": False}, {"completeFile": "true"},
    {"exists": False}, {"deleted": True}, {"unavailable": True},
    {"changed": True, "source": "review-overlay"}, {"source": "target-base"},
    {"fileContent": None}, {"error": "read failed"}, {"status": "error"},
    {"isError": True}, {"filePath": "other/lib/file_helper.rb"}, {"filePath": None},
])
def test_ineligible_observations_are_skipped_without_changing_other_evidence(patch):
    response = {**json.loads(COMPLETE["observation"]), **patch}
    state = Stage1RagState(complete_file_contents={"already.rb": "exact body"})
    consume([event(response=response)], state=state)
    assert state.complete_file_contents == {"already.rb": "exact body"}


@pytest.mark.parametrize("observation", ["not JSON", "[]", "null", None])
def test_malformed_observations_fail_open(observation):
    source_event = event()
    source_event = AgentToolEvent(action=source_event.action, observation=observation)
    assert consume([source_event]).complete_file_contents == {}


@pytest.mark.parametrize("argument_patch,request_patch", [
    ({"workspace": "another-tenant"}, {}),
    ({"repoSlug": "another-repository"}, {}),
    ({"filePath": "file_helper.rb"}, {}),
    ({"filePath": [HELPER]}, {}),
    ({}, {"localRepoRevision": None}),
    ({}, {"changedFiles": [HELPER]}),
    ({}, {"deletedFiles": [HELPER]}),
])
def test_request_binding_and_exact_path_are_required(argument_patch, request_patch):
    args = {**COMPLETE["action"]["tool_input"], **argument_patch}
    request = SimpleNamespace(**{**COMPLETE["request"], **request_patch})
    assert consume([event(arguments=args)], request).complete_file_contents == {}


def test_normalized_paths_and_json_arguments_work_but_conflicts_never_revive(caplog):
    args = {**COMPLETE["action"]["tool_input"], "filePath": "./lib\\file_helper.rb"}
    source_event = event(arguments=json.dumps(args))
    state = consume([source_event, source_event])
    assert state.complete_file_contents == {HELPER: SOURCE}
    conflicting = {**json.loads(COMPLETE["observation"]), "fileContent": "different bytes"}
    consume([event(response=conflicting), source_event], state=state)
    assert state.complete_file_contents == {HELPER: None}
    assert "conflicting complete file" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("fails_after_read", [False, True])
async def test_agent_success_and_existing_failure_path_preserve_completed_reads(fails_after_read):
    source_event = event()
    async def execute(_request):
        if fails_after_read:
            raise AgentExecutionError("later agent failure", tool_events=(source_event,), metadata={})
        return SimpleNamespace(
            output=FileReviewBatchOutput(reviews=[FileReviewOutput(
                file="src/file_0.py", analysis_summary="test fixture", issues=[],
            )]),
            tool_events=(source_event,),
        )
    state = Stage1RagState()
    invocation = _invoke_stage_1_batch_llm(
        object(), "Existing prompt", ["src/file_0.py"],
        agent_service=SimpleNamespace(execute=execute),
        agent_allowed_tool_names=frozenset({"getReviewFileContent"}),
        rag_state=state, request=review_request(), fail_closed_agent=True,
    )
    if fails_after_read:
        with pytest.raises(RuntimeError, match="repository-agent execution failed"):
            await invocation
    else:
        assert await invocation == []
    assert state.complete_file_contents == {HELPER: SOURCE}


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery", [False, True])
async def test_batch_primary_and_recovery_forward_the_active_request(recovery):
    request = review_request()
    request.useMcpTools = True
    request.mcpLocalOnly = True
    request.localReviewOverlayPath = "/fixture/review-overlay"
    phases = []
    async def execute(agent_request):
        phases.append(agent_request.metadata["phase"])
        if recovery and len(phases) == 1:
            raise AgentExecutionError("fixture transport failure", tool_events=(), metadata={})
        return SimpleNamespace(
            output=FileReviewBatchOutput(reviews=[FileReviewOutput(
                file="src/file_0.py", analysis_summary="No fixture defect", issues=[],
            )]),
            tool_events=(event(),),
        )
    state = Stage1RagState()
    assert await review_file_batch(
        object(), request,
        [{"file": ReviewFile(path="src/file_0.py"), "priority": "MEDIUM"}],
        rag_client=None, rag_state=state,
        agent_service=SimpleNamespace(
            available_tool_names=frozenset({"getReviewFileContent"}), execute=execute,
        ),
    ) == []
    assert phases == (["primary", "missing_path_recovery"] if recovery else ["primary"])
    assert state.complete_file_contents == {HELPER: SOURCE}


@pytest.mark.asyncio
async def test_real_verifier_uses_handoff_for_search_and_citation_without_prompt_injection(monkeypatch):
    request = review_request()
    original_enrichment = request.enrichmentData.model_dump()
    state = consume([event()], request)
    calls = []
    async def inspect(_llm, prompt, **kwargs):
        calls.append((prompt, kwargs))
        assert SOURCE not in prompt
        assert "Found" in verifier.search_file_content(HELPER, "def self.download")
        return verifier.VerificationResult(assessments=[verifier.VerificationAssessment(
            issue_id="issue_0", verdict="disproven",
            reason="Controlled citation-mechanics check, not a real defect verdict.",
            contradicting_evidence=[verifier.VerificationCitation(
                file_path=HELPER, snippet="while f.size <= max_file_size",
            )],
        )])
    monkeypatch.setattr(verifier, "_run_verification_tool_loop", inspect)
    assert await verifier.run_verification_agent(
        object(), [candidate()], request, DiffProcessor().process(request.rawDiff),
        observed_file_contents=state.complete_file_contents,
    ) == []
    assert len(calls) == 1
    assert calls[0][1]["optional_tools"] == ()
    assert request.enrichmentData.model_dump() == original_enrichment
    assert verifier._ACTIVE_FILE_CONTENTS.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("enriched_path", [HELPER, "./lib\\file_helper.rb"])
async def test_enrichment_remains_authoritative(enriched_path, monkeypatch):
    request = review_request()
    request.enrichmentData.fileContents.append(FileContentDto(
        path=enriched_path, content="authoritative source", sizeBytes=20,
    ))
    async def inspect(_llm, _prompt, **kwargs):
        contents = verifier._ACTIVE_FILE_CONTENTS.get()
        assert contents[enriched_path] == "authoritative source"
        assert SOURCE not in contents.values()
        return verifier.VerificationResult()
    callback = AsyncMock(side_effect=inspect)
    monkeypatch.setattr(verifier, "_run_verification_tool_loop", callback)
    assert len(await verifier.run_verification_agent(
        object(), [candidate()], request, DiffProcessor().process(request.rawDiff),
        observed_file_contents={HELPER: SOURCE},
    )) == 1
    callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_parallel_review_handoffs_are_isolated_and_reset(monkeypatch):
    entered = 0
    both_entered = asyncio.Event()
    bodies = [SOURCE, "another tenant's complete source\n"]
    seen = []
    async def inspect(expected, _prompt, **kwargs):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await both_entered.wait()
        seen.append(verifier._ACTIVE_FILE_CONTENTS.get()[HELPER])
        assert verifier._ACTIVE_FILE_CONTENTS.get()[HELPER] == expected
        return verifier.VerificationResult()
    monkeypatch.setattr(verifier, "_run_verification_tool_loop", inspect)
    async def review(body):
        request = review_request()
        request.projectVcsWorkspace = "tenant-" + str(bodies.index(body))
        response = {**json.loads(COMPLETE["observation"]), "fileContent": body}
        args = {**COMPLETE["action"]["tool_input"], "workspace": request.projectVcsWorkspace}
        state = consume([event(arguments=args, response=response)], request)
        return await verifier.run_verification_agent(
            body, [candidate()], request, DiffProcessor().process(request.rawDiff),
            observed_file_contents=dict(state.complete_file_contents),
        )
    outputs = await asyncio.gather(*(review(body) for body in bodies))
    assert [len(output) for output in outputs] == [1, 1]
    assert sorted(seen) == sorted(bodies)
    assert verifier._ACTIVE_FILE_CONTENTS.get() is None
    assert HELPER not in verifier._FILE_CONTENTS_CACHE


@pytest.mark.asyncio
@pytest.mark.parametrize("handoff", [{}, {HELPER: SOURCE}])
async def test_unchanged_helper_cannot_become_a_new_issue_anchor(handoff, monkeypatch):
    request = review_request()
    # Include helper in normal evidence too so this exercises changed-hunk gate.
    request.enrichmentData.fileContents.append(FileContentDto(
        path=HELPER, content=SOURCE, sizeBytes=len(SOURCE),
    ))
    callback = AsyncMock()
    monkeypatch.setattr(verifier, "_run_verification_tool_loop", callback)
    assert await verifier.run_verification_agent(
        object(), [candidate(HELPER, "class FileHelper")], request,
        DiffProcessor().process(request.rawDiff), observed_file_contents=handoff,
    ) == []
    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_orchestration_passes_a_copy_after_stage1_finishes(monkeypatch):
    request = review_request()
    request.ragEnabled = False
    session = PromptCaptureSession(request=request)
    stage1 = orchestration.execute_stage_1_file_reviews
    states = []
    async def reviewed(*args, **kwargs):
        result = await stage1(*args, **kwargs)
        state = consume([event()], request, kwargs["rag_state"])
        state.complete_file_contents["conflicted.rb"] = None
        states.append(state)
        return result
    async def verify(_llm, issues, *args, observed_file_contents, **kwargs):
        assert states and observed_file_contents == {HELPER: SOURCE}
        assert observed_file_contents is not states[0].complete_file_contents
        observed_file_contents[HELPER] = "mutation remains local to the copy"
        return issues
    monkeypatch.setattr(orchestration, "execute_stage_1_file_reviews", reviewed)
    callback = AsyncMock(side_effect=verify)
    monkeypatch.setattr(orchestration, "run_verification_agent", callback)
    monkeypatch.setattr(orchestration, "VERIFICATION_ENABLED", True)
    engine = orchestration.MultiStageReviewOrchestrator(
        llm=PromptCaptureLLM(session), mcp_client=None, rag_client=None,
    )
    await engine.orchestrate_review(request, processed_diff=DiffProcessor().process(request.rawDiff))
    callback.assert_awaited_once()
    assert states[0].complete_file_contents[HELPER] == SOURCE
