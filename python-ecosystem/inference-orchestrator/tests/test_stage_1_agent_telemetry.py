from types import SimpleNamespace

import pytest

from service.agent.models import AgentToolEvent
from service.review.orchestrator.stage_1_agent_telemetry import (
    Stage1AgentTelemetryRecorder,
)


def _event(name, arguments, observation):
    return AgentToolEvent(
        action=SimpleNamespace(tool=name, tool_input=arguments),
        observation=observation,
    )


def test_agent_telemetry_records_optional_graph_context_and_exact_source_reads():
    evidence_id = "relation:" + "a" * 64
    recorder = Stage1AgentTelemetryRecorder(
        batch_number=3,
        batch_paths=("src/payment.py",),
        review_unit_ids=("review-unit-3",),
        agent_requested=True,
        source_revision="source-head",
    )
    started = recorder.begin_agent(None)

    recorder.record_agent_events(
        (
            _event(
                "getMinimalReviewContext",
                {"question": "Can authorization be bypassed?"},
                {
                    "status": "ready",
                    "snapshot": {
                        "kind": "proposed_tree",
                        "baseCollectionTarget": "sealed-base",
                        "baseGenerationManifestSha256": "a" * 64,
                        "sourceRevision": "source-head",
                        "generationManifestSha256": "b" * 64,
                    },
                    "changed": {"paths": ["src/payment.py"]},
                    "nodes": [{"id": "unit:payment"}],
                    "edges": [{"kind": "CALLS"}],
                    "evidence": {
                        "relations": [{"evidenceId": evidence_id}],
                    },
                    "sourceWindows": [
                        {"path": "src/policy.py", "content": "allow = False"},
                    ],
                    "omittedFollowups": ["tests_for"],
                },
            ),
            _event(
                "getStructuralUnit",
                {"unitId": "unit:policy"},
                {
                    "status": "ready",
                    "snapshot": {"kind": "proposed_tree"},
                    "unit": {
                        "path": "src/policy_unit.py",
                        "content": "allow = False",
                    },
                    "sourceEvidence": True,
                },
            ),
            _event(
                "getReviewFileContent",
                {"filePath": "src/config.py"},
                "feature_enabled = True",
            ),
        ),
        started_at=started,
    )
    recorder.record_issues((
        SimpleNamespace(evidenceRefs=(evidence_id, "source:unit:ignored")),
    ))
    recorder.finish(status="completed")

    payload = recorder.payload()
    assert payload["firstAction"] == {
        "kind": "tool",
        "name": "getMinimalReviewContext",
    }
    assert payload["initialRequiredTool"] is None
    assert [item["name"] for item in payload["toolSequence"]] == [
        "getMinimalReviewContext",
        "getStructuralUnit",
        "getReviewFileContent",
    ]
    composite = payload["toolSequence"][0]
    assert composite["resultCounts"] == {
        "changedPaths": 1,
        "edges": 1,
        "nodes": 1,
        "omittedFollowups": 1,
        "relations": 1,
        "sourceWindows": 1,
    }
    assert composite["snapshot"] == {
        "kind": "proposed_tree",
        "baseCollectionTarget": "sealed-base",
        "baseGenerationManifestSha256": "a" * 64,
        "sourceRevision": "source-head",
        "generationManifestSha256": "b" * 64,
    }
    assert composite["sourceRevisionMatchesRequest"] is True
    assert payload["sourceReadPaths"] == [
        "src/config.py",
        "src/policy.py",
        "src/policy_unit.py",
    ]
    assert payload["sourceReadCount"] == 3
    assert payload["toolSequence"][-1]["sourceRead"] == {
        "mode": "whole_file",
        "boundsState": "absent",
    }
    assert payload["sourceReadSummary"] == {
        "totalRequests": 1,
        "boundedRangeRequests": 0,
        "wholeFileRequests": 1,
        "byTool": {
            "getReviewFileContent": {
                "totalRequests": 1,
                "boundedRangeRequests": 0,
                "wholeFileRequests": 1,
            },
        },
    }
    assert payload["returnedEvidenceIds"] == [evidence_id]
    assert payload["citedEvidenceIds"] == [evidence_id]
    assert payload["uncitedReturnedEvidenceIds"] == []
    assert payload["fallback"] == {
        "used": False,
        "structuralContextLoaded": False,
    }


