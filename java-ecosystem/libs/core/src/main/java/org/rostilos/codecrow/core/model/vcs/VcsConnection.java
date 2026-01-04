package org.rostilos.codecrow.core.model.vcs;

import com.fasterxml.jackson.annotation.JsonIgnore;
import jakarta.persistence.*;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.annotations.UpdateTimestamp;
import org.hibernate.type.SqlTypes;
import org.rostilos.codecrow.core.model.vcs.config.VcsConnectionConfig;
import org.rostilos.codecrow.core.model.workspace.Workspace;

import java.time.LocalDateTime;

/**
 * Represents a VCS (Version Control System) connection for a workspace.
 * This is a provider-agnostic entity that can represent connections to
 * Bitbucket Cloud, Bitbucket Server, GitHub, GitLab, etc.
 */
@Entity
@Table(name = "vcs_connection", indexes = {
    @Index(name = "idx_vcs_connection_workspace", columnList = "workspace_id"),
    @Index(name = "idx_vcs_connection_provider", columnList = "workspace_id, provider_type"),
    @Index(name = "idx_vcs_connection_external", columnList = "provider_type, external_workspace_id")
})
public class VcsConnection {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "workspace_id", nullable = false)
    @JsonIgnore
    private Workspace workspace;

    /**
     * Display name for the connection (e.g., "Bitbucket Cloud – Foo Workspace")
     */
    @Column(nullable = false)
    private String connectionName;

    @Enumerated(EnumType.STRING)
    private EVcsSetupStatus setupStatus;

    /**
     * The VCS provider type (bitbucket-cloud, github, gitlab, etc.)
     */
    @Enumerated(EnumType.STRING)
    @Column(name = "provider_type")
    private EVcsProvider providerType;

    /**
     * The connection type within the provider (oauth-manual, app, etc.)
     */
    @Enumerated(EnumType.STRING)
    @Column(name = "connection_type")
    private EVcsConnectionType connectionType;

    /**
     * External workspace/organization ID from the provider.
     * For Bitbucket Cloud: workspace UUID
     * For GitHub: organization ID or user ID
     * For GitLab: group ID or user ID
     */
    @Column(name = "external_workspace_id", length = 128)
    private String externalWorkspaceId;

    /**
     * External workspace/organization slug (human-readable identifier).
     * For Bitbucket Cloud: workspace slug
     * For GitHub: org login or username
     * For GitLab: group path or username
     */
    @Column(name = "external_workspace_slug", length = 256)
    private String externalWorkspaceSlug;

    /**
     * App installation ID for app-style integrations.
     * Used by Bitbucket Cloud App, GitHub App, etc.
     */
    @Column(name = "installation_id", length = 128)
    private String installationId;

    /**
     * Full repository path for REPOSITORY_TOKEN connections.
     * For GitLab: "namespace/project" or project ID
     * For GitHub: "owner/repo"
     * For Bitbucket: "workspace/repo-slug"
     * Only set when connectionType = REPOSITORY_TOKEN
     */
    @Column(name = "repository_path", length = 512)
    private String repositoryPath;

    @Column(name = "access_token", length = 1024)
    private String accessToken;

    @Column(name = "refresh_token", length = 1024)
    private String refreshToken;

    @Column(name = "token_expires_at")
    private LocalDateTime tokenExpiresAt;

    /**
     * Comma-separated list of granted scopes
     */
    @Column(name = "scopes", length = 512)
    private String scopes;

    @Column(name = "repo_count")
    private int repoCount;

    @CreationTimestamp
    @Column(name = "created_at")
    private LocalDateTime createdAt;

    @UpdateTimestamp
    @Column(name = "updated_at")
    private LocalDateTime updatedAt;

    /**
     * Provider-specific configuration (JSON column).
     * Stores additional settings like OAuth keys for manual connections.
     */
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "configuration")
    private VcsConnectionConfig configuration;

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

    public String getConnectionName() {
        return connectionName;
    }

    public void setConnectionName(String connectionName) {
        this.connectionName = connectionName;
    }

    public EVcsSetupStatus getSetupStatus() {
        return setupStatus;
    }

    public void setSetupStatus(EVcsSetupStatus setupStatus){
        this.setupStatus = setupStatus;
    }

    public EVcsProvider getProviderType() {
        return providerType;
    }

    public void setProviderType(EVcsProvider providerType){
        this.providerType = providerType;
    }

    public EVcsConnectionType getConnectionType() {
        return connectionType;
    }

    public void setConnectionType(EVcsConnectionType connectionType) {
        this.connectionType = connectionType;
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

    public String getInstallationId() {
        return installationId;
    }

    public void setInstallationId(String installationId) {
        this.installationId = installationId;
    }

    public String getRepositoryPath() {
        return repositoryPath;
    }

    public void setRepositoryPath(String repositoryPath) {
        this.repositoryPath = repositoryPath;
    }

    public String getAccessToken() {
        return accessToken;
    }

    public void setAccessToken(String accessToken) {
        this.accessToken = accessToken;
    }

    public String getRefreshToken() {
        return refreshToken;
    }

    public void setRefreshToken(String refreshToken) {
        this.refreshToken = refreshToken;
    }

    public LocalDateTime getTokenExpiresAt() {
        return tokenExpiresAt;
    }

    public void setTokenExpiresAt(LocalDateTime tokenExpiresAt) {
        this.tokenExpiresAt = tokenExpiresAt;
    }

    public String getScopes() {
        return scopes;
    }

    public void setScopes(String scopes) {
        this.scopes = scopes;
    }

    public int getRepoCount() {
        return repoCount;
    }

    public void setRepoCount(int repoCount) {
        this.repoCount = repoCount;
    }

    public VcsConnectionConfig getConfiguration() {
        return configuration;
    }

    public void setConfiguration(VcsConnectionConfig configuration){
        this.configuration = configuration;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }

    /**
     * Check if the connection uses OAuth tokens (as opposed to API keys or other auth).
     */
    public boolean hasOAuthTokens() {
        return accessToken != null && !accessToken.isBlank();
    }

    /**
     * Check if the access token has expired.
     */
    public boolean isTokenExpired() {
        if (tokenExpiresAt == null) {
            return false;
        }
        return LocalDateTime.now().isAfter(tokenExpiresAt);
    }
}
