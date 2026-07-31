from model.multi_stage import CrossFileIssue
from service.review.pr_evidence import (
    STAGE_2_PR_EVIDENCE_CHAR_BUDGET,
    build_pr_evidence_ledger,
    gate_task_coverage_candidates,
)
from utils.diff_processor import DiffProcessor


def _section(path: str, old: str, new: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        f"-{old}\n"
        f"+{new}\n"
    )


def _coverage_issue(
    *,
    title: str = "PR does not implement the requested coupon tracking",
    refs: list[str] | None = None,
    regression: bool = False,
    finding_scope: str = "TASK_COVERAGE_GAP",
) -> CrossFileIssue:
    return CrossFileIssue(
        id="CROSS_001",
        severity="MEDIUM",
        category="BUG_RISK",
        title=title,
        primary_file="app/UPS/GenerateLabel.php",
        line=1,
        codeSnippet="$label = $this->generate();",
        affected_files=["app/UPS/GenerateLabel.php"],
        description=title,
        evidence="The current delta does not show the requested implementation.",
        business_impact="Tracking would be incomplete.",
        suggestion="Add tracking.",
        findingScope=finding_scope,
        coverageEvidenceRefs=refs or [],
        coverageRegression=regression,
    )


def test_incremental_ledger_keeps_delta_review_separate_from_full_pr_state():
    earlier_implementation = _section(
        "app/Tracking/NewRelicCouponTracker.php",
        "return null;",
        "return $newRelic->recordCustomEvent('CouponApplied', $payload);",
    )
    delta = _section(
        "app/UPS/GenerateLabel.php",
        "return $label;",
        "return $this->normalize($label);",
    )
    full_pr = DiffProcessor().process(earlier_implementation + delta)
    review_delta = DiffProcessor().process(delta)

    ledger = build_pr_evidence_ledger(
        full_pr,
        review_delta,
        incremental=True,
        task_context={
            "task_key": "SHOP-42",
            "task_summary": "Add coupon and checkout New Relic tracking",
        },
    )

    assert "app/Tracking/NewRelicCouponTracker.php" in ledger.full_pr_context
    assert "app/UPS/GenerateLabel.php" in ledger.full_pr_context
    assert "app/Tracking/NewRelicCouponTracker.php" not in ledger.incremental_delta_context
    assert "app/UPS/GenerateLabel.php" in ledger.incremental_delta_context
    assert "app/Tracking/NewRelicCouponTracker.php" in ledger.task_relevant_paths
    assert ledger.prompt_chars <= STAGE_2_PR_EVIDENCE_CHAR_BUDGET

    persisted = ledger.task_implementation_evidence_payload("SHOP-42")
    assert persisted is not None
    assert persisted["taskKey"] == "SHOP-42"
    assert persisted["source"] == "DETERMINISTIC_PR_LEDGER"
    assert persisted["items"][0]["filePath"] == (
        "app/Tracking/NewRelicCouponTracker.php"
    )
    assert "recordCustomEvent" in persisted["items"][0]["excerpt"]
    assert persisted["items"][0]["lineStart"] == 1
    assert persisted["items"][0]["lineEnd"] == 1


def test_incremental_gate_rejects_missing_requirement_claim_even_if_mislabelled():
    full_pr = DiffProcessor().process(
        _section(
            "app/Tracking/Coupon.php",
            "return null;",
            "return record_coupon_tracking();",
        )
        + _section(
            "app/UPS/GenerateLabel.php",
            "return $label;",
            "return normalize($label);",
        )
    )
    delta = DiffProcessor().process(
        _section(
            "app/UPS/GenerateLabel.php",
            "return $label;",
            "return normalize($label);",
        )
    )
    ledger = build_pr_evidence_ledger(
        full_pr,
        delta,
        incremental=True,
        task_context={"task_summary": "Coupon tracking"},
    )
    delta_ref = next(
        ref
        for ref, evidence in ledger.evidence_by_ref.items()
        if evidence.scope == "delta"
    )
    issue = _coverage_issue(
        refs=[delta_ref],
        finding_scope="CONCRETE_DEFECT",
    )

    result = gate_task_coverage_candidates(
        [issue],
        incremental=True,
        task_context={"task_summary": "Coupon tracking"},
        previous_issue_ids=[],
        ledger=ledger,
    )

    assert result.kept == ()
    assert result.rejected[0][1] == "new_incremental_omission_claim"


