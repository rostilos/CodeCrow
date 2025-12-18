package org.rostilos.codecrow.core.model.codeanalysis;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

/**
 * Stores cached summarize results for PR comments.
 * Keyed by project, commit hash, and PR number to enable result reuse.
 */
@Entity
@Table(name = "pr_summarize_cache",
        uniqueConstraints = {
                @UniqueConstraint(
                        name = "uq_summarize_cache_project_commit_pr",
                        columnNames = {"project_id", "commit_hash", "pr_number"}
                )
        },
        indexes = {
                @Index(name = "idx_summarize_cache_project", columnList = "project_id"),
                @Index(name = "idx_summarize_cache_created", columnList = "created_at")
        })
public class PrSummarizeCache {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "project_id", nullable = false)
    private Project project;

    @Column(name = "commit_hash", nullable = false, length = 40)
    private String commitHash;

    @Column(name = "pr_number", nullable = false)
    private Long prNumber;

    @Column(name = "summary_content", columnDefinition = "TEXT", nullable = false)
    private String summaryContent;

    @Column(name = "diagram_content", columnDefinition = "TEXT")
    private String diagramContent;

    @Column(name = "diagram_type", length = 20)
    @Enumerated(EnumType.STRING)
    private DiagramType diagramType;

    @Column(name = "rag_context_used", nullable = false)
    private boolean ragContextUsed = false;

    @Column(name = "source_branch_name", length = 255)
    private String sourceBranchName;

    @Column(name = "target_branch_name", length = 255)
    private String targetBranchName;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "expires_at")
    private OffsetDateTime expiresAt;

    public enum DiagramType {
        MERMAID,
        ASCII,
        NONE
    }

    public PrSummarizeCache() {
    }

    public Long getId() {
        return id;
    }

    public Project getProject() {
        return project;
    }

    public void setProject(Project project) {
        this.project = project;
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

    public String getSummaryContent() {
        return summaryContent;
    }

    public void setSummaryContent(String summaryContent) {
        this.summaryContent = summaryContent;
    }

    public String getDiagramContent() {
        return diagramContent;
    }

    public void setDiagramContent(String diagramContent) {
        this.diagramContent = diagramContent;
    }

    public DiagramType getDiagramType() {
        return diagramType;
    }

    public void setDiagramType(DiagramType diagramType) {
        this.diagramType = diagramType;
    }

    public boolean isRagContextUsed() {
        return ragContextUsed;
    }

    public void setRagContextUsed(boolean ragContextUsed) {
        this.ragContextUsed = ragContextUsed;
    }

    public String getSourceBranchName() {
        return sourceBranchName;
    }

    public void setSourceBranchName(String sourceBranchName) {
        this.sourceBranchName = sourceBranchName;
    }

    public String getTargetBranchName() {
        return targetBranchName;
    }

    public void setTargetBranchName(String targetBranchName) {
        this.targetBranchName = targetBranchName;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public OffsetDateTime getExpiresAt() {
        return expiresAt;
    }

    public void setExpiresAt(OffsetDateTime expiresAt) {
        this.expiresAt = expiresAt;
    }

    /**
     * Check if this cache entry has expired.
     */
    public boolean isExpired() {
        return expiresAt != null && OffsetDateTime.now().isAfter(expiresAt);
    }

    /**
     * Get the full formatted content for posting as a comment.
     * Includes both summary and diagram if present.
     */
    public String getFormattedContent() {
        StringBuilder content = new StringBuilder();
        content.append(summaryContent);
        
        if (diagramContent != null && !diagramContent.isBlank()) {
            content.append("\n\n");
            if (diagramType == DiagramType.MERMAID) {
                content.append("```mermaid\n");
                content.append(diagramContent);
                content.append("\n```");
            } else {
                content.append("```\n");
                content.append(diagramContent);
                content.append("\n```");
            }
        }
        
        return content.toString();
    }
}
