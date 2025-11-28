package org.rostilos.codecrow.webserver.dto.permission;

import java.time.OffsetDateTime;
import java.util.Set;

public class PermissionTemplateDTO {
    private Long id;
    private String name;
    private String description;
    private Set<String> permissions;
    private Long createdByUserId;
    private OffsetDateTime createdAt;

    public PermissionTemplateDTO() {
    }

    public PermissionTemplateDTO(Long id, String name, String description, Set<String> permissions, Long createdByUserId, OffsetDateTime createdAt) {
        this.id = id;
        this.name = name;
        this.description = description;
        this.permissions = permissions;
        this.createdByUserId = createdByUserId;
        this.createdAt = createdAt;
    }

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    public String getDescription() {
        return description;
    }

    public void setDescription(String description) {
        this.description = description;
    }

    public Set<String> getPermissions() {
        return permissions;
    }

    public void setPermissions(Set<String> permissions) {
        this.permissions = permissions;
    }

    public Long getCreatedByUserId() {
        return createdByUserId;
    }

    public void setCreatedByUserId(Long createdByUserId) {
        this.createdByUserId = createdByUserId;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
