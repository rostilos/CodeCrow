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

@Entity
@Table(name = "vcs_connection")
public class VcsConnection {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "workspace_id", nullable = false)
    @JsonIgnore
    private Workspace workspace;

    @Column(nullable = false)
    private String connectionName;

    @Enumerated(EnumType.STRING)
    private EVcsSetupStatus setupStatus;

    @Enumerated(EnumType.STRING)
    @Column(name = "provider_type")
    private EVcsProvider providerType;

    @Column(name = "repo_count")
    private int repoCount;

    @CreationTimestamp
    @Column(name = "created_at")
    private LocalDateTime createdAt;

    @UpdateTimestamp
    @Column(name = "updated_at")
    private LocalDateTime updatedAt;

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

    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }
}
