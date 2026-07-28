from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from llm.llm_factory import LLMFactory
from model.dtos import ReviewRequestDto
from model.enrichment import FileContentDto, PrEnrichmentDataDto
from service.review.orchestrator.orchestrator import (
    MultiStageReviewOrchestrator,
    PrIndexPreconditionError,
)
from service.review.prompt_dry_run import PromptCaptureSession
from service.review.prompt_dry_run import capture_review_prompts
from service.review.prompt_dry_run import capture_and_store_review_prompts
from service.review.review_service import ReviewService
from utils.diff_processor import DiffProcessor, HunkDisposition
from utils.hunk_coverage import ReviewManifestPreconditionError
from .prompt_dry_run_neutral_fixture import (
    BASE_REVISION,
    HEAD_REVISION,
    SECRET_API_KEY,
    DeterministicRagSpy,
    mixed_language_request as _mixed_language_request,
    neutral_request as _request,
)


@pytest.mark.asyncio
async def test_non_reviewable_hunk_manifest_completes_without_model_stage():
    request = _request()
    processed = DiffProcessor().process(request.rawDiff)
    generated = processed.files[0]
    generated.plugin_disposition = "generated"
    generated.is_skipped = True
    generated.skip_reason = "Plugin file policy: generated"
    generated.hunks = [
        replace(hunk, disposition=HunkDisposition.GENERATED)
        for hunk in generated.hunks
    ]
    rag = DeterministicRagSpy()
    orchestrator = MultiStageReviewOrchestrator(
        llm=object(),
        mcp_client=None,
        rag_client=rag,
    )

    result = await orchestrator.orchestrate_review(
        request,
        processed_diff=processed,
    )

    assert result["issues"] == []
    assert "No text source hunks required model review" in result["comment"]
    assert rag.index_requests == []
    assert rag.requests == []
    assert rag.semantic_requests == []


@pytest.mark.asyncio
async def test_metadata_only_diff_completes_without_model_or_rag_stage():
    request = _request().model_copy(update={
        "rawDiff": (
            "diff --git a/src/file_0.py b/src/file_0.py\n"
            "old mode 100644\n"
            "new mode 100755\n"
        ),
    })
    processed = DiffProcessor().process(request.rawDiff)
    rag = DeterministicRagSpy()
    orchestrator = MultiStageReviewOrchestrator(
        llm=object(),
        mcp_client=None,
        rag_client=rag,
    )

    result = await orchestrator.orchestrate_review(
        request,
        processed_diff=processed,
    )

    assert result["issues"] == []
    assert "No text source hunks required model review" in result["comment"]
    assert rag.index_requests == []
    assert rag.requests == []
    assert rag.semantic_requests == []


@pytest.mark.asyncio
async def test_diff_path_mismatch_fails_before_indexing_or_stage_zero(
    monkeypatch,
):
    request = _request().model_copy(update={
        "changedFiles": ["src/file_0.py", "src/missing.py"],
    })
    processed = DiffProcessor().process(request.rawDiff)
    rag = DeterministicRagSpy()
    stage_0 = AsyncMock()
    monkeypatch.setattr(
        "service.review.orchestrator.orchestrator.execute_stage_0_planning",
        stage_0,
    )
    orchestrator = MultiStageReviewOrchestrator(
        llm=object(),
        mcp_client=None,
        rag_client=rag,
    )

    with pytest.raises(ReviewManifestPreconditionError, match="src/missing.py"):
        await orchestrator.orchestrate_review(
            request,
            processed_diff=processed,
        )

    assert rag.index_requests == []
    stage_0.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_changed_text_fails_before_indexing_or_stage_zero(
    monkeypatch,
):
    request = _request().model_copy(update={
        "rawDiff": "\n".join([
            "diff --git a/src/file_0.py b/src/file_0.py",
            "--- a/src/file_0.py",
            "+++ b/src/file_0.py",
            "@@ invalid hunk header @@",
            "-value_0 = 0",
            "+value_0 = 1",
            "",
        ]),
    })
    processed = DiffProcessor().process(request.rawDiff)
    rag = DeterministicRagSpy()
    stage_0 = AsyncMock()
    monkeypatch.setattr(
        "service.review.orchestrator.orchestrator.execute_stage_0_planning",
        stage_0,
    )
    orchestrator = MultiStageReviewOrchestrator(
        llm=object(),
        mcp_client=None,
        rag_client=rag,
    )

    with pytest.raises(ReviewManifestPreconditionError, match="malformed"):
        await orchestrator.orchestrate_review(
            request,
            processed_diff=processed,
        )

    assert rag.index_requests == []
    stage_0.assert_not_awaited()


