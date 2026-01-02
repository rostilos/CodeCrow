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
    String issueCategory,
    // Detection info - where was this issue first found
    Long analysisId,
    Long prNumber,
    String commitHash,
    OffsetDateTime detectedAt
) {
    public static IssueDTO fromEntity(CodeAnalysisIssue issue) {
        String categoryStr = issue.getIssueCategory() != null 
            ? issue.getIssueCategory().name() 
            : IssueCategory.CODE_QUALITY.name();
        
        var analysis = issue.getAnalysis();
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
                analysis == null ? null : analysis.getBranchName(),
                analysis == null || analysis.getPrNumber() == null ? null : String.valueOf(analysis.getPrNumber()),
                issue.isResolved() ? "resolved" : "open",
                issue.getCreatedAt(),
                categoryStr,
                // Detection info
                analysis != null ? analysis.getId() : null,
                analysis != null ? analysis.getPrNumber() : null,
                analysis != null ? analysis.getCommitHash() : null,
                issue.getCreatedAt()
        );
    }
}
