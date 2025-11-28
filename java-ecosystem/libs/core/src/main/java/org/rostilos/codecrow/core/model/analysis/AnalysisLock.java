package org.rostilos.codecrow.core.model.analysis;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

@Entity
@Table(name = "analysis_lock",
    uniqueConstraints = {
        @UniqueConstraint(name = "uq_analysis_lock",
            columnNames = {"project_id", "branch_name", "analysis_type"})
    },
    indexes = {
        @Index(name = "idx_lock_expiry", columnList = "expires_at"),
        @Index(name = "idx_lock_project_branch", columnList = "project_id, branch_name")
    }
)
public class AnalysisLock {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;

    @Column(name = "branch_name", nullable = false, length = 200)
    private String branchName;

    @Enumerated(EnumType.STRING)
    @Column(name = "analysis_type", nullable = false, length = 50)
    private AnalysisLockType analysisType;

    @Column(name = "lock_key", nullable = false, unique = true, length = 500)
    private String lockKey;

    @Column(name = "owner_instance_id", length = 100)
    private String ownerInstanceId;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "expires_at", nullable = false)
    private OffsetDateTime expiresAt;

    @Column(name = "commit_hash", length = 40)
    private String commitHash;

    @Column(name = "pr_number")
    private Long prNumber;

    public AnalysisLock() {
    }

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public Project getProject() {
        return project;
    }

    public void setProject(Project project) {
        this.project = project;
    }

    public String getBranchName() {
        return branchName;
    }

    public void setBranchName(String branchName) {
        this.branchName = branchName;
    }

    public AnalysisLockType getAnalysisType() {
        return analysisType;
    }

    public void setAnalysisType(AnalysisLockType analysisType) {
        this.analysisType = analysisType;
    }

    public String getLockKey() {
        return lockKey;
    }

    public void setLockKey(String lockKey) {
        this.lockKey = lockKey;
    }

    public String getOwnerInstanceId() {
        return ownerInstanceId;
    }

    public void setOwnerInstanceId(String ownerInstanceId) {
        this.ownerInstanceId = ownerInstanceId;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }

    public OffsetDateTime getExpiresAt() {
        return expiresAt;
    }

    public void setExpiresAt(OffsetDateTime expiresAt) {
        this.expiresAt = expiresAt;
    }

    public String getCommitHash() {
        return commitHash;
    }

    public void setCommitHash(String commitHash) {
        this.commitHash = commitHash;
    }

    public Long getPrNumber() {
        return prNumber;
    }

    public void setPrNumber(Long prNumber) {
        this.prNumber = prNumber;
    }

    public boolean isExpired() {
        return OffsetDateTime.now().isAfter(expiresAt);
    }
}

