package org.rostilos.codecrow.analysisengine.dto.request.ai;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;

public record AiRequestPreviousIssueDTO(
        String id,
        String type, // security|quality|performance|style
        String severity, // critical|high|medium|low|info
        String reason,
        String suggestedFixDescription,
        String suggestedFixDiff,
        String file,
        Integer line,
        String branch,
        String pullRequestId,
        String status, // open|resolved|ignored
        String category
) {
    public static AiRequestPreviousIssueDTO fromEntity(CodeAnalysisIssue issue) {
        String categoryStr = issue.getIssueCategory() != null 
            ? issue.getIssueCategory().name() 
            : IssueCategory.CODE_QUALITY.name();
        return new AiRequestPreviousIssueDTO(
                String.valueOf(issue.getId()),
                categoryStr,
                issue.getSeverity() != null ? issue.getSeverity().name() : null,
                issue.getReason(),
                issue.getSuggestedFixDescription(),
                issue.getSuggestedFixDiff(),
                issue.getFilePath(),
                issue.getLineNumber(),
                issue.getAnalysis() == null ? null : issue.getAnalysis().getBranchName(),
                issue.getAnalysis() == null || issue.getAnalysis().getPrNumber() == null ? null : String.valueOf(issue.getAnalysis().getPrNumber()),
                issue.isResolved() ? "resolved" : "open",
                categoryStr
        );
    }
}
