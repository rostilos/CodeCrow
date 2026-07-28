import copy
import hashlib
import json

import pytest

from tools.review_quality.capture_pair_evaluation import (
    _digest,
    bind_cost_evidence,
    bind_ground_truth,
    create_template,
    evaluate_capture_manifest,
    merge_templates,
)


def _issue(title, line):
    return {
        "file": "app/code/Vendor/Module/Model/Example.php",
        "line": line,
        "title": title,
        "category": "BUG_RISK",
        "severity": "HIGH",
        "reason": f"Private explanation for {title}",
        "codeSnippet": "private source line",
    }


def _capture(
    *,
    mode_identity,
    plugins,
    issues,
    input_tokens=10,
    output_tokens=2,
    head="b" * 40,
    reviewable_hunks=4,
):
    request = {
        "projectId": 1752,
        "projectVcsWorkspace": "merchant",
        "projectVcsRepoSlug": "shop",
        "projectWorkspace": "merchant",
        "projectNamespace": "shop",
        "pullRequestId": 42,
        "sourceBranchName": "feature",
        "targetBranchName": "main",
        "baseCommitHash": "a" * 40,
        "currentCommitHash": head,
        "previousCommitHash": None,
        "analysisMode": "FULL",
        "rawDiff": "diff --git a/example.php b/example.php\n+changed",
        "deltaDiff": None,
        "changedFiles": ["app/code/Vendor/Module/Model/Example.php"],
        "deletedFiles": [],
        "aiProvider": "OPENAI",
        "aiModel": "review-model",
        "aiApiKey": "[REDACTED]",
        "aiBaseUrl": "https://example.test/llm",
        "aiCustomParameters": {"temperature": 0},
        "maxAllowedTokens": 20000,
        "useMcpTools": False,
        "projectRules": None,
        "promptDryRun": False,
    }
    response = {
        "answer": "captured",
        "usage_metadata": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }
    provider_event_response = {
        "generations": [[{
            "text": "{}",
            "usage_metadata": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        }]],
    }
    call = {
        "sequence": 1,
        "stage": "stage_1",
        "status": "completed",
        "input": "private prompt",
        "renderedPrompt": "private prompt",
        "response": response,
        "responseDigest": _digest(response),
        "providerEvents": [{
            "status": "completed",
            "response": provider_event_response,
        }],
        "providerCallCount": 1,
    }
    result = {"result": {"issues": issues}}
    plugin_identity = {
        "status": "resolved",
        "repositoryPlugins": plugins,
        "selectionFingerprint": f"selection-{mode_identity}",
        "requestDescriptorFingerprint": f"descriptor-{mode_identity}",
        "runtimeDescriptorFingerprint": f"descriptor-{mode_identity}",
        "implementationFingerprint": f"implementation-{mode_identity}",
        "descriptorMatch": True,
    }
    candidate_records = sorted(
        (
            {
                "candidateId": "sha256:" + hashlib.sha256(
                    f"{mode_identity}:{index}".encode("utf-8")
                ).hexdigest(),
                "stage": "stage_1",
                "generationPromptDigest": (
                    "sha256:"
                    + hashlib.sha256(
                        call["renderedPrompt"].encode("utf-8")
                    ).hexdigest()
                ),
                "reviewUnitIds": ["sha256:unit"],
                "promptHunkIds": ["sha256:hunk"],
                "anchorHunkIds": ["sha256:hunk"],
                "evidenceRefs": [],
                "visibleEvidenceIds": ["RAG-visible"],
                "visibleEvidenceFactDigests": {
                    "RAG-visible": [],
                },
                "terminalState": "published",
                "rejection": None,
            }
            for index, _issue_payload in enumerate(issues)
        ),
        key=lambda record: record["candidateId"],
    )
    pipeline_evidence = {
        "state": "review_evidence_completed",
        "hunkCoverage": {
            "ingested": 0,
            "planned": 0,
            "reviewed": 0,
            "validated": 0,
            "completed": reviewable_hunks,
            "excluded": 0,
        },
        "reviewUnits": {
            "registered": reviewable_hunks,
            "completed": reviewable_hunks,
        },
        "candidates": {
            "generated": len(candidate_records),
            "published": len(candidate_records),
            "rejected": 0,
            "rejectionCounts": {},
            "records": candidate_records,
        },
        "hunkReceipts": [
            {
                "hunkId": (
                    "sha256:hunk"
                    if hunk_index == 0
                    else f"sha256:hunk-{hunk_index:03d}"
                ),
                "path": "src/app.py",
                "promptCandidateIds": (
                    [
                        record["candidateId"]
                        for record in candidate_records
                    ]
                    if hunk_index == 0
                    else []
                ),
                "anchoredCandidateIds": (
                    [
                        record["candidateId"]
                        for record in candidate_records
                    ]
                    if hunk_index == 0
                    else []
                ),
                "publishedCandidateIds": (
                    [
                        record["candidateId"]
                        for record in candidate_records
                    ]
                    if hunk_index == 0
                    else []
                ),
                "rejectedCandidateIds": [],
                "outcome": (
                    "published"
                    if hunk_index == 0 and candidate_records
                    else "no_anchored_candidate"
                ),
            }
            for hunk_index in range(reviewable_hunks)
        ],
        "retrieval": {
            "deterministicStates": ["complete"],
            "semanticFailures": 0,
            "semanticDisabled": False,
            "exactEvidenceIds": reviewable_hunks,
        },
    }
    artifact = {
        "kind": "review-quality-candidate-capture",
        "status": "completed",
        "createdAt": "2026-07-25T00:00:00+00:00",
        "completedAt": "2026-07-25T00:01:00+00:00",
        "provider": "OPENAI",
        "model": "review-model",
        "pluginIdentity": plugin_identity,
        "reviewRuntimeFingerprint": f"runtime-{mode_identity}",
        "modeIdentity": mode_identity,
        "requestDigest": _digest(request),
        "request": request,
        "modelBoundaryInvocations": 1,
        "providerCalls": 1,
        "calls": [call],
        "pipelineEvidenceStatus": "complete",
        "pipelineEvidence": pipeline_evidence,
        "pipelineEvidenceDigest": _digest(pipeline_evidence),
        "result": result,
        "resultDigest": _digest(result),
        "captureDigest": None,
    }
    artifact["captureDigest"] = _digest(artifact)
    return artifact


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def _finding_label(issue, verdict, expected_id=None):
    return {
        "digest": _digest(issue),
        "verdict": verdict,
        "expectedId": expected_id,
    }


