"""Mocked host-level checks for the complete multi-stage review workflow.

These exercise the current planner and prompts without model, MCP, RAG, provider,
queue, or deployment traffic. Provider cases describe their common review DTO
shapes; external webhook/queue behavior is tested by those components separately.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from model.dtos import ReviewRequestDto
from service.review import review_service
from service.review.review_service import ReviewService, _parts
from service.review.verifier import VerificationResult


def change(path="service.py", before="old()", after="new()"):
    return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -1 +1 @@\n-{before}\n+{after}\n")


def request(**overrides):
    values = {
        "projectId": 1, "projectVcsWorkspace": "provider-workspace",
        "projectVcsRepoSlug": "repository", "projectWorkspace": "tenant-one",
        "projectNamespace": "project-one", "aiProvider": "openai",
        "aiModel": "review-model", "aiApiKey": "mock-key",
        "targetBranchName": "main", "sourceBranchName": "feature",
        "pullRequestId": 17, "currentCommitHash": "head123",
        "targetHeadCommitHash": "base123", "localRepoRevision": "base123",
        "localRepoTargetBranch": "main", "rawDiff": change(),
    }
    values.update(overrides)
    return ReviewRequestDto(**values)


def candidate(part, reason="Changed behavior raises for a supported input", **overrides):
    value = {
        "partId": part["id"], "file": part["path"], "line": part["anchorLines"][0],
        "title": "Input contract is violated", "reason": reason,
        "severity": "HIGH", "category": "BUG_RISK",
        "suggestedFixDescription": "Preserve the supported input contract",
    }
    value.update(overrides)
    return value


def default_turn(payload):
    if "ownedParts" in payload:
        return {
            "reviewedHunkIds": [part["id"] for part in payload["ownedParts"]],
            "findings": [],
            "summary": {
                "behaviorChanges": ["Behavior changed in the owned hunk"],
                "contracts": ["Inputs and outputs remain compatible"],
                "risks": [], "unresolvedQuestions": [], "evidence": [],
            },
        }
    return {"findings": [], "investigations": [],
            "checkedScopeIds": [scope["id"] for scope in payload["plannedScopes"]]}


class Model:
    def __init__(self, handler=default_turn):
        self.handler = handler
        self.calls = []
        self.tool_schemas = []

    def bind_tools(self, schemas):
        self.tool_schemas = schemas
        return self

    async def ainvoke(self, messages, **options):
        # A case keeps its initial source packet and appends native tool pairs.
        # Expose observed evidence without changing that outgoing transcript.
        payloads = [json.loads(message[1]) for message in messages
                    if isinstance(message, tuple) and message[0] == "human"]
        payload = dict(payloads[0])
        observations = [dict(json.loads(message.content), tool=message.name)
                        for message in messages if getattr(message, "type", None) == "tool"]
        observations += [value for item in payloads[1:] for value in item.get("toolObservations", [])]
        if observations:
            payload["toolObservations"] = observations
        self.calls.append((payload, options))
        value = self.handler(payload)
        if isinstance(value, str):
            return SimpleNamespace(content=value, tool_calls=[], invalid_tool_calls=[])
        value = dict(value)
        calls = value.pop("toolCalls", []) if self.tool_schemas else []
        return SimpleNamespace(content=json.dumps(value), invalid_tool_calls=[], tool_calls=[{
            "id": f"call-{len(self.calls)}-{index}", "name": call["name"], "args": call["arguments"],
        } for index, call in enumerate(calls)])


def observed_evidence(payload, kind):
    observations = [
        {"id": result["evidenceId"], "kind": result["tool"], "result": result}
        for result in payload.get("toolObservations", []) if result.get("evidenceId")
    ]
    return next((item for item in [*payload.get("evidence", []), *observations] if item["kind"] == kind), None)


@pytest.fixture
def pipeline(monkeypatch):
    from service.review import agent_calls
    monkeypatch.setattr(agent_calls, "ToolMessage", lambda **values: SimpleNamespace(type="tool", **values))

    def configure(handler=default_turn, *, enabled=False, verify=None):
        model = Model(handler)
        rag = SimpleNamespace(
            enabled=enabled,
            prepare_review_generation=AsyncMock(),
            query_review_graph=AsyncMock(),
            get_review_file_content=AsyncMock(),
        )

        async def retain(**kwargs):
            return VerificationResult(issues=list(kwargs["findings"]))

        verifier = SimpleNamespace(verify=AsyncMock(side_effect=verify or retain))
        monkeypatch.setattr(review_service.LLMFactory, "create_llm", lambda *args, **kwargs: model)
        monkeypatch.setattr(review_service, "ReviewVerifier", lambda **kwargs: verifier)
        return ReviewService(rag_client=rag), model, rag, verifier
    return configure


def ready_graph(rag, req, units=(), relations=()):
    rag.prepare_review_generation.return_value = {
        "status": "ready", "source_revision": req.currentCommitHash or req.commitHash,
        "collection_target": "bound-review-collection", "generation_manifest_sha256": "receipt",
    }

    async def query(**kwargs):
        if kwargs["pattern"] == "file_summary":
            values = [value for value in units if value["path"] == kwargs["target"]]
        else:
            values = [value for value in relations
                      if kwargs["target"] in {value["sourceUnit"]["unitId"], value["targetUnit"]["unitId"]}]
        return {"status": "ready", "results": values, "nextCursor": None}

    rag.query_review_graph.side_effect = query


@pytest.mark.asyncio
async def test_every_changed_hunk_is_owned_once_and_never_clipped(pipeline):
    large_source = "necessary_source_" * 8000
    raw = change("a.py", after=large_source) + "@@ -20 +20 @@\n-before\n+after\n" + change("b.py")
    service, model, _, verifier = pipeline()

    result = (await service.process_review_request(request(rawDiff=raw)))["result"]

    parts, _ = _parts(raw)
    owned = [part for payload, _ in model.calls if "ownedParts" in payload for part in payload["ownedParts"]]
    assert {part["id"] for part in owned} == {part.id for part in parts}
    assert len(owned) == len(parts) == 3
    assert {part["id"]: part["diff"] for part in owned} == {part.id: part.diff for part in parts}
    assert large_source in owned[0]["diff"]
    assert len([payload for payload, _ in model.calls if "ownedParts" in payload]) == 2
    assert len(model.calls) == 2  # Independent files do not pay for another whole-PR pass.
    assert all("max_tokens" not in options and "max_output_tokens" not in options for _, options in model.calls)
    assert result["status"] == "complete"
    assert set(result["reviewedHunkIds"]) == {part.id for part in parts}
    verifier.verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_migrated_caller_and_callee_are_reviewed_together(pipeline, tmp_path):
    def handler(payload):
        assert {part["path"] for part in payload["ownedParts"]} == {"caller.py", "callee.py"}
        assert "MIGRATED_CALLER" in json.dumps(payload)
        assert "NEW_CONTRACT" in json.dumps(payload)
        return default_turn(payload)

    service, model, rag, verifier = pipeline(handler, enabled=True)
    req = request(rawDiff=change("caller.py", after="MIGRATED_CALLER") + change("callee.py", after="NEW_CONTRACT"),
                  localRepoPath=str(tmp_path / "target"), localReviewOverlayPath=str(tmp_path / "overlay"))
    units = [{"unitId": path, "path": path, "startLine": 1, "endLine": 1} for path in ("caller.py", "callee.py")]
    ready_graph(rag, req, units, [{"kind": "CALLS", "sourceUnit": units[0], "targetUnit": units[1]}])

    result = (await service.process_review_request(req))["result"]

    assert result["status"] == "complete"
    assert len(model.calls) == 1
    assert len(model.calls[0][0]["graphRelations"]) == 1
    verifier.verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_graph_preparation_failure_continues_and_preserves_candidate(pipeline, tmp_path):
    def handler(payload):
        turn = default_turn(payload)
        turn["findings"] = [candidate(payload["ownedParts"][0])]
        return turn

    service, model, rag, verifier = pipeline(handler, enabled=True)
    rag.prepare_review_generation.side_effect = OSError("graph temporarily down")
    events = []
    req = request(localRepoPath=str(tmp_path / "target"), localReviewOverlayPath=str(tmp_path / "overlay"))

    result = (await service.process_review_request(req, events.append))["result"]

    assert len(model.calls) == 1
    assert len(result["issues"]) == 1
    assert result["issues"][0]["codeSnippet"] == "new()"
    assert any("graph temporarily down" in message for message in result["diagnostics"])
    assert any(event["state"] == "review_diagnostic" for event in events)
    assert verifier.verify.await_count == 1
    rag.query_review_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_synthesis_failure_is_diagnostic_without_inventing_verifier_work(pipeline, tmp_path):
    def handler(payload):
        if "ownedParts" not in payload:
            return "invalid model JSON"
        turn = default_turn(payload)
        if payload["ownedParts"][0]["path"] == "a.py":
            turn["findings"] = [candidate(payload["ownedParts"][0])]
        return turn

    service, model, rag, verifier = pipeline(handler, enabled=True)
    req = request(rawDiff=change("a.py") + change("b.py"),
                  localRepoPath=str(tmp_path / "target"), localReviewOverlayPath=str(tmp_path / "overlay"))
    units = [{"unitId": path, "path": path, "startLine": 1, "endLine": 1} for path in ("a.py", "b.py")]
    common = {"unitId": "common", "path": "common.py", "startLine": 1, "endLine": 2}
    ready_graph(rag, req, units, [{"kind": "CALLS", "sourceUnit": item, "targetUnit": common} for item in units])

    result = (await service.process_review_request(req))["result"]

    assert len(model.calls) == 3
    assert result["status"] == "complete"
    assert len(result["issues"]) == 1
    assert any("Cross-file synthesis unavailable" in message for message in result["diagnostics"])
    assert verifier.verify.await_args.kwargs["investigations"] == []


@pytest.mark.asyncio
async def test_omitted_hunk_becomes_verifier_investigation_and_can_be_resolved(pipeline):
    def handler(payload):
        turn = default_turn(payload)
        turn["reviewedHunkIds"] = []
        turn["unresolvedReason"] = "The changed caller contract was unavailable in discovery source"
        return turn

    async def resolve(**kwargs):
        return VerificationResult(issues=[], resolved_investigation_ids={item["id"] for item in kwargs["investigations"]})

    service, _, _, verifier = pipeline(handler, verify=resolve)

    result = (await service.process_review_request(request()))["result"]

    investigation = verifier.verify.await_args.kwargs["investigations"][0]
    assert investigation["id"].startswith("hunk:")
    assert result["reviewedHunkIds"] == investigation["partIds"]
    assert not result["unresolvedScopes"]
    assert result["status"] == "complete"


@pytest.mark.asyncio
async def test_failed_discovery_does_not_cancel_other_batch_findings(pipeline):
    def handler(payload):
        if "ownedParts" in payload and payload["ownedParts"][0]["path"] == "broken.py":
            raise OSError("discovery provider timeout")
        turn = default_turn(payload)
        if "ownedParts" in payload:
            turn["findings"] = [candidate(payload["ownedParts"][0])]
        return turn

    service, _, _, verifier = pipeline(handler)

    result = (await service.process_review_request(request(rawDiff=change("broken.py") + change("good.py"))))["result"]

    assert result["status"] == "partial"
    assert [issue["file"] for issue in result["issues"]] == ["good.py"]
    assert any("discovery provider timeout" in message for message in result["diagnostics"])
    assert any(item["paths"] == ["broken.py"] for item in verifier.verify.await_args.kwargs["investigations"])


@pytest.mark.asyncio
async def test_verifier_failure_retains_candidates_with_explicit_partial_result(pipeline):
    def handler(payload):
        turn = default_turn(payload)
        turn["findings"] = [candidate(payload["ownedParts"][0])]
        return turn

    async def fail(**kwargs):
        raise OSError("verifier unavailable")

    service, _, _, _ = pipeline(handler, verify=fail)

    result = (await service.process_review_request(request()))["result"]

    assert result["status"] == "partial"
    assert len(result["issues"]) == 1
    assert "verifier unavailable" in result["unresolvedScopes"]["verification"]


@pytest.mark.asyncio
async def test_same_title_and_line_preserve_independent_failure_mechanisms(pipeline):
    reasons = ["Empty input triggers division by zero", "Oversized input overflows the output buffer"]

    def handler(payload):
        turn = default_turn(payload)
        turn["findings"] = [candidate(payload["ownedParts"][0], reason) for reason in reasons]
        turn["findings"].append(dict(turn["findings"][0]))
        return turn

    service, _, _, verifier = pipeline(handler)

    result = (await service.process_review_request(request()))["result"]

    assert len(result["issues"]) == 2
    assert {issue["reason"] for issue in result["issues"]} == set(reasons)
    assert all("partId" in issue for issue in verifier.verify.await_args.kwargs["findings"])
    assert all("partId" not in issue and "batchIds" not in issue for issue in result["issues"])
    assert all(issue["scope"] == "LINE" and issue["codeSnippet"] == "new()" for issue in result["issues"])


@pytest.mark.asyncio
async def test_verifier_discovered_issue_keeps_part_identity_until_public_normalization(pipeline):
    def handler(payload):
        turn = default_turn(payload)
        turn["summary"]["unresolvedQuestions"] = [{"question": "Does the caller satisfy this new contract?"}]
        return turn

    async def discover(**kwargs):
        part = kwargs["parts"][0]
        issue = candidate({"id": part.id, "path": part.path, "anchorLines": list(part.anchors)})
        return VerificationResult(issues=[issue], resolved_investigation_ids={item["id"] for item in kwargs["investigations"]})

    service, _, _, _ = pipeline(handler, verify=discover)

    result = (await service.process_review_request(request()))["result"]

    assert result["status"] == "complete"
    assert len(result["issues"]) == 1
    assert result["issues"][0]["file"] == "service.py"
    assert "partId" not in result["issues"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["github", "gitlab", "bitbucket_cloud"])
@pytest.mark.parametrize("trigger", ["pr", "branch", "manual", "comment"])
async def test_provider_neutral_dto_shapes_continue_without_full_snapshot_metadata(pipeline, provider, trigger):
    service, model, rag, _ = pipeline()
    values = {"projectVcsWorkspace": provider, "currentCommitHash": "ab12cd3",
              "targetHeadCommitHash": None, "localRepoRevision": None, "sourceBranchName": None}
    if trigger in {"branch", "manual"}:
        values["pullRequestId"] = None
    if trigger == "manual":
        values.update(targetBranchName=None, currentCommitHash=None, commitHash=None)
    if trigger == "comment":
        values["taskContext"] = {"command": "/codecrow review", "source": "comment"}

    result = (await service.process_review_request(request(**values)))["result"]

    assert result["status"] == "complete"
    assert len(result["reviewedHunkIds"]) == 1
    assert len(model.calls) == 1
    assert result["diagnostics"]
    rag.prepare_review_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_incremental_review_uses_complete_delta_worklist(pipeline):
    service, model, _, _ = pipeline()
    delta = change("incremental.py", after="latest_change()")

    result = (await service.process_review_request(request(rawDiff=change("previous.py"), deltaDiff=delta, analysisMode="INCREMENTAL")))["result"]

    owned = model.calls[0][0]["ownedParts"]
    assert [part["path"] for part in owned] == ["incremental.py"]
    assert owned[0]["diff"] == _parts(delta)[0][0].diff
    assert result["status"] == "complete"


@pytest.mark.asyncio
async def test_unparsed_binary_change_remains_observably_unresolved(pipeline):
    service, _, _, _ = pipeline()
    binary = "diff --git a/image.png b/image.png\nBinary files a/image.png and b/image.png differ\n"

    result = (await service.process_review_request(request(rawDiff=change() + binary)))["result"]

    assert result["status"] == "partial"
    assert "path:image.png" in result["unresolvedScopes"]
    assert len(result["reviewedHunkIds"]) == 1


@pytest.mark.asyncio
async def test_independent_changes_do_not_invoke_synthesis_or_verifier(pipeline):
    def handler(payload):
        assert "ownedParts" in payload
        turn = default_turn(payload)
        turn.pop("reviewedHunkIds")
        return turn

    service, model, _, verifier = pipeline(handler)

    result = (await service.process_review_request(request(rawDiff=change("a.py") + change("b.py"))))["result"]

    assert result["status"] == "complete"
    assert len(model.calls) == 2
    assert len(result["reviewedHunkIds"]) == 2
    verifier.verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_summary_is_diagnostic_without_a_second_code_review(pipeline):
    def handler(payload):
        turn = default_turn(payload)
        turn["summary"] = {"notes": "Looks fine"}
        return turn

    service, model, _, verifier = pipeline(handler)

    result = (await service.process_review_request(request()))["result"]

    assert result["status"] == "complete"
    assert any("summary unavailable" in item for item in result["diagnostics"])
    assert len(model.calls) == 1
    verifier.verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_verifier_semantic_dedup_integrates_with_public_issue_schema(pipeline, monkeypatch):
    from service.review.verifier import ReviewVerifier

    def handler(payload):
        if "ownedParts" in payload:
            turn = default_turn(payload)
            part = payload["ownedParts"][0]
            turn["findings"] = [
                candidate(part, "Empty input divides by zero", title="Empty input fails"),
                candidate(part, "The zero-length denominator raises on empty input", title="Division by zero"),
            ]
            return turn
        if "issues" in payload:
            # Reconciliation receives all source-verified records, including a
            # verifier's expanded restatement, and no source/tool corpus.
            assert set(payload) == {"issues"}
            assert len(payload["issues"]) == 3
            assert {issue["line"] for issue in payload["issues"]} == {1}
            return {"groups": [{"memberIds": [issue["issueId"] for issue in payload["issues"]],
                                "representativeId": payload["issues"][-1]["issueId"],
                                "rationale": "Same empty-input trigger, zero denominator and practical repair"}]}
        first, second = payload["candidates"]
        evidence = observed_evidence(payload, "diff")
        assert evidence["id"] == f'diff:{first["partId"]}'
        assert "total / len(items)" in evidence["result"]["diff"]
        return {"decisions": [
            {"candidateId": item["candidateId"], "verdict": "keep", "reason": "The changed expression divides by the input length",
             "evidenceIds": [evidence["id"]]} for item in (first, second)
        ], "findings": [{
            "partId": first["partId"], "file": first["file"], "line": first["line"],
            "title": "Empty collections raise during average calculation",
            "reason": "An empty collection gives a zero denominator, so this average raises ZeroDivisionError",
            "suggestedFixDescription": "Handle an empty collection before dividing",
            "evidenceIds": [evidence["id"]],
        }]}

    service, model, rag, _ = pipeline(handler)
    monkeypatch.setattr(review_service, "ReviewVerifier", ReviewVerifier)

    result = (await service.process_review_request(request(rawDiff=change(after="total / len(items)"))))["result"]

    assert result["status"] == "complete"
    assert len(result["issues"]) == 1
    assert result["issues"][0]["title"] == "Empty collections raise during average calculation"
    assert result["issues"][0]["codeSnippet"] == "total / len(items)"
    assert "partId" not in result["issues"][0]
    assert len(model.calls) == 3  # Discovery, source-seeded verification, semantic partition.
    assert "caseId" in model.calls[1][0]
    assert set(model.calls[2][0]) == {"issues"}
    assert model.tool_schemas
    rag.query_review_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_local_source_guard_dismisses_batch_candidate(pipeline, monkeypatch, tmp_path):
    from service.review.verifier import ReviewVerifier

    target, overlay = tmp_path / "target", tmp_path / "overlay"
    target.mkdir()
    (overlay / "files").mkdir(parents=True)
    proposed = "def mean(items):\n    if not items:\n        return 0\n    return sum(items) / len(items)\n"
    (target / "service.py").write_text(proposed.replace("sum(items) / len(items)", "sum(items)"))
    (overlay / "files" / "service.py").write_text(proposed)
    (overlay / "manifest.json").write_text(json.dumps({"changedFiles": ["service.py"], "deletedFiles": []}))
    raw = change(before="    return sum(items)", after="    return sum(items) / len(items)").replace("@@ -1 +1 @@", "@@ -4 +4 @@")

    def handler(payload):
        if "ownedParts" in payload:
            turn = default_turn(payload)
            turn["findings"] = [candidate(payload["ownedParts"][0], "Empty input divides by zero")]
            return turn
        source = observed_evidence(payload, "readReviewFile")
        if source is None:
            return {"toolCalls": [{"name": "readReviewFile", "arguments": {"path": "service.py"}}]}
        assert source["result"]["content"] == proposed
        assert source["result"]["origin"] == "review_overlay"
        return {"decisions": [{"candidateId": payload["candidates"][0]["candidateId"],
                               "verdict": "dismiss", "reason": "The empty-input guard returns before division",
                               "evidenceIds": [source["id"]]}]}

    service, model, rag, _ = pipeline(handler)
    monkeypatch.setattr(review_service, "ReviewVerifier", ReviewVerifier)
    req = request(rawDiff=raw, localRepoPath=str(target), localReviewOverlayPath=str(overlay))

    result = (await service.process_review_request(req))["result"]

    assert result["status"] == "complete"
    assert result["issues"] == []
    assert len(model.calls) == 3
    assert model.calls[1][0]["candidates"][0]["line"] == 4
    assert all("workspace" not in tool["function"]["parameters"].get("properties", {}) for tool in model.tool_schemas)
    assert "tools" not in model.calls[1][0]
    rag.query_review_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_incremental_prior_change_is_tool_evidence_but_not_publication_worklist(pipeline, monkeypatch):
    from service.review.verifier import ReviewVerifier

    previous = change("contract.py", after="UPSTREAM_GUARD_RETURNS_EARLY")
    delta = change("consumer.py", after="consume_validated_value()")
    previous_part = _parts(previous)[0][0]
    delta_part = _parts(delta)[0][0]

    def handler(payload):
        if "ownedParts" in payload:
            assert [item["path"] for item in payload["ownedParts"]] == ["consumer.py"]
            assert "UPSTREAM_GUARD_RETURNS_EARLY" not in json.dumps(payload)
            turn = default_turn(payload)
            turn["findings"] = [candidate(payload["ownedParts"][0], "An invalid value reaches the consumer")]
            return turn
        assert {part_id for item in payload["changedFiles"] for part_id in item["partIds"]} == {delta_part.id}
        assert {item["id"] for item in payload["contextChangedParts"]} == {previous_part.id}
        source = observed_evidence(payload, "getReviewDiff")
        if source is None:
            return {"toolCalls": [{"name": "getReviewDiff", "arguments": {"partIds": [previous_part.id, delta_part.id]}}]}
        assert source["result"]["parts"][0]["diff"] == previous_part.diff
        return {
            "decisions": [{"candidateId": payload["candidates"][0]["candidateId"], "verdict": "dismiss",
                           "reason": "The earlier PR change rejects invalid values before this consumer",
                           "evidenceIds": [source["id"]]}],
            "findings": [{"partId": previous_part.id, "file": previous_part.path, "line": 1,
                          "title": "Out-of-delta suggestion", "reason": "This would reopen previously reviewed work",
                          "evidenceIds": [source["id"]]}],
        }

    service, model, _, _ = pipeline(handler)
    monkeypatch.setattr(review_service, "ReviewVerifier", ReviewVerifier)
    req = request(rawDiff=previous + delta, deltaDiff=delta, analysisMode="INCREMENTAL")

    result = (await service.process_review_request(req))["result"]

    assert result["status"] == "complete"
    assert result["issues"] == []
    assert result["reviewedHunkIds"] == [delta_part.id]
    assert len(model.calls) == 3


@pytest.mark.asyncio
async def test_incomplete_summary_preserves_specific_investigation(pipeline):
    def handler(payload):
        turn = default_turn(payload)
        turn["summary"] = {"unresolvedQuestions": [{
            "question": "Does the upstream guard reject an empty sequence before this division?",
            "paths": ["caller.py"],
        }]}
        return turn

    service, _, _, verifier = pipeline(handler)
    result = (await service.process_review_request(request()))["result"]

    questions = verifier.verify.await_args.kwargs["investigations"]
    assert any("upstream guard" in item["question"] for item in questions)
    assert result["status"] == "partial"


@pytest.mark.asyncio
async def test_recovered_verification_warning_does_not_leave_review_partial(pipeline):
    def handler(payload):
        turn = default_turn(payload)
        turn["findings"] = [candidate(payload["ownedParts"][0])]
        return turn

    async def recovered(**kwargs):
        return VerificationResult(issues=kwargs["findings"], warnings=["Earlier source uncertainty resolved by final verification"])

    service, _, _, _ = pipeline(handler, verify=recovered)
    result = (await service.process_review_request(request()))["result"]

    assert result["status"] == "complete"
    assert not result["unresolvedScopes"]
    assert any("resolved by final verification" in message for message in result["diagnostics"])


@pytest.mark.asyncio
async def test_boundary_context_preserves_companion_hunks_and_excludes_unrelated_summaries(pipeline, tmp_path):
    marker = "NECESSARY_CONTRACT_" * 8000

    def handler(payload):
        if "ownedParts" in payload:
            if any(part["path"] == "c.py" for part in payload["ownedParts"]):
                companion = payload["companionParts"]
                assert [item["path"] for item in companion] == ["b.py"]
                assert marker in companion[0]["diff"]
                assert all(part["path"] != "a.py" for part in companion)
            return default_turn(payload)
        assert {path for summary in payload["batchSummaries"] for path in summary["paths"]} == {"a.py", "b.py", "c.py"}
        assert {part["path"] for part in payload["changedAnchors"]} == {"a.py", "b.py", "c.py"}
        return default_turn(payload)

    service, model, rag, verifier = pipeline(handler, enabled=True)
    req = request(rawDiff=change("a.py") + change("b.py", after=marker) + change("c.py") + change("unrelated.py"),
                  localRepoPath=str(tmp_path / "target"), localReviewOverlayPath=str(tmp_path / "overlay"))
    units = [{"unitId": path, "path": path, "startLine": 1, "endLine": 1} for path in ("a.py", "b.py", "c.py", "unrelated.py")]
    ready_graph(rag, req, units, [{"kind": "CALLS", "sourceUnit": units[left], "targetUnit": units[right]} for left, right in ((0, 1), (1, 2))])

    result = (await service.process_review_request(req))["result"]

    assert result["status"] == "complete"
    assert len(model.calls) == 4  # Three discovery groups and the remaining contract boundary.
    verifier.verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_summary_only_claims_need_source_investigation_and_never_become_public_findings(pipeline, tmp_path):
    def handler(payload):
        if "ownedParts" in payload:
            return default_turn(payload)
        anchor = payload["changedAnchors"][0]
        return {"findings": [{"partId": anchor["partId"], "file": anchor["path"], "line": 1,
                              "title": "Summary suggests broken caller", "reason": "A caller might use the old signature"}],
                "investigations": []}

    service, model, rag, verifier = pipeline(handler, enabled=True)
    req = request(rawDiff=change("a.py") + change("b.py"),
                  localRepoPath=str(tmp_path / "target"), localReviewOverlayPath=str(tmp_path / "overlay"))
    units = [{"unitId": path, "path": path, "startLine": 1, "endLine": 1} for path in ("a.py", "b.py")]
    common = {"unitId": "common", "path": "common.py", "startLine": 1, "endLine": 2}
    ready_graph(rag, req, units, [{"kind": "CALLS", "sourceUnit": item, "targetUnit": common} for item in units])

    result = (await service.process_review_request(req))["result"]

    assert result["issues"] == []
    assert verifier.verify.await_args.kwargs["findings"] == []
    question = verifier.verify.await_args.kwargs["investigations"][0]
    assert question["claim"] == "A caller might use the old signature"
    assert question["evidenceNeeded"].startswith("Exact source")
    assert question["partIds"]
    assert len(model.calls) == 3


@pytest.mark.asyncio
async def test_discovery_preserves_causal_provenance_for_final_verification(pipeline):
    def handler(payload):
        turn = default_turn(payload)
        owned = payload["ownedParts"][0]
        turn["findings"] = [candidate(owned, trigger="empty input", failureMechanism="division by zero",
                                     causalEvidence=[{"partId": owned["id"], "path": owned["path"],
                                                      "startLine": 1, "endLine": 1,
                                                      "observation": "Changed expression divides by input length"}])]
        return turn

    service, _, _, verifier = pipeline(handler)
    await service.process_review_request(request())
    finding = verifier.verify.await_args.kwargs["findings"][0]
    assert finding["trigger"] == "empty input"
    assert finding["failureMechanism"] == "division by zero"
    assert finding["causalEvidence"][0]["observation"] == "Changed expression divides by input length"


@pytest.mark.asyncio
async def test_graph_failure_routes_concrete_contract_question_to_local_source(pipeline, tmp_path):
    from service.review.local_source import LocalReviewSource

    target, overlay = tmp_path / "target", tmp_path / "overlay"
    target.mkdir()
    (overlay / "files").mkdir(parents=True)
    source = "def caller(value):\n    return callee(value=value)\n"
    (overlay / "files" / "caller.py").write_text(source)
    (overlay / "manifest.json").write_text(json.dumps({"changedFiles": ["caller.py"], "deletedFiles": []}))

    def handler(payload):
        assert "ownedParts" in payload
        turn = default_turn(payload)
        owned = payload["ownedParts"][0]
        turn["summary"]["unresolvedQuestions"] = [{
            "question": "Was caller migrated to the new keyword-only callee contract?",
            "claim": "An unmigrated call would fail with TypeError",
            "evidenceNeeded": "The proposed caller's invocation arguments",
            "partIds": [owned["id"]], "paths": ["caller.py"],
        }]
        return turn

    async def resolve(**kwargs):
        assert kwargs["cross_batch_scopes"] == []
        assert kwargs["findings"] == []
        question = kwargs["investigations"][0]
        exact = LocalReviewSource(kwargs["binding"]).read("caller.py")
        assert exact["status"] == "ready" and exact["content"] == source
        assert question["evidenceNeeded"] == "The proposed caller's invocation arguments"
        return VerificationResult(issues=[], resolved_investigation_ids={question["id"]})

    service, model, rag, verifier = pipeline(handler, enabled=True, verify=resolve)
    rag.prepare_review_generation.side_effect = OSError("graph unavailable")
    req = request(rawDiff=change("caller.py", after="    return callee(value=value)"),
                  localRepoPath=str(target), localReviewOverlayPath=str(overlay))

    result = (await service.process_review_request(req))["result"]

    assert result["status"] == "complete"
    assert result["issues"] == []
    assert len(model.calls) == 1
    assert verifier.verify.await_count == 1
    rag.query_review_graph.assert_not_awaited()
    assert any("graph unavailable" in diagnostic for diagnostic in result["diagnostics"])


@pytest.mark.asyncio
async def test_real_verifier_keeps_unsupported_caller_hypothesis_internal(pipeline, monkeypatch):
    from service.review.verifier import ReviewVerifier

    def handler(payload):
        if "ownedParts" in payload:
            turn = default_turn(payload)
            turn["findings"] = [candidate(payload["ownedParts"][0], "A hypothetical external caller may use the old signature")]
            return turn
        evidence = observed_evidence(payload, "diff")
        assert evidence["result"]["diff"] == _parts(change())[0][0].diff
        assert "external caller" not in evidence["result"]["diff"]
        return {"decisions": [{"candidateId": payload["candidates"][0]["candidateId"], "verdict": "uncertain",
                               "reason": "No actual affected caller or supported external contract establishes a failure"}]}

    service, model, _, _ = pipeline(handler)
    monkeypatch.setattr(review_service, "ReviewVerifier", ReviewVerifier)

    result = (await service.process_review_request(request()))["result"]

    assert result["status"] == "partial"
    assert result["issues"] == []
    assert len(model.calls) == 2
    assert any("unconfirmed hypothesis not published" in message for message in result["diagnostics"])
