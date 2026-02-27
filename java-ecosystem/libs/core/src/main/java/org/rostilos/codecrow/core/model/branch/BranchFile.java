package org.rostilos.codecrow.core.model.branch;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

@Entity
@Table(name = "branch_file", uniqueConstraints = {
        @UniqueConstraint(name = "uq_branch_file_project_branch_path", columnNames = {"project_id", "branch_name", "file_path"})
})
public class BranchFile {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;

    @Column(name = "branch_name", nullable = false, length = 200)
    private String branchName;

    @Column(name = "file_path", nullable = false, length = 500)
    private String filePath;

    @Column(name = "issue_count", nullable = false)
    private int issueCount = 0;

    // --- Content tracking fields ---

    /** SHA-1 hex of the full file content at last analysis. */
    @Column(name = "content_hash", length = 40)
    private String contentHash;

    /** Number of lines in the file at last analysis. */
    @Column(name = "line_count")
    private Integer lineCount;

    /** Commit SHA at which this file was last analyzed. */
    @Column(name = "last_analyzed_commit", length = 40)
    private String lastAnalyzedCommit;

    /** FK to the branch entity (supplements the denormalized branchName). */
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "branch_id")
    private Branch branch;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @PreUpdate
    public void onUpdate() {
        this.updatedAt = OffsetDateTime.now();
    }

    public Long getId() { return id; }

    public Project getProject() { return project; }
    public void setProject(Project project) { this.project = project; }

    public String getBranchName() { return branchName; }
    public void setBranchName(String branchName) { this.branchName = branchName; }

    public String getFilePath() { return filePath; }
    public void setFilePath(String filePath) { this.filePath = filePath; }

    public int getIssueCount() { return issueCount; }
    public void setIssueCount(int issueCount) { this.issueCount = issueCount; }

    public OffsetDateTime getCreatedAt() { return createdAt; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }

    public String getContentHash() { return contentHash; }
    public void setContentHash(String contentHash) { this.contentHash = contentHash; }

    public Integer getLineCount() { return lineCount; }
    public void setLineCount(Integer lineCount) { this.lineCount = lineCount; }

    public String getLastAnalyzedCommit() { return lastAnalyzedCommit; }
    public void setLastAnalyzedCommit(String lastAnalyzedCommit) { this.lastAnalyzedCommit = lastAnalyzedCommit; }

    public Branch getBranch() { return branch; }
    public void setBranch(Branch branch) { this.branch = branch; }
}