def _manifest(fallback_path, plugin_path, fallback_issues, plugin_issues):
    fallback_capture = json.loads(fallback_path.read_text(encoding="utf-8"))
    source_identity_digest = _digest({
        "projectId": 1752,
        "workspace": "merchant",
        "repository": "shop",
        "pullRequestId": 42,
        "sourceBranch": "feature",
        "targetBranch": "main",
        "baseCommit": "a" * 40,
        "headCommit": "b" * 40,
        "previousCommit": None,
        "analysisMode": "FULL",
        "rawDiffSha256": hashlib.sha256(
            fallback_capture["request"]["rawDiff"].encode("utf-8")
        ).hexdigest(),
        "deltaDiffSha256": None,
        "changedFiles": ["app/code/Vendor/Module/Model/Example.php"],
        "deletedFiles": [],
    })
    return {
        "baseline": "fallback",
        "cases": [{
            "caseId": "magento-pr-42",
            "languages": ["php"],
            "frameworks": ["magento"],
            "groundTruth": {
                "status": "complete",
                "scope": "complete-fixed-diff",
                "candidateOutputsHiddenDuringDefectInventory": True,
                "adjudicator": "reviewer",
                "adjudicatedAt": "2026-07-25T00:00:00Z",
                "method": "manual review of every changed hunk and related source",
                "sourceIdentityDigest": source_identity_digest,
                "expectedDefects": [
                    {
                        "id": "defect-a",
                        "file": "app/code/Vendor/Module/Model/Example.php",
                        "line": 10,
                        "summary": "First independently identified defect",
                    },
                    {
                        "id": "defect-b",
                        "file": "app/code/Vendor/Module/Model/Example.php",
                        "line": 30,
                        "summary": "Second independently identified defect",
                    },
                ],
            },
            "reviewableHunks": 4,
            "terminalHunks": 4,
            "changedLines": 1,
            "modes": [
                {
                    "mode": "fallback",
                    "capture": str(fallback_path),
                    "cost": 0.01,
                    "findings": [
                        _finding_label(fallback_issues[0], "TP", "defect-a"),
                        _finding_label(fallback_issues[1], "FP"),
                    ],
                },
                {
                    "mode": "php-magento",
                    "capture": str(plugin_path),
                    "cost": 0.012,
                    "findings": [
                        _finding_label(plugin_issues[0], "TP", "defect-a"),
                        _finding_label(plugin_issues[1], "TP", "defect-b"),
                    ],
                },
            ],
        }],
    }


