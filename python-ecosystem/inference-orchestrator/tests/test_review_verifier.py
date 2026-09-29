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
    assert source.grep("compute", paths=["caller.py"])["results"] == [{"path": "caller.py", "lines": [2]}]
    assert source.grep("old_symbol", side="target")["results"] == [{"path": "deleted.py", "lines": [1]}]

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
    assert {schema["name"] for schema in schemas} == {"queryCodeGraph", "getStructuralUnit", "traverseCodeGraph", "getImpactRadius", "getMinimalReviewContext", "readReviewFile", "grepReviewCode", "getReviewDiff", "listReviewChanges"}
    assert all("workspace" not in schema["inputSchema"]["properties"] for schema in schemas)
    result = await tools.call("readReviewFile", {"path": "change.py"})
    assert result["status"] == "ready"
    assert "value / 0" in result["content"]
    assert await tools.call("readReviewFile", {"path": "../private"}) == await tools.call("readReviewFile", {"path": "../private"})
    await tools.call("queryCodeGraph", {"pattern": "callers_of", "target": "compute"})
    assert rag.query_review_graph.call_args.kwargs["workspace"] == "tenant-a"
    assert rag.query_review_graph.call_args.kwargs["include_source"] is False
    assert (await tools.call("deleteFile", {"path": "change.py"}))["status"] == "unavailable"

@pytest.mark.asyncio
async def test_investigation_can_discover_missing_issue_without_existing_candidates(source_tree):
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
        response(toolCalls=[{"name": "getReviewDiff", "arguments": {"partIds": ["part-1"]}}]),
        response(investigations=[{"id": "edge-1", "status": "resolved", "reason": "The function divides by zero", "evidenceIds": ["read-1"]}],
                 findings=[{**finding(), "evidenceIds": ["read-1"]}]),
    ]))
    output = await ReviewVerifier(None).verify(llm=llm, request=request(), findings=[], summaries=[], parts=[part()],
        binding=source_tree, investigations=[{"id": "edge-1", "partIds": ["part-1"], "paths": ["change.py"], "question": "Does compute still return normally?"}])
    assert len(output.issues) == 1
    assert output.issues[0]["partId"] == "part-1"
    assert output.resolved_investigation_ids == {"edge-1"}

def test_duplicate_cycles_and_dismissed_representatives_do_not_publish_hypotheses():
    candidates = {"a": finding(), "b": finding(title="different title")}
    for decisions in ({"a": {"verdict": "duplicate", "duplicateOf": "b"}, "b": {"verdict": "duplicate", "duplicateOf": "a"}},
                      {"a": {"verdict": "duplicate", "duplicateOf": "b"}, "b": {"verdict": "dismiss"}}):
        diagnostics = []
        result = ReviewVerifier._apply_decisions(candidates, decisions, diagnostics)
        assert result == {}
        if decisions["b"]["verdict"] == "duplicate":
            assert diagnostics

@pytest.mark.asyncio
async def test_graph_failure_recovered_by_source_does_not_leave_validation_incomplete(source_tree):
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
        response(toolCalls=[{"name": "queryCodeGraph", "arguments": {"pattern": "callers_of", "target": "compute"}}]),
        response(toolCalls=[{"name": "readReviewFile", "arguments": {"path": "change.py"}}]),
        response(decisions=[{"candidateId": "candidate-1", "verdict": "keep",
                             "reason": "Exact source confirms defect", "evidenceIds": ["read-2"]}]),
    ]))
    output = await ReviewVerifier(None).verify(llm=llm, request=request(), findings=[finding()], summaries=[],
                                              parts=[part()], binding=source_tree)
    assert output.issues == [finding()]
    assert output.diagnostics == []

def test_malformed_changed_path_metadata_does_not_disable_valid_source_reads(source_tree):
    source = LocalReviewSource(source_tree, ["../invalid.py", "change.py"])
    assert source.metadata_diagnostics
    assert source.read("change.py")["status"] == "ready"

@pytest.mark.asyncio
async def test_full_pr_context_can_be_read_but_not_published_in_incremental_review(source_tree):
    historical = part("caller.py", "historical-part")
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
        response(toolCalls=[{"name": "getReviewDiff", "arguments": {"partIds": ["historical-part"]}}]),
        response(investigations=[{"id": "check-history", "status": "resolved", "reason": "Related earlier change inspected", "evidenceIds": ["read-1"]}],
                 findings=[{**finding("caller.py", "historical-part"), "evidenceIds": ["read-1"]}]),
    ]))
    output = await ReviewVerifier(None).verify(llm=llm, request=request(), findings=[], summaries=[], parts=[part()],
        context_parts=[historical], binding=source_tree,
        investigations=[{"id": "check-history", "partIds": ["part-1"], "paths": ["change.py"], "question": "Does earlier change compensate?"}])
    assert output.issues == []
    assert output.resolved_investigation_ids == {"check-history"}
    last_prompt = prompt_at(llm, -1)
    assert last_prompt["contextChangedParts"] == [{"id": "historical-part", "path": "caller.py"}]
    assert "historical-part" in str(llm.ainvoke.call_args_list[-1].args[0])

