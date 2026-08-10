package org.rostilos.codecrow.pipelineagent.github.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;
import org.rostilos.codecrow.vcsclient.github.actions.GetPullRequestDiffAction.PullRequestFilePatch;

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

        GitHubReviewFormatter.ReviewPlan plan = formatter.planComments(
                List.of(issue), MARKER, List.of(patch(
                        "src/App.java", "@@ -42 +42 @@\n-old\n+new")));
        List<Map<String, Object>> comments = plan.inlineComments();

        assertThat(comments).hasSize(1);
        assertThat(plan.nonInlineFindings()).isEmpty();
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

        GitHubReviewFormatter.ReviewPlan plan = formatter.planComments(
                List.of(noPath, noLine, syntheticLineOne),
                MARKER,
                List.of(patch("src/App.java", "@@ -1,10 +1,10 @@\n context")));

        assertThat(plan.inlineComments()).isEmpty();
        assertThat(plan.nonInlineFindings()).hasSize(3);
    }

    @Test
    void keepsLineOneWithASourceSnippetOnlyWhenItIsInTheDiff() {
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

        GitHubReviewFormatter.ReviewPlan outsideDiff = formatter.planComments(
                List.of(issue),
                MARKER,
                List.of(patch("src/App.java", "@@ -20 +20 @@\n-old\n+new")));
        GitHubReviewFormatter.ReviewPlan newFile = formatter.planComments(
                List.of(issue),
                MARKER,
                List.of(patch("src/App.java", "@@ -0,0 +1,2 @@\n+package example;\n+class App {}")));

        assertThat(outsideDiff.inlineComments()).isEmpty();
        assertThat(outsideDiff.nonInlineFindings()).singleElement()
                .satisfies(finding -> assertThat(finding.reason())
                        .contains("outside the current pull-request diff"));
        assertThat(newFile.inlineComments()).hasSize(1);
        assertThat(newFile.nonInlineFindings()).isEmpty();
    }

    @Test
    void separatesOutOfDiffFindingsWithoutDroppingValidComments() {
        AnalysisSummary.IssueSummary valid = issue(
                "src/App.java", 42, "Valid anchor", "Reason", null, null);
        AnalysisSummary.IssueSummary outside = issue(
                "src/App.java", 67, "File-wide finding", "Reason", null, null);

        GitHubReviewFormatter.ReviewPlan plan = formatter.planComments(
                List.of(valid, outside),
                MARKER,
                List.of(patch("src/App.java", "@@ -42 +42 @@\n-old\n+new")));

        assertThat(plan.inlineComments()).singleElement()
                .satisfies(comment -> assertThat(comment).containsEntry("line", 42));
        assertThat(plan.nonInlineFindings()).singleElement()
                .satisfies(finding -> {
                    assertThat(finding.issue().getTitle()).isEqualTo("File-wide finding");
                    assertThat(finding.reason()).contains("outside the current pull-request diff");
                });
        assertThat(formatter.formatNonInlineFindings(plan.nonInlineFindings()))
                .contains("<summary><b>📍 Findings not posted inline (1)</b></summary>")
                .contains("[File-wide finding](https://codecrow.example/issues/1)")
                .contains("`src/App.java:67`")
                .contains("outside the current pull-request diff");
    }

    @Test
    void limitsTheReviewToTwentyInlineComments() {
        AnalysisSummary.IssueSummary issue = issue(
                "src/App.java", 10, "Title", "Reason", null, null);

        GitHubReviewFormatter.ReviewPlan plan = formatter.planComments(
                java.util.Collections.nCopies(25, issue),
                MARKER,
                List.of(patch("src/App.java", "@@ -10 +10 @@\n-old\n+new")));

        assertThat(plan.inlineComments()).hasSize(20);
        assertThat(plan.nonInlineFindings()).hasSize(5)
                .allSatisfy(finding -> assertThat(finding.reason()).contains("limit of 20"));
        assertThat(formatter.formatReviewBody(20, MARKER))
                .contains("**Actionable comments posted: 20**")
                .endsWith(MARKER);
    }

    @Test
    void keepsAllFindingsInTheSummaryWhenTheDiffCannotBeLoaded() {
        AnalysisSummary.IssueSummary issue = issue(
                "src/App.java", 10, "Title", "Reason", null, null);

        GitHubReviewFormatter.ReviewPlan plan = formatter.planWithoutDiff(List.of(issue));

        assertThat(plan.inlineComments()).isEmpty();
        assertThat(plan.nonInlineFindings()).singleElement()
                .satisfies(finding -> assertThat(finding.reason())
                        .contains("diff could not be loaded"));
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

    private PullRequestFilePatch patch(String path, String patch) {
        return new PullRequestFilePatch(path, "", "modified", patch);
    }
}
