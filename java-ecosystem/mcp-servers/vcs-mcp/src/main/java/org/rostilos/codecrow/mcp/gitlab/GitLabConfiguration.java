package org.rostilos.codecrow.mcp.gitlab;

/**
 * Configuration for GitLab MCP client.
 */
public class GitLabConfiguration {
    
    private final String accessToken;
    private final String namespace;
    private final String project;
    private final String mrIid;
    private final String baseUrl;

    public GitLabConfiguration(String accessToken, String namespace, String project, String mrIid) {
        this(accessToken, namespace, project, mrIid, null);
    }

    public GitLabConfiguration(
            String accessToken,
            String namespace,
            String project,
            String mrIid,
            String baseUrl
    ) {
        this.accessToken = accessToken;
        this.namespace = namespace;
        this.project = project;
        this.mrIid = mrIid;
        this.baseUrl = org.rostilos.codecrow.vcsclient.gitlab.GitLabConfig
                .instanceBaseUrl(baseUrl);
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

    public String getBaseUrl() {
        return baseUrl;
    }

    public String getApiBaseUrl() {
        return org.rostilos.codecrow.vcsclient.gitlab.GitLabConfig.apiBaseUrl(baseUrl);
    }
}
