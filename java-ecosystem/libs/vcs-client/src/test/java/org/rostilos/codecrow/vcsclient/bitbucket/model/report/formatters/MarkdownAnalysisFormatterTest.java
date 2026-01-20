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

@DisplayName("MarkdownAnalysisFormatter")
class MarkdownAnalysisFormatterTest {

    private MarkdownAnalysisFormatter formatter;

    @BeforeEach
    void setUp() {
        formatter = new MarkdownAnalysisFormatter();
    }

    @Nested
    @DisplayName("Constructor Options")
    class ConstructorOptions {

        @Test
        @DisplayName("should create formatter with default options")
        void shouldCreateFormatterWithDefaultOptions() {
            MarkdownAnalysisFormatter defaultFormatter = new MarkdownAnalysisFormatter();

            AnalysisSummary summary = createSummaryWithHighIssue();
            String result = defaultFormatter.format(summary);

            assertThat(result).doesNotContain("<details>");
            assertThat(result).doesNotContain("<summary>");
        }

        @Test
        @DisplayName("should create formatter with GitHub spoilers enabled")
        void shouldCreateFormatterWithGitHubSpoilersEnabled() {
            MarkdownAnalysisFormatter githubFormatter = new MarkdownAnalysisFormatter(true);

            AnalysisSummary summary = createSummaryWithIssuesAndDetailedIssues();
            String result = githubFormatter.formatDetailedIssues(summary);

            assertThat(result).contains("<details>");
            assertThat(result).contains("<summary>");
            assertThat(result).contains("</details>");
        }

        @Test
        @DisplayName("should create formatter with all options")
        void shouldCreateFormatterWithAllOptions() {
            MarkdownAnalysisFormatter fullFormatter = new MarkdownAnalysisFormatter(true, true);

            AnalysisSummary summary = createSummaryWithIssuesAndDetailedIssues();
            String result = fullFormatter.format(summary);

            assertThat(result).contains("Detailed Issues");
        }
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
            AnalysisSummary summary = createSummaryWithHighIssue();

            String result = formatter.format(summary);

