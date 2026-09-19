"""
Tests for pure helper functions in stage_1_file_review.py.

Covers deterministic Stage 1 batching, context, scheduling, and issue extraction.
"""
import pytest
import asyncio
import json
import logging
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, AsyncMock

from service.review.orchestrator.stage_1_file_review import (
    chunk_files,
    Stage1PreparedContext,
    _build_stage_1_prepared_context,
    _bounded_current_file_context,
    _diff_contains_complete_added_source,
    _find_diff_file_for_path,
    _split_hunk_by_lines,
    _chunk_diff_preserving_hunks,
    _expand_oversized_diff_batches,
    _expand_oversized_current_source_batches,
    _expand_oversized_stage1_evidence_batches,
    _repack_stage1_batches_by_rendered_input,
    _prepare_stage1_prompt_material,
    _render_stage1_prompt,
    _estimated_prompt_tokens,
    _build_stage1_invocations,
    _stage1_relation_briefing_capsule,
    _safe_stage1_relation_briefing_capsule,
    _stage1_batch_token_limit,
    structural_relation_evidence,
    structural_tool_observation_evidence,
    _structural_retrieval_state,
    _consume_stage1_agent_tool_events,
    _format_batch_metadata_json,
    _iter_batch_enrichment_metadata,
    _extract_metadata_identifiers,
    Stage1RagState,
    Stage1ReviewUnitState,
    execute_stage_1_file_reviews,
    review_file_batch,
    _supports_structured_output,
    _extract_calibrated_issues,
    _invoke_stage_1_batch_llm,
    _salvage_stage1_schema_tool_outputs,
    _Stage1BatchReviewAccumulator,
    _Stage1DirectFallbackPrompt,
    _validate_batch_review_coverage,
    _validate_required_agent_tool_sequence,
    create_smart_batches_wrapper,
    STAGE1_CURRENT_SOURCE_BATCH_CHAR_BUDGET,
    STAGE1_METADATA_CHAR_BUDGET,
    STAGE1_AGENT_MAX_STEPS,
    STAGE1_AGENT_MAX_OUTPUT_TOKENS,
    STAGE1_AGENT_TOOL_NAMES,
    STAGE1_VCS_TOOL_NAMES,
)
from service.review.candidate_ledger import CandidateEvidenceLedger
from service.review.orchestrator.stage_1_tool_inventory import (
    STAGE1_BRANCH_FILE_TOOL_NAME,
    STAGE1_LEGACY_VCS_TOOL_NAMES,
    STAGE1_MINIMAL_REVIEW_CONTEXT_TOOL_NAME,
    STAGE1_REQUIRED_STRUCTURAL_TOOL_SEQUENCE,
    STAGE1_REVIEW_CONTEXT_TOOL_NAME,
    STAGE1_STRUCTURAL_TOOL_NAMES,
)
from service.review.orchestrator.stage_1_agent_telemetry import (
    Stage1AgentTelemetryRecorder,
)
from service.review.orchestrator.stage_1_rag_retrieval import (
    STAGE1_RELATION_BRIEFING_MAX_RELATIONS,
    has_exact_proposed_tree_binding,
)
from model.multi_stage import (
    FileGroup,
    ReviewFile,
    FileReviewBatchOutput,
    FileReviewOutput,
    ReviewPlan,
)
from model.output_schemas import CodeReviewIssue
from llm.reasoning_policy import ReasoningEffort
from utils.diff_processor import DiffChangeType, DiffFile, DiffProcessor, ProcessedDiff


def _required_structural_tool_events(*, first_observation=None):
    """Return one successful event for every mandatory graph workflow step."""
    default_observation = {
        "status": "ready",
        "snapshot": {"kind": "proposed_tree"},
        "coverage": {"state": "complete"},
    }
    return tuple(
        SimpleNamespace(
            action=SimpleNamespace(tool=tool_name),
            observation=(
                first_observation
                if index == 0 and first_observation is not None
                else default_observation
            ),
        )
        for index, tool_name in enumerate(
            STAGE1_REQUIRED_STRUCTURAL_TOOL_SEQUENCE
        )
    )


# ── chunk_files ──────────────────────────────────────────────────


def test_required_agent_tool_sequence_requires_order_and_success():
    required = STAGE1_REQUIRED_STRUCTURAL_TOOL_SEQUENCE
    completed = [
        SimpleNamespace(
            action=SimpleNamespace(tool=name),
            observation={"status": "ready"},
        )
        for name in required
    ]

    _validate_required_agent_tool_sequence(completed, required)

    with pytest.raises(RuntimeError, match="workflow was incomplete"):
        _validate_required_agent_tool_sequence(completed[:-1], required)
    failed = list(completed)
    failed[1] = SimpleNamespace(
        action=SimpleNamespace(tool=required[1]),
        observation={"status": "error", "error": "graph unavailable"},
    )
    with pytest.raises(RuntimeError, match="graph operation failed"):
        _validate_required_agent_tool_sequence(failed, required)


def test_required_agent_tool_sequence_allows_completed_repeated_steps():
    required = STAGE1_REQUIRED_STRUCTURAL_TOOL_SEQUENCE
    completed_with_repeats = [
        SimpleNamespace(
            action=SimpleNamespace(tool=name),
            observation={"status": "ready"},
        )
        for name in (
            required[0],
            required[0],
            required[1],
            required[0],
            required[2],
            required[2],
            required[3],
        )
    ]

    _validate_required_agent_tool_sequence(completed_with_repeats, required)

    skipped = list(completed_with_repeats)
    skipped[2] = SimpleNamespace(
        action=SimpleNamespace(tool=required[2]),
        observation={"status": "ready"},
    )
    with pytest.raises(RuntimeError, match="workflow was violated"):
        _validate_required_agent_tool_sequence(skipped, required)


@pytest.mark.parametrize(
    ("response", "expected"),
    (
        (None, "unavailable"),
        ({}, "unavailable"),
        (
            {
                "status": "error",
                "error": "request timed out",
                "coverage": {"state": "complete"},
            },
            "unavailable",
        ),
        (
            {
                "status": "unavailable",
                "coverage": {"state": "complete"},
            },
            "unavailable",
        ),
        (
            {
                "unavailable": True,
                "coverage": {"state": "complete"},
            },
            "unavailable",
        ),
        (
            {
                "error": "transport failed",
                "coverage": {"state": "complete"},
            },
            "unavailable",
        ),
        (
            {
                "status": "ready",
                "coverage": {"state": "bounded"},
            },
            "bounded",
        ),
        ({"status": "ready", "unit": {}}, "complete"),
        (
            {
                "status": "ready",
                "context": {"coverage": {"graphState": "complete"}},
            },
            "complete",
        ),
    ),
)
def test_structural_retrieval_state_never_completes_degraded_observations(
    response,
    expected,
):
    assert _structural_retrieval_state(response) == expected


@pytest.mark.parametrize("tool_name", tuple(STAGE1_STRUCTURAL_TOOL_NAMES))
@pytest.mark.parametrize("status", ("error", "unavailable"))
def test_structural_tool_failures_record_unavailable_retrieval(
    tool_name,
    status,
):
    rag_state = Stage1RagState()
    _consume_stage1_agent_tool_events(
        (
            SimpleNamespace(
                action=SimpleNamespace(tool=tool_name),
                observation={
                    "status": status,
                    "error": "graph call failed" if status == "error" else None,
                    "coverage": {},
                },
            ),
        ),
        visible_evidence_by_id={},
        rag_state=rag_state,
        context_holder={},
    )

    assert rag_state.deterministic_retrieval_states == ["unavailable"]


@pytest.mark.asyncio(loop_scope="function")
async def test_batch_llm_attempt_details_do_not_duplicate_owner_warning(caplog):
    class FailingLlm:
        def with_structured_output(self, _schema):
            return self

        async def ainvoke(self, _prompt):
            raise RuntimeError("provider unavailable")

    with caplog.at_level(logging.DEBUG):
        result = await _invoke_stage_1_batch_llm(
            FailingLlm(), "prompt", ["src/a.py"]
        )

    assert result is None
    assert not [
        record for record in caplog.records
        if record.levelno >= logging.WARNING
    ]


@pytest.mark.asyncio(loop_scope="function")
async def test_agent_result_without_confidence_does_not_trigger_direct_fallback():
    class AgentService:
        async def execute(self, _request):
            return SimpleNamespace(
                output={
                    "reviews": [{
                        "file": "src/a.py",
                        "analysis_summary": "No defect found",
                        "issues": [],
                    }],
                },
                tool_events=(),
            )

    class DirectLlmMustNotRun:
        def with_structured_output(self, _schema):
            raise AssertionError("direct fallback must not run")

    result = await _invoke_stage_1_batch_llm(
        DirectLlmMustNotRun(),
        "prompt",
        ["src/a.py"],
        agent_service=AgentService(),
    )

    assert result == []


@pytest.mark.asyncio(loop_scope="function")
async def test_complete_agent_result_reports_structured_repository_tool_error():
    class AgentService:
        async def execute(self, _request):
            return SimpleNamespace(
                output=FileReviewBatchOutput(
                    reviews=[_clean_file_review("src/a.py")]
                ),
                tool_events=(SimpleNamespace(
                    action=SimpleNamespace(
                        tool="exploreReviewContext",
                        tool_input={"question": "Inspect the changed path"},
                    ),
                    observation=json.dumps({
                        "status": "error",
                        "status_code": 503,
                        "error": "review context unavailable",
                        "snapshot": {},
                        "evidence": {"relations": []},
                    }),
                ),),
            )

    class DirectLlmMustNotRun:
        def with_structured_output(self, _schema):
            raise AssertionError("complete agent result must remain accepted")

    events = []
    telemetry = Stage1AgentTelemetryRecorder(
        batch_number=1,
        batch_paths=("src/a.py",),
        agent_requested=True,
    )
    result = await _invoke_stage_1_batch_llm(
        DirectLlmMustNotRun(),
        "prompt",
        ["src/a.py"],
        agent_service=AgentService(),
        event_callback=events.append,
        agent_telemetry=telemetry,
    )

    assert result == []
    assert [event["state"] for event in events] == [
        "stage_1_agent_degraded",
    ]
    assert "exploreReviewContext" in events[0]["message"]
    payload = telemetry.payload()
    assert payload["degraded"] is True
    assert payload["toolSequence"][0]["status"] == "failed"
    assert payload["fallback"]["used"] is False


def _clean_file_review(path):
    return FileReviewOutput(
        file=path,
        analysis_summary="No defect found",
        issues=[],
        confidence="HIGH",
    )


def _packing_request(paths, enrichment):
    request = MagicMock(
        deltaDiff=None,
        rawDiff="",
        taskContext=None,
        enrichmentData=enrichment,
        projectRules=[],
        previousCodeAnalysisIssues=[],
        changedFiles=paths,
        deletedFiles=[],
        currentCommitHash="a" * 40,
        commitHash="a" * 40,
        maxAllowedTokens=200000,
        useMcpTools=False,
    )
    request.get_rag_branch.return_value = "feature"
    request.get_rag_base_branch.return_value = "main"
    return request


def _bind_exact_proposed_tree(request):
    """Populate the request-scoped target snapshot and complete PR overlay."""
    request.projectWorkspace = "workspace"
    request.projectNamespace = "project"
    request.targetBranchName = "main"
    request.currentCommitHash = "source-head-sha"
    request.commitHash = None
    request.localRepoRevision = "target-head-sha"
    request.localRepoPath = "/tmp/target-snapshot"
    request.localReviewOverlayPath = "/tmp/review-overlay"
    request.ragCollectionTarget = "sealed-target-generation"
    request.ragBaseGenerationManifestSha256 = "b" * 64
    request.ragReviewGenerationStatus = "ready"
    request.ragReviewCollectionTarget = "sealed-review-generation"
    request.ragReviewGenerationManifestSha256 = "c" * 64
    request.ragReviewGenerationError = None
    request.get_target_head_commit_hash.return_value = "target-head-sha"
    return request


def test_exact_proposed_tree_binding_accepts_dedicated_rag_snapshot_path():
    request = _bind_exact_proposed_tree(_packing_request([], MagicMock()))
    request.localRepoPath = ""
    request.localRagRepoPath = "/tmp/structural-target-snapshot"

    assert has_exact_proposed_tree_binding(request) is True


@pytest.mark.parametrize(
    "field",
    ("ragCollectionTarget", "ragBaseGenerationManifestSha256"),
)
def test_exact_proposed_tree_binding_requires_sealed_generation_receipt(field):
    request = _bind_exact_proposed_tree(_packing_request([], MagicMock()))
    setattr(request, field, None)

    assert has_exact_proposed_tree_binding(request) is False


