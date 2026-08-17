package org.rostilos.codecrow.webserver.project.dto.request;

import com.fasterxml.jackson.annotation.JsonSetter;
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
    private String projectType;
    private String sourceRoot;
    private boolean projectTypeSpecified;
    private boolean sourceRootSpecified;

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

    public String getProjectType() {
        return projectType;
    }

    @JsonSetter("projectType")
    public void setProjectType(String projectType) {
        this.projectType = projectType;
        this.projectTypeSpecified = true;
    }

    public String getSourceRoot() {
        return sourceRoot;
    }

    @JsonSetter("sourceRoot")
    public void setSourceRoot(String sourceRoot) {
        this.sourceRoot = sourceRoot;
        this.sourceRootSpecified = true;
    }

    public boolean hasAnalysisProfileUpdate() {
        return projectTypeSpecified || sourceRootSpecified;
    }

    public boolean hasProjectTypeUpdate() {
        return projectTypeSpecified;
    }

    public boolean hasSourceRootUpdate() {
        return sourceRootSpecified;
    }
}