            assertThat(result).contains("⚠️");
            assertThat(result).contains("Code Analysis Results");
            assertThat(result).contains("🔴");
            assertThat(result).contains("High");
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
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(null, 0, ""))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("🟡");
            assertThat(result).contains("Medium");
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
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(null, 0, ""))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("🔵");
            assertThat(result).contains("Low");
            assertThat(result).contains("Minor issues and improvements");
        }

        @Test
        @DisplayName("should format summary with info severity issues")
        void shouldFormatSummaryWithInfoSeverityIssues() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(2)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 2, "http://example.com/info"))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(null, 0, ""))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("ℹ️");
            assertThat(result).contains("Info");
            assertThat(result).contains("Informational notes and suggestions");
        }

        @Test
        @DisplayName("should format summary with resolved issues")
        void shouldFormatSummaryWithResolvedIssues() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(1)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 1, ""))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(null, 3, "http://example.com/resolved"))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("Resolved");
        }

        @Test
        @DisplayName("should format summary with all severity levels")
        void shouldFormatSummaryWithAllSeverityLevels() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(10)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 2, "http://example.com/high"))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 3, "http://example.com/medium"))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 4, "http://example.com/low"))
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 1, "http://example.com/info"))
                    .withResolvedIssues(new AnalysisSummary.SeverityMetric(null, 0, ""))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).contains("🔴").contains("High");
            assertThat(result).contains("🟡").contains("Medium");
            assertThat(result).contains("🔵").contains("Low");
            assertThat(result).contains("ℹ️").contains("Info");
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

            assertThat(result).contains("### Summary");
            assertThat(result).contains("This is a summary comment");
        }

        @Test
        @DisplayName("should not include empty comment")
        void shouldNotIncludeEmptyComment() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(0)
                    .withComment("   ")
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.format(summary);

            assertThat(result).doesNotContain("### Summary");
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

            assertThat(result).contains("[View Full Report](http://example.com/analysis/123)");
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

            assertThat(result).contains("[Pull Request](http://example.com/pr/42)");
        }
    }

    @Nested
    @DisplayName("format() - Detailed Issues")
    class FormatDetailedIssues {

        @Test
        @DisplayName("should include detailed issues when enabled")
        void shouldIncludeDetailedIssuesWhenEnabled() {
            MarkdownAnalysisFormatter formatterWithDetails = new MarkdownAnalysisFormatter(false, true);
            AnalysisSummary summary = createSummaryWithIssuesAndDetailedIssues();

            String result = formatterWithDetails.format(summary);

            assertThat(result).contains("### Detailed Issues");
            assertThat(result).contains("High Severity Issues");
            assertThat(result).contains("Id on Platform");
        }

        @Test
        @DisplayName("should not include detailed issues by default")
        void shouldNotIncludeDetailedIssuesByDefault() {
            AnalysisSummary summary = createSummaryWithIssuesAndDetailedIssues();

            String result = formatter.format(summary);

            assertThat(result).doesNotContain("### Detailed Issues");
        }
    }

    @Nested
    @DisplayName("formatDetailedIssues()")
    class FormatDetailedIssuesMethod {

        @Test
        @DisplayName("should return empty string for no issues")
        void shouldReturnEmptyStringForNoIssues() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(0)
                    .withIssues(Collections.emptyList())
                    .build();

            String result = formatter.formatDetailedIssues(summary);

            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should return empty string for null issues")
        void shouldReturnEmptyStringForNullIssues() {
            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(0)
                    .withIssues(null)
                    .build();

            String result = formatter.formatDetailedIssues(summary);

            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should format issues without spoilers for Bitbucket")
        void shouldFormatIssuesWithoutSpoilersForBitbucket() {
            MarkdownAnalysisFormatter bitbucketFormatter = new MarkdownAnalysisFormatter(false);
            AnalysisSummary summary = createSummaryWithIssuesAndDetailedIssues();

            String result = bitbucketFormatter.formatDetailedIssues(summary);

            assertThat(result).contains("## 📋 Detailed Issues");
            assertThat(result).doesNotContain("<details>");
            assertThat(result).doesNotContain("<summary>");
        }

        @Test
        @DisplayName("should format issues with spoilers for GitHub")
        void shouldFormatIssuesWithSpoilersForGitHub() {
            MarkdownAnalysisFormatter githubFormatter = new MarkdownAnalysisFormatter(true);
            AnalysisSummary summary = createSummaryWithIssuesAndDetailedIssues();

            String result = githubFormatter.formatDetailedIssues(summary);

            assertThat(result).contains("<details>");
            assertThat(result).contains("<summary>");
            assertThat(result).contains("📋 Detailed Issues");
            assertThat(result).contains("</details>");
        }

        @Test
        @DisplayName("should include category emoji for security issues")
        void shouldIncludeCategoryEmojiForSecurityIssues() {
            AnalysisSummary.IssueSummary securityIssue = new AnalysisSummary.IssueSummary(
                    IssueSeverity.HIGH,
                    "SECURITY",
                    "src/App.java",
                    10,
                    "SQL Injection vulnerability",
                    "Use parameterized queries",
                    null,
                    null,
                    1L
            );

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(1)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 1, ""))
                    .withIssues(List.of(securityIssue))
                    .withFileIssueCount(Map.of("src/App.java", 1))
                    .build();

            String result = formatter.formatDetailedIssues(summary);

            assertThat(result).contains("🔒");
            assertThat(result).contains("Security");
        }

        @Test
        @DisplayName("should include suggested fix for Bitbucket")
        void shouldIncludeSuggestedFixForBitbucket() {
            AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                    IssueSeverity.MEDIUM,
                    "CODE_QUALITY",
                    "src/Service.java",
                    25,
                    "Method too long",
                    "Extract method to improve readability",
                    null,
                    null,
                    2L
            );

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(1)
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 1, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Map.of("src/Service.java", 1))
                    .build();

            String result = formatter.formatDetailedIssues(summary);

            assertThat(result).contains("**Suggested Fix:**");
            assertThat(result).contains("> Extract method to improve readability");
        }

        @Test
        @DisplayName("should include suggested fix diff")
        void shouldIncludeSuggestedFixDiff() {
            AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                    IssueSeverity.LOW,
                    "STYLE",
                    "src/Util.java",
                    5,
                    "Inconsistent naming",
                    null,
                    "- int myVar;\n+ int myVariable;",
                    null,
                    3L
            );

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(1)
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 1, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Map.of("src/Util.java", 1))
                    .build();

            String result = formatter.formatDetailedIssues(summary);

            assertThat(result).contains("```diff");
            assertThat(result).contains("- int myVar;");
            assertThat(result).contains("+ int myVariable;");
        }

        @Test
        @DisplayName("should include issue URL when present")
        void shouldIncludeIssueUrlWhenPresent() {
            AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                    IssueSeverity.INFO,
                    "DOCUMENTATION",
                    "src/Main.java",
                    1,
                    "Missing Javadoc",
                    null,
                    null,
                    "http://example.com/issue/4",
                    4L
            );

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(1)
                    .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 1, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Map.of("src/Main.java", 1))
                    .build();

            String result = formatter.formatDetailedIssues(summary);

            assertThat(result).contains("[View Issue Details](http://example.com/issue/4)");
        }

        @Test
        @DisplayName("should include files affected section")
        void shouldIncludeFilesAffectedSection() {
            AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                    IssueSeverity.HIGH,
                    "BUG_RISK",
                    "src/main/java/com/example/MyService.java",
                    100,
                    "Null pointer risk",
                    null,
                    null,
                    null,
                    5L
            );

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(2)
                    .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 2, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Map.of(
                            "src/main/java/com/example/MyService.java", 2
                    ))
                    .build();

            String result = formatter.formatDetailedIssues(summary);

            assertThat(result).contains("### Files Affected");
            assertThat(result).contains("2 issues");
        }
    }

    @Nested
    @DisplayName("Category Formatting")
    class CategoryFormatting {

        @Test
        @DisplayName("should format CODE_QUALITY category")
        void shouldFormatCodeQualityCategory() {
            AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                    IssueSeverity.LOW,
                    "CODE_QUALITY",
                    "src/Test.java",
                    1,
                    "Issue",
                    null, null, null, 1L
            );

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(1)
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 1, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.formatDetailedIssues(summary);

            assertThat(result).contains("🧹");
            assertThat(result).contains("Code Quality");
        }

        @Test
        @DisplayName("should format PERFORMANCE category")
        void shouldFormatPerformanceCategory() {
            AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                    IssueSeverity.MEDIUM,
                    "PERFORMANCE",
                    "src/Test.java",
                    1,
                    "Issue",
                    null, null, null, 1L
            );

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(1)
                    .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 1, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.formatDetailedIssues(summary);

            assertThat(result).contains("⚡");
            assertThat(result).contains("Performance");
        }

        @Test
        @DisplayName("should use default emoji for unknown category")
        void shouldUseDefaultEmojiForUnknownCategory() {
            AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                    IssueSeverity.LOW,
                    "UNKNOWN_CATEGORY",
                    "src/Test.java",
                    1,
                    "Issue",
                    null, null, null, 1L
            );

            AnalysisSummary summary = AnalysisSummary.builder()
                    .withTotalIssues(1)
                    .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 1, ""))
                    .withIssues(List.of(issue))
                    .withFileIssueCount(Collections.emptyMap())
                    .build();

            String result = formatter.formatDetailedIssues(summary);

            assertThat(result).contains("📋");
        }
    }

    // Helper methods
    private AnalysisSummary createSummaryWithHighIssue() {
        return AnalysisSummary.builder()
                .withTotalIssues(2)
                .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 2, "http://example.com/high"))
                .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                .withResolvedIssues(new AnalysisSummary.SeverityMetric(null, 0, ""))
                .withFileIssueCount(Collections.emptyMap())
                .build();
    }

    private AnalysisSummary createSummaryWithIssuesAndDetailedIssues() {
        AnalysisSummary.IssueSummary highIssue = new AnalysisSummary.IssueSummary(
                IssueSeverity.HIGH,
                "SECURITY",
                "src/main/java/com/example/Service.java",
                42,
                "Potential SQL injection",
                "Use prepared statements",
                "- query = \"SELECT * FROM users WHERE id = \" + id;\n+ query = \"SELECT * FROM users WHERE id = ?\"",
                "http://example.com/issue/1",
                1L
        );

        return AnalysisSummary.builder()
                .withTotalIssues(1)
                .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 1, "http://example.com/high"))
                .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                .withResolvedIssues(new AnalysisSummary.SeverityMetric(null, 0, ""))
                .withIssues(List.of(highIssue))
                .withFileIssueCount(Map.of("src/main/java/com/example/Service.java", 1))
                .build();
    }
}