def test_incremental_gate_allows_explicit_delta_removal_regression():
    removal_delta = DiffProcessor().process(
        _section(
            "app/Tracking/Coupon.php",
            "record_coupon_tracking();",
            "return;",
        )
    )
    ledger = build_pr_evidence_ledger(
        removal_delta,
        removal_delta,
        incremental=True,
        task_context={"task_summary": "Coupon tracking"},
    )
    delta_ref = next(iter(ledger.delta_removal_refs))
    issue = _coverage_issue(refs=[delta_ref], regression=True)

    result = gate_task_coverage_candidates(
        [issue],
        incremental=True,
        task_context={"task_summary": "Coupon tracking"},
        previous_issue_ids=[],
        ledger=ledger,
    )

    assert result.kept == (issue,)
    assert result.rejected == ()


def test_incremental_gate_rejects_regression_flag_for_unrelated_removal():
    removal_delta = DiffProcessor().process(
        _section(
            "app/UPS/GenerateLabel.php",
            "trim($label);",
            "normalize($label);",
        )
    )
    ledger = build_pr_evidence_ledger(
        removal_delta,
        removal_delta,
        incremental=True,
        task_context={"task_summary": "Coupon tracking"},
    )
    delta_ref = next(
        ref
        for ref, evidence in ledger.evidence_by_ref.items()
        if evidence.scope == "delta"
    )
    issue = _coverage_issue(refs=[delta_ref], regression=True)

    result = gate_task_coverage_candidates(
        [issue],
        incremental=True,
        task_context={"task_summary": "Coupon tracking"},
        previous_issue_ids=[],
        ledger=ledger,
    )

    assert ledger.delta_removal_refs == frozenset()
    assert result.kept == ()
    assert result.rejected[0][1] == "delta_removal_evidence_missing"


def test_removed_task_behavior_is_not_persisted_as_positive_evidence():
    removal = DiffProcessor().process(
        _section(
            "app/Tracking/NewRelicCouponTracker.php",
            "record_coupon_tracking();",
            "return;",
        )
    )
    ledger = build_pr_evidence_ledger(
        removal,
        removal,
        incremental=True,
        task_context={
            "task_key": "SHOP-42",
            "task_summary": "Coupon New Relic tracking",
        },
    )

    assert ledger.task_implementation_evidence_payload("SHOP-42") is None


def test_task_metadata_does_not_rank_unrelated_code_as_implementation_evidence():
    unrelated = DiffProcessor().process(
        _section("app/Unrelated.php", "old();", "return new_value();")
    )
    ledger = build_pr_evidence_ledger(
        unrelated,
        unrelated,
        incremental=True,
        task_context={
            "task_key": "SHOP-42",
            "task_summary": "Coupon tracking",
            "status": "New",
            "assignee": "app@example.com",
            "provider": "return",
        },
    )

    assert ledger.task_relevant_paths == ()
    assert ledger.task_implementation_evidence_payload("SHOP-42") is None