@pytest.mark.asyncio
async def test_new_discovery_must_have_evidence_for_its_changed_anchor(source_tree):
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
        response(toolCalls=[{"name": "readReviewFile", "arguments": {"path": "caller.py"}}]),
        response(investigations=[{"id": "inspect", "status": "resolved", "reason": "Caller passes one", "evidenceIds": ["read-1"]}],
                 findings=[{**finding(), "evidenceIds": ["read-1"]}]),
    ]))
    output = await ReviewVerifier(None).verify(llm=llm, request=request(), findings=[], summaries=[], parts=[part()],
        binding=source_tree, investigations=[{"id": "inspect", "partIds": ["part-1"], "paths": ["change.py"], "question": "Check call contract"}])
    assert output.issues == []

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

@pytest.mark.asyncio
async def test_discovery_without_progress_on_pending_question_terminates_with_partial_result(source_tree):
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
        response(findings=[{**finding(), "evidenceIds": ["diff:part-1"]}]),
        response(findings=[{**finding(title="Another phrasing"), "evidenceIds": ["diff:part-1"]}]),
        response(groups=[{"memberIds": ["issue-1", "issue-2"], "representativeId": "issue-1",
                          "rationale": "Same zero division and repair"}]),
    ]))
    output = await ReviewVerifier(None).verify(llm=llm, request=request(), findings=[], summaries=[], parts=[part()],
        binding=source_tree, investigations=[{"id": "unresolved", "partIds": ["part-1"], "paths": ["change.py"], "question": "Check caller compensation"}])
    assert len(output.issues) == 1
    assert output.issues[0]["title"] == "Division by zero"
    assert llm.ainvoke.await_count == 3  # two no-progress case turns, then final reconciliation
    assert any("unresolved" in item for item in output.diagnostics)
    assert any("without new facts" in item for item in output.diagnostics)

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
            if "candidates" in payload:
                return payload
    raise AssertionError("review prompt missing")


def read_call(path):
    return {"name": "readReviewFile", "arguments": {"path": path}}


def verdict(candidate_id, value, evidence, reason="The changed expression divides every argument by zero.", **extra):
    return {"candidateId": candidate_id, "verdict": value, "reason": reason, "evidenceIds": evidence, **extra}


@pytest.mark.asyncio
async def test_unconfirmed_caller_hypothesis_is_not_published(source_tree):
    """Reproduce the observed regression: inability to disprove is not proof."""
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=response(decisions=[
        verdict("candidate-1", "uncertain", [], "No unmigrated consumer was located; the claim assumes one exists.")
    ])))
    hypothesis = finding(title="Hypothetical unmigrated caller crashes")
    result = await ReviewVerifier(None).verify(llm=llm, request=request(), findings=[hypothesis],
                                               summaries=[], parts=[part()], binding=source_tree)
    assert result.issues == []
    assert result.decisions[0]["verdict"] == "uncertain"
    assert any("not published" in message for message in result.diagnostics)
    assert llm.ainvoke.await_count == 1


@pytest.mark.asyncio
async def test_provider_failure_preserves_undecided_discovery_but_not_settled_uncertainty(source_tree):
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
        response(decisions=[verdict("candidate-1", "uncertain", [], "No concrete failing consumer")],
                 toolCalls=[read_call("change.py")]),
        RuntimeError("provider unavailable"),
    ]))
    issues = [finding(title="Speculation"), finding()]
    result = await ReviewVerifier(None).verify(llm=llm, request=request(), findings=issues,
                                               summaries=[], parts=[part()], binding=source_tree)
    assert result.issues == [issues[1]]
    assert any("without verification" in message for message in result.diagnostics)


@pytest.mark.asyncio
async def test_semantic_duplicates_across_cases_are_resolved_after_source_review(source_tree):
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
        response(decisions=[verdict("candidate-1", "keep", ["diff:part-1"])]),
        response(decisions=[verdict("candidate-1", "keep", ["diff:part-2"])]),
        response(groups=[{"memberIds": ["issue-1", "issue-2"], "representativeId": "issue-1",
                          "rationale": "Both report the same zero divisor reached by this caller; same repair"}]),
    ]))
    result = await ReviewVerifier(None).verify(llm=llm, request=request(),
        findings=[finding(), finding("caller.py", "part-2", "Caller crashes")], summaries=[],
        parts=[part(), part("caller.py", "part-2")], binding=source_tree)
    assert [item["file"] for item in result.issues] == ["change.py"]
    assert llm.ainvoke.await_count == 3
    assert not result.diagnostics
    final = str(llm.ainvoke.call_args_list[2].args[0])
    assert "issue-1" in final and "issue-2" in final
    assert "evidenceIds" not in final and "@@" not in final

