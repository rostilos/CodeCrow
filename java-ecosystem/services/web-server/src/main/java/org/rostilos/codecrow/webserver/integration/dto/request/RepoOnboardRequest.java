package org.rostilos.codecrow.webserver.integration.dto.request;

import jakarta.validation.constraints.NotNull;

/**
 * Request to onboard a repository from a VCS connection.
 */
public class RepoOnboardRequest {
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
    

    private String projectNamespace;
    

    private String projectDescription;
    

    private Long aiConnectionId;
    

    private boolean setupWebhooks = true;
    

    private String defaultBranch;
    

    private Boolean prAnalysisEnabled = true;
    

    private Boolean branchAnalysisEnabled = true;
    

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
    
    public String getDefaultBranch() {
        return defaultBranch;
    }
    
    public void setDefaultBranch(String defaultBranch) {
        this.defaultBranch = defaultBranch;
    }
    
    public Boolean getPrAnalysisEnabled() {
        return prAnalysisEnabled;
    }
    
    public void setPrAnalysisEnabled(Boolean prAnalysisEnabled) {
        this.prAnalysisEnabled = prAnalysisEnabled;
    }
    
    public Boolean getBranchAnalysisEnabled() {
        return branchAnalysisEnabled;
    }
    
    public void setBranchAnalysisEnabled(Boolean branchAnalysisEnabled) {
        this.branchAnalysisEnabled = branchAnalysisEnabled;
    }
}