def _single_language_request(language: str) -> ReviewRequestDto:
    templates = {
        "python": (
            "service/module_{index}.py",
            "value = {index}\n",
            "value = -1",
            "value = {index}",
        ),
        "java": (
            "backend/src/main/java/example/Module{index}.java",
            "package example;\npublic final class Module{index} {{ int value = {index}; }}\n",
            "public final class Module{index} {{ int value = -1; }}",
            "public final class Module{index} {{ int value = {index}; }}",
        ),
        "typescript": (
            "web/src/module_{index}.ts",
            "export const value{index} = {index};\n",
            "export const value{index} = -1;",
            "export const value{index} = {index};",
        ),
    }
    path_template, source_template, old_template, new_template = templates[language]
    sources = {
        path_template.format(index=index): source_template.format(index=index)
        for index in range(6)
    }
    diffs = []
    for index, path in enumerate(sources):
        diffs.append("\n".join([
            f"diff --git a/{path} b/{path}",
            "index 1111111..2222222 100644",
            f"--- a/{path}",
            f"+++ b/{path}",
            "@@ -1 +1 @@",
            f"-{old_template.format(index=index)}",
            f"+{new_template.format(index=index)}",
        ]))
    return _request().model_copy(update={
        "changedFiles": list(sources),
        "rawDiff": "\n".join(diffs) + "\n",
        "enrichmentData": PrEnrichmentDataDto(fileContents=[
            FileContentDto(
                path=path,
                content=content,
                sizeBytes=len(content.encode("utf-8")),
            )
            for path, content in sources.items()
        ]),
        "prTitle": f"Neutral {language} project pipeline gate",
    })


