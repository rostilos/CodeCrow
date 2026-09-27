"""Actual native-message context checks; runs without the unit-suite stubs."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from service.review.review_service import _parts
from service.review.verifier import ReviewVerifier


FIXTURE = json.loads((Path(__file__).parent / "fixtures/review_reconciliation/source_case_inputs.json").read_text())["discourse_header_layout"]


@pytest.mark.asyncio
async def test_template_navigation_retains_real_native_history_and_exact_source(tmp_path):
    parts, missing = _parts(FIXTURE["diff"])
    assert not missing
    panel = next(part for part in parts if 37 in part.anchors)
    target = tmp_path / "target"
    overlay = tmp_path / "overlay"
    (overlay / "files").mkdir(parents=True)
    for path, content in FIXTURE["additionalFiles"].items():
        file = target / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
    (overlay / "manifest.json").write_text(json.dumps({"changedFiles": [panel.path], "deletedFiles": []}))
    binding = {"target_repo_path": str(target), "review_overlay_path": str(overlay)}
    template_path, template_source = next(iter(FIXTURE["additionalFiles"].items()))
    messages_seen = []
    responses = []
    bound_schemas = []

    class NativeModel:
        def bind_tools(self, schemas):
            bound_schemas.append(schemas)
            return self

        async def ainvoke(self, messages, **options):
            messages_seen.append(list(messages))
            index = len(messages_seen)
            assert "max_tokens" not in options
            if index == 1:
                result = AIMessage(content="", additional_kwargs={"provider_signature": "grep-signature"}, tool_calls=[{
                    "id": "grep-template", "name": "grepReviewCode",
                    "args": {"query": 'class="contents"', "paths": ["app/views"]},
                }])
            elif index == 2:
                search = json.loads(messages[-1].content)
                assert search["status"] == "ready"
                assert search["results"] == [{"path": template_path, "lines": [3]}]
                result = AIMessage(content="", additional_kwargs={"provider_signature": "read-signature"}, tool_calls=[{
                    "id": "read-template", "name": "readReviewFile", "args": {"path": template_path},
                }])
            else:
                assert index == 3
                source = json.loads(messages[-1].content)
                assert source["content"] == template_source
                result = AIMessage(content=json.dumps({
                    "investigations": [{"id": "header-structure", "status": "resolved",
                        "reason": "The server-rendered panel is nested inside .row rather than being a direct flex child.",
                        "evidenceIds": [source["evidenceId"], f"diff:{panel.id}"]}],
                    "findings": [{"partId": panel.id, "file": panel.path, "line": 37,
                        "title": "Server-rendered header panel loses right alignment",
                        "reason": "The .panel float is removed, but the unchanged server header nests it inside .row. Only .contents becomes a flex container, so auto margin and order do not replace its former right alignment.",
                        "suggestedFixDescription": "Preserve right alignment for the nested server-rendered panel or update that template's structure.",
                        "evidenceIds": [source["evidenceId"], f"diff:{panel.id}"]}],
                }))
            responses.append(result)
            return result

    result = await ReviewVerifier(None).verify(llm=NativeModel(), request=SimpleNamespace(
        aiProvider="openai", pullRequestId="5", prTitle="Optimize header layout performance with flexbox mixins",
        prDescription="Convert existing header layout to flexbox.", projectRules=None, taskContext=None),
        findings=[], summaries=[], parts=parts, binding=binding, source_context=FIXTURE["sourceContext"],
        investigations=[{"id": "header-structure", "partIds": [part.id for part in parts],
                         "paths": [panel.path], "question": "Is the panel a direct child of the new flex container in each header template?"}])

    assert len(result.issues) == 1
    assert result.issues[0]["title"] == "Server-rendered header panel loses right alignment"
    assert result.resolved_investigation_ids == {"header-structure"}
    assert not result.diagnostics
    assert len(bound_schemas) == 1
    assert len(messages_seen) == 3
    initial = json.loads(messages_seen[0][1][1])
    assert initial["changePurpose"]["title"] == "Optimize header layout performance with flexbox mixins"
    assert [entry["result"]["content"] for entry in initial["evidence"] if entry["kind"] == "readReviewFile"] == [FIXTURE["sourceContext"][0]["content"]]
    assert json.dumps(template_source)[1:-1] not in messages_seen[0][1][1]
    assert messages_seen[1][:2] == messages_seen[0]
    assert messages_seen[1][2] is responses[0]
    assert messages_seen[1][2].additional_kwargs["provider_signature"] == "grep-signature"
    assert isinstance(messages_seen[1][3], ToolMessage)
    assert messages_seen[1][3].tool_call_id == "grep-template"
    assert messages_seen[2][:4] == messages_seen[1]
    assert messages_seen[2][4] is responses[1]
    assert messages_seen[2][4].additional_kwargs["provider_signature"] == "read-signature"
    assert isinstance(messages_seen[2][5], ToolMessage)
    assert messages_seen[2][5].tool_call_id == "read-template"


@pytest.mark.asyncio
async def test_new_source_requested_with_last_decision_is_seen_before_case_finishes(tmp_path):
    parts, _ = _parts(FIXTURE["diff"])
    panel = next(part for part in parts if 37 in part.anchors)
    template_path, template_source = next(iter(FIXTURE["additionalFiles"].items()))
    target = tmp_path / "target"
    file = target / template_path
    file.parent.mkdir(parents=True)
    file.write_text(template_source)
    overlay = tmp_path / "overlay"
    (overlay / "files").mkdir(parents=True)
    (overlay / "manifest.json").write_text(json.dumps({"changedFiles": [panel.path], "deletedFiles": []}))
    calls = []

    class NativeModel:
        def bind_tools(self, schemas):
            return self

        async def ainvoke(self, messages, **options):
            calls.append(list(messages))
            if len(calls) == 1:
                # A model can tentatively settle an item and request the exact
                # contract implementation in the same native assistant turn.
                return AIMessage(content=json.dumps({"decisions": [{
                    "candidateId": "candidate-1", "verdict": "keep",
                    "reason": "The selector appears to have no server-rendered consumer.",
                    "evidenceIds": [f"diff:{panel.id}"],
                }]}), tool_calls=[{"id": "check-server-consumer", "name": "readReviewFile", "args": {"path": template_path}}])
            assert len(calls) == 2
            source = json.loads(messages[-1].content)
            assert source["content"] == template_source
            return AIMessage(content=json.dumps({"decisions": [{
                "candidateId": "candidate-1", "verdict": "dismiss",
                "reason": "The server-rendered header has a panel element, refuting the no-consumer claim.",
                "evidenceIds": [f"diff:{panel.id}", source["evidenceId"]],
            }]}))

    result = await ReviewVerifier(None).verify(llm=NativeModel(), request=SimpleNamespace(
        aiProvider="openai", pullRequestId="5", prTitle="Convert header layout", prDescription="Use flexbox."),
        findings=[{"partId": panel.id, "file": panel.path, "line": 37, "title": "Panel selector has no server consumer",
                   "reason": "The server header no longer provides a panel element."}], summaries=[], parts=parts,
        binding={"target_repo_path": str(target), "review_overlay_path": str(overlay)}, source_context=FIXTURE["sourceContext"])
    assert len(calls) == 2
    assert result.issues == []
    assert result.decisions[0]["verdict"] == "dismiss"
