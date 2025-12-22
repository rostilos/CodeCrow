package org.rostilos.codecrow.core.model.vcs;

import jakarta.persistence.*;
import org.hibernate.annotations.CreationTimestamp;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.workspace.Workspace;

import java.time.LocalDateTime;

/**
 * Tracks pending Forge app installations.
 * 
 * Security flow:
 * 1. User clicks "Install Bitbucket App" in CodeCrow UI
 * 2. Backend creates PendingForgeInstallation with user, workspace, and state token
 * 3. User is redirected to Atlassian install page
 * 4. Forge sends installed event - we match by state OR by workspace/user combination
 * 5. VcsConnection is created and linked to the correct CodeCrow workspace
 * 6. PendingForgeInstallation is marked as completed
 * 
 * Expiry: Pending installations expire after 30 minutes for security.
 */
@Entity
@Table(name = "pending_forge_installation", indexes = {
    @Index(name = "idx_pending_forge_state", columnList = "state"),
    @Index(name = "idx_pending_forge_workspace", columnList = "workspace_id"),
    @Index(name = "idx_pending_forge_user", columnList = "user_id"),
    @Index(name = "idx_pending_forge_status", columnList = "status")
})
public class PendingForgeInstallation {
    
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /**
     * Unique state token for tracking this installation.
     * Used in the install URL to match callback with initiating user.
     */
    @Column(nullable = false, unique = true, length = 64)
    private String state;

    /**
     * The CodeCrow workspace that will own this installation.
     */
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "workspace_id", nullable = false)
    private Workspace workspace;

    /**
     * The CodeCrow user who initiated the installation.
     */
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private User initiatedBy;

    /**
     * Provider type (should be BITBUCKET_CLOUD for Forge).
     */
    @Enumerated(EnumType.STRING)
    @Column(name = "provider_type", nullable = false)
    private EVcsProvider providerType;

    /**
     * Status of the installation.
     */
    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    private Status status = Status.PENDING;

    /**
     * External workspace ID from Bitbucket (filled after installation).
     */
    @Column(name = "external_workspace_id", length = 128)
    private String externalWorkspaceId;

    /**
     * External workspace slug from Bitbucket (filled after installation).
     */
    @Column(name = "external_workspace_slug", length = 256)
    private String externalWorkspaceSlug;

    /**
     * Resulting VcsConnection ID (filled after successful installation).
     */
    @Column(name = "vcs_connection_id")
    private Long vcsConnectionId;

    /**
     * Error message if installation failed.
     */
    @Column(length = 1000)
    private String errorMessage;

    @CreationTimestamp
    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "completed_at")
    private LocalDateTime completedAt;

    /**
     * Expiry time - installations expire after 30 minutes.
     */
    @Column(name = "expires_at", nullable = false)
    private LocalDateTime expiresAt;

    public enum Status {
        PENDING,      // User initiated, waiting for Forge callback
        COMPLETED,    // Successfully installed and linked
        EXPIRED,      // Timed out
        FAILED        // Installation failed
    }

    // Constructors
    public PendingForgeInstallation() {
    }

    public PendingForgeInstallation(String state, Workspace workspace, User initiatedBy, EVcsProvider providerType) {
        this.state = state;
        this.workspace = workspace;
        this.initiatedBy = initiatedBy;
        this.providerType = providerType;
        this.status = Status.PENDING;
        this.expiresAt = LocalDateTime.now().plusMinutes(30);
    }

    // Getters and Setters
    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getState() {
        return state;
    }

    public void setState(String state) {
        this.state = state;
    }

    public Workspace getWorkspace() {
        return workspace;
    }

    public void setWorkspace(Workspace workspace) {
        this.workspace = workspace;
    }

    public User getInitiatedBy() {
        return initiatedBy;
    }

    public void setInitiatedBy(User initiatedBy) {
        this.initiatedBy = initiatedBy;
    }

    public EVcsProvider getProviderType() {
        return providerType;
    }

    public void setProviderType(EVcsProvider providerType) {
        this.providerType = providerType;
    }

    public Status getStatus() {
        return status;
    }

    public void setStatus(Status status) {
        this.status = status;
    }

    public String getExternalWorkspaceId() {
        return externalWorkspaceId;
    }

    public void setExternalWorkspaceId(String externalWorkspaceId) {
        this.externalWorkspaceId = externalWorkspaceId;
    }

    public String getExternalWorkspaceSlug() {
        return externalWorkspaceSlug;
    }

    public void setExternalWorkspaceSlug(String externalWorkspaceSlug) {
        this.externalWorkspaceSlug = externalWorkspaceSlug;
    }

    public Long getVcsConnectionId() {
        return vcsConnectionId;
    }

    public void setVcsConnectionId(Long vcsConnectionId) {
        this.vcsConnectionId = vcsConnectionId;
    }

    public String getErrorMessage() {
        return errorMessage;
    }

    public void setErrorMessage(String errorMessage) {
        this.errorMessage = errorMessage;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }

    public LocalDateTime getCompletedAt() {
        return completedAt;
    }

    public void setCompletedAt(LocalDateTime completedAt) {
        this.completedAt = completedAt;
    }

    public LocalDateTime getExpiresAt() {
        return expiresAt;
    }

    public void setExpiresAt(LocalDateTime expiresAt) {
        this.expiresAt = expiresAt;
    }

    public boolean isExpired() {
        return LocalDateTime.now().isAfter(expiresAt);
    }

    public void markCompleted(Long vcsConnectionId, String externalWorkspaceId, String externalWorkspaceSlug) {
        this.status = Status.COMPLETED;
        this.vcsConnectionId = vcsConnectionId;
        this.externalWorkspaceId = externalWorkspaceId;
        this.externalWorkspaceSlug = externalWorkspaceSlug;
        this.completedAt = LocalDateTime.now();
    }

    public void markFailed(String errorMessage) {
        this.status = Status.FAILED;
        this.errorMessage = errorMessage;
        this.completedAt = LocalDateTime.now();
    }

    public void markExpired() {
        this.status = Status.EXPIRED;
        this.completedAt = LocalDateTime.now();
    }
}
