"""Cumulative scan and downstream reuse contracts; no paid model calls."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from model.dtos import ReviewRequestDto
from service.review import change_summary as scan
from service.qa_documentation.qa_doc_orchestrator import QaDocOrchestrator
from utils.diff_processor import DiffProcessor


def request(**values):
    return ReviewRequestDto(projectId=7, projectVcsWorkspace="owner", projectVcsRepoSlug="repo",
        projectWorkspace="tenant", projectNamespace="project", aiProvider="openai", aiModel="test",
        aiApiKey="test", pullRequestId=9, commitHash="abc", **values)


def diff(path, operation="modified"):
    if operation == "deleted":
        return f"diff --git a/{path} b/{path}\ndeleted file mode 100644\n--- a/{path}\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n"
    if operation == "added":
        return f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+new\n"
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n"


@pytest.mark.asyncio
async def test_every_batch_receives_previous_output_and_retains_graph(monkeypatch):
    monkeypatch.setattr(scan, "supports_structured_output", lambda llm: False)
    paths = [f"f{i}.py" for i in range(16)]
    prompts = []
    async def invoke(prompt, **kwargs):
        prompts.append(prompt)
        records = json.loads(prompt.split(f"Batch {len(prompts)}/2:\n")[1])
        return SimpleNamespace(content=json.dumps({"summary": f"Cumulative pass {len(prompts)}",
            "files": [{"path": item["path"], "summary": f"Changed {item['path']}"} for item in records],
            "changeAreas": [{"title": "Behavior", "paths": [item['path'] for item in records],
                             "changes": [f"Concrete change in {item['path']}" for item in records]}],
            "relationships": [{"source": records[0]["path"], "target": "f1.py",
                               "relationship": "calls", "evidence": "inferred from changed call"}]}))
    raw = "".join(diff(path, "deleted" if i == 15 else "added") for i, path in enumerate(paths))
    events = []
    result = await scan.scan_pr_changes(SimpleNamespace(ainvoke=invoke), request(rawDiff=raw, changedFiles=paths), event_callback=events.append)
    assert len(prompts) == 2
    assert '"summary":"Cumulative pass 1"' in prompts[1]
    assert result["semanticStatus"] == "complete"
    assert result["status"] == "degraded"  # No indexed repository supplied.
    assert len(result["files"]) == 16 and len(result["relationships"]) == 2
    assert result["files"][-1]["changeType"] == "deleted"
    assert result["files"][-1]["ranges"] == [{"base": [1, 1], "head": [0, 0]}]
    assert result["files"][0]["scanStatus"] == "scanned"
    assert [event["batch"] for event in events] == [1, 2]
    assert len(result['changeAreas'][0]['changes']) == 16
    assert 'Concrete change in f0.py' in result['summary']
    assert 'Concrete change in f15.py' in result['summary']


@pytest.mark.asyncio
async def test_malformed_scan_fails_open_with_all_file_facts(monkeypatch):
    monkeypatch.setattr(scan, "supports_structured_output", lambda llm: False)
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content="invalid")))
    result = await scan.scan_pr_changes(llm, request(rawDiff=diff("a.py"), changedFiles=["a.py"]))
    assert result["status"] == "degraded" and result["diagnostics"]
    assert result["files"][0]["path"] == "a.py" and result["files"][0]["ranges"]


@pytest.mark.asyncio
async def test_incremental_scan_uses_full_pr_and_not_just_delta(monkeypatch):
    monkeypatch.setattr(scan, "supports_structured_output", lambda llm: False)
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=TimeoutError))
    result = await scan.scan_pr_changes(llm, request(rawDiff=diff("old.py") + diff("new.py"),
        deltaDiff=diff("new.py"), analysisMode="INCREMENTAL", changedFiles=["new.py"]),
        DiffProcessor().process(diff("new.py")))
    assert {item["path"] for item in result["files"]} == {"old.py", "new.py"}
    assert "old.py" in llm.ainvoke.call_args.args[0]


def test_large_diff_parts_are_lossless_and_include_missing_paths():
    parsed = DiffProcessor(10000, 10000, 10000, 10000).process(diff("a.py") + "+" + "x" * 5000)
    files, batches = scan._scan_records(parsed, ["a.py", "missing.py"], 200)
    records = [record for batch in batches for record in batch]
    assert "".join(record["diff"] for record in records if record["path"] == "a.py") == parsed.files[0].content
    assert len(batches) > 20 and files[-1]["scanStatus"] == "unavailable"


def test_context_reuse_checks_project_pr_revision_and_hides_tool_paths():
    summary = {"projectId": 7, "pullRequestId": 9, "sourceRevision": "abc",
        "files": [{"path": "a.py"}], "toolContext": {"secretPath": "private-local-root"}}
    assert scan.compatible_change_summary(summary, 7, 9, "abc") == summary
    for identity in [(8, 9, "abc"), (7, 10, "abc"), (7, 9, "other"), (7, 9, None)]:
        assert scan.compatible_change_summary(summary, *identity) is None
    assert "private-local-root" not in scan.change_summary_context(summary)
    assert not scan.change_summary_context({"files": [None]})


@pytest.mark.asyncio
async def test_qa_reuses_graph_task_and_issues_without_diff_pipeline():
    orchestrator = QaDocOrchestrator(SimpleNamespace())
    orchestrator._is_documentation_needed = AsyncMock(return_value=True)
    orchestrator._run_multi_stage = AsyncMock()
    orchestrator._run_single_pass = AsyncMock(return_value="# QA\n" + "Test the changed endpoint. " * 10)
    orchestrator._ensure_shareable_sections = AsyncMock(side_effect=lambda doc, placeholders: doc)
    result = await orchestrator.run(project_name="repo", pr_number=9, issues_found=1,
        files_analyzed=1, pr_metadata={"analysisSummary": "Issue: unsafe empty input"},
        template_mode="BASE", custom_template=None, task_context={"description": "Handle empty input"},
        diff="x" * 20000, delta_diff=None, enrichment_data=None, changed_file_paths=["a.py"],
        previous_documentation=None, is_same_pr_rerun=False,
        pr_change_summary={"files": [{"path": "a.py", "summary": "Changes endpoint"}], "summary": "Endpoint update"})
    assert result["documentation_needed"]
    orchestrator._run_multi_stage.assert_not_called()
    placeholders = orchestrator._run_single_pass.call_args.kwargs["placeholders"]
    assert "Endpoint update" in placeholders["analysis_summary"] and "unsafe empty input" in placeholders["analysis_summary"]
    assert "Handle empty input" in placeholders["task_context"] and "x" * 20000 not in str(placeholders)


@pytest.mark.asyncio
async def test_qa_agent_failure_keeps_generation_available():
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content="saved context document")))
    agent = SimpleNamespace(available_tool_names=frozenset({"getImpactRadius"}), execute=AsyncMock(side_effect=TimeoutError))
    orchestrator = QaDocOrchestrator(llm, agent_service=agent)
    result = await orchestrator._invoke_document([{"content": "QA task and graph"}])
    assert result.content == "saved context document"
    assert agent.execute.call_args.args[0].tool_argument_bindings == {}
    assert orchestrator.agent_service is None


@pytest.mark.asyncio
async def test_large_saved_qa_keeps_all_files_without_forcing_oversized_graph_requests():
    document = "# QA\n" + "Test the changed endpoint. " * 10
    agent = SimpleNamespace(available_tool_names=frozenset({"getMinimalReviewContext", "getReviewFileContent"}),
        execute=AsyncMock(return_value=SimpleNamespace(output=document, tool_events=[])))
    orchestrator = QaDocOrchestrator(SimpleNamespace(), agent_service=agent)
    orchestrator._is_documentation_needed = AsyncMock(return_value=True)
    orchestrator._ensure_shareable_sections = AsyncMock(side_effect=lambda doc, placeholders: doc)
    paths = [f"src/file_{i}.py" for i in range(106)]
    result = await orchestrator.run(project_name="repo", pr_number=9, issues_found=1,
        files_analyzed=len(paths), pr_metadata={"analysisSummary": "Issue: unsafe empty input"},
        template_mode="BASE", custom_template=None, task_context={"description": "Handle empty input"},
        diff=None, delta_diff=None, enrichment_data=None, changed_file_paths=paths,
        previous_documentation=None, is_same_pr_rerun=False,
        pr_change_summary={"files": [{"path": path, "summary": "Changed behavior"} for path in paths],
                           "summary": "All 106 changes retained"})
    invocation = agent.execute.call_args.args[0]
    assert all(path in invocation.prompt for path in paths)
    assert not invocation.tool_argument_bindings
    assert "at most 100 per call" in invocation.additional_instructions
    assert result["documentation_needed"]


def test_review_revisits_scan_batches_without_duplicate_split_files():
    items = [{"file": SimpleNamespace(path=path)} for path in ("b.py", "a.py", "c.py")]
    groups = scan.group_review_items_by_scan(items, {"scanBatches": [["a.py"], ["a.py", "b.py"]]})
    assert [[item["file"].path for item in batch] for batch in groups] == [["a.py"], ["b.py"], ["c.py"]]


@pytest.mark.asyncio
async def test_review_cannot_start_until_all_scan_batches_complete(monkeypatch):
    from service.review.orchestrator import orchestrator as pipeline
    stages = []
    async def scanned(*args):
        stages.extend(["scan1", "scan2"])
        return {"summary": "All files described", "files": [{"path": "a.py"}], "status": "complete"}
    async def reviewed(*args, **kwargs):
        assert stages == ["scan1", "scan2"]
        assert args[1].prChangeSummary["summary"] == "All files described"
        stages.append("review")
        raise RuntimeError("test review boundary reached")
    monkeypatch.setattr(pipeline, "scan_pr_changes", scanned)
    monkeypatch.setattr(pipeline, "execute_stage_1_file_reviews", reviewed)
    req = request(rawDiff=diff("a.py"), changedFiles=["a.py"], targetBranchName="main")
    with pytest.raises(RuntimeError, match="test review boundary"):
        await pipeline.MultiStageReviewOrchestrator(None, None).orchestrate_review(req, DiffProcessor().process(req.rawDiff))
    assert stages[-1] == "review"


@pytest.mark.asyncio
async def test_saved_summary_generation_works_without_mcp(monkeypatch):
    from service.command.command_service import CommandService
    from model.output_schemas import SummarizeOutput
    from service.review.orchestrator import structured_output, json_utils
    from service.review.orchestrator.structured_output import StructuredOutputInvocation
    output = SummarizeOutput(summary="PR description from saved graph", diagram="", diagramType="ASCII")
    monkeypatch.setattr(structured_output, "invoke_structured_output", AsyncMock(return_value=StructuredOutputInvocation(parsed=output)))
    service = CommandService()
    monkeypatch.setattr(service, "_install_provider_input_guard", lambda *args: None)
    result = await service._execute_summarize(None, None, "Saved context", False, None)
    assert result["summary"] == "PR description from saved graph"


def test_saved_description_prompt_uses_context_without_requesting_unavailable_tools():
    from model.dtos import SummarizeRequestDto
    from service.command.command_service import CommandService
    req = SummarizeRequestDto(**request().model_dump(), prChangeSummary={
        "summary": "Added empty-input handling", "files": [{"path": "a.py"}]})
    prompt = CommandService()._build_summarize_prompt(req)
    assert "Added empty-input handling" in prompt
    assert "getPullRequestDiff" not in prompt
    assert "Use the MCP tools available" not in prompt