def test_agent_telemetry_accepts_completed_repeats_in_required_sequence():
    required = (
        "getMinimalReviewContext",
        "getImpactRadius",
        "queryCodeGraph",
        "getStructuralUnit",
    )
    recorder = Stage1AgentTelemetryRecorder(
        batch_number=1,
        batch_paths=("src/payment.py",),
        agent_requested=True,
    )
    started = recorder.begin_agent(
        required[0],
        required_tool_sequence=required,
    )
    recorder.record_agent_events(
        tuple(
            _event(name, {}, {"status": "ready"})
            for name in (
                required[0],
                required[0],
                required[1],
                required[0],
                required[2],
                required[2],
                required[3],
            )
        ),
        started_at=started,
    )
    recorder.finish(status="completed")

    assert recorder.payload()["requiredToolSequenceSatisfied"] is True


def test_agent_telemetry_distinguishes_zero_tool_and_partial_failure():
    no_tool = Stage1AgentTelemetryRecorder(
        batch_number=1,
        batch_paths=("src/one.py",),
        agent_requested=True,
    )
    no_tool.begin_agent(None)
    no_tool.finish(status="completed")
    assert no_tool.payload()["firstAction"] == {
        "kind": "final_response_without_tool",
    }
    assert no_tool.payload()["sourceReadSummary"] == {
        "totalRequests": 0,
        "boundedRangeRequests": 0,
        "wholeFileRequests": 0,
        "byTool": {},
    }

    failed = Stage1AgentTelemetryRecorder(
        batch_number=2,
        batch_paths=("src/two.py",),
        agent_requested=True,
    )
    started = failed.begin_agent(None)
    failed.record_agent_events(
        (
            _event(
                "exploreReviewContext",
                {},
                {"status": "error", "evidence": {"relations": []}},
            ),
        ),
        started_at=started,
        failed=True,
        error=RuntimeError("transport unavailable"),
    )
    failed.record_fallback(structural_context_loaded=False)
    failed.finish(status="completed")

    payload = failed.payload()
    assert payload["firstAction"]["name"] == "exploreReviewContext"
    assert payload["toolSequence"][0]["status"] == "failed"
    assert payload["degraded"] is True
    assert payload["partialFailure"] == (
        "RuntimeError: transport unavailable"
    )
    assert payload["fallback"] == {
        "used": True,
        "structuralContextLoaded": False,
    }


def test_generation_preparation_failure_is_explicitly_degraded():
    recorder = Stage1AgentTelemetryRecorder(
        batch_number=9,
        batch_paths=("src/a.py",),
        agent_requested=True,
    )
    recorder.record_generation_preparation(
        status="unavailable",
        collection_target=None,
        generation_manifest_sha256=None,
        error="capacity unavailable",
    )
    recorder.begin_agent(None)
    recorder.begin_repository_agent_recovery()
    recorder.finish(status="completed")

    payload = recorder.payload()
    assert payload["generationPreparation"] == {
        "eligible": True,
        "status": "unavailable",
        "receiptPresent": False,
        "error": "capacity unavailable",
    }
    assert payload["degraded"] is True
    assert "capacity unavailable" in payload["partialFailure"]


def test_repository_agent_recovery_replaces_only_the_primary_failure_state():
    recorder = Stage1AgentTelemetryRecorder(
        batch_number=3,
        batch_paths=("src/recovered.py",),
        agent_requested=True,
    )
    recorder.begin_agent(None)
    recorder.mark_agent_failure(RuntimeError("primary schema incomplete"))

    recorder.begin_repository_agent_recovery()
    recovery_started = recorder.begin_agent(None)
    recorder.record_agent_events(
        (_event("exploreReviewContext", {}, {"status": "ready"}),),
        started_at=recovery_started,
    )
    recorder.finish(status="completed")

    payload = recorder.payload()
    assert payload["degraded"] is False
    assert payload["partialFailure"] is None
    assert payload["fallback"]["used"] is False
    assert payload["toolSequence"][0]["invocation"] == 2


