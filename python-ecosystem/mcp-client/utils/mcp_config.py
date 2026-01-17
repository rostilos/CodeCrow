from typing import Dict, Optional
import os


class MCPConfigBuilder:
    """Builder class for creating MCP server configurations."""

    @staticmethod
    def build_config(jar_path: str, jvm_props: Optional[Dict[str, str]] = None,
                     include_platform_mcp: bool = False,
                     platform_mcp_jar_path: Optional[str] = None,
                     platform_jvm_props: Optional[Dict[str, str]] = None) -> dict:
        """
        Build MCP configuration with optional Platform MCP server.
        
        Args:
            jar_path: Path to the VCS MCP server JAR (bitbucket/github)
            jvm_props: JVM properties for VCS MCP server
            include_platform_mcp: Whether to include Platform MCP server
            platform_mcp_jar_path: Path to Platform MCP server JAR
            platform_jvm_props: JVM properties for Platform MCP server
        """
        jvm_props = jvm_props or {}
        jvm_args = []

        for key, value in jvm_props.items():
            # sanitize: convert to string and replace newlines
            sanitized_value = str(value).replace("\n", " ")
            jvm_args.append(f"-D{key}={sanitized_value}")

        # Enable JVM debugging if MCP_DEBUG_PORT is set
        debug_port = os.environ.get("MCP_DEBUG_PORT")
        if debug_port:
            jvm_args = [f"-agentlib:jdwp=transport=dt_socket,server=y,suspend=y,address=*:{debug_port}"] + jvm_args
        
        args = jvm_args + ["-jar", jar_path]

        mcp_servers = {
            "codecrow-vcs-mcp": {
                "command": "java",
                "args": args,
                "type": "stdio"
            }
        }
        
        # Add Platform MCP server if requested
        if include_platform_mcp and platform_mcp_jar_path and os.path.exists(platform_mcp_jar_path):
            platform_jvm_props = platform_jvm_props or {}
            platform_args = []
            for key, value in platform_jvm_props.items():
                sanitized_value = str(value).replace("\n", " ")
                platform_args.append(f"-D{key}={sanitized_value}")
            platform_args.extend(["-jar", platform_mcp_jar_path])
            
            mcp_servers["codecrow-platform-mcp"] = {
                "command": "java",
                "args": platform_args,
                "type": "stdio"
            }

        return {
            "mcpServers": mcp_servers
        }

    @staticmethod
    def build_jvm_props(project_id: int, pull_request_id: int, workspace: str,
         repo_slug: str, oAuthClient: str = None, oAuthSecret: str = None, 
         access_token: str = None, max_allowed_tokens: int = None,
         vcs_provider: str = None) -> Dict[str, str]:
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
            max_allowed_tokens: Optional per-request token limit to pass to the MCP server.
            vcs_provider: VCS provider type (github, bitbucket_cloud) for MCP server selection.

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

        # If provided, expose max allowed tokens as a JVM property so the MCP server
        # can read it (System.getProperty) and decide whether to fetch large diffs / files.
        if max_allowed_tokens is not None:
            jvm_props["max.allowed.tokens"] = str(max_allowed_tokens)

        # VCS provider type for MCP server to select the correct client factory
        if vcs_provider is not None:
            jvm_props["vcs.provider"] = vcs_provider

        return jvm_props
