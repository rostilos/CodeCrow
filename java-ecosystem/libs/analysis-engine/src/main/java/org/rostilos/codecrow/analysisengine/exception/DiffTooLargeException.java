package org.rostilos.codecrow.analysisengine.exception;

/**
 * Exception thrown when a diff exceeds the configured token limit for analysis.
 * This is a soft skip - the analysis is not performed but the job is not marked as failed.
 */
public class DiffTooLargeException extends RuntimeException {

    private final int estimatedTokens;
    private final int maxAllowedTokens;
    private final Long projectId;
    private final Long pullRequestId;

    public DiffTooLargeException(int estimatedTokens, int maxAllowedTokens, Long projectId, Long pullRequestId) {
        super(String.format(
            "PR diff exceeds token limit: estimated %d tokens, max allowed %d tokens (project=%d, PR=%d)",
            estimatedTokens, maxAllowedTokens, projectId, pullRequestId
        ));
        this.estimatedTokens = estimatedTokens;
        this.maxAllowedTokens = maxAllowedTokens;
        this.projectId = projectId;
        this.pullRequestId = pullRequestId;
    }

    public int getEstimatedTokens() {
        return estimatedTokens;
    }

    public int getMaxAllowedTokens() {
        return maxAllowedTokens;
    }

    public Long getProjectId() {
        return projectId;
    }

    public Long getPullRequestId() {
        return pullRequestId;
    }
    
    /**
     * Returns the percentage of the token limit that would be used.
     */
    public double getUtilizationPercentage() {
        return maxAllowedTokens > 0 ? (estimatedTokens * 100.0 / maxAllowedTokens) : 0;
    }
}