def test_evaluates_integrity_checked_paired_full_pipeline_captures(tmp_path):
    fallback_issues = [_issue("A", 10), _issue("Noise", 20)]
    plugin_issues = [_issue("A, clearer", 10), _issue("B", 30)]
    fallback_path = tmp_path / "fallback.json"
    plugin_path = tmp_path / "plugin.json"
    _write(
        fallback_path,
        _capture(
            mode_identity="fallback-runtime",
            plugins=[],
            issues=fallback_issues,
        ),
    )
    _write(
        plugin_path,
        _capture(
            mode_identity="plugin-runtime",
            plugins=["json", "php", "magento"],
            issues=plugin_issues,
            input_tokens=12,
            output_tokens=3,
        ),
    )

    result = evaluate_capture_manifest(
        _manifest(fallback_path, plugin_path, fallback_issues, plugin_issues),
        base_dir=tmp_path,
    )

    assert result["kind"] == "review-quality-paired-capture-report"
    assert len(result["provenance"]) == 2
    assert {
        item["mode"]: item["repositoryPlugins"]
        for item in result["provenance"]
    } == {
        "fallback": [],
        "php-magento": ["json", "php", "magento"],
    }
    assert {
        item["sourceIdentityDigest"] for item in result["provenance"]
    } == {result["provenance"][0]["sourceIdentityDigest"]}
    assert {
        item["groundTruthDigest"] for item in result["provenance"]
    } == {result["provenance"][0]["groundTruthDigest"]}
    case_metrics = {
        (item["caseId"], item["mode"]): item
        for item in result["caseMetrics"]
    }
    assert case_metrics == {
        ("magento-pr-42", "fallback"): {
            "caseId": "magento-pr-42",
            "mode": "fallback",
            "repositoryPlugins": [],
            "languages": ["php"],
            "frameworks": ["magento"],
            "truePositives": 1,
            "falsePositives": 1,
            "falseNegatives": 1,
            "precision": 0.5,
            "recall": 0.5,
            "reviewableHunks": 4,
            "terminalHunks": 4,
            "changedLines": 1,
            "modelCalls": 1,
            "inputTokens": 10,
            "outputTokens": 2,
            "cost": 0.01,
            "costPerChangedKloc": 10.0,
        },
        ("magento-pr-42", "php-magento"): {
            "caseId": "magento-pr-42",
            "mode": "php-magento",
            "repositoryPlugins": ["json", "php", "magento"],
            "languages": ["php"],
            "frameworks": ["magento"],
            "truePositives": 2,
            "falsePositives": 0,
            "falseNegatives": 0,
            "precision": 1.0,
            "recall": 1.0,
            "reviewableHunks": 4,
            "terminalHunks": 4,
            "changedLines": 1,
            "modelCalls": 1,
            "inputTokens": 12,
            "outputTokens": 3,
            "cost": 0.012,
            "costPerChangedKloc": 12.0,
        },
    }
    metrics = {
        item["mode"]: item for item in result["report"]["modes"]
    }
    assert metrics["fallback"] == {
        "mode": "fallback",
        "cases": 1,
        "true_positives": 1,
        "false_positives": 1,
        "false_negatives": 1,
        "abstentions": 0,
        "precision": 0.5,
        "recall": 0.5,
        "coverage": 1.0,
        "changed_lines": 1,
        "model_calls": 1,
        "input_tokens": 10,
        "output_tokens": 2,
        "cost": 0.01,
        "cost_per_changed_kloc": 10.0,
        "median_cost_per_changed_kloc": 10.0,
        "p95_cost_per_changed_kloc": 10.0,
    }
    assert metrics["php-magento"]["true_positives"] == 2
    assert metrics["php-magento"]["false_positives"] == 0
    assert metrics["php-magento"]["false_negatives"] == 0
    assert metrics["php-magento"]["input_tokens"] == 12
    assert result["report"]["pairedDeltas"][0]["precision"] == 0.5
    assert result["report"]["pairedDeltas"][0]["recall"] == 0.5


