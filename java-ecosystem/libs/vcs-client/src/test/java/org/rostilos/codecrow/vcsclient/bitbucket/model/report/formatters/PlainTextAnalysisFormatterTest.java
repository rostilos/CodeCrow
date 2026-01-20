package org.rostilos.codecrow.vcsclient.bitbucket.model.report.formatters;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;

import java.time.OffsetDateTime;
import java.util.Collections;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("PlainTextAnalysisFormatter")
class PlainTextAnalysisFormatterTest {

    private PlainTextAnalysisFormatter formatter;

    @BeforeEach
    void setUp() {
        formatter = new PlainTextAnalysisFormatter();
    }

    // Helper method to create IssueSummary with correct parameter order:
    // IssueSeverity severity, String category, String filePath, Integer lineNumber,
    // String reason, String suggestedFix, String suggestedFixDiff, String issueUrl, Long issueId
    private AnalysisSummary.IssueSummary createIssue(IssueSeverity severity, String filePath, 
            Integer lineNumber, String reason) {
        return new AnalysisSummary.IssueSummary(
                severity, "BUG", filePath, lineNumber, reason, null, null, null, 1L);
    }

    private AnalysisSummary.IssueSummary createIssueWithFix(IssueSeverity severity, String filePath,
            Integer lineNumber, String reason, String suggestedFix, String suggestedFixDiff) {
        return new AnalysisSummary.IssueSummary(
                severity, "BUG", filePath, lineNumber, reason, suggestedFix, suggestedFixDiff, null, 1L);
    }

    private AnalysisSummary.IssueSummary createIssueWithUrl(IssueSeverity severity, String filePath,
            Integer lineNumber, String reason, String issueUrl) {
        return new AnalysisSummary.IssueSummary(
                severity, "BUG", filePath, lineNumber, reason, null, null, issueUrl, 1L);
    }

    @Nested
    @DisplayName("format() - No Issues")
    class FormatNoIssues {

        @Test
        @DisplayName("should show no issues message when total issues is 0")
        void shouldShowNoIssuesMessage() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("")
                    .withTotalIssues(0)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(Collections.emptyList())
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("NO ISSUES FOUND");
        }

