package org.rostilos.codecrow.core.model.workspace;

import com.fasterxml.jackson.annotation.JsonManagedReference;
import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;
import java.util.HashSet;
import java.util.Set;

@Entity
@Table(name = "workspace",
        uniqueConstraints = {
                @UniqueConstraint(columnNames = {"name"}),
                @UniqueConstraint(columnNames = {"slug"})
        })
public class Workspace {

    @Id
    @GeneratedValue
    @Column(nullable = false, updatable = false)
    private Long id;

    @Column(name = "slug", nullable = false, unique = true, length = 64)
    private String slug;

    @Column(name = "name", nullable = false, length = 128)
    private String name;

    @Column(name = "description", length = 1000)
    private String description;

    @Column(name = "is_active", nullable = false)
    private boolean active = true;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @Column(name = "scheduled_deletion_at")
    private OffsetDateTime scheduledDeletionAt;

    @Column(name = "deletion_requested_by")
    private Long deletionRequestedBy;

    @Column(name = "deletion_requested_at")
    private OffsetDateTime deletionRequestedAt;

    @OneToMany(mappedBy = "workspace", cascade = CascadeType.ALL, orphanRemoval = true)
    @JsonManagedReference
    private Set<Project> projects = new HashSet<>();

    @PreUpdate
    public void onUpdate() {
        this.updatedAt = OffsetDateTime.now();
    }

    public Workspace() {
    }

    public Workspace(String slug, String name, String description) {
        this.slug = slug;
        this.name = name;
        this.description = description;
    }

    public Long getId() {
        return id;
    }

    public String getSlug() {
        return slug;
    }

    public void setSlug(String slug) {
        this.slug = slug;
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

    public boolean getIsActive() {
        return active;
    }

    public void setIsActive(boolean active) {
        this.active = active;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public OffsetDateTime getUpdatedAt() {
        return updatedAt;
    }

    public Set<Project> getProjects() {
        return projects;
    }

    public void addProject(Project project) {
        projects.add(project);
        project.setWorkspace(this);
    }

    public void removeProject(Project project) {
        projects.remove(project);
        project.setWorkspace(null);
    }

    public OffsetDateTime getScheduledDeletionAt() {
        return scheduledDeletionAt;
    }

    public void setScheduledDeletionAt(OffsetDateTime scheduledDeletionAt) {
        this.scheduledDeletionAt = scheduledDeletionAt;
    }

    public Long getDeletionRequestedBy() {
        return deletionRequestedBy;
    }

    public void setDeletionRequestedBy(Long deletionRequestedBy) {
        this.deletionRequestedBy = deletionRequestedBy;
    }

    public OffsetDateTime getDeletionRequestedAt() {
        return deletionRequestedAt;
    }

    public void setDeletionRequestedAt(OffsetDateTime deletionRequestedAt) {
        this.deletionRequestedAt = deletionRequestedAt;
    }

    public boolean isScheduledForDeletion() {
        return scheduledDeletionAt != null;
    }

    public void scheduleDeletion(Long requestedBy) {
        this.deletionRequestedBy = requestedBy;
        this.deletionRequestedAt = OffsetDateTime.now();
        this.scheduledDeletionAt = OffsetDateTime.now().plusWeeks(1);
    }

    public void cancelDeletion() {
        this.deletionRequestedBy = null;
        this.deletionRequestedAt = null;
        this.scheduledDeletionAt = null;
    }
}
