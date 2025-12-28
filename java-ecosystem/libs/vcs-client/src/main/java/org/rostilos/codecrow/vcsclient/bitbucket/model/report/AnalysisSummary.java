package org.rostilos.codecrow.vcsclient.bitbucket.model.report;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.formatters.AnalysisFormatter;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

public final class AnalysisSummary {

    private final String projectNamespace;
    private final Long pullRequestId;
    private final String comment;
    private final String platformAnalysisUrl;
    private final String pullRequestUrl;
    private final OffsetDateTime analysisDate;

    private final SeverityMetric highSeverityIssues;
    private final SeverityMetric mediumSeverityIssues;
    private final SeverityMetric lowSeverityIssues;
    private final SeverityMetric resolvedIssues;
    private final int totalIssues;
    private final int totalUnresolvedIssues;

    private final List<IssueSummary> issues;
    private final Map<String, Integer> fileIssueCount;

    private AnalysisSummary(Builder builder) {
        this.projectNamespace = builder.projectNamespace;
        this.pullRequestId = builder.pullRequestId;
        this.comment = builder.comment;
        this.platformAnalysisUrl = builder.platformAnalysisUrl;
        this.pullRequestUrl = builder.pullRequestUrl;
        this.analysisDate = builder.analysisDate;
        this.highSeverityIssues = builder.highSeverityIssues;
        this.mediumSeverityIssues = builder.mediumSeverityIssues;
        this.lowSeverityIssues = builder.lowSeverityIssues;
        this.totalIssues = builder.totalIssues;
        this.totalUnresolvedIssues = builder.totalUnresolvedIssues;
        this.issues = builder.issues;
        this.fileIssueCount = builder.fileIssueCount;
        this.resolvedIssues = builder.resolvedIssues;
    }

    public String getProjectNamespace() {
        return projectNamespace;
    }

    public Long getPullRequestId() {
        return pullRequestId;
    }

    public String getComment() {
        return comment;
    }

    public String getPlatformAnalysisUrl() {
        return platformAnalysisUrl;
    }

    public String getPullRequestUrl() {
        return pullRequestUrl;
    }

    public OffsetDateTime getAnalysisDate() {
        return analysisDate;
    }

    public SeverityMetric getHighSeverityIssues() {
        return highSeverityIssues;
    }

    public SeverityMetric getMediumSeverityIssues() {
        return mediumSeverityIssues;
    }

    public SeverityMetric getLowSeverityIssues() {
        return lowSeverityIssues;
    }

    public int getTotalIssues() {
        return totalIssues;
    }

    public int getTotalUnresolvedIssues() { return totalUnresolvedIssues; }

    public List<IssueSummary> getIssues() {
        return issues;
    }

    public Map<String, Integer> getFileIssueCount() {
        return fileIssueCount;
    }

    public SeverityMetric getResolvedIssues() {
        return resolvedIssues;
    }

    /**
     * Formats the analysis summary for display in pull request comments
     *
     * @param formatter The formatter to use for rendering
     * @return Formatted string representation
     */
    public String format(AnalysisFormatter formatter) {
        return formatter.format(this);
    }

    /**
     * Gets a brief status description based on issue counts
     *
     * @return Status description
     */
    public String getStatusDescription() {
        if (totalIssues == 0) {
            return "No issues found";
        } else if (highSeverityIssues.getCount() > 0) {
            return String.format("Analysis found %d issues (%d high severity)", totalIssues, highSeverityIssues.getCount());
        } else if (mediumSeverityIssues.getCount() > 0) {
            return String.format("Analysis found %d issues (%d medium severity)", totalIssues, mediumSeverityIssues.getCount());
        } else {
            return String.format("Analysis found %d low severity issues", totalIssues);
        }
    }

    public static Builder builder() {
        return new Builder();
    }

    public static class Builder {
        private String projectNamespace;
        private Long pullRequestId;
        private String comment;
        private String platformAnalysisUrl;
        private String pullRequestUrl;
        private OffsetDateTime analysisDate;
        private SeverityMetric highSeverityIssues;
        private SeverityMetric mediumSeverityIssues;
        private SeverityMetric lowSeverityIssues;
        private SeverityMetric resolvedIssues;
        private int totalIssues;
        private int totalUnresolvedIssues;
        private List<IssueSummary> issues;
        private Map<String, Integer> fileIssueCount;

        private Builder() {
            super();
        }

