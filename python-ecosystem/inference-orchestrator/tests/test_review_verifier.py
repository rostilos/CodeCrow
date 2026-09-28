"""Architecture/behavior checks; these do not measure review precision or recall."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.review.verification_tools import LocalReviewSource, VerificationTools
from service.review.verifier import ReviewVerifier


@pytest.fixture
def source_tree(tmp_path):
    base = tmp_path / "target"
    overlay = tmp_path / "overlay"
    base.mkdir()
    (overlay / "files").mkdir(parents=True)
    (base / "caller.py").write_text("def call():\n    return compute(1)\n")
    (base / "change.py").write_text("def compute(value):\n    return value\n")
    (base / "deleted.py").write_text("old_symbol = True\n")
    (base / "missing.py").write_text("stale_symbol = True\n")
    (overlay / "files" / "change.py").write_text("def compute(value):\n    return value / 0\n")
    (overlay / "files" / "caller.py").write_text("def call():\n    return compute(1)\n")
    (overlay / "manifest.json").write_text(json.dumps({
        "changedFiles": ["change.py", "caller.py", "deleted.py", "missing.py"],
        "deletedFiles": ["deleted.py"],
    }))
    return {"target_repo_path": str(base), "review_overlay_path": str(overlay)}


def part(path="change.py", part_id="part-1"):
    return SimpleNamespace(id=part_id, path=path, side="proposed", anchors={2: "    return value / 0"},
                           diff="@@ -1,2 +1,2 @@\n def compute(value):\n-    return value\n+    return value / 0")


def finding(path="change.py", part_id="part-1", title="Division by zero"):
    return {"partId": part_id, "file": path, "line": 2, "title": title,
            "reason": "The changed return divides the supplied value by zero.", "severity": "HIGH"}


def response(**value):
    return SimpleNamespace(content=json.dumps(value))


def request():
    return SimpleNamespace(aiProvider="openai", pullRequestId="42", projectRules=None, taskContext=None)


def test_local_reads_preserve_proposed_target_and_missing_overlay_semantics(source_tree):
    source = LocalReviewSource(source_tree)
    assert "value / 0" in source.read("change.py")["content"]
    assert "value / 0" not in source.read("change.py", side="target")["content"]
    assert source.read("deleted.py")["status"] == "deleted"
    assert source.read("missing.py")["status"] == "unavailable"
    assert "stale_symbol" in source.read("missing.py", side="target")["content"]
    assert source.read("caller.py", start_line=2, end_line=2)["content"] == "    return compute(1)\n"

def test_local_reads_reject_traversal_and_symlinks(source_tree, tmp_path):
    source = LocalReviewSource(source_tree)
    secret = tmp_path / "other-tenant.txt"
    secret.write_text("tenant-private")
    target = tmp_path / "target"
    (target / "escape.py").symlink_to(secret)
    (target / "inside.py").symlink_to(target / "caller.py")
    for path in ("../other-tenant.txt", str(secret), "escape.py", "inside.py", ".git/config"):
        result = source.read(path)
        assert result["status"] == "unavailable"
        assert "tenant-private" not in str(result)

def test_grep_does_not_use_stale_target_and_reports_incomplete_search(source_tree):
    source = LocalReviewSource(source_tree)
    result = source.grep("stale_symbol")
    assert result["results"] == []
    assert result["status"] == "partial"
    assert result["unavailablePaths"] == ["missing.py"]
    assert source.grep("compute", paths=["caller.py"])["results"] == [{"path": "caller.py", "matches": [{"line": 2, "text": "    return compute(1)"}]}]
    assert source.grep("old_symbol", side="target")["results"] == [{"path": "deleted.py", "matches": [{"line": 1, "text": "old_symbol = True"}]}]

def test_source_reads_do_not_clip_large_semantic_unit(source_tree, tmp_path):
    long_line = "    marker = '" + "x" * 50000 + "'\n"
    (tmp_path / "overlay" / "files" / "change.py").write_text("def compute(value):\n" + long_line)
    result = LocalReviewSource(source_tree).read("change.py")
    assert result["content"].endswith(long_line)
    assert len(result["content"]) > 50000

def test_malformed_overlay_does_not_become_proposed_target_content(source_tree, tmp_path):
    (tmp_path / "overlay" / "manifest.json").write_text("broken")
    source = LocalReviewSource(source_tree, ["change.py"])
    assert source.read("change.py")["status"] == "unavailable"
    assert source.read("caller.py")["status"] == "unavailable"
    assert source.read("caller.py", side="target")["status"] == "ready"

def test_missing_target_and_partially_invalid_manifest_never_prove_absence(source_tree, tmp_path):
    source = LocalReviewSource({**source_tree, "target_repo_path": str(tmp_path / "gone")})
    assert source.grep("nonexistent")["complete"] is False
    (tmp_path / "overlay" / "manifest.json").write_text(json.dumps({
        "changedFiles": ["deleted.py"], "deletedFiles": ["deleted.py", "../private"],
    }))
    source = LocalReviewSource(source_tree)
    assert source.read("deleted.py")["status"] == "unavailable"

@pytest.mark.asyncio
async def test_request_bound_mcp_exposes_only_read_tools_and_uses_host_graph_identity(source_tree):
    rag = SimpleNamespace(query_review_graph=AsyncMock(return_value={"status": "ready", "results": []}))
    binding = {**source_tree, "workspace": "tenant-a", "project": "p", "review_collection_target": "sealed"}
    tools = VerificationTools(rag_client=rag, binding=binding, parts=[part()])
    schemas = await tools.schemas()
    assert {schema["name"] for schema in schemas} == {"queryCodeGraph", "getStructuralUnit", "traverseCodeGraph", "getImpactRadius", "getMinimalReviewContext", "readReviewFile", "grepReviewCode", "getReviewDiff", "findReviewFiles"}
    assert all("workspace" not in schema["inputSchema"]["properties"] for schema in schemas)
    result = await tools.call("readReviewFile", {"path": "change.py"})
    assert result["status"] == "ready"
    assert "value / 0" in result["content"]
    assert await tools.call("readReviewFile", {"path": "../private"}) == await tools.call("readReviewFile", {"path": "../private"})
    await tools.call("queryCodeGraph", {"pattern": "callers_of", "target": "compute"})
    assert rag.query_review_graph.call_args.kwargs["workspace"] == "tenant-a"
    assert rag.query_review_graph.call_args.kwargs["include_source"] is False
    assert (await tools.call("deleteFile", {"path": "change.py"}))["status"] == "unavailable"


def test_duplicate_cycles_and_dismissed_representatives_do_not_publish_hypotheses():
    candidates = {"a": finding(), "b": finding(title="different title")}
    for decisions in ({"a": {"verdict": "duplicate", "duplicateOf": "b"}, "b": {"verdict": "duplicate", "duplicateOf": "a"}},
                      {"a": {"verdict": "duplicate", "duplicateOf": "b"}, "b": {"verdict": "dismiss"}}):
        diagnostics = []
        result = ReviewVerifier._apply_decisions(candidates, decisions, diagnostics)
        assert result == {}
        if decisions["b"]["verdict"] == "duplicate":
            assert diagnostics


def test_malformed_changed_path_metadata_does_not_disable_valid_source_reads(source_tree):
    source = LocalReviewSource(source_tree, ["../invalid.py", "change.py"])
    assert source.metadata_diagnostics
    assert source.read("change.py")["status"] == "ready"



def test_duplicate_chain_keeps_one_live_representative():
    candidates = {key: finding(title=key) for key in ("a", "b", "c", "unrelated")}
    diagnostics = []
    result = ReviewVerifier._apply_decisions(candidates, {
        "a": {"verdict": "duplicate", "duplicateOf": "b"},
        "b": {"verdict": "duplicate", "duplicateOf": "c"},
        "c": {"verdict": "keep"},
        "unrelated": {"verdict": "keep"},
    }, diagnostics)
    assert set(result) == {"c", "unrelated"}
    assert not diagnostics


def test_local_reads_keep_directory_binding_when_parent_is_replaced(source_tree, tmp_path, monkeypatch):
    import os
    import service.review.local_source as local_source
    root = tmp_path / "target"
    folder = root / "nested"
    folder.mkdir()
    (folder / "data.py").write_text("bound_source = True\n")
    external = tmp_path / "other-company"
    external.mkdir()
    (external / "data.py").write_text("tenant_private = True\n")
    source = LocalReviewSource(source_tree)
    original_open = os.open
    replaced = False

    def replacing_open(path, flags, *args, **kwargs):
        nonlocal replaced
        if path == "data.py" and not replaced:
            replaced = True
            folder.rename(root / "original")
            folder.symlink_to(external, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(local_source.os, "open", replacing_open)
    result = source.read("nested/data.py", side="target")
    assert replaced
    assert result["status"] == "ready"
    assert result["content"] == "bound_source = True\n"
    assert source.grep("tenant_private", side="target")["results"] == []

def test_local_read_of_fifo_does_not_block(source_tree, tmp_path):
    import os
    os.mkfifo(tmp_path / "target" / "pipe.py")
    assert LocalReviewSource(source_tree).read("pipe.py", side="target")["status"] == "unavailable"

def test_local_grep_wide_repository_uses_only_ancestor_directory_descriptors(
    source_tree, tmp_path, monkeypatch,
):
    import errno
    import os
    from service.review import local_source

    target = tmp_path / "target"
    for index in range(40):
        folder = target / f"directory-{index:02d}"
        folder.mkdir()
        (folder / "value.py").write_text("wide_repository_marker = True\n")
    source = LocalReviewSource(source_tree)
    original_open = os.open
    original_close = os.close
    directory_fds = set()
    high_water = 0

    def limited_open(path, flags, *args, **kwargs):
        nonlocal high_water
        is_directory = bool(flags & os.O_DIRECTORY)
        if is_directory and len(directory_fds) >= 4:
            raise OSError(errno.EMFILE, "test directory descriptor ceiling")
        descriptor = original_open(path, flags, *args, **kwargs)
        if is_directory:
            directory_fds.add(descriptor)
            high_water = max(high_water, len(directory_fds))
        return descriptor

    def tracked_close(descriptor):
        directory_fds.discard(descriptor)
        return original_close(descriptor)

    monkeypatch.setattr(local_source.os, "open", limited_open)
    monkeypatch.setattr(local_source.os, "close", tracked_close)
    result = source.grep("wide_repository_marker", side="target")
    assert result["status"] == "ready"
    assert result["complete"] is True
    assert len(result["results"]) == 40
    assert high_water <= 2
    assert directory_fds == set()


def prompt_at(llm, index):
    for message in llm.ainvoke.call_args_list[index].args[0]:
        if isinstance(message, tuple) and message[0] == "human":
            payload = json.loads(message[1])
            if "workItems" in payload:
                return payload
    raise AssertionError("review prompt missing")


def read_call(path):
    return {"name": "readReviewFile", "arguments": {"path": path}}


def verdict(candidate_id, value, evidence, reason="The changed return divides the supplied value by zero.", **extra):
    return {"candidateId": candidate_id, "verdict": value, "reason": reason, "evidenceIds": evidence, **extra}















def test_case_source_stays_available_for_later_related_decisions(source_tree):
    from service.review.verification_state import VerificationState
    state = VerificationState([finding(), finding(title="Related second failure")], [], {"part-1": part()})
    first, _ = state.add_evidence("readReviewFile", LocalReviewSource(source_tree).read("change.py"))
    second, _ = state.add_evidence("readReviewFile", LocalReviewSource(source_tree).read("caller.py"))
    state.begin_turn()
    state.record(decisions=[verdict("candidate-1", "keep", [first])])
    state.begin_turn()
    state.record(decisions=[verdict("candidate-2", "dismiss", [first, second])])
    assert state.complete
    assert set(state.evidence) == {first, second}
    assert state.decisions["candidate-2"]["evidenceIds"] == [first, second]


def test_future_guessed_source_ids_do_not_support_decisions(source_tree):
    from service.review.verification_state import VerificationState
    state = VerificationState([finding()], [], {"part-1": part()})
    key, _ = state.add_evidence("readReviewFile", LocalReviewSource(source_tree).read("change.py"))
    receipt = state.record(decisions=[verdict("candidate-1", "keep", [key])])
    assert receipt["rejected"]
    assert not state.decisions


def test_discovered_findings_join_same_session_dedup_catalog(source_tree):
    from service.review.verification_state import VerificationState
    state = VerificationState([], [{"id": "check", "question": "Check compute"}], {"part-1": part()})
    key, _ = state.add_evidence("readReviewFile", LocalReviewSource(source_tree).read("change.py"))
    state.visible.add(key)
    state.record(findings=[{**finding(), "evidenceIds": [key]}])
    assert state.decisions["candidate-1"]["verdict"] == "keep"
    state.record(findings=[{**finding(title="Same propagated failure"), "evidenceIds": [key], "duplicateOf": "candidate-1"}])
    assert len(ReviewVerifier._apply_decisions(state.candidates, state.decisions, [])) == 1
    assert not state.complete  # no invented coverage from finding a defect


def test_grep_distinguishes_complete_matching_declarations_without_body_dump(source_tree, tmp_path):
    source = "model BookingReference {\n  referenceData String\n}\nmodel Booking {\n  id Int @id\n}\nmodel BookingSeat {\n  bookingId Int\n}\n"
    (tmp_path / "target" / "schema.prisma").write_text(source)
    matches = LocalReviewSource(source_tree).grep("model Booking", paths=["schema.prisma"])
    assert matches["results"] == [{"path": "schema.prisma", "matches": [
        {"line": 1, "text": "model BookingReference {"},
        {"line": 4, "text": "model Booking {"},
        {"line": 7, "text": "model BookingSeat {"},
    ]}]
    assert "referenceData" not in str(matches)
    assert matches["complete"]








def assess(work_id="work-1", verdict="confirmed", evidence=("diff:part-1",), reason="The changed return divides the supplied value by zero.", **extra):
    return {"workId": work_id, "verdict": verdict, "reason": reason, "evidenceIds": list(evidence), **extra}


def step(*assessments, calls=(), work_ids=("work-1",), findings=()):
    pending = [key for key in work_ids if not any(item["workId"] == key for item in assessments)] if calls else []
    return response(assessments=[*assessments, *(assess(key, "needs_evidence", (), "Inspect the caller to establish compensation") for key in pending)],
                    evidenceRequests=[{"workIds": list(work_ids), "missingFact": "Does the caller compensate for the changed return?", "calls": list(calls)}] if calls else [],
                    findings=list(findings))


async def run_case(model, source_tree, *, findings, investigations=(), parts=None):
    """Exercise one controller case independently of semantic planning."""
    from service.review.verification_state import VerificationState
    parts = parts or [part()]
    state = VerificationState(findings, list(investigations), {item.id: item for item in parts})
    for item in parts:
        state.evidence[f"diff:{item.id}"] = {"kind": "diff", "result": {
            "status": "ready", "partId": item.id, "path": item.path, "side": item.side, "diff": item.diff,
        }}
    tools = VerificationTools(rag_client=None, binding=source_tree, parts=parts)
    return await ReviewVerifier(None)._verify_case(model, request(), state, tools, {"caseId": "test-case"}, [])


async def review(source_tree, responses, *, findings=None, investigations=None, single_case=False, **kwargs):
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=responses))
    findings = [finding()] if findings is None else findings
    if single_case:
        result = await run_case(llm, source_tree, findings=findings, investigations=investigations or [], **kwargs)
    else:
        result = await ReviewVerifier(None).verify(llm=llm, request=request(), findings=findings,
            summaries=kwargs.pop("summaries", []), parts=kwargs.pop("parts", [part()]), binding=source_tree,
            investigations=investigations or [], **kwargs)
    return result, llm


@pytest.mark.asyncio
async def test_source_question_reports_new_issue_using_same_work_id(source_tree):
    result, llm = await review(source_tree, [step(assess(issue=finding()))], findings=[], investigations=[
        {"id": "edge-1", "partIds": ["part-1"], "paths": ["change.py"], "question": "Does compute return normally?"}])
    assert len(result.issues) == 1
    assert result.issues[0]["partId"] == "part-1"
    assert result.resolved_investigation_ids == {"edge-1"}
    assert llm.ainvoke.await_count == 1
    assert prompt_at(llm, 0)["workItems"][0]["id"] == "work-1"


@pytest.mark.asyncio
async def test_graph_outage_falls_back_to_local_source_without_protocol_retry(source_tree):
    result, llm = await review(source_tree, [
        step(calls=[{"name": "queryCodeGraph", "arguments": {"pattern": "callers_of", "target": "compute"}}]),
        step(calls=[read_call("change.py")]), step(assess(evidence=["read-2"]))])
    assert result.issues == [finding()]
    assert not result.diagnostics
    assert any("graph" in warning.lower() for warning in result.warnings)
    assert llm.ainvoke.await_count == 3


@pytest.mark.asyncio
async def test_full_pr_context_is_readable_but_not_publishable_in_incremental_review(source_tree):
    result, llm = await review(source_tree, [
        step(calls=[{"name": "getReviewDiff", "arguments": {"partIds": ["historical-part"]}}]),
        step(assess(evidence=["read-1"], issue=finding("caller.py", "historical-part"))),
        step(),
    ], findings=[], context_parts=[part("caller.py", "historical-part")], investigations=[
        {"id": "history", "partIds": ["part-1"], "paths": ["change.py"], "question": "Does earlier work compensate?"}])
    assert result.issues == []
    assert result.resolved_investigation_ids == {"history"}
    assert prompt_at(llm, 1)["contextChangedParts"] == [{"id": "historical-part", "path": "caller.py"}]


@pytest.mark.asyncio
async def test_new_finding_needs_its_active_anchor_not_an_unrelated_caller(source_tree):
    result, _ = await review(source_tree, [step(calls=[read_call("caller.py")]),
        step(assess(evidence=["read-1"]), findings=[{**finding(), "evidenceIds": ["read-1"]}]), step()],
        findings=[], investigations=[{"id": "inspect", "partIds": ["part-1"], "question": "Check call contract"}])
    assert result.issues == []


@pytest.mark.asyncio
async def test_uncertain_caller_hypothesis_is_not_published(source_tree):
    result, llm = await review(source_tree, [step(assess(verdict="uncertain", evidence=[], reason="No concrete unmigrated caller was located"))])
    assert result.issues == []
    assert result.decisions[0]["verdict"] == "uncertain"
    assert llm.ainvoke.await_count == 1


@pytest.mark.asyncio
async def test_transport_failure_retains_only_undecided_discovery(source_tree):
    issues = [finding(title="Speculation"), finding()]
    result, _ = await review(source_tree, [
        response(groups=[{"caseIds": ["case-1"]}, {"caseIds": ["case-2"]}]),
        step(assess(verdict="uncertain", evidence=[], reason="No failing consumer")),
        RuntimeError("provider unavailable")], findings=issues)
    assert result.issues == [issues[1]]
    assert any("without verification" in message for message in result.diagnostics)


@pytest.mark.asyncio
async def test_final_dedup_follows_contract_planning_and_source_review(source_tree):
    result, llm = await review(source_tree, [
        response(groups=[{"caseIds": ["case-1"]}, {"caseIds": ["case-2"]}]),
        step(assess()), step(assess(evidence=["diff:part-2"])),
        response(groups=[{"memberIds": ["issue-1", "issue-2"], "representativeId": "issue-1", "rationale": "Same divisor and practical repair"}]),
    ], findings=[finding(), finding("caller.py", "part-2", "Caller crashes")], parts=[part(), part("caller.py", "part-2")])
    assert [item["file"] for item in result.issues] == ["change.py"]
    assert llm.ainvoke.await_count == 4
    assert not result.diagnostics


@pytest.mark.asyncio
async def test_complete_shared_source_survives_settled_sibling_and_context_rebuild(source_tree, tmp_path):
    marker = "shared_source_marker_" + "x" * 50000
    (tmp_path / "overlay" / "files" / "change.py").write_text("def compute(value):\n    return value / 0\n# " + marker + "\n")
    result, llm = await review(source_tree, [step(calls=[read_call("change.py")], work_ids=["work-1", "work-2"]),
        step(assess(evidence=["read-1"]), calls=[read_call("caller.py")], work_ids=["work-2"]),
        step(assess("work-2", "refuted", ["read-1", "read-2"], "The caller compensates for this second allegation")),
    ], findings=[finding(), finding(title="Caller compensation allegation")], single_case=True)
    assert result.issues == [finding()]
    assert not result.diagnostics
    for index in (1, 2):
        prompt = str(llm.ainvoke.call_args_list[index].args[0])
        assert prompt.count(marker) == 1
        assert "malformed_summary_" not in prompt


@pytest.mark.asyncio
async def test_existing_candidate_joins_supplied_anchor_to_caller_witness(source_tree):
    result, _ = await review(source_tree, [step(calls=[read_call("caller.py")]), step(assess(evidence=["read-1"]))])
    assert result.issues == [finding()]
    assert result.decisions[0]["evidenceIds"] == ["read-1", "diff:part-1"]


@pytest.mark.asyncio
async def test_source_compensation_dismisses_migrated_caller_claim(source_tree, tmp_path):
    (tmp_path / "overlay" / "files" / "caller.py").write_text("async def call():\n    return await compute(1)\n")
    (tmp_path / "overlay" / "files" / "change.py").write_text("async def compute(value):\n    return value\n")
    result, _ = await review(source_tree, [step(calls=[read_call("change.py"), read_call("caller.py")]),
        step(assess(verdict="refuted", evidence=["read-1", "read-2"], reason="The caller awaits the newly asynchronous function"))])
    assert result.issues == []
    assert not result.diagnostics


@pytest.mark.asyncio
async def test_requested_counterevidence_defers_same_step_confirmation(source_tree, monkeypatch):
    original = VerificationTools.call
    calls = []

    async def tracked(self, name, args):
        calls.append((name, args))
        return await original(self, name, args)

    monkeypatch.setattr(VerificationTools, "call", tracked)
    result, llm = await review(source_tree, [
        step(assess(), calls=[read_call("caller.py")]),
        step(assess(verdict="refuted", evidence=["read-1"], reason="The requested caller establishes compensation")),
    ])
    assert result.issues == []
    assert llm.ainvoke.await_count == 2
    assert calls == [("readReviewFile", {"path": "caller.py"})]
    work = prompt_at(llm, 1)["workItems"][0]
    assert "outcome" not in work
    assert work["pendingAssessment"]["verdict"] == "confirmed"


@pytest.mark.asyncio
async def test_direct_source_call_and_sibling_outcomes_are_processed_together(source_tree, monkeypatch):
    from service.review.review_step import STEP_TOOL
    model = SimpleNamespace(bind_tools=lambda schemas, **kw: model)
    model.ainvoke = AsyncMock(side_effect=[SimpleNamespace(content="", tool_calls=[
        {"id": "outcome", "name": STEP_TOOL, "args": {"assessments": [
            assess(), assess("work-2", "refuted", ["diff:part-1"], "Independent second claim is disproved"),
        ]}},
        {"id": "source", "name": "readReviewFile", "args": {
            "path": "caller.py", "workIds": ["work-1"], "missingFact": "Does the caller compensate?",
        }},
    ]), SimpleNamespace(content="", tool_calls=[
        {"id": "final", "name": STEP_TOOL, "args": {"assessments": [assess(evidence=["read-1"])]}},
    ])])
    original = VerificationTools.call
    execute = AsyncMock(side_effect=lambda name, args: None)

    async def tracked(self, name, args):
        await execute(name, args)
        return await original(self, name, args)

    monkeypatch.setattr(VerificationTools, "call", tracked)
    result = await run_case(model, source_tree, findings=[finding(), finding(title="Independent second claim")])
    assert result.issues == [finding()]
    execute.assert_awaited_once_with("readReviewFile", {"path": "caller.py"})
    assert model.ainvoke.await_count == 2
    work = prompt_at(model, 1)["workItems"]
    assert "outcome" not in work[0]
    assert work[1]["outcome"]["verdict"] == "refuted"


@pytest.mark.asyncio
async def test_pending_work_can_read_source_without_repeating_assessment(source_tree):
    result, llm = await review(source_tree, [
        response(evidenceRequests=[{"workIds": ["work-1"], "missingFact": "Read the concrete caller", "calls": [read_call("caller.py")]}]),
        step(assess(evidence=["read-1"])),
    ])
    assert result.issues == [finding()]
    assert llm.ainvoke.await_count == 2
    assert any(item["id"] == "read-1" for item in prompt_at(llm, 1)["evidence"])


@pytest.mark.asyncio
async def test_reopened_question_keeps_derived_report_pending_for_explicit_disposition(source_tree):
    result, llm = await review(source_tree, [
        step(assess(issue=finding()), calls=[read_call("caller.py")], work_ids=["work-2"]),
        step(assess(verdict="needs_evidence", evidence=[], reason="Recheck the implementation premise"),
             calls=[read_call("change.py")]),
        step(assess(verdict="refuted", evidence=["read-2"], reason="Rechecked source changes the answer"),
             assess("work-2", "confirmed", ["read-1"], "The requested caller question is answered")),
        step(assess("work-3", "refuted", ["read-2"], "The prior report is explicitly withdrawn using the rechecked source")),
    ], findings=[], single_case=True, investigations=[
        {"id": "implementation", "partIds": ["part-1"], "question": "Check the changed implementation"},
        {"id": "caller", "partIds": ["part-1"], "question": "Check the caller"},
    ])
    assert result.issues == []
    assert result.resolved_investigation_ids == {"implementation", "caller"}
    assert not result.diagnostics
    assert llm.ainvoke.await_count == 4
    pending = prompt_at(llm, 3)
    assert pending["reviewWork"]["pendingWorkIds"] == ["work-3"]
    assert "outcome" not in next(item for item in pending["workItems"] if item["id"] == "work-3")


@pytest.mark.asyncio
async def test_malformed_provider_prose_is_not_replayed_as_context(source_tree):
    prose = "record and conclusion FINAL " * 13000
    result, llm = await review(source_tree, [SimpleNamespace(content=prose, response_metadata={"finish_reason": "error"}),
        step(assess(verdict="uncertain", evidence=[], reason="External contract unavailable"))])
    assert result.issues == []
    assert prose not in str(llm.ainvoke.call_args_list[1])
    assert "@@" in str(llm.ainvoke.call_args_list[1])
    assert any("finish_reason=error" in warning for warning in result.warnings)
    assert not any("retained" in item for item in result.diagnostics)


@pytest.mark.asyncio
async def test_bad_id_repaired_without_source_acquisition(source_tree):
    result, llm = await review(source_tree, [step(assess("NEW-FINDING-PANEL")), step(assess())])
    assert result.issues == [finding()]
    assert "unknown workId" in str(prompt_at(llm, 1)["reviewWork"]["corrections"])
    assert llm.ainvoke.await_count == 2


@pytest.mark.asyncio
async def test_question_issue_format_repair_keeps_answer_and_requires_no_new_read(source_tree):
    result, llm = await review(source_tree, [step(assess(issue={"title": "Missing location"})), step(assess(issue=finding()))],
        findings=[], investigations=[{"id": "header", "partIds": ["part-1"], "question": "Does nesting preserve the layout?"}])
    assert len(result.issues) == 1
    assert result.resolved_investigation_ids == {"header"}
    assert llm.ainvoke.await_count == 2


@pytest.mark.asyncio
async def test_tool_requests_without_work_binding_are_rejected_without_execution(source_tree, monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(VerificationTools, "call", execute)
    result, llm = await review(source_tree, [response(toolCalls=[read_call("caller.py")]), step(assess())])
    assert result.issues == [finding()]
    execute.assert_not_awaited()
    assert "workIds" in str(prompt_at(llm, 1)["reviewWork"]["corrections"])


@pytest.mark.asyncio
async def test_repeated_source_gets_outcome_assessment_without_funding_more_reads(source_tree, monkeypatch):
    original = VerificationTools.call
    reads = []
    async def tracked(self, name, args):
        reads.append((name, args))
        return await original(self, name, args)
    monkeypatch.setattr(VerificationTools, "call", tracked)
    result, llm = await review(source_tree, [step(calls=[read_call("caller.py")]),
        step(calls=[read_call("caller.py")]), step(assess(evidence=["read-1"]))])
    assert result.issues == [finding()]
    assert llm.ainvoke.await_count == 3
    assert "different concrete source lead" in prompt_at(llm, 2)["reviewWork"]["instruction"]
    assert len(reads) == 2


@pytest.mark.asyncio
async def test_distinct_negative_source_leads_all_get_an_assessment_opportunity(source_tree, monkeypatch):
    original = VerificationTools.call
    reads = []
    async def tracked(self, name, args):
        reads.append((name, args))
        return await original(self, name, args)
    monkeypatch.setattr(VerificationTools, "call", tracked)
    responses = [step(calls=[{"name": "grepReviewCode", "arguments": {
        "query": word, "mode": "literal", "paths": ["caller.py"]}}]) for word in ("missing_one", "missing_two", "missing_three")]
    responses.append(step(assess(verdict="uncertain", evidence=[], reason="None of the concrete source routes establishes the missing contract")))
    result, llm = await review(source_tree, responses)
    assert result.issues == []
    assert llm.ainvoke.await_count == 4
    assert len(reads) == 3
    assert result.decisions[0]["verdict"] == "uncertain"
    assert "missing_one" in str(prompt_at(llm, 2)) and "missing_two" in str(prompt_at(llm, 2))


@pytest.mark.asyncio
async def test_contradictory_reports_reuse_source_in_one_conditional_case(source_tree):
    singleton_plan = response(groups=[{"caseIds": ["case-1"]}, {"caseIds": ["case-2"]}])
    reconciliation = response(groups=[{"memberIds": ["issue-1"], "representativeId": "issue-1"},
                                     {"memberIds": ["issue-2"], "representativeId": "issue-2"}],
        conflicts=[{"memberIds": ["issue-1", "issue-2"], "question": "Does the identified caller compensate for the return?"}])
    result, llm = await review(source_tree, [singleton_plan, step(assess()), step(assess(evidence=["diff:part-2"])),
        reconciliation, step(assess(), assess("work-2", "refuted", ["diff:part-1", "diff:part-2"], "Both exact definitions show the second claim is incorrect"))],
        findings=[finding(), finding("caller.py", "part-2", "Conflicting compensation claim")], parts=[part(), part("caller.py", "part-2")])
    assert [item["file"] for item in result.issues] == ["change.py"]
    assert llm.ainvoke.await_count == 5
    packet = prompt_at(llm, 4)
    assert packet["contradictoryClaims"] == "Does the identified caller compensate for the return?"
    assert {item["id"] for item in packet["workItems"]} == {"work-1", "work-2"}
    assert {item["result"].get("path") for item in packet["evidence"]} >= {"change.py", "caller.py"}
    assert not result.diagnostics


@pytest.mark.asyncio
async def test_conflict_source_outage_preserves_previously_verified_reports(source_tree):
    issues = [finding(), finding("caller.py", "part-2", "Conflicting compensation claim")]
    result, llm = await review(source_tree, [response(groups=[{"caseIds": ["case-1"]}, {"caseIds": ["case-2"]}]),
        step(assess()), step(assess(evidence=["diff:part-2"])),
        response(groups=[{"memberIds": ["issue-1"], "representativeId": "issue-1"}, {"memberIds": ["issue-2"], "representativeId": "issue-2"}],
                 conflicts=[{"memberIds": ["issue-1", "issue-2"], "question": "Which validation premise is supported?"}]),
        RuntimeError("provider unavailable")], findings=issues, parts=[part(), part("caller.py", "part-2")])
    assert result.issues == issues
    assert llm.ainvoke.await_count == 5
    assert any("retained" in message for message in result.diagnostics)


@pytest.mark.asyncio
@pytest.mark.parametrize("adjudication", [
    [step(assess(verdict="uncertain", evidence=[], reason="Missing external contract"),
          assess("work-2", "uncertain", [], "Missing external contract"))],
    [SimpleNamespace(content="unfinished answer"), SimpleNamespace(content="unfinished answer")],
])
async def test_inconclusive_optional_conflict_check_cannot_suppress_verified_reports(source_tree, adjudication):
    issues = [finding(), finding("caller.py", "part-2", "Conflicting compensation claim")]
    result, _ = await review(source_tree, [response(groups=[{"caseIds": ["case-1"]}, {"caseIds": ["case-2"]}]),
        step(assess()), step(assess(evidence=["diff:part-2"])),
        response(groups=[{"memberIds": ["issue-1"], "representativeId": "issue-1"}, {"memberIds": ["issue-2"], "representativeId": "issue-2"}],
                 conflicts=[{"memberIds": ["issue-1", "issue-2"], "question": "Which validation premise is supported?"}]),
        *adjudication], findings=issues, parts=[part(), part("caller.py", "part-2")])
    assert result.issues == issues
    assert any("prior verified report retained" in message for message in result.diagnostics)
    assert not any("unconfirmed hypothesis not published" in message for message in result.diagnostics)


@pytest.mark.asyncio
async def test_native_binder_without_forced_choice_keeps_verifier_available(source_tree):
    from service.review.review_step import STEP_TOOL
    model = SimpleNamespace(bind_tools=lambda schemas: model)
    model.ainvoke = AsyncMock(return_value=SimpleNamespace(content="", tool_calls=[
        {"id": "a", "name": STEP_TOOL, "args": json.loads(step(assess()).content)}]))
    result = await ReviewVerifier(None).verify(llm=model, request=request(), findings=[finding()], summaries=[], parts=[part()], binding=source_tree)
    assert result.issues == [finding()]
    assert model.ainvoke.await_count == 1
    assert any("does not support forced tool choice" in warning for warning in result.warnings)
    assert not result.diagnostics


@pytest.mark.asyncio
async def test_declared_provider_capability_omits_forced_choice_before_request(source_tree):
    from service.review.review_step import STEP_TOOL
    binding_options = []
    def bind(schemas, **kwargs):
        binding_options.append(kwargs)
        assert {schema["function"]["name"] for schema in schemas} == {STEP_TOOL}
        return model
    model = SimpleNamespace(bind_tools=bind, _supports_tool_choice=False)
    model.ainvoke = AsyncMock(return_value=SimpleNamespace(content="", tool_calls=[
        {"id": "a", "name": STEP_TOOL, "args": json.loads(step(assess()).content)}]))
    result = await ReviewVerifier(None).verify(llm=model, request=request(), findings=[finding()], summaries=[], parts=[part()], binding=source_tree)
    assert result.issues == [finding()]
    assert binding_options == [{}]
    assert model.ainvoke.await_count == 1


def needs(fact):
    return step(assess(verdict="needs_evidence", evidence=[], reason=fact))


def scoped_read(path, fact):
    return response(toolCalls=[{"name": "readReviewFile", "arguments": {
        "path": path, "workIds": ["work-1"], "missingFact": fact,
    }}])


@pytest.mark.asyncio
async def test_missing_fact_handoff_assesses_each_observation_before_further_read(source_tree):
    caller_fact = "Does the caller compensate for the changed division?"
    implementation_fact = "Does compute itself guard the divisor?"
    result, model = await review(source_tree, [
        needs(caller_fact), scoped_read("caller.py", caller_fact),
        needs(implementation_fact), scoped_read("change.py", implementation_fact),
        step(assess(evidence=["read-1", "read-2"])),
    ])
    assert result.issues == [finding()]
    packets = [prompt_at(model, index) for index in range(5)]
    assert [packet["reviewWork"]["phase"] for packet in packets] == [
        "assessment", "evidence", "assessment", "evidence", "assessment"]
    assert packets[1]["reviewWork"]["missingFacts"] == [{"workId": "work-1", "missingFact": caller_fact}]
    assert packets[2]["reviewWork"]["missingFacts"] == packets[1]["reviewWork"]["missingFacts"]
    assert packets[3]["reviewWork"]["missingFacts"] == [{"workId": "work-1", "missingFact": implementation_fact}]
    assert packets[2]["reviewWork"]["observations"][0]["evidenceId"] == "read-1"
    assert not result.diagnostics


@pytest.mark.asyncio
async def test_assessment_after_repeated_reads_can_resolve_the_finding(source_tree):
    fact = "Does the caller guard the changed operation?"
    responses = [item for _ in range(3) for item in (needs(fact), scoped_read("caller.py", fact))]
    responses.append(step(assess(evidence=["read-1"])))
    result, model = await review(source_tree, responses)
    assert result.issues == [finding()]
    assert model.ainvoke.await_count == 7
    assert prompt_at(model, 6)["reviewWork"]["phase"] == "assessment"
    assert not result.diagnostics


@pytest.mark.asyncio
async def test_stalled_reads_can_recover_through_a_new_missing_fact_handoff(source_tree):
    fact = "Does the caller guard the changed operation?"
    new_fact = "Does the implementation validate before dividing?"
    responses = [item for _ in range(3) for item in (needs(fact), scoped_read("caller.py", fact))]
    responses.extend([needs(new_fact), scoped_read("change.py", new_fact), step(assess(evidence=["read-1", "read-2"]))])
    result, model = await review(source_tree, responses)
    assert result.issues == [finding()]
    assert model.ainvoke.await_count == 9
    assert prompt_at(model, 7)["reviewWork"]["missingFacts"] == [{"workId": "work-1", "missingFact": new_fact}]
    assert not result.diagnostics


@pytest.mark.asyncio
async def test_reworded_purpose_of_repeated_call_cannot_keep_unresolved_case_alive(source_tree):
    responses = [item for fact in ("Check caller compensation", "Inspect caller guards", "Verify caller inputs", "Caller behavior")
                 for item in (needs(fact), scoped_read("caller.py", fact))]
    result, model = await review(source_tree, responses)
    assert result.issues == []
    assert model.ainvoke.await_count == 8
    assert any("outcome correction" in message for message in result.diagnostics)
    assert all(prompt_at(model, index)["reviewWork"]["phase"] == "assessment" for index in (0, 2, 4, 6))
    assert not any("without verification" in message for message in result.diagnostics)


@pytest.mark.asyncio
async def test_pending_report_correction_does_not_block_a_valid_missing_source_handoff(source_tree):
    report = {**finding(), "evidenceIds": ["read-not-observed"]}
    reason = "The changed return divides the supplied value by zero."
    result, model = await review(source_tree, [
        step(assess(issue=report)), needs("Read the definition supporting the report"),
        scoped_read("change.py", "Read the definition supporting the report"),
        step(assess(evidence=["read-1"], reason=reason, issue={"evidenceIds": ["read-1"]})),
    ], findings=[], investigations=[{"id": "compute", "partIds": ["part-1"], "question": "Does compute return normally?"}])
    assert len(result.issues) == 1
    assert result.issues[0]["title"] == report["title"]
    assert result.resolved_investigation_ids == {"compute"}
    assert model.ainvoke.await_count == 4
    assert prompt_at(model, 1)["workItems"][0]["issueCorrections"]
    assert [prompt_at(model, index)["reviewWork"]["phase"] for index in range(4)] == [
        "assessment", "assessment", "evidence", "assessment"]
    assert not result.diagnostics
