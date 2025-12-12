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
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
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

    public ProjectVcsConnectionBinding getVcsBinding() {
        return this.vcsBinding;
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
