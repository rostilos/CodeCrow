package org.rostilos.codecrow.core.dto.analysis.issue;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;

import java.time.OffsetDateTime;

public record IssueDTO (
    String id,
    String type, // security|quality|performance|style
    String severity, // critical|high|medium|low
    String title,
    String suggestedFixDescription,
    String suggestedFixDiff,
    String file,
    Integer line,
    Integer column,
    String rule,
    String branch,
    String pullRequestId,
    String status, // open|resolved|ignored
    OffsetDateTime createdAt,
    String issueCategory
) {
    public static IssueDTO fromEntity(CodeAnalysisIssue issue) {
        String categoryStr = issue.getIssueCategory() != null 
            ? issue.getIssueCategory().name() 
            : IssueCategory.CODE_QUALITY.name();
        return new IssueDTO(
                String.valueOf(issue.getId()),
                categoryStr,
                issue.getSeverity() != null ? issue.getSeverity().name().toLowerCase() : null,
                issue.getReason(),
                issue.getSuggestedFixDescription(),
                issue.getSuggestedFixDiff(),
                issue.getFilePath(),
                issue.getLineNumber(),
                null,
                null,
                issue.getAnalysis() == null ? null : issue.getAnalysis().getBranchName(),
                issue.getAnalysis() == null || issue.getAnalysis().getPrNumber() == null ? null : String.valueOf(issue.getAnalysis().getPrNumber()),
                issue.isResolved() ? "resolved" : "open",
                issue.getCreatedAt(),
                categoryStr
        );
    }
}
