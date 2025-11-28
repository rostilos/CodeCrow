package org.rostilos.codecrow.core.model.project;

import java.time.Instant;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;

/**
 * Represents an API/auth token belonging to a Project.
 * The actual token value is stored encrypted in tokenEncrypted.
 * The plain token value is returned only at creation time by the API.
 */
@Entity
@Table(name = "project_token")
public class ProjectToken {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;

    @Column(name = "name")
    private String name;

    @Column(name = "token_encrypted", nullable = false, columnDefinition = "text")
    private String tokenEncrypted;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "expires_at")
    private Instant expiresAt;

    public ProjectToken() {
    }

    public Long getId() {
        return id;
    }

    public ProjectToken setId(Long id) {
        this.id = id;
        return this;
    }

    public Project getProject() {
        return project;
    }

    public ProjectToken setProject(Project project) {
        this.project = project;
        return this;
    }

    public String getName() {
        return name;
    }

    public ProjectToken setName(String name) {
        this.name = name;
        return this;
    }

    public String getTokenEncrypted() {
        return tokenEncrypted;
    }

    public ProjectToken setTokenEncrypted(String tokenEncrypted) {
        this.tokenEncrypted = tokenEncrypted;
        return this;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public ProjectToken setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
        return this;
    }

    public Instant getExpiresAt() {
        return expiresAt;
    }

    public ProjectToken setExpiresAt(Instant expiresAt) {
        this.expiresAt = expiresAt;
        return this;
    }
}
