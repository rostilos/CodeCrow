package org.rostilos.codecrow.webserver.dto.request.project;

public class BindRepositoryRequest {
    private String provider; // e.g., BITBUCKET_CLOUD
    private Long connectionId;
    private String workspaceId;
    private String repositorySlug;
    private String repositoryId;
    private String defaultBranch;
    private String name;

    public String getProvider() {
        return provider;
    }

    public Long getConnectionId() {
        return connectionId;
    }

    public String getWorkspaceId() {
        return workspaceId;
    }

    public String getRepositorySlug() {
        return repositorySlug;
    }

    public String getRepositoryId() {
        return repositoryId;
    }

    public String getDefaultBranch() {
        return defaultBranch;
    }

    public String getName() {
        return name;
    }
}
