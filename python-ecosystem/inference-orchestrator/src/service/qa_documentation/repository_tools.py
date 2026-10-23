"""Request-scoped reuse of review tools for QA, without rebuilding indexes."""
from contextlib import asynccontextmanager
import logging
import os

from utils.mcp_config import MCPConfigBuilder

logger = logging.getLogger(__name__)


@asynccontextmanager
async def qa_repository_tools(llm, summary, context):
    client = None
    agent = None
    try:
        if context:
            from mcp_use import MCPClient
            from service.agent import AgentExecutionService

            saved = context.get("tool_context") or (summary or {}).get("toolContext") or {}
            repository = saved.get("repository") or {}
            props = MCPConfigBuilder.build_jvm_props(
                project_id=context["project_id"], pull_request_id=context.get("pr_number"),
                workspace=context.get("workspace_slug"), repo_slug=context.get("repo_slug"),
                oAuthClient=context.get("oauth_client"), oAuthSecret=context.get("oauth_secret"),
                access_token=context.get("access_token"), vcs_provider=context.get("vcs_provider"),
                vcs_base_url=context.get("vcs_base_url"),
                local_repo_path=repository.get("localRepoPath"),
                local_repo_target_branch=repository.get("localRepoTargetBranch"),
                local_repo_revision=repository.get("localRepoRevision"),
                local_review_overlay_path=repository.get("localReviewOverlayPath"),
                local_mcp_only=repository.get("mcpLocalOnly") is True,
            )
            # Both contexts were created by the review host. Match the current
            # tenant again before exposing a saved generation to the QA model.
            def scoped(binding):
                if not isinstance(binding, dict):
                    return None
                if (binding.get("workspace") == context.get("project_workspace")
                        and binding.get("project") == context.get("project_namespace")
                        and binding.get("source_revision") == context.get("source_revision")):
                    return binding
                logger.warning("QA saved tool binding is stale or belongs to another project; skipped")
                return None

            config = MCPConfigBuilder.build_config(
                os.environ.get("MCP_SERVER_JAR", "/app/codecrow-vcs-mcp-1.0.jar"), props,
                rag_mcp_context=scoped(saved.get("rag")),
                verification_context=scoped(saved.get("verification")),
            )
            client = MCPClient.from_dict(config)
            candidate = AgentExecutionService(llm=llm, client=client)
            errors = await candidate.initialize(
                required_server_names=(),
                optional_server_names=tuple(config["mcpServers"]),
                session_timeout_seconds=30,
            )
            for name in errors:
                logger.warning("QA optional MCP server unavailable: %s", name)
            if candidate.available_tool_names:
                agent = candidate
    except Exception as error:
        logger.warning("QA repository context unavailable (%s); using saved change context", type(error).__name__)
    try:
        yield agent
    finally:
        if client is not None:
            try:
                await client.close_all_sessions()
            except Exception as error:
                logger.warning("QA MCP cleanup failed: %s", type(error).__name__)
