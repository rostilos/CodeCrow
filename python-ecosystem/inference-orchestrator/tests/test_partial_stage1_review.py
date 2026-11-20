"""Core batch failure must retain useful output and honest coverage evidence."""
import asyncio
from types import SimpleNamespace

import pytest

from service.review.execution_scheduler import FairReviewScheduler, review_execution
from service.review.inference_usage import current_inference_usage
from service.review.orchestrator import stage_1_file_review as stage
from service.review.orchestrator.orchestrator import MultiStageReviewOrchestrator
from service.review.prompt_dry_run import PromptCaptureLLM, PromptCaptureSession
from service.review.quality_capture import _terminal_pipeline_evidence
from service.review.review_service import ReviewService
from utils.diff_processor import DiffProcessor
from .prompt_dry_run_neutral_fixture import neutral_request


@pytest.mark.asyncio
async def test_partial_stage1_reaches_final_output_without_claiming_complete(monkeypatch):
    request = neutral_request(file_count=2)
    request.ragEnabled = False
    capture = PromptCaptureSession(request, simulated_findings_per_file=1)
    original_review = stage._review_batch_with_timing

    async def review(batch_number, *args, **kwargs):
        if batch_number == 1:
            raise RuntimeError("provider unavailable")
        await asyncio.sleep(0.01)
        return await original_review(batch_number, *args, **kwargs)

    monkeypatch.setattr(stage, "_review_batch_with_timing", review)
    monkeypatch.setattr(stage, "_repack_stage1_batches_by_rendered_input",
                        lambda batches, *_args: [[item] for batch in batches for item in batch])
    events = []
    engine = MultiStageReviewOrchestrator(llm=PromptCaptureLLM(capture), mcp_client=None,
                                        rag_client=None, event_callback=events.append)
    with review_execution(FairReviewScheduler(2), asyncio.Semaphore(2)):
        result = await engine.orchestrate_review(request, processed_diff=DiffProcessor().process(request.rawDiff))
        response = {"result": result, "inferenceUsage": current_inference_usage()}
        ReviewService._include_coverage_notice(SimpleNamespace(), response)
    assert result["issues"], "The successful sibling's finding must survive"
    assert result["analysisCompletion"]["status"] == "partial"
    assert "stage_1" in result["analysisCompletion"]["incompleteStages"]
    evidence = next(_terminal_pipeline_evidence(event) for event in events
                    if event.get("state") == "review_evidence_completed")
    assert evidence["completionStatus"] == "partial"
    assert evidence["hunkCoverage"]["planned"] == 1
    assert evidence["hunkCoverage"]["completed"] == 1
    assert evidence["reviewUnits"] == {"registered": 2, "completed": 1}
    assert len(evidence["hunkReceipts"]) == 1
