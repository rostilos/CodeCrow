package org.rostilos.codecrow.core.model.branch;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.util.tracking.TrackingConfidence;

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

    @Column(name = "resolved_description", columnDefinition = "TEXT")
    private String resolvedDescription;

    @Column(name = "resolved_at")
    private OffsetDateTime resolvedAt;

    @Column(name = "resolved_by", length = 100)
    private String resolvedBy;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    // --- Content-based tracking fields ---

    /** Current line number on the branch (may drift from original detection line). */
    @Column(name = "current_line_number")
    private Integer currentLineNumber;

    /** MD5 hex of the current source line content on the branch. */
    @Column(name = "current_line_hash", length = 32)
    private String currentLineHash;

    /** Commit SHA at which this issue's position was last verified. */
    @Column(name = "last_verified_commit", length = 40)
    private String lastVerifiedCommit;

    /** Confidence level of the last tracking match. */
    @Column(name = "tracking_confidence", length = 10)
    @Enumerated(EnumType.STRING)
    private TrackingConfidence trackingConfidence;

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

    public String getResolvedDescription() { return resolvedDescription; }
    public void setResolvedDescription(String resolvedDescription) { this.resolvedDescription = resolvedDescription; }

    public OffsetDateTime getResolvedAt() { return resolvedAt; }
    public void setResolvedAt(OffsetDateTime resolvedAt) { this.resolvedAt = resolvedAt; }

    public String getResolvedBy() { return resolvedBy; }
    public void setResolvedBy(String resolvedBy) { this.resolvedBy = resolvedBy; }

    public OffsetDateTime getCreatedAt() { return createdAt; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }

    public Integer getCurrentLineNumber() { return currentLineNumber; }
    public void setCurrentLineNumber(Integer currentLineNumber) { this.currentLineNumber = currentLineNumber; }

    public String getCurrentLineHash() { return currentLineHash; }
    public void setCurrentLineHash(String currentLineHash) { this.currentLineHash = currentLineHash; }

    public String getLastVerifiedCommit() { return lastVerifiedCommit; }
    public void setLastVerifiedCommit(String lastVerifiedCommit) { this.lastVerifiedCommit = lastVerifiedCommit; }

    public TrackingConfidence getTrackingConfidence() { return trackingConfidence; }
    public void setTrackingConfidence(TrackingConfidence trackingConfidence) { this.trackingConfidence = trackingConfidence; }
}