@pytest.mark.asyncio
async def test_settled_decisions_are_not_discarded_when_model_also_requests_source(source_tree, tmp_path):
    # Related candidates retain their shared source until the case ends.
    marker = "shared_source_marker_" + "x" * 50000
    (tmp_path / "overlay" / "files" / "change.py").write_text("def compute(value):\n    return value / 0\n# " + marker + "\n")
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
        response(toolCalls=[read_call("change.py")]),
        response(decisions=[verdict("candidate-1", "keep", ["read-1"])], toolCalls=[read_call("caller.py")]),
        response(decisions=[verdict("candidate-2", "dismiss", ["read-1", "read-2"],
                                    reason="The complete caller establishes compensation for the second allegation")]),
    ]))
    result = await ReviewVerifier(None).verify(llm=llm, request=request(),
        findings=[finding(), finding(title="Caller compensation allegation")],
        summaries=[{"paths": ["change.py"], "summary": "malformed_summary_" * 10000}],
        parts=[part()], binding=source_tree)
    assert result.issues == [finding()]
    assert llm.ainvoke.await_count == 3
    second = str(llm.ainvoke.call_args_list[1].args[0])
    third = str(llm.ainvoke.call_args_list[2].args[0])
    assert second.count(marker) == 1
    assert third.count(marker) == 1
    assert "malformed_summary_" not in second + third
    assert not result.diagnostics

@pytest.mark.asyncio
async def test_reworded_partial_searches_do_not_extend_verification(source_tree):
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
        response(toolCalls=[{"name": "grepReviewCode", "arguments": {"query": word, "paths": ["missing.py"]}}])
        for word in ("oldName", "newName", "anotherGuess", "lastGuess")
    ]))
    result = await ReviewVerifier(None).verify(llm=llm, request=request(), findings=[finding()],
                                               summaries=[], parts=[part()], binding=source_tree)
    assert llm.ainvoke.await_count == 2
    assert result.issues == []
    assert any("without new facts" in message for message in result.diagnostics)


@pytest.mark.asyncio
async def test_graph_failure_can_switch_to_source_without_retrying_paid_call(source_tree):
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
        response(toolCalls=[{"name": "queryCodeGraph", "arguments": {"pattern": "callers_of", "target": "compute"}}]),
        response(toolCalls=[read_call("change.py")]),
        response(decisions=[verdict("candidate-1", "keep", ["read-2"])]),
    ]))
    result = await ReviewVerifier(None).verify(llm=llm, request=request(), findings=[finding()],
                                               summaries=[], parts=[part()], binding=source_tree)
    assert result.issues == [finding()]
    assert not result.diagnostics
    assert any("graph" in message.lower() for message in result.warnings)


@pytest.mark.asyncio
async def test_unrelated_exact_source_cannot_confirm_changed_anchor(source_tree):
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
        response(toolCalls=[read_call("caller.py")]),
        response(decisions=[verdict("candidate-1", "keep", ["read-1"])]),
        response(decisions=[verdict("candidate-1", "uncertain", [], "Caller alone does not establish the proposed failure")]),
    ]))
    result = await ReviewVerifier(None).verify(llm=llm, request=request(), findings=[finding()],
                                               summaries=[], parts=[part()], binding=source_tree)
    assert result.issues == []
    assert result.decisions[0]["verdict"] == "uncertain"
    assert "changed anchor" in json.loads(llm.ainvoke.call_args_list[2].args[0][-1][1])["corrections"][0]

@pytest.mark.asyncio
async def test_known_source_compensation_dismisses_migrated_caller_claim(source_tree, tmp_path):
    (tmp_path / "overlay" / "files" / "caller.py").write_text("async def call():\n    return await compute(1)\n")
    (tmp_path / "overlay" / "files" / "change.py").write_text("async def compute(value):\n    return value\n")
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
        response(toolCalls=[read_call("change.py"), read_call("caller.py")]),
        response(decisions=[verdict("candidate-1", "dismiss", ["read-1", "read-2"],
            reason="The identified caller awaits the newly asynchronous function and still returns the integer.")]),
    ]))
    issue = finding(title="Caller receives coroutine instead of integer")
    result = await ReviewVerifier(None).verify(llm=llm, request=request(), findings=[issue],
                                               summaries=[], parts=[part()], binding=source_tree)
    assert result.issues == []
    assert not result.diagnostics


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
