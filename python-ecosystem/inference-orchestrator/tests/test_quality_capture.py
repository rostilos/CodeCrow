import asyncio
import json
import os

import pytest

from model.dtos import ReviewRequestDto
from service.review.orchestrator.json_utils import supports_structured_output
from service.review import quality_capture
from service.review.quality_capture import (
    ReviewQualityCaptureLLM,
    create_quality_capture_session,
    review_response_indicates_failure,
)
from service.review.review_service import ReviewService


def _request(**updates):
    values = {
        "projectId": 42,
        "projectVcsWorkspace": "workspace",
        "projectVcsRepoSlug": "repository",
        "projectWorkspace": "workspace",
        "projectNamespace": "repository",
        "aiProvider": "OPENAI",
        "aiModel": "review-model",
        "aiApiKey": "provider-secret",
        "aiBaseUrl": "https://user:password@example.test/llm?secret=query",
        "aiCustomParameters": {
            "temperature": 0.2,
            "default_headers": {
                "Authorization": "Bearer header-secret",
                "X-Trace": "trace-value",
            },
        },
        "oAuthClient": "oauth-client-secret",
        "oAuthSecret": "oauth-secret",
        "accessToken": "vcs-secret",
        "pullRequestId": 12,
        "sourceBranchName": "feature",
        "targetBranchName": "main",
        "baseCommitHash": "a" * 40,
        "currentCommitHash": "b" * 40,
        "rawDiff": "diff --git a/private.py b/private.py\n+proprietary_source = True",
        "changedFiles": ["private.py"],
    }
    values.update(updates)
    return ReviewRequestDto(**values)


def _revision_binding():
    return {
        "prIndexed": True,
        "pullRequestId": 12,
        "targetBranch": "main",
        "sourceRevision": "b" * 40,
        "baseRevision": "a" * 40,
        "baseGenerationManifestSha256": "c" * 64,
        "prGenerationFingerprint": "sha256:" + "d" * 64,
        "prOverlayGenerationManifestSha256": "e" * 64,
        "basePluginFingerprint": "sha256:" + "1" * 64,
        "basePluginDescriptorFingerprint": "sha256:" + "2" * 64,
        "basePluginImplementationFingerprint": "sha256:" + "3" * 64,
        "baseIndexRepresentationFingerprint": "sha256:" + "4" * 64,
    }


class _FakeDelegate:
    def __init__(self):
        self.calls = []
        self.model_kwargs = {}
        self.max_tokens = None

    def model_copy(self, update=None, **kwargs):
        self.model_kwargs.update((update or {}).get("model_kwargs", {}))
        self.max_tokens = (update or {}).get("max_tokens", self.max_tokens)
        return self

    def copy(self, update=None, **kwargs):
        return self.model_copy(update=update, **kwargs)

    def bind(self, **kwargs):
        return self

    def with_structured_output(self, schema, include_raw=False, **kwargs):
        return self

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, input_data, **kwargs):
        self.calls.append((input_data, kwargs))
        for callback in kwargs.get("config", {}).get("callbacks", []):
            callback.on_llm_end({
                "generations": [[{
                    "text": '{"answer":"captured"}',
                    "usage_metadata": {
                        "input_tokens": 10,
                        "output_tokens": 2,
                    },
                }]],
                "llm_output": {
                    "model_name": "provider-resolved-review-model",
                },
            })
        return {
            "answer": "captured",
            "usage_metadata": {
                "input_tokens": 10,
                "output_tokens": 2,
            },
        }


