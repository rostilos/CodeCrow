package org.rostilos.codecrow.core.model.project;

import jakarta.persistence.*;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.UpdateTimestamp;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;

import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * Entity representing a VCS user who is allowed to execute CodeCrow commands.
 * Used when CommandAuthorizationMode is set to ALLOWED_USERS_ONLY.
 * 
 * This allows workspace admins to maintain a list of specific users
 * who can trigger CodeCrow analysis commands via PR comments.
 */
@Entity
@Table(name = "allowed_command_users", 
    uniqueConstraints = @UniqueConstraint(
        columnNames = {"project_id", "vcs_user_id"},
        name = "uk_allowed_command_user_project_vcs"
    ),
    indexes = {
        @Index(name = "idx_allowed_cmd_user_project", columnList = "project_id"),
        @Index(name = "idx_allowed_cmd_user_vcs_id", columnList = "vcs_user_id")
    }
)
public class AllowedCommandUser {
    
    @Id
    @GeneratedValue(strategy = GenerationType.UUID)
    private UUID id;
    
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;
    
    /**
     * VCS provider (BITBUCKET_CLOUD, GITHUB, etc.)
     */
    @Enumerated(EnumType.STRING)
    @Column(name = "vcs_provider", nullable = false)
    private EVcsProvider vcsProvider;
    
    /**
     * Unique user ID from the VCS provider.
     * - Bitbucket: Account UUID (e.g., "{abc123-def456-...}")
     * - GitHub: User ID (numeric, stored as string)
     */
    @Column(name = "vcs_user_id", nullable = false)
    private String vcsUserId;
    
    /**
     * VCS username for display purposes.
     * - Bitbucket: Account nickname
     * - GitHub: Login username
     */
    @Column(name = "vcs_username", nullable = false)
    private String vcsUsername;
    
    /**
     * Display name from VCS profile (if available).
     */
    @Column(name = "display_name")
    private String displayName;
    
    /**
     * Avatar URL from VCS profile (if available).
     */
    @Column(name = "avatar_url")
    private String avatarUrl;
    
    /**
     * User's permission level on the repository (if known).
     * - Bitbucket: "read", "write", "admin"
     * - GitHub: "read", "triage", "write", "maintain", "admin"
     */
    @Column(name = "repo_permission")
    private String repoPermission;
    
    /**
     * Whether this user was synced from VCS collaborators list
     * or manually added by workspace admin.
     */
    @Column(name = "synced_from_vcs", nullable = false)
    private boolean syncedFromVcs = false;
    
    /**
     * Whether this user is currently active/enabled.
     * Allows "soft disable" without removing the user.
     */
    @Column(name = "enabled", nullable = false)
    private boolean enabled = true;
    
    /**
     * Who added this user (CodeCrow user ID or "SYSTEM" for sync).
     */
    @Column(name = "added_by")
    private String addedBy;
    
    @CreationTimestamp
    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt;
    
    @UpdateTimestamp
    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt;
    
    /**
     * Last time this user was synced from VCS.
     */
    @Column(name = "last_synced_at")
    private OffsetDateTime lastSyncedAt;
    
    // Constructors
    public AllowedCommandUser() {}
    
    public AllowedCommandUser(Project project, EVcsProvider vcsProvider, 
                              String vcsUserId, String vcsUsername) {
        this.project = project;
        this.vcsProvider = vcsProvider;
        this.vcsUserId = vcsUserId;
        this.vcsUsername = vcsUsername;
    }
    
    // Getters and Setters
    public UUID getId() { return id; }
    public void setId(UUID id) { this.id = id; }
    
    public Project getProject() { return project; }
    public void setProject(Project project) { this.project = project; }
    
    public EVcsProvider getVcsProvider() { return vcsProvider; }
    public void setVcsProvider(EVcsProvider vcsProvider) { this.vcsProvider = vcsProvider; }
    
    public String getVcsUserId() { return vcsUserId; }
    public void setVcsUserId(String vcsUserId) { this.vcsUserId = vcsUserId; }
    
    public String getVcsUsername() { return vcsUsername; }
    public void setVcsUsername(String vcsUsername) { this.vcsUsername = vcsUsername; }
    
    public String getDisplayName() { return displayName; }
    public void setDisplayName(String displayName) { this.displayName = displayName; }
    
    public String getAvatarUrl() { return avatarUrl; }
    public void setAvatarUrl(String avatarUrl) { this.avatarUrl = avatarUrl; }
    
    public String getRepoPermission() { return repoPermission; }
    public void setRepoPermission(String repoPermission) { this.repoPermission = repoPermission; }
    
    public boolean isSyncedFromVcs() { return syncedFromVcs; }
    public void setSyncedFromVcs(boolean syncedFromVcs) { this.syncedFromVcs = syncedFromVcs; }
    
    public boolean isEnabled() { return enabled; }
    public void setEnabled(boolean enabled) { this.enabled = enabled; }
    
    public String getAddedBy() { return addedBy; }
    public void setAddedBy(String addedBy) { this.addedBy = addedBy; }
    
    public OffsetDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(OffsetDateTime createdAt) { this.createdAt = createdAt; }
    
    public OffsetDateTime getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(OffsetDateTime updatedAt) { this.updatedAt = updatedAt; }
    
    public OffsetDateTime getLastSyncedAt() { return lastSyncedAt; }
    public void setLastSyncedAt(OffsetDateTime lastSyncedAt) { this.lastSyncedAt = lastSyncedAt; }
    
    @Override
    public String toString() {
        return "AllowedCommandUser{" +
                "id=" + id +
                ", vcsProvider=" + vcsProvider +
                ", vcsUserId='" + vcsUserId + '\'' +
                ", vcsUsername='" + vcsUsername + '\'' +
                ", enabled=" + enabled +
                '}';
    }
}
