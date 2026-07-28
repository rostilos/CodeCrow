import pytest

from tools.review_quality.isolated_deployed_replay import (
    build_review_request,
    build_synthetic_repository,
)
from tools.review_quality.isolated_paired_capture_preflight import (
    audit_paired_requests,
)


def _pair(tmp_path):
    repository = build_synthetic_repository(tmp_path)
    candidate = build_review_request(
        repository,
        project_namespace="neutral-paired-preflight",
        dry_run_id="candidate",
    ).model_copy(update={"aiApiKey": "dry-run-provider-disabled"})
    candidate_capabilities = candidate.projectCapabilities
    baseline = candidate.model_copy(update={
        "promptDryRunId": "baseline",
        "projectCapabilities": candidate_capabilities.model_copy(update={
            "repositoryPlugins": [],
            "filePlugins": {},
            "detectionEvidence": {},
            "fingerprint": "sha256:" + "1" * 64,
            "descriptorFingerprint": "sha256:" + "2" * 64,
        }),
    })
    return baseline, candidate


def test_accepts_same_disconnected_snapshot_with_distinct_plugin_identity(
    tmp_path,
):
    baseline, candidate = _pair(tmp_path)

    result = audit_paired_requests(baseline, candidate)

    assert result["status"] == "passed"
    assert result["reviewProviderCalls"] == 0
    assert result["embeddingProviderCalls"] == 0
    assert result["modes"]["fallback"]["repositoryPlugins"] == []
    assert result["modes"]["plugin-context"]["repositoryPlugins"] == [
        "java",
        "python",
        "typescript",
    ]
    assert "rawDiff" not in result["sourceIdentity"]
    assert "enrichmentData" not in result["sourceIdentity"]


def test_rejects_source_drift_or_credential_presence(tmp_path):
    baseline, candidate = _pair(tmp_path)

    with pytest.raises(RuntimeError, match="source identity drift"):
        audit_paired_requests(
            baseline,
            candidate.model_copy(update={"sourceBranchName": "other"}),
        )

    with pytest.raises(RuntimeError, match="review credential"):
        audit_paired_requests(
            baseline.model_copy(update={"aiApiKey": "real-key"}),
            candidate,
        )