@pytest.fixture
def capture_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("REVIEW_QUALITY_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("REVIEW_QUALITY_CAPTURE_PROJECT_IDS", "42")
    monkeypatch.setenv("REVIEW_QUALITY_CAPTURE_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REVIEW_QUALITY_CAPTURE_MAX_FILES", "3")
    return tmp_path


def test_capture_requires_explicit_project_allowlist(monkeypatch):
    monkeypatch.setenv("REVIEW_QUALITY_CAPTURE_ENABLED", "true")
    monkeypatch.delenv("REVIEW_QUALITY_CAPTURE_PROJECT_IDS", raising=False)

    with pytest.raises(ValueError, match="non-empty"):
        create_quality_capture_session(_request())


def test_non_allowlisted_project_is_not_captured(capture_environment):
    assert create_quality_capture_session(_request(projectId=43)) is None
    assert list(capture_environment.iterdir()) == []


def test_provider_model_identity_uses_metadata_not_tool_arguments():
    response = {
        "llm_output": {"model_name": "provider/resolved"},
        "generations": [[{
            "message": {
                "tool_calls": [{
                    "args": {"model": "domain-object-name"},
                }],
                "response_metadata": {
                    "model": "provider/resolved",
                },
            },
        }]],
    }

    assert quality_capture._provider_reported_models(response) == [
        "provider/resolved"
    ]


@pytest.mark.asyncio
async def test_capture_records_exact_model_boundary_and_redacts_credentials(
    capture_environment,
):
    session = create_quality_capture_session(_request())
    delegate = _FakeDelegate()
    llm = ReviewQualityCaptureLLM(delegate, session)
    forwarded_events = []
    event_callback = session.wrap_event_callback(forwarded_events.append)

    capped = llm.model_copy(update={"max_tokens": 321})
    structured = capped.with_structured_output(
        dict,
        include_raw=True,
        method="json_schema",
        strict=True,
    )
    result = await structured.ainvoke("Review proprietary_source safely")
    event_callback({
        "type": "status",
        "state": "review_evidence_completed",
        "hunkCoverage": {
            "ingested": 0,
            "planned": 0,
            "reviewed": 0,
            "validated": 0,
            "completed": 1,
            "excluded": 0,
        },
        "reviewUnits": {"registered": 1, "completed": 1},
        "candidates": {
            "generated": 0,
            "published": 0,
            "rejected": 0,
            "rejectionCounts": {},
            "records": [],
        },
        "hunkReceipts": [{
            "hunkId": "sha256:hunk",
            "path": "src/example.py",
            "promptCandidateIds": [],
            "anchoredCandidateIds": [],
            "publishedCandidateIds": [],
            "rejectedCandidateIds": [],
            "outcome": "no_anchored_candidate",
        }],
        "retrieval": {
            "deterministicStates": ["complete"],
            "semanticFailures": 0,
            "semanticDisabled": False,
            "exactEvidenceIds": 2,
        },
        "revisionBinding": _revision_binding(),
    })
    response = {"result": {"issues": [{"file": "private.py", "title": "Example"}]}}
    await session.complete(response)

    assert result["answer"] == "captured"
    assert len(delegate.calls) == 1
    artifact = json.loads(session.path.read_text(encoding="utf-8"))
    serialized = session.path.read_text(encoding="utf-8")

    assert artifact["kind"] == "review-quality-candidate-capture"
    assert artifact["status"] == "completed"
    assert artifact["providerCalls"] == 1
    assert artifact["pipelineEvidenceStatus"] == "complete"
    assert artifact["pipelineEvidence"]["hunkCoverage"]["completed"] == 1
    assert artifact["pipelineEvidence"]["reviewUnits"] == {
        "registered": 1,
        "completed": 1,
    }
    assert artifact["pipelineEvidence"]["revisionBinding"] == (
        _revision_binding()
    )
    assert artifact["pipelineEvidenceDigest"]
    assert forwarded_events[0]["state"] == "review_evidence_completed"
    assert artifact["calls"][0]["status"] == "completed"
    assert artifact["calls"][0]["renderedPrompt"] == (
        "Review proprietary_source safely"
    )
    assert artifact["calls"][0]["modelBindings"]["max_tokens"] == 321
    assert artifact["calls"][0]["modelBindings"]["structured_output"] == {
        "include_raw": True,
        "options": {
            "method": "json_schema",
            "strict": True,
        },
    }
    assert artifact["calls"][0]["response"]["answer"] == "captured"
    assert artifact["calls"][0]["providerCallCountSource"] == "callback"
    assert artifact["calls"][0]["providerEvents"][0]["response"]["generations"]
    assert artifact["calls"][0]["providerEvents"][0][
        "providerReportedModels"
    ] == ["provider-resolved-review-model"]
    assert artifact["request"]["rawDiff"].endswith("proprietary_source = True")
    assert artifact["request"]["aiCustomParameters"]["temperature"] == 0.2
    assert artifact["request"]["aiCustomParameters"]["default_headers"]["X-Trace"] == (
        "trace-value"
    )
    assert artifact["request"]["aiApiKey"] == "[REDACTED]"
    assert artifact["request"]["aiBaseUrl"] == "https://example.test/llm"
    assert artifact["pluginIdentity"] == {
        "status": "fallback-unresolved",
        "repositoryPlugins": [],
        "selectionFingerprint": None,
        "requestDescriptorFingerprint": None,
        "runtimeDescriptorFingerprint": None,
        "implementationFingerprint": None,
        "descriptorMatch": None,
    }
    assert len(artifact["reviewRuntimeFingerprint"]) == 64
    assert len(artifact["modeIdentity"]) == 64
    assert artifact["captureDigest"]
    assert artifact["resultDigest"]
    receipt = session.receipt()
    assert receipt["requestedModel"] == "review-model"
    assert receipt["providerReportedModels"] == [
        "provider-resolved-review-model"
    ]
    assert receipt["providerModelEvidenceComplete"] is True
    assert receipt["calls"][0]["stage"]
    assert receipt["receiptDigest"]
    assert "provider-secret" not in serialized
    assert "header-secret" not in serialized
    assert "oauth-secret" not in serialized
    assert "vcs-secret" not in serialized
    assert (os.stat(session.path).st_mode & 0o777) == 0o600


@pytest.mark.asyncio
async def test_capture_records_tool_binding_options(capture_environment):
    session = create_quality_capture_session(_request())
    delegate = _FakeDelegate()
    llm = ReviewQualityCaptureLLM(delegate, session)

    bound = llm.bind_tools(
        [{"name": "lookup", "description": "Read source evidence"}],
        tool_choice="required",
        strict=True,
    )
    await bound.ainvoke("Use the declared evidence tool")
    await session.complete({"result": {"issues": []}})

    artifact = json.loads(session.path.read_text(encoding="utf-8"))
    assert artifact["calls"][0]["modelBindings"]["tool_binding"] == {
        "options": {
            "strict": True,
            "tool_choice": "required",
        },
    }
    assert artifact["calls"][0]["tools"] == [
        {
            "description": "Read source evidence",
            "name": "lookup",
        },
    ]


@pytest.mark.asyncio
async def test_capture_marks_missing_or_invalid_terminal_pipeline_evidence(
    capture_environment,
):
    missing = create_quality_capture_session(_request())
    await missing.complete({"result": {"issues": []}})
    missing_artifact = json.loads(missing.path.read_text(encoding="utf-8"))
    assert missing_artifact["status"] == "completed"
    assert missing_artifact["pipelineEvidenceStatus"] == "missing"
    assert missing_artifact["pipelineEvidence"] is None

    invalid = create_quality_capture_session(_request())
    invalid.observe_pipeline_event({
        "state": "review_evidence_completed",
        "hunkCoverage": {
            "ingested": 1,
            "planned": 0,
            "reviewed": 0,
            "validated": 0,
            "completed": 0,
            "excluded": 0,
        },
        "reviewUnits": {"registered": 1, "completed": 0},
        "candidates": {
            "generated": 0,
            "published": 0,
            "rejected": 0,
            "rejectionCounts": {},
            "records": [],
        },
        "hunkReceipts": [],
        "retrieval": {
            "deterministicStates": [],
            "semanticFailures": 0,
            "semanticDisabled": True,
            "exactEvidenceIds": 0,
        },
    })
    await invalid.complete({"result": {"issues": []}})
    invalid_artifact = json.loads(invalid.path.read_text(encoding="utf-8"))
    assert invalid_artifact["status"] == "completed"
    assert invalid_artifact["pipelineEvidenceStatus"] == "invalid"
    assert "non-terminal hunk states" in invalid_artifact["pipelineEvidenceError"]

    degraded = create_quality_capture_session(_request())
    degraded.observe_pipeline_event({
        "state": "review_evidence_completed",
        "hunkCoverage": {
            "ingested": 0,
            "planned": 0,
            "reviewed": 0,
            "validated": 0,
            "completed": 1,
            "excluded": 0,
        },
        "reviewUnits": {"registered": 1, "completed": 1},
        "candidates": {
            "generated": 0,
            "published": 0,
            "rejected": 0,
            "rejectionCounts": {},
            "records": [],
        },
        "hunkReceipts": [{
            "hunkId": "sha256:hunk",
            "path": "src/example.py",
            "promptCandidateIds": [],
            "anchoredCandidateIds": [],
            "publishedCandidateIds": [],
            "rejectedCandidateIds": [],
            "outcome": "no_anchored_candidate",
        }],
        "retrieval": {
            "deterministicStates": ["failed"],
            "semanticFailures": 1,
            "semanticDisabled": True,
            "exactEvidenceIds": 0,
        },
    })
    await degraded.complete({"result": {"issues": []}})
    degraded_artifact = json.loads(degraded.path.read_text(encoding="utf-8"))
    assert degraded_artifact["pipelineEvidenceStatus"] == "invalid"
    assert (
        "incomplete deterministic retrieval states"
        in degraded_artifact["pipelineEvidenceError"]
    )


@pytest.mark.asyncio
async def test_review_service_routes_pipeline_events_through_capture_session(
    monkeypatch,
):
    observed = []
    forwarded = []
    completed = []

    class CaptureSpy:
        def wrap_event_callback(self, callback):
            def wrapped(event):
                observed.append(event)
                if callback is not None:
                    callback(event)

            return wrapped

        async def complete(self, response, error=None, *, failed=False):
            completed.append((response, error, failed))

        def receipt(self):
            return {
                "kind": "review-quality-capture-receipt",
                "receiptDigest": "a" * 64,
            }

    capture = CaptureSpy()

    async def fake_review(
        _self,
        *,
        request,
        repo_path,
        event_callback,
        quality_capture,
    ):
        assert quality_capture is capture
        event_callback({"state": "review_evidence_completed"})
        return {"result": {"issues": []}}

    monkeypatch.setattr(
        "service.review.review_service.create_quality_capture_session",
        lambda _request: capture,
    )
    monkeypatch.setattr(ReviewService, "_process_review", fake_review)
    service = ReviewService.__new__(ReviewService)
    service._review_semaphore = asyncio.Semaphore(1)

    response = await service.process_review_request(
        _request(),
        event_callback=forwarded.append,
    )

    assert response == {"result": {"issues": []}}
    assert observed == [{"state": "review_evidence_completed"}]
    assert forwarded == [
        {"state": "review_evidence_completed"},
        {
            "type": "status",
            "state": "review_quality_capture_completed",
            "qualityCapture": {
                "kind": "review-quality-capture-receipt",
                "receiptDigest": "a" * 64,
            },
        },
    ]
    assert completed == [(response, None, False)]


def test_capture_records_resolved_runtime_plugin_identity(
    capture_environment,
):
    catalog = quality_capture._runtime_plugin_catalog()
    plugin_ids = ("json",)
    descriptor_fingerprint = catalog.registry.fingerprint_for(plugin_ids)
    request = _request(
        projectCapabilities={
            "repositoryPlugins": list(plugin_ids),
            "filePlugins": {"composer.json": ["json"]},
            "detectionEvidence": {},
            "unavailableCapabilities": [],
            "fingerprint": "sha256:" + "1" * 64,
            "descriptorFingerprint": descriptor_fingerprint,
        },
    )

    session = create_quality_capture_session(request)
    artifact = json.loads(session.path.read_text(encoding="utf-8"))

    assert artifact["pluginIdentity"] == {
        "status": "resolved",
        "repositoryPlugins": ["json"],
        "selectionFingerprint": "sha256:" + "1" * 64,
        "requestDescriptorFingerprint": descriptor_fingerprint,
        "runtimeDescriptorFingerprint": descriptor_fingerprint,
        "implementationFingerprint": catalog.implementation_fingerprint(plugin_ids),
        "descriptorMatch": True,
    }
    assert len(artifact["reviewRuntimeFingerprint"]) == 64
    assert len(artifact["modeIdentity"]) == 64


def test_capture_rejects_plugin_order_that_runtime_would_expand(
    capture_environment,
):
    catalog = quality_capture._runtime_plugin_catalog()
    descriptor_fingerprint = catalog.registry.fingerprint_for(("magento",))
    request = _request(
        projectCapabilities={
            "repositoryPlugins": ["magento"],
            "filePlugins": {},
            "detectionEvidence": {},
            "unavailableCapabilities": [],
            "fingerprint": "sha256:" + "2" * 64,
            "descriptorFingerprint": descriptor_fingerprint,
        },
    )

    with pytest.raises(ValueError, match="dependency-stable"):
        create_quality_capture_session(request)


def test_capture_records_request_plugin_descriptor_mismatch_as_provenance(
    capture_environment,
):
    request = _request(
        projectCapabilities={
            "repositoryPlugins": ["json"],
            "filePlugins": {},
            "detectionEvidence": {},
            "unavailableCapabilities": [],
            "fingerprint": "sha256:" + "3" * 64,
            "descriptorFingerprint": "sha256:" + "4" * 64,
        },
    )

    session = create_quality_capture_session(request)
    artifact = json.loads(session.path.read_text(encoding="utf-8"))

    assert artifact["pluginIdentity"]["descriptorMatch"] is False
    assert artifact["pluginIdentity"]["requestDescriptorFingerprint"] == (
        "sha256:" + "4" * 64
    )


@pytest.mark.asyncio
async def test_failed_provider_call_is_recorded_and_re_raised(capture_environment):
    class FailingDelegate(_FakeDelegate):
        async def ainvoke(self, input_data, **kwargs):
            self.calls.append((input_data, kwargs))
            raise RuntimeError("provider unavailable")

    session = create_quality_capture_session(_request())
    delegate = FailingDelegate()
    llm = ReviewQualityCaptureLLM(delegate, session)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await llm.ainvoke("prompt")
    await session.complete(None, RuntimeError("review failed"))

    artifact = json.loads(session.path.read_text(encoding="utf-8"))
    assert artifact["status"] == "failed"
    assert artifact["providerCalls"] == 1
    assert artifact["calls"][0]["status"] == "failed"
    assert artifact["calls"][0]["error"]["type"] == "RuntimeError"


def test_provider_capability_checks_see_through_capture_wrapper(
    capture_environment,
):
    cloudflare_class = type("ChatCloudflareOpenAI", (_FakeDelegate,), {})
    session = create_quality_capture_session(_request())
    wrapped = ReviewQualityCaptureLLM(cloudflare_class(), session)

    assert supports_structured_output(wrapped) is False


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"result": {"issues": []}}, False),
        ({"error": "missing MCP jar"}, True),
        ({"result": {"error": "provider failed", "issues": []}}, True),
    ],
)
def test_review_response_failure_classification(response, expected):
    assert review_response_indicates_failure(response) is expected
