package org.rostilos.codecrow.webserver.vcs.dto.request.gitlab;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;

/**
 * Request DTO for creating a GitLab repository-scoped token connection.
 * Used with GitLab Project Access Tokens that are scoped to a single project.
 */
public class GitLabRepositoryTokenRequest {
    
    @NotBlank(message = "Access token is required")
    @JsonProperty("accessToken")
    private String accessToken;

    /**
     * Full repository path (e.g., "namespace/project-name") or numeric project ID.
     * For project access tokens, this is the project the token is scoped to.
     */
    @NotBlank(message = "Repository path is required")
    @JsonProperty("repositoryPath")
    private String repositoryPath;
    
    /**
     * Optional custom name for the connection.
     * If not provided, defaults to "GitLab – {repository_name}"
     */
    @JsonProperty("connectionName")
    private String connectionName;

    /**
     * GitLab instance base URL for self-hosted instances.
     * Defaults to "https://gitlab.com" if not specified.
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
