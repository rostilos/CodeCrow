package org.rostilos.codecrow.core.model.codeanalysis;

import jakarta.persistence.*;

import java.time.OffsetDateTime;

@Entity
@Table(name = "code_analysis_issue")
public class CodeAnalysisIssue {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(nullable = false, updatable = false)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "analysis_id", nullable = false)
    private CodeAnalysis analysis;

    @Column(name = "severity", nullable = false, length = 10)
    @Enumerated(EnumType.STRING)
    private IssueSeverity severity;

    @Column(name = "file_path", nullable = false, length = 500)
    private String filePath;

    @Column(name = "line_number")
    private Integer lineNumber;

    @Column(name = "reason", columnDefinition = "TEXT")
    private String reason;

    @Column(name = "suggested_fix_description", columnDefinition = "TEXT")
    private String suggestedFixDescription;

    @Column(name = "suggested_fix_diff", columnDefinition = "TEXT")
    private String suggestedFixDiff;

    @Column(name = "issue_category", length = 50)
    @Enumerated(EnumType.STRING)
    private IssueCategory issueCategory;

    @Column(name = "is_resolved", nullable = false)
    private boolean resolved;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    public Long getId() { return id; }

    public CodeAnalysis getAnalysis() { return analysis; }
    public void setAnalysis(CodeAnalysis analysis) { this.analysis = analysis; }

    public IssueSeverity getSeverity() { return severity; }
    public void setSeverity(IssueSeverity severity) { this.severity = severity; }

    public String getFilePath() { return filePath; }
    public void setFilePath(String filePath) { this.filePath = filePath; }

    public Integer getLineNumber() { return lineNumber; }
    public void setLineNumber(Integer lineNumber) { this.lineNumber = lineNumber; }

    public String getReason() { return reason; }
    public void setReason(String reason) { this.reason = reason; }

    public String getSuggestedFixDescription() { return suggestedFixDescription; }
    public void setSuggestedFixDescription(String suggestedFixDescription) { this.suggestedFixDescription = suggestedFixDescription; }

    public String getSuggestedFixDiff() { return suggestedFixDiff; }
    public void setSuggestedFixDiff(String suggestedFixDiff) { this.suggestedFixDiff = suggestedFixDiff; }

    public IssueCategory getIssueCategory() { return issueCategory; }
    public void setIssueCategory(IssueCategory issueCategory) { this.issueCategory = issueCategory; }

    public boolean isResolved() { return resolved; }
    public void setResolved(boolean resolved) { this.resolved = resolved; }

    public OffsetDateTime getCreatedAt() { return createdAt; }
}
