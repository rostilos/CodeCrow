package org.rostilos.codecrow.core.model.rag;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;
import java.util.HashSet;
import java.util.Set;

/**
 * Entity tracking RAG index state for a specific branch within a project.
 * 
 * With single-collection-per-project architecture, all branches share one Qdrant collection.
 * This entity tracks:
 * - Which commit is indexed for each branch
 * - Deleted files that should be excluded from queries
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

    /**
     * The commit hash that is currently indexed for this branch.
     */
    @Column(name = "commit_hash", length = 64)
    private String commitHash;

    /**
     * Files that were deleted in this branch (for query-time filtering).
     * These files should be excluded when querying the branch's context.
     */
    @ElementCollection(fetch = FetchType.EAGER)
    @CollectionTable(
        name = "rag_branch_deleted_files",
        joinColumns = @JoinColumn(name = "branch_index_id")
    )
    @Column(name = "file_path", length = 512)
    private Set<String> deletedFiles = new HashSet<>();

    @Column(name = "chunk_count")
    private Integer chunkCount;

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

    public RagBranchIndex(Project project, String branchName) {
        this.project = project;
        this.branchName = branchName;
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

    public String getCommitHash() {
        return commitHash;
    }

    public void setCommitHash(String commitHash) {
        this.commitHash = commitHash;
    }

    public Set<String> getDeletedFiles() {
        return deletedFiles;
    }

    public void setDeletedFiles(Set<String> deletedFiles) {
        this.deletedFiles = deletedFiles;
    }

    public Integer getChunkCount() {
        return chunkCount;
    }

    public void setChunkCount(Integer chunkCount) {
        this.chunkCount = chunkCount;
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
