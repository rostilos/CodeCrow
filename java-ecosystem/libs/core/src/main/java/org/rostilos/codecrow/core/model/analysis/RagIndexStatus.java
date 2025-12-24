package org.rostilos.codecrow.core.model.analysis;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

@Entity
@Table(name = "rag_index_status",
    uniqueConstraints = {
        @UniqueConstraint(name = "uq_rag_index_project", columnNames = {"project_id"})
    },
    indexes = {
        @Index(name = "idx_rag_status", columnList = "status"),
        @Index(name = "idx_rag_workspace_project", columnList = "workspace_name, project_name")
    }
)
public class RagIndexStatus {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @OneToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false, unique = true)
    private Project project;

    @Column(name = "workspace_name", nullable = false, length = 200)
    private String workspaceName;

    @Column(name = "project_name", nullable = false, length = 200)
    private String projectName;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 50)
    private RagIndexingStatus status;

    @Column(name = "indexed_branch", length = 200)
    private String indexedBranch;

    @Column(name = "indexed_commit_hash", length = 40)
    private String indexedCommitHash;

    @Column(name = "total_files_indexed")
    private Integer totalFilesIndexed;

    @Column(name = "last_indexed_at")
    private OffsetDateTime lastIndexedAt;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @Column(name = "error_message", columnDefinition = "TEXT")
    private String errorMessage;

    @Column(name = "collection_name", length = 300)
    private String collectionName;

    @Column(name = "failed_incremental_count", nullable = false)
    private Integer failedIncrementalCount = 0;

    @PreUpdate
    protected void onUpdate() {
        updatedAt = OffsetDateTime.now();
    }

    public RagIndexStatus() {
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

    public String getWorkspaceName() {
        return workspaceName;
    }

    public void setWorkspaceName(String workspaceName) {
        this.workspaceName = workspaceName;
    }

    public String getProjectName() {
        return projectName;
    }

    public void setProjectName(String projectName) {
        this.projectName = projectName;
    }

    public RagIndexingStatus getStatus() {
        return status;
    }

    public void setStatus(RagIndexingStatus status) {
        this.status = status;
    }

    public String getIndexedBranch() {
        return indexedBranch;
    }

    public void setIndexedBranch(String indexedBranch) {
        this.indexedBranch = indexedBranch;
    }

    public String getIndexedCommitHash() {
        return indexedCommitHash;
    }

    public void setIndexedCommitHash(String indexedCommitHash) {
        this.indexedCommitHash = indexedCommitHash;
    }

    public Integer getTotalFilesIndexed() {
        return totalFilesIndexed;
    }

    public void setTotalFilesIndexed(Integer totalFilesIndexed) {
        this.totalFilesIndexed = totalFilesIndexed;
    }

    public OffsetDateTime getLastIndexedAt() {
        return lastIndexedAt;
    }

    public void setLastIndexedAt(OffsetDateTime lastIndexedAt) {
        this.lastIndexedAt = lastIndexedAt;
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

    public String getErrorMessage() {
        return errorMessage;
    }

    public void setErrorMessage(String errorMessage) {
        this.errorMessage = errorMessage;
    }

    public String getCollectionName() {
        return collectionName;
    }

    public void setCollectionName(String collectionName) {
        this.collectionName = collectionName;
    }

    public Integer getFailedIncrementalCount() {
        return failedIncrementalCount != null ? failedIncrementalCount : 0;
    }

    public void setFailedIncrementalCount(Integer failedIncrementalCount) {
        this.failedIncrementalCount = failedIncrementalCount;
    }

    public void incrementFailedIncrementalCount() {
        this.failedIncrementalCount = (this.failedIncrementalCount != null ? this.failedIncrementalCount : 0) + 1;
    }

    public void resetFailedIncrementalCount() {
        this.failedIncrementalCount = 0;
    }
}

