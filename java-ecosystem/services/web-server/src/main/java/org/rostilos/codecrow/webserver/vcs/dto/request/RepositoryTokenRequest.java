package org.rostilos.codecrow.webserver.vcs.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;

/**
 * Generic request DTO for creating a VCS connection using a repository-scoped access token.
 * Works with any VCS provider (Bitbucket Cloud, GitHub, GitLab).
 */
public class RepositoryTokenRequest {

    @NotBlank(message = "Access token is required")
    @JsonProperty("accessToken")
    private String accessToken;

    /**
     * Full repository path (e.g., "workspace/repo-slug" for Bitbucket, "owner/repo" for GitHub).
     */
    @NotBlank(message = "Repository path is required")
    @JsonProperty("repositoryPath")
    private String repositoryPath;

    /**
     * Optional custom name for the connection.
     * If not provided, defaults to "{Provider} – {repo_name}"
     */
    @JsonProperty("connectionName")
    private String connectionName;

    /**
     * Optional base URL for self-hosted instances (e.g., GitHub Enterprise, GitLab self-hosted).
     * Ignored for cloud-only providers like Bitbucket Cloud.
     */
    @JsonProperty("baseUrl")
    private String baseUrl;

    public String getAccessToken() {
        return accessToken;
    }

    public void setAccessToken(String accessToken) {
        this.accessToken = accessToken;
    }

    public String getRepositoryPath() {
        return repositoryPath;
    }

    public void setRepositoryPath(String repositoryPath) {
        this.repositoryPath = repositoryPath;
    }

    public String getConnectionName() {
        return connectionName;
    }

    public void setConnectionName(String connectionName) {
        this.connectionName = connectionName;
    }

    public String getBaseUrl() {
        return baseUrl;
    }

    public void setBaseUrl(String baseUrl) {
        this.baseUrl = baseUrl;
    }
}
