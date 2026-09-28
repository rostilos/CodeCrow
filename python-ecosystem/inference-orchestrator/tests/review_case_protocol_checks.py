"""Actual structured-step source checks, without the unit-suite provider stubs."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from service.review.review_service import _parts
from service.review.review_step import STEP_TOOL
from service.review.verifier import ReviewVerifier


FIXTURE = json.loads((Path(__file__).parent / "fixtures/review_reconciliation/source_case_inputs.json").read_text())["discourse_header_layout"]


def submit(assessment, calls=(), findings=()):
    outcomes = [{"id": "outcome", "name": STEP_TOOL, "args": {
        "assessments": [assessment], "findings": list(findings),
    }}]
    reads = [{"id": f"read-{index}", "name": call["name"], "args": {
        **call["arguments"], "workIds": ["work-1"], "missingFact": assessment["reason"],
    }} for index, call in enumerate(calls)]
    return AIMessage(content="", tool_calls=[*outcomes, *reads])


def tree(tmp_path):
    parts, missing = _parts(FIXTURE["diff"])
    assert not missing
    panel = next(part for part in parts if 37 in part.anchors)
    target, overlay = tmp_path / "target", tmp_path / "overlay"
    (overlay / "files").mkdir(parents=True)
    for path, content in FIXTURE["additionalFiles"].items():
        file = target / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
    (overlay / "manifest.json").write_text(json.dumps({"changedFiles": [panel.path], "deletedFiles": []}))
    return parts, panel, {"target_repo_path": str(target), "review_overlay_path": str(overlay)}


@pytest.mark.asyncio
async def test_template_navigation_keeps_exact_source_in_canonical_steps(tmp_path):
    parts, panel, binding = tree(tmp_path)
    template_path, template_source = next(iter(FIXTURE["additionalFiles"].items()))
    messages_seen, bound_schemas = [], []

    class NativeModel:
        def bind_tools(self, schemas, **options):
            names = {schema["function"]["name"] for schema in schemas}
            assert options == {"tool_choice": STEP_TOOL if names == {STEP_TOOL} else "any"}
            bound_schemas.append(schemas)
            return self

        async def ainvoke(self, messages, **options):
            messages_seen.append(list(messages))
            packet = json.loads(messages[1][1])
            assert "max_tokens" not in options
            assessment = {"workId": "work-1", "verdict": "needs_evidence", "evidenceIds": []}
            if len(messages_seen) == 1:
                assert packet["reviewWork"]["phase"] == "assessment"
                return submit({**assessment, "reason": "Locate the server-rendered flex container"})
            if len(messages_seen) == 2:
                assert packet["reviewWork"]["phase"] == "evidence"
                response = submit({**assessment, "reason": "Locate the server-rendered flex container"}, [{
                    "name": "grepReviewCode", "arguments": {"query": 'class="contents"', "mode": "literal", "paths": ["app/views"]},
                }])
                return AIMessage(content="", tool_calls=response.tool_calls[1:])
            if len(messages_seen) == 3:
                assert packet["reviewWork"]["phase"] == "assessment"
                search = next(item["result"] for item in packet["evidence"] if item["kind"] == "grepReviewCode")
                assert search["status"] == "ready"
                assert search["results"] == [{"path": template_path, "matches": [{"line": 3, "text": template_source.splitlines()[2]}]}]
                return submit({**assessment, "reason": "Read the actual parent of the server panel"})
            if len(messages_seen) == 4:
                assert packet["reviewWork"]["phase"] == "evidence"
                response = submit({**assessment, "reason": "Read the actual parent of the server panel"}, [{
                    "name": "readReviewFile", "arguments": {"path": template_path},
                }])
                return AIMessage(content="", tool_calls=response.tool_calls[1:])
            assert len(messages_seen) == 5
            assert packet["reviewWork"]["phase"] == "assessment"
            source = next(item for item in packet["evidence"] if item["result"].get("path") == template_path and item["kind"] == "readReviewFile")
            assert source["result"]["content"] == template_source
            refs = [source["id"], f"diff:{panel.id}"]
            return submit({"workId": "work-1", "verdict": "refuted",
                "reason": "The server-rendered panel is nested inside .row rather than being a direct flex child.",
                "evidenceIds": refs, "issue": {"partId": panel.id, "file": panel.path, "line": 37,
                    "title": "Server-rendered header panel loses right alignment",
                    "reason": "The .panel float is removed, but the unchanged server header nests it inside .row. Only .contents becomes a flex container, so auto margin and order do not replace its former right alignment.",
                    "suggestedFixDescription": "Preserve right alignment for the nested server-rendered panel or update that template's structure.",
                    "evidenceIds": refs}})

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
    assert {len(schemas) for schemas in bound_schemas} == {1, 10}
    assert len(messages_seen) == 5 and all(len(messages) == 2 for messages in messages_seen)
    initial = json.loads(messages_seen[0][1][1])
    assert initial["changePurpose"]["title"] == "Optimize header layout performance with flexbox mixins"
    visible_source = "\n".join(
        str(entry["result"].get("diff") or entry["result"].get("content") or "")
        + "".join(segment["content"] for segment in entry["result"].get("sourceSegments", []))
        for entry in initial["evidence"])
    assert all(line in visible_source for line in FIXTURE["sourceContext"][0]["content"].splitlines())
    assert any(entry["result"].get("sourceReferences") for entry in initial["evidence"] if entry["kind"] == "readReviewFile")
    assert json.dumps(template_source)[1:-1] not in messages_seen[0][1][1]
    assert json.dumps(template_source)[1:-1] in messages_seen[4][1][1]
    assert all(not isinstance(message, AIMessage) for messages in messages_seen for message in messages)


@pytest.mark.asyncio
async def test_missing_compensating_source_is_read_before_deciding_candidate(tmp_path):
    parts, panel, binding = tree(tmp_path)
    template_path, template_source = next(iter(FIXTURE["additionalFiles"].items()))
    calls = []

    class NativeModel:
        def bind_tools(self, schemas, **options):
            return self

        async def ainvoke(self, messages, **options):
            calls.append(list(messages))
            if len(calls) == 1:
                return submit({"workId": "work-1", "verdict": "needs_evidence",
                    "reason": "The server-rendered consumer could refute the claimed unused selector",
                    "evidenceIds": [f"diff:{panel.id}"]})
            if len(calls) == 2:
                response = submit({"workId": "work-1", "verdict": "needs_evidence",
                    "reason": "The server-rendered consumer could refute the claimed unused selector",
                    "evidenceIds": []}, [{"name": "readReviewFile", "arguments": {"path": template_path}}])
                return AIMessage(content="", tool_calls=response.tool_calls[1:])
            assert len(calls) == 3
            packet = json.loads(messages[1][1])
            source = next(item for item in packet["evidence"] if item["result"].get("path") == template_path)
            assert source["result"]["content"] == template_source
            return submit({"workId": "work-1", "verdict": "refuted",
                "reason": "The server-rendered header has a panel element, refuting the no-consumer claim.",
                "evidenceIds": [f"diff:{panel.id}", source["id"]]})

    result = await ReviewVerifier(None).verify(llm=NativeModel(), request=SimpleNamespace(
        aiProvider="openai", pullRequestId="5", prTitle="Convert header layout", prDescription="Use flexbox."),
        findings=[{"partId": panel.id, "file": panel.path, "line": 37, "title": "Panel selector has no server consumer",
                   "reason": "The server header no longer provides a panel element."}], summaries=[], parts=parts,
        binding=binding, source_context=FIXTURE["sourceContext"])
    assert len(calls) == 3
    assert result.issues == []
    assert result.decisions[0]["verdict"] == "dismiss"
