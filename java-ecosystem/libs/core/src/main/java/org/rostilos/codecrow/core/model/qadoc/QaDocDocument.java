package org.rostilos.codecrow.core.model.qadoc;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

/**
 * Stores the latest generated QA documentation for a project pull request.
 * <p>
 * The unique {@code (project_id, pr_number)} constraint ensures the database
 * keeps only the latest rendered markdown document for each PR.
 */
@Entity
@Table(
        name = "qa_doc_document",
        uniqueConstraints = {
                @UniqueConstraint(
                        name = "uq_qa_doc_document_project_pr",
                        columnNames = {"project_id", "pr_number"}
                )
        },
        indexes = {
                @Index(name = "idx_qa_doc_document_project", columnList = "project_id"),
                @Index(name = "idx_qa_doc_document_project_pr", columnList = "project_id, pr_number")
        }
)
public class QaDocDocument {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;

    @Column(name = "pr_number", nullable = false)
    private Long prNumber;

    @Column(name = "task_id", length = 128)
    private String taskId;

    @Column(name = "last_analysis_id")
    private Long lastAnalysisId;

    @Column(name = "commit_hash", length = 40)
    private String commitHash;

    @Column(name = "markdown_content", nullable = false, columnDefinition = "TEXT")
    private String markdownContent;

    @Column(name = "generated_at", nullable = false)
    private OffsetDateTime generatedAt = OffsetDateTime.now();

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    public QaDocDocument() {
    }

    public QaDocDocument(Project project, Long prNumber) {
        this.project = project;
        this.prNumber = prNumber;
    }

    @PreUpdate
    public void onUpdate() {
        this.updatedAt = OffsetDateTime.now();
    }

    public void replaceContent(String taskId, Long analysisId, String commitHash, String markdownContent) {
        OffsetDateTime now = OffsetDateTime.now();
        this.taskId = taskId;
        this.lastAnalysisId = analysisId;
        this.commitHash = commitHash;
        this.markdownContent = markdownContent;
        this.generatedAt = now;
        this.updatedAt = now;
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public Project getProject() { return project; }
    public void setProject(Project project) { this.project = project; }

    public Long getPrNumber() { return prNumber; }
    public void setPrNumber(Long prNumber) { this.prNumber = prNumber; }

    public String getTaskId() { return taskId; }
    public void setTaskId(String taskId) { this.taskId = taskId; }

    public Long getLastAnalysisId() { return lastAnalysisId; }
    public void setLastAnalysisId(Long lastAnalysisId) { this.lastAnalysisId = lastAnalysisId; }

    public String getCommitHash() { return commitHash; }
    public void setCommitHash(String commitHash) { this.commitHash = commitHash; }

    public String getMarkdownContent() { return markdownContent; }
    public void setMarkdownContent(String markdownContent) { this.markdownContent = markdownContent; }

    public OffsetDateTime getGeneratedAt() { return generatedAt; }
    public void setGeneratedAt(OffsetDateTime generatedAt) { this.generatedAt = generatedAt; }

    public OffsetDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(OffsetDateTime createdAt) { this.createdAt = createdAt; }

    public OffsetDateTime getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(OffsetDateTime updatedAt) { this.updatedAt = updatedAt; }
}