        @Test
        @DisplayName("should include summary comment when provided")
        void shouldIncludeSummaryComment() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("Great job, no issues!")
                    .withTotalIssues(0)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(Collections.emptyList())
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("SUMMARY:");
            assertThat(result).contains("Great job, no issues!");
        }
    }

    @Nested
    @DisplayName("format() - With Issues")
    class FormatWithIssues {

        @Test
        @DisplayName("should show issues header when there are issues")
        void shouldShowIssuesHeader() {
            AnalysisSummary.IssueSummary issue = createIssue(IssueSeverity.HIGH, "file.java", 10, "Potential bug");

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("")
                    .withTotalIssues(1)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 1, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("CODE ANALYSIS RESULTS");
            assertThat(result).contains("Total Issues: 1");
        }

        @Test
        @DisplayName("should show high severity count")
        void shouldShowHighSeverityCount() {
            AnalysisSummary.IssueSummary issue = createIssue(IssueSeverity.HIGH, "file.java", 10, "Potential bug");

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("")
                    .withTotalIssues(1)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 1, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("High Severity: 1");
        }

        @Test
        @DisplayName("should show medium severity count")
        void shouldShowMediumSeverityCount() {
            AnalysisSummary.IssueSummary issue = createIssue(IssueSeverity.MEDIUM, "file.java", 10, "Code smell");

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("")
                    .withTotalIssues(1)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 1, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("Medium Severity: 1");
        }

        @Test
        @DisplayName("should show low severity count")
        void shouldShowLowSeverityCount() {
            AnalysisSummary.IssueSummary issue = createIssue(IssueSeverity.LOW, "file.java", 10, "Minor issue");

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("")
                    .withTotalIssues(1)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 1, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("Low Severity: 1");
        }

        @Test
        @DisplayName("should show info count")
        void shouldShowInfoCount() {
            AnalysisSummary.IssueSummary issue = createIssue(IssueSeverity.INFO, "file.java", 10, "Information");

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("")
                    .withTotalIssues(1)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 1, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("Info: 1");
        }
    }

    @Nested
    @DisplayName("format() - Issue Details")
    class FormatIssueDetails {

        @Test
        @DisplayName("should include issue location")
        void shouldIncludeIssueLocation() {
            AnalysisSummary.IssueSummary issue = createIssue(IssueSeverity.HIGH, "src/main/java/Test.java", 42, "Bug found");

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("")
                    .withTotalIssues(1)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 1, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("42");
        }

        @Test
        @DisplayName("should include issue reason")
        void shouldIncludeIssueReason() {
            AnalysisSummary.IssueSummary issue = createIssue(IssueSeverity.HIGH, "Test.java", 10, "Null pointer risk");

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("")
                    .withTotalIssues(1)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 1, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("Null pointer risk");
        }

        @Test
        @DisplayName("should include suggested fix when present")
        void shouldIncludeSuggestedFix() {
            AnalysisSummary.IssueSummary issue = createIssueWithFix(
                    IssueSeverity.HIGH, "Test.java", 10, "Bug", "Add null check", null);

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("")
                    .withTotalIssues(1)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 1, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("Add null check");
        }

        @Test
        @DisplayName("should include diff when present")
        void shouldIncludeDiff() {
            AnalysisSummary.IssueSummary issue = createIssueWithFix(
                    IssueSeverity.HIGH, "Test.java", 10, "Bug", "Fix", "- old\n+ new");

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("")
                    .withTotalIssues(1)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 1, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("- old");
            assertThat(result).contains("+ new");
        }
    }

    @Nested
    @DisplayName("format() - Files Affected")
    class FormatFilesAffected {

        @Test
        @DisplayName("should show files affected section")
        void shouldShowFilesAffectedSection() {
            AnalysisSummary.IssueSummary issue = createIssue(IssueSeverity.HIGH, "Test.java", 10, "Bug");

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("")
                    .withTotalIssues(1)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 1, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Map.of("Test.java", 1))
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("FILES AFFECTED:");
        }

        @Test
        @DisplayName("should show singular issue for one issue")
        void shouldShowSingularIssue() {
            AnalysisSummary.IssueSummary issue = createIssue(IssueSeverity.HIGH, "Test.java", 10, "Bug");

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("")
                    .withTotalIssues(1)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 1, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Map.of("Test.java", 1))
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("1 issue");
            assertThat(result).doesNotContain("1 issues");
        }

        @Test
        @DisplayName("should show plural issues for multiple issues")
        void shouldShowPluralIssues() {
            AnalysisSummary.IssueSummary issue1 = createIssue(IssueSeverity.HIGH, "Test.java", 10, "Bug1");
            AnalysisSummary.IssueSummary issue2 = createIssue(IssueSeverity.HIGH, "Test.java", 20, "Bug2");

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("")
                    .withTotalIssues(2)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 2, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(List.of(issue1, issue2))
                    .withFileIssueCount(Map.of("Test.java", 2))
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("2 issues");
        }
    }

    @Nested
    @DisplayName("format() - Footer")
    class FormatFooter {

        @Test
        @DisplayName("should show analysis date when provided")
        void shouldShowAnalysisDate() {
            OffsetDateTime analysisDate = OffsetDateTime.now();

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("")
                    .withTotalIssues(0)
                    .withAnalysisDate(analysisDate)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(Collections.emptyList())
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("Analysis completed on:");
        }

        @Test
        @DisplayName("should show platform URL when provided")
        void shouldShowPlatformUrl() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("")
                    .withTotalIssues(0)
                    .withPlatformAnalysisUrl("https://platform.example.com/analysis/123")
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(Collections.emptyList())
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("Full Report:");
            assertThat(result).contains("https://platform.example.com/analysis/123");
        }

        @Test
        @DisplayName("should show pull request URL when provided")
        void shouldShowPullRequestUrl() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("")
                    .withTotalIssues(0)
                    .withPullRequestUrl("https://bitbucket.org/workspace/repo/pull-requests/42")
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(Collections.emptyList())
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("Pull Request:");
            assertThat(result).contains("https://bitbucket.org/workspace/repo/pull-requests/42");
        }
    }

    @Nested
    @DisplayName("format() - Multiple Severity Levels")
    class FormatMultipleSeverityLevels {

        @Test
        @DisplayName("should format all severity levels together")
        void shouldFormatAllSeverityLevelsTogether() {
            AnalysisSummary.IssueSummary highIssue = createIssue(IssueSeverity.HIGH, "High.java", 1, "High severity");
            AnalysisSummary.IssueSummary mediumIssue = createIssue(IssueSeverity.MEDIUM, "Medium.java", 2, "Medium severity");
            AnalysisSummary.IssueSummary lowIssue = createIssue(IssueSeverity.LOW, "Low.java", 3, "Low severity");
            AnalysisSummary.IssueSummary infoIssue = createIssue(IssueSeverity.INFO, "Info.java", 4, "Info note");

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withComment("Multiple issues found")
                    .withTotalIssues(4)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 1, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 1, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 1, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 1, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.RESOLVED, 0, ""))
                    .withIssues(List.of(highIssue, mediumIssue, lowIssue, infoIssue))
                    .withFileIssueCount(Map.of(
                            "High.java", 1,
                            "Medium.java", 1,
                            "Low.java", 1,
                            "Info.java", 1))
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("CODE ANALYSIS RESULTS");
            assertThat(result).contains("Total Issues: 4");
            assertThat(result).contains("High Severity: 1");
            assertThat(result).contains("Medium Severity: 1");
            assertThat(result).contains("Low Severity: 1");
            assertThat(result).contains("Info: 1");
            assertThat(result).contains("High severity");
            assertThat(result).contains("Medium severity");
            assertThat(result).contains("Low severity");
            assertThat(result).contains("Info note");
        }
    }
}
