"""Source/authority regressions; these are not precision or recall evaluations."""
import difflib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from service.review.verification_context import VerificationContext
from service.review.verification_state import VerificationState
from service.review.verification_tools import VerificationTools
from service.review.verifier import ReviewVerifier, VerificationResult


CASES = json.loads((Path(__file__).parent / "fixtures/review_reconciliation/evidence_quality_cases.json").read_text())["cases"]


def _part(case):
    diff = "".join(difflib.unified_diff(case["before"].splitlines(keepends=True),
        case["after"].splitlines(keepends=True), fromfile="a/" + case["path"],
        tofile="b/" + case["path"], n=max(len(case["before"].splitlines()), len(case["after"].splitlines()))))
    return SimpleNamespace(id="part-1", path=case["path"], side="proposed",
        anchors={case["anchor"]: case["after"].splitlines()[case["anchor"] - 1]}, diff=diff)


def _binding(tmp_path, case):
    target, overlay = tmp_path / "target", tmp_path / "overlay"
    for root, files in ((target, {case["path"]: case["before"], **case["context"]}),
                        (overlay / "files", {case["path"]: case["after"]})):
        root.mkdir(parents=True)
        for path, text in files.items():
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text)
    (overlay / "manifest.json").write_text(json.dumps({"changedFiles": [case["path"]], "deletedFiles": []}))
    return {"target_repo_path": str(target), "review_overlay_path": str(overlay)}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda item: item["id"])
async def test_case_prompt_preserves_exact_source_without_promoting_routing_claims(tmp_path, monkeypatch, case):
    """Inspect the real case setup; no fake verdict is used as a quality oracle."""
    import service.review.verifier as module
    from service.review.verification_cases import build_cases

    part = _part(case)
    finding = {"partId": part.id, "file": part.path, "line": case["anchor"],
               "title": case["claim"], "reason": case["claim"], "batchIds": ["batch-a"]}
    cases = build_cases([finding], [], {part.id: part}, {})

    async def planned(*args, **kwargs):
        return SimpleNamespace(cases=cases, diagnostics=[], groups=[{
            "caseId": cases[0].id, "contract": case["routingClaim"], "rationale": "model-generated routing only"}])

    observed = {}

    async def capture(llm, request, state, tools, payload, batch_ids, **kwargs):
        observed.update(payload=payload, state=state, tools=tools)
        return VerificationResult(issues=[])

    monkeypatch.setattr(module, "plan_verification_cases", planned)
    verifier = ReviewVerifier(None)
    monkeypatch.setattr(verifier, "_verify_case", capture)
    await verifier.verify(llm=None, request=SimpleNamespace(aiProvider="openai", pullRequestId="example",
        prTitle="Change behavior", prDescription="User-provided intent", projectRules="User-defined rule", taskContext=None),
        findings=[finding], summaries=[{"batchId": "batch-a", "paths": [part.path],
            "summary": {"contracts": [case["routingClaim"]]}}], parts=[part], binding=_binding(tmp_path, case),
        source_context=[{"status": "ready", "path": part.path, "side": "proposed", "startLine": 1,
                         "endLine": len(case["after"].splitlines()), "content": case["after"]}])
    assert "contractHints" not in observed["payload"]
    assert "sharedContract" not in observed["payload"]
    assert case["routingClaim"] not in json.dumps(observed["payload"])
    assert observed["payload"]["changePurpose"]["description"] == "User-provided intent"
    assert observed["payload"]["projectRules"] == "User-defined rule"
    assert observed["state"].work_items()[0]["title"] == case["claim"]
    # The hypothesis remains work, while source preserves the actual before/after.
    assert observed["state"].evidence["diff:part-1"]["result"]["diff"] == part.diff
    assert any(record["result"].get("content") == case["after"]
               for record in observed["state"].evidence.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda item: item["id"])
async def test_source_queries_keep_governing_files_and_baseline_separate(tmp_path, case):
    """A requested config, callee, template or vendored implementation is exact source."""
    tools = VerificationTools(rag_client=None, binding=_binding(tmp_path, case), parts=[_part(case)])
    state = VerificationState([], [], {})
    for side, expected in (("target", case["before"]), ("proposed", case["after"])):
        result = await tools.call("readReviewFile", {"path": case["path"], "side": side})
        assert result["content"] == expected
        state.add_evidence("readReviewFile", result)
    for path, expected in case["context"].items():
        result = await tools.call("readReviewFile", {"path": path, "side": "proposed"})
        assert result["status"] == "ready"
        assert result["content"] == expected
        state.add_evidence("readReviewFile", result)
    projection = VerificationContext(state).render()["evidence"]
    source = {(record["result"]["path"], record["result"]["side"]): record["result"] for record in projection}
    assert source[(case["path"], "target")]["content"] == case["before"]
    assert source[(case["path"], "proposed")]["content"] == case["after"]
    for path, expected in case["context"].items():
        assert source[(path, "proposed")]["content"] == expected
