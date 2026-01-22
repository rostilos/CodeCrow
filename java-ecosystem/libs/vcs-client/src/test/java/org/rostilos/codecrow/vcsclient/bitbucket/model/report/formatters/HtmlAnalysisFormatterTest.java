package org.rostilos.codecrow.vcsclient.bitbucket.model.report.formatters;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Collections;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("HtmlAnalysisFormatter")
class HtmlAnalysisFormatterTest {

    private HtmlAnalysisFormatter formatter;

    @BeforeEach
    void setUp() {
        formatter = new HtmlAnalysisFormatter();
    }

    @Nested
    @DisplayName("format() - No Issues")
    class FormatNoIssues {

        @Test
        @DisplayName("should format summary with no issues")
        void shouldFormatSummaryWithNoIssues() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(0)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("<div class=\"analysis-summary\">");
            assertThat(result).contains("✅");
            assertThat(result).contains("No Issues Found");
        }
    }

    @Nested
    @DisplayName("format() - With Issues")
    class FormatWithIssues {

        @Test
        @DisplayName("should format summary with high severity issues")
        void shouldFormatSummaryWithHighSeverityIssues() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(2)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 2, "http://example.com/high"))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withIssues(Collections.emptyList())
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("⚠️");
            assertThat(result).contains("Code Analysis Results");
            assertThat(result).contains("<tr class=\"high-severity\">");
            assertThat(result).contains("🔴 High");
            assertThat(result).contains("Critical issues requiring immediate attention");
        }

        @Test
        @DisplayName("should format summary with medium severity issues")
        void shouldFormatSummaryWithMediumSeverityIssues() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(3)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 3, "http://example.com/medium"))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withIssues(Collections.emptyList())
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("<tr class=\"medium-severity\">");
            assertThat(result).contains("🟡 Medium");
            assertThat(result).contains("Issues that should be addressed");
        }

        @Test
        @DisplayName("should format summary with low severity issues")
        void shouldFormatSummaryWithLowSeverityIssues() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(5)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 5, "http://example.com/low"))
                    .withIssues(Collections.emptyList())
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("<tr class=\"low-severity\">");
            assertThat(result).contains("🔵 Low");
            assertThat(result).contains("Minor issues and improvements");
        }
    }

    @Nested
    @DisplayName("format() - Summary Comment")
    class FormatSummaryComment {

        @Test
        @DisplayName("should include comment in summary")
        void shouldIncludeCommentInSummary() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(0)
                    .withComment("This is a summary comment")
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("<div class=\"summary-comment\">");
            assertThat(result).contains("<h3>Summary</h3>");
            assertThat(result).contains("This is a summary comment");
        }

        @Test
        @DisplayName("should escape HTML in comment")
        void shouldEscapeHtmlInComment() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(0)
                    .withComment("<script>alert('XSS')</script>")
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("&lt;script&gt;");
            assertThat(result).doesNotContain("<script>");
        }
    }

    @Nested
    @DisplayName("format() - Files Affected")
    class FormatFilesAffected {

        @Test
        @DisplayName("should include files affected section")
        void shouldIncludeFilesAffectedSection() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(0)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withFileIssueCount(Map.of(
                            "src/main/java/Service.java", 3,
                            "src/main/java/Controller.java", 2
                    ))
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("<div class=\"files-affected\">");
            assertThat(result).contains("<h3>Files Affected</h3>");
            assertThat(result).contains("<ul>");
            assertThat(result).contains("3 issues");
            assertThat(result).contains("2 issues");
        }

        @Test
        @DisplayName("should show singular issue for count of 1")
        void shouldShowSingularIssue() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(0)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withFileIssueCount(Map.of("src/File.java", 1))
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("1 issue</li>");
            assertThat(result).doesNotContain("1 issues");
        }
    }

    @Nested
    @DisplayName("format() - Detailed Issues")
    class FormatDetailedIssues {

        @Test
        @DisplayName("should include detailed issues section")
        void shouldIncludeDetailedIssuesSection() {
            AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                    IssueSeverity.HIGH,
                    "SECURITY",
                    "src/App.java",
                    10,
                    "SQL Injection vulnerability",
                    "Use parameterized queries",
                    null,
                    "http://example.com/issue/1",
                    1L
            );

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(1)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 1, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Map.of("src/App.java", 1))
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("<div class=\"detailed-issues\">");
            assertThat(result).contains("🔴 High Severity Issues");
            assertThat(result).contains("SQL Injection vulnerability");
        }
    }

    @Nested
    @DisplayName("format() - Footer")
    class FormatFooter {

        @Test
        @DisplayName("should include analysis date")
        void shouldIncludeAnalysisDate() {
            OffsetDateTime date = OffsetDateTime.of(2024, 6, 15, 10, 30, 0, 0, ZoneOffset.UTC);
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(0)
                    .withAnalysisDate(date)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("<div class=\"analysis-footer\">");
            assertThat(result).contains("Analysis completed on");
            assertThat(result).contains("2024-06-15");
        }

        @Test
        @DisplayName("should include platform analysis URL")
        void shouldIncludePlatformAnalysisUrl() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(0)
                    .withPlatformAnalysisUrl("http://example.com/analysis/123")
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("View Full Report");
            assertThat(result).contains("http://example.com/analysis/123");
        }

        @Test
        @DisplayName("should include pull request URL")
        void shouldIncludePullRequestUrl() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(0)
                    .withPullRequestUrl("http://example.com/pr/42")
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("Pull Request");
            assertThat(result).contains("http://example.com/pr/42");
        }
    }

    @Nested
    @DisplayName("format() - HTML Structure")
    class FormatHtmlStructure {

        @Test
        @DisplayName("should produce valid HTML structure")
        void shouldProduceValidHtmlStructure() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(0)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("<div class=\"analysis-summary\">");
            assertThat(result).contains("</div>");
        }

        @Test
        @DisplayName("should include proper table structure for issues")
        void shouldIncludeProperTableStructure() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(1)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 1, "http://test.com"))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withIssues(Collections.emptyList())
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("<table class=\"issues-table\">");
            assertThat(result).contains("<thead>");
            assertThat(result).contains("<tbody>");
            assertThat(result).contains("</table>");
        }
    }
}
