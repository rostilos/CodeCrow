"""Offline provider-protocol checks; no model calls or review quality claims."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from service.review.agent_calls import ReviewAgentSession, native_tool_definitions, result_message
from service.review.verification_tools import VerificationTools
from service.review.review_step import STEP_TOOL, review_tool_schemas, submitted_steps


SCHEMAS = [{"name": "readReviewFile", "description": "Read exact source.", "inputSchema": {
    "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"],
}}]
REQUEST = SimpleNamespace(aiProvider="openai", pullRequestId="17")


class NativeModel:
    def __init__(self, responses):
        self.bound = SimpleNamespace(ainvoke=AsyncMock(side_effect=responses))
        self.ainvoke = self.bound.ainvoke
        self.schemas = None
        self.bindings = []

    def bind_tools(self, schemas, **options):
        self.schemas = schemas
        self.binding_options = options
        self.bindings.append((schemas, options))
        return self.bound


@pytest.mark.asyncio
async def test_native_calls_and_tool_messages_preserve_provider_response():
    response = AIMessage(content=[{"type": "text", "text": "Inspect caller."}],
                         tool_calls=[{"name": "readReviewFile", "args": {"path": "caller.py"}, "id": "provider-call"}],
                         additional_kwargs={"signature": "provider-signature"})
    model = NativeModel([response, AIMessage(content='{"decisions":[]}')])
    session = ReviewAgentSession(model, REQUEST, SCHEMAS)
    messages = [SystemMessage(content="Verify."), HumanMessage(content='{"candidates":[]}')]
    turn = await session.invoke(messages, stage="verification")
    assert session.native_tools is True
    assert turn.response is response
    assert turn.output is None
    assert turn.tool_calls == [{"id": "provider-call", "name": "readReviewFile", "arguments": {"path": "caller.py"}}]
    assert model.schemas == native_tool_definitions(SCHEMAS)
    assert model.bound.ainvoke.call_args.args[0] == messages
    assert not model.bound.ainvoke.call_args.kwargs
    observation = result_message(turn.tool_calls[0], {"status": "ready", "content": "caller()"})
    assert isinstance(observation, ToolMessage)
    assert observation.tool_call_id == "provider-call"
    final = await session.invoke([*messages, turn.response, observation], stage="verification")
    assert final.output == {"decisions": []}
    assert model.bound.ainvoke.call_args.args[0][-2].additional_kwargs["signature"] == "provider-signature"


@pytest.mark.asyncio
async def test_unsupported_binding_fallback_is_observable_before_one_request():
    class Unsupported:
        ainvoke = AsyncMock(return_value=AIMessage(content=json.dumps({"toolCalls": [
            {"name": "readReviewFile", "arguments": {"path": "caller.py"}},
        ]})))

        def bind_tools(self, schemas):
            raise NotImplementedError("No native functions")

    model = Unsupported()
    session = ReviewAgentSession(model, REQUEST, SCHEMAS)
    assert not session.native_tools
    assert session.diagnostics
    result = await session.invoke([HumanMessage(content="Review.")], stage="verification")
    assert result.tool_calls[0]["protocol"] == "json"
    assert result.tool_calls[0]["arguments"] == {"path": "caller.py"}
    assert model.ainvoke.await_count == 1
    assert isinstance(model.ainvoke.call_args.args[0][0], SystemMessage)


@pytest.mark.asyncio
async def test_provider_error_never_retries_paid_request_with_json_fallback():
    model = NativeModel([RuntimeError("Provider unavailable")])
    session = ReviewAgentSession(model, REQUEST, SCHEMAS)
    with pytest.raises(RuntimeError, match="Provider unavailable"):
        await session.invoke([HumanMessage(content="Verify.")], stage="verification")
    assert model.bound.ainvoke.await_count == 1
    assert session.native_tools


def test_invalid_native_schema_does_not_silently_disable_native_tools():
    class InvalidSchema:
        def bind_tools(self, schemas):
            raise ValueError("invalid schema")

    with pytest.raises(ValueError, match="invalid schema"):
        ReviewAgentSession(InvalidSchema(), REQUEST, SCHEMAS)


@pytest.mark.asyncio
async def test_native_model_json_requests_are_observable_without_replay():
    model = NativeModel([AIMessage(content=json.dumps({"toolCalls": [
        {"name": "readReviewFile", "arguments": {"path": "caller.py"}},
    ]}))])
    session = ReviewAgentSession(model, REQUEST, SCHEMAS)
    result = await session.invoke([HumanMessage(content="Review.")], stage="verification")
    assert result.tool_calls[0]["protocol"] == "json"
    assert result.diagnostics
    assert model.bound.ainvoke.await_count == 1


@pytest.mark.asyncio
async def test_openrouter_native_turn_retains_provider_reasoning_configuration(monkeypatch):
    from llm.openai_adapters import ChatOpenRouter
    from llm.reasoning_policy import ReasoningEffort

    model = ChatOpenRouter(api_key="test-key", model="test-model", extra_body={"provider": {"order": ["provider"]}})
    responses = AsyncMock(return_value=AIMessage(content='{"decisions":[]}'))
    monkeypatch.setattr(ChatOpenRouter, "ainvoke", responses)
    session = ReviewAgentSession(model, REQUEST, SCHEMAS, effort=ReasoningEffort.LOW)
    await session.invoke([HumanMessage(content="Verify.")], stage="verification")
    assert responses.call_args.kwargs["extra_body"] == {"provider": {"order": ["provider"]}, "reasoning": {"effort": "low"}}
    assert responses.call_args.kwargs["tools"][0]["function"]["name"] == "readReviewFile"
    assert "response_format" not in responses.call_args.kwargs


@pytest.mark.parametrize("provider", ["openai", "openrouter", "anthropic", "google"])
@pytest.mark.asyncio
async def test_every_provider_binds_real_mcp_inventory_without_network(provider):
    from llm.llm_factory import LLMFactory

    model = LLMFactory.create_llm(ai_provider=provider, ai_model="gemini-2.5-flash" if provider == "google" else "test-model", ai_api_key="test-key")
    tools = VerificationTools(rag_client=None, binding={}, parts=[])
    session = ReviewAgentSession(model, REQUEST, review_tool_schemas(await tools.schemas()), tool_choice="any")
    assert session.native_tools
    definitions = session.model.kwargs["tools"]
    assert len(definitions) > 1
    assert STEP_TOOL in str(definitions)
    assert "findReviewFiles" in str(definitions)
    assert "regex" in str(definitions)
    assert any(value in str(session.model.kwargs.get("tool_choice")) for value in ("required", "any", "ANY"))
    assert not session.diagnostics


@pytest.mark.asyncio
async def test_native_turn_preserves_settled_decisions_alongside_evidence_requests():
    decision = {"candidateId": "candidate-1", "verdict": "dismiss", "reason": "Caller guards the input", "evidenceIds": ["read-1"]}
    response = AIMessage(content=json.dumps({"decisions": [decision]}), tool_calls=[
        {"name": "readReviewFile", "args": {"path": "other.py"}, "id": "next-evidence"},
    ])
    session = ReviewAgentSession(NativeModel([response]), REQUEST, SCHEMAS)
    turn = await session.invoke([HumanMessage(content="Verify remaining work.")], stage="verification")
    assert turn.output == {"decisions": [decision]}
    assert turn.tool_calls[0]["id"] == "next-evidence"


def test_openai_native_payload_contains_real_function_and_tool_roles():
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(api_key="test-key", model="test-model")
    session = ReviewAgentSession(model, REQUEST, SCHEMAS)
    assistant = AIMessage(content="", tool_calls=[
        {"name": "readReviewFile", "args": {"path": "caller.py"}, "id": "read-id"},
    ])
    observation = result_message({"id": "read-id", "name": "readReviewFile"}, {"status": "ready", "content": "caller()"})
    payload = model._get_request_payload([SystemMessage(content="Verify."), HumanMessage(content="Candidate."), assistant, observation], **session.model.kwargs)
    assert payload["tools"][0]["function"]["name"] == "readReviewFile"
    assert [message["role"] for message in payload["messages"]] == ["system", "user", "assistant", "tool"]
    assert payload["messages"][2]["tool_calls"][0]["id"] == "read-id"
    assert payload["messages"][3]["tool_call_id"] == "read-id"
    assert "response_format" not in payload


def test_anthropic_native_payload_preserves_tool_use_and_result_pair():
    from langchain_anthropic import ChatAnthropic

    model = ChatAnthropic(api_key="test-key", model="test-model", max_tokens=100)
    session = ReviewAgentSession(model, REQUEST, SCHEMAS)
    assistant = AIMessage(content="", tool_calls=[
        {"name": "readReviewFile", "args": {"path": "caller.py"}, "id": "read-id"},
    ])
    observation = result_message({"id": "read-id", "name": "readReviewFile"}, {"status": "ready", "content": "caller()"})
    payload = model._get_request_payload([SystemMessage(content="Verify."), HumanMessage(content="Candidate."), assistant, observation], **session.model.kwargs)
    assert payload["tools"][0]["name"] == "readReviewFile"
    assert payload["messages"][-2]["content"][0]["type"] == "tool_use"
    assert payload["messages"][-1]["content"][0]["type"] == "tool_result"
    assert payload["messages"][-1]["content"][0]["tool_use_id"] == "read-id"


def step(*, assessments=(), requests=(), findings=(), identifier="step"):
    calls = []
    if assessments or findings:
        calls.append({"id": identifier, "name": STEP_TOOL, "args": {
            "assessments": list(assessments), "findings": list(findings),
        }})
    for request in requests:
        for call in request["calls"]:
            calls.append({"id": f"{identifier}-read-{len(calls)}", "name": call["name"], "args": {
                **call["arguments"], "workIds": request["workIds"], "missingFact": request["missingFact"],
            }})
    return AIMessage(content="", tool_calls=calls)


def assessment(verdict, *, reason="The changed expression divides by zero", evidence=("diff:part",)):
    return {"workId": "work-1", "verdict": verdict, "reason": reason, "evidenceIds": list(evidence)}


@pytest.mark.asyncio
async def test_native_verifier_rebuilds_exact_evidence_context_and_isolates_unrelated_cases(tmp_path):
    from langchain_openai import ChatOpenAI
    from service.review.verifier import ReviewVerifier

    target, overlay = tmp_path / "target", tmp_path / "overlay"
    target.mkdir()
    (overlay / "files").mkdir(parents=True)
    first_source = "def first(value):\n    return value / 0  # first_mechanism_source\n"
    second_source = "def second(value):\n    return value / 0  # second_mechanism_source\n"
    caller_source = "def caller(value):\n    return first(value)  # no compensating guard\n"
    (target / "caller.py").write_text(caller_source)
    (overlay / "files" / "first.py").write_text(first_source)
    (overlay / "files" / "second.py").write_text(second_source)
    (overlay / "manifest.json").write_text(json.dumps({"changedFiles": ["first.py", "second.py"], "deletedFiles": []}))
    parts = [SimpleNamespace(id=f"part-{index}", path=path, side="proposed", anchors={2: "return value / 0"},
                             diff=f"@@ -2 +2 @@\n-return value\n+return value / 0 # {path}\n")
             for index, path in enumerate(("first.py", "second.py"), 1)]
    findings = [{"partId": part.id, "file": part.path, "line": 2, "title": "Unconditional zero divisor",
                 "reason": f"Invoking {part.path} divides by zero", "batchIds": [f"batch-{index}"]}
                for index, part in enumerate(parts, 1)]
    sources = [{"status": "ready", "path": part.path, "side": "proposed", "startLine": 1,
                "endLine": 2, "content": source, "origin": "review_overlay"}
               for part, source in zip(parts, (first_source, second_source))]
    plan = AIMessage(content=json.dumps({"groups": [
        {"caseIds": ["case-1"]}, {"caseIds": ["case-2"]},
    ]}))
    missing_caller = step(assessments=[assessment("needs_evidence", reason="Caller guard could make the failing return unreachable", evidence=())])
    first_read = step(requests=[{"workIds": ["work-1"], "missingFact": "Caller compensation for first()",
                                 "calls": [{"name": "readReviewFile", "arguments": {"path": "caller.py"}}]}])
    first_verdict = step(assessments=[assessment("confirmed", evidence=("read-1", "read-2"))])
    second_verdict = step(assessments=[assessment("confirmed", evidence=("read-1",))])
    partition = AIMessage(content=json.dumps({"groups": [
        {"memberIds": ["issue-1"], "representativeId": "issue-1"},
        {"memberIds": ["issue-2"], "representativeId": "issue-2"},
    ]}))
    model = NativeModel([plan, missing_caller, first_read, first_verdict, second_verdict, partition])
    output = await ReviewVerifier(None).verify(llm=model, request=REQUEST, findings=findings,
        summaries=[{"batchId": "unrelated", "paths": ["other.py"], "summary": {"contracts": ["irrelevant summary corpus"]}}],
        parts=parts, source_context=sources,
        binding={"target_repo_path": str(target), "review_overlay_path": str(overlay)})
    assert output.issues == [{**finding, "reason": assessment("confirmed")["reason"]} for finding in findings]
    assert not output.diagnostics
    assert model.bound.ainvoke.await_count == 6
    assert {options["tool_choice"] for _, options in model.bindings} == {STEP_TOOL, "any"}
    calls = model.bound.ainvoke.call_args_list
    initial_messages, continued_messages, next_messages = [calls[index].args[0] for index in (1, 3, 4)]
    initial = json.loads(initial_messages[1][1])
    continued = json.loads(continued_messages[1][1])
    assert all(len(messages) == 2 for messages in (initial_messages, continued_messages, next_messages))
    assert initial["workItems"][0]["id"] == "work-1"
    assert initial["evidence"][0]["id"] == "diff:part-1"
    assert initial["evidence"][1]["result"]["content"] == first_source
    assert "second_mechanism_source" not in str(initial_messages)
    assert "first_mechanism_source" in str(continued_messages)
    assert next(item for item in continued["evidence"] if item["id"] == "read-2")["result"]["content"] == caller_source
    assert "assistant" not in [getattr(item, "type", None) for item in continued_messages]
    # The actual provider serializes one selected function and a fresh source
    # packet; no stale assistant/tool transcript or unrelated case source.
    wire = ChatOpenAI(api_key="test-key", model="test-model")._get_request_payload(
        continued_messages, tools=model.schemas,
        tool_choice="required")
    assert [item["role"] for item in wire["messages"]] == ["system", "user"]
    assert STEP_TOOL in {tool["function"]["name"] for tool in wire["tools"]}
    assert wire["tool_choice"] == "required"
    assert "first_mechanism_source" not in str(next_messages)
    assert "no compensating guard" not in str(next_messages)
    assert "second_mechanism_source" in str(next_messages)
    assert "irrelevant summary corpus" not in str(next_messages)
    assert json.loads(next_messages[1][1])["caseId"] != initial["caseId"]
    final_messages = calls[5].args[0]
    assert set(json.loads(final_messages[1][1])) == {"issues"}
    assert "first_mechanism_source" not in str(final_messages)
    assert "tools" not in calls[5].kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("valid_call", [True, False])
async def test_invalid_step_arguments_get_repair_feedback_without_unbound_tool_execution(valid_call, monkeypatch):
    from langchain_openai import ChatOpenAI
    from service.review.verifier import ReviewVerifier

    part = SimpleNamespace(id="part", path="service.py", side="proposed", anchors={2: "return value / 0"},
                           diff="@@ -2 +2 @@\n-return value\n+return value / 0\n")
    finding = {"partId": part.id, "file": part.path, "line": 2, "title": "Zero divisor", "reason": "Division by zero"}
    request_step = step(assessments=[assessment("needs_evidence", evidence=())], requests=[{
        "workIds": ["work-1"], "missingFact": "The enclosing return and guard", "calls": [
            {"name": "readReviewFile", "arguments": {"path": "service.py"}}]}])
    broken = AIMessage(content="", additional_kwargs={"signature": "captured-original"},
        tool_calls=request_step.tool_calls if valid_call else [],
        invalid_tool_calls=[{"name": STEP_TOOL, "args": '{"assessments":', "id": "invalid", "error": "invalid JSON"}])
    model = NativeModel([broken, step(assessments=[assessment("confirmed")])])
    execute = AsyncMock(return_value={"status": "ready", "path": part.path, "side": "proposed",
                                     "startLine": 1, "endLine": 2, "content": "def f(value):\n    return value / 0\n"})
    monkeypatch.setattr(VerificationTools, "call", execute)
    result = await ReviewVerifier(None).verify(llm=model, request=REQUEST, findings=[finding], summaries=[], parts=[part], binding={})
    assert result.issues == [{**finding, "reason": assessment("confirmed")["reason"]}] and not result.diagnostics
    assert execute.await_count == int(valid_call)
    assert model.ainvoke.await_count == 2
    assert any("invalid native tool arguments" in message for message in result.warnings)
    followup = model.ainvoke.call_args_list[1].args[0]
    assert len(followup) == 2 and not any(message is broken for message in followup)
    feedback = json.loads(followup[1][1])["reviewWork"]
    assert any("not executed" in item for item in feedback["corrections"])
    wire = ChatOpenAI(api_key="test-key", model="test-model")._get_request_payload(followup)
    assert [message["role"] for message in wire["messages"]] == ["system", "user"]


@pytest.mark.asyncio
async def test_malformed_request_sibling_preserves_valid_assessment_without_more_source(monkeypatch):
    from service.review.verifier import ReviewVerifier

    part = SimpleNamespace(id="part", path="service.py", side="proposed", anchors={2: "return value / 0"},
                           diff="@@ -2 +2 @@\n-return value\n+return value / 0\n")
    finding = {"partId": part.id, "file": part.path, "line": 2, "title": "Zero divisor", "reason": "Division by zero"}
    first = AIMessage(content=json.dumps({"assessments": [assessment("confirmed")],
                                         "evidenceRequests": "invalid sibling", "findings": []}))
    model = NativeModel([first, step(assessments=[assessment("confirmed")])])
    execute = AsyncMock()
    monkeypatch.setattr(VerificationTools, "call", execute)
    result = await ReviewVerifier(None).verify(llm=model, request=REQUEST, findings=[finding], summaries=[], parts=[part], binding={})
    assert result.issues == [{**finding, "reason": assessment("confirmed")["reason"]}]
    assert result.decisions[0]["verdict"] == "keep"
    assert not result.diagnostics
    execute.assert_not_awaited()
    assert model.ainvoke.await_count == 2
    assert json.loads(model.ainvoke.call_args_list[1].args[0][1][1])["reviewWork"]["pendingWorkIds"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    'Completed review:\n{"decisions":[],"note":"literal, } text",}',
    '```json\n{"decisions":[],"note":"literal, } text",}\n```',
])
async def test_complete_object_in_prose_or_fence_preserves_quoted_contents(text):
    session = ReviewAgentSession(NativeModel([AIMessage(content=text)]), REQUEST, SCHEMAS)
    turn = await session.invoke([HumanMessage(content="Verify.")], stage="verification")
    assert turn.output == {"decisions": [], "note": "literal, } text"}
    assert turn.output_error is None


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", [True, False])
async def test_prose_final_is_format_recovery_not_outage_publication(repair):
    from service.review.verifier import ReviewVerifier

    part = SimpleNamespace(id="part", path="service.py", side="proposed", anchors={2: "return value / 0"},
                           diff="@@ -2 +2 @@\n-return value\n+return value / 0\n")
    candidate = {"partId": part.id, "file": part.path, "line": 2, "title": "Caller failure", "reason": "An external caller may break"}
    prose = AIMessage(content="The alleged external caller is absent from the available source.")
    fixed = step(assessments=[assessment("uncertain", reason="The allegation assumes an external caller not supplied in the source.", evidence=())])
    model = NativeModel([prose, fixed] if repair else [prose, prose])
    result = await ReviewVerifier(None).verify(llm=model, request=REQUEST, findings=[candidate],
                                               summaries=[], parts=[part], binding={})
    assert result.issues == []
    assert model.ainvoke.await_count == 2
    followup = model.ainvoke.call_args_list[1].args[0]
    assert not any(message is prose for message in followup)
    feedback = json.loads(followup[-1][1])["reviewWork"]
    assert any("not a complete structured review step" in item for item in feedback["corrections"])
    assert feedback["pendingWorkIds"] == ["work-1"]
    assert json.loads(followup[-1][1])["evidence"] == json.loads(model.ainvoke.call_args_list[0].args[0][-1][1])["evidence"]
    assert all("retained" not in diagnostic and "interrupted" not in diagnostic for diagnostic in result.diagnostics)
    if repair:
        assert result.decisions[0]["verdict"] == "uncertain"


@pytest.mark.asyncio
async def test_scoped_native_reads_and_assessments_normalize_without_echoing_needs_evidence():
    model = NativeModel([AIMessage(content="", tool_calls=[
        {"id": "read", "name": "readReviewFile", "args": {"path": "header.hbs", "workIds": ["work-2"],
          "missingFact": "The panel's actual parent element"}},
        {"id": "settled", "name": STEP_TOOL, "args": {"assessments": [assessment("refuted")]}}
    ])])
    schemas = review_tool_schemas(SCHEMAS)
    session = ReviewAgentSession(model, REQUEST, schemas, tool_choice="any")
    turn = await session.invoke([HumanMessage(content="Review the supplied work.")], stage="verification_validate")
    steps, errors = submitted_steps(turn, tool_names={"readReviewFile"})
    assert not errors
    assert steps[0]["evidenceRequests"][0] == {
        "workIds": ["work-2"], "missingFact": "The panel's actual parent element",
        "calls": [{"name": "readReviewFile", "arguments": {"path": "header.hbs"}}],
    }
    assert steps[1]["assessments"] == [assessment("refuted")]
    assert model.ainvoke.await_count == 1
    assert model.binding_options == {"tool_choice": "any"}
    read_schema = next(item for item in schemas if item["name"] == "readReviewFile")["inputSchema"]
    assert set(read_schema["required"]) == {"path", "workIds", "missingFact"}
    assert SCHEMAS[0]["inputSchema"]["required"] == ["path"]


@pytest.mark.asyncio
async def test_empty_outcome_and_unscoped_read_cannot_discard_valid_sibling():
    model = NativeModel([AIMessage(content="", tool_calls=[
        {"id": "empty", "name": STEP_TOOL, "args": {"assessments": [], "findings": []}},
        {"id": "unscoped", "name": "readReviewFile", "args": {"path": "caller.py"}},
        {"id": "unknown", "name": "shell", "args": {"command": "echo unsafe"}},
        {"id": "settled", "name": STEP_TOOL, "args": {"assessments": [assessment("refuted")]}}
    ])])
    session = ReviewAgentSession(model, REQUEST, review_tool_schemas(SCHEMAS), tool_choice="any")
    turn = await session.invoke([HumanMessage(content="Review.")], stage="verification_validate")
    steps, errors = submitted_steps(turn, tool_names={"readReviewFile"})
    assert len(steps) == 1 and steps[0]["assessments"] == [assessment("refuted")]
    assert len(errors) == 3
    assert any("at least one" in message for message in errors)
    assert any("not executed" in message for message in errors)
    assert any("Unknown review tool" in message for message in errors)


@pytest.mark.asyncio
async def test_json_fallback_uses_same_scoped_tools_before_one_provider_invocation():
    class NoBinding:
        ainvoke = AsyncMock(return_value=AIMessage(content=json.dumps({"toolCalls": [{
            "name": "readReviewFile", "arguments": {"path": "header.hbs", "workIds": ["work-1"],
            "missingFact": "The nesting of the header panel"},
        }]})))

    model = NoBinding()
    session = ReviewAgentSession(model, REQUEST, review_tool_schemas(SCHEMAS), tool_choice="any")
    turn = await session.invoke([HumanMessage(content="Verify.")], stage="verification_validate")
    steps, errors = submitted_steps(turn, tool_names={"readReviewFile"})
    assert not errors and steps[0]["evidenceRequests"][0]["workIds"] == ["work-1"]
    instruction = model.ainvoke.call_args.args[0][0].content
    assert "toolCalls" in instruction and "selected tool" not in instruction
    assert model.ainvoke.await_count == 1


@pytest.mark.asyncio
async def test_complete_json_assessment_survives_native_read_sibling():
    response = AIMessage(content=json.dumps({"assessments": [assessment("refuted")]}), tool_calls=[
        {"id": "source", "name": "readReviewFile", "args": {"path": "other.py", "workIds": ["work-2"],
            "missingFact": "The remaining caller's validation"}},
    ])
    session = ReviewAgentSession(NativeModel([response]), REQUEST, review_tool_schemas(SCHEMAS), tool_choice="any")
    turn = await session.invoke([HumanMessage(content="Verify remaining work.")], stage="verification_validate")
    steps, errors = submitted_steps(turn, tool_names={"readReviewFile"})
    assert not errors
    assert steps[0]["evidenceRequests"][0]["workIds"] == ["work-2"]
    assert steps[1]["assessments"] == [assessment("refuted")]


@pytest.mark.asyncio
async def test_same_complete_outcome_in_native_call_and_json_content_is_not_replayed():
    outcome = {"assessments": [assessment("refuted")], "findings": []}
    response = AIMessage(content=json.dumps(outcome), tool_calls=[
        {"id": "outcome", "name": STEP_TOOL, "args": outcome},
    ])
    session = ReviewAgentSession(NativeModel([response]), REQUEST, review_tool_schemas(SCHEMAS), tool_choice="any")
    turn = await session.invoke([HumanMessage(content="Verify remaining work.")], stage="verification_validate")
    steps, errors = submitted_steps(turn, tool_names={"readReviewFile"})
    assert not errors
    assert len(steps) == 1 and steps[0]["assessments"] == [assessment("refuted")]
