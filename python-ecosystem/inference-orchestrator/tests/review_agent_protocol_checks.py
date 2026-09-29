"""Offline provider-protocol checks; no model calls or review quality claims."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from service.review.agent_calls import ReviewAgentSession, native_tool_definitions, result_message
from service.review.verification_tools import VerificationTools


SCHEMAS = [{"name": "readReviewFile", "description": "Read exact source.", "inputSchema": {
    "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"],
}}]
REQUEST = SimpleNamespace(aiProvider="openai", pullRequestId="17")


class NativeModel:
    def __init__(self, responses):
        self.bound = SimpleNamespace(ainvoke=AsyncMock(side_effect=responses))
        self.ainvoke = self.bound.ainvoke
        self.schemas = None

    def bind_tools(self, schemas):
        self.schemas = schemas
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
    tools.register_decisions(lambda **values: {"status": "ready"})
    session = ReviewAgentSession(model, REQUEST, await tools.schemas())
    assert session.native_tools
    definitions = session.model.kwargs["tools"]
    assert len(definitions) == 10
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


@pytest.mark.asyncio
async def test_native_verifier_keeps_history_isolated_between_concurrent_cases(tmp_path):
    from langchain_openai import ChatOpenAI
    from service.review.verifier import ReviewVerifier

    target = tmp_path / "target"
    overlay = tmp_path / "overlay"
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
    first_read = AIMessage(content="", tool_calls=[
        {"id": "caller-read", "name": "readReviewFile", "args": {"path": "caller.py"}},
    ], additional_kwargs={"signature": "keep-provider-metadata"})
    first_verdict = AIMessage(content="", tool_calls=[
        {"id": "first-verdict", "name": "recordReviewDecisions", "args": {"decisions": [
            {"candidateId": "candidate-1", "verdict": "keep", "reason": "The changed return divides by zero; its caller supplies no guard",
             "evidenceIds": ["read-1", "read-2"]},
        ]}},
    ])
    second_verdict = AIMessage(content=json.dumps({"decisions": [
        {"candidateId": "candidate-1", "verdict": "keep", "reason": "This independent function also divides by zero",
         "evidenceIds": ["read-1"]},
    ]}))
    final_partition = AIMessage(content=json.dumps({"groups": [
        {"memberIds": ["issue-1"], "representativeId": "issue-1"},
        {"memberIds": ["issue-2"], "representativeId": "issue-2"},
    ]}))
    replies = {"case-1": iter([first_read, first_verdict]), "case-2": iter([second_verdict]),
               "reconcile": iter([final_partition])}

    async def respond(messages, **options):
        payload = json.loads(messages[1][1])
        return next(replies[payload.get("caseId", "reconcile")])

    model = NativeModel([])
    model.bound.ainvoke.side_effect = respond
    output = await ReviewVerifier(None).verify(llm=model, request=REQUEST, findings=findings,
        summaries=[{"batchId": "unrelated", "paths": ["other.py"], "summary": {"contracts": ["irrelevant summary corpus"]}}],
        parts=parts, source_context=sources,
        binding={"target_repo_path": str(target), "review_overlay_path": str(overlay)})
    assert output.issues == findings
    assert not output.diagnostics
    assert model.bound.ainvoke.await_count == 4

    histories = {}
    for call in model.bound.ainvoke.call_args_list:
        messages = call.args[0]
        case_id = json.loads(messages[1][1]).get("caseId", "reconcile")
        histories.setdefault(case_id, []).append(messages)
    initial_messages, continued_messages = histories["case-1"]
    next_case_messages, = histories["case-2"]
    initial_payload = json.loads(initial_messages[1][1])
    assert len(initial_messages) == 2
    assert continued_messages[:2] == initial_messages  # Stable full source prefix.
    assert initial_payload["evidence"][0]["id"] == "diff:part-1"
    assert initial_payload["evidence"][1]["result"]["content"] == first_source
    assert "second_mechanism_source" not in str(initial_messages)
    assert continued_messages[-2] is first_read
    assert continued_messages[-2].additional_kwargs["signature"] == "keep-provider-metadata"
    assert isinstance(continued_messages[-1], ToolMessage)
    assert continued_messages[-1].tool_call_id == "caller-read"
    assert json.loads(continued_messages[-1].content)["content"] == caller_source
    # Check that the actual OpenAI adapter accepts this native history and
    # serializes the matching assistant/tool IDs, rather than JSON pseudo-tools.
    provider = ChatOpenAI(api_key="test-key", model="test-model")
    wire = provider._get_request_payload(continued_messages, tools=native_tool_definitions(SCHEMAS))
    assert [item["role"] for item in wire["messages"]] == ["system", "user", "assistant", "tool"]
    assert wire["messages"][-2]["tool_calls"][0]["id"] == wire["messages"][-1]["tool_call_id"] == "caller-read"

    assert len(next_case_messages) == 2
    assert "first_mechanism_source" not in str(next_case_messages)
    assert "no compensating guard" not in str(next_case_messages)
    assert "second_mechanism_source" in str(next_case_messages)
    assert "irrelevant summary corpus" not in str(next_case_messages)
    next_payload = json.loads(next_case_messages[1][1])
    assert next_payload["caseId"] != initial_payload["caseId"]
    assert next_payload["evidence"][0]["id"] == "diff:part-2"
    final_messages = model.bound.ainvoke.call_args_list[3].args[0]
    final_payload = json.loads(final_messages[1][1])
    assert len(final_messages) == 2 and set(final_payload) == {"issues"}
    assert len(final_payload["issues"]) == 2
    assert "first_mechanism_source" not in str(final_messages)
    assert "second_mechanism_source" not in str(final_messages)
    assert "tools" not in model.bound.ainvoke.call_args_list[3].kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("valid_call", [True, False])
async def test_invalid_native_arguments_get_error_receipts_without_tool_execution(valid_call, monkeypatch):
    from langchain_openai import ChatOpenAI
    from service.review.verifier import ReviewVerifier

    part = SimpleNamespace(id="part", path="service.py", side="proposed", anchors={2: "return value / 0"},
                           diff="@@ -2 +2 @@\n-return value\n+return value / 0\n")
    finding = {"partId": part.id, "file": part.path, "line": 2, "title": "Zero divisor", "reason": "Division by zero"}
    broken = AIMessage(content="", additional_kwargs={"signature": "preserved-original"},
        tool_calls=[{"name": "readReviewFile", "args": {"path": "service.py"}, "id": "valid"}] if valid_call else [],
        invalid_tool_calls=[{"name": "readReviewFile", "args": '{"path":', "id": "invalid", "error": "invalid JSON"}])
    final = AIMessage(content=json.dumps({"decisions": [{"candidateId": "candidate-1", "verdict": "keep",
        "reason": "The changed expression divides by zero", "evidenceIds": ["diff:part"]}]}))
    model = NativeModel([broken, final])
    execute = AsyncMock(return_value={"status": "ready", "path": part.path, "side": "proposed",
                                     "startLine": 1, "endLine": 2, "content": "def f(value):\n    return value / 0\n"})
    monkeypatch.setattr(VerificationTools, "call", execute)
    result = await ReviewVerifier(None).verify(llm=model, request=REQUEST, findings=[finding],
                                              summaries=[], parts=[part], binding={})
    assert result.issues == [finding]
    assert not result.diagnostics
    assert execute.await_count == int(valid_call)
    if valid_call:
        execute.assert_awaited_once_with("readReviewFile", {"path": "service.py"})
    assert model.ainvoke.await_count == 2
    assert any("invalid native tool arguments" in message for message in result.warnings)
    followup = model.ainvoke.call_args_list[1].args[0]
    assert any(message is broken for message in followup)
    error_reply = next(message for message in followup if isinstance(message, ToolMessage) and message.tool_call_id == "invalid")
    assert error_reply.status == "error"
    assert "not executed" in error_reply.content
    assert broken.additional_kwargs["signature"] == "preserved-original"
    wire = ChatOpenAI(api_key="test-key", model="test-model")._get_request_payload(followup)
    assistant = next(message for message in wire["messages"] if message["role"] == "assistant")
    call_ids = {call["id"] for call in assistant["tool_calls"]}
    reply_ids = {message["tool_call_id"] for message in wire["messages"] if message["role"] == "tool"}
    assert call_ids == reply_ids == ({"valid", "invalid"} if valid_call else {"invalid"})


@pytest.mark.asyncio
async def test_malformed_json_sibling_preserves_valid_decision_and_native_read(monkeypatch):
    from service.review.verifier import ReviewVerifier

    part = SimpleNamespace(id="part", path="service.py", side="proposed", anchors={2: "return value / 0"},
                           diff="@@ -2 +2 @@\n-return value\n+return value / 0\n")
    finding = {"partId": part.id, "file": part.path, "line": 2, "title": "Zero divisor", "reason": "Division by zero"}
    decision = {"candidateId": "candidate-1", "verdict": "keep", "reason": "Changed zero divisor", "evidenceIds": ["diff:part"]}
    first = AIMessage(content=json.dumps({"decisions": [decision], "investigations": "invalid sibling"}),
                      tool_calls=[{"name": "readReviewFile", "args": {"path": "service.py"}, "id": "counterevidence"}])
    final = AIMessage(content='{"decisions":[],"investigations":[],"findings":[]}')
    model = NativeModel([first, final])
    execute = AsyncMock(return_value={"status": "ready", "path": part.path, "side": "proposed",
                                     "startLine": 1, "endLine": 2, "content": "def f(value):\n    return value / 0\n"})
    monkeypatch.setattr(VerificationTools, "call", execute)
    result = await ReviewVerifier(None).verify(llm=model, request=REQUEST, findings=[finding],
                                              summaries=[], parts=[part], binding={})
    assert result.issues == [finding]
    assert result.decisions[0]["verdict"] == "keep"
    assert not result.diagnostics
    execute.assert_awaited_once_with("readReviewFile", {"path": "service.py"})
    assert model.ainvoke.await_count == 2
    assert any(isinstance(message, ToolMessage) and message.tool_call_id == "counterevidence"
               for message in model.ainvoke.call_args_list[1].args[0])