def test_agent_telemetry_marks_mapping_and_serialized_tool_errors_degraded():
    recorder = Stage1AgentTelemetryRecorder(
        batch_number=4,
        batch_paths=("src/failing.py",),
        agent_requested=True,
    )
    started = recorder.begin_agent(None)

    failures = recorder.record_agent_events(
        (
            _event(
                "exploreReviewContext",
                {},
                {
                    "status": "error",
                    "status_code": 503,
                    "error": "review context unavailable",
                    "snapshot": {},
                },
            ),
            _event(
                "getReviewFileContent",
                {"filePath": "src/failing.py"},
                '{"status":"error","error":"repository read failed"}',
            ),
        ),
        started_at=started,
    )
    recorder.finish(status="completed")

    payload = recorder.payload()
    assert failures == (
        "exploreReviewContext returned status:error (status 503): "
        "review context unavailable",
        "getReviewFileContent returned status:error: repository read failed",
    )
    assert [event["status"] for event in payload["toolSequence"]] == [
        "failed",
        "failed",
    ]
    assert payload["toolSequence"][0]["statusCode"] == 503
    assert payload["toolSequence"][1]["error"] == "repository read failed"
    assert payload["degraded"] is True
    assert payload["partialFailure"] == "; ".join(failures)
    assert payload["fallback"]["used"] is False


def test_agent_telemetry_aggregates_bounded_and_whole_file_requests_by_tool():
    recorder = Stage1AgentTelemetryRecorder(
        batch_number=6,
        batch_paths=("src/a.py", "src/b.py"),
        agent_requested=True,
    )
    started = recorder.begin_agent(None)

    recorder.record_agent_events(
        (
            _event(
                "getReviewFileContent",
                {
                    "filePath": "src/a.py",
                    "startLine": 20,
                    "endLine": 34,
                },
                {"fileContent": "bounded"},
            ),
            _event(
                "getReviewFileContent",
                {"filePath": "src/b.py"},
                {"fileContent": "whole"},
            ),
            _event(
                "getBranchFileContent",
                {"filePath": "src/c.py", "startLine": 9},
                {"fileContent": "one line"},
            ),
            _event(
                "queryCodeGraph",
                {"target": "src/a.py", "startLine": 1, "endLine": 2},
                {"status": "ready", "results": []},
            ),
        ),
        started_at=started,
    )

    payload = recorder.payload()
    source_reads = [
        event.get("sourceRead") for event in payload["toolSequence"]
    ]
    assert source_reads == [
        {
            "mode": "bounded_range",
            "boundsState": "valid",
            "startLine": 20,
            "endLine": 34,
            "requestedLineCount": 15,
        },
        {
            "mode": "whole_file",
            "boundsState": "absent",
        },
        {
            "mode": "bounded_range",
            "boundsState": "valid",
            "startLine": 9,
            "endLine": 9,
            "requestedLineCount": 1,
        },
        None,
    ]
    assert payload["sourceReadSummary"] == {
        "totalRequests": 3,
        "boundedRangeRequests": 2,
        "wholeFileRequests": 1,
        "byTool": {
            "getBranchFileContent": {
                "totalRequests": 1,
                "boundedRangeRequests": 1,
                "wholeFileRequests": 0,
            },
            "getReviewFileContent": {
                "totalRequests": 2,
                "boundedRangeRequests": 1,
                "wholeFileRequests": 1,
            },
        },
    }


def test_agent_telemetry_classifies_serialized_source_tool_input():
    recorder = Stage1AgentTelemetryRecorder(
        batch_number=8,
        batch_paths=("src/a.py",),
        agent_requested=True,
    )
    started = recorder.begin_agent(None)
    recorder.record_agent_events(
        (
            AgentToolEvent(
                action=SimpleNamespace(
                    tool="getReviewFileContent",
                    tool_input=(
                        '{"filePath":"src/a.py","startLine":3,'
                        '"endLine":5}'
                    ),
                ),
                observation={"fileContent": "three lines"},
            ),
        ),
        started_at=started,
    )

    payload = recorder.payload()
    assert payload["toolSequence"][0]["sourceRead"] == {
        "mode": "bounded_range",
        "boundsState": "valid",
        "startLine": 3,
        "endLine": 5,
        "requestedLineCount": 3,
    }
    assert payload["sourceReadPaths"] == ["src/a.py"]


