package org.rostilos.codecrow.webserver.dto.request.integration;

import jakarta.validation.constraints.NotNull;

/**
 * Request to onboard a repository from a VCS connection.
 */
public class RepoOnboardRequest {
    
    /**
     * The VCS connection ID to use for this repository.
     */
    @NotNull
    private Long vcsConnectionId;
    
    /**
     * Existing project ID to bind this repository to.
     * If null, a new project will be created.
     */
    private Long projectId;
    
    /**
     * Name for the new project (required if projectId is null).
     */
    private String projectName;
    
    /**
     * Optional namespace for the new project.
     */
    private String projectNamespace;
    
    /**
     * Optional description for the new project.
     */
    private String projectDescription;
    
    /**
     * Optional AI connection ID to bind to the project.
     */
    private Long aiConnectionId;
    
    /**
     * Whether to set up webhooks for this repository.
     */
    private boolean setupWebhooks = true;
    
    // Getters and Setters
    
    public Long getVcsConnectionId() {
        return vcsConnectionId;
    }
    
    public void setVcsConnectionId(Long vcsConnectionId) {
        this.vcsConnectionId = vcsConnectionId;
    }
    
    public Long getProjectId() {
        return projectId;
    }
    
    public void setProjectId(Long projectId) {
        this.projectId = projectId;
    }
    
    public String getProjectName() {
        return projectName;
    }
    
    public void setProjectName(String projectName) {
        this.projectName = projectName;
    }
    
    public String getProjectNamespace() {
        return projectNamespace;
    }
    
    public void setProjectNamespace(String projectNamespace) {
        this.projectNamespace = projectNamespace;
    }
    
    public String getProjectDescription() {
        return projectDescription;
    }
    
    public void setProjectDescription(String projectDescription) {
        this.projectDescription = projectDescription;
    }
    
    public Long getAiConnectionId() {
        return aiConnectionId;
    }
    
    public void setAiConnectionId(Long aiConnectionId) {
        this.aiConnectionId = aiConnectionId;
    }
    
    public boolean isSetupWebhooks() {
        return setupWebhooks;
    }
    
    public void setSetupWebhooks(boolean setupWebhooks) {
        this.setupWebhooks = setupWebhooks;
    }
}
