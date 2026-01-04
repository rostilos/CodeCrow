package org.rostilos.codecrow.mcp.gitlab;

/**
 * Configuration for GitLab MCP client.
 */
public class GitLabConfiguration {
    
    private final String accessToken;
    private final String namespace;
    private final String project;
    private final String mrIid;

    public GitLabConfiguration(String accessToken, String namespace, String project, String mrIid) {
        this.accessToken = accessToken;
        this.namespace = namespace;
        this.project = project;
        this.mrIid = mrIid;
    }

    public String getAccessToken() {
        return accessToken;
    }

    public String getNamespace() {
        return namespace;
    }

    public String getProject() {
        return project;
    }

    public String getMrIid() {
        return mrIid;
    }
}
