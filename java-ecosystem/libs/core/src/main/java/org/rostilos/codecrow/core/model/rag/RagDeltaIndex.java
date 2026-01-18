package org.rostilos.codecrow.core.model.rag;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

/**
 * Entity representing a RAG delta index for a specific branch.
 * 
 * Delta indexes store only the differences between a branch (e.g., release/1.0) 
 * and the base branch (e.g., master), enabling efficient hybrid RAG queries
 * that combine base context with branch-specific changes.
 */
@Entity
@Table(name = "rag_delta_index",
    uniqueConstraints = {
        @UniqueConstraint(columnNames = {"project_id", "branch_name"})
    },
    indexes = {
        @Index(name = "idx_rag_delta_project", columnList = "project_id"),
        @Index(name = "idx_rag_delta_status", columnList = "status"),
        @Index(name = "idx_rag_delta_branch", columnList = "branch_name")
    }
)
public class RagDeltaIndex {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;

    /**
     * The branch this delta index is for (e.g., "release/1.0", "release/2.0").
     */
    @Column(name = "branch_name", nullable = false, length = 256)
    private String branchName;

    /**
     * The base branch this delta is computed against (e.g., "master", "main").
     */
    @Column(name = "base_branch", nullable = false, length = 256)
    private String baseBranch;

    @Column(name = "base_commit_hash", length = 64)
    private String baseCommitHash;

    @Column(name = "delta_commit_hash", length = 64)
    private String deltaCommitHash;

    @Column(name = "collection_name", nullable = false, length = 256)
    private String collectionName;

    @Column(name = "chunk_count")
    private Integer chunkCount;

    @Column(name = "file_count")
    private Integer fileCount;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 32)
    private DeltaIndexStatus status = DeltaIndexStatus.CREATING;

    @Column(name = "error_message", length = 1000)
    private String errorMessage;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @Column(name = "last_accessed_at")
    private OffsetDateTime lastAccessedAt;

    @PreUpdate
    protected void onUpdate() {
        this.updatedAt = OffsetDateTime.now();
    }

    public RagDeltaIndex() {
    }

    public RagDeltaIndex(Project project, String branchName, String baseBranch, String collectionName) {
        this.project = project;
        this.branchName = branchName;
        this.baseBranch = baseBranch;
        this.collectionName = collectionName;
        this.status = DeltaIndexStatus.CREATING;
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

    public String getBaseBranch() {
        return baseBranch;
    }

    public void setBaseBranch(String baseBranch) {
        this.baseBranch = baseBranch;
    }

    public String getBaseCommitHash() {
        return baseCommitHash;
    }

    public void setBaseCommitHash(String baseCommitHash) {
        this.baseCommitHash = baseCommitHash;
    }

    public String getDeltaCommitHash() {
        return deltaCommitHash;
    }

    public void setDeltaCommitHash(String deltaCommitHash) {
        this.deltaCommitHash = deltaCommitHash;
    }

    public String getCollectionName() {
        return collectionName;
    }

    public void setCollectionName(String collectionName) {
        this.collectionName = collectionName;
    }

    public Integer getChunkCount() {
        return chunkCount;
    }

    public void setChunkCount(Integer chunkCount) {
        this.chunkCount = chunkCount;
    }

    public Integer getFileCount() {
        return fileCount;
    }

    public void setFileCount(Integer fileCount) {
        this.fileCount = fileCount;
    }

    public DeltaIndexStatus getStatus() {
        return status;
    }

    public void setStatus(DeltaIndexStatus status) {
        this.status = status;
    }

    public String getErrorMessage() {
        return errorMessage;
    }

    public void setErrorMessage(String errorMessage) {
        this.errorMessage = errorMessage;
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

    public OffsetDateTime getLastAccessedAt() {
        return lastAccessedAt;
    }

    public void setLastAccessedAt(OffsetDateTime lastAccessedAt) {
        this.lastAccessedAt = lastAccessedAt;
    }

    public void markAccessed() {
        this.lastAccessedAt = OffsetDateTime.now();
    }

    public boolean isReady() {
        return status == DeltaIndexStatus.READY;
    }

    public boolean needsRebuild() {
        return status == DeltaIndexStatus.STALE || status == DeltaIndexStatus.FAILED;
    }

    @Override
    public String toString() {
        return "RagDeltaIndex{" +
                "id=" + id +
                ", branchName='" + branchName + '\'' +
                ", baseBranch='" + baseBranch + '\'' +
                ", status=" + status +
                ", chunkCount=" + chunkCount +
                '}';
    }
}
