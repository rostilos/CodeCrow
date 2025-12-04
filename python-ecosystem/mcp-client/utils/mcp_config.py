from typing import Dict, Optional


class MCPConfigBuilder:
    """Builder class for creating MCP server configurations."""

    @staticmethod
    def build_config(jar_path: str, jvm_props: Optional[Dict[str, str]] = None) -> dict:
        jvm_props = jvm_props or {}
        jvm_args = []

        for key, value in jvm_props.items():
            # sanitize: convert to string and replace newlines
            sanitized_value = str(value).replace("\n", " ")
            jvm_args.append(f"-D{key}={sanitized_value}")

        # For debugging, uncomment the next line:
        #args = ["-agentlib:jdwp=transport=dt_socket,server=y,suspend=y,address=*:5007"] + jvm_args + ["-jar", jar_path]
        args = jvm_args + ["-jar", jar_path]

        return {
            "mcpServers": {
                "codecrow-mcp-server": {
                    "command": "java",
                    "args": args,
                    "type": "stdio"
                }
            }
        }

    @staticmethod
    def build_jvm_props(project_id: int, pull_request_id: int, workspace: str,
         repo_slug: str, oAuthClient: str = None, oAuthSecret: str = None, 
         access_token: str = None, max_allowed_tokens: int = None) -> Dict[str, str]:
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

        return jvm_props
