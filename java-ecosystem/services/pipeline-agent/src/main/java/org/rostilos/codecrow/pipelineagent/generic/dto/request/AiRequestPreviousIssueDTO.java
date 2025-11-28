package org.rostilos.codecrow.pipelineagent.generic.dto.request;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;

public record AiRequestPreviousIssueDTO(
        String id,
        String type, // security|quality|performance|style
        String severity, // critical|high|medium|low
        String title,
        String suggestedFixDescription,
        String file,
        Integer line,
        String branch,
        String pullRequestId,
        String status, // open|resolved|ignored
        String issueCategory
) {
    public static AiRequestPreviousIssueDTO fromEntity(CodeAnalysisIssue issue) {
        return new AiRequestPreviousIssueDTO(
                String.valueOf(issue.getId()),
                issue.getIssueCategory(),
                issue.getSeverity() != null ? issue.getSeverity().name().toLowerCase() : null,
                issue.getReason(),
                issue.getSuggestedFixDescription(),
                issue.getFilePath(),
                issue.getLineNumber(),
                issue.getAnalysis() == null ? null : issue.getAnalysis().getBranchName(),
                issue.getAnalysis() == null || issue.getAnalysis().getPrNumber() == null ? null : String.valueOf(issue.getAnalysis().getPrNumber()),
                issue.isResolved() ? "resolved" : "open",
                issue.getIssueCategory()
        );
    }
}
