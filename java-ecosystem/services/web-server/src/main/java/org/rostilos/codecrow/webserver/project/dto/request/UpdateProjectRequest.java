package org.rostilos.codecrow.webserver.project.dto.request;

import jakarta.validation.constraints.NotBlank;

public class UpdateProjectRequest {
    @NotBlank(message = "Project name is required")
    private String name;

    @NotBlank(message = "Project namespace is required")
    private String namespace;

    private String description;

    // Main branch - the primary branch used as baseline for RAG indexing and analysis
    private String mainBranch;
    
    /**
     * @deprecated Use mainBranch instead
     */
    @Deprecated
    private String defaultBranch;

    public String getName() {
        return name;
    }

    public String getNamespace() {
        return namespace;
    }

    public String getDescription() {
        return description;
    }

    public String getMainBranch() {
        return mainBranch != null ? mainBranch : defaultBranch;
    }
    
    /**
     * @deprecated Use getMainBranch() instead
     */
    @Deprecated
    public String getDefaultBranch() {
        return getMainBranch();
    }
}
