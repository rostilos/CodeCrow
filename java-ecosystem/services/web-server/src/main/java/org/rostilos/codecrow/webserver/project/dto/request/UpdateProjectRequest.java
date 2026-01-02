package org.rostilos.codecrow.webserver.project.dto.request;

import jakarta.validation.constraints.NotBlank;

public class UpdateProjectRequest {
    @NotBlank(message = "Project name is required")
    private String name;

    @NotBlank(message = "Project namespace is required")
    private String namespace;

    private String description;

    // optional: update default branch for the project (e.g. "main")
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

    public String getDefaultBranch() {
        return defaultBranch;
    }
}