def test_full_review_requires_complete_changed_line_evidence_for_gap():
    sections = []
    for index in range(120):
        sections.append(
            _section(
                f"src/Feature{index}.php",
                f"old_{index}_" + ("x" * 260),
                f"new_coupon_tracking_{index}_" + ("y" * 260),
            )
        )
    full_pr = DiffProcessor().process("".join(sections))
    ledger = build_pr_evidence_ledger(
        full_pr,
        full_pr,
        incremental=False,
        task_context={"task_summary": "Coupon tracking"},
    )
    pr_ref = next(
        ref
        for ref, evidence in ledger.evidence_by_ref.items()
        if evidence.scope == "full_pr"
    )
    issue = _coverage_issue(refs=[pr_ref])

    result = gate_task_coverage_candidates(
        [issue],
        incremental=False,
        task_context={"task_summary": "Coupon tracking"},
        previous_issue_ids=[],
        ledger=ledger,
    )

    assert ledger.prompt_chars <= STAGE_2_PR_EVIDENCE_CHAR_BUDGET
    assert ledger.manifest_complete
    assert not ledger.full_evidence_complete
    assert result.kept == ()
    assert result.rejected[0][1] == "full_pr_changed_line_evidence_bounded"


def test_full_review_marks_a_truncated_hunk_excerpt_as_bounded():
    changed_lines = "\n".join(
        f"+coupon_tracking_step_{index}('{('x' * 80)}');"
        for index in range(30)
    )
    raw_diff = (
        "diff --git a/app/Tracking/Coupon.php b/app/Tracking/Coupon.php\n"
        "--- a/app/Tracking/Coupon.php\n"
        "+++ b/app/Tracking/Coupon.php\n"
        "@@ -0,0 +1,30 @@\n"
        f"{changed_lines}\n"
    )
    full_pr = DiffProcessor().process(raw_diff)
    ledger = build_pr_evidence_ledger(
        full_pr,
        full_pr,
        incremental=False,
        task_context={"task_summary": "Coupon tracking"},
    )

    assert ledger.manifest_complete
    assert not ledger.full_evidence_complete
    assert "Changed-line evidence status: BOUNDED" in ledger.full_pr_context


def test_full_review_allows_evidence_backed_gap_when_full_diff_fits():
    full_pr = DiffProcessor().process(
        _section(
            "app/Checkout/Config.php",
            "enable_coupon_tracking();",
            "disable_coupon_tracking();",
        )
    )
    ledger = build_pr_evidence_ledger(
        full_pr,
        full_pr,
        incremental=False,
        task_context={"task_summary": "Coupon tracking must remain enabled"},
    )
    pr_ref = next(
        ref
        for ref, evidence in ledger.evidence_by_ref.items()
        if evidence.scope == "full_pr"
    )
    issue = _coverage_issue(refs=[pr_ref])

    result = gate_task_coverage_candidates(
        [issue],
        incremental=False,
        task_context={"task_summary": "Coupon tracking must remain enabled"},
        previous_issue_ids=[],
        ledger=ledger,
    )

    assert ledger.full_evidence_complete
    assert result.kept == (issue,)


def test_gate_is_independent_of_rag_and_suppresses_claim_without_task_context():
    diff = DiffProcessor().process(
        _section("src/App.php", "old();", "new();")
    )
    ledger = build_pr_evidence_ledger(
        diff,
        diff,
        incremental=True,
        task_context=None,
    )
    issue = _coverage_issue()

    result = gate_task_coverage_candidates(
        [issue],
        incremental=True,
        task_context=None,
        previous_issue_ids=[],
        ledger=ledger,
    )

    assert result.kept == ()
    assert result.rejected[0][1] == "task_context_unavailable"


def test_missing_incremental_full_pr_scope_cannot_prove_task_coverage_gap():
    delta = DiffProcessor().process(
        _section("src/current.php", "old();", "new();")
    )
    ledger = build_pr_evidence_ledger(
        None,
        delta,
        incremental=True,
        task_context={"task_summary": "Coupon tracking"},
    )
    issue = _coverage_issue(refs=[])

    result = gate_task_coverage_candidates(
        [issue],
        incremental=True,
        task_context={"task_summary": "Coupon tracking"},
        previous_issue_ids=[],
        ledger=ledger,
    )

    assert "No evidence is available" in ledger.full_pr_context
    assert not ledger.manifest_complete
    assert result.kept == ()
    assert result.rejected[0][1] == "full_pr_manifest_incomplete"
