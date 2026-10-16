"""Pipeline regressions: real coordinators with deterministic model boundaries."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.review.change_summary import compatible_change_summary
from service.qa_documentation.qa_doc_orchestrator import QaDocOrchestrator
from service.qa_documentation.qa_doc_service import QaDocumentationService


def saved_summary(**updates):
    return {"projectId": 7, "pullRequestId": 9, "sourceRevision": "abc",
            "files": [{"path": "a.py", "scanStatus": "scanned", "summary": "Input validation"}],
            "semanticStatus": "complete", **updates}


@pytest.mark.parametrize("updates", [
    {"semanticStatus": "degraded"},
    {"files": [{"path": "a.py", "scanStatus": "unavailable"}]},
    {"files": [{"path": "a.py", "scanStatus": "degraded"}]},
])
def test_incomplete_semantics_require_source_fallback(updates):
    assert compatible_change_summary(saved_summary(**updates), 7, 9, "abc") is None


def test_graph_failure_does_not_discard_complete_semantics():
    summary = saved_summary(status="degraded", diagnostics=["Graph unavailable"])
    assert compatible_change_summary(summary, 7, 9, "abc") is summary


@pytest.mark.asyncio
async def test_qa_service_falls_back_to_real_diff_with_configured_budget(monkeypatch):
    captured = []

    async def document(self, **kwargs):
        captured.append((kwargs["placeholders"], self.qa_input_token_target))
        return "# QA\n" + "Verify empty input returns a validation error. " * 5

    monkeypatch.setattr(QaDocOrchestrator, "_run_single_pass", document)
    monkeypatch.setattr(QaDocOrchestrator, "_is_documentation_needed", AsyncMock(return_value=True))
    monkeypatch.setattr(QaDocOrchestrator, "_ensure_shareable_sections",
                        AsyncMock(side_effect=lambda doc, placeholders: doc))
    service = QaDocumentationService()
    monkeypatch.setattr(service, "_create_llm", lambda **kwargs: SimpleNamespace())
    source = "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-old\n+validate_input(payload)\n"
    result = await service.generate(
        project_id=7, project_name="repo", pr_number=9, issues_found=0, files_analyzed=1,
        pr_metadata={"maxAllowedTokens": 32768}, template_mode="BASE", custom_template=None,
        task_context=None, diff=source, changed_file_paths=["a.py"],
        pr_change_summary=saved_summary(semanticStatus="degraded"),
        repository_context={"source_revision": "abc"})
    assert result["documentation_needed"]
    assert "validate_input(payload)" in captured[0][0]["diff"]
    assert captured[0][1] == 12768


@pytest.mark.asyncio
async def test_small_model_scans_every_dense_batch_without_growing_past_context(monkeypatch):
    import json
    from service.review import change_summary as scan
    from tests.test_pr_change_summary import request, diff

    monkeypatch.setattr(scan, "supports_structured_output", lambda llm: False)
    prompts = []

    async def invoke(prompt, **kwargs):
        prompts.append(prompt)
        records = json.loads(prompt.split(f"Batch {len(prompts)}/20:\n")[1])
        return SimpleNamespace(content=json.dumps({"summary": "All retained behavior areas",
            "files": [{"path": r["path"], "summary": r["path"] + " behavior" * 64} for r in records],
            "changeAreas": [{"title": "Behavior", "paths": [r["path"] for r in records],
                             "changes": [r["path"] + " changed" * 64 for r in records]}]}))

    paths = [f"src/file_{i:03d}.py" for i in range(300)]
    result = await scan.scan_pr_changes(SimpleNamespace(ainvoke=invoke), request(
        maxAllowedTokens=32768, rawDiff="".join(diff(path) for path in paths), changedFiles=paths))
    assert len(prompts) == 20
    assert all(file["scanStatus"] == "scanned" for file in result["files"])
    assert len(result["changeAreas"][0]["changes"]) == 300
    assert all(len(prompt.encode()) // 2 <= 12768 for prompt in prompts)


def test_review_context_is_bounded_and_keeps_focused_file_without_mutating_saved_ledger():
    from service.review.change_summary import change_summary_context
    files = [{"path": f"src/{i}.py", "summary": f"Unique behavior {i}. " * 100} for i in range(300)]
    summary = {"files": files, "summary": "Complete large PR overview"}
    rendered = change_summary_context(summary, ["src/299.py"], max_tokens=3000)
    assert len(rendered.encode()) <= 6000
    assert "Unique behavior 299" in rendered
    assert "partial" in rendered.lower()
    assert len(summary["files"]) == 300


@pytest.mark.asyncio
async def test_slow_optional_scan_leaves_time_for_core_review_and_retains_completed_batches(monkeypatch):
    import asyncio
    import json
    from service.review import change_summary as scan
    from service.review.execution_scheduler import FairReviewScheduler, review_execution, set_review_deadline
    from tests.test_pr_change_summary import request, diff

    monkeypatch.setattr(scan, "supports_structured_output", lambda llm: False)
    calls = 0

    async def invoke(prompt, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            await asyncio.sleep(10)
        records = json.loads(prompt.split(f"Batch {calls}/2:\n")[1])
        return SimpleNamespace(content=json.dumps({"summary": "Input behavior",
            "files": [{"path": r["path"], "summary": "Concrete behavior"} for r in records],
            "changeAreas": [{"title": "Behavior", "paths": [r["path"] for r in records], "changes": ["New behavior"]}]}))

    paths = [f"f{i}.py" for i in range(30)]
    loop = asyncio.get_running_loop()
    with review_execution(FairReviewScheduler(1), asyncio.Semaphore(1)):
        deadline = loop.time() + 0.5
        set_review_deadline(deadline)
        async with asyncio.timeout_at(deadline):
            result = await scan.scan_pr_changes(SimpleNamespace(ainvoke=invoke), request(
                rawDiff="".join(diff(path) for path in paths), changedFiles=paths))
            await asyncio.sleep(0.05)  # Core review still has time to execute.
    assert result["semanticStatus"] == "degraded"
    assert sum(f["scanStatus"] == "scanned" for f in result["files"]) == 15
    assert len(result["files"]) == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("stage_failure", [False, True])
async def test_large_qa_honors_custom_template_in_multistage_and_fallback(monkeypatch, stage_failure):
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(
        content="# QA\n" + "Validate the changed contract and its error paths. " * 5)))
    qa = QaDocOrchestrator(llm)
    qa._is_documentation_needed = AsyncMock(return_value=True)
    qa._ensure_shareable_sections = AsyncMock(side_effect=lambda doc, placeholders: doc)
    if stage_failure:
        qa._execute_stage_1 = AsyncMock(side_effect=RuntimeError("transient batch error"))
    result = await qa.run(project_name="repo", pr_number=9, issues_found=0, files_analyzed=1,
        pr_metadata={}, template_mode="CUSTOM", custom_template="CUSTOM_LAYOUT: requirement | expected result",
        task_context=None, diff="diff --git a/a.py b/a.py\n@@ -1 +1 @@\n" + "+guard\n" * 2000,
        delta_diff=None, enrichment_data=None, changed_file_paths=["a.py"],
        previous_documentation=None, is_same_pr_rerun=False)
    assert result["documentation_needed"]
    assert "CUSTOM_LAYOUT" in str(llm.ainvoke.call_args.args[0])
    assert result["template_mode_used"] == "CUSTOM"


@pytest.mark.asyncio
async def test_qa_reports_actual_base_fallback_and_unknown_source_coverage():
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=[SimpleNamespace(content="short"),
        SimpleNamespace(content="# QA\n" + "Validate the changed contract and its error paths. " * 5)]))
    qa = QaDocOrchestrator(llm)
    qa._is_documentation_needed = AsyncMock(return_value=True)
    qa._ensure_shareable_sections = AsyncMock(side_effect=lambda doc, placeholders: doc)
    result = await qa.run(project_name="repo", pr_number=9, issues_found=0, files_analyzed=1,
        pr_metadata={}, template_mode="CUSTOM", custom_template="CUSTOM_LAYOUT", task_context=None,
        diff=None, delta_diff=None, enrichment_data=None, changed_file_paths=["a.py"],
        previous_documentation=None, is_same_pr_rerun=False)
    assert result["template_mode_used"] == "BASE"
    assert result["input_coverage"]["status"] == "unknown"


@pytest.mark.asyncio
async def test_qa_endpoint_returns_effective_template_and_input_coverage(monkeypatch):
    from api.routers.qa_documentation import QaDocumentationRequest, generate_qa_documentation
    monkeypatch.setattr(QaDocumentationService, "generate", AsyncMock(return_value={
        "documentation": "Generated document", "documentation_needed": True,
        "template_mode_used": "BASE", "input_coverage": {
            "status": "unknown", "source": "analysis_only", "provided_file_count": 0}}))
    response = await generate_qa_documentation(None, QaDocumentationRequest(
        project_id=7, project_name="repo", pr_number=9, issues_found=0, files_analyzed=0,
        template_mode="CUSTOM", custom_template="Custom instructions"))
    assert response.template_mode_used == "BASE"
    assert response.input_coverage.status == "unknown"


@pytest.mark.asyncio
async def test_qa_does_not_claim_complete_input_coverage_for_missing_changed_files():
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(
        content="# QA\n" + "Validate the changed contract and its error paths. " * 5)))
    qa = QaDocOrchestrator(llm)
    qa._is_documentation_needed = AsyncMock(return_value=True)
    qa._ensure_shareable_sections = AsyncMock(side_effect=lambda doc, placeholders: doc)
    result = await qa.run(project_name="repo", pr_number=9, issues_found=0, files_analyzed=2,
        pr_metadata={}, template_mode="BASE", custom_template=None, task_context=None,
        diff="diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-old\n+new\n", delta_diff=None,
        enrichment_data=None, changed_file_paths=["a.py", "missing.py"],
        previous_documentation=None, is_same_pr_rerun=False)
    assert result["input_coverage"]["status"] == "partial"
    assert result["input_coverage"]["provided_file_count"] == 1


@pytest.mark.asyncio
async def test_qa_api_keeps_host_tool_context_when_semantic_summary_is_unavailable(monkeypatch):
    from api.routers.qa_documentation import QaDocumentationRequest, generate_qa_documentation
    generation = AsyncMock(return_value={"documentation": "QA", "documentation_needed": True})
    monkeypatch.setattr(QaDocumentationService, "generate", generation)
    binding = {"repository": {"localRepoPath": "/qa/fresh", "localReviewOverlayPath": "/qa/overlay"}}
    await generate_qa_documentation(None, QaDocumentationRequest(
        project_id=7, project_name="repo", pr_number=9, source_revision="abc", tool_context=binding))
    assert generation.call_args.kwargs["repository_context"]["tool_context"] == binding


@pytest.mark.asyncio
async def test_qa_repository_tools_use_fresh_host_binding_without_semantic_summary(monkeypatch):
    import mcp_use
    import service.agent
    from utils.mcp_config import MCPConfigBuilder
    from service.qa_documentation.repository_tools import qa_repository_tools
    captured = {}
    def properties(**kwargs):
        captured.update(kwargs)
        return {}
    monkeypatch.setattr(MCPConfigBuilder, "build_jvm_props", properties)
    monkeypatch.setattr(MCPConfigBuilder, "build_config", lambda *args, **kwargs: {"mcpServers": {"vcs": {}}})
    client = SimpleNamespace(close_all_sessions=AsyncMock())
    monkeypatch.setattr(mcp_use.MCPClient, "from_dict", lambda config: client)
    agent = SimpleNamespace(initialize=AsyncMock(return_value={}), available_tool_names=["getReviewFileContent"])
    monkeypatch.setattr(service.agent, "AgentExecutionService", lambda **kwargs: agent)
    async with qa_repository_tools(None, None, {"project_id": 7, "pr_number": 9,
            "source_revision": "abc", "tool_context": {"repository": {
                "localRepoPath": "/qa/fresh", "localReviewOverlayPath": "/qa/overlay"}}}) as prepared:
        assert prepared is agent
        assert captured["local_repo_path"] == "/qa/fresh"
        assert captured["local_review_overlay_path"] == "/qa/overlay"
    client.close_all_sessions.assert_awaited_once()
