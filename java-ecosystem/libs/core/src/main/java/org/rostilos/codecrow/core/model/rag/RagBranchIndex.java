package org.rostilos.codecrow.core.model.rag;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

/**
 * Registry head for one branch's immutable physical generations.
 */
@Entity
@Table(name = "rag_branch_index",
    uniqueConstraints = {
        @UniqueConstraint(columnNames = {"project_id", "branch_name"})
    },
    indexes = {
        @Index(name = "idx_rag_branch_project", columnList = "project_id"),
        @Index(name = "idx_rag_branch_name", columnList = "branch_name")
    }
)
public class RagBranchIndex {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;

    /**
     * The branch name (e.g., "main", "feature/xyz", "release/1.0").
     */
    @Column(name = "branch_name", nullable = false, length = 256)
    private String branchName;

    @Enumerated(EnumType.STRING)
    @Column(name = "index_kind", nullable = false, length = 24)
    private RagBranchIndexKind indexKind;

    @Enumerated(EnumType.STRING)
    @Column(name = "lifecycle_status", nullable = false, length = 24)
    private RagBranchIndexLifecycleStatus lifecycleStatus = RagBranchIndexLifecycleStatus.READY;

    @Column(name = "desired_commit_hash", length = 64)
    private String desiredCommitHash;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "active_generation_id")
    private RagBranchIndexGeneration activeGeneration;

    @Column(name = "last_accessed_at")
    private OffsetDateTime lastAccessedAt;

    @Column(name = "error_message", columnDefinition = "TEXT")
    private String errorMessage;

    @Column(name = "last_failed_at")
    private OffsetDateTime lastFailedAt;

    @Column(name = "cleanup_claim_token", length = 64)
    private String cleanupClaimToken;

    @Column(name = "cleanup_claimed_at")
    private OffsetDateTime cleanupClaimedAt;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @PreUpdate
    protected void onUpdate() {
        this.updatedAt = OffsetDateTime.now();
    }

    public RagBranchIndex() {
    }

    public RagBranchIndex(
            Project project,
            String branchName,
            RagBranchIndexKind indexKind) {
        this.project = project;
        this.branchName = branchName;
        this.indexKind = indexKind;
        this.lifecycleStatus = RagBranchIndexLifecycleStatus.PENDING;
    }

    public void requestRevision(String desiredCommitHash) {
        this.desiredCommitHash = desiredCommitHash;
        this.lifecycleStatus = RagBranchIndexLifecycleStatus.BUILDING;
        this.errorMessage = null;
    }

    public void activate(RagBranchIndexGeneration generation) {
        this.activeGeneration = generation;
        this.desiredCommitHash = generation.getRevision();
        this.lifecycleStatus = RagBranchIndexLifecycleStatus.READY;
        this.errorMessage = null;
        this.lastFailedAt = null;
        this.lastAccessedAt = OffsetDateTime.now();
    }

    public void failUpdate(String errorMessage) {
        this.lifecycleStatus = activeGeneration == null
                ? RagBranchIndexLifecycleStatus.FAILED
                : RagBranchIndexLifecycleStatus.READY;
        this.errorMessage = errorMessage;
        this.lastFailedAt = OffsetDateTime.now();
    }

    public void markAccessed() {
        this.lastAccessedAt = OffsetDateTime.now();
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

    public RagBranchIndexKind getIndexKind() {
        return indexKind;
    }

    public void setIndexKind(RagBranchIndexKind indexKind) {
        this.indexKind = indexKind;
    }

    public RagBranchIndexLifecycleStatus getLifecycleStatus() {
        return lifecycleStatus;
    }

    public void setLifecycleStatus(RagBranchIndexLifecycleStatus lifecycleStatus) {
        this.lifecycleStatus = lifecycleStatus;
    }

    public String getDesiredCommitHash() {
        return desiredCommitHash;
    }

    public void setDesiredCommitHash(String desiredCommitHash) {
        this.desiredCommitHash = desiredCommitHash;
    }

    public RagBranchIndexGeneration getActiveGeneration() {
        return activeGeneration;
    }

    public void setActiveGeneration(RagBranchIndexGeneration activeGeneration) {
        this.activeGeneration = activeGeneration;
    }

    public OffsetDateTime getLastAccessedAt() {
        return lastAccessedAt;
    }

    public void setLastAccessedAt(OffsetDateTime lastAccessedAt) {
        this.lastAccessedAt = lastAccessedAt;
    }

    public String getErrorMessage() {
        return errorMessage;
    }

    public void setErrorMessage(String errorMessage) {
        this.errorMessage = errorMessage;
    }

    public OffsetDateTime getLastFailedAt() {
        return lastFailedAt;
    }

    public void setLastFailedAt(OffsetDateTime lastFailedAt) {
        this.lastFailedAt = lastFailedAt;
    }

    public String getCleanupClaimToken() {
        return cleanupClaimToken;
    }

    public void setCleanupClaimToken(String cleanupClaimToken) {
        this.cleanupClaimToken = cleanupClaimToken;
    }

    public OffsetDateTime getCleanupClaimedAt() {
        return cleanupClaimedAt;
    }

    public void setCleanupClaimedAt(OffsetDateTime cleanupClaimedAt) {
        this.cleanupClaimedAt = cleanupClaimedAt;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(OffsetDateTime createdAt) {
        this.createdAt = createdAt;
    }

    public OffsetDateTime getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(OffsetDateTime updatedAt) {
        this.updatedAt = updatedAt;
    }
}
