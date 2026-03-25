package org.rostilos.codecrow.core.model.taskmanagement;

import com.fasterxml.jackson.annotation.JsonIgnore;
import jakarta.persistence.*;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.annotations.UpdateTimestamp;
import org.hibernate.type.SqlTypes;
import org.rostilos.codecrow.core.model.workspace.Workspace;

import java.time.LocalDateTime;
import java.util.Map;

/**
 * Represents a task management platform connection for a workspace.
 * <p>
 * This is a provider-agnostic entity that can represent connections to
 * Jira Cloud, Jira Data Center (future), or other task management platforms.
 * </p>
 * <p>
 * Credentials are stored as an encrypted JSON map and decrypted at runtime
 * by the task management client factory.
 * </p>
 */
@Entity
@Table(name = "task_management_connection", indexes = {
        @Index(name = "idx_tm_connection_workspace", columnList = "workspace_id"),
        @Index(name = "idx_tm_connection_provider", columnList = "workspace_id, provider_type")
})
public class TaskManagementConnection {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "workspace_id", nullable = false)
    @JsonIgnore
    private Workspace workspace;

    /**
     * Display name for the connection (e.g., "Jira Cloud – My Org")
     */
    @Column(name = "connection_name", nullable = false, length = 256)
    private String connectionName;

    /**
     * The task management platform type (JIRA_CLOUD, JIRA_DATA_CENTER, etc.)
     */
    @Enumerated(EnumType.STRING)
    @Column(name = "provider_type", nullable = false, length = 64)
    private ETaskManagementProvider providerType;

    /**
     * Connection status.
     */
    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 32)
    private ETaskManagementConnectionStatus status;

    /**
     * Base URL of the platform instance (e.g. "https://myorg.atlassian.net").
     */
    @Column(name = "base_url", nullable = false, length = 512)
    private String baseUrl;

    /**
     * Encrypted credentials stored as JSON.
     * <p>
     * For Jira Cloud: {@code {"email": "...", "apiToken": "..."}}
     * For Jira Data Center (future): {@code {"personalAccessToken": "..."}}
     * </p>
     */
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "credentials", nullable = false)
    private Map<String, String> credentials;

    @CreationTimestamp
    @Column(name = "created_at")
    private LocalDateTime createdAt;

    @UpdateTimestamp
    @Column(name = "updated_at")
    private LocalDateTime updatedAt;

    @Version
    @Column(name = "version", nullable = false, columnDefinition = "BIGINT DEFAULT 0")
    private Long version = 0L;

    // ─── Getters & Setters ───────────────────────────────────────────

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

    public ETaskManagementProvider getProviderType() {
        return providerType;
    }

    public void setProviderType(ETaskManagementProvider providerType) {
        this.providerType = providerType;
    }

    public ETaskManagementConnectionStatus getStatus() {
        return status;
    }

    public void setStatus(ETaskManagementConnectionStatus status) {
        this.status = status;
    }

    public String getBaseUrl() {
        return baseUrl;
    }

    public void setBaseUrl(String baseUrl) {
        this.baseUrl = baseUrl;
    }

    public Map<String, String> getCredentials() {
        return credentials;
    }

    public void setCredentials(Map<String, String> credentials) {
        this.credentials = credentials;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }

    public Long getVersion() {
        return version;
    }
}
