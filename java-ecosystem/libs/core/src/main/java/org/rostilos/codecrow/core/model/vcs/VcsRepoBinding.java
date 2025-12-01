package org.rostilos.codecrow.core.model.vcs;

import com.fasterxml.jackson.annotation.JsonBackReference;
import jakarta.persistence.*;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;

import java.time.LocalDateTime;

/**
 * Represents a binding between a CodeCrow project and an external VCS repository.
 * This is a provider-agnostic entity that stores the mapping for any VCS provider.
 */
@Entity
@Table(name = "vcs_repo_binding", indexes = {
    @Index(name = "idx_vcs_repo_binding_project", columnList = "project_id"),
    @Index(name = "idx_vcs_repo_binding_workspace", columnList = "workspace_id"),
    @Index(name = "idx_vcs_repo_binding_connection", columnList = "vcs_connection_id"),
    @Index(name = "idx_vcs_repo_binding_external", columnList = "provider, external_repo_id")
}, uniqueConstraints = {
    @UniqueConstraint(name = "uq_vcs_repo_binding_external", columnNames = {"provider", "external_repo_id"})
})
public class VcsRepoBinding implements VcsRepoInfo {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    /**
     * The workspace this binding belongs to (denormalized for faster queries)
     */
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "workspace_id", nullable = false)
    @JsonBackReference
    private Workspace workspace;

    /**
     * The CodeCrow project this repository is bound to
     */
    @OneToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    @JsonBackReference
    private Project project;

    /**
     * The VCS connection used to access this repository
     */
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "vcs_connection_id", nullable = false)
    private VcsConnection vcsConnection;

    /**
     * VCS provider (denormalized from VcsConnection for faster filtering)
     */
    @Enumerated(EnumType.STRING)
    @Column(name = "provider", nullable = false, length = 32)
    private EVcsProvider provider;

    /**
     * The stable repository ID from the external provider.
     * - Bitbucket Cloud: repository UUID (without braces)
     * - GitHub: repository node ID or numeric ID
     * - GitLab: project ID
     */
    @Column(name = "external_repo_id", nullable = false, length = 128)
    private String externalRepoId;

    /**
     * Human-readable repository slug/name
     */
    @Column(name = "external_repo_slug", length = 256)
    private String externalRepoSlug;

    /**
     * The workspace/namespace this repo belongs to in the external provider.
     * - Bitbucket Cloud: workspace slug
     * - GitHub: owner login (org or user)
     * - GitLab: namespace path
     */
    @Column(name = "external_namespace", length = 256)
    private String externalNamespace;

    /**
     * Display name for the repository
     */
    @Column(name = "display_name", length = 256)
    private String displayName;

    /**
     * Default branch name
     */
    @Column(name = "default_branch", length = 128)
    private String defaultBranch;

    /**
     * Whether webhooks are configured for this repository
     */
    @Column(name = "webhooks_configured")
    private boolean webhooksConfigured = false;

    /**
     * Webhook ID(s) if any are registered (for cleanup)
     */
    @Column(name = "webhook_id", length = 256)
    private String webhookId;

    @CreationTimestamp
    @Column(name = "created_at")
    private LocalDateTime createdAt;

    @UpdateTimestamp
    @Column(name = "updated_at")
    private LocalDateTime updatedAt;

    // Getters and Setters

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public Workspace getWorkspace() {
        return workspace;
    }

    public void setWorkspace(Workspace workspace) {
        this.workspace = workspace;
    }

    public Project getProject() {
        return project;
    }

    public void setProject(Project project) {
        this.project = project;
    }

    @Override
    public VcsConnection getVcsConnection() {
        return vcsConnection;
    }

    public void setVcsConnection(VcsConnection vcsConnection) {
        this.vcsConnection = vcsConnection;
    }

    public EVcsProvider getProvider() {
        return provider;
    }

    public void setProvider(EVcsProvider provider) {
        this.provider = provider;
    }

    public String getExternalRepoId() {
        return externalRepoId;
    }

    public void setExternalRepoId(String externalRepoId) {
        this.externalRepoId = externalRepoId;
    }

    public String getExternalRepoSlug() {
        return externalRepoSlug;
    }

    public void setExternalRepoSlug(String externalRepoSlug) {
        this.externalRepoSlug = externalRepoSlug;
    }

    public String getExternalNamespace() {
        return externalNamespace;
    }

    public void setExternalNamespace(String externalNamespace) {
        this.externalNamespace = externalNamespace;
    }

    public String getDisplayName() {
        return displayName;
    }

    public void setDisplayName(String displayName) {
        this.displayName = displayName;
    }

    public String getDefaultBranch() {
        return defaultBranch;
    }

    public void setDefaultBranch(String defaultBranch) {
        this.defaultBranch = defaultBranch;
    }

    public boolean isWebhooksConfigured() {
        return webhooksConfigured;
    }

    public void setWebhooksConfigured(boolean webhooksConfigured) {
        this.webhooksConfigured = webhooksConfigured;
    }

    public String getWebhookId() {
        return webhookId;
    }

    public void setWebhookId(String webhookId) {
        this.webhookId = webhookId;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }

    /**
     * Get the full repository name in the format "namespace/slug"
     */
    public String getFullName() {
        if (externalNamespace != null && externalRepoSlug != null) {
            return externalNamespace + "/" + externalRepoSlug;
        }
        return displayName;
    }

    @Override
    public String getRepoWorkspace() {
        return externalNamespace;
    }

    @Override
    public String getRepoSlug() {
        return externalRepoSlug;
    }
}
