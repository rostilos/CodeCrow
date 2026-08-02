package org.rostilos.codecrow.pipelineagent.github.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class GitHubReviewFormatterTest {
    private static final String MARKER = "<!-- codecrow-analysis-review -->";

    private final GitHubReviewFormatter formatter = new GitHubReviewFormatter();

    @Test
    void formatsAnchoredIssuesAsGitHubReviewComments() {
        AnalysisSummary.IssueSummary issue = issue(
                "/src/App.java",
                42,
                "Use a bounded cache",
                "The cache can grow without limit.",
                "Replace it with a bounded implementation.",
                "--- a/src/App.java\n+++ b/src/App.java\n@@ -42 +42 @@\n-old\n+new"
        );

        List<Map<String, Object>> comments = formatter.formatComments(List.of(issue), MARKER);

        assertThat(comments).hasSize(1);
        assertThat(comments.get(0))
                .containsEntry("path", "src/App.java")
                .containsEntry("line", 42)
                .containsEntry("side", "RIGHT");
        assertThat(comments.get(0).get("body").toString())
                .contains("🟡 **MEDIUM** | Error Handling")
                .contains("**Use a bounded cache**")
                .contains("The cache can grow without limit.")
                .contains("<summary>💡 Suggested fix</summary>")
                .contains("```diff")
                .contains("[View issue in CodeCrow](https://codecrow.example/issues/1)")
                .endsWith(MARKER);
    }

    @Test
    void skipsIssuesWithoutAConfidentLineAnchor() {
        AnalysisSummary.IssueSummary noPath = issue(null, 10, "No path", "Reason", null, null);
        AnalysisSummary.IssueSummary noLine = issue("src/App.java", null, "No line", "Reason", null, null);
        AnalysisSummary.IssueSummary syntheticLineOne = issue(
                "src/App.java", 1, "Synthetic anchor", "Reason", null, null);

        List<Map<String, Object>> comments = formatter.formatComments(
                List.of(noPath, noLine, syntheticLineOne), MARKER);

        assertThat(comments).isEmpty();
    }

    @Test
    void keepsLineOneWhenTheIssueIncludesItsSourceSnippet() {
        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.LOW,
                "CODE_QUALITY",
                "src/App.java",
                1,
                "Package declaration",
                "The declaration is inconsistent.",
                null,
                null,
                null,
                1L,
                "package example;"
        );

        assertThat(formatter.formatComments(List.of(issue), MARKER)).hasSize(1);
    }

    @Test
    void limitsTheReviewToTwentyInlineComments() {
        AnalysisSummary.IssueSummary issue = issue(
                "src/App.java", 10, "Title", "Reason", null, null);

        assertThat(formatter.formatComments(java.util.Collections.nCopies(25, issue), MARKER))
                .hasSize(20);
        assertThat(formatter.formatReviewBody(20, MARKER))
                .contains("**Actionable comments posted: 20**")
                .endsWith(MARKER);
    }

    private AnalysisSummary.IssueSummary issue(
            String path,
            Integer line,
            String title,
            String reason,
            String fix,
            String diff
    ) {
        return new AnalysisSummary.IssueSummary(
                IssueSeverity.MEDIUM,
                "ERROR_HANDLING",
                path,
                line,
                title,
                reason,
                fix,
                diff,
                "https://codecrow.example/issues/1",
                1L
        );
    }
}