class TestBatchReviewCoverage:
    def test_exact_clean_coverage_is_valid(self):
        output = FileReviewBatchOutput(
            reviews=[_clean_file_review("src/a.py"), _clean_file_review("src/b.py")]
        )

        _validate_batch_review_coverage(output, ["src/a.py", "src/b.py"])

    @pytest.mark.parametrize(
        ("review_paths", "match"),
        [
            ([], "missing=src/a.py,src/b.py"),
            (["src/a.py"], "missing=src/b.py"),
            (["src/a.py", "src/a.py"], "duplicates=src/a.py"),
            (["src/a.py", "src/c.py"], "unexpected=src/c.py"),
            (["src/a.py", ""], "empty_paths=1"),
        ],
    )
    def test_incomplete_or_ambiguous_coverage_is_rejected(
        self,
        review_paths,
        match,
    ):
        output = FileReviewBatchOutput(
            reviews=[_clean_file_review(path) for path in review_paths]
        )

        with pytest.raises(ValueError, match=match):
            _validate_batch_review_coverage(
                output,
                ["src/a.py", "src/b.py"],
            )

    def test_multiple_stage1_schema_tool_payloads_are_salvaged(self):
        accumulator = _Stage1BatchReviewAccumulator.for_paths((
            "src/a.py",
            "src/b.py",
            "src/c.py",
        ))
        events = tuple(
            SimpleNamespace(
                action=SimpleNamespace(
                    tool="FileReviewBatchOutput",
                    tool_input={
                        "reviews": [
                            _clean_file_review(path).model_dump()
                        ],
                    },
                ),
                observation="accepted",
            )
            for path in ("src/a.py", "src/b.py")
        )

        salvaged = _salvage_stage1_schema_tool_outputs(
            events,
            accumulator,
            ("src/a.py", "src/b.py", "src/c.py"),
            generation_prompt="agent prompt",
            source_phase="agent",
        )

        assert salvaged == 2
        assert [review.file for review in accumulator.output().reviews] == [
            "src/a.py",
            "src/b.py",
        ]
        assert accumulator.missing_paths() == ["src/c.py"]

    def test_malformed_mixed_schema_call_is_completed_by_corrected_singleton(
        self,
    ):
        paths = tuple(f"src/file-{index}.xml" for index in range(1, 10))
        malformed_review = _clean_file_review(paths[-1]).model_dump()
        malformed_review.pop("analysis_summary")
        events = (
            SimpleNamespace(
                action=SimpleNamespace(
                    tool="FileReviewBatchOutput",
                    tool_input={
                        "reviews": [
                            *(
                                _clean_file_review(path).model_dump()
                                for path in paths[:-1]
                            ),
                            malformed_review,
                        ],
                    },
                ),
                observation="schema validation failed",
            ),
            SimpleNamespace(
                action=SimpleNamespace(
                    tool="FileReviewBatchOutput",
                    tool_input={
                        "reviews": [
                            _clean_file_review(paths[-1]).model_dump()
                        ],
                    },
                ),
                observation="accepted correction",
            ),
        )
        accumulator = _Stage1BatchReviewAccumulator.for_paths(paths)

        salvaged = _salvage_stage1_schema_tool_outputs(
            events,
            accumulator,
            paths,
            generation_prompt="agent prompt",
            source_phase="agent",
        )

        assert salvaged == len(paths)
        assert accumulator.missing_paths() == []
        assert [
            review.file for review in accumulator.output().reviews
        ] == list(paths)

    def test_malformed_schema_salvage_keeps_raw_duplicate_ambiguous(self):
        paths = ("src/a.py", "src/b.py")
        malformed_duplicate = _clean_file_review(paths[0]).model_dump()
        malformed_duplicate.pop("analysis_summary")
        event = SimpleNamespace(
            action=SimpleNamespace(
                tool="FileReviewBatchOutput",
                tool_input={
                    "reviews": [
                        _clean_file_review(paths[0]).model_dump(),
                        malformed_duplicate,
                        _clean_file_review(paths[1]).model_dump(),
                    ],
                },
            ),
            observation="schema validation failed",
        )
        accumulator = _Stage1BatchReviewAccumulator.for_paths(paths)

        salvaged = _salvage_stage1_schema_tool_outputs(
            (event,),
            accumulator,
            paths,
            generation_prompt="agent prompt",
            source_phase="agent",
        )

        assert salvaged == 1
        assert [
            review.file for review in accumulator.output().reviews
        ] == ["src/b.py"]
        assert accumulator.missing_paths() == ["src/a.py"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_fail_closed_agent_merges_rejected_batch_with_correction(
        self,
    ):
        paths = tuple(f"src/file-{index}.xml" for index in range(1, 10))
        malformed_review = _clean_file_review(paths[-1]).model_dump()
        malformed_review.pop("analysis_summary")
        context_event = SimpleNamespace(
            action=SimpleNamespace(
                tool=STAGE1_REVIEW_CONTEXT_TOOL_NAME,
            ),
            observation={
                "status": "ready",
                "snapshot": {"kind": "proposed_tree"},
                "coverage": {"state": "complete"},
            },
        )
        rejected_schema_event = SimpleNamespace(
            action=SimpleNamespace(
                tool="FileReviewBatchOutput",
                tool_input={
                    "reviews": [
                        *(
                            _clean_file_review(path).model_dump()
                            for path in paths[:-1]
                        ),
                        malformed_review,
                    ],
                },
            ),
            observation="schema validation failed",
        )

        class AgentService:
            def __init__(self):
                self.requests = []

            async def execute(self, request):
                self.requests.append(request)
                return SimpleNamespace(
                    # The reserved correction contains only the repaired file.
                    output=FileReviewBatchOutput(reviews=[
                        _clean_file_review(paths[-1]),
                    ]),
                    tool_events=(context_event, rejected_schema_event),
                )

        class DirectLlmMustNotRun:
            calls = 0

            def with_structured_output(self, _schema, **_kwargs):
                self.calls += 1
                raise AssertionError("direct fallback must not run")

            async def ainvoke(self, _prompt, **_kwargs):
                self.calls += 1
                raise AssertionError("direct fallback must not run")

        accumulator = _Stage1BatchReviewAccumulator.for_paths(paths)
        context_holder = {"response": None}
        agent_service = AgentService()
        direct_llm = DirectLlmMustNotRun()

        result = await _invoke_stage_1_batch_llm(
            direct_llm,
            "agent prompt",
            list(paths),
            agent_service=agent_service,
            agent_context_holder=context_holder,
            review_accumulator=accumulator,
            fail_closed_agent=True,
        )

        assert result == []
        assert direct_llm.calls == 0
        assert len(agent_service.requests) == 1
        assert context_holder["response"] == context_event.observation
        assert accumulator.missing_paths() == []
        assert [
            review.file for review in accumulator.output().reviews
        ] == list(paths)

    def test_stage1_schema_salvage_ignores_foreign_and_malformed_events(self):
        accumulator = _Stage1BatchReviewAccumulator.for_paths(("src/a.py",))
        valid_payload = {
            "reviews": [_clean_file_review("src/a.py").model_dump()],
        }
        events = (
            SimpleNamespace(
                action=SimpleNamespace(
                    tool="CrossFileAnalysisResult",
                    tool_input=valid_payload,
                ),
                observation="foreign schema",
            ),
            SimpleNamespace(
                action=SimpleNamespace(
                    tool="FileReviewBatchOutput",
                    tool_input={"reviews": [{"analysis_summary": "missing file"}]},
                ),
                observation="validation error",
            ),
            SimpleNamespace(
                action={
                    "tool": "FileReviewBatchOutput",
                    "tool_input": "not a schema payload",
                },
                observation="invalid input type",
            ),
        )

        salvaged = _salvage_stage1_schema_tool_outputs(
            events,
            accumulator,
            ("src/a.py",),
            generation_prompt="agent prompt",
            source_phase="agent",
        )

        assert salvaged == 0
        assert accumulator.output().reviews == []
        assert accumulator.missing_paths() == ["src/a.py"]

    def test_schema_salvage_never_credits_a_later_graph_observation(self):
        accumulator = _Stage1BatchReviewAccumulator.for_paths(("src/a.py",))
        events = (
            SimpleNamespace(
                action=SimpleNamespace(
                    tool="FileReviewBatchOutput",
                    tool_input={
                        "reviews": [_clean_file_review("src/a.py").model_dump()],
                    },
                ),
                observation="intermediate generation",
            ),
            SimpleNamespace(
                action=SimpleNamespace(tool=STAGE1_REVIEW_CONTEXT_TOOL_NAME),
                observation={
                    "status": "ready",
                    "snapshot": {"kind": "proposed_tree"},
                    "relations": [],
                },
            ),
        )

        salvaged = _salvage_stage1_schema_tool_outputs(
            events,
            accumulator,
            ("src/a.py",),
            generation_prompt="agent prompt",
            source_phase="agent",
        )

        assert salvaged == 0
        assert accumulator.missing_paths() == ["src/a.py"]

    def test_stage1_schema_salvage_uses_accumulator_path_and_duplicate_rules(self):
        accumulator = _Stage1BatchReviewAccumulator.for_paths((
            "src/a.py",
            "src/b.py",
            "src/c.py",
        ))
        events = (
            SimpleNamespace(
                action={
                    "tool": "FileReviewBatchOutput",
                    "tool_input": {
                        "reviews": [
                            _clean_file_review("src/a.py").model_dump(),
                            _clean_file_review("/src/a.py").model_dump(),
                            _clean_file_review("src/b.py").model_dump(),
                            _clean_file_review("src/c.py").model_dump(),
                            _clean_file_review("src/outside.py").model_dump(),
                        ],
                    },
                },
                observation="accepted",
            ),
            SimpleNamespace(
                action=SimpleNamespace(
                    tool="FileReviewBatchOutput",
                    tool_input={
                        "reviews": [
                            _clean_file_review("src/a.py").model_dump(),
                            _clean_file_review("src/b.py").model_dump(),
                        ],
                    },
                ),
                observation="accepted",
            ),
        )

        salvaged = _salvage_stage1_schema_tool_outputs(
            events,
            accumulator,
            ("src/a.py", "src/b.py"),
            generation_prompt="agent prompt",
            source_phase="agent",
        )

        assert salvaged == 2
        assert [review.file for review in accumulator.output().reviews] == [
            "src/a.py",
            "src/b.py",
        ]
        assert accumulator.missing_paths() == ["src/c.py"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_schema_tool_salvage_recovers_only_missing_paths(self):
        paths = ("src/a.py", "src/b.py", "src/c.py")
        accumulator = _Stage1BatchReviewAccumulator.for_paths(paths)

        class AgentService:
            async def execute(self, _request):
                return SimpleNamespace(
                    output=None,
                    tool_events=tuple(
                        SimpleNamespace(
                            action=SimpleNamespace(
                                tool="FileReviewBatchOutput",
                                tool_input={
                                    "reviews": [
                                        _clean_file_review(path).model_dump()
                                    ],
                                },
                            ),
                            observation="accepted",
                        )
                        for path in paths[:2]
                    ),
                )

        class StructuredAttempt:
            async def ainvoke(self, prompt, **_kwargs):
                assert prompt == "missing-only prompt"
                return FileReviewBatchOutput(
                    reviews=[_clean_file_review("src/c.py")]
                )

        class RecoveryLlm:
            def with_structured_output(self, _schema, **_kwargs):
                return StructuredAttempt()

        recovery_requests = []

        async def prepare_recovery(missing_paths):
            recovery_requests.append(tuple(missing_paths))
            return _Stage1DirectFallbackPrompt(
                prompt="missing-only prompt",
                structural_context_loaded=False,
            )

        result = await _invoke_stage_1_batch_llm(
            RecoveryLlm(),
            "agent prompt",
            list(paths),
            agent_service=AgentService(),
            direct_fallback_prompt_factory=prepare_recovery,
            review_accumulator=accumulator,
        )

        assert result == []
        assert recovery_requests == [("src/c.py",)]
        assert accumulator.missing_paths() == []
        assert [review.file for review in accumulator.output().reviews] == list(paths)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_invalid_structured_coverage_leaves_retry_to_outer_loop(self):
        class StructuredAttempt:
            async def ainvoke(self, _prompt):
                return FileReviewBatchOutput(reviews=[])

        class Llm:
            def __init__(self):
                self.raw_calls = 0
                self.structured_calls = 0

            def with_structured_output(self, _schema):
                self.structured_calls += 1
                return StructuredAttempt()

            async def ainvoke(self, _prompt):
                self.raw_calls += 1
                return (
                    '{"reviews":[{"file":"src/a.py",'
                    '"analysis_summary":"No defect found","issues":[],'
                    '"confidence":"HIGH","note":""}]}'
                )

        llm = Llm()

        result = await _invoke_stage_1_batch_llm(
            llm,
            "complete review prompt",
            ["src/a.py"],
        )

        assert result is None
        assert llm.structured_calls == 1
        assert llm.raw_calls == 0

        retry_result = await _invoke_stage_1_batch_llm(
            llm,
            "complete review prompt",
            ["src/a.py"],
            label="retry",
            force_unstructured=True,
        )

        assert retry_result == []
        assert llm.structured_calls == 1
        assert llm.raw_calls == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_agent_executes_complete_batch_prompt_with_read_tools(self):
        class AgentService:
            def __init__(self):
                self.requests = []

            async def execute(self, request):
                self.requests.append(request)
                return SimpleNamespace(output=FileReviewBatchOutput(
                    reviews=[_clean_file_review("src/a.py")]
                ))

        class DirectLlmMustNotRun:
            def with_structured_output(self, _schema):
                raise AssertionError("direct model path must not run")

        agent_service = AgentService()
        result = await _invoke_stage_1_batch_llm(
            DirectLlmMustNotRun(),
            "diff plus prepared RAG evidence",
            ["src/a.py"],
            agent_service=agent_service,
        )

        assert result == []
        assert len(agent_service.requests) == 1
        request = agent_service.requests[0]
        assert request.prompt == "diff plus prepared RAG evidence"
        assert request.allowed_tool_names == STAGE1_AGENT_TOOL_NAMES
        assert request.reasoning_effort is ReasoningEffort.MEDIUM
        assert request.max_output_tokens == STAGE1_AGENT_MAX_OUTPUT_TOKENS
        assert request.output_schema is FileReviewBatchOutput
        assert request.metadata["batchPaths"] == ("src/a.py",)
        assert request.initial_required_tool_name == "getMinimalReviewContext"
        expected_bindings = {
            tool_name: {"focusPaths": ("src/a.py",)}
            for tool_name in STAGE1_STRUCTURAL_TOOL_NAMES
        }
        expected_bindings["getReviewFileContent"] = {
            "contextSuppliedPaths": (),
        }
        assert request.tool_argument_bindings == expected_bindings

    @pytest.mark.asyncio(loop_scope="function")
    async def test_agent_tool_observations_are_not_prompt_citation_evidence(self):
        class AgentService:
            async def execute(self, _request):
                return SimpleNamespace(
                    output=FileReviewBatchOutput(
                        reviews=[_clean_file_review("src/a.py")]
                    ),
                    tool_events=[SimpleNamespace(
                        action=SimpleNamespace(tool="searchRepositoryCode"),
                        observation={
                            "results": [{
                                "navigationId": "NAV-candidate",
                                "path": "src/dependency.py",
                            }],
                        },
                    )],
                )

        result = await _invoke_stage_1_batch_llm(
            MagicMock(),
            "agent review prompt",
            ["src/a.py"],
            agent_service=AgentService(),
        )

        assert result == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_agent_fenced_json_is_parsed_without_post_formatting_call(self):
        class AgentService:
            def __init__(self):
                self.requests = []

            async def execute(self, request):
                self.requests.append(request)
                return SimpleNamespace(output=(
                    "```json\n"
                    '{"reviews":[{"file":"src/a.py",'
                    '"analysis_summary":"No defect found","issues":[],'
                    '"confidence":"HIGH","note":""}]}\n'
                    "```"
                ))

        class DirectLlmMustNotRun:
            def with_structured_output(self, _schema):
                raise AssertionError("direct model path must not run")

        agent_service = AgentService()
        result = await _invoke_stage_1_batch_llm(
            DirectLlmMustNotRun(),
            "diff plus prepared RAG evidence",
            ["src/a.py"],
            agent_service=agent_service,
        )

        assert result == []
        assert len(agent_service.requests) == 1
        assert agent_service.requests[0].output_schema is FileReviewBatchOutput

    @pytest.mark.asyncio(loop_scope="function")
    async def test_agent_transport_failure_falls_back_to_prepared_prompt(self):
        class AgentService:
            async def execute(self, _request):
                raise RuntimeError("MCP subprocess unavailable")

        class StructuredAttempt:
            async def ainvoke(self, prompt):
                assert prompt == "prepared prompt without tool instructions"
                return FileReviewBatchOutput(
                    reviews=[_clean_file_review("src/a.py")]
                )

        class Llm:
            def with_structured_output(self, _schema):
                return StructuredAttempt()

        events = []
        result = await _invoke_stage_1_batch_llm(
            Llm(),
            "prepared prompt remains intact",
            ["src/a.py"],
            agent_service=AgentService(),
            event_callback=events.append,
            direct_fallback_prompt=(
                "prepared prompt without tool instructions"
            ),
        )

        assert result == []
        assert events[-1]["state"] == "stage_1_agent_degraded"
        assert "did not produce a complete result" in events[-1]["message"]
        assert "tools were unavailable" not in events[-1]["message"]
        assert (
            "no exact proposed-tree context was available"
            in events[-1]["message"]
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_agent_batch_deadline_is_passed_to_shared_direct_recovery(
        self,
        monkeypatch,
    ):
        class AgentService:
            async def execute(self, request):
                assert request.timeout_seconds == 17
                raise TimeoutError(
                    "Agent execution exceeded its 17s deadline"
                )

        class StructuredAttempt:
            async def ainvoke(self, prompt):
                assert prompt == "prepared direct recovery prompt"
                return FileReviewBatchOutput(
                    reviews=[_clean_file_review("src/a.py")]
                )

        class Llm:
            def with_structured_output(self, _schema):
                return StructuredAttempt()

        monkeypatch.setattr(
            "service.review.orchestrator.stage_1_file_review."
            "STAGE1_AGENT_TIMEOUT_SECONDS",
            17,
        )
        events = []

        result = await _invoke_stage_1_batch_llm(
            Llm(),
            "agent prompt",
            ["src/a.py"],
            agent_service=AgentService(),
            event_callback=events.append,
            direct_fallback_prompt="prepared direct recovery prompt",
        )

        assert result == []
        assert events[-1]["state"] == "stage_1_agent_degraded"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_local_only_agent_failure_never_calls_direct_fallback(self):
        class AgentService:
            async def execute(self, _request):
                raise RuntimeError("structural tool failed")

        class DirectLlmMustNotRun:
            def with_structured_output(self, _schema, **_kwargs):
                raise AssertionError("direct fallback must not run")

            async def ainvoke(self, _prompt, **_kwargs):
                raise AssertionError("direct fallback must not run")

        with pytest.raises(RuntimeError, match="direct review fallback is disabled"):
            await _invoke_stage_1_batch_llm(
                DirectLlmMustNotRun(),
                "agent prompt",
                ["src/a.py"],
                agent_service=AgentService(),
                direct_fallback_prompt="forbidden direct prompt",
                fail_closed_agent=True,
            )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_agent_failure_reports_loaded_proposed_tree_context(self):
        class AgentService:
            async def execute(self, _request):
                raise RuntimeError("MCP subprocess unavailable")

        class StructuredAttempt:
            async def ainvoke(self, prompt):
                assert prompt == "direct prompt with proposed-tree context"
                return FileReviewBatchOutput(
                    reviews=[_clean_file_review("src/a.py")]
                )

        class Llm:
            def with_structured_output(self, _schema):
                return StructuredAttempt()

        async def prepare_fallback(_recovery_paths):
            return _Stage1DirectFallbackPrompt(
                prompt="direct prompt with proposed-tree context",
                structural_context_loaded=True,
            )

        events = []
        result = await _invoke_stage_1_batch_llm(
            Llm(),
            "agent prompt",
            ["src/a.py"],
            agent_service=AgentService(),
            event_callback=events.append,
            direct_fallback_prompt_factory=prepare_fallback,
        )

        assert result == []
        assert events[-1]["state"] == "stage_1_agent_degraded"
        assert "already-retrieved exact proposed-tree context" in (
            events[-1]["message"]
        )
        assert "no exact proposed-tree context" not in events[-1]["message"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_agent_output_uses_mcp_free_direct_fallback(self):
        class AgentService:
            async def execute(self, _request):
                return SimpleNamespace(
                    output="",
                    tool_events=[SimpleNamespace(
                        action=SimpleNamespace(tool="searchRepositoryCode"),
                        observation=json.dumps({
                            "results": [{"navigationId": "NAV-not-evidence"}],
                        }),
                    )],
                )

        class StructuredAttempt:
            async def ainvoke(self, prompt):
                assert prompt == "direct evidence-only prompt"
                return FileReviewBatchOutput(
                    reviews=[_clean_file_review("src/a.py")]
                )

        class Llm:
            def with_structured_output(self, _schema):
                return StructuredAttempt()

        result = await _invoke_stage_1_batch_llm(
            Llm(),
            "agent prompt with repository tools",
            ["src/a.py"],
            agent_service=AgentService(),
            direct_fallback_prompt="direct evidence-only prompt",
        )

        assert result == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_all_coverage_invalid_attempts_do_not_publish_clean_result(self):
        class Llm:
            def with_structured_output(self, _schema):
                return self

            async def ainvoke(self, _prompt):
                return '{"reviews":[]}'

        result = await _invoke_stage_1_batch_llm(
            Llm(),
            "complete review prompt",
            ["src/a.py"],
        )

        assert result is None

    @staticmethod
    def _length_exhaustion_review_case(recovery_content):
        class ChatOpenRouter:
            model_name = "openai/reasoning-model"
            extra_body = {}

            def __init__(self):
                self.calls = []
                self.binding_calls = []

            def with_structured_output(self, _schema, **kwargs):
                self.binding_calls.append(kwargs)
                return self

            async def ainvoke(self, prompt, **kwargs):
                self.calls.append((prompt, kwargs))
                if len(self.calls) == 1:
                    return {
                        "raw": SimpleNamespace(
                            content="",
                            tool_calls=[],
                            response_metadata={
                                "finish_reason": "max_tokens",
                                "token_usage": {
                                    "completion_tokens": 18_000,
                                    "completion_tokens_details": {
                                        "reasoning_tokens": 18_000,
                                    },
                                },
                            },
                        ),
                        "parsed": None,
                        "parsing_error": None,
                    }
                return SimpleNamespace(
                    content=recovery_content,
                    response_metadata={"finish_reason": "stop"},
                )

        path = "src/a.py"
        request = MagicMock(
            deltaDiff=None,
            rawDiff="",
            taskContext=None,
            taskHistoryContext=None,
            enrichmentData=None,
            projectRules=[],
            projectCapabilities=None,
            previousCodeAnalysisIssues=[],
            changedFiles=[path],
            deletedFiles=[],
            currentCommitHash=None,
            commitHash=None,
            baseCommitHash=None,
            pullRequestId=None,
            ragCollectionTarget=None,
            ragBaseGenerationManifestSha256=None,
            maxAllowedTokens=200_000,
        )
        prepared = _build_stage_1_prepared_context(
            request,
            None,
            is_incremental=False,
        )
        batch = [{
            "file": ReviewFile(
                path=path,
                focus_areas=["general"],
                risk_level="LOW",
            ),
            "priority": "LOW",
        }]
        return ChatOpenRouter(), request, prepared, batch

    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_length_primary_recovers_with_reasoning_disabled(self):
        recovery = json.dumps({
            "reviews": [{
                "file": "src/a.py",
                "analysis_summary": "No defect found",
                "issues": [],
                "confidence": "HIGH",
                "note": "",
            }],
        })
        llm, request, prepared, batch = self._length_exhaustion_review_case(
            recovery
        )

        result = await review_file_batch(
            llm,
            request,
            batch,
            rag_client=None,
            prepared_context=prepared,
        )

        assert result == []
        assert len(llm.calls) == 2
        assert llm.binding_calls[0]["extra_body"]["reasoning"] == {
            "effort": "low"
        }
        assert llm.calls[1][1]["extra_body"]["reasoning"] == {
            "effort": "none"
        }

    @pytest.mark.asyncio(loop_scope="function")
    async def test_direct_recovery_does_not_rerun_the_agent(self):
        recovery = json.dumps({
            "reviews": [{
                "file": "src/a.py",
                "analysis_summary": "No defect found",
                "issues": [],
                "confidence": "HIGH",
                "note": "",
            }],
        })
        llm, request, prepared, batch = self._length_exhaustion_review_case(
            recovery
        )

        class AgentService:
            def __init__(self):
                self.calls = 0

            async def execute(self, _request):
                self.calls += 1
                return SimpleNamespace(output="")

        agent_service = AgentService()
        events = []

        result = await review_file_batch(
            llm,
            request,
            batch,
            rag_client=None,
            prepared_context=prepared,
            agent_service=agent_service,
            event_callback=events.append,
        )

        assert result == []
        assert agent_service.calls == 1
        assert [
            event["state"]
            for event in events
            if event.get("state") == "stage_1_agent_degraded"
        ] == ["stage_1_agent_degraded"]
        assert len(llm.calls) == 2
        assert llm.calls[1][1]["extra_body"]["reasoning"] == {
            "effort": "none"
        }

    @pytest.mark.asyncio(loop_scope="function")
    async def test_local_only_missing_agent_inventory_fails_before_model_call(self):
        llm, request, prepared, batch = self._length_exhaustion_review_case("{}")
        request.useMcpTools = True
        request.mcpLocalOnly = True
        request.projectWorkspace = "workspace"
        request.projectNamespace = "project"
        request.targetBranchName = "main"
        request.currentCommitHash = "head-revision"
        request.localRepoRevision = "base-revision"
        request.localRepoPath = "/staged/base"
        request.localReviewOverlayPath = "/staged/overlay"

        class IncompleteAgentService:
            available_tool_names = {STAGE1_BRANCH_FILE_TOOL_NAME}

            async def execute(self, _request):
                raise AssertionError("agent must not start")

        with pytest.raises(RuntimeError, match="direct review fallback is disabled"):
            await review_file_batch(
                llm,
                request,
                batch,
                rag_client=None,
                prepared_context=prepared,
                agent_service=IncompleteAgentService(),
            )

        assert llm.calls == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_length_recovery_does_not_accept_truncated_json(self):
        llm, request, prepared, batch = self._length_exhaustion_review_case(
            '{"reviews":[{"file":"src/a.py"'
        )

        with pytest.raises(RuntimeError, match="produced no valid result"):
            await review_file_batch(
                llm,
                request,
                batch,
                rag_client=None,
                prepared_context=prepared,
            )

        assert len(llm.calls) == 2
        assert llm.calls[1][1]["extra_body"]["reasoning"] == {
            "effort": "none"
        }


class TestChunkFiles:
    def _make_groups(self, paths_per_group):
        groups = []
        for gid, paths in enumerate(paths_per_group):
            files = [ReviewFile(path=p, focus_areas=[], risk_level="MEDIUM") for p in paths]
            groups.append(
                FileGroup(group_id=f"g{gid}", priority="MEDIUM", rationale="test", files=files)
            )
        return groups

    def test_single_small_group(self):
        groups = self._make_groups([["a.py", "b.py"]])
        batches = chunk_files(groups, max_files_per_batch=5)
        assert len(batches) == 1
        assert len(batches[0]) == 2

    def test_group_exceeds_batch_size(self):
        groups = self._make_groups([["a.py", "b.py", "c.py", "d.py", "e.py", "f.py"]])
        batches = chunk_files(groups, max_files_per_batch=3)
        assert len(batches) == 2
        assert len(batches[0]) == 3
        assert len(batches[1]) == 3

    def test_multiple_groups_fit(self):
        groups = self._make_groups([["a.py"], ["b.py"]])
        batches = chunk_files(groups, max_files_per_batch=5)
        assert len(batches) == 1
        assert len(batches[0]) == 2

    def test_empty_groups(self):
        batches = chunk_files([], max_files_per_batch=5)
        assert batches == []

    def test_groups_split_across_batches(self):
        groups = self._make_groups([["a.py", "b.py", "c.py"], ["d.py", "e.py", "f.py"]])
        batches = chunk_files(groups, max_files_per_batch=3)
        assert len(batches) == 2

    def test_batch_size_one(self):
        groups = self._make_groups([["a.py", "b.py"]])
        batches = chunk_files(groups, max_files_per_batch=1)
        assert len(batches) == 2
        assert len(batches[0]) == 1
        assert len(batches[1]) == 1


# ── Stage 1 prepared context ────────────────────────────────────

class TestStage1PreparedContext:
    def test_diff_lookup_uses_suffix_index(self):
        request = MagicMock(deltaDiff=None, taskContext=None, enrichmentData=None)
        processed = ProcessedDiff(files=[
            DiffFile(
                path="repo/services/api/src/Foo.py",
                change_type=DiffChangeType.MODIFIED,
                content="diff --git a/repo/services/api/src/Foo.py b/repo/services/api/src/Foo.py",
            )
        ])

        context = _build_stage_1_prepared_context(request, processed, is_incremental=False)

        assert _find_diff_file_for_path(context, "services/api/src/Foo.py").path == "repo/services/api/src/Foo.py"
        assert _find_diff_file_for_path(context, "src/Foo.py").path == "repo/services/api/src/Foo.py"

    def test_current_file_content_is_indexed_for_direct_stage_1_evidence(self):
        file_content = MagicMock(
            path="repo/templates/ratings.phtml",
            content="use SwatchHelper;\n$this->helper(SwatchHelper::class);",
            skipped=False,
        )
        enrichment = MagicMock(fileContents=[file_content], fileMetadata=[])
        request = MagicMock(
            deltaDiff=None,
            taskContext=None,
            enrichmentData=enrichment,
        )

        context = _build_stage_1_prepared_context(request, None, is_incremental=False)

        assert context.file_content_by_path["templates/ratings.phtml"] == file_content.content

    def test_large_current_source_is_never_reduced_to_hunk_windows(self):
        source = "\n".join(
            f"line_{line_number}" for line_number in range(1, 401)
        )
        diff = """\
diff --git a/src/large.py b/src/large.py
--- a/src/large.py
+++ b/src/large.py
@@ -198,3 +198,3 @@
-old
+new
"""

        rendered = _bounded_current_file_context(
            source,
            diff,
            context_lines=3,
        )

        assert rendered == source
        assert "line_1\n" in rendered
        assert "line_400" in rendered

    def test_large_current_source_is_complete_when_diff_has_no_hunk(self):
        source = "start\n" + ("middle\n" * 100) + "end\n"

        rendered = _bounded_current_file_context(source, "metadata only")

        assert rendered == source

    def test_complete_added_source_requires_contiguous_lossless_diff(self):
        source = "first()\nsecond()\n"
        complete_diff = """\
diff --git a/src/new.py b/src/new.py
new file mode 100644
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,2 @@
+first()
+second()
"""
        partial_diff = """\
diff --git a/src/new.py b/src/new.py
new file mode 100644
--- /dev/null
+++ b/src/new.py
@@ -0,0 +2,1 @@
+second()
"""
        modified_diff = """\
diff --git a/src/new.py b/src/new.py
--- a/src/new.py
+++ b/src/new.py
@@ -1 +1 @@
-first()
+second()
"""

        assert _diff_contains_complete_added_source(source, complete_diff)
        assert not _diff_contains_complete_added_source(source, partial_diff)
        assert not _diff_contains_complete_added_source(source, modified_diff)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_batch_prompt_receives_current_file_content_without_rag(self):
        path = "templates/ratings.phtml"
        source = "use SwatchHelper;\n$this->helper(SwatchHelper::class);"
        file_content = MagicMock(path=path, content=source, skipped=False)
        enrichment = MagicMock(fileContents=[file_content], fileMetadata=[])
        request = MagicMock(
            deltaDiff=None,
            rawDiff="",
            taskContext=None,
            enrichmentData=enrichment,
            projectRules=[],
            previousCodeAnalysisIssues=[],
            changedFiles=[path],
            deletedFiles=[],
            currentCommitHash="a" * 40,
        )
        prepared = _build_stage_1_prepared_context(request, None, is_incremental=False)
        batch = [{
            "file": ReviewFile(path=path, focus_areas=["general"], risk_level="LOW"),
            "priority": "LOW",
        }]

        with patch(
            "service.review.orchestrator.stage_1_file_review._invoke_stage_1_batch_llm",
            new_callable=AsyncMock,
            return_value=[],
        ) as invoke:
            result = await review_file_batch(
                MagicMock(),
                request,
                batch,
                rag_client=None,
                prepared_context=prepared,
            )

        assert result == []
        prompt = invoke.await_args.args[1]
        assert "Current File Content (post-change" in prompt
        assert source in prompt

    @pytest.mark.asyncio(loop_scope="function")
    async def test_added_file_source_is_not_duplicated_when_diff_is_complete(self):
        path = "src/new.py"
        source = "first()\nsecond()\n"
        raw_diff = """\
diff --git a/src/new.py b/src/new.py
new file mode 100644
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,2 @@
+first()
+second()
"""
        file_content = MagicMock(
            path=path,
            content=source,
            skipped=False,
        )
        enrichment = MagicMock(
            fileContents=[file_content],
            fileMetadata=[],
        )
        request = MagicMock(
            deltaDiff=None,
            rawDiff=raw_diff,
            taskContext=None,
            enrichmentData=enrichment,
            projectRules=[],
            previousCodeAnalysisIssues=[],
            changedFiles=[path],
            deletedFiles=[],
            currentCommitHash="a" * 40,
        )
        processed = DiffProcessor().process(raw_diff)
        prepared = _build_stage_1_prepared_context(
            request,
            processed,
            is_incremental=False,
        )
        batch = [{
            "file": ReviewFile(
                path=path,
                focus_areas=["general"],
                risk_level="LOW",
            ),
            "priority": "LOW",
        }]

        with patch(
            "service.review.orchestrator.stage_1_file_review."
            "_invoke_stage_1_batch_llm",
            new_callable=AsyncMock,
            return_value=[],
        ) as invoke:
            await review_file_batch(
                MagicMock(),
                request,
                batch,
                rag_client=None,
                prepared_context=prepared,
            )

        prompt = invoke.await_args.args[1]
        assert "Type: ADDED" in prompt
        assert (
            "[Complete post-change source is present once as the added side "
            "of the diff below"
        ) in prompt
        assert "\nfirst()\nsecond()\n\nDiff:" not in prompt
        assert "+first()\n+second()" in prompt

    @pytest.mark.asyncio(loop_scope="function")
    async def test_batch_current_source_bounds_hunkless_files_without_losing_ends(self):
        paths = ["src/first.py", "src/second.py"]
        source = "start\n" + ("middle\n" * 2_000) + "end\n"
        enrichment = MagicMock(
            fileContents=[
                MagicMock(path=path, content=source, skipped=False)
                for path in paths
            ],
            fileMetadata=[],
        )
        request = MagicMock(
            deltaDiff=None,
            rawDiff="",
            taskContext=None,
            enrichmentData=enrichment,
            projectRules=[],
            previousCodeAnalysisIssues=[],
            changedFiles=paths,
            deletedFiles=[],
            currentCommitHash="a" * 40,
        )
        prepared = _build_stage_1_prepared_context(
            request,
            None,
            is_incremental=False,
        )
        batch = [
            {
                "file": ReviewFile(
                    path=path,
                    focus_areas=["general"],
                    risk_level="LOW",
                ),
                "priority": "LOW",
            }
            for path in paths
        ]

        with patch(
            "service.review.orchestrator.stage_1_file_review."
            "_invoke_stage_1_batch_llm",
            new_callable=AsyncMock,
            return_value=[],
        ) as invoke:
            await review_file_batch(
                MagicMock(),
                request,
                batch,
                rag_client=None,
                prepared_context=prepared,
            )

        prompt = invoke.await_args.args[1]
        source_marker = "Current File Content (post-change):\n"
        current_source_sections = prompt.split(source_marker)[1:]
        assert len(current_source_sections) == 2
        bounded_sections = [
            section.split("\n\nDiff:\n", 1)[0]
            for section in current_source_sections
        ]
        assert all(source not in section for section in bounded_sections)
        assert all(section.startswith("start\n") for section in bounded_sections)
        assert all(section.endswith("end\n") for section in bounded_sections)
        assert all("Current file context truncated" in section for section in bounded_sections)
        assert all(
            len(section) <= STAGE1_CURRENT_SOURCE_BATCH_CHAR_BUDGET // len(paths)
            for section in bounded_sections
        )

    def test_cloudflare_structured_output_disabled_by_default(self):
        ChatCloudflareOpenAI = type("ChatCloudflareOpenAI", (), {})

        assert _supports_structured_output(ChatCloudflareOpenAI()) is False

    def test_oversized_processed_diff_does_not_reload_full_raw(self):
        raw_diff = """\
diff --git a/src/big.py b/src/big.py
--- a/src/big.py
+++ b/src/big.py
@@ -1 +1,3 @@
+first_changed_line()
+second_changed_line()
"""
        summarized = DiffFile(
            path="src/big.py",
            change_type=DiffChangeType.MODIFIED,
            content="[summary only]",
            is_skipped=False,
            skip_reason="File too large: 999999 bytes > 1",
        )
        request = MagicMock(rawDiff=raw_diff, deltaDiff=None, enrichmentData=None, taskContext=None)

        prepared = _build_stage_1_prepared_context(
            request,
            ProcessedDiff(files=[summarized]),
            is_incremental=False,
        )
        diff_file = _find_diff_file_for_path(prepared, "src/big.py")

        assert diff_file is summarized
        assert diff_file.content == "[summary only]"
        assert "first_changed_line" not in diff_file.content
        assert prepared.full_diff_index_loaded is False
        assert prepared.full_diff_by_path == {}

        full_diff_file = _find_diff_file_for_path(
            prepared,
            "src/big.py",
            use_full_diff=True,
        )

        assert full_diff_file is summarized
        assert full_diff_file.content == "[summary only]"
        assert prepared.full_diff_index_loaded is False
        assert prepared.full_diff_by_path == {}

    def test_globally_compacted_diff_does_not_reload_full_raw(self):
        raw_diff = """\
diff --git a/src/after_limit.py b/src/after_limit.py
--- a/src/after_limit.py
+++ b/src/after_limit.py
@@ -1 +1,3 @@
+first_changed_line()
+second_changed_line()
"""
        summarized = DiffFile(
            path="src/after_limit.py",
            change_type=DiffChangeType.MODIFIED,
            content="[summary only]",
            is_skipped=False,
            skip_reason="Would exceed total size limit: 120000",
        )
        request = MagicMock(rawDiff=raw_diff, deltaDiff=None, enrichmentData=None, taskContext=None)

        prepared = _build_stage_1_prepared_context(
            request,
            ProcessedDiff(files=[summarized]),
            is_incremental=False,
        )
        diff_file = _find_diff_file_for_path(prepared, "src/after_limit.py")

        assert diff_file is summarized
        assert diff_file.content == "[summary only]"
        assert "first_changed_line" not in diff_file.content
        assert prepared.full_diff_index_loaded is False
        assert prepared.full_diff_by_path == {}

        full_diff_file = _find_diff_file_for_path(
            prepared,
            "src/after_limit.py",
            use_full_diff=True,
        )

        assert full_diff_file is summarized
        assert full_diff_file.content == "[summary only]"
        assert "second_changed_line" not in full_diff_file.content
        assert prepared.full_diff_index_loaded is False
        assert prepared.full_diff_by_path == {}


class TestLargeDiffSegmentation:
    def test_chunk_diff_preserves_file_header_and_hunk_headers(self):
        diff = """\
diff --git a/src/big.py b/src/big.py
--- a/src/big.py
+++ b/src/big.py
@@ -1 +1,2 @@
+aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
@@ -10 +11,2 @@
+bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
"""

        chunks = _chunk_diff_preserving_hunks(diff, max_input_tokens=20)

        assert len(chunks) > 1
        assert all("diff --git a/src/big.py b/src/big.py" in chunk for chunk in chunks)
        assert any("@@ -1,0 +1,1 @@" in chunk for chunk in chunks)
        assert any("@@ -10,0 +11,1 @@" in chunk for chunk in chunks)

    def test_split_hunk_recomputes_each_fragment_coordinates(self):
        hunk = (
            "@@ -100,3 +200,3 @@ def changed():\n"
            " context_one_xxxxxxxxx\n"
            "-removed_two_xxxxxxxxx\n"
            "+added_two_xxxxxxxxxxx\n"
            " context_three_xxxxxxx\n"
        )

        chunks = _split_hunk_by_lines(hunk, max_chars=55)

        assert len(chunks) == 4
        assert chunks[0].startswith("@@ -100,1 +200,1 @@ def changed():")
        assert chunks[1].startswith("@@ -101,1 +201,0 @@ def changed():")
        assert chunks[2].startswith("@@ -102,0 +201,1 @@ def changed():")
        assert chunks[3].startswith("@@ -102,1 +202,1 @@ def changed():")

    def test_owned_segments_require_every_fragment_before_hunk_is_reviewed(self):
        diff = """\
diff --git a/src/big.py b/src/big.py
--- a/src/big.py
+++ b/src/big.py
@@ -10,4 +10,4 @@ def changed():
 context_one_xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
-removed_two_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
+added_two_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
 context_three_xxxxxxxxxxxxxxxxxxxxxxxxxx
"""
        processed = DiffProcessor().process(diff)
        diff_file = processed.files[0]
        file_info = ReviewFile(
            path="src/big.py",
            focus_areas=[],
            risk_level="MEDIUM",
        )
        prepared = Stage1PreparedContext(
            diff_source=processed,
            diff_by_path={"src/big.py": diff_file},
        )

        expanded = _expand_oversized_diff_batches(
            [[{"file": file_info, "priority": "MEDIUM"}]],
            prepared,
            diff_chunk_token_budget=24,
        )
        unit_ids = tuple(
            batch[0]["_review_unit_id"]
            for batch in expanded
        )
        state = Stage1ReviewUnitState()
        state.register_batches(expanded)

        assert len(expanded) > 1
        assert len(set(unit_ids)) == len(unit_ids)
        assert {
            hunk_id
            for batch in expanded
            for hunk_id in batch[0]["_hunk_ids"]
        } == {diff_file.hunks[0].id}

        state.mark_completed(unit_ids[:-1])
        assert state.reviewed_hunk_ids == ()
        with pytest.raises(RuntimeError, match="coverage is incomplete"):
            state.assert_complete()

        state.mark_completed(unit_ids[-1:])
        state.assert_complete()
        assert state.reviewed_hunk_ids == (diff_file.hunks[0].id,)

    def test_duplicate_review_unit_assignment_fails_closed(self):
        file_info = ReviewFile(
            path="src/a.py",
            focus_areas=[],
            risk_level="MEDIUM",
        )
        item = {
            "file": file_info,
            "_review_unit_id": "sha256:unit",
            "_hunk_ids": ("sha256:hunk",),
        }

        with pytest.raises(RuntimeError, match="assigned more than once"):
            Stage1ReviewUnitState().register_batches([[item], [dict(item)]])

    def test_expand_oversized_batches_creates_segment_batches(self):
        file_info = ReviewFile(path="src/big.py", focus_areas=[], risk_level="MEDIUM")
        diff = """\
diff --git a/src/big.py b/src/big.py
--- a/src/big.py
+++ b/src/big.py
@@ -1 +1,2 @@
+aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
@@ -10 +11,2 @@
+bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
"""
        diff_file = DiffFile(path="src/big.py", change_type=DiffChangeType.MODIFIED, content=diff)
        prepared = Stage1PreparedContext(
            diff_source=ProcessedDiff(files=[diff_file]),
            diff_by_path={"src/big.py": diff_file},
        )

        expanded = _expand_oversized_diff_batches(
            [[{"file": file_info, "priority": "MEDIUM"}]],
            prepared,
            diff_chunk_token_budget=20,
        )

        assert len(expanded) > 1
        assert all(len(batch) == 1 for batch in expanded)
        assert expanded[0][0]["_diff_chunk_total"] == len(expanded)

    def test_size_limited_diff_retains_bounded_summary_without_focus_flag(self):
        raw_diff = """\
diff --git a/src/big.py b/src/big.py
--- a/src/big.py
+++ b/src/big.py
@@ -1 +1,5 @@
+aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
+bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
+cccccccccccccccccccccccccccccccccccccccccccccc
+dddddddddddddddddddddddddddddddddddddddddddddd
"""
        summarized = DiffFile(
            path="src/big.py",
            change_type=DiffChangeType.MODIFIED,
            content="[summary only]",
            is_skipped=False,
            skip_reason="File too large: 999999 bytes > 1",
        )
        request = MagicMock(rawDiff=raw_diff, deltaDiff=None, enrichmentData=None, taskContext=None)
        prepared = _build_stage_1_prepared_context(
            request,
            ProcessedDiff(files=[summarized]),
            is_incremental=False,
        )
        file_info = ReviewFile(path="src/big.py", focus_areas=[], risk_level="MEDIUM")

        expanded = _expand_oversized_diff_batches(
            [[{"file": file_info, "priority": "MEDIUM"}]],
            prepared,
            diff_chunk_token_budget=20,
        )

        assert len(expanded) == 1
        assert expanded[0][0]["_hunk_ids"] == ()
        assert "_diff_chunk_total" not in expanded[0][0]
        assert _find_diff_file_for_path(prepared, "src/big.py") is summarized
        assert "first_changed_line" not in summarized.content
        assert prepared.full_diff_index_loaded is False

    def test_full_diff_focus_cannot_bypass_bounded_diff_admission(self):
        raw_diff = """\
diff --git a/src/big.py b/src/big.py
--- a/src/big.py
+++ b/src/big.py
@@ -1 +1,5 @@
+aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
+bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
+cccccccccccccccccccccccccccccccccccccccccccccc
+dddddddddddddddddddddddddddddddddddddddddddddd
"""
        summarized = DiffFile(
            path="src/big.py",
            change_type=DiffChangeType.MODIFIED,
            content="[summary only]",
            is_skipped=False,
            skip_reason="File too large: 999999 bytes > 1",
        )
        request = MagicMock(rawDiff=raw_diff, deltaDiff=None, enrichmentData=None, taskContext=None)
        prepared = _build_stage_1_prepared_context(
            request,
            ProcessedDiff(files=[summarized]),
            is_incremental=False,
        )
        file_info = ReviewFile(
            path="src/big.py",
            focus_areas=["FULL_DIFF_REVIEW"],
            risk_level="MEDIUM",
        )

        expanded = _expand_oversized_diff_batches(
            [[{"file": file_info, "priority": "MEDIUM"}]],
            prepared,
            diff_chunk_token_budget=20,
        )

        assert len(expanded) == 1
        assert expanded[0][0]["_hunk_ids"] == ()
        assert "_diff_chunk_total" not in expanded[0][0]
        assert _find_diff_file_for_path(
            prepared,
            "src/big.py",
            use_full_diff=True,
        ) is summarized
        assert "first_changed_line" not in summarized.content
        assert prepared.full_diff_index_loaded is False


# ── Structured metadata formatting ───────────────────────────────

class TestBatchEnrichmentMetadataScoping:
    def test_same_basename_in_another_module_is_not_selected(self):
        checkout = MagicMock(path="app/code/Acme/Checkout/etc/di.xml")
        cart = MagicMock(path="app/code/Acme/Cart/etc/di.xml")
        request = MagicMock()
        request.enrichmentData.fileMetadata = [cart, checkout]

        result = _iter_batch_enrichment_metadata(
            request,
            ["app/code/Acme/Checkout/etc/di.xml"],
            prepared_context=None,
        )

        assert result == [checkout]

    def test_absolute_prefix_metadata_matches_repository_path(self):
        checkout = MagicMock(
            path="/tmp/checkout/app/code/Acme/Checkout/etc/di.xml"
        )
        request = MagicMock()
        request.enrichmentData.fileMetadata = [checkout]

        result = _iter_batch_enrichment_metadata(
            request,
            ["app/code/Acme/Checkout/etc/di.xml"],
            prepared_context=None,
        )

        assert result == [checkout]


class TestStructuredMetadataFormatting:
    def test_metadata_is_serialized_as_json_without_outline_truncation(self):
        meta = MagicMock()
        meta.model_dump.return_value = {
            "path": "src/Foo.py",
            "imports": [f"pkg{i}" for i in range(25)],
            "symbolNames": [f"symbol{i}" for i in range(35)],
            "calls": [f"call{i}" for i in range(20)],
        }

        result = _format_batch_metadata_json([meta])

        assert '"path":"src/Foo.py"' in result
        assert "pkg24" in result
        assert "symbol34" in result
        assert "call19" in result

    def test_large_plugin_metadata_is_bounded_with_omission_marker(self):
        meta = {
            "path": "app/code/Acme/Checkout/Model/Cart.php",
            "language": "php",
            "pluginSpecificFacts": [
                {
                    "relation": f"relation-{index}",
                    "target": "x" * 200,
                }
                for index in range(500)
            ],
        }

        result = _format_batch_metadata_json([meta])

        assert "app/code/Acme/Checkout/Model/Cart.php" in result
        assert "relation-0" in result
        assert "relation-499" not in result
        assert "_codecrowOmittedItems" in result
        assert len(result) <= STAGE1_METADATA_CHAR_BUDGET

    def test_metadata_projection_is_deterministic_and_schema_neutral(self):
        first = {
            "path": "src/Foo.php",
            "frameworkExtension": {
                "zeta": ["z2", "z1"],
                "alpha": "value",
            },
        }
        second = {
            "frameworkExtension": {
                "alpha": "value",
                "zeta": ["z2", "z1"],
            },
            "path": "src/Foo.php",
        }

        assert _format_batch_metadata_json([first]) == _format_batch_metadata_json([second])

    def test_metadata_identifiers_only_use_structural_symbol_fields(self):
        meta = {
            "path": "src/Foo.py",
            "imports": ["KnownDependency"],
            "symbolNames": ["KnownSymbol"],
            "unknownParserField": {
                "frameworkSpecificName": "FrameworkThing",
                "nested": ["NestedValue"],
            },
        }

        result = _extract_metadata_identifiers([meta])

        assert result == ["KnownDependency"]
        assert "KnownSymbol" not in result
        assert "src/Foo.py" not in result
        assert "FrameworkThing" not in result
        assert "NestedValue" not in result

    def test_metadata_identifier_expansion_has_finite_structural_name_cap(self):
        imports = [f"Dependency{index}" for index in range(350)]

        result = _extract_metadata_identifiers([{"imports": imports}])

        assert result == imports[:200]
        assert result[-1] == "Dependency199"


# ── Deterministic RAG normalization ──────────────────────────────

class TestExtractCalibratedIssues:
    def _make_issue(self, severity="MEDIUM"):
        return CodeReviewIssue(
            id="i1",
            severity=severity,
            category="BUG",
            file="a.py",
            line=10,
            title="Test issue",
            reason="Test reason",
            suggestedFixDescription="Fix it",
        )

    def test_empty_batch(self):
        batch_output = FileReviewBatchOutput(reviews=[])
        result = _extract_calibrated_issues(batch_output)
        assert result == []

    def test_issues_returned(self):
        batch_output = FileReviewBatchOutput(reviews=[
            FileReviewOutput(
                file="a.py",
                analysis_summary="ok",
                issues=[self._make_issue()],
                confidence="HIGH",
            )
        ])
        result = _extract_calibrated_issues(batch_output)
        assert len(result) == 1

    def test_low_confidence_downgrades_high_to_medium(self):
        issue = self._make_issue(severity="HIGH")
        batch_output = FileReviewBatchOutput(reviews=[
            FileReviewOutput(
                file="a.py",
                analysis_summary="uncertain",
                issues=[issue],
                confidence="LOW",
            )
        ])
        result = _extract_calibrated_issues(batch_output)
        assert len(result) == 1
        assert result[0].severity == "MEDIUM"

    def test_low_confidence_does_not_downgrade_medium(self):
        issue = self._make_issue(severity="MEDIUM")
        batch_output = FileReviewBatchOutput(reviews=[
            FileReviewOutput(
                file="a.py",
                analysis_summary="ok",
                issues=[issue],
                confidence="LOW",
            )
        ])
        result = _extract_calibrated_issues(batch_output)
        assert result[0].severity == "MEDIUM"

    def test_high_confidence_keeps_high_severity(self):
        issue = self._make_issue(severity="HIGH")
        batch_output = FileReviewBatchOutput(reviews=[
            FileReviewOutput(
                file="a.py",
                analysis_summary="ok",
                issues=[issue],
                confidence="HIGH",
            )
        ])
        result = _extract_calibrated_issues(batch_output)
        assert result[0].severity == "HIGH"

    def test_multiple_reviews_aggregated(self):
        batch_output = FileReviewBatchOutput(reviews=[
            FileReviewOutput(
                file="a.py",
                analysis_summary="ok",
                issues=[self._make_issue(), self._make_issue()],
                confidence="HIGH",
            ),
            FileReviewOutput(
                file="b.py",
                analysis_summary="ok",
                issues=[self._make_issue()],
                confidence="MEDIUM",
            ),
        ])
        result = _extract_calibrated_issues(batch_output)
        assert len(result) == 3


# ── bounded rendered-input packing ───────────────────────────────


class TestRenderedInputPacking:
    def _batch(self, paths):
        return [{
            "file": ReviewFile(
                path=path,
                focus_areas=["general"],
                risk_level="MEDIUM",
            ),
            "priority": "MEDIUM",
            "_review_unit_id": f"unit:{path}",
            "_hunk_ids": (f"hunk:{path}",),
        } for path in paths]

    def test_agent_repository_tools_use_the_pinned_target_revision(self):
        request = _packing_request(["src/a.py"], None)
        request.useMcpTools = True
        request.localRepoRevision = "target-head-sha"
        request.baseCommitHash = "base-sha"
        request.targetBranchName = "main"
        request.projectVcsWorkspace = "team"
        request.projectVcsRepoSlug = "repo"
        prepared = _build_stage_1_prepared_context(request, None, False)
        material = _prepare_stage1_prompt_material(
            request,
            self._batch(["src/a.py"]),
            prepared,
            False,
        )

        prompt, _ = _render_stage1_prompt(
            material,
            "",
            use_mcp_tools=True,
        )

        assert "TARGET BRANCH/REVISION REF: target-head-sha" in prompt
        assert "TARGET BRANCH/REVISION REF: main" not in prompt

    def test_agent_repository_tools_fall_back_to_request_target_head(self):
        request = _packing_request(["src/a.py"], None)
        request.useMcpTools = True
        request.localRepoRevision = None
        request.targetHeadCommitHash = "target-head-sha"
        request.baseCommitHash = "merge-base-sha"
        request.get_target_head_commit_hash.return_value = "target-head-sha"
        request.targetBranchName = "main"
        prepared = _build_stage_1_prepared_context(request, None, False)
        material = _prepare_stage1_prompt_material(
            request,
            self._batch(["src/a.py"]),
            prepared,
            False,
        )

        prompt, _ = _render_stage1_prompt(
            material,
            "",
            use_mcp_tools=True,
        )

        assert "TARGET BRANCH/REVISION REF: target-head-sha" in prompt
        assert "TARGET BRANCH/REVISION REF: merge-base-sha" not in prompt

    def test_rendered_local_prompt_uses_bounded_source_projections(self):
        paths = ["src/a.py", "src/b.py"]
        sources = {
            path: (f"# {path}\n" + ("value = 1\n" * 8_000))
            for path in paths
        }
        enrichment = MagicMock(
            fileContents=[
                MagicMock(path=path, content=source, skipped=False)
                for path, source in sources.items()
            ],
            fileMetadata=[],
            relationships=[],
        )
        request = _packing_request(paths, enrichment)
        prepared = _build_stage_1_prepared_context(request, None, False)

        repacked = _repack_stage1_batches_by_rendered_input(
            [self._batch(paths)],
            request,
            prepared,
            False,
            token_budget=30_000,
        )

        assert [[item["file"].path for item in batch] for batch in repacked] == [
            paths,
        ]
        prompts = [
            _render_stage1_prompt(
                _prepare_stage1_prompt_material(
                    request, batch, prepared, False
                ),
                "",
            )[0]
            for batch in repacked
        ]
        assert all(_estimated_prompt_tokens(prompt) <= 30_000 for prompt in prompts)
        assert all(sources[path] not in prompts[0] for path in paths)
        assert prompts[0].count("Current file context truncated") == len(paths)
        assert "# src/a.py" in prompts[0]
        assert "# src/b.py" in prompts[0]

    def test_related_files_stay_together_when_rendered_prompt_fits(self):
        paths = ["src/a.py", "src/b.py"]
        relationship = SimpleNamespace(
            sourceFile=paths[0],
            targetFile=paths[1],
            relationshipType=SimpleNamespace(value="IMPORTS"),
            matchedOn="B",
        )
        enrichment = MagicMock(
            fileContents=[
                MagicMock(path=path, content="value = 1\n", skipped=False)
                for path in paths
            ],
            fileMetadata=[],
            relationships=[relationship],
        )
        request = _packing_request(paths, enrichment)
        prepared = _build_stage_1_prepared_context(request, None, False)
        batch = self._batch(paths)

        repacked = _repack_stage1_batches_by_rendered_input(
            [batch], request, prepared, False, token_budget=60_000
        )

        assert repacked == [batch]

    def test_bounded_source_projection_keeps_exact_dependency_capsule(self):
        paths = ["src/a.py", "src/b.py"]
        relationship = SimpleNamespace(
            sourceFile=paths[0],
            targetFile=paths[1],
            relationshipType=SimpleNamespace(value="IMPORTS"),
            matchedOn="B",
        )
        sources = {path: "value = 1\n" * 7_000 for path in paths}
        enrichment = MagicMock(
            fileContents=[
                MagicMock(path=path, content=source, skipped=False)
                for path, source in sources.items()
            ],
            fileMetadata=[],
            relationships=[relationship],
        )
        request = _packing_request(paths, enrichment)
        prepared = _build_stage_1_prepared_context(request, None, False)

        repacked = _repack_stage1_batches_by_rendered_input(
            [self._batch(paths)],
            request,
            prepared,
            False,
            token_budget=8_000,
        )

        assert len(repacked) == 2
        for batch in repacked:
            material = _prepare_stage1_prompt_material(
                request, batch, prepared, False
            )
            prompt, _ = _render_stage1_prompt(material, "")
            assert '"source": "src/a.py"' in prompt
            assert '"target": "src/b.py"' in prompt
            assert '"type": "IMPORTS"' in prompt
            assert '"matchedOn": "B"' in prompt

    def test_individual_source_is_bounded_without_new_invocations(self):
        path = "src/large.py"
        source = "".join(f"line_{index} = {index}\n" for index in range(12_000))
        enrichment = MagicMock(
            fileContents=[MagicMock(path=path, content=source, skipped=False)],
            fileMetadata=[],
            relationships=[],
        )
        request = _packing_request([path], enrichment)
        prepared = _build_stage_1_prepared_context(request, None, False)
        batches = [self._batch([path])]

        expanded = _expand_oversized_current_source_batches(
            batches,
            request,
            prepared,
            False,
            token_budget=18_000,
        )

        assert len(expanded) == 1
        material = _prepare_stage1_prompt_material(
            request,
            expanded[0],
            prepared,
            False,
        )
        bounded_source = material.batch_files_data[0]["current_code"]
        assert bounded_source != source
        assert bounded_source.startswith("line_0 = 0\n")
        assert bounded_source.endswith("line_11999 = 11999\n")
        assert "Current file context truncated" in bounded_source
        assert len(bounded_source) <= STAGE1_CURRENT_SOURCE_BATCH_CHAR_BUDGET
        assert expanded[0][0]["_hunk_ids"] == (f"hunk:{path}",)

    def test_oversized_shared_scaffold_does_not_spawn_review_calls(self):
        path = "src/shared.py"
        task_lines = [f"TASK_FACT_{index:04d} " + "t" * 80 for index in range(350)]
        project_lines = [
            f"PROJECT_RULE_{index:04d} " + "p" * 80
            for index in range(350)
        ]
        plugin_lines = [
            f"PLUGIN_RULE_{index:04d} " + "g" * 80
            for index in range(350)
        ]
        previous_lines = [
            f"PREVIOUS_ISSUE_FACT_{index:04d} " + "v" * 80
            for index in range(350)
        ]
        metadata_facts = [
            f"METADATA_FACT_{index:04d}_" + "m" * 80
            for index in range(350)
        ]
        metadata = SimpleNamespace(
            path=path,
            pluginSpecificFacts=metadata_facts,
        )
        enrichment = MagicMock(
            fileContents=[MagicMock(path=path, content="value = 1\n", skipped=False)],
            fileMetadata=[metadata],
            relationships=[],
        )
        request = _packing_request([path], enrichment)
        request.projectRules = json.dumps([{
            "title": "Lossless rules",
            "description": "\n".join(project_lines),
            "filePatterns": ["*.py"],
            "ruleType": "ENFORCE",
        }])
        request.previousCodeAnalysisIssues = [{
            "id": "previous-1",
            "status": "OPEN",
            "severity": "MEDIUM",
            "file": path,
            "line": 1,
            "reason": "\n".join(previous_lines),
        }]
        prepared = _build_stage_1_prepared_context(request, None, False)
        prepared.task_context = "\n".join(task_lines)
        batch = self._batch([path])
        token_budget = 8_000

        with patch(
            "service.review.orchestrator.stage_1_file_review.review_plugin_context",
            return_value="\n".join(plugin_lines),
        ):
            expanded = _expand_oversized_stage1_evidence_batches(
                [batch],
                request,
                prepared,
                False,
                token_budget,
            )

        assert len(expanded) == 1
        prompts = [
            _render_stage1_prompt(
                _prepare_stage1_prompt_material(
                    request,
                    batch,
                    prepared,
                    False,
                ),
                "",
            )[0]
            for batch in expanded
        ]
        assert all(
            _estimated_prompt_tokens(prompt) <= token_budget
            for prompt in prompts
        )

        prompt = prompts[0]
        assert "CodeCrow bounded optional Stage 1 context" in prompt
        assert "omitted character counts" in prompt
        assert "value = 1" in prompt
        assert task_lines[-1] not in prompt
        assert project_lines[-1] not in prompt
        assert plugin_lines[-1] not in prompt
        assert previous_lines[-1] not in prompt
        assert metadata_facts[-1] not in prompt

    def test_compacted_diff_and_source_have_bounded_truthful_coverage(self):
        path = "src/joint.py"
        source = "".join(
            f"SOURCE_LINE_{index:05d} = '{'s' * 64}'\n"
            for index in range(5_000)
        )
        diff_parts = [
            f"diff --git a/{path} b/{path}\n",
            f"--- a/{path}\n",
            f"+++ b/{path}\n",
        ]
        diff_sentinels = []
        for index in range(180):
            sentinel = f"DIFF_SENTINEL_{index:04d}_" + "d" * 96
            diff_sentinels.append(sentinel)
            line_number = index + 1
            diff_parts.extend([
                f"@@ -{line_number},1 +{line_number},1 @@\n",
                f"-old_{index:04d}\n",
                f"+{sentinel}\n",
            ])
        diff = "".join(diff_parts)
        processed = DiffProcessor().process(diff)
        assert len(processed.files) == 1
        enrichment = MagicMock(
            fileContents=[MagicMock(path=path, content=source, skipped=False)],
            fileMetadata=[],
            relationships=[],
        )
        request = _packing_request([path], enrichment)
        prepared = _build_stage_1_prepared_context(request, processed, False)
        item = {
            "file": ReviewFile(
                path=path,
                focus_areas=["general"],
                risk_level="MEDIUM",
            ),
            "priority": "MEDIUM",
        }
        token_budget = 9_000

        expanded = _expand_oversized_stage1_evidence_batches(
            [[item]],
            request,
            prepared,
            False,
            token_budget,
            max_units_per_item=3,
            max_total_batches=3,
        )
        units = [batch[0] for batch in expanded]

        source_parts = {}
        source_total = 0
        diff_parts_by_index = {}
        diff_total = 0
        rendered_prompts = []
        for unit in units:
            source_override = unit.get("_current_source_override") or ""
            source_match = re.match(
                r"\[Lossless Stage 1 source slice (\d+)/(\d+) [^\n]*\]\n",
                source_override,
            )
            if source_match:
                source_index, source_total = map(int, source_match.groups())
                source_parts[source_index] = source_override[source_match.end():]
            diff_override = unit.get("_diff_override") or ""
            diff_match = re.match(
                r"\[Lossless Stage 1 diff slice (\d+)/(\d+) [^\n]*\]\n",
                diff_override,
            )
            if diff_match:
                diff_index, diff_total = map(int, diff_match.groups())
                diff_parts_by_index[diff_index] = diff_override[diff_match.end():]

            prompt, _ = _render_stage1_prompt(
                _prepare_stage1_prompt_material(
                    request,
                    [unit],
                    prepared,
                    False,
                ),
                "",
            )
            rendered_prompts.append(prompt)
            assert _estimated_prompt_tokens(prompt) <= token_budget
            assert _prepare_stage1_prompt_material(
                request,
                [unit],
                prepared,
                False,
            ).batch_file_paths == [path]

        assert processed.files[0].skip_reason.startswith("File too large:")
        assert "[CodeCrow Summary:" in processed.files[0].content
        assert 1 <= len(units) <= 3
        assert diff_total == 1
        assert len(diff_parts_by_index) == 1
        packed_source = "\n".join(
            unit.get("_current_source_override") or "" for unit in units
        )
        assert "SOURCE_LINE_" in packed_source
        assert source not in packed_source
        packed_diff = "".join(
            diff_parts_by_index[index]
            for index in sorted(diff_parts_by_index)
        )
        assert "[CodeCrow Summary:" in packed_diff
        assert diff_sentinels[0] in packed_diff
        assert diff_sentinels[-1] not in packed_diff
        assert len(units) <= source_total + diff_total
        assert len(units) == max(source_total, diff_total)
        assert all(
            "CodeCrow bounded diff compaction" in prompt
            for prompt in rendered_prompts
        )

        expected_hunks = tuple(sorted(hunk.id for hunk in processed.files[0].hunks))
        assert expected_hunks
        omitted_hunks = tuple(units[-1]["_omitted_hunk_ids"])
        admitted_hunks = tuple(sorted({
            hunk_id for unit in units for hunk_id in unit["_hunk_ids"]
        }))
        assert units[-1]["_omitted_stage1_unit_count"] >= 1
        assert admitted_hunks == ()
        assert omitted_hunks == expected_hunks
        state = Stage1ReviewUnitState()
        state.register_batches(expanded)
        unit_ids = tuple(unit["_review_unit_id"] for unit in units)
        state.mark_completed(unit_ids)
        state.assert_complete()
        assert state.reviewed_hunk_ids == ()
        assert tuple(sorted(state.omitted_hunk_ids)) == omitted_hunks

    def test_compacted_added_file_uses_bounded_source_and_omits_hunk(self):
        path = "src/new-large.py"
        source_lines = [
            f"ADDED_SOURCE_{index:05d} = '{'a' * 64}'"
            for index in range(2_000)
        ]
        source = "\n".join(source_lines) + "\n"
        diff = (
            f"diff --git a/{path} b/{path}\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            f"+++ b/{path}\n"
            f"@@ -0,0 +1,{len(source_lines)} @@\n"
            + "".join(f"+{line}\n" for line in source_lines)
        )
        processed = DiffProcessor().process(diff)
        enrichment = MagicMock(
            fileContents=[MagicMock(path=path, content=source, skipped=False)],
            fileMetadata=[],
            relationships=[],
        )
        request = _packing_request([path], enrichment)
        prepared = _build_stage_1_prepared_context(request, processed, False)
        item = {
            "file": ReviewFile(
                path=path,
                focus_areas=["general"],
                risk_level="MEDIUM",
            ),
            "priority": "MEDIUM",
        }

        expanded = _expand_oversized_stage1_evidence_batches(
            [[item]],
            request,
            prepared,
            False,
            token_budget=8_000,
            max_units_per_item=3,
            max_total_batches=3,
        )

        assert 1 <= len(expanded) <= 3
        assert processed.files[0].skip_reason.startswith("File too large:")
        assert "[CodeCrow Summary:" in processed.files[0].content
        packed_source = "\n".join(
            batch[0].get("_current_source_override") or ""
            for batch in expanded
        )
        packed_diff = "\n".join(
            batch[0]["_diff_override"] for batch in expanded
        )
        assert source_lines[0] in packed_source
        assert source_lines[-1] not in packed_source
        assert source_lines[0] in packed_diff
        assert source_lines[-1] not in packed_diff
        assert expanded[-1][0]["_omitted_stage1_unit_count"] > 0
        expected_hunks = tuple(
            sorted(hunk.id for hunk in processed.files[0].hunks)
        )
        assert expected_hunks
        assert all(
            not (batch[0].get("_hunk_ids") or ()) for batch in expanded
        )
        assert tuple(expanded[-1][0]["_omitted_hunk_ids"]) == expected_hunks
        assert all(
            "CodeCrow bounded diff compaction" in _render_stage1_prompt(
                _prepare_stage1_prompt_material(
                    request,
                    batch,
                    prepared,
                    False,
                ),
                "",
            )[0]
            for batch in expanded
        )

    def test_agentic_invocation_drops_preassembled_structural_context(self):
        path = "src/owner.py"
        structural_map = '{"relations":[{"target":"DependencyService"}]}'
        visible_structural_evidence = {
            "REL-owner-dependency": ({
                "kind": "CALLS",
                "source": "owner",
                "relation": "calls",
                "target": "DependencyService",
                "path": path,
                "line": 1,
                "attributes": {},
                "related_paths": (),
            },),
        }
        enrichment = MagicMock(
            fileContents=[MagicMock(path=path, content="owner = True\n", skipped=False)],
            fileMetadata=[],
            relationships=[],
        )
        request = _packing_request([path], enrichment)
        request.useMcpTools = True
        request.localRepoRevision = "target-head-sha"
        request.projectVcsWorkspace = "tenant"
        request.projectVcsRepoSlug = "repository"
        prepared = _build_stage_1_prepared_context(request, None, False)
        material = _prepare_stage1_prompt_material(
            request, self._batch([path]), prepared, False
        )

        invocations = _build_stage1_invocations(
            material,
            use_mcp_tools=True,
            structural_tools_available=True,
            review_file_tool_available=True,
            preloaded_structural_context=structural_map,
            preloaded_structural_evidence=visible_structural_evidence,
        )

        assert len(invocations) == 1
        prompt, structural_text, _, visible_evidence = invocations[0]
        assert structural_map not in prompt
        assert structural_text == ""
        assert visible_evidence == {}
        assert "PRELOADED STRUCTURAL RELATION MAP" not in prompt
        assert "No structural context is preloaded in agentic mode" in prompt
        assert "Graph use is optional" not in prompt
        assert "owner = True" in prompt
        assert "searchRepositoryCode" not in prompt
        assert "getReviewFileContent" in prompt
        assert "getBranchFileContent" not in prompt
        assert "exploreReviewContext" in prompt
        assert "getStructuralRelations" not in prompt
        assert "getMinimalReviewContext" in prompt
        assert "getImpactRadius" in prompt
        assert "traverseCodeGraph" in prompt
        assert "queryCodeGraph" in prompt
        assert "getStructuralUnit" in prompt

    def test_structural_relation_ids_become_prompt_visible_fact_evidence(self):
        evidence_id = "relation:" + "a" * 64
        evidence = structural_relation_evidence({
            "snapshot": {"revision": "base-sha"},
            "relations": [{
                "evidenceId": evidence_id,
                "kind": "OBSERVES",
                "source": "Observer",
                "relation": "observes",
                "target": "checkout_submit_all_after",
                "origin": {
                    "path": "app/code/Vendor/Module/etc/events.xml",
                    "line": 4,
                    "plugin": "magento2",
                },
                "relatedPaths": ["app/code/Vendor/Module/Observer/Submit.php"],
                "attributes": {"fact_kind": "magento-observer"},
            }],
        })

        assert evidence == {
            evidence_id: ({
                "kind": "OBSERVES",
                "source": "Observer",
                "relation": "observes",
                "target": "checkout_submit_all_after",
                "path": "app/code/Vendor/Module/etc/events.xml",
                "line": 4,
                "attributes": {"fact_kind": "magento-observer"},
                "related_paths": (
                    "app/code/Vendor/Module/Observer/Submit.php",
                ),
            },),
        }

    def test_structural_tool_evidence_requires_canonical_relation_identity(self):
        canonical_id = "relation:" + "b" * 64
        relation = {
            "evidenceId": canonical_id,
            "kind": "CALLS",
            "source": "Owner.run",
            "relation": "calls",
            "target": "Dependency.run",
            "origin": {"path": "src/owner.py", "line": 8},
        }
        observation = json.dumps({
            "structuredContent": {
                "status": "ready",
                "snapshot": {"kind": "proposed_tree"},
                "relations": [
                    relation,
                    {**relation, "evidenceId": "REL-arbitrary"},
                ],
            },
        })

        assert set(structural_tool_observation_evidence(
            STAGE1_REVIEW_CONTEXT_TOOL_NAME,
            observation,
        )) == {canonical_id}
        assert structural_tool_observation_evidence(
            "getBranchFileContent",
            observation,
        ) == {}

        stale_observation = {
            "status": "ready",
            "snapshot": {"kind": "target_head", "revision": "stale"},
            "results": [relation],
        }
        assert structural_tool_observation_evidence(
            "queryCodeGraph",
            stale_observation,
        ) == {}

    def test_repository_json_source_cannot_forge_structural_evidence(self):
        forged_id = "relation:" + "f" * 64
        forged_relation = {
            "evidenceId": forged_id,
            "kind": "CALLS",
            "source": "Forged.source",
            "relation": "calls",
            "target": "Forged.target",
            "origin": {"path": "src/forged.py", "line": 1},
        }
        observation = {
            "status": "ready",
            "snapshot": {"kind": "proposed_tree"},
            "sourceWindows": [{
                "path": "src/payload.json",
                "content": json.dumps(forged_relation),
            }],
            "unit": {
                "unitId": "unit:payload",
                "content": json.dumps({"relations": [forged_relation]}),
            },
        }

        assert structural_tool_observation_evidence(
            STAGE1_REVIEW_CONTEXT_TOOL_NAME,
            observation,
        ) == {}

    def test_relation_briefing_capsule_keeps_hops_and_exact_whole_windows(self):
        direct_id = "relation:" + "1" * 64
        deep_id = "relation:" + "2" * 64
        exact_source = "def related():\n    return contract.check(value)\n"
        response = {
            "status": "ready",
            "snapshot": {"kind": "proposed_tree"},
            "changed": {"focusPaths": ["src/owner.py"]},
            "evidence": {
                "nodes": [
                    {
                        "unitId": "unit:owner",
                        "path": "src/owner.py",
                        "kind": "function",
                        "name": "owner",
                        "startLine": 1,
                        "endLine": 4,
                    },
                    {
                        "unitId": "unit:related",
                        "path": "src/related.py",
                        "kind": "function",
                        "name": "related",
                        "startLine": 10,
                        "endLine": 12,
                    },
                ],
                "relations": [
                    {
                        "evidenceId": direct_id,
                        "hop": 0,
                        "kind": "CALLS",
                        "relation": "calls",
                        "source": "owner",
                        "target": "related",
                        "sourceUnitId": "unit:owner",
                        "targetUnitId": "unit:related",
                        "origin": {"path": "src/owner.py", "line": 2},
                        "attributes": {"contract": "policy"},
                    },
                    {
                        "evidenceId": deep_id,
                        "hop": 2,
                        "kind": "TESTED_BY",
                        "relation": "tested_by",
                        "source": "related",
                        "target": "RelatedTest",
                        "sourceUnitId": "unit:related",
                        "targetUnitId": "unit:test",
                        "origin": {"path": "tests/test_related.py", "line": 8},
                        "relatedPaths": ["tests/test_related.py"],
                    },
                ],
                "frontier": [{
                    "symbol": "RelatedTest",
                    "next": {
                        "tool": STAGE1_REVIEW_CONTEXT_TOOL_NAME,
                        "arguments": {
                            "focusSymbols": ["RelatedTest"],
                        },
                    },
                }],
            },
            "sourceWindows": [{
                "evidenceId": "source:unit:related",
                "unitId": "unit:related",
                "path": "src/related.py",
                "startLine": 10,
                "endLine": 11,
                "content": exact_source,
                "contentSha256": "source-sha",
                "changedFile": False,
                "truncated": False,
                "relationEvidenceIds": [direct_id],
            }],
            "coverage": {
                "graphState": "bounded",
                "partialReasons": ["graph_relation_limit"],
            },
        }

        text, visible = _stage1_relation_briefing_capsule(
            response,
            max_characters=6_000,
        )

        assert len(text) <= 6_000
        assert {edge["evidenceId"] for edge in visible["edges"]} == {
            direct_id,
            deep_id,
        }
        assert {edge["hop"] for edge in visible["edges"]} == {0, 2}
        assert visible["edges"][0]["attributes"] == {"contract": "policy"}
        assert visible["sourceWindows"][0]["content"] == exact_source
        assert visible["sourceWindows"][0]["endLine"] == 11
        assert visible["sourceWindows"][0]["contentSha256"] == "source-sha"
        assert visible["sourceWindows"][0]["truncated"] is False
        assert visible["continuations"] == [{
            "tool": "queryCodeGraph",
            "reason": "Continue from the bounded relation frontier.",
            "arguments": {
                "pattern": "relations_of",
                "target": "RelatedTest",
                "detailLevel": "standard",
            },
        }]
        assert STAGE1_REVIEW_CONTEXT_TOOL_NAME not in text

    def test_relation_briefing_skips_oversized_fact_without_mutating_its_id(self):
        oversized_id = "relation:" + "7" * 64
        retained_id = "relation:" + "8" * 64
        response = {
            "status": "ready",
            "snapshot": {"kind": "proposed_tree"},
            "edges": [
                {
                    "evidenceId": oversized_id,
                    "kind": "CALLS",
                    "source": "Owner.run",
                    "relation": "calls",
                    "target": "Dependency.run",
                    "origin": {"path": "src/owner.py", "line": 2},
                    "attributes": {"payload": "x" * 10_000},
                    "hop": 0,
                },
                {
                    "evidenceId": retained_id,
                    "kind": "TESTED_BY",
                    "source": "Owner.run",
                    "relation": "tested_by",
                    "target": "OwnerTest",
                    "origin": {"path": "tests/test_owner.py", "line": 4},
                    "attributes": {"framework": "pytest"},
                    "hop": 2,
                },
            ],
            "coverage": {"state": "bounded"},
        }

        text, visible = _stage1_relation_briefing_capsule(
            response,
            max_characters=2_500,
        )

        assert oversized_id not in text
        assert [edge["evidenceId"] for edge in visible["edges"]] == [
            retained_id
        ]
        assert visible["edges"][0]["attributes"] == {"framework": "pytest"}

    def test_relation_briefing_never_relabels_long_source_window_paths(self):
        evidence_id = "relation:" + "6" * 64
        long_path = "/".join(["nested" * 20] * 10) + "/related.py"
        content_sha = "d" * 64
        text, visible = _stage1_relation_briefing_capsule({
            "status": "ready",
            "snapshot": {"kind": "proposed_tree"},
            "nodes": [{
                "unitId": "unit:related",
                "path": long_path,
                "kind": "function",
                "name": "related",
                "startLine": 3,
                "endLine": 4,
            }],
            "edges": [{
                "evidenceId": evidence_id,
                "kind": "CALLS",
                "source": "Owner.run",
                "relation": "calls",
                "target": "related",
                "sourceUnitId": "unit:owner",
                "targetUnitId": "unit:related",
                "origin": {"path": "src/owner.py", "line": 2},
                "relatedPaths": [long_path],
            }],
            "sourceWindows": [{
                "evidenceId": "source:unit:related",
                "unitId": "unit:related",
                "path": long_path,
                "startLine": 3,
                "endLine": 4,
                "content": "def related():\n    return True\n",
                "contentSha256": content_sha,
                "relationEvidenceIds": [evidence_id],
            }],
        }, max_characters=10_000)

        assert long_path in text
        assert visible["nodes"][0]["path"] == long_path
        assert visible["sourceWindows"][0]["path"] == long_path
        assert visible["sourceWindows"][0]["contentSha256"] == content_sha

    def test_relation_briefing_tolerates_malformed_optional_counters(self):
        evidence_id = "relation:" + "9" * 64
        text, visible = _stage1_relation_briefing_capsule({
            "status": "ready",
            "snapshot": {"kind": "proposed_tree"},
            "edges": [{
                "evidenceId": evidence_id,
                "kind": "CALLS",
                "source": "Owner.run",
                "relation": "calls",
                "target": "Dependency.run",
                "origin": {"path": "src/owner.py", "line": 2},
                "hop": "not-a-number",
            }],
        })

        assert evidence_id in text
        assert visible["edges"][0]["hop"] == 0

    def test_malformed_relation_briefing_fails_open(self):
        evidence_id = "relation:" + "a" * 64
        text, visible = _safe_stage1_relation_briefing_capsule({
            "status": "ready",
            "snapshot": {"kind": "proposed_tree"},
            "edges": [{
                "evidenceId": evidence_id,
                "kind": "CALLS",
                "source": "Owner.run",
                "relation": "calls",
                "target": "Dependency.run",
                "origin": {"path": "src/owner.py", "line": 2},
            }],
            "coverage": {"partialReasons": 17},
        })

        assert text == ""
        assert visible == {}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_agentic_batch_requires_model_graph_without_host_prefetch(self):
        path = "src/owner.py"
        direct_id = "relation:" + "3" * 64
        deep_id = "relation:" + "4" * 64
        enrichment = MagicMock(
            fileContents=[MagicMock(
                path=path,
                content="def owner():\n    return related()\n",
                skipped=False,
            )],
            fileMetadata=[],
            relationships=[],
        )
        request = _bind_exact_proposed_tree(
            _packing_request([path], enrichment)
        )
        request.useMcpTools = True
        prepared = _build_stage_1_prepared_context(request, None, False)
        briefing_response = {
            "status": "ready",
            "snapshot": {
                "kind": "proposed_tree",
                "baseRevision": "target-head-sha",
                "sourceRevision": "source-head-sha",
            },
            "changed": {"focusPaths": [path]},
            "evidence": {
                "nodes": [
                    {
                        "unitId": "unit:owner",
                        "path": path,
                        "name": "owner",
                        "kind": "function",
                        "startLine": 1,
                        "endLine": 2,
                    },
                    {
                        "unitId": "unit:related",
                        "path": "src/related.py",
                        "name": "related",
                        "kind": "function",
                        "startLine": 5,
                        "endLine": 7,
                    },
                ],
                "relations": [
                    {
                        "evidenceId": direct_id,
                        "hop": 0,
                        "kind": "CALLS",
                        "relation": "calls",
                        "source": "owner",
                        "target": "related",
                        "sourceUnitId": "unit:owner",
                        "targetUnitId": "unit:related",
                        "origin": {"path": path, "line": 2},
                    },
                    {
                        "evidenceId": deep_id,
                        "hop": 2,
                        "kind": "TESTED_BY",
                        "relation": "tested_by",
                        "source": "related",
                        "target": "RelatedTest",
                        "sourceUnitId": "unit:related",
                        "targetUnitId": "unit:test",
                        "origin": {
                            "path": "tests/test_related.py",
                            "line": 10,
                        },
                    },
                ],
                "frontier": [{
                    "symbol": "RelatedTest",
                    "next": {
                        "tool": "queryCodeGraph",
                        "arguments": {
                            "pattern": "relations_of",
                            "target": "RelatedTest",
                        },
                    },
                }],
            },
            "sourceWindows": [{
                "evidenceId": "source:unit:related",
                "unitId": "unit:related",
                "path": "src/related.py",
                "startLine": 5,
                "endLine": 6,
                "content": "def related():\n    return contract.check()\n",
                "contentSha256": "exact-related-sha",
                "changedFile": False,
                "truncated": False,
                "relationEvidenceIds": [direct_id],
            }],
            "coverage": {
                "graphState": "bounded",
                "partialReasons": ["graph_relation_limit"],
            },
        }
        rag_client = SimpleNamespace(
            explore_review_context=AsyncMock(
                return_value=briefing_response
            )
        )
        invoke_review = AsyncMock(return_value=[])
        rag_state = Stage1RagState()
        telemetry = Stage1AgentTelemetryRecorder(
            batch_number=1,
            batch_paths=(path,),
            agent_requested=True,
            source_revision="source-head-sha",
        )

        with patch(
            "service.review.orchestrator.stage_1_file_review."
            "_invoke_stage_1_batch_llm",
            invoke_review,
        ):
            issues = await review_file_batch(
                object(),
                request,
                self._batch([path]),
                rag_client=rag_client,
                prepared_context=prepared,
                agent_service=SimpleNamespace(
                    available_tool_names=STAGE1_AGENT_TOOL_NAMES,
                ),
                rag_state=rag_state,
                agent_telemetry=telemetry,
            )

        assert issues == []
        rag_client.explore_review_context.assert_not_awaited()
        agent_call = invoke_review.await_args
        prompt = agent_call.args[1]
        assert "RELATION-FIRST PROPOSED-TREE BRIEFING" not in prompt
        assert '"hop":2' not in prompt
        assert "tests/test_related.py" not in prompt
        assert "return contract.check()" not in prompt
        assert "exploreReviewContext" in prompt
        assert STAGE1_REVIEW_CONTEXT_TOOL_NAME in (
            agent_call.kwargs["agent_allowed_tool_names"]
        )
        assert agent_call.kwargs["agent_visible_evidence_by_id"] == {}
        assert _estimated_prompt_tokens(prompt) <= _stage1_batch_token_limit(
            request
        )
        assert rag_state.exact_evidence_by_id == {}
        assert rag_state.deterministic_retrieval_states == []
        preparation = telemetry.payload()["generationPreparation"]
        assert preparation == {
            "eligible": True,
            "status": "ready",
            "receiptPresent": True,
            "error": None,
        }
        briefing_telemetry = telemetry.payload()["relationBriefing"]
        assert briefing_telemetry["eligible"] is False
        assert briefing_telemetry["attempted"] is False
        assert briefing_telemetry["status"] == "not_eligible"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_generation_preparation_failure_falls_open_to_source_review(self):
        path = "src/owner.py"
        enrichment = MagicMock(
            fileContents=[MagicMock(
                path=path,
                content="owner = True\n",
                skipped=False,
            )],
            fileMetadata=[],
            relationships=[],
        )
        request = _bind_exact_proposed_tree(
            _packing_request([path], enrichment)
        )
        request.useMcpTools = True
        request.ragReviewGenerationStatus = "unavailable"
        request.ragReviewCollectionTarget = None
        request.ragReviewGenerationManifestSha256 = None
        request.ragReviewGenerationError = "graph unavailable"
        prepared = _build_stage_1_prepared_context(request, None, False)
        rag_client = SimpleNamespace(
            explore_review_context=AsyncMock(return_value={
                "status": "error",
                "error": "graph unavailable",
                "coverage": {"graphState": "unavailable"},
            })
        )
        invoke_review = AsyncMock(return_value=[])
        rag_state = Stage1RagState()
        telemetry = Stage1AgentTelemetryRecorder(
            batch_number=1,
            batch_paths=(path,),
            agent_requested=True,
        )

        with patch(
            "service.review.orchestrator.stage_1_file_review."
            "_invoke_stage_1_batch_llm",
            invoke_review,
        ):
            issues = await review_file_batch(
                object(),
                request,
                self._batch([path]),
                rag_client=rag_client,
                prepared_context=prepared,
                agent_service=SimpleNamespace(
                    available_tool_names=STAGE1_AGENT_TOOL_NAMES,
                ),
                rag_state=rag_state,
                agent_telemetry=telemetry,
            )

        assert issues == []
        assert "RELATION-FIRST PROPOSED-TREE BRIEFING" not in (
            invoke_review.await_args.args[1]
        )
        rag_client.explore_review_context.assert_not_awaited()
        assert rag_state.deterministic_retrieval_states == []
        assert rag_state.exact_evidence_by_id == {}
        payload = telemetry.payload()
        assert payload["generationPreparation"]["status"] == "unavailable"
        assert payload["degraded"] is True
        assert payload["relationBriefing"]["status"] == "not_eligible"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_agentic_batch_uses_composite_proposed_tree_without_prefetch(self):
        path = "src/owner.py"
        enrichment = MagicMock(
            fileContents=[MagicMock(path=path, content="owner = True\n", skipped=False)],
            fileMetadata=[],
            relationships=[],
        )
        request = _bind_exact_proposed_tree(
            _packing_request([path], enrichment)
        )
        request.useMcpTools = True
        prepared = _build_stage_1_prepared_context(request, None, False)
        invoke_review = AsyncMock(return_value=[])
        rag_state = Stage1RagState()

        with patch(
            "service.review.orchestrator.stage_1_file_review._invoke_stage_1_batch_llm",
            invoke_review,
        ):
            issues = await review_file_batch(
                object(),
                request,
                self._batch([path]),
                rag_client=object(),
                prepared_context=prepared,
                agent_service=SimpleNamespace(
                    available_tool_names=STAGE1_AGENT_TOOL_NAMES,
                ),
                rag_state=rag_state,
            )

        assert issues == []
        invoke_review.assert_awaited_once()
        agent_call = invoke_review.await_args
        assert "DependencyService" not in agent_call.args[1]
        assert "PRELOADED STRUCTURAL RELATION MAP" not in agent_call.args[1]
        assert "No structural context is preloaded" in agent_call.args[1]
        assert "Graph use is optional" not in agent_call.args[1]
        assert "## Exact Proposed-Tree Repository Exploration" in (
            agent_call.args[1]
        )
        assert "getReviewFileContent" in agent_call.args[1]
        assert "getBranchFileContent(" not in agent_call.args[1]
        assert "getStructuralRelations" not in agent_call.args[1]
        assert "getMinimalReviewContext" in agent_call.args[1]
        assert "getImpactRadius" in agent_call.args[1]
        assert "traverseCodeGraph" in agent_call.args[1]
        assert "queryCodeGraph" in agent_call.args[1]
        assert "getStructuralUnit" in agent_call.args[1]
        assert "searchRepositoryCode" not in agent_call.args[1]
        assert "getRootDirectory" not in agent_call.args[1]
        assert "getDirectoryByPath" not in agent_call.args[1]
        assert "Protected Local Roots" not in agent_call.args[1]
        assert agent_call.kwargs["agent_service"] is not None
        assert agent_call.kwargs["agent_allowed_tool_names"] == (
            STAGE1_AGENT_TOOL_NAMES.difference({
                STAGE1_BRANCH_FILE_TOOL_NAME,
            })
        )
        assert agent_call.kwargs["agent_max_steps"] == (
            STAGE1_AGENT_MAX_STEPS
        ) == 6
        assert agent_call.kwargs["agent_phase"] == "primary"
        assert agent_call.kwargs["required_agent_tool_names"] == (
            STAGE1_REQUIRED_STRUCTURAL_TOOL_SEQUENCE
        )
        assert agent_call.kwargs["fail_closed_agent"] is False
        assert agent_call.kwargs["agent_context_holder"] == {
            "response": None,
            "responses": [],
        }
        assert rag_state.exact_evidence_by_id == {}
        assert rag_state.deterministic_retrieval_states == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_required_structural_mcp_forces_graph_workflow_and_fail_closed(self):
        path = "src/owner.py"
        enrichment = MagicMock(
            fileContents=[MagicMock(
                path=path,
                content="owner = True\n",
                skipped=False,
            )],
            fileMetadata=[],
            relationships=[],
        )
        request = _bind_exact_proposed_tree(
            _packing_request([path], enrichment)
        )
        request.useMcpTools = True
        request.requireStructuralMcp = True
        invoke_review = AsyncMock(return_value=[])

        with patch(
            "service.review.orchestrator.stage_1_file_review._invoke_stage_1_batch_llm",
            invoke_review,
        ):
            issues = await review_file_batch(
                object(),
                request,
                self._batch([path]),
                rag_client=object(),
                prepared_context=_build_stage_1_prepared_context(
                    request,
                    None,
                    False,
                ),
                agent_service=SimpleNamespace(
                    available_tool_names=STAGE1_AGENT_TOOL_NAMES,
                ),
            )

        assert issues == []
        agent_call = invoke_review.await_args
        assert agent_call.kwargs["required_agent_tool_names"] == (
            STAGE1_REQUIRED_STRUCTURAL_TOOL_SEQUENCE
        )
        assert agent_call.kwargs["fail_closed_agent"] is True

    @pytest.mark.asyncio(loop_scope="function")
    async def test_required_structural_mcp_rejects_incomplete_tool_inventory(self):
        path = "src/owner.py"
        enrichment = MagicMock(
            fileContents=[MagicMock(
                path=path,
                content="owner = True\n",
                skipped=False,
            )],
            fileMetadata=[],
            relationships=[],
        )
        request = _bind_exact_proposed_tree(
            _packing_request([path], enrichment)
        )
        request.useMcpTools = True
        request.requireStructuralMcp = True

        with pytest.raises(
            RuntimeError,
            match="structural MCP inventory is incomplete",
        ):
            await review_file_batch(
                object(),
                request,
                self._batch([path]),
                rag_client=object(),
                prepared_context=_build_stage_1_prepared_context(
                    request,
                    None,
                    False,
                ),
                agent_service=SimpleNamespace(
                    available_tool_names=(
                        STAGE1_AGENT_TOOL_NAMES.difference({
                            "traverseCodeGraph",
                        })
                    ),
                ),
            )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_partial_agent_reviews_are_merged_with_missing_only_recovery(self):
        paths = ["src/a.py", "src/b.py", "src/c.py"]
        enrichment = MagicMock(
            fileContents=[
                MagicMock(
                    path=path,
                    content=f"value = '{path}'\n",
                    skipped=False,
                )
                for path in paths
            ],
            fileMetadata=[],
            relationships=[],
        )
        request = _bind_exact_proposed_tree(
            _packing_request(paths, enrichment)
        )
        request.useMcpTools = True
        prepared = _build_stage_1_prepared_context(request, None, False)

        def issue(path, identifier):
            return CodeReviewIssue(
                id=identifier,
                severity="MEDIUM",
                category="BUG_RISK",
                file=path,
                line=1,
                title=f"Defect in {path}",
                reason=f"The changed behavior in {path} is incorrect.",
                suggestedFixDescription="Correct the changed behavior.",
                codeSnippet=f"value = '{path}'",
            )

        issues_by_path = {
            path: issue(path, f"issue-{index}")
            for index, path in enumerate(paths, start=1)
        }

        class AgentService:
            available_tool_names = STAGE1_AGENT_TOOL_NAMES

            def __init__(self):
                self.requests = []

            async def execute(self, agent_request):
                self.requests.append(agent_request)
                return SimpleNamespace(
                    output=FileReviewBatchOutput(reviews=[FileReviewOutput(
                        file=paths[0],
                        analysis_summary="Found a concrete defect.",
                        issues=[issues_by_path[paths[0]]],
                        confidence="HIGH",
                    )]),
                    tool_events=_required_structural_tool_events(),
                )

        class StructuredAttempt:
            def __init__(self, owner):
                self.owner = owner

            async def ainvoke(self, prompt, **_kwargs):
                self.owner.structured_prompts.append(prompt)
                return FileReviewBatchOutput(reviews=[FileReviewOutput(
                    file=paths[1],
                    analysis_summary="Found a concrete defect.",
                    issues=[issues_by_path[paths[1]]],
                    confidence="HIGH",
                )])

        class RecoveryLlm:
            def __init__(self):
                self.structured_prompts = []
                self.raw_prompts = []

            def with_structured_output(self, _schema, **_kwargs):
                return StructuredAttempt(self)

            async def ainvoke(self, prompt, **_kwargs):
                self.raw_prompts.append(prompt)
                return SimpleNamespace(content=FileReviewBatchOutput(
                    reviews=[FileReviewOutput(
                        file=paths[2],
                        analysis_summary="Found a concrete defect.",
                        issues=[issues_by_path[paths[2]]],
                        confidence="HIGH",
                    )]
                ).model_dump_json())

        agent_service = AgentService()
        recovery_llm = RecoveryLlm()
        ledger = CandidateEvidenceLedger()
        events = []
        result = await review_file_batch(
            recovery_llm,
            request,
            self._batch(paths),
            rag_client=object(),
            prepared_context=prepared,
            agent_service=agent_service,
            candidate_ledger=ledger,
            event_callback=events.append,
        )

        assert result == [issues_by_path[path] for path in paths]
        assert len(agent_service.requests) == 1
        assert len(recovery_llm.structured_prompts) == 1
        structured_prompt = recovery_llm.structured_prompts[0]
        assert "FILE #1: src/b.py" in structured_prompt
        assert "FILE #2: src/c.py" in structured_prompt
        assert "FILE #1: src/a.py" not in structured_prompt
        assert len(recovery_llm.raw_prompts) == 1
        raw_prompt = recovery_llm.raw_prompts[0]
        assert "FILE #1: src/c.py" in raw_prompt
        assert "FILE #1: src/a.py" not in raw_prompt
        assert "FILE #1: src/b.py" not in raw_prompt
        assert events[-1]["state"] == "stage_1_agent_degraded"
        assert "only the missing files" in events[-1]["message"]
        records = [ledger.record_for(result_issue) for result_issue in result]
        assert all(record is not None for record in records)
        assert ":agent:" in records[0].source_key
        assert all(
            ":direct_recovery:" in record.source_key
            for record in records[1:]
        )

    @pytest.mark.parametrize(
        "primary_result",
        ["exception", "empty", "wrong_path", "partial"],
    )
    @pytest.mark.asyncio(loop_scope="function")
    async def test_local_only_retries_agent_once_for_missing_paths(
        self,
        primary_result,
    ):
        paths = ["src/a.py", "src/b.py", "src/c.py"]
        enrichment = MagicMock(
            fileContents=[
                MagicMock(
                    path=path,
                    content=f"value = '{path}'\n",
                    skipped=False,
                )
                for path in paths
            ],
            fileMetadata=[],
            relationships=[],
        )
        request = _bind_exact_proposed_tree(
            _packing_request(paths, enrichment)
        )
        request.useMcpTools = True
        request.mcpLocalOnly = True
        prepared = _build_stage_1_prepared_context(request, None, False)

        def issue(path, identifier):
            return CodeReviewIssue(
                id=identifier,
                severity="MEDIUM",
                category="BUG_RISK",
                file=path,
                line=1,
                title=f"Defect in {path}",
                reason=f"The changed behavior in {path} is incorrect.",
                suggestedFixDescription="Correct the changed behavior.",
                codeSnippet=f"value = '{path}'",
            )

        issues_by_path = {
            path: issue(path, f"issue-{index}")
            for index, path in enumerate(paths, start=1)
        }

        def review(path):
            return FileReviewOutput(
                file=path,
                analysis_summary="Found a concrete defect.",
                issues=[issues_by_path[path]],
                confidence="HIGH",
            )

        context_events = _required_structural_tool_events()

        class AgentService:
            available_tool_names = STAGE1_AGENT_TOOL_NAMES

            def __init__(self):
                self.requests = []

            async def execute(self, agent_request):
                self.requests.append(agent_request)
                if len(self.requests) == 1:
                    if primary_result == "exception":
                        raise RuntimeError("primary agent transport failed")
                    if primary_result == "empty":
                        return SimpleNamespace(
                            output="",
                            tool_events=context_events,
                        )
                    if primary_result == "wrong_path":
                        return SimpleNamespace(
                            output=FileReviewBatchOutput(reviews=[
                                _clean_file_review("src/Asset.php"),
                            ]),
                            tool_events=context_events,
                        )
                    return SimpleNamespace(
                        output=FileReviewBatchOutput(
                            reviews=[review(paths[0])],
                        ),
                        tool_events=context_events,
                    )

                missing = (
                    paths[1:] if primary_result == "partial" else paths
                )
                return SimpleNamespace(
                    output=FileReviewBatchOutput(
                        reviews=[review(path) for path in missing],
                    ),
                    tool_events=context_events,
                )

        class DirectLlmMustNotRun:
            def with_structured_output(self, _schema, **_kwargs):
                raise AssertionError("direct fallback must not run")

            async def ainvoke(self, _prompt, **_kwargs):
                raise AssertionError("direct fallback must not run")

        agent_service = AgentService()
        telemetry = Stage1AgentTelemetryRecorder(
            batch_number=1,
            batch_paths=tuple(paths),
            agent_requested=True,
        )
        result = await review_file_batch(
            DirectLlmMustNotRun(),
            request,
            self._batch(paths),
            rag_client=object(),
            prepared_context=prepared,
            agent_service=agent_service,
            agent_telemetry=telemetry,
        )

        assert result == [issues_by_path[path] for path in paths]
        assert len(agent_service.requests) == 2
        primary_request, recovery_request = agent_service.requests
        assert primary_request.metadata["phase"] == "primary"
        expected_missing = (
            tuple(paths[1:])
            if primary_result == "partial"
            else tuple(paths)
        )
        assert recovery_request.metadata == {
            "stage": "stage_1",
            "label": "agentic missing-path recovery",
            "phase": "missing_path_recovery",
            "batchPaths": expected_missing,
        }
        assert recovery_request.max_steps == STAGE1_AGENT_MAX_STEPS == 6
        assert recovery_request.initial_required_tool_name == (
            STAGE1_MINIMAL_REVIEW_CONTEXT_TOOL_NAME
        )
        assert recovery_request.required_tool_names == (
            STAGE1_REQUIRED_STRUCTURAL_TOOL_SEQUENCE
        )
        expected_bindings = {
            tool_name: {"focusPaths": expected_missing}
            for tool_name in STAGE1_STRUCTURAL_TOOL_NAMES
        }
        expected_bindings["getReviewFileContent"] = {
            "contextSuppliedPaths": expected_missing,
        }
        assert recovery_request.tool_argument_bindings == expected_bindings
        for index, path in enumerate(expected_missing, start=1):
            assert f"FILE #{index}: {path}" in recovery_request.prompt
        for path in set(paths).difference(expected_missing):
            assert not re.search(
                rf"FILE #\d+: {re.escape(path)}",
                recovery_request.prompt,
            )
        payload = telemetry.payload()
        assert payload["degraded"] is False
        assert payload["partialFailure"] is None
        assert payload["fallback"]["used"] is False
        assert {step["invocation"] for step in payload["toolSequence"]} <= {
            1,
            2,
        }
        assert payload["toolSequence"][-1]["invocation"] == 2

    @pytest.mark.parametrize(
        "recovery_failure",
        ["partial", "repeated_empty", "stale_context"],
    )
    @pytest.mark.asyncio(loop_scope="function")
    async def test_local_only_missing_path_recovery_fails_after_two_agent_invocations(
        self,
        recovery_failure,
    ):
        paths = ["src/a.py", "src/b.py", "src/c.py"]
        enrichment = MagicMock(
            fileContents=[
                MagicMock(path=path, content="value = 1\n", skipped=False)
                for path in paths
            ],
            fileMetadata=[],
            relationships=[],
        )
        request = _bind_exact_proposed_tree(
            _packing_request(paths, enrichment)
        )
        request.useMcpTools = True
        request.mcpLocalOnly = True
        prepared = _build_stage_1_prepared_context(request, None, False)
        context_events = _required_structural_tool_events()
        stale_context_events = _required_structural_tool_events(
            first_observation={
                "status": "ready",
                "snapshot": {"kind": "target_head"},
                "coverage": {"state": "complete"},
            },
        )

        class AgentService:
            available_tool_names = STAGE1_AGENT_TOOL_NAMES

            def __init__(self):
                self.requests = []

            async def execute(self, agent_request):
                self.requests.append(agent_request)
                if len(self.requests) > 2:
                    raise AssertionError("repository agent retried more than once")
                if recovery_failure == "repeated_empty":
                    return SimpleNamespace(
                        output="",
                        tool_events=context_events,
                    )
                if (
                    recovery_failure == "stale_context"
                    and len(self.requests) == 2
                ):
                    return SimpleNamespace(
                        output=FileReviewBatchOutput(reviews=[
                            _clean_file_review(path) for path in paths[1:]
                        ]),
                        tool_events=stale_context_events,
                    )
                returned_path = paths[len(self.requests) - 1]
                return SimpleNamespace(
                    output=FileReviewBatchOutput(
                        reviews=[_clean_file_review(returned_path)],
                    ),
                    tool_events=context_events,
                )

        class DirectLlmMustNotRun:
            def with_structured_output(self, _schema, **_kwargs):
                raise AssertionError("direct fallback must not run")

            async def ainvoke(self, _prompt, **_kwargs):
                raise AssertionError("direct fallback must not run")

        agent_service = AgentService()
        review_call = review_file_batch(
            DirectLlmMustNotRun(),
            request,
            self._batch(paths),
            rag_client=object(),
            prepared_context=prepared,
            agent_service=agent_service,
        )
        if recovery_failure == "stale_context":
            # Structural enrichment is optional. A complete source review is
            # accepted even when a graph observation reports a non-proposed
            # snapshot.
            assert await review_call == []
        else:
            with pytest.raises(
                RuntimeError,
                match="one bounded repository-agent recovery",
            ):
                await review_call

        assert len(agent_service.requests) == 2
        expected_missing = (
            tuple(paths)
            if recovery_failure == "repeated_empty"
            else tuple(paths[1:])
        )
        expected_bindings = {
            tool_name: {"focusPaths": expected_missing}
            for tool_name in STAGE1_STRUCTURAL_TOOL_NAMES
        }
        expected_bindings["getReviewFileContent"] = {
            "contextSuppliedPaths": expected_missing,
        }
        assert agent_service.requests[1].tool_argument_bindings == (
            expected_bindings
        )
        for index, path in enumerate(expected_missing, start=1):
            assert (
                f"FILE #{index}: {path}"
                in agent_service.requests[1].prompt
            )
        if recovery_failure != "repeated_empty":
            assert not re.search(
                r"FILE #\d+: src/a\.py",
                agent_service.requests[1].prompt,
            )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_partial_model_graph_result_is_reused_by_direct_fallback(self):
        path = "src/owner.py"
        evidence_id = "relation:" + "c" * 64
        oversized_source_marker = "OVERSIZED-RELATED-SOURCE-"
        enrichment = MagicMock(
            fileContents=[MagicMock(path=path, content="owner = True\n", skipped=False)],
            fileMetadata=[],
            relationships=[],
        )
        request = _bind_exact_proposed_tree(
            _packing_request([path], enrichment)
        )
        request.useMcpTools = True
        prepared = _build_stage_1_prepared_context(request, None, False)
        proposed_context = {
            "status": "ready",
            "snapshot": {
                "kind": "proposed_tree",
                "baseRevision": "target-head-sha",
                "sourceRevision": "source-head-sha",
            },
            "changed": {"paths": [path]},
            "evidence": {"relations": [{
                "evidenceId": evidence_id,
                "kind": "CALLS",
                "source": "owner",
                "relation": "calls",
                "target": "DependencyService",
                "origin": {"path": path, "line": 1},
            }]},
            "sourceWindows": [{
                "path": "src/dependency.py",
                "startLine": 1,
                "endLine": 4,
                "content": oversized_source_marker + "x" * 60_000,
            }],
            "coverage": {"state": "complete"},
            "omittedFollowups": [],
        }
        class PartialAgentFailure(RuntimeError):
            def __init__(self):
                super().__init__("final model call failed")
                self.tool_events = (
                    SimpleNamespace(
                        action=SimpleNamespace(
                            tool=STAGE1_REVIEW_CONTEXT_TOOL_NAME,
                        ),
                        observation=proposed_context,
                    ),
                )

        class AgentService:
            available_tool_names = STAGE1_AGENT_TOOL_NAMES

            def __init__(self):
                self.requests = []

            async def execute(self, agent_request):
                self.requests.append(agent_request)
                raise PartialAgentFailure()

        direct_prompts = []

        class StructuredAttempt:
            async def ainvoke(self, prompt):
                direct_prompts.append(prompt)
                return FileReviewBatchOutput(
                    reviews=[_clean_file_review(path)]
                )

        class Llm:
            def with_structured_output(self, _schema):
                return StructuredAttempt()

        agent_service = AgentService()
        events = []
        rag_state = Stage1RagState()
        rag_client = SimpleNamespace(
            explore_review_context=AsyncMock(),
        )
        issues = await review_file_batch(
            Llm(),
            request,
            self._batch([path]),
            rag_client=rag_client,
            prepared_context=prepared,
            agent_service=agent_service,
            rag_state=rag_state,
            event_callback=events.append,
        )

        assert issues == []
        assert len(direct_prompts) == 1
        direct_prompt = direct_prompts[0]
        assert "PRELOADED STRUCTURAL RELATION MAP" not in direct_prompt
        assert "RELATION-FIRST STRUCTURAL REVIEW CONTEXT" in direct_prompt
        assert "RELATION-FIRST PROPOSED-TREE BRIEFING" in direct_prompt
        assert "DependencyService" in direct_prompt
        assert oversized_source_marker not in direct_prompt
        assert _estimated_prompt_tokens(direct_prompt) <= (
            _stage1_batch_token_limit(request)
        )
        assert len(agent_service.requests) == 1
        agent_request = agent_service.requests[0]
        assert agent_request.initial_required_tool_name == (
            STAGE1_MINIMAL_REVIEW_CONTEXT_TOOL_NAME
        )
        expected_bindings = {
            tool_name: {"focusPaths": (path,)}
            for tool_name in STAGE1_STRUCTURAL_TOOL_NAMES
        }
        expected_bindings["getReviewFileContent"] = {
            "contextSuppliedPaths": (path,),
        }
        assert agent_request.tool_argument_bindings == expected_bindings
        rag_client.explore_review_context.assert_not_awaited()
        assert events[-1]["state"] == "stage_1_agent_degraded"
        assert "already-retrieved exact proposed-tree context" in (
            events[-1]["message"]
        )
        assert set(rag_state.exact_evidence_by_id) == {evidence_id}
        assert rag_state.deterministic_retrieval_states == ["complete"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_non_agentic_stage1_does_not_fetch_target_head_relation_map(self):
        path = "src/owner.py"
        evidence_id = "relation:" + "e" * 64
        enrichment = MagicMock(
            fileContents=[MagicMock(
                path=path,
                content="owner = True\n",
                skipped=False,
            )],
            fileMetadata=[],
            relationships=[],
        )
        request = _packing_request([path], enrichment)
        request.useMcpTools = True
        request.ragCollectionTarget = "collection"
        request.ragBaseGenerationManifestSha256 = "a" * 64
        request.get_target_head_commit_hash.return_value = "f" * 40
        rag_client = SimpleNamespace(get_structural_relations=AsyncMock(
            return_value={
                "coverage": {"state": "complete"},
                "relations": [{
                    "evidenceId": evidence_id,
                    "kind": "CALLS",
                    "source": "owner",
                    "relation": "calls",
                    "target": "DependencyService",
                    "origin": {"path": path, "line": 1},
                }],
            }
        ))
        prepared = _build_stage_1_prepared_context(request, None, False)
        invoke_review = AsyncMock(return_value=[])
        rag_state = Stage1RagState()

        with patch(
            "service.review.orchestrator.stage_1_file_review."
            "_invoke_stage_1_batch_llm",
            new=invoke_review,
        ):
            issues = await review_file_batch(
                object(),
                request,
                self._batch([path]),
                rag_client=rag_client,
                prepared_context=prepared,
                agent_service=None,
                rag_state=rag_state,
            )

        assert issues == []
        rag_client.get_structural_relations.assert_not_awaited()
        prompt = invoke_review.await_args.args[1]
        assert "PRELOADED STRUCTURAL RELATION MAP" not in prompt
        assert "DependencyService" not in prompt
        assert "exploreReviewContext" not in prompt
        assert invoke_review.await_args.kwargs["agent_service"] is None
        assert rag_state.exact_evidence_by_id == {}
        assert rag_state.deterministic_retrieval_states == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_structural_agent_observation_is_candidate_and_rag_evidence(self):
        path = "src/owner.py"
        evidence_id = "relation:" + "d" * 64
        enrichment = MagicMock(
            fileContents=[MagicMock(
                path=path,
                content="dangerous_call()\n",
                skipped=False,
            )],
            fileMetadata=[],
            relationships=[],
        )
        request = _bind_exact_proposed_tree(
            _packing_request([path], enrichment)
        )
        request.useMcpTools = True
        prepared = _build_stage_1_prepared_context(request, None, False)
        issue = CodeReviewIssue(
            id="agent-issue",
            severity="HIGH",
            category="BUG",
            file=path,
            line=1,
            title="Graph-confirmed defect",
            reason="The exact related contract rejects this call.",
            suggestedFixDescription="Satisfy the related contract.",
            codeSnippet="dangerous_call()",
            evidenceRefs=[evidence_id],
        )
        relation = {
            "evidenceId": evidence_id,
            "kind": "CALLS",
            "source": "owner",
            "relation": "calls",
            "target": "DependencyService",
            "origin": {"path": path, "line": 1},
        }

        class AgentService:
            available_tool_names = STAGE1_AGENT_TOOL_NAMES

            def __init__(self):
                self.requests = []

            async def execute(self, agent_request):
                self.requests.append(agent_request)
                return SimpleNamespace(
                    output=FileReviewBatchOutput(reviews=[FileReviewOutput(
                        file=path,
                        analysis_summary="Exact relation confirms a defect.",
                        issues=[issue],
                        confidence="HIGH",
                    )]),
                    tool_events=_required_structural_tool_events(
                        first_observation=json.dumps({
                                "status": "ready",
                                "snapshot": {
                                    "kind": "proposed_tree",
                                    "baseRevision": "target-head-sha",
                                    "sourceRevision": "source-head-sha",
                                },
                                "coverage": {"state": "complete"},
                                "evidence": {
                                    "relations": [
                                        relation,
                                        {**relation, "evidenceId": "REL-not-canonical"},
                                    ],
                                },
                            }),
                    ) + (
                        SimpleNamespace(
                            action=SimpleNamespace(tool="getReviewFileContent"),
                            observation={"relations": [{
                                **relation,
                                "evidenceId": "relation:" + "e" * 64,
                            }]},
                        ),
                    ),
                )

        class DirectLlmMustNotRun:
            def with_structured_output(self, _schema):
                raise AssertionError("direct fallback must not run")

        agent_service = AgentService()
        ledger = CandidateEvidenceLedger()
        rag_state = Stage1RagState()
        issues = await review_file_batch(
            DirectLlmMustNotRun(),
            request,
            self._batch([path]),
            rag_client=object(),
            prepared_context=prepared,
            agent_service=agent_service,
            candidate_ledger=ledger,
            rag_state=rag_state,
        )

        assert issues == [issue]
        record = ledger.record_for(issue)
        assert record is not None
        assert set(record.visible_evidence_by_id) == {evidence_id}
        assert set(rag_state.exact_evidence_by_id) == {evidence_id}
        assert rag_state.deterministic_retrieval_states == ["complete"]
        assert len(agent_service.requests) == 1
        agent_request = agent_service.requests[0]
        assert agent_request.allowed_tool_names == (
            STAGE1_AGENT_TOOL_NAMES.difference({
                STAGE1_BRANCH_FILE_TOOL_NAME,
            })
        )
        assert agent_request.initial_required_tool_name == (
            STAGE1_MINIMAL_REVIEW_CONTEXT_TOOL_NAME
        )
        expected_bindings = {
            tool_name: {"focusPaths": (path,)}
            for tool_name in STAGE1_STRUCTURAL_TOOL_NAMES
        }
        expected_bindings["getReviewFileContent"] = {
            "contextSuppliedPaths": (path,),
        }
        assert agent_request.tool_argument_bindings == expected_bindings

    @pytest.mark.asyncio(loop_scope="function")
    async def test_agentic_batch_without_structural_tools_is_vcs_only(self):
        path = "src/owner.py"
        enrichment = MagicMock(
            fileContents=[MagicMock(
                path=path,
                content="owner = True\n",
                skipped=False,
            )],
            fileMetadata=[],
            relationships=[],
        )
        request = _packing_request([path], enrichment)
        request.useMcpTools = True
        request.ragEnabled = False
        request.localRepoRevision = "target-head-sha"
        request.projectVcsWorkspace = "tenant"
        request.projectVcsRepoSlug = "repository"
        prepared = _build_stage_1_prepared_context(request, None, False)
        invoke_review = AsyncMock(return_value=[])
        rag_state = Stage1RagState()

        with patch(
            "service.review.orchestrator.stage_1_file_review."
            "_invoke_stage_1_batch_llm",
            invoke_review,
        ):
            issues = await review_file_batch(
                object(),
                request,
                self._batch([path]),
                rag_client=None,
                prepared_context=prepared,
                agent_service=SimpleNamespace(
                    available_tool_names=STAGE1_VCS_TOOL_NAMES,
                ),
                rag_state=rag_state,
            )

        assert issues == []
        invoke_review.assert_awaited_once()
        agent_call = invoke_review.await_args
        prompt = agent_call.args[1]
        assert "## Repository File Tool" in prompt
        assert "PRELOADED STRUCTURAL RELATION MAP" not in prompt
        assert "getBranchFileContent" in prompt
        assert "getReviewFileContent" not in prompt
        assert "getStructuralRelations" not in prompt
        assert "queryCodeGraph" not in prompt
        assert "getStructuralUnit" not in prompt
        assert agent_call.kwargs["agent_allowed_tool_names"] == (
            STAGE1_LEGACY_VCS_TOOL_NAMES
        )
        assert rag_state.exact_evidence_by_id == {}
        assert rag_state.deterministic_retrieval_states == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_agentic_batch_registers_one_agent_phase(self):
        path = "src/owner.py"
        enrichment = MagicMock(
            fileContents=[MagicMock(
                path=path,
                content="dangerous_call()\n",
                skipped=False,
            )],
            fileMetadata=[],
            relationships=[],
        )
        request = _bind_exact_proposed_tree(
            _packing_request([path], enrichment)
        )
        request.useMcpTools = True
        prepared = _build_stage_1_prepared_context(request, None, False)
        issue = CodeReviewIssue(
            id="agent-issue",
            severity="MEDIUM",
            category="BUG",
            file=path,
            line=1,
            title="Agent-confirmed defect",
            reason="The changed call is unconditionally unsafe.",
            suggestedFixDescription="Guard the changed call.",
            codeSnippet="dangerous_call()",
        )
        invoke_review = AsyncMock(return_value=[issue])

        ledger = CandidateEvidenceLedger()
        with patch(
            "service.review.orchestrator.stage_1_file_review."
            "_invoke_stage_1_batch_llm",
            new=invoke_review,
        ):
            issues = await review_file_batch(
                object(),
                request,
                self._batch([path]),
                rag_client=object(),
                prepared_context=prepared,
                agent_service=object(),
                candidate_ledger=ledger,
            )

        assert issues == [issue]
        records = ledger.summary()["records"]
        assert len(records) == 1
        assert records[0]["visibleEvidenceIds"] == []
        invoke_review.assert_awaited_once()
        agent_call = invoke_review.await_args
        assert agent_call.kwargs["agent_phase"] == "primary"
        assert agent_call.kwargs["agent_allowed_tool_names"] == (
            STAGE1_AGENT_TOOL_NAMES.difference({
                STAGE1_BRANCH_FILE_TOOL_NAME,
            })
        )

# ── create_smart_batches_wrapper ─────────────────────────────────

class TestCreateSmartBatchesWrapper:
    def _make_plan(self, paths):
        files = [ReviewFile(path=p, focus_areas=[], risk_level="MEDIUM") for p in paths]
        return [FileGroup(group_id="g0", priority="HIGH", rationale="test", files=files)]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_fallback_when_no_processed_diff(self):
        groups = self._make_plan(["a.py", "b.py"])
        result = await create_smart_batches_wrapper(
            file_groups=groups,
            processed_diff=None,
            request=MagicMock(),
            rag_client=None,
        )
        assert len(result) >= 1
        # Each item is a dict with 'file' key
        for batch in result:
            for item in batch:
                assert "file" in item

    @pytest.mark.asyncio(loop_scope="function")
    async def test_single_file(self):
        groups = self._make_plan(["a.py"])
        result = await create_smart_batches_wrapper(
            file_groups=groups,
            processed_diff=None,
            request=MagicMock(),
            rag_client=None,
        )
        assert len(result) == 1
        assert len(result[0]) == 1

    @patch("service.review.orchestrator.stage_1_file_review.create_smart_batches_async")
    @pytest.mark.asyncio(loop_scope="function")
    async def test_uses_smart_batches_when_available(self, mock_smart):
        mock_smart.return_value = None  # Force fallback
        groups = self._make_plan(["a.py", "b.py"])
        result = await create_smart_batches_wrapper(
            file_groups=groups,
            processed_diff=MagicMock(),
            request=MagicMock(enrichmentData=None),
            rag_client=None,
        )
        assert [
            [item["file"].path for item in batch]
            for batch in result
        ] == [["a.py"], ["b.py"]]

    @patch("service.review.orchestrator.stage_1_file_review.create_smart_batches_async")
    @pytest.mark.asyncio(loop_scope="function")
    async def test_caps_stage_1_batch_token_budget_for_latency(self, mock_smart):
        groups = self._make_plan(["a.py", "b.py"])
        mock_smart.return_value = [[{"file": groups[0].files[0], "priority": "MEDIUM"}]]
        request = MagicMock(
            enrichmentData=None,
            maxAllowedTokens=200000,
            projectWorkspace="ws",
            projectNamespace="proj",
        )
        request.get_rag_branch.return_value = "feature"
        request.get_rag_base_branch.return_value = "main"

        result = await create_smart_batches_wrapper(
            file_groups=groups,
            processed_diff=MagicMock(),
            request=request,
            rag_client=None,
        )

        assert result == mock_smart.return_value
        assert mock_smart.call_args.kwargs["max_allowed_tokens"] == 60000

    @patch("service.review.orchestrator.stage_1_file_review.create_smart_batches_async")
    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_target_branch_uses_local_grouping_without_rag(self, mock_smart):
        groups = self._make_plan(["a.py"])
        mock_smart.return_value = [[{"file": groups[0].files[0], "priority": "MEDIUM"}]]
        request = MagicMock(
            enrichmentData=None,
            maxAllowedTokens=200000,
            projectWorkspace="ws",
            projectNamespace="proj",
        )
        request.get_rag_branch.return_value = None
        request.get_rag_base_branch.return_value = None

        result = await create_smart_batches_wrapper(
            file_groups=groups,
            processed_diff=MagicMock(),
            request=request,
            rag_client=MagicMock(),
        )

        assert result == mock_smart.return_value
        assert mock_smart.call_args.kwargs["branches"] == []
        assert mock_smart.call_args.kwargs["rag_client"] is None

    @patch("service.review.orchestrator.stage_1_file_review.create_smart_batches_async")
    @pytest.mark.asyncio(loop_scope="function")
    async def test_exact_receipts_disable_unbound_batching_rag(self, mock_smart):
        groups = self._make_plan(["a.py"])
        mock_smart.return_value = [[{
            "file": groups[0].files[0],
            "priority": "MEDIUM",
        }]]
        request = MagicMock(
            enrichmentData=None,
            maxAllowedTokens=200000,
            projectWorkspace="ws",
            projectNamespace="proj",
            ragBaseGenerationManifestSha256="a" * 64,
        )
        request.get_rag_branch.return_value = "main"
        request.get_rag_base_branch.return_value = "main"

        result = await create_smart_batches_wrapper(
            file_groups=groups,
            processed_diff=MagicMock(),
            request=request,
            rag_client=MagicMock(),
        )

        assert result == mock_smart.return_value
        assert mock_smart.call_args.kwargs["rag_client"] is None

    @patch("service.review.orchestrator.stage_1_file_review.create_smart_batches_async")
    @pytest.mark.asyncio(loop_scope="function")
    async def test_pr_merge_base_fallback_cannot_enable_graph_batching(
        self,
        mock_smart,
    ):
        groups = self._make_plan(["a.py"])
        mock_smart.return_value = [[{
            "file": groups[0].files[0],
            "priority": "MEDIUM",
        }]]
        request = _bind_exact_proposed_tree(
            _packing_request(["a.py"], MagicMock())
        )
        request.pullRequestId = 7
        request.targetHeadCommitHash = None
        request.baseCommitHash = "merge-base"
        request.localRepoRevision = "merge-base"
        request.localRepoTargetBranch = "main"
        request.useMcpTools = True

        result = await create_smart_batches_wrapper(
            file_groups=groups,
            processed_diff=MagicMock(),
            request=request,
            rag_client=MagicMock(),
        )

        assert result == mock_smart.return_value
        assert mock_smart.call_args.kwargs["rag_client"] is None
        assert mock_smart.call_args.kwargs["structural_binding"] is None


class TestStage1Scheduling:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_all_isolated_files_run_when_profile_cap_is_lower_than_core_batches(self):
        files = [
            ReviewFile(
                path=f"src/f{index}.py",
                focus_areas=[],
                risk_level="MEDIUM",
            )
            for index in range(15)
        ]
        batches = [
            [{"file": file, "priority": "MEDIUM"}]
            for file in files
        ]
        request = MagicMock(
            deltaDiff=None,
            rawDiff="",
            taskContext=None,
            enrichmentData=None,
            changedFiles=[file.path for file in files],
            useMcpTools=True,
            currentCommitHash="source-revision",
            commitHash="source-revision",
        )
        profile = SimpleNamespace(
            invocation_cap=lambda stage: {
                "stage_1_total": 12,
                "stage_1_per_unit": 2,
            }[stage]
        )
        agent_service = object()
        reviewed_paths = []

        async def fake_batches(**kwargs):
            return batches

        async def fake_review(_llm, _request, batch, *_args, **kwargs):
            assert kwargs["agent_service"] is agent_service
            assert len(batch) == 1
            reviewed_paths.append(batch[0]["file"].path)
            return []

        with patch(
            "service.review.orchestrator.stage_1_file_review.create_smart_batches_wrapper",
            side_effect=fake_batches,
        ), patch(
            "service.review.orchestrator.stage_1_file_review.review_file_batch",
            side_effect=fake_review,
        ):
            issues = await execute_stage_1_file_reviews(
                llm=MagicMock(),
                request=request,
                plan=ReviewPlan(
                    analysis_summary="x",
                    file_groups=[],
                    cross_file_concerns=[],
                ),
                rag_client=None,
                max_parallel=15,
                inference_profile=profile,
                agent_service=agent_service,
            )

        assert issues == []
        assert set(reviewed_paths) == {file.path for file in files}
        assert len(reviewed_paths) == 15

    @pytest.mark.asyncio(loop_scope="function")
    async def test_batches_run_with_bounded_concurrency(self):
        files = [ReviewFile(path=f"src/f{i}.py", focus_areas=[], risk_level="MEDIUM") for i in range(5)]
        batches = [[{"file": f, "priority": "MEDIUM"}] for f in files]
        request = MagicMock()
        request.deltaDiff = None
        request.rawDiff = ""
        request.taskContext = None
        request.enrichmentData = None
        request.changedFiles = [f.path for f in files]
        release = asyncio.Event()
        two_running = asyncio.Event()
        state = {"active": 0, "maximum": 0, "started": 0}

        async def fake_batches(**kwargs):
            return batches

        async def fake_review(*args, **kwargs):
            state["active"] += 1
            state["started"] += 1
            state["maximum"] = max(state["maximum"], state["active"])
            if state["active"] == 2:
                two_running.set()
            try:
                await release.wait()
                return []
            finally:
                state["active"] -= 1

        with patch(
            "service.review.orchestrator.stage_1_file_review.create_smart_batches_wrapper",
            side_effect=fake_batches,
        ), patch(
            "service.review.orchestrator.stage_1_file_review.review_file_batch",
            side_effect=fake_review,
        ):
            review_task = asyncio.create_task(execute_stage_1_file_reviews(
                llm=MagicMock(),
                request=request,
                plan=ReviewPlan(analysis_summary="x", file_groups=[], cross_file_concerns=[]),
                rag_client=None,
                max_parallel=2,
            ))
            await asyncio.wait_for(two_running.wait(), timeout=1)
            await asyncio.sleep(0)

            assert state["active"] == 2
            assert state["started"] == 2

            release.set()
            issues = await asyncio.wait_for(review_task, timeout=1)

        assert issues == []
        assert state == {
            "active": 0,
            "maximum": 2,
            "started": len(files),
        }

    @pytest.mark.asyncio(loop_scope="function")
    async def test_reverse_completion_keeps_batch_order_and_completes_units(self):
        files = [
            ReviewFile(
                path=f"src/f{i}.py",
                focus_areas=[],
                risk_level="MEDIUM",
            )
            for i in range(3)
        ]
        batches = [[{"file": file, "priority": "MEDIUM"}] for file in files]
        request = MagicMock(
            deltaDiff=None,
            rawDiff="",
            taskContext=None,
            enrichmentData=None,
            changedFiles=[file.path for file in files],
        )
        state = Stage1ReviewUnitState()

        async def fake_batches(**kwargs):
            return batches

        async def fake_review(_llm, _request, batch, *_args, **_kwargs):
            index = int(batch[0]["file"].path.removesuffix(".py")[-1])
            await asyncio.sleep((2 - index) * 0.02)
            return [batch[0]["file"].path]

        with patch(
            "service.review.orchestrator.stage_1_file_review.create_smart_batches_wrapper",
            side_effect=fake_batches,
        ), patch(
            "service.review.orchestrator.stage_1_file_review.review_file_batch",
            side_effect=fake_review,
        ):
            issues = await execute_stage_1_file_reviews(
                llm=MagicMock(),
                request=request,
                plan=ReviewPlan(
                    analysis_summary="x",
                    file_groups=[],
                    cross_file_concerns=[],
                ),
                rag_client=None,
                max_parallel=3,
                review_unit_state=state,
            )

        assert issues == [file.path for file in files]
        state.assert_complete()
        assert len(state.completed_unit_ids) == 3

    @pytest.mark.asyncio(loop_scope="function")
    async def test_any_failed_batch_fails_the_whole_stage(self):
        files = [ReviewFile(path=f"src/f{i}.py", focus_areas=[], risk_level="MEDIUM") for i in range(2)]
        batches = [[{"file": file, "priority": "MEDIUM"}] for file in files]
        request = MagicMock(
            deltaDiff=None,
            rawDiff="",
            taskContext=None,
            enrichmentData=None,
            changedFiles=[file.path for file in files],
        )

        async def fake_batches(**kwargs):
            return batches

        async def fake_review(_llm, _request, batch, *_args, **_kwargs):
            if batch[0]["file"].path.endswith("f0.py"):
                raise RuntimeError("provider timeout")
            await asyncio.sleep(0.1)
            return []

        with patch(
            "service.review.orchestrator.stage_1_file_review.create_smart_batches_wrapper",
            side_effect=fake_batches,
        ), patch(
            "service.review.orchestrator.stage_1_file_review.review_file_batch",
            side_effect=fake_review,
        ):
            with pytest.raises(RuntimeError, match="Stage 1 review is incomplete"):
                await execute_stage_1_file_reviews(
                    llm=MagicMock(),
                    request=request,
                    plan=ReviewPlan(analysis_summary="x", file_groups=[], cross_file_concerns=[]),
                    rag_client=None,
                    max_parallel=2,
                )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cancellation_joins_batch_agents_before_propagating(self):
        files = [
            ReviewFile(
                path=f"src/f{index}.py",
                focus_areas=[],
                risk_level="MEDIUM",
            )
            for index in range(2)
        ]
        batches = [
            [{"file": file, "priority": "MEDIUM"}]
            for file in files
        ]
        request = MagicMock(
            deltaDiff=None,
            rawDiff="",
            taskContext=None,
            enrichmentData=None,
            changedFiles=[file.path for file in files],
        )
        all_started = asyncio.Event()
        blocked = asyncio.Event()
        started: set[str] = set()
        cleaned_up: set[str] = set()

        async def fake_batches(**kwargs):
            return batches

        async def fake_review(_llm, _request, batch, *_args, **_kwargs):
            path = batch[0]["file"].path
            started.add(path)
            if len(started) == len(files):
                all_started.set()
            try:
                await blocked.wait()
            finally:
                cleaned_up.add(path)

        with patch(
            "service.review.orchestrator.stage_1_file_review.create_smart_batches_wrapper",
            side_effect=fake_batches,
        ), patch(
            "service.review.orchestrator.stage_1_file_review.review_file_batch",
            side_effect=fake_review,
        ):
            review_task = asyncio.create_task(execute_stage_1_file_reviews(
                llm=MagicMock(),
                request=request,
                plan=ReviewPlan(
                    analysis_summary="x",
                    file_groups=[],
                    cross_file_concerns=[],
                ),
                rag_client=None,
                max_parallel=2,
            ))
            await asyncio.wait_for(all_started.wait(), timeout=1)
            review_task.cancel()

            with pytest.raises(asyncio.CancelledError):
                await review_task

        assert cleaned_up == {file.path for file in files}
