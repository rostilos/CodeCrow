package org.rostilos.codecrow.webserver.project.dto.request;

import jakarta.validation.constraints.NotNull;
import java.util.List;

public class UpdateRagConfigRequest {

    @NotNull(message = "Enabled flag is required")
    private Boolean enabled;

    private String branch;
    
    private List<String> includePatterns;
    
    private List<String> excludePatterns;
    
    private Boolean multiBranchEnabled;
    
    private Integer branchRetentionDays;

    public UpdateRagConfigRequest() {
    }

    public UpdateRagConfigRequest(Boolean enabled, String branch) {
        this.enabled = enabled;
        this.branch = branch;
    }
    
    public UpdateRagConfigRequest(Boolean enabled, String branch, List<String> excludePatterns) {
        this.enabled = enabled;
        this.branch = branch;
        this.excludePatterns = excludePatterns;
    }
    
    public UpdateRagConfigRequest(Boolean enabled, String branch, List<String> includePatterns,
                                   List<String> excludePatterns,
                                   Boolean multiBranchEnabled, Integer branchRetentionDays) {
        this.enabled = enabled;
        this.branch = branch;
        this.includePatterns = includePatterns;
        this.excludePatterns = excludePatterns;
        this.multiBranchEnabled = multiBranchEnabled;
        this.branchRetentionDays = branchRetentionDays;
    }

    public Boolean getEnabled() {
        return enabled;
    }

    public void setEnabled(Boolean enabled) {
        this.enabled = enabled;
    }

    public String getBranch() {
        return branch;
    }

    public void setBranch(String branch) {
        this.branch = branch;
    }
    
    public List<String> getIncludePatterns() {
        return includePatterns;
    }
    
    public void setIncludePatterns(List<String> includePatterns) {
        this.includePatterns = includePatterns;
    }
    
    public List<String> getExcludePatterns() {
        return excludePatterns;
    }
    
    public void setExcludePatterns(List<String> excludePatterns) {
        this.excludePatterns = excludePatterns;
    }
    
    public Boolean getMultiBranchEnabled() {
        return multiBranchEnabled;
    }
    
    public void setMultiBranchEnabled(Boolean multiBranchEnabled) {
        this.multiBranchEnabled = multiBranchEnabled;
    }
    
    public Integer getBranchRetentionDays() {
        return branchRetentionDays;
    }
    
    public void setBranchRetentionDays(Integer branchRetentionDays) {
        this.branchRetentionDays = branchRetentionDays;
    }
}
