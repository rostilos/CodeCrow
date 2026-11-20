package org.rostilos.codecrow.core.model.codeanalysis;

import jakarta.persistence.*;

/** Common finding data only; each concrete entity owns its persistence and lifecycle. */
@MappedSuperclass
public abstract class AnalysisFinding {
    @Column(name = "severity", nullable = false, length = 10)
    @Enumerated(EnumType.STRING)
    protected IssueSeverity severity;

    @Column(name = "file_path", nullable = false, length = 500)
    protected String filePath;

    @Column(name = "line_number")
    protected Integer lineNumber;

    @Column(name = "reason", columnDefinition = "TEXT")
    protected String reason;

    @Column(name = "title", length = 255)
    protected String title;

    @Column(name = "suggested_fix_description", columnDefinition = "TEXT")
    protected String suggestedFixDescription;

    @Column(name = "issue_category", length = 50)
    @Enumerated(EnumType.STRING)
    protected IssueCategory issueCategory;

    @Column(name = "is_resolved", nullable = false)
    protected boolean resolved;

    @Column(name = "code_snippet", columnDefinition = "TEXT")
    protected String codeSnippet;

    public IssueSeverity getSeverity() { return severity; }
    public void setSeverity(IssueSeverity severity) { this.severity = severity; }
    public String getFilePath() { return filePath; }
    public void setFilePath(String filePath) { this.filePath = filePath; }
    public Integer getLineNumber() { return lineNumber; }
    public void setLineNumber(Integer lineNumber) { this.lineNumber = lineNumber; }
    public String getReason() { return reason; }
    public void setReason(String reason) { this.reason = reason; }
    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }
    public String getSuggestedFixDescription() { return suggestedFixDescription; }
    public void setSuggestedFixDescription(String suggestedFixDescription) { this.suggestedFixDescription = suggestedFixDescription; }
    public IssueCategory getIssueCategory() { return issueCategory; }
    public void setIssueCategory(IssueCategory issueCategory) { this.issueCategory = issueCategory; }
    public boolean isResolved() { return resolved; }
    public void setResolved(boolean resolved) { this.resolved = resolved; }
    public String getCodeSnippet() { return codeSnippet; }
    public void setCodeSnippet(String codeSnippet) { this.codeSnippet = codeSnippet; }
}
