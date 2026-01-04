package org.rostilos.codecrow.webserver.project.dto.request;

import java.util.UUID;

import org.rostilos.codecrow.core.model.vcs.EVcsProvider;

import com.fasterxml.jackson.annotation.JsonInclude;

import jakarta.validation.constraints.NotBlank;

@JsonInclude(JsonInclude.Include.NON_NULL)
public class CreateProjectRequest {
    @NotBlank(message = "Project name is required")
    private String name;

    @NotBlank(message = "Project namespace is required")
    private String namespace;

    private String description;

    @NotBlank(message = "Creation mode is required")
    private EProjectCreationMode creationMode; // MANUAL or IMPORT

    private EVcsProvider vcsProvider;
    private Long vcsConnectionId;
    private String repositorySlug;
    private UUID repositoryUUID;

    // optional default branch (e.g. "main" or "master")
    private String defaultBranch;
    
    private Long aiConnectionId;

    public String getName() {
        return name;
    }

    public String getNamespace() {
        return namespace;
    }

    public EProjectCreationMode getCreationMode() {
        return creationMode;
    }

    public EVcsProvider getVcsProvider() {
        return vcsProvider;
    }

    public Long getVcsConnectionId() {
        return vcsConnectionId;
    }

    public boolean hasVcsConnection() {
        return vcsConnectionId != null && vcsProvider != null;
    }

    public String getRepositorySlug() {
        return repositorySlug;
    }

    public UUID getRepositoryUUID() {
        return repositoryUUID;
    }

    public String getDescription() {
        return description;
    }

    public boolean isImportMode() {
        return EProjectCreationMode.IMPORT.equals(creationMode);
    }

    public String getDefaultBranch() {
        return defaultBranch;
    }
    
    public Long getAiConnectionId() {
        return aiConnectionId;
    }
}
