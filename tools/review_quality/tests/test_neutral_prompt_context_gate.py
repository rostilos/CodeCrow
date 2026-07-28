from __future__ import annotations

from tools.review_quality.neutral_prompt_context_gate import run_gate


def test_neutral_prompt_context_gate_delivers_all_expected_context():
    report = run_gate()

    assert report["status"] == "passed"
    assert report["checks"] == {
        "allCasesPassed": True,
        "allProviderCallsZero": True,
        "caseSetExact": True,
    }
    assert {
        case["caseId"] for case in report["cases"]
    } == {
        "neutral-java-refund-policy",
        "neutral-polyglot-invoice-contract",
        "neutral-python-authorization",
        "neutral-typescript-report-access",
    }
    for case in report["cases"]:
        assert all(case["checks"].values())
        assert case["providerCalls"] == 0
        assert case["missingEvidence"] == {}
        assert case["missingRemovedRelation"] == []


def test_neutral_prompt_context_gate_is_byte_stable():
    assert run_gate() == run_gate()
