package org.rostilos.codecrow.analysisengine.exception;

/**
 * Exception thrown when a diff exceeds the configured token limit for analysis.
 * This is a soft skip - the analysis is not performed but the job is not marked as failed.
 */
public class DiffTooLargeException extends RuntimeException {

    public enum LimitType {
        TOKENS("estimated tokens"),
        FILES("changed files"),
        FILE_SIZE("bytes in a single file diff"),
        TOTAL_DIFF_SIZE("total diff bytes");

        private final String unit;

        LimitType(String unit) {
            this.unit = unit;
        }

        public String unit() {
            return unit;
        }
    }

    private final long actualValue;
    private final long maxAllowedValue;
    private final Long projectId;
    private final Long pullRequestId;
    private final LimitType limitType;
    private final String filePath;

    public DiffTooLargeException(int estimatedTokens, int maxAllowedTokens, Long projectId, Long pullRequestId) {
        this(LimitType.TOKENS, estimatedTokens, maxAllowedTokens, projectId, pullRequestId, null);
    }

    public DiffTooLargeException(LimitType limitType, long actualValue, long maxAllowedValue,
            Long projectId, Long pullRequestId, String filePath) {
        super(String.format(
            "PR exceeds hard analysis %s limit: actual %d, max allowed %d%s (project=%d, PR=%d)",
            limitType.name().toLowerCase(), actualValue, maxAllowedValue,
            filePath != null ? ", file=" + filePath : "", projectId, pullRequestId
        ));
        this.limitType = limitType;
        this.actualValue = actualValue;
        this.maxAllowedValue = maxAllowedValue;
        this.projectId = projectId;
        this.pullRequestId = pullRequestId;
        this.filePath = filePath;
    }

    public int getEstimatedTokens() {
        return (int) Math.min(actualValue, Integer.MAX_VALUE);
    }

    public int getMaxAllowedTokens() {
        return (int) Math.min(maxAllowedValue, Integer.MAX_VALUE);
    }

    public long getActualValue() { return actualValue; }
    public long getMaxAllowedValue() { return maxAllowedValue; }
    public LimitType getLimitType() { return limitType; }
    public String getFilePath() { return filePath; }
    public String getUnit() { return limitType.unit(); }

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
        return maxAllowedValue > 0 ? (actualValue * 100.0 / maxAllowedValue) : 0;
    }
}
