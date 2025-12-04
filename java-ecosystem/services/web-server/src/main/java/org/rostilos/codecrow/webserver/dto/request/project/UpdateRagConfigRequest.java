package org.rostilos.codecrow.webserver.dto.request.project;

import jakarta.validation.constraints.NotNull;
import java.util.List;

public class UpdateRagConfigRequest {

    @NotNull(message = "Enabled flag is required")
    private Boolean enabled;

    private String branch;
    
    private List<String> excludePatterns;

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
}
