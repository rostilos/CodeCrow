import csv

from tools.review_quality.validated_branch_publication_replay import replay


VALIDATION_FIELDS = [
    "branch_issue_id",
    "project_id",
    "branch_name",
    "validation_status",
]
ISSUE_FIELDS = [
    "branch_issue_id",
    "branch_severity",
    "branch_issue_category",
    "branch_file_path",
    "branch_line_number",
    "current_line_number",
    "issue_scope",
    "branch_title",
    "branch_reason",
    "branch_suggested_fix_description",
    "branch_code_snippet",
]


def _write(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _issue(identity, **overrides):
    row = {
        "branch_issue_id": identity,
        "branch_severity": "MEDIUM",
        "branch_issue_category": "BUG_RISK",
        "branch_file_path": "src/example.php",
        "branch_line_number": "10",
        "current_line_number": "10",
        "issue_scope": "LINE",
        "branch_title": "Concrete defect",
        "branch_reason": "The changed call fails for null input.",
        "branch_suggested_fix_description": "Add the missing guard.",
        "branch_code_snippet": "$service->run($value);",
    }
    row.update(overrides)
    return row


def test_replay_preserves_explicit_label_lenses_and_contract_exclusions(tmp_path):
    validation = tmp_path / "validation.csv"
    issues = tmp_path / "issues.csv"
    _write(validation, VALIDATION_FIELDS, [
        {
            "branch_issue_id": "1",
            "project_id": "402",
            "branch_name": "develop",
            "validation_status": "GENUINE_CURRENT",
        },
        {
            "branch_issue_id": "2",
            "project_id": "402",
            "branch_name": "develop",
            "validation_status": "FALSE_POSITIVE",
        },
        {
            "branch_issue_id": "3",
            "project_id": "402",
            "branch_name": "develop",
            "validation_status": "DUPLICATE_OF_GENUINE",
        },
        {
            "branch_issue_id": "4",
            "project_id": "402",
            "branch_name": "develop",
            "validation_status": "STALE_GENUINE_RESOLVED_IN_CODE",
        },
        {
            "branch_issue_id": "5",
            "project_id": "402",
            "branch_name": "develop",
            "validation_status": "UNREVIEWED",
        },
    ])
    _write(issues, ISSUE_FIELDS, [
        _issue("1"),
        _issue(
            "2",
            branch_reason="The current diff correctly fixes the typo.",
            branch_suggested_fix_description=(
                "No further code changes are required."
            ),
        ),
        _issue(
            "3",
            branch_file_path="src/duplicate.php",
            branch_reason="A separately reported duplicate candidate.",
            branch_code_snippet="$duplicate->run();",
        ),
        _issue(
            "4",
            branch_file_path="src/resolved.php",
            branch_reason="A genuine issue that was resolved later.",
            branch_code_snippet="$resolved->run();",
        ),
        _issue("5", branch_code_snippet=""),
    ])

    report = replay(validation, issues, precision_target=0.70)

    assert report["cohort"]["explicitlyReviewed"] == 4
    assert report["cohort"]["contractReady"] == 4
    assert report["originCandidateCorrectness"]["baseline"] == {
        "truePositives": 3,
        "falsePositives": 1,
        "falseNegatives": 0,
        "trueNegatives": 0,
        "precision": 0.75,
        "labeledCandidateRecall": 1.0,
    }
    assert report["originCandidateCorrectness"]["current"] == {
        "truePositives": 3,
        "falsePositives": 0,
        "falseNegatives": 0,
        "trueNegatives": 1,
        "precision": 1.0,
        "labeledCandidateRecall": 1.0,
    }
    assert report["currentUniqueActionability"]["current"] == {
        "truePositives": 1,
        "falsePositives": 2,
        "falseNegatives": 0,
        "trueNegatives": 1,
        "precision": 1 / 3,
        "labeledCandidateRecall": 1.0,
    }
    assert report["publication"]["modelCalls"] == 0
    assert report["publication"]["nonPublishablePolicyWithheld"] == 1
    assert report["publication"]["deterministicDedupWithheld"] == 0
    origin_breakdown = report["diagnosticBreakdowns"][
        "originCandidateCorrectness"
    ]["baseline"]
    assert origin_breakdown["severity"]["MEDIUM"] == {
        "candidates": 4,
        "truePositives": 3,
        "falsePositives": 1,
        "falseNegatives": 0,
        "trueNegatives": 0,
        "precision": 0.75,
        "labeledCandidateRecall": 1.0,
    }
    assert origin_breakdown["fileFamily"]["php"]["candidates"] == 4
    assert report["diagnosticBreakdowns"]["scope"].startswith("reporting only")
    assert report["status"] == "passed"


def test_replay_excludes_legacy_info_and_missing_anchor_from_fair_policy_pair(
    tmp_path,
):
    validation = tmp_path / "validation.csv"
    issues = tmp_path / "issues.csv"
    rows = [
        {
            "branch_issue_id": identity,
            "project_id": "402",
            "branch_name": "develop",
            "validation_status": "GENUINE_CURRENT",
        }
        for identity in ("1", "2", "3")
    ]
    _write(validation, VALIDATION_FIELDS, rows)
    _write(issues, ISSUE_FIELDS, [
        _issue("1"),
        _issue("2", branch_code_snippet=""),
        _issue("3", branch_severity="INFO"),
    ])

    report = replay(validation, issues)

    assert report["cohort"]["contractReady"] == 1
    assert report["cohort"]["contractExclusions"] == {
        "infoSeverity": 1,
        "missingCodeSnippet": 1,
    }


def test_replay_reports_xml_config_families_without_changing_policy(tmp_path):
    validation = tmp_path / "validation.csv"
    issues = tmp_path / "issues.csv"
    _write(validation, VALIDATION_FIELDS, [
        {
            "branch_issue_id": "1",
            "project_id": "402",
            "branch_name": "develop",
            "validation_status": "GENUINE_CURRENT",
        },
        {
            "branch_issue_id": "2",
            "project_id": "402",
            "branch_name": "develop",
            "validation_status": "FALSE_POSITIVE",
        },
    ])
    _write(issues, ISSUE_FIELDS, [
        _issue(
            "1",
            branch_file_path="app/code/Acme/Module/etc/di.xml",
            branch_issue_category="ARCHITECTURE",
        ),
        _issue(
            "2",
            branch_file_path="app/code/Acme/Module/etc/db_schema.xml",
            branch_issue_category="PERFORMANCE",
        ),
    ])

    report = replay(validation, issues)
    families = report["diagnosticBreakdowns"]["originCandidateCorrectness"][
        "baseline"
    ]["fileFamily"]

    assert families["xml:di.xml"]["truePositives"] == 1
    assert families["xml:db_schema.xml"]["falsePositives"] == 1
