"""Exact context assembly: no duplicate bodies and no omitted source facts."""
import json
from types import SimpleNamespace

from service.review.verification_context import VerificationContext
from service.review.verification_state import VerificationState


def state():
    return VerificationState([], [], {})


def read(ledger, content, start=1, side="proposed", path="a.py"):
    return ledger.add_evidence("readReviewFile", {"status": "ready", "path": path, "side": side,
        "startLine": start, "endLine": start + len(content.splitlines()) - 1, "content": content})[0]


def rendered(ledger):
    return {item["id"]: item for item in VerificationContext(ledger).render()["evidence"]}


def test_overlapping_reads_keep_every_line_once_and_original_citations_intact():
    ledger = state()
    first = read(ledger, "one\ntwo\n")
    second = read(ledger, "two\nthree\nfour\n", start=2)
    third = read(ledger, "two\n", start=2)
    result = rendered(ledger)
    assert result[first]["result"]["content"] == "one\ntwo\n"
    assert result[second]["result"]["sourceSegments"] == [{"startLine": 3, "endLine": 4, "content": "three\nfour\n"}]
    assert result[third]["result"]["sourceSegments"] == []
    assert result[third]["result"]["sourceReferences"] == [{"evidenceId": first, "path": "a.py", "side": "proposed", "startLine": 2, "endLine": 2}]
    assert ledger.evidence[second]["result"]["content"] == "two\nthree\nfour\n"


def test_grep_matches_point_to_exact_source_despite_different_line_endings_in_envelope():
    ledger = state()
    grep, _ = ledger.add_evidence("grepReviewCode", {"status": "ready", "side": "proposed", "query": "call", "results": [
        {"path": "a.py", "matches": [{"line": 2, "text": "call()"}]}]}, {"paths": ["a.py"], "query": "call"})
    source = read(ledger, "first\r\ncall()\r\n")
    result = rendered(ledger)
    assert result[source]["result"]["content"] == "first\r\ncall()\r\n"
    assert result[grep]["result"]["results"][0]["matches"][0] == {"line": 2, "sourceReference": {
        "evidenceId": source, "path": "a.py", "side": "proposed", "line": 2}}
    assert result[grep]["arguments"]["paths"] == ["a.py"]


def test_seeded_diff_and_tool_diff_share_hunk_without_conflating_before_with_target_head():
    ledger = state()
    diff = "@@ -1,2 +1,2 @@\n before\n-old()\n+new()\n"
    ledger.evidence["diff:part"] = {"kind": "diff", "result": {"status": "ready", "partId": "part", "path": "a.py", "side": "proposed", "diff": diff}}
    extra, _ = ledger.add_evidence("getReviewDiff", {"status": "ready", "parts": [{"id": "part", "path": "a.py", "side": "proposed", "diff": diff}]})
    proposed = read(ledger, "before\nnew()\n")
    target = read(ledger, "before\nold()\n", side="target")
    result = rendered(ledger)
    assert result["diff:part"]["result"]["diff"] == diff
    assert "diff" not in result[extra]["result"]["parts"][0]
    assert result[extra]["result"]["parts"][0]["sourceReference"]["evidenceId"] == "diff:part"
    assert result[proposed]["result"]["sourceSegments"] == []
    assert result[target]["result"]["content"] == "before\nold()\n"


def test_reusing_part_id_with_different_source_cannot_hide_new_bytes():
    ledger = state()
    for text in ("first", "second"):
        ledger.add_evidence("getReviewDiff", {"status": "ready", "parts": [{"id": "part", "path": "a.py", "diff": f"@@ -1 +1 @@\n-old\n+{text}\n"}]})
    result = VerificationContext(ledger).render()
    assert all("diff" in item["result"]["parts"][0] for item in result["evidence"])


def test_absence_scope_diagnostics_and_plugin_literals_are_preserved():
    ledger = state()
    graph = {"status": "partial", "nodes": [], "plugin": {"content": "unit@1", "unitRef": "raw"}, "frontier": ["node"]}
    ledger.add_evidence("queryCodeGraph", graph, {"pattern": "callers_of", "target": "symbol"})
    search = {"status": "partial", "query": "unused", "side": "proposed", "results": [], "unavailablePaths": ["b.py"], "complete": False}
    ledger.add_evidence("grepReviewCode", search, {"query": "unused", "paths": ["a.py", "b.py"]})
    result = VerificationContext(ledger).render()["evidence"]
    assert result[0]["result"] == graph
    assert result[1]["result"] == search
    assert result[1]["arguments"]["paths"] == ["a.py", "b.py"]


