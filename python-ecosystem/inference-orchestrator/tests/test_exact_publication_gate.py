"""Accept-only publication contract shared by exact review implementations."""

from service.review.publication_gate import (
    ExactPublicationClaim,
    ExactSourceProof,
    accept_exact_publication,
    changed_lines_from_diff,
    evaluate_exact_publication,
    reviewable_lines_from_diff,
)
from model.output_schemas import CodeReviewIssue


EXECUTION_ID = "execution-publication-gate"
HEAD_SHA = "b" * 40
SOURCE_DIGEST = "d" * 64
RAW_DIFF = """\
diff --git a/src/payment.py b/src/payment.py
--- a/src/payment.py
+++ b/src/payment.py
@@ -7,2 +7,3 @@ def charge(token):
     prepare(token)
+    debit_without_limit(token)
     settle(token)
"""
QUOTED_RENAME_DIFF = r'''diff --git "a/old folder/na\303\257ve.py" "b/new folder/\344\275\240\345\245\275.py"
similarity index 80%
rename from "old folder/na\303\257ve.py"
rename to "new folder/\344\275\240\345\245\275.py"
--- "a/old folder/na\303\257ve.py"
+++ "b/new folder/\344\275\240\345\245\275.py"
@@ -1 +1,2 @@
-old = 1
+new = 1
+validate(new)
'''


def _issue(**updates) -> CodeReviewIssue:
    values = {
        "severity": "HIGH",
        "category": "BUG_RISK",
        "file": "src/payment.py",
        "line": 8,
        "title": "Debit bypasses configured limit",
        "reason": "The new debit executes without enforcing the account limit.",
        "suggestedFixDescription": "Validate the limit before debiting.",
        "codeSnippet": "    debit_without_limit(token)",
    }
    values.update(updates)
    return CodeReviewIssue(**values)


def _proof(**updates) -> ExactSourceProof:
    values = {
        "execution_id": EXECUTION_ID,
        "head_sha": HEAD_SHA,
        "path": "src/payment.py",
        "line": 8,
        "code_snippet": "    debit_without_limit(token)",
        "source_digest": SOURCE_DIGEST,
        "verified": True,
    }
    values.update(updates)
    return ExactSourceProof(**values)


def _claim(**updates) -> ExactPublicationClaim:
    values = {
        "finding_type": "DEFECT",
        "verification_status": "CONFIRMED",
        "precondition": "An authenticated request supplies an account token.",
        "reachable_path": "The request handler reaches charge() and then this debit.",
        "failure": "The debit executes before any configured limit is checked.",
        "impact": "An account can be debited beyond its configured safety limit.",
        "counter_evidence": "Checked the complete exact-head function and no guard exists.",
    }
    values.update(updates)
    return ExactPublicationClaim(**values)


def _accept(issue=None, claim=None, proof=None):
    return accept_exact_publication(
        issue or _issue(),
        claim or _claim(),
        proof,
        changed_lines=changed_lines_from_diff(RAW_DIFF),
        execution_id=EXECUTION_ID,
        head_sha=HEAD_SHA,
    )


def test_confirmed_defect_with_verified_changed_source_line_is_accepted():
    accepted = _accept(proof=_proof())

    assert accepted is not None
    assert accepted.file == "src/payment.py"
    assert accepted.line == 8
    assert accepted.codeSnippet == "    debit_without_limit(token)"


def test_changed_lines_decode_java_accepted_quoted_utf8_rename_path():
    changed = changed_lines_from_diff(QUOTED_RENAME_DIFF)

    assert changed == {
        "new folder/你好.py": {
            1: "new = 1",
            2: "validate(new)",
        }
    }


def test_reviewable_lines_include_new_side_context_and_additions():
    visible = reviewable_lines_from_diff(RAW_DIFF)

    assert visible == {
        "src/payment.py": {
            7: "    prepare(token)",
            8: "    debit_without_limit(token)",
            9: "    settle(token)",
        }
    }


def test_rejected_and_inconclusive_findings_are_not_published():
    assert _accept(claim=_claim(verification_status="REJECTED"), proof=_proof()) is None
    assert _accept(claim=_claim(verification_status="INCONCLUSIVE"), proof=_proof()) is None


def test_advisory_finding_is_not_published_even_when_confirmed():
    assert _accept(claim=_claim(finding_type="ADVISORY"), proof=_proof()) is None


def test_confirmed_defect_without_verified_exact_source_proof_is_not_published():
    assert _accept(proof=None) is None
    assert _accept(proof=_proof(verified=False)) is None
    assert _accept(proof=_proof(source_digest="not-a-digest")) is None


def test_confirmed_defect_requires_every_nontrivial_evidence_chain_link():
    for field in (
        "precondition",
        "reachable_path",
        "failure",
        "impact",
        "counter_evidence",
    ):
        assert _accept(claim=_claim(**{field: "short"}), proof=_proof()) is None


def test_source_proof_must_match_exact_execution_file_and_changed_line():
    assert _accept(proof=_proof(execution_id="other-execution")) is None
    assert _accept(proof=_proof(head_sha="a" * 40)) is None
    assert _accept(proof=_proof(path="src/other.py")) is None
    assert _accept(proof=_proof(line=7)) is None
    assert _accept(proof=_proof(code_snippet="prepare(token)")) is None


def test_info_and_non_defect_categories_are_not_publishable_defects():
    assert _accept(issue=_issue(severity="INFO"), proof=_proof()) is None
    assert _accept(issue=_issue(severity="CRITICAL"), proof=_proof()) is None
    assert _accept(issue=_issue(category="STYLE"), proof=_proof()) is None
    assert _accept(issue=_issue(category="DOCUMENTATION"), proof=_proof()) is None


def test_evaluation_exposes_stable_rejection_reason():
    decision = evaluate_exact_publication(
        _issue(category="LOGIC"),
        _claim(),
        _proof(),
        changed_lines=changed_lines_from_diff(RAW_DIFF),
        execution_id=EXECUTION_ID,
        head_sha=HEAD_SHA,
    )

    assert decision.issue is None
    assert decision.rejection_reason == "unsupported_or_non_defect_category"