def test_template_exposes_only_safe_finding_fields_and_requires_labels(tmp_path):
    issue = _issue("Private defect", 10)
    fallback_path = tmp_path / "fallback.json"
    plugin_path = tmp_path / "plugin.json"
    _write(
        fallback_path,
        _capture(
            mode_identity="fallback-runtime",
            plugins=[],
            issues=[issue],
            reviewable_hunks=1,
        ),
    )
    _write(
        plugin_path,
        _capture(
            mode_identity="plugin-runtime",
            plugins=["json", "php", "magento"],
            issues=[],
            reviewable_hunks=1,
        ),
    )

    template = create_template(
        case_id="case",
        languages=["php"],
        frameworks=["magento"],
        captures=[
            ("fallback", fallback_path),
            ("php-magento", plugin_path),
        ],
        baseline="fallback",
    )

    finding = template["cases"][0]["modes"][0]["findings"][0]
    assert set(finding) == {
        "digest",
        "file",
        "line",
        "title",
        "category",
        "severity",
        "verdict",
        "expectedId",
    }
    assert "private source line" not in json.dumps(template)
    assert template["cases"][0]["languages"] == ["php"]
    assert template["cases"][0]["changedLines"] == 1
    fallback_cost_evidence = template["cases"][0]["modes"][0]["costEvidence"]
    fallback_capture = json.loads(fallback_path.read_text(encoding="utf-8"))
    assert fallback_cost_evidence == {
        "status": "unverified",
        "currency": "USD",
        "source": "",
        "costUsd": None,
        "verifiedBy": "",
        "verifiedAt": "",
        "artifact": None,
        "responseCosts": [{
            "responseDigest": _digest(
                fallback_capture["calls"][0]["providerEvents"][0]["response"]
            ),
            "jsonPointer": "",
            "costUsd": None,
        }],
        "sourceDigest": None,
    }
    assert template["cases"][0]["groundTruth"] == {
        "status": "unreviewed",
        "scope": "complete-fixed-diff",
        "candidateOutputsHiddenDuringDefectInventory": False,
        "adjudicator": "",
        "adjudicatedAt": "",
        "method": "",
        "sourceIdentityDigest": _digest({
            "projectId": 1752,
            "workspace": "merchant",
            "repository": "shop",
            "pullRequestId": 42,
            "sourceBranch": "feature",
            "targetBranch": "main",
            "baseCommit": "a" * 40,
            "headCommit": "b" * 40,
            "previousCommit": None,
            "analysisMode": "FULL",
            "rawDiffSha256": hashlib.sha256(
                (
                    "diff --git a/example.php b/example.php\n+changed"
                ).encode("utf-8")
            ).hexdigest(),
            "deltaDiffSha256": None,
            "changedFiles": ["app/code/Vendor/Module/Model/Example.php"],
            "deletedFiles": [],
        }),
        "expectedDefects": [],
    }
    with pytest.raises(ValueError, match="ground truth is not complete"):
        evaluate_capture_manifest(template, base_dir=tmp_path)


def _certified_inventory():
    raw_diff = "diff --git a/example.php b/example.php\n+changed"
    return {
        "kind": "review-quality-ground-truth-inventory",
        "status": "complete",
        "scope": "complete-fixed-diff",
        "candidateOutputsHiddenDuringDefectInventory": True,
        "caseId": "neutral-case",
        "definitionDigest": "fixture-definition-digest",
        "baseCommit": "a" * 40,
        "headCommit": "b" * 40,
        "rawDiffSha256": hashlib.sha256(
            raw_diff.encode("utf-8")
        ).hexdigest(),
        "changedFiles": ["app/code/Vendor/Module/Model/Example.php"],
        "expectedDefects": [{
            "id": "defect-a",
            "file": "app/code/Vendor/Module/Model/Example.php",
            "line": 10,
            "summary": "Candidate-blind semantic regression",
            "evidenceFiles": ["app/code/Vendor/Module/Policy.php"],
        }],
        "certification": {
            "adjudicator": "independent reviewer",
            "adjudicatedAt": "2026-07-27T00:00:00Z",
            "method": "complete diff and related-source inspection",
        },
    }


def _unreviewed_template(tmp_path):
    fallback_path = tmp_path / "fallback.json"
    plugin_path = tmp_path / "plugin.json"
    _write(
        fallback_path,
        _capture(mode_identity="fallback", plugins=[], issues=[]),
    )
    _write(
        plugin_path,
        _capture(
            mode_identity="plugin",
            plugins=["php"],
            issues=[],
        ),
    )
    return create_template(
        case_id="neutral-case",
        languages=["php"],
        frameworks=[],
        captures=[
            ("fallback", fallback_path),
            ("plugin-context", plugin_path),
        ],
        baseline="fallback",
    )


def test_binds_candidate_blind_inventory_to_exact_capture_identity(tmp_path):
    template = _unreviewed_template(tmp_path)
    inventory = _certified_inventory()

    bound = bind_ground_truth(
        template,
        inventory,
        base_dir=tmp_path,
    )

    ground_truth = bound["cases"][0]["groundTruth"]
    assert ground_truth["status"] == "complete"
    assert ground_truth["candidateOutputsHiddenDuringDefectInventory"] is True
    assert ground_truth["adjudicator"] == "independent reviewer"
    assert ground_truth["inventoryDigest"] == _digest(inventory)
    assert ground_truth["expectedDefects"] == [{
        "id": "defect-a",
        "file": "app/code/Vendor/Module/Model/Example.php",
        "line": 10,
        "summary": "Candidate-blind semantic regression",
        "evidenceFiles": ["app/code/Vendor/Module/Policy.php"],
    }]
    assert template["cases"][0]["groundTruth"]["status"] == "unreviewed"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("baseCommit", "c" * 40, "baseCommit"),
        ("headCommit", "c" * 40, "headCommit"),
        ("rawDiffSha256", "c" * 64, "rawDiffSha256"),
        ("changedFiles", ["other.py"], "changedFiles"),
    ],
)
def test_ground_truth_binding_rejects_snapshot_drift(
    tmp_path,
    field,
    value,
    message,
):
    template = _unreviewed_template(tmp_path)
    inventory = _certified_inventory()
    inventory[field] = value
    if field == "changedFiles":
        inventory["expectedDefects"] = []

    with pytest.raises(ValueError, match=message):
        bind_ground_truth(template, inventory, base_dir=tmp_path)


