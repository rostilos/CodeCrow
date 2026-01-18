package org.rostilos.codecrow.webserver.project.dto.request;

import jakarta.validation.constraints.NotNull;
import java.util.List;

public class UpdateRagConfigRequest {

    @NotNull(message = "Enabled flag is required")
    private Boolean enabled;

    private String branch;
    
    private List<String> excludePatterns;
    
    private Boolean deltaEnabled;
    
    private Integer deltaRetentionDays;

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
    
    public UpdateRagConfigRequest(Boolean enabled, String branch, List<String> excludePatterns,
                                   Boolean deltaEnabled, Integer deltaRetentionDays) {
        this.enabled = enabled;
        this.branch = branch;
        this.excludePatterns = excludePatterns;
        this.deltaEnabled = deltaEnabled;
        this.deltaRetentionDays = deltaRetentionDays;
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
    
    public List<String> getExcludePatterns() {
        return excludePatterns;
    }
    
    public void setExcludePatterns(List<String> excludePatterns) {
        this.excludePatterns = excludePatterns;
    }
    
    public Boolean getDeltaEnabled() {
        return deltaEnabled;
    }
    
    public void setDeltaEnabled(Boolean deltaEnabled) {
        this.deltaEnabled = deltaEnabled;
    }
    
    public Integer getDeltaRetentionDays() {
        return deltaRetentionDays;
    }
    
    public void setDeltaRetentionDays(Integer deltaRetentionDays) {
        this.deltaRetentionDays = deltaRetentionDays;
    }
}
