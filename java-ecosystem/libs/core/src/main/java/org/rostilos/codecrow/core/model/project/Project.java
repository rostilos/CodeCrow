package org.rostilos.codecrow.core.model.project;

import java.time.OffsetDateTime;

import com.fasterxml.jackson.annotation.JsonBackReference;
import com.fasterxml.jackson.annotation.JsonManagedReference;
import jakarta.persistence.CascadeType;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.OneToOne;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.model.workspace.Workspace;

@Entity
@Table(name = "project",
        uniqueConstraints = {
                @UniqueConstraint(columnNames = {"workspace_id", "namespace"})
        })
public class Project {

    @Id
    @GeneratedValue
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "workspace_id", nullable = false)
    @JsonBackReference
    private Workspace workspace;

    @Column(name = "name", length = 128)
    private String name;

    @Column(name = "namespace", nullable = false, length = 128)
    private String namespace;

    @Column(name = "description", length = 1000)
    private String description;

    @Column(name = "is_active", nullable = false)
    private boolean active = true;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    private String authToken;

    @OneToOne(mappedBy = "project", cascade = CascadeType.ALL, orphanRemoval = true, fetch = FetchType.LAZY)
    @JsonManagedReference
    private ProjectVcsConnectionBinding vcsBinding;

    @OneToOne(mappedBy = "project", cascade = CascadeType.ALL, orphanRemoval = true, fetch = FetchType.LAZY)
    @JsonManagedReference
    private ProjectAiConnectionBinding aiBinding;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "configuration")
    private ProjectConfig configuration;

    @ManyToOne(fetch = FetchType.EAGER)
    @JoinColumn(name = "default_branch_id")
    private org.rostilos.codecrow.core.model.branch.Branch defaultBranch;

    @OneToOne(mappedBy = "project", fetch = FetchType.LAZY)
    @JsonManagedReference
    private VcsRepoBinding vcsRepoBinding;
    
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "quality_gate_id")
    private org.rostilos.codecrow.core.model.qualitygate.QualityGate qualityGate;
    
    @Column(name = "pr_analysis_enabled", nullable = false)
    private boolean prAnalysisEnabled = true;
    
    @Column(name = "branch_analysis_enabled", nullable = false)
    private boolean branchAnalysisEnabled = true;

    @PreUpdate
    public void onUpdate() {
        this.updatedAt = OffsetDateTime.now();
    }

    public Long getId() {
        return id;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    public String getNamespace() {
        return namespace;
    }

    public void setNamespace(String namespace) {
        this.namespace = namespace;
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

    public Workspace getWorkspace() {
        return workspace;
    }

    public void setWorkspace(Workspace workspace) {
        this.workspace = workspace;
    }

    public String getAuthToken() {
        return authToken;
    }

    public void setAuthToken(String authToken) {
        this.authToken = authToken;
    }

    public void setVcsBinding(ProjectVcsConnectionBinding projectVcsConnectionBinding) {
        this.vcsBinding = projectVcsConnectionBinding;
    }

    /**
     * @deprecated Use {@link #getEffectiveVcsRepoInfo()} or {@link #getVcsRepoBinding()} instead.
     *             This method returns the legacy Bitbucket-specific binding.
     */
    @Deprecated(since = "2.0", forRemoval = true)
    public ProjectVcsConnectionBinding getVcsBinding() {
        return this.vcsBinding;
    }

    /**
     * Returns the effective VCS repository information for this project.
     * <p>
     * This method provides a unified way to access VCS repository info regardless
     * of whether the project uses the legacy {@link ProjectVcsConnectionBinding}
     * or the new provider-agnostic {@link VcsRepoBinding}.
     * <p>
     * Priority: VcsRepoBinding (new) > ProjectVcsConnectionBinding (legacy)
     *
     * @return VcsRepoInfo if any binding exists, null otherwise
     */
    public VcsRepoInfo getEffectiveVcsRepoInfo() {
        if (vcsRepoBinding != null) {
            return vcsRepoBinding;
        }
        return vcsBinding;
    }

    /**
     * Returns the VCS connection for this project, checking both new and legacy bindings.
     *
     * @return VcsConnection if any binding exists, null otherwise
     */
    public VcsConnection getEffectiveVcsConnection() {
        VcsRepoInfo info = getEffectiveVcsRepoInfo();
        return info != null ? info.getVcsConnection() : null;
    }

    /**
     * Checks if this project has any VCS binding configured.
     *
     * @return true if either vcsRepoBinding or vcsBinding is present
     */
    public boolean hasVcsBinding() {
        return vcsRepoBinding != null || vcsBinding != null;
    }

    public void setAiConnectionBinding(ProjectAiConnectionBinding projectAiConnectionBinding) {
        this.aiBinding = projectAiConnectionBinding;
    }

    public ProjectAiConnectionBinding getAiBinding() {
        return this.aiBinding;
    }

    public org.rostilos.codecrow.core.model.project.config.ProjectConfig getConfiguration() {
        return configuration;
    }

    public void setConfiguration(org.rostilos.codecrow.core.model.project.config.ProjectConfig configuration) {
        this.configuration = configuration;
    }

    /**
     * Returns the effective project configuration.
     * If configuration is null, returns a new default ProjectConfig.
     * This ensures callers always get a valid config with default values.
     */
    public org.rostilos.codecrow.core.model.project.config.ProjectConfig getEffectiveConfig() {
        return configuration != null ? configuration : new org.rostilos.codecrow.core.model.project.config.ProjectConfig();
    }

    public org.rostilos.codecrow.core.model.branch.Branch getDefaultBranch() {
        return defaultBranch;
    }

    public void setDefaultBranch(org.rostilos.codecrow.core.model.branch.Branch defaultBranch) {
        this.defaultBranch = defaultBranch;
    }

    public VcsRepoBinding getVcsRepoBinding() {
        return vcsRepoBinding;
    }

    public void setVcsRepoBinding(VcsRepoBinding vcsRepoBinding) {
        this.vcsRepoBinding = vcsRepoBinding;
    }
    
    public org.rostilos.codecrow.core.model.qualitygate.QualityGate getQualityGate() {
        return qualityGate;
    }
    
    public void setQualityGate(org.rostilos.codecrow.core.model.qualitygate.QualityGate qualityGate) {
        this.qualityGate = qualityGate;
    }
    
    public boolean isPrAnalysisEnabled() {
        return prAnalysisEnabled;
    }
    
    public void setPrAnalysisEnabled(boolean prAnalysisEnabled) {
        this.prAnalysisEnabled = prAnalysisEnabled;
    }
    
    public boolean isBranchAnalysisEnabled() {
        return branchAnalysisEnabled;
    }
    
    public void setBranchAnalysisEnabled(boolean branchAnalysisEnabled) {
        this.branchAnalysisEnabled = branchAnalysisEnabled;
    }
}