@pytest.mark.asyncio
async def test_dry_run_captures_complete_baseline_without_provider_or_semantic_calls(
    monkeypatch,
):
    def fail_provider_construction(*_args, **_kwargs):
        raise AssertionError("dry-run must not construct an LLM provider")

    monkeypatch.setattr(
        "llm.llm_factory.LLMFactory.create_llm",
        fail_provider_construction,
    )
    rag = DeterministicRagSpy()
    request = _request(use_mcp_tools=True)

    result = await capture_review_prompts(
        request,
        rag,
        include_deterministic_rag=True,
    )

    assert result["dryRun"] is True
    assert result["providerCalls"] == 0
    assert result["providerCallsScope"] == "review-llm-only"
    assert result["embeddingProviderCallsMeasured"] is False
    assert result["providerConstructionGuard"] == {
        "enabled": True,
        "boundary": "LLMFactory.create_llm",
    }
    assert "result" not in result
    assert result["simulation"] == {
        "simulatedFindingsPerFile": 0,
        "simulatedFindingsMaxTotal": 24,
        "simulatedFindingsProduced": 0,
        "fullPipelineContext": False,
        "deterministicRagEnabled": True,
        "deterministicRagRequests": len(rag.requests),
        "semanticRagEnabled": False,
        "duplicationRagEnabled": False,
        "prIndexMutationEnabled": False,
        "mcpToolsEnabled": False,
    }
    assert list(result["promptCountsByStage"]) == ["stage_0", "stage_1", "stage_3"]
    assert [prompt["sequence"] for prompt in result["prompts"]] == [1, 2, 3]
    assert result["promptCount"] == len(result["prompts"])
    assert result["totalCharacterCount"] == sum(
        prompt["characterCount"] for prompt in result["prompts"]
    )
    assert result["qualitySignals"]["stage1"]["promptCount"] == (
        result["promptCountsByStage"]["stage_1"]
    )
    assert result["qualitySignals"]["stage1"][
        "totalEstimatedInputTokens"
    ] == sum(
        prompt["estimatedInputTokens"]
        for prompt in result["prompts"]
        if prompt["stage"] == "stage_1"
    )
    assert result["qualitySignals"]["stage1"][
        "maxEstimatedInputTokens"
    ] > 0
    assert result["qualitySignals"]["stage1"][
        "addedSourceDuplicateOmissions"
    ] == 0
    stage_0 = result["prompts"][0]
    assert stage_0["responseSchema"] == "ReviewPlan"
    assert stage_0["responseSchemaDefinition"]["title"] == "ReviewPlan"
    assert stage_0["characterCount"] > stage_0["renderedPromptCharacterCount"]

    serialized = json.dumps(result)
    assert "value_0 = 1" in serialized
    assert "SHARED_CONTEXT_SENTINEL" in serialized
    assert SECRET_API_KEY not in serialized
    assert request.useMcpTools is True
    assert any("useMcpTools was disabled" in warning for warning in result["warnings"])
    assert any("JSON-repair" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_dry_run_preserves_neutral_mixed_language_manifest(monkeypatch):
    monkeypatch.setattr(
        "llm.llm_factory.LLMFactory.create_llm",
        lambda *_args, **_kwargs: pytest.fail("provider construction is forbidden"),
    )
    request = _mixed_language_request()

    result = await capture_review_prompts(
        request,
        DeterministicRagSpy(),
        include_deterministic_rag=True,
    )

    serialized = json.dumps(result)
    assert result["providerCalls"] == 0
    assert result["promptCountsByStage"]["stage_0"] == 1
    assert result["promptCountsByStage"]["stage_1"] >= 1
    assert result["promptCountsByStage"]["stage_3"] == 1
    assert all(path in serialized for path in request.changedFiles)
    assert "return account.active" in serialized
    assert "record Account(boolean active)" in serialized
    assert "account.active" in serialized
    assert SECRET_API_KEY not in serialized


@pytest.mark.asyncio
async def test_neutral_mixed_language_prompt_replay_is_byte_stable_and_bounded(
    monkeypatch,
):
    monkeypatch.setattr(
        "llm.llm_factory.LLMFactory.create_llm",
        lambda *_args, **_kwargs: pytest.fail("provider construction is forbidden"),
    )
    request = _mixed_language_request()

    first = await capture_review_prompts(
        request,
        DeterministicRagSpy(),
        include_deterministic_rag=True,
    )
    second = await capture_review_prompts(
        request,
        DeterministicRagSpy(),
        include_deterministic_rag=True,
    )

    assert first["prompts"] == second["prompts"]
    assert first["qualitySignals"] == second["qualitySignals"]
    assert first["providerCalls"] == second["providerCalls"] == 0
    assert first["qualitySignals"]["stage1"]["maxEstimatedInputTokens"] < 20_000


def test_neutral_mixed_language_prompt_replay_is_stable_across_hash_seeds():
    """Prove canonical prompt inputs outside one interpreter's set ordering."""
    tests_dir = Path(__file__).resolve().parent
    source_dir = tests_dir.parent / "src"
    plugin_contracts = (
        tests_dir.parents[2] / "analysis-plugins" / "contracts" / "python"
    )
    script = """
import asyncio
import hashlib
import json

from service.review.prompt_dry_run import capture_review_prompts
from prompt_dry_run_neutral_fixture import (
    DeterministicRagSpy,
    mixed_language_request,
)

async def main():
    report = await capture_review_prompts(
        mixed_language_request(),
        DeterministicRagSpy(),
        include_deterministic_rag=True,
        simulated_findings_per_file=1,
    )
    assert report["providerCalls"] == 0
    payload = {
        "prompts": report["prompts"],
        "qualitySignals": report["qualitySignals"],
        "promptCountsByStage": report["promptCountsByStage"],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    print(hashlib.sha256(encoded).hexdigest())

asyncio.run(main())
"""
    digests = []
    for seed in ("1", "777"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        environment["PYTHONPATH"] = os.pathsep.join(
            (
                str(source_dir),
                str(tests_dir),
                str(plugin_contracts),
                environment.get("PYTHONPATH", ""),
            )
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=source_dir,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        digests.append(completed.stdout.strip().splitlines()[-1])

    assert digests[0]
    assert digests[0] == digests[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["python", "java", "typescript"])
async def test_neutral_single_language_project_replays_are_stable_and_bounded(
    monkeypatch,
    language,
):
    monkeypatch.setattr(
        "llm.llm_factory.LLMFactory.create_llm",
        lambda *_args, **_kwargs: pytest.fail("provider construction is forbidden"),
    )
    request = _single_language_request(language)

    first = await capture_review_prompts(
        request,
        DeterministicRagSpy(),
        include_deterministic_rag=True,
    )
    second = await capture_review_prompts(
        request,
        DeterministicRagSpy(),
        include_deterministic_rag=True,
    )

    serialized = json.dumps(first)
    rendered = "\n".join(prompt["renderedPrompt"] for prompt in first["prompts"])
    assert first["prompts"] == second["prompts"]
    assert first["qualitySignals"] == second["qualitySignals"]
    assert first["providerCalls"] == second["providerCalls"] == 0
    assert all(path in serialized for path in request.changedFiles)
    assert all(
        file_info.content.strip() in rendered
        for file_info in request.enrichmentData.fileContents
    )
    assert first["qualitySignals"]["stage1"]["maxEstimatedInputTokens"] < 20_000


@pytest.mark.asyncio
async def test_dry_run_fails_closed_on_hidden_provider_construction(monkeypatch):
    async def escape_to_provider_factory(*_args, **_kwargs):
        LLMFactory.create_llm(
            "provider-model",
            "OPENAI",
            SECRET_API_KEY,
        )

    monkeypatch.setattr(
        MultiStageReviewOrchestrator,
        "orchestrate_review",
        escape_to_provider_factory,
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "LLM provider construction is forbidden.*review prompt dry run"
        ),
    ):
        await capture_review_prompts(
            _request(),
            DeterministicRagSpy(),
        )


@pytest.mark.asyncio
async def test_synthetic_findings_capture_conditional_review_prompts(monkeypatch):
    monkeypatch.setattr(
        "llm.llm_factory.LLMFactory.create_llm",
        lambda *_args, **_kwargs: pytest.fail("provider construction is forbidden"),
    )

    result = await capture_review_prompts(
        _request(file_count=6),
        DeterministicRagSpy(),
        include_deterministic_rag=False,
        simulated_findings_per_file=1,
    )

    stages = result["promptCountsByStage"]
    assert stages["stage_0"] == 1
    assert stages["stage_1"] >= 1
    assert stages["verification"] == 1
    assert stages["stage_2"] == 1
    assert stages.get("deduplication", 0) == 0
    assert stages["stage_3"] == 1
    assert result["providerCalls"] == 0
    assert result["simulation"]["deterministicRagRequests"] == 0
    assert result["simulation"]["simulatedFindingsProduced"] == 6


def test_synthetic_findings_are_deterministically_bounded_across_large_pr():
    request = _request(file_count=130)
    session = PromptCaptureSession(
        request=request,
        simulated_findings_per_file=6,
        simulated_findings_max_total=24,
    )
    allocation = {
        path: session._simulated_finding_count(path)
        for path in reversed(request.changedFiles)
    }

    assert sum(allocation.values()) == 24
    assert max(allocation.values()) == 1
    assert [
        path for path, count in sorted(allocation.items()) if count
    ] == sorted(request.changedFiles)[:24]


@pytest.mark.asyncio
async def test_full_pipeline_capture_persists_real_context_artifact(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("ANALYSIS_PROMPT_DRY_RUN_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(
        "llm.llm_factory.LLMFactory.create_llm",
        lambda *_args, **_kwargs: pytest.fail("provider construction is forbidden"),
    )
    rag = DeterministicRagSpy()
    forwarded_events = []
    request = _request().model_copy(update={
        "promptDryRun": True,
        "promptDryRunId": "queue-job-123",
    })

    summary = await capture_and_store_review_prompts(
        request,
        rag,
        simulated_findings_per_file=1,
        event_callback=forwarded_events.append,
    )

    artifact_path = tmp_path / summary["promptArtifact"]["filename"]
    report = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert summary["dryRun"] is True
    assert summary["issues"] == []
    assert "prompts" not in summary
    assert summary["promptArtifact"]["providerCalls"] == 0
    assert summary["promptArtifact"][
        "providerCallsScope"
    ] == "review-llm-only"
    assert summary["promptArtifact"][
        "embeddingProviderCallsMeasured"
    ] is False
    assert summary["promptArtifact"]["providerConstructionGuard"] == {
        "enabled": True,
        "boundary": "LLMFactory.create_llm",
    }
    assert summary["promptArtifact"]["pipeline"]["completed"] is True
    assert report["providerCalls"] == 0
    assert report["simulation"]["fullPipelineContext"] is True
    assert report["simulation"]["semanticRagEnabled"] is True
    assert report["pipeline"]["completed"] is True
    assert report["pipeline"]["evidence"]["hunkCoverage"]["completed"] == 1
    assert report["pipeline"]["evidence"]["reviewUnits"] == {
        "registered": 1,
        "completed": 1,
    }
    assert report["pipeline"]["evidence"]["candidates"]["generated"] == 1
    assert report["pipeline"]["evidence"]["candidates"]["published"] == 1
    assert report["pipeline"]["evidence"]["candidates"]["rejected"] == 0
    candidate_record = report["pipeline"]["evidence"]["candidates"]["records"][0]
    assert candidate_record["stage"] == "stage_1"
    assert len(candidate_record["reviewUnitIds"]) == 1
    assert len(candidate_record["promptHunkIds"]) == 1
    assert candidate_record["anchorHunkIds"] == candidate_record["promptHunkIds"]
    assert candidate_record["evidenceRefs"] == []
    assert candidate_record["visibleEvidenceIds"]
    assert candidate_record["terminalState"] == "published"
    assert report["pipeline"]["evidence"]["hunkReceipts"] == [{
        "hunkId": candidate_record["anchorHunkIds"][0],
        "path": "src/file_0.py",
        "promptCandidateIds": [candidate_record["candidateId"]],
        "anchoredCandidateIds": [candidate_record["candidateId"]],
        "publishedCandidateIds": [candidate_record["candidateId"]],
        "rejectedCandidateIds": [],
        "outcome": "published",
    }]
    assert report["pipeline"]["evidence"]["retrieval"][
        "deterministicStates"
    ] == ["complete"]
    assert report["pluginDiagnostics"] == {
        "count": 0,
        "exceptionCount": 0,
        "items": [],
    }
    assert summary["promptArtifact"]["pluginDiagnostics"] == {
        "count": 0,
        "exceptionCount": 0,
    }
    assembly = report["promptAssemblyDiagnostics"]["stage1"]
    assert len(assembly) == report["promptCountsByStage"]["stage_1"]
    assert assembly[0]["batchPaths"] == ["src/file_0.py"]
    assert assembly[0]["totalPromptChars"] > 0
    assert assembly[0]["currentSourceChars"] > 0
    assert assembly[0]["diffChars"] > 0
    assert assembly[0]["ragChars"] > 0
    assert assembly[0]["pluginChars"] <= 6_000
    assert "promptAssemblyDiagnostics" not in summary["promptArtifact"]
    assert report["pipeline"]["events"] == forwarded_events
    assert any(
        event.get("state") == "review_evidence_completed"
        for event in forwarded_events
    )
    assert report["reviewIdentity"]["targetBranch"] == "main"
    assert report["reviewIdentity"]["sourceBranch"] == "feature/dry-run"
    assert report["reviewIdentity"]["headRevision"] == HEAD_REVISION
    assert report["reviewIdentity"]["baseRevision"] == BASE_REVISION
    assert report["reviewIdentity"]["changedFiles"] == ["src/file_0.py"]
    assert summary["promptArtifact"]["qualitySignals"] == report["qualitySignals"]
    assert SECRET_API_KEY not in artifact_path.read_text(encoding="utf-8")
    assert rag.index_requests
    assert rag.index_requests[0]["source_revision"] == HEAD_REVISION
    assert rag.index_requests[0]["base_revision"] == BASE_REVISION
    assert {
        file_info["content_state"]
        for file_info in rag.index_requests[0]["files"]
    } == {"complete"}
    # The normal review pipeline keeps the PR overlay available for subsequent
    # context queries. Dry-run exercises the same RAG lifecycle.
    assert rag.delete_requests == []


@pytest.mark.asyncio
async def test_full_pipeline_capture_fails_when_pr_overlay_is_not_indexed(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("ANALYSIS_PROMPT_DRY_RUN_OUTPUT_DIR", str(tmp_path))
    rag = DeterministicRagSpy()

    async def reject_overlay(**kwargs):
        rag.index_requests.append(kwargs)
        return {
            "status": "error",
            "status_code": 409,
            "error": "target branch must be reindexed",
        }

    rag.index_pr_files = reject_overlay
    request = _request().model_copy(update={
        "promptDryRun": True,
        "promptDryRunId": "queue-job-failure",
    })

    with pytest.raises(RuntimeError, match="target branch must be reindexed"):
        await capture_and_store_review_prompts(
            request,
            rag,
            simulated_findings_per_file=1,
        )

    assert rag.index_requests
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_updates", "expected_field"),
    [
        ({"targetBranchName": None}, "targetBranchName"),
        ({"sourceBranchName": None}, "sourceBranchName"),
        ({"currentCommitHash": None, "commitHash": None}, "currentCommitHash"),
        ({"currentCommitHash": "HEAD"}, "currentCommitHash"),
        ({"currentCommitHash": "abc123"}, "currentCommitHash"),
        ({"baseCommitHash": None}, "baseCommitHash"),
    ],
)
async def test_review_snapshot_identity_fails_before_indexing_or_stage_zero(
    monkeypatch,
    request_updates,
    expected_field,
):
    rag = DeterministicRagSpy()
    stage_0 = AsyncMock()
    monkeypatch.setattr(
        "service.review.orchestrator.orchestrator.execute_stage_0_planning",
        stage_0,
    )
    request = _request().model_copy(update=request_updates)
    orchestrator = MultiStageReviewOrchestrator(
        llm=object(),
        mcp_client=None,
        rag_client=rag,
    )

    with pytest.raises(PrIndexPreconditionError, match=expected_field):
        await orchestrator.orchestrate_review(
            request,
            processed_diff=DiffProcessor().process(request.rawDiff),
        )

    assert rag.index_requests == []
    assert rag.requests == []
    assert rag.semantic_requests == []
    stage_0.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_service_rejects_snapshot_before_provider_construction(
    monkeypatch,
):
    monkeypatch.setattr(
        "llm.llm_factory.LLMFactory.create_llm",
        lambda *_args, **_kwargs: pytest.fail("provider construction is forbidden"),
    )
    service = ReviewService()
    request = _request().model_copy(update={"currentCommitHash": "HEAD"})

    with pytest.raises(PrIndexPreconditionError, match="currentCommitHash"):
        await service.process_review_request(request)


@pytest.mark.asyncio
async def test_review_service_rejects_diff_manifest_before_provider_construction(
    monkeypatch,
):
    monkeypatch.setattr(
        "llm.llm_factory.LLMFactory.create_llm",
        lambda *_args, **_kwargs: pytest.fail("provider construction is forbidden"),
    )
    service = ReviewService()
    request = _request().model_copy(update={
        "changedFiles": ["src/file_0.py", "src/not-acquired.py"],
    })

    with pytest.raises(
        ReviewManifestPreconditionError,
        match="src/not-acquired.py",
    ):
        await service.process_review_request(request)


@pytest.mark.asyncio
async def test_pr_overlay_receives_one_exact_snapshot_identity():
    rag = DeterministicRagSpy()
    request = _request()
    orchestrator = MultiStageReviewOrchestrator(
        llm=object(),
        mcp_client=None,
        rag_client=rag,
    )

    await orchestrator._index_pr_files(
        request,
        DiffProcessor().process(request.rawDiff),
    )

    assert len(rag.index_requests) == 1
    assert rag.index_requests[0]["branch"] == "main"
    assert rag.index_requests[0]["base_branch"] == "main"
    assert rag.index_requests[0]["source_revision"] == HEAD_REVISION
    assert rag.index_requests[0]["base_revision"] == BASE_REVISION


@pytest.mark.asyncio
async def test_pr_index_precondition_fails_before_stage_0_for_normal_review(
    monkeypatch,
):
    rag = DeterministicRagSpy()

    async def reject_overlay(**kwargs):
        rag.index_requests.append(kwargs)
        return {
            "status": "error",
            "status_code": 409,
            "error": (
                "target branch is missing repository-analysis snapshots for "
                "magento; reindex it before review"
            ),
        }

    rag.index_pr_files = reject_overlay
    stage_0 = AsyncMock()
    monkeypatch.setattr(
        "service.review.orchestrator.orchestrator.execute_stage_0_planning",
        stage_0,
    )
    request = _request()
    orchestrator = MultiStageReviewOrchestrator(
        llm=object(),
        mcp_client=None,
        rag_client=rag,
    )

    with pytest.raises(
        PrIndexPreconditionError,
        match="No review-model stage was started",
    ):
        await orchestrator.orchestrate_review(
            request,
            processed_diff=DiffProcessor().process(request.rawDiff),
        )

    assert rag.index_requests
    stage_0.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_overlay_review_groups_are_retained_for_stage_zero_planning():
    rag = DeterministicRagSpy()

    async def index_with_groups(**kwargs):
        rag.index_requests.append(kwargs)
        return {
            "status": "indexed",
            "chunks_indexed": 4,
            "review_groups": [
                ["src/file_0.py", "src/file_1.py"],
                ["src/file_1.py", "src/file_2.py"],
            ],
        }

    rag.index_pr_files = index_with_groups
    request = _request(file_count=3)
    orchestrator = MultiStageReviewOrchestrator(
        llm=object(),
        mcp_client=None,
        rag_client=rag,
    )

    await orchestrator._index_pr_files(
        request,
        DiffProcessor().process(request.rawDiff),
    )

    assert orchestrator._repository_review_groups == (
        ("src/file_0.py", "src/file_1.py"),
        ("src/file_1.py", "src/file_2.py"),
    )


@pytest.mark.asyncio
async def test_pr_overlay_marks_unenriched_diff_as_partial_evidence():
    rag = DeterministicRagSpy()
    request = _request().model_copy(update={
        "enrichmentData": PrEnrichmentDataDto(fileContents=[]),
    })
    orchestrator = MultiStageReviewOrchestrator(
        llm=object(),
        mcp_client=None,
        rag_client=rag,
    )

    await orchestrator._index_pr_files(
        request,
        DiffProcessor().process(request.rawDiff),
    )

    assert len(rag.index_requests) == 1
    indexed_file = rag.index_requests[0]["files"][0]
    assert indexed_file["path"] == "src/file_0.py"
    assert indexed_file["change_type"] == "MODIFIED"
    assert indexed_file["content_state"] == "partial_diff"
    assert indexed_file["content"].startswith(
        "diff --git a/src/file_0.py b/src/file_0.py"
    )


@pytest.mark.asyncio
async def test_pr_overlay_preserves_empty_post_change_source_as_complete():
    rag = DeterministicRagSpy()
    request = _request().model_copy(update={
        "enrichmentData": PrEnrichmentDataDto(fileContents=[
            FileContentDto(
                path="src/file_0.py",
                content="",
                sizeBytes=0,
            ),
        ]),
    })
    orchestrator = MultiStageReviewOrchestrator(
        llm=object(),
        mcp_client=None,
        rag_client=rag,
    )

    await orchestrator._index_pr_files(
        request,
        DiffProcessor().process(request.rawDiff),
    )

    indexed_file = rag.index_requests[0]["files"][0]
    assert indexed_file["path"] == "src/file_0.py"
    assert indexed_file["content"] == ""
    assert indexed_file["content_state"] == "complete"


@pytest.mark.asyncio
async def test_pr_overlay_does_not_choose_ambiguous_monorepo_enrichment():
    rag = DeterministicRagSpy()
    request = _request().model_copy(update={
        "enrichmentData": PrEnrichmentDataDto(fileContents=[
            FileContentDto(
                path="repo-a/src/file_0.py",
                content="origin = 'a'\n",
                sizeBytes=13,
            ),
            FileContentDto(
                path="repo-b/src/file_0.py",
                content="origin = 'b'\n",
                sizeBytes=13,
            ),
        ]),
    })
    orchestrator = MultiStageReviewOrchestrator(
        llm=object(),
        mcp_client=None,
        rag_client=rag,
    )

    await orchestrator._index_pr_files(
        request,
        DiffProcessor().process(request.rawDiff),
    )

    indexed_file = rag.index_requests[0]["files"][0]
    assert indexed_file["content_state"] == "partial_diff"
    assert "origin = " not in indexed_file["content"]


@pytest.mark.asyncio
async def test_pr_overlay_preserves_deleted_file_as_tombstone():
    rag = DeterministicRagSpy()
    path = "src/removed.py"
    raw_diff = "\n".join([
        f"diff --git a/{path} b/{path}",
        "deleted file mode 100644",
        "index 1111111..0000000",
        f"--- a/{path}",
        "+++ /dev/null",
        "@@ -1 +0,0 @@",
        "-obsolete = True",
        "",
    ])
    request = _request().model_copy(update={
        "changedFiles": [path],
        "rawDiff": raw_diff,
        "enrichmentData": PrEnrichmentDataDto(fileContents=[]),
    })
    orchestrator = MultiStageReviewOrchestrator(
        llm=object(),
        mcp_client=None,
        rag_client=rag,
    )

    await orchestrator._index_pr_files(
        request,
        DiffProcessor().process(request.rawDiff),
    )

    assert rag.index_requests[0]["files"] == [{
        "path": path,
        "content": "",
        "change_type": "DELETED",
        "content_state": "complete",
    }]


@pytest.mark.asyncio
async def test_normal_review_entrypoint_routes_marked_job_to_prompt_capture(
    monkeypatch,
):
    monkeypatch.setenv("ANALYSIS_PROMPT_DRY_RUN_ENABLED", "true")
    monkeypatch.setenv("ANALYSIS_PROMPT_DRY_RUN_SYNTHETIC_FINDINGS_PER_FILE", "3")
    monkeypatch.setenv("ANALYSIS_PROMPT_DRY_RUN_SYNTHETIC_FINDINGS_MAX_TOTAL", "17")
    captured: dict = {}

    async def fake_capture(
        request,
        rag_client,
        *,
        simulated_findings_per_file,
        simulated_findings_max_total,
        event_callback,
    ):
        captured.update({
            "request": request,
            "rag_client": rag_client,
            "simulated_findings_per_file": simulated_findings_per_file,
            "simulated_findings_max_total": simulated_findings_max_total,
            "event_callback": event_callback,
        })
        return {
            "dryRun": True,
            "status": "prompt_capture_completed",
            "comment": "captured",
            "issues": [],
            "promptArtifact": {
                "containerPath": "/app/logs/prompt-dry-runs/capture.json",
            },
        }

    async def fail_normal_review(*_args, **_kwargs):
        raise AssertionError("marked jobs must not enter provider-backed review")

    monkeypatch.setattr(
        "service.review.prompt_dry_run.capture_and_store_review_prompts",
        fake_capture,
    )
    monkeypatch.setattr(ReviewService, "_process_review", fail_normal_review)

    service = ReviewService.__new__(ReviewService)
    service._review_semaphore = asyncio.Semaphore(1)
    service.rag_client = DeterministicRagSpy()
    request = _request().model_copy(update={
        "promptDryRun": True,
        "promptDryRunId": "queue-job-123",
    })

    result = await service.process_review_request(request)

    assert result["result"]["dryRun"] is True
    assert captured["request"] is request
    assert captured["rag_client"] is service.rag_client
    assert captured["simulated_findings_per_file"] == 3
    assert captured["simulated_findings_max_total"] == 17
    assert captured["event_callback"] is None
