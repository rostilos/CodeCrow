package org.rostilos.codecrow.webserver.project.dto.request;

import jakarta.validation.constraints.NotNull;

/**
 * Request to change the VCS connection for a project.
 * This will unbind the current repository and bind a new one.
 */
public class ChangeVcsConnectionRequest {
    
    @NotNull(message = "Connection ID is required")
    private Long connectionId;
    
    @NotNull(message = "Repository slug is required")
    private String repositorySlug;
    
    private String workspaceId;
    
    private String repositoryId;
    
    private String defaultBranch;
    
    private boolean setupWebhooks = true;
    
    private boolean clearAnalysisHistory = false;

    public Long getConnectionId() {
        return connectionId;
    }

    public void setConnectionId(Long connectionId) {
        this.connectionId = connectionId;
    }

    public String getRepositorySlug() {
        return repositorySlug;
    }

    public void setRepositorySlug(String repositorySlug) {
        this.repositorySlug = repositorySlug;
    }

    public String getWorkspaceId() {
        return workspaceId;
    }

    public void setWorkspaceId(String workspaceId) {
        this.workspaceId = workspaceId;
    }

    public String getRepositoryId() {
        return repositoryId;
    }

    public void setRepositoryId(String repositoryId) {
        this.repositoryId = repositoryId;
    }

    public String getDefaultBranch() {
        return defaultBranch;
    }

    public void setDefaultBranch(String defaultBranch) {
        this.defaultBranch = defaultBranch;
    }

    public boolean isSetupWebhooks() {
        return setupWebhooks;
    }

    public void setSetupWebhooks(boolean setupWebhooks) {
        this.setupWebhooks = setupWebhooks;
    }

    public boolean isClearAnalysisHistory() {
        return clearAnalysisHistory;
    }

    public void setClearAnalysisHistory(boolean clearAnalysisHistory) {
        this.clearAnalysisHistory = clearAnalysisHistory;
    }
}