@pytest.mark.parametrize(
    ("arguments", "bounds_state"),
    (
        ({"filePath": "src/a.py", "endLine": 12}, "partial"),
        ({"filePath": "src/a.py", "startLine": 0}, "invalid"),
        ({"filePath": "src/a.py", "startLine": True}, "invalid"),
        ({"filePath": "src/a.py", "startLine": "10"}, "invalid"),
        (
            {"filePath": "src/a.py", "startLine": 12, "endLine": 11},
            "invalid",
        ),
        (
            {"filePath": "src/a.py", "startLine": 12, "endLine": "14"},
            "invalid",
        ),
    ),
)
def test_agent_telemetry_never_labels_invalid_bounds_as_bounded(
    arguments,
    bounds_state,
):
    recorder = Stage1AgentTelemetryRecorder(
        batch_number=7,
        batch_paths=("src/a.py",),
        agent_requested=True,
    )
    started = recorder.begin_agent(None)
    recorder.record_agent_events(
        (_event("getReviewFileContent", arguments, {"fileContent": "x"}),),
        started_at=started,
    )

    payload = recorder.payload()
    assert payload["toolSequence"][0]["sourceRead"] == {
        "mode": "whole_file",
        "boundsState": bounds_state,
    }
    assert payload["sourceReadSummary"] == {
        "totalRequests": 1,
        "boundedRangeRequests": 0,
        "wholeFileRequests": 1,
        "byTool": {
            "getReviewFileContent": {
                "totalRequests": 1,
                "boundedRangeRequests": 0,
                "wholeFileRequests": 1,
            },
        },
    }


def test_agent_telemetry_honors_top_level_unavailable_and_error_fields():
    recorder = Stage1AgentTelemetryRecorder(
        batch_number=5,
        batch_paths=("src/degraded.py",),
        agent_requested=True,
    )
    started = recorder.begin_agent(None)

    failures = recorder.record_agent_events(
        (
            _event(
                "getMinimalReviewContext",
                {"question": "Review the change"},
                {
                    "status": "unavailable",
                    "coverage": {"state": "unavailable"},
                },
            ),
            _event(
                "getImpactRadius",
                {},
                {
                    "unavailable": True,
                    "message": "graph generation is unavailable",
                },
            ),
            _event(
                "queryCodeGraph",
                {"pattern": "callers_of", "target": "authorize"},
                {
                    "status_code": 409,
                    "errorCode": "current_source_already_supplied",
                    "error": "proposed-tree mutation conflict",
                },
            ),
            _event(
                "traverseCodeGraph",
                {"start": "authorize"},
                {"error": "graph request timed out"},
            ),
            _event(
                "getStructuralUnit",
                {"unitId": "unit:missing"},
                '{"status":"error","statusCode":404,'
                '"error":"unit was not found"}',
            ),
        ),
        started_at=started,
    )
    recorder.finish(status="completed")

    assert failures == (
        "getMinimalReviewContext returned status:unavailable: "
        "tool returned status:unavailable",
        "getImpactRadius returned status:unavailable: "
        "graph generation is unavailable",
        "queryCodeGraph returned status:error (status 409): "
        "proposed-tree mutation conflict",
        "traverseCodeGraph returned status:error: graph request timed out",
        "getStructuralUnit returned status:error (status 404): "
        "unit was not found",
    )
    payload = recorder.payload()
    assert [event["status"] for event in payload["toolSequence"]] == [
        "failed",
        "failed",
        "failed",
        "failed",
        "failed",
    ]
    assert [
        event["failureStatus"] for event in payload["toolSequence"]
    ] == ["unavailable", "unavailable", "error", "error", "error"]
    assert payload["toolSequence"][2]["errorCode"] == (
        "current_source_already_supplied"
    )
    assert payload["degraded"] is True
    assert payload["partialFailure"] == "; ".join(failures)


def test_redundant_complete_prompt_source_rejection_is_observable_not_degraded():
    recorder = Stage1AgentTelemetryRecorder(
        batch_number=7,
        batch_paths=("src/already-supplied.py",),
        agent_requested=True,
    )
    started = recorder.begin_agent("getMinimalReviewContext")

    failures = recorder.record_agent_events(
        (
            _event(
                "getReviewFileContent",
                {"filePath": "src/already-supplied.py"},
                {
                    "status": "error",
                    "errorCode": "current_source_already_supplied",
                    "error": "Complete current source is already supplied",
                },
            ),
        ),
        started_at=started,
    )
    recorder.finish(status="completed")

    assert failures == ()
    payload = recorder.payload()
    assert payload["degraded"] is False
    assert payload["partialFailure"] is None
    assert payload["toolSequence"][0]["status"] == "failed"
    assert payload["toolSequence"][0]["handledRejection"] is True
    assert payload["toolSequence"][0]["errorCode"] == (
        "current_source_already_supplied"
    )