def test_ground_truth_binding_rejects_uncertified_or_preedited_input(tmp_path):
    template = _unreviewed_template(tmp_path)
    inventory = _certified_inventory()
    inventory["status"] = "draft-pending-independent-certification"

    with pytest.raises(ValueError, match="independently certified"):
        bind_ground_truth(template, inventory, base_dir=tmp_path)

    inventory["status"] = "complete"
    template["cases"][0]["groundTruth"]["status"] = "complete"
    with pytest.raises(ValueError, match="already edited"):
        bind_ground_truth(template, inventory, base_dir=tmp_path)


def test_merges_distinct_case_templates_with_one_baseline():
    first = {
        "baseline": "fallback",
        "cases": [{"caseId": "python-case"}],
    }
    second = {
        "baseline": "fallback",
        "cases": [{"caseId": "java-case"}],
    }

    merged = merge_templates([first, second])

    assert merged == {
        "baseline": "fallback",
        "cases": [
            {"caseId": "python-case"},
            {"caseId": "java-case"},
        ],
    }

    with pytest.raises(ValueError, match="changes baseline"):
        merge_templates([
            first,
            {"baseline": "generic", "cases": [{"caseId": "other"}]},
        ])
    with pytest.raises(ValueError, match="duplicate caseId"):
        merge_templates([first, copy.deepcopy(first)])


def test_binds_cost_evidence_without_claiming_human_verification(tmp_path):
    billing = tmp_path / "billing.json"
    billing.write_text('{"cost":0.25}', encoding="utf-8")
    response_costs = [{
        "responseDigest": "a" * 64,
        "jsonPointer": "/usage/cost",
        "costUsd": 0.25,
    }]
    manifest = {
        "baseline": "fallback",
        "cases": [{
            "caseId": "case",
            "modes": [
                {
                    "mode": "fallback",
                    "cost": 0.25,
                    "costEvidence": {
                        "status": "unverified",
                        "source": "provider-billing",
                        "artifact": billing.name,
                    },
                },
                {
                    "mode": "plugin-context",
                    "cost": 0.25,
                    "costEvidence": {
                        "status": "unverified",
                        "source": "provider-response",
                        "responseCosts": response_costs,
                    },
                },
            ],
        }],
    }

    bound = bind_cost_evidence(manifest, base_dir=tmp_path)

    fallback = bound["cases"][0]["modes"][0]["costEvidence"]
    candidate = bound["cases"][0]["modes"][1]["costEvidence"]
    assert fallback["costUsd"] == 0.25
    assert fallback["sourceDigest"] == hashlib.sha256(
        billing.read_bytes()
    ).hexdigest()
    assert candidate["costUsd"] == 0.25
    assert candidate["sourceDigest"] == _digest(response_costs)
    assert fallback["status"] == "unverified"
    assert candidate["status"] == "unverified"
    assert manifest["cases"][0]["modes"][0]["costEvidence"].get(
        "sourceDigest"
    ) is None


def test_rejects_candidate_derived_or_unbound_ground_truth(tmp_path):
    fallback_issues = [_issue("A", 10), _issue("Noise", 20)]
    plugin_issues = [_issue("A, clearer", 10), _issue("B", 30)]
    fallback_path = tmp_path / "fallback.json"
    plugin_path = tmp_path / "plugin.json"
    _write(
        fallback_path,
        _capture(
            mode_identity="fallback-runtime",
            plugins=[],
            issues=fallback_issues,
        ),
    )
    _write(
        plugin_path,
        _capture(
            mode_identity="plugin-runtime",
            plugins=["json", "php", "magento"],
            issues=plugin_issues,
        ),
    )
    manifest = _manifest(
        fallback_path,
        plugin_path,
        fallback_issues,
        plugin_issues,
    )
    manifest["cases"][0]["groundTruth"][
        "candidateOutputsHiddenDuringDefectInventory"
    ] = False

    with pytest.raises(ValueError, match="inventoried independently"):
        evaluate_capture_manifest(manifest, base_dir=tmp_path)

    manifest["cases"][0]["groundTruth"][
        "candidateOutputsHiddenDuringDefectInventory"
    ] = True
    manifest["cases"][0]["groundTruth"]["sourceIdentityDigest"] = "wrong"
    with pytest.raises(ValueError, match="not bound to the captured immutable PR"):
        evaluate_capture_manifest(manifest, base_dir=tmp_path)


