package org.rostilos.codecrow.webserver.analysis.dto.response;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisResult;

/**
 * Response DTO for issue status update containing the updated analysis result.
 */
public record IssueStatusUpdateResponse(
        boolean success,
        Long issueId,
        String newStatus,
        Long analysisId,
        AnalysisResult analysisResult,
        int totalIssues,
        int highSeverityCount,
        int mediumSeverityCount,
        int lowSeverityCount,
        int infoSeverityCount,
        int resolvedCount,
        String errorMessage
) {
    public static IssueStatusUpdateResponse success(
            Long issueId,
            boolean isResolved,
            Long analysisId,
            AnalysisResult analysisResult,
            int totalIssues,
            int highSeverityCount,
            int mediumSeverityCount,
            int lowSeverityCount,
            int infoSeverityCount,
            int resolvedCount
    ) {
        return new IssueStatusUpdateResponse(
                true,
                issueId,
                isResolved ? "resolved" : "open",
                analysisId,
                analysisResult,
                totalIssues,
                highSeverityCount,
                mediumSeverityCount,
                lowSeverityCount,
                infoSeverityCount,
                resolvedCount,
                null
        );
    }

    public static IssueStatusUpdateResponse failure(Long issueId, String errorMessage) {
        return new IssueStatusUpdateResponse(
                false,
                issueId,
                null,
                null,
                null,
                0, 0, 0, 0, 0, 0,
                errorMessage
        );
    }
}