        public Builder withProjectNamespace(String projectNamespace) {
            this.projectNamespace = projectNamespace;
            return this;
        }

        public Builder withPullRequestId(Long pullRequestId) {
            this.pullRequestId = pullRequestId;
            return this;
        }

        public Builder withComment(String comment) {
            this.comment = comment;
            return this;
        }

        public Builder withPlatformAnalysisUrl(String platformAnalysisUrl) {
            this.platformAnalysisUrl = platformAnalysisUrl;
            return this;
        }

        public Builder withPullRequestUrl(String pullRequestUrl) {
            this.pullRequestUrl = pullRequestUrl;
            return this;
        }

        public Builder withAnalysisDate(OffsetDateTime analysisDate) {
            this.analysisDate = analysisDate;
            return this;
        }

        public Builder withHighSeverityIssues(SeverityMetric highSeverityIssues) {
            this.highSeverityIssues = highSeverityIssues;
            return this;
        }

        public Builder withMediumSeverityIssues(SeverityMetric mediumSeverityIssues) {
            this.mediumSeverityIssues = mediumSeverityIssues;
            return this;
        }

        public Builder withLowSeverityIssues(SeverityMetric lowSeverityIssues) {
            this.lowSeverityIssues = lowSeverityIssues;
            return this;
        }

        public Builder withResolvedIssues(SeverityMetric resolvedIssues) {
            this.resolvedIssues = resolvedIssues;
            return this;
        }

        public Builder withTotalIssues(int totalIssues) {
            this.totalIssues = totalIssues;
            return this;
        }

        public Builder withTotalUnresolvedIssues(int totalUnresolvedIssues) {
            this.totalUnresolvedIssues = totalUnresolvedIssues;
            return this;
        }

        public Builder withIssues(List<IssueSummary> issues) {
            this.issues = issues;
            return this;
        }

        public Builder withFileIssueCount(Map<String, Integer> fileIssueCount) {
            this.fileIssueCount = fileIssueCount;
            return this;
        }

        public AnalysisSummary build() {
            return new AnalysisSummary(this);
        }
    }
    /**
     * Represents metrics for a specific severity level
     */
    public static class SeverityMetric {
        private final IssueSeverity severity;
        private final int count;
        private final String url;

        public SeverityMetric(IssueSeverity severity, int count, String url) {
            this.severity = severity;
            this.count = count;
            this.url = url;
        }

        public IssueSeverity getSeverity() {
            return severity;
        }

        public int getCount() {
            return count;
        }

        public String getUrl() {
            return url;
        }
    }

    /**
     * Summary of a single issue
     */
    public static class IssueSummary {
        private final IssueSeverity severity;
        private final String category;
        private final String filePath;
        private final Integer lineNumber;
        private final String reason;
        private final String suggestedFix;
        private final String suggestedFixDiff;
        private final String issueUrl;
        private final Long issueId;

        public IssueSummary(
                IssueSeverity severity,
                String category,
                String filePath,
                Integer lineNumber,
                String reason,
                String suggestedFix,
                String suggestedFixDiff,
                String issueUrl,
                Long issueId
        ) {
            this.severity = severity;
            this.category = category;
            this.filePath = filePath;
            this.lineNumber = lineNumber;
            this.reason = reason;
            this.suggestedFix = suggestedFix;
            this.suggestedFixDiff = suggestedFixDiff;
            this.issueUrl = issueUrl;
            this.issueId = issueId;
        }

        public IssueSeverity getSeverity() {
            return severity;
        }

        public String getCategory() {
            return category;
        }

        public String getFilePath() {
            return filePath;
        }

        public Integer getLineNumber() {
            return lineNumber;
        }

        public String getReason() {
            return reason;
        }

        public String getSuggestedFix() {
            return suggestedFix;
        }

        public String getSuggestedFixDiff() {
            return suggestedFixDiff;
        }

        public String getIssueUrl() {
            return issueUrl;
        }

        public String getShortFilePath() {
            if (filePath == null) return "unknown";
            String[] parts = filePath.split("/");
            return parts.length > 2 ? "..." + filePath.substring(filePath.lastIndexOf('/', filePath.lastIndexOf('/') - 1)) : filePath;
        }

        public String getLocationDescription() {
            if (lineNumber != null && lineNumber > 0) {
                return String.format("%s:%d", getShortFilePath(), lineNumber);
            }
            return getShortFilePath();
        }

        public Long getIssueId() {
            return issueId;
        }
    }
}