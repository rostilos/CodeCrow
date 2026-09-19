from typing import Dict, Mapping, Optional, Tuple
import os
import sys


class MCPConfigBuilder:
    """Builder class for creating MCP server configurations."""

    # mcp-use records stdio command arguments and derives public connector
    # identifiers from them. Credentials therefore belong in the child-only
    # environment, never in Java -D arguments.
    _CREDENTIAL_ENV_BY_JVM_PROPERTY = {
        "accessToken": "CODECROW_MCP_ACCESS_TOKEN",
        "oAuthClient": "CODECROW_MCP_OAUTH_CLIENT",
        "oAuthSecret": "CODECROW_MCP_OAUTH_SECRET",
        "internal.api.secret": "CODECROW_MCP_INTERNAL_API_SECRET",
    }

    @classmethod
    def build_java_launch_options(
            cls,
            jvm_props: Optional[Mapping[str, str]] = None,
    ) -> Tuple[list[str], Dict[str, str]]:
        """Return nonsecret JVM args and request-scoped child credentials."""
        jvm_args: list[str] = []
        server_env: Dict[str, str] = {}

        for key, value in (jvm_props or {}).items():
            text_value = str(value)
            credential_env_name = cls._CREDENTIAL_ENV_BY_JVM_PROPERTY.get(key)
            if credential_env_name is not None:
                server_env[credential_env_name] = text_value
                continue

            # Preserve the existing newline handling for loggable command-line
            # metadata. Credential values are not rewritten.
            jvm_args.append(f"-D{key}={text_value.replace(chr(10), ' ')}")

        return jvm_args, server_env

    @staticmethod
    def build_config(jar_path: str, jvm_props: Optional[Dict[str, str]] = None,
                     include_platform_mcp: bool = False,
                     platform_mcp_jar_path: Optional[str] = None,
                     platform_jvm_props: Optional[Dict[str, str]] = None,
                     rag_mcp_context: Optional[Dict[str, str]] = None) -> dict:
        """
        Build MCP configuration with optional Platform MCP server.
        
        Args:
            jar_path: Path to the VCS MCP server JAR (bitbucket/github)
            jvm_props: JVM properties for VCS MCP server
            include_platform_mcp: Whether to include Platform MCP server
            platform_mcp_jar_path: Path to Platform MCP server JAR
            platform_jvm_props: JVM properties for Platform MCP server
            rag_mcp_context: Exact repository-generation binding for the optional
                structural graph MCP server.
        """
        jvm_args, vcs_env = MCPConfigBuilder.build_java_launch_options(jvm_props)
        local_mcp_only = str(
            (jvm_props or {}).get("local.mcp.only", "false")
        ).strip().lower() == "true"
        if local_mcp_only:
            # The child environment otherwise inherits service-level values.
            # Blank every supported VCS credential in addition to the Java
            # fail-closed property so a local-only request cannot reuse an
            # ambient provider credential.
            vcs_env.update({
                "CODECROW_MCP_ACCESS_TOKEN": "",
                "CODECROW_MCP_OAUTH_CLIENT": "",
                "CODECROW_MCP_OAUTH_SECRET": "",
            })

        # Enable JVM debugging if MCP_DEBUG_PORT is set
        debug_port = os.environ.get("MCP_DEBUG_PORT")
        if debug_port:
            jvm_args = [f"-agentlib:jdwp=transport=dt_socket,server=y,suspend=y,address=*:{debug_port}"] + jvm_args
        
        args = jvm_args + ["-jar", jar_path]

        vcs_server = {
            "command": "java",
            "args": args,
            "type": "stdio"
        }
        if vcs_env:
            vcs_server["env"] = vcs_env

        mcp_servers = {
            "codecrow-vcs-mcp": vcs_server
        }
        
        # Add Platform MCP server if requested
        if include_platform_mcp and platform_mcp_jar_path and os.path.exists(platform_mcp_jar_path):
            platform_args, platform_env = (
                MCPConfigBuilder.build_java_launch_options(platform_jvm_props)
            )
            platform_args.extend(["-jar", platform_mcp_jar_path])

            platform_server = {
                "command": "java",
                "args": platform_args,
                "type": "stdio"
            }
            if platform_env:
                platform_server["env"] = platform_env
            mcp_servers["codecrow-platform-mcp"] = platform_server

        if rag_mcp_context:
            rag_env = {
                f"CODECROW_RAG_MCP_{key.upper()}": str(value)
                for key, value in rag_mcp_context.items()
                if value is not None and str(value).strip()
            }
            rag_env["RAG_API_URL"] = os.environ.get(
                "RAG_API_URL", "http://codecrow-rag-pipeline:8001"
            )
            service_secret = (
                os.environ.get("SERVICE_SECRET")
                or os.environ.get("CODECROW_RAG_API_SECRET")
            )
            if service_secret:
                rag_env["SERVICE_SECRET"] = service_secret
            mcp_servers["codecrow-rag-mcp"] = {
                "command": sys.executable,
                "args": ["-m", "service.rag.rag_mcp_server"],
                "env": rag_env,
                "type": "stdio",
            }

        return {
            "mcpServers": mcp_servers
        }

    @staticmethod
    def build_jvm_props(project_id: int, pull_request_id: int, workspace: str,
         repo_slug: str, oAuthClient: str = None, oAuthSecret: str = None, 
         access_token: str = None, max_allowed_tokens: int = None,
         vcs_provider: str = None, vcs_base_url: str = None,
         local_repo_path: str = None,
         local_repo_target_branch: str = None,
         local_repo_revision: str = None,
         local_review_overlay_path: str = None,
         local_mcp_only: bool = False) -> Dict[str, str]:
        """
        Build JVM properties dictionary from request parameters.

        Args:
            project_id: Project identifier
            pull_request_id: Pull request identifier
            workspace: Repository workspace
            repo_slug: Repository slug
            oAuthClient: OAuth consumer key (for OAUTH_MANUAL connections)
            oAuthSecret: OAuth consumer secret (for OAUTH_MANUAL connections)
            access_token: Bearer token (for APP connections - used instead of oAuthClient/oAuthSecret)
            max_allowed_tokens: Optional per-request token limit passed to the MCP server.
            vcs_provider: VCS provider type (github, bitbucket_cloud, gitlab) for MCP server selection.
            vcs_base_url: GitLab instance root for self-managed GitLab.
            local_repo_path: Shared target-head snapshot directory.
            local_repo_target_branch: Branch name represented by the snapshot.
            local_repo_revision: Immutable revision represented by the snapshot.
            local_review_overlay_path: Request-scoped proposed-tree overlay root.
            local_mcp_only: Disable provider tools and provider fallbacks.

        Returns:
            Dictionary of JVM properties
        """
        jvm_props = {}

        if project_id is not None:
            jvm_props["project.id"] = str(project_id)

        if pull_request_id is not None:
            jvm_props["pullRequest.id"] = str(pull_request_id)
        if workspace is not None:
            jvm_props["workspace"] = workspace
        if repo_slug is not None:
            jvm_props["repo.slug"] = repo_slug

        # For APP connections, use accessToken directly
        if access_token is not None:
            jvm_props["accessToken"] = access_token
        else:
            # For OAUTH_MANUAL connections, use oAuthClient/oAuthSecret
            if oAuthClient is not None:
                jvm_props["oAuthClient"] = oAuthClient

            if oAuthSecret is not None:
                jvm_props["oAuthSecret"] = oAuthSecret

        if max_allowed_tokens is not None:
            jvm_props["max.allowed.tokens"] = str(max_allowed_tokens)

        # VCS provider type for MCP server to select the correct client factory
        if vcs_provider is not None:
            jvm_props["vcs.provider"] = vcs_provider
        if vcs_base_url is not None:
            jvm_props["vcs.baseUrl"] = vcs_base_url
        if local_repo_path is not None:
            jvm_props["local.repo.path"] = local_repo_path
        if local_repo_target_branch is not None:
            jvm_props["local.repo.targetBranch"] = local_repo_target_branch
        if local_repo_revision is not None:
            jvm_props["local.repo.revision"] = local_repo_revision
        if local_review_overlay_path is not None:
            jvm_props["local.review.overlay.path"] = local_review_overlay_path
        if local_mcp_only:
            jvm_props["local.mcp.only"] = "true"

        return jvm_props