def test_rejects_expected_defect_outside_fixed_diff(tmp_path):
    fallback_issues = [_issue("A", 10), _issue("Noise", 20)]
    plugin_issues = [_issue("A, clearer", 10), _issue("B", 30)]
    fallback_path = tmp_path / "fallback.json"
    plugin_path = tmp_path / "plugin.json"
    _write(
        fallback_path,
        _capture(
            mode_identity="fallback-runtime",
            plugins=[],
            issues=fallback_issues,
        ),
    )
    _write(
        plugin_path,
        _capture(
            mode_identity="plugin-runtime",
            plugins=["json", "php", "magento"],
            issues=plugin_issues,
        ),
    )
    manifest = _manifest(
        fallback_path,
        plugin_path,
        fallback_issues,
        plugin_issues,
    )
    manifest["cases"][0]["groundTruth"]["expectedDefects"][0][
        "file"
    ] = "app/code/Vendor/Module/Model/Unchanged.php"

    with pytest.raises(ValueError, match="outside the fixed diff"):
        evaluate_capture_manifest(manifest, base_dir=tmp_path)


def test_rejects_capture_pair_from_different_immutable_source(tmp_path):
    fallback_issues = [_issue("A", 10), _issue("Noise", 20)]
    plugin_issues = [_issue("A, clearer", 10), _issue("B", 30)]
    fallback_path = tmp_path / "fallback.json"
    plugin_path = tmp_path / "plugin.json"
    _write(
        fallback_path,
        _capture(
            mode_identity="fallback-runtime",
            plugins=[],
            issues=fallback_issues,
        ),
    )
    _write(
        plugin_path,
        _capture(
            mode_identity="plugin-runtime",
            plugins=["json", "php", "magento"],
            issues=plugin_issues,
            head="c" * 40,
        ),
    )

    with pytest.raises(ValueError, match="not the same immutable PR"):
        evaluate_capture_manifest(
            _manifest(fallback_path, plugin_path, fallback_issues, plugin_issues),
            base_dir=tmp_path,
        )


def test_rejects_pair_that_did_not_change_review_or_plugin_runtime(tmp_path):
    fallback_issues = [_issue("A", 10), _issue("Noise", 20)]
    plugin_issues = [_issue("A, clearer", 10), _issue("B", 30)]
    fallback_path = tmp_path / "fallback.json"
    plugin_path = tmp_path / "plugin.json"
    _write(
        fallback_path,
        _capture(
            mode_identity="same-runtime",
            plugins=[],
            issues=fallback_issues,
        ),
    )
    _write(
        plugin_path,
        _capture(
            mode_identity="same-runtime",
            plugins=["json", "php", "magento"],
            issues=plugin_issues,
        ),
    )

    with pytest.raises(ValueError, match="distinct review/plugin runtimes"):
        evaluate_capture_manifest(
            _manifest(fallback_path, plugin_path, fallback_issues, plugin_issues),
            base_dir=tmp_path,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda capture: capture["request"].update({"rawDiff": "tampered"}),
            "request digest mismatch",
        ),
        (
            lambda capture: capture["result"]["result"]["issues"][0].update(
                {"title": "tampered"}
            ),
            "result digest mismatch",
        ),
        (
            lambda capture: capture.update({"createdAt": "tampered"}),
            "capture digest mismatch",
        ),
        (
            lambda capture: capture["pipelineEvidence"]["hunkCoverage"].update(
                {"completed": 3}
            ),
            "terminal pipeline evidence digest mismatch",
        ),
    ],
)
def test_rejects_tampered_capture(tmp_path, mutation, message):
    fallback_issues = [_issue("A", 10), _issue("Noise", 20)]
    plugin_issues = [_issue("A, clearer", 10), _issue("B", 30)]
    fallback = _capture(
        mode_identity="fallback-runtime",
        plugins=[],
        issues=fallback_issues,
    )
    plugin = _capture(
        mode_identity="plugin-runtime",
        plugins=["json", "php", "magento"],
        issues=plugin_issues,
    )
    mutation(plugin)
    fallback_path = tmp_path / "fallback.json"
    plugin_path = tmp_path / "plugin.json"
    _write(fallback_path, fallback)
    _write(plugin_path, plugin)

    with pytest.raises(ValueError, match=message):
        evaluate_capture_manifest(
            _manifest(fallback_path, plugin_path, fallback_issues, plugin_issues),
            base_dir=tmp_path,
        )


