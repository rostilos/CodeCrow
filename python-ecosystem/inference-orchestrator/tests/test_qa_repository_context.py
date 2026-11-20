"""QA repository identity, optional failure diagnostics and connection ownership."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.agent.models import AgentToolEvent
from service.qa_documentation.qa_doc_orchestrator import QaDocOrchestrator
from service.qa_documentation.repository_tools import qa_repository_tools


async def run_qa(agent, *, workspace="owner", repo="repository", provider="github"):
    orchestrator = QaDocOrchestrator(SimpleNamespace(), agent_service=agent)
    orchestrator._is_documentation_needed = AsyncMock(return_value=True)
    orchestrator._ensure_shareable_sections = AsyncMock(side_effect=lambda doc, _: doc)
    return await orchestrator.run(
        project_name="display-name-is-not-a-vcs-identity", pr_number=9, issues_found=0,
        files_analyzed=1, pr_metadata={}, template_mode="BASE", custom_template=None,
        task_context=None, diff="small diff", delta_diff=None, enrichment_data=None,
        changed_file_paths=["a.py"], previous_documentation=None, is_same_pr_rerun=False,
        workspace_slug=workspace, repo_slug=repo, vcs_provider=provider,
    )


def agent_with_results(observations=()):
    return SimpleNamespace(
        available_tool_names=frozenset({
            "getReviewFileContent", "getBranchFileContent", "getDirectoryByPath", "getImpactRadius",
        }),
        execute=AsyncMock(return_value=SimpleNamespace(
            output="# QA\n" + "Check the changed endpoint with valid and invalid input. " * 3,
            tool_events=tuple(AgentToolEvent(None, observation) for observation in observations),
        )),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,workspace", [
    ("github", "Exact-Owner"), ("bitbucket", "exact-workspace"), ("gitlab", "group/subgroup"),
])
async def test_qa_binds_vcs_arguments_from_request_for_every_provider(provider, workspace):
    agent = agent_with_results()
    result = await run_qa(agent, workspace=workspace, provider=provider)
    assert result["documentation_needed"]
    invocation = agent.execute.call_args.args[0]
    assert invocation.tool_argument_bindings == {
        "getReviewFileContent": {"workspace": workspace, "repoSlug": "repository"},
        "getBranchFileContent": {"workspace": workspace, "repoSlug": "repository"},
        "getDirectoryByPath": {"workspace": workspace, "projectKey": "repository"},
    }
    assert invocation.allowed_tool_names == agent.available_tool_names


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace,repo", [(None, "repository"), ("owner", None)])
async def test_qa_missing_repository_identity_keeps_graph_and_document_generation(workspace, repo, caplog):
    agent = agent_with_results()
    result = await run_qa(agent, workspace=workspace, repo=repo)
    assert result["documentation_needed"]
    invocation = agent.execute.call_args.args[0]
    assert invocation.allowed_tool_names == {"getImpactRadius"}
    assert invocation.tool_argument_bindings == {}
    assert "lack request repository identity" in caplog.text


@pytest.mark.asyncio
async def test_qa_reports_returned_tool_errors_without_discarding_document(caplog):
    agent = agent_with_results([
        '{"error":"Repository arguments do not match the request-bound repository"}',
        {"status": "unavailable", "reason": "optional graph offline"},
        {"fileContent": "source"},
    ])
    with caplog.at_level(logging.INFO):
        result = await run_qa(agent)
    assert result["documentation_needed"]
    assert "calls=3 unavailable=2" in caplog.text
    assert "QA repository tools completed" not in caplog.text


@pytest.mark.asyncio
async def test_qa_connection_serializes_calls_and_closes_after_failure(monkeypatch):
    import mcp_use
    import service.agent

    middleware = []
    client = SimpleNamespace(add_middleware=middleware.append, close_all_sessions=AsyncMock())
    monkeypatch.setattr(mcp_use.MCPClient, "from_dict", lambda config: client)
    agent = agent_with_results()

    async def initialize(**kwargs):
        assert len(middleware) == 1  # Installed before sessions can send tools/call.
        return {}

    agent.initialize = initialize
    monkeypatch.setattr(service.agent, "AgentExecutionService", lambda **kwargs: agent)
    active = maximum = 0

    async def respond(context):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1
        return context.id

    with pytest.raises(RuntimeError, match="generation failed"):
        async with qa_repository_tools(None, None, {
            "project_id": 7, "workspace_slug": "owner", "repo_slug": "repository",
        }) as prepared:
            assert prepared is agent
            responses = await asyncio.gather(*(
                middleware[0](SimpleNamespace(method="tools/call", connection_id="vcs", id=i), respond)
                for i in range(16)
            ))
            assert responses == list(range(16))
            assert maximum == 1
            raise RuntimeError("generation failed")
    client.close_all_sessions.assert_awaited_once()
