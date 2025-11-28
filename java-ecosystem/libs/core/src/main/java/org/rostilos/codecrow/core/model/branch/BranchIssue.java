package org.rostilos.codecrow.core.model.branch;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;

import java.time.OffsetDateTime;

@Entity
@Table(name = "branch_issue", uniqueConstraints = {
        @UniqueConstraint(name = "uq_branch_issue_code_analysis_issue", columnNames = {"branch_id", "code_analysis_issue_id"})
})
public class BranchIssue {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "branch_id", nullable = false)
    private Branch branch;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "code_analysis_issue_id", nullable = false)
    private CodeAnalysisIssue codeAnalysisIssue;

    @Column(name = "severity", nullable = false, length = 10)
    @Enumerated(EnumType.STRING)
    private IssueSeverity severity;

    @Column(name = "is_resolved", nullable = false)
    private boolean resolved = false;

    @Column(name = "first_detected_pr_number")
    private Long firstDetectedPrNumber;

    @Column(name = "resolved_in_pr_number")
    private Long resolvedInPrNumber;

    @Column(name = "resolved_in_commit_hash", length = 40)
    private String resolvedInCommitHash;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @PreUpdate
    public void onUpdate() {
        this.updatedAt = OffsetDateTime.now();
    }

    public Long getId() { return id; }

    public Branch getBranch() { return branch; }
    public void setBranch(Branch branch) { this.branch = branch; }

    public CodeAnalysisIssue getCodeAnalysisIssue() { return codeAnalysisIssue; }
    public void setCodeAnalysisIssue(CodeAnalysisIssue codeAnalysisIssue) { this.codeAnalysisIssue = codeAnalysisIssue; }

    public IssueSeverity getSeverity() { return severity; }
    public void setSeverity(IssueSeverity severity) { this.severity = severity; }

    public boolean isResolved() { return resolved; }
    public void setResolved(boolean resolved) { this.resolved = resolved; }

    public Long getFirstDetectedPrNumber() { return firstDetectedPrNumber; }
    public void setFirstDetectedPrNumber(Long firstDetectedPrNumber) { this.firstDetectedPrNumber = firstDetectedPrNumber; }

    public Long getResolvedInPrNumber() { return resolvedInPrNumber; }
    public void setResolvedInPrNumber(Long resolvedInPrNumber) { this.resolvedInPrNumber = resolvedInPrNumber; }

    public String getResolvedInCommitHash() { return resolvedInCommitHash; }
    public void setResolvedInCommitHash(String resolvedInCommitHash) { this.resolvedInCommitHash = resolvedInCommitHash; }

    public OffsetDateTime getCreatedAt() { return createdAt; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }
}
