package org.rostilos.codecrow.webserver.project.dto;

import java.time.Instant;

import org.rostilos.codecrow.core.model.project.ProjectToken;

public class ProjectTokenDTO {
    private Long id;
    private String name;
    private Instant createdAt;
    private Instant expiresAt;

    public ProjectTokenDTO() {
    }

    public Long getId() {
        return id;
    }

    public ProjectTokenDTO setId(Long id) {
        this.id = id;
        return this;
    }

    public String getName() {
        return name;
    }

    public ProjectTokenDTO setName(String name) {
        this.name = name;
        return this;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public ProjectTokenDTO setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
        return this;
    }

    public Instant getExpiresAt() {
        return expiresAt;
    }

    public ProjectTokenDTO setExpiresAt(Instant expiresAt) {
        this.expiresAt = expiresAt;
        return this;
    }

    public static ProjectTokenDTO from(ProjectToken t) {
        ProjectTokenDTO dto = new ProjectTokenDTO();
        dto.setId(t.getId());
        dto.setName(t.getName());
        dto.setCreatedAt(t.getCreatedAt());
        dto.setExpiresAt(t.getExpiresAt());
        return dto;
    }
}
