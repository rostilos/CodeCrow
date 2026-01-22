package org.rostilos.codecrow.vcsclient.bitbucket.model.report;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.formatters.AnalysisFormatter;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class AnalysisSummaryTest {

    @Test
    void testBuilder_CreatesAnalysisSummary() {
        OffsetDateTime now = OffsetDateTime.now();
        AnalysisSummary.SeverityMetric highMetric = new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 5, "url");
        AnalysisSummary.SeverityMetric mediumMetric = new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 10, "url");

        AnalysisSummary summary = AnalysisSummary.builder()
                .withProjectNamespace("my-project")
                .withPullRequestId(123L)
                .withComment("Test comment")
                .withPlatformAnalysisUrl("https://analysis.url")
                .withPullRequestUrl("https://pr.url")
                .withAnalysisDate(now)
                .withHighSeverityIssues(highMetric)
                .withMediumSeverityIssues(mediumMetric)
                .withTotalIssues(15)
                .withTotalUnresolvedIssues(12)
                .withIssues(List.of())
                .withFileIssueCount(Map.of())
                .build();

        assertThat(summary.getProjectNamespace()).isEqualTo("my-project");
        assertThat(summary.getPullRequestId()).isEqualTo(123L);
        assertThat(summary.getComment()).isEqualTo("Test comment");
        assertThat(summary.getPlatformAnalysisUrl()).isEqualTo("https://analysis.url");
        assertThat(summary.getPullRequestUrl()).isEqualTo("https://pr.url");
        assertThat(summary.getAnalysisDate()).isEqualTo(now);
        assertThat(summary.getHighSeverityIssues()).isEqualTo(highMetric);
        assertThat(summary.getMediumSeverityIssues()).isEqualTo(mediumMetric);
        assertThat(summary.getTotalIssues()).isEqualTo(15);
        assertThat(summary.getTotalUnresolvedIssues()).isEqualTo(12);
    }

    @Test
    void testGetStatusDescription_NoIssues() {
        AnalysisSummary summary = AnalysisSummary.builder()
                .withTotalIssues(0)
                .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, null))
                .build();

        assertThat(summary.getStatusDescription()).isEqualTo("No issues found");
    }

    @Test
    void testGetStatusDescription_WithHighSeverity() {
        AnalysisSummary summary = AnalysisSummary.builder()
                .withTotalIssues(15)
                .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 5, null))
                .build();

        assertThat(summary.getStatusDescription()).isEqualTo("Analysis found 15 issues (5 high severity)");
    }

    @Test
    void testGetStatusDescription_WithMediumSeverity() {
        AnalysisSummary summary = AnalysisSummary.builder()
                .withTotalIssues(10)
                .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, null))
                .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 7, null))
                .build();

        assertThat(summary.getStatusDescription()).isEqualTo("Analysis found 10 issues (7 medium severity)");
    }

    @Test
    void testGetStatusDescription_OnlyLowSeverity() {
        AnalysisSummary summary = AnalysisSummary.builder()
                .withTotalIssues(5)
                .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, null))
                .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, null))
                .build();

        assertThat(summary.getStatusDescription()).isEqualTo("Analysis found 5 low severity issues");
    }

    @Test
    void testFormat_CallsFormatter() {
        AnalysisFormatter formatter = mock(AnalysisFormatter.class);
        AnalysisSummary summary = AnalysisSummary.builder().build();
        when(formatter.format(summary)).thenReturn("Formatted output");

        String result = summary.format(formatter);

        assertThat(result).isEqualTo("Formatted output");
    }

    @Test
    void testSeverityMetric_GettersWork() {
        AnalysisSummary.SeverityMetric metric = new AnalysisSummary.SeverityMetric(
                IssueSeverity.HIGH, 10, "https://issues.url");

        assertThat(metric.getSeverity()).isEqualTo(IssueSeverity.HIGH);
        assertThat(metric.getCount()).isEqualTo(10);
        assertThat(metric.getUrl()).isEqualTo("https://issues.url");
    }

    @Test
    void testIssueSummary_GettersWork() {
        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.MEDIUM,
                "security",
                "src/main/java/Test.java",
                42,
                "Security issue",
                "Fix suggestion",
                "diff",
                "https://issue.url",
                123L
        );

        assertThat(issue.getSeverity()).isEqualTo(IssueSeverity.MEDIUM);
        assertThat(issue.getCategory()).isEqualTo("security");
        assertThat(issue.getFilePath()).isEqualTo("src/main/java/Test.java");
        assertThat(issue.getLineNumber()).isEqualTo(42);
        assertThat(issue.getReason()).isEqualTo("Security issue");
        assertThat(issue.getSuggestedFix()).isEqualTo("Fix suggestion");
        assertThat(issue.getSuggestedFixDiff()).isEqualTo("diff");
        assertThat(issue.getIssueUrl()).isEqualTo("https://issue.url");
        assertThat(issue.getIssueId()).isEqualTo(123L);
    }

    @Test
    void testIssueSummary_GetShortFilePath_LongPath() {
        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.LOW, "test", "src/main/java/com/example/Test.java",
                null, null, null, null, null, null);

        assertThat(issue.getShortFilePath()).startsWith("...");
        assertThat(issue.getShortFilePath()).contains("Test.java");
    }

    @Test
    void testIssueSummary_GetShortFilePath_ShortPath() {
        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.LOW, "test", "Test.java",
                null, null, null, null, null, null);

        assertThat(issue.getShortFilePath()).isEqualTo("Test.java");
    }

    @Test
    void testIssueSummary_GetShortFilePath_NullPath() {
        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.LOW, "test", null,
                null, null, null, null, null, null);

        assertThat(issue.getShortFilePath()).isEqualTo("unknown");
    }

    @Test
    void testIssueSummary_GetLocationDescription_WithLineNumber() {
        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.LOW, "test", "Test.java",
                42, null, null, null, null, null);

        assertThat(issue.getLocationDescription()).isEqualTo("Test.java:42");
    }

    @Test
    void testIssueSummary_GetLocationDescription_WithoutLineNumber() {
        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.LOW, "test", "Test.java",
                null, null, null, null, null, null);

        assertThat(issue.getLocationDescription()).isEqualTo("Test.java");
    }
}