def test_rejects_missing_actual_token_usage(tmp_path):
    fallback_issues = [_issue("A", 10), _issue("Noise", 20)]
    plugin_issues = [_issue("A, clearer", 10), _issue("B", 30)]
    fallback = _capture(
        mode_identity="fallback-runtime",
        plugins=[],
        issues=fallback_issues,
    )
    plugin = _capture(
        mode_identity="plugin-runtime",
        plugins=["json", "php", "magento"],
        issues=plugin_issues,
    )
    plugin["calls"][0]["providerEvents"][0]["response"] = {"generations": [[{}]]}
    plugin["captureDigest"] = None
    plugin["captureDigest"] = _digest(plugin)
    fallback_path = tmp_path / "fallback.json"
    plugin_path = tmp_path / "plugin.json"
    _write(fallback_path, fallback)
    _write(plugin_path, plugin)

    with pytest.raises(ValueError, match="no actual token usage"):
        evaluate_capture_manifest(
            _manifest(fallback_path, plugin_path, fallback_issues, plugin_issues),
            base_dir=tmp_path,
        )


def test_rejects_unresolved_plugin_identity(tmp_path):
    capture = _capture(
        mode_identity="fallback-runtime",
        plugins=[],
        issues=[],
    )
    capture["pluginIdentity"] = {
        "status": "fallback-unresolved",
        "repositoryPlugins": [],
    }
    capture["captureDigest"] = None
    capture["captureDigest"] = _digest(capture)
    path = tmp_path / "capture.json"
    _write(path, capture)

    with pytest.raises(ValueError, match="explicit empty selection"):
        create_template(
            case_id="case",
            languages=["php"],
            frameworks=["magento"],
            captures=[("fallback", path), ("other", path)],
            baseline="fallback",
        )


def test_rejects_declared_changed_lines_that_do_not_match_capture(tmp_path):
    fallback_issues = [_issue("A", 10), _issue("Noise", 20)]
    plugin_issues = [_issue("A", 10), _issue("B", 30)]
    fallback_path = tmp_path / "fallback.json"
    plugin_path = tmp_path / "plugin.json"
    _write(
        fallback_path,
        _capture(
            mode_identity="fallback-runtime",
            plugins=[],
            issues=fallback_issues,
        ),
    )
    _write(
        plugin_path,
        _capture(
            mode_identity="plugin-runtime",
            plugins=["python"],
            issues=plugin_issues,
        ),
    )
    manifest = _manifest(
        fallback_path,
        plugin_path,
        fallback_issues,
        plugin_issues,
    )
    manifest["cases"][0]["changedLines"] = 2

    with pytest.raises(ValueError, match="does not match"):
        evaluate_capture_manifest(manifest, base_dir=tmp_path)


def test_rejects_declared_hunk_coverage_that_does_not_match_capture(tmp_path):
    fallback_issues = [_issue("A", 10), _issue("Noise", 20)]
    plugin_issues = [_issue("A", 10), _issue("B", 30)]
    fallback_path = tmp_path / "fallback.json"
    plugin_path = tmp_path / "plugin.json"
    _write(
        fallback_path,
        _capture(
            mode_identity="fallback-runtime",
            plugins=[],
            issues=fallback_issues,
        ),
    )
    _write(
        plugin_path,
        _capture(
            mode_identity="plugin-runtime",
            plugins=["python"],
            issues=plugin_issues,
        ),
    )
    manifest = _manifest(
        fallback_path,
        plugin_path,
        fallback_issues,
        plugin_issues,
    )
    manifest["cases"][0]["terminalHunks"] = 3

    with pytest.raises(ValueError, match="does not match terminal capture evidence"):
        evaluate_capture_manifest(manifest, base_dir=tmp_path)


def test_rejects_capture_with_degraded_terminal_retrieval_evidence(tmp_path):
    issue = _issue("A", 10)
    fallback = _capture(
        mode_identity="fallback-runtime",
        plugins=[],
        issues=[issue],
    )
    plugin = _capture(
        mode_identity="plugin-runtime",
        plugins=["python"],
        issues=[issue],
    )
    plugin["pipelineEvidence"]["retrieval"]["deterministicStates"] = ["failed"]
    plugin["pipelineEvidenceDigest"] = _digest(plugin["pipelineEvidence"])
    plugin["captureDigest"] = None
    plugin["captureDigest"] = _digest(plugin)
    fallback_path = tmp_path / "fallback.json"
    plugin_path = tmp_path / "plugin.json"
    _write(fallback_path, fallback)
    _write(plugin_path, plugin)

    with pytest.raises(ValueError, match="retrieval evidence is incomplete"):
        create_template(
            case_id="case",
            languages=["python"],
            frameworks=[],
            captures=[
                ("fallback", fallback_path),
                ("plugin-context", plugin_path),
            ],
            baseline="fallback",
        )


