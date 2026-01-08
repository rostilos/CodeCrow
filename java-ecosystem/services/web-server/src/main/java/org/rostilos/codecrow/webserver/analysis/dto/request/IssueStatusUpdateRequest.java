package org.rostilos.codecrow.webserver.analysis.dto.request;

/**
 * Request to update issue status with optional resolution context.
 * 
 * @param isResolved Whether the issue is resolved
 * @param comment Optional comment/description for the status change
 * @param resolvedByPr Optional PR number that resolved the issue (for context when manually resolving from PR view)
 * @param resolvedCommitHash Optional commit hash that resolved the issue
 */
public record IssueStatusUpdateRequest(
        boolean isResolved,
        String comment,
        Long resolvedByPr,
        String resolvedCommitHash
) {
    // Constructor for backward compatibility (isResolved + comment only)
    public IssueStatusUpdateRequest(boolean isResolved, String comment) {
        this(isResolved, comment, null, null);
    }
}