def test_no_newline_marker_on_removed_line_does_not_change_proposed_context():
    ledger = state()
    diff = "@@ -1,2 +1,2 @@\n context\n-old\n\\ No newline at end of file\n+new\n"
    ledger.evidence["diff:part"] = {"kind": "diff", "result": {"path": "a.py", "diff": diff}}
    source = read(ledger, "context\nnew\n")
    assert rendered(ledger)[source]["result"]["sourceSegments"] == []


def test_newline_distinctions_in_complete_source_are_not_clipped():
    ledger = state()
    first = read(ledger, "same\n")
    second = read(ledger, "same")
    result = rendered(ledger)
    assert result[first]["result"]["content"] == "same\n"
    assert result[second]["result"]["content"] == "same"


def test_fact_identity_ignores_query_wording_receipts_and_contained_source_ranges():
    ledger = state()
    read(ledger, "one\ntwo\nthree\n")
    original = VerificationContext(ledger).fingerprint()
    read(ledger, "two\n", start=2)
    for query in ("never-called", "another-absent-spelling"):
        ledger.add_evidence("grepReviewCode", {"status": "ready", "side": "proposed", "query": query, "results": [], "complete": True},
                            {"query": query, "paths": ["a.py"]})
    ledger.add_evidence("grepReviewCode", {"status": "ready", "side": "proposed", "query": "two", "results": [
        {"path": "a.py", "matches": [{"line": 2, "text": "two"}]}]})
    assert VerificationContext(ledger).fingerprint() == original
    assert "another-absent-spelling" in json.dumps(VerificationContext(ledger).render())


def test_seed_diff_already_owns_proposed_source_facts():
    ledger = state()
    diff = "@@ -1 +1 @@\n-old()\n+new()\n"
    ledger.evidence["diff:part"] = {"kind": "diff", "result": {"path": "a.py", "diff": diff}}
    original = VerificationContext(ledger).fingerprint()
    read(ledger, "new()\n")
    ledger.add_evidence("getReviewDiff", {"status": "ready", "parts": [{"id": "part", "path": "a.py", "diff": diff}]})
    assert VerificationContext(ledger).fingerprint() == original
    read(ledger, "new()\ncaller()\n")
    assert VerificationContext(ledger).fingerprint() != original


def test_graph_occurrence_scores_do_not_manufacture_new_contract_facts():
    ledger = state()
    node = {"unitId": "unit@1", "path": "a.py", "name": "call", "plugin": {"reason": "literal contract", "score": 9}}
    ledger.add_evidence("queryCodeGraph", {"status": "ready", "nodes": [node]})
    original = VerificationContext(ledger).fingerprint()
    ledger.add_evidence("getImpactRadius", {"status": "ready", "roots": [{**node, "depth": 3, "impactScore": 0.6, "reason": "query-dependent"}]})
    assert VerificationContext(ledger).fingerprint() == original
    ledger.add_evidence("queryCodeGraph", {"status": "ready", "nodes": [{**node, "plugin": {"reason": "different contract", "score": 9}}]})
    assert VerificationContext(ledger).fingerprint() != original


def test_new_file_locator_paths_are_facts_but_repeat_patterns_are_not():
    ledger = state()
    ledger.add_evidence("findReviewFiles", {"status": "ready", "side": "proposed", "pattern": "*.hbs", "paths": ["components/home-logo.hbs"]})
    original = VerificationContext(ledger).fingerprint()
    ledger.add_evidence("findReviewFiles", {"status": "ready", "side": "proposed", "pattern": "*logo*", "paths": ["components/home-logo.hbs"]})
    assert VerificationContext(ledger).fingerprint() == original
    ledger.add_evidence("findReviewFiles", {"status": "ready", "side": "proposed", "pattern": "*header*", "paths": ["components/header.hbs"]})
    assert VerificationContext(ledger).fingerprint() != original


def test_guessed_missing_paths_do_not_fund_repeated_search_but_remain_visible():
    ledger = state()
    original = VerificationContext(ledger).fingerprint()
    for path in ("guessed/header.hbs", "another/guessed-header.hbs"):
        ledger.add_evidence("readReviewFile", {"status": "missing", "path": path, "side": "proposed", "diagnostic": "requested source file is unavailable"})
    assert VerificationContext(ledger).fingerprint() == original
    assert "another/guessed-header.hbs" in json.dumps(VerificationContext(ledger).render())
    ledger.add_evidence("readReviewFile", {"status": "deleted", "path": "removed.py", "side": "proposed"})
    assert VerificationContext(ledger).fingerprint() != original