def test_rejects_capture_with_incomplete_terminal_candidate_evidence(tmp_path):
    issue = _issue("A", 10)
    fallback = _capture(
        mode_identity="fallback-runtime",
        plugins=[],
        issues=[issue],
    )
    plugin = _capture(
        mode_identity="plugin-runtime",
        plugins=["python"],
        issues=[issue],
    )
    plugin["pipelineEvidence"]["candidates"]["published"] = 0
    plugin["pipelineEvidenceDigest"] = _digest(plugin["pipelineEvidence"])
    plugin["captureDigest"] = None
    plugin["captureDigest"] = _digest(plugin)
    fallback_path = tmp_path / "fallback.json"
    plugin_path = tmp_path / "plugin.json"
    _write(fallback_path, fallback)
    _write(plugin_path, plugin)

    with pytest.raises(ValueError, match="candidate evidence is incomplete"):
        create_template(
            case_id="case",
            languages=["python"],
            frameworks=[],
            captures=[
                ("fallback", fallback_path),
                ("plugin-context", plugin_path),
            ],
            baseline="fallback",
        )


def test_rejects_capture_with_missing_per_hunk_receipt(tmp_path):
    issue = _issue("A", 10)
    fallback = _capture(
        mode_identity="fallback-runtime",
        plugins=[],
        issues=[issue],
    )
    plugin = _capture(
        mode_identity="plugin-runtime",
        plugins=["python"],
        issues=[issue],
    )
    plugin["pipelineEvidence"]["hunkReceipts"].pop()
    plugin["pipelineEvidenceDigest"] = _digest(plugin["pipelineEvidence"])
    plugin["captureDigest"] = None
    plugin["captureDigest"] = _digest(plugin)
    fallback_path = tmp_path / "fallback.json"
    plugin_path = tmp_path / "plugin.json"
    _write(fallback_path, fallback)
    _write(plugin_path, plugin)

    with pytest.raises(ValueError, match="hunk receipts"):
        create_template(
            case_id="case",
            languages=["python"],
            frameworks=[],
            captures=[
                ("fallback", fallback_path),
                ("plugin-context", plugin_path),
            ],
            baseline="fallback",
        )


def test_rejects_published_candidate_using_evidence_from_another_prompt(tmp_path):
    issue = _issue("A", 10)
    fallback = _capture(
        mode_identity="fallback-runtime",
        plugins=[],
        issues=[issue],
    )
    plugin = _capture(
        mode_identity="plugin-runtime",
        plugins=["python"],
        issues=[issue],
    )
    record = plugin["pipelineEvidence"]["candidates"]["records"][0]
    record["evidenceRefs"] = ["RAG-not-visible"]
    plugin["pipelineEvidenceDigest"] = _digest(plugin["pipelineEvidence"])
    plugin["captureDigest"] = None
    plugin["captureDigest"] = _digest(plugin)
    fallback_path = tmp_path / "fallback.json"
    plugin_path = tmp_path / "plugin.json"
    _write(fallback_path, fallback)
    _write(plugin_path, plugin)

    with pytest.raises(ValueError, match="outside its generation prompt"):
        create_template(
            case_id="case",
            languages=["python"],
            frameworks=[],
            captures=[
                ("fallback", fallback_path),
                ("plugin-context", plugin_path),
            ],
            baseline="fallback",
        )


def test_rejects_candidate_not_bound_to_captured_prompt(tmp_path):
    issue = _issue("A", 10)
    fallback = _capture(
        mode_identity="fallback-runtime",
        plugins=[],
        issues=[issue],
    )
    plugin = _capture(
        mode_identity="plugin-runtime",
        plugins=["python"],
        issues=[issue],
    )
    record = plugin["pipelineEvidence"]["candidates"]["records"][0]
    record["generationPromptDigest"] = "sha256:" + "f" * 64
    plugin["pipelineEvidenceDigest"] = _digest(plugin["pipelineEvidence"])
    plugin["captureDigest"] = None
    plugin["captureDigest"] = _digest(plugin)
    fallback_path = tmp_path / "fallback.json"
    plugin_path = tmp_path / "plugin.json"
    _write(fallback_path, fallback)
    _write(plugin_path, plugin)

    with pytest.raises(ValueError, match="not bound to a captured prompt"):
        create_template(
            case_id="case",
            languages=["python"],
            frameworks=[],
            captures=[
                ("fallback", fallback_path),
                ("plugin-context", plugin_path),
            ],
            baseline="fallback",
        )
